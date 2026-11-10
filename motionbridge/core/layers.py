"""보정 레이어 (비파괴 키프레임 수정) — 모션빌더의 '애니메이션 레이어(Additive)'에 해당한다.

리타깃 결과(타깃 모델의 로컬 회전/위치) 위에 얹는 수정 층이다. 원본 모션과 리타깃 결과는 그대로 두고, 레이어마다
본별 키(프레임 → 회전 변화량·위치 변화량)를 가진다. 키 사이는 보간하고(회전은 구면 보간), 첫/마지막 키 밖은 그 값을 유지한다.
 - 회전 변화량은 '부모 기준(부모 로컬 축)' 이다:  새 로컬 회전 = 변화량 * 원래 로컬 회전  (하위 본은 FK 로 따라온다)
 - 위치 변화량은 부모 로컬 축 기준 벡터(타깃 단위)를 로컬 위치에 더한다.
 - 레이어 가중치(0~1)로 세기를 줄이고, 끄기(mute)로 잠시 빼서 비교한다. 레이어는 아래에서 위로 차례로 쌓인다.
프레임 번호는 '프레임 편집(자르기/속도)까지 적용된 결과 모션' 기준이다. Qt 를 쓰지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import numpy as np

from . import quat as Q
from .model import Animation

_ID = np.array([1.0, 0.0, 0.0, 0.0])


@dataclass
class Key:
    rot: np.ndarray = field(default_factory=lambda: _ID.copy())      # (4,) 부모 기준 회전 변화량 (w,x,y,z)
    pos: np.ndarray = field(default_factory=lambda: np.zeros(3))     # (3,) 부모 로컬 위치 변화량

    def is_identity(self, eps: float = 1e-9) -> bool:
        return float(np.max(np.abs(self.pos))) < eps and abs(abs(float(self.rot[0])) - 1.0) < eps


@dataclass
class Layer:
    name: str = "레이어 1"
    weight: float = 1.0
    mute: bool = False
    keys: Dict[int, Dict[int, Key]] = field(default_factory=dict)   # 관절 -> 프레임 -> 키

    # ---- 키 편집
    def set_key(self, j: int, f: int, rot: Optional[np.ndarray] = None, pos: Optional[np.ndarray] = None) -> Key:
        k = self.keys.setdefault(int(j), {}).get(int(f))
        if k is None:
            r0, p0 = self.eval_at(j, f)             # 새 키는 지금 그 프레임에 보이던 값에서 시작한다 (눈에 띄는 튐 방지)
            k = Key(r0, p0)
            self.keys[int(j)][int(f)] = k
        if rot is not None:
            k.rot = Q.normalize(np.asarray(rot, dtype=float))
        if pos is not None:
            k.pos = np.asarray(pos, dtype=float).copy()
        return k

    def remove_key(self, j: int, f: int) -> bool:
        d = self.keys.get(int(j))
        if not d or int(f) not in d:
            return False
        del d[int(f)]
        if not d:
            del self.keys[int(j)]
        return True

    def clear_joint(self, j: int) -> None:
        self.keys.pop(int(j), None)

    def frames(self, j: int) -> List[int]:
        return sorted(self.keys.get(int(j), {}))

    def joints(self) -> List[int]:
        return sorted(self.keys)

    def n_keys(self) -> int:
        return sum(len(d) for d in self.keys.values())

    def next_key(self, j: int, f: int, step: int = 1) -> Optional[int]:
        fr = self.frames(j)
        c = [x for x in fr if (x > f if step > 0 else x < f)]
        return (min(c) if step > 0 else max(c)) if c else None

    # ---- 평가
    def eval_at(self, j: int, f: float):
        """관절 j 의 프레임 f 에서의 (회전 (4,), 위치 (3,)) — 가중치 적용 전의 원래 값."""
        r, p = self.eval(j, int(f) + 1)
        return r[int(f)].copy(), p[int(f)].copy()

    def eval(self, j: int, T: int):
        """관절 j 의 (회전 (T,4), 위치 (T,3)) 곡선 — 가중치 적용 전."""
        rot = np.repeat(_ID[None], T, axis=0)
        pos = np.zeros((T, 3))
        d = self.keys.get(int(j))
        if not d or T <= 0:
            return rot, pos
        fr = sorted(d)
        qs = [d[f].rot for f in fr]
        ps = [d[f].pos for f in fr]
        t = np.arange(T)
        if len(fr) == 1:
            rot[:] = qs[0]
            pos[:] = ps[0]
            return rot, pos
        arr = np.array(fr)
        idx = np.clip(np.searchsorted(arr, t, side="right") - 1, 0, len(fr) - 2)
        f0, f1 = arr[idx], arr[idx + 1]
        u = np.clip((t - f0) / np.maximum(f1 - f0, 1), 0.0, 1.0)
        qa, qb = np.array(qs)[idx], np.array(qs)[idx + 1]
        rot = Q.slerp(qa, qb, u)
        pa, pb = np.array(ps)[idx], np.array(ps)[idx + 1]
        pos = pa + (pb - pa) * u[:, None]
        rot[t <= arr[0]] = qs[0]                    # 첫 키 앞/마지막 키 뒤는 값을 유지
        pos[t <= arr[0]] = ps[0]
        rot[t >= arr[-1]] = qs[-1]
        pos[t >= arr[-1]] = ps[-1]
        return rot, pos

    def effective(self, j: int, T: int):
        """가중치·끄기를 반영한 곡선 (회전, 위치)."""
        rot, pos = self.eval(j, T)
        w = 0.0 if self.mute else float(np.clip(self.weight, 0.0, 1.0))
        if w >= 1.0:
            return rot, pos
        idn = np.repeat(_ID[None], T, axis=0)
        return (Q.slerp(idn, rot, w) if w > 0 else idn), pos * w

    # ---- 저장
    def to_dict(self, names: Sequence[str]) -> dict:
        ks = {}
        for j, d in self.keys.items():
            if 0 <= j < len(names):
                ks[names[j]] = [[int(f), [float(x) for x in k.rot], [float(x) for x in k.pos]] for f, k in sorted(d.items())]
        return {"name": self.name, "weight": float(self.weight), "mute": bool(self.mute), "keys": ks}

    @staticmethod
    def from_dict(d: dict, index: Dict[str, int], warns: Optional[List[str]] = None) -> "Layer":
        lay = Layer(str(d.get("name", "레이어")), float(d.get("weight", 1.0)), bool(d.get("mute", False)))
        for nm, lst in (d.get("keys") or {}).items():
            if nm not in index:
                if warns is not None:
                    warns.append("레이어 '%s' 의 '%s' 본이 타깃에 없어 키를 건너뜀" % (lay.name, nm))
                continue
            for f, r, p in lst:
                lay.keys.setdefault(index[nm], {})[int(f)] = Key(Q.normalize(np.array(r, float)), np.array(p, float))
        return lay


class LayerStack:
    """레이어 묶음 (아래에서 위로 쌓임)과 지금 편집 중인 레이어."""

    def __init__(self) -> None:
        self.layers: List[Layer] = []
        self.active = -1

    def __len__(self) -> int:
        return len(self.layers)

    def add(self, name: Optional[str] = None) -> Layer:
        lay = Layer(name or "레이어 %d" % (len(self.layers) + 1))
        self.layers.append(lay)
        self.active = len(self.layers) - 1
        return lay

    def remove(self, i: int) -> None:
        if 0 <= i < len(self.layers):
            del self.layers[i]
            self.active = min(self.active, len(self.layers) - 1)

    def current(self) -> Optional[Layer]:
        return self.layers[self.active] if 0 <= self.active < len(self.layers) else None

    def move(self, i: int, d: int) -> int:
        j = i + d
        if 0 <= i < len(self.layers) and 0 <= j < len(self.layers):
            self.layers[i], self.layers[j] = self.layers[j], self.layers[i]
            if self.active == i:
                self.active = j
            elif self.active == j:
                self.active = i
            return j
        return i

    def has_effect(self) -> bool:
        return any((not l.mute) and l.weight > 0 and l.n_keys() for l in self.layers)

    def total_keys(self) -> int:
        return sum(l.n_keys() for l in self.layers)

    def snapshot(self):
        """실행 취소용 상태 (순수 튜플 — 같은지 비교할 수 있다)."""
        return (self.active, tuple(
            (l.name, float(l.weight), bool(l.mute),
             tuple((j, tuple((f, tuple(float(x) for x in k.rot), tuple(float(x) for x in k.pos)) for f, k in sorted(d.items())))
                   for j, d in sorted(l.keys.items())))
            for l in self.layers))

    @staticmethod
    def from_snapshot(snap) -> "LayerStack":
        st = LayerStack()
        active, layers = snap
        for name, w, mute, joints in layers:
            lay = Layer(name, w, mute)
            for j, ks in joints:
                lay.keys[j] = {f: Key(np.array(r, float), np.array(p, float)) for f, r, p in ks}
            st.layers.append(lay)
        st.active = active
        return st

    def to_list(self, names: Sequence[str]) -> list:
        return [l.to_dict(names) for l in self.layers]

    @staticmethod
    def from_list(data: list, names: Sequence[str], warns: Optional[List[str]] = None) -> "LayerStack":
        idx = {n: i for i, n in enumerate(names)}
        st = LayerStack()
        st.layers = [Layer.from_dict(d, idx, warns) for d in (data or [])]
        st.active = len(st.layers) - 1
        return st


def apply_layers(anim: Animation, stack: Optional[LayerStack]) -> Animation:
    """레이어를 모션에 얹은 새 Animation (레이어가 없거나 효과가 없으면 입력을 그대로 돌려준다)."""
    if stack is None or not stack.has_effect():
        return anim
    out = anim.copy()
    T, J = out.rot.shape[0], out.rot.shape[1]
    for lay in stack.layers:
        if lay.mute or lay.weight <= 0:
            continue
        for j in lay.joints():
            if not (0 <= j < J):
                continue
            r, p = lay.effective(j, T)
            out.rot[:, j] = Q.mul(r, out.rot[:, j])
            out.pos[:, j] = out.pos[:, j] + p
    out.rot = Q.make_continuous(Q.normalize(out.rot))
    return out
