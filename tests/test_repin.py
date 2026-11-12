"""IK 로 고정한 구간: 본 편집(레이어)으로 허벅지/무릎을 돌려도 발은 제자리, 무릎만 움직인다."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import quat as Q
from motionbridge.core import repin as R
from motionbridge.core.model import Animation, Skeleton, fk
from motionbridge.core.retarget import IKChain


def make():
    names = ["Hips", "UpLeg", "Leg", "Foot", "Toe"]
    parents = np.array([-1, 0, 1, 2, 3])
    T = 30
    rot = np.tile(np.array([1.0, 0, 0, 0]), (T, 5, 1))
    pos = np.zeros((T, 5, 3))
    pos[:, 0] = [0, 2.0, 0]
    pos[:, 1] = [0.2, 0, 0]
    pos[:, 2] = [0, -1.0, 0]
    pos[:, 3] = [0, -1.0, 0]
    pos[:, 4] = [0, 0, 0.3]
    rot[:, 1] = Q.from_axis_angle(np.array([1.0, 0, 0]), np.radians(-30))     # 무릎이 앞으로 나온 자세
    rot[:, 2] = Q.from_axis_angle(np.array([1.0, 0, 0]), np.radians(60))
    rot[:, 3] = Q.from_axis_angle(np.array([1.0, 0, 0]), np.radians(-30))
    sk = Skeleton(names, parents, pos[0].copy())
    return sk, Animation("a", 30.0, rot, pos)


def test_repin():
    sk, base = make()
    ch = IKChain(3, 1, 2, 3, "왼발")
    anim = Animation("a", 30.0, base.rot.copy(), base.pos.copy())
    twist = Q.from_axis_angle(np.array([0, 1.0, 0]), np.radians(35))         # 허벅지를 옆으로 돌림 (무릎을 바깥으로)
    lift = Q.from_axis_angle(np.array([1.0, 0, 0]), np.radians(-25))         # 그리고 들어 올림
    anim.rot[:, 1] = Q.mul(Q.mul(twist, lift), base.rot[:, 1])
    _w0, p0 = fk(sk, base.rot, base.pos)
    _w1, p1 = fk(sk, anim.rot, anim.pos)
    assert np.linalg.norm(p1[12, 3] - p0[12, 3]) > 0.3, "고치기 전에는 발이 같이 들린다"
    before = anim.rot.copy()
    n = R.repin(sk, base, anim, [ch], {"왼발": [[10, 19, True], [22, 24, False]]})
    assert n == 10, n
    w2, p2 = fk(sk, anim.rot, anim.pos)
    assert np.abs(p2[10:20, 3] - p0[10:20, 3]).max() < 1e-6, "고정 구간: 발 위치 그대로"
    assert np.linalg.norm(p2[12, 2] - p0[12, 2]) > 0.1, "무릎은 돌린 쪽으로 움직임"
    wb, _ = fk(sk, base.rot, base.pos)
    assert np.degrees(Q.angle_between(w2[10:20, 3], wb[10:20, 3])).max() < 1e-3, "발 방향도 그대로 (발 본을 안 건드렸으면)"
    assert np.allclose(anim.rot[:10], before[:10]) and np.allclose(anim.rot[20:], before[20:]), "구간 밖·끄기 구간은 손대지 않음"
    assert np.allclose(np.linalg.norm(p2[:, 2] - p2[:, 1], axis=1), 1.0) and np.allclose(np.linalg.norm(p2[:, 3] - p2[:, 2], axis=1), 1.0), "뼈 길이 유지"
    # 레이어가 이 다리를 안 건드렸으면 아무것도 안 함
    same = Animation("a", 30.0, base.rot.copy(), base.pos.copy())
    assert R.repin(sk, base, same, [ch], {"왼발": [[0, 29, True]]}) == 0
    # 골반을 너무 올려 닿지 않으면 다리가 펴지는 데까지만
    far = Animation("a", 30.0, base.rot.copy(), base.pos.copy())
    far.pos[:, 0, 1] += 1.5
    R.repin(sk, base, far, [ch], {"왼발": [[0, 29, True]]})
    _w, p3 = fk(sk, far.rot, far.pos)
    assert np.isfinite(p3).all() and np.linalg.norm(p3[5, 3] - p3[5, 1]) > 1.99
    print("repin OK")


if __name__ == "__main__":
    test_repin()
