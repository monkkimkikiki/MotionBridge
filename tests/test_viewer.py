"""뷰어 계산 검증 + PIL 로 렌더해서 눈으로 확인할 수 있는 PNG 생성."""
import os
import sys
import tempfile
import types

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge.core import quat as Q  # noqa: E402
from motionbridge.core.pipeline import Project  # noqa: E402
from motionbridge.ui.viewer_math import COLORS, DRAW_ORDER, OrbitCamera, ViewData, build_scene  # noqa: E402
import test_pipeline as TP  # noqa: E402
import synth  # noqa: E402


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


def test_drag_placement():
    from motionbridge.ui.viewer_math import drag_placement

    cam = OrbitCamera()
    cam.target = np.array([0.0, 0.0, 0.0])
    cam.dist = 5.0
    W, H = 800, 600
    # 바닥 위 한 점을 투영했다가 되돌려 맞힌다
    pt = np.array([0.7, 0.0, -0.4])
    xy, _ = cam.project(np.array([pt]), W, H)
    g = cam.ground_point(xy[0, 0], xy[0, 1], W, H, 0.0)
    assert np.allclose(g, pt, atol=1e-6), g
    # 수평선 위쪽(하늘)을 누르면 바닥을 못 맞힌다
    assert cam.ground_point(W / 2, 0.0, W, H, 0.0) is None
    # 이동: 바닥 점이 p0 -> p1 로 가도록 끈 만큼 dx,dz 가 나온다
    a, b = np.array([0.2, 0.0, 0.1]), np.array([0.9, 0.0, -0.5])
    pa, _ = cam.project(np.array([a]), W, H)
    pb, _ = cam.project(np.array([b]), W, H)
    dx, dz, dy = drag_placement(cam, "move", pa[0], pb[0], W, H, 0.0, (0.0, 0.0))
    assert abs(dx - 0.7) < 1e-6 and abs(dz + 0.6) < 1e-6 and dy == 0.0
    # 회전: pivot 주위로 +Z 에서 +X 쪽으로 가면 (Y축 양의 회전: z->x) +각도
    a, b = np.array([0.0, 0.0, 1.0]), np.array([1.0, 0.0, 0.0])
    pa, _ = cam.project(np.array([a]), W, H)
    pb, _ = cam.project(np.array([b]), W, H)
    dx, dz, dy = drag_placement(cam, "rotate", pa[0], pb[0], W, H, 0.0, (0.0, 0.0))
    assert dx == 0.0 and dz == 0.0 and abs(dy - 90.0) < 1e-4, dy
    # 같은 각도만큼 retarget 쪽 yaw 와 방향이 일치: (0,0,1) 을 Y축 +90° 돌리면 (1,0,0)
    qy = Q.from_axis_angle(np.array([0.0, 1.0, 0.0]), np.radians(90.0))
    assert np.allclose(Q.rotate(qy, np.array([0.0, 0.0, 1.0])), [1.0, 0.0, 0.0], atol=1e-9)
    print("drag placement OK")


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
        # 고스트가 타깃 옆으로 멀리 떨어져 있지 않다 (골반이 서로 겹침)
        assert np.linalg.norm(gh[:, [0, 2]].mean(axis=0) - vd.tgt_pos[0][:, [0, 2]].mean(axis=0)) < 0.6 * vd.height
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


def test_material_colors():
    """머티리얼 보기: 텍스처가 없어도 재질마다 다른 색이 나와야 하고, 실제 색/텍스처가 있으면 그것이 우선한다."""
    from motionbridge.core.matcolor import is_placeholder_color, material_color
    from motionbridge.ui.viewer_math import DEFAULT_SURFACE_RGB, build_display_mesh, render_surface

    # 이름 색: 고정, 서로 다름, 너무 어둡거나 탁하지 않음
    names = ["MI_Dwarf_Torso_01", "MI_Dwarf_Head_01", "MI_Dwarf_Leg_01", "MI_Dwarf_Arms_01", "MI_Dwarf_Belt_01", "MI_Dwarf_Cloth_01"]
    cols = [material_color(n, i) for i, n in enumerate(names)]  # 번호 기준: 겹치지 않음
    assert cols == [material_color(n, i) for i, n in enumerate(names)] and material_color("x") == material_color("x")
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            assert max(abs(a - b) for a, b in zip(cols[i], cols[j])) > 0.08, (names[i], names[j], cols[i], cols[j])
    assert all(0.3 < max(c) <= 1.0 for c in cols)
    assert is_placeholder_color(None) and is_placeholder_color((0.8, 0.8, 0.8, 1.0)) and is_placeholder_color((1, 1, 1, 1))
    assert not is_placeholder_color((0.8, 0.2, 0.2, 1.0))

    cam = OrbitCamera()
    cam.frame(np.array([0.0, 1.0, 0.0]), 1.2)

    def center(m, material=True):
        dm = build_display_mesh(m, max_tris=1500)
        img = render_surface([(dm, dm.sub.vertices)], cam, 200, 200, material=material)
        return dm, img[100, 100, :3].astype(int)

    a = _sphere(30, 40); a.material = names[0]; a.color = (0.8, 0.8, 0.8, 1.0)  # Blender 기본 회색 = 색 없음
    b = _sphere(30, 40); b.material = names[1]
    def center(m, material=True, idx=None):
        dm = build_display_mesh(m, max_tris=1500, color_index=idx)
        img = render_surface([(dm, dm.sub.vertices)], cam, 200, 200, material=material)
        return dm, img[100, 100, :3].astype(int)

    dma, ca = center(a, idx=0)
    dmb, cb = center(b, idx=1)
    assert not dma.real_material and not dmb.real_material
    assert np.abs(ca - cb).max() > 15, "머티리얼이 다른데 색이 같음 (솔리드와 구분 안 됨)"
    _, sa = center(a, material=False, idx=0)
    assert tuple(sa) != tuple(ca), "머티리얼 보기가 솔리드와 같아 보임"
    # 실제 색이 있으면 그 색
    c = _sphere(30, 40, color=(0.9, 0.1, 0.1, 1.0)); c.material = names[0]
    dmc, cc = center(c)
    assert dmc.real_material and cc[0] > 150 and cc[1] < 90, cc
    # 실제 텍스처가 있으면 이름 색이 곱해지지 않는다 (빨강 텍스처는 빨강 그대로)
    from motionbridge.ui.viewer_math import build_display_mesh as bdm
    t = _sphere(30, 40, texture=_tiny_png((255, 0, 0))); t.material = names[1]
    dec = lambda _b: np.tile(np.array([255, 0, 0, 255], np.uint8), (2, 2, 1))
    dmt = bdm(t, decode=dec, max_tris=1500)
    img = render_surface([(dmt, dmt.sub.vertices)], cam, 200, 200, material=True)[100, 100, :3].astype(int)
    assert dmt.real_material and img[0] > 200 and img[1] < 40 and img[2] < 40, img
    print("material colors OK")


def test_heavy_mesh_performance():
    """무거운 메시: 점 표시는 간추린 정점만 스키닝하고, 품질은 삼각형 수로 자동 선택, 솎아 그리기는 점 수를 줄인다."""
    from motionbridge.core.model import Mesh as M
    from motionbridge.ui.viewer_math import QUALITY, build_display_mesh, pick_quality, render_surface

    assert pick_quality(10000) == "high" and pick_quality(80000) == "mid" and pick_quality(428066) == "low"
    assert pick_quality(428066, "high") == "high" and pick_quality(10, "low") == "low"
    assert QUALITY["low"][0] < QUALITY["mid"][0] < QUALITY["high"][0] and QUALITY["low"][1] < QUALITY["high"][1]

    # 점 표시: 정점 20만 개 메시를 매 프레임 통째로 스키닝하지 않는다
    sk, meta = synth.build_skeleton("mixamo", "y_up_z", 1.0, "T", "aligned")
    big = _sphere(400, 500)  # 약 20만 정점
    big.joints = np.zeros((len(big.vertices), 1), int)
    pr = Project()
    pr.tgt = types.SimpleNamespace(meshes=[big], skeleton=sk)
    vd = ViewData()
    bwr, bwp = sk.bind_world()
    vd.tgt_wr, vd.tgt_wp, vd.Ft = bwr[None], bwp[None], np.array([1.0, 0, 0, 0])
    sizes = []
    real = M.skin
    M.skin = lambda self, *a, **k: (sizes.append(len(self.vertices)), real(self, *a, **k))[1]
    try:
        pts = vd.mesh_points(pr, 0, max_points=8000)
        pts2 = vd.mesh_points(pr, 0, max_points=8000)
    finally:
        M.skin = real
    assert len(big.vertices) > 100000 and sizes and max(sizes) <= 8000 and len(pts) == sizes[0] and np.allclose(pts, pts2), (sizes, len(big.vertices))
    assert len(vd._pt_cache) == 1, "간추린 정점은 한 번만 만든다"

    # 솎아 그리기
    cam = OrbitCamera()
    cam.frame(np.array([0.0, 1.0, 0.0]), 1.2)
    m = _sphere(60, 80)
    dm = build_display_mesh(m, max_tris=1500)
    full = render_surface([(dm, dm.sub.vertices)], cam, 200, 200, False)
    thin = render_surface([(dm, dm.sub.vertices)], cam, 200, 200, False, sample_step=3)
    assert thin is not None and thin[100, 100, 3] == 255
    assert abs(float((thin[..., 3] > 0).mean()) - float((full[..., 3] > 0).mean())) < 0.05, "솎아도 모양(면적)은 비슷해야 함"
    print("heavy mesh performance OK")


def test_overlay_names():
    from types import SimpleNamespace as NS
    from motionbridge.ui.viewer_math import overlay_names

    an = NS(name="Attack_01")
    src = NS(path="/x/y/Attack_01.psa", animations=[an])
    tgt = NS(path="/m/Hero.fbx", meshes=[NS(name="Body"), NS(name="Cloak")])
    out = overlay_names(NS(src=src, tgt=tgt, clip=0))
    assert out == ["애니메이션: Attack_01.psa", "메쉬: Hero.fbx  [Body, Cloak]"], out
    src2 = NS(path="/x/pack.psa", animations=[NS(name="Run"), NS(name="Walk")])
    out = overlay_names(NS(src=src2, tgt=None, clip=1))
    assert out == ["애니메이션: pack.psa  [Walk]"], out
    assert overlay_names(NS(src=None, tgt=None, clip=0)) == []
    print("overlay names OK")


if __name__ == "__main__":
    test_overlay_names()
    test_material_colors()
    test_heavy_mesh_performance()
    test_camera()
    test_drag_placement()
    test_reduced_parents()
    test_display_mesh_render()
    test_gltf_material_roundtrip()
    test_scene_and_render()
    print("test_viewer ALL OK")
