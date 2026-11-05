"""영상/이미지에서 인식용 장면(프레임)을 뽑는다. ffmpeg 를 쓴다 (이미지 파일은 그대로 써도 된다)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

VIDEO_EXTS = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv", ".ts")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
MAX_RAW_IMAGE = 4_500_000  # ffmpeg 없이 이미지를 그대로 보낼 수 있는 최대 크기 (Claude 한도 5MB)


class FrameError(Exception):
    pass


@dataclass
class Frame:
    path: str       # 뽑아 둔 이미지 파일
    source: str     # 원본 영상/이미지
    t: float = 0.0  # 영상 안의 위치(초)


def kind_of(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return "video" if ext in VIDEO_EXTS else "image" if ext in IMAGE_EXTS else ""


def find_ffmpeg(explicit: Optional[str] = None) -> Optional[str]:
    cands: List[str] = [c for c in (explicit, os.environ.get("MOTIONBRIDGE_FFMPEG")) if c]
    for c in cands:
        if os.path.isfile(c):
            return c
    w = shutil.which("ffmpeg")
    if w:
        return w
    try:  # pip 의 imageio-ffmpeg 가 설치돼 있으면 거기 들어 있는 ffmpeg 를 쓴다
        import imageio_ffmpeg  # type: ignore

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception:  # noqa: BLE001
        pass
    return None


def _run(cmd: List[str], timeout: float = 120.0) -> subprocess.CompletedProcess:
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, **kw)


_DUR = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def video_duration(ffmpeg: str, path: str, run: Callable = _run) -> Optional[float]:
    r = run([ffmpeg, "-hide_banner", "-i", path])
    m = _DUR.search((r.stderr or "") + (r.stdout or ""))
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def extract_frames(ffmpeg: str, src: str, out_dir: str, count: int = 6, width: int = 1024, run: Callable = _run) -> List[Frame]:
    """영상 하나에서 count 장을 고르게 뽑는다. 이미지면 크기만 줄여 한 장."""
    os.makedirs(out_dir, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", os.path.splitext(os.path.basename(src))[0])[:40] or "src"
    vf = "scale='min(%d,iw)':-2" % width
    kind = kind_of(src)
    if kind == "image":
        out = os.path.join(out_dir, "%s.jpg" % stem)
        r = run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", src, "-vf", vf, "-q:v", "3", out])
        if r.returncode != 0 or not os.path.isfile(out):
            raise FrameError("이미지를 읽지 못했습니다: %s\n%s" % (os.path.basename(src), (r.stderr or "").strip()[-300:]))
        return [Frame(out, src, 0.0)]
    if kind != "video":
        raise FrameError("지원하지 않는 파일입니다: " + os.path.basename(src))
    dur = video_duration(ffmpeg, src, run)
    if not dur or dur <= 0:
        raise FrameError("영상 길이를 읽지 못했습니다: " + os.path.basename(src))
    count = max(1, int(count))
    frames: List[Frame] = []
    for i in range(count):
        t = dur * (i + 0.5) / count
        out = os.path.join(out_dir, "%s_%02d.jpg" % (stem, i))
        r = run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-ss", "%.3f" % t, "-i", src, "-frames:v", "1", "-vf", vf, "-q:v", "3", out])
        if r.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 0:
            frames.append(Frame(out, src, t))
    if not frames:
        raise FrameError("프레임을 하나도 뽑지 못했습니다: " + os.path.basename(src))
    return frames


def sample_sources(ffmpeg: Optional[str], sources: Sequence[str], out_dir: str, per_source: int = 6, cap: int = 24,
                   on_progress: Optional[Callable[[int, int, str], None]] = None, run: Callable = _run) -> List[Frame]:
    """여러 영상/이미지에서 전체 cap 장 이하로 장면을 모은다. 영상은 균등하게 나눠 뽑는다."""
    srcs = [s for s in sources if kind_of(s)]
    if not srcs:
        raise FrameError("영상이나 이미지 파일이 없습니다 (mp4/mkv/mov/avi/webm, png/jpg).")
    if len(srcs) > cap:  # 원본이 한도보다 많으면 고르게 솎아서 쓴다
        srcs = [srcs[int(i * len(srcs) / cap)] for i in range(cap)]
    per = max(1, min(per_source, cap // len(srcs) or 1))
    out: List[Frame] = []
    for i, s in enumerate(srcs):
        if on_progress:
            on_progress(i, len(srcs), os.path.basename(s))
        if ffmpeg is None:
            if kind_of(s) == "image" and os.path.getsize(s) <= MAX_RAW_IMAGE:
                out.append(Frame(s, s, 0.0))
                continue
            raise FrameError("영상 처리에는 ffmpeg 가 필요합니다. 설정에서 ffmpeg 경로를 지정하거나 설치하세요 "
                             "(이미지 파일은 ffmpeg 없이도 쓸 수 있습니다).")
        out += extract_frames(ffmpeg, s, out_dir, per, run=run)
        if len(out) >= cap:
            break
    return out[:cap]
