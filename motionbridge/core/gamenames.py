"""화면에 보이는 이름('The Nameless Captive') -> 게임 내부 이름표(키) -> 메시 후보.

리소스 이름과 게임 안 표시 이름이 다를 때를 위한 알고리즘.
  1) 게임의 .locres(글자 사전)를 읽어 '표시 이름 글자'가 들어 있는 항목을 찾는다 (관사/대소문자/구두점 무시, 비슷한 글자 허용).
  2) 그 항목의 키(예: Enemy_Prisoner_Name)에서 내부 이름 단어(prisoner)를 뽑아 메시 이름과 맞춘다.
  3) 동의어 사전과 AI(이름 목록 안에서 고르기)로 보완한다 -> assetidx.expand_words / vision.suggest_from_vocab
키가 해시/GUID 인 항목은 단서가 못 되므로 건너뛴다.
"""
from __future__ import annotations

import difflib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import layout, locres, vision
from .assetidx import STOP, UNKNOWN_WORDS, AssetIndex, MeshCandidate, tokens

ARTICLES = {"the", "a", "an"}
KEY_NOISE = {"name", "names", "desc", "description", "title", "text", "label", "display", "displayname", "tooltip", "lore", "short",
             "long", "enemy", "enemies", "boss", "npc", "monster", "creature", "character", "data", "row", "ui", "loc", "str", "string"}
_HEXLIKE = re.compile(r"^[0-9A-Fa-f]{16,}$")
KEEP_LANGS = ("en", "ko")


def norm_text(s: str) -> str:
    """비교용 글자: 소문자, 글자·숫자·한글만, 앞의 관사 제거."""
    words = re.findall(r"[a-z0-9가-힣]+", (s or "").lower())
    while words and words[0] in ARTICLES:
        words = words[1:]
    return " ".join(words)


def key_words(ns: str, key: str) -> List[str]:
    """키(와 네임스페이스)에서 캐릭터를 가리키는 단어만 뽑는다. 해시/GUID 키는 빈 목록."""
    if _HEXLIKE.match(key or ""):
        return []
    out: List[str] = []
    for w in tokens(key.replace(".", "_").replace("-", "_"), drop_stop=True):
        if w not in KEY_NOISE and w not in out:
            out.append(w)
    return out


def lang_of(path: str) -> str:
    """파일 경로에서 언어 코드(en, ko-KR …)를 짐작한다."""
    parts = re.split(r"[/\\_]", path)
    for p in reversed(parts[:-1]):
        if re.fullmatch(r"[a-z]{2,3}(-[A-Za-z]{2,4})?", p):
            return p
    return ""


@dataclass
class NameHit:
    text: str
    ns: str
    key: str
    score: float
    lang: str = ""


class NameBook:
    def __init__(self, entries: Iterable[Sequence[str]] = ()) -> None:
        # [언어, 네임스페이스, 키, 글자]
        self.entries: List[Tuple[str, str, str, str]] = [tuple(e) for e in entries]  # type: ignore[misc]
        self._norm: List[str] = []
        self._by_tok: Dict[str, List[int]] = {}
        for i, e in enumerate(self.entries):
            n = norm_text(e[3])
            self._norm.append(n)
            for w in set(n.split()):
                self._by_tok.setdefault(w, []).append(i)

    def __len__(self) -> int:
        return len(self.entries)

    def lookup(self, query: str, limit: int = 12) -> List[NameHit]:
        q = norm_text(query)
        if not q:
            return []
        qw = set(q.split())
        cand: Dict[int, int] = {}
        for w in qw:  # 단어를 하나라도 공유하는 항목만 훑는다
            for i in self._by_tok.get(w, ())[:5000]:
                cand[i] = cand.get(i, 0) + 1
        hits: List[NameHit] = []
        for i, shared in cand.items():
            t = self._norm[i]
            if len(t) > 80:  # 긴 설명문은 이름이 아니다
                continue
            tw = set(t.split())
            if t == q:
                sc = 1.0
            elif tw <= qw or qw <= tw:  # 한쪽 단어가 다른 쪽에 모두 들어 있음 (예: 'Captive' 와 'Nameless Captive')
                sc = 0.6 + 0.3 * min(len(tw), len(qw)) / max(len(tw), len(qw))
            else:
                r = difflib.SequenceMatcher(None, q, t).ratio()
                sc = r if r >= 0.85 else 0.0
            if sc > 0:
                lg, ns, key, text = self.entries[i]
                hits.append(NameHit(text, ns, key, round(sc, 3), lg))
        hits.sort(key=lambda h: (-h.score, len(h.text), h.key))
        return hits[:limit]

    def meshes_for(self, query: str, index: AssetIndex, limit: int = 8) -> List[MeshCandidate]:
        """표시 이름 -> 키의 단어 -> 그 단어가 이름에 들어 있는 메시."""
        out: Dict[str, MeshCandidate] = {}
        for h in self.lookup(query):
            kw = key_words(h.ns, h.key)
            if not kw:
                continue
            for m in index.meshes:
                got = [w for w in kw if w in m.toks or (len(w) >= 5 and w in m.name.lower())]
                if not got:
                    continue
                cov = len(got) / max(len(kw), 1)
                spec = len([w for w in kw if w in m.toks]) / max(len(m.toks), 1)  # 이름이 키와 딱 맞는 메시(본체)를 부위(Arm 등)보다 위로
                sc = h.score * (0.6 + 0.4 * cov) * (0.85 + 0.15 * spec)
                why = "게임 글자 '%s' (키 %s) → %s" % (h.text, h.key, ", ".join(got))
                prev = out.get(m.path)
                if prev is None or sc > prev.score:
                    out[m.path] = MeshCandidate(m.path, m.name, round(sc, 3), why)
        res = sorted(out.values(), key=lambda c: (-c.score, len(c.name), c.name.lower()))
        return res[:limit]

    # ------------------------------------------------------------------ 저장
    def save(self, path: str, game_key: str = "") -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"game": game_key, "entries": self.entries}, f, ensure_ascii=False)

    @staticmethod
    def load(path: str, game_key: str = "") -> Optional["NameBook"]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(d, dict) or (game_key and d.get("game") != game_key):
            return None
        nb = NameBook(d.get("entries") or [])
        return nb if len(nb) else None


def build_book(files: Sequence[str]) -> NameBook:
    """뽑아 둔 .locres 파일들로 사전을 만든다 (영어/한국어 위주, 둘 다 없으면 처음 언어)."""
    per_lang: Dict[str, List[Tuple[str, str, str, str]]] = {}
    for f in files:
        try:
            rows = locres.read_locres(f)
        except (locres.LocresError, OSError):
            continue
        lg = lang_of(os.path.basename(f)) or "?"
        per_lang.setdefault(lg, []).extend((lg, ns, key, text) for ns, key, text in rows if text.strip())
    keep = [lg for lg in per_lang if lg.lower().startswith(KEEP_LANGS)] or list(per_lang)[:1]
    entries: List[Tuple[str, str, str, str]] = []
    for lg in keep:
        entries += per_lang[lg]
    return NameBook(entries)


# ---------------------------------------------------------------------- 합치기
def _vocab_for_ai(index: AssetIndex, limit: int) -> List[str]:
    names = index.names()
    if len(names) <= limit:
        return names
    keep = {m.name for m in index.meshes if set(m.dirs) & layout.MARKERS}  # 캐릭터/캐릭터 폴더의 것만
    narrowed = [n for n in names if n in keep]
    return (narrowed or names)[:limit]


def find_meshes(name: str, index: AssetIndex, book: Optional[NameBook] = None, provider=None, description: str = "",
                limit: int = 8) -> Tuple[List[MeshCandidate], List[str]]:
    """게임 속 이름 -> 메시 후보. (후보 목록, 안내 글 목록)

    1) 게임 글자 사전(.locres)  2) 낱말·비슷한 말·오타  3) AI 가 이름 목록 안에서 고름 — 같은 후보를 여러 방법이 가리키면 점수를 올린다.
    """
    notes: List[str] = []
    found: Dict[str, MeshCandidate] = {}
    votes: Dict[str, int] = {}

    def add(c: MeshCandidate, score: float, why: str) -> None:
        votes[c.path] = votes.get(c.path, 0) + 1
        prev = found.get(c.path)
        if prev is None:
            found[c.path] = MeshCandidate(c.path, c.name, round(score, 3), why)
        else:
            found[c.path] = MeshCandidate(c.path, c.name, max(prev.score, round(score, 3)), prev.why + " / " + why)

    if not (name or "").strip():
        return [], ["게임 속 이름을 입력하세요."]
    if book is not None and len(book):
        got = book.meshes_for(name, index, limit)
        for c in got:
            add(c, c.score, c.why)
        if not got:
            if book.lookup(name):
                notes.append("게임 글자 사전에는 이름이 있지만 키가 해시(GUID)라 리소스 이름과 이어지지 않았습니다.")
            else:
                notes.append("게임 글자 사전에서 '%s' 을(를) 찾지 못했습니다." % name)
    else:
        notes.append("게임 글자 사전(.locres)을 아직 만들지 않았습니다 — [글자 사전 만들기]를 누르면 정확도가 올라갑니다.")
    for c in index.find_by_name(name, limit):
        add(c, c.score * 0.9, c.why)
    if provider is not None:
        try:
            for vn, conf, reason in vision.suggest_from_vocab(provider, name, _vocab_for_ai(index, provider.vocab_limit()), description):
                for m in index.meshes:
                    if m.name == vn:
                        add(MeshCandidate(m.path, m.name, 0.0), 0.4 + 0.5 * conf, "AI 추천" + (": " + reason if reason else ""))
        except (vision.VisionError, OSError) as e:
            notes.append("AI 추천을 못 받았습니다: %s" % e)
    for path, c in list(found.items()):
        if votes[path] > 1:  # 여러 방법이 같은 곳을 가리킴
            found[path] = MeshCandidate(c.path, c.name, round(min(1.0, c.score + 0.05 * (votes[path] - 1)), 3), c.why)
    out = sorted(found.values(), key=lambda c: (-c.score, len(c.name), c.name.lower()))
    if not out:
        notes.append("비슷한 이름의 메시를 찾지 못했습니다. 영상 장면으로 찾거나 검색창에 리소스 이름 일부를 직접 넣어 보세요.")
    return out[:limit], notes


# ---------------------------------------------------------------------- 사용자가 아는 정보
@dataclass
class Known:
    """사용자가 적어 준 '이 영상에 나오는 캐릭터' 한 줄."""
    name: str                       # 게임 속 이름 (The Nameless Captive)
    note: str = ""                  # 메모 (보스, 쇠사슬에 묶임 …)
    cands: List[MeshCandidate] = field(default_factory=list)  # 이름으로 찾은 메시 후보

    @property
    def top(self) -> Optional[MeshCandidate]:
        return self.cands[0] if self.cands and self.cands[0].score >= 0.5 else None

    def prompt_line(self) -> str:
        t = self.top
        s = self.name + ((" (internal name probably: %s)" % t.name) if t else "")
        return s + ((" — " + self.note) if self.note else "")


def parse_known(text: str) -> List[Known]:
    """한 줄에 하나: '이름 — 메모' (구분은 —, -, :, 쉼표 앞의 첫 구분자)."""
    out: List[Known] = []
    for line in re.split(r"[\r\n;]+", text or ""):
        line = line.strip(" \t-•*")
        if not line:
            continue
        m = re.split(r"\s+[—–-]\s+|\s*[:：]\s*|\s*\|\s*", line, maxsplit=1)
        name, note = m[0].strip(), (m[1].strip() if len(m) > 1 else "")
        if name and not any(k.name.lower() == name.lower() for k in out):
            out.append(Known(name, note))
    return out


def resolve_known(known: Sequence[Known], index: AssetIndex, book: Optional[NameBook] = None) -> List[Known]:
    for k in known:
        k.cands, _n = find_meshes(k.name, index, book, None, k.note, 5)
    return list(known)


def merge_known(groups: List["vision.Group"], known: Sequence[Known]) -> List["vision.Group"]:
    """AI 가 찾은 캐릭터 목록에 사용자가 알려 준 캐릭터를 합친다.

    * AI 가 같은 메시로 인식했으면 그 줄을 확신 높게 + 게임 속 이름 메모
    * AI 가 '모름' 한 줄이 하나뿐이고 알려 준 것도 하나 남았으면 같은 캐릭터로 보고 합친다
    * 그 밖에는 '사용자 지정' 줄을 새로 추가 (AI 가 놓친 캐릭터)
    """
    groups = list(groups)
    unmatched: List[Known] = []
    for k in known:
        t = k.top
        hit = next((g for g in groups if t and g.name and g.name.lower() == t.name.lower()), None)
        if hit is not None:
            hit.confidence = max(hit.confidence, 0.9)
            hit.description = ("게임 속 이름: %s. %s" % (k.name, hit.description)).strip()
        else:
            unmatched.append(k)
    unk = [g for g in groups if not g.name or g.name.lower() in UNKNOWN_WORDS]
    for k in unmatched:
        label = k.top.name if k.top else k.name
        desc = "게임 속 이름: %s%s" % (k.name, (". " + k.note) if k.note else "")
        if len(unk) == 1 and len(unmatched) == 1:
            g = unk[0]
            g.name, g.description = label, desc + ". " + g.description
            g.confidence = 0.85 if k.top else 0.0
        else:
            groups.append(vision.Group(label, desc, 0, 0.85 if k.top else 0.0, "", None, []))
    return groups
