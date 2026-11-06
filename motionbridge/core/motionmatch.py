"""동작 비교: 영상에서 뽑은 3D 동작(AI 모캡 BVH 등)과 게임 애니메이션이 얼마나 비슷한지 점수를 매긴다.

이름으로 찾은 후보를 '실제 움직임'으로 다시 줄 세우는 데 쓴다 (모션 검색의 motion-to-motion 방식).

방법
  1) 스켈레톤이 달라도 같은 기준이 되게, 팔다리·몸통의 '뼈 방향'만 쓴다. 키/비율/본 이름/축이 달라도 되고,
     본 이름을 자동 매핑(mapping.auto_map)해서 표준 프레임(Y위, Z앞)으로 돌린 뒤, 프레임마다 골반 방향(좌우)을 +X 로 맞춘다
     → 캐릭터가 어디로 걷든 어느 쪽을 보든 같은 값.
  2) 두 동작을 같은 길이(최대 N 프레임)로 줄이고 DTW(동적 시간 정렬)로 맞춘다 → 빠르기가 달라도 같은 동작이면 가깝다.
  3) 비교 값은 뼈 방향 사이의 평균 각도(도). 점수 = 1 - 각도/35 (0~1). 좌우 반전(거울) 동작도 같이 비교해 더 좋은 쪽을 쓴다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

from . import mapping
from . import quat as Q
from .model import Animation, Skeleton, fk

# (이름, 시작 슬롯, 끝 슬롯 후보들) — 끝 슬롯은 있는 첫 번째를 쓴다
SEGMENTS: List[Tuple[str, str, Tuple[str, ...]]] = [
    ("spine", "Hips", ("Spine2", "Spine1", "Spine", "Neck")),
    ("chest", "Spine2", ("Neck", "Head")),
    ("head", "Neck", ("Head",)),
    ("L_uparm", "L_UpperArm", ("L_LowerArm",)), ("L_loarm", "L_LowerArm", ("L_Hand",)),
    ("R_uparm", "R_UpperArm", ("R_LowerArm",)), ("R_loarm", "R_LowerArm", ("R_Hand",)),
    ("L_thigh", "L_UpLeg", ("L_LowLeg",)), ("L_calf", "L_LowLeg", ("L_Foot",)),
    ("R_thigh", "R_UpLeg", ("R_LowLeg",)), ("R_calf", "R_LowLeg", ("R_Foot",)),
]
MIN_SEGMENTS = 7  # 이보다 적게 잡히면 비교하지 않는다
MAX_FRAMES = 48
DEG_ZERO = 35.0  # 평균 각도가 이 값(도) 이상이면 점수 0


def _pairs(slots: dict):
    """슬롯에 실제로 있는 (구간 이름, 시작 본, 끝 본)."""
    out = []
    for name, a, bs in SEGMENTS:
        if a not in slots:
            continue
        b = next((slots[x] for x in bs if x in slots), None)
        if b is not None and slots[a] != b:
            out.append((name, slots[a], b))
    return out


def descriptor(skel: Skeleton, anim: Animation, slots: Optional[dict] = None) -> Optional[np.ndarray]:
    """(T, S, 3) 뼈 방향(단위 벡터, 골반 좌우=+X, 위=+Y). 비교할 수 없으면 None. 구간 순서는 SEGMENTS 의 이름 순서(names() 참고)."""
    slots = slots if slots is not None else mapping.auto_map(skel)
    pr = _pairs(slots)
    if len(pr) < MIN_SEGMENTS or "L_UpLeg" not in slots or "R_UpLeg" not in slots:
        return None
    det = mapping.detect_frame(skel, slots)
    F = det[0] if det else mapping.DEFAULT_FRAME
    _wr, wp = fk(skel, anim.rot, anim.pos)
    p = Q.rotate(F, wp)  # (T,J,3) 표준 프레임
    left = p[:, slots["L_UpLeg"]] - p[:, slots["R_UpLeg"]]
    yaw = np.arctan2(-left[:, 2], left[:, 0])  # 좌우 벡터가 +X 가 되도록 Y 축으로 돌릴 각도
    c, s = np.cos(yaw), np.sin(yaw)
    segs = []
    for _n, a, b in pr:
        v = p[:, b] - p[:, a]
        x = v[:, 0] * c - v[:, 2] * s
        z = v[:, 0] * s + v[:, 2] * c
        w = np.stack([x, v[:, 1], z], axis=1)
        segs.append(w / np.maximum(np.linalg.norm(w, axis=1, keepdims=True), 1e-9))
    return np.stack(segs, axis=1)


def names_of(skel: Skeleton, slots: Optional[dict] = None) -> List[str]:
    return [n for n, _a, _b in _pairs(slots if slots is not None else mapping.auto_map(skel))]


def _resample(d: np.ndarray, n: int) -> np.ndarray:
    T = d.shape[0]
    if T <= n:
        return d
    idx = np.linspace(0, T - 1, n).round().astype(int)
    return d[idx]


def _align(a: np.ndarray, an: List[str], b: np.ndarray, bn: List[str]):
    """두 구간 이름 목록의 교집합만 같은 순서로."""
    common = [n for n in an if n in bn]
    return a[:, [an.index(n) for n in common]], b[:, [bn.index(n) for n in common]], common


def mirror(d: np.ndarray, names: List[str]) -> np.ndarray:
    """좌우 반전: 왼쪽<->오른쪽 구간을 바꾸고 X 를 뒤집는다."""
    order = []
    for n in names:
        if n.startswith("L_"):
            m = "R_" + n[2:]
        elif n.startswith("R_"):
            m = "L_" + n[2:]
        else:
            m = n
        order.append(names.index(m) if m in names else names.index(n))
    out = d[:, order].copy()
    out[..., 0] *= -1.0
    return out


def _dtw_deg(a: np.ndarray, b: np.ndarray, band: float = 0.3) -> float:
    """두 (T,S,3) 사이의 DTW 평균 각도(도). 시간 축은 대각선에서 band 비율까지만 벗어나게."""
    n, m = a.shape[0], b.shape[0]
    cos = np.clip(np.einsum("isk,jsk->ijs", a, b), -1.0, 1.0)
    cost = np.degrees(np.arccos(cos)).mean(axis=2)  # (n,m)
    inf = 1e18
    D = np.full((n + 1, m + 1), inf)
    L = np.zeros((n + 1, m + 1))
    D[0, 0] = 0.0
    w = max(int(band * max(n, m)), abs(n - m)) + 1
    for i in range(1, n + 1):
        c = int(round(i * m / n))
        for j in range(max(1, c - w), min(m, c + w) + 1):
            prev = min((D[i - 1, j], L[i - 1, j]), (D[i, j - 1], L[i, j - 1]), (D[i - 1, j - 1], L[i - 1, j - 1]), key=lambda t: t[0])
            D[i, j] = prev[0] + cost[i - 1, j - 1]
            L[i, j] = prev[1] + 1
    return float(D[n, m] / max(L[n, m], 1.0))


@dataclass
class MatchScore:
    score: float  # 0~1 (높을수록 비슷)
    deg: float  # 평균 각도 차(도)
    mirrored: bool  # 좌우 반전했을 때가 더 비슷했다
    parts: int  # 비교에 쓴 뼈 구간 수


def compare(qd: np.ndarray, qn: List[str], cd: np.ndarray, cn: List[str], allow_mirror: bool = True) -> Optional[MatchScore]:
    a, b, common = _align(qd, qn, cd, cn)
    if len(common) < MIN_SEGMENTS:
        return None
    a, b = _resample(a, MAX_FRAMES), _resample(b, MAX_FRAMES)
    d0 = _dtw_deg(a, b)
    best, mir = d0, False
    if allow_mirror:
        d1 = _dtw_deg(mirror(a, common), b)
        if d1 < d0:
            best, mir = d1, True
    return MatchScore(float(np.clip(1.0 - best / DEG_ZERO, 0.0, 1.0)), best, mir, len(common))


def rank(query: Tuple[Skeleton, Animation], cands: Sequence[Tuple[str, Skeleton, Animation]], allow_mirror: bool = True) -> List[Tuple[str, MatchScore]]:
    """cands = [(라벨, 스켈레톤, 애니메이션)]. 비교할 수 있는 것만, 점수 높은 순."""
    qs = mapping.auto_map(query[0])
    qd = descriptor(query[0], query[1], qs)
    if qd is None:
        return []
    qn = names_of(query[0], qs)
    out = []
    for label, sk, an in cands:
        sl = mapping.auto_map(sk)
        d = descriptor(sk, an, sl)
        if d is None:
            continue
        sc = compare(qd, qn, d, names_of(sk, sl), allow_mirror)
        if sc is not None:
            out.append((label, sc))
    out.sort(key=lambda t: -t[1].score)
    return out
