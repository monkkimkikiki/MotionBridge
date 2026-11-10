"""리타깃 결과에 거는 모션빌더식 후처리: 부위별 모션 세기(Reach 대용), 튀는 프레임 제거(Peak Removal), 부드럽게(Butterworth 대용),
좌우 반전(Mirror Animation). 입력은 타깃 모델의 로컬 회전/위치 Animation 이고 Qt 를 쓰지 않는다.

적용 순서: 부위 세기 -> 튀는 프레임 -> 부드럽게 -> 좌우 반전. (보정 레이어는 이 뒤에 얹는다: core/layers)
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from . import quat as Q
from .model import Animation, Skeleton, fk, world_to_local

# 부위 이름 -> (화면 이름, 슬롯 이름 목록, 하위 본까지 포함할지)
PARTS: Dict[str, Tuple[str, List[str], bool]] = {
    "spine": ("몸통(척추)", ["Spine", "Spine1", "Spine2"], False),
    "head": ("목·머리", ["Neck", "Head"], True),
    "arm_L": ("왼팔(손가락 포함)", ["L_Shoulder", "L_UpperArm", "L_LowerArm", "L_Hand"], True),
    "arm_R": ("오른팔(손가락 포함)", ["R_Shoulder", "R_UpperArm", "R_LowerArm", "R_Hand"], True),
    "leg_L": ("왼다리", ["L_UpLeg", "L_LowLeg", "L_Foot", "L_Toe"], True),
    "leg_R": ("오른다리", ["R_UpLeg", "R_LowLeg", "R_Foot", "R_Toe"], True),
}
PART_ORDER = ["spine", "head", "arm_L", "arm_R", "leg_L", "leg_R"]


def part_joints(skel: Skeleton, tgt_slots: Dict[str, int]) -> Dict[str, List[int]]:
    """부위 -> 타깃 관절 번호들 (매핑된 슬롯 기준; 팔/다리/머리는 그 아래 본(손가락 등)까지)."""
    out: Dict[str, List[int]] = {}
    for key, (_label, slots, deep) in PARTS.items():
        s = set()
        for sl in slots:
            j = tgt_slots.get(sl)
            if j is None:
                continue
            s.add(int(j))
            if deep:
                s.update(skel.descendants(int(j)))
        out[key] = sorted(s)
    return out


def scale_parts(anim: Animation, skel: Skeleton, weights: Dict[str, float], tgt_slots: Dict[str, int]) -> Animation:
    """부위별 모션 세기: 1 = 그대로, 0 = 기준 자세 고정, 1 초과 = 과장. (기준 자세에서 벗어난 만큼에 곱한다)"""
    pj = part_joints(skel, tgt_slots)
    out = anim.copy()
    T = out.rot.shape[0]
    for key, w in weights.items():
        w = float(np.clip(w, 0.0, 1.5))
        if abs(w - 1.0) < 1e-6 or key not in pj:
            continue
        for j in pj[key]:
            rest = np.repeat(skel.rest_rot[j][None], T, axis=0)
            out.rot[:, j] = Q.slerp(rest, Q.make_continuous(out.rot[:, j]), w)
    return out


def remove_spikes(anim: Animation, thresh_deg: float, passes: int = 2, max_len: int = 6) -> Tuple[Animation, int]:
    """튀는 구간 제거. 앞뒤의 정상 프레임(앵커)은 서로 비슷한데 그 사이 1~max_len 프레임이 앵커를 잇는 선(구면 보간)에서 thresh_deg 이상 벗어나 있으면
    그 구간을 앵커 사이 보간 값으로 바꾼다 (진짜 빠른 움직임은 앵커끼리도 많이 달라서 건드리지 않는다). (애니메이션, 고친 키 수)
    한 프레임짜리 모캡 튐뿐 아니라 IK 가 원래 동작을 억지로 따라가며 몇 프레임 동안 이상한 각도로 꺾이는 것도 잡는다."""
    out = anim.copy()
    if thresh_deg <= 0 or out.n_frames < 3:
        return out, 0
    th = np.radians(thresh_deg)
    max_len = max(1, int(max_len))
    fixed = 0
    for _ in range(passes):
        r = Q.make_continuous(Q.normalize(out.rot))
        T, J = r.shape[0], r.shape[1]
        done = np.zeros((T, J), bool)
        n_pass = 0
        for L in range(1, min(max_len, T - 2) + 1):
            S = T - L - 1                                   # 시작 프레임 s = 1 .. T-L-1
            if S < 1:
                break
            ra, rb = r[0:S], r[L + 1:L + 1 + S]             # 앵커: s-1, s+L  (인덱스 s-1 = 0..S-1)
            dev = np.zeros((S, J))
            for k in range(L):
                mid = Q.slerp(ra, rb, (k + 1.0) / (L + 1.0))
                dev = np.maximum(dev, Q.angle_between(r[1 + k:1 + k + S], mid))
            side = Q.angle_between(ra, rb)
            bad = (dev > th) & (side < 0.6 * dev)
            for si, j in zip(*np.nonzero(bad)):
                s0 = int(si) + 1
                if done[s0 - 1:s0 + L + 1, j].any():        # 이미 고친 구간과 겹치면 건너뜀 (앵커도 고친 프레임이면 믿을 수 없다)
                    continue
                u = ((np.arange(L) + 1.0) / (L + 1.0))
                r[s0:s0 + L, j] = Q.slerp(np.repeat(r[s0 - 1, j][None], L, axis=0), np.repeat(r[s0 + L, j][None], L, axis=0), u)
                done[s0:s0 + L, j] = True
                n_pass += L
        out.rot = r
        fixed += n_pass
        if n_pass == 0:
            break
    return out, fixed


def smooth(anim: Animation, sigma: float) -> Animation:
    """시간 방향 가우시안 필터 (sigma = 프레임 수). 회전은 연속으로 맞춘 뒤 섞고 다시 정규화한다."""
    out = anim.copy()
    if sigma <= 0.05 or out.n_frames < 3:
        return out
    rad = max(1, int(np.ceil(3 * sigma)))
    k = np.exp(-0.5 * (np.arange(-rad, rad + 1) / sigma) ** 2)
    k /= k.sum()
    T = out.n_frames

    def conv(a):
        pad = np.concatenate([np.repeat(a[:1], rad, axis=0), a, np.repeat(a[-1:], rad, axis=0)], axis=0)
        acc = np.zeros_like(a)
        for i, w in enumerate(k):
            acc += w * pad[i:i + T]
        return acc

    out.rot = Q.normalize(conv(Q.make_continuous(Q.normalize(out.rot))))
    out.pos = conv(out.pos)
    return out


# ---------------------------------------------------------------------------- 좌우 반전
def _mirror_q(q: np.ndarray) -> np.ndarray:
    """X 축 수직 평면(x -> -x)으로 거울 반사한 회전 쿼터니언."""
    q = np.array(q, dtype=float, copy=True)
    q[..., 2] *= -1.0
    q[..., 3] *= -1.0
    return q


def mirror_partners(skel: Skeleton, Ft: np.ndarray) -> Optional[Tuple[np.ndarray, float]]:
    """기준 자세의 좌우 대칭 짝 (partner[j] = 반대편 관절, 가운데 본은 자기 자신)과 대칭 중심의 X. 짝을 못 찾는 본이 많으면 None."""
    _, bwp = skel.bind_world()
    bp = Q.rotate(Ft, bwp)
    size = float(np.linalg.norm(np.ptp(bp, axis=0)))
    if size < 1e-9:
        return None
    x0 = float(bp[:, 0].mean())
    m = bp.copy()
    m[:, 0] = 2 * x0 - m[:, 0]
    d = np.linalg.norm(m[:, None, :] - bp[None, :, :], axis=2)
    part = d.argmin(axis=1)
    ok = (d[np.arange(skel.n), part] < 0.03 * size) & (part[part] == np.arange(skel.n))
    if float((~ok).mean()) > 0.15:
        return None
    part = np.where(ok, part, np.arange(skel.n))
    return part, x0


def mirror(anim: Animation, skel: Skeleton, Ft: np.ndarray) -> Tuple[Animation, Optional[str]]:
    """모션을 좌우로 뒤집는다 (왼팔이 하던 일을 오른팔이, 몸 전체가 거울상으로). (애니메이션, 실패 이유 또는 None)"""
    mp = mirror_partners(skel, Ft)
    if mp is None:
        return anim, "좌우 반전: 기준 자세가 좌우 대칭이 아니라서 짝이 되는 본을 찾지 못했습니다 (반전하지 않았습니다)."
    part, x0 = mp
    wr, wp = fk(skel, anim.rot, anim.pos)
    bwr, _ = skel.bind_world()
    brs = Q.mul(Ft, bwr)                                   # 표준 프레임의 바인드 회전
    rs = Q.mul(Ft, wr)
    ps = Q.rotate(Ft, wp)
    D = Q.mul(rs, Q.conj(brs[None]))                        # 바인드 대비 변화량 (표준 프레임)
    nr = np.empty_like(rs)
    npos = np.empty_like(ps)
    for j in range(skel.n):
        p = int(part[j])
        nr[:, p] = Q.mul(_mirror_q(D[:, j]), brs[p][None])
        q = ps[:, j].copy()
        q[:, 0] = 2 * x0 - q[:, 0]
        npos[:, p] = q
    FtI = Q.conj(Ft)
    wr2 = Q.mul(FtI, nr)
    wp2 = Q.rotate(FtI, npos)
    rot, pos = world_to_local(skel, wr2, wp2)
    const = np.ptp(anim.pos, axis=0).max(axis=-1) < 1e-9   # 위치가 움직이지 않는 본은 자기 값을 유지 (대칭 오차로 길이가 흔들리지 않게)
    pos[:, const] = anim.pos[:, const]
    out = Animation(anim.name, anim.fps, Q.make_continuous(Q.normalize(rot)), pos)
    return out, None


def active(st) -> bool:
    return bool(getattr(st, "fx_mirror", False) or getattr(st, "fx_smooth", 0.0) > 0.05 or getattr(st, "fx_spike_deg", 0.0) > 0
                or any(abs(float(v) - 1.0) > 1e-6 for v in (getattr(st, "fx_parts", None) or {}).values()))


def apply(res, skel: Skeleton, tgt_slots: Dict[str, int], st) -> None:
    """RetargetResult 의 anim 에 후처리를 건다 (제자리에서 res.anim, res.warnings 를 바꾼다)."""
    if not active(st):
        return
    a = res.anim
    parts = getattr(st, "fx_parts", None) or {}
    if any(abs(float(v) - 1.0) > 1e-6 for v in parts.values()):
        a = scale_parts(a, skel, parts, tgt_slots)
        res.warnings.append("ℹ 부위별 모션 세기: " + ", ".join("%s %d%%" % (PARTS[k][0], round(100 * float(v))) for k, v in parts.items()
                                                       if k in PARTS and abs(float(v) - 1.0) > 1e-6))
    if getattr(st, "fx_spike_deg", 0.0) > 0:
        a, n = remove_spikes(a, float(st.fx_spike_deg), max_len=int(getattr(st, "fx_spike_len", 6)))
        res.warnings.append("ℹ 튀는 프레임 제거: %d 곳을 앞뒤 값으로 고쳤습니다 (기준 %.0f°)" % (n, st.fx_spike_deg))
    if getattr(st, "fx_smooth", 0.0) > 0.05:
        a = smooth(a, float(st.fx_smooth))
        res.warnings.append("ℹ 부드럽게(강도 %.1f프레임): 빠르고 작은 떨림이 줄지만 발 고정(IK)이 아주 조금 미끄러질 수 있습니다." % st.fx_smooth)
    if getattr(st, "fx_mirror", False):
        a, why = mirror(a, skel, res.frame_tgt[0])
        if why:
            res.warnings.append("⚠ " + why)
        else:
            for p in res.plans:       # 발·손 접지 표시는 반전 전 기준이라 맞지 않는다 -> 화면 표시만 끈다 (결과 모션에는 영향 없음)
                p.applied = False
            res.warnings.append("ℹ 좌우 반전 적용: 화면의 IK 접지 표시와 소스 고스트는 반전 전 기준 그대로입니다.")
    res.anim = a
