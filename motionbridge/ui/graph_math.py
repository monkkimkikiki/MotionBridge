"""곡선 그래프의 좌표 계산 (Qt 없음): 프레임/값 <-> 화면 픽셀, 눈금, 키·곡선 집기."""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np


class GraphMap:
    """그래프 영역(픽셀)과 보이는 범위(프레임 f0..f1, 값 v0..v1)를 잇는다. 값은 위로 갈수록 커진다."""

    def __init__(self, w: float, h: float, f0: float, f1: float, v0: float, v1: float, left: float = 48.0, right: float = 8.0, top: float = 8.0, bottom: float = 20.0):
        self.w, self.h = float(w), float(h)
        self.f0, self.f1 = float(f0), float(max(f1, f0 + 1e-6))
        self.v0, self.v1 = float(v0), float(max(v1, v0 + 1e-9))
        self.left, self.right, self.top, self.bottom = left, right, top, bottom

    @property
    def pw(self) -> float:
        return max(self.w - self.left - self.right, 1.0)

    @property
    def ph(self) -> float:
        return max(self.h - self.top - self.bottom, 1.0)

    def x_of(self, f):
        return self.left + (np.asarray(f, dtype=float) - self.f0) / (self.f1 - self.f0) * self.pw

    def f_of(self, x):
        return self.f0 + (np.asarray(x, dtype=float) - self.left) / self.pw * (self.f1 - self.f0)

    def y_of(self, v):
        return self.top + (1.0 - (np.asarray(v, dtype=float) - self.v0) / (self.v1 - self.v0)) * self.ph

    def v_of(self, y):
        return self.v0 + (1.0 - (np.asarray(y, dtype=float) - self.top) / self.ph) * (self.v1 - self.v0)

    def zoom_frames(self, factor: float, around_x: float) -> None:
        """around_x 픽셀 아래의 프레임을 고정한 채 보이는 프레임 범위를 factor 배로 (작을수록 확대)."""
        c = float(self.f_of(around_x))
        self.f0 = c - (c - self.f0) * factor
        self.f1 = c + (self.f1 - c) * factor

    def zoom_values(self, factor: float, around_y: float) -> None:
        c = float(self.v_of(around_y))
        self.v0 = c - (c - self.v0) * factor
        self.v1 = c + (self.v1 - c) * factor


def fit_range(arrays: Sequence[np.ndarray], pad: float = 0.12, min_span: float = 1.0) -> Tuple[float, float]:
    """곡선들이 모두 들어오는 값 범위 (위아래 pad 비율 여유, 평평해도 min_span 이상)."""
    vals = [np.asarray(a, dtype=float) for a in arrays if a is not None and len(a)]
    if not vals:
        return -1.0, 1.0
    lo = min(float(v.min()) for v in vals)
    hi = max(float(v.max()) for v in vals)
    span = max(hi - lo, min_span)
    mid = 0.5 * (hi + lo)
    return mid - span * (0.5 + pad), mid + span * (0.5 + pad)


def nice_ticks(lo: float, hi: float, n: int = 5) -> List[float]:
    """lo..hi 안의 보기 좋은 눈금 값들 (1·2·5 × 10^k 간격)."""
    span = max(hi - lo, 1e-12)
    raw = span / max(n, 1)
    mag = 10.0 ** np.floor(np.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    first = np.ceil(lo / step) * step
    out = []
    v = first
    while v <= hi + 1e-9:
        out.append(float(round(v, 10)) + 0.0)   # -0.0 방지
        v += step
    return out


def nearest_point(gm: GraphMap, x: float, y: float, pts: Sequence[Tuple[float, float, object]], tol: float = 9.0):
    """(프레임, 값, 식별자) 점들 중 (x,y) 픽셀에서 tol 안에서 가장 가까운 것의 식별자 (없으면 None)."""
    best, bid = tol, None
    for f, v, ident in pts:
        d = float(np.hypot(gm.x_of(f) - x, gm.y_of(v) - y))
        if d < best:
            best, bid = d, ident
    return bid


def curve_hit(gm: GraphMap, x: float, y: float, values: np.ndarray, tol: float = 7.0) -> Optional[int]:
    """(x,y) 가 곡선(프레임마다 값) 위(세로로 tol 픽셀 안)에 있으면 그 프레임 번호, 아니면 None."""
    if values is None or len(values) == 0:
        return None
    f = int(np.clip(round(float(gm.f_of(x))), 0, len(values) - 1))
    return f if abs(float(gm.y_of(values[f])) - y) <= tol else None
