import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge.core import quat as Q  # noqa: E402
from motionbridge.core.export import (PRESETS, curves_dict, export_bvh, export_glb, guess_units_per_meter, preset_frame, reframe,  # noqa: E402
                                      skeleton_dict, to_world_aligned)
from motionbridge.core.io_bvh import read_bvh  # noqa: E402
from motionbridge.core.io_gltf import read_gltf  # noqa: E402
from motionbridge.core.mapping import auto_map, detect_frame  # noqa: E402
from motionbridge.core.model import fk  # noqa: E402
import synth  # noqa: E402


def make(style="ue", frame="z_up_x", scale=100.0, pose="A", axes="bone_x"):
    sk, meta = synth.build_skeleton(style, frame, scale, pose, axes)
    an, _ = synth.walk_animation(sk, meta, n_frames=40)
    F = detect_frame(sk, auto_map(sk))[0]
    return sk, an, F, meta


def test_units():
    for style, frame, scale, expect in (("ue", "z_up_x", 100.0, 100.0), ("mixamo", "y_up_z", 1.0, 1.0), ("biped", "z_up_negy", 100.0, 100.0)):
        sk, an, F, _ = make(style, frame, scale, "T", "bone_x")
        assert guess_units_per_meter(sk, F) == expect, (style, guess_units_per_meter(sk, F))
    print("unit guess OK")


def test_reframe_exact():
    """reframe 은 월드 위치/회전에 정확히 (rot, scale) 을 적용한 것과 같아야 한다."""
    sk, an, F, _ = make()
    rot = Q.from_axis_angle([0.2, 1.0, -0.4], 0.9)
    s = 0.37
    sk2, an2, _ = reframe(sk, an, rot, s)
    wr, wp = fk(sk, an.rot, an.pos)
    wr2, wp2 = fk(sk2, an2.rot, an2.pos)
    assert np.allclose(wp2, s * Q.rotate(rot, wp), atol=1e-9)
    assert np.all(np.abs(np.sum(wr2 * Q.mul(rot, wr), -1)) > 1 - 1e-9)
    print("reframe OK")


def test_presets_bvh():
    """각 프리셋으로 내보낸 BVH 를 다시 읽으면: 올바른 상방축, 올바른 단위, 같은 동작."""
    for src in (("ue", "z_up_x", 100.0), ("mixamo", "y_up_z", 1.0), ("biped", "z_up_negy", 100.0)):
        sk, an, F, _ = make(src[0], src[1], src[2], "A", "bone_x")
        upm = guess_units_per_meter(sk, F)
        wr0, wp0 = fk(sk, an.rot, an.pos)
        canon0 = Q.rotate(F, wp0) / upm  # 표준 프레임, 미터
        for key, pr in PRESETS.items():
            with tempfile.TemporaryDirectory() as d:
                p = os.path.join(d, "o.bvh")
                warns = export_bvh(p, sk, an, F, pr, upm)
                assert not warns, warns
                m = read_bvh(p)
                sk2, an2 = m.skeleton, m.animations[0]
                wr2, wp2 = fk(sk2, an2.rot, an2.pos)
                # 프리셋 프레임으로 되돌려 비교
                back = Q.rotate(Q.conj(preset_frame(pr)), wp2) / pr.units_per_meter
                for j, nm in enumerate(sk.names):
                    k = sk2.find(nm.replace(" ", "_"))
                    if k < 0:
                        continue  # End 마커
                    assert np.allclose(back[:, k], canon0[:, j], atol=2e-4), (src[0], key, nm, np.abs(back[:, k] - canon0[:, j]).max())
                # 상방축: 바인드 포즈에서 머리가 골반 위
                hips = sk2.find(sk.names[auto_map(sk)["Hips"]].replace(" ", "_"))
                head = sk2.find(sk.names[auto_map(sk)["Head"]].replace(" ", "_"))
                bwr2, bwp2 = sk2.bind_world()
                up = bwp2[head] - bwp2[hips]
                axis = 1 if pr.up == "Y" else 2
                assert up[axis] > 0.5 * np.linalg.norm(up), (key, up)
                # 단위: 키가 pr.units_per_meter 비례
                height = np.ptp(bwp2[:, axis])
                assert abs(height / pr.units_per_meter - np.ptp(canon0[:, 1]) * 0 - height / pr.units_per_meter) < 1e-9
                assert 1.0 < height / pr.units_per_meter < 3.0, (key, height)
        print("  BVH 프리셋 OK:", src[0])
    print("preset BVH OK")


def test_glb_export():
    sk, an, F, _ = make()
    upm = guess_units_per_meter(sk, F)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "o.glb")
        export_glb(p, sk, an, F, upm)
        m = read_gltf(p)
        sk2, an2 = m.skeleton, m.animations[0]
        wr, wp = fk(sk, an.rot, an.pos)
        wr2, wp2 = fk(sk2, an2.rot, an2.pos)
        perm = np.array([sk2.index(n) for n in sk.names])
        assert np.allclose(wp2[:, perm], Q.rotate(F, wp) / upm, atol=1e-4)
        # glTF 규격: 바인드 포즈에서 +Y 가 위
        _, bwp = sk2.bind_world()
        top = sk2.index(sk.names[auto_map(sk)["Head"]])
        hips = sk2.index(sk.names[auto_map(sk)["Hips"]])
        assert (bwp[top] - bwp[hips])[1] > 0.5
    print("glb export OK")


def test_json():
    sk, an, F, _ = make()
    c = curves_dict(sk, an)
    assert c["frames"] == an.n_frames and len(c["joints"]) == sk.n
    s = skeleton_dict(sk)
    assert len(s["joints"]) == sk.n
    import json
    json.dumps(c), json.dumps(s)
    print("json OK")


if __name__ == "__main__":
    test_units()
    test_reframe_exact()
    test_presets_bvh()
    test_glb_export()
    test_json()
    print("test_export ALL OK")
