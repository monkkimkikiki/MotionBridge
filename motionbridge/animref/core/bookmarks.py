"""원본 영상별 책갈피: AI가 '클립으로 쓸 만한 동작'이라고 본 순간 + 사용자가 직접 단 책갈피.
<게임 폴더>/bookmarks/<영상이름>_<해시>.json 에 저장되고, 썸네일은 같은 폴더의 thumbs/ 에 저장된다.
사용자가 옮기거나 숨기거나 지운 내용은 다시 분석해도 유지된다."""
import hashlib
import json
import time
import uuid
from pathlib import Path

from . import Cancelled, media, noop
from .library import game_dir, safe_name

KIND_NAMES = {"auto": "AI 추천", "cine": "연출 후보", "weak": "약한 후보", "user": "내 책갈피"}


def _dir(s):
    d = game_dir(s) / "bookmarks"
    (d / "thumbs").mkdir(parents=True, exist_ok=True)
    return d


def path_for(s, source) -> Path:
    name = Path(source).name
    h = hashlib.md5(name.lower().encode("utf-8")).hexdigest()[:8]
    return _dir(s) / f"{safe_name(Path(source).stem)[:40]}_{h}.json"


def load(s, source):
    p = path_for(s, source)
    if not p.exists():
        return {"source": str(source), "items": [], "deleted": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        data.setdefault("items", [])
        data.setdefault("deleted", [])
        return data
    except Exception:
        return {"source": str(source), "items": [], "deleted": []}


def save(s, source, data):
    p = path_for(s, source)
    data["source"] = str(source)
    data["items"].sort(key=lambda b: b["t"])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def make_thumb(s, source, t):
    out = _dir(s) / "thumbs" / f"{uuid.uuid4().hex[:12]}.jpg"
    return str(out) if media.thumbnail(source, t, out, width=192) else ""


def new_item(s, source, t, t0=None, t1=None, kind="user", note="", thumb=True):
    t0 = t if t0 is None else t0
    t1 = t if t1 is None else t1
    return {
        "id": uuid.uuid4().hex[:10], "t": round(float(t), 3),
        "t0": round(float(t0), 3), "t1": round(float(t1), 3),
        "kind": kind, "hidden": False, "note": note,
        "thumb": make_thumb(s, source, t) if thumb else "",
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def _near(t, items, tol=0.6):
    return any(abs(t - b["t"]) < tol for b in items)


def add_from_segments(s, source, strong, weak=(), log=noop, progress=noop, cancel=None, f0=0.0, f1=1.0):
    """분석 결과로 책갈피를 추가한다. 이미 있는 책갈피(사용자가 수정/삭제한 것 포함) 근처에는 다시 만들지 않는다."""
    data = load(s, source)
    blocked = (data["items"] + [{"t": t} for t in data.get("deleted", [])]
               + [{"t": b["origin_t"]} for b in data["items"] if "origin_t" in b])
    todo = [(sg, "cine" if sg.get("tid", 0) < 0 else "auto") for sg in strong]
    todo += [(sg, "weak") for sg in weak]
    added = 0
    for n, (sg, kind) in enumerate(todo):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        progress(f0 + (f1 - f0) * n / max(1, len(todo)), f"책갈피 만드는 중 {n + 1}/{len(todo)}")
        t = (sg["t0"] + sg["t1"]) / 2
        if _near(t, blocked):
            continue
        h = sg.get("hints") or {}
        note = []
        if h.get("impact_sounds"):
            note.append(f"효과음 {h['impact_sounds']}")
        if h.get("flashes"):
            note.append(f"번쩍임 {h['flashes']}")
        if h.get("letterbox", 0) >= 0.5:
            note.append("레터박스")
        if h.get("cuts"):
            note.append(f"컷 {h['cuts']}")
        it = new_item(s, source, t, sg["t0"], sg["t1"], kind, " · ".join(note))
        data["items"].append(it)
        blocked.append(it)
        added += 1
    save(s, source, data)
    if added:
        log(f"  타임라인 책갈피 {added}개 추가")
    return added


def delete_item(s, source, data, item_id):
    """삭제한 위치는 기억해 두어 다시 분석해도 같은 자리에 책갈피가 생기지 않게 한다."""
    for b in list(data["items"]):
        if b["id"] == item_id:
            data["items"].remove(b)
            data.setdefault("deleted", []).append(b["t"])
            try:
                if b.get("thumb"):
                    Path(b["thumb"]).unlink(missing_ok=True)
            except OSError:
                pass
    save(s, source, data)


def move_item(s, source, data, item_id, new_t, refresh_thumb=True):
    for b in data["items"]:
        if b["id"] == item_id:
            b.setdefault("origin_t", b["t"])  # 원래 AI가 단 위치 (다시 분석해도 그 자리에 또 생기지 않게)
            d = float(new_t) - b["t"]
            b["t"], b["t0"], b["t1"] = round(b["t"] + d, 3), round(b["t0"] + d, 3), round(b["t1"] + d, 3)
            if refresh_thumb:
                old = b.get("thumb")
                b["thumb"] = make_thumb(s, source, b["t"])
                try:
                    if old:
                        Path(old).unlink(missing_ok=True)
                except OSError:
                    pass
    save(s, source, data)


def detect(source, s, log=noop, progress=noop, cancel=None):
    """편집기의 [모션 찾기]: 클립은 만들지 않고 책갈피만 만든다."""
    from .pipeline import find_segments

    by_view, info, weak = find_segments(source, s, log, progress, cancel, 0.0, 0.85, want_weak=True)
    strong = by_view.get("full") or by_view.get("crop") or []
    return add_from_segments(s, source, strong, weak, log, progress, cancel, 0.85, 1.0)
