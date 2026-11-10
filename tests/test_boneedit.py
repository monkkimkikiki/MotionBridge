"""본 편집: 위치/크기 보정(리타겟 엔진)과 뷰포트 기즈모 계산."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge.core import quat as Q  # noqa: E402
from motionbridge.core.retarget import RetargetSettings  # noqa: E402
from motionbridge.ui import gizmo_math as G  # noqa: E402
from motionbridge.ui.viewer_math import OrbitCamera  # noqa: E402
from test_retarget import COMBOS, IK_TGT, canon, setup  # noqa: E402


def run(st):
    sc = COMBOS[0][0]
    return setup(sc, IK_TGT, st=st, n_frames=30)


def test_pos_offset():
    base = run(RetargetSettings(foot_ik=False, hand_ik=False))
    tsk, tm = base[2], base[5]
    j = tm["Head"]
    d = np.array([0.0, 0.05, 0.02])
    mod = run(RetargetSettings(foot_ik=False, hand_ik=False, pos_offsets={j: tuple(d)}))
    pa, _ = canon(tsk, base[3].anim, base[3].frame_tgt[0])
    pb, _ = canon(tsk, mod[3].anim, mod[3].frame_tgt[0])
    shift = np.linalg.norm(pb - pa, axis=-1)
    moved = {j} | set(tsk.descendants(j))
    # 보정한 본과 그 자손은 모든 프레임에서 똑같이 |d| 만큼 (부모를 따라 돌며) 밀리고, 나머지는 그대로
    for b in range(tsk.n):
        if b in moved:
            assert np.allclose(shift[:, b], np.linalg.norm(d), atol=1e-6), (tsk.names[b], shift[:, b].min(), shift[:, b].max())
        else:
            assert shift[:, b].max() < 1e-9, tsk.names[b]
    # 정지 자세(바인드)에서는 정확히 d: 부모 로컬 벡터로 들어가므로 부모 회전이 바인드 대비 변한 만큼 돌아 있다 -> 방향은 부모를 따라간다
    assert np.allclose(Q.angle_between(mod[3].anim.rot, base[3].anim.rot), 0, atol=1e-6), "위치 보정이 회전을 바꿈"
    print("pos offset OK")


def test_scale_offset():
    base = run(RetargetSettings(foot_ik=False, hand_ik=False))
    tsk, tm = base[2], base[5]
    j = tm["L_UpperArm"]
    mod = run(RetargetSettings(foot_ik=False, hand_ik=False, scale_offsets={j: 2.0}))
    rb, rm = base[3], mod[3]
    assert rm.scale_cum is not None and abs(rm.scale_cum[j] - 2.0) < 1e-12
    assert base[3].scale_cum is None
    for b in range(tsk.n):
        p = int(tsk.parents[b])
        want = 2.0 if (p == j or (p >= 0 and p in tsk.descendants(j))) else 1.0
        got = np.linalg.norm(rm.anim.pos[:, b], axis=-1) / np.maximum(np.linalg.norm(rb.anim.pos[:, b], axis=-1), 1e-12)
        if np.linalg.norm(rb.anim.pos[:, b]).max() < 1e-9:
            continue
        assert np.allclose(got, want, atol=1e-9), (tsk.names[b], got.min(), got.max(), want)
    assert np.allclose(rm.anim.rot, rb.anim.rot), "크기 보정이 회전을 바꿈"
    print("scale offset OK")


def test_gizmo():
    cam = OrbitCamera()
    w, h = 800.0, 600.0
    p = np.array([0.0, 1.0, 0.0])
    for ax in range(3):
        d = G.axis_move_delta(cam, p, ax, 30.0, 0.0, w, h) + G.axis_move_delta(cam, p, ax, 0.0, -30.0, w, h)
        assert np.linalg.norm(np.delete(d, ax)) < 1e-12, "축 이동이 다른 축으로 샘"
    # 화살표 끝 근처를 찍으면 그 축이 잡힌다 (겹쳐 보이는 축은 건너뛴다)
    c, tips, _L = G.handle_tips(cam, p, w, h)
    for i in range(3):
        v = tips[i] - c
        if np.hypot(*v) < 30:
            continue
        q = c + v * 0.9
        got = G.hit_axis(cam, p, float(q[0]), float(q[1]), w, h)
        assert got == i or got in (0, 1, 2), (i, got)
        assert G.hit_axis(cam, p, float(q[0]), float(q[1]), w, h, tol=0.01) in (i, -1)
    # 아무 데도 아닌 곳은 -1
    assert G.hit_axis(cam, p, 1.0, 1.0, w, h) == -1
    assert G.scale_factor(50, 0) > 1.0 > G.scale_factor(-50, 0)
    assert abs(G.scale_factor(10, 10) - 1.0) < 1e-12
    # 회전: 한 바퀴 돌면 ±2π 근처가 아니라 각 구간 합이 누적된다
    c = cam.project(np.asarray(p).reshape(1, 3), w, h)[0][0]
    a = G.ring_angle(cam, p, 1, c[0] + 50, c[1], c[0], c[1] - 50, w, h)
    assert abs(abs(a) - np.pi / 2) < 1e-6
    print("gizmo OK")


def test_gizmo_spaces():
    """월드/로컬/화면 좌표계: 축 방향, 그 축을 따른 이동·회전, 눌러 잡기가 모두 그 좌표계를 따른다."""
    cam = OrbitCamera()
    w, h = 800.0, 600.0
    p = np.array([0.0, 1.0, 0.0])
    assert np.allclose(G.space_axes("world", cam), np.eye(3))
    r, u, f = cam.basis()
    sa = G.space_axes("screen", cam)
    assert np.allclose(sa[0], r) and np.allclose(sa[1], u) and np.allclose(sa[2], -f)
    # 로컬: 본이 Y 축으로 90° 돌아 있으면 로컬 X 축은 월드 -Z
    q = Q.from_axis_angle(np.array([0.0, 1.0, 0.0]), np.radians(90))
    la = G.space_axes("local", cam, q)
    assert np.allclose(la[0], [0, 0, -1], atol=1e-9) and np.allclose(la[1], [0, 1, 0], atol=1e-9) and np.allclose(la[2], [1, 0, 0], atol=1e-9), la
    assert np.allclose(G.space_axes("local", cam, None), np.eye(3))          # 회전을 모르면 월드로 대신한다
    for ax_set in (la, sa):
        for i in range(3):          # 이동은 그 좌표계의 축 방향으로만 일어난다
            d = G.axis_move_delta(cam, p, i, 25.0, -10.0, w, h, axes=ax_set)
            if np.linalg.norm(d) > 1e-12:
                assert np.linalg.norm(d - ax_set[i] * float(d @ ax_set[i])) < 1e-9, (i, d)
        # 화살표 끝을 누르면 같은 좌표계의 축이 잡힌다
        c, tips, _L = G.handle_tips(cam, p, w, h, axes=ax_set)
        for i in range(3):
            v = tips[i] - c
            if np.hypot(*v) < 30:
                continue
            q2 = c + v * 0.9
            got = G.hit_axis(cam, p, float(q2[0]), float(q2[1]), w, h, axes=ax_set)
            assert got in (i, 0, 1, 2) and got != -1, (i, got)
        # 회전 고리: 각 고리 위의 점은 그 축에 수직인 평면 위에 있다
        for i in range(3):
            pts = G.ring_world(p, i, 0.5, axes=ax_set)
            assert np.allclose((pts - p) @ ax_set[i], 0.0, atol=1e-9)
            assert np.allclose(np.linalg.norm(pts - p, axis=1), 0.5)
    # 화면 좌표계의 화면 안쪽 축(Z) 이동은 시선 방향이라 화면에서는 거의 안 보인다 -> 0 에 가깝게 처리
    d = G.axis_move_delta(cam, p, 2, 25.0, 0.0, w, h, axes=sa)
    assert np.linalg.norm(d) < 1e-6
    # 화면 X(오른쪽)로 끌면 그만큼 화면 오른쪽으로 간다
    d = G.axis_move_delta(cam, p, 0, 25.0, 0.0, w, h, axes=sa)
    a0 = cam.project(p.reshape(1, 3), w, h)[0][0]
    a1 = cam.project((p + d).reshape(1, 3), w, h)[0][0]
    assert abs((a1[0] - a0[0]) - 25.0) < 0.5 and abs(a1[1] - a0[1]) < 0.5, (a0, a1)
    print("gizmo spaces OK")


if __name__ == "__main__":
    test_gizmo_spaces()
    test_pos_offset()
    test_scale_offset()
    test_gizmo()
    print("test_boneedit ALL OK")
