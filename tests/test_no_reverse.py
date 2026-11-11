"""무릎 역꺾임 방지: 주로 앞으로 꺾이는 다리가 몇 프레임만 뒤로 꺾이면, 그 프레임의 무릎을 앞쪽으로 되돌린다 (발 위치는 그대로)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import quat as Q
from motionbridge.core.retarget import stable_pole, two_bone_ik


def test_no_reverse():
    N, l1, l2 = 40, 1.0, 1.0
    A = np.tile(np.array([0.0, 2.0, 0.0]), (N, 1))
    bend = np.full(N, 0.35)                       # 무릎이 +Z(앞)으로 나옴
    bend[18:23] = -0.30                           # 5프레임만 뒤(-Z)로 꺾임 = 역관절
    B = A + np.stack([np.zeros(N), -np.sqrt(l1 ** 2 - bend ** 2), bend], axis=1)
    C = B + np.stack([np.zeros(N), -np.sqrt(l2 ** 2 - bend ** 2), -bend], axis=1)     # 발은 골반 바로 아래
    Rref = np.tile(np.array([1.0, 0, 0, 0]), (N, 1))
    info = {}
    pole, rel = stable_pole(A, B, C, Rref, no_reverse=True, info=info)
    assert info["reversed"][18:23].all() and info["reversed"].sum() == 5, info["reversed"].nonzero()
    rel2 = np.where(info["reversed"], 0.0, rel)
    Bn, Cn, _fail = two_bone_ik(A, B, C, C.copy(), l1, l2, pole=pole, pole_rel=rel2)
    assert (Bn[:, 2] > 0.2).all(), "모든 프레임에서 무릎이 앞쪽: %s" % Bn[16:25, 2]
    assert np.abs(Cn - C).max() < 1e-6, "발 위치는 그대로"
    assert np.abs(Bn[:18] - B[:18]).max() < 1e-6, "정상 프레임은 바뀌지 않음"
    # 끄면 예전처럼 그대로 (뒤로 꺾인 채)
    pole0, rel0 = stable_pole(A, B, C, Rref)
    B0, _C0, _f = two_bone_ik(A, B, C, C.copy(), l1, l2, pole=pole0, pole_rel=rel0)
    assert (B0[18:23, 2] < -0.2).all()
    # 클립 전체가 한쪽으로만 꺾이면(원래 그런 다리) 아무것도 건드리지 않는다
    info2 = {}
    stable_pole(A, B0 * 0 + (A + np.array([0, -0.9, -0.43])), C, Rref, no_reverse=True, info=info2)
    assert not info2["reversed"].any()
    print("no reverse OK")


if __name__ == "__main__":
    test_no_reverse()
