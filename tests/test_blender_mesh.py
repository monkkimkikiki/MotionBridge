"""blender_mesh.py 의 흐름을 가짜 bpy 로 시험한다 (실제 Blender 호출은 못 한다: 파일 순서/이름/인자 확인용)."""
import importlib.util
import json
import os
import sys
import tempfile
import types
from unittest import mock

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def load_script():
    spec = importlib.util.spec_from_file_location("blender_mesh", os.path.join(ROOT, "blender_scripts", "blender_mesh.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class Obj:
    def __init__(self, typ, name):
        self.type, self.name = typ, name
        self.modifiers = [types.SimpleNamespace(type="ARMATURE")] if typ == "MESH" else []
        self.vertex_groups = mock.MagicMock()
        self.parent = None
        self.matrix_world = "M"
        self.selected = False

    def select_set(self, v):
        self.selected = v


class Mods(list):
    def remove(self, m):
        super().remove(m)


def run(variants, formats, with_arm=True, obj_new=True):
    m = load_script()
    mesh = Obj("MESH", "body")
    mesh.modifiers = Mods(mesh.modifiers)
    mesh.matrix_world = mock.MagicMock()
    objs = [mesh] + ([Obj("ARMATURE", "arm")] if with_arm else [])
    exported = []
    ops = mock.MagicMock()
    ops.export_scene.fbx = lambda **kw: exported.append(("fbx", kw))
    ops.export_scene.gltf = lambda **kw: exported.append(("glb", kw))
    ops.wm.obj_export = lambda **kw: exported.append(("obj", kw))
    if not obj_new:
        del ops.wm.obj_export
        ops.export_scene.obj = lambda **kw: exported.append(("obj", kw))
    for o in (ops.export_scene.fbx, ops.export_scene.gltf):
        pass
    data = types.SimpleNamespace(objects=types.SimpleNamespace(
        __iter__=None))
    removed = []

    class Objects(list):
        def remove(self, o, do_unlink=True):
            removed.append(o.name)
            super().remove(o)
    objects = Objects(objs)
    bpy = types.SimpleNamespace(ops=ops, data=types.SimpleNamespace(objects=objects), context=mock.MagicMock())
    with tempfile.TemporaryDirectory() as d, mock.patch.dict(sys.modules, {"bpy": bpy}):
        m.import_any = lambda path: "x"
        m._filter = lambda op, kw: kw
        argv = ["blender", "--", os.path.join(d, "in.npz"), os.path.join(d, "out"), "SK_Hero",
                json.dumps({"formats": formats, "variants": variants, "axis_forward": "Y", "axis_up": "Z"})]
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            m.main(argv)
        res = json.loads([l for l in buf.getvalue().splitlines() if l.startswith("MB_RESULT ")][0][len("MB_RESULT "):])
        res["files"] = [os.path.relpath(f, d) for f in res["files"]]
    return res, exported, removed, mesh, buf.getvalue()


def test_flow():
    res, ex, removed, mesh, out = run(["rigged", "static"], ["fbx", "obj"])
    assert res["files"] == [os.path.join("out", "SK_Hero_rigged.fbx"), os.path.join("out", "SK_Hero_static.fbx"), os.path.join("out", "SK_Hero_static.obj")], res
    kinds = [(k, kw.get("object_types")) for k, kw in ex]
    assert kinds[0] == ("fbx", {"ARMATURE", "MESH"}) and kinds[1] == ("fbx", {"MESH"}) and kinds[2][0] == "obj", kinds
    assert ex[0][1]["axis_forward"] == "Y" and ex[0][1]["axis_up"] == "Z" and ex[0][1]["use_selection"]
    # 리깅 없는 판을 만들 때 스킨이 걷힌다
    assert removed == ["arm"] and mesh.modifiers == [] and mesh.parent is None
    mesh.vertex_groups.clear.assert_called()
    print("blender_mesh flow OK")


def test_only_static_and_glb():
    res, ex, removed, mesh, out = run(["static"], ["glb"])
    assert res["files"] == [os.path.join("out", "SK_Hero_static.glb")] and ex[0][0] == "glb" and ex[0][1]["export_format"] == "GLB"
    print("blender_mesh static glb OK")


def test_no_armature():
    res, ex, removed, mesh, out = run(["rigged", "static"], ["fbx"], with_arm=False)
    assert "MB_WARN" in out and res["files"] == [os.path.join("out", "SK_Hero_static.fbx")], (out, res)
    print("blender_mesh no armature OK")


def test_old_obj_exporter():
    res, ex, removed, mesh, out = run(["static"], ["obj"], obj_new=False)
    assert ex[0][0] == "obj" and ex[0][1]["axis_forward"] == "Y"
    print("blender_mesh old obj OK")


if __name__ == "__main__":
    test_flow()
    test_only_static_and_glb()
    test_no_armature()
    test_old_obj_exporter()
