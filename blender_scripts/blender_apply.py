"""Blender 안에서 실행하는 스크립트: 모델을 열고, 리타깃된 월드 포즈를 본 포즈(키프레임)로 굽고, FBX 로 내보낸다.

사용: blender -b --python blender_apply.py -- <모델 파일> <payload.npz> <출력.fbx> <옵션 JSON 문자열>

payload.npz (motionbridge.core.export.write_blender_payload 가 만든다)
  names, wr (T,J,4 월드 회전 wxyz), wp (T,J,3 월드 위치), bind_wr, bind_wp, fps, clip
  -> 앱이 불러온 타깃 모델의 프레임(= Blender 가 내보낸 glTF 프레임)

앱과 Blender 의 좌표계가 달라도 되도록, 같은 이름의 관절/본 머리 위치로 상사 변환(Umeyama)을 구해 정합한다.
본마다 Blender 가 다시 잡은 본 축(roll)과 앱의 본 축 사이의 회전 E_b 는 휴지 포즈에서 구한다.
수학 부분은 bpy 없이 numpy 만 쓰므로 단독으로 테스트할 수 있다.
"""
import json
import os
import sys

import numpy as np


# ----------------------------------------------------------------------------- 수학 (bpy 불필요)
def quat_to_mat(q):
    q = np.asarray(q, dtype=float)
    q = q / np.maximum(np.linalg.norm(q, axis=-1, keepdims=True), 1e-12)
    w, x, y, z = q[..., 0], q[..., 1], q[..., 2], q[..., 3]
    m = np.empty(q.shape[:-1] + (3, 3))
    m[..., 0, 0] = 1 - 2 * (y * y + z * z)
    m[..., 0, 1] = 2 * (x * y - z * w)
    m[..., 0, 2] = 2 * (x * z + y * w)
    m[..., 1, 0] = 2 * (x * y + z * w)
    m[..., 1, 1] = 1 - 2 * (x * x + z * z)
    m[..., 1, 2] = 2 * (y * z - x * w)
    m[..., 2, 0] = 2 * (x * z - y * w)
    m[..., 2, 1] = 2 * (y * z + x * w)
    m[..., 2, 2] = 1 - 2 * (x * x + y * y)
    return m


def mat_to_quat(m):
    """3x3(스케일이 약간 있어도 됨) -> 정규화된 wxyz. 배치 지원."""
    m = np.asarray(m, dtype=float)
    shp = m.shape[:-2]
    m = m.reshape(-1, 3, 3)
    # 열 정규화로 스케일 제거
    n = np.linalg.norm(m, axis=1, keepdims=True)
    m = m / np.maximum(n, 1e-12)
    out = np.empty((m.shape[0], 4))
    for i in range(m.shape[0]):
        a = m[i]
        tr = a[0, 0] + a[1, 1] + a[2, 2]
        if tr > 0:
            s = np.sqrt(tr + 1.0) * 2
            q = [0.25 * s, (a[2, 1] - a[1, 2]) / s, (a[0, 2] - a[2, 0]) / s, (a[1, 0] - a[0, 1]) / s]
        elif a[0, 0] > a[1, 1] and a[0, 0] > a[2, 2]:
            s = np.sqrt(1.0 + a[0, 0] - a[1, 1] - a[2, 2]) * 2
            q = [(a[2, 1] - a[1, 2]) / s, 0.25 * s, (a[0, 1] + a[1, 0]) / s, (a[0, 2] + a[2, 0]) / s]
        elif a[1, 1] > a[2, 2]:
            s = np.sqrt(1.0 + a[1, 1] - a[0, 0] - a[2, 2]) * 2
            q = [(a[0, 2] - a[2, 0]) / s, (a[0, 1] + a[1, 0]) / s, 0.25 * s, (a[1, 2] + a[2, 1]) / s]
        else:
            s = np.sqrt(1.0 + a[2, 2] - a[0, 0] - a[1, 1]) * 2
            q = [(a[1, 0] - a[0, 1]) / s, (a[0, 2] + a[2, 0]) / s, (a[1, 2] + a[2, 1]) / s, 0.25 * s]
        q = np.array(q)
        out[i] = q / np.linalg.norm(q)
    return out.reshape(shp + (4,))


def umeyama(X, Y):
    """Y ~= s * R @ X + t 를 만족하는 (s, R, t). X, Y: (N,3)."""
    X = np.asarray(X, float)
    Y = np.asarray(Y, float)
    mx, my = X.mean(0), Y.mean(0)
    Xc, Yc = X - mx, Y - my
    cov = Yc.T @ Xc / len(X)
    U, S, Vt = np.linalg.svd(cov)
    d = np.sign(np.linalg.det(U) * np.linalg.det(Vt))
    D = np.diag([1.0, 1.0, d])
    R = U @ D @ Vt
    var_x = (Xc ** 2).sum() / len(X)
    s = float(np.trace(np.diag(S) @ D) / max(var_x, 1e-18))
    t = my - s * R @ mx
    return s, R, t


def decompose_obj(M):
    """객체 월드 행렬 4x4 -> (정규화된 회전 R, 균일 스케일 s, 이동 t)."""
    M = np.asarray(M, float)
    A = M[:3, :3]
    sc = np.linalg.norm(A, axis=0)
    s = float(np.mean(sc)) if np.all(sc > 1e-12) else 1.0
    return A / np.where(sc < 1e-12, 1.0, sc)[None, :], s, M[:3, 3].copy()


def compose(R, p):
    """R (...,3,3), p (...,3) -> (...,4,4)."""
    R = np.asarray(R, float)
    p = np.asarray(p, float)
    M = np.zeros(R.shape[:-2] + (4, 4))
    M[..., :3, :3] = R
    M[..., :3, 3] = p
    M[..., 3, 3] = 1.0
    return M


def armature_targets(wr, wp, bind_wr, bind_wp, J_idx, rest_world_R, G, obj):
    """앱 쪽 월드 포즈 -> 매칭된 본들의 '암 공간' 목표 (A_rot (T,K,3,3), A_pos (T,K,3)).

    J_idx: 매칭된 앱 관절 인덱스 (K,), rest_world_R: 같은 순서의 Blender 휴지 월드 회전 (K,3,3).
    G = (s, R_G, t_G): 앱 월드 -> Blender 월드 상사 변환. obj = (R_obj, s_obj, t_obj): 암 객체 월드 변환.
    """
    s, RG, tG = G
    R_obj, s_obj, t_obj = obj
    bind_R = quat_to_mat(bind_wr[J_idx])  # (K,3,3)
    E = np.einsum("kij,kjl->kil", np.transpose(RG @ bind_R, (0, 2, 1)), rest_world_R)  # (K,3,3)
    Rw = np.einsum("ij,tkjl->tkil", RG, quat_to_mat(wr[:, J_idx]))
    Rw = np.einsum("tkij,kjl->tkil", Rw, E)
    pw = np.einsum("ij,tkj->tki", RG, wp[:, J_idx]) * s + tG
    A_rot = np.einsum("ji,tkjl->tkil", R_obj, Rw)  # R_obj^T @ Rw
    A_pos = np.einsum("ji,tkj->tki", R_obj, pw - t_obj) / s_obj
    return A_rot, A_pos


def solve_pose_basis(rest, parent, matched, A_rot, A_pos):
    """각 본의 포즈 기저(location, rotation_quaternion) 계산.

    rest (B,4,4) 암 공간 휴지 행렬(부모가 자식보다 앞), parent (B,), matched (B,) bool,
    A_rot (T,B,3,3) / A_pos (T,B,3): 매칭된 본에서만 의미가 있는 목표.
    Blender 포즈 평가:  A[b] = A[p] @ inv(rest[p]) @ rest[b] @ basis[b]   (루트: rest[b] @ basis[b])
    매칭되지 않은 본은 기저 항등(부모를 따라감).
    """
    T = A_rot.shape[0]
    B = rest.shape[0]
    A = np.zeros((T, B, 4, 4))
    loc = np.zeros((T, B, 3))
    quat = np.zeros((T, B, 4))
    quat[..., 0] = 1.0
    inv_rest = np.linalg.inv(rest)
    for b in range(B):
        p = parent[b]
        if matched[b]:
            A_b = compose(A_rot[:, b], A_pos[:, b])
        elif p < 0:
            A_b = np.broadcast_to(rest[b], (T, 4, 4)).copy()
        else:
            A_b = A[:, p] @ inv_rest[p] @ rest[b]
        A[:, b] = A_b
        if matched[b]:
            if p < 0:
                basis = inv_rest[b] @ A_b
            else:
                basis = inv_rest[b] @ rest[p] @ np.linalg.inv(A[:, p]) @ A_b
            loc[:, b] = basis[:, :3, 3]
            quat[:, b] = mat_to_quat(basis[:, :3, :3])
    return loc, quat


def eval_pose(rest, parent, loc, quat):
    """solve_pose_basis 의 역: 기저로부터 암 공간 행렬 (T,B,4,4) 복원 (테스트/검증용)."""
    T, B = loc.shape[0], rest.shape[0]
    A = np.zeros((T, B, 4, 4))
    inv_rest = np.linalg.inv(rest)
    for b in range(B):
        basis = compose(quat_to_mat(quat[:, b]), loc[:, b])
        p = parent[b]
        A[:, b] = (rest[b] @ basis) if p < 0 else (A[:, p] @ inv_rest[p] @ rest[b] @ basis)
    return A


# ----------------------------------------------------------------------------- bpy 쪽
def _filter(op, kwargs):
    """이 Blender 버전에서 지원하는 옵션만 남긴다."""
    try:
        keys = set(op.get_rna_type().properties.keys())
        return {k: v for k, v in kwargs.items() if k in keys}
    except Exception:
        return kwargs


def import_model(bpy, path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".fbx":
        bpy.ops.import_scene.fbx(**_filter(bpy.ops.import_scene.fbx, dict(filepath=path, use_anim=True)))
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".blend":
        bpy.ops.wm.open_mainfile(filepath=path)
    elif ext == ".dae":
        bpy.ops.wm.collada_import(filepath=path)
    else:
        raise RuntimeError("지원하지 않는 모델 형식: " + ext)


def main(argv=None):
    import bpy  # noqa: F401  (Blender 안에서만 존재)

    argv = list(sys.argv if argv is None else argv)
    args = argv[argv.index("--") + 1:] if "--" in argv else argv[1:]
    model_path, npz_path, out_path = args[0], args[1], args[2]
    opts = json.loads(args[3]) if len(args) > 3 else {}

    if os.path.splitext(model_path)[1].lower() != ".blend":
        bpy.ops.wm.read_factory_settings(use_empty=True)
    import_model(bpy, model_path)

    data = np.load(npz_path, allow_pickle=True)
    names = [str(n) for n in data["names"]]
    wr, wp = data["wr"], data["wp"]
    bind_wr, bind_wp = data["bind_wr"], data["bind_wp"]
    fps = float(data["fps"])
    clip = str(data["clip"])
    T = wr.shape[0]

    # 가장 많은 이름이 일치하는 아마추어 선택
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if not armatures:
        raise RuntimeError("모델에 아마추어(스켈레톤)가 없습니다.")
    nameset = set(names)
    arm = max(armatures, key=lambda o: sum(1 for b in o.data.bones if b.name in nameset))
    bones = list(arm.data.bones)
    bones.sort(key=lambda b: len(b.parent_recursive) if hasattr(b, "parent_recursive") else _depth(b))
    bidx = {b.name: i for i, b in enumerate(bones)}
    B = len(bones)
    parent = np.array([(bidx[b.parent.name] if b.parent else -1) for b in bones])
    rest = np.array([np.array(b.matrix_local) for b in bones], dtype=float).reshape(B, 4, 4)
    M_obj = np.array(arm.matrix_world, dtype=float).reshape(4, 4)
    R_obj, s_obj, t_obj = decompose_obj(M_obj)

    j_of = {n: i for i, n in enumerate(names)}
    matched = np.array([b.name in j_of for b in bones])
    if matched.sum() < 4:
        raise RuntimeError("이름이 일치하는 본이 너무 적습니다 (%d). 앱에서 불러온 모델과 같은 파일을 쓰세요." % matched.sum())
    mb = np.flatnonzero(matched)
    J_idx = np.array([j_of[bones[b].name] for b in mb])

    # 앱 월드 -> Blender 월드 정합
    rest_world = np.einsum("ij,bjk->bik", M_obj, rest)  # (B,4,4)
    head_world = rest_world[mb][:, :3, 3]
    s, RG, tG = umeyama(bind_wp[J_idx], head_world)
    resid = np.linalg.norm(s * (bind_wp[J_idx] @ RG.T) + tG - head_world, axis=1)
    height = float(np.ptp(head_world, axis=0).max())
    if resid.max() > 0.02 * max(height, 1e-9):
        print("MB_WARN: 좌표 정합 오차가 큽니다 (%.3f, 키 %.3f). 본 위치가 앱과 다른 모델일 수 있습니다." % (resid.max(), height))
    rest_world_R = rest_world[mb][:, :3, :3] / max(s_obj, 1e-12)
    A_rot, A_pos = armature_targets(wr, wp, bind_wr, bind_wp, J_idx, rest_world_R, (s, RG, tG), (R_obj, s_obj, t_obj))
    # 전체 본 배열로 확장
    Arot_full = np.zeros((T, B, 3, 3))
    Apos_full = np.zeros((T, B, 3))
    Arot_full[:, mb] = A_rot
    Apos_full[:, mb] = A_pos
    loc, quat = solve_pose_basis(rest, parent, matched, Arot_full, Apos_full)

    # 키프레임 굽기
    scene = bpy.context.scene
    scene.render.fps = int(round(fps))
    scene.render.fps_base = float(scene.render.fps) / fps if fps > 0 else 1.0
    scene.frame_start = 1
    scene.frame_end = T
    arm.animation_data_clear()
    arm.animation_data_create()
    act = bpy.data.actions.new(clip or "MotionBridge")
    arm.animation_data.action = act
    size = max(height, 1e-9)
    for b in mb:
        pb = arm.pose.bones[bones[b].name]
        pb.rotation_mode = "QUATERNION"
        use_loc = float(np.abs(loc[:, b]).max()) > 1e-6 * size
        for f in range(T):
            pb.rotation_quaternion = tuple(float(x) for x in quat[f, b])
            pb.keyframe_insert(data_path="rotation_quaternion", frame=f + 1)
            if use_loc:
                pb.location = tuple(float(x) for x in loc[f, b])
                pb.keyframe_insert(data_path="location", frame=f + 1)
    bpy.context.view_layer.update()

    # FBX 내보내기
    fkw = dict(
        filepath=out_path,
        use_selection=False,
        object_types={"ARMATURE", "MESH"},
        add_leaf_bones=False,
        bake_anim=True,
        bake_anim_use_all_actions=False,
        bake_anim_use_nla_strips=False,
        bake_anim_force_startend_keying=True,
        bake_anim_simplify_factor=float(opts.get("simplify", 0.0)),
        axis_forward=opts.get("axis_forward", "-Z"),
        axis_up=opts.get("axis_up", "Y"),
        path_mode="COPY",
        embed_textures=True,
    )
    bpy.ops.export_scene.fbx(**_filter(bpy.ops.export_scene.fbx, fkw))
    print("MB_RESULT " + json.dumps({"out": out_path, "bones_matched": int(matched.sum()), "bones_total": B, "frames": int(T), "fit_residual": float(resid.max())}))


def _depth(b):
    d = 0
    while b.parent is not None:
        b = b.parent
        d += 1
    return d


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # Blender 종료 코드로 실패를 알린다
        print("MB_ERROR " + str(e))
        sys.exit(1)
