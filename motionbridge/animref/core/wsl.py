"""WSL(Windows 안의 리눅스) 도우미. AI 모캡(WHAM)는 WSL 안에서 돌아가므로, Windows 프로그램인 AnimRef가
WSL 명령을 실행하고, 경로를 서로 바꾸고, 파일을 넣어 주는 일을 여기서 한다."""
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import PureWindowsPath

from . import Cancelled

NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class IdleTimeout(RuntimeError):
    """명령이 오래 아무 출력도 없이 멈춰 있어서 우리가 중단했다."""


def is_windows() -> bool:
    return sys.platform == "win32"


def wsl_exe():
    return shutil.which("wsl") or shutil.which("wsl.exe")


def decode_output(b: bytes) -> str:
    """wsl.exe 자신의 메시지(-l 등)는 UTF-16LE, 리눅스 프로그램의 출력은 UTF-8 이다."""
    if not b:
        return ""
    if b"\x00" in b[:200]:
        try:
            return b.decode("utf-16-le").lstrip("\ufeff")
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", errors="replace").lstrip("\ufeff")


def parse_distros(text: str):
    out = []
    for line in text.replace("\x00", "").splitlines():
        n = line.strip().replace("(Default)", "").replace("(기본값)", "").strip()
        if n and not n.lower().startswith("docker-desktop"):
            out.append(n)
    return out


def list_distros():
    exe = wsl_exe()
    if not exe:
        return []
    try:
        r = subprocess.run([exe, "-l", "-q"], capture_output=True, timeout=20, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if r.returncode != 0:
        return []
    return parse_distros(decode_output(r.stdout))


def pick_distro(distros, preferred="Ubuntu-22.04"):
    for d in distros:
        if d.lower() == preferred.lower():
            return d
    for d in distros:
        if d.lower().startswith("ubuntu"):
            return d
    return None


# ---------------------------------------------------------------- 경로 변환
def win_to_wsl(path) -> str:
    """C:\\Users\\a\\b.mp4 -> /mnt/c/Users/a/b.mp4,  \\\\wsl.localhost\\Ubuntu\\home\\x -> /home/x"""
    s = str(path)
    m = re.match(r"^\\\\(?:wsl\.localhost|wsl\$)\\[^\\]+(\\.*)?$", s, re.IGNORECASE)
    if m:
        return (m.group(1) or "\\").replace("\\", "/")
    p = PureWindowsPath(s)
    if not p.drive or p.drive.startswith("\\\\"):
        raise ValueError(f"이 경로는 AI 모캡이 읽을 수 없어요: {s}")
    drive = p.drive.rstrip(":").lower()
    rest = "/".join(p.parts[1:])
    return f"/mnt/{drive}/{rest}" if rest else f"/mnt/{drive}"


def wsl_to_win(path, distro="Ubuntu-22.04") -> str:
    """/mnt/c/Users/a -> C:\\Users\\a,  /root/x -> \\\\wsl.localhost\\Ubuntu-22.04\\root\\x"""
    m = re.match(r"^/mnt/([a-zA-Z])(/.*)?$", path)
    if m:
        return f"{m.group(1).upper()}:" + (m.group(2) or "\\").replace("/", "\\")
    return f"\\\\wsl.localhost\\{distro}" + path.replace("/", "\\")


def q(s) -> str:
    """bash 명령에 안전하게 넣을 문자열."""
    return shlex.quote(str(s))


# ---------------------------------------------------------------- 실행
def command(distro, bash_cmd):
    """wsl 로 root 권한 bash 명령을 실행하는 인자 목록 (쉘을 한 번만 거치도록 --exec 사용)."""
    exe = wsl_exe()
    if not exe:
        raise RuntimeError("WSL이 설치되어 있지 않아요.")
    return [exe, "-d", distro, "-u", "root", "--exec", "bash", "-c", bash_cmd]


def _lines(stream):
    """\\r 로 갱신되는 진행 막대도 한 줄씩 나눠서 읽는다."""
    buf = b""
    while True:
        chunk = stream.read1(4096) if hasattr(stream, "read1") else stream.read(4096)
        if not chunk:
            break
        buf += chunk
        while True:
            i = min([k for k in (buf.find(b"\n"), buf.find(b"\r")) if k >= 0], default=-1)
            if i < 0:
                break
            line, buf = buf[:i], buf[i + 1:]
            if line.strip():
                yield line.decode("utf-8", errors="replace").rstrip()
    if buf.strip():
        yield buf.decode("utf-8", errors="replace").rstrip()


def run(distro, bash_cmd, log=None, cancel=None, stdin_bytes=None, keep=200, idle_timeout=None):
    """명령을 실행하고 출력을 한 줄씩 log(line)로 보낸다. 반환: (종료코드, 마지막 keep줄 목록).
    cancel(threading.Event)이 켜지면 프로세스를 끄고 Cancelled 를 던진다.
    idle_timeout(초) 동안 아무 출력이 없으면 프로세스를 끄고 IdleTimeout 을 던진다."""
    proc = subprocess.Popen(command(distro, bash_cmd), stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
    stop = threading.Event()
    cancelled = threading.Event()
    timed_out = threading.Event()
    last = [time.monotonic()]

    def watcher():
        while not stop.is_set():
            if cancel is not None and cancel.is_set():
                cancelled.set()
                try:
                    proc.kill()
                except OSError:
                    pass
                return
            if idle_timeout and time.monotonic() - last[0] > idle_timeout:
                timed_out.set()
                try:
                    proc.kill()
                except OSError:
                    pass
                return
            stop.wait(0.3)
    t = threading.Thread(target=watcher, daemon=True)
    t.start()
    if stdin_bytes is not None:
        def feed():
            try:
                proc.stdin.write(stdin_bytes)
                proc.stdin.close()
            except OSError:
                pass
        threading.Thread(target=feed, daemon=True).start()
    tail = []
    box = queue.Queue()
    _END = object()

    def reader():                       # 출력 읽기는 따로: 취소/멈춤 때 읽기가 막혀 있어도 우리는 바로 빠져나온다
        try:
            for ln in _lines(proc.stdout):
                box.put(ln)
        except (OSError, ValueError):
            pass
        box.put(_END)
    threading.Thread(target=reader, daemon=True).start()
    rc = -1
    try:
        while True:
            try:
                line = box.get(timeout=0.2)
            except queue.Empty:
                if cancelled.is_set() or timed_out.is_set():
                    break
                continue
            if line is _END:
                rc = proc.wait()
                break
            last[0] = time.monotonic()
            tail.append(line)
            if len(tail) > keep:
                del tail[0]
            if log:
                log(line)
    finally:
        stop.set()
        if proc.poll() is None:
            proc.kill()
    if cancelled.is_set():
        raise Cancelled()
    if timed_out.is_set():
        raise IdleTimeout(f"{int(idle_timeout)}초 동안 아무 출력이 없어서 멈춘 것으로 보고 중단했어요.")
    return rc, tail


def push_file(distro, local_path, remote_path):
    """Windows 파일을 WSL 안으로 복사한다 (줄 끝 \\r 제거: 스크립트가 CRLF여도 안전)."""
    data = open(local_path, "rb").read()
    folder = remote_path.rsplit("/", 1)[0]
    rc, tail = run(distro, f"mkdir -p {q(folder)} && tr -d '\\r' > {q(remote_path)}", stdin_bytes=data)
    if rc != 0:
        raise RuntimeError("WSL로 파일을 복사하지 못했어요: " + " / ".join(tail[-3:]))


def kill_matching(distro, pattern):
    """취소했을 때 WSL 안에 남은 프로세스 정리 (실패해도 무시)."""
    try:
        subprocess.run(command(distro, f"pkill -f {q(pattern)}"), capture_output=True, timeout=15, creationflags=NO_WINDOW)
    except Exception:  # noqa: BLE001
        pass
