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
            m2 = read_psa(p, fmt="generic")
            sel = m2.notes[0]
            assert ("비루트켤레=%s" % conj[1]) in sel, (conj, sel)
            if not np.allclose(sk.rest_rot[0], [1, 0, 0, 0], atol=1e-6) or conj[0] is False:
                same_motion(sk, anims[0], m2.skeleton, m2.animations[0])
    print("psa roundtrip OK")


def simulate_cue4parse_writer(path, sk, anims):
    """CUE4Parse ActorXAnim.cs 의 변환을 언리얼(왼손) 값에서 그대로 따라 한다 — 앱 쪽 코드와 따로 구현."""
    import struct
    J = sk.n
    # 앱의 오른손 값 -> 언리얼 원본(왼손): 위치 y 반전, 쿼터니언 (x,z 반전)
    def ue_q(q):  # wxyz
        q = np.asarray(q, float).copy()
        q[..., 1] *= -1
        q[..., 3] *= -1
        return q
    def ue_p(p):
        p = np.asarray(p, float).copy()
        p[..., 1] *= -1
        return p
    out = bytearray()
    def hdr(name, flag, size, count):
        out.extend(name.encode().ljust(20, b"\0") + struct.pack("<iii", flag, size, count))
    hdr("ANIMHEAD", 20100422, 0, 0)
    hdr("BONENAMES", 0, 120, J)
    rq, rp = ue_q(sk.rest_rot), ue_p(sk.offsets)
    for j in range(J):
        r = bytearray(120)
        r[:64] = sk.names[j].encode().ljust(64, b"\0")
        struct.pack_into("<Iii", r, 64, 0, 0, max(int(sk.parents[j]), 0))
        w, x, y, z = rq[j]
        struct.pack_into("<7f", r, 76, x, y, z, w, *rp[j])
        out += r
    a = anims[0]
    T = a.n_frames
    hdr("ANIMINFO", 0, 168, 1)
    r = bytearray(168)
    r[:64] = a.name.encode().ljust(64, b"\0")
    struct.pack_into("<i", r, 128, J)
    struct.pack_into("<ff", r, 148, float(T), a.fps)
    struct.pack_into("<iii", r, 156, 0, 0, T)
    out += r
    hdr("ANIMKEYS", 0, 32, T * J)
    uq, up = ue_q(a.rot), ue_p(a.pos)
    for f in range(T):
        for j in range(J):
            w, x, y, z = uq[f, j]
            px, py, pz = up[f, j]
            # --- C# 쪽: MIRROR_MESH ---
            y = -y
            if j == 0:
                w = -w
            py = -py
            out += struct.pack("<7ff", px, py, pz, x, y, z, w, 1.0)
    open(path, "wb").write(bytes(out))


def test_cue4parse_layout():
    sk, anims = make()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "c.psa")
        simulate_cue4parse_writer(p, sk, anims)
        m = read_psa(p)
        assert "CUE4Parse" in m.notes[0], m.notes
        same_motion(sk, anims[0], m.skeleton, m.animations[0])
        # write_psa 기본값도 같은 배치여야 한다
        p2 = os.path.join(d, "w.psa")
        write_psa(p2, sk, anims)
        m2 = read_psa(p2)
        assert "CUE4Parse" in m2.notes[0]
        for a0, a1 in zip(anims, m2.animations):
            same_motion(sk, a0, m2.skeleton, a1)
    print("cue4parse layout OK")


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
        write_psa(psa, sk, anims)
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
    test_cue4parse_layout()
    test_errors()
    test_psa_as_source()
    print("test_psa ALL OK")
