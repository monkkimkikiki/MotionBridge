"""화면: 다른 본 구조 모션을 열어도 확인창 없이 목록에 올라가고, 고르면 소스 구조(매핑 표)가 바뀐다."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qt_stub  # noqa: E402

qt_stub.install()
import synth  # noqa: E402
import test_pipeline as TP  # noqa: E402
from qt_stub import QFileDialog, QMessageBox  # noqa: E402
from motionbridge.ui import app as A  # noqa: E402


def main():
    with tempfile.TemporaryDirectory() as d:
        os.environ["HOME"] = d
        from motionbridge.core import bridge
        bridge.CONFIG_PATH = os.path.join(d, "cfg.json")
        src, src2, tgt, _a, _b = TP.make_inputs(d)
        sk2, meta2 = synth.build_skeleton("ue", "z_up_x", 100.0, "A", "bone_x", height=1.3)
        an2, _ = synth.walk_animation(sk2, meta2, n_frames=30, name="other_walk")
        other = os.path.join(d, "other.glb")
        TP.write_glb(other, sk2, an2)
        asked = []
        QMessageBox.question = staticmethod(lambda *a, **k: asked.append(a) or QMessageBox.StandardButton.No)
        mw = A.MainWindow()
        QFileDialog.open_names_ret = ([src], "")
        mw.open_source()
        QFileDialog.open_ret = (tgt, "")
        mw.open_target()
        n_a = mw.pr.src.skeleton.n
        QFileDialog.open_names_ret = ([other], "")
        mw.open_source()
        assert not asked, "다른 본 구조라고 묻는 확인창이 뜨면 안 됨: %s" % asked
        assert len(mw.pr.src.animations) == 2 and mw.clip_combo.count() == 2, "목록에 둘 다 올라감"
        assert mw.pr.clip == 1 and mw.pr.src.skeleton.n == sk2.n != n_a, "새로 연 모션이 골라지고 그 구조로 바뀜"
        assert mw.vd is not None and mw.vd.n_frames == 30
        mw.clip_combo.setCurrentIndex(0)
        assert mw.pr.clip == 0 and mw.pr.src.skeleton.n == n_a and mw.vd.n_frames == 40, "되돌아가면 원래 구조"
        print("test_ui_mixed ALL OK")


main()
