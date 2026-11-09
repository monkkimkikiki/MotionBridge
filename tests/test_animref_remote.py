"""AnimRef 자동 분석을 별도 프로세스(worker)로 맡기는 부분: 진행/취소/오류 전달, 파이프라인 연결."""
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
fails = []
import faulthandler  # noqa: E402

faulthandler.dump_traceback_later(150, exit=True)  # 멈추면 어디서 멈췄는지 남기고 끝낸다 (CI 에서 원인 파악용)


def check(c, m):
    if not c:
        fails.append(m)
        print("FAIL", m)


with tempfile.TemporaryDirectory() as d:
    os.environ["APPDATA"] = d
    os.environ["HOME"] = d
    os.environ["USERPROFILE"] = d
    os.environ.pop("ANIMREF_WORKER", None)
    from motionbridge.animref.core import Cancelled, classify, pipeline, remote, settings as S

    py = sys.executable
    logs, prog = [], []
    r = remote.run(py, "selftest", {"steps": 3, "x": "한글"}, {}, logs.append, lambda f, m: prog.append((round(f, 2), m)))
    check(r["echo"]["x"] == "한글" and "worker 시작" in logs and prog[-1] == (1.0, "단계 3"), "로그/진행/결과 전달: %s %s %s" % (r, logs, prog))

    try:
        remote.run(py, "selftest", {"steps": 1, "fail": "일부러 실패"}, {})
        check(False, "오류가 전달돼야 함")
    except RuntimeError as e:
        check("일부러 실패" in str(e), "오류 전달: %s" % e)

    ev = threading.Event()
    threading.Timer(0.6, ev.set).start()
    t0 = time.time()
    try:
        remote.run(py, "selftest", {"steps": 200, "sleep": 0.1}, {}, cancel=ev)
        check(False, "취소돼야 함")
    except Cancelled:
        check(time.time() - t0 < 10, "취소가 작업자에게 전달됨 (%.1fs)" % (time.time() - t0))

    # 라이브러리가 print 해도 프로토콜이 깨지지 않는지는 worker 가 stdout 을 stderr 로 돌려서 보장한다 (아래는 알 수 없는 작업 오류)
    try:
        remote.run(py, "없는작업", {}, {})
        check(False, "알 수 없는 작업은 오류")
    except RuntimeError as e:
        check("알 수 없는 작업" in str(e), "알 수 없는 작업: %s" % e)

    # 파이프라인 연결: torch 가 없으면 분석 환경으로 넘긴다 (여기서는 이 파이썬을 분석 환경처럼 사용)
    s = S.load()
    s["library_dir"] = os.path.join(d, "lib")
    check(remote.should_offload() == (not remote.local_has_torch()), "should_offload 판정")
    if remote.should_offload():
        orig = remote.find_env
        remote.find_env = lambda s=None: (None, "")
        try:
            pipeline.process_videos([], s)
            check(False, "분석 환경이 없으면 안내 오류")
        except RuntimeError as e:
            check("분석 환경" in str(e) and "자동 설치" in str(e), "분석 환경 없음 안내(자동 설치 버튼 안내)")
        remote.find_env = lambda s=None: (py, "시험")
        log2 = []
        res = pipeline.process_videos([], s, log2.append)
        check(isinstance(res, pipeline.Result) and list(res) == [] and res.discarded == 0, "빈 영상 목록을 별도 프로세스로 처리: %s" % list(res))
        check(any("분석 환경" in x for x in log2), "어느 환경으로 돌렸는지 로그")
        st = classify.run(s, log2.append)
        check(hasattr(st, "kept"), "classify.run 도 같은 방식")
        remote.find_env = orig

    cands = remote.candidates({"analysis_python": "C:/x/python.exe"})
    check(str(cands[0]).replace("\\", "/").endswith("x/python.exe") and len(cands) > 3, "후보 경로: 설정값이 먼저")

print("모두 통과" if not fails else "실패 %d개" % len(fails))
sys.exit(1 if fails else 0)
