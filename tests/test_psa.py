import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge.core import quat as Q  # noqa: E402
from motionbridge.core.io_psa import PSAError, parse_psa, read_psa, write_psa  # noqa: E402
from motionbridge.core.model import fk  # noqa: E402
from motionbridge.core.pipeline import Project  # noqa: E402
import synth  # noqa: E402


def same_motion(sk1, an1, sk2, an2):
    assert sk1.names == sk2.names and np.array_equal(sk1.parents, sk2.parents)
    assert np.allclose(sk1.offsets, sk2.offsets, atol=1e-4)
    assert np.all(np.abs(np.sum(sk1.rest_rot * sk2.rest_rot, -1)) > 1 - 1e-6)
    wr1, wp1 = fk(sk1, an1.rot, an1.pos)
    wr2, wp2 = fk(sk2, an2.rot, an2.pos)
    assert np.allclose(wp1, wp2, atol=1e-3), np.abs(wp1 - wp2).max()
    assert np.all(np.abs(np.sum(wr1 * wr2, -1)) > 1 - 1e-5)


def make():
    sk, meta = synth.build_skeleton("ue", "z_up_x", 100.0, "A", "bone_x")
    an, _ = synth.walk_animation(sk, meta, n_frames=30, name="walk_a")
    an2, _ = synth.walk_animation(sk, meta, n_frames=18, name="walk_b", speed=1.2)
    return sk, [an, an2]


def test_roundtrip_all_conventions():
    sk, anims = make()
    with tempfile.TemporaryDirectory() as d:
        for conj in [(False, False), (False, True), (True, False), (True, True)]:
            p = os.path.join(d, "t.psa")
            write_psa(p, sk, anims, conj=conj)
            # 관례를 알려주면 정확히 복원
            m = read_psa(p, conj=conj)
            assert [a.name for a in m.animations] == ["walk_a", "walk_b"] and m.animations[1].n_frames == 18
            for a0, a1 in zip(anims, m.animations):
                assert abs(a1.fps - a0.fps) < 1e-3
                same_motion(sk, a0, m.skeleton, a1)
            # 자동 선택: 비루트 켤레 여부는 반드시 맞혀야 한다
            m2 = read_psa(p)
            sel = m2.notes[0]
            assert ("비루트켤레=%s" % conj[1]) in sel, (conj, sel)
            if not np.allclose(sk.rest_rot[0], [1, 0, 0, 0], atol=1e-6) or conj[0] is False:
                same_motion(sk, anims[0], m2.skeleton, m2.animations[0])
    print("psa roundtrip OK")


def test_errors():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "bad.psa")
        open(p, "wb").write(b"not a psa" * 10)
        try:
            parse_psa(p)
        except PSAError:
            pass
        else:
            raise AssertionError
    print("psa errors OK")


def test_psa_as_source():
    """PSA -> 소스 모션 -> 내 모델(GLB) 리타깃 -> BVH, 전체 경로."""
    import test_pipeline as TP

    sk, anims = make()
    with tempfile.TemporaryDirectory() as d:
        psa = os.path.join(d, "src.psa")
        write_psa(psa, sk, anims, conj=(False, True))
        _, _, tgt, _, _ = TP.make_inputs(d)
        pr = Project()
        m = pr.load_source(psa)
        assert len(m.animations) == 2 and len(pr.src_slots) > 15
        pr.load_target(tgt)
        res = pr.run(0)
        assert res.ik_applied and len(res.pairs) > 15
        errs = [i for i in pr.issues if i.severity == "error"]
        assert not errs, errs
        files, warns = pr.export(__import__("motionbridge.core.pipeline", fromlist=["x"]).ExportOptions(
            out_dir=os.path.join(d, "o"), presets=["maya"], formats=["bvh"], clips=[0, 1]))
        assert len(files) == 2
    print("psa as source OK")


if __name__ == "__main__":
    test_roundtrip_all_conventions()
    test_errors()
    test_psa_as_source()
    print("test_psa ALL OK")
