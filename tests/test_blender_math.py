"""Blender 적용 스크립트의 수학 검증 (bpy 없이) + 가짜 bpy 로 main() 흐름 점검."""
import importlib.util
import os
import sys
import types

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from motionbridge.core import quat as Q  # noqa: E402
from motionbridge.core.model import fk  # noqa: E402
import synth  # noqa: E402

spec = importlib.util.spec_from_file_location("blender_apply", os.path.join(HERE, "..", "blender_scripts", "blender_apply.py"))
BA = importlib.util.module_from_spec(spec)
spec.loader.exec_module(BA)


def rand_rot(rng):
    return Q.to_matrix(Q.normalize(rng.normal(size=4)))


def test_umeyama_and_quat():
    rng = np.random.default_rng(3)
    X = rng.normal(size=(30, 3))
    R = rand_rot(rng)
    s, t = 0.37, np.array([1.0, -2.0, 0.5])
    Y = s * X @ R.T + t
    s2, R2, t2 = BA.umeyama(X, Y)
    assert abs(s2 - s) < 1e-9 and np.allclose(R2, R, atol=1e-9) and np.allclose(t2, t, atol=1e-9)
    for _ in range(200):
        m = rand_rot(rng)
        q = BA.mat_to_quat(m)
        assert np.allclose(BA.quat_to_mat(q), m, atol=1e-9)
    # 스케일이 섞인 행렬
    m = rand_rot(rng) * 1.0
    assert np.allclose(BA.quat_to_mat(BA.mat_to_quat(m * 3.0)), m, atol=1e-9)
    print("umeyama/quat OK")


def make_fake_blender_armature(skel, rng, G, obj):
    """앱 스켈레톤으로부터 '본 축이 제각각으로 재배치된' Blender 아마추어 휴지 행렬을 만든다."""
    s, RG, tG = G
    R_obj, s_obj, t_obj = obj
    bwr, bwp = skel.bind_world()
    J = skel.n
    E = np.stack([rand_rot(rng) for _ in range(J)])  # 본마다 임의의 roll/축 재배치
    Rw = np.einsum("ij,bjk->bik", RG, Q.to_matrix(bwr)) @ E  # Blender 월드 휴지 회전
    pw = (bwp @ RG.T) * s + tG
    rest_world = BA.compose(Rw, pw)
    M_obj = BA.compose(R_obj * s_obj, t_obj)
    # 암 공간 휴지: inv(M_obj) @ rest_world, 회전 정규화
    rest = np.linalg.inv(M_obj) @ rest_world
    rest[:, :3, :3] = rest[:, :3, :3] * s_obj  # inv(M_obj)가 이미 1/s 를 곱했으므로 되돌려 직교 행렬로 (Blender 의 matrix_local 처럼)
    assert np.allclose(rest[:, :3, :3] @ rest[:, :3, :3].transpose(0, 2, 1), np.eye(3), atol=1e-9)
    return rest, M_obj, E


def test_pose_math():
    rng = np.random.default_rng(5)
    sk, meta = synth.build_skeleton("ue", "y_up_z", 1.0, "A", "bone_x")
    an, _ = synth.walk_animation(sk, meta, n_frames=24)
    wr, wp = fk(sk, an.rot, an.pos)
    bwr, bwp = sk.bind_world()
    # 임의의 Blender 월드: Z-up 변환 + 스케일 + 이동, 아마추어 객체는 0.01 스케일 + 회전
    qG = Q.from_axis_angle([1, 0, 0], np.radians(90.0))
    G = (1.0, Q.to_matrix(qG), np.array([0.3, -0.2, 0.1]))
    qo = Q.from_axis_angle([0.2, 1.0, 0.3], 0.6)
    obj = (Q.to_matrix(qo), 0.01, np.array([5.0, 6.0, 7.0]))
    rest, M_obj, E = make_fake_blender_armature(sk, rng, G, obj)
    J = sk.n
    # (1) 바인드 포즈 애니메이션 -> 기저 항등
    wr0 = np.repeat(bwr[None], 3, axis=0)
    wp0 = np.repeat(bwp[None], 3, axis=0)
    J_idx = np.arange(J)
    rest_world_R = (np.einsum("ij,bjk->bik", M_obj, rest))[:, :3, :3] / obj[1]
    A_rot, A_pos = BA.armature_targets(wr0, wp0, bwr, bwp, J_idx, rest_world_R, G, obj)
    loc, quat = BA.solve_pose_basis(rest, sk.parents, np.ones(J, bool), A_rot, A_pos)
    assert np.abs(loc).max() < 1e-9, "바인드에서 location 이 0 이 아님 %g" % np.abs(loc).max()
    assert np.all(np.abs(quat[..., 0]) > 1 - 1e-9), "바인드에서 회전이 항등이 아님"
    # (2) 걷기: 평가 결과가 목표와 같고, 월드로 되돌리면 앱 월드 포즈와 같은 변환
    A_rot, A_pos = BA.armature_targets(wr, wp, bwr, bwp, J_idx, rest_world_R, G, obj)
    loc, quat = BA.solve_pose_basis(rest, sk.parents, np.ones(J, bool), A_rot, A_pos)
    A_eval = BA.eval_pose(rest, sk.parents, loc, quat)
    A_tgt = BA.compose(A_rot, A_pos)
    assert np.allclose(A_eval, A_tgt, atol=1e-7), "포즈 평가가 목표와 다름 %g" % np.abs(A_eval - A_tgt).max()
    # 일관된 FK 이므로 루트가 아닌 본의 location 은 거의 0
    # (이동하는 관절 — 예: UE 의 pelvis — 은 location 이 있는 것이 정상)
    moving = np.abs(an.pos - sk.offsets[None]).max(axis=(0, 2)) > 1e-6
    still = (sk.parents >= 0) & ~moving
    assert np.abs(loc[:, still]).max() < 1e-6, "움직이지 않는 관절인데 location 이 큼 %g" % np.abs(loc[:, still]).max()
    assert np.abs(loc[:, moving & (sk.parents >= 0)]).max() > 1e-4, "이동하는 관절의 location 이 비어 있음"
    # 월드 복원: Blender 월드 회전 = R_G * R_app * E, 위치 = s*R_G*p + t
    Rw_eval = np.einsum("ij,tbjk->tbik", obj[0], A_eval[..., :3, :3])
    Rw_exp = np.einsum("ij,tbjk->tbik", G[1], Q.to_matrix(wr)) @ E[None]
    assert np.allclose(Rw_eval, Rw_exp, atol=1e-7)
    pw_eval = np.einsum("ij,tbj->tbi", obj[0], A_eval[..., :3, 3]) * obj[1] + obj[2]
    pw_exp = np.einsum("ij,tbj->tbi", G[1], wp) * G[0] + G[2]
    assert np.allclose(pw_eval, pw_exp, atol=1e-7)
    # (3) 일부 본만 매칭: 매칭 안 된 본은 부모를 따라간다 (기저 항등)
    matched = np.ones(J, bool)
    cut = [sk.index("ball_l"), sk.index("middle_01_l")]
    matched[cut] = False
    loc2, quat2 = BA.solve_pose_basis(rest, sk.parents, matched, A_rot, A_pos)
    for c in cut:
        assert np.abs(loc2[:, c]).max() < 1e-12 and np.all(quat2[:, c, 0] > 1 - 1e-12)
    print("pose math OK")


# ------------------------------------------------------------------ 가짜 bpy 로 main() 흐름 점검
class _Mat(np.ndarray):
    pass


class FakeBone:
    def __init__(self, name, parent, mat):
        self.name, self.parent, self.matrix_local = name, parent, mat


class FakePoseBone:
    def __init__(self, name):
        self.name = name
        self.rotation_mode = "XYZ"
        self.rotation_quaternion = (1, 0, 0, 0)
        self.location = (0, 0, 0)
        self.keys = []

    def keyframe_insert(self, data_path, frame):
        self.keys.append((data_path, frame))


def run_fake_main(tmpdir, sk, an, rest, M_obj):
    bpy = types.ModuleType("bpy")
    bones, by_name = [], {}
    for j in range(sk.n):
        p = by_name.get(sk.names[sk.parents[j]]) if sk.parents[j] >= 0 else None
        b = FakeBone(sk.names[j], p, rest[j])
        bones.append(b)
        by_name[b.name] = b
    arm = types.SimpleNamespace(
        type="ARMATURE",
        data=types.SimpleNamespace(bones=bones),
        matrix_world=M_obj,
        pose=types.SimpleNamespace(bones={b.name: FakePoseBone(b.name) for b in bones}),
        animation_data=None,
    )
    arm.animation_data_clear = lambda: setattr(arm, "animation_data", None)
    arm.animation_data_create = lambda: setattr(arm, "animation_data", types.SimpleNamespace(action=None))
    calls = []
    ops = types.SimpleNamespace(
        wm=types.SimpleNamespace(read_factory_settings=lambda **k: calls.append("factory"), open_mainfile=lambda **k: None),
        import_scene=types.SimpleNamespace(fbx=lambda **k: calls.append(("import_fbx", k)), gltf=lambda **k: None),
        export_scene=types.SimpleNamespace(fbx=lambda **k: calls.append(("export_fbx", k))),
    )
    scene = types.SimpleNamespace(render=types.SimpleNamespace(fps=24, fps_base=1.0), frame_start=0, frame_end=0)
    bpy.ops = ops
    bpy.data = types.SimpleNamespace(objects=[arm], actions=types.SimpleNamespace(new=lambda n: types.SimpleNamespace(name=n)))
    bpy.context = types.SimpleNamespace(scene=scene, view_layer=types.SimpleNamespace(update=lambda: None))
    sys.modules["bpy"] = bpy
    try:
        wr, wp = fk(sk, an.rot, an.pos)
        bwr, bwp = sk.bind_world()
        npz = os.path.join(tmpdir, "p.npz")
        np.savez(npz, names=np.array(sk.names, dtype=object), wr=wr, wp=wp, bind_wr=bwr, bind_wp=bwp, fps=np.array(30.0), clip=np.array("walk", dtype=object))
        BA.main(["x", "--", "model.fbx", npz, os.path.join(tmpdir, "o.fbx"), '{"axis_forward": "Y", "axis_up": "Z"}'])
    finally:
        del sys.modules["bpy"]
    return arm, scene, calls


def test_fake_main():
    import tempfile

    rng = np.random.default_rng(9)
    sk, meta = synth.build_skeleton("mixamo", "y_up_z", 1.0, "T", "bone_x")
    an, _ = synth.walk_animation(sk, meta, n_frames=12)
    qG = Q.from_axis_angle([1, 0, 0], np.radians(90.0))
    G = (1.0, Q.to_matrix(qG), np.zeros(3))
    obj = (np.eye(3), 1.0, np.zeros(3))
    rest, M_obj, E = make_fake_blender_armature(sk, rng, G, obj)
    with tempfile.TemporaryDirectory() as d:
        arm, scene, calls = run_fake_main(d, sk, an, rest, M_obj)
    assert scene.frame_end == 12 and scene.render.fps == 30
    kinds = [c if isinstance(c, str) else c[0] for c in calls]
    assert kinds == ["factory", "import_fbx", "export_fbx"], kinds
    exp = [c for c in calls if not isinstance(c, str) and c[0] == "export_fbx"][0][1]
    assert exp["axis_forward"] == "Y" and exp["axis_up"] == "Z" and exp["bake_anim"] is True
    pb = arm.pose.bones[sk.names[sk.index("mixamorig:Hips")]]
    assert pb.rotation_mode == "QUATERNION"
    assert sum(1 for k in pb.keys if k[0] == "rotation_quaternion") == 12
    assert sum(1 for k in pb.keys if k[0] == "location") == 12, "골반은 location 키가 있어야 함"
    spine = arm.pose.bones["mixamorig:Spine1"]
    assert sum(1 for k in spine.keys if k[0] == "location") == 0, "움직이지 않는 location 은 키를 만들지 않음"
    print("fake bpy main OK")


if __name__ == "__main__":
    test_umeyama_and_quat()
    test_pose_math()
    test_fake_main()
    print("test_blender_math ALL OK")
