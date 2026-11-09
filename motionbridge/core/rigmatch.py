"""같은 스켈레톤(본 이름·계층·바인드 모양이 같은 리그) 알아보기.

몬스터처럼 사람 모양이 아닌 캐릭터는 '골반/다리/팔' 같은 사람 본 매핑이 안 되어, 같은 스켈레톤을 쓰는 애니메이션과 메시를 같이 열어도
일반 리타깃(사람 기준 좌표축 추정·본 매핑)이 엉뚱하게 돌아간다. 두 스켈레톤이 사실상 같은 리그면 사람 매핑을 거치지 않고
본 이름대로 1:1 로 적용해야 한다.

판단은 이름과 '모양' 둘 다 본다:
  - 이름: 글자/숫자만 남겨 소문자로 맞춰서(공백·점·밑줄 차이는 무시) 같은 이름인 본을 짝지음. 거의 다 짝지어지고 부모-자식도 같아야 한다.
  - 모양: 짝지은 본들의 바인드 포즈 위치를 겹쳐 본다 (회전 + 크기만 맞춤 — 파일마다 좌표축/단위가 달라도 되게).
    잘 겹치면 같은 리그, 안 겹치면(비율이 다른 사람 모델 등) 일반 리타깃을 쓴다.
돌려주는 값에는 소스→타깃 좌표축 회전(쿼터니언)과 크기 비율이 들어 있어, 좌표축·스케일 추정 없이 그대로 쓴다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from . import quat as Q
from .model import Skeleton

MAX_REL_RMS = 0.03          # 겹쳤을 때 남는 오차 / 몸 크기 가 이 값 이하면 같은 리그
MIN_MATCH_FRAC = 0.9        # 더 작은 쪽 스켈레톤의 본 중 이름이 짝지어진 비율
MIN_BIG_FRAC = 0.7          # 더 큰 쪽 스켈레톤의 본 중 이름이 짝지어진 비율 (한쪽이 일부만 가진 경우를 거른다)
MIN_PAIRS = 3


@dataclass
class RigMatch:
    pairs: List[Tuple[int, int]]   # (소스 본, 타깃 본)
    q: np.ndarray                  # 소스 좌표축 -> 타깃 좌표축 회전 (쿼터니언)
    scale: float                   # 소스 길이 x scale = 타깃 길이
    rel_rms: float                 # 겹친 뒤 남은 오차 (몸 크기 대비)
    n_src: int
    n_tgt: int

    def describe(self) -> str:
        return "같은 스켈레톤으로 확인 (본 %d개 이름 일치 · 오차 %.1f%%) — 사람 매핑 없이 본 이름대로 그대로 적용" % (len(self.pairs), self.rel_rms * 100)


_CORE = ("Hips", "L_UpLeg", "R_UpLeg", "L_Foot", "R_Foot", "L_UpperArm", "R_UpperArm")


def is_humanoid(slots) -> bool:
    """사람 본 매핑(골반·양 다리·양 팔)이 제대로 잡힌 스켈레톤인가. 잡혔으면 기존 사람 리타깃(발 IK 포함)이 잘 맞으므로 쓴다."""
    return bool(slots) and all(k in slots for k in _CORE)


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def match_rig(src: Skeleton, tgt: Skeleton, max_rel_rms: float = MAX_REL_RMS) -> Optional[RigMatch]:
    """두 스켈레톤이 같은 리그면 RigMatch, 아니면 None."""
    return explain_rig(src, tgt, max_rel_rms)[0]


def explain_rig(src: Skeleton, tgt: Skeleton, max_rel_rms: float = MAX_REL_RMS) -> Tuple[Optional[RigMatch], str]:
    """(RigMatch 또는 None, 못 알아봤을 때 사람이 읽을 이유). 이름이 거의 같은데 모양이 안 맞는 경우를 화면에 알려 주기 위함."""
    sk, tk = {}, {}
    for i, n in enumerate(src.names):
        k = _key(n)
        if k and k not in sk:
            sk[k] = i
    for i, n in enumerate(tgt.names):
        k = _key(n)
        if k and k not in tk:
            tk[k] = i
    common = [k for k in sk if k in tk]
    small, big = min(src.n, tgt.n), max(src.n, tgt.n)
    if len(common) < max(MIN_PAIRS, int(np.ceil(MIN_MATCH_FRAC * small)), int(np.ceil(MIN_BIG_FRAC * big))):
        return None, "이름이 같은 본이 %d개뿐이라 같은 스켈레톤으로 보지 않았습니다 (소스 %d · 타깃 %d)" % (len(common), src.n, tgt.n)
    pairs = [(sk[k], tk[k]) for k in common]
    # 부모-자식 관계도 같아야 한다 (이름만 같고 구조가 다른 리그를 거른다)
    s_of_t = {t: s for s, t in pairs}
    same_parent = tot = 0
    for s, t in pairs:
        ps, pt = int(src.parents[s]), int(tgt.parents[t])
        if ps < 0 or pt < 0:
            continue
        tot += 1
        if s_of_t.get(pt) == ps:
            same_parent += 1
    if tot and same_parent < 0.9 * tot:
        return None, "본 이름은 같지만 부모-자식 구조가 달라 같은 스켈레톤으로 보지 않았습니다"
    _, wps = src.bind_world()
    _, wpt = tgt.bind_world()
    xs = np.array([wps[s] for s, _ in pairs], dtype=float)
    xt = np.array([wpt[t] for _, t in pairs], dtype=float)
    cs, ct = xs.mean(axis=0), xt.mean(axis=0)
    xs, xt = xs - cs, xt - ct
    den = float((xs ** 2).sum())
    if den < 1e-12 or float((xt ** 2).sum()) < 1e-12:
        return None, "바인드 포즈 크기를 알 수 없습니다"
    U, S, Vt = np.linalg.svd(xs.T @ xt)
    if S[1] < 1e-6 * max(S[0], 1e-12):
        return None, "본들이 한 줄로 늘어서 있어 방향을 정할 수 없습니다"
    d = 1.0 if np.linalg.det(Vt.T @ U.T) >= 0 else -1.0
    D = np.diag([1.0, 1.0, d])
    R = Vt.T @ D @ U.T                               # xt ≈ scale * R @ xs
    scale = float((S * np.diag(D)).sum() / den)
    if scale <= 0:
        return None, "크기 비율을 구하지 못했습니다"
    resid = xt - scale * (xs @ R.T)
    rel = float(np.sqrt((resid ** 2).sum(axis=1).mean() / max((xt ** 2).sum(axis=1).mean(), 1e-12)))
    if rel > max_rel_rms:
        return None, "본 이름은 같지만 바인드 포즈 모양이 맞지 않습니다 (오차 %.1f%% > %.0f%%) — 좌우 반전(거울)이거나 비율이 다른 모델일 수 있습니다" % (rel * 100, max_rel_rms * 100)
    return RigMatch(pairs, Q.normalize(Q.from_matrix(R)), scale, rel, src.n, tgt.n), ""
