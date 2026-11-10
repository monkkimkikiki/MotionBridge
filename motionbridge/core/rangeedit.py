"""프레임 구간 편집 (Qt 없음): 튀는 구간을 지우고 앞뒤 정상 프레임으로 블렌드 / 일직선으로 잇기 / 떨림 정리.

맥스에서 키를 지우면 앞뒤 키 사이가 자동으로 보간되는 것과 같은 일을 보정 레이어로 한다. 원본·아래 레이어는 건드리지 않고,
고르는 본의 '지금 상태 곡선'을 원하는 모양으로 다시 만든 뒤, 그 모양이 되도록 맨 위 레이어의 키를 다시 쓴다 (키는 0.1° 오차 안으로 근사해 적게 찍는다).
  blend  — 프레임 a..b 를 지우고 a-1 과 b+1 의 값을 이어서 채운다 (a 가 0 이거나 b 가 끝이면 반대쪽 값을 그대로 유지)
  line   — 프레임 a 와 b 의 값은 그대로 두고 그 사이를 일직선(회전은 구면 보간)으로 잇는다 (미세하게 흔들리는 구간을 직선화)
  smooth — a..b 를 가우시안으로 펴고, 앞뒤 pad 프레임에 걸쳐 원래 값으로 서서히 이어 붙인다 (떨림 정리)
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np

from . import quat as Q
from .layeredit import MIN_WEIGHT, editable
from .layers import Layer, fit_keys

_ID = np.array([1.0, 0.0, 0.0, 0.0])
MODES = ("blend", "line", "smooth")


def _gauss(a: np.ndarray, sigma: float) -> np.ndarray:
    rad = max(1, int(np.ceil(3 * sigma)))
    k = np.exp(-0.5 * (np.arange(-rad, rad + 1) / sigma) ** 2)
    k /= k.sum()
    T = a.shape[0]
    pad = np.concatenate([np.repeat(a[:1], rad, axis=0), a, np.repeat(a[-1:], rad, axis=0)], axis=0)
    acc = np.zeros_like(a)
    for i, w in enumerate(k):
        acc += w * pad[i:i + T]
    return acc


def _plan(mode: str, a: int, b: int, T: int, pad: int) -> Optional[Tuple[int, int]]:
    """다시 쓸 프레임 범위 (lo, hi) — 양 끝은 값이 바뀌지 않는 기준 프레임. 안 되면 None."""
    if mode == "blend":
        lo, hi = a - 1, b + 1
        if lo < 0 and hi > T - 1:
            return None
        return max(lo, 0), min(hi, T - 1)
    if mode == "line":
        return (a, b) if b - a >= 2 else None
    lo, hi = a - pad - 1, b + pad + 1
    return max(lo, 0), min(hi, T - 1)


def _targets(mode: str, rot: np.ndarray, pos: np.ndarray, lo: int, hi: int, a: int, b: int, sigma: float, pad: int):
    """프레임 lo..hi 의 목표 (회전 (n,4), 위치 (n,3)) — '지금 상태' 곡선 rot/pos 에서 만든다."""
    n = hi - lo + 1
    r = Q.make_continuous(Q.normalize(rot))
    if mode in ("blend", "line"):
        if mode == "blend" and a == 0:                        # 앞쪽 기준이 없으면 뒤 기준을 그대로
            return np.repeat(r[hi][None], n, axis=0), np.repeat(pos[hi][None], n, axis=0)
        if mode == "blend" and b == len(r) - 1:
            return np.repeat(r[lo][None], n, axis=0), np.repeat(pos[lo][None], n, axis=0)
        u = np.linspace(0.0, 1.0, n)
        tr = Q.slerp(np.repeat(r[lo][None], n, axis=0), np.repeat(r[hi][None], n, axis=0), u)
        tp = pos[lo][None] + (pos[hi] - pos[lo])[None] * u[:, None]
        return tr, tp
    sm_r = Q.normalize(_gauss(r, sigma))
    sm_p = _gauss(pos, sigma)
    w = np.zeros(len(r))
    w[a:b + 1] = 1.0
    for k in range(1, pad + 1):
        t = 1.0 - k / (pad + 1.0)
        s = t * t * (3 - 2 * t)
        if a - k >= 0:
            w[a - k] = s
        if b + k < len(r):
            w[b + k] = s
    ww = w[lo:hi + 1]
    tr = Q.slerp(r[lo:hi + 1], sm_r[lo:hi + 1], ww)
    tp = pos[lo:hi + 1] * (1 - ww)[:, None] + sm_p[lo:hi + 1] * ww[:, None]
    return tr, tp


def rewrite(layer: Layer, below, final, joints: Sequence[int], a: int, b: int, mode: str = "blend", sigma: float = 2.0, pad: int = 3,
            tol_deg: float = 0.1, tol_pos: float = 1e-4) -> Tuple[int, str]:
    """맨 위 레이어(layer)의 키를 고쳐 joints 의 프레임 a..b(끝 포함, 0부터)를 mode 대로 만든다. below = 이 레이어 아래 상태, final = 지금 상태(이 레이어 포함).
    돌려주는 값: (바뀐 본 수, 한국어 설명). 안 되면 (0, 이유)."""
    why = editable(layer)
    if why:
        return 0, why
    if mode not in MODES:
        return 0, "알 수 없는 방식입니다."
    T = final.n_frames
    a, b = max(min(int(a), int(b)), 0), min(max(int(a), int(b)), T - 1)
    plan = _plan(mode, a, b, T, pad)
    if plan is None:
        return 0, ("지울 구간이 전체 모션이라 앞뒤 기준이 없습니다." if mode == "blend" else "'일직선으로' 는 구간이 3프레임 이상이어야 합니다 (시작·끝 값은 그대로 두고 그 사이를 이어요).")
    lo, hi = plan
    w = float(np.clip(layer.weight, 0.0, 1.0))
    n_j, n_keys = 0, 0
    for j in joints:
        j = int(j)
        if not (0 <= j < final.rot.shape[1]):
            continue
        tr, tp = _targets(mode, final.rot[:, j], np.asarray(final.pos[:, j], dtype=float), lo, hi, a, b, float(max(sigma, 0.3)), pad)
        bw = Q.make_continuous(Q.normalize(below.rot[lo:hi + 1, j]))
        d_rot = Q.make_continuous(Q.normalize(Q.mul(tr, Q.conj(bw))))
        d_pos = tp - np.asarray(below.pos[lo:hi + 1, j], dtype=float)
        if w < 1.0:                                                  # 레이어 가중치가 낮아도 보이는 값이 목표가 되도록 거꾸로 계산
            d_rot = Q.slerp(np.repeat(_ID[None], len(d_rot), axis=0), d_rot, 1.0 / max(w, MIN_WEIGHT))
            d_pos = d_pos / max(w, MIN_WEIGHT)
        ks = fit_keys(d_rot, d_pos, [0, hi - lo], tol_deg, tol_pos)
        old = layer.keys.get(j, {})
        if not ks and not old:
            continue
        for f in [f for f in old if lo <= f <= hi]:
            layer.remove_key(j, f)
        if all(k.is_identity(1e-7) for k in ks.values()) and not layer.keys.get(j):
            continue                                                 # 바꿀 게 없다 (이 본은 이미 목표대로)
        for f, k in ks.items():
            layer.keys.setdefault(j, {})[lo + f] = k
        n_j += 1
        n_keys += len(ks)
    label = {"blend": "지우고 블렌드", "line": "일직선으로", "smooth": "떨림 정리"}[mode]
    return n_j, "%s: 프레임 %d~%d, 본 %d개 (키 %d개 기록)" % (label, a + 1, b + 1, n_j, n_keys)
