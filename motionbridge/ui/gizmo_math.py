"""뷰포트 본 편집 기즈모 계산 (이동 W / 회전 E / 크기 R). 3ds Max 처럼 축 핸들을 끌어 한 축으로만, 중심을 끌어 자유롭게 바꾼다.

Qt 를 쓰지 않는다 (OrbitCamera.project 만 사용). 모든 좌표는 표준 프레임(X=좌우, Y=위, Z=앞).
"""
from __future__ import annotations

from typing import Tuple

import numpy as np

AXES = np.eye(3)
AXIS_NAMES = ("X", "Y", "Z")
HANDLE_PX = 70.0    # 이동/크기 화살표 길이 (화면 픽셀)
RING_PX = 60.0      # 회전 고리 반지름 (화면 픽셀)


def _proj(cam, pts, w: float, h: float) -> np.ndarray:
    xy, _z = cam.project(np.asarray(pts, dtype=float).reshape(-1, 3), w, h)
    return xy


def px_per_unit(cam, p, v, w: float, h: float) -> float:
    """p 에서 v 방향(단위 벡터) 1 만큼이 화면에서 몇 픽셀인지. 시선과 거의 같은 방향이면 0 에 가깝다."""
    p = np.asarray(p, dtype=float)
    v = np.asarray(v, dtype=float)
    u = 0.02 * max(cam.dist, 1e-9)
    xy = _proj(cam, [p, p + v * u], w, h)
    d = float(np.hypot(*(xy[1] - xy[0])))
    return d / u if np.isfinite(d) else 0.0


def world_len(cam, p, px: float, w: float, h: float) -> float:
    """p 근처에서 화면 px 픽셀에 해당하는 월드 길이 (카메라 오른쪽 방향 기준)."""
    r, _u, _f = cam.basis()
    ppu = px_per_unit(cam, p, r, w, h)
    return px / ppu if ppu > 1e-9 else 0.0


def handle_tips(cam, p, w: float, h: float, px: float = HANDLE_PX) -> Tuple[np.ndarray, np.ndarray, float]:
    """(중심 화면 좌표 (2,), 세 축 끝의 화면 좌표 (3,2), 월드 길이)."""
    L = world_len(cam, p, px, w, h)
    pts = np.vstack([np.asarray(p, dtype=float)[None], np.asarray(p, dtype=float)[None] + AXES * L])
    xy = _proj(cam, pts, w, h)
    return xy[0], xy[1:], L


def seg_dist(pt, a, b) -> float:
    pt, a, b = (np.asarray(x, dtype=float) for x in (pt, a, b))
    ab = b - a
    n = float(ab @ ab)
    t = 0.0 if n < 1e-12 else float(np.clip((pt - a) @ ab / n, 0.0, 1.0))
    return float(np.hypot(*(pt - (a + ab * t))))


def hit_axis(cam, p, x: float, y: float, w: float, h: float, px: float = HANDLE_PX, tol: float = 9.0) -> int:
    """(x,y) 가 어느 축 화살표(중심→끝) 위인지: 0/1/2, 없으면 -1. 중심 가까이(자유 이동 영역)는 제외한다."""
    c, tips, _L = handle_tips(cam, p, w, h, px)
    if not np.all(np.isfinite(c)):
        return -1
    best, bi = tol, -1
    for i in range(3):
        if not np.all(np.isfinite(tips[i])):
            continue
        if float(np.hypot(x - c[0], y - c[1])) < 11.0:
            continue
        d = seg_dist((x, y), c, tips[i])
        if d < best:
            best, bi = d, i
    return bi


def ring_world(p, axis_idx: int, radius: float, n: int = 72) -> np.ndarray:
    """p 를 중심으로 axis_idx 축에 수직인 원의 월드 점 (n+1,3) (닫힌 곡선)."""
    a = np.linspace(0.0, 2 * np.pi, n + 1)
    i, j = [(1, 2), (2, 0), (0, 1)][axis_idx]
    pts = np.zeros((n + 1, 3))
    pts[:, i] = np.cos(a) * radius
    pts[:, j] = np.sin(a) * radius
    return pts + np.asarray(p, dtype=float)


def hit_ring(cam, p, x: float, y: float, w: float, h: float, px: float = RING_PX, tol: float = 8.0) -> int:
    """(x,y) 가 어느 회전 고리 위인지 0/1/2, 없으면 -1."""
    R = world_len(cam, p, px, w, h)
    best, bi = tol, -1
    for i in range(3):
        xy = _proj(cam, ring_world(p, i, R), w, h)
        ok = np.all(np.isfinite(xy), axis=1)
        for k in range(len(xy) - 1):
            if ok[k] and ok[k + 1]:
                d = seg_dist((x, y), xy[k], xy[k + 1])
                if d < best:
                    best, bi = d, i
    return bi


def axis_move_delta(cam, p, axis_idx: int, dx: float, dy: float, w: float, h: float) -> np.ndarray:
    """화면 드래그 (dx,dy 픽셀) -> 그 축을 따라 움직인 월드 이동 벡터. 축이 시선과 거의 같으면 0."""
    p = np.asarray(p, dtype=float)
    a = AXES[axis_idx]
    ppu = px_per_unit(cam, p, a, w, h)
    if ppu < 1e-6:
        return np.zeros(3)
    u = 0.02 * max(cam.dist, 1e-9)
    xy = _proj(cam, [p, p + a * u], w, h)
    d2 = xy[1] - xy[0]
    n = float(np.hypot(*d2))
    if not np.isfinite(n) or n < 1e-9:
        return np.zeros(3)
    d2 = d2 / n
    return a * ((dx * d2[0] + dy * d2[1]) / ppu)


def plane_move_delta(cam, p, dx: float, dy: float, w: float, h: float) -> np.ndarray:
    """화면 평면에서 자유롭게 끈 만큼의 월드 이동 벡터 (카메라 오른쪽/위 방향)."""
    r, u, _f = cam.basis()
    pr_, pu_ = px_per_unit(cam, p, r, w, h), px_per_unit(cam, p, u, w, h)
    if pr_ < 1e-6 or pu_ < 1e-6:
        return np.zeros(3)
    return r * (dx / pr_) - u * (dy / pu_)


def ring_angle(cam, p, axis_idx: int, x0: float, y0: float, x1: float, y1: float, w: float, h: float) -> float:
    """고리를 끈 각도(라디안): 중심 둘레로 마우스가 돈 만큼. 축이 보는 사람 쪽이면 반시계 = +."""
    c = _proj(cam, [p], w, h)[0]
    if not np.all(np.isfinite(c)):
        return 0.0
    a0 = np.arctan2(-(y0 - c[1]), x0 - c[0])
    a1 = np.arctan2(-(y1 - c[1]), x1 - c[0])
    da = (a1 - a0 + np.pi) % (2 * np.pi) - np.pi
    toward = float(AXES[axis_idx] @ (cam.eye() - np.asarray(p, dtype=float)))
    return float(da if toward >= 0 else -da)


def scale_factor(dx: float, dy: float) -> float:
    """오른쪽/위로 끌면 커지고 왼쪽/아래로 끌면 작아지는 배율 (한 번의 이동량에 대한 곱)."""
    return float(np.exp((dx - dy) * 0.006))
