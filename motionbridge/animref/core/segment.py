"""영상만 보고 캐릭터별 액션 구간을 찾아 클립으로 잘라낸다.

1) YOLO-World로 캐릭터(아군/적 구분 없이) 탐지 + ByteTrack 추적
2) 옵티컬 플로우에서 카메라 이동(배경 움직임)을 빼고, 캐릭터 박스 안의 '몸 움직임 에너지' 계산
3) 에너지가 큰 구간 = 액션 -> 앞뒤 여유를 두고 잘라 저장
"""
import json
import math
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from . import Cancelled, media, noop
from .signals import FrameCues
from .library import game_dir, safe_name
from .settings import SPEED_PRESETS, models_dir, percentile

_DETECTOR = {"key": None, "model": None}


def parse_targets(text):
    t = [x.strip() for x in str(text).replace("，", ",").split(",") if x.strip()]
    return t or ["person"]


def _use_writable_dirs():
    """ultralytics는 모델을 현재 폴더 기준의 'weights' 폴더(상대 경로)로 내려받는다.
    현재 폴더에 쓸 권한이 없으면 'PermissionError: weights'가 나므로, 모든 경로를 쓸 수 있는 AnimRef 폴더로 고정한다."""
    try:
        from ultralytics import settings as ul

        from .settings import app_dir

        ul.update({"weights_dir": str(models_dir()), "datasets_dir": str(app_dir() / "datasets"),
                   "runs_dir": str(app_dir() / "runs")})
    except Exception:
        pass


def get_detector(s, log=noop):
    targets = tuple(parse_targets(s.get("targets", "")))
    key = (s.get("detector"), targets)
    if _DETECTOR["key"] != key:
        log("캐릭터 탐지 AI를 불러오는 중... (처음 한 번은 모델을 내려받느라 몇 분 걸릴 수 있어요)")
        from ultralytics import YOLOWorld

        _use_writable_dirs()
        model = YOLOWorld(str(models_dir() / s.get("detector", "yolov8s-worldv2.pt")))
        model.set_classes(list(targets))
        _DETECTOR.update(key=key, model=model)
    model = _DETECTOR["model"]
    model.predictor = None  # 영상마다 추적기 초기화
    return model


def _fmt_eta(sec):
    if sec < 60:
        return f"{int(sec)}초"
    return f"{int(sec // 60)}분 {int(sec % 60)}초"


def track_video(path, s, log=noop, progress=noop, cancel=None, f0=0.0, f1=1.0):
    """트랙별 [{t, e(움직임 에너지), box}] 시계열을 만든다."""
    import cv2

    info = media.probe(path)
    det_w, fps = SPEED_PRESETS.get(s.get("speed", "normal"), SPEED_PRESETS["normal"])
    det_w = media.even(min(det_w, info.w))
    det_h = media.even(info.h * det_w / info.w)
    scale = info.w / det_w
    FW = 192
    fh = media.even(det_h * FW / det_w)
    kx, ky = FW / det_w, fh / det_h

    model = get_detector(s, log)
    kw = dict(persist=True, tracker="bytetrack.yaml", conf=float(s.get("conf", 0.25)),
              verbose=False, imgsz=det_w)
    total = max(1, int(info.duration * fps))
    name = Path(path).name
    series, prev = {}, None
    cues = FrameCues()
    t_start = time.time()

    for i, (t, frame) in enumerate(media.iter_frames(path, fps, det_w, det_h)):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        gray = cv2.cvtColor(cv2.resize(frame, (FW, fh), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        flow = None
        if prev is not None:
            flow = cv2.calcOpticalFlowFarneback(prev, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        prev = gray
        cues.add(t, frame, gray)

        res = model.track(frame, **kw)[0]
        if i % 10 == 0:
            frac = min(1.0, i / total)
            el = time.time() - t_start
            eta = el / frac * (1 - frac) if frac > 0.02 else None
            msg = f"영상 분석 중: {name}  {frac:.0%}"
            if eta is not None:
                msg += f"  · 남은 시간 약 {_fmt_eta(eta)}"
            progress(f0 + (f1 - f0) * frac, msg)

        b = res.boxes
        if flow is None or b is None or b.id is None:
            continue
        ids = b.id.int().cpu().tolist()
        xyxy = b.xyxy.cpu().numpy()

        mask = np.ones((fh, FW), bool)
        fboxes = []
        for bb in xyxy:
            x1, y1, x2, y2 = (bb * np.array([kx, ky, kx, ky])).astype(int)
            x1, y1, x2, y2 = max(x1, 0), max(y1, 0), min(x2, FW), min(y2, fh)
            fboxes.append((x1, y1, x2, y2))
            mask[y1:y2, x1:x2] = False
        bg = flow[mask] if mask.sum() > 200 else flow.reshape(-1, 2)
        cam = np.median(bg, axis=0)  # 카메라 이동 = 배경 플로우 중앙값

        for tid, bb, (x1, y1, x2, y2) in zip(ids, xyxy, fboxes):
            if x2 - x1 < 4 or y2 - y1 < 4:
                continue
            rel = flow[y1:y2, x1:x2] - cam
            energy = float(np.linalg.norm(rel, axis=2).mean() / (y2 - y1))
            series.setdefault(int(tid), []).append({"t": t, "e": energy, "box": (bb * scale).tolist()})
    return series, fps, info, cues.arrays()


def cinematic_segments(cues, info, min_len=1.0, max_s=4.0):
    """레터박스가 1초 이상 이어지는 구간 = 연출 후보 (캐릭터가 안 잡혀도)."""
    t, _, lb, _ = cues
    segs, start = [], None
    for i in range(len(t)):
        if lb[i] and start is None:
            start = float(t[i])
        if (not lb[i] or i == len(t) - 1) and start is not None:
            end = float(t[i])
            if end - start >= min_len:
                n = max(1, math.ceil((end - start) / max_s))
                for k in range(n):
                    a = start + (end - start) * k / n
                    segs.append({"tid": -1, "t0": a, "t1": a + (end - start) / n, "peak": 0.0,
                                 "box": [0.0, 0.0, float(info.w), float(info.h)]})
            start = None
    return segs


def segment_series(series, s, eff_fps, floor=0.008, min_track_frames=8):
    tracks = {k: v for k, v in series.items() if len(v) >= min_track_frames}
    if not tracks:
        return [], 0.0
    all_e = np.concatenate([[p["e"] for p in v] for v in tracks.values()])
    thr = max(float(np.percentile(all_e, percentile(s))), floor)
    gap_s, min_s, max_s = 0.3, float(s.get("min_s", 0.3)), float(s.get("max_s", 8.0))
    k = max(1, int(eff_fps * 0.2))

    segs = []
    for tid, v in tracks.items():
        t = np.array([p["t"] for p in v])
        e = np.convolve(np.array([p["e"] for p in v]), np.ones(k) / k, mode="same")
        found, cur = [], None
        for j in range(len(t)):
            if e[j] <= thr:
                continue
            if cur is not None and t[j] - t[cur["j1"]] <= gap_s:
                cur["j1"] = j
                cur["peak"] = max(cur["peak"], float(e[j]))
            else:
                if cur:
                    found.append(cur)
                cur = {"j0": j, "j1": j, "peak": float(e[j])}
        if cur:
            found.append(cur)
        # 동작의 시작(예비동작)과 끝(후속동작)까지 포함: 약한 움직임이 이어지는 동안 앞뒤로 넓힌다
        low = thr * 0.45
        for c in found:
            j0, j1 = c["j0"], c["j1"]
            while j0 > 0 and e[j0 - 1] > low and t[j0] - t[j0 - 1] < 0.5:
                j0 -= 1
            while j1 < len(t) - 1 and e[j1 + 1] > low and t[j1 + 1] - t[j1] < 0.5:
                j1 += 1
            c["t0"], c["t1"] = float(t[j0]), float(t[j1])
        merged = []
        for c in sorted(found, key=lambda c: c["t0"]):
            if merged and c["t0"] <= merged[-1]["t1"] + gap_s:
                merged[-1]["t1"] = max(merged[-1]["t1"], c["t1"])
                merged[-1]["peak"] = max(merged[-1]["peak"], c["peak"])
            else:
                merged.append(c)
        found = merged
        for c in found:
            dur = c["t1"] - c["t0"]
            if dur < min_s:
                continue
            n = max(1, math.ceil(dur / max_s))
            for i in range(n):
                a, b = c["t0"] + dur * i / n, c["t0"] + dur * (i + 1) / n
                boxes = [p["box"] for p in v if a - 1e-6 <= p["t"] <= b + 1e-6]
                if not boxes:
                    continue
                arr = np.array(boxes)
                segs.append({"tid": int(tid), "t0": a, "t1": b, "peak": c["peak"],
                             "box": [float(arr[:, 0].min()), float(arr[:, 1].min()),
                                     float(arr[:, 2].max()), float(arr[:, 3].max())]})
    segs.sort(key=lambda x: x["t0"])
    return segs, thr


def crop_rect(box, w_img, h_img, margin):
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    x1, x2 = x1 - w * margin, x2 + w * margin
    y1, y2 = y1 - h * margin, y2 + h * margin
    x1, y1 = max(0, int(x1)), max(0, int(y1))
    x2, y2 = min(w_img, int(x2)), min(h_img, int(y2))
    w, h = max(16, (x2 - x1) // 2 * 2), max(16, (y2 - y1) // 2 * 2)
    x1, y1 = min(x1, max(0, w_img - w)), min(y1, max(0, h_img - h))
    return x1, y1, w, h


def write_clips(path, segs, info, s, view="full", log=noop, progress=noop, cancel=None, f0=0.0, f1=1.0):
    gdir = game_dir(s)
    cdir = gdir / "clips"
    stem = safe_name(Path(path).stem)[:40]
    pre, post = float(s.get("pre_pad", 0.5)), float(s.get("post_pad", 0.5))
    if view == "crop":
        segs = [sg for sg in segs if sg["tid"] >= 0]
    min_len = float(s.get("min_clip_len", 2.0))
    existing = existing_ranges(cdir, path, view)
    out = []
    skipped = 0
    for n, sg in enumerate(segs):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        progress(f0 + (f1 - f0) * n / max(1, len(segs)), f"클립 저장 중 {n + 1}/{len(segs)}")
        if sg.get("exact"):  # 한 타 클립: 이미 계산된 구간 그대로
            start, end = max(0.0, sg["t0"]), min(info.duration or sg["t1"], sg["t1"])
        else:
            start, end = clip_bounds(sg["t0"], sg["t1"], pre, post, min_len, info.duration)
        length = end - start
        if length <= 0.2:
            continue
        if overlaps_existing(start, end, existing, 0.85 if view == "hit" else 0.5):
            skipped += 1  # 이미 같은 장면의 클립이 있음
            continue
        if view != "hit":  # 한 타 클립끼리는 빠른 연타에서 조금 겹칠 수 있으므로 같은 실행 안에서는 비교하지 않음
            existing.append((start, end))
        tag = f"c{sg['tid']}" if sg["tid"] >= 0 else "cine"
        if view == "hit":
            tag = f"hit{sg.get('hit_index', 1)}of{sg.get('hit_count', 1)}"
        base = f"{stem}_{tag}_{int(sg['t0'] * 1000):08d}_{view}"
        name, k = base, 2
        while (cdir / f"{name}.mp4").exists() or (cdir / f"{name}.json").exists():
            name, k = f"{base}_{k}", k + 1  # 같은 영상을 다시 분석해도 기존 클립을 덮어쓰지 않음
        mp4 = cdir / f"{name}.mp4"
        vf = None
        if view == "crop":
            x, y, w, h = crop_rect(sg["box"], info.w, info.h, float(s.get("crop_margin", 0.3)))
            vf = f"crop={w}:{h}:{x}:{y}"
        media.cut(path, start, length, mp4, s.get("crf", 23), vf)
        media.thumbnail(mp4, (sg["t0"] + sg["t1"]) / 2 - start, cdir / f"{name}.thumb.jpg")
        meta = {
            "game": s["game"],
            "source_video": str(path),
            "source_name": Path(path).name,
            "track_id": sg["tid"],
            "track_ids": sg.get("tids", [sg["tid"]]),
            "t_range": [round(sg["t0"], 3), round(sg["t1"], 3)],
            "start_in_source": round(start, 3),
            "duration": round(length, 3),
            "action_offset": round(sg["t0"] - start, 3),
            "fps": info.fps,
            "view": view,
            "peak": round(sg["peak"], 4),
            "hints": sg.get("hints", {}),
            "hit_index": sg.get("hit_index"), "hit_count": sg.get("hit_count"),
            "hit_time": sg.get("hit_t"),
            "ai_label": None, "ai_conf": None, "ai_top": None, "ai_reason": None, "ai_engine": None,
            "label_manual": None, "group": None,
            "created": datetime.now().isoformat(timespec="seconds"),
        }
        jp = cdir / f"{name}.json"
        jp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        out.append(str(jp))
    if skipped:
        log(f"  이미 만들어 둔 장면 {skipped}개는 건너뜀 (중복 방지)")
    return out


def clip_bounds(t0, t1, pre, post, min_len, duration):
    """여유 시간을 붙이고, 너무 짧으면 최소 길이까지 앞뒤로 늘린다."""
    dur = duration or (t1 + post)
    start, end = max(0.0, t0 - pre), min(dur, t1 + post)
    if end - start < min_len:
        extra = (min_len - (end - start)) / 2
        start, end = max(0.0, start - extra), min(dur, end + extra)
        if end - start < min_len:  # 영상 처음/끝에 붙은 경우 반대쪽으로
            if start <= 0:
                end = min(dur, start + min_len)
            else:
                start = max(0.0, end - min_len)
    return start, end


def existing_ranges(cdir, source, view):
    """같은 원본 영상에서 이미 만든 클립들의 (시작, 끝) 목록."""
    src, name = str(source), Path(source).name
    out = []
    for jp in Path(cdir).glob("*.json"):
        try:
            m = json.loads(jp.read_text(encoding="utf-8"))
        except Exception:
            continue
        if m.get("view", "full") != view or not (jp.with_suffix(".mp4")).exists():
            continue
        if m.get("source_video") == src or m.get("source_name") == name:
            a = float(m.get("start_in_source", 0))
            out.append((a, a + float(m.get("duration", 0))))
    return out


def overlaps_existing(start, end, ranges, ratio=0.5):
    for a, b in ranges:
        inter = min(end, b) - max(start, a)
        if inter > 0 and inter >= ratio * min(end - start, b - a):
            return True
    return False


def merge_padded(segs, pre, post, max_len):
    """여유 시간을 붙였을 때 겹치는 구간은 한 클립으로 합친다 (거의 같은 클립이 연달아 생기는 것 방지)."""
    out = []
    for sg in sorted(segs, key=lambda x: x["t0"]):
        if out and (sg["t0"] - pre) <= (out[-1]["t1"] + post) and (sg["t1"] - out[-1]["t0"]) <= max_len:
            o = out[-1]
            o["t1"] = max(o["t1"], sg["t1"])
            o.setdefault("tids", [o["tid"]])
            if sg["tid"] not in o["tids"]:
                o["tids"].append(sg["tid"])
            if sg["peak"] > o["peak"]:
                o["peak"], o["tid"], o["box"] = sg["peak"], sg["tid"], sg["box"]
        else:
            out.append(dict(sg))
    return out


def make_manual_clip(path, t0, t1, s, label=None, log=noop):
    """사용자가 타임라인에서 직접 지정한 구간으로 클립을 만든다 (여유 시간 없이 그대로)."""
    info = media.probe(path)
    t0, t1 = max(0.0, float(t0)), min(float(t1), info.duration or float(t1))
    if t1 - t0 < 0.1:
        raise ValueError("구간이 너무 짧아요.")
    gdir = game_dir(s)
    cdir = gdir / "clips"
    stem = safe_name(Path(path).stem)[:40]
    base = f"{stem}_manual_{int(t0 * 1000):08d}_full"
    name, k = base, 2
    while (cdir / f"{name}.mp4").exists() or (cdir / f"{name}.json").exists():
        name, k = f"{base}_{k}", k + 1
    mp4 = cdir / f"{name}.mp4"
    media.cut(path, t0, t1 - t0, mp4, s.get("crf", 23))
    media.thumbnail(mp4, (t1 - t0) / 2, cdir / f"{name}.thumb.jpg")
    meta = {
        "game": s["game"], "source_video": str(path), "source_name": Path(path).name,
        "track_id": -1, "track_ids": [], "t_range": [round(t0, 3), round(t1, 3)],
        "start_in_source": round(t0, 3), "duration": round(t1 - t0, 3), "action_offset": 0.0,
        "fps": info.fps, "view": "full", "peak": 0.0, "hints": {}, "manual": True,
        "ai_label": None, "ai_conf": None, "ai_top": None, "ai_reason": None, "ai_engine": None,
        "label_manual": label or None, "group": None,
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    jp = cdir / f"{name}.json"
    jp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"수동 클립 저장: {name}.mp4 ({t1 - t0:.2f}초)")
    return str(jp)
