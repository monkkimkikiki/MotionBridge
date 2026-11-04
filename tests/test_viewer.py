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


if __name__ == "__main__":
    test_camera()
    test_scene_and_render()
    print("test_viewer ALL OK")
