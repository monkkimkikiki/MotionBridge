"""동작·패턴 설명으로 애니메이션을 찾고, 그 애니메이션이 속한 캐릭터를 거꾸로 알아낸다.

예) "7연속 스핀 공격" -> spin 계열 낱말(spin/rotate/whirl…)이 들어간 애니메이션 이름을 전부 찾고,
    그 애니메이션이 많이/정확히 걸리는 캐릭터 폴더(Swordman 등)를 후보로 올린다.
이름만으로 안 풀릴 때, 사람이 하던 "특징을 떠올려 라이브러리에서 spin 으로 검색" 방식을 자동화한 것이다.
Qt/네트워크에 의존하지 않는다 (AI 는 키워드를 영어로 바꿔 주는 데만 선택적으로 쓴다).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import assetidx, layout

# 개념 -> (영어 낱말들[첫 낱말이 대표], 한국어 신호)
CONCEPTS: Dict[str, Tuple[List[str], List[str]]] = {
    "spin": (["spin", "rotate", "rotation", "whirl", "whirlwind", "twirl", "revolve", "cyclone", "tornado", "turn", "circle"],
             ["스핀", "회전", "돌기", "돌면", "돌아", "돌리", "빙글", "팽이", "회오리", "선풍", "휘돌"]),
    "slam": (["slam", "smash", "pound", "hammer", "overhead", "crush", "stomp", "ground", "bash"],
             ["내려치", "내리치", "내리찍", "찍기", "찍는", "찍어", "강타", "쾅", "리라", "스매시", "스탬프", "밟"]),
    "slash": (["slash", "swing", "cut", "sweep", "swipe", "cleave", "chop", "horizontal", "vertical"],
              ["베기", "베는", "베어", "휘두", "횡베", "쓸어", "휩쓸", "할퀴"]),
    "thrust": (["thrust", "stab", "lunge", "pierce", "poke", "spear"], ["찌르", "찌름", "관통", "창"]),
    "jump": (["jump", "leap", "hop", "pounce", "vault", "dive", "land"], ["점프", "도약", "뛰어", "뛰어오", "내려앉", "착지"]),
    "charge": (["charge", "dash", "rush", "tackle", "ram", "sprint"], ["돌진", "대시", "달려들", "달려", "박치기", "충돌"]),
    "throw": (["throw", "toss", "hurl", "fling", "shoot", "fire", "projectile", "ranged", "bow"], ["던지", "던져", "투척", "발사", "쏘"]),
    "roar": (["roar", "scream", "shout", "howl", "cry", "taunt", "yell"], ["포효", "괴성", "외침", "울부", "고함", "도발"]),
    "combo": (["combo", "chain", "multi", "series", "sequence", "consecutive", "rapid", "flurry"], ["연속", "콤보", "연타", "난타", "연격"]),
    "kick": (["kick", "stomp"], ["발차기", "차기", "킥"]),
    "punch": (["punch", "fist", "jab", "hook", "uppercut"], ["주먹", "펀치", "치기"]),
    "bite": (["bite", "chomp", "maul"], ["물기", "깨물", "물어"]),
    "claw": (["claw", "scratch", "rake", "talon"], ["발톱", "할퀴", "긁"]),
    "grab": (["grab", "grapple", "seize", "choke", "throw", "hold"], ["잡기", "잡아", "붙잡", "움켜", "던지기"]),
    "summon": (["summon", "spawn", "call", "conjure", "minion"], ["소환", "불러"]),
    "magic": (["cast", "spell", "magic", "beam", "laser", "blast", "bolt", "orb", "fireball", "aoe"], ["마법", "시전", "광선", "빔", "폭발", "구체"]),
    "evade": (["dodge", "evade", "roll", "sidestep", "backstep", "retreat", "step"], ["회피", "구르", "뒤로", "물러", "백스텝"]),
    "block": (["block", "guard", "parry", "shield", "deflect"], ["막기", "방어", "패링", "방패"]),
    "stun": (["stun", "stagger", "knockdown", "fall", "down", "recover", "getup"], ["스턴", "기절", "쓰러", "넘어", "일어"]),
    "death": (["death", "die", "dead", "dying", "kill"], ["사망", "죽", "쓰러짐"]),
    "rage": (["rage", "enrage", "berserk", "frenzy", "fury", "phase"], ["분노", "광폭", "격노", "페이즈", "각성"]),
    "walk": (["walk", "stroll", "patrol"], ["걷", "걸어", "순찰"]),
    "run": (["run", "sprint", "chase"], ["달리", "뛰기", "추격"]),
    "idle": (["idle", "stand", "breath", "wait"], ["대기", "서있", "숨쉬"]),
    "attack": (["attack", "atk", "strike", "melee", "hit"], ["공격", "타격"]),
}
_NEED_NOT: Set[str] = {"combo", "attack"}  # 있으면 가산점만 주고 없어도 탈락시키지 않는 개념
_COUNT_RE = re.compile(r"(\d{1,2})\s*(?:연속|연타|연격|타|번|회|hits?|times|x\b|-hit|hit)", re.I)


@dataclass
class Concept:
    name: str
    words: Dict[str, float]
    required: bool = True


@dataclass
class MotionQuery:
    text: str
    concepts: List[Concept] = field(default_factory=list)
    count: Optional[int] = None

    def describe(self) -> str:
        parts = [c.name if c.name in CONCEPTS else "/".join(c.words) for c in self.concepts]
        if self.count:
            parts.append("%d타" % self.count)
        return ", ".join(parts) or "(알아들은 동작 없음)"


@dataclass
class MotionHit:
    path: str
    score: float
    why: str
    monster: str


@dataclass
class MonsterHit:
    name: str
    score: float
    anims: List[MotionHit]
    why: str = ""


def parse_motion(text: str, extra_words: Sequence[str] = ()) -> MotionQuery:
    """설명 -> 개념들. 한국어 신호·영어 낱말·AI 가 준 영어 키워드·'N연속' 을 읽는다."""
    q = MotionQuery(text=text or "")
    t = text or ""
    m = _COUNT_RE.search(t)
    if m:
        n = int(m.group(1))
        if 2 <= n <= 20:
            q.count = n
    ko = re.sub(r"[^가-힣]", "", t)
    seen: Set[str] = set()

    def add(name: str, literal: Optional[str] = None) -> None:
        if name in seen:
            return
        seen.add(name)
        words = {w: 0.8 for w in CONCEPTS[name][0]}
        words[CONCEPTS[name][0][0]] = 1.0
        if literal:
            words[literal] = 1.0
        q.concepts.append(Concept(name, words, name not in _NEED_NOT))

    for name, (_en, kos) in CONCEPTS.items():
        if any(k in ko for k in kos):
            add(name)
    literal = [w for w in assetidx.tokens(t, drop_stop=True) if w not in assetidx.NAME_NOISE]
    lookup = {w: n for n, (en, _k) in CONCEPTS.items() for w in en}
    for w in literal + [str(x).lower() for x in extra_words]:
        if w in lookup:
            add(lookup[w], w)
        elif len(w) >= 3 and w not in {"attack", "monster", "boss", "enemy"}:
            if ("lit:" + w) not in seen:
                seen.add("lit:" + w)
                q.concepts.append(Concept(w, {w: 1.0}, True))
    if q.count and "combo" not in seen:
        add("combo")
    return q


def _score_anim(path: str, q: MotionQuery, idx: assetidx.AssetIndex) -> Tuple[float, str]:
    base = os.path.basename(path.replace("\\", "/")).split(".")[0]
    name_toks = set(assetidx.tokens(base, drop_stop=False))
    dir_toks = set(assetidx.tokens(os.path.dirname(path.replace("\\", "/")), drop_stop=False))
    compact = re.sub(r"[^a-z0-9]", "", base.lower())
    need = [c for c in q.concepts if c.required] or q.concepts
    if not need:
        return 0.0, ""
    total, hit_names = 0.0, []
    for c in q.concepts:
        best = 0.0
        for w, wt in c.words.items():
            if w in name_toks:
                best = max(best, wt)
            elif len(w) >= 4 and w in compact:
                best = max(best, 0.6 * wt)
            elif w in dir_toks:
                best = max(best, 0.4 * wt)
        if best > 0:
            hit_names.append(c.name)
        if c in need:
            total += best
        else:
            total += 0.15 * best  # combo/attack 같은 곁다리 단어는 가산점만
    s = total / len(need)
    if q.count:
        n = assetidx.hit_count(base)
        if n == q.count:
            s += 0.2
            hit_names.append("%d타" % n)
        elif re.search(r"(?<![0-9])0*%d(?![0-9])" % q.count, base):
            s += 0.08
    why = "동작 일치: " + ", ".join(hit_names) if hit_names else ""
    return round(min(s, 1.2), 3), why


def monster_of(path: str) -> str:
    return layout.character_of(path)


def search_anims(idx: assetidx.AssetIndex, text: str, extra_words: Sequence[str] = (), restrict: Optional[Iterable[str]] = None,
                 limit: int = 120, min_score: float = 0.34, kinds: Optional[Iterable[str]] = None) -> Tuple[List[MotionHit], MotionQuery]:
    """설명과 이름이 맞는 애니메이션 목록(점수 순). restrict: 이 캐릭터 폴더 이름들 안에서만(소문자 비교).
    kinds: {'monster'} / {'player'} 로 종류를 좁히면 점수 계산 전에 걸러서 더 빠르다 (None 이나 둘 다면 전체)."""
    q = parse_motion(text, extra_words)
    allow = {r.lower() for r in restrict} if restrict else None
    out: List[MotionHit] = []
    if not q.concepts:
        return out, q
    for a in idx.anims_of_kinds(kinds):
        mon = monster_of(a)
        if allow is not None and mon.lower() not in allow:
            continue
        s, why = _score_anim(a, q, idx)
        if s >= min_score:
            out.append(MotionHit(a, s, why, mon))
    out.sort(key=lambda h: (-h.score, h.path.lower()))
    return out[:limit], q


def monsters_from_hits(hits: Sequence[MotionHit], limit: int = 8) -> List[MonsterHit]:
    """맞는 애니메이션을 캐릭터별로 묶어 순위를 매긴다 (가장 잘 맞는 것 + 맞는 개수가 많을수록)."""
    by: Dict[str, List[MotionHit]] = {}
    for h in hits:
        by.setdefault(h.monster, []).append(h)
    res: List[MonsterHit] = []
    for name, hs in by.items():
        hs = sorted(hs, key=lambda h: -h.score)
        top = hs[0].score
        mean3 = sum(h.score for h in hs[:3]) / min(len(hs), 3)
        bonus = min(0.1, 0.03 * (len(hs) - 1))
        sc = 0.6 * top + 0.4 * mean3 + bonus  # 1.0 을 넘을 수 있다(곁다리 낱말까지 맞으면 가산) — 순위용
        res.append(MonsterHit(name, round(sc, 3), hs, "맞는 애니메이션 %d개 (최고 %s)" % (len(hs), os.path.basename(hs[0].path).split(".")[0])))
    res.sort(key=lambda m: (-m.score, m.name.lower()))
    return res[:limit]


def meshes_for(idx: assetidx.AssetIndex, mh: MonsterHit) -> List[assetidx.MeshCandidate]:
    """캐릭터 폴더에 속한 메시들을 후보로. 이름이 폴더명과 같은 것(본체)을 먼저."""
    out: List[assetidx.MeshCandidate] = []
    for e in idx.meshes:
        if monster_of(e.path).lower() != mh.name.lower():
            continue
        main = e.name.lower().replace("_", "") == mh.name.lower().replace("_", "")
        sc = min(mh.score, 1.0) * (1.0 if main else 0.85)
        out.append(assetidx.MeshCandidate(e.path, e.name, round(sc, 3), "동작으로 추정 — " + mh.why))
    out.sort(key=lambda c: (-c.score, len(c.name), c.name.lower()))
    return out


def find_meshes_by_motion(idx: assetidx.AssetIndex, text: str, extra_words: Sequence[str] = (), limit: int = 8, kinds: Optional[Iterable[str]] = None):
    """(메시 후보, 캐릭터 순위, 맞는 애니메이션, 질의). 메시 후보는 캐릭터 순위대로 이어 붙인다."""
    hits, q = search_anims(idx, text, extra_words, kinds=kinds)
    mons = monsters_from_hits(hits, limit)
    cands: List[assetidx.MeshCandidate] = []
    for m in mons:
        cands += meshes_for(idx, m)
    cands.sort(key=lambda c: -c.score)  # 동점이면 캐릭터 순위 순서 유지(안정 정렬)
    return cands[:limit * 2], mons, hits, q
