"""뷰어 계산 검증 + PIL 로 렌더해서 눈으로 확인할 수 있는 PNG 생성."""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge.core.pipeline import Project  # noqa: E402
from motionbridge.ui.viewer_math import COLORS, DRAW_ORDER, OrbitCamera, ViewData, build_scene  # noqa: E402
import test_pipeline as TP  # noqa: E402


def test_camera():
    cam = OrbitCamera()
    cam.target = np.array([0.0, 1.0, 0.0])
    cam.dist = 5.0
    xy, z = cam.project(np.array([cam.target]), 800, 600)
    assert np.allclose(xy[0], [400, 300], atol=1e-6) and abs(z[0] - 5.0) < 1e-9
    # 위쪽 점은 화면 위쪽(작은 y)에
    xy, _ = cam.project(np.array([[0, 2.0, 0], [0, 0.0, 0]]), 800, 600)
    assert xy[0, 1] < 300 < xy[1, 1]
    # 오른쪽 이동 -> 화면 오른쪽
    r, u, f = cam.basis()
    xy, _ = cam.project(np.array([cam.target + r]), 800, 600)
    assert xy[0, 0] > 400
    # 카메라 뒤쪽 선분이 잘려서 NaN 이 없다
    e = cam.eye()
    seg = np.array([[e + (cam.target - e) * 0.5, e - (cam.target - e) * 2.0]])
    out = cam.project_segments(seg, 800, 600)
    assert out.shape == (1, 4) and np.all(np.isfinite(out))
    # 패닝은 목표점을 화면과 평행하게 옮긴다
    t0 = cam.target.copy()
    cam.pan(10, 0, 600)
    assert abs((cam.target - t0) @ f) < 1e-9
    print("camera OK")


def render_png(vd, pr, frame, path, cam=None, size=(640, 520)):
    from PIL import Image, ImageDraw

    cam = cam or OrbitCamera()
    cam.frame(vd.center, vd.height)
    sc = build_scene(vd, pr, frame)
    img = Image.new("RGB", size, (28, 31, 38))
    dr = ImageDraw.Draw(img)
    for role in DRAW_ORDER:
        if role not in sc or len(sc[role]) == 0:
            continue
        col = COLORS[role]
        if role.startswith("seg_"):
            for x0, y0, x1, y1 in cam.project_segments(sc[role], *size):
                dr.line([x0, y0, x1, y1], fill=col, width=2 if role != "seg_grid" else 1)
        else:
            xy, z = cam.project(sc[role], *size)
            rad = {"pt_mesh": 1, "pt_target_joints": 2}.get(role, 5)
            for (x, y), zz in zip(xy, z):
                if zz > 0 and np.isfinite(x):
                    dr.ellipse([x - rad, y - rad, x + rad, y + rad], fill=col)
    img.save(path)
    return sc


def test_scene_and_render():
    with tempfile.TemporaryDirectory() as d:
        src, src2, tgt, _, _ = TP.make_inputs(d)
        pr = Project()
        pr.load_source(src)
        pr.load_target(tgt)
        # 결과 없이도 (바인드 포즈) 그려져야 한다
        vd0 = ViewData.build(pr)
        assert vd0.tgt_pos.shape[0] == 1 and vd0.height > 50
        pr.run()
        vd = ViewData.build(pr)
        assert vd.tgt_pos.shape[0] == pr.result.anim.n_frames == vd.ghost_pos.shape[0]
        sc = build_scene(vd, pr, 10)
        assert "seg_target" in sc and "seg_ghost" in sc and "pt_mesh" in sc and len(sc["seg_grid"]) > 10
        # 타깃 표준 프레임에서 서 있다: 발이 바닥 근처, 머리가 위
        p0 = vd.tgt_pos[0]
        assert abs(vd.ground_y) < 0.05 * vd.height or vd.ground_y < 0
        # 고스트는 같은 스케일: 키 비가 소스 키*k 와 맞는다
        gh = vd.ghost_pos[0]
        assert abs(np.ptp(gh[:, 1]) / np.ptp(vd.tgt_pos[0][:, 1]) - 1) < 0.35
        # 접지 표시가 어떤 프레임에는 있다
        assert any("pt_plant" in build_scene(vd, pr, f) for f in range(vd.n_frames))
        out = os.environ.get("MB_RENDER_DIR")
        if out:
            os.makedirs(out, exist_ok=True)
            for f in (0, 10, 20):
                render_png(vd, pr, f, os.path.join(out, "frame%02d.png" % f))
        # 소스만 있을 때
        pr2 = Project()
        pr2.load_source(src)
        vd2 = ViewData.build(pr2)
        assert vd2.ghost_pos is not None and vd2.tgt_pos is None
        assert "seg_ghost" in build_scene(vd2, pr2, 3)
    print("scene OK")


def test_reduced_parents():
    from motionbridge.ui.viewer_math import reduced_parents

    # 작은 스켈레톤은 그대로
    par = np.array([-1, 0, 1, 2])
    assert np.array_equal(reduced_parents(par, {"a": 1}), par)
    # 큰 스켈레톤: 매핑된 본만 가장 가까운 매핑된 조상으로 연결, 나머지는 -1
    n = 400
    par = np.arange(-1, n - 1)  # 일직선 사슬
    slots = {"s%d" % i: i * 30 for i in range(12)}  # 0,30,60,...
    r = reduced_parents(par, slots)
    for i in range(12):
        j = i * 30
        assert r[j] == (j - 30 if i > 0 else -1)
    assert (r >= 0).sum() == 11 and r[1] == -1 and r[31] == -1
    # 본이 적어도(98개짜리 보스 리그처럼) 매핑 안 된 보조 본(카메라/IK)은 기본으로 숨기고, 켜면 전부 그린다
    n = 40
    par = np.arange(-1, n - 1)
    slots = {"s%d" % i: i for i in range(12)}
    r = reduced_parents(par, slots)
    assert (r >= 0).sum() == 11 and r[20] == -1, "보조 본은 그리지 않음"
    assert np.array_equal(reduced_parents(par, slots, 10 ** 9), par), "보조 본 표시를 켜면 전부"
    print("reduced parents OK")


def _sphere(n_lat=24, n_lon=32, r=0.5, center=(0.0, 1.0, 0.0), color=None, texture=None):
    from motionbridge.core.model import Mesh

    lat = np.linspace(0, np.pi, n_lat + 1)
    lon = np.linspace(0, 2 * np.pi, n_lon, endpoint=False)
    v = [(0, 1, 0)]
    for a in lat[1:-1]:
        for b in lon:
            v.append((np.sin(a) * np.cos(b), np.cos(a), np.sin(a) * np.sin(b)))
    v.append((0, -1, 0))
    v = np.array(v, float)
    f = []
    for i in range(n_lon):
        f.append((0, 1 + i, 1 + (i + 1) % n_lon))
    for k in range(n_lat - 2):
        for i in range(n_lon):
            a, b = 1 + k * n_lon + i, 1 + k * n_lon + (i + 1) % n_lon
            c, d = a + n_lon, b + n_lon
            f += [(a, c, b), (b, c, d)]
    last = len(v) - 1
    for i in range(n_lon):
        f.append((last, 1 + (n_lat - 2) * n_lon + (i + 1) % n_lon, 1 + (n_lat - 2) * n_lon + i))
    uv = np.stack([(np.arctan2(v[:, 2], v[:, 0]) / (2 * np.pi)) % 1.0, 0.5 - np.arcsin(v[:, 1]) / np.pi], axis=1)
    pos = v * r + np.array(center)
    J = np.zeros((len(v), 1), int)
    W = np.ones((len(v), 1))
    return Mesh("sph", pos, np.array(f), J, W, uv, color, texture)


def _tiny_png(rgb=(255, 0, 0)):
    import struct
    import zlib

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + bytes(rgb) * 2 for _ in range(2))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def test_display_mesh_render():
    from motionbridge.ui.viewer_math import DEFAULT_SURFACE_RGB, build_display_mesh, render_surface, wire_segments

    cam = OrbitCamera()
    cam.frame(np.array([0.0, 1.0, 0.0]), 1.2)
    # 큰 메시는 줄어든다
    m = _sphere(60, 80)
    dm = build_display_mesh(m, max_tris=1500, want_edges=True)
    assert len(dm.tris) <= 1500 and dm.n_full_faces == len(m.faces) and dm.orient != 0.0, "닫힌 메시로 판정"
    assert len(dm.sub.vertices) == len(dm.sub.joints) == len(dm.sub.weights), "스킨 정보가 같이 줄어야 함"
    assert dm.edges is not None and wire_segments(dm, dm.sub.vertices).shape == (len(dm.edges), 2, 3)
    assert build_display_mesh(_sphere(2, 3).__class__("e", np.zeros((0, 3)), np.zeros((0, 3), int), np.zeros((0, 1), int), np.zeros((0, 1)))) is None
    # 솔리드: 화면 가운데가 칠해지고 모서리는 비어 있다
    img = render_surface([(dm, dm.sub.vertices)], cam, 200, 200, material=False)
    assert img.shape == (200, 200, 4) and img[100, 100, 3] == 255 and img[2, 2, 3] == 0
    cov = (img[..., 3] > 0).mean()
    assert 0.05 < cov < 0.6, cov
    # 기본 색(머티리얼 없음)은 기본 회청색 계열: 파랑 >= 빨강
    px = img[100, 100, :3].astype(int)
    assert px[2] >= px[0], px
    # 머티리얼 색: 빨강 baseColor -> 머티리얼 모드는 빨갛고, 솔리드는 그대로 회청색
    mr = _sphere(60, 80, color=(1.0, 0.0, 0.0, 1.0))
    dmr = build_display_mesh(mr, max_tris=1500)
    assert dmr.has_material
    a = render_surface([(dmr, dmr.sub.vertices)], cam, 200, 200, material=True)[100, 100, :3].astype(int)
    b = render_surface([(dmr, dmr.sub.vertices)], cam, 200, 200, material=False)[100, 100, :3].astype(int)
    assert a[0] > 60 and a[1] < 10 and a[2] < 10, a
    assert b[2] >= b[0] and b[1] > 40, b
    # 텍스처(2x2 PNG) 디코더를 넣으면 샘플마다 텍스처 색이 쓰인다
    def dec(_bytes):
        t = np.zeros((2, 2, 3), np.uint8)
        t[:, :, 1] = 255  # 초록
        return t

    mt = _sphere(60, 80, color=(1.0, 1.0, 1.0, 1.0), texture=b"x")
    dmt = build_display_mesh(mt, decode=dec, max_tris=1500)
    c = render_surface([(dmt, dmt.sub.vertices)], cam, 200, 200, material=True)[100, 100, :3].astype(int)
    assert c[1] > 60 and c[0] < 10 and c[2] < 10, c
    # 디코더가 실패(None)해도 기본 색으로 계속
    dmn = build_display_mesh(mt, decode=lambda _b: None, max_tris=1500)
    assert dmn.has_material and render_surface([(dmn, dmn.sub.vertices)], cam, 200, 200, material=True) is not None
    v = dm.sub.vertices
    # 입력이 없거나 이미지가 너무 작으면 None
    assert render_surface([], cam, 100, 100, False) is None
    assert render_surface([(dm, v)], cam, 1, 1, False) is None
    # 점 수를 줄여도 구멍 없이 채워진다 (적은 샘플 -> 큰 점 + 메우기)
    dm_sparse = build_display_mesh(m, max_tris=1500, n_samples=8000)
    sp = render_surface([(dm_sparse, dm_sparse.sub.vertices)], cam, 200, 200, False)
    inner = sp[80:120, 80:120, 3]
    assert (inner > 0).mean() > 0.97, (inner > 0).mean()
    print("display mesh render OK")


def test_gltf_material_roundtrip():
    from motionbridge.core.io_gltf import read_gltf, write_glb
    from motionbridge.core.model import Skeleton

    sk = Skeleton(["root"], np.array([-1]), np.zeros((1, 3)), None, "s")
    png = _tiny_png((255, 0, 0))
    m = _sphere(8, 10, color=(0.2, 0.4, 0.6, 1.0), texture=png)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "a.glb")
        write_glb(p, sk, None, [m])
        back = read_gltf(p)
        mm = back.meshes[0]
        assert mm.color is not None and np.allclose(mm.color[:3], (0.2, 0.4, 0.6), atol=1e-3)
        assert mm.texture is not None and mm.texture[:8] == b"\x89PNG\r\n\x1a\n" and mm.texture == png
        assert mm.uv is not None and mm.uv.shape == (len(m.vertices), 2)
        # 머티리얼이 없는 메시는 None 그대로
        p2 = os.path.join(d, "b.glb")
        write_glb(p2, sk, None, [_sphere(8, 10)])
        m2 = read_gltf(p2).meshes[0]
        assert m2.color is None and m2.texture is None
    print("gltf material roundtrip OK")


if __name__ == "__main__":
    test_camera()
    test_reduced_parents()
    test_display_mesh_render()
    test_gltf_material_roundtrip()
    test_scene_and_render()
    print("test_viewer ALL OK")
