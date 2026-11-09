"""AI 분석 환경 자동 설치: 파이썬 찾기/없을 때 안내/설치 순서 (실제 설치는 하지 않고 가짜 실행기로)."""
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from motionbridge.animref.core import envsetup as E, remote  # noqa: E402

fails = []


def check(c, m):
    if not c:
        fails.append(m)
        print("FAIL", m)


check(E.ok_version((3, 11)) and not E.ok_version((3, 13)) and not E.ok_version((3, 8)) and not E.ok_version(None), "파이썬 버전 범위 3.9~3.12")
check("cu121" in " ".join(E.torch_args(True)) and "cpu" in " ".join(E.torch_args(False)), "그래픽카드에 따라 torch 종류")
check("ultralytics" in E.PACKAGES and "clip-anytorch" in E.PACKAGES, "필요 패키지 목록")

with tempfile.TemporaryDirectory() as d:
    E.venv_dir = lambda: __import__("pathlib").Path(d) / "venv"
    vp = E.venv_python()
    check(str(vp).startswith(d), "venv 위치")
    check(any(str(c) == str(vp) for c in remote.candidates({})), "분석 환경 후보에 전용 venv 포함")

    # 파이썬이 없고 winget 도 없으면 설치 방법을 안내하는 오류
    E.find_system_python = lambda: None
    E.shutil.which = lambda n: None
    try:
        E.install(lambda *_a: None, lambda *_a: None, None, runner=lambda *a, **k: 0)
        check(False, "파이썬 없으면 오류여야 함")
    except RuntimeError as e:
        check("python.org" in str(e), "파이썬 없을 때 안내: %s" % e)

    # 설치 순서: venv 만들기 -> pip 갱신 -> torch -> 나머지 -> 확인
    calls = []

    def fake_runner(cmd, log, cancel=None, env=None):
        calls.append(list(cmd))
        if "venv" in cmd:
            vp.parent.mkdir(parents=True, exist_ok=True)
            vp.write_text("")
        return 0

    E.find_system_python = lambda: sys.executable
    E.has_nvidia = lambda: False

    class _R:
        returncode, stdout, stderr = 0, "2.5.0\n", ""

    E.subprocess.run = lambda *a, **k: _R()
    out = E.install(lambda *_a: None, lambda *_a: None, threading.Event(), runner=fake_runner)
    check(out == str(vp), "설치 결과 = venv 파이썬")
    kinds = ["venv" if "venv" in c else "torch" if "torch" in c else "pkgs" if "ultralytics" in c else "pip" for c in calls]
    check(kinds == ["venv", "pip", "torch", "pkgs"], "설치 순서: %s" % kinds)

    # torch 설치 실패는 이어서 설치 안내와 함께 오류
    def bad_runner(cmd, log, cancel=None, env=None):
        return 1 if "torch" in cmd else fake_runner(cmd, log, cancel)

    try:
        E.install(lambda *_a: None, lambda *_a: None, None, runner=bad_runner)
        check(False, "torch 실패는 오류")
    except RuntimeError as e:
        check("이어서" in str(e), "torch 실패 안내: %s" % e)

print("모두 통과" if not fails else "실패 %d개" % len(fails))
sys.exit(1 if fails else 0)
