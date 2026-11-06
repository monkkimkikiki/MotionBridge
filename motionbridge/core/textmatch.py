"""검색칸 공통: 쉼표(,)로 나눈 낱말은 '모두 포함'(AND)으로 찾는다.  예) character,attack -> 둘 다 들어 있는 것만."""
from __future__ import annotations

import re
from typing import List

_OPEN, _CLOSE = "([{", ")]}"


def split_top_level(text: str, sep: str = ",") -> List[str]:
    """괄호·대괄호·중괄호 밖의 sep 로만 나눈다 (정규식 안의 a{2,3}, [a,b], (x|y,z) 는 그대로 둔다). 역슬래시 뒤 글자는 건너뛴다."""
    out, cur, depth, i = [], [], 0, 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            cur.append(text[i:i + 2])
            i += 2
            continue
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            depth = max(depth - 1, 0)
        if ch == sep and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    out.append("".join(cur))
    return out


def parts(text: str) -> List[str]:
    """쉼표로 나눈 비어 있지 않은 조각 (앞뒤 공백 제거)."""
    return [p.strip() for p in (text or "").split(",") if p.strip()]


def terms(text: str) -> List[str]:
    """목록 거르기용: 쉼표와 공백 모두 '모두 포함' 구분자. 소문자."""
    return [t for t in re.split(r"[,\s]+", (text or "").lower()) if t]


def contains_all(hay: str, text: str) -> bool:
    h = (hay or "").lower()
    return all(t in h for t in terms(text))


def and_regex(pattern: str) -> str:
    """쉼표(정규식 바깥)로 나뉜 검색식을 '모두 포함'하는 하나의 정규식으로 바꾼다. 쉼표가 없으면 그대로.
    앞쪽의 (?i) 같은 옵션은 유지하고, 쉼표를 쓴 경우는 대소문자를 구분하지 않는다."""
    pat = (pattern or "").strip()
    segs = [s.strip() for s in split_top_level(pat) if s.strip()]
    if len(segs) < 2:
        return pat
    flags = "(?i)"
    m = re.match(r"^\(\?([a-z]+)\)", segs[0])
    if m:
        flags = "(?%s)" % "".join(sorted(set(m.group(1)) | {"i"}))
        segs[0] = segs[0][m.end():]
    return flags + "^" + "".join("(?=.*(?:%s))" % s for s in segs if s) + ".*$"
