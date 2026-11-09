"""무거운 AI 분석(torch · ultralytics · CLIP)을 별도 프로세스에서 돌린다.

MotionBridge 설치 파일에는 이 라이브러리들이 들어 있지 않다(수 GB). 대신 이미 AnimRef 용으로 만들어 둔 파이썬 환경(.venv)이
있으면 그 파이썬으로 worker.py 를 실행해 분석만 맡긴다. 결과는 평소처럼 라이브러리 폴더의 파일로 남고, 진행 상황/로그는
JSON 한 줄씩 표준출력으로 받는다. 이 프로그램 쪽은 가볍게 유지된다.

프로토콜 (worker.py):
  요청  : 표준입력 첫 줄에 JSON {"fn", "params", "settings", "cancel_file"} 후 입력을 닫는다.
          취소는 cancel_file 이 생기면 취소 (표준입력을 계속 읽는 스레드를 두면 Windows 에서 numpy 같은 DLL 을 불러올 때 멈춘다).
  응답  : 표준출력 JSON 줄  {"t":"log","m"} {"t":"progress","f","m"} {"t":"done","result"} {"t":"error","m","cancelled"}
"""
import collections
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from . import Cancelled, noop

WORKER_ENV = "ANIMREF_WORKER"
NEED_MSG = ("자동 분석(캐릭터 탐지 · AI 분류)에는 AI 구성요소(torch, ultralytics)가 필요한데, 이 프로그램에는 들어 있지 않아요.\n\n"
            "[⚙ 설정] → '분석 환경' 의 [자동 설치] 버튼을 누르면 이 프로그램이 알아서 만들어요 (처음 한 번, 수 GB). "
            "AnimRef 를 따로 설치해 둔 폴더의 .venv 가 있으면 그것도 자동으로 찾아 써요.")
def is_need_env(msg) -> bool:
    """오류 메시지가 '분석 환경이 없음' 때문인지."""
    return NEED_MSG[:30] in str(msg or "")


_CHECKED = {}   # python 경로 -> (ok, 메시지)


def local_has_torch():
    """이 프로세스에서 바로 분석할 수 있는지."""
    try:
        return importlib.util.find_spec("torch") is not None and importlib.util.find_spec("ultralytics") is not None
    except (ImportError, ValueError):
        return False


def should_offload():
    return os.environ.get(WORKER_ENV) != "1" and not local_has_torch()


def _venv_pythons(base: Path):
    return [base / ".venv" / "Scripts" / "python.exe", base / ".venv" / "bin" / "python"]


def candidates(s=None):
    out = []
    for key in ((s or {}).get("analysis_python") or "", os.environ.get("ANIMREF_PYTHON", "")):
        if key:
            out.append(Path(key))
    try:
        from . import envsetup
        out.append(envsetup.venv_python())      # 이 프로그램이 직접 만든 분석 환경 (설정 → 분석 환경 → 자동 설치)
    except Exception:  # noqa: BLE001
        pass
    home = Path.home()
    for rel in ("Downloads/AnimRef/AnimRef", "Downloads/AnimRef", "Desktop/AnimRef/AnimRef", "Desktop/AnimRef",
                "Documents/AnimRef/AnimRef", "AnimRef/AnimRef", "AnimRef"):
        out += _venv_pythons(home / rel)
    seen, uniq = set(), []
    for p in out:
        if str(p) not in seen:
            seen.add(str(p))
            uniq.append(p)
    return uniq


def _clean_env(extra=None):
    env = dict(os.environ)
    env.update({WORKER_ENV: "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1",
                "YOLO_VERBOSE": "False", "YOLO_AUTOINSTALL": "False"})
    for k in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH"):
        env.pop(k, None)
    mei = getattr(sys, "_MEIPASS", "")
    if mei:  # exe 로 묶였을 때: 이 프로그램의 DLL 폴더가 PATH 앞에 붙어 다른 파이썬의 DLL 과 충돌하는 것을 막는다
        parts = [p for p in env.get("PATH", "").split(os.pathsep) if p and os.path.normcase(os.path.abspath(p)) != os.path.normcase(os.path.abspath(mei))]
        env["PATH"] = os.pathsep.join(parts)
    env.update(extra or {})
    return env


def _flags():
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def check(py, force=False):
    """그 파이썬이 분석에 쓸 수 있는지 확인한다 -> (ok, 설명). 결과는 기억해 둔다."""
    py = str(py)
    if not force and py in _CHECKED:
        return _CHECKED[py]
    if not Path(py).is_file():
        res = (False, "파일이 없어요")
    else:
        try:
            r = subprocess.run([py, "-c", "import torch, ultralytics, cv2, numpy; print(torch.__version__)"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=120, env=_clean_env(), creationflags=_flags())
            if r.returncode == 0:
                res = (True, "torch " + (r.stdout.strip().splitlines() or ["?"])[-1])
            else:
                tail = (r.stderr.strip().splitlines() or ["알 수 없는 오류"])[-1]
                res = (False, "필요한 라이브러리를 불러오지 못했어요: " + tail)
        except (OSError, subprocess.TimeoutExpired) as e:
            res = (False, "실행하지 못했어요: %s" % e)
    _CHECKED[py] = res
    return res


def find_env(s=None):
    """쓸 수 있는 분석 환경 -> (python 경로 또는 None, 설명)."""
    why = ""
    for p in candidates(s):
        if p.is_file():
            ok, msg = check(p)
            if ok:
                return str(p), msg
            why = "%s: %s" % (p, msg)
    return None, why


def worker_path() -> Path:
    return Path(__file__).resolve().parent.parent / "worker.py"


def run(py, fn, params, s, log=noop, progress=noop, cancel=None):
    """worker 를 실행해 fn 을 맡기고 결과(dict)를 돌려준다. 취소하면 Cancelled, 실패하면 RuntimeError."""
    wp = worker_path()
    if not wp.is_file():
        raise RuntimeError("분석용 worker.py 를 찾지 못했어요: %s" % wp)
    p = subprocess.Popen([str(py), "-X", "utf8", str(wp)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         env=_clean_env(), creationflags=_flags(), cwd=str(wp.parent.parent))
    tail = collections.deque(maxlen=60)

    def drain():
        for raw in iter(p.stderr.readline, b""):
            t = raw.decode("utf-8", "replace").rstrip()
            if t:
                tail.append(t)

    threading.Thread(target=drain, daemon=True).start()
    done = threading.Event()
    cancel_file = os.path.join(tempfile.gettempdir(), "animref_cancel_%d_%d" % (os.getpid(), int(time.time() * 1000)))

    def watch():
        flagged = False
        t_cancel = 0.0
        while not done.wait(0.2):
            if cancel is not None and cancel.is_set():
                if not flagged:
                    flagged, t_cancel = True, time.time()
                    try:
                        open(cancel_file, "w").close()   # 작업자가 이 파일을 보고 멈춘다
                    except OSError:
                        pass
                elif time.time() - t_cancel > 25:  # 정리하지 않으면 강제 종료
                    p.kill()
                    return

    threading.Thread(target=watch, daemon=True).start()
    try:
        req = json.dumps({"fn": fn, "params": params, "settings": s, "cancel_file": cancel_file}, ensure_ascii=False) + "\n"
        p.stdin.write(req.encode("utf-8"))
        p.stdin.flush()
        p.stdin.close()
    except OSError as e:
        done.set()
        raise RuntimeError("분석 프로세스를 시작하지 못했어요: %s" % e)
    final = None
    try:
        for raw in iter(p.stdout.readline, b""):
            try:
                m = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            t = m.get("t")
            if t == "log":
                log(m.get("m", ""))
            elif t == "progress":
                progress(float(m.get("f", 0.0)), m.get("m", ""))
            elif t in ("done", "error"):
                final = m
                break  # 결과를 받았으면 프로세스가 곧 끝나든 말든 기다리지 않는다 (아래에서 정리)
        try:
            p.wait(timeout=20)
        except subprocess.TimeoutExpired:  # 결과를 받은 뒤에도 끝나지 않으면 정리한다 (결과는 이미 받았다)
            p.kill()
            p.wait()
    finally:
        done.set()
        try:
            os.remove(cancel_file)
        except OSError:
            pass
    if final is None:
        why = "\n".join(list(tail)[-8:])
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        raise RuntimeError("분석 프로세스가 결과 없이 끝났어요 (코드 %s).\n%s" % (p.returncode, why))
    if final["t"] == "error":
        if final.get("cancelled"):
            raise Cancelled()
        raise RuntimeError(final.get("m") or "분석 중 오류가 났어요.")
    return final.get("result") or {}


def process(fn, params, s, log=noop, progress=noop, cancel=None):
    """분석 환경을 찾아 run() 한다. 없으면 안내 오류."""
    py, why = find_env(s)
    if py is None:
        raise RuntimeError(NEED_MSG + ("\n\n(" + why + ")" if why else ""))
    log("[분석 환경] %s 로 분석합니다 (%s)" % (py, _CHECKED.get(py, (True, ""))[1]))
    return run(py, fn, params, s, log, progress, cancel)
