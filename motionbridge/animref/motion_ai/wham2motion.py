#!/usr/bin/env python3
"""WHAM 결과(wham_output.pkl) -> BVH + AnimRef 모션 파일(.motion.json / .motion.txt).

WSL 안의 WHAM 환경(numpy, joblib 있음)에서 실행된다. numpy 외에는 SMPL 모델 파일을 읽을 때만 chumpy가 필요하다.

WHAM 결과(공식 demo.py 의 results[id]):
  pose   (N,72) : 몸 24관절의 axis-angle. 0~2번이 루트(골반) 회전, 카메라 기준 (OpenCV: y 아래, z 앞)
  trans  (N,3)  : 카메라 기준 이동
  betas  (N,10) : 체형
  verts  (N,6890,3) : 카메라 기준 메시 (바닥 높이를 잡는 데만 사용)
  frame_ids (N,) : 영상 프레임 번호 (사람을 놓친 프레임은 빠져 있을 수 있음)
  pose_world / trans_world : DPVO(카메라 추적)가 있을 때의 세계 기준 값

만드는 것:
  <name>.bvh          어디서나 읽히는 모션 (SMPL 뼈대, y 위 / +z 앞 / +x 왼쪽, 단위 m, 채널 Zrot Xrot Yrot)
  <name>.motion.json  Blender 적용 스크립트가 읽는 파일 (로컬 회전 쿼터니언)
  <name>.motion.txt   3ds Max 스크립트가 읽는 파일 (관절별 세계 회전 행렬)
"""
import argparse
import json
import math
import os
import pickle
import sys

import numpy as np

JOINT_NAMES = ["Pelvis", "L_Hip", "R_Hip", "Spine1", "L_Knee", "R_Knee", "Spine2", "L_Ankle", "R_Ankle",
               "Spine3", "L_Foot", "R_Foot", "Neck", "L_Collar", "R_Collar", "Head", "L_Shoulder",
               "R_Shoulder", "L_Elbow", "R_Elbow", "L_Wrist", "R_Wrist", "L_Hand", "R_Hand"]
PARENTS = [-1, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9, 12, 13, 14, 16, 17, 18, 19, 20, 21]
NJ = 24


# ---------------------------------------------------------------- 기본 자세(뼈 길이)
def _fallback_offsets():
    """SMPL 모델 파일을 못 읽을 때 쓰는 대략적인 성인 체형 (부모 기준 오프셋, m). 리타겟은 회전만 쓰므로 치명적이진 않다."""
    o = {
        0: (0, 0, 0), 1: (0.07, -0.09, -0.02), 2: (-0.07, -0.09, -0.02), 3: (0, 0.11, -0.02),
        4: (0.0, -0.38, 0.0), 5: (0.0, -0.38, 0.0), 6: (0, 0.13, 0.0), 7: (0.0, -0.40, -0.03),
        8: (0.0, -0.40, -0.03), 9: (0, 0.05, 0.03), 10: (0.03, -0.05, 0.12), 11: (-0.03, -0.05, 0.12),
        12: (0, 0.21, -0.04), 13: (0.08, 0.10, -0.02), 14: (-0.08, 0.10, -0.02), 15: (0, 0.10, 0.03),
        16: (0.09, 0.03, -0.01), 17: (-0.09, 0.03, -0.01), 18: (0.26, 0, 0), 19: (-0.26, 0, 0),
        20: (0.26, 0, 0), 21: (-0.26, 0, 0), 22: (0.08, 0, 0), 23: (-0.08, 0, 0)}
    return np.array([o[i] for i in range(NJ)], float)


def fallback_rest():
    """골반이 모델 원점 기준 (0,-0.22,0.03)에 있는 SMPL 기본 체형과 비슷한 절대 좌표."""
    off = _fallback_offsets()
    rest = np.zeros((NJ, 3))
    rest[0] = (0.0, -0.22, 0.03)
    for j in range(1, NJ):
        rest[j] = rest[PARENTS[j]] + off[j]
    return rest


def load_smpl_rest(model_path, betas):
    """SMPL 모델에서 체형(betas)에 맞는 24관절 기본 자세 위치 (pelvis 기준 아님, 모델 좌표 그대로)."""
    with open(model_path, "rb") as f:
        d = pickle.load(f, encoding="latin1")
    vt = np.array(d["v_template"], dtype=np.float64)
    sd = np.array(d["shapedirs"], dtype=np.float64)
    jr = d["J_regressor"]
    jr = jr.toarray() if hasattr(jr, "toarray") else np.array(jr)
    b = np.asarray(betas, float).reshape(-1)
    nb = min(len(b), sd.shape[-1])
    v = vt + sd[:, :, :nb] @ b[:nb]
    j = jr @ v
    if j.shape != (NJ, 3) or not np.all(np.isfinite(j)):
        raise ValueError("SMPL 관절 모양이 이상해요")
    return j


# ---------------------------------------------------------------- 회전 도구 (numpy만 사용)
def aa_to_mat(aa):
    aa = np.asarray(aa, float)
    th = np.linalg.norm(aa, axis=-1, keepdims=True)
    k = aa / np.where(th < 1e-9, 1.0, th)
    x, y, z = k[..., 0], k[..., 1], k[..., 2]
    c, s = np.cos(th[..., 0]), np.sin(th[..., 0])
    C = 1.0 - c
    R = np.stack([c + x * x * C, x * y * C - z * s, x * z * C + y * s,
                  y * x * C + z * s, c + y * y * C, y * z * C - x * s,
                  z * x * C - y * s, z * y * C + x * s, c + z * z * C], axis=-1)
    return R.reshape(aa.shape[:-1] + (3, 3))


def mat_to_quat(R):
    """(...,3,3) -> (...,4) (w,x,y,z). w>=0."""
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


def quat_to_mat(q):
    q = np.asarray(q, float)
    q = q / np.linalg.norm(q, axis=-1, keepdims=True)
    w, x, y, z = q[..., 0], q[..., 1], q[..., 2], q[..., 3]
    R = np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w),
                  2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w),
                  2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)], axis=-1)
    return R.reshape(q.shape[:-1] + (3, 3))


def slerp(q0, q1, t):
    """q0,q1: (...,4) / t: (...) 또는 스칼라. 짧은 쪽으로 보간."""
    q0 = np.asarray(q0, float)
    q1 = np.asarray(q1, float)
    d = np.sum(q0 * q1, axis=-1, keepdims=True)
    q1 = np.where(d < 0, -q1, q1)
    d = np.abs(d)
    t = np.asarray(t, float)[..., None]
    th = np.arccos(np.clip(d, -1, 1))
    sn = np.sin(th)
    near = sn < 1e-6
    a = np.where(near, 1 - t, np.sin((1 - t) * th) / np.where(near, 1, sn))
    b = np.where(near, t, np.sin(t * th) / np.where(near, 1, sn))
    q = a * q0 + b * q1
    return q / np.linalg.norm(q, axis=-1, keepdims=True)


def rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def rot_z(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def mat_to_euler_zxy(R):
    """R = Rz(z) @ Rx(x) @ Ry(y)  (BVH 채널 순서 Zrotation Xrotation Yrotation). (...,3,3) -> (...,3) = (z,x,y) 라디안."""
    R = np.asarray(R, float)
    sx = np.clip(R[..., 2, 1], -1, 1)
    x = np.arcsin(sx)
    gimbal = np.abs(sx) > 0.999999
    z = np.where(gimbal, 0.0, np.arctan2(-R[..., 0, 1], R[..., 1, 1]))
    y = np.where(gimbal, np.arctan2(R[..., 0, 2], R[..., 0, 0]), np.arctan2(-R[..., 2, 0], R[..., 2, 2]))
    return np.stack([z, x, y], axis=-1)


def euler_zxy_to_mat(z, x, y):
    return rot_z(z) @ rot_x(x) @ rot_y(y)


# ---------------------------------------------------------------- 보정/가공
def resample_frames(frame_ids, quats, trans, extra=None):
    """빠진 프레임(사람을 놓친 구간)을 보간해서 연속 프레임으로 만든다.
    quats (N,J,4), trans (N,3). 반환: 연속 frame 번호, quats, trans, 보간으로 채운 프레임 수."""
    fid = np.asarray(frame_ids).astype(int).reshape(-1)
    order = np.argsort(fid)
    fid, quats, trans = fid[order], quats[order], trans[order]
    keep = np.concatenate([[True], np.diff(fid) > 0])      # 같은 프레임이 두 번 있으면 앞의 것만
    fid, quats, trans = fid[keep], quats[keep], trans[keep]
    full = np.arange(fid[0], fid[-1] + 1)
    if len(full) == len(fid):
        return full, quats, trans, 0
    idx = np.searchsorted(fid, full, side="left")
    hi = np.clip(idx, 0, len(fid) - 1)
    lo = np.clip(idx - 1, 0, len(fid) - 1)
    exact = fid[hi] == full
    span = np.where(fid[hi] == fid[lo], 1, fid[hi] - fid[lo])
    t = np.where(exact, 1.0, (full - fid[lo]) / span)
    q = slerp(quats[lo], quats[hi], t[:, None] * np.ones((1, quats.shape[1])))
    tr = trans[lo] * (1 - t[:, None]) + trans[hi] * t[:, None]
    return full, q, tr, int((~exact).sum())


def smooth_quats(q, sigma):
    """시간 방향 가우시안 평균으로 떨림을 줄인다 (q: (N,J,4))."""
    if sigma <= 0 or len(q) < 3:
        return q
    q = q.copy()
    for i in range(1, len(q)):                      # 부호를 앞 프레임에 맞춤
        flip = np.sum(q[i] * q[i - 1], axis=-1, keepdims=True) < 0
        q[i] = np.where(flip, -q[i], q[i])
    r = max(1, int(math.ceil(sigma * 3)))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    pad = np.concatenate([np.repeat(q[:1], r, 0), q, np.repeat(q[-1:], r, 0)], 0)
    out = np.zeros_like(q)
    for i, w in enumerate(k):
        out += w * pad[i:i + len(q)]
    return out / np.linalg.norm(out, axis=-1, keepdims=True)


def smooth_vec(v, sigma):
    if sigma <= 0 or len(v) < 3:
        return v
    r = max(1, int(math.ceil(sigma * 3)))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    pad = np.concatenate([np.repeat(v[:1], r, 0), v, np.repeat(v[-1:], r, 0)], 0)
    out = np.zeros_like(v)
    for i, w in enumerate(k):
        out += w * pad[i:i + len(v)]
    return out


def fk_world_rotations(R_local, parents=PARENTS):
    """R_local (N,J,3,3) -> 세계 회전 (N,J,3,3)."""
    W = np.empty_like(R_local)
    for j in range(R_local.shape[1]):
        p = parents[j]
        W[:, j] = R_local[:, j] if p < 0 else W[:, p] @ R_local[:, j]
    return W


def fk_positions(rest, R_local, root_pos, parents=PARENTS):
    """관절 위치 (N,J,3). rest: (J,3) 기본 자세 위치. root_pos: (N,3) = 골반의 세계 위치."""
    W = fk_world_rotations(R_local, parents)
    pos = np.zeros((R_local.shape[0], R_local.shape[1], 3))
    for j in range(R_local.shape[1]):
        p = parents[j]
        if p < 0:
            pos[:, j] = root_pos
        else:
            pos[:, j] = pos[:, p] + np.einsum("nij,j->ni", W[:, p], rest[j] - rest[p])
    return pos


def _box_at(boxes, t, tol=0.35):
    """boxes: 시간순 [[t,x1,y1,x2,y2],...]. 시각 t 의 상자(보간). 범위 밖(tol초 초과)이면 None."""
    if not boxes:
        return None
    ts = [b[0] for b in boxes]
    if t < ts[0] - tol or t > ts[-1] + tol:
        return None
    if t <= ts[0]:
        return boxes[0][1:]
    if t >= ts[-1]:
        return boxes[-1][1:]
    lo, hi = 0, len(ts) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if ts[mid] <= t:
            lo = mid
        else:
            hi = mid
    a, b = boxes[lo], boxes[hi]
    w = (t - a[0]) / max(b[0] - a[0], 1e-9)
    if b[0] - a[0] > 1.0:                        # 중간이 너무 비면 가까운 쪽 값
        return (a if w < 0.5 else b)[1:]
    return [a[i] * (1 - w) + b[i] * w for i in range(1, 5)]


def pick_by_boxes(results, boxes, fps, size, max_dist=1.5, log=print):
    """AnimRef 가 찾아 둔 '대상 캐릭터' 상자와 가장 잘 겹치는 WHAM 사람을 고른다.
    WHAM 의 카메라 좌표 몸 중심을 영상에 투영(초점거리 = 대각선 길이 가정)해서, 상자 중심과의 거리(상자 대각선 기준)가 가장 작은 사람.
    반환: (사람 번호 또는 None, {번호: (거리 중앙값, 비교한 프레임 수)})"""
    W, H = size
    f = math.hypot(W, H)
    scores = {}
    for k, sub in results.items():
        fid = np.asarray(sub["frame_ids"]).reshape(-1)
        if "verts" in sub:
            v = np.asarray(sub["verts"], float)
            pts = v.reshape(len(fid), -1, 3).mean(axis=1)
        else:
            pts = np.asarray(sub["trans"], float).reshape(len(fid), -1)[:, :3]
        ds = []
        for fi, p in zip(fid, pts):
            if not np.all(np.isfinite(p)) or p[2] < 0.1:
                continue
            tb = _box_at(boxes, float(fi) / fps)
            if tb is None:
                continue
            cx, cy = (tb[0] + tb[2]) / 2.0, (tb[1] + tb[3]) / 2.0
            diag = math.hypot(tb[2] - tb[0], tb[3] - tb[1])
            u, vv = f * p[0] / p[2] + W / 2.0, f * p[1] / p[2] + H / 2.0
            ds.append(math.hypot(u - cx, vv - cy) / max(diag, 1.0))
        if len(ds) >= 3:
            scores[k] = (float(np.median(ds)), len(ds))
    if not scores:
        return None, scores
    best = min(scores, key=lambda k: scores[k][0])
    if scores[best][0] > max_dist:
        return None, scores
    return best, scores


def pick_subject(results, subject=None):
    keys = list(results.keys())
    if not keys:
        raise ValueError("WHAM 결과에 사람이 한 명도 없어요. 사람이 크게 나오는 클립인지 확인해 주세요.")
    if subject is not None:
        for k in keys:
            if str(k) == str(subject):
                return k
        raise ValueError(f"사람 번호 {subject}이(가) 없어요. 있는 번호: {keys}")
    return max(keys, key=lambda k: len(np.asarray(results[k]["frame_ids"]).reshape(-1)))


def process(sub, fps, smpl_path=None, in_place=False, keep_heading=False, smooth=0.5, use_world=False, log=print):
    """WHAM 사람 한 명의 결과 -> 연속 프레임의 SMPL 모션.
    반환 dict: R_local (N,24,3,3), root_pos (N,3), rest (24,3; 골반이 원점), frames(영상 프레임 번호), notes"""
    notes = []
    fid = np.asarray(sub["frame_ids"]).reshape(-1)
    n = len(fid)
    pose_key, trans_key = ("pose_world", "trans_world") if (use_world and "pose_world" in sub) else ("pose", "trans")
    pose = np.asarray(sub[pose_key], float).reshape(n, -1)[:, :72]
    trans = np.asarray(sub[trans_key], float).reshape(n, -1)[:, :3]
    betas = (np.asarray(sub["betas"], float).reshape(-1, 10).mean(0) if "betas" in sub else np.zeros(10))
    if not (np.all(np.isfinite(pose)) and np.all(np.isfinite(trans))):
        raise ValueError("WHAM 결과에 계산 오류(NaN)가 있어요.")

    R = aa_to_mat(pose.reshape(n, NJ, 3))

    # 1) 좌표 보정 행렬 T: 카메라 기준(y 아래)이면 y 위로 뒤집고, 첫 프레임에서 바라보는 방향을 +z(앞)로 맞춘다.
    #    '서 있는 사람의 루트 회전의 위쪽(y축)'이 -y 를 향하면 카메라 좌표로 판단한다.
    up = R[:, 0, :, 1]
    flipped = bool(up[:, 1].mean() < 0)
    T = np.diag([1.0, -1.0, -1.0]) if flipped else np.eye(3)
    if flipped:
        notes.append("카메라 좌표(y 아래)를 y 위 좌표로 바꿨어요.")
    if not keep_heading:
        fwd = (T @ R[0, 0]) @ np.array([0.0, 0.0, 1.0])
        T = rot_y(-math.atan2(fwd[0], fwd[2])) @ T
    R[:, 0] = T @ R[:, 0]
    trans = trans @ T.T

    verts_min_y = None
    if "verts" in sub:
        v = np.asarray(sub["verts"])
        if v.ndim == 3 and v.shape[0] == n:
            verts_min_y = (v @ T.T)[:, :, 1].min(axis=1)

    # 2) 기본 자세(체형). SMPL은 골반 관절이 제자리에서 돌기 때문에 골반의 실제 위치 = (모델 기준 골반 위치 + trans)
    rest = None
    if smpl_path and os.path.exists(smpl_path):
        try:
            rest = load_smpl_rest(smpl_path, betas)
        except Exception as e:                                      # chumpy 없음 등
            notes.append(f"SMPL 모델을 읽지 못해 대략적인 체형을 썼어요 ({type(e).__name__}).")
    else:
        notes.append("SMPL 모델 파일이 없어 대략적인 체형을 썼어요.")
    if rest is None:
        rest = fallback_rest()
    pelvis_off = T @ rest[0]                                        # trans 와 같은 좌표계로 옮긴 골반 기본 위치
    rest = rest - rest[0]                                           # 골반이 원점

    # 3) 빠진 프레임 보간 + 떨림 줄이기
    q = mat_to_quat(R)
    frames, q, trans, filled = resample_frames(fid, q, trans)
    if verts_min_y is not None:
        order = np.argsort(fid)
        verts_min_y = np.interp(frames, fid[order], verts_min_y[order])
    if filled:
        notes.append(f"사람을 놓친 {filled}프레임은 앞뒤 동작으로 채웠어요.")
    q = smooth_quats(q, smooth)
    trans = smooth_vec(trans, smooth)
    R = quat_to_mat(q)

    # 4) 골반의 세계 위치와 바닥 높이
    pelvis_world = trans + pelvis_off
    if verts_min_y is not None:
        ground = float(np.percentile(verts_min_y, 5))               # 가장 낮은 점(공중에 뜬 프레임은 제외되도록 하위 5%)
    else:
        ground = float(pelvis_world[0, 1] + rest[[7, 8, 10, 11], 1].min() - 0.03)
        notes.append("메시 정보가 없어 첫 프레임에 서 있다고 보고 바닥을 정했어요.")
    root = pelvis_world.copy()
    root[:, 1] -= ground
    root[:, [0, 2]] -= root[0, [0, 2]]
    if in_place:
        root[:, [0, 2]] = 0.0
        notes.append("제자리 모드: 앞뒤·좌우 이동을 뺐어요.")
    return {"R_local": R, "root_pos": root, "rest": rest, "frames": frames, "notes": notes, "betas": betas,
            "flipped": flipped}


# ---------------------------------------------------------------- 파일 쓰기
def _children(parents):
    ch = [[] for _ in parents]
    for j, p in enumerate(parents):
        if p >= 0:
            ch[p].append(j)
    return ch


def write_bvh(path, rest, R_local, root_pos, fps, names=JOINT_NAMES, parents=PARENTS):
    n = R_local.shape[0]
    eul = mat_to_euler_zxy(R_local)                                   # (N,J,3) z,x,y
    eul = np.unwrap(eul, axis=0)
    deg = np.degrees(eul)
    ch = _children(parents)
    order = []                                                         # 깊이 우선 순서

    def dfs(j):
        order.append(j)
        for c in ch[j]:
            dfs(c)
    dfs(0)

    lines = ["HIERARCHY"]

    def emit(j, depth):
        pad = "  " * depth
        off = np.zeros(3) if parents[j] < 0 else rest[j] - rest[parents[j]]
        lines.append(f"{pad}{'ROOT' if parents[j] < 0 else 'JOINT'} {names[j]}")
        lines.append(f"{pad}{{")
        lines.append(f"{pad}  OFFSET {off[0]:.5f} {off[1]:.5f} {off[2]:.5f}")
        if parents[j] < 0:
            lines.append(f"{pad}  CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation")
        else:
            lines.append(f"{pad}  CHANNELS 3 Zrotation Xrotation Yrotation")
        for c in ch[j]:
            emit(c, depth + 1)
        if not ch[j]:
            d = off / (np.linalg.norm(off) + 1e-9) * 0.08
            lines.append(f"{pad}  End Site")
            lines.append(f"{pad}  {{")
            lines.append(f"{pad}    OFFSET {d[0]:.5f} {d[1]:.5f} {d[2]:.5f}")
            lines.append(f"{pad}  }}")
        lines.append(f"{pad}}}")
    emit(0, 0)
    lines += ["MOTION", f"Frames: {n}", f"Frame Time: {1.0 / fps:.7f}"]
    for i in range(n):
        vals = [*root_pos[i]]
        for j in order:
            vals += list(deg[i, j])
        lines.append(" ".join(f"{v:.5f}" for v in vals))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return order


def write_json(path, motion, fps, markers=None, source=None):
    q = mat_to_quat(motion["R_local"])
    data = {
        "format": "animref-motion", "version": 1, "fps": float(fps), "frames": int(q.shape[0]),
        "axes": "SMPL: y-up, +z forward, +x = character's left; meters",
        "joint_names": JOINT_NAMES, "parents": PARENTS,
        "rest_joints": np.round(motion["rest"], 5).tolist(),
        "root_pos": np.round(motion["root_pos"], 5).tolist(),
        "rot": np.round(q, 6).tolist(),
        "markers": [round(float(m), 3) for m in (markers or [])],
        "source": source or {},
        "notes": motion.get("notes", []),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    return data


def write_txt(path, motion, fps, markers=None):
    """3ds Max(MAXScript)가 쉽게 읽도록 줄 단위 글자 파일로: 관절별 '세계' 회전 행렬(3x3, 행 우선)."""
    W = fk_world_rotations(motion["R_local"])
    n = W.shape[0]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("ANIMREF_MOTION 1\n")
        f.write(f"fps {fps:.4f}\nframes {n}\njoints {NJ}\n")
        f.write("names " + " ".join(JOINT_NAMES) + "\n")
        f.write("parents " + " ".join(str(p) for p in PARENTS) + "\n")
        f.write("rest " + " ".join(f"{v:.5f}" for v in motion["rest"].reshape(-1)) + "\n")
        f.write("markers " + " ".join(f"{m:.3f}" for m in (markers or [])) + "\n")
        for i in range(n):
            rp = motion["root_pos"][i]
            vals = [*rp, *W[i].reshape(-1)]
            f.write(f"F {i} " + " ".join(f"{v:.5f}" for v in vals) + "\n")


# ---------------------------------------------------------------- 실행
def convert(pkl_path, out_dir, name, fps=30.0, subject=None, smpl_path=None, in_place=False,
            keep_heading=False, smooth=0.5, markers=None, source=None, use_world=False, log=print,
            target_boxes=None, video_size=None):
    import joblib
    results = joblib.load(pkl_path)
    how = "longest"
    extra_notes = []
    sid = None
    if subject is None and target_boxes and video_size:
        sid, sc = pick_by_boxes(results, target_boxes, fps, video_size, log=log)
        if sid is not None:
            how = "target"
            log(f"대상 캐릭터와 겹치는 사람: {sid}번 (거리 {sc[sid][0]:.2f}, 프레임 {sc[sid][1]}개)")
            if len(sc) > 1:
                extra_notes.append("여러 명 중 등록한 대상 캐릭터와 위치가 가장 잘 맞는 사람을 골랐어요.")
        else:
            how = "longest-fallback"
            msg = ("대상 캐릭터와 위치가 맞는 사람을 WHAM 결과에서 못 찾았어요. 가장 오래 나온 사람을 대신 썼어요."
                   " (WHAM이 대상을 놓쳤을 수 있어요: '대상만 크게 보기'를 켜 보세요)")
            log(msg)
            extra_notes.append(msg)
    if sid is None:
        sid = pick_subject(results, subject)
        if len(results) > 1:
            log(f"사람이 {len(results)}명 찾아졌어요. 가장 오래 나온 사람({sid}번)을 사용합니다. (번호: {list(results.keys())})")
    motion = process(results[sid], fps, smpl_path, in_place, keep_heading, smooth, use_world, log)
    motion["notes"] = list(motion["notes"]) + extra_notes
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.join(out_dir, name)
    write_bvh(base + ".bvh", motion["rest"], motion["R_local"], motion["root_pos"], fps)
    src = dict(source or {})
    src.update(subject=str(sid), subject_method=how, in_place=bool(in_place), first_frame=int(motion["frames"][0]))
    write_json(base + ".motion.json", motion, fps, markers, src)
    write_txt(base + ".motion.txt", motion, fps, markers)
    return {"bvh": base + ".bvh", "json": base + ".motion.json", "txt": base + ".motion.txt",
            "frames": int(len(motion["frames"])), "fps": fps, "notes": motion["notes"], "subject": str(sid),
            "subject_method": how}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pkl")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--subject")
    ap.add_argument("--smpl", help="SMPL_NEUTRAL.pkl 경로 (체형을 맞추는 데 사용)")
    ap.add_argument("--in-place", action="store_true")
    ap.add_argument("--keep-heading", action="store_true")
    ap.add_argument("--smooth", type=float, default=0.5)
    ap.add_argument("--markers", default="", help="쉼표로 구분한 타격 시각(초)")
    ap.add_argument("--use-world", action="store_true", help="pose_world(DPVO 결과)를 사용")
    ap.add_argument("--target-boxes", help="대상 캐릭터 상자 JSON ([[t,x1,y1,x2,y2],...] 또는 {\"boxes\": ...})")
    ap.add_argument("--video-size", help="분석한 영상의 크기 WxH (--target-boxes 와 함께)")
    a = ap.parse_args(argv)
    tb, vs = None, None
    if a.target_boxes:
        data = json.load(open(a.target_boxes, encoding="utf-8"))
        tb = data["boxes"] if isinstance(data, dict) else data
        vs = tuple(int(x) for x in a.video_size.lower().split("x")) if a.video_size else None
    mk = [float(x) for x in a.markers.split(",") if x.strip()]
    r = convert(a.pkl, a.out_dir, a.name, a.fps, a.subject, a.smpl, a.in_place, a.keep_heading, a.smooth, mk,
                use_world=a.use_world, target_boxes=tb, video_size=vs)
    print("ANIMREF_RESULT " + json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:                       # 사람이 읽을 수 있는 한 줄 + 코드
        print("ANIMREF_ERROR " + f"{type(e).__name__}: {e}")
        raise SystemExit(1)
