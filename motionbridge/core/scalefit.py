"""소스 모션과 타깃 모델의 크기가 다를 때, 모션 스케일을 모델에 맞추는 값을 구한다.

리타겟은 기본으로 '골반 높이' 비율로 스케일을 정한다(자동 스케일). 몬스터처럼 체형이 많이 다르면 기준을
바꾸고 싶을 수 있어서 세 가지 기준(골반 높이 / 전체 키 / 다리 길이)을 모두 재서 보여 주고,
고른 기준에 맞도록 '스케일 배율'(RetargetSettings.scale_mult)을 계산해 준다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import quat as Q
from .mapping import DEFAULT_FRAME, detect_frame

BASES = (("hip", "골반 높이 (기본)"), ("height", "전체 키"), ("leg", "다리 길이"))


@dataclass
class Measure:
    key: str
    label: str
    src: float
    tgt: float

    @property
    def ratio(self) -> float:
        return self.tgt / self.src if self.src > 1e-9 else 0.0


@dataclass
class ScaleReport:
    measures: Dict[str, Measure] = field(default_factory=dict)
    k_auto: float = 1.0  # 지금 자동 스케일(배율 1)이 쓰는 비율 = 골반 높이 비율
    chosen: Optional[Measure] = None
    mult: float = 1.0  # scale_mult 에 넣을 값
    notes: List[str] = field(default_factory=list)

    def text(self) -> str:
        parts = []
        for key, _lab in BASES:
            m = self.measures.get(key)
            if m is not None:
                parts.append("%s: 소스 %.3g / 타깃 %.3g → x%.3g" % (m.label.split(" ")[0] if key != "hip" else "골반 높이", m.src, m.tgt, m.ratio))
        s = " · ".join(parts) if parts else "재지 못했습니다."
        if self.chosen is not None:
            s += "  ▶ '%s' 기준으로 스케일 배율을 %.3g 로 맞춤 (최종 스케일 x%.3g)" % (self.chosen.label.split(" (")[0], self.mult, self.chosen.ratio)
        return " ".join([s] + self.notes).strip()


def _frame(skel, slots) -> np.ndarray:
    det = detect_frame(skel, slots) if slots else None
    return det[0] if det is not None else DEFAULT_FRAME.copy()


def _up_positions(skel, slots) -> np.ndarray:
    """바인드 포즈 관절 위치를 표준 프레임(Y 가 위)으로."""
    _bwr, bwp = skel.bind_world()
    return Q.rotate(_frame(skel, slots), bwp)


def measure(pr, basis: str = "hip") -> ScaleReport:
    """pr: Project (소스·타깃·매핑이 있어야 함). basis: hip | height | leg."""
    rep = ScaleReport()
    if not (pr.src and pr.tgt):
        rep.notes.append("소스 모션과 타깃 모델을 먼저 불러오세요.")
        return rep
    pairs = pr.pairs()
    if not pairs:
        rep.notes.append("본 매핑이 없습니다.")
        return rep
    ps = _up_positions(pr.src.skeleton, pr.src_slots)
    pt = _up_positions(pr.tgt.skeleton, pr.tgt_slots)
    s_idx = [s for s, _t in pairs]
    t_idx = [t for _s, t in pairs]
    ground_s, ground_t = float(ps[s_idx, 1].min()), float(pt[t_idx, 1].min())
    ss, st = pr.src_slots, pr.tgt_slots
    hips_s, hips_t = ss.get("Hips"), st.get("Hips")
    if hips_s is not None and hips_t is not None:
        hs, ht = float(ps[hips_s, 1] - ground_s), float(pt[hips_t, 1] - ground_t)
        if hs > 1e-9 and ht > 1e-9:
            rep.measures["hip"] = Measure("hip", BASES[0][1], hs, ht)
    hs_all, ht_all = float(ps[s_idx, 1].max() - ground_s), float(pt[t_idx, 1].max() - ground_t)
    if hs_all > 1e-9 and ht_all > 1e-9:
        rep.measures["height"] = Measure("height", BASES[1][1], hs_all, ht_all)
    legs_s, legs_t = [], []
    for side in ("L", "R"):
        f_s, f_t = ss.get(side + "_Foot"), st.get(side + "_Foot")
        if None not in (hips_s, hips_t, f_s, f_t):
            legs_s.append(float(np.linalg.norm(ps[hips_s] - ps[f_s])))
            legs_t.append(float(np.linalg.norm(pt[hips_t] - pt[f_t])))
    if legs_s and np.mean(legs_s) > 1e-9 and np.mean(legs_t) > 1e-9:
        rep.measures["leg"] = Measure("leg", BASES[2][1], float(np.mean(legs_s)), float(np.mean(legs_t)))
    if not rep.measures:
        rep.notes.append("골반/발 매핑이 없어 크기를 재지 못했습니다. 매핑을 확인하세요.")
        return rep
    base = rep.measures.get("hip") or next(iter(rep.measures.values()))
    rep.k_auto = base.ratio
    ch = rep.measures.get(basis) or base
    if basis not in rep.measures:
        rep.notes.append("선택한 기준을 잴 수 없어 '%s' 로 대신했습니다." % ch.label.split(" (")[0])
    rep.chosen = ch
    rep.mult = ch.ratio / rep.k_auto if rep.k_auto > 1e-9 else 1.0
    r = ch.ratio
    if r > 0 and any(abs(np.log10(r / u)) < 0.25 for u in (100.0, 0.01, 1000.0, 0.001)):
        rep.notes.append("(단위가 cm↔m 로 달라 보입니다 — 비율로 계산하므로 그대로 맞춰집니다.)")
    elif r > 8 or (0 < r < 0.125):
        rep.notes.append("⚠ 크기 차이가 큽니다 (x%.3g). 몬스터↔사람처럼 체형이 많이 다르면 다른 기준(전체 키·다리 길이)도 비교해 보세요." % r)
    return rep
