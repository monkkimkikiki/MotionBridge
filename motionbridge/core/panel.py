"""AI 회의: 여러 AI 가 같은 영상 장면과 같은 애니메이션 이름 목록을 보면서 서로 의견을 주고받아 가장 맞는 애니메이션을 고른다.

- 이미지를 볼 수 있는 AI(Qwen-VL, Claude, Gemini …)는 장면 시트(여러 컷을 한 장으로 붙인 그림)를 직접 본다.
- 글 전용 AI(GPT-OSS)는 그림을 못 보므로, 다른 AI 들이 말한 장면 묘사를 근거로 같은 목록에서 추론한다.
- 라운드마다 서로의 선택과 이유를 보여 주고 고치게 한다. 목록에 없는 이름은 버린다(지어내기 방지).
- 마지막에 참가자들의 최종 선택을 합쳐 합의 순위를 낸다. 대화 기록은 그대로 돌려줘 검수자가 볼 수 있다.
Qt 에 의존하지 않는다. AI 는 Provider(ask/ask_text) 로만 부른다.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import vision


@dataclass
class Member:
    name: str                      # 화면에 보일 이름 (예: "Qwen2.5-VL", "GPT-OSS")
    provider: "vision.Provider"

    @property
    def sees(self) -> bool:
        return bool(getattr(self.provider, "supports_images", True))


@dataclass
class Pick:
    name: str
    conf: float
    reason: str = ""


@dataclass
class Turn:
    round: int
    speaker: str
    text: str                      # 사람이 읽는 요약
    picks: List[Pick] = field(default_factory=list)


@dataclass
class Verdict:
    ranking: List[Tuple[str, float, List[str]]]   # (이름, 합의 점수 0~1, 지지한 참가자들)
    transcript: List[Turn]
    descriptions: Dict[str, str]
    errors: List[str]


def make_sheet(frame_paths: Sequence[str], out_path: str, cols: int = 4, cell_w: int = 480, max_frames: int = 12) -> str:
    """장면 여러 장을 한 장의 시트로 붙인다 (시간 순서, 왼쪽 위부터). OpenCV 사용."""
    import cv2
    import numpy as np
    paths = list(frame_paths)
    if len(paths) > max_frames:
        paths = [paths[int(i * len(paths) / max_frames)] for i in range(max_frames)]
    imgs = []
    for p in paths:
        im = cv2.imread(p)
        if im is None:
            continue
        h, w = im.shape[:2]
        im = cv2.resize(im, (cell_w, max(1, int(h * cell_w / w))))
        imgs.append(im)
    if not imgs:
        raise vision.VisionError("장면 이미지를 읽지 못했습니다.")
    ch = max(i.shape[0] for i in imgs)
    cols = min(cols, len(imgs))
    rows = (len(imgs) + cols - 1) // cols
    sheet = np.zeros((rows * ch, cols * cell_w, 3), np.uint8)
    for k, im in enumerate(imgs):
        r, c = divmod(k, cols)
        sheet[r * ch : r * ch + im.shape[0], c * cell_w : (c + 1) * cell_w] = im
        cv2.putText(sheet, str(k + 1), (c * cell_w + 8, r * ch + 28), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    cv2.imwrite(out_path, sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return out_path


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", os.path.splitext(os.path.basename(str(s)))[0].lower())


class _Names:
    """애니메이션 이름 목록 안에서만 고르게 하는 검증기 (대소문자·경로·확장자 무시)."""

    def __init__(self, names: Sequence[str]) -> None:
        self.names = list(dict.fromkeys(names))
        self.by = {_norm(n): n for n in self.names}

    def find(self, raw: str) -> Optional[str]:
        k = _norm(raw)
        if not k:
            return None
        if k in self.by:
            return self.by[k]
        # 거의 같은 이름(한쪽이 다른 쪽에 들어 있고 길이가 80% 이상)만 인정 — 짧은 앞부분만 맞는 건 다른 애니메이션일 수 있다
        hits = [v for kk, v in self.by.items() if (k in kk or kk in k) and min(len(k), len(kk)) >= 0.8 * max(len(k), len(kk))]
        return hits[0] if len(hits) == 1 else None


def _intro(hint: str, names: Sequence[str], extra: str) -> str:
    return ("You are one member of a panel of AIs identifying WHICH game animation asset matches a short gameplay clip.\n"
            "The clip is shown as a contact sheet (numbered frames in time order, left to right, top to bottom)%s.\n"
            "Closed list of animation asset names (pick ONLY from this list, exactly as written):\n%s\n"
            "%s\n") % ("" if not extra else " " + extra, ", ".join(names), ("Known context: " + hint + "\n") if hint else "")


FORMAT = ("Answer with JSON only: {\"description\": \"<2-3 sentences: what the player character does, in order>\", "
          "\"picks\": [{\"name\": \"<name from list>\", \"confidence\": 0.0-1.0, \"reason\": \"<short, cite what you see/read>\"}]} "
          "with at most 5 picks, best first. If nothing in the list plausibly matches, return an empty picks list.")


def _parse(text: str, names: _Names) -> Tuple[str, List[Pick]]:
    obj = vision._extract_json(text)
    if not isinstance(obj, dict):
        raise vision.VisionError("답을 해석하지 못했습니다: " + (text or "")[:120])
    desc = str(obj.get("description") or "").strip()[:600]
    out: List[Pick] = []
    seen = set()
    for it in obj.get("picks") or obj.get("matches") or []:
        if isinstance(it, str):
            it = {"name": it}
        if not isinstance(it, dict):
            continue
        nm = names.find(str(it.get("name") or ""))
        if not nm or nm in seen:
            continue
        seen.add(nm)
        try:
            c = float(it.get("confidence", 0.5))
        except (TypeError, ValueError):
            c = 0.5
        if c > 1.0:
            c = min(1.0, c / 100.0)
        out.append(Pick(nm, max(0.0, min(1.0, c)), str(it.get("reason") or "").strip()[:200]))
    return desc, out[:5]


def _summary(picks: Sequence[Pick]) -> str:
    return "; ".join("%s (%d%%) %s" % (p.name, round(p.conf * 100), p.reason) for p in picks) or "(맞는 것 없음)"


def run_panel(members: Sequence[Member], sheet_path: Optional[str], names: Sequence[str], hint: str = "", rounds: int = 2,
              on_progress: Optional[Callable[[str], None]] = None) -> Verdict:
    """참가자 모두가 같은 시트·같은 이름 목록으로 라운드를 돌려 합의 순위를 만든다.

    1라운드: 이미지 보는 AI 가 먼저(장면 묘사 + 선택), 글 전용 AI 는 그 묘사를 읽고 선택.
    2라운드~: 모두 다른 사람의 선택·이유를 보고 고친다."""
    nm = _Names(names)
    if not nm.names:
        raise vision.VisionError("애니메이션 이름 목록이 비어 있습니다.")
    if not members:
        raise vision.VisionError("참가자가 없습니다.")
    if not any(m.sees for m in members):
        raise vision.VisionError("이미지를 볼 수 있는 AI 가 한 명은 있어야 합니다 (글 전용 AI 만으로는 영상을 알 수 없습니다).")
    if sheet_path is None:
        raise vision.VisionError("장면 시트가 없습니다.")
    say = on_progress or (lambda _s: None)
    transcript: List[Turn] = []
    errors: List[str] = []
    desc: Dict[str, str] = {}
    current: Dict[str, List[Pick]] = {}
    limit = min(m.provider.vocab_limit() for m in members)
    shown = nm.names[:limit]
    order = [m for m in members if m.sees] + [m for m in members if not m.sees]
    for rd in range(1, max(1, rounds) + 1):
        new: Dict[str, List[Pick]] = {}
        for m in order:
            say("라운드 %d · %s 생각 중" % (rd, m.name))
            if rd == 1:
                seen_desc = "\n".join("- %s saw: %s" % (k, v) for k, v in desc.items() if v)
                extra = "" if m.sees else "(you cannot see images; rely on what the other panelists describe below)"
                body = _intro(hint, shown, "" if m.sees else "") + (("Other panelists describe the clip:\n" + seen_desc + "\n") if (not m.sees and seen_desc) else "") + \
                    ("\n" + extra + "\n" if extra else "") + FORMAT
            else:
                others = "\n".join("- %s: %s" % (k, _summary(v)) for k, v in current.items() if k != m.name)
                mine = _summary(current.get(m.name, []))
                body = _intro(hint, shown, "") + "Other panelists' latest choices:\n%s\nYour previous choice: %s\n" % (others or "(none)", mine) + \
                    "Re-check the clip against each candidate name. Keep your pick if you still believe it, or switch if another panelist's reason is better. " + FORMAT
            try:
                text = m.provider.ask(sheet_path, body) if m.sees else m.provider.ask_text(body)
                d, picks = _parse(text, nm)
            except vision.VisionError as e:
                errors.append("%s: %s" % (m.name, e))
                transcript.append(Turn(rd, m.name, "⚠ " + str(e)[:160]))
                continue
            if d and (rd == 1 or m.name not in desc):
                desc[m.name] = d
            new[m.name] = picks
            transcript.append(Turn(rd, m.name, (d + "\n" if d else "") + "→ " + _summary(picks), picks))
        if new:
            current.update(new)
    score: Dict[str, float] = {}
    who: Dict[str, List[str]] = {}
    n = max(1, len(current))
    for member, picks in current.items():
        for rank, p in enumerate(picks):
            w = p.conf * (1.0 - 0.15 * rank) / n
            score[p.name] = score.get(p.name, 0.0) + w
            who.setdefault(p.name, []).append(member)
    ranking = sorted(((k, round(v, 4), who[k]) for k, v in score.items()), key=lambda t: (-t[1], -len(t[2]), t[0]))
    return Verdict(ranking, transcript, desc, errors)
