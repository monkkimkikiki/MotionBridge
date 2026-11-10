"""바이패드식 위치 편집: 사슬 끝을 옮기면 위/가운데 본 회전으로 끝이 목표에 닿고 뼈 길이는 그대로."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from motionbridge.core import posik, quat as Q  # noqa: E402


def apply(A, B, C, q1, q2):
    Bn = A + Q.rotate(q1, B - A)
    Cn = Bn + Q.rotate(Q.mul(q2, q1), C - B)
    return Bn, Cn


def main():
    rng = np.random.default_rng(1)
    A, B, C = np.array([0, 1.0, 0]), np.array([0, 0.5, 0.12]), np.array([0, 0.05, 0.0])
    for _ in range(60):
        T = C + rng.normal(scale=0.12, size=3)
        q1, q2, ok = posik.solve(A, B, C, T)
        Bn, Cn = apply(A, B, C, q1, q2)
        assert abs(np.linalg.norm(Bn - A) - np.linalg.norm(B - A)) < 1e-9 and abs(np.linalg.norm(Cn - Bn) - np.linalg.norm(C - B)) < 1e-9, "뼈 길이 유지"
        if ok:
            assert np.linalg.norm(Cn - T) < 1e-9, "닿을 수 있으면 정확히 목표"
    q1, q2, ok = posik.solve(A, B, C, A + np.array([0, -3.0, 0]))        # 너무 멀면 쭉 펴서 그쪽으로
    Bn, Cn = apply(A, B, C, q1, q2)
    assert not ok and np.linalg.norm(Cn - A) > 0.99 * (np.linalg.norm(B - A) + np.linalg.norm(C - B))
    assert posik.solve(A, A, C, C) is None
    slots = {"L_UpLeg": 1, "L_LowLeg": 2, "L_Foot": 3, "R_UpperArm": 5, "R_LowerArm": 6, "R_Hand": 7, "Hips": 0}
    assert posik.end_chain(slots, 3)[:3] == (1, 2, 3) and posik.end_chain(slots, 7)[:3] == (5, 6, 7)
    assert posik.end_chain(slots, 2) is None and posik.end_chain(slots, 0) is None
    print("posik OK")


if __name__ == "__main__":
    main()
