"""쿼터니언 유틸리티.

쿼터니언은 (w, x, y, z) 순서의 numpy 배열 (..., 4) 이다.
오일러 각은 '내재(intrinsic) 합성' 규칙을 쓴다: order="ZXY" 이면 R = Rz(a) * Rx(b) * Ry(c).
이 규칙은 BVH 채널 순서(예: Zrotation Xrotation Yrotation)와 같다.
"""
from __future__ import annotations

import numpy as np

_AXIS = {"X": 0, "Y": 1, "Z": 2}


def identity(shape=()) -> np.ndarray:
    q = np.zeros(tuple(shape) + (4,), dtype=float)
    q[..., 0] = 1.0
    return q


def normalize(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, dtype=float)
    n = np.linalg.norm(q, axis=-1, keepdims=True)
    n = np.where(n < 1e-12, 1.0, n)
    return q / n


def conj(q: np.ndarray) -> np.ndarray:
    r = np.array(q, dtype=float, copy=True)
    r[..., 1:] *= -1.0
    return r


def mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """해밀턴 곱 a*b (브로드캐스트 지원). 회전 a*b 는 b 를 먼저 적용한 뒤 a 를 적용한다."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    aw, ax, ay, az = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    bw, bx, by, bz = b[..., 0], b[..., 1], b[..., 2], b[..., 3]
    return np.stack(
        [
            aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
        ],
        axis=-1,
    )


def rotate(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    """벡터 v 를 쿼터니언 q 로 회전."""
    q = np.asarray(q, dtype=float)
    v = np.asarray(v, dtype=float)
    u = q[..., 1:]
    w = q[..., :1]
    t = 2.0 * np.cross(u, v)
    return v + w * t + np.cross(u, t)


def from_axis_angle(axis, angle) -> np.ndarray:
    axis = np.asarray(axis, dtype=float)
    angle = np.asarray(angle, dtype=float)
    n = np.linalg.norm(axis, axis=-1, keepdims=True)
    axis = axis / np.where(n < 1e-12, 1.0, n)
    h = 0.5 * angle
    return np.concatenate([np.cos(h)[..., None], axis * np.sin(h)[..., None]], axis=-1)


def to_matrix(q: np.ndarray) -> np.ndarray:
    q = normalize(q)
    w, x, y, z = q[..., 0], q[..., 1], q[..., 2], q[..., 3]
    m = np.empty(q.shape[:-1] + (3, 3), dtype=float)
    m[..., 0, 0] = 1 - 2 * (y * y + z * z)
    m[..., 0, 1] = 2 * (x * y - z * w)
    m[..., 0, 2] = 2 * (x * z + y * w)
    m[..., 1, 0] = 2 * (x * y + z * w)
    m[..., 1, 1] = 1 - 2 * (x * x + z * z)
    m[..., 1, 2] = 2 * (y * z - x * w)
    m[..., 2, 0] = 2 * (x * z - y * w)
    m[..., 2, 1] = 2 * (y * z + x * w)
    m[..., 2, 2] = 1 - 2 * (x * x + y * y)
    return m


def from_matrix(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=float)
    shp = m.shape[:-2]
    m = m.reshape(-1, 3, 3)
    n = m.shape[0]
    m00, m11, m22 = m[:, 0, 0], m[:, 1, 1], m[:, 2, 2]
    cand = np.stack([1 + m00 + m11 + m22, 1 + m00 - m11 - m22, 1 - m00 + m11 - m22, 1 - m00 - m11 + m22], axis=-1)
    k = cand.argmax(-1)
    q = np.empty((n, 4), dtype=float)
    for idx in range(4):
        sel = k == idx
        if not sel.any():
            continue
        mm = m[sel]
        if idx == 0:
            s = np.sqrt(np.maximum(1 + mm[:, 0, 0] + mm[:, 1, 1] + mm[:, 2, 2], 1e-16)) * 2
            q[sel] = np.stack([s / 4, (mm[:, 2, 1] - mm[:, 1, 2]) / s, (mm[:, 0, 2] - mm[:, 2, 0]) / s, (mm[:, 1, 0] - mm[:, 0, 1]) / s], -1)
        elif idx == 1:
            s = np.sqrt(np.maximum(1 + mm[:, 0, 0] - mm[:, 1, 1] - mm[:, 2, 2], 1e-16)) * 2
            q[sel] = np.stack([(mm[:, 2, 1] - mm[:, 1, 2]) / s, s / 4, (mm[:, 0, 1] + mm[:, 1, 0]) / s, (mm[:, 0, 2] + mm[:, 2, 0]) / s], -1)
        elif idx == 2:
            s = np.sqrt(np.maximum(1 - mm[:, 0, 0] + mm[:, 1, 1] - mm[:, 2, 2], 1e-16)) * 2
            q[sel] = np.stack([(mm[:, 0, 2] - mm[:, 2, 0]) / s, (mm[:, 0, 1] + mm[:, 1, 0]) / s, s / 4, (mm[:, 1, 2] + mm[:, 2, 1]) / s], -1)
        else:
            s = np.sqrt(np.maximum(1 - mm[:, 0, 0] - mm[:, 1, 1] + mm[:, 2, 2], 1e-16)) * 2
            q[sel] = np.stack([(mm[:, 1, 0] - mm[:, 0, 1]) / s, (mm[:, 0, 2] + mm[:, 2, 0]) / s, (mm[:, 1, 2] + mm[:, 2, 1]) / s, s / 4], -1)
    return normalize(q).reshape(shp + (4,))


def between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """벡터 a 를 벡터 b 로 돌리는 최소 회전."""
    a, b = np.broadcast_arrays(np.asarray(a, dtype=float), np.asarray(b, dtype=float))
    an = np.linalg.norm(a, axis=-1, keepdims=True)
    bn = np.linalg.norm(b, axis=-1, keepdims=True)
    a = a / np.where(an < 1e-12, 1.0, an)
    b = b / np.where(bn < 1e-12, 1.0, bn)
    d = np.sum(a * b, axis=-1)
    c = np.cross(a, b)
    q = np.concatenate([(1.0 + d)[..., None], c], axis=-1)
    anti = d < -1.0 + 1e-8
    if np.any(anti):
        aa = a[anti]
        helper = np.where(np.abs(aa[:, :1]) < 0.9, np.array([[1.0, 0.0, 0.0]]), np.array([[0.0, 1.0, 0.0]]))
        ax = np.cross(aa, helper)
        ax /= np.linalg.norm(ax, axis=-1, keepdims=True)
        q[anti] = np.concatenate([np.zeros((aa.shape[0], 1)), ax], axis=-1)
    # a 또는 b 가 영벡터면 항등
    zero = ((an[..., 0] < 1e-12) | (bn[..., 0] < 1e-12))
    if np.any(zero):
        q[zero] = np.array([1.0, 0.0, 0.0, 0.0])
    return normalize(q)


def slerp(q0: np.ndarray, q1: np.ndarray, t) -> np.ndarray:
    q0 = normalize(q0)
    q1 = normalize(q1)
    t = np.asarray(t, dtype=float)
    d = np.sum(q0 * q1, axis=-1, keepdims=True)
    q1 = np.where(d < 0, -q1, q1)
    d = np.abs(d)
    t = t[..., None] if t.ndim == d.ndim - 1 else t
    theta = np.arccos(np.clip(d, -1.0, 1.0))
    s = np.sin(theta)
    small = s < 1e-6
    s_safe = np.where(small, 1.0, s)
    w0 = np.where(small, 1.0 - t, np.sin((1.0 - t) * theta) / s_safe)
    w1 = np.where(small, t, np.sin(t * theta) / s_safe)
    return normalize(w0 * q0 + w1 * q1)


def make_continuous(q: np.ndarray) -> np.ndarray:
    """시간 축(0번 축)을 따라 부호를 맞춰 쿼터니언 이중 덮개 뒤집힘을 없앤다."""
    q = np.array(q, dtype=float, copy=True)
    for i in range(1, q.shape[0]):
        d = np.sum(q[i] * q[i - 1], axis=-1)
        q[i] = np.where((d < 0)[..., None], -q[i], q[i])
    return q


def angle_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """두 회전 사이 각도(라디안)."""
    d = np.abs(np.sum(normalize(a) * normalize(b), axis=-1))
    return 2.0 * np.arccos(np.clip(d, -1.0, 1.0))


def from_euler(angles, order: str = "XYZ", degrees: bool = True) -> np.ndarray:
    """오일러 -> 쿼터니언. angles[..., i] 는 order[i] 축 회전각. R = R_o0 * R_o1 * R_o2."""
    angles = np.asarray(angles, dtype=float)
    if degrees:
        angles = np.radians(angles)
    q = None
    for i, ch in enumerate(order.upper()):
        axis = np.zeros(3)
        axis[_AXIS[ch]] = 1.0
        qi = from_axis_angle(axis, angles[..., i])
        q = qi if q is None else mul(q, qi)
    return normalize(q)


def to_euler(q: np.ndarray, order: str = "XYZ", degrees: bool = True) -> np.ndarray:
    """쿼터니언 -> 오일러 (from_euler 의 역). order 는 서로 다른 세 축."""
    order = order.upper()
    i, j, k = (_AXIS[c] for c in order)
    if len({i, j, k}) != 3:
        raise ValueError("order 는 서로 다른 세 축이어야 합니다: %s" % order)
    s = 1.0 if (i, j, k) in ((0, 1, 2), (1, 2, 0), (2, 0, 1)) else -1.0
    m = to_matrix(q)
    b = np.arcsin(np.clip(s * m[..., i, k], -1.0, 1.0))
    cb = np.cos(b)
    gimbal = np.abs(cb) < 1e-6
    a = np.arctan2(-s * m[..., j, k], m[..., k, k])
    c = np.arctan2(-s * m[..., i, j], m[..., i, i])
    if np.any(gimbal):
        a = np.where(gimbal, np.arctan2(s * m[..., k, j], m[..., j, j]), a)
        c = np.where(gimbal, 0.0, c)
    out = np.stack([a, b, c], axis=-1)
    return np.degrees(out) if degrees else out
