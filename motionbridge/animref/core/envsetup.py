"""AI 분석 환경(torch · ultralytics · CLIP)을 이 프로그램이 직접 만들어 준다.

설치 파일에는 수 GB 짜리 AI 라이브러리가 안 들어 있으므로, 처음 한 번 사용자 PC 의 파이썬으로 전용 venv 를 만들어
(`<앱 데이터>/analysis_venv`) 거기에 설치한다. 이후 core/remote.py 가 그 파이썬을 자동으로 찾아 쓴다.
파이썬이 없으면 winget 으로 파이썬 3.11 을 먼저 설치해 본다 (Windows 10/11 에 기본 포함). 모든 단계는 로그로 보인다.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from . import Cancelled, noop
from . import settings as S

MIN_PY, MAX_PY = (3, 9), (3, 12)       # torch/ultralytics 휠이 있는 범위
PACKAGES = ["numpy<2", "opencv-python-headless", "Pillow", "ultralytics", "ftfy", "regex", "tqdm", "clip-anytorch"]


def venv_dir() -> Path:
    return S.app_dir() / "analysis_venv"


def venv_python() -> Path:
    d = venv_dir()
    return d / "Scripts" / "python.exe" if os.name == "nt" else d / "bin" / "python"


def _flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _version_of(py: str) -> Optional[Tuple[int, int]]:
    try:
        r = subprocess.run([py, "-c", "import sys;print('%d.%d'%sys.version_info[:2])"], capture_output=True, text=True, timeout=30, creationflags=_flags())
        if r.returncode == 0:
            a, b = r.stdout.strip().split(".")
            return int(a), int(b)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


def ok_version(v: Optional[Tuple[int, int]]) -> bool:
    return v is not None and MIN_PY <= v <= MAX_PY


def system_pythons() -> List[str]:
    """이 PC 에서 찾을 수 있는 파이썬 후보 (경로)."""
    out: List[str] = []
    if os.name == "nt":
        la = os.environ.get("LOCALAPPDATA", "")
        for ver in ("311", "312", "310", "39"):
            for base in (Path(la) / "Programs" / "Python" / ("Python" + ver) if la else None, Path("C:/Program Files/Python" + ver)):
                if base and (base / "python.exe").is_file():
                    out.append(str(base / "python.exe"))
        pyl = shutil.which("py")
        if pyl:
            for v in ("-3.11", "-3.12", "-3.10", "-3.9"):
                try:
                    r = subprocess.run([pyl, v, "-c", "import sys;print(sys.executable)"], capture_output=True, text=True, timeout=30, creationflags=_flags())
                    if r.returncode == 0 and r.stdout.strip():
                        out.append(r.stdout.strip().splitlines()[-1])
                except (OSError, subprocess.TimeoutExpired):
                    pass
    for name in ("python3.11", "python3.12", "python3.10", "python3", "python"):
        p = shutil.which(name)
        if p:
            out.append(p)
    seen, uniq = set(), []
    for p in out:
        k = os.path.normcase(p)
        if k not in seen and "WindowsApps" not in p:      # 스토어 바로가기(가짜 python.exe)는 제외
            seen.add(k)
            uniq.append(p)
    return uniq


def find_system_python() -> Optional[str]:
    for p in system_pythons():
        if ok_version(_version_of(p)):
            return p
    return None


def has_nvidia() -> bool:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return False
    try:
        return subprocess.run([exe], capture_output=True, timeout=20, creationflags=_flags()).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def torch_args(nvidia: bool) -> List[str]:
    """torch 설치 인자. NVIDIA 그래픽카드가 있으면 CUDA 휠, 없으면 CPU 휠."""
    if nvidia:
        return ["torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cu121"]
    return ["torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cpu"]


def _run(cmd: List[str], log: Callable, cancel=None, env=None) -> int:
    log("$ " + " ".join(cmd))
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", creationflags=_flags(), env=env)
    try:
        assert p.stdout is not None
        for line in p.stdout:
            line = line.rstrip()
            if line:
                log(line[:300])
            if cancel is not None and cancel.is_set():
                p.kill()
                raise Cancelled()
    finally:
        p.wait()
    return p.returncode


def install(log=noop, progress=noop, cancel=None, runner=_run) -> str:
    """분석 환경을 만든다. 성공하면 venv 의 python 경로. 실패하면 RuntimeError(이유와 해결 방법 포함)."""
    py = find_system_python()
    if py is None and os.name == "nt" and shutil.which("winget"):
        progress(0.02, "파이썬 설치 중 (winget)")
        log("이 PC 에서 쓸 수 있는 파이썬(3.9~3.12)을 못 찾아 winget 으로 파이썬 3.11 을 설치합니다 (관리자 확인 창이 뜰 수 있어요)")
        runner(["winget", "install", "-e", "--id", "Python.Python.3.11", "--scope", "user", "--silent", "--accept-package-agreements", "--accept-source-agreements"], log, cancel)
        py = find_system_python()
    if py is None:
        raise RuntimeError("이 PC 에서 파이썬(3.9~3.12)을 찾지 못했어요.\n https://www.python.org/downloads/ 에서 Python 3.11 을 설치(설치 첫 화면의 'Add python.exe to PATH' 체크)한 뒤 다시 눌러 주세요.")
    log("사용할 파이썬: %s" % py)
    vp = venv_python()
    if not vp.is_file():
        progress(0.05, "전용 환경 만드는 중")
        if runner([py, "-m", "venv", str(venv_dir())], log, cancel) != 0 or not vp.is_file():
            raise RuntimeError("전용 환경(venv)을 만들지 못했어요. 로그를 확인하세요.")
    pip = [str(vp), "-m", "pip", "install", "--disable-pip-version-check"]
    progress(0.08, "pip 갱신")
    runner([str(vp), "-m", "pip", "install", "--upgrade", "pip", "--disable-pip-version-check"], log, cancel)
    nv = has_nvidia()
    log("그래픽카드: %s → torch %s 버전" % ("NVIDIA 있음" if nv else "NVIDIA 없음", "CUDA" if nv else "CPU"))
    progress(0.12, "torch 설치 중 (수 GB, 오래 걸려요)")
    if runner(pip + torch_args(nv), log, cancel) != 0:
        raise RuntimeError("torch 설치에 실패했어요. 인터넷 연결/디스크 공간(약 5GB)을 확인하고 다시 눌러 주세요 (이어서 설치돼요).")
    progress(0.7, "나머지 패키지 설치 중")
    if runner(pip + PACKAGES, log, cancel) != 0:
        raise RuntimeError("ultralytics 등 패키지 설치에 실패했어요. 로그를 확인하세요.")
    progress(0.95, "확인 중")
    r = subprocess.run([str(vp), "-c", "import torch, ultralytics, cv2, numpy, clip; print(torch.__version__)"], capture_output=True, text=True, timeout=300, creationflags=_flags())
    if r.returncode != 0:
        tail = (r.stderr.strip().splitlines() or ["?"])[-1]
        raise RuntimeError("설치는 됐지만 불러오지 못해요: " + tail)
    log("완료: torch " + r.stdout.strip())
    return str(vp)
