"""PSK/PSKX 읽기, 좌표 변환, 본 켤레 자동 판별, OBJ 쓰기, 메시 변환 호출(가짜 Blender)."""
import json
import os
import struct
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import bridge, meshconv, osopen, psk  # noqa: E402


def chunk(cid, size, items):
    return cid.encode().ljust(20, b"\0") + struct.pack("<iii", 1999801, size, len(items)) + b"".join(items)


def cube(c, s):
    """중심 c, 반지름 s 인 육면체의 점 8개와 바깥을 향하는 삼각형 12개 (점 번호는 0..7)."""
    pts = [(c[0] + sx * s, c[1] + sy * s, c[2] + sz * s) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    # 각 면의 바깥 방향이 맞도록 검사하며 만든다
    tris = []
    for q in quads:
        for t in ((q[0], q[1], q[2]), (q[0], q[2], q[3])):
            a, b, d = (np.array(pts[i]) for i in t)
            n = np.cross(b - a, d - a)
            mid = (a + b + d) / 3 - np.array(c)
            tris.append(t if np.dot(n, mid) > 0 else (t[0], t[2], t[1]))
    return pts, tris


def quat_z(deg):
    h = np.radians(deg) / 2
    return (0.0, 0.0, float(np.sin(h)), float(np.cos(h)))


def write_pskx(path, wide=False, reverse=False, conj_root=False, bones=True):
    # 본: 0 root(0,0,0) 월드 회전 z+90, 1 자식 로컬 (50,0,0) -> 월드 (0,50,0), 2 자식 로컬 (50,0,0) -> 월드 (0,100,0)
    true_root = quat_z(90)
    file_root = (-true_root[0], -true_root[1], -true_root[2], true_root[3]) if conj_root else true_root
    world = [(0, 0, 0), (0, 50, 0), (0, 100, 0)]
    pts, wedge_pt, tris, wts = [], [], [], []
    for bi, c in enumerate(world):
        cp, ct = cube(c, 8)
        base = len(pts)
        pts += cp
        for t in ct:
            tt = (t[0] + base, t[1] + base, t[2] + base)
            tris.append(tt[::-1] if reverse else tt)
        wts += [(1.0, base + k, bi) for k in range(8)]
    pnts = [struct.pack("<3f", *p) for p in pts]
    # 웨지 = 점마다 하나 (UV 는 점 번호로 구분)
    if wide:
        wedges = [struct.pack("<I2f2Bh", i, i * 0.01, 0.5, 0, 0, 0) for i in range(len(pts))]
        vtx = chunk("VTXW3200", 16, wedges)
    else:
        wedges = [struct.pack("<Hh2f2Bh", i, 0, i * 0.01, 0.5, 0, 0, 0) for i in range(len(pts))]
        vtx = chunk("VTXW0000", 16, wedges)
    if wide:
        faces = [struct.pack("<3IBBI", *t, 0, 0, 1) for t in tris]
        fc = chunk("FACE3200", 18, faces)
    else:
        faces = [struct.pack("<3HBBI", *t, 0, 0, 1) for t in tris]
        fc = chunk("FACE0000", 12, faces)
    matt = chunk("MATT0000", 88, [struct.pack("<64s6i", b"M_Hero", 0, 0, 0, 0, 0, 0)])
    out = [chunk("ACTRHEAD", 0, []), chunk("PNTS0000", 12, pnts), vtx, fc, matt]
    if bones:
        def bone(name, par, q, p, nch):
            return struct.pack("<64sIii4f3f4f", name.encode(), 0, nch, par, *q, *p, 0, 0, 0, 0)
        out.append(chunk("REFSKELT", 120, [bone("root", -1, file_root, (0, 0, 0), 1), bone("spine", 0, (0, 0, 0, 1), (50, 0, 0), 1),
                                           bone("head", 1, (0, 0, 0, 1), (50, 0, 0), 0)]))
        out.append(chunk("RAWWEIGHTS", 12, [struct.pack("<fii", w, p, b) for w, p, b in wts]))
    with open(path, "wb") as f:
        f.write(b"".join(out))
    return pts, tris


def outward_ok(payload):
    v, t, wp = payload["verts"], payload["tris"], payload["wedge_point"]
    p = wp[t]
    a, b, c = v[p[:, 0]], v[p[:, 1]], v[p[:, 2]]
    n = np.cross(b - a, c - a)
    # 큐브마다 따로 검사 (점 8개씩)
    ok = 0
    for i in range(len(t)):
        cube_id = p[i, 0] // 8
        ctr = v[cube_id * 8:cube_id * 8 + 8].mean(0)
        ok += np.dot(n[i], (a[i] + b[i] + c[i]) / 3 - ctr) > 0
    return ok == len(t)


def test_read_variants():
    with tempfile.TemporaryDirectory() as d:
        for wide in (False, True):
            f = os.path.join(d, "SK_Hero%s.pskx" % ("W" if wide else ""))
            pts, tris = write_pskx(f, wide=wide)
            m = psk.read_psk(f)
            assert m.name.startswith("SK_Hero") and len(m.points) == 24 and len(m.faces) == 36 and m.materials == ["M_Hero"]
            assert np.allclose(m.points, np.array(pts), atol=1e-4) and m.wedge_uv.shape == (24, 2)
            assert m.bone_names == ["root", "spine", "head"] and m.bone_parent == [-1, 0, 1] and m.has_skeleton
            assert len(m.weights) == 24
        # 스켈레톤 없는 파일
        f = os.path.join(d, "static.psk")
        write_pskx(f, bones=False)
        assert not psk.read_psk(f).has_skeleton
        # 깨진 파일
        bad = os.path.join(d, "bad.psk")
        open(bad, "wb").write(b"hello world" * 10)
        try:
            psk.read_psk(bad)
        except psk.PskError:
            pass
        else:
            raise AssertionError("깨진 파일을 받아들임")
    print("psk read OK")


def test_wedge_index_32bit():
    """정점이 65536개를 넘는 메시: VTXW0000 이어도 점 번호가 32비트 (실제 SK_Dwarf 에서 발견된 문제)."""
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "big.pskx")
        wed = [struct.pack("<I2f2Bh", 70000, 0.0, 0.0, 0, 0, 0), struct.pack("<I2f2Bh", 3, 0.5, 0.5, 0, 0, 0)]
        open(f, "wb").write(b"".join([chunk("ACTRHEAD", 0, []), chunk("PNTS0000", 12, [struct.pack("<3f", 0, 0, 0)] * 3),
                                      chunk("VTXW0000", 16, wed), chunk("FACE3200", 18, [struct.pack("<3IBBI", 0, 1, 1, 0, 0, 1)]),
                                      chunk("VTXNORMS", 12, [struct.pack("<3f", 0, 0, 1)] * 2)]))
        m = psk.read_psk(f)
        assert list(m.wedge_point) == [70000, 3], m.wedge_point
        assert m.wedge_normal is not None and m.wedge_normal.shape == (2, 3)
    print("psk 32-bit wedge OK")


def test_winding_and_axes():
    with tempfile.TemporaryDirectory() as d:
        for rev in (False, True):
            f = os.path.join(d, "w%d.pskx" % rev)
            write_pskx(f, reverse=rev)
            m = psk.read_psk(f)
            p = psk.to_blender(m)
            assert outward_ok(p), "삼각형이 안쪽을 향함 (reverse=%s)" % rev
            # 단위: (0,50,0) cm -> (0, 0.5, 0) m (축은 그대로)
            assert np.allclose(p["verts"][8:16].mean(0), [0.0, 0.5, 0.0], atol=1e-4), p["verts"][8:16].mean(0)
    print("psk winding/axes OK")


def test_skeleton_convention():
    with tempfile.TemporaryDirectory() as d:
        for conj in (False, True):
            f = os.path.join(d, "c%d.pskx" % conj)
            write_pskx(f, conj_root=conj)
            m = psk.read_psk(f)
            rot, pos, how = psk.solve_skeleton(m)
            assert np.allclose(pos, [[0, 0, 0], [0, 50, 0], [0, 100, 0]], atol=1e-3), (conj, pos, how)
            p = psk.to_blender(m)
            w = p["bone_world"]
            assert np.allclose(w[2][:3, 3], [0.0, 1.0, 0.0], atol=1e-4)  # (0,100,0)cm -> (0,1,0)m
            assert list(p["bone_parent"]) == [-1, 0, 1] and p["weights"].shape == (24, 3)
            assert np.allclose(np.linalg.det(w[1][:3, :3]), 1.0, atol=1e-4), "본 회전이 오른손계가 아님"
        assert "bone_names" not in psk.to_blender(m, keep_bones=False)
    print("psk skeleton convention OK")


def test_obj():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "SK_Hero.pskx")
        write_pskx(f)
        out = psk.write_obj(psk.read_psk(f), os.path.join(d, "SK_Hero_static.obj"))
        txt = open(out).read()
        assert txt.count("\nv ") == 24 and txt.count("\nf ") == 36 and "usemtl M_Hero" in txt
        assert os.path.isfile(os.path.join(d, "SK_Hero_static.mtl"))
    print("psk obj OK")


def test_convert_calls():
    calls = []
    real_find, real_run = bridge.find_blender, bridge.run_script

    class R:
        warnings = ["경고"]
        result = {}

    def fake_run(script, args, blender=None, timeout=900.0):
        calls.append((script, args))
        opts = json.loads(args[3])
        r = R()
        out = []
        for v in opts["variants"]:
            for fm in opts["formats"]:
                if fm == "obj" and v == "rigged":
                    continue
                p = os.path.join(args[1], "%s_%s.%s" % (args[2], v, fm))
                open(p, "wb").write(b"x")
                out.append(p)
        r.result = {"files": out}
        return r

    try:
        bridge.run_script = fake_run
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "SK_Hero.pskx")
            write_pskx(src)
            out = os.path.join(d, "out")
            logs = []
            # Blender 없음: FBX 는 오류, OBJ(static) 는 Blender 없이 성공
            bridge.find_blender = lambda *a, **k: None
            try:
                meshconv.convert_mesh(src, out, ["fbx"], ["rigged", "static"], "max")
            except meshconv.MeshConvError as e:
                assert "Blender" in str(e)
            else:
                raise AssertionError("Blender 없이 FBX 가 됨")
            got = meshconv.convert_mesh(src, out, ["obj"], ["static"], "max")
            assert [os.path.basename(x) for x in got] == ["SK_Hero_static.obj"] and not calls
            try:
                meshconv.convert_mesh(src, out, ["obj"], ["rigged"], "max")
            except meshconv.MeshConvError as e:
                assert "리깅" in str(e)
            else:
                raise AssertionError
            # Blender 있음: 둘 다 + FBX/OBJ
            bridge.find_blender = lambda *a, **k: "/fake/blender"
            got = meshconv.convert_mesh(src, out, ["fbx", "obj"], ["rigged", "static"], "max", on_log=logs.append)
            names = sorted(os.path.basename(x) for x in got)
            assert names == ["SK_Hero_rigged.fbx", "SK_Hero_static.fbx", "SK_Hero_static.obj"], names
            script, args = calls[-1]
            assert script == "blender_mesh.py" and args[0].endswith(".npz") and args[2] == "SK_Hero"
            o = json.loads(args[3])
            assert o["axis_forward"] == "Y" and o["axis_up"] == "Z" and o["variants"] == ["rigged", "static"]
            assert logs == ["경고"]
            # 임시 npz 는 지워진다
            assert not os.path.exists(args[0])
            # 스켈레톤 없는 PSK: 리깅 포함 요청은 안내하고 static 만
            st = os.path.join(d, "SK_Static.psk")
            write_pskx(st, bones=False)
            logs.clear()
            got = meshconv.convert_mesh(st, out, ["fbx"], ["rigged", "static"], "maya", on_log=logs.append)
            assert [os.path.basename(x) for x in got] == ["SK_Static_static.fbx"] and any("스켈레톤" in x for x in logs)
            assert json.loads(calls[-1][1][3])["axis_forward"] == "-Z"
            # GLB 는 Blender 에 그대로 넘긴다
            g = os.path.join(d, "SK_G.glb")
            open(g, "wb").write(b"glb")
            meshconv.convert_mesh(g, out, ["fbx"], ["static"], "blender")
            assert calls[-1][1][0].endswith("SK_G.glb")
            for bad in [dict(formats=[]), dict(variants=[])]:
                kw = dict(formats=["fbx"], variants=["static"]); kw.update(bad)
                try:
                    meshconv.convert_mesh(src, out, kw["formats"], kw["variants"], "max")
                except meshconv.MeshConvError:
                    continue
                raise AssertionError(bad)
    finally:
        bridge.find_blender, bridge.run_script = real_find, real_run
    print("mesh convert calls OK")


def test_osopen():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "a.fbx")
        open(f, "w").write("x")
        assert osopen.folder_of(f) == d and osopen.folder_of(d) == d
        assert osopen.open_command(f, "win32")[:2] == ["explorer", "/select,"]
        assert osopen.open_command(d, "win32") == []
        assert osopen.open_command(f, "darwin") == ["open", "-R", f] and osopen.open_command(d, "darwin") == ["open", d]
        assert osopen.open_command(f, "linux") == ["xdg-open", d]
        launched = []
        real = osopen.subprocess.Popen
        osopen.subprocess.Popen = lambda cmd, *a, **k: launched.append(cmd)
        try:
            assert osopen.open_in_explorer(os.path.join(d, "no", "such")) in (True, False)  # 없는 경로는 상위 폴더로, 예외 없이
        finally:
            osopen.subprocess.Popen = real
    print("osopen OK")


if __name__ == "__main__":
    test_read_variants()
    test_wedge_index_32bit()
    test_winding_and_axes()
    test_skeleton_convention()
    test_obj()
    test_convert_calls()
    test_osopen()
