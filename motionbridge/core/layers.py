"""보정 레이어 (비파괴 키프레임 수정) — 모션빌더의 '애니메이션 레이어(Additive)'에 해당한다.

리타겟 결과(타깃 모델의 로컬 회전/위치) 위에 얹는 수정 층이다. 원본 모션과 리타겟 결과는 그대로 두고, 레이어마다
본별 키(프레임 → 회전 변화량·위치 변화량)를 가진다. 키 사이는 보간하고(회전은 구면 보간), 첫/마지막 키 밖은 그 값을 유지한다 (맥스 레이어처럼: 키가 하나면 전체 구간이 그만큼 바뀌고,
다른 키(예: 변화 0 인 '원본 키')를 찍어야 그 키부터 달라진다). 레이어의 '퍼짐'을 0 보다 크게 주면 끝 키 밖에서 서서히 원래 값으로 돌아간다.
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
DEFAULT_FADE = 0        # 새 레이어: 0 = 첫/마지막 키 밖에서도 그 키 값을 계속 유지 (키 하나만 찍으면 전체 구간에 적용). 0 보다 크면 그 프레임 수에 걸쳐 원래 값으로 돌아간다


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
    fade: int = DEFAULT_FADE

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
        arr = np.array(fr)
        if len(fr) == 1:
            rot[:] = qs[0]
            pos[:] = ps[0]
        else:
            idx = np.clip(np.searchsorted(arr, t, side="right") - 1, 0, len(fr) - 2)
            f0, f1 = arr[idx], arr[idx + 1]
            u = np.clip((t - f0) / np.maximum(f1 - f0, 1), 0.0, 1.0)
            qa, qb = np.array(qs)[idx], np.array(qs)[idx + 1]
            rot = Q.slerp(qa, qb, u)
            pa, pb = np.array(ps)[idx], np.array(ps)[idx + 1]
            pos = pa + (pb - pa) * u[:, None]
            rot[t <= arr[0]] = qs[0]
            pos[t <= arr[0]] = ps[0]
            rot[t >= arr[-1]] = qs[-1]
            pos[t >= arr[-1]] = ps[-1]
        # 첫 키 앞/마지막 키 뒤: fade 프레임에 걸쳐 원래 값(변화 없음)으로 부드럽게 돌아간다 (fade=0 이면 끝 키 값을 계속 유지)
        n = max(int(self.fade), 0)
        if n > 0:
            for lo_side, edge, q_e, p_e in ((True, arr[0], qs[0], ps[0]), (False, arr[-1], qs[-1], ps[-1])):
                dist = (edge - t) if lo_side else (t - edge)
                m = dist > 0
                if not m.any():
                    continue
                x = np.clip(dist[m] / float(n), 0.0, 1.0)
                keep = 1.0 - x * x * (3 - 2 * x)                     # 1 -> 0 (smoothstep)
                rot[m] = Q.slerp(np.repeat(_ID[None], int(m.sum()), axis=0), np.repeat(q_e[None], int(m.sum()), axis=0), keep)
                pos[m] = p_e[None, :] * keep[:, None]
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
        return {"name": self.name, "weight": float(self.weight), "mute": bool(self.mute), "fade": int(self.fade), "keys": ks}

    @staticmethod
    def from_dict(d: dict, index: Dict[str, int], warns: Optional[List[str]] = None) -> "Layer":
        lay = Layer(str(d.get("name", "레이어")), float(d.get("weight", 1.0)), bool(d.get("mute", False)), fade=int(d.get("fade", 0)))      # 예전 파일은 fade 가 없으면 끝 키 값 유지
        for nm, lst in (d.get("keys") or {}).items():
            if nm not in index:
                if warns is not None:
                    warns.append("레이어 '%s' 의 '%s' 본이 타깃에 없어 키를 건너뜀" % (lay.name, nm))
                continue
            for f, r, p in lst:
                lay.keys.setdefault(index[nm], {})[int(f)] = Key(Q.normalize(np.array(r, float)), np.array(p, float))
        return lay


def fit_keys(rot: np.ndarray, pos: np.ndarray, hint, tol_deg: float = 0.1, tol_pos: float = 1e-4, max_keys: int = 400) -> Dict[int, Key]:
    """곡선(회전 (T,4)·위치 (T,3))을 적은 수의 키로 근사한다. hint 프레임(반드시 포함)에서 시작해, 키 사이 보간과 곡선의 오차가 가장 큰 프레임을
    오차가 허용치(회전 도 · 위치) 안에 들 때까지 키로 더한다. 키 범위 밖은 키 값이 유지되므로 곡선도 그 범위 밖에서는 일정해야 한다."""
    T = rot.shape[0]
    frames = sorted({int(f) for f in hint if 0 <= int(f) < T})
    if not frames:
        return {}
    keys = {f: Key(Q.normalize(rot[f].copy()), pos[f].copy()) for f in frames}
    lo, hi = frames[0], frames[-1]
    tol_r = np.radians(tol_deg)
    for _ in range(max_keys):
        tmp = Layer(keys={0: dict(keys)}, fade=0)
        r, p = tmp.eval(0, T)
        er = Q.angle_between(r[lo:hi + 1], rot[lo:hi + 1])
        ep = np.linalg.norm(p[lo:hi + 1] - pos[lo:hi + 1], axis=1)
        bad = np.maximum(er / max(tol_r, 1e-12), ep / max(tol_pos, 1e-12))
        i = int(np.argmax(bad))
        if bad[i] <= 1.0:
            break
        f = lo + i
        if f in keys:
            break
        keys[f] = Key(Q.normalize(rot[f].copy()), pos[f].copy())
    return keys


def combine_effective(layers: Sequence["Layer"], j: int, T: int):
    """아래에서 위로 쌓인 레이어들의 관절 j 효과를 하나로 (회전 (T,4), 위치 (T,3)). 나중 레이어가 왼쪽에 곱해진다. 꺼진 레이어는 효과 없음."""
    rot = np.repeat(_ID[None], T, axis=0)
    pos = np.zeros((T, 3))
    for lay in layers:
        r, p = lay.effective(j, T)
        rot = Q.mul(r, rot)
        pos = pos + p
    return rot, pos


def merge_layers(layers: Sequence["Layer"], T: int, name: Optional[str] = None, tol_deg: float = 0.1, tol_pos: float = 1e-4) -> "Layer":
    """여러 레이어를 효과가 같은(허용 오차 안) 레이어 하나로 합친다. 가중치는 값에 구워 넣고 새 레이어의 가중치는 1.
    키는 원래 키 프레임에서 시작해 오차가 큰 곳에만 더 찍는다 (모든 프레임에 키를 찍지 않는다)."""
    out = Layer(name or layers[0].name, fade=0)       # 퍼짐 구간까지 키로 구워 넣으므로 합친 레이어는 끝 값 유지(fade 0)로 충분하다
    F = max([max(int(l.fade), 0) for l in layers] + [0])
    for j in sorted({j for l in layers for j in l.keys}):
        hint = sorted({f for l in layers for f in l.keys.get(j, {})})
        if hint and F:
            hint = sorted(set(hint) | {max(hint[0] - F, 0), min(hint[-1] + F, T - 1)})
        rot, pos = combine_effective(layers, j, T)
        ks = fit_keys(rot, pos, hint, tol_deg, tol_pos)
        if ks and not all(k.is_identity(1e-7) for k in ks.values()):
            out.keys[j] = ks
    return out


class LayerStack:
    """레이어 묶음(아래에서 위로 쌓임)과 지금 보고 있는 '상태'.

    top = 적용해서 보여 줄 레이어 수 (0 = 원본 그대로, len = 전부). 그 위의 레이어는 지우지 않고 잠시 안 보일 뿐이다.
    화면·내보내기·곡선 그래프는 모두 이 상태를 따르고, 편집(키 기록)은 상태의 맨 위 레이어 (top-1)에 한다."""

    def __init__(self) -> None:
        self.layers: List[Layer] = []
        self.top = 0

    def __len__(self) -> int:
        return len(self.layers)

    # 편집 대상 레이어 번호 (원본 상태면 -1). 예전 코드와 맞추려고 active 로도 읽고 쓴다.
    @property
    def active(self) -> int:
        return self.top - 1

    @active.setter
    def active(self, i: int) -> None:
        self.top = int(np.clip(int(i) + 1, 0, len(self.layers)))

    def set_top(self, n: int) -> None:
        self.top = int(np.clip(int(n), 0, len(self.layers)))

    def add(self, name: Optional[str] = None) -> Layer:
        """지금 상태의 바로 위에 새 레이어를 쌓는다 (그 위에 있던 레이어는 한 칸씩 위로)."""
        lay = Layer(name or "레이어 %d" % (len(self.layers) + 1))
        self.layers.insert(self.top, lay)
        self.top += 1
        return lay

    def push(self, lay: Layer) -> None:
        """만들어 둔 레이어를 지금 상태 바로 위에 끼워 넣고 그 상태로 본다."""
        self.layers.insert(self.top, lay)
        self.top += 1

    def remove(self, i: int) -> None:
        if 0 <= i < len(self.layers):
            del self.layers[i]
            if i < self.top:
                self.top -= 1
            self.top = min(self.top, len(self.layers))

    def current(self) -> Optional[Layer]:
        return self.layers[self.top - 1] if 1 <= self.top <= len(self.layers) else None

    def visible(self) -> List[Layer]:
        return self.layers[:self.top]

    def move(self, i: int, d: int) -> int:
        j = i + d
        if 0 <= i < len(self.layers) and 0 <= j < len(self.layers):
            sel = self.top - 1
            self.layers[i], self.layers[j] = self.layers[j], self.layers[i]
            if sel == i:
                self.top = j + 1
            elif sel == j:
                self.top = i + 1
            return j
        return i

    def has_effect(self) -> bool:
        return any((not l.mute) and l.weight > 0 and l.n_keys() for l in self.visible())

    def total_keys(self) -> int:
        return sum(l.n_keys() for l in self.layers)

    # ---- 합치기
    def merge_down(self, i: int, T: int) -> Optional[str]:
        """레이어 i 를 바로 아래(i-1)와 합친다. 안 되는 이유를 문장으로 돌려준다 (되면 None)."""
        if not (1 <= i < len(self.layers)):
            return "합칠 아래 레이어가 없습니다."
        return self.merge_range(i - 1, i, T)

    def merge_range(self, a: int, b: int, T: int) -> Optional[str]:
        """레이어 a..b(포함)를 하나로 합친다. 꺼진 레이어가 있으면 합치지 않는다 (조용히 사라지는 것을 막기 위해)."""
        a, b = max(a, 0), min(b, len(self.layers) - 1)
        if b <= a:
            return "합칠 레이어가 둘 이상 필요합니다."
        grp = self.layers[a:b + 1]
        off = [l.name for l in grp if l.mute]
        if off:
            return "꺼진 레이어(%s)가 있어 합칠 수 없습니다. 켜거나 지운 뒤 다시 하세요." % ", ".join(off)
        merged = merge_layers(grp, T, grp[0].name)
        top = self.top
        self.layers[a:b + 1] = [merged]
        if top > b:
            self.top = top - (b - a)
        elif top > a:
            self.top = a + 1
        self.top = min(self.top, len(self.layers))
        return None

    def snapshot(self):
        """실행 취소용 상태 (순수 튜플 — 같은지 비교할 수 있다)."""
        return (self.top, tuple(
            (l.name, float(l.weight), bool(l.mute), int(l.fade),
             tuple((j, tuple((f, tuple(float(x) for x in k.rot), tuple(float(x) for x in k.pos)) for f, k in sorted(d.items())))
                   for j, d in sorted(l.keys.items())))
            for l in self.layers))

    @staticmethod
    def from_snapshot(snap) -> "LayerStack":
        st = LayerStack()
        top, layers = snap
        for name, w, mute, fade, joints in layers:
            lay = Layer(name, w, mute, fade=fade)
            for j, ks in joints:
                lay.keys[j] = {f: Key(np.array(r, float), np.array(p, float)) for f, r, p in ks}
            st.layers.append(lay)
        st.top = int(np.clip(top, 0, len(st.layers)))
        return st

    def to_list(self, names: Sequence[str]) -> list:
        return [l.to_dict(names) for l in self.layers]

    @staticmethod
    def from_list(data: list, names: Sequence[str], warns: Optional[List[str]] = None) -> "LayerStack":
        idx = {n: i for i, n in enumerate(names)}
        st = LayerStack()
        st.layers = [Layer.from_dict(d, idx, warns) for d in (data or [])]
        st.top = len(st.layers)
        return st


def apply_layers(anim: Animation, stack: Optional[LayerStack], upto: Optional[int] = None) -> Animation:
    """보이는 상태(앞의 top 개, 또는 upto 개)의 레이어를 모션에 얹은 새 Animation (효과가 없으면 입력을 그대로 돌려준다)."""
    if stack is None:
        return anim
    n = stack.top if upto is None else int(upto)
    lays = [l for l in stack.layers[:max(n, 0)] if (not l.mute) and l.weight > 0 and l.n_keys()]
    if not lays:
        return anim
    out = anim.copy()
    T, J = out.rot.shape[0], out.rot.shape[1]
    for lay in lays:
        for j in lay.joints():
            if not (0 <= j < J):
                continue
            r, p = lay.effective(j, T)
            out.rot[:, j] = Q.mul(r, out.rot[:, j])
            out.pos[:, j] = out.pos[:, j] + p
    out.rot = Q.make_continuous(Q.normalize(out.rot))
    return out
