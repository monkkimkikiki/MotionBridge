"""ffmpeg 기반 영상 처리. ffmpeg는 imageio-ffmpeg에 포함된 것을 사용하므로 따로 설치할 필요가 없다.
OpenCV의 파일 입출력 대신 ffmpeg/파이썬 파일 I/O를 써서 한글 경로에서도 안전하게 동작한다."""
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".flv", ".webm", ".ts", ".m4v", ".wmv"}
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0  # ffmpeg 콘솔창 깜빡임 방지


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        p = shutil.which("ffmpeg")
        if p:
            return p
    raise RuntimeError("ffmpeg를 찾을 수 없습니다. 프로그램을 다시 설치해 주세요.")


def _run(args):
    return subprocess.run([ffmpeg_exe(), "-hide_banner", *map(str, args)], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)


@dataclass
class VideoInfo:
    w: int
    h: int
    fps: float
    duration: float


def probe(path) -> VideoInfo:
    err = _run(["-i", path]).stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0
    line = next((l for l in err.splitlines() if "Video:" in l), "")
    m = re.search(r"[ ,](\d{2,5})x(\d{2,5})[ ,\[]", line + " ")
    if not m:
        raise RuntimeError(f"영상 정보를 읽을 수 없습니다: {Path(path).name}")
    f = re.search(r"(\d+(?:\.\d+)?) fps", line) or re.search(r"(\d+(?:\.\d+)?) tbr", line)
    fps = float(f[1]) if f else 30.0
    return VideoInfo(int(m[1]), int(m[2]), fps, dur)


def has_audio(path) -> bool:
    return "Audio:" in _run(["-i", path]).stderr


def even(x) -> int:
    return max(2, int(round(x / 2.0)) * 2)


def iter_frames(path, fps, width, height, start=0.0, length=None, pix_fmt="bgr24"):
    """영상을 지정 fps/크기로 디코딩해서 (시간, 프레임) 을 내보낸다."""
    args = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error"]
    if start > 0:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(path)]
    if length is not None:
        args += ["-t", f"{length:.3f}"]
    args += ["-an", "-vf", f"fps={fps},scale={width}:{height}", "-f", "rawvideo", "-pix_fmt", pix_fmt, "-"]
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=_NO_WINDOW, bufsize=10 ** 7)
    n = width * height * 3
    i = 0
    try:
        while True:
            buf = proc.stdout.read(n)
            if len(buf) < n:
                break
            yield start + i / fps, np.frombuffer(buf, np.uint8).reshape(height, width, 3).copy()
            i += 1
    finally:
        proc.kill()
        proc.wait()


def sample_frames(path, n=8, width=320):
    """클립 전체에서 RGB 프레임 n장을 고르게 뽑는다."""
    info = probe(path)
    h = even(info.h * width / info.w)
    dur = max(info.duration, 0.1)
    fps = max(n / dur, 0.5)
    frames = [f for _, f in iter_frames(path, round(fps, 4), width, h, pix_fmt="rgb24")]
    if len(frames) > n:
        idx = np.linspace(0, len(frames) - 1, n).astype(int)
        frames = [frames[i] for i in idx]
    return frames


def cut(src, start, length, out, crf=18, vf=None):
    args = ["-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", src, "-t", f"{length:.3f}"]
    if vf:
        args += ["-vf", vf]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out]
    r = _run(args)
    if r.returncode != 0:
        raise RuntimeError("클립 저장 실패: " + r.stderr.strip()[-400:])


def thumbnail(video, t, out, width=320):
    r = _run(["-y", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", video,
              "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", out])
    return r.returncode == 0


def write_image(path, rgb):
    import cv2

    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
    if ok:
        Path(path).write_bytes(buf.tobytes())


def contact_sheet(frames, cols=4, tile_w=240):
    import cv2

    tiles = []
    for f in frames:
        h, w = f.shape[:2]
        tiles.append(cv2.resize(f, (tile_w, max(2, int(h * tile_w / w)))))
    th = max(t.shape[0] for t in tiles)
    tiles = [np.pad(t, ((0, th - t.shape[0]), (0, 0), (0, 0))) for t in tiles]
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    return np.vstack([np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)])


def wait_until_stable(path, timeout=60.0):
    """OBS가 녹화 파일을 다 쓸 때까지 기다린다."""
    last, stable, t0 = -1, 0, time.time()
    while time.time() - t0 < timeout:
        try:
            size = Path(path).stat().st_size
        except OSError:
            size = -1
        stable = stable + 1 if (size > 0 and size == last) else 0
        if stable >= 3:
            return True
        last = size
        time.sleep(0.5)
    return False
