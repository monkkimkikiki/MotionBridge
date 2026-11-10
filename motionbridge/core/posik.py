"""바이패드식 위치 편집 (Qt 없음): 손/발 같은 사슬 끝을 옮기면 그 본만 늘어나는 대신 위팔·아래팔(허벅지·정강이)이 IK 처럼 돌아 팔다리가 자연스럽게 펴지고 접히며 따라온다.

결과는 '위쪽 본 회전 q1, 가운데 본 회전 q2' (표준 프레임 월드 회전 변화) 로 돌려주고, 이것을 본 회전 편집(레이어 키 / 본별 보정)으로 그대로 기록하면 된다."""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np

from . import quat as Q
from .retarget import two_bone_ik

SIDES = (("L", "왼"), ("R", "오른"))


def end_chain(tgt_slots: Dict[str, int], j: int) -> Optional[Tuple[int, int, int, str]]:
    """j 가 다리(발)/팔(손) 사슬의 끝이면 (위 본, 가운데 본, 끝 본, 이름), 아니면 None."""
    for side, nm in SIDES:
        for up, lo, end, label in (("UpLeg", "LowLeg", "Foot", "다리"), ("UpperArm", "LowerArm", "Hand", "팔")):
            keys = ["%s_%s" % (side, up), "%s_%s" % (side, lo), "%s_%s" % (side, end)]
            if all(k in tgt_slots for k in keys) and tgt_slots[keys[2]] == int(j):
                return tgt_slots[keys[0]], tgt_slots[keys[1]], tgt_slots[keys[2]], "%s쪽 %s" % (nm, label)
    return None


def solve(A, B, C, T, pole_fallback=(0.0, 0.0, 1.0)) -> Optional[Tuple[np.ndarray, np.ndarray, bool]]:
    """위(A)·가운데(B)·끝(C) 위치(표준 프레임)와 끝의 목표 T 로, 위쪽 본에 곱할 월드 회전 q1 과 가운데 본에 곱할 월드 회전 q2, 목표에 닿았는지를 구한다.
    무릎/팔꿈치가 굽던 방향은 그대로 유지한다. 길이가 0 이거나 계산이 안 되면 None."""
    A, B, C, T = (np.asarray(x, dtype=float).reshape(1, 3) for x in (A, B, C, T))
    l1, l2 = float(np.linalg.norm(B - A)), float(np.linalg.norm(C - B))
    if l1 < 1e-9 or l2 < 1e-9:
        return None
    Bn, Cn, fail = two_bone_ik(A, B, C, T, l1, l2, pole_fallback=pole_fallback)
    q1 = Q.between(B - A, Bn - A)
    Cpre = Bn + Q.rotate(q1, C - B)
    q2 = Q.between(Cpre - Bn, Cn - Bn)
    if not (np.isfinite(q1).all() and np.isfinite(q2).all()):
        return None
    return np.asarray(q1[0], dtype=float), np.asarray(q2[0], dtype=float), not bool(fail[0])
