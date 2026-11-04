import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge.core import quat as Q  # noqa: E402
from motionbridge.core.mapping import auto_map, classify_name, detect_frame, frame_from_axes, SLOT_ORDER  # noqa: E402
import synth  # noqa: E402

EXPECT = {
    "Hips": "hips", "Spine": "spine", "Spine1": "spine1", "Spine2": "spine2", "Neck": "neck", "Head": "head",
    "L_Shoulder": "shoulder@L", "L_UpperArm": "upperarm@L", "L_LowerArm": "lowerarm@L", "L_Hand": "hand@L",
    "L_UpLeg": "upleg@L", "L_LowLeg": "lowleg@L", "L_Foot": "foot@L", "L_Toe": "toe@L",
    "R_Shoulder": "shoulder@R", "R_UpperArm": "upperarm@R", "R_LowerArm": "lowerarm@R", "R_Hand": "hand@R",
    "R_UpLeg": "upleg@R", "R_LowLeg": "lowleg@R", "R_Foot": "foot@R", "R_Toe": "toe@R",
}


def test_names():
    for style in ("mixamo", "ue", "biped"):
        sk, meta = synth.build_skeleton(style, "y_up_z", 1.0, "T", "aligned")
        m = auto_map(sk)
        for slot, key in EXPECT.items():
            assert slot in m, (style, "슬롯 없음", slot)
            assert m[slot] == meta["idx"][key], (style, slot, sk.names[m[slot]], "기대", sk.names[meta["idx"][key]])
        if style in ("mixamo", "ue"):
            for side in "LR":
                for i, k in enumerate(("middle1", "middle2", "middle3"), 1):
                    slot = "%s_Middle%d" % (side, i)
                    assert m.get(slot) == meta["idx"][k + "@" + side], (style, slot, m.get(slot))
        # 끝 본(End/Nub)은 매핑되면 안 된다
        mapped = set(m.values())
        for j, nm in enumerate(sk.names):
            if nm.lower().endswith(("_end", "nub", "end_l", "end_r", "_end_l", "_end_r")) or "end" in nm.lower().split("_")[-2:]:
                assert j not in mapped, (style, "끝 본이 매핑됨", nm)
    # 알려진 이름 몇 개
    assert classify_name("mixamorig:LeftForeArm")[:2] == ("L", "LowerArm")
    assert classify_name("mixamorig:LeftArm")[:2] == ("L", "UpperArm")
    assert classify_name("upperarm_twist_01_l") is None
    assert classify_name("Bip001 R Calf")[:2] == ("R", "LowLeg")
    assert classify_name("thigh.L")[:2] == ("L", "UpLeg")
    assert classify_name("DEF-upper_arm.R")[:2] == ("R", "UpperArm")
    print("name mapping OK")


def test_frames():
    for frame in ("y_up_z", "z_up_x", "z_up_negy"):
        for style in ("mixamo", "ue", "biped"):
            sk, meta = synth.build_skeleton(style, "y_up_z" if False else frame, 1.0, "A", "bone_x")
            m = auto_map(sk)
            res = detect_frame(sk, m)
            assert res is not None
            F, desc = res
            # 월드 -> 표준 프레임. 표준 프레임 좌표는 원래 합성 좌표와 같아야 한다 (G^T 적용).
            _, bwp = sk.bind_world()
            G = meta["G"]
            expect = bwp @ G  # 파일 -> 표준
            got = Q.rotate(F, bwp)
            assert np.allclose(got, expect, atol=1e-9), (frame, style, desc)
    # 수동 축
    F = frame_from_axes("+Z", "+X")
    assert np.allclose(Q.rotate(F, np.array([0.0, 0.0, 1.0])), [0, 1, 0], atol=1e-9)
    assert np.allclose(Q.rotate(F, np.array([1.0, 0.0, 0.0])), [0, 0, 1], atol=1e-9)
    assert np.allclose(Q.rotate(F, np.array([0.0, 1.0, 0.0])), [1, 0, 0], atol=1e-9)  # 왼쪽 = 상 x 전 = Z x X = +Y
    print("frames OK")


if __name__ == "__main__":
    test_names()
    test_frames()
    print("test_mapping ALL OK")
