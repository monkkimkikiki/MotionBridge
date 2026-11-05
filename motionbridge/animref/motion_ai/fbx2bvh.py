"""Blender 안에서 FBX(외부 서비스가 만든 모션)를 BVH 로 바꾼다.
사용: blender --background --python fbx2bvh.py -- 입력.fbx 출력.bvh
(첫 번째 뼈대 오브젝트의 애니메이션 구간을 그대로 내보낸다. 결과 BVH 는 AnimRef 가 다시 읽는다.)"""
import sys

import bpy


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    src, dst = argv[0], argv[1]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=src)
    arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    if not arms:
        print("ANIMREF_ERROR FBX 안에 뼈대(Armature)가 없어요.")
        sys.exit(2)
    arm = max(arms, key=lambda o: len(o.data.bones))
    act = arm.animation_data.action if arm.animation_data else None
    if act is None:
        print("ANIMREF_ERROR FBX 안에 애니메이션이 없어요.")
        sys.exit(3)
    f0, f1 = int(act.frame_range[0]), int(act.frame_range[1])
    bpy.context.scene.frame_start, bpy.context.scene.frame_end = f0, f1
    for o in bpy.context.scene.objects:
        o.select_set(False)
    arm.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.export_anim.bvh(filepath=dst, frame_start=f0, frame_end=f1, rotate_mode="ZYX", root_transform_only=False)
    print("ANIMREF_OK", dst, f0, f1, bpy.context.scene.render.fps)


main()
