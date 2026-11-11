"""추출 탭의 '같은 종류 모션 모으기' 버튼과 에셋 찾기의 추출→모으기 흐름 (가짜 Qt)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qt_stub  # noqa: E402

qt_stub.install()
from motionbridge.ui import app as A  # noqa: E402


def main():
    with tempfile.TemporaryDirectory() as d:
        os.environ["HOME"] = d
        from motionbridge.core import bridge
        bridge.CONFIG_PATH = os.path.join(d, "cfg.json")
        mw = A.MainWindow()
        out = os.path.join(d, "out")
        mw.ex_out.setText(out)
        f = os.path.join(out, "G", "Orc", "Animations", "Attacks", "A_Orc_Attacks_Smash_01.psa")
        os.makedirs(os.path.dirname(f))
        open(f, "wb").write(b"abc")
        # 목록이 비어 있어도 출력 폴더를 훑어서 모은다
        mw.ex_files.set_paths([])
        mw._collect_extracted()
        assert os.path.exists(os.path.join(out, "Library", "attack", "Smash", "A_Orc_Attacks_Smash_01.psa")), os.listdir(out)
        # 추출 직후 모으기 플래그
        g = os.path.join(out, "G", "Orc", "Animations", "Locomotion", "A_Orc_Locomotion_Run.psa")
        os.makedirs(os.path.dirname(g))
        open(g, "wb").write(b"run")
        mw._collect_after = True
        mw._after_extract([g])
        assert os.path.exists(os.path.join(out, "Library", "locomotion", "Run", "A_Orc_Locomotion_Run.psa"))
        assert not mw.__dict__.get("_collect_after")
        print("test_ui_library ALL OK")


main()
