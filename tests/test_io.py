import json
import os
import struct
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge.core import quat as Q  # noqa: E402
from motionbridge.core.io_bvh import read_bvh, write_bvh  # noqa: E402
from motionbridge.core.io_gltf import read_gltf, write_glb  # noqa: E402
from motionbridge.core.model import Mesh, fk  # noqa: E402
import synth  # noqa: E402


def test_generator_truth():
    """생성기 자체 검증: 스탠스 구간에서 발 월드 위치가 정지, 정답 발 위치와 FK 결과가 일치."""
    sk, meta = synth.build_skeleton("ue", "z_up_x", 100.0, "A", "bone_x")
    an, truth = synth.walk_animation(sk, meta, n_frames=60)
    wr, wp = fk(sk, an.rot, an.pos)
    G, sc = meta["G"], meta["scale"]
    for side in "LR":
        j = sk.index({"L": "foot_l", "R": "foot_r"}[side])
        std = (wp[:, j] / sc) @ G  # 파일 -> 표준
        assert np.allclose(std, truth["ankle_std"][side], atol=1e-6), "발 위치 불일치 " + side
    # 스탠스 중 발 정지: 속도가 0 인 프레임이 충분히 있어야 한다
    j = sk.index("foot_l")
    sp = np.linalg.norm(np.diff(wp[:, j], axis=0), axis=1) / sc
    assert (sp < 1e-6 * 100).sum() >= 10, "정지 구간이 없음"
    print("generator truth OK")


def test_bvh_roundtrip():
    sk, meta = synth.build_skeleton("mixamo", "y_up_z", 1.0, "T", "aligned")
    an, _ = synth.walk_animation(sk, meta, n_frames=45)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.bvh")
        for order in ["ZXY", "XYZ", "YXZ"]:
            write_bvh(p, sk, an, order=order)
            m = read_bvh(p)
            sk2, an2 = m.skeleton, m.animations[0]
            assert abs(an2.fps - an.fps) < 1e-3
            assert an2.n_frames == an.n_frames
            wr1, wp1 = fk(sk, an.rot, an.pos)
            wr2, wp2 = fk(sk2, an2.rot, an2.pos)
            for j, nm in enumerate(sk.names):
                if nm.endswith("_End"):
                    continue
                k = sk2.find(nm)
                assert k >= 0, nm
                assert np.allclose(wp1[:, j], wp2[:, k], atol=1e-4), (order, nm)
                assert np.all(np.abs(np.sum(wr1[:, j] * wr2[:, k], -1)) > 1 - 1e-6), (order, nm)
    print("bvh roundtrip OK")


def make_mesh(sk):
    rng = np.random.default_rng(0)
    bwr, bwp = sk.bind_world()
    V = 200
    j0 = rng.integers(0, sk.n, V)
    j1 = np.array([sk.parents[j] if sk.parents[j] >= 0 else j for j in j0])
    verts = bwp[j0] + rng.normal(scale=0.03, size=(V, 3)) * np.abs(bwp).max() * 0.1
    w0 = rng.uniform(0.3, 1.0, V)
    joints = np.stack([j0, j1, j0, j0], 1)
    weights = np.stack([w0, 1 - w0, np.zeros(V), np.zeros(V)], 1)
    faces = np.stack([np.arange(0, V - 2, 3), np.arange(1, V - 1, 3), np.arange(2, V, 3)], 1)[: V // 3]
    return Mesh("body", verts, faces, joints, weights, rng.uniform(size=(V, 2)))


def test_glb_roundtrip():
    sk, meta = synth.build_skeleton("ue", "z_up_x", 100.0, "A", "bone_x")
    an, _ = synth.walk_animation(sk, meta, n_frames=40)
    mesh = make_mesh(sk)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.glb")
        write_glb(p, sk, an, [mesh])
        m = read_gltf(p)
        sk2, an2 = m.skeleton, m.animations[0]
        assert sorted(sk2.names) == sorted(sk.names)
        assert an2.n_frames == an.n_frames and abs(an2.fps - an.fps) < 1e-6
        wr1, wp1 = fk(sk, an.rot, an.pos)
        wr2, wp2 = fk(sk2, an2.rot, an2.pos)
        perm = np.array([sk2.index(n) for n in sk.names])  # 원본 인덱스 -> 읽은 인덱스
        assert np.allclose(wp1, wp2[:, perm], atol=1e-3), "glb 위치"
        assert np.all(np.abs(np.sum(wr1 * wr2[:, perm], -1)) > 1 - 1e-6), "glb 회전"
        assert len(m.meshes) == 1
        m2 = m.meshes[0]
        assert np.allclose(m2.vertices, mesh.vertices, atol=1e-3), "메시 바인드 좌표"
        # 스키닝 결과 비교
        a = mesh.skin(sk, wr1[17], wp1[17])
        b = m2.skin(sk2, wr2[17], wp2[17])
        assert np.allclose(a, b, atol=1e-2), "스키닝 결과"
    print("glb roundtrip OK")


def _repack(src, dst, fn):
    with open(src, "rb") as f:
        raw = f.read()
    off = 12
    js = None
    binb = b""
    while off < len(raw):
        clen, ctype = struct.unpack_from("<II", raw, off)
        body = raw[off + 8 : off + 8 + clen]
        if ctype == 0x4E4F534A:
            js = json.loads(body)
        else:
            binb = bytes(body)
        off += 8 + clen
    fn(js)
    j = json.dumps(js).encode()
    while len(j) % 4:
        j += b" "
    with open(dst, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(j) + 8 + len(binb)))
        f.write(struct.pack("<II", len(j), 0x4E4F534A) + j)
        f.write(struct.pack("<II", len(binb), 0x004E4942) + binb)


def test_armature_ancestor_baking():
    """Blender 처럼 상위 Armature 노드에 스케일/회전이 있을 때 월드 좌표가 보존되는지."""
    sk, meta = synth.build_skeleton("mixamo", "y_up_z", 1.0, "T", "bone_x")
    an, _ = synth.walk_animation(sk, meta, n_frames=30)
    mesh = make_mesh(sk)
    s = 0.01
    ang = np.radians(35.0)
    qa = Q.from_axis_angle([0.3, 1.0, 0.2], ang)
    qa_xyzw = [float(qa[1]), float(qa[2]), float(qa[3]), float(qa[0])]
    with tempfile.TemporaryDirectory() as d:
        p1, p2 = os.path.join(d, "a.glb"), os.path.join(d, "b.glb")
        write_glb(p1, sk, an, [mesh])

        def fn(js):
            roots = js["scenes"][0]["nodes"]
            js["nodes"].append({"name": "Armature", "scale": [s, s, s], "rotation": qa_xyzw, "translation": [1.0, 2.0, 3.0], "children": [r for r in roots if "mesh" not in js["nodes"][r]]})
            arm = len(js["nodes"]) - 1
            js["scenes"][0]["nodes"] = [arm] + [r for r in roots if "mesh" in js["nodes"][r]]

        _repack(p1, p2, fn)
        m = read_gltf(p2)
        sk2, an2 = m.skeleton, m.animations[0]
        wr1, wp1 = fk(sk, an.rot, an.pos)
        wr2, wp2 = fk(sk2, an2.rot, an2.pos)
        perm = np.array([sk2.index(n) for n in sk.names])
        wr2, wp2 = wr2[:, perm], wp2[:, perm]
        # 기대: A 변환 적용 -> pos' = t + s * R(qa) pos
        exp = np.array([1.0, 2.0, 3.0]) + s * Q.rotate(qa, wp1)
        assert np.allclose(wp2, exp, atol=1e-6), "조상 변환 위치: %g" % np.abs(wp2 - exp).max()
        expq = Q.mul(qa, wr1)
        assert np.all(np.abs(np.sum(wr2 * expq, -1)) > 1 - 1e-6), "조상 변환 회전"
        # 메시: 스키닝된 월드 좌표도 같은 변환
        a = mesh.skin(sk, wr1[9], wp1[9])
        inv = np.argsort(perm)  # 읽은 인덱스 -> 원본 인덱스 순서로 되돌려 스키닝
        wr2r, wp2r = np.empty_like(wr2), np.empty_like(wp2)
        wr2r[:, perm] = wr2
        wp2r[:, perm] = wp2
        b = m.meshes[0].skin(sk2, np.asarray(fk(sk2, an2.rot, an2.pos)[0])[9], np.asarray(fk(sk2, an2.rot, an2.pos)[1])[9])
        expm = np.array([1.0, 2.0, 3.0]) + s * Q.rotate(qa, a)
        assert np.allclose(b, expm, atol=1e-5), "조상 변환 메시"
    print("armature ancestor baking OK")


if __name__ == "__main__":
    test_generator_truth()
    test_bvh_roundtrip()
    test_glb_roundtrip()
    test_armature_ancestor_baking()
    print("test_io ALL OK")
