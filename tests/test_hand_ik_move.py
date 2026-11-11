"""손 IK 가 걸린 구간에서 손을 끌어 옮기면 (발과 똑같이) IK 목표가 옮겨져서 되돌려지지 않는다. 포지션 분리를 켜면 그 본만."""
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
        hand = mw.pr.tgt_slots["L_Hand"]
        lab = [c.label for c in mw.pr.chains() if c.kind == "hand" and int(c.foot) == hand][0]
        f = 12
        mw.frame_slider.setValue(f)
        mw.viewport.frame = f
        assert not mw._foot_ik_on(hand), "바닥에 닿는 구간이 없으면 손 IK 는 안 걸림 -> 회전 방식"
        mw.pr.settings.ik_ranges[mw.pr.clip] = {lab: [[4, 30, True]]}
        mw.recompute()
        assert mw._foot_ik_on(hand), "직접 켠 손 IK 구간이 있으면 걸림"
        P0 = np.array(mw.vd.tgt_pos, float)
        dv = np.array([0.0, 0.0, 0.05])
        mw._ik_drag = None
        mw._bone_drag_started()
        mw._bone_moved(hand, dv)
        mw.recompute()
        P1 = np.array(mw.vd.tgt_pos, float)
        sh = np.linalg.norm(P1[:, hand] - P0[:, hand], axis=-1)
        assert mw.pr.settings.ik_offsets.get(hand) is not None and not mw.pr.settings.rot_offsets, "손 IK 목표로 기록"
        dz = (P1 - P0)[:, hand, 2]
        # 이 합성 팔은 바닥까지 다 펴져도 못 닿는 상태라(팔이 한계까지 펴짐) 옮긴 만큼 다 가지는 못하지만, 옮긴 방향(+Z)으로 따라와야 한다 (예전엔 IK 가 되돌려 0)
        assert dz[8:26].min() > 0.008 and dz[8] > 0.03 and sh[f] > 0.03, "IK 구간 전체에서 옮긴 쪽으로 따라옴: %s" % np.round(dz[8:26], 3)
        assert abs(dz[0]) < 1e-3 and abs(dz[-1]) < 1e-3, "IK 구간 밖은 그대로 (구간: 4~30)"
        # 포지션 분리: 그 본만 (IK 목표는 안 건드림)
        mw.pr.settings.ik_offsets.clear()
        mw.cb_pos_only.setChecked(True)
        mw._bone_moved(hand, dv)
        assert not mw.pr.settings.ik_offsets and mw.pr.settings.pos_offsets.get(hand) is not None, "포지션 분리: 위치 보정으로 기록"
        print("test_hand_ik_move ALL OK")


main()
