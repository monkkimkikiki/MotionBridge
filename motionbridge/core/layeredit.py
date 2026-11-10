"""뷰포트에서 본을 끈 만큼을 보정 레이어의 키로 바꾸는 계산 (Qt 없음).

화면에서 본을 끌면 '표준 프레임의 월드 회전/이동' 이 나온다. 레이어 키는 '부모 로컬 기준 변화량' 이므로 지금 보이는 부모의 월드 회전으로 바꿔 준다.
레이어 가중치가 1 보다 작으면 화면에는 가중치가 곱해진 값이 보이므로, 끌린 만큼 화면이 움직이도록 키 값을 거꾸로 계산한다.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from . import quat as Q
from .layers import Layer

MIN_WEIGHT = 0.05


def editable(layer: Optional[Layer]) -> Optional[str]:
    """편집할 수 없는 이유 (편집 가능하면 None)."""
    if layer is None:
        return "지금은 레이어가 적용되지 않은 상태(원본)이거나 레이어가 없습니다. 목록에서 레이어를 고르거나 '＋ 새 레이어'를 누르세요."
    if layer.mute:
        return "레이어 '%s' 이(가) 꺼져 있습니다. 켜야 눈에 보이게 고칠 수 있어요." % layer.name
    if layer.weight < MIN_WEIGHT:
        return "레이어 '%s' 의 가중치가 거의 0 이라 고쳐도 보이지 않습니다. 가중치를 올려 주세요." % layer.name
    return None


def _eff(layer: Layer, j: int, f: int):
    r, p = layer.effective(j, f + 1)
    return r[f], p[f]


def rot_edit(layer: Layer, j: int, f: int, q_view, Ft, Wp_parent: Optional[np.ndarray]) -> bool:
    """본 j 를 프레임 f 에서 q_view(표준 프레임 월드 회전)만큼 더 돌린 것처럼 보이도록 키를 쓴다. Wp_parent: 지금 보이는 부모의 월드 회전(파일 프레임, 루트는 None)."""
    if editable(layer) is not None:
        return False
    Ft = np.asarray(Ft, dtype=float)
    d_w = Q.mul(Q.conj(Ft), Q.mul(np.asarray(q_view, dtype=float), Ft))        # 파일 프레임 월드 변화
    Wp = np.array([1.0, 0, 0, 0]) if Wp_parent is None else np.asarray(Wp_parent, dtype=float)
    qd = Q.mul(Q.conj(Wp), Q.mul(d_w, Wp))                                      # 부모 로컬 기준 변화
    w = float(np.clip(layer.weight, 0.0, 1.0))
    cur_eff = _eff(layer, j, f)[0]
    tgt_eff = Q.mul(qd, cur_eff)                                                # 화면에 보여야 할 효과
    idn = np.array([1.0, 0, 0, 0])
    new = tgt_eff if w >= 1.0 else Q.slerp(idn, tgt_eff, 1.0 / w)
    layer.set_key(j, f, rot=Q.normalize(new))
    return True


def pos_edit(layer: Layer, j: int, f: int, dview, Ft, Wp_parent: Optional[np.ndarray]) -> bool:
    """본 j 를 프레임 f 에서 dview(표준 프레임 월드 이동 벡터)만큼 더 옮긴 것처럼 보이도록 키를 쓴다."""
    if editable(layer) is not None:
        return False
    Ft = np.asarray(Ft, dtype=float)
    dw = Q.rotate(Q.conj(Ft), np.asarray(dview, dtype=float))
    Wp = np.array([1.0, 0, 0, 0]) if Wp_parent is None else np.asarray(Wp_parent, dtype=float)
    local = Q.rotate(Q.conj(Wp), dw)
    w = float(np.clip(layer.weight, 0.0, 1.0))
    k = layer.set_key(j, f)
    layer.set_key(j, f, pos=k.pos + local / w)
    return True
