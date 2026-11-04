import itertools
import sys
import os

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import quat as Q  # noqa: E402


def rand_q(n, rng):
    return Q.normalize(rng.normal(size=(n, 4)))


def test_all():
    rng = np.random.default_rng(1)
    q = rand_q(500, rng)

    # 행렬 왕복
    m = Q.to_matrix(q)
    q2 = Q.from_matrix(m)
    assert np.all(np.abs(np.sum(q * q2, -1)) > 1 - 1e-9), "matrix roundtrip"

    # 곱 == 행렬 곱
    a, b = rand_q(200, rng), rand_q(200, rng)
    assert np.allclose(Q.to_matrix(Q.mul(a, b)), Q.to_matrix(a) @ Q.to_matrix(b), atol=1e-9), "mul vs matmul"

    # 벡터 회전
    v = rng.normal(size=(200, 3))
    assert np.allclose(Q.rotate(a, v), np.einsum("nij,nj->ni", Q.to_matrix(a), v), atol=1e-9), "rotate"

    # 오일러: 6가지 순서 왕복 (짐벌락 아닌 각도)
    for order in ["XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"]:
        ang = rng.uniform(-80, 80, size=(300, 3))
        qq = Q.from_euler(ang, order)
        back = Q.to_euler(qq, order)
        qb = Q.from_euler(back, order)
        assert np.all(np.abs(np.sum(qq * qb, -1)) > 1 - 1e-9), "euler roundtrip " + order
        assert np.allclose(back, ang, atol=1e-6), "euler angles " + order

    # 짐벌락 근처에서도 회전은 보존
    for order in ["XYZ", "ZXY", "ZYX"]:
        ang = np.array([[30.0, 90.0, 20.0], [10.0, -90.0, 45.0]])
        qq = Q.from_euler(ang, order)
        qb = Q.from_euler(Q.to_euler(qq, order), order)
        assert np.all(np.abs(np.sum(qq * qb, -1)) > 1 - 1e-7), "gimbal " + order

    # from_euler 는 내재 합성 R = R_o0 R_o1 R_o2
    ang = np.array([[20.0, 35.0, -50.0]])
    qz = Q.from_axis_angle([0, 0, 1], np.radians(20.0))
    qx = Q.from_axis_angle([1, 0, 0], np.radians(35.0))
    qy = Q.from_axis_angle([0, 1, 0], np.radians(-50.0))
    ref = Q.mul(Q.mul(qz, qx), qy)
    assert np.allclose(Q.from_euler(ang, "ZXY")[0], ref) or np.allclose(Q.from_euler(ang, "ZXY")[0], -ref)

    # between
    x, y = rng.normal(size=(300, 3)), rng.normal(size=(300, 3))
    qb = Q.between(x, y)
    r = Q.rotate(qb, x / np.linalg.norm(x, axis=-1, keepdims=True))
    assert np.allclose(r, y / np.linalg.norm(y, axis=-1, keepdims=True), atol=1e-9), "between"
    anti = Q.between(np.array([[0.0, 1.0, 0.0]]), np.array([[0.0, -1.0, 0.0]]))
    assert np.allclose(Q.rotate(anti, np.array([[0.0, 1.0, 0.0]])), [[0.0, -1.0, 0.0]], atol=1e-9), "antiparallel"

    # slerp 끝점과 중점
    s0 = Q.slerp(a, b, np.zeros(200))
    s1 = Q.slerp(a, b, np.ones(200))
    assert np.all(np.abs(np.sum(s0 * a, -1)) > 1 - 1e-9) and np.all(np.abs(np.sum(s1 * b, -1)) > 1 - 1e-9), "slerp ends"
    mid = Q.slerp(a, b, np.full(200, 0.5))
    assert np.allclose(Q.angle_between(a, mid), Q.angle_between(mid, b), atol=1e-7), "slerp mid"

    # 연속성
    seq = np.stack([Q.from_axis_angle([0, 1, 0], np.radians(t)) for t in np.linspace(0, 700, 100)])
    seq[::2] *= -1  # 일부러 부호를 뒤집음
    cont = Q.make_continuous(seq)
    assert np.all(np.sum(cont[1:] * cont[:-1], -1) > 0), "continuous"
    print("test_quat OK")


if __name__ == "__main__":
    test_all()
