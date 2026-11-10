"""장면 고르기: 영상에서 '캐릭터가 잘 보이는' 장면만 뽑는다. (균등 간격으로 뽑으면 검은 화면·메뉴·정지 화면이 섞여 AI 가 헛다리를 짚는다.)

방법 (OpenCV 만 사용, 모델 없음)
  1) 후보를 넉넉히(원하는 장수의 4배, 12~40장) 고르게 뽑는다. 후보마다 0.5초 간격으로 3장을 짧게 뽑아 가운데 장을 쓰고, 앞뒤 장으로 '움직임'을 잰다.
  2) 점수 = 잘 보임(밝은 곳 상위 1% 밝기 + 명암 편차) + 세부(가운데 영역 윤곽선) + 움직임.
       검은 화면(상위 1% 밝기가 아주 낮음)은 탈락, 0.5초 동안 거의 안 변하는 화면(메뉴·일시정지·컷 사이)은 점수를 깎는다.
  3) 점수 높은 순으로 고르되, 시간이 너무 가깝거나 거의 같은 그림(8x8 평균 해시가 비슷)은 건너뛰어 서로 다른 장면을 모은다.
Qt 에 의존하지 않는다. ffmpeg 호출은 run 으로 바꿔 끼울 수 있다.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

import numpy as np

from . import frames as F

MIN_P99 = 45.0        # 밝은 곳 상위 1% 밝기가 이보다 낮으면 검은 화면
MIN_STD = 6.0         # 명암 편차가 이보다 낮으면 빈 화면
STATIC_DIFF = 1.0     # 0.5초 사이 평균 밝기 변화가 이보다 작으면 정지 화면
MIN_HASH_DIST = 5     # 서로 다른 장면으로 보는 최소 해시 차이 (64비트 중)


@dataclass
class Info:
    path: str
    t: float = 0.0
    score: float = 0.0
    visible: float = 0.0
    detail: float = 0.0
    motion: Optional[float] = None
    ok: bool = True
    why: str = ""
    hash: int = 0


def _cv2():
    import cv2
    return cv2


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def ahash(gray: np.ndarray) -> int:
    cv2 = _cv2()
    small = cv2.resize(gray, (8, 8), interpolation=cv2.INTER_AREA)
    bits = (small > small.mean()).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def measure(img_bgr: np.ndarray, prev_gray: Optional[np.ndarray] = None, next_gray: Optional[np.ndarray] = None) -> Info:
    """한 장면의 점수. prev/next 는 같은 크기로 줄인 앞뒤 흑백 장면 (움직임 재기용)."""
    cv2 = _cv2()
    g = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    h, w = g.shape
    p99, std = float(np.percentile(g, 99)), float(g.std())
    info = Info("", hash=ahash(g))
    if p99 < MIN_P99 or std < MIN_STD:
        info.ok, info.why = False, "검은/빈 화면"
        return info
    core = g[int(h * 0.12):int(h * 0.88), int(w * 0.12):int(w * 0.88)]
    info.visible = 0.5 * _clamp((p99 - 40.0) / 120.0) + 0.5 * _clamp(std / 35.0)
    info.detail = _clamp(float(cv2.Canny(core, 60, 150).mean()) / 255.0 / 0.07)
    small = cv2.resize(g, (160, 90), interpolation=cv2.INTER_AREA).astype(np.float32)
    diffs = [float(np.abs(small - o).mean()) for o in (prev_gray, next_gray) if o is not None]
    if diffs:
        info.motion = max(diffs)
    m = 0.5 if info.motion is None else _clamp(info.motion / 6.0)
    info.score = 0.45 * info.visible + 0.20 * info.detail + 0.35 * m
    if info.motion is not None and info.motion < STATIC_DIFF:
        info.score *= 0.5
        info.why = "정지 화면(메뉴/일시정지)"
    return info


def pick(infos: Sequence[Info], k: int, duration: float = 0.0) -> List[Info]:
    """점수 높은 순으로 서로 다른 장면 k 장 (시간 순으로 돌려준다). 모자라면 탈락하지 않은 것 중 점수순으로 채운다."""
    good = sorted((i for i in infos if i.ok), key=lambda i: -i.score)
    gap = duration / (2.0 * max(k, 1)) if duration else 0.0
    chosen: List[Info] = []
    for relax in (False, True):
        for i in good:
            if len(chosen) >= k:
                break
            if i in chosen:
                continue
            if not relax and any(abs(i.t - c.t) < gap or hamming(i.hash, c.hash) < MIN_HASH_DIST for c in chosen):
                continue
            chosen.append(i)
    return sorted(chosen, key=lambda i: i.t)


def _read_small(path: str, cv2) -> Optional[np.ndarray]:
    im = cv2.imread(path)
    if im is None:
        return None
    return cv2.resize(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY), (160, 90), interpolation=cv2.INTER_AREA).astype(np.float32)


def sample_best(ffmpeg: str, src: str, out_dir: str, per: int = 6, width: int = 1024, run: Callable = F._run, workers: int = 4) -> List[F.Frame]:
    """영상 하나에서 좋은 장면 per 장. 이미지·짧은 영상·실패 때는 기존 균등 뽑기로 돌아간다."""
    if F.kind_of(src) != "video":
        return F.extract_frames(ffmpeg, src, out_dir, per, width, run)
    dur = F.video_duration(ffmpeg, src, run)
    if not dur or dur <= 0:
        raise F.FrameError("영상 길이를 읽지 못했습니다: " + os.path.basename(src))
    per = max(1, int(per))
    n = int(min(max(per * 4, 12), 40))
    if dur < 3.0 or n <= per:
        return F.extract_frames(ffmpeg, src, out_dir, per, width, run)
    cv2 = _cv2()
    os.makedirs(out_dir, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", os.path.splitext(os.path.basename(src))[0])[:40] or "src"
    tmp = tempfile.mkdtemp(prefix="fp_", dir=out_dir)
    vf = "fps=4,scale='min(%d,iw)':-2" % width

    def grab(i: int):
        t = max(0.0, min(dur * (i + 0.5) / n, dur - 1.0))
        pat = os.path.join(tmp, "c%02d_%%d.jpg" % i)
        r = run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", "%.3f" % t, "-i", src, "-t", "0.8", "-vf", vf, "-q:v", "3", "-frames:v", "3", pat])
        files = [os.path.join(tmp, "c%02d_%d.jpg" % (i, j)) for j in (1, 2, 3)]
        files = [f for f in files if os.path.isfile(f) and os.path.getsize(f) > 0]
        return i, t, files

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            results = list(ex.map(grab, range(n)))
        infos: List[Info] = []
        for i, t, files in results:
            if not files:
                continue
            mid = files[min(1, len(files) - 1)]
            im = cv2.imread(mid)
            if im is None:
                continue
            prev = _read_small(files[0], cv2) if len(files) >= 3 else None
            nxt = _read_small(files[-1], cv2) if len(files) >= 3 else None
            info = measure(im, prev, nxt)
            info.path, info.t = mid, t
            infos.append(info)
        chosen = pick(infos, per, dur)
        if not chosen:   # 쓸 만한 장면이 하나도 없으면 (전부 어두운 영상 등) 기존 방식으로
            return F.extract_frames(ffmpeg, src, out_dir, per, width, run)
        out: List[F.Frame] = []
        for j, c in enumerate(chosen):
            dst = os.path.join(out_dir, "%s_%02d.jpg" % (stem, j))
            shutil.copyfile(c.path, dst)
            out.append(F.Frame(dst, src, c.t))
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def sample_sources_best(ffmpeg: Optional[str], sources: Sequence[str], out_dir: str, per_source: int = 6, cap: int = 24,
                        on_progress: Optional[Callable[[int, int, str], None]] = None, run: Callable = F._run) -> List[F.Frame]:
    """frames.sample_sources 와 같은 모양, 영상은 '좋은 장면' 위주로 뽑는다. 문제가 생기면 균등 뽑기로 돌아간다."""
    def uniform():   # 예전 균등 뽑기 (run 은 바꿔 끼운 경우에만 넘긴다)
        extra = {} if run is F._run else {"run": run}
        return F.sample_sources(ffmpeg, sources, out_dir, per_source, cap, on_progress, **extra)

    try:
        _cv2()
    except Exception:  # noqa: BLE001 - OpenCV 가 없으면 예전 방식
        return uniform()
    srcs = [s for s in sources if F.kind_of(s)]
    if not srcs or ffmpeg is None:
        return uniform()
    if len(srcs) > cap:
        srcs = [srcs[int(i * len(srcs) / cap)] for i in range(cap)]
    per = max(1, min(per_source, cap // len(srcs) or 1))
    out: List[F.Frame] = []
    for i, s in enumerate(srcs):
        if on_progress:
            on_progress(i, len(srcs), os.path.basename(s))
        try:
            out += sample_best(ffmpeg, s, out_dir, per, run=run)
        except F.FrameError:
            raise
        except Exception:  # noqa: BLE001 - 어떤 이유로든 실패하면 그 영상은 균등 뽑기
            out += F.extract_frames(ffmpeg, s, out_dir, per, run=run)
        if len(out) >= cap:
            break
    return out[:cap]
