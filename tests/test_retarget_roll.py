"""리타겟 롤 보정 시험: 바인드 척추가 '앞+옆 대각선'으로 굽은 소스(검은 신화 오공의 굽은 노승 등)에서
방향만 최소 회전(between)으로 맞추면 본 축을 도는 롤이 생겨 어깨·다리·발이 통째로 비틀린다.
(척추 선 자체는 방향 복사로 맞지만, 주 자식이 아닌 가지 — 어깨선·골반의 다리·발바닥 — 가 옆으로 돌아간다)"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge.core import quat as Q  # noqa: E402
from motionbridge.core.mapping import auto_map, pairs_from_slots  # noqa: E402
from motionbridge.core.model import Animation, Skeleton, fk  # noqa: E402
from motionbridge.core.retarget import RetargetSettings, default_chains, retarget  # noqa: E402
import synth  # noqa: E402

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("FAIL", msg)


def hunched_source(side_deg=15.0, fwd_deg=25.0):
    """ue 스켈레톤의 바인드를 '척추부터 위 전부 옆(Z축)+앞(X축)으로 굽힌' 모습으로 다시 만든다."""
    skel0, meta = synth.build_skeleton(style="ue", frame="y_up_z", scale=1.0, pose="T", local_axes="aligned")
    keys, idx, pos = meta["keys"], meta["idx"], meta["pos_std"].copy()
    parents = list(skel0.parents)
    spine = idx["spine"]

    def under_spine(j):
        while j >= 0:
            if j == spine:
                return True
            j = parents[j]
        return False

    piv = pos[spine].copy()
    rz = Q.from_axis_angle(np.array([0.0, 0.0, 1.0]), np.radians(-side_deg))   # 옆으로 기움
    rx = Q.from_axis_angle(np.array([1.0, 0.0, 0.0]), np.radians(fwd_deg))    # 앞으로 굽음
    q = Q.mul(rx, rz)
    for j in range(len(keys)):
        if under_spine(j):
            pos[j] = piv + Q.rotate(q, pos[j] - piv)
    J = len(keys)
    offsets = np.empty((J, 3))
    for j in range(J):
        offsets[j] = pos[j] - (pos[parents[j]] if parents[j] >= 0 else 0.0)
    return Skeleton(list(skel0.names), parents, offsets, Q.identity((J,)), name="hunched"), idx


def bend_anim(skel, idx, n=30, deg=35.0):
    """척추 로컬에 앞으로 숙이기(+X축 회전)만 거는 애니메이션 (로컬축 정렬 스켈레톤)."""
    J, T = skel.n, n
    rot = np.tile(Q.identity(()), (T, J, 1))
    pos = np.tile(skel.offsets[None], (T, 1, 1))
    rot[:, idx["spine"]] = Q.from_axis_angle(np.array([1.0, 0.0, 0.0]), np.radians(np.linspace(0.0, deg, T)))
    return Animation("bend", 30.0, rot, pos)


def yaw_of(v):
    """수평면에서 어깨선(왼-오른) 방향 각도 (도). 비틀림 지표."""
    return np.degrees(np.arctan2(v[..., 2], v[..., 0]))


src, sidx = hunched_source()
san = bend_anim(src, sidx)
tgt, _tm = synth.build_skeleton(style="mixamo", frame="y_up_z", scale=1.0, pose="T", local_axes="aligned")
sm, tm = auto_map(src), auto_map(tgt)
check("L_UpperArm" in sm and "L_UpperArm" in tm and "Hips" in tm, "자동 매핑")
pairs = pairs_from_slots(sm, tm)


def run(corr_roll):
    st = RetargetSettings(foot_ik=False, hand_ik=False, corr_roll=corr_roll)
    res = retarget(src, san, tgt, pairs, st, default_chains(sm, tm), sm, tm)
    _, wp = fk(tgt, res.anim.rot, res.anim.pos)
    p = Q.rotate(res.frame_tgt[0], wp)
    ps = res.src_canon_pos
    # 어깨선(왼팔-오른팔)의 수평 방향: 소스와 같아야 한다 (롤이 끼면 어긋난다)
    vt = p[:, tm["L_UpperArm"]] - p[:, tm["R_UpperArm"]]
    vs = ps[:, sm["L_UpperArm"]] - ps[:, sm["R_UpperArm"]]
    twist = float(np.abs(((yaw_of(vt) - yaw_of(vs)) + 180.0) % 360.0 - 180.0).max())
    # 다리(골반의 비주류 자식)도 같은 지표
    vt2 = p[:, tm["L_UpLeg"]] - p[:, tm["R_UpLeg"]]
    vs2 = ps[:, sm["L_UpLeg"]] - ps[:, sm["R_UpLeg"]]
    twist_leg = float(np.abs(((yaw_of(vt2) - yaw_of(vs2)) + 180.0) % 360.0 - 180.0).max())
    return twist, twist_leg, res


tw_on, twleg_on, _ = run(True)
tw_off, twleg_off, _ = run(False)
print("  어깨선 비틀림: corr_roll 켬 %.2f° / 끔(기본) %.2f° · 골반-다리: %.2f° / %.2f°" % (tw_on, tw_off, twleg_on, twleg_off))
check(tw_off < 4.0, "굽은 바인드라도 어깨선이 소스와 같은 방향(기본 설정): %.2f°" % tw_off)
check(tw_on < 4.0, "corr_roll 을 켜도 어깨선이 틀어지지 않는다: %.2f°" % tw_on)
check(max(twleg_on, twleg_off) < 4.0, "골반-다리 방향도 소스와 같다 (%.2f° / %.2f°)" % (twleg_on, twleg_off))

# ---- 바인드가 똑바른 조합(기존 게임)은 결과가 사실상 그대로 (회귀 없음)
ssk2, smeta2 = synth.build_skeleton(style="ue", frame="z_up_x", scale=100.0, pose="A", local_axes="bone_x")
an2, _ = synth.walk_animation(ssk2, smeta2, n_frames=30)
sm2, tm2 = auto_map(ssk2), auto_map(tgt)
pairs2 = pairs_from_slots(sm2, tm2)
r_on = retarget(ssk2, an2, tgt, pairs2, RetargetSettings(foot_ik=False, hand_ik=False, corr_roll=True), default_chains(sm2, tm2), sm2, tm2)
r_off = retarget(ssk2, an2, tgt, pairs2, RetargetSettings(foot_ik=False, hand_ik=False, corr_roll=False), default_chains(sm2, tm2), sm2, tm2)
_, wp_on = fk(tgt, r_on.anim.rot, r_on.anim.pos)
_, wp_off = fk(tgt, r_off.anim.rot, r_off.anim.pos)
dmax = float(np.abs(wp_on - wp_off).max())
check(dmax < 0.06, "똑바른 바인드(T/A 포즈)에서는 예전 결과와 거의 같다: 최대 차이 %.4f" % dmax)

if fails:
    print("\n실패 %d개" % len(fails))
    sys.exit(1)
print("test_retarget_roll ALL OK")
