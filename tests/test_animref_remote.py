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

    # 오류 메시지로 '분석 환경 없음'을 알아본다 (앱이 이걸 보고 오류창 대신 자동 설치를 제안한다)
    check(remote.is_need_env(remote.NEED_MSG + "\n\n(x)") and not remote.is_need_env("다른 오류"), "환경 없음 오류 판별")

    # 원하는 구간만 분석: 구간 다듬기, 구간 파일 만들기, 원본은 그대로, 환경 없으면 자르기 전에 안내
    check(pipeline.clean_ranges([(10, 5), (0, 0.3), (20, 40), (30, 50), (100, 200)], 60) == [(5.0, 10.0), (20.0, 50.0)],
          "구간 다듬기(뒤집힘/짧음/겹침/길이초과): %s" % pipeline.clean_ranges([(10, 5), (0, 0.3), (20, 40), (30, 50), (100, 200)], 60))
    check(pipeline.clean_ranges([], 60) == [] and pipeline.clean_ranges([("a", 1)], 60) == [], "빈/잘못된 구간은 버림")
    src = os.path.join(d, "rec.mp4")
    open(src, "wb").write(b"x")
    cut_calls, seen = [], {}
    def fake_cut(sp, a, length, out):
        cut_calls.append((round(a, 1), round(length, 1)))
        open(out, "wb").write(b"y")
    orig_pv, orig_probe, orig_find, orig_off = pipeline.process_videos, pipeline.media.probe, remote.find_env, remote.should_offload
    pipeline.process_videos = lambda files, s, log=None, progress=None, cancel=None, origins=None: seen.update(files=list(files), origins=dict(origins)) or pipeline.Result()
    class _Info:
        duration = 120.0
    pipeline.media.probe = lambda p: _Info()
    remote.should_offload = lambda: False
    try:
        pipeline.process_ranges(src, [(10, 20), (50, 70), (60, 80)], s, out_dir=os.path.join(d, "rg"), cut_fn=fake_cut)
        check(cut_calls == [(10.0, 10.0), (50.0, 30.0)], "겹치는 구간은 합쳐 두 번만 자름: %s" % cut_calls)
        check(len(seen["files"]) == 2 and all(os.path.isfile(f) for f in seen["files"]) and os.path.isfile(src), "구간 파일 생성, 원본은 그대로")
        check(all(v == pipeline.RANGE for v in seen["origins"].values()) and pipeline.policy_for(s, pipeline.RANGE) == "keep", "구간 파일은 지우지 않는 정책")
        n = len(cut_calls)
        pipeline.process_ranges(src, [(10, 20)], s, out_dir=os.path.join(d, "rg"), cut_fn=fake_cut)
        check(len(cut_calls) == n, "같은 구간은 다시 자르지 않음")
        try:
            pipeline.process_ranges(src, [(1, 1.2)], s, out_dir=os.path.join(d, "rg"), cut_fn=fake_cut)
            check(False, "너무 짧은 구간만 있으면 오류")
        except RuntimeError as e:
            check("구간" in str(e), "짧은 구간 안내: %s" % e)
        remote.should_offload = lambda: True
        remote.find_env = lambda s=None: (None, "")
        n = len(cut_calls)
        try:
            pipeline.process_ranges(src, [(30, 40)], s, out_dir=os.path.join(d, "rg2"), cut_fn=fake_cut)
            check(False, "분석 환경이 없으면 안내")
        except RuntimeError as e:
            check(remote.is_need_env(str(e)) and len(cut_calls) == n, "분석 환경 없으면 자르기 전에 안내")
    finally:
        pipeline.process_videos, pipeline.media.probe, remote.find_env, remote.should_offload = orig_pv, orig_probe, orig_find, orig_off

    # 구간에서 만든 클립은 원본 영상의 위치를 가리키도록 바로잡는다 + 하이라이트 자동 자르기
    import json as _json
    rf = os.path.join(d, "rg", "rec__10s-20s.mp4")
    cj = os.path.join(d, "clip1.json")
    open(cj, "w", encoding="utf-8").write(_json.dumps({"source_video": rf, "source_name": "rec__10s-20s.mp4", "start_in_source": 2.5, "t_range": [2.0, 5.0]}))
    pipeline.remap_clips([cj], {rf: (src, 10.0)})
    m = _json.load(open(cj, encoding="utf-8"))
    check(m["source_video"] == src and m["source_name"] == "rec.mp4" and m["start_in_source"] == 12.5 and m["t_range"] == [12.0, 15.0], "구간 클립을 원본 위치로: %s" % m)
    seen2 = {}
    orig_pv2, orig_pr = pipeline.process_videos, pipeline.process_ranges
    pipeline.process_videos = lambda files, s, log=None, progress=None, cancel=None, origins=None: seen2.update(v=(list(files), dict(origins))) or pipeline.Result()
    pipeline.process_ranges = lambda path, rs, s, log=None, progress=None, cancel=None: seen2.update(r=(path, rs)) or pipeline.Result()
    remote.should_offload = lambda: False
    try:
        pipeline.process_highlights(src, None, None, s)
        check(seen2["v"][0] == [src] and seen2["v"][1][src] == pipeline.RANGE, "하이라이트: 구간을 안 정하면 영상 전체, 원본은 지우지 않는 정책")
        pipeline.process_highlights(src, 30.0, 50.0, s)
        check(seen2["r"] == (src, [(30.0, 50.0)]), "하이라이트: 구간을 정하면 그 구간만")
    finally:
        pipeline.process_videos, pipeline.process_ranges, remote.should_offload = orig_pv2, orig_pr, orig_off

    # 부품(모듈) 하나가 빠졌다는 오류가 나면 자동 설치 후 한 번 더 시도한다 (예: No module named 'lap')
    check(remote.missing_module("RuntimeError: ModuleNotFoundError: No module named 'lap'") == "lap" and remote.missing_module("다른 오류") is None, "빠진 모듈 이름 읽기")
    calls = {"run": 0, "pip": []}
    orig_run, orig_rep, orig_find2 = remote.run, remote.repair, remote.find_env
    def flaky_run(py, fn, params, s, log=None, progress=None, cancel=None):
        calls["run"] += 1
        if calls["run"] == 1:
            raise RuntimeError("ModuleNotFoundError: No module named 'lap'")
        return {"ok": 1}
    remote.run = flaky_run
    remote.find_env = lambda s=None: ("pyx", "시험")
    remote.repair = lambda py, mod, log=None, runner=None: calls["pip"].append(mod) or True
    try:
        check(remote.process("selftest", {}, {}) == {"ok": 1} and calls["pip"] == ["lap"] and calls["run"] == 2, "lap 이 빠지면 자동 설치 후 다시 분석: %s" % calls)
        calls.update(run=0, pip=[])
        remote.repair = lambda py, mod, log=None, runner=None: False
        try:
            remote.process("selftest", {}, {})
            check(False, "자동 설치 실패면 안내 오류")
        except RuntimeError as e:
            check("부품" in str(e) and "자동 설치" in str(e), "자동 설치 실패 안내")
        def other_err(py, fn, params, s, log=None, progress=None, cancel=None):
            raise RuntimeError("전혀 다른 오류")
        remote.run = other_err
        try:
            remote.process("selftest", {}, {})
            check(False, "다른 오류는 그대로 전달")
        except RuntimeError as e:
            check("전혀 다른 오류" in str(e), "다른 오류는 그대로")
    finally:
        remote.run, remote.repair, remote.find_env = orig_run, orig_rep, orig_find2
    from motionbridge.animref.core import envsetup
    check("lapx" in envsetup.PACKAGES, "분석 환경 설치 목록에 lapx(추적용) 포함")
    pcalls = []
    check(remote.repair("pyx", "lap", runner=lambda cmd, log: pcalls.append(cmd) or 0) and pcalls and pcalls[0][-1] == "lapx" and not remote.repair("pyx", "엉뚱한", runner=lambda c, l: 0), "repair: lap -> lapx 설치, 모르는 모듈은 안 함")

    cands = remote.candidates({"analysis_python": "C:/x/python.exe"})
    check(str(cands[0]).replace("\\", "/").endswith("x/python.exe") and len(cands) > 3, "후보 경로: 설정값이 먼저")

print("모두 통과" if not fails else "실패 %d개" % len(fails))
sys.exit(1 if fails else 0)
