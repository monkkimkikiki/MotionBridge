"""Ollama(내 PC에서 AI 돌리는 프로그램)와 모델을 한 번에 준비한다: 설치 -> 실행 -> 모델 받기.

Windows 에서는 공식 설치 파일(OllamaSetup.exe)을 받아 조용히 설치한다 (관리자 권한 불필요, 사용자 폴더에 설치).
다른 OS 는 안내만 한다. 네트워크·프로세스 실행은 모두 바깥에서 바꿔 끼울 수 있어 테스트가 쉽다.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from typing import Callable, List, Optional

from . import vision

INSTALLER_URL = "https://ollama.com/download/OllamaSetup.exe"
DOWNLOAD_PAGE = "https://ollama.com/download"

Progress = Callable[[int, str], None]


def _noop(_pct: int, _msg: str) -> None:
    pass


def find_ollama() -> Optional[str]:
    """설치된 ollama 실행 파일 경로 (없으면 None)."""
    p = shutil.which("ollama")
    if p:
        return p
    cands: List[str] = []
    if os.name == "nt":
        for env in ("LOCALAPPDATA", "ProgramFiles"):
            base = os.environ.get(env)
            if base:
                cands.append(os.path.join(base, "Programs", "Ollama", "ollama.exe") if env == "LOCALAPPDATA" else os.path.join(base, "Ollama", "ollama.exe"))
    else:
        cands += ["/usr/local/bin/ollama", "/usr/bin/ollama", "/opt/homebrew/bin/ollama", "/Applications/Ollama.app/Contents/Resources/ollama"]
    return next((c for c in cands if os.path.isfile(c)), None)


def is_running(host: str = vision.DEFAULT_OLLAMA_HOST, post: Callable = vision._http) -> bool:
    try:
        post(host.rstrip("/") + "/api/tags", None, {}, 4.0)
        return True
    except vision.VisionError:
        return False


def start_server(exe: str, popen: Callable = subprocess.Popen) -> None:
    """ollama 서버를 창 없이 뒤에서 띄운다."""
    kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000 | 0x00000008  # CREATE_NO_WINDOW | DETACHED_PROCESS
    popen([exe, "serve"], **kw)


def download(url: str, dest: str, on_progress: Progress = _noop, opener: Callable = urllib.request.urlopen) -> None:
    with opener(url, timeout=60) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    on_progress(int(done * 100 / total), "Ollama 설치 파일 받는 중 (%d / %d MB)" % (done >> 20, total >> 20))


def install(on_progress: Progress = _noop, download_fn: Callable = download, run: Callable = subprocess.run,
            windows: Optional[bool] = None, find_fn: Callable = find_ollama) -> str:
    """Ollama 설치 (Windows). 설치된 실행 파일 경로를 돌려준다. 못 하면 VisionError."""
    if not (os.name == "nt" if windows is None else windows):
        raise vision.VisionError("이 운영체제에서는 자동 설치를 지원하지 않습니다. %s 에서 설치하거나 'curl -fsSL https://ollama.com/install.sh | sh' 를 실행하세요." % DOWNLOAD_PAGE)
    tmp = os.path.join(tempfile.mkdtemp(prefix="mb_ollama_"), "OllamaSetup.exe")
    try:
        download_fn(INSTALLER_URL, tmp, on_progress)
    except Exception as e:  # 네트워크 오류
        raise vision.VisionError("Ollama 설치 파일을 받지 못했습니다: %s\n직접 설치: %s" % (e, DOWNLOAD_PAGE))
    on_progress(100, "Ollama 설치 중 (1~2분)")
    try:
        r = run([tmp, "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"], timeout=900)
    except Exception as e:
        raise vision.VisionError("Ollama 설치 파일을 실행하지 못했습니다: %s\n직접 설치: %s" % (e, DOWNLOAD_PAGE))
    finally:
        shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)
    exe = find_fn()
    if getattr(r, "returncode", 0) not in (0, None) or not exe:
        raise vision.VisionError("Ollama 설치가 끝나지 않았습니다(코드 %s). 직접 설치: %s" % (getattr(r, "returncode", "?"), DOWNLOAD_PAGE))
    return exe


def ensure_ready(prov: "vision.OllamaProvider", on_progress: Progress = _noop, *, install_fn: Callable = install,
                 find_fn: Callable = find_ollama, running_fn: Callable = is_running, start_fn: Callable = start_server,
                 sleep: Callable = time.sleep, wait_s: float = 40.0) -> str:
    """Ollama 설치 -> 서버 실행 -> 모델 받기를 필요한 것만 순서대로. 끝나면 안내 글을 돌려준다."""
    steps: List[str] = []
    exe = find_fn()
    if exe is None:
        steps.append("Ollama 설치")
        exe = install_fn(on_progress)
    if not running_fn(prov.host):
        on_progress(100, "Ollama 실행 중")
        start_fn(exe)
        t0 = time.time()
        while not running_fn(prov.host):
            if time.time() - t0 > wait_s:
                raise vision.VisionError("Ollama 를 실행했지만 응답이 없습니다. 작업 표시줄의 Ollama 를 직접 실행한 뒤 다시 눌러 주세요.")
            sleep(1.0)
        steps.append("Ollama 실행")
    ok, _msg, _names = prov.check()
    if not ok:
        steps.append("모델 %s 받기" % prov.model)
        prov.pull(on_progress)
    return ("준비 완료 (%s)" % ", ".join(steps)) if steps else "이미 모두 준비되어 있습니다 (%s)" % prov.model
