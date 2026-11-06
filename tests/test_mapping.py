import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge.core import quat as Q  # noqa: E402
from motionbridge.core.mapping import auto_map, classify_name, detect_frame, frame_from_axes, refine_frame_with_anim, SLOT_ORDER  # noqa: E402
from motionbridge.core.model import Animation, Skeleton, fk, world_to_local  # noqa: E402
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


def test_prefers_standard_names():
    """소품 본(Dwarf_l_sheepArmR)이 진짜 팔(upperarm_l)보다 얕아도 표준 이름이 이긴다."""
    names = ["root", "pelvis", "Dwarf_l_sheepArmR", "Dwarf_l_sheepArmL", "spine_01", "upperarm_l", "upperarm_r", "head", "neck_01", "thigh_l", "thigh_r"]
    par = [-1, 0, 0, 0, 1, 4, 4, 4, 4, 1, 1]
    sk = Skeleton(names, par, np.zeros((len(names), 3)))
    m = auto_map(sk)
    assert names[m["L_UpperArm"]] == "upperarm_l" and names[m["R_UpperArm"]] == "upperarm_r", {k: names[v] for k, v in m.items()}
    # 표준 이름이 없으면 그래도 소품 본이라도 쓴다 (매핑을 비우지 않음)
    sk2 = Skeleton(["root", "Dwarf_l_sheepArmR"], [-1, 0], np.zeros((2, 3)))
    assert "L_UpperArm" in auto_map(sk2)
    print("standard names preferred OK")


def test_hunched_character_frame():
    """구부정한 캐릭터(드워프 실측: 골반→머리가 앞으로 19° 기욺)도 상방이 정확히 축이어야 한다. 아니면 모션이 대각선으로 솟는다."""
    def build(head_dz, spine_dz):
        spec = [("root", -1, (0, 0, 0)), ("pelvis", 0, (0, 1.0, 0)), ("spine_01", 1, (0, 1.2, spine_dz)), ("head", 2, (0, 1.65, head_dz)),
                ("thigh_l", 1, (0.1, 0.95, 0)), ("calf_l", 4, (0.1, 0.5, 0)), ("foot_l", 5, (0.1, 0.05, 0)),
                ("thigh_r", 1, (-0.1, 0.95, 0)), ("calf_r", 7, (-0.1, 0.5, 0)), ("foot_r", 8, (-0.1, 0.05, 0))]
        names = [n for n, _, _ in spec]
        par = [p for _, p, _ in spec]
        pos = np.array([q for _, _, q in spec], float)
        off = pos.copy()
        for i, p in enumerate(par):
            if p >= 0:
                off[i] = pos[i] - pos[p]
        return Skeleton(names, par, off)

    for head_dz in (0.0, -0.25, -0.35, 0.35):  # 0.35/0.65 ≈ 28° (몸통 기준 15° 한계를 훌쩍 넘음)
        sk = build(head_dz, head_dz * 0.4)
        F, desc = detect_frame(sk, auto_map(sk))
        up = Q.rotate(F, np.array([0.0, 1.0, 0.0]))
        # 표준 프레임에서 월드 +Y 는 정확히 표준 상방(+Y)이어야 한다 (기울지 않음)
        assert np.allclose(Q.rotate(F, np.array([0.0, 1.0, 0.0])), [0, 1, 0], atol=1e-6), (head_dz, up, desc)
        assert "+Y" in desc, desc
    # 다리를 못 쓰면 몸통 기준 35° 까지 축으로 맞춘다 (발이 없는 리그)
    print("hunched frame OK")


def test_refine_frame():
    sk, meta = synth.build_skeleton("mixamo", "y_up_z", 1.0, "T", "aligned")
    slots = auto_map(sk)
    anim, _ = synth.walk_animation(sk, meta, n_frames=30)
    F0, d0 = detect_frame(sk, slots)
    F, d, note = refine_frame_with_anim(sk, anim, slots, F0, d0)
    assert note is None and F is F0, "멀쩡한 클립을 건드림"
    # 클립 전체를 X 축으로 90도 눕힌다 (축이 나간 클립)
    wr, wp = fk(sk, anim.rot, anim.pos)
    R = Q.from_axis_angle([1, 0, 0], np.radians(90.0))
    wr2, wp2 = Q.mul(R, wr), Q.rotate(R, wp)
    lr, lp = world_to_local(sk, wr2, wp2)
    bad = Animation("lying", anim.fps, lr, lp)
    F2, d2, note = refine_frame_with_anim(sk, bad, slots, F0, d0)
    assert note and "클립" in d2, (d2, note)
    # 고친 좌표계로 보면 몸통이 서 있다
    wr3, wp3 = fk(sk, bad.rot, bad.pos)
    up = Q.rotate(np.broadcast_to(F2, (len(wp3), 4)), wp3[:, slots["Head"]] - wp3[:, slots["Hips"]])
    up = up / np.linalg.norm(up, axis=1)[:, None]
    assert float(up[:, 1].min()) > 0.9, up[:, 1].min()
    print("refine frame OK")


def test_axes_quick_fix():
    import itertools

    from motionbridge.core import mapping as M

    n = 0
    for u, f in itertools.product(M.AXES, M.AXES):
        if abs(float(np.dot(M.AXES[u], M.AXES[f]))) > 1e-6:
            continue
        assert M.axes_of_frame(M.frame_from_axes(u, f)) == (u, f), (u, f)
        for op in ("yaw90", "yaw180", "flip_up", "pitch90"):
            a, b = M.turn_axes(u, f, op)
            assert abs(float(np.dot(M.AXES[a], M.AXES[b]))) < 1e-6, (u, f, op)
        assert M.turn_axes(*M.turn_axes(*M.turn_axes(*M.turn_axes(u, f, "yaw90"), "yaw90"), "yaw90"), "yaw90") == (u, f)
        assert M.turn_axes(*M.turn_axes(u, f, "yaw180"), "yaw180") == (u, f) and M.turn_axes(*M.turn_axes(u, f, "flip_up"), "flip_up") == (u, f)
        n += 1
    assert n == 24
    try:
        M.turn_axes("+Y", "+Z", "zzz")
        raise AssertionError("알 수 없는 동작은 오류")
    except ValueError:
        pass


def test_diagnose_frame():
    from motionbridge.core import mapping as M

    sk0 = Skeleton(["a", "b"], [-1, 0], np.zeros((2, 3)))
    notes = M.diagnose_frame(sk0, {})
    assert notes and "골반" in notes[0] and "기본값" in notes[0], notes  # 매핑이 없으면 이유를 알려 준다
    sk, meta = synth.build_skeleton("mixamo", "y_up_z", 1.0, "T", "aligned")
    slots = auto_map(sk)
    assert M.diagnose_frame(sk, slots) == [], "정상 리그는 문제 없음"
    # 일부 매핑만 있으면 (머리 없음)
    part = {k: v for k, v in slots.items() if k not in ("Head", "Neck", "Spine2")}
    assert any("상방" in n for n in M.diagnose_frame(sk, part))
    # 리그 전체가 비스듬하게 기울어 있으면 이유를 알려 준다 (루트를 X 로 40° 눕힘)
    sk2, meta2 = synth.build_skeleton("mixamo", "y_up_z", 1.0, "T", "aligned")
    sk2.rest_rot[0] = Q.mul(Q.from_axis_angle(np.array([1.0, 0, 0]), np.radians(40)), sk2.rest_rot[0])
    ns = M.diagnose_frame(sk2, auto_map(sk2))
    assert ns and any("벗어나" in n or "기울" in n for n in ns), ns


if __name__ == "__main__":
    test_prefers_standard_names()
    test_hunched_character_frame()
    test_refine_frame()
    test_names()
    test_frames()
    test_axes_quick_fix()
    test_diagnose_frame()
    print("test_mapping ALL OK")
