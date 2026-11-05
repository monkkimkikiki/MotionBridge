"""라이브러리에 있는 클립 수정: 길이 조절(덮어쓰기), 새 클립으로 저장, 둘로 나누기.

원본 영상이 남아 있으면 원본에서 다시 잘라서 앞뒤로 늘릴 수도 있고,
원본이 없으면 클립 파일 안에서만 줄이거나 나눌 수 있다.
시간 값(t0, t1)은 편집기에서 연 영상(path) 기준이고, offset은 그 영상의 0초가 원본의 몇 초인지다."""
import json
import os
from datetime import datetime
from pathlib import Path

from . import media, noop
from .library import file_of, save_clip


def source_for(meta):
    """(편집에 쓸 영상 경로, 원본 기준 오프셋, 원본 여부)"""
    if meta.get("retimed"):   # 속도·이어붙이기가 구워진 클립은 원본 시간과 맞지 않으므로 클립 파일 안에서만 편집
        return str(file_of(meta, "video")), 0.0, False
    src = meta.get("source_video")
    if src and Path(src).exists():
        return str(src), 0.0, True
    return str(file_of(meta, "video")), float(meta.get("start_in_source", 0.0)), False


def clip_range_in(meta, offset, is_source):
    """편집기 기준으로 이 클립이 차지하는 구간."""
    a = float(meta.get("start_in_source", 0.0))
    d = float(meta.get("duration", 0.0))
    return (a, a + d) if is_source else (0.0, d)


def _invalidate_ai(meta):
    """내용이 바뀌었으니 AI 특징/판정을 다시 하도록 표시 (사용자가 고른 분류는 유지)."""
    for kind in ("emb", "sheet"):
        try:
            file_of(meta, kind).unlink(missing_ok=True)
        except OSError:
            pass
    for k in ("vlm_hash", "vlm_fail_hash", "clip_top"):
        meta.pop(k, None)


def replace_range(meta, path, offset, t0, t1, s, label="__keep__", log=noop):
    """클립을 새 구간으로 다시 잘라 덮어쓴다."""
    t0, t1 = max(0.0, float(t0)), float(t1)
    if t1 - t0 < 0.1:
        raise ValueError("구간이 너무 짧아요.")
    mp4 = file_of(meta, "video")
    tmp = mp4.with_name(mp4.stem + ".editing.mp4")
    media.cut(path, t0, t1 - t0, tmp, s.get("crf", 23))
    try:
        os.replace(tmp, mp4)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("클립 파일이 다른 프로그램에서 열려 있어서 바꾸지 못했어요. 닫고 다시 시도하세요.") from e
    start = offset + t0
    meta.update(start_in_source=round(start, 3), duration=round(t1 - t0, 3),
                t_range=[round(start, 3), round(offset + t1, 3)], action_offset=0.0,
                edited=datetime.now().isoformat(timespec="seconds"))
    if label != "__keep__":
        meta["label_manual"] = label or None
    _invalidate_ai(meta)
    media.thumbnail(mp4, (t1 - t0) / 2, file_of(meta, "thumb"))
    save_clip(meta)
    log(f"클립 수정: {mp4.name} ({t1 - t0:.2f}초)")
    return meta["_json"]


def new_from_range(meta, path, offset, t0, t1, s, label=None, log=noop, suffix="edit", extra=None):
    """같은 원본에서 새 클립을 만든다 (원래 클립은 그대로). extra: 새 클립 정보에 덧붙일 값."""
    t0, t1 = max(0.0, float(t0)), float(t1)
    if t1 - t0 < 0.1:
        raise ValueError("구간이 너무 짧아요.")
    base_old = Path(meta["_json"]).stem
    cdir = Path(meta["_json"]).parent
    base = f"{base_old}_{suffix}"
    name, k = base, 2
    while (cdir / f"{name}.mp4").exists() or (cdir / f"{name}.json").exists():
        name, k = f"{base}{k}", k + 1
    mp4 = cdir / f"{name}.mp4"
    media.cut(path, t0, t1 - t0, mp4, s.get("crf", 23))
    media.thumbnail(mp4, (t1 - t0) / 2, cdir / f"{name}.thumb.jpg")
    start = offset + t0
    new = {k: v for k, v in meta.items() if not k.startswith("_")}
    for k in ("vlm_hash", "vlm_fail_hash", "clip_top", "vlm_label", "vlm_conf", "vlm_reason",
              "ai_label", "ai_conf", "ai_top", "ai_reason", "ai_engine", "group", "hit_index", "hit_count", "hit_time"):
        new.pop(k, None)
    new.update(start_in_source=round(start, 3), duration=round(t1 - t0, 3),
               t_range=[round(start, 3), round(offset + t1, 3)], action_offset=0.0,
               manual=True, label_manual=label or None, edited_from=base_old,
               created=datetime.now().isoformat(timespec="seconds"))
    new.update(extra or {})
    jp = cdir / f"{name}.json"
    jp.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"새 클립 저장: {name}.mp4 ({t1 - t0:.2f}초)")
    return str(jp)


def split(meta, path, offset, t0, t_split, t1, s, log=noop):
    """재생 위치에서 둘로 나눈다: 앞부분은 이 클립(덮어쓰기), 뒷부분은 새 클립."""
    if not (t0 + 0.1 < t_split < t1 - 0.1):
        raise ValueError("나눌 위치가 클립의 시작이나 끝에 너무 가까워요.")
    # 클립 파일 자체를 편집 중이면 덮어쓰기 전에 뒷부분부터 잘라야 한다
    second = new_from_range(meta, path, offset, t_split, t1, s, meta.get("label_manual"), log, suffix="part2")
    replace_range(meta, path, offset, t0, t_split, s, log=log)
    return meta["_json"], second
