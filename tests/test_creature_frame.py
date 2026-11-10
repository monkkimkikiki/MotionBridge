"""사람 모양이 아닌 뼈대(몬스터)의 앞쪽 추정 시험: 파일 안에서 +X 나 -Z 를 보고 있어도 표준 프레임에서는 +Z(정면)가 되어야 한다."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import quat as Q
from motionbridge.core.mapping import detect_creature_frame
from motionbridge.core.model import Animation, Skeleton


def make(fwd):
    """몸통(루트) + 머리 쪽 갈라진 턱/뿔 4개 + 꼬리 한 줄 + 좌우 다리 2쌍. fwd 방향(수평 단위벡터)을 앞으로 본다. 위는 +Y."""
    f = np.array(fwd, float)
    l = np.cross([0, 1, 0], f)
    names, par, off = ["root"], [-1], [np.zeros(3)]

    def add(n, p, v):
        names.append(n); par.append(p); off.append(np.array(v, float)); return len(names) - 1

    neck = add("neck", 0, f * 0.8 + [0, 0.3, 0])
    head = add("head", neck, f * 0.5)
    for i, s in enumerate((-0.3, 0.0, 0.3)):
        add("jaw%d" % i, head, f * 0.4 + l * s)
    add("horn", head, [0, 0.4, 0] + f * 0.1 + l * 0.2)
    add("horn2", head, [0, 0.4, 0] + f * 0.1 - l * 0.2)
    t = 0
    for i in range(3):
        t = add("tail%d" % i, t, -f * 0.5)
    for s in (-1, 1):
        for z in (0.5, -0.5):
            a = add("leg%d_%d_a" % (s, int(z * 10)), 0, l * s * 0.4 + f * z + [0, -0.2, 0])
            add("leg%d_%d_b" % (s, int(z * 10)), a, [0, -0.8, 0])
    return Skeleton(names, par, off)


def clip(sk, move):
    T = 20
    rot = np.repeat(sk.rest_rot[None], T, axis=0)
    pos = np.repeat(sk.offsets[None], T, axis=0)
    for t in range(T):
        pos[t, 0] = sk.offsets[0] + np.array(move, float) * t * 0.2
    return Animation("c", 30.0, rot, pos)


def facing(F, fwd):
    """파일의 fwd 방향이 표준 프레임에서 어느 쪽인가."""
    return Q.rotate(F, np.array(fwd, float))


def test_bind_pose_only():
    for fwd in ([1, 0, 0], [-1, 0, 0], [0, 0, 1], [0, 0, -1]):
        sk = make(fwd)
        det = detect_creature_frame(sk, None)
        assert det is not None, fwd
        v = facing(det[0], fwd)
        assert np.allclose(v, [0, 0, 1], atol=1e-6), (fwd, v, det[1])
        assert np.allclose(facing(det[0], [0, 1, 0]), [0, 1, 0], atol=1e-6)
    print("creature bind-pose facing OK")


def test_motion_decides_direction():
    sk = make([1, 0, 0])
    det = detect_creature_frame(sk, clip(sk, [-1, 0, 0]))   # 머리가 +X 인데 뒤로 걸어 간다 -> 이동 방향을 따른다
    assert np.allclose(facing(det[0], [-1, 0, 0]), [0, 0, 1], atol=1e-6), det
    det = detect_creature_frame(sk, clip(sk, [1, 0, 0]))
    assert np.allclose(facing(det[0], [1, 0, 0]), [0, 0, 1], atol=1e-6), det
    print("creature motion direction OK")


def test_unknown_returns_none():
    sk = Skeleton(["a", "b", "c"], [-1, 0, 1], [[0, 0, 0], [0, 1, 0], [0, 1, 0]])
    assert detect_creature_frame(sk, None) is None
    print("creature unknown OK")


def test_viewer_uses_it():
    from motionbridge.core.pipeline import Project
    from motionbridge.ui.viewer_math import ViewData
    pr = Project()
    sk = make([1, 0, 0])

    class M:  # 최소 모델
        skeleton = sk
        animations = [clip(sk, [0, 0, 0])]
        meshes = []
        notes = []
        path = ""
    pr.src, pr.src_slots = M(), {}
    vd = ViewData.build(pr)
    head = sk.names.index("head")
    gp = vd.ghost_pos[0]
    d = gp[head] - gp[0]
    assert d[2] > abs(d[0]) * 3, d      # 머리가 화면 앞(+Z)쪽
    print("creature viewer facing OK")


if __name__ == "__main__":
    test_bind_pose_only()
    test_motion_decides_direction()
    test_unknown_returns_none()
    test_viewer_uses_it()
    print("test_creature_frame ALL OK")
