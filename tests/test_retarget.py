import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge.core import quat as Q  # noqa: E402
from motionbridge.core.mapping import auto_map, pairs_from_slots  # noqa: E402
from motionbridge.core.model import fk  # noqa: E402
from motionbridge.core.quality import analyze  # noqa: E402
from motionbridge.core.retarget import RetargetSettings, default_chains, retarget  # noqa: E402
import synth  # noqa: E402


def setup(src_cfg, tgt_cfg, anim_kw=None, st=None, n_frames=60):
    ssk, smeta = synth.build_skeleton(**src_cfg)
    tsk, tmeta = synth.build_skeleton(**tgt_cfg)
    an, truth = synth.walk_animation(ssk, smeta, n_frames=n_frames, **(anim_kw or {}))
    sm, tm = auto_map(ssk), auto_map(tsk)
    pairs = pairs_from_slots(sm, tm)
    chains = default_chains(sm, tm)
    res = retarget(ssk, an, tsk, pairs, st or RetargetSettings(foot_ik=False), chains, sm, tm)
    return ssk, an, tsk, res, sm, tm, smeta, tmeta


def canon(skel, anim, F):
    wr, wp = fk(skel, anim.rot, anim.pos)
    return Q.rotate(F, wp), wr


def unit(v):
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12)


COMBOS = [
    (dict(style="mixamo", frame="y_up_z", scale=1.0, pose="T", local_axes="aligned"),
     dict(style="ue", frame="z_up_x", scale=100.0, pose="A", local_axes="bone_x", height=1.1, leg_scale=1.15)),
    (dict(style="ue", frame="z_up_x", scale=100.0, pose="A", local_axes="bone_x"),
     dict(style="biped", frame="z_up_negy", scale=100.0, pose="T", local_axes="bone_x", height=0.9)),
    (dict(style="biped", frame="z_up_negy", scale=1.0, pose="T", local_axes="aligned"),
     dict(style="mixamo", frame="y_up_z", scale=1.0, pose="A", local_axes="bone_x", height=1.3)),
]


def test_fk_invariants():
    """IK 없이: 매핑된 본의 방향이 소스와 같고, 골반 궤적은 스케일된 소스 궤적과 같다."""
    for ci, (sc, tc) in enumerate(COMBOS):
        ssk, an, tsk, res, sm, tm, smeta, tmeta = setup(sc, tc)
        pc, wr = canon(tsk, res.anim, res.frame_tgt[0])
        ps = res.src_canon_pos
        pair_of = {t: s for s, t in res.pairs}
        assert len(res.primary) >= 15, "주 자식이 너무 적음: %d" % len(res.primary)
        worst = 0.0
        for t, d in res.primary.items():
            dt = unit(pc[:, d] - pc[:, t])
            ds = unit(ps[:, pair_of[d]] - ps[:, pair_of[t]])
            err = np.linalg.norm(dt - ds, axis=1).max()
            worst = max(worst, err)
            assert err < 1e-5, ("본 방향 불일치", ci, tsk.names[t], tsk.names[d], err)
        # 골반 위치
        _, bwp_s = ssk.bind_world()
        _, bwp_t = tsk.bind_world()
        bps = Q.rotate(res.frame_src[0], bwp_s)
        bpt = Q.rotate(res.frame_tgt[0], bwp_t)
        com = res.com
        assert tsk.names[com] == tsk.names[tm["Hips"]]
        exp = bpt[com] + res.k * (ps[:, pair_of[com]] - bps[pair_of[com]])
        assert np.allclose(pc[:, com], exp, atol=1e-6), "COM 위치 %g" % np.abs(pc[:, com] - exp).max()
        # 진행 방향: 표준 프레임 +Z
        travel = pc[-1, com] - pc[0, com]
        assert travel[2] > 0.3 * res.k and abs(travel[0]) < 1e-6, ("진행 방향", travel)
        q = np.linalg.norm(res.anim.rot, axis=-1)
        assert np.allclose(q, 1.0, atol=1e-9)
        print("  조합 %d: 본 방향 최대 오차 %.2e, k=%.3f, %s | %s" % (ci, worst, res.k, res.frame_src[1], res.frame_tgt[1]))
    print("FK invariants OK")


IK_TGT = dict(style="ue", frame="z_up_x", scale=100.0, pose="A", local_axes="bone_x", height=1.1, knee_frac=0.3)


def slide_metric(tsk, res, ch_i, anim=None):
    """접지 중 연속 프레임 사이 수평 이동 최대값, 다리 길이, 접지쌍 수, 전체 프레임 최대 이동."""
    pc, _ = canon(tsk, anim or res.anim, res.frame_tgt[0])
    ch, pl = res.chains[ch_i], res.plans[ch_i]
    foot = pc[:, ch.foot]
    both = pl.contact[1:] & pl.contact[:-1]
    d = np.linalg.norm(np.diff(foot[:, [0, 2]], axis=0), axis=1)
    return (d[both].max() if both.any() else 0.0), pl.leg_length, int(both.sum()), d.max()


def _dip_setup(hand_ik, dip=1.40):
    """소스 골반을 20~39 프레임 동안 크게 낮춰 손이 바닥(높이 0) 아래로 내려가게 만든다."""
    sc = COMBOS[0][0]
    ssk, smeta = synth.build_skeleton(**sc)
    tsk, tmeta = synth.build_skeleton(**IK_TGT)
    an, _ = synth.walk_animation(ssk, smeta, n_frames=60)
    sm, tm = auto_map(ssk), auto_map(tsk)
    h = sm["Hips"]
    t = np.arange(60)
    w = np.clip(np.minimum(t - 14, 45 - t) / 6.0, 0.0, 1.0)      # 14~45 사이를 부드럽게 오르내리는 가중치
    up = np.array(an.pos, dtype=float)
    # 소스는 y-up(mixamo): 골반 로컬 위치의 Y 를 낮춘다 (골반이 루트 바로 아래라서 월드 Y 와 같다)
    up[:, h, 1] -= dip * w
    an.pos = up
    st = RetargetSettings(foot_ik=False, hand_ik=hand_ik)
    res = retarget(ssk, an, tsk, pairs_from_slots(sm, tm), st, default_chains(sm, tm), sm, tm)
    return ssk, an, tsk, res, sm, tm


def test_hand_ik():
    """손 IK: 원본 손 높이가 0 이하일 때만 그 팔에 IK 가 걸리고, 0 에서 벗어나면 풀린다 (손이 안 닿는 동작에는 아무 영향 없음)."""
    # 1) 손이 바닥에 안 닿는 걷기: 손 IK 켜도 결과가 완전히 같다
    sc = COMBOS[0][0]
    a = setup(sc, IK_TGT, st=RetargetSettings(foot_ik=False, hand_ik=False))[3]
    b = setup(sc, IK_TGT, st=RetargetSettings(foot_ik=False, hand_ik=True))[3]
    assert np.allclose(a.anim.rot, b.anim.rot) and not b.ik_applied, "손이 안 닿는데 IK 가 걸림"
    # 2) 골반을 낮춘 동작
    ssk, an, tsk, off, sm, tm = _dip_setup(False)
    _, _, _, on, _, _ = _dip_setup(True)
    assert not off.ik_applied and on.ik_applied
    hands = [(c, pl) for c, pl in zip(on.chains, on.plans) if c.kind == "hand"]
    assert len(hands) == 2
    pc_on, _ = canon(tsk, on.anim, on.frame_tgt[0])
    pc_off, _ = canon(tsk, off.anim, off.frame_tgt[0])
    _, bwp_t = tsk.bind_world()
    bpt = Q.rotate(on.frame_tgt[0], bwp_t)
    ground_t = min(bpt[t, 1] for t in set(t for _s, t in on.pairs))
    for ch, pl in hands:
        c = pl.contact
        assert c[25:35].all(), "깊이 내려간 구간은 접촉이어야 함"
        assert not c[:8].any() and not c[52:].any(), "서 있는 구간은 접촉이 아니어야 함"
        w = pl.weight
        # 접촉 한가운데: 손이 고정(수평 이동 없음)되고 바닥 아래로 파고들지 않음
        hand = pc_on[:, ch.foot]
        mid = hand[27:33]
        # (걷기 팔 흔들림 때문에 손이 수평으로 움직이는 구간은 고정하지 않고 따라간다 — 고정은 test_hand_ik_plant 에서)
        assert hand[:, 1][w > 0.999].min() >= ground_t - 0.01, "손이 바닥을 뚫음"   # 단위가 cm 라 0.01 = 0.1mm
        # 접촉에서 벗어난 프레임은 IK 없는 결과와 같다 (풀림)
        free = w < 1e-9
        assert free.any()
        for j in (ch.upper, ch.lower, ch.foot):
            assert np.allclose(on.anim.rot[free][:, j], off.anim.rot[free][:, j], atol=1e-9) or \
                np.allclose(on.anim.rot[free][:, j], -off.anim.rot[free][:, j], atol=1e-9), "벗어났는데 IK 가 남음"
    # 3) 설정으로 끄면 다시 같다
    assert np.allclose(off.anim.rot, _dip_setup(False)[3].anim.rot)
    print("hand IK OK")


def _hand_case(src_cfg, tgt_cfg, still_arms=False, hand_ik=True):
    """소스 골반을 낮춰 프레임 14~45 에서 손이 바닥 높이(≈2% 팔 길이)까지 내려오게 한다."""
    ssk, smeta = synth.build_skeleton(**src_cfg)
    tsk, _ = synth.build_skeleton(**tgt_cfg)
    an, _ = synth.walk_animation(ssk, smeta, n_frames=60)
    sm, tm = auto_map(ssk), auto_map(tsk)
    pairs, ch = pairs_from_slots(sm, tm), default_chains(sm, tm)
    base = retarget(ssk, an, tsk, pairs, RetargetSettings(foot_ik=False, hand_ik=False), ch, sm, tm)
    Fs = base.frame_src[0]
    upv = Q.rotate(Q.conj(Fs), np.array([0.0, 1.0, 0.0]))
    _, bwp_s = ssk.bind_world()
    bps = Q.rotate(Fs, bwp_s)
    gs = min(bps[sm[k], 1] for k in sm)
    if still_arms:       # 모든 본을 바인드 그대로 고정 → 손은 골반과 함께 수직으로만 움직인다
        an.rot = np.repeat(np.asarray(ssk.rest_rot, dtype=float)[None], an.n_frames, axis=0)
    _, wp = fk(ssk, an.rot, an.pos)
    ps = Q.rotate(Fs, wp)
    hj = [c.src_foot for c in ch if c.kind == "hand"]
    L = float(np.linalg.norm(bps[hj[0]] - bps[sm["Hips"]]))
    drop = min(ps[30, j, 1] for j in hj) - gs - 0.02 * L
    t = np.arange(60)
    w = np.clip(np.minimum(t - 14, 45 - t) / 6.0, 0.0, 1.0)
    wrr, _ = fk(ssk, an.rot, an.pos)
    h = sm["Hips"]
    dw = -(drop * w)[:, None] * upv[None]
    if ssk.parents[h] >= 0:
        dw = Q.rotate(Q.conj(wrr[:, ssk.parents[h]]), dw)
    pos = np.array(an.pos, dtype=float)
    if still_arms:       # 골반도 제자리 (걷는 이동량이 손 속도에 섞이지 않게)
        pos[:, h] = pos[0, h]
    pos[:, h] += dw
    an.pos = pos
    off = retarget(ssk, an, tsk, pairs, RetargetSettings(foot_ik=False, hand_ik=False), ch, sm, tm)
    on = retarget(ssk, an, tsk, pairs, RetargetSettings(foot_ik=False, hand_ik=hand_ik), ch, sm, tm)
    _, wp = fk(ssk, an.rot, an.pos)
    ps = Q.rotate(Fs, wp)
    _, bwp_t = tsk.bind_world()
    bpt = Q.rotate(on.frame_tgt[0], bwp_t)
    gt = min(bpt[tm[k], 1] for k in tm)
    return ssk, tsk, on, off, gs, gt, ps


def test_ik_off_shows_nothing():
    """발 IK 를 꺼도 뷰포트에 IK 접지 점(초록)이 계속 보이던 문제: 적용되지 않은 체인은 plan.applied=False 이고 뷰어 데이터에 들어가지 않는다."""
    ssk, smeta = synth.build_skeleton(**COMBOS[0][0])
    tsk, _ = synth.build_skeleton(**IK_TGT)
    an, _ = synth.walk_animation(ssk, smeta, n_frames=40)
    sm, tm = auto_map(ssk), auto_map(tsk)
    pairs, ch = pairs_from_slots(sm, tm), default_chains(sm, tm)
    off = retarget(ssk, an, tsk, pairs, RetargetSettings(foot_ik=False, hand_ik=False), ch, sm, tm)
    assert not off.ik_applied and not any(p.applied for p in off.plans)
    on = retarget(ssk, an, tsk, pairs, RetargetSettings(foot_ik=True, hand_ik=False), ch, sm, tm)
    assert on.ik_applied and all(p.applied for c, p in zip(on.chains, on.plans) if c.kind == "foot")
    # 손만 켜고 손이 바닥에 안 닿으면 아무것도 적용 안 됨
    hand = retarget(ssk, an, tsk, pairs, RetargetSettings(foot_ik=False, hand_ik=True), ch, sm, tm)
    assert not any(p.applied for p in hand.plans)
    print("ik off OK")


def test_scale_independent_of_frame():
    """눕히기/세우기/위아래 반전으로 좌표축을 돌려도 스케일(k)은 같아야 한다 (몸 자체의 위쪽 기준으로 계산)."""
    from motionbridge.core.mapping import axes_of_frame, turn_axes
    ssk, smeta = synth.build_skeleton(**COMBOS[0][0])
    tsk, _ = synth.build_skeleton(**IK_TGT)
    an, _ = synth.walk_animation(ssk, smeta, n_frames=20)
    sm, tm = auto_map(ssk), auto_map(tsk)
    pairs = pairs_from_slots(sm, tm)
    r0 = retarget(ssk, an, tsk, pairs, RetargetSettings(foot_ik=False), [], sm, tm)
    up, fwd = axes_of_frame(r0.frame_src[0])
    for side in ("src", "tgt"):
        for op in ("pitch90", "yaw90", "yaw180", "flip_up", "pitch90"):
            u, f = turn_axes(up, fwd, op)
            kw = {side + "_up": u, side + "_fwd": f}
            r = retarget(ssk, an, tsk, pairs, RetargetSettings(foot_ik=False, **kw), [], sm, tm)
            assert abs(r.k - r0.k) < 1e-9 * max(r0.k, 1.0), (side, op, r.k, r0.k)
    print("scale independent of frame OK")


def test_hand_ik_bind_pose_mismatch():
    """소스/타깃의 바인드 포즈에서 손 높이가 다르면(A 포즈 ↔ T 포즈) 손 IK 가 손을 공중으로 끌어올리던 버그.
    손이 바닥에 닿을 때 타깃 손도 '바닥 + 소스 손 높이 x 스케일' 에 있어야 하고, IK 를 끈 결과와 크게 다르지 않아야 한다."""
    cases = [
        (dict(style="ue", frame="z_up_x", scale=100.0, pose="A", local_axes="bone_x"),
         dict(style="biped", frame="z_up_negy", scale=100.0, pose="T", local_axes="bone_x", height=1.0)),     # 소스 손이 낮은 포즈 -> 타깃 손이 높은 포즈
        (COMBOS[0][0], IK_TGT),
        (COMBOS[0][0], dict(style="biped", frame="z_up_negy", scale=100.0, pose="T", local_axes="bone_x", height=1.2)),
    ]
    for ci, (sc, tc) in enumerate(cases):
        ssk, tsk, on, off, gs, gt, ps = _hand_case(sc, tc)
        pc_on, _ = canon(tsk, on.anim, on.frame_tgt[0])
        pc_off, _ = canon(tsk, off.anim, off.frame_tgt[0])
        hands = [(c, pl) for c, pl in zip(on.chains, on.plans) if c.kind == "hand"]
        assert hands
        for ch, pl in hands:
            assert pl.contact[25:35].all(), (ci, "접촉이어야 함")
            f = 30
            arm = float(pl.leg_length)
            h_on = pc_on[f, ch.foot, 1] - gt
            h_off = pc_off[f, ch.foot, 1] - gt
            h_exp = on.k * max(ps[f, ch.src_foot, 1] - gs, 0.0)
            assert abs(h_on - h_exp) < 0.02 * arm + 1e-6, (ci, ch.label, "손이 바닥 대비 높이가 맞지 않음", h_on, h_exp)
            assert abs(h_on - h_off) < 0.10 * arm, (ci, ch.label, "IK 가 손을 엉뚱한 높이로 끌고 감", h_on, h_off)
            assert h_on >= -1e-6, (ci, "바닥을 뚫음")
    print("hand IK bind-pose mismatch OK")


def test_hand_ik_plant():
    """손이 수평으로 거의 안 움직이는 접촉 구간은 한 점에 고정되고, 움직이는 구간은 고정하지 않는다."""
    ssk, tsk, on, off, gs, gt, ps = _hand_case(COMBOS[0][0], IK_TGT, still_arms=True)
    pc_on, _ = canon(tsk, on.anim, on.frame_tgt[0])
    for ch, pl in [(c, p) for c, p in zip(on.chains, on.plans) if c.kind == "hand"]:
        assert pl.contact[26:34].all()
        mid = pc_on[26:34, ch.foot]
        assert np.ptp(mid[:, [0, 2]], axis=0).max() < 1e-3 * max(on.k, 1.0), "접촉 중 손이 미끄러짐"
    print("hand IK plant OK")


def test_spine_blend():
    """소스/타깃의 척추 본 개수가 다르면 척추 움직임을 자연스러운 중간값으로 섞는다."""
    from motionbridge.core.mapping import spine_blend
    full = {"Spine": 1, "Spine1": 2, "Spine2": 3}
    two = {"Spine": 1, "Spine2": 3}
    assert spine_blend(full, full) == {} and spine_blend(two, two) == {}
    b = spine_blend(full, two)       # 3 -> 2
    assert [x for x, _w in b["Spine"]] == ["Spine", "Spine1"] and abs(b["Spine"][0][1] - 0.75) < 1e-9
    assert [x for x, _w in b["Spine2"]] == ["Spine1", "Spine2"] and abs(b["Spine2"][1][1] - 0.75) < 1e-9
    b = spine_blend(two, full)       # 2 -> 3
    assert b["Spine"] == [("Spine", 1.0)] and b["Spine2"] == [("Spine2", 1.0)]
    assert [x for x, _w in b["Spine1"]] == ["Spine", "Spine2"] and abs(b["Spine1"][1][1] - 0.5) < 1e-9
    assert spine_blend({"Spine": 1}, full)["Spine1"] == [("Spine", 1.0)]
    print("spine blend slots OK")


def _drop_joint(sk, j):
    """관절 j 를 없애고 자식을 부모에 직접 붙인다 (월드 바인드 위치 유지, 회전은 항등 가정)."""
    from motionbridge.core.model import Skeleton
    keep = [i for i in range(sk.n) if i != j]
    idx = {o: n for n, o in enumerate(keep)}
    par, off = [], []
    for o in keep:
        p, of = int(sk.parents[o]), sk.offsets[o].copy()
        if p == j:
            of, p = of + sk.offsets[j], int(sk.parents[j])
        par.append(idx[p] if p >= 0 else -1)
        off.append(of)
    return Skeleton([sk.names[o] for o in keep], par, off, sk.rest_rot[keep], name=sk.name)


def _split_joint(sk, j, name):
    """관절 j 와 그 자식 사이에 새 관절을 하나 끼운다 (j 의 첫 자식 구간을 반으로 나눔)."""
    from motionbridge.core.model import Skeleton
    c = [i for i in range(sk.n) if sk.parents[i] == j][0]
    names, par, off, rr = list(sk.names), list(sk.parents), [o.copy() for o in sk.offsets], list(sk.rest_rot)
    # 새 관절은 맨 뒤에 붙이고(부모는 j), c 의 부모를 새 관절로 바꾼다 -> 위상 정렬은 Skeleton 이 요구하므로 다시 정렬
    names.append(name)
    par.append(j)
    off.append(sk.offsets[c] * 0.5)
    rr.append(np.array([1.0, 0, 0, 0]))
    par[c] = len(names) - 1
    off[c] = sk.offsets[c] * 0.5
    from motionbridge.core.model import topo_order
    order = topo_order(par)
    inv = {o: n for n, o in enumerate(order)}
    return Skeleton([names[o] for o in order], [(inv[par[o]] if par[o] >= 0 else -1) for o in order],
                    [off[o] for o in order], np.array([rr[o] for o in order]), name=sk.name)


def test_spine_arc():
    """척추 본 개수가 소스와 달라도(3->2, 3->4, 3->5, 2->3) 목 밑 위치가 소스와 거의 같고, 매핑에서 빠진 척추 본도 같이 움직인다."""
    cfg = dict(style="mixamo", frame="y_up_z", scale=1.0, pose="T", local_axes="aligned")
    base, smeta = synth.build_skeleton(**cfg)
    an, _ = synth.walk_animation(base, smeta, n_frames=30)
    rot0 = np.array(an.rot, dtype=float)
    spine = [base.index("mixamorig:" + n) for n in ("Spine", "Spine1", "Spine2")]
    # 타깃 변형: 개수가 줄거나 늘어난 척추
    variants = {
        "3->2": _drop_joint(base, base.index("mixamorig:Spine1")),
        "3->4": _split_joint(base, base.index("mixamorig:Spine1"), "mixamorig:SpineX"),
        "3->5": _split_joint(_split_joint(base, base.index("mixamorig:Spine1"), "mixamorig:SpineX"), base.index("mixamorig:Spine2"), "mixamorig:SpineY"),
    }
    sm = auto_map(base)
    spine_len = 0.1 + 0.15 + 0.15 + 0.15
    for label, tsk in variants.items():
        tm = auto_map(tsk)
        for angs in ((10, 20, 30), (30, 30, 30), (0, 0, 60), (50, 40, 10), (40, -20, 60)):
            rot = rot0.copy()
            for j, a in zip(spine, angs):
                rot[:, j] = Q.mul(rot[:, j], Q.from_axis_angle(np.array([1.0, 0, 0]), np.radians(a)))
            an.rot = rot
            res = retarget(base, an, tsk, pairs_from_slots(sm, tm), RetargetSettings(foot_ik=False, hand_ik=False), [], sm, tm)
            assert any("척추" in w for w in res.warnings), label
            ws, wps = fk(base, an.rot, an.pos)
            wt, wpt = fk(tsk, res.anim.rot, res.anim.pos)
            ps, pt = Q.rotate(res.frame_src[0], wps), Q.rotate(res.frame_tgt[0], wpt)
            hs, ht = base.index("mixamorig:Hips"), tsk.index("mixamorig:Hips")
            for nm in ("Neck", "Head", "LeftShoulder"):
                ds = ps[:, base.index("mixamorig:" + nm)] - ps[:, hs]
                dt = pt[:, tsk.index("mixamorig:" + nm)] - pt[:, ht]
                err = np.linalg.norm(ds - dt, axis=1).max()
                assert err < 0.05 * spine_len, (label, angs, nm, "목 밑 위치가 소스와 다름", err / spine_len)
        # 매핑에서 빠진 척추 본도 움직인다 (3->4, 3->5 의 끼워 넣은 본): 부모 대비 로컬 회전이 항등이 아니다
        for nm in ("mixamorig:SpineX", "mixamorig:SpineY"):
            j = tsk.find(nm)
            if j >= 0 and j not in tm.values():
                bwt, _ = tsk.bind_world()
                moved = Q.angle_between(Q.mul(res.frame_tgt[0], wt[:, j]), Q.mul(res.frame_tgt[0], np.broadcast_to(bwt[j], wt[:, j].shape))).max()
                assert moved > 1e-2, (label, nm, "빠진 척추 본이 안 움직임", moved)   # 월드 방향이 소스 척추를 따라 움직임
    # 체크박스를 끄면 예전처럼 이름이 맞는 본만 1:1 (빠진 본은 안 움직임, 경고 없음)
    tsk = variants["3->2"]
    tm = auto_map(tsk)
    res = retarget(base, an, tsk, pairs_from_slots(sm, tm), RetargetSettings(foot_ik=False, hand_ik=False, spine_mix=False), [], sm, tm)
    assert not any("척추" in w for w in res.warnings), "끄면 섞지 않아야 함"
    print("spine arc OK")


def test_ik():
    """허벅지/종아리 비율이 다른 대상: FK 만으로는 접지 중 발이 미끄러지고, IK 는 정확히 고정한다."""
    sc = COMBOS[0][0]
    base = setup(sc, IK_TGT, st=RetargetSettings(foot_ik=False))
    ik = setup(sc, IK_TGT, st=RetargetSettings(foot_ik=True))
    ssk, an, tsk, res_fk = base[0], base[1], base[2], base[3]
    res_ik = ik[3]
    assert not res_fk.ik_applied and res_ik.ik_applied and len([c for c in res_ik.chains if c.kind == 'foot']) == 2
    for i in range(2):
        m_fk, L, n_fk, all_fk = slide_metric(tsk, res_fk, i)
        m_ik, L2, n_ik, all_ik = slide_metric(tsk, res_ik, i)
        assert n_ik >= 10, "접지 구간이 너무 짧음 %d" % n_ik
        print("  체인 %d: 접지쌍 %d, 접지 중 미끄러짐 FK %.3f%% -> IK %.4f%% | 전체 프레임 최대 이동 FK %.2f%% IK %.2f%% (다리 길이 대비)" % (i, n_ik, 100 * m_fk / L, 100 * m_ik / L2, 100 * all_fk / L, 100 * all_ik / L2))
        assert m_fk > 0.01 * L, "테스트 조건이 약함: FK 도 거의 안 미끄러짐"
        assert m_ik < 0.0005 * L2, "IK 후에도 미끄러짐 %.5f" % (m_ik / L2)
        assert all_ik <= all_fk * 1.05, "IK 가 전환 구간에 점프를 만듦 (%.3f vs %.3f)" % (all_ik / L2, all_fk / L)
    # 접지 위치 = 스케일된 소스 위치
    pc, _ = canon(tsk, res_ik.anim, res_ik.frame_tgt[0])
    ps = res_ik.src_canon_pos
    _, bwp_s = ssk.bind_world()
    _, bwp_t = tsk.bind_world()
    bps = Q.rotate(res_ik.frame_src[0], bwp_s)
    bpt = Q.rotate(res_ik.frame_tgt[0], bwp_t)
    for ch, pl in zip(res_ik.chains, res_ik.plans):
        if ch.kind != "foot":
            continue
        D = bpt[ch.foot] + res_ik.k * (ps[:, ch.src_foot] - bps[ch.src_foot])
        err = np.linalg.norm(pc[:, ch.foot] - D, axis=1).max()
        assert err < 0.01 * pl.leg_length, "발 위치가 스케일된 소스 위치와 다름 %.4f" % err
        assert pc[:, ch.foot, 1].min() >= bpt[ch.foot, 1] - 1e-6, "지면 관통"
        A, B, C = pc[:, ch.upper], pc[:, ch.lower], pc[:, ch.foot]
        d = unit(C - A)
        perp = (B - A) - d * np.sum((B - A) * d, axis=1, keepdims=True)
        assert perp[:, 2].min() > -1e-6, "무릎이 뒤집힘"
        assert not pl.fail.any(), "다리가 닿지 않는 프레임 %d" % pl.fail.sum()
    # 본 길이 보존, 회전 연속성
    _, wp = fk(tsk, res_ik.anim.rot, res_ik.anim.pos)
    for ch in res_ik.chains:
        l1 = np.linalg.norm(wp[:, ch.lower] - wp[:, ch.upper], axis=1)
        l2 = np.linalg.norm(wp[:, ch.foot] - wp[:, ch.lower], axis=1)
        assert np.ptp(l1) < 1e-6 and np.ptp(l2) < 1e-6, "본 길이 변함"
    jump = np.degrees(Q.angle_between(res_ik.anim.rot[1:], res_ik.anim.rot[:-1])).max()
    assert jump < 60.0, "회전 점프 %.1f" % jump
    issues = analyze(tsk, res_ik, RetargetSettings())
    bad = [i for i in issues if i.kind in ("slide", "penetration", "overreach", "pop", "nan")]
    assert not bad, [i.message for i in bad]
    iss_fk = analyze(tsk, res_fk, RetargetSettings(foot_ik=False))
    assert any(i.kind == "slide" for i in iss_fk), "FK 결과의 미끄러짐을 검출하지 못함"
    # 접지 구간에서만 IK (ik_follow=0) 모드도 동작은 해야 한다
    r0 = setup(sc, IK_TGT, st=RetargetSettings(foot_ik=True, ik_follow=0.0))[3]
    assert np.isfinite(r0.anim.rot).all()
    print("IK OK (hip drop max %.4f)" % res_ik.drop.max())


def test_overreach():
    """골반을 너무 높이면 다리가 닿지 않는다: 골반을 낮추고, 그래도 안 되면 경고한다."""
    sc = COMBOS[0][0]
    st_hi = RetargetSettings(foot_ik=True, hip_offset=(0.0, 12.0, 0.0), hip_drop=False)
    r_nodrop = setup(sc, IK_TGT, st=st_hi)
    fails_nodrop = sum(int(p.fail.sum()) for p in r_nodrop[3].plans)
    assert fails_nodrop > 0, "테스트 조건: 골반을 올리면 다리가 닿지 않아야 함"
    assert any(i.kind == "overreach" for i in analyze(r_nodrop[2], r_nodrop[3], st_hi)), "과신장 경고 없음"
    st_drop = RetargetSettings(foot_ik=True, hip_offset=(0.0, 12.0, 0.0), hip_drop=True)
    r_drop = setup(sc, IK_TGT, st=st_drop)
    fails_drop = sum(int(p.fail.sum()) for p in r_drop[3].plans)
    assert r_drop[3].drop.max() > 1.0, "골반이 낮춰지지 않음"
    assert fails_drop < fails_nodrop, "골반 낮추기가 과신장을 줄이지 못함 (%d -> %d)" % (fails_nodrop, fails_drop)
    # 닿지 않는 프레임에서도 결과가 유한하고 본 길이가 유지
    tsk = r_drop[2]
    _, wp = fk(tsk, r_drop[3].anim.rot, r_drop[3].anim.pos)
    ch = r_drop[3].chains[0]
    assert np.isfinite(wp).all()
    assert np.ptp(np.linalg.norm(wp[:, ch.lower] - wp[:, ch.upper], axis=1)) < 1e-6
    print("overreach OK (닿지 않는 프레임 %d -> %d, 골반 낮춤 최대 %.1f)" % (fails_nodrop, fails_drop, r_drop[3].drop.max()))


def test_in_place_not_locked():
    sc, tc = COMBOS[0]
    r = setup(sc, tc, anim_kw=dict(in_place=True), st=RetargetSettings(foot_ik=True))[3]
    for pl in r.plans:
        frac = pl.contact.mean()
        assert frac < 0.2, "제자리 모션인데 접지로 판정됨 %.2f" % frac
    print("in-place OK")


def test_start_at_origin():
    """원본 모션이 월드 좌표(원점에서 멀리)에서 시작해도 골반과 발이 모델 옆에 놓인다 (발 IK 가 멀리 끌려가지 않음)."""
    sc, tc = COMBOS[0]
    ssk, smeta = synth.build_skeleton(**sc)
    tsk, tmeta = synth.build_skeleton(**tc)
    an, _ = synth.walk_animation(ssk, smeta, n_frames=60)
    sm, tm = auto_map(ssk), auto_map(tsk)
    pairs, chains = pairs_from_slots(sm, tm), default_chains(sm, tm)
    hips_s = ssk.parents.tolist().index(-1) if sm.get("Hips", -1) < 0 else sm["Hips"]
    # 원본을 월드 좌표로 멀리 옮긴다 (루트 위치에 수평 오프셋 더하기)
    far = type(an)(an.name, an.fps, an.rot.copy(), an.pos.copy())
    F0 = retarget(ssk, an, tsk, pairs, RetargetSettings(foot_ik=True), chains, sm, tm).frame_src[0]
    off = Q.rotate(Q.conj(F0), np.array([500.0, 0.0, 800.0]) * smeta.get("scale", 1.0))  # 표준 프레임 수평 (X,Z) 오프셋 -> 원본 프레임
    for j in range(ssk.n):
        if ssk.parents[j] < 0:
            far.pos[:, j] += off
    base = retarget(ssk, an, tsk, pairs, RetargetSettings(foot_ik=True), chains, sm, tm)
    r_on = retarget(ssk, far, tsk, pairs, RetargetSettings(foot_ik=True), chains, sm, tm)
    r_off = retarget(ssk, far, tsk, pairs, RetargetSettings(foot_ik=True, start_at_origin=False), chains, sm, tm)
    F = base.frame_tgt[0]
    pb, _ = canon(tsk, base.anim, F)
    pon, _ = canon(tsk, r_on.anim, F)
    poff, _ = canon(tsk, r_off.anim, F)
    h = tm["Hips"]
    height = np.ptp(pb[0, :, 1])
    hb0 = pb[0, h, [0, 2]]
    # 첫 프레임 골반 수평 위치 = 원본이 원점에서 시작했을 때(첫 프레임 기준)의 위치와 같다 (오프셋과 무관)
    p_on_rel = pon[:, h, :][:, [0, 2]] - pon[0, h, [0, 2]]
    p_base_rel = pb[:, h, :][:, [0, 2]] - pb[0, h, [0, 2]]
    assert np.abs(p_on_rel - p_base_rel).max() < 0.02 * height, "이동량이 달라짐"
    bwp = tsk.bind_world()[1]
    bind_xz = Q.rotate(F, bwp)[h][[0, 2]]
    assert np.linalg.norm(pon[0, h, [0, 2]] - bind_xz) < 0.02 * height, "시작 위치가 모델 바인드 위치와 다름"
    assert np.linalg.norm(poff[0, h, [0, 2]] - bind_xz) > 3 * height, "끄면 원본 절대 위치가 그대로 옮겨져야 함(켜고 끈 차이 확인)"
    # 발이 골반에서 멀리 떨어지지 않는다 (IK 목표도 같이 옮겨졌는지)
    lf = tm["L_Foot"]
    assert np.linalg.norm(pon[:, lf] - pon[:, h], axis=1).max() < 1.3 * height, "발이 골반에서 너무 멀다 (IK 목표가 멀리 끌려감)"
    print("start at origin OK")


def test_offsets():
    sc, tc = COMBOS[0]
    base = setup(sc, tc)
    r0 = base[3]
    tsk, tm = base[2], base[5]
    pc0, wr0 = canon(tsk, r0.anim, r0.frame_tgt[0])
    com = r0.com
    # 골반 오프셋
    st = RetargetSettings(foot_ik=False, hip_offset=(0.02, 0.05, 0.10))
    r1 = setup(sc, tc, st=st)[3]
    pc1, _ = canon(tsk, r1.anim, r1.frame_tgt[0])
    assert np.allclose(pc1[:, com] - pc0[:, com], [0.02, 0.05, 0.10], atol=1e-6), "골반 오프셋"
    # 제자리
    st = RetargetSettings(foot_ik=False, transfer_root_motion=False)
    r2 = setup(sc, tc, st=st)[3]
    pc2, _ = canon(tsk, r2.anim, r2.frame_tgt[0])
    assert np.ptp(pc2[:, com, 2]) < 1e-6 and np.ptp(pc2[:, com, 0]) < 1e-6, "제자리 모드에서 수평 이동 남음"
    # 루트 yaw 90도: 진행 방향이 +Z 에서 +X 쪽으로 (Y 축 +90도: z -> x)
    st = RetargetSettings(foot_ik=False, root_yaw_deg=90.0)
    r3 = setup(sc, tc, st=st)[3]
    pc3, _ = canon(tsk, r3.anim, r3.frame_tgt[0])
    tr = pc3[-1, com] - pc3[0, com]
    assert tr[0] > 0.3 * r3.k and abs(tr[2]) < 1e-6, ("루트 yaw", tr)
    # 서브트리 회전 보정: 척추를 X 축으로 10도 숙이면 척추~머리 모두 같은 각도 변함
    sp = tm["Spine1"]
    st = RetargetSettings(foot_ik=False, rot_offsets={sp: (10.0, 0.0, 0.0)})
    r4 = setup(sc, tc, st=st)[3]
    _, wr4 = canon(tsk, r4.anim, r4.frame_tgt[0])
    for slot in ("Spine1", "Spine2", "Neck", "Head"):
        j = tm[slot]
        ang = np.degrees(Q.angle_between(wr4[:, j], wr0[:, j]))
        assert np.allclose(ang, 10.0, atol=1e-6), (slot, ang[:3])
    j = tm["Hips"]
    assert np.degrees(Q.angle_between(wr4[:, j], wr0[:, j])).max() < 1e-6, "보정이 상위 본으로 번짐"
    # 수동 스케일
    st = RetargetSettings(foot_ik=False, auto_scale=False, manual_scale=2.0)
    r5 = setup(sc, tc, st=st)[3]
    assert abs(r5.k - 2.0) < 1e-12
    print("offsets OK")


def test_partial_mapping():
    """일부 본만 매핑돼도(발가락/손가락 없음) 동작하고, 매핑 안 된 본은 부모를 따라간다."""
    sc, tc = COMBOS[0]
    ssk, smeta = synth.build_skeleton(**sc)
    tsk, tmeta = synth.build_skeleton(**tc)
    an, _ = synth.walk_animation(ssk, smeta, n_frames=30)
    sm, tm = auto_map(ssk), auto_map(tsk)
    for k in [k for k in list(sm) if "Toe" in k or "Middle" in k]:
        sm.pop(k)
        tm.pop(k, None)
    for k in [k for k in list(tm) if "Toe" in k or "Middle" in k]:
        tm.pop(k)
    pairs = pairs_from_slots(sm, tm)
    res = retarget(ssk, an, tsk, pairs, RetargetSettings(foot_ik=True), default_chains(sm, tm), sm, tm)
    assert np.isfinite(res.anim.rot).all() and np.isfinite(res.anim.pos).all()
    # 매핑 안 된 발가락은 발(부모)의 월드 회전을 그대로 따라간다
    toe = tsk.index("ball_l")
    foot = tsk.index("foot_l")
    wr, _ = fk(tsk, res.anim.rot, res.anim.pos)
    rel = Q.mul(Q.conj(wr[:, foot]), wr[:, toe])
    assert np.allclose(np.abs(np.sum(rel * tsk.rest_rot[toe][None], -1)), 1.0, atol=1e-9)
    print("partial mapping OK")


def test_bvh_source_pipeline():
    """BVH(루트 OFFSET=0, 절대 위치 채널)로 저장했다가 읽은 소스로 리타깃해도 원본 소스와 거의 같은 결과."""
    import tempfile
    from motionbridge.core.io_bvh import read_bvh, write_bvh

    sc = dict(style="mixamo", frame="y_up_z", scale=1.0, pose="T", local_axes="aligned")
    ssk, smeta = synth.build_skeleton(**sc)
    an, _ = synth.walk_animation(ssk, smeta, n_frames=60)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "s.bvh")
        write_bvh(p, ssk, an, order="ZXY")
        m = read_bvh(p)
    ssk2, an2 = m.skeleton, m.animations[0]
    tsk, tmeta = synth.build_skeleton(**IK_TGT)
    sm1, sm2, tm = auto_map(ssk), auto_map(ssk2), auto_map(tsk)
    st = RetargetSettings(foot_ik=True)
    r1 = retarget(ssk, an, tsk, pairs_from_slots(sm1, tm), st, default_chains(sm1, tm), sm1, tm)
    r2 = retarget(ssk2, an2, tsk, pairs_from_slots(sm2, tm), st, default_chains(sm2, tm), sm2, tm)
    assert abs(r2.k - r1.k) / r1.k < 0.05, ("스케일 차이", r1.k, r2.k)
    F = r1.frame_tgt[0]
    p1, _ = canon(tsk, r1.anim, F)
    p2, _ = canon(tsk, r2.anim, F)
    height = np.ptp(p1[0, :, 1])
    err = np.linalg.norm(p1 - p2, axis=-1).max()
    assert err < 0.06 * height, "BVH 경유 결과가 원본과 다름 %.3f (키 %.1f)" % (err, height)
    # 바인드 포즈가 바닥에 서 있어야 한다 (가장 낮은 관절 ~ 0)
    _, bwp = ssk2.bind_world()
    pc = Q.rotate(r2.frame_src[0], bwp)
    assert abs(pc[:, 1].min()) < 0.03 * np.ptp(pc[:, 1]), "BVH 바인드가 바닥에 서 있지 않음"
    print("bvh source pipeline OK (최대 오차 %.2f%% of height)" % (100 * err / height))


def test_placement_and_ghost():
    """배치 이동·회전은 몸 전체(골반+발)를 함께 옮기고, 고스트는 타깃 골반 위에 겹친다."""
    sc, tc = COMBOS[0]
    base = setup(sc, tc, st=RetargetSettings(foot_ik=True))
    ssk, an, tsk, res0 = base[0], base[1], base[2], base[3]
    sm, tm = base[4], base[5]
    com = res0.com
    pair_of = {t: s for s, t in res0.pairs}
    pc0, _ = canon(tsk, res0.anim, res0.frame_tgt[0])
    # 고스트: 소스 COM 의 수평 위치가 타깃 COM 과 모든 프레임에서 같다 (루트 모션까지 포함)
    gh = res0.ghost_pos
    assert gh.shape == res0.src_canon_pos.shape
    assert np.allclose(gh[:, pair_of[com]][:, [0, 2]], pc0[:, com][:, [0, 2]], atol=1e-4), "고스트가 타깃 골반에 안 겹침"
    # 이동: 모든 관절이 같은 벡터만큼 (IK 켠 상태에서도 발까지)
    st = RetargetSettings(foot_ik=True, place_offset=(0.3, -0.2))
    res1 = setup(sc, tc, st=st)[3]
    pc1, _ = canon(tsk, res1.anim, res1.frame_tgt[0])
    mapped = sorted(pair_of)  # 매핑 안 된 최상위 본(루트 등)은 원래 안 움직인다
    d = (pc1 - pc0)[:, mapped]
    assert np.allclose(d[..., 0], 0.3, atol=2e-2) and np.allclose(d[..., 2], -0.2, atol=2e-2), (d[..., 0].min(), d[..., 0].max())
    assert np.allclose(res1.ghost_pos[..., [0, 2]] - gh[..., [0, 2]], [0.3, -0.2], atol=1e-6)
    # 회전: 시작 위치를 축으로 돌고, 고스트도 같이 돈다
    st = RetargetSettings(foot_ik=False, root_yaw_deg=90.0)
    res2 = setup(sc, tc, st=st)[3]
    pc2, _ = canon(tsk, res2.anim, res2.frame_tgt[0])
    p0 = pc0[0, com]
    a0, a2 = pc0[:, com] - p0, pc2[:, com] - p0
    assert np.allclose(np.linalg.norm(a0[:, [0, 2]], axis=1), np.linalg.norm(a2[:, [0, 2]], axis=1), atol=1e-4)
    assert np.allclose(res2.ghost_pos[:, pair_of[com]][:, [0, 2]], pc2[:, com][:, [0, 2]], atol=1e-4)
    print("placement OK")


if __name__ == "__main__":
    test_placement_and_ghost()
    test_fk_invariants()
    test_ik()
    test_hand_ik()
    test_ik_off_shows_nothing()
    test_scale_independent_of_frame()
    test_hand_ik_bind_pose_mismatch()
    test_hand_ik_plant()
    test_spine_blend()
    test_spine_arc()
    test_overreach()
    test_in_place_not_locked()
    test_start_at_origin()
    test_offsets()
    test_partial_mapping()
    test_bvh_source_pipeline()
    print("test_retarget ALL OK")
