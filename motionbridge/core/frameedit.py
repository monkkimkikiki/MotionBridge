"""프레임 구간 편집 (조각 모델): 영상 편집기처럼 모션을 '조각' 목록으로 보고, 구간별 속도 조절 · 자르기 · 삭제 · 복제 · 순서 · 정지 프레임을 한다.

AnimRef 의 영상 편집(조각 줄)과 같은 방식이다. 추출한 모션은 모든 프레임이 키이므로 키 단위가 아니라 프레임 구간 단위로 편집하면 된다.

조각(Piece) 하나 = 원본의 a~b 프레임 구간을 n 개의 결과 프레임으로 보여 주는 것 (속도 = (b-a)/n, 1 이면 원래 속도).
정지 프레임 조각은 a 한 포즈를 n 프레임 동안 유지한다. 결과 모션 = 조각들을 순서대로 이어붙이고 마지막 조각의 끝 포즈를 한 장 더한다
(편집하지 않았으면 원본과 정확히 같다). 위치는 모두 '결과 프레임 번호(0부터)'로 말한다.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from .animops import _sample
from .model import Animation

SPEED_MIN, SPEED_MAX = 0.1, 8.0
HOLD_MAX = 600


def clamp_speed(v: float) -> float:
    return float(min(SPEED_MAX, max(SPEED_MIN, float(v))))


@dataclass
class Piece:
    a: float          # 원본 시작 프레임 (소수 가능)
    b: float          # 원본 끝 프레임
    n: int            # 결과 프레임 수
    hold: bool = False  # True 면 a 한 포즈를 n 프레임 유지

    @property
    def speed(self) -> float:
        return 0.0 if self.hold else (self.b - self.a) / max(self.n, 1)


class FrameEdit:
    """조각 목록 + 되돌리기/다시하기. 화면 없이 동작한다."""

    def __init__(self, n_frames: int):
        self.T = max(int(n_frames), 1)
        self.pieces: List[Piece] = [Piece(0.0, float(self.T - 1), max(self.T - 1, 1))] if self.T > 1 else [Piece(0.0, 0.0, 1, True)]
        self._undo: List[List[Piece]] = []
        self._redo: List[List[Piece]] = []

    # ---- 상태
    def _push(self) -> None:
        self._undo.append(copy.deepcopy(self.pieces))
        self._undo = self._undo[-100:]
        self._redo.clear()

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(copy.deepcopy(self.pieces))
        self.pieces = self._undo.pop()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(copy.deepcopy(self.pieces))
        self.pieces = self._redo.pop()
        return True

    def reset(self) -> bool:
        if self.is_untouched():
            return False
        self._push()
        self.pieces = FrameEdit(self.T).pieces
        return True

    def is_untouched(self) -> bool:
        p = self.pieces
        return self.T <= 1 or (len(p) == 1 and not p[0].hold and p[0].a == 0.0 and p[0].b == float(self.T - 1) and p[0].n == self.T - 1)

    def total(self) -> int:
        """결과 프레임 수"""
        return sum(p.n for p in self.pieces) + 1

    def layout(self) -> List[Tuple[int, int]]:
        """각 조각의 결과 프레임 구간 [(시작, 끝 다음)]. 마지막 조각은 끝 포즈 한 장을 더 가진다."""
        out, t = [], 0
        for i, p in enumerate(self.pieces):
            e = t + p.n + (1 if i == len(self.pieces) - 1 else 0)
            out.append((t, e))
            t += p.n
        return out

    def index_at(self, r: int) -> int:
        r = int(min(max(r, 0), self.total() - 1))
        for i, (s, e) in enumerate(self.layout()):
            if s <= r < e:
                return i
        return len(self.pieces) - 1

    def src_pos(self, r: int) -> float:
        """결과 프레임 r 이 가리키는 원본 프레임 위치."""
        r = int(min(max(r, 0), self.total() - 1))
        i = self.index_at(r)
        p = self.pieces[i]
        s, _ = self.layout()[i]
        if p.hold:
            return p.a
        return p.a + (r - s) * (p.b - p.a) / p.n

    # ---- 나누기
    def _split_at(self, r: int) -> int:
        """결과 프레임 r 이 조각 경계가 되도록 나누고, r 에서 시작하는 조각 번호를 돌려준다 (끝 포즈 위치면 조각 수)."""
        i = self.index_at(r)
        s, _ = self.layout()[i]
        p = self.pieces[i]
        if r <= s:
            return i
        if r >= s + p.n:
            return len(self.pieces)
        k = r - s
        if p.hold:
            left, right = Piece(p.a, p.b, k, True), Piece(p.a, p.b, p.n - k, True)
        else:
            mid = p.a + k * (p.b - p.a) / p.n
            left, right = Piece(p.a, mid, k), Piece(mid, p.b, p.n - k)
        self.pieces[i : i + 1] = [left, right]
        return i + 1

    def split(self, r: int) -> bool:
        r = int(r)
        i = self.index_at(r)
        s, _ = self.layout()[i]
        if not (s < r < s + self.pieces[i].n):
            return False
        self._push()
        self._split_at(r)
        return True

    def _isolate(self, r0: int, r1: int) -> Optional[Tuple[int, int]]:
        """결과 프레임 r0~r1 을 조각 경계에 맞춰 분리하고 (첫 조각 번호, 끝 조각 번호 + 1) 을 돌려준다."""
        r0, r1 = int(min(r0, r1)), int(min(max(r0, r1), self.total() - 1))
        if r1 <= r0:
            return None
        self._split_at(r1)
        j0 = self._split_at(r0)
        starts = [s for s, _ in self.layout()]
        j1 = starts.index(r1) if r1 in starts else len(self.pieces)
        return (j0, j1) if j1 > j0 else None

    # ---- 속도
    def set_speed(self, r0: int, r1: int, v: float) -> Optional[Tuple[int, int]]:
        """결과 프레임 r0~r1 구간의 속도를 v 배로 (1 = 원래 속도). 새 구간 (시작, 끝) 결과 프레임을 돌려준다. 못 하면 None."""
        return self._retime(r0, r1, lambda p: max(1, int(round((p.b - p.a) / clamp_speed(v)))))

    def set_length(self, r0: int, r1: int, frames: int) -> Optional[Tuple[int, int]]:
        """결과 프레임 r0~r1 구간을 frames 프레임이 되게 (조각들의 비율은 유지)."""
        frames = max(int(frames), 1)
        cur = max(int(r1) - int(r0), 1)
        return self._retime(r0, r1, lambda p: max(1, int(round(p.n * frames / cur))), total=frames)

    def resize_piece(self, i: int, frames: int) -> Optional[Tuple[int, int]]:
        """i 번째 조각의 결과 길이를 frames 프레임으로 (블록 끝을 끌어 늘이거나 줄일 때). 새 (시작, 끝) 결과 프레임, 못 하면 None."""
        if not (0 <= i < len(self.pieces)):
            return None
        frames = max(int(frames), 1)
        s, _ = self.layout()[i]
        p = self.pieces[i]
        if frames == p.n:
            return None
        if p.hold:
            self._push()
            p.n = int(min(frames, HOLD_MAX))
            return s, s + p.n
        return self.set_length(s, s + p.n, frames)

    def _retime(self, r0, r1, new_n, total=None):
        r0, r1 = int(min(r0, r1)), int(max(r0, r1))
        if r1 <= r0 or r0 < 0 or r1 > self.total() - 1:
            return None
        self._push()
        iso = self._isolate(r0, r1)
        if iso is None:
            self._undo.pop()
            return None
        j0, j1 = iso
        ps = [p for p in self.pieces[j0:j1] if not p.hold]
        for p in ps:
            p.n = new_n(p)
        if total is not None and ps:  # 반올림 오차를 마지막 조각에 몰아 정확한 길이로
            diff = total - sum(p.n for p in self.pieces[j0:j1])
            ps[-1].n = max(1, ps[-1].n + diff)
        new_len = sum(p.n for p in self.pieces[j0:j1])
        start = sum(p.n for p in self.pieces[:j0])
        if not ps:
            self._undo.pop()
            return None
        return start, start + new_len

    def ramp(self, r0: int, r1: int, s0: float, s1: float, steps: int = 0) -> Optional[Tuple[int, int]]:
        """구간을 같은 길이(원본 기준)로 나눠 속도를 s0 -> s1 로 매끄럽게(곱셈 비율) 바꾼다."""
        r0, r1 = int(min(r0, r1)), int(max(r0, r1))
        if r1 <= r0 or r1 > self.total() - 1:
            return None
        s0, s1 = clamp_speed(s0), clamp_speed(s1)
        self._push()
        iso = self._isolate(r0, r1)
        if iso is None:
            self._undo.pop()
            return None
        j0, j1 = iso
        seg = [p for p in self.pieces[j0:j1] if not p.hold]
        if not seg:
            self._undo.pop()
            return None
        a, b = seg[0].a, seg[-1].b
        length = sum(p.b - p.a for p in seg)
        k = int(steps) or int(min(12, max(4, round(length / 3))))
        k = max(2, k)
        parts = []
        for m in range(k):
            aa = a + length * m / k
            bb = a + length * (m + 1) / k if m < k - 1 else a + length
            sp = s0 * (s1 / s0) ** (m / (k - 1))
            parts.append(Piece(aa, bb, max(1, int(round((bb - aa) / sp)))))
        self.pieces[j0:j1] = parts
        start = sum(p.n for p in self.pieces[:j0])
        return start, start + sum(p.n for p in parts)

    # ---- 자르기 / 지우기 / 복제 / 순서 / 정지
    def delete(self, r0: int, r1: Optional[int] = None) -> bool:
        """r0~r1 구간을 지운다. r1 이 없으면 r0 이 속한 조각 전체."""
        if r1 is None:
            i = self.index_at(r0)
            if len(self.pieces) <= 1:
                return False
            self._push()
            del self.pieces[i]
            return True
        r0, r1 = int(min(r0, r1)), int(max(r0, r1))
        if r1 <= r0:
            return False
        self._push()
        iso = self._isolate(r0, r1)
        if iso is None or iso[1] - iso[0] >= len(self.pieces):
            self._undo.pop()
            return False
        del self.pieces[iso[0] : iso[1]]
        return True

    def duplicate(self, r: int) -> int:
        """r 이 속한 조각을 바로 뒤에 복제한다. 복제본의 첫 결과 프레임을 돌려준다(-1 = 실패)."""
        i = self.index_at(r)
        self._push()
        self.pieces.insert(i + 1, copy.deepcopy(self.pieces[i]))
        return self.layout()[i + 1][0]

    def move(self, r: int, d: int) -> int:
        """r 이 속한 조각을 d 칸(-1 앞 / +1 뒤) 옮기고 그 조각의 첫 결과 프레임을 돌려준다."""
        i = self.index_at(r)
        j = i + d
        if not (0 <= j < len(self.pieces)):
            return self.layout()[i][0]
        self._push()
        self.pieces[i], self.pieces[j] = self.pieces[j], self.pieces[i]
        return self.layout()[j][0]

    def add_hold(self, r: int, frames: int) -> int:
        """결과 프레임 r 의 포즈를 frames 프레임 멈춘다. 정지 조각의 첫 결과 프레임을 돌려준다(-1 = 실패)."""
        frames = int(min(max(frames, 1), HOLD_MAX))
        pose = self.src_pos(r)
        self._push()
        j = self._split_at(r) if r > 0 else 0
        if j >= len(self.pieces):
            j = len(self.pieces)
        self.pieces.insert(j, Piece(pose, pose, frames, True))
        return self.layout()[j][0]

    def set_hold(self, r: int, frames: int) -> bool:
        i = self.index_at(r)
        if not self.pieces[i].hold:
            return False
        self._push()
        self.pieces[i].n = int(min(max(frames, 1), HOLD_MAX))
        return True

    # ---- 결과
    def positions(self) -> np.ndarray:
        """결과 각 프레임이 가리키는 원본 프레임 위치 (정수 + 소수)."""
        pos = []
        for p in self.pieces:
            if p.hold:
                pos += [p.a] * p.n
            else:
                pos += list(p.a + np.arange(p.n) * (p.b - p.a) / p.n)
        last = self.pieces[-1]
        pos.append(last.a if last.hold else last.b)
        return np.asarray(pos, float)

    def describe(self) -> str:
        sp = [p for p in self.pieces if not p.hold]
        fast = sum(1 for p in sp if p.speed > 1.0001)
        slow = sum(1 for p in sp if p.speed < 0.9999)
        holds = sum(1 for p in self.pieces if p.hold)
        bits = ["조각 %d개" % len(self.pieces)]
        if fast:
            bits.append("빠르게 %d" % fast)
        if slow:
            bits.append("느리게 %d" % slow)
        if holds:
            bits.append("정지 %d" % holds)
        return " · ".join(bits)


def render(anim: Animation, edit: FrameEdit) -> Animation:
    """조각 목록을 적용한 모션. 편집하지 않았으면 같은 객체를 돌려준다."""
    if edit.is_untouched() or anim.n_frames < 2:
        return anim
    rot, pos = _sample(anim, np.clip(edit.positions(), 0.0, anim.n_frames - 1))
    return Animation(anim.name, anim.fps, rot, pos)
