"""리타겟 결과 자동 검수: 발 미끄러짐, 지면 관통, 다리 과신장, 회전 튐, 루프 이음새."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from . import quat as Q
from .model import Skeleton, fk
from .retarget import RetargetResult, RetargetSettings


@dataclass
class Issue:
    kind: str  # slide | penetration | overreach | pop | loop | nan
    severity: str  # info | warn | error
    start: int
    end: int
    message: str


def _merge(frames: np.ndarray, gap: int = 1):
    idx = np.flatnonzero(frames)
    if idx.size == 0:
        return []
    out, a, b = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - b <= gap:
            b = i
        else:
            out.append((int(a), int(b)))
            a = b = i
    out.append((int(a), int(b)))
    return out


def analyze(tgt: Skeleton, res: RetargetResult, st: Optional[RetargetSettings] = None) -> List[Issue]:
    st = st or RetargetSettings()
    anim = res.anim
    T = anim.n_frames
    issues: List[Issue] = []
    if not (np.isfinite(anim.rot).all() and np.isfinite(anim.pos).all()):
        issues.append(Issue("nan", "error", 0, T - 1, "NaN/Inf 값이 있습니다. 매핑이나 스켈레톤을 확인하세요."))
        return issues

    wr, wp = fk(tgt, anim.rot, anim.pos)
    Ft = res.frame_tgt[0]
    pc = Q.rotate(Ft, wp)
    bwr, bwp = tgt.bind_world()
    bpc = Q.rotate(Ft, bwp)

    for ch, pl in zip(res.chains, res.plans):
        foot = pc[:, ch.foot]
        L = pl.leg_length
        # 발 미끄러짐: 접지 중인 연속 프레임 사이의 수평 이동
        if T > 1:
            both = pl.contact[1:] & pl.contact[:-1]
            disp = np.linalg.norm(np.diff(foot[:, [0, 2]], axis=0), axis=1)
            disp = np.where(both, disp, 0.0)
            tol = 0.004 * L
            bad = disp > tol
            for a, b in _merge(np.concatenate([bad, [False]]), 1):
                worst = disp[a : b + 1].max()
                issues.append(
                    Issue("slide", "warn", a, b + 1, "%s 발 미끄러짐: 프레임당 최대 %.2f%% (다리 길이 대비)" % (ch.label, 100 * worst / L))
                )
        # 지면 관통
        floor = bpc[ch.foot, 1] + st.ground_offset
        pen = foot[:, 1] < floor - 0.02 * L
        for a, b in _merge(pen):
            depth = (floor - foot[a : b + 1, 1]).max()
            issues.append(Issue("penetration", "warn", a, b, "%s 발이 바닥 아래로 %.1f%% 내려감" % (ch.label, 100 * depth / L)))
        # 다리 과신장
        for a, b in _merge(pl.fail):
            issues.append(Issue("overreach", "warn", a, b, "%s 다리가 완전히 펴져도 닿지 않음(골반이 너무 높거나 보폭이 큼)" % ch.label))

    # 회전 튐
    if T > 1:
        ang = np.degrees(Q.angle_between(anim.rot[1:], anim.rot[:-1]))  # (T-1, J)
        worst_j = ang.argmax(axis=1)
        worst = ang.max(axis=1)
        for a, b in _merge(np.concatenate([worst > 100.0, [False]])):
            j = int(worst_j[a : b + 1][np.argmax(worst[a : b + 1])])
            issues.append(Issue("pop", "warn", a, b + 1, "회전 튐: '%s' 이(가) 한 프레임에 %.0f° 회전" % (tgt.names[j], worst[a : b + 1].max())))

        # 루프 이음새 (참고)
        seam = np.degrees(Q.angle_between(anim.rot[0], anim.rot[-1]))
        seam_mean = float(np.mean(seam))
        if seam_mean > 6.0:
            issues.append(Issue("loop", "info", T - 1, T - 1, "첫/마지막 프레임 평균 자세 차이 %.1f° (루프 용도라면 이음새가 보일 수 있음)" % seam_mean))

    order = {"error": 0, "warn": 1, "info": 2}
    issues.sort(key=lambda i: (order[i.severity], i.start))
    return issues
