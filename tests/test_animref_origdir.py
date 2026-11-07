"""AnimRef 설정 탭의 '원본 위치 열기': 어느 폴더를 여는지 (가짜 Qt)."""
import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qt_stub  # noqa: E402

qt_stub.install()
from motionbridge.animref.ui import settings_tab as T  # noqa: E402

fails = []


def check(c, m):
    if not c:
        fails.append(m)
        print("FAIL", m)


with tempfile.TemporaryDirectory() as d:
    os.environ["HOME"] = d
    obs, last = os.path.join(d, "obs"), os.path.join(d, "last")
    os.makedirs(obs)
    os.makedirs(last)
    asked = []
    main = types.SimpleNamespace(s={"obs_record_dir": obs, "last_dir": last}, collect=types.SimpleNamespace(remember_obs_dir=lambda: asked.append(1)))
    tab = T.SettingsTab.__new__(T.SettingsTab)
    tab.main = main
    check(tab.original_dir("obs") == obs and not asked, "OBS 녹화 폴더를 연다")
    check(tab.original_dir("dropped") == last, "직접 넣은 영상은 마지막에 연 폴더")
    main.s["obs_record_dir"] = os.path.join(d, "없는폴더")
    check(tab.original_dir("obs") == last and asked, "OBS 폴더가 없으면 OBS 에 물어보고, 그래도 없으면 마지막 폴더")
    main.s.update({"obs_record_dir": "", "last_dir": ""})
    check(tab.original_dir("obs") == "" or os.path.isdir(tab.original_dir("obs")), "아무것도 없으면 영상 폴더 후보 또는 빈 값")
    opened = []
    T.open_path = opened.append
    main.s["obs_record_dir"] = obs
    tab._open_original_dir("obs")
    check(opened == [obs], "열기 호출: %s" % opened)

print("모두 통과" if not fails else "실패 %d개" % len(fails))
sys.exit(1 if fails else 0)
