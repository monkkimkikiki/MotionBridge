"""합성 인간형 스켈레톤 + 걷기 모션 생성기 (테스트 전용).

표준 프레임: Y-up, 캐릭터는 +Z 를 바라보며 왼쪽이 +X.
리타깃 모듈과 독립적인 수식(평면 2본 IK 해석해)으로 발이 땅에 고정되는 걷기를 만든다.
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import quat as Q  # noqa: E402
from motionbridge.core.model import Animation, Skeleton, fk, world_to_local  # noqa: E402

# (이름키, 부모키, 표준프레임 월드 위치(T 포즈, 미터))
_BASE = [
    ("root", None, (0, 0.0, 0)),
    ("hips", "root", (0, 1.0, 0)),
    ("spine", "hips", (0, 1.1, 0)),
    ("spine1", "spine", (0, 1.25, 0)),
    ("spine2", "spine1", (0, 1.4, 0)),
    ("neck", "spine2", (0, 1.55, 0)),
    ("head", "neck", (0, 1.65, 0)),
    ("head_end", "head", (0, 1.85, 0)),
]
_ARM = [  # 왼쪽(+X) 기준, 오른쪽은 x 반전
    ("shoulder", "spine2", (0.05, 1.5, 0)),
    ("upperarm", "shoulder", (0.2, 1.5, 0)),
    ("lowerarm", "upperarm", (0.48, 1.5, 0)),
    ("hand", "lowerarm", (0.74, 1.5, 0)),
    ("middle1", "hand", (0.82, 1.5, 0)),
    ("middle2", "middle1", (0.86, 1.5, 0)),
    ("middle3", "middle2", (0.89, 1.5, 0)),
    ("middle_end", "middle3", (0.92, 1.5, 0)),
]
_LEG = [
    ("upleg", "hips", (0.09, 0.95, 0)),
    ("lowleg", "upleg", (0.09, 0.52, 0)),
    ("foot", "lowleg", (0.09, 0.08, 0)),
    ("toe", "foot", (0.09, 0.04, 0.12)),
    ("toe_end", "toe", (0.09, 0.04, 0.20)),
]

NAMES = {
    "mixamo": {
        "root": None, "hips": "mixamorig:Hips", "spine": "mixamorig:Spine", "spine1": "mixamorig:Spine1",
        "spine2": "mixamorig:Spine2", "neck": "mixamorig:Neck", "head": "mixamorig:Head", "head_end": "mixamorig:HeadTop_End",
        "side": {"L": "Left", "R": "Right"},
        "shoulder": "mixamorig:{S}Shoulder", "upperarm": "mixamorig:{S}Arm", "lowerarm": "mixamorig:{S}ForeArm",
        "hand": "mixamorig:{S}Hand", "middle1": "mixamorig:{S}HandMiddle1", "middle2": "mixamorig:{S}HandMiddle2",
        "middle3": "mixamorig:{S}HandMiddle3", "middle_end": "mixamorig:{S}HandMiddle4",
        "upleg": "mixamorig:{S}UpLeg", "lowleg": "mixamorig:{S}Leg", "foot": "mixamorig:{S}Foot",
        "toe": "mixamorig:{S}ToeBase", "toe_end": "mixamorig:{S}Toe_End",
    },
    "ue": {
        "root": "root", "hips": "pelvis", "spine": "spine_01", "spine1": "spine_02", "spine2": "spine_03",
        "neck": "neck_01", "head": "head", "head_end": "head_end",
        "side": {"L": "l", "R": "r"},
        "shoulder": "clavicle_{S}", "upperarm": "upperarm_{S}", "lowerarm": "lowerarm_{S}", "hand": "hand_{S}",
        "middle1": "middle_01_{S}", "middle2": "middle_02_{S}", "middle3": "middle_03_{S}", "middle_end": "middle_end_{S}",
        "upleg": "thigh_{S}", "lowleg": "calf_{S}", "foot": "foot_{S}", "toe": "ball_{S}", "toe_end": "ball_end_{S}",
    },
    "biped": {
        "root": None, "hips": "Bip001 Pelvis", "spine": "Bip001 Spine", "spine1": "Bip001 Spine1",
        "spine2": "Bip001 Spine2", "neck": "Bip001 Neck", "head": "Bip001 Head", "head_end": "Bip001 HeadNub",
        "side": {"L": "L", "R": "R"},
        "shoulder": "Bip001 {S} Clavicle", "upperarm": "Bip001 {S} UpperArm", "lowerarm": "Bip001 {S} Forearm",
        "hand": "Bip001 {S} Hand", "middle1": "Bip001 {S} Finger1", "middle2": "Bip001 {S} Finger11",
        "middle3": "Bip001 {S} Finger12", "middle_end": "Bip001 {S} Finger1Nub",
        "upleg": "Bip001 {S} Thigh", "lowleg": "Bip001 {S} Calf", "foot": "Bip001 {S} Foot",
        "toe": "Bip001 {S} Toe0", "toe_end": "Bip001 {S} Toe0Nub",
    },
}

FRAMES = {
    "y_up_z": np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1]], float),  # 표준
    "z_up_x": np.array([[0, 0, 1], [1, 0, 0], [0, 1, 0]], float),  # Z-up, +X 를 바라봄 (언리얼 계열)
    "z_up_negy": np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float),  # Z-up, -Y 를 바라봄 (맥스 계열)
}


def _rx(a):
    return Q.from_axis_angle([1, 0, 0], a)


def _ry(a):
    return Q.from_axis_angle([0, 1, 0], a)


def _rz(a):
    return Q.from_axis_angle([0, 0, 1], a)


def build_skeleton(style="mixamo", frame="y_up_z", scale=1.0, pose="T", local_axes="aligned", height=1.0, with_root=None, leg_scale=1.0, knee_frac=None):
    """(skeleton, key_to_index, world_bind_rot_std, world_bind_pos_std) 를 만든다. *_std 는 표준 프레임/미터."""
    nm = NAMES[style]
    if with_root is None:
        with_root = nm["root"] is not None
    items = []
    for k, p, pos in _BASE:
        items.append((k, p, np.array(pos, float), None))
    for side, sx in (("L", 1.0), ("R", -1.0)):
        for k, p, pos in _ARM + _LEG:
            pp = np.array(pos, float)
            pp[0] *= sx
            parent = (p + "@" + side) if p not in ("spine2", "hips") else p
            items.append((k + "@" + side, parent, pp, side))
    if not with_root:
        items = [it for it in items if it[0] != "root"]
        items = [(k, (None if p == "root" else p), pos, s) for k, p, pos, s in items]
    # A 포즈: 팔을 어깨 기준으로 아래로 40도 회전
    if pose.upper() == "A":
        new = []
        pivot = {}
        for k, p, pos, s in items:
            if s and k.split("@")[0] in ("upperarm", "lowerarm", "hand", "middle1", "middle2", "middle3", "middle_end"):
                piv = np.array([0.2 * (1 if s == "L" else -1), 1.5, 0.0])
                ang = np.radians(-40.0) if s == "L" else np.radians(40.0)
                v = pos - piv
                c, sn = np.cos(ang), np.sin(ang)
                pos = piv + np.array([v[0] * c - v[1] * sn, v[0] * sn + v[1] * c, v[2]])
            new.append((k, p, pos, s))
        items = new
    keys = [it[0] for it in items]
    idx = {k: i for i, k in enumerate(keys)}
    parents = [(-1 if it[1] is None else idx[it[1]]) for it in items]
    pos_std = np.array([it[2] for it in items]) * height
    if knee_frac is not None:  # 무릎 위치(허벅지/종아리 길이비) 변경
        for side in ("L", "R"):
            iu, il, ifo = keys.index("upleg@" + side), keys.index("lowleg@" + side), keys.index("foot@" + side)
            pos_std[il, 1] = pos_std[ifo, 1] + knee_frac * (pos_std[iu, 1] - pos_std[ifo, 1])
    if leg_scale != 1.0:  # 다리만 늘이거나 줄이고 상체를 그만큼 올리거나 내린다
        dshift = 0.95 * (leg_scale - 1.0)
        for i, k in enumerate(keys):
            base = k.split("@")[0]
            if base in ("upleg", "lowleg", "foot", "toe", "toe_end"):
                pos_std[i, 1] *= leg_scale
            elif base != "root":
                pos_std[i, 1] += dshift

    names = []
    for k, p, pos, s in items:
        base = k.split("@")[0]
        pattern = nm[base]
        if pattern is None:
            raise ValueError("root 없음")
        if "{S}" in pattern:
            pattern = pattern.replace("{S}", nm["side"][s])
        names.append(pattern)

    J = len(keys)
    # 월드 바인드 회전(표준 프레임)
    wr = Q.identity((J,))
    if local_axes == "bone_x":
        for j in range(J):
            kids = [c for c in range(J) if parents[c] == j]
            if kids:
                d = pos_std[kids[0]] - pos_std[j]
                if np.linalg.norm(d) > 1e-9:
                    wr[j] = Q.between(np.array([1.0, 0.0, 0.0]), d)
            else:
                wr[j] = wr[parents[j]] if parents[j] >= 0 else wr[j]
    # 프레임/스케일 적용
    G = FRAMES[frame]
    qG = Q.from_matrix(G)
    pos_file = (pos_std @ G.T) * scale
    wr_file = Q.mul(qG, wr)
    # 로컬로 변환
    rest_rot = np.empty((J, 4))
    offsets = np.empty((J, 3))
    for j in range(J):
        p = parents[j]
        if p < 0:
            rest_rot[j] = wr_file[j]
            offsets[j] = pos_file[j]
        else:
            rest_rot[j] = Q.mul(Q.conj(wr_file[p]), wr_file[j])
            offsets[j] = Q.rotate(Q.conj(wr_file[p]), pos_file[j] - pos_file[p])
    skel = Skeleton(names, parents, offsets, rest_rot, name=style)
    meta = {"keys": keys, "idx": idx, "G": G, "qG": qG, "scale": scale, "wr_std": wr, "pos_std": pos_std}
    return skel, meta


def walk_animation(skel, meta, n_frames=60, fps=30.0, speed=0.9, cycle=1.0, stance=0.6, hip_drop=0.08, name="walk", in_place=False):
    """발이 고정되는 걷기. 반환: Animation(파일 프레임) 과 정답 정보(발 월드 위치 등)."""
    keys, idx, G, qG, scale = meta["keys"], meta["idx"], meta["G"], meta["qG"], meta["scale"]
    pos_std, wr_std = meta["pos_std"], meta["wr_std"]
    J = len(keys)
    T = n_frames
    t = np.arange(T) / fps
    stride = speed * stance * cycle
    l1 = np.linalg.norm(pos_std[idx["lowleg@L"]] - pos_std[idx["upleg@L"]])
    l2 = np.linalg.norm(pos_std[idx["foot@L"]] - pos_std[idx["lowleg@L"]])

    hips_bind = pos_std[idx["hips"]]
    hip_y = hips_bind[1] - hip_drop + 0.012 * np.cos(2 * np.pi * 2 * t / cycle)
    hip_z = np.zeros(T) if in_place else speed * t
    hips_pos = np.stack([np.zeros(T), hip_y, hip_z], axis=1)

    D = np.repeat(Q.identity((J,))[None], T, axis=0)  # 월드 델타
    absolute = np.zeros(J, bool)
    ankle_world = {}
    for side, phase0 in (("L", 0.0), ("R", 0.5)):
        up, lo, ft = idx["upleg@" + side], idx["lowleg@" + side], idx["foot@" + side]
        hipj_off = pos_std[up] - hips_bind  # 힙 루트 기준 UpLeg 오프셋(수평 포함)
        s_arr, ank = [], []
        for i in range(T):
            ph = ((t[i] / cycle) + phase0) % 1.0
            if ph < stance:
                u = ph / stance
                zrel = stride / 2 - stride * u
                lift = 0.0
            else:
                u = (ph - stance) / (1 - stance)
                e = 0.5 - 0.5 * np.cos(np.pi * u)
                zrel = -stride / 2 + stride * e
                lift = 0.08 * np.sin(np.pi * u)
            if in_place:
                zrel = zrel - 0.0
            ay = pos_std[ft][1] + lift
            az = hips_pos[i, 2] + zrel
            if in_place:
                az = zrel
            Hy = hips_pos[i, 1] + hipj_off[1]
            Hz = hips_pos[i, 2] + hipj_off[2]
            dy, dz = ay - Hy, az - Hz
            Dd = np.hypot(dy, dz)
            Dd = min(max(Dd, abs(l1 - l2) + 1e-6), l1 + l2 - 1e-6)
            psi = np.arctan2(dz, -dy)
            alpha = np.arccos(np.clip((l1 * l1 + Dd * Dd - l2 * l2) / (2 * l1 * Dd), -1, 1))
            kappa = np.pi - np.arccos(np.clip((l1 * l1 + l2 * l2 - Dd * Dd) / (2 * l1 * l2), -1, 1))
            s1 = psi + alpha
            s2 = s1 - kappa
            D[i, up] = _rx(-s1)
            D[i, lo] = _rx(-s2)
            D[i, ft] = Q.identity(())
            ank.append([pos_std[ft][0], ay, az])
        absolute[[up, lo, ft]] = True
        ankle_world[side] = np.array(ank)

    # 상체: 팔 흔들기와 척추 비틀림 (부모 델타에 이어 적용)
    for i in range(T):
        ph = (t[i] / cycle) % 1.0
        sw = 0.45 * np.sin(2 * np.pi * ph)
        twist = 0.10 * np.sin(2 * np.pi * ph)
        extra = {}
        extra[idx["spine1"]] = _ry(twist)
        extra[idx["spine2"]] = _ry(twist * 0.5)
        extra[idx["head"]] = _ry(-twist)
        extra[idx["upperarm@L"]] = _rx(-sw)
        extra[idx["upperarm@R"]] = _rx(sw)
        extra[idx["lowerarm@L"]] = _rx(-0.25 - 0.15 * sw)
        extra[idx["lowerarm@R"]] = _rx(-0.25 + 0.15 * sw)
        for j in range(J):
            if absolute[j]:
                continue
            p = _parent_std(keys, j)
            dp = D[i, p] if p >= 0 else Q.identity(())
            d = extra.get(j, Q.identity(()))
            D[i, j] = Q.mul(d, dp)

    # 표준 프레임 월드 회전/위치 -> 파일 프레임 -> 로컬
    wr_world_std = Q.mul(D, wr_std[None])  # (T,J,4)
    wp_std = np.empty((T, J, 3))
    # 위치: 바인드 위치를 부모 월드 회전으로 따라가게 FK 로 계산하되 힙은 직접 지정
    parents_std = [_parent_std(keys, j) for j in range(J)]
    for j in range(J):
        p = parents_std[j]
        if keys[j] == "hips":
            wp_std[:, j] = hips_pos
        elif p < 0:
            wp_std[:, j] = pos_std[j][None]
        else:
            off = pos_std[j] - pos_std[p]
            wp_std[:, j] = wp_std[:, p] + Q.rotate(D[:, p], off[None])
    pos_file = (wp_std @ G.T) * scale
    wr_file = Q.mul(qG, wr_world_std)
    rot_l, pos_l = world_to_local(skel, wr_file, pos_file)
    # 정적 관절의 위치는 오프셋 그대로(수치 잡음 제거)
    for j in range(J):
        if keys[j] != "hips":
            pos_l[:, j] = skel.offsets[j][None]
    anim = Animation(name, fps, Q.make_continuous(rot_l), pos_l)
    truth = {"ankle_std": ankle_world, "hips_std": hips_pos, "stride": stride, "stance": stance, "cycle": cycle}
    return anim, truth


def _parent_std(keys, j):
    k = keys[j]
    base, _, side = k.partition("@")
    table = {i[0]: i[1] for i in _BASE}
    if not side:
        p = table[base]
        return keys.index(p) if p in keys else -1
    parent_map = {a: b for a, b, _ in _ARM + _LEG}
    p = parent_map[base]
    if p in ("spine2", "hips"):
        return keys.index(p)
    return keys.index(p + "@" + side)


if __name__ == "__main__":
    sk, meta = build_skeleton("ue", "z_up_x", 100.0, "A", "bone_x")
    an, tr = walk_animation(sk, meta)
    wr, wp = fk(sk, an.rot, an.pos)
    print(sk.n, an.rot.shape, wp[0, sk.index("pelvis")])
