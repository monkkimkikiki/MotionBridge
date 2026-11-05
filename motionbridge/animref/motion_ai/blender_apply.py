"""AnimRef -> Blender: 모션 파일(.motion.json)을 '이미 세팅된 리그(Armature)'에 키프레임으로 입력한다.

사용 (AnimRef가 대신 실행해 줍니다. 직접 하려면):
    blender 내리그.blend --python blender_apply.py -- --motion 클립.motion.json --save 결과.blend
  또는 Blender 안의 Scripting 탭에서 이 파일을 열어 아래 RUN_ARGS 를 고친 뒤 실행.

방법 (리타깃):
  - 리그 뼈 이름을 자동으로 알아봅니다 (Mixamo, Unreal 마네킹, 3ds Max Biped, Rigify FK, UpperArm_L 식 이름 등).
    틀리거나 못 찾은 뼈는 --map 맵.json ( {"upperarm_L": "내뼈이름", ...} ) 으로 직접 지정할 수 있어요.
  - 리그의 기본 자세가 T자든 A자든 상관없이, 각 뼈가 가리키는 방향을 SMPL(모션)의 방향에 맞춘 뒤 회전을 옮깁니다.
  - 리그가 위쪽 축이 Z든 Y든, 어디를 보고 있든 골반/허벅지 위치로 알아내서 맞춥니다.
  - 골반(hips)의 이동은 다리 길이 비율로 크기를 맞춰 입력합니다.
수학 부분은 numpy만 쓰고, bpy 는 읽기/키 입력에만 씁니다."""
import json
import math
import os
import re
import sys

import numpy as np

try:
    import bpy  # Blender 안에서만 있음
except ImportError:                                  # 테스트/일반 파이썬
    bpy = None

# Blender 안에서 직접 실행할 때 여기를 고치세요 (--python 으로 실행하면 명령줄 값이 우선)
RUN_ARGS = {"motion": "", "armature": "", "map": "", "save": "", "start": 1, "reduce": 0.0,
            "markers": True, "video": "", "scale": 0.0, "no_move": False}

SMPL_CHAIN = [0, 3, 6, 9, 12, 15]

# 역할 -> SMPL 관절 번호
ROLE_SMPL = {"hips": 0, "neck": 12, "head": 15,
             "shoulder_L": 13, "shoulder_R": 14, "upperarm_L": 16, "upperarm_R": 17,
             "forearm_L": 18, "forearm_R": 19, "hand_L": 20, "hand_R": 21,
             "thigh_L": 1, "thigh_R": 2, "shin_L": 4, "shin_R": 5, "foot_L": 7, "foot_R": 8,
             "toe_L": 10, "toe_R": 11}
# 방향을 재는 기준: 역할 -> (다음 역할, SMPL 쪽 다음 관절). 다음 역할이 리그에 없으면 tail 방향을 쓰는 것은 TAIL_OK 만.
NEXT_ROLE = {"hips": ("spine_0", 3), "neck": ("head", 15), "shoulder_L": ("upperarm_L", 16),
             "shoulder_R": ("upperarm_R", 17), "upperarm_L": ("forearm_L", 18), "upperarm_R": ("forearm_R", 19),
             "forearm_L": ("hand_L", 20), "forearm_R": ("hand_R", 21), "hand_L": (None, 22), "hand_R": (None, 23),
             "thigh_L": ("shin_L", 4), "thigh_R": ("shin_R", 5), "shin_L": ("foot_L", 7), "shin_R": ("foot_R", 8),
             "foot_L": ("toe_L", 10), "foot_R": ("toe_R", 11)}
TAIL_OK = {"hand_L", "hand_R", "foot_L", "foot_R"}

# 이름 규칙 -----------------------------------------------------------------
# 짧아서 다른 단어 안에 섞여 있을 수 있는 말은 '이름 조각이 정확히 같을 때만', 긴 말은 이름 안에 들어 있으면 제외
TOKEN_EXCLUDE = {"ik", "end", "top", "ear", "eye", "ring", "tail", "hair", "pole", "target", "mch", "roll", "nub", "tip"}
SUBSTR_EXCLUDE = ("twist", "tweak", "helper", "dummy", "proxy", "thumb", "index", "middle", "pinky", "little",
                  "finger", "jaw", "cloth", "skirt", "breast", "prop", "weapon", "corrective", "stretch")
# 'stretch' 는 Auto-Rig Pro 의 변형 뼈. 키를 넣어도 컨트롤에 덮이므로 후보에서 뺍니다.


def name_tokens(name):
    """이름을 (소문자 조각들, 방향 'L'/'R'/None, 붙여쓴 키) 로. 'mixamorig:LeftUpLeg' -> (['left','up','leg'], 'L', 'upleg')"""
    s = re.split(r"[:|]", name)[-1]
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s)
    s = re.sub(r"([A-Za-z])(\d+)", r"\1 \2", s)
    toks = [t for t in re.split(r"[^A-Za-z0-9]+", s.lower()) if t]
    side = None
    keep = []
    for t in toks:
        if t in ("l", "left", "lf") and side is None:
            side = "L"
        elif t in ("r", "right", "rt") and side is None:
            side = "R"
        elif t.isdigit() or t in ("bip", "bone", "c", "cc", "mixamorig"):
            continue
        else:
            keep.append(t)
    return keep, side, "".join(keep)


def classify_bone(name):
    """뼈 이름 하나 -> (역할, 점수) 또는 None. 점수가 낮을수록 좋음."""
    toks, side, key = name_tokens(name)
    if not key:
        return None
    if any(t in TOKEN_EXCLUDE for t in toks) or any(e in key for e in SUBSTR_EXCLUDE):
        return None
    role = None
    if "forearm" in key or "lowerarm" in key or ("fore" in toks and "arm" in toks) or ("lower" in toks and "arm" in toks):
        role = "forearm"
    elif "upperarm" in key or ("upper" in toks and "arm" in toks) or key in ("arm", "armfk", "armdef", "armorg"):
        role = "upperarm"
    elif "hand" in key or "wrist" in key:
        role = "hand"
    elif "shoulder" in key or "clavicle" in key or "collar" in key:
        role = "shoulder"
    elif "thigh" in key or "upleg" in key or "upperleg" in key or ("up" in toks and "leg" in toks) \
            or ("upper" in toks and "leg" in toks):
        role = "thigh"
    elif "shin" in key or "calf" in key or "lowerleg" in key or ("lower" in toks and "leg" in toks) \
            or key in ("leg", "legfk", "legdef", "legorg"):
        role = "shin"
    elif "foot" in key or "ankle" in key:
        role = "foot"
    elif "toe" in key or key.startswith("ball"):
        role = "toe"
    elif "neck" in key:
        role = "neck"
    elif "head" in key:
        role = "head"
    elif "spine" in key or "chest" in key or "abdomen" in key:
        role = "spine"
    elif "hips" in key or "pelvis" in key or key in ("hip", "hipfk", "hipsfk"):
        role = "hips"
    if role is None:
        return None
    limb = role in ("forearm", "upperarm", "hand", "shoulder", "thigh", "shin", "foot", "toe")
    if limb and side is None:
        return None
    score = 0.0
    if "def" in toks:
        score += 5
    if "org" in toks:
        score += 6
    if "fk" not in toks:
        score += 0.5
    score += 0.01 * len(key)
    return (role + ("_" + side if limb else ""), score)


def auto_map(rig):
    """리그 뼈들 -> {역할: 뼈 이름}. rig: read_rig() 결과. 반환: (mapping, 메모들)"""
    cands = {}
    for name in rig["order"]:
        r = classify_bone(name)
        if r:
            cands.setdefault(r[0], []).append((r[1], name))
    has_fk = any("fk" in name_tokens(n)[0] for lst in cands.values() for _, n in lst)
    mapping, notes = {}, []
    for role, lst in cands.items():
        if role == "spine":
            continue
        lst.sort(key=lambda x: (x[0] + (3.0 if has_fk and "fk" not in name_tokens(x[1])[0] else 0.0), x[1]))
        mapping[role] = lst[0][1]
    # 척추: 계층 깊이 순서대로, 같은 깊이에 여러 개면 이름이 앞선 하나만
    sp = sorted((n for _, n in cands.get("spine", [])), key=lambda n: (rig["depth"][n], n))
    uniq, seen = [], set()
    for n in sp:
        if rig["depth"][n] in seen:
            continue
        seen.add(rig["depth"][n])
        uniq.append(n)
    for i, n in enumerate(uniq[:6]):
        mapping[f"spine_{i}"] = n
    if any("fk" in name_tokens(n)[0] for n in mapping.values()):
        notes.append("FK 컨트롤 뼈에 키를 넣어요. 리그의 IK/FK 전환이 있다면 FK로 맞춰야 보여요.")
    return mapping, notes


def spine_smpl_joints(k):
    """척추 뼈 k개에 대응시킬 SMPL 척추 관절 번호들."""
    if k <= 0:
        return []
    if k == 1:
        return [6]
    if k == 2:
        return [3, 9]
    if k == 3:
        return [3, 6, 9]
    return [[3, 6, 9][int(round(i * 2.0 / (k - 1)))] for i in range(k)]


# 수학 ----------------------------------------------------------------------
def quat_to_mat(q):
    q = np.asarray(q, float)
    q = q / np.linalg.norm(q, axis=-1, keepdims=True)
    w, x, y, z = q[..., 0], q[..., 1], q[..., 2], q[..., 3]
    R = np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
                  2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
                  2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], axis=-1)
    return R.reshape(q.shape[:-1] + (3, 3))


def mat_to_quat(R):
    R = np.asarray(R, float)
    sh = R.shape[:-2]
    M = R.reshape(-1, 3, 3)
    q = np.empty((M.shape[0], 4))
    for i, m in enumerate(M):
        t = m[0, 0] + m[1, 1] + m[2, 2]
        if t > 0:
            s = math.sqrt(t + 1.0) * 2
            w, x, y, z = 0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s
        elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
            s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
            w, x, y, z = (m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s
        elif m[1, 1] > m[2, 2]:
            s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
            w, x, y, z = (m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s
        else:
            s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
            w, x, y, z = (m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s
        qq = np.array([w, x, y, z])
        qq /= np.linalg.norm(qq)
        q[i] = qq if qq[0] >= 0 else -qq
    return q.reshape(sh + (4,))


def orthonormal(R):
    """스케일/기울임이 섞인 3x3 을 가장 가까운 회전으로."""
    u, _, vt = np.linalg.svd(R)
    r = u @ vt
    if np.linalg.det(r) < 0:
        u[:, -1] *= -1
        r = u @ vt
    return r


def rotation_between(a, b):
    """a 방향을 b 방향으로 돌리는 최소 회전 (3x3)."""
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    a = a / (np.linalg.norm(a) + 1e-12)
    b = b / (np.linalg.norm(b) + 1e-12)
    v = np.cross(a, b)
    c = float(np.dot(a, b))
    s = np.linalg.norm(v)
    if s < 1e-9:
        if c > 0:
            return np.eye(3)
        axis = np.cross(a, [1.0, 0, 0])               # 정반대: 아무 수직 축으로 180도
        if np.linalg.norm(axis) < 1e-6:
            axis = np.cross(a, [0, 1.0, 0])
        axis /= np.linalg.norm(axis)
        K = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
        return np.eye(3) + 2 * K @ K
    K = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + K + K @ K * ((1 - c) / (s * s))


def smpl_world_rotations(rot_quat, parents):
    R = quat_to_mat(rot_quat)                                  # (N,24,3,3) 로컬
    W = np.empty_like(R)
    for j in range(R.shape[1]):
        p = parents[j]
        W[:, j] = R[:, j] if p < 0 else W[:, p] @ R[:, j]
    return W


def _frame_from(pts_up_from, pts_up_to, pts_left_a, pts_left_b):
    """(위 방향, 왼쪽 방향) 두 벡터로 열 = (왼쪽, 위, 앞) 인 3x3 을 만든다. 만들 수 없으면 None."""
    up = pts_up_to - pts_up_from
    left = pts_left_a - pts_left_b
    if np.linalg.norm(up) < 1e-6 or np.linalg.norm(left) < 1e-6:
        return None
    up = up / np.linalg.norm(up)
    left = left - up * np.dot(left, up)
    if np.linalg.norm(left) < 1e-6:
        return None
    left /= np.linalg.norm(left)
    return np.stack([left, up, np.cross(left, up)], axis=1)


def rig_frame(rig, mapping, rest_joints):
    """SMPL 공간을 리그 공간으로 보내는 회전 C (3x3).
    리그와 SMPL 양쪽에서 '골반→목' 과 '왼쪽 허벅지−오른쪽 허벅지' 같은 같은 부위로 (왼쪽, 위, 앞) 틀을 재서 맞춘다.
    (SMPL의 위쪽을 (0,1,0)이라고 가정하지 않는다: 척추가 살짝 기울어 있으면 몇 도씩 틀어지기 때문)
    알아낼 수 없으면 Blender 기본(+X 왼쪽, +Z 위, -Y 앞)을 가정한다. 반환: (C, 알아냈는지)"""
    rest_joints = np.asarray(rest_joints, float)

    def head(role):
        n = mapping.get(role)
        return None if n is None else rig["rest"][n][:3, 3]

    base = np.array([[1.0, 0, 0], [0, 0, 1.0], [0, -1.0, 0]]).T        # 열: left=+X, up=+Z, forward=-Y
    top_role, top_j = ("neck", 12) if head("neck") is not None else ("head", 15)
    if head("hips") is None or head(top_role) is None:
        return base, False
    pair = None
    for ra, rb, ja, jb in (("thigh_L", "thigh_R", 1, 2), ("upperarm_L", "upperarm_R", 16, 17),
                           ("shoulder_L", "shoulder_R", 13, 14)):
        if head(ra) is not None and head(rb) is not None:
            pair = (head(ra), head(rb), rest_joints[ja], rest_joints[jb])
            break
    if pair is None:
        return base, False
    Fr = _frame_from(head("hips"), head(top_role), pair[0], pair[1])
    Fs = _frame_from(rest_joints[0], rest_joints[top_j], pair[2], pair[3])
    if Fr is None or Fs is None:
        return base, False
    return Fr @ Fs.T, True


def _dist(a, b):
    return float(np.linalg.norm(np.asarray(a) - np.asarray(b)))


def leg_scale(rig, mapping, rest_joints):
    """리그 다리 길이 / SMPL 다리 길이 (크기 맞춤). 못 재면 0."""
    def h(role):
        n = mapping.get(role)
        return None if n is None else rig["rest"][n][:3, 3]
    for s, (hip, kn, an) in (("L", (1, 4, 7)), ("R", (2, 5, 8))):
        a, b, c = h(f"thigh_{s}"), h(f"shin_{s}"), h(f"foot_{s}")
        if a is not None and b is not None and c is not None:
            leg_rig = _dist(a, b) + _dist(b, c)
            leg_smpl = _dist(rest_joints[hip], rest_joints[kn]) + _dist(rest_joints[kn], rest_joints[an])
            if leg_smpl > 1e-6:
                return leg_rig / leg_smpl
    return 0.0


def retarget(motion, rig, mapping, scale=0.0, move=True):
    """모션 + 리그 -> 뼈마다 '포즈 회전(로컬, 쿼터니언 wxyz)' 과 골반 위치.
    rig = {"order": [부모 먼저 정렬된 뼈 이름], "parent": {이름: 부모|None}, "rest": {이름: 4x4(리그 공간)},
           "inherit": {이름: bool}, "tail": {이름: 3}, "depth": {이름: int}}
    반환: {"rot": {이름: (N,4)}, "loc": {이름: (N,3)}, "info": {...}}"""
    parents = motion["parents"]
    rest_s = np.asarray(motion["rest_joints"], float)
    rot = np.asarray(motion["rot"], float)
    N = rot.shape[0]
    Wsm = smpl_world_rotations(rot, parents)                    # (N,24,3,3)
    C, found = rig_frame(rig, mapping, rest_s)                  # SMPL 공간 -> 리그 공간 회전
    # 역할 -> 뼈 목록 (척추는 SMPL 척추 관절을 나눠 맡음)
    role_smpl = {r: j for r, j in ROLE_SMPL.items() if r in mapping}
    spines = [mapping[f"spine_{i}"] for i in range(10) if f"spine_{i}" in mapping]
    for i, j in enumerate(spine_smpl_joints(len(spines))):
        role_smpl[f"spine_{i}"] = j

    # 각 뼈의 목표 세계 회전 (리그 공간)
    Rr0 = {b: orthonormal(rig["rest"][b][:3, :3]) for b in rig["order"]}
    head = {b: rig["rest"][b][:3, 3] for b in rig["order"]}
    target = {}
    dir_notes = {}
    for role, j in role_smpl.items():
        b = mapping[role]
        # 방향 맞춤(Rshort): 이 뼈 -> 다음 뼈 방향이 SMPL 쪽과 같아지게 하는 최소 회전
        child_role, child_smpl = None, None
        if role.startswith("spine_"):
            nxt = f"spine_{int(role.split('_')[1]) + 1}"
            child_role = nxt if nxt in mapping else ("neck" if "neck" in mapping else None)
            child_smpl = role_smpl.get(nxt, 12)
        elif role == "hips":
            child_role = "spine_0" if "spine_0" in mapping else None
            child_smpl = role_smpl.get("spine_0", 3)
        elif role in NEXT_ROLE:
            child_role, child_smpl = NEXT_ROLE[role]
        d_rig = None
        if child_role is not None and child_role in mapping and child_smpl is not None:
            d_rig = head[mapping[child_role]] - head[b]
        elif role in TAIL_OK and child_smpl is not None:
            d_rig = np.asarray(rig["tail"][b]) - head[b]
        Rshort = np.eye(3)
        if d_rig is not None and child_smpl is not None and child_smpl != j:
            d_smpl = rest_s[child_smpl] - rest_s[j]
            if np.linalg.norm(d_rig) > 1e-9 and np.linalg.norm(d_smpl) > 1e-9:
                Rshort = rotation_between(d_rig, C @ d_smpl)
                dir_notes[role] = "방향 맞춤"
        # R_target(f) = C · R_sw(f) · C^T · Rshort · R_r0
        A = Rshort @ Rr0[b]
        target[b] = np.einsum("ij,njk,kl,lm->nim", C, Wsm[:, j], C.T, A)

    # 포즈 회전을 부모 먼저 순서로 추적하면서 로컬 기저 회전을 계산
    pose_rot = {}
    out_q = {}
    for b in rig["order"]:
        p = rig["parent"][b]
        use_parent = p is not None and rig["inherit"][b]
        if b in target:
            Rt = target[b]
            if use_parent:
                # B_pose = P_pose · P_rest^-1 · B_rest · basis  ->  basis = B_rest^-1 · P_rest · P_pose^-1 · Rt
                M = Rr0[b].T @ Rr0[p]
                basis = np.einsum("ij,njk,nkl->nil", M, np.swapaxes(pose_rot[p], -1, -2), Rt)
            else:
                basis = np.einsum("ij,njk->nik", Rr0[b].T, Rt)
            pose_rot[b] = Rt
            out_q[b] = mat_to_quat(basis)
        else:
            if use_parent:
                pose_rot[b] = np.einsum("njk,kl->njl", pose_rot[p], Rr0[p].T @ Rr0[b])
            else:
                pose_rot[b] = np.broadcast_to(Rr0[b], (N, 3, 3)).copy()

    # 골반 이동
    loc = {}
    info = {"scale": 1.0, "frame_found": found, "mapped": len(target)}
    if move and "hips" in mapping:
        hb = mapping["hips"]
        s = scale if scale > 0 else leg_scale(rig, mapping, rest_s)
        s = s if s > 0 else 1.0
        info["scale"] = s
        root = np.asarray(motion["root_pos"], float)
        ank = min(rest_s[7][1], rest_s[8][1])
        h_stand = -ank + 0.08                                   # 서 있을 때 골반 높이 (발목~바닥 약 8cm)
        d = np.stack([root[:, 0], root[:, 1] - h_stand, root[:, 2]], axis=1) * s
        d_rig = d @ C.T
        p = rig["parent"][hb]
        if p is not None and rig["inherit"][hb]:
            # 부모 포즈 회전이 위치도 돌린다
            m = np.einsum("njk,kl->njl", pose_rot[p], Rr0[p].T @ Rr0[hb])
        else:
            m = np.broadcast_to(Rr0[hb], (N, 3, 3))
        loc[hb] = np.einsum("nji,nj->ni", m, d_rig)               # m^-1 · d  (m 은 회전이라 전치)
    return {"rot": out_q, "loc": loc, "info": info, "frame": C, "dir_notes": dir_notes}


def reduce_keys(q, tol_deg, must=()):
    """회전 곡선에서 꼭 필요한 프레임만 남긴다 (Ramer–Douglas–Peucker, 오차 = 각도).
    q: (N,4). 반환: 남길 프레임 번호 목록(정렬)."""
    N = len(q)
    keep = {0, N - 1} | {int(m) for m in must if 0 <= int(m) < N}
    if tol_deg <= 0 or N <= 2:
        return list(range(N))
    qq = q.copy()
    for i in range(1, N):                                         # 부호 연속
        if np.dot(qq[i], qq[i - 1]) < 0:
            qq[i] = -qq[i]
    tol = math.radians(tol_deg)

    def err(a, b, i):
        t = (i - a) / float(b - a)
        d = np.dot(qq[a], qq[b])
        th = math.acos(max(-1.0, min(1.0, abs(d))))
        if th < 1e-6:
            qi = qq[a]
        else:
            qi = (math.sin((1 - t) * th) * qq[a] + math.sin(t * th) * qq[b] * (1 if d >= 0 else -1)) / math.sin(th)
        c = abs(float(np.dot(qi / np.linalg.norm(qi), qq[i])))
        return 2 * math.acos(max(-1.0, min(1.0, c)))

    def rec(a, b):
        if b - a < 2:
            return
        worst, wi = -1.0, None
        for i in range(a + 1, b):
            e = err(a, b, i)
            if e > worst:
                worst, wi = e, i
        if worst > tol:
            keep.add(wi)
            rec(a, wi)
            rec(wi, b)
    pts = sorted(keep)
    for a, b in zip(pts, pts[1:]):
        rec(a, b)
    return sorted(keep)


def reduce_vec_keys(v, tol, must=()):
    """위치 곡선(N,3) 줄이기. tol: 같은 단위(m)."""
    N = len(v)
    keep = {0, N - 1} | {int(m) for m in must if 0 <= int(m) < N}
    if tol <= 0 or N <= 2:
        return list(range(N))

    def rec(a, b):
        if b - a < 2:
            return
        t = (np.arange(a + 1, b) - a) / float(b - a)
        lin = v[a][None] * (1 - t[:, None]) + v[b][None] * t[:, None]
        e = np.linalg.norm(lin - v[a + 1:b], axis=1)
        k = int(np.argmax(e))
        if e[k] > tol:
            keep.add(a + 1 + k)
            rec(a, a + 1 + k)
            rec(a + 1 + k, b)
    pts = sorted(keep)
    for a, b in zip(pts, pts[1:]):
        rec(a, b)
    return sorted(keep)


# Blender 쪽 ----------------------------------------------------------------
def read_rig(arm):
    """Blender Armature 오브젝트 -> retarget() 에 쓰는 rig dict."""
    bones = list(arm.data.bones)
    byname = {b.name: b for b in bones}

    def depth(b):
        d = 0
        while b.parent is not None:
            b = b.parent
            d += 1
        return d
    order = sorted(bones, key=lambda b: (depth(b), b.name))
    rig = {"order": [b.name for b in order], "parent": {}, "rest": {}, "inherit": {}, "tail": {}, "depth": {}}
    for b in order:
        rig["parent"][b.name] = b.parent.name if b.parent else None
        rig["rest"][b.name] = np.array([list(r) for r in b.matrix_local], float)
        rig["inherit"][b.name] = bool(getattr(b, "use_inherit_rotation", True))
        rig["tail"][b.name] = np.array(list(b.tail_local), float)
        rig["depth"][b.name] = depth(b)
    assert len(byname) == len(order)
    return rig


def pick_armature(name=""):
    objs = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if not objs:
        raise RuntimeError("이 파일에 리그(Armature)가 없어요.")
    if name:
        for o in objs:
            if o.name == name:
                return o
        raise RuntimeError(f"'{name}' 이라는 리그가 없어요. 있는 리그: {[o.name for o in objs]}")
    act = getattr(bpy.context, "active_object", None)
    if act is not None and act.type == "ARMATURE":
        return act
    return max(objs, key=lambda o: len(o.data.bones))


def log(msg):
    """AnimRef 가 읽는 로그. Windows 파이프에서도 한글이 깨지지 않게 UTF-8 바이트로 직접 쓴다."""
    line = ("ANIMREF: " + msg + "\n").encode("utf-8")
    try:
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()
    except Exception:  # noqa: BLE001  (버퍼가 없는 환경)
        print("ANIMREF: " + msg, flush=True)


def add_reference_video(video, scene, start):
    """참고 영상을 카메라 배경으로 깐다 (실패해도 무시)."""
    try:
        cam = scene.camera
        if cam is None:
            data = bpy.data.cameras.new("AnimRef_Cam")
            cam = bpy.data.objects.new("AnimRef_Cam", data)
            scene.collection.objects.link(cam)
            cam.location = (0, -6, 1.2)
            cam.rotation_euler = (math.radians(90), 0, 0)
            scene.camera = cam
        clip = bpy.data.movieclips.load(video)
        cam.data.show_background_images = True
        bg = cam.data.background_images.new()
        bg.source = "MOVIE_CLIP"
        bg.clip = clip
        bg.clip_user.use_render_undistorted = False
        bg.alpha = 0.6
        bg.frame_method = "FIT"
        bg.clip_user.proxy_render_size = "PROXY_100"
        log("참고 영상을 카메라 배경으로 깔았어요 (카메라 시점으로 보세요: 숫자 0).")
    except Exception as e:                                          # noqa: BLE001
        log(f"참고 영상은 깔지 못했어요 ({type(e).__name__}: {e})")


def apply_motion(args):
    scene = bpy.context.scene
    with open(args["motion"], encoding="utf-8") as f:
        motion = json.load(f)
    arm = pick_armature(args.get("armature", ""))
    log(f"리그: {arm.name} (뼈 {len(arm.data.bones)}개)")
    rig = read_rig(arm)
    mapping, notes = auto_map(rig)
    if args.get("map"):
        with open(args["map"], encoding="utf-8") as f:
            extra = json.load(f)
        bad = [v for v in extra.values() if v not in rig["rest"]]
        if bad:
            raise RuntimeError(f"맵 파일에 없는 뼈 이름이 있어요: {bad}")
        mapping.update(extra)
    for n in notes:
        log(n)
    need = ["hips", "thigh_L", "thigh_R", "shin_L", "shin_R", "upperarm_L", "upperarm_R", "forearm_L", "forearm_R"]
    missing = [r for r in need if r not in mapping]
    log("뼈 대응: " + ", ".join(f"{r}={mapping[r]}" for r in sorted(mapping)))
    if "hips" not in mapping or len(mapping) < 6:
        raise RuntimeError("뼈 이름을 거의 못 알아봤어요. 맵 파일(--map)로 직접 알려 주세요. "
                           f"찾은 것: {sorted(mapping)} / 이름 예: {rig['order'][:8]}")
    if missing:
        log("못 찾은 필수 뼈: " + ", ".join(missing) + " → 맵 파일로 지정하면 더 정확해져요.")

    res = retarget(motion, rig, mapping, scale=float(args.get("scale", 0) or 0), move=not args.get("no_move"))
    info = res["info"]
    log(f"방향 자동 인식: {'성공' if info['frame_found'] else '실패(Blender 기본 방향 가정)'} / 크기 비율 {info['scale']:.2f}")

    fps = float(motion["fps"])
    start = int(args.get("start", 1))
    N = int(motion["frames"])
    scene.render.fps = max(1, int(round(fps)))
    scene.render.fps_base = scene.render.fps / fps
    scene.frame_start = start
    scene.frame_end = start + N - 1

    markers = [float(m) for m in motion.get("markers", [])] if args.get("markers", True) else []
    mark_frames = [int(round(m * fps)) for m in markers]
    must = set()
    for mf in mark_frames:                                          # 타격 순간과 앞뒤 2프레임은 꼭 남김
        must.update(range(mf - 2, mf + 3))
    reduce_deg = float(args.get("reduce", 0) or 0)

    name = os.path.basename(args["motion"]).replace(".motion.json", "")
    arm.animation_data_create()
    act = bpy.data.actions.new("AnimRef_" + name)
    arm.animation_data.action = act
    total_keys = 0
    for i, (bname, q) in enumerate(sorted(res["rot"].items())):
        pb = arm.pose.bones[bname]
        pb.rotation_mode = "QUATERNION"
        frames = reduce_keys(q, reduce_deg, must) if reduce_deg > 0 else range(N)
        for fi in frames:
            pb.rotation_quaternion = tuple(float(x) for x in q[fi])
            pb.keyframe_insert("rotation_quaternion", frame=start + fi)
            total_keys += 1
        if getattr(pb, "constraints", None) and len(pb.constraints):
            log(f"주의: '{bname}' 뼈에는 제약(Constraint)이 있어서 키가 덮일 수 있어요.")
    for bname, v in res["loc"].items():
        pb = arm.pose.bones[bname]
        frames = reduce_vec_keys(v, 0.005 * info["scale"], must) if reduce_deg > 0 else range(N)
        for fi in frames:
            pb.location = tuple(float(x) for x in v[fi])
            pb.keyframe_insert("location", frame=start + fi)
            total_keys += 1
    for k, mf in enumerate(mark_frames):
        scene.timeline_markers.new(f"타격 {k + 1}", frame=start + mf)
    scene.frame_set(start)
    if args.get("video"):
        add_reference_video(args["video"], scene, start)
    log(f"키프레임 {total_keys}개 입력 (뼈 {len(res['rot'])}개 × {N}프레임" + (f", 줄임 {reduce_deg}°" if reduce_deg > 0 else "") + ")")
    if args.get("save"):
        bpy.ops.wm.save_as_mainfile(filepath=args["save"])
        log("저장했어요: " + args["save"])
    log("DONE")
    return res


def parse_args(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--motion", required=True)
    ap.add_argument("--armature", default="")
    ap.add_argument("--map", default="")
    ap.add_argument("--save", default="")
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--reduce", type=float, default=0.0, help="키 줄이기 허용 오차(도). 0이면 매 프레임 키")
    ap.add_argument("--no-markers", action="store_true")
    ap.add_argument("--video", default="")
    ap.add_argument("--scale", type=float, default=0.0)
    ap.add_argument("--no-move", action="store_true")
    a = ap.parse_args(argv)
    return {"motion": a.motion, "armature": a.armature, "map": a.map, "save": a.save, "start": a.start,
            "reduce": a.reduce, "markers": not a.no_markers, "video": a.video, "scale": a.scale, "no_move": a.no_move}


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else None
    args = parse_args(argv) if argv else dict(RUN_ARGS)
    if not args.get("motion"):
        raise SystemExit("모션 파일(--motion)을 지정해 주세요.")
    try:
        apply_motion(args)
    except Exception as e:                                          # noqa: BLE001
        log(f"ERROR {type(e).__name__}: {e}")
        raise


if bpy is not None and __name__ == "__main__":
    main()
