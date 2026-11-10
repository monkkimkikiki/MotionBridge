"""곡선 그래프 편집 계산 (Qt 없음): 본 하나의 채널 곡선(회전 오일러 °/위치)과, 그래프에서 끈 값을 보정 레이어의 키로 바꾸는 일.

그래프에 보이는 값은 '지금 상태의 최종 로컬 값'이다. 키를 위아래로 끌면 '그 프레임의 최종 값이 끈 값이 되도록' 활성 레이어의 키를 고친다
(가중치가 1 미만이어도 보이는 값이 정확히 따라오도록 거꾸로 계산). 끌기 한 번 동안은 누르기 시작할 때의 값을 기준으로 매번 다시 계산하므로
화면이 다시 계산되기 전에 마우스가 여러 번 움직여도 겹쳐서 더해지지 않는다."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

import numpy as np

from . import quat as Q
from .layers import Key, Layer

CHANNELS = ("rx", "ry", "rz", "px", "py", "pz")
LABELS = {"rx": "회전 X", "ry": "회전 Y", "rz": "회전 Z", "px": "위치 X", "py": "위치 Y", "pz": "위치 Z"}
ROT, POS = ("rx", "ry", "rz"), ("px", "py", "pz")


def euler_curve(rot: np.ndarray) -> np.ndarray:
    """(T,4) 로컬 회전 -> (T,3) 오일러 XYZ(도), 시간 방향으로 360° 점프 없이 이어 붙인다."""
    e = np.asarray(Q.to_euler(Q.make_continuous(Q.normalize(rot)), "XYZ"), dtype=float).reshape(-1, 3)
    return np.degrees(np.unwrap(np.radians(e), axis=0))


def channel_values(anim, j: int) -> Dict[str, np.ndarray]:
    """관절 j 의 모든 채널 곡선 {채널: (T,)}."""
    e = euler_curve(anim.rot[:, j])
    p = np.asarray(anim.pos[:, j], dtype=float)
    return {"rx": e[:, 0], "ry": e[:, 1], "rz": e[:, 2], "px": p[:, 0], "py": p[:, 1], "pz": p[:, 2]}


@dataclass
class Drag:
    """키 하나를 끄는 동안의 기준 (누른 순간의 키와 그 프레임의 최종 값)."""
    j: int
    f: int
    key0: Key
    rot0: np.ndarray        # 최종 로컬 회전 (4,)
    euler0: np.ndarray      # 최종 오일러 (3,) 도 — 시간 방향으로 이어 붙인 값
    pos0: np.ndarray        # 최종 로컬 위치 (3,)


def begin_drag(layer: Layer, anim, j: int, f: int) -> Optional[Drag]:
    """레이어에 (j, f) 키가 있으면 끌기 기준을 만든다. 없으면 None."""
    k = layer.keys.get(int(j), {}).get(int(f))
    if k is None or not (0 <= f < anim.n_frames):
        return None
    e = euler_curve(anim.rot[:, j])[f]
    return Drag(int(j), int(f), Key(k.rot.copy(), k.pos.copy()), anim.rot[f, j].copy(), e, np.asarray(anim.pos[f, j], dtype=float).copy())


def drag_to(layer: Layer, d: Drag, ch: str, value: float) -> bool:
    """채널 ch 의 (d.f 프레임) 최종 값을 value 로 만들도록 키를 다시 쓴다. 끌기를 시작한 기준에서 매번 다시 계산한다."""
    from .layeredit import editable
    if editable(layer) is not None or ch not in CHANNELS:
        return False
    w = float(np.clip(layer.weight, 0.0, 1.0))
    idn = np.array([1.0, 0, 0, 0])
    rot, pos = d.key0.rot.copy(), d.key0.pos.copy()
    if ch in ROT:
        e = d.euler0.copy()
        e[ROT.index(ch)] = float(value)
        target = Q.from_euler(e, "XYZ")
        qd = Q.mul(target, Q.conj(d.rot0))                            # 최종 회전이 target 이 되려면 왼쪽에서 곱할 변화
        eff = d.key0.rot if w >= 1.0 else Q.slerp(idn, d.key0.rot, w)
        want = Q.mul(qd, eff)
        rot = Q.normalize(want if w >= 1.0 else Q.slerp(idn, want, 1.0 / w))
    else:
        i = POS.index(ch)
        dp = np.zeros(3)
        dp[i] = float(value) - d.pos0[i]
        pos = d.key0.pos + dp / w
    layer.set_key(d.j, d.f, rot=rot, pos=pos)
    return True


def add_key(layer: Layer, j: int, f: int) -> bool:
    """(j, f) 에 지금 보이는 값 그대로 키를 더한다 (이미 있으면 그대로)."""
    from .layeredit import editable
    if editable(layer) is not None:
        return False
    layer.set_key(j, f)
    return True
