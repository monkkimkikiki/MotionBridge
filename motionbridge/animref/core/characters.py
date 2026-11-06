"""등록한 캐릭터(참고 이미지)로 영상 속 '대상 캐릭터'를 찾는다.

흐름
  1) 캐릭터 등록: 이름 + 참고 이미지 몇 장 -> 외형 특징(CLIP 임베딩 + 색 히스토그램)을 저장
  2) locate():  클립을 추적해서 사람(캐릭터) 후보 추적선들을 만들고, 등록한 캐릭터와 닮은 정도로 순위를 매김
  3) decide():  1등이 확실하면 자동 선택 / 애매하면 VLM(Qwen3-VL)에게 물어봄 / 그래도 애매하면 화면에서 사용자가 클릭
  4) save_subject(): 고른 추적선의 프레임별 상자를 클립 정보(meta["subject"])에 저장 -> 모션 뽑기가 이 사람의 모션만 쓴다

점수는 '순위를 매기는 용도'예요. 절대적인 정답 확률이 아니라서, 1·2등 차이가 작으면 애매하다고 보고 물어봅니다.
"""
import json
import math
import time
from pathlib import Path

import numpy as np

from . import Cancelled, media, noop
from . import library
from .library import file_of, game_dir, safe_name

KINDS = {"humanoid": "인간형", "creature": "크리처 (몬스터 등)"}
REF_MAX_SIDE = 768            # 저장하는 참고 이미지의 긴 변
CROP_SIDE = 224               # 비교용 크롭 크기
W_CLIP, W_COLOR = 0.7, 0.3    # 점수 = 0.7*CLIP 유사도 + 0.3*색 유사도
AUTO_MARGIN = 0.06            # 1등이 2등보다 이만큼 높으면 자동 선택
MIN_SCORE = 0.40              # 이보다 낮으면 '닮은 사람이 없다'고 본다
MIN_TRACK_FRAMES = 6


# ---------------------------------------------------------------- 저장소
def char_root(s) -> Path:
    d = game_dir(s) / "characters"
    d.mkdir(parents=True, exist_ok=True)
    return d


def char_dir(s, name) -> Path:
    return char_root(s) / safe_name(name)


def list_characters(s):
    out = []
    for p in sorted(char_root(s).iterdir()):
        j = p / "character.json"
        if p.is_dir() and j.exists():
            try:
                c = json.loads(j.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            c["_dir"] = str(p)
            out.append(c)
    return out


def load_character(s, name):
    p = char_dir(s, name) / "character.json"
    if not p.exists():
        return None
    c = json.loads(p.read_text(encoding="utf-8"))
    c["_dir"] = str(p.parent)
    return c


def ref_images(c):
    return sorted(Path(c["_dir"]).glob("ref_*.jpg"))


# ---------------------------------------------------------------- 특징 추출
def _read_rgb(path):
    from PIL import Image

    im = Image.open(path).convert("RGB")
    return np.asarray(im)


def _resize_max(rgb, side):
    import cv2

    h, w = rgb.shape[:2]
    k = side / max(h, w)
    if k >= 1.0:
        return rgb
    return cv2.resize(rgb, (max(1, int(w * k)), max(1, int(h * k))), interpolation=cv2.INTER_AREA)


def _jpeg(rgb, q=88):
    import cv2

    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, q])
    return buf.tobytes() if ok else b""


def embed_images(rgbs, log=noop):
    """RGB 이미지 목록 -> (n, 512) L2 정규화 CLIP 임베딩. (시험에서는 이 함수를 바꿔 끼운다)"""
    from PIL import Image

    from . import classify

    c = classify.get_clip(log)
    torch = c["torch"]
    with torch.no_grad():
        x = torch.stack([c["pre"](classify._pad_square(Image.fromarray(r))) for r in rgbs]).to(c["dev"])
        fe = c["model"].encode_image(x).float()
        fe = fe / fe.norm(dim=-1, keepdim=True)
    return fe.cpu().numpy().astype(np.float32)


def color_hist(rgb, bins=(18, 8)):
    """색(색상·채도) 히스토그램. 가운데 영역만 쓰고 너무 어두운 픽셀은 뺀다. 합이 1."""
    import cv2

    h, w = rgb.shape[:2]
    y0, y1, x0, x1 = int(h * 0.15), int(h * 0.85), int(w * 0.2), int(w * 0.8)
    c = rgb[y0:y1, x0:x1]
    hsv = cv2.cvtColor(np.ascontiguousarray(c), cv2.COLOR_RGB2HSV)
    mask = (hsv[..., 2] > 40).astype(np.uint8)
    hist = cv2.calcHist([hsv], [0, 1], mask, list(bins), [0, 180, 0, 256]).astype(np.float64)
    tot = hist.sum()
    return (hist / tot).reshape(-1) if tot > 0 else np.zeros(bins[0] * bins[1])


def hist_sim(a, b):
    return float(np.minimum(a, b).sum())


def features(rgbs, log=noop):
    return {"clip": embed_images(rgbs, log), "hist": np.stack([color_hist(r) for r in rgbs])}


# ---------------------------------------------------------------- 등록
def create_character(s, name, kind="humanoid", detect_text="", image_paths=(), log=noop):
    """새 캐릭터를 만든다(이미 있으면 참고 이미지를 추가). 반환: 캐릭터 dict."""
    name = (name or "").strip()
    if not name:
        raise ValueError("캐릭터 이름을 입력해 주세요.")
    if kind not in KINDS:
        kind = "humanoid"
    d = char_dir(s, name)
    d.mkdir(parents=True, exist_ok=True)
    jp = d / "character.json"
    c = json.loads(jp.read_text(encoding="utf-8")) if jp.exists() else {"created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    c.update(name=name, kind=kind, detect_text=(detect_text or "").strip())
    n0 = len(list(d.glob("ref_*.jpg")))
    added = 0
    for p in image_paths:
        try:
            rgb = _resize_max(_read_rgb(p), REF_MAX_SIDE)
        except Exception as e:  # noqa: BLE001
            log(f"이미지를 읽지 못했어요 (건너뜀): {Path(p).name} ({e})")
            continue
        n0 += 1
        (d / f"ref_{n0:02d}.jpg").write_bytes(_jpeg(rgb, 92))
        added += 1
    if not list(d.glob("ref_*.jpg")):
        raise ValueError("참고 이미지가 한 장도 없어요. 캐릭터가 크게 보이는 이미지를 1장 이상 넣어 주세요.")
    c["images"] = len(list(d.glob("ref_*.jpg")))
    jp.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
    c["_dir"] = str(d)
    rebuild_features(c, log)
    return c


def rebuild_features(c, log=noop):
    imgs = [_read_rgb(p) for p in ref_images(c)]
    rgbs = imgs + [np.ascontiguousarray(im[:, ::-1]) for im in imgs]      # 좌우 뒤집은 것도 같이 (캐릭터 방향 무관)
    f = features(rgbs, log)
    np.savez(Path(c["_dir"]) / "refs.npz", clip=f["clip"], hist=f["hist"])


def remove_image(c, path):
    Path(path).unlink(missing_ok=True)
    rest = ref_images(c)
    if not rest:
        raise ValueError("마지막 참고 이미지는 지울 수 없어요. 캐릭터를 통째로 지우려면 [캐릭터 삭제]를 쓰세요.")
    c["images"] = len(rest)
    (Path(c["_dir"]) / "character.json").write_text(json.dumps({k: v for k, v in c.items() if k != "_dir"}, ensure_ascii=False, indent=1), encoding="utf-8")


def delete_character(c):
    import shutil

    shutil.rmtree(c["_dir"], ignore_errors=True)


def load_refs(c, log=noop):
    p = Path(c["_dir"]) / "refs.npz"
    if not p.exists():
        rebuild_features(c, log)
    z = np.load(p)
    return {"clip": z["clip"], "hist": z["hist"]}


# ---------------------------------------------------------------- 후보 추적선
def _crop(rgb, box, margin=0.08):
    h, w = rgb.shape[:2]
    x1, y1, x2, y2 = box
    bw, bh = x2 - x1, y2 - y1
    x1, x2 = x1 - bw * margin, x2 + bw * margin
    y1, y2 = y1 - bh * margin, y2 + bh * margin
    x1, y1, x2, y2 = int(max(0, x1)), int(max(0, y1)), int(min(w, x2)), int(min(h, y2))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    return rgb[y1:y2, x1:x2]


def pick_samples(pts, per_track=5):
    """추적선 한 개에서 비교용 시점을 고른다: 시간을 per_track 구간으로 나눠 각 구간에서 상자가 가장 큰 것."""
    if not pts:
        return []
    n = len(pts)
    k = min(per_track, n)
    edges = np.linspace(0, n, k + 1).astype(int)
    out = []
    for a, b in zip(edges[:-1], edges[1:]):
        if b <= a:
            continue
        seg = pts[a:b]
        out.append(max(seg, key=lambda p: (p["box"][2] - p["box"][0]) * (p["box"][3] - p["box"][1])))
    return out


def grab_crops(video, wanted, width=960, fps=4):
    """wanted: [(키, 시간, 상자(원본 픽셀))]. 영상을 한 번만 훑어서 그 시점의 크롭(RGB)을 돌려준다. {키: RGB 또는 None}"""
    info = media.probe(video)
    w = media.even(min(width, info.w))
    h = media.even(info.h * w / info.w)
    k = w / info.w
    need = {}
    for key, t, box in wanted:
        need.setdefault(max(0, int(round(t * fps))), []).append((key, [v * k for v in box]))
    out = {key: None for key, _, _ in wanted}
    if not need:
        return out
    last = max(need)
    for i, (t, frame) in enumerate(media.iter_frames(video, fps, w, h, pix_fmt="rgb24")):
        if i in need:
            for key, box in need[i]:
                c = _crop(frame, box)
                if c is not None:
                    out[key] = _resize_max(c, CROP_SIDE * 2)
        if i >= last:
            break
    return out


def build_candidates(series, min_frames=MIN_TRACK_FRAMES):
    """track_video 의 series -> 후보 목록(길이순). 상자는 [t,x1,y1,x2,y2] 목록."""
    cands = []
    for tid, pts in series.items():
        if len(pts) < min_frames:
            continue
        pts = sorted(pts, key=lambda p: p["t"])
        boxes = [[round(p["t"], 3)] + [round(float(v), 1) for v in p["box"]] for p in pts]
        area = float(np.mean([(p["box"][2] - p["box"][0]) * (p["box"][3] - p["box"][1]) for p in pts]))
        cands.append({"tid": int(tid), "n": len(pts), "t0": boxes[0][0], "t1": boxes[-1][0], "boxes": boxes,
                      "area": area, "score": None, "clip": None, "color": None, "thumb": b"", "samples": pick_samples(pts)})
    cands.sort(key=lambda c: -c["n"])
    return cands


def score_candidates(cands, crops_by_key, refs, log=noop):
    """각 후보의 크롭들을 참고 이미지와 비교해서 score/clip/color/thumb 를 채운다."""
    keys, rgbs = [], []
    for c in cands:
        for si, _ in enumerate(c["samples"]):
            r = crops_by_key.get((c["tid"], si))
            if r is not None:
                keys.append((c["tid"], si))
                rgbs.append(r)
    if not rgbs:
        return cands
    f = features(rgbs, log) if refs is not None else None
    per = {}
    for i, key in enumerate(keys):
        if refs is None:
            per.setdefault(key[0], []).append((0.0, 0.0, i))
            continue
        sc = float((f["clip"][i] @ refs["clip"].T).max())
        sh = max(hist_sim(f["hist"][i], h) for h in refs["hist"])
        per.setdefault(key[0], []).append((sc, sh, i))
    for c in cands:
        items = per.get(c["tid"], [])
        if not items:
            c["score"] = 0.0
            continue
        c["clip"] = float(np.mean([x[0] for x in items]))
        c["color"] = float(np.mean([x[1] for x in items]))
        c["score"] = W_CLIP * c["clip"] + W_COLOR * c["color"] if refs is not None else 0.0
        best = max(items, key=lambda x: (x[0] * W_CLIP + x[1] * W_COLOR))
        c["thumb"] = _jpeg(_resize_max(rgbs[best[2]], 200), 85)
    return cands


def decide(cands, has_refs=True):
    """(선택된 후보 또는 None, 상태). 상태: auto / ambiguous / none / empty.
    참고 이미지가 없으면(has_refs=False) 가장 오래 나온 후보를 'auto' 로 고른다(지금까지의 동작)."""
    if not cands:
        return None, "empty"
    if not has_refs:
        return cands[0], "auto"
    ranked = sorted(cands, key=lambda c: -(c["score"] or 0.0))
    best = ranked[0]
    if (best["score"] or 0.0) < MIN_SCORE:
        return None, "none"
    if len(ranked) == 1:
        return best, "auto"
    if (best["score"] or 0.0) - (ranked[1]["score"] or 0.0) >= AUTO_MARGIN:
        return best, "auto"
    return None, "ambiguous"


# ---------------------------------------------------------------- VLM 확인
def contact_for_vlm(ref_rgb, cands, tile=224):
    """왼쪽에 참고 이미지, 오른쪽에 번호 붙은 후보들을 놓은 한 장짜리 이미지(JPEG bytes)."""
    import cv2

    def fit(rgb):
        rgb = _resize_max(rgb, tile)
        canvas = np.full((tile, tile, 3), 40, np.uint8)
        h, w = rgb.shape[:2]
        canvas[(tile - h) // 2:(tile - h) // 2 + h, (tile - w) // 2:(tile - w) // 2 + w] = rgb
        return canvas
    tiles = [fit(ref_rgb)]
    cv2.putText(tiles[0], "REF", (6, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2, cv2.LINE_AA)
    for i, c in enumerate(cands, 1):
        t = c.get("_rgb")
        t = fit(t) if t is not None else np.zeros((tile, tile, 3), np.uint8)
        cv2.rectangle(t, (0, 0), (40, 32), (0, 0, 0), -1)
        cv2.putText(t, str(i), (8, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
        tiles.append(t)
    return _jpeg(np.hstack(tiles), 85)


def vlm_verify(s, c, cands, log=noop, ask=None):
    """애매할 때 VLM 에게 '참고 이미지와 같은 캐릭터는 몇 번?'을 묻는다. 반환: (후보 또는 None, 설명). 실패하면 (None, 이유)."""
    from . import vlm

    ask = ask or vlm.choose_among
    refs = ref_images(c)
    if not refs:
        return None, "참고 이미지가 없어요."
    top = sorted([x for x in cands if x.get("thumb")], key=lambda x: -(x["score"] or 0.0))[:4]
    if len(top) < 2:
        return None, "비교할 후보가 부족해요."
    import cv2

    for x in top:
        arr = cv2.imdecode(np.frombuffer(x["thumb"], np.uint8), cv2.IMREAD_COLOR)
        x["_rgb"] = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
    img = contact_for_vlm(_read_rgb(refs[0]), top)
    desc = c.get("detect_text") or c["name"]
    prompt = ("The first tile (REF) is a reference image of a video-game character"
              f" ({desc}). The other tiles are numbered candidate characters cropped from a gameplay video. "
              "Which numbered candidate is the SAME character as REF (same outfit, colors, body shape)? "
              "Answer 0 if none of them is the same character. Write `reason` as one short sentence in Korean.")
    try:
        choice, conf, reason = ask(s, prompt, img, len(top))
    except Exception as e:  # noqa: BLE001
        return None, f"VLM 확인을 못 했어요: {e}"
    if choice == 0 or choice > len(top):
        return None, f"VLM: 같은 캐릭터가 없다고 해요 ({reason})"
    if conf < 0.5:
        return None, f"VLM이 자신 없어 해요 ({reason})"
    return top[choice - 1], f"VLM이 {choice}번으로 골랐어요: {reason}"


# ---------------------------------------------------------------- 메인: 클립에서 대상 찾기
def locate(meta, s, character=None, use_vlm=True, log=noop, progress=noop, cancel=None, tracker=None):
    """Job용. 클립 하나에서 대상 캐릭터를 찾는다.
    character: 캐릭터 dict 또는 None(참고 이미지 없이 '가장 오래 나온 후보'를 고름).
    반환: {"candidates": [...], "chosen": 후보|None, "status": str, "how": str, "video_size": [w,h]}"""
    from . import segment

    video = file_of(meta, "video")
    info = media.probe(video)
    s2 = dict(s)
    if character and character.get("detect_text"):
        extra = [t for t in segment.parse_targets(s.get("targets", "")) if t != character["detect_text"]]
        s2["targets"] = ", ".join(extra + [character["detect_text"]])
    track = tracker or segment.track_video
    progress(0.02, "인물 찾는 중...")
    series, fps, info2, _cues = track(str(video), s2, log=log, progress=lambda f, t: progress(0.02 + 0.6 * f, t), cancel=cancel)
    cands = build_candidates(series)
    if cancel is not None and cancel.is_set():
        raise Cancelled()
    refs = load_refs(character, log) if character else None
    if cands:
        progress(0.65, "후보를 비교하는 중...")
        wanted = [((c["tid"], si), p["t"], p["box"]) for c in cands for si, p in enumerate(c["samples"])]
        crops = grab_crops(str(video), wanted)
        score_candidates(cands, crops, refs, log)
        if refs is None:                       # 참고 이미지가 없으면 썸네일만 만들어 둔다 (직접 고를 수 있게)
            for c in cands:
                for si in range(len(c["samples"])):
                    r = crops.get((c["tid"], si))
                    if r is not None:
                        c["thumb"] = _jpeg(_resize_max(r, 200), 85)
                        break
    chosen, status = decide(cands, has_refs=refs is not None)
    how = "auto" if chosen else ""
    if chosen is None and status == "ambiguous" and character and use_vlm:
        progress(0.9, "애매해서 AI에게 확인하는 중...")
        v, why = vlm_verify(s, character, cands, log)
        log(why)
        if v is not None:
            chosen, status, how = v, "auto", "vlm"
    for c in cands:
        c.pop("_rgb", None)
    progress(1.0, "완료")
    return {"candidates": cands, "chosen": chosen, "status": status, "how": how,
            "video_size": [info.w, info.h], "fps": fps}


def locate_batch(metas, s, character_name="", use_vlm=True, log=noop, progress=noop, cancel=None, tracker=None):
    """Job용. 클립 여러 개에서 대상을 찾는다. 확실한 것은 바로 저장하고, 애매한 것은 사용자가 고르도록 돌려 준다.
    character_name 이 비어 있으면(등록 캐릭터 없음) 사람이 한 명뿐인 클립만 자동으로 저장한다.
    반환: {"auto": [클립이름], "pending": [(meta, 결과)], "failed": [(클립이름, 이유)]}"""
    character = load_character(s, character_name) if character_name else None
    if character_name and character is None:
        raise RuntimeError(f"'{character_name}' 캐릭터를 찾지 못했어요. 캐릭터 등록에서 다시 확인해 주세요.")
    auto, pending, failed, auto_entries = [], [], [], []
    n = max(1, len(metas))
    for i, m in enumerate(metas):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        name = Path(m["_json"]).stem
        base, span = i / n, 1.0 / n
        try:
            res = locate(m, s, character, use_vlm=use_vlm, log=log, cancel=cancel, tracker=tracker,
                         progress=lambda f, t, b=base, sp=span, k=i: progress(b + sp * f, f"({k + 1}/{n}) {t}"))
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            log(f"[대상 찾기] {name} 실패: {e}")
            failed.append((name, str(e)))
            continue
        chosen = res["chosen"]
        if chosen is not None and character is None and len(res["candidates"]) > 1:
            chosen = None                       # 캐릭터 없이는 '가장 오래 나온 사람'을 멋대로 고르지 않는다
            res["status"] = "ambiguous"
        if chosen is not None:
            save_subject(m, chosen, character_name, res["how"] or "auto", res["video_size"])
            auto.append(name)
            auto_entries.append((m, res))
        else:
            pending.append((m, res))
    progress(1.0, "완료")
    return {"auto": auto, "auto_entries": auto_entries, "pending": pending, "failed": failed}


# ---------------------------------------------------------------- 결과 저장 / 읽기
def subject_of(cand, character_name, how, video_size):
    """후보 하나를 클립에 저장할 형태로. 상자는 [t,x1,y1,x2,y2]."""
    return {"character": character_name or "", "tid": int(cand["tid"]), "boxes": cand["boxes"],
            "score": None if cand.get("score") is None else round(float(cand["score"]), 3),
            "how": how or "user", "video_size": list(video_size), "created": time.strftime("%Y-%m-%dT%H:%M:%S")}


def save_subject(meta, cand, character_name, how, video_size):
    meta["subject"] = subject_of(cand, character_name, how, video_size)
    library.save_clip(meta)
    return meta["subject"]


def clear_subject(meta):
    meta.pop("subject", None)
    library.save_clip(meta)


def describe_status(status, how=""):
    return {"auto": "대상을 찾았어요" + (" (AI가 확인)" if how == "vlm" else ""),
            "ambiguous": "닮은 후보가 여럿이라 직접 골라 주세요",
            "none": "등록한 캐릭터와 닮은 사람을 못 찾았어요 (직접 고르거나 참고 이미지를 더 추가해 보세요)",
            "empty": "영상에서 사람(캐릭터)을 하나도 찾지 못했어요"}.get(status, status)


def crop_window(boxes, size, margin=0.3):
    """대상 상자 전체를 덮는 고정 크롭 창 (x, y, w, h), 짝수 크기. size=(W,H)."""
    W, H = size
    xs1 = min(b[1] for b in boxes)
    ys1 = min(b[2] for b in boxes)
    xs2 = max(b[3] for b in boxes)
    ys2 = max(b[4] for b in boxes)
    w, h = xs2 - xs1, ys2 - ys1
    x1, y1 = xs1 - w * margin, ys1 - h * margin
    x2, y2 = xs2 + w * margin, ys2 + h * margin
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(W, x2), min(H, y2)
    cw = int(max(64, (x2 - x1)) // 2 * 2)
    ch = int(max(64, (y2 - y1)) // 2 * 2)
    cw, ch = min(cw, W // 2 * 2), min(ch, H // 2 * 2)
    cx = int(min(max(0, x1), W - cw)) // 2 * 2
    cy = int(min(max(0, y1), H - ch)) // 2 * 2
    return cx, cy, cw, ch


def shift_boxes(boxes, dx, dy):
    return [[b[0], b[1] - dx, b[2] - dy, b[3] - dx, b[4] - dy] for b in boxes]


# ---------------------------------------------------------------- 대상을 따라가며 확대 (AI 모캡이 작은 캐릭터를 놓치지 않게)
FOLLOW_OUT = 768              # 확대 영상의 한 변 (정사각형)
FOLLOW_MARGIN = 2.4           # 크롭 한 변 = 대상 키 × 2.4 (대상이 화면 높이의 약 40%)
FOLLOW_MIN_SIDE = 160         # 너무 작게 자르면 흐려져서 이보다 작게는 안 자른다


def _smooth(a, win):
    """이동 평균 (가장자리는 같은 값으로 늘려서 끝이 쏠리지 않게)."""
    a = np.asarray(a, dtype=float)
    win = int(max(1, win)) | 1
    if win <= 1 or len(a) < 2:
        return a
    pad = win // 2
    ap = np.pad(a, pad, mode="edge")
    return np.convolve(ap, np.ones(win) / win, mode="valid")[: len(a)]


def _running_max(a, win):
    a = np.asarray(a, dtype=float)
    r = int(max(0, win // 2))
    if r == 0:
        return a
    ap = np.pad(a, r, mode="edge")
    return np.array([ap[i:i + 2 * r + 1].max() for i in range(len(a))])


def follow_crop_plan(boxes, fps, n_frames, size, out=FOLLOW_OUT, margin=FOLLOW_MARGIN, smooth_s=0.5):
    """대상 상자([t,x1,y1,x2,y2], 듬성듬성)로 프레임마다의 정사각 크롭 창을 계산한다.
    반환: {"x0","y0","side": 프레임별 배열, "boxes": 크롭 영상 좌표의 프레임별 상자 [t,x1,y1,x2,y2]}"""
    W, H = size
    bx = np.asarray(boxes, dtype=float)
    order = np.argsort(bx[:, 0])
    bx = bx[order]
    t = np.arange(n_frames) / float(fps)
    cx = np.interp(t, bx[:, 0], (bx[:, 1] + bx[:, 3]) / 2)
    cy = np.interp(t, bx[:, 0], (bx[:, 2] + bx[:, 4]) / 2)
    bw = np.interp(t, bx[:, 0], bx[:, 3] - bx[:, 1])
    bh = np.interp(t, bx[:, 0], bx[:, 4] - bx[:, 2])
    side = np.maximum(bh * margin, bw * 1.9)
    win = max(1, int(round(smooth_s * fps)))
    side = _smooth(_running_max(side, win * 2), win * 2)            # 확대 배율이 출렁이지 않게 '최대값→평균'
    cx, cy = _smooth(cx, win), _smooth(cy, win)
    smax = float(min(W, H))
    side = np.clip(side, min(FOLLOW_MIN_SIDE, smax), smax)
    x0 = np.clip(cx - side / 2, 0, W - side)
    y0 = np.clip(cy - side / 2, 0, H - side)
    k = out / side
    ob = []
    for i in range(n_frames):
        x1 = float(np.interp(t[i], bx[:, 0], bx[:, 1]))
        y1 = float(np.interp(t[i], bx[:, 0], bx[:, 2]))
        x2 = float(np.interp(t[i], bx[:, 0], bx[:, 3]))
        y2 = float(np.interp(t[i], bx[:, 0], bx[:, 4]))
        ob.append([round(float(t[i]), 4), (x1 - x0[i]) * k[i], (y1 - y0[i]) * k[i], (x2 - x0[i]) * k[i], (y2 - y0[i]) * k[i]])
    return {"x0": x0, "y0": y0, "side": side, "boxes": ob, "out": out}


def render_follow_crop(video, boxes, out_path, log=noop, cancel=None, out=FOLLOW_OUT):
    """대상을 따라가며 확대한 짧은 영상(소리 없음)을 만든다. 반환: (크롭 영상 좌표의 상자, (out,out), 프레임 수)"""
    import subprocess

    import cv2

    info = media.probe(video)
    fps = float(info.fps or 30.0)
    n = max(2, int(round(info.duration * fps)) + 1)
    plan = follow_crop_plan(boxes, fps, n, (info.w, info.h), out=out)
    side_min, side_max = float(plan["side"].min()), float(plan["side"].max())
    log(f"대상을 따라가며 확대: 원본 {info.w}x{info.h} → {out}x{out} (확대 {out / side_max:.1f}~{out / side_min:.1f}배)")
    cmd = [media.ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{out}x{out}", "-r", f"{fps:.4f}", "-i", "-", "-an", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "16", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=getattr(media, "_NO_WINDOW", 0))
    written = 0
    try:
        for i, (_t, frame) in enumerate(media.iter_frames(video, fps, info.w, info.h, pix_fmt="rgb24")):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            j = min(i, n - 1)
            x0, y0, sd = int(round(plan["x0"][j])), int(round(plan["y0"][j])), int(round(plan["side"][j]))
            x0, y0 = max(0, min(x0, info.w - sd)), max(0, min(y0, info.h - sd))
            c = frame[y0:y0 + sd, x0:x0 + sd]
            interp = cv2.INTER_CUBIC if sd < out else cv2.INTER_AREA
            c = cv2.resize(c, (out, out), interpolation=interp)
            proc.stdin.write(np.ascontiguousarray(c).tobytes())
            written += 1
    finally:
        try:
            proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        proc.wait()
    if written < 2 or proc.returncode != 0:
        raise RuntimeError("대상 확대 영상을 만들지 못했어요: " + err.strip()[-300:])
    return plan["boxes"][:written], (out, out), written


# ---------------------------------------------------------------- 선택 확인용 미리보기
def preview_jpeg(video, boxes, label="", width=720):
    """대상 상자를 그린 장면(JPEG 바이트). 상자가 가장 큰 시점의 프레임을 쓴다."""
    import cv2

    info = media.probe(video)
    b = max(boxes, key=lambda b: (b[3] - b[1]) * (b[4] - b[2]))
    w = media.even(min(width, info.w))
    h = media.even(info.h * w / info.w)
    k = w / info.w
    frame = None
    for _t, f in media.iter_frames(video, 10, w, h, start=max(0.0, b[0] - 0.05), length=0.3, pix_fmt="bgr24"):
        frame = f
        break
    if frame is None:
        return b""
    x1, y1, x2, y2 = [int(v * k) for v in b[1:5]]
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 0), 5)
    cv2.rectangle(frame, (x1, y1), (x2, y2), (80, 220, 90), 3)
    if label:
        cv2.rectangle(frame, (x1, max(0, y1 - 26)), (min(w - 1, x1 + 8 + 14 * len(label)), y1), (80, 220, 90), -1)
        cv2.putText(frame, label.encode("ascii", "ignore").decode() or "TARGET", (x1 + 4, max(14, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return buf.tobytes() if ok else b""
