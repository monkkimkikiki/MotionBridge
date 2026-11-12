"""보스/캐릭터 통합 엔티티 저장소 — 어떤 언어·별명·음차·코드명으로 물어도 같은 캐릭터를 찾는다.

여러 출처(에셋 카탈로그, 화면 글자, AI 추측, 웹 검색, 검수자 수정)가 주는 이름을
캐릭터(엔티티) 하나로 모은다. 닫힌 집합: 엔티티는 게임 에셋 카탈로그에서 출발하고,
웹/AI 는 이름·별명·근거(evidence)를 더할 뿐이다.

- 저장: ~/.motionbridge/entities/<게임>.json (nameglossary 와 같은 방식: 원자적 저장, 깨진 파일 무시)
- 검색: 정규화 정확 일치 > 별명 > 낱말 집합 > 접두/포함 > difflib 퍼지. 모든 결과에 '왜 맞았는지'를 남긴다.
- 정규화: charcatalog.norm_text 는 영문·한글만 남겨 중국어(黑风大王)·일본어가 사라지므로
  여기서는 유니코드 글자를 보존하는 자체 norm() 을 쓴다.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from .. import bridge

KIND_RANK = {"boss": 3, "miniboss": 2, "enemy": 1, "npc": 1, "shell": 1, "": 0}
GROUPS = ("official", "alias", "romanized", "codename", "tag")
_SEP = re.compile(r"[\W_]+", re.UNICODE)          # 유니코드 글자·숫자만 남긴다 (한자·가나 보존)
_ART = re.compile(r"(?i)^(the|a|an)\s+")
_WORD = re.compile(r"[a-z0-9]+")


def norm(s: str) -> str:
    """비교용 글자: 소문자, 관사 제거, 공백·문장부호 제거. 한자/가나/한글은 그대로 남는다."""
    return _SEP.sub("", _ART.sub("", (s or "").strip().lower()))


def words(s: str) -> frozenset:
    """라틴 낱말 집합 (어순이 달라도 같은 이름을 잡는 용도: 'Black Wind King' = 'King of the Black Wind')."""
    stop = {"the", "a", "an", "of"}
    return frozenset(w for w in _WORD.findall((s or "").lower()) if w not in stop)


def _safe(s: str) -> str:
    return re.sub(r"[^\w가-힣.-]+", "_", (s or "game").strip()) or "game"


@dataclass
class Entity:
    eid: str                                            # 'Sparta/Swordman' 처럼 카탈로그 (root/id)
    kind: str = ""                                      # boss | miniboss | enemy | npc | shell
    official: Dict[str, List[str]] = field(default_factory=dict)   # 언어 -> 정식 이름들
    romanized: List[str] = field(default_factory=list)  # 음차 (Heifeng Dawang ...)
    aliases: Dict[str, List[str]] = field(default_factory=dict)    # 언어 -> 커뮤니티 별명·줄임말
    codenames: List[str] = field(default_factory=list)  # 내부 이름 (폴더/대표 메시/locres 키)
    tags: List[str] = field(default_factory=list)       # 외형 특징 낱말 (bear, staff ...)
    context: Dict[str, str] = field(default_factory=dict)          # chapter / area / phases ...
    folder: str = ""                                    # 에셋 폴더 (카탈로그 연결)
    sources: List[dict] = field(default_factory=list)   # {url, tier, lang, evidence}
    verified: bool = False                              # 검수자가 확인한 엔티티
    merged_from: List[str] = field(default_factory=list)

    def names(self) -> List[Tuple[str, str, str]]:
        """모든 이름 형태 -> (글자, 묶음, 언어). 묶음은 GROUPS 중 하나."""
        out: List[Tuple[str, str, str]] = []
        for lang, lst in self.official.items():
            out += [(t, "official", lang) for t in lst]
        for lang, lst in self.aliases.items():
            out += [(t, "alias", lang) for t in lst]
        out += [(t, "romanized", "") for t in self.romanized]
        out += [(t, "codename", "") for t in self.codenames]
        return out

    def display(self) -> str:
        for lang in ("ko", "en"):
            if self.official.get(lang):
                return self.official[lang][0]
        for lst in self.official.values():
            if lst:
                return lst[0]
        return self.eid.split("/")[-1]

    def unknowns(self) -> List[str]:
        """비어 있어서 역질문/추가 조사가 필요한 칸들."""
        out = []
        if not self.official.get("ko"):
            out.append("한국어 정식 이름")
        if not self.official.get("en"):
            out.append("영어 정식 이름")
        if not any(self.aliases.values()):
            out.append("커뮤니티 별명")
        if not self.context.get("area") and not self.context.get("chapter"):
            out.append("등장 지역/챕터")
        if not any(s.get("url") or s.get("tier") for s in self.sources):   # 카탈로그 근거는 외부 출처가 아니다
            out.append("출처(위키/공략)")
        return out


@dataclass
class Hit:
    entity: Entity
    score: float
    why: str = ""   # 근거: 어떤 형태의 어떤 이름이 맞았는지


_GROUP_KO = {"official": "정식 이름", "alias": "별명", "romanized": "음차", "codename": "코드명"}


class EntityStore:
    def __init__(self, game: str = "", path: Optional[str] = None) -> None:
        self.game = game
        self.path = path or os.path.join(bridge.CONFIG_DIR, "entities", _safe(game) + ".json")
        self.entities: Dict[str, Entity] = {}

    # ------------------------------------------------------------------ 저장/불러오기
    @classmethod
    def load(cls, game: str, path: Optional[str] = None) -> "EntityStore":
        st = cls(game, path)
        try:
            with open(st.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            ents = d.get("entities") if isinstance(d, dict) else None
            if isinstance(ents, dict):
                for eid, e in ents.items():
                    if isinstance(e, dict):
                        e = {k: v for k, v in e.items() if k in Entity.__dataclass_fields__}
                        st.entities[str(eid)] = Entity(**{**e, "eid": str(eid)})
        except (OSError, ValueError, TypeError):
            pass
        return st

    def save(self) -> bool:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"game": self.game, "updated": time.time(),
                           "entities": {eid: asdict(e) for eid, e in self.entities.items()}},
                          f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------ 만들기/더하기
    def ensure(self, eid: str) -> Entity:
        e = self.entities.get(eid)
        if e is None:
            e = self.entities[eid] = Entity(eid)
        return e

    def from_catalog(self, catalog, include_legacy: bool = False) -> int:
        """charcatalog.Catalog 의 캐릭터들로 엔티티를 만들거나 갱신한다. 다룬 캐릭터 수를 돌려준다.

        카탈로그가 주는 것(코드명·폴더·en/ko 정식 이름·종류)만 덮고, 웹/검수자가 더한 것은 남긴다."""
        n = 0
        for c in catalog.all(include_legacy=include_legacy):
            e = self.ensure("%s/%s" % (c.root, c.id))
            if KIND_RANK.get(c.kind, 0) >= KIND_RANK.get(e.kind, 0):
                e.kind = c.kind or e.kind
            e.folder = c.folder or e.folder
            for t in (c.leaf, c.body_name()):
                self.add_name(e.eid, t, group="codename", evidence="에셋 카탈로그")
            for lang, lst in (("en", c.en), ("ko", c.ko)):
                for t in lst:
                    self.add_name(e.eid, t, lang=lang, group="official", evidence="게임 현지화 표(.locres)")
            n += 1
        return n

    def add_name(self, eid: str, text: str, lang: str = "", group: str = "official",
                 evidence: str = "", url: str = "", tier: str = "") -> bool:
        """이름 형태 하나를 더한다. 같은 묶음에 이미 있으면(정규화 기준) False.
        (묶음을 가로질러 지우지 않는다: 코드명 'LadyOfTheWoods' 가 있어도 정식 이름 'Lady of the Woods' 는
        띄어쓰기가 있어 낱말 집합 검색에 필요하므로 둘 다 둔다.)"""
        text = (text or "").strip()
        e = self.ensure(eid)
        if not text or any(norm(t) == norm(text) for t, g, _l in e.names() if g == group):
            return False
        if group == "official":
            e.official.setdefault(lang or "en", []).append(text)
        elif group == "alias":
            e.aliases.setdefault(lang or "", []).append(text)
        elif group == "romanized":
            e.romanized.append(text)
        elif group == "codename":
            e.codenames.append(text)
        elif group == "tag":
            if text.lower() in (t.lower() for t in e.tags):
                return False
            e.tags.append(text.lower())
        else:
            raise ValueError("모르는 묶음: %s" % group)
        if evidence or url:
            e.sources.append({"url": url, "tier": tier, "lang": lang, "evidence": evidence or text})
        return True

    def learn(self, eid: str, text: str, lang: str = "") -> None:
        """검수자가 '이 글자는 이 캐릭터다'라고 고친 것을 기억한다 (별명 + 확인 표시)."""
        self.add_name(eid, text, lang=lang, group="alias", evidence="검수자 수정")
        self.ensure(eid).verified = True

    def merge(self, keep_eid: str, drop_eid: str) -> Entity:
        """두 엔티티가 같은 캐릭터로 판명되면 하나로 합친다."""
        keep, drop = self.ensure(keep_eid), self.entities.pop(drop_eid, None)
        if drop is None or drop_eid == keep_eid:
            return keep
        for t, g, lang in drop.names():
            self.add_name(keep_eid, t, lang=lang, group=g)
        for t in drop.tags:
            self.add_name(keep_eid, t, group="tag")
        for k, v in drop.context.items():
            keep.context.setdefault(k, v)
        keep.sources += drop.sources
        keep.folder = keep.folder or drop.folder
        keep.verified = keep.verified or drop.verified
        if KIND_RANK.get(drop.kind, 0) > KIND_RANK.get(keep.kind, 0):
            keep.kind = drop.kind
        keep.merged_from.append(drop_eid)
        return keep

    # ------------------------------------------------------------------ 찾기
    def find(self, text: str, limit: int = 5) -> List[Hit]:
        """글자 하나로 후보를 찾는다. 점수 순서:
        정확 일치 1.0 > 별명 0.95 > 낱말 집합(어순 무시) 0.93 > 접두/포함 0.88 > 퍼지(difflib) <=0.87."""
        n, ws = norm(text), words(text)
        if not n:
            return []
        hits: List[Hit] = []
        for e in self.entities.values():
            best: Optional[Tuple[float, str]] = None
            for t, g, lang in e.names():
                tn = norm(t)
                if not tn:
                    continue
                label = "%s%s '%s'" % (_GROUP_KO.get(g, g), ("(%s)" % lang) if lang else "", t)
                if tn == n:
                    sc = 0.95 if g == "alias" else 1.0
                    why = label + " 일치"
                elif ws and len(ws) >= 2 and words(t) == ws:
                    sc, why = 0.93, label + " 낱말 일치(어순 다름)"
                elif min(len(tn), len(n)) >= 5 and (tn.startswith(n) or n.startswith(tn) or (len(n) >= 6 and n in tn)):
                    sc, why = 0.88, label + " 접두/포함"
                else:
                    r = difflib.SequenceMatcher(None, n, tn).ratio()
                    if r < 0.8:
                        continue
                    sc, why = min(r, 0.87), label + " 비슷함(%.2f)" % r
                if best is None or sc > best[0]:
                    best = (sc, why)
            if best:
                sc = best[0] + (0.02 if e.verified else 0.0) + 0.002 * KIND_RANK.get(e.kind, 0)
                hits.append(Hit(e, min(sc, 1.0), best[1]))
        hits.sort(key=lambda h: (-h.score, h.entity.eid))
        return hits[:limit]

    def resolve(self, texts: Iterable[str]) -> List[Hit]:
        """단서 여러 개(화면 글자 + AI 추측 + 유저 입력)를 모아 교차 검증한다.
        서로 다른 단서가 같은 엔티티를 가리키면 점수를 더한다 (출처 합의 가산 +0.05/개)."""
        agg: Dict[str, Hit] = {}
        for t in texts:
            for h in self.find(t, limit=3):
                old = agg.get(h.entity.eid)
                if old is None:
                    agg[h.entity.eid] = Hit(h.entity, h.score, h.why)
                else:
                    old.score = min(max(old.score, h.score) + 0.05, 1.0)
                    old.why += " + " + h.why
        out = sorted(agg.values(), key=lambda h: (-h.score, h.entity.eid))
        return out

    def need_clarify(self, hits: List[Hit], gap: float = 0.15) -> bool:
        """1위와 2위가 가까우면(기본 0.15 미만) 역질문이 필요하다."""
        return len(hits) >= 2 and (hits[0].score - hits[1].score) < gap
