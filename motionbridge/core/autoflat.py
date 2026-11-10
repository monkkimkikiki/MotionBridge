"""IK 접지 구간 자동 평탄화 (Qt 없음, 규칙 기반 — LLM 이 아님).

발/손 IK 가 걸려 바닥에 닿아 있는 구간에서는 다리(위/아래/발) 회전 곡선이 소스 모션의 잔떨림·IK 전환 때문에 톱니처럼 흔들리는 일이 많다.
이 모듈은 접지 구간(앞뒤로 pad 프레임 여유)마다 그 관절들의 회전 곡선을 가우시안으로 펴고, 구간 가장자리에서는 서서히 원래 값으로 이어 붙인다.
결과는 원본을 바꾸지 않고 **새 보정 레이어**(키 근사)로 돌려주므로 레이어를 끄거나 지우면 바로 원래대로 돌아온다.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np

from . import quat as Q
from .layers import Layer, fit_keys

_ID = np.array([1.0, 0.0, 0.0, 0.0])


def _runs(mask: np.ndarray) -> List[Tuple[int, int]]:
    out, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(mask) - 1))
    return out


def jitter(rot: np.ndarray, a: int, b: int) -> float:
    """프레임 a..b 의 회전 떨림 지표: 이웃 프레임 회전의 가속도(2차 차분) 크기 평균 (도/프레임²). 작을수록 매끄럽다."""
    if b - a < 2:
        return 0.0
    r = Q.make_continuous(Q.normalize(rot[a:b + 1]))
    mid = Q.slerp(r[:-2], r[2:], 0.5)
    return float(np.degrees(Q.angle_between(r[1:-1], mid)).mean())


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


def flatten_layer(anim, chains, plans, sigma: float = 2.5, pad: int = 3, tol_deg: float = 0.15, name: str = "자동 평탄화 (IK 접지 구간)"
                  ) -> Tuple[Optional[Layer], str]:
    """(새 레이어 또는 None, 한국어 요약). anim: 지금 상태의 결과 모션. chains/plans: 리타깃 결과의 IK 체인과 접지 계획(같은 순서)."""
    T = anim.n_frames
    layer = Layer(name)
    runs_n = 0
    before: List[float] = []
    after: List[float] = []
    joints = set()
    sigma = max(float(sigma), 0.3)
    for ch, pl in zip(chains, plans):
        for a, b in _runs(np.asarray(pl.contact, dtype=bool)):
            a0, b0 = max(0, a - pad), min(T - 1, b + pad)
            if b0 - a0 < 3:
                continue
            runs_n += 1
            # 구간 안은 1, 바깥 여유 구간에서는 0 으로 부드럽게 (가장자리는 원래 값과 이어짐)
            f = np.arange(T)
            w = np.zeros(T)
            w[a:b + 1] = 1.0
            for k in range(1, pad + 1):
                t = 1.0 - k / (pad + 1.0)
                s = t * t * (3 - 2 * t)
                if a - k >= 0:
                    w[a - k] = s
                if b + k < T:
                    w[b + k] = s
            for j in (ch.upper, ch.lower, ch.foot):
                cur = Q.make_continuous(Q.normalize(anim.rot[:, j]))
                sm = Q.normalize(_gauss(cur, sigma))
                target = Q.slerp(cur, sm, w)
                qd = Q.mul(target, Q.conj(cur))                       # 왼쪽에서 곱하는 변화량 (바깥은 항등)
                rot = np.repeat(_ID[None], T, axis=0)
                rot[a0:b0 + 1] = qd[a0:b0 + 1]
                if float(np.degrees(Q.angle_between(rot[a0:b0 + 1], np.tile(_ID, (b0 - a0 + 1, 1)))).max()) < tol_deg:
                    continue                                          # 바뀌는 게 거의 없으면 키를 만들지 않는다
                ks = fit_keys(rot, np.zeros((T, 3)), [a0, b0], tol_deg=tol_deg)
                old = layer.keys.get(j, {})
                if old:      # 같은 관절의 다른 구간 키와 합친다 (구간 밖은 항등 키로 막혀 있다)
                    old.update(ks)
                else:
                    layer.keys[j] = ks
                joints.add(j)
                before.append(jitter(cur, a, b))
                after.append(jitter(Q.mul(qd, cur), a, b))
    if not layer.n_keys():
        if runs_n == 0:
            return None, "IK 로 바닥에 닿는 구간이 없어 평탄화할 곳이 없습니다 (발 IK 를 켜고 접지가 잡혔는지 확인하세요)."
        return None, "접지 구간은 %d곳 있지만 이미 매끄러워서 고칠 것이 없습니다." % runs_n
    b0, a0 = float(np.mean(before)), float(np.mean(after))
    msg = "접지 구간 %d곳 · 관절 %d개 · 키 %d개 — 회전 떨림 %.2f° → %.2f° (평균 %.0f%% 감소). 새 레이어로 만들었으니 끄거나 지우면 원래대로 돌아옵니다." % (
        runs_n, len(joints), layer.n_keys(), b0, a0, 100.0 * (1.0 - a0 / max(b0, 1e-9)))
    return layer, msg
