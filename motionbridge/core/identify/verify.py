"""생김새 검증 (Qt 불필요): 장면 속 캐릭터와 후보 메시의 렌더를 한 시트에 놓고 VLM 에게 비교시킨다.

이름만으로 고르던 추측의 다음 단계: 이미지를 넣어도 엉뚱한 메시가 나올 때,
후보 메시를 실제로 렌더해서 '생김새'로 거른다.

흐름: 후보 메시 -> (추출 도우미) GLB -> (Blender) 썸네일 PNG  ← 게임별 캐시, 한 번만
      장면(box 가 있으면 크롭) + 썸네일들 -> 시트 한 장 -> "몇 번이 같은 캐릭터인가?" -> 후보 재정렬
추출/Blender/VLM 어느 하나라도 안 되면 조용히 건너뛴다 (보조 기능)."""
from __future__ import annotations

import hashlib
import os
import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .. import bridge
from ..vision import _extract_json
from .entity_store import _safe

THUMB_SIZE = 512
MAX_CANDS = 4          # 시트에 넣는 후보 수 (많으면 VLM 이 헷갈린다)


def cache_dir(game: str, kind: str = "refimg") -> str:
    return os.path.join(bridge.CONFIG_DIR, kind, _safe(game))


def thumb_path(game: str, mesh_path: str) -> str:
    low = (mesh_path or "").replace("\\", "/").lower()
    h = hashlib.sha1(low.encode("utf-8")).hexdigest()[:16]
    name = re.sub(r"[^\w-]+", "_", os.path.splitext(os.path.basename(low))[0])[:40]
    return os.path.join(cache_dir(game), "%s_%s.png" % (name, h))


def render_thumb(glb: str, out_png: str, blender: Optional[str] = None, size: int = THUMB_SIZE) -> bool:
    """Blender 로 GLB 를 PNG 한 장으로 렌더한다."""
    bridge.run_script("blender_thumb.py", [os.path.abspath(glb), os.path.abspath(out_png), str(size)], blender)
    return os.path.isfile(out_png)


def ensure_thumbs(game: str, mesh_paths: Sequence[str], glb_of: Callable[[str], Optional[str]],
                  blender: Optional[str] = None, render: Callable = render_thumb,
                  progress: Optional[Callable[[int, int, str], None]] = None) -> Dict[str, Optional[str]]:
    """메시 경로 -> 썸네일 PNG (못 만들면 None). 이미 있으면 다시 만들지 않는다."""
    out: Dict[str, Optional[str]] = {}
    todo = list(dict.fromkeys(mesh_paths))
    for i, p in enumerate(todo):
        if progress:
            progress(i, len(todo), os.path.basename(p))
        t = thumb_path(game, p)
        if os.path.isfile(t):
            out[p] = t
            continue
        try:
            glb = glb_of(p)
        except Exception:  # noqa: BLE001 - 추출 실패는 건너뜀
            glb = None
        if not glb or not os.path.isfile(glb):
            out[p] = None
            continue
        os.makedirs(os.path.dirname(t), exist_ok=True)
        try:
            ok = render(glb, t, blender)
        except Exception:  # noqa: BLE001 - 렌더 실패는 건너뜀
            ok = False
        out[p] = t if (ok and os.path.isfile(t)) else None
    return out


def crop_query(frame: str, box, out_png: str):
    """장면에서 캐릭터 영역(box: 0~1 비율 l,t,r,b)만 여유를 두고 잘라 저장. 안 되면 원본 경로."""
    if not box:
        return frame
    try:
        import cv2
        img = cv2.imread(frame)
        if img is None:
            return frame
        h, w = img.shape[:2]
        l, t, r, b = box
        mx, my = int(0.08 * w), int(0.08 * h)
        x0, y0 = max(int(l * w) - mx, 0), max(int(t * h) - my, 0)
        x1, y1 = min(int(r * w) + mx, w), min(int(b * h) + my, h)
        if x1 - x0 < 32 or y1 - y0 < 32:
            return frame
        cv2.imwrite(out_png, img[y0:y1, x0:x1])
        return out_png if os.path.isfile(out_png) else frame
    except Exception:  # noqa: BLE001
        return frame


def build_prompt(n: int) -> str:
    return (
        "This sheet contains %d images in reading order (left to right). "
        "Image 1 is a game screenshot showing a character or monster. "
        "Images 2..%d are 3D model renders of candidate characters on a gray background. "
        "Which render shows the SAME character as the screenshot? Compare body shape, head, limbs, "
        "clothing/armor and distinctive features; ignore pose, lighting and background. "
        'Answer ONLY JSON: {"match": <image number 2..%d, or null if none match>, '
        '"confidence": <0..1>, "why": "<short reason>"}' % (n + 1, n + 1, n + 1)
    )


def parse_verdict(text: str, n: int) -> Tuple[Optional[int], float, str]:
    """VLM 답 -> (후보 0-기준 번호 | None, 확신, 이유)."""
    obj = _extract_json(text or "")
    if not isinstance(obj, dict):
        return None, 0.0, ""
    why = str(obj.get("why") or "")[:160]
    try:
        conf = max(0.0, min(float(obj.get("confidence") or 0.0), 1.0))
    except (TypeError, ValueError):
        conf = 0.0
    m = obj.get("match")
    try:
        m = int(m)
    except (TypeError, ValueError):
        return None, conf, why
    idx = m - 2   # 시트에서 2번부터가 후보
    return (idx, conf, why) if 0 <= idx < n else (None, conf, why)


def verify_one(ask: Callable[[str, str], str], game: str, frame: str, box,
               cand_paths: Sequence[str], thumbs: Dict[str, Optional[str]],
               workdir: str, make_sheet: Callable) -> Optional[dict]:
    """한 줄(그룹)을 검증한다 -> {"path": 맞는 메시|None, "confidence", "why", "sheet"} | 검증 불가면 None.

    ask = provider.ask(image, prompt). make_sheet = panel.make_sheet (주입: cv2 의존을 여기서 끊는다)."""
    pairs = [(p, thumbs.get(p)) for p in list(dict.fromkeys(cand_paths))[:MAX_CANDS]]
    pairs = [(p, t) for p, t in pairs if t]
    if not pairs or not frame or not os.path.isfile(frame):
        return None
    os.makedirs(workdir, exist_ok=True)
    q = crop_query(frame, box, os.path.join(workdir, "query.png"))
    sheet = make_sheet([q] + [t for _p, t in pairs], os.path.join(workdir, "verify_sheet.jpg"),
                       cols=len(pairs) + 1, cell_w=384, max_frames=len(pairs) + 1)
    idx, conf, why = parse_verdict(ask(sheet, build_prompt(len(pairs))), len(pairs))
    return {"path": pairs[idx][0] if idx is not None else None, "confidence": conf, "why": why, "sheet": sheet}
