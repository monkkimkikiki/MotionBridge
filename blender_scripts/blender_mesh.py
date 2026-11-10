"""Blender 안에서 실행: 메시를 FBX / OBJ / GLB 로 내보낸다. 리깅 있는 것 / 없는 것을 따로 만들 수 있다.

사용: blender -b --python blender_mesh.py -- <입력> <출력폴더> <이름> <옵션JSON>
  입력: .npz (motionbridge.core.psk.to_blender 가 만든 PSK 변환본), .glb/.gltf, .fbx
  옵션: {"formats": ["fbx","obj","glb"], "variants": ["rigged","static"], "axis_forward": "Y", "axis_up": "Z"}
출력: <이름>_rigged.fbx, <이름>_static.fbx ... (OBJ 는 static 만)
"""
import json
import os
import sys


def _filter(op, kwargs):
    try:
        keys = set(op.get_rna_type().properties.keys())
        return {k: v for k, v in kwargs.items() if k in keys}
    except Exception:
        return kwargs


AXIS_OBJ = {"X": "X", "Y": "Y", "Z": "Z", "-X": "NEGATIVE_X", "-Y": "NEGATIVE_Y", "-Z": "NEGATIVE_Z"}


def build_from_npz(path):
    import bpy
    import numpy as np
    from mathutils import Matrix, Vector

    d = np.load(path, allow_pickle=True)
    verts = d["verts"]
    tris = d["tris"]
    wpt = d["wedge_point"]
    wuv = d["wedge_uv"]
    fmat = d["face_mat"]
    mats = [str(m) for m in d["materials"]]
    name = str(d["name"])

    me = bpy.data.meshes.new(name)
    pt_idx = wpt[tris]  # (F,3) 점 번호
    me.from_pydata([tuple(map(float, v)) for v in verts], [], [tuple(map(int, t)) for t in pt_idx])
    me.update()
    mcols = d["material_colors"] if "material_colors" in d.files else None
    for i, m in enumerate(mats):
        mat = bpy.data.materials.new(m)
        if mcols is not None and i < len(mcols):
            # 머티리얼마다 다른 색을 줘서, 텍스처가 없어도 FBX/GLB 에서 재질이 구분되게 한다
            r, g, b = (float(x) for x in mcols[i])
            mat.diffuse_color = (r, g, b, 1.0)
            try:
                mat.use_nodes = True
                bsdf = mat.node_tree.nodes.get("Principled BSDF")
                if bsdf is not None:
                    bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
            except Exception:  # noqa: BLE001
                pass
        me.materials.append(mat)
    if len(me.polygons) == len(fmat):
        me.polygons.foreach_set("material_index", [int(min(x, max(len(mats) - 1, 0))) for x in fmat])
    uv = me.uv_layers.new(name="UVMap")
    loop_w = tris.reshape(-1)  # 루프(삼각형 모서리) 순서대로 웨지 번호
    luv = np.asarray(wuv, dtype=np.float32)[loop_w].copy()
    luv[:, 1] = 1.0 - luv[:, 1]
    uv.data.foreach_set("uv", luv.reshape(-1))
    me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
    me.update()
    if "wedge_normal" in d.files:
        try:
            if hasattr(me, "use_auto_smooth"):
                me.use_auto_smooth = True  # Blender 4.0 이하
            me.normals_split_custom_set([tuple(map(float, n)) for n in d["wedge_normal"][loop_w]])
        except Exception as e:  # noqa: BLE001
            print("MB_WARN 법선을 적용하지 못했습니다: %s" % e)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)

    if "bone_names" in d.files:
        names = [str(n) for n in d["bone_names"]]
        parent = [int(p) for p in d["bone_parent"]]
        world = d["bone_world"]
        arm = bpy.data.armatures.new(name + "_Armature")
        ao = bpy.data.objects.new(name + "_Armature", arm)
        bpy.context.scene.collection.objects.link(ao)
        bpy.context.view_layer.objects.active = ao
        ao.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        heads = [Vector(world[i][:3, 3]) for i in range(len(names))]
        children = [[] for _ in names]
        for i, p in enumerate(parent):
            if p >= 0:
                children[p].append(i)
        ebs = []
        for i, n in enumerate(names):
            eb = arm.edit_bones.new(n)
            eb.head = heads[i]
            if children[i]:
                tail = sum((heads[c] for c in children[i]), Vector()) / len(children[i])
                if (tail - heads[i]).length < 1e-4:
                    tail = heads[i] + Vector((0, 0, 0.02))
            elif parent[i] >= 0 and (heads[i] - heads[parent[i]]).length > 1e-4:
                tail = heads[i] + (heads[i] - heads[parent[i]]).normalized() * 0.03
            else:
                tail = heads[i] + Vector((0, 0, 0.02))
            eb.tail = tail
            ebs.append(eb)
        for i, p in enumerate(parent):
            if p >= 0:
                ebs[i].parent = ebs[p]
        bpy.ops.object.mode_set(mode="OBJECT")
        # 가중치 -> 버텍스 그룹
        groups = [ob.vertex_groups.new(name=n) for n in names]
        w = d["weights"]
        order = np.argsort(w[:, 2], kind="stable")
        bs = w[order, 2].astype(np.int64)
        starts = np.searchsorted(bs, np.arange(len(names) + 1))
        for b in range(len(names)):
            seg = w[order[starts[b]:starts[b + 1]]]
            if len(seg) == 0:
                continue
            vals, inv = np.unique(seg[:, 0].round(4), return_inverse=True)  # 같은 가중치끼리 묶어서 한 번에 넣는다
            o2 = np.argsort(inv, kind="stable")
            parts = np.split(seg[o2, 1].astype(np.int64), np.cumsum(np.bincount(inv))[:-1])
            for val, idx in zip(vals, parts):
                groups[b].add([int(x) for x in idx], float(val), "REPLACE")
        mod = ob.modifiers.new("Armature", "ARMATURE")
        mod.object = ao
        ob.parent = ao
    return name


def import_any(path):
    import bpy

    ext = os.path.splitext(path)[1].lower()
    if ext == ".npz":
        return build_from_npz(path)
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(**_filter(bpy.ops.import_scene.fbx, dict(filepath=path, use_anim=False)))
    else:
        raise RuntimeError("지원하지 않는 형식: " + ext)
    return os.path.splitext(os.path.basename(path))[0]


def _select(objs):
    import bpy

    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    if objs:
        bpy.context.view_layer.objects.active = objs[0]


def _has_textures():
    import bpy

    try:
        return any(getattr(im, "has_data", False) or getattr(im, "packed_file", None) for im in bpy.data.images if getattr(im, "type", "") == "IMAGE")
    except Exception:
        return False


def _export(fmt, path, objs, rigged, opts):
    import bpy

    _select(objs)
    if fmt == "fbx":
        types = {"ARMATURE", "MESH"} if rigged else {"MESH"}
        kw = dict(filepath=path, use_selection=True, object_types=types, add_leaf_bones=False, bake_anim=False,
                  axis_forward=opts.get("axis_forward", "-Z"), axis_up=opts.get("axis_up", "Y"),
                  path_mode="COPY" if _has_textures() else "AUTO", embed_textures=_has_textures(), use_mesh_modifiers=True, mesh_smooth_type="FACE")
        bpy.ops.export_scene.fbx(**_filter(bpy.ops.export_scene.fbx, kw))
    elif fmt == "obj":
        f = AXIS_OBJ.get(opts.get("axis_forward", "-Z"), "NEGATIVE_Z")
        u = AXIS_OBJ.get(opts.get("axis_up", "Y"), "Y")
        if hasattr(bpy.ops.wm, "obj_export"):
            bpy.ops.wm.obj_export(**_filter(bpy.ops.wm.obj_export, dict(filepath=path, export_selected_objects=True,
                                                                         forward_axis=f, up_axis=u)))
        else:
            bpy.ops.export_scene.obj(**_filter(bpy.ops.export_scene.obj, dict(filepath=path, use_selection=True,
                                                                              axis_forward=opts.get("axis_forward", "-Z"),
                                                                              axis_up=opts.get("axis_up", "Y"))))
    elif fmt == "glb":
        bpy.ops.export_scene.gltf(**_filter(bpy.ops.export_scene.gltf, dict(filepath=path, use_selection=True, export_format="GLB")))
    else:
        raise RuntimeError("알 수 없는 형식: " + fmt)


def make_static(meshes, armatures):
    """스킨을 걷어낸다: 아마추어 모디파이어/버텍스 그룹 제거, 부모 해제(월드 위치 유지), 아마추어 삭제."""
    import bpy

    for m in meshes:
        mw = m.matrix_world.copy()
        for mod in [x for x in m.modifiers if x.type == "ARMATURE"]:
            m.modifiers.remove(mod)
        m.vertex_groups.clear()
        m.parent = None
        m.matrix_world = mw
    for a in armatures:
        bpy.data.objects.remove(a, do_unlink=True)


def main(argv=None):
    import bpy

    argv = list(sys.argv if argv is None else argv)
    args = argv[argv.index("--") + 1:] if "--" in argv else argv[1:]
    src, out_dir, base = args[0], args[1], args[2]
    opts = json.loads(args[3]) if len(args) > 3 else {}
    formats = opts.get("formats") or ["fbx"]
    variants = opts.get("variants") or ["rigged", "static"]
    os.makedirs(out_dir, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    import_any(src)
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if not meshes:
        raise RuntimeError("메시가 없습니다: " + src)
    made = []
    if "rigged" in variants:
        if not armatures:
            print("MB_WARN 이 메시에는 스켈레톤이 없어 '리깅 포함' 판은 만들지 못했습니다")
        else:
            for fmt in formats:
                if fmt == "obj":
                    continue  # OBJ 는 리깅을 담을 수 없다
                path = os.path.join(out_dir, "%s_rigged.%s" % (base, fmt))
                _export(fmt, path, meshes + armatures, True, opts)
                made.append(path)
    if "static" in variants:
        make_static(meshes, armatures)
        for fmt in formats:
            path = os.path.join(out_dir, "%s_static.%s" % (base, fmt))
            _export(fmt, path, meshes, False, opts)
            made.append(path)
    print("MB_RESULT " + json.dumps({"files": made}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print("MB_ERROR %s" % e)
        raise
