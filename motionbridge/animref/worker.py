"""AnimRef 분석 작업자 (별도 프로세스). core/remote.py 가 AnimRef 용 파이썬 환경(.venv)으로 실행한다.

이 파일은 MotionBridge 의 파이썬이 아니라 torch/ultralytics 가 설치된 파이썬에서 돌기 때문에 MotionBridge 의 다른 부분을
가져오지 않고, animref 패키지의 core 만 쓴다(Qt 불필요)."""
import json
import os
import sys
import threading
import time
from pathlib import Path


def main():
    out = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr  # 라이브러리가 print 해도 프로토콜(표준출력)을 깨지 않게

    def send(o):
        out.write(json.dumps(o, ensure_ascii=False) + "\n")
        out.flush()

    os.environ["ANIMREF_WORKER"] = "1"
    os.environ.setdefault("YOLO_VERBOSE", "False")
    os.environ.setdefault("YOLO_AUTOINSTALL", "False")
    root = Path(__file__).resolve().parent.parent       # 이 폴더의 부모 = 'animref' 패키지가 있는 곳
    sys.path.insert(0, str(root))
    req = json.loads(sys.stdin.readline())
    fn, params, s = req["fn"], req.get("params") or {}, req.get("settings") or {}
    cancel = threading.Event()

    def watch():
        for line in sys.stdin:
            if line.strip() == "cancel":
                cancel.set()

    threading.Thread(target=watch, daemon=True).start()

    def log(m):
        send({"t": "log", "m": str(m)})

    def progress(f, m=""):
        send({"t": "progress", "f": float(f), "m": str(m or "")})

    try:
        if fn == "selftest":       # 시험용: 로그/진행/취소/오류
            log("worker 시작")
            for i in range(int(params.get("steps", 3))):
                if cancel.is_set():
                    send({"t": "error", "m": "취소", "cancelled": True})
                    return
                progress((i + 1) / max(1, int(params.get("steps", 3))), "단계 %d" % (i + 1))
                time.sleep(float(params.get("sleep", 0.0)))
            if params.get("fail"):
                raise RuntimeError(str(params["fail"]))
            send({"t": "done", "result": {"echo": params, "worker": ANIMREF_OK()}})
            return
        try:
            import torch  # noqa: F401  Windows 에서는 Qt/다른 DLL 보다 먼저 불러야 오류가 없다
        except Exception:
            pass
        from animref.core import Cancelled, pipeline, settings as S

        try:
            os.environ.setdefault("YOLO_CONFIG_DIR", str(S.app_dir() / "ultralytics"))
            os.chdir(S.app_dir())
        except OSError:
            pass
        if fn == "process_videos":
            r = pipeline.process_videos(params["paths"], s, log, progress, cancel, params.get("origins") or {})
            send({"t": "done", "result": {"paths": list(r), "cleaned": [list(x) for x in r.cleaned], "discarded": r.discarded}})
        elif fn == "process_recording":
            r = pipeline.process_recording(params["path"], s, log, progress, cancel)
            send({"t": "done", "result": {"paths": list(r), "cleaned": [list(x) for x in r.cleaned], "discarded": r.discarded}})
        elif fn == "classify_run":
            from animref.core import classify

            st = classify.run(s, log, progress, cancel, float(params.get("f0", 0.0)), float(params.get("f1", 1.0)),
                              force=set(params.get("force") or ()) or None, discard_ok=set(params.get("discard_ok") or ()) or None)
            send({"t": "done", "result": {k: getattr(st, k, None) for k in ("kept", "discarded", "engine", "vlm_done", "vlm_failed")}})
        else:
            raise RuntimeError("알 수 없는 작업: %s" % fn)
    except BaseException as e:  # noqa: BLE001
        try:
            from animref.core import Cancelled as _C
        except Exception:
            _C = ()
        cancelled = bool(_C) and isinstance(e, _C)
        import traceback

        if not cancelled:
            traceback.print_exc()
        send({"t": "error", "m": "%s: %s" % (type(e).__name__, e) if not cancelled else "취소", "cancelled": cancelled})


def ANIMREF_OK():
    return sys.version.split()[0]


if __name__ == "__main__":
    try:
        main()
    finally:
        # 표준입력을 읽는 감시 스레드가 남아 있으면 Windows 에서 파이썬이 종료되지 않고 멈추므로 바로 끝낸다
        try:
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
        os._exit(0)
