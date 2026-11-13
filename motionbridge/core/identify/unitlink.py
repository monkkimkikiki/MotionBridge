"""게임이 '데이터로 적어 둔' 유닛 ↔ 표시 이름 연결을 읽는다 — 추측(AI 확장) 없이 정답을 얻는 길.

원리 (오공 실측, 다른 UE 게임도 같은 꼴이 흔하다):
  · locres 에 CardDesc.<ID>.UnitName = "금지장로"/"Elder Jinchi"... 처럼 'ID ↔ 표시 이름' 이 있고
  · 유닛 데이터 에셋(Design/Units/<지역>/Unit_<코드명>.uasset)의 속성 어딘가에 그 ID 가 들어 있다.
  → 유닛 에셋을 JSON 으로 덤프해 ID 를 찾으면 '표시 이름 ↔ 코드명' 표가 나온다.
이 표를 엔티티 사전에 넣으면 이름 검색('아는 정보'에 금지장로)이 AI 확장 없이 즉시 정확히 맞는다.
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, Iterable, List, Sequence, Set

from .entity_store import EntityStore, norm

_CARD = re.compile(r"CardDesc\.(\d{4,8})\.UnitName$")
_NUM = re.compile(r"\b\d{4,8}\b")
_UNIT_STEM = re.compile(r"(?i)^unit_+(.+)$")


def card_names(entries: Sequence[Sequence[str]]) -> Dict[int, Dict[str, str]]:
    """locres 항목 -> 도감 ID -> {언어: 표시 이름}."""
    out: Dict[int, Dict[str, str]] = {}
    for e in entries or ():
        if len(e) < 4 or not e[3]:
            continue
        m = _CARD.match(str(e[2] or ""))
        if m:
            out.setdefault(int(m.group(1)), {})[str(e[0])] = str(e[3]).strip()
    return out


def codename_of(unit_path: str) -> str:
    """'…/Units/GYCY/Unit_GYCY_YanJianXi_01.uasset' -> 'GYCY_YanJianXi_01'."""
    stem = os.path.splitext(os.path.basename((unit_path or "").replace("\\", "/")))[0]
    m = _UNIT_STEM.match(stem)
    return (m.group(1) if m else stem).strip("_")


def ids_in_text(text: str, known_ids: Set[int]) -> List[int]:
    """덤프 JSON 글에서 도감 ID 로 알려진 숫자만 뽑는다 (아무 숫자나 잇지 않게)."""
    out: List[int] = []
    for m in _NUM.finditer(text or ""):
        v = int(m.group(0))
        if v in known_ids and v not in out:
            out.append(v)
    return out


def build_links(unit_texts: Dict[str, str], entries: Sequence[Sequence[str]]) -> List[dict]:
    """{유닛 에셋 경로: 덤프 JSON 글} + locres -> [{codename, ids, names{lang: name}}] (ID 가 이어진 것만)."""
    card = card_names(entries)
    known = set(card)
    out: List[dict] = []
    for path, text in (unit_texts or {}).items():
        code = codename_of(path)
        ids = ids_in_text(text, known)
        if not code or not ids:
            continue
        names: Dict[str, str] = {}
        for i in ids:
            for lang, nm in card.get(i, {}).items():
                names.setdefault(lang, nm)
        if names:
            out.append({"codename": code, "ids": ids, "names": names})
    return out


def apply_to_store(store: EntityStore, links: Iterable[dict]) -> int:
    """연결 표를 엔티티 사전에 넣는다 — 이후 이름 검색은 공식 이름/코드명 '완전 일치'로 즉시 맞는다. 새로 이은 수를 돌려준다."""
    n = 0
    ev = "게임 유닛 데이터(CardDesc)"
    for lk in links or ():
        names = lk.get("names") or {}
        code = (lk.get("codename") or "").strip()
        main = names.get("en") or names.get("ko") or next(iter(names.values()), "")
        if not main or not code:
            continue
        eid = norm(code) or norm(main)
        e = store.ensure(eid)
        added = store.add_name(eid, code, group="codename", evidence=ev)
        for lang, nm in sorted(names.items()):
            added = store.add_name(eid, nm, lang=lang, group="official", evidence=ev) or added
        e.verified = True            # 게임 데이터가 근거라 사람 확인과 같은 급으로 본다
        if added:
            n += 1
    return n
