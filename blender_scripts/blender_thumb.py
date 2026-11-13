"""GLB 를 참조 썸네일 PNG 로 렌더한다 (생김새 검증용).

사용: blender -b --factory-startup -P blender_thumb.py -- <glb> <out_png> [크기]
      blender -b --factory-startup -P blender_thumb.py -- --jobs <목록파일> [크기]
      (목록파일: 한 줄에 '<glb>\t<out_png>' — Blender 를 한 번만 띄워 여러 장을 연달아 렌더)
카메라는 모델 전체가 들어오게 3/4 각도에서 자동으로 잡고, 회색 배경에 해 조명 하나.
"""
import math
import sys

import bpy
from mathutils import Vector


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:]
    if argv and argv[0] == "--jobs":
        size = int(argv[2]) if len(argv) > 2 else 512
        with open(argv[1], "r", encoding="utf-8") as f:
            jobs = [ln.rstrip("\n").split("\t") for ln in f if "\t" in ln]
        for glb, out in jobs:
            try:
                render_one(glb, out, size)
            except Exception as e:  # noqa: BLE001 - 한 장 실패해도 다음 장은 계속
                print("THUMB FAIL:", glb, e)
        return
    render_one(argv[0], argv[1], int(argv[2]) if len(argv) > 2 else 512)


def render_one(glb: str, out: str, size: int) -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=glb)

    scene = bpy.context.scene
    meshes = [o for o in scene.objects if o.type == "MESH"]
    if not meshes:
        raise SystemExit("MESH 없음: " + glb)

    # 전체 바운딩 박스
    lo = Vector((1e18, 1e18, 1e18))
    hi = Vector((-1e18, -1e18, -1e18))
    for o in meshes:
        for c in o.bound_box:
            p = o.matrix_world @ Vector(c)
            lo = Vector(map(min, lo, p))
            hi = Vector(map(max, hi, p))
    center = (lo + hi) / 2
    diag = max((hi - lo).length, 0.01)

    # 카메라: 앞 3/4 위에서 전체가 들어오게
    cam_data = bpy.data.cameras.new("cam")
    cam_data.lens = 50.0
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    direction = Vector((1.0, -1.2, 0.55)).normalized()
    cam.location = center + direction * (diag * 1.25)
    look = center - cam.location
    cam.rotation_euler = look.to_track_quat("-Z", "Y").to_euler()
    cam_data.clip_end = max(diag * 10.0, 100.0)

    # 조명: 해 + 하늘빛 느낌의 월드 색
    sun_data = bpy.data.lights.new("sun", type="SUN")
    sun_data.energy = 3.0
    sun = bpy.data.objects.new("sun", sun_data)
    scene.collection.objects.link(sun)
    sun.rotation_euler = (math.radians(50), 0.0, math.radians(30))
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs[0].default_value = (0.28, 0.29, 0.31, 1.0)
        bg.inputs[1].default_value = 1.0
    scene.world = world

    for engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH"):
        try:
            scene.render.engine = engine
            break
        except TypeError:
            continue
    scene.render.resolution_x = scene.render.resolution_y = size
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = out
    bpy.ops.render.render(write_still=True)


main()
