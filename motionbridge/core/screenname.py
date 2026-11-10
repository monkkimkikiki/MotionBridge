"""화면 글자로 캐릭터 찾기 + AI 답 정리. AI 에게 '이 캐릭터가 누구냐'를 묻는 대신, 화면에 적힌 이름을 읽게 해서 카탈로그에서 바로 찾는다.

  * read_names: 장면 몇 장에서 이름 글자를 읽고 -> 카탈로그(영/한 표시 이름)에서 찾는다. 확실한 것만 Detection 으로 돌려준다.
  * canonicalize: AI 가 낸 이름(ID, 줄 통째, 영어/한국어 이름)을 대표 메시 이름으로 통일한다 -> 같은 캐릭터로 합쳐지고 메시 후보가 정확해진다.
Qt 에 의존하지 않는다. AI 는 Provider.read_texts 로만 부른다.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Sequence

from . import charcatalog, vision

CONFIDENT = 0.85      # 이 이상이면 화면 글자로 '확인'했다고 본다
TEXT_CONF = 0.95      # 확인한 캐릭터의 확신 값


def read_names(prov: "vision.Provider", frame_paths: Sequence[str], cat: "charcatalog.Catalog", max_frames: int = 3,
               on_error: Optional[Callable] = None) -> List["vision.Detection"]:
    """장면에서 이름 글자를 읽어 카탈로그와 맞춰 본다. 같은 캐릭터는 한 번만. 읽기에 실패한 장면은 건너뛴다 (on_error 로 알림)."""
    out: List[vision.Detection] = []
    seen = set()
    paths = list(frame_paths)
    if len(paths) > max_frames:   # 앞뒤에 치우치지 않게 고르게 고른다
        paths = [paths[int(i * len(paths) / max_frames)] for i in range(max_frames)]
    for p in paths:
        try:
            texts = prov.read_texts(p)
        except vision.VisionError as e:
            if on_error:
                on_error(e)
            continue
        for t in texts:
            hit = cat.find(t, 1)
            if not hit or hit[0].score < CONFIDENT:
                continue
            ch = hit[0].char
            if ch.id in seen:
                continue
            seen.add(ch.id)
            out.append(vision.Detection(ch.body_name(), TEXT_CONF, '화면 글자 "%s" → %s' % (t, ch.display()), None, p))
    return out


def canonicalize(dets: Sequence["vision.Detection"], cat: "charcatalog.Catalog") -> List["vision.Detection"]:
    """AI 가 낸 이름들을 카탈로그의 대표 메시 이름으로 바꾼다 (제자리 수정). 카탈로그에 없는 이름은 그대로 둔다."""
    for d in dets:
        if d.name.strip().lower() not in vision.UNKNOWN_WORDS:
            ch = cat.resolve(d.name)
            if ch is not None:
                d.name = ch.body_name()
        fixed: List[str] = []
        for a in d.alts:
            ch = cat.resolve(a)
            n = ch.body_name() if ch is not None else a
            if n != d.name and n not in fixed:
                fixed.append(n)
        d.alts = fixed
    return list(dets)
