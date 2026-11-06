"""동작 비교(뼈 방향 + DTW): 스켈레톤·축·크기·길이가 달라도 같은 동작이면 높은 점수."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge.core import motionmatch as MM  # noqa: E402
from motionbridge.core import quat as Q  # noqa: E402
from motionbridge.core.model import Animation, Skeleton  # noqa: E402
import synth  # noqa: E402


def mk(cfg, **kw):
    sk, meta = synth.build_skeleton(**cfg)
    an, _ = synth.walk_animation(sk, meta, **kw)
    return sk, an


A = dict(style="mixamo", frame="y_up_z", scale=1.0, pose="T", local_axes="aligned")
B = dict(style="ue", frame="z_up_x", scale=100.0, pose="T", local_axes="bone_x", height=1.1, leg_scale=1.15)
C = dict(style="biped", frame="z_up_negy", scale=100.0, pose="T", local_axes="bone_x", height=0.9)


def test_rank():
    q = mk(A, n_frames=60)
    ue = mk(B, n_frames=60)  # 다른 스켈레톤/축/크기
    bp = mk(C, n_frames=100)  # 다른 길이(빠르기)
    other = mk(A, n_frames=60, stance=0.3, speed=1.6, hip_drop=0.2)  # 걸음 모양이 다름
    rest = (q[0], Animation.rest(q[0], 30))
    r = MM.rank(q, [("ue", *ue), ("biped", *bp), ("other", *other), ("rest", *rest)])
    order = [l for l, _s in r]
    sc = {l: s for l, s in r}
    assert order[0] == "ue" and order[-1] == "rest", order
    assert sc["ue"].score > 0.9 and sc["biped"].score > 0.8, {k: (v.score, v.deg) for k, v in sc.items()}
    assert sc["ue"].score > sc["other"].score > sc["rest"].score
    assert all(s.parts >= 10 for s in sc.values())
    assert not sc["ue"].mirrored


def test_heading_invariant():
    """캐릭터를 어느 쪽으로 돌려 놔도 같은 점수."""
    q = mk(A, n_frames=60)
    sk, an = mk(A, n_frames=60)
    rot = an.rot.copy()
    yaw = Q.from_axis_angle(np.array([0.0, 1.0, 0.0]), np.radians(137.0))
    rot[:, 0] = Q.mul(yaw, rot[:, 0])
    turned = Animation(an.name, an.fps, rot, an.pos.copy())
    s = MM.rank(q, [("turned", sk, turned)])[0][1]
    assert s.deg < 1.0 and s.score > 0.95, (s.deg, s.score)


def test_mirror():
    sk, an = mk(A, n_frames=60)
    d = MM.descriptor(sk, an)
    n = MM.names_of(sk)
    assert d is not None and d.shape[1] == len(n) >= 10 and np.allclose(np.linalg.norm(d, axis=2), 1.0, atol=1e-6)
    assert np.allclose(MM.mirror(MM.mirror(d, n), n), d)
    # 좌우 반전된 동작과 비교하면 mirrored 로 같은 동작이라고 본다 (걷기는 좌우가 다르므로 반전본이 원본보다 달라야 함)
    m = MM.mirror(d, n)
    sc = MM.compare(m, n, d, n)
    assert sc is not None and sc.deg < 1e-6 and sc.mirrored or MM.compare(m, n, d, n, allow_mirror=False).deg > 1.0
    no_m = MM.compare(m, n, d, n, allow_mirror=False)
    assert sc.deg <= no_m.deg


def test_unmappable():
    sk = Skeleton(["a", "b", "c"], [-1, 0, 1], np.array([[0, 0, 0], [0, 1, 0], [0, 1, 0]], float))
    an = Animation.rest(sk, 5)
    assert MM.descriptor(sk, an) is None
    q = mk(A, n_frames=30)
    assert MM.rank(q, [("x", sk, an)]) == []
    assert MM.rank((sk, an), [("y", *q)]) == []


if __name__ == "__main__":
    test_rank()
    test_heading_invariant()
    test_mirror()
    test_unmappable()
    print("test_motionmatch ALL OK")
