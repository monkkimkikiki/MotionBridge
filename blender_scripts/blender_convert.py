"""Blender 안에서 실행: 모델/모션 파일(FBX, blend, glTF, dae)을 GLB 로 변환한다.

사용: blender -b --python blender_convert.py -- <입력> <출력.glb>
FBX 에 들어 있는 여러 클립(Action)은 각각 별도 애니메이션으로 내보낸다.
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


def main(argv=None):
    import bpy

    argv = list(sys.argv if argv is None else argv)
    args = argv[argv.index("--") + 1:] if "--" in argv else argv[1:]
    src, dst = args[0], args[1]
    ext = os.path.splitext(src)[1].lower()
    if ext != ".blend":
        bpy.ops.wm.read_factory_settings(use_empty=True)
    if ext == ".fbx":
        bpy.ops.import_scene.fbx(**_filter(bpy.ops.import_scene.fbx, dict(filepath=src, use_anim=True)))
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=src)
    elif ext == ".blend":
        bpy.ops.wm.open_mainfile(filepath=src)
    elif ext == ".dae":
        bpy.ops.wm.collada_import(filepath=src)
    else:
        raise RuntimeError("지원하지 않는 형식: " + ext)

    # 액션이 아마추어에 연결돼 있지 않으면 첫 번째 아마추어에 붙여서 내보내지게 한다
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if arms and bpy.data.actions:
        a0 = arms[0]
        if a0.animation_data is None:
            a0.animation_data_create()
        if a0.animation_data.action is None:
            a0.animation_data.action = bpy.data.actions[0]
    kw = dict(
        filepath=dst,
        export_format="GLB",
        export_animations=True,
        export_skins=True,
        export_yup=True,
        export_apply=False,
        export_force_sampling=True,
        export_animation_mode="ACTIONS",
        export_texcoords=True,
        export_normals=True,
        export_image_format="AUTO",
    )
    bpy.ops.export_scene.gltf(**_filter(bpy.ops.export_scene.gltf, kw))
    print("MB_RESULT " + json.dumps({"out": dst, "actions": [a.name for a in bpy.data.actions], "armatures": [a.name for a in arms]}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("MB_ERROR " + str(e))
        sys.exit(1)
