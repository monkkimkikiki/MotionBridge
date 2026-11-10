"""IK 구간 직접 지정의 계산 (Qt 없음). 한 체인의 구간 목록은 [[시작, 끝, 켬(True)/끔(False)], ...] (프레임 0부터, 끝 포함).

자동 접지 판정 위에 덮어쓰는 값이라, '켜기'는 그 프레임들에서 발(손)을 한 점에 고정하고 '끄기'는 그 프레임들에서 IK 없이 원래 동작을 따르게 한다.
새로 지정한 구간이 이전 지정과 겹치면 새 것이 이긴다 (겹친 부분만 덮어쓰고 나머지는 남긴다)."""
from __future__ import annotations

from typing import List, Optional, Sequence

import numpy as np


def normalize(ranges: Sequence[Sequence], T: int) -> List[list]:
    """겹침을 풀어(나중 지정 우선) 시작 순서의 겹치지 않는 구간으로 정리한다. 범위 밖은 잘라낸다. 이어진 같은 종류는 하나로 합친다."""
    st = np.full(max(int(T), 0), -1, dtype=int)
    for a, b, on in ranges:
        a, b = max(min(int(a), int(b)), 0), min(max(int(a), int(b)), T - 1)
        if b >= a:
            st[a:b + 1] = 1 if on else 0
    out: List[list] = []
    i = 0
    while i < T:
        if st[i] < 0:
            i += 1
            continue
        j = i
        while j + 1 < T and st[j + 1] == st[i]:
            j += 1
        out.append([i, j, bool(st[i])])
        i = j + 1
    return out


def add(ranges: Sequence[Sequence], a: int, b: int, on: bool, T: int) -> List[list]:
    return normalize(list(ranges) + [[a, b, on]], T)


def remove(ranges: Sequence[Sequence], index: int, T: int) -> List[list]:
    r = normalize(ranges, T)
    if 0 <= index < len(r):
        del r[index]
    return r


def runs(mask: Optional[np.ndarray]) -> List[tuple]:
    """(T,) bool 에서 True 가 이어지는 구간 목록 [(시작, 끝), ...] (끝 포함)."""
    if mask is None or len(mask) == 0:
        return []
    m = np.asarray(mask, dtype=bool)
    idx = np.flatnonzero(m)
    if len(idx) == 0:
        return []
    cuts = np.flatnonzero(np.diff(idx) > 1) + 1
    return [(int(g[0]), int(g[-1])) for g in np.split(idx, cuts)]


def describe(rngs: Sequence[tuple]) -> str:
    """사람이 읽는 구간 문장 (프레임은 1부터)."""
    if not rngs:
        return "없음"
    return ", ".join("%d~%d" % (a + 1, b + 1) if b > a else "%d" % (a + 1) for a, b in rngs)
