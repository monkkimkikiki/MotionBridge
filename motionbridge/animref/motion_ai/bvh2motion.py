"""외부 모션 AI 서비스(DeepMotion, Move.ai, Rokoko Vision, Plask 등)가 준 BVH 를 AnimRef 모션 파일로 바꾼다.

  BVH(어떤 뼈 이름/축/단위든)  --관절 구조로 사람 뼈대를 찾아서-->  SMPL 24관절 모션(.motion.json/.motion.txt/.bvh)

핵심 아이디어
  · 뼈 이름은 믿지 않고 '구조'로 찾는다: 골반(자식 3개 이상) → 가장 큰 가지가 척추 → 나머지 둘이 다리,
    척추 맨 위에서 갈라지는 가지 = 목/머리, 왼팔, 오른팔.
  · 위(up)는 '척추 쪽 평균 위치'로, 앞(forward)은 '발끝 방향'으로(없으면 뼈 이름의 Left/Right) 정한다.
  · 기본 자세(BVH의 0회전 자세)의 뼈 위치를 rest_joints 로 같이 저장하므로, 리타깃은 리그와 이 기본 자세의
    뼈 방향 차이를 알아서 보정한다 (T자세든 A자세든 상관없음).
  · numpy 만 쓴다.
"""
import math
import re

import numpy as np

JOINT_NAMES = ["Pelvis", "L_Hip", "R_Hip", "Spine1", "L_Knee", "R_Knee", "Spine2", "L_Ankle", "R_Ankle",
               "Spine3", "L_Foot", "R_Foot", "Neck", "L_Collar", "R_Collar", "Head", "L_Shoulder",
               "R_Shoulder", "L_Elbow", "R_Elbow", "L_Wrist", "R_Wrist", "L_Hand", "R_Hand"]
PARENTS = [-1, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9, 12, 13, 14, 16, 17, 18, 19, 20, 21]
NJ = 24
END_WORDS = ("end", "top", "tip", "nub")


class BvhError(ValueError):
    pass


# ---------------------------------------------------------------- 파서
def parse_bvh(text):
    """반환: dict(names, parent, offset(J,3), chans(list[list[str]]), data(N,C), frame_time, has_site)"""
    tok = text.replace("\r", "\n").split()
    i = 0
    names, parent, offset, chans, has_site = [], [], [], [], []

    def need(k):
        if i + k > len(tok):
            raise BvhError("BVH 파일이 중간에 끊겨 있어요.")

    if not tok or tok[0].upper() != "HIERARCHY":
        raise BvhError("BVH 파일이 아니에요 (HIERARCHY 로 시작해야 해요).")
    i = 1
    stack = []
    cur = -1
    while i < len(tok):
        t = tok[i]
        u = t.upper()
        if u in ("ROOT", "JOINT"):
            need(2)
            names.append(tok[i + 1])
            parent.append(stack[-1] if stack else -1)
            offset.append([0.0, 0.0, 0.0])
            chans.append([])
            has_site.append(False)
            cur = len(names) - 1
            i += 2
        elif u == "END" and i + 1 < len(tok) and tok[i + 1].upper() == "SITE":
            has_site[stack[-1]] = True
            i += 2
            # { OFFSET x y z }
            depth = 0
            while i < len(tok):
                if tok[i] == "{":
                    depth += 1
                elif tok[i] == "}":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
        elif t == "{":
            stack.append(cur)
            i += 1
        elif t == "}":
            if stack:
                stack.pop()
            i += 1
        elif u == "OFFSET":
            need(4)
            if stack:
                offset[stack[-1]] = [float(tok[i + 1]), float(tok[i + 2]), float(tok[i + 3])]
            i += 4
        elif u == "CHANNELS":
            need(2)
            n = int(tok[i + 1])
            need(2 + n)
            chans[stack[-1]] = [c for c in tok[i + 2:i + 2 + n]]
            i += 2 + n
        elif u == "MOTION":
            i += 1
            break
        else:
            i += 1
    if not names:
        raise BvhError("BVH 에서 뼈를 찾지 못했어요.")
    # Frames: N / Frame Time: dt
    n_frames, dt = None, None
    while i < len(tok):
        if tok[i].lower().startswith("frames"):
            n_frames = int(float(tok[i + 1] if tok[i].endswith(":") and tok[i].lower() == "frames:" else tok[i + 1]))
            i += 2
        elif tok[i].lower().startswith("frame") and i + 2 < len(tok) and tok[i + 1].lower().startswith("time"):
            j = i + 2
            dt = float(tok[j + 1] if tok[j] == ":" else tok[j])
            i = j + (2 if tok[j] == ":" else 1)
            break
        else:
            i += 1
    if not n_frames or not dt or dt <= 0:
        raise BvhError("BVH 의 프레임 정보(Frames / Frame Time)를 읽지 못했어요.")
    total = sum(len(c) for c in chans)
    vals = np.array(tok[i:i + n_frames * total], dtype=float)
    if vals.size < total * 2:
        raise BvhError("BVH 의 모션 데이터가 너무 짧아요.")
    n_frames = vals.size // total
    data = vals[: n_frames * total].reshape(n_frames, total)
    return {"names": names, "parent": parent, "offset": np.array(offset, float), "chans": chans, "data": data,
            "frame_time": dt, "has_site": has_site}


# ---------------------------------------------------------------- 회전/FK
def _rot(axis, a):
    c, s = np.cos(a), np.sin(a)
    n = len(a)
    m = np.zeros((n, 3, 3))
    m[:, axis, axis] = 1.0
    i, j = [(1, 2), (2, 0), (0, 1)][axis]
    m[:, i, i] = c
    m[:, j, j] = c
    m[:, i, j] = -s
    m[:, j, i] = s
    return m


def forward_kinematics(b):
    """프레임별 전역 회전 G (N,J,3,3), 전역 위치 P (N,J,3), 기본 자세 위치 Prest (J,3)."""
    names, parent, off, chans, data = b["names"], b["parent"], b["offset"], b["chans"], b["data"]
    n, nj = data.shape[0], len(names)
    G = np.zeros((n, nj, 3, 3))
    P = np.zeros((n, nj, 3))
    Prest = np.zeros((nj, 3))
    col = 0
    for j in range(nj):
        ch = chans[j]
        loc_pos = np.tile(off[j], (n, 1))
        R = np.tile(np.eye(3), (n, 1, 1))
        for k, c in enumerate(ch):
            cl = c.lower()
            v = data[:, col + k]
            if cl.endswith("position"):
                loc_pos[:, "xyz".index(cl[0])] += v
            elif cl.endswith("rotation"):
                R = R @ _rot("xyz".index(cl[0]), np.radians(v))          # 채널에 적힌 순서대로 곱한다
        col += len(ch)
        p = parent[j]
        if p < 0:
            G[:, j] = R
            P[:, j] = loc_pos
            Prest[j] = off[j]
        else:
            G[:, j] = G[:, p] @ R
            P[:, j] = P[:, p] + np.einsum("nij,nj->ni", G[:, p], loc_pos)
            Prest[j] = Prest[p] + off[j]
    return G, P, Prest


# ---------------------------------------------------------------- 구조로 사람 뼈대 찾기
def _children(parent):
    ch = [[] for _ in parent]
    for j, p in enumerate(parent):
        if p >= 0:
            ch[p].append(j)
    return ch


def _subtree(ch, j):
    out, st = [], [j]
    while st:
        k = st.pop()
        out.append(k)
        st.extend(ch[k])
    return out


def _is_end(name):
    n = name.lower()
    return any(w in n for w in END_WORDS)


def _side(name):
    """이름에서 왼쪽(+1)/오른쪽(-1)/모름(0)."""
    n = name.lower()
    if "left" in n:
        return 1
    if "right" in n:
        return -1
    if re.search(r"(?:^|[^a-z])l(?:[^a-z]|$)", n) or re.match(r"^[lL](?=[A-Z])", name):
        return 1
    if re.search(r"(?:^|[^a-z])r(?:[^a-z]|$)", n) or re.match(r"^[rR](?=[A-Z])", name):
        return -1
    return 0


def _snap(v, min_dom=0.7):
    """벡터를 가장 가까운 축(±x,±y,±z)으로. 한 축이 충분히 우세하지 않으면 None."""
    v = np.asarray(v, float)
    nv = np.linalg.norm(v)
    if nv < 1e-9:
        return None
    a = int(np.argmax(np.abs(v)))
    if abs(v[a]) / nv < min_dom:
        return None
    e = np.zeros(3)
    e[a] = 1.0 if v[a] > 0 else -1.0
    return e


def _chain(ch, names, j, cap):
    """j 에서 시작해 자식이 하나뿐인 동안 따라간다 (갈라지는 관절까지 포함). 끝 표시용 뼈('..End')는 뺀다."""
    out = [j]
    while len(ch[out[-1]]) == 1 and len(out) < cap + 1:
        out.append(ch[out[-1]][0])
    while len(out) > 1 and _is_end(names[out[-1]]):
        out.pop()
    return out[:cap]


def find_humanoid(b, Prest):
    """반환: (smpl_to_bvh: 길이 24 리스트(없으면 None), basis B(3x3), notes)"""
    names, parent = b["names"], b["parent"]
    ch = _children(parent)
    notes = []

    # 1) 골반: 앞에서부터 처음으로 자식이 3개 이상인 관절
    pel = next((j for j in range(len(names)) if len(ch[j]) >= 3), None)
    if pel is None:
        raise BvhError("사람 골격으로 읽지 못했어요: 골반(자식 뼈가 3개 이상인 뼈)을 찾지 못했어요.")
    sizes = {c: len(_subtree(ch, c)) for c in ch[pel]}
    spine0 = max(sizes, key=sizes.get)
    others = [c for c in ch[pel] if c != spine0]

    # 2) 위쪽 = 골반 → 척추 쪽 뼈들의 평균 위치 (양팔은 좌우로 상쇄된다)
    up = _snap(Prest[_subtree(ch, spine0)].mean(axis=0) - Prest[pel])
    if up is None:
        raise BvhError("사람 골격으로 읽지 못했어요: 위쪽 방향을 정하지 못했어요 (기본 자세가 서 있는 자세가 아닌 것 같아요).")
    # 3) 다리 두 개: 가장 아래쪽으로 뻗은 두 가지
    legs = sorted(others, key=lambda c: float(np.dot(Prest[c] - Prest[pel], up)))[:2]
    if len(legs) < 2 or any(float(np.dot(Prest[c] - Prest[pel], up)) >= 0 for c in legs):
        raise BvhError("사람 골격으로 읽지 못했어요: 다리를 찾지 못했어요.")
    leg_chain = {c: _chain(ch, names, c, 4) for c in legs}

    # 4) 앞쪽: 발끝 방향 (없으면 뼈 이름의 Left/Right)
    fwd_vecs = []
    for c in legs:
        lc = leg_chain[c]
        if len(lc) >= 4:
            v = Prest[lc[3]] - Prest[lc[2]]
            fwd_vecs.append(v - np.dot(v, up) * up)
    forward = None
    if fwd_vecs:
        v = np.mean(fwd_vecs, axis=0)
        ref = np.linalg.norm(Prest[leg_chain[legs[0]][1]] - Prest[leg_chain[legs[0]][2]]) + 1e-9
        if np.linalg.norm(v) / ref > 0.15:
            forward = _snap(v, 0.7)
    if forward is not None:
        left = np.cross(up, forward)
        notes.append("앞쪽 방향은 발끝 방향으로 정했어요.")
    else:
        s = [_side(names[c]) for c in legs]
        if sorted(s) != [-1, 1]:
            raise BvhError("사람 골격으로 읽지 못했어요: 발끝이 없고 뼈 이름에서도 왼쪽/오른쪽을 알 수 없어요.")
        lv = Prest[legs[s.index(1)]] - Prest[legs[s.index(-1)]]
        left = _snap(lv - np.dot(lv, up) * up)
        if left is None:
            raise BvhError("사람 골격으로 읽지 못했어요: 좌우 방향을 정하지 못했어요.")
        forward = np.cross(left, up)
        notes.append("앞쪽 방향은 뼈 이름의 왼쪽/오른쪽으로 정했어요.")
    left_leg = next(c for c in legs if np.dot(Prest[c] - Prest[pel], left) > 0)
    right_leg = next(c for c in legs if c != left_leg)
    B = np.stack([left, up, forward])
    if abs(np.linalg.det(B) - 1.0) > 1e-6:
        raise BvhError("사람 골격으로 읽지 못했어요: 축 방향이 이상해요.")

    m = [None] * NJ
    m[0] = pel
    for side, leg, base in ((1, left_leg, 0), (2, right_leg, 1)):
        lc = leg_chain[leg]
        m[side] = lc[0]
        if len(lc) > 1:
            m[side + 3] = lc[1]
        if len(lc) > 2:
            m[side + 6] = lc[2]
        if len(lc) > 3:
            m[side + 9] = lc[3]
    if m[4] is None or m[7] is None:
        raise BvhError("사람 골격으로 읽지 못했어요: 다리 뼈가 3개(엉덩이-무릎-발목) 미만이에요.")

    # 5) 척추: 골반 위 가지를 따라 올라가다 갈라지는 곳이 가슴(Spine3)
    S = _chain(ch, names, spine0, 6)
    top = S[-1]
    if len(ch[top]) < 2:
        raise BvhError("사람 골격으로 읽지 못했어요: 척추 위에서 목/팔이 갈라지는 곳을 찾지 못했어요.")
    m[3] = S[0]
    m[9] = S[-1] if len(S) >= 2 else None
    m[6] = S[len(S) // 2] if len(S) >= 3 else None
    if len(S) == 1:
        m[9] = None
        notes.append("척추 뼈가 1개뿐이라 가슴 쪽은 그대로 뒀어요.")

    # 6) 가슴에서 갈라지는 가지: 목/머리, 왼팔, 오른팔
    kids = ch[top]
    def up_score(c):
        v = Prest[c] - Prest[top]
        return float(np.dot(v, up) / (np.linalg.norm(v) + 1e-9))
    neck0 = max(kids, key=up_score)
    arms = [c for c in kids if c != neck0]
    if len(arms) < 2:
        raise BvhError("사람 골격으로 읽지 못했어요: 팔 두 개를 찾지 못했어요.")
    arms = sorted(arms, key=lambda c: -abs(float(np.dot(Prest[_subtree(ch, c)].mean(axis=0) - Prest[top], left))))[:2]
    arm_l = max(arms, key=lambda c: float(np.dot(Prest[_subtree(ch, c)].mean(axis=0) - Prest[top], left)))
    arm_r = next(c for c in arms if c != arm_l)

    N = _chain(ch, names, neck0, 3)
    if len(N) >= 2:
        m[12], m[15] = N[0], N[-1]
    else:
        m[15] = N[0]
        notes.append("목 뼈가 따로 없어서 머리만 연결했어요.")
    for side, a in ((0, arm_l), (1, arm_r)):
        A = _chain(ch, names, a, 5)
        if len(A) >= 4:
            m[13 + side], m[16 + side], m[18 + side], m[20 + side] = A[0], A[1], A[2], A[3]
            if len(A) >= 5:
                m[22 + side] = A[4]
        elif len(A) == 3:
            m[16 + side], m[18 + side], m[20 + side] = A[0], A[1], A[2]          # 어깨(쇄골) 뼈가 없는 구조
            notes.append("쇄골 뼈가 없는 구조예요.")
        else:
            raise BvhError("사람 골격으로 읽지 못했어요: 팔 뼈가 3개(어깨-팔꿈치-손목) 미만이에요.")
    return m, B, notes


# ---------------------------------------------------------------- 변환
def _fill_rest(rest, m, forward_unit, leg_len):
    """BVH 에 없는 SMPL 관절의 기본 자세 위치를 부모/자식 사이에 적당히 채운다 (회전은 부모와 같게 둔다)."""
    r = rest.copy()
    have = [x is not None for x in m]
    if not have[10]:
        r[10] = r[7] + forward_unit * 0.12 * leg_len
    if not have[11]:
        r[11] = r[8] + forward_unit * 0.12 * leg_len
    if not have[6]:
        r[6] = (r[3] + r[9]) / 2 if have[9] else r[3] + (r[12] - r[3]) * 0.4
    if not have[9]:
        r[9] = r[6] + (r[12] - r[6]) * 0.5
    if not have[12]:
        r[12] = r[9] + (r[15] - r[9]) * 0.5
    for s in (0, 1):
        if not have[13 + s]:
            r[13 + s] = r[9] + (r[16 + s] - r[9]) * 0.4
        if not have[22 + s]:
            r[22 + s] = r[20 + s] + (r[20 + s] - r[18 + s]) * 0.35
    return r


def convert_bvh(bvh_text, in_place=False, keep_heading=False, log=print):
    """BVH 글자 -> {"R_local","root_pos","rest","frames","notes","fps","mapping"} (wham2motion.process 결과와 같은 모양)."""
    b = parse_bvh(bvh_text)
    G, P, Prest = forward_kinematics(b)
    m, B, notes = find_humanoid(b, Prest)
    n = G.shape[0]
    fps = 1.0 / b["frame_time"]

    # 단위: 다리 길이가 20 넘으면 cm 로 본다
    leg = float(np.linalg.norm(Prest[m[4]] - Prest[m[1]]) + np.linalg.norm(Prest[m[7]] - Prest[m[4]]))
    scale = 0.01 if leg > 20 else 1.0
    if scale != 1.0:
        notes.append("길이 단위를 cm 로 보고 m 로 바꿨어요.")

    Rb = np.einsum("ik,njkl,ml->njim", B, G, B)                      # B G B^T
    rest_all = (Prest @ B.T) * scale
    pel_pos = (P[:, m[0]] @ B.T) * scale

    leg_len = leg * scale
    fwd_unit = np.array([0.0, 0.0, 1.0])
    rest = np.zeros((NJ, 3))
    for j in range(NJ):
        if m[j] is not None:
            rest[j] = rest_all[m[j]]
    rest = _fill_rest(rest, m, fwd_unit, leg_len)
    rest = rest - rest[0]

    # 전역 회전 -> SMPL 부모 기준 지역 회전. 없는 관절은 부모와 같은 회전(= 지역 회전 단위행렬)
    Gs = np.zeros((n, NJ, 3, 3))
    R_local = np.zeros((n, NJ, 3, 3))
    for j in range(NJ):
        p = PARENTS[j]
        Gs[:, j] = Rb[:, m[j]] if m[j] is not None else Gs[:, p]
        R_local[:, j] = Gs[:, j] if p < 0 else np.einsum("nji,njk->nik", Gs[:, p], Gs[:, j])
    missing = [JOINT_NAMES[j] for j in range(NJ) if m[j] is None]
    if missing:
        notes.append("이 모션 파일에 없는 관절(그대로 둠): " + ", ".join(missing))

    # 첫 프레임에서 바라보는 방향을 +z 로 (WHAM 쪽과 같은 규칙)
    root = pel_pos.copy()
    if not keep_heading:
        fwd = Gs[0, 0] @ np.array([0.0, 0.0, 1.0])
        a = -math.atan2(fwd[0], fwd[2])
        c, s = math.cos(a), math.sin(a)
        T = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
        R_local[:, 0] = T @ R_local[:, 0]
        root = root @ T.T
    root[:, [0, 2]] -= root[0, [0, 2]]
    if in_place:
        root[:, [0, 2]] = 0.0
        notes.append("제자리 모드: 앞뒤·좌우 이동을 뺐어요.")
    named = {JOINT_NAMES[j]: b["names"][m[j]] for j in range(NJ) if m[j] is not None}
    return {"R_local": R_local, "root_pos": root, "rest": rest, "frames": np.arange(n), "notes": notes,
            "fps": fps, "mapping": named, "betas": np.zeros(10), "flipped": False}
