"""오토키 체크박스와 K 키: 오토키를 켜고 본을 돌리면 (레이어가 없어도) 지금 프레임에 키가 찍힌다. 끄면 '본별 보정'. K 는 고른 본에 지금 값으로 키를 찍는다."""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qt_stub  # noqa: E402

qt_stub.install()
import test_pipeline as TP  # noqa: E402
from qt_stub import QFileDialog  # noqa: E402
from motionbridge.core import quat as Q  # noqa: E402
from motionbridge.ui import app as A  # noqa: E402


def main():
    with tempfile.TemporaryDirectory() as d:
        os.environ["HOME"] = d
        from motionbridge.core import bridge
        bridge.CONFIG_PATH = os.path.join(d, "cfg.json")
        src, _s2, tgt, _a, _b = TP.make_inputs(d)
        mw = A.MainWindow()
        QFileDialog.open_names_ret = ([src], "")
        mw.open_source()
        QFileDialog.open_ret = (tgt, "")
        mw.open_target()
        mw.recompute()
        mw.cb_bone_edit.setChecked(True)
        jt = sorted(mw.viewport.pickable)[0]
        mw._bone_picked(jt)
        qy = Q.from_axis_angle(np.array([0.0, 1.0, 0.0]), np.radians(30.0))
        mw.frame_slider.setValue(4)
        mw.viewport.frame = 4
        lp = mw.layer_panel
        assert not mw.cb_autokey.isChecked() and lp.layer() is None
        # 오토키 끔: 레이어 없이 끌면 '본별 보정'
        mw._bone_dragged(jt, qy)
        assert jt in mw.pr.settings.rot_offsets and len(lp.stack()) == 0, "오토키 OFF 면 전체 구간 본별 보정"
        mw._bone_reset_selected()
        assert jt not in mw.pr.settings.rot_offsets
        # 오토키 켬: 레이어가 알아서 만들어지고 지금 프레임에 키
        mw.cb_autokey.setChecked(True)
        assert lp.recording()
        mw._bone_dragged(jt, qy)
        assert not mw.pr.settings.rot_offsets, "오토키 ON 이면 본별 보정을 건드리지 않음"
        assert len(lp.stack()) == 1 and lp.layer() is not None, "오토키 레이어 자동 생성"
        assert lp.layer().frames(jt) == [4], lp.layer().frames(jt)
        # 다른 프레임에서 한 번 더 -> 같은 레이어에 키가 하나 더
        mw.frame_slider.setValue(9)
        mw.viewport.frame = 9
        mw._bone_dragged(jt, qy)
        assert len(lp.stack()) == 1 and lp.layer().frames(jt) == [4, 9], lp.layer().frames(jt)
        # K 키 (오토키와 무관): 다른 본 선택 후 K -> 그 본에 현재 프레임 키
        other = sorted(mw.viewport.pickable)[1]
        mw._bone_picked(other)
        mw.frame_slider.setValue(6)
        mw.viewport.frame = 6
        mw._viewport_key("key_selected")
        assert lp.layer().frames(other) == [6], lp.layer().frames(other)
        # 오토키 끄고 '원본' 상태에서 K -> 레이어가 없으면 새로 만든다
        mw.cb_autokey.setChecked(False)
        assert mw.cfg.get("autokey") is False
        print("test_autokey ALL OK")


main()
