"""직접 지정한 IK '켜기' 구간의 발/손을, 보정 레이어(본 편집)를 얹은 뒤에도 그 자리에 다시 고정한다.

레이어는 리타겟(IK 포함) 결과 위에 얹히므로, 고정해 둔 다리의 무릎이나 허벅지를 돌리면 발까지 같이 들려 버렸다.
여기서는 그 구간의 프레임에서 2본 IK 를 한 번 더 풀어 발을 레이어 적용 전 위치로 되돌린다. 무릎이 향하는 쪽은 레이어로 돌린 방향을 그대로 쓴다
(= 고정된 발은 그대로 두고 무릎만 좌우/앞뒤로 돌리는 동작이 된다). 골반을 움직여 다리가 닿지 않으면 다리가 펴지는 데까지만 따라간다.
발 본 자체를 돌린 경우(발목 각도 수정)는 그 회전을 존중하고, 건드리지 않았으면 발의 월드 방향을 고정 전과 같게 유지한다."""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np

from . import quat as Q
from .model import Animation, Skeleton, fk


def pinned_mask(ranges: Optional[Sequence[Sequence]], T: int) -> np.ndarray:
    m = np.zeros(int(T), dtype=bool)
    for a, b, on in (ranges or []):
        a, b = max(min(int(a), int(b)), 0), min(max(int(a), int(b)), T - 1)
        if b >= a:
            m[a:b + 1] = bool(on)
    return m


def repin(skel: Skeleton, base: Animation, anim: Animation, chains, manual: Optional[Dict[str, list]], eps: float = 1e-5) -> int:
    """anim(레이어 적용 후)을 그 자리에서 고친다. 고친 (체인, 프레임) 수를 돌려준다."""
    from .retarget import two_bone_ik
    if not manual or base is None or anim is None or anim.rot.shape != base.rot.shape:
        return 0
    T = anim.n_frames
    todo = []
    for ch in chains or []:
        m = pinned_mask(manual.get(ch.label), T)
        if m.any():
            todo.append((ch, m))
    if not todo:
        return 0
    wr0, wp0 = fk(skel, base.rot, base.pos)
    fixed = 0
    for ch, m in todo:                       # 체인 하나를 고칠 때마다 FK 를 다시 계산 (앞 체인의 수정이 반영되게; 체인은 보통 2~4개)
        wr, wp = fk(skel, anim.rot, anim.pos)
        u, lo, ft = int(ch.upper), int(ch.lower), int(ch.foot)
        moved = np.linalg.norm(wp[:, ft] - wp0[:, ft], axis=1) > eps * max(1.0, float(np.abs(wp0[:, ft]).max()))
        fr = np.flatnonzero(m & moved)
        if len(fr) == 0:
            continue
        A, B, C, Tg = wp[fr, u], wp[fr, lo], wp[fr, ft], wp0[fr, ft]
        l1 = float(np.linalg.norm(wp0[0, lo] - wp0[0, u]))
        l2 = float(np.linalg.norm(wp0[0, ft] - wp0[0, lo]))
        if l1 < 1e-9 or l2 < 1e-9:
            continue
        Bn, Cn, _fail = two_bone_ik(A, B, C, Tg, l1, l2, min_fold_deg=0.0)
        q1 = Q.between(B - A, Bn - A)
        Cpre = Bn + Q.rotate(q1, C - B)
        q2 = Q.between(Cpre - Bn, Cn - Bn)
        Ru2 = Q.mul(q1, wr[fr, u])
        Rl2 = Q.mul(q2, Q.mul(q1, wr[fr, lo]))
        # 발: 레이어로 발 본 자체를 돌렸으면 그 로컬 회전을 유지, 아니면 고정 전의 월드 방향을 유지
        foot_edited = Q.angle_between(anim.rot[fr, ft], base.rot[fr, ft]) > 1e-4
        Rf = np.where(foot_edited[:, None], Q.mul(Rl2, anim.rot[fr, ft]), wr0[fr, ft])
        pu = int(skel.parents[u])
        anim.rot[fr, u] = Q.normalize(Q.mul(Q.conj(wr[fr, pu]), Ru2) if pu >= 0 else Ru2)
        anim.rot[fr, lo] = Q.normalize(Q.mul(Q.conj(Ru2), Rl2))
        anim.rot[fr, ft] = Q.normalize(Q.mul(Q.conj(Rl2), Rf))
        fixed += len(fr)
    return fixed
