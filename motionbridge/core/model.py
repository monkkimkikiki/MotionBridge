"""스켈레톤 / 애니메이션 / 메시 데이터 모델과 순기구학(FK)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple, List, Optional, Sequence

import numpy as np

from . import quat as Q


def topo_order(parents: Sequence[int]) -> List[int]:
    """부모가 항상 자식보다 먼저 오도록 정렬한 인덱스 목록(안정 정렬)."""
    n = len(parents)
    children = [[] for _ in range(n)]
    roots = []
    for i, p in enumerate(parents):
        if p < 0:
            roots.append(i)
        else:
            children[p].append(i)
    order: List[int] = []
    stack = list(reversed(roots))
    while stack:
        i = stack.pop()
        order.append(i)
        for c in reversed(children[i]):
            stack.append(c)
    if len(order) != n:
        raise ValueError("스켈레톤 계층에 순환이 있습니다.")
    return order


class Skeleton:
    """관절 계층. 모든 관절은 부모보다 뒤에 온다(parents[j] < j)."""

    def __init__(self, names, parents, offsets, rest_rot=None, name: str = "skeleton"):
        n = len(names)
        self.names: List[str] = list(names)
        self.parents = np.asarray(parents, dtype=int).reshape(n)
        self.offsets = np.asarray(offsets, dtype=float).reshape(n, 3)
        self.rest_rot = Q.identity((n,)) if rest_rot is None else Q.normalize(np.asarray(rest_rot, dtype=float).reshape(n, 4))
        self.name = name
        for j in range(n):
            if self.parents[j] >= j:
                raise ValueError("parents[%d]=%d: 부모는 자식보다 앞에 와야 합니다(topo_order 사용)." % (j, self.parents[j]))
        self.children: List[List[int]] = [[] for _ in range(n)]
        for j, p in enumerate(self.parents):
            if p >= 0:
                self.children[p].append(j)
        self._index = {nm: i for i, nm in enumerate(self.names)}
        self._bind = None

    @property
    def n(self) -> int:
        return len(self.names)

    def index(self, name: str) -> int:
        return self._index[name]

    def find(self, name: str) -> int:
        return self._index.get(name, -1)

    def bind_world(self):
        """바인드 포즈의 월드 회전 (J,4) / 위치 (J,3)."""
        if self._bind is None:
            wr, wp = fk(self, self.rest_rot[None], self.offsets[None])
            self._bind = (wr[0], wp[0])
        return self._bind

    def descendants(self, j: int) -> List[int]:
        out, stack = [], list(self.children[j])
        while stack:
            c = stack.pop()
            out.append(c)
            stack.extend(self.children[c])
        return sorted(out)

    def subtree_size(self, j: int) -> int:
        return 1 + len(self.descendants(j))

    def depth(self, j: int) -> int:
        d = 0
        while self.parents[j] >= 0:
            j = self.parents[j]
            d += 1
        return d

    def ancestors(self, j: int) -> List[int]:
        out = []
        while self.parents[j] >= 0:
            j = self.parents[j]
            out.append(j)
        return out

    def is_leaf(self, j: int) -> bool:
        return len(self.children[j]) == 0

    def roots(self) -> List[int]:
        return [j for j in range(self.n) if self.parents[j] < 0]

    def copy(self) -> "Skeleton":
        return Skeleton(self.names, self.parents.copy(), self.offsets.copy(), self.rest_rot.copy(), self.name)


@dataclass
class Animation:
    """고정 fps 로 리샘플된 애니메이션. rot (T,J,4) 로컬 회전, pos (T,J,3) 로컬 위치."""

    name: str
    fps: float
    rot: np.ndarray
    pos: np.ndarray

    @property
    def n_frames(self) -> int:
        return self.rot.shape[0]

    @property
    def duration(self) -> float:
        return self.n_frames / float(self.fps)

    def copy(self) -> "Animation":
        return Animation(self.name, self.fps, self.rot.copy(), self.pos.copy())

    def slice(self, start: int, end: int) -> "Animation":
        return Animation(self.name, self.fps, self.rot[start:end].copy(), self.pos[start:end].copy())

    @staticmethod
    def rest(skel: Skeleton, n_frames: int = 1, fps: float = 30.0, name: str = "rest") -> "Animation":
        rot = np.repeat(skel.rest_rot[None], n_frames, axis=0)
        pos = np.repeat(skel.offsets[None], n_frames, axis=0)
        return Animation(name, fps, rot, pos)


def fk(skel: Skeleton, rot: np.ndarray, pos: np.ndarray):
    """순기구학. rot (T,J,4), pos (T,J,3) 로컬 -> 월드 회전 (T,J,4), 월드 위치 (T,J,3)."""
    rot = np.asarray(rot, dtype=float)
    pos = np.asarray(pos, dtype=float)
    wr = np.empty_like(rot)
    wp = np.empty_like(pos)
    for j in range(skel.n):
        p = skel.parents[j]
        if p < 0:
            wr[:, j] = rot[:, j]
            wp[:, j] = pos[:, j]
        else:
            wr[:, j] = Q.mul(wr[:, p], rot[:, j])
            wp[:, j] = wp[:, p] + Q.rotate(wr[:, p], pos[:, j])
    return wr, wp


def world_to_local(skel: Skeleton, wr: np.ndarray, wp: np.ndarray):
    """월드 회전/위치 (T,J,*) -> 로컬 회전/위치."""
    rot = np.empty_like(wr)
    pos = np.empty_like(wp)
    for j in range(skel.n):
        p = skel.parents[j]
        if p < 0:
            rot[:, j] = wr[:, j]
            pos[:, j] = wp[:, j]
        else:
            rot[:, j] = Q.mul(Q.conj(wr[:, p]), wr[:, j])
            pos[:, j] = Q.rotate(Q.conj(wr[:, p]), wp[:, j] - wp[:, p])
    return rot, pos


@dataclass
class Mesh:
    """스킨드 메시. vertices 는 바인드 포즈의 월드 좌표."""

    name: str
    vertices: np.ndarray  # (V,3)
    faces: np.ndarray  # (F,3) int
    joints: np.ndarray  # (V,K) int, 스켈레톤 인덱스
    weights: np.ndarray  # (V,K)
    uv: Optional[np.ndarray] = None
    color: Optional[Tuple[float, float, float, float]] = None  # 머티리얼 기본 색(RGBA 0..1)
    texture: Optional[bytes] = None  # 기본 색 텍스처(PNG/JPEG 원본 바이트). 화면 표시용
    material: Optional[str] = None  # 머티리얼 이름 (색이 없을 때 이름으로 구분 색을 정한다)

    def skin(self, skel: Skeleton, wr: np.ndarray, wp: np.ndarray, sc: Optional[np.ndarray] = None) -> np.ndarray:
        """한 프레임의 월드 회전 (J,4) / 위치 (J,3) 로 선형 블렌드 스키닝. sc (J,) 는 본별 누적 크기 배율(없으면 1) — 그 본의 위치를 기준으로 키운다."""
        bwr, bwp = skel.bind_world()
        rm = Q.mul(wr, Q.conj(bwr))
        rb = Q.rotate(rm, bwp)
        if sc is not None:
            sc = np.asarray(sc, dtype=float).reshape(-1)
            tm = wp - sc[:, None] * rb
        else:
            tm = wp - rb
        out = np.zeros_like(self.vertices)
        for k in range(self.joints.shape[1]):
            j = self.joints[:, k]
            w = self.weights[:, k : k + 1]
            v = Q.rotate(rm[j], self.vertices)
            if sc is not None:
                v = v * sc[j][:, None]
            out += w * (v + tm[j])
        return out


@dataclass
class Model:
    """불러온 파일 하나(스켈레톤 + 클립 목록 + 선택적 메시)."""

    path: str
    skeleton: Skeleton
    animations: List[Animation] = field(default_factory=list)
    meshes: List[Mesh] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
