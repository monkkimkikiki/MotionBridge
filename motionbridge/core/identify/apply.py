"""엔티티 사전을 '영상으로 찾기' 흐름에 잇는 접착부 (Qt 불필요).

- fix_groups   : AI 추측/화면 글자 이름이 카탈로그와 바로 안 이어질 때,
                 엔티티 사전(별명·다국어 정식명·음차·오타)으로 대표 메시 이름으로 교정한다.
- learn_confirm: 검수자가 확정한 메시로부터 '이 글자 = 이 캐릭터' 를 사전에 학습한다.
                 다음 추측부터는 같은 글자가 웹/AI 없이 바로 맞는다.
"""
from __future__ import annotations

import re
from typing import List, Optional, Sequence, Tuple

from .. import assetidx
from .entity_store import EntityStore, norm

_INGAME = re.compile(r"게임 속 이름: ([^.]+)")


def _clues(name: str, description: str) -> List[str]:
    """그룹 하나에서 찾기에 쓸 글자들: 추측 이름 + 설명 속 '게임 속 이름: ...'."""
    out = [(name or "").strip()]
    m = _INGAME.search(description or "")
    if m:
        out.append(m.group(1).strip())
    return [t for t in dict.fromkeys(out) if t and t.lower() not in assetidx.UNKNOWN_WORDS]


def char_for_mesh(cat, path: str):
    """메시 경로 -> 카탈로그 캐릭터 (그 폴더를 접두로 갖는 캐릭터, 가장 깊은 것)."""
    low = (path or "").replace("\\", "/")
    best = None
    for ch in cat.chars.values():
        if ch.folder and low.startswith(ch.folder + "/") and (best is None or len(ch.folder) > len(best.folder)):
            best = ch
    return best


def fix_groups(store: Optional[EntityStore], cat, groups, notes: List[str], min_score: float = 0.86) -> int:
    """카탈로그가 바로 못 잇는 그룹 이름을 엔티티 사전으로 교정한다. 고친 수를 돌려준다.

    후보 1·2위가 비등하면(need_clarify) 건드리지 않는다 — 검수자가 후보 목록에서 고르는 편이 안전."""
    if store is None or cat is None or not getattr(cat, "chars", None):
        return 0
    n = 0
    for g in groups:
        clues = _clues(getattr(g, "name", ""), getattr(g, "description", ""))
        if not clues:
            continue
        if cat.by_name(clues[0]) is not None:
            continue                       # 카탈로그가 바로 아는 이름이면 그대로
        hits = store.resolve(clues)
        if not hits or hits[0].score < min_score or store.need_clarify(hits):
            continue
        ent = hits[0].entity
        ch = None
        for code in ent.codenames:
            ch = cat.by_name(code)
            if ch is not None:
                break
        if ch is None:
            continue
        new = ch.body_name()
        if not new or norm(new) == norm(g.name):
            continue
        notes.append("엔티티 사전: '%s' → %s (%s)" % (g.name, new, hits[0].why))
        if g.name not in (getattr(g, "alts", None) or []):
            g.alts = [g.name] + list(getattr(g, "alts", []) or [])   # 원래 추측도 후보로 남긴다
        g.name = new
        # 검수자가 확인했던 엔티티면 그 점수 그대로, 아니면 0.75 로 눌러 ⚠ 검수 대상으로 둔다
        g.confidence = max(getattr(g, "confidence", 0.0) or 0.0, hits[0].score if ent.verified else min(hits[0].score, 0.75))
        n += 1
    return n


def learn_confirm(store: Optional[EntityStore], cat, picks: Sequence[Tuple[str, str, str]]) -> int:
    """검수자가 확정한 짝을 학습한다. picks = (AI 가 냈던 이름, 설명, 확정된 메시 경로).

    새로 배운 별명 수를 돌려준다 (이미 아는 이름으로 맞혔으면 확인 표시만 남긴다)."""
    if store is None or cat is None or not getattr(cat, "chars", None):
        return 0
    n = 0
    for name, desc, path in picks:
        ch = char_for_mesh(cat, path)
        if ch is None:
            continue
        eid = "%s/%s" % (ch.root, ch.id)
        ent = store.ensure(eid)
        known = {norm(ch.leaf), norm(ch.body_name())} | {norm(x) for x in ch.names} | {norm(t) for t, _g, _l in ent.names()}
        for t in _clues(name, desc):
            if not norm(t) or norm(t) in known:
                store.ensure(eid).verified = True
                continue
            if store.learn(eid, t):
                n += 1
    return n
