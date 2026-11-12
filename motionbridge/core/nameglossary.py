"""리소스 이름 풀이 사전 (Qt 없이).

게임 파일의 캐릭터 이름이 영어 낱말이 아닐 때가 있다. 《검은 신화: 오공》은 중국어 병음이다:
  GYCY_Lang_02 = 狼(늑대), HYS_XiangGongXing = 相公형 …, psd_daoshi = 道士(도사), GYCY_TuFei = 土匪(산적)
영상에서 AI 가 '머리가 큰 인간형 보스'라고 알아봐도, 이름 글자만으로는 어느 리소스인지 이을 수 없었다.

그래서 게임마다 한 번, 리소스 이름 목록을 AI(글 전용도 됨)에게 보내 '이 이름이 무슨 뜻인지'를 풀어 받아 저장한다:
  {이름: {"zh": 한자, "en": 영어 뜻/공식 영어 이름, "ko": 한국어 뜻/공식 한국어 이름, "kind": boss|enemy|npc|player|prop|unknown}}
이 사전을 ① 인식 AI 에게 주는 이름 목록에 뜻으로 붙이고 ② 한국어/영어 이름·외형 설명으로 리소스를 찾는 데 쓴다.
풀이는 AI 의 추측이라 틀릴 수 있다 — 후보를 내놓는 용도이고, 최종 선택은 검수자가 한다.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import time
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import bridge

CHUNK = 80
MAX_NAMES = 640
_VARIANT = re.compile(r"(?i)(_lod\d+|_\d{1,3}[a-z]?|_[a-z]\d{1,2}|_v\d+)+$")
_PREFIX = re.compile(r"(?i)^(sk|skm|sm|skel|mesh)_")
_WORD = re.compile(r"[a-z]{3,}")
STOP = {"the", "and", "with", "likely", "used", "for", "its", "has", "from", "that", "this", "large", "small", "big", "boss", "enemy",
        "character", "monster", "humanoid", "creature", "wearing", "holding", "body", "look", "looking", "appears", "like", "very"}


def base_key(name: str) -> str:
    """변형(…_01a, …_02, _LOD1)을 뗀 대표 이름. 같은 캐릭터의 여러 벌을 한 번만 풀게 한다."""
    n = _PREFIX.sub("", str(name).strip())
    return _VARIANT.sub("", n) or n


def looks_opaque(names: Sequence[str]) -> bool:
    """이름들이 영어 낱말로 읽히지 않는가 (병음·로마자·약어 위주인가). 표본의 낱말 중 흔한 영어 낱말 비율로 어림한다."""
    common = ("man", "woman", "boss", "wolf", "bear", "king", "queen", "knight", "soldier", "guard", "monk", "dragon", "spider", "snake", "rat", "bat",
              "zombie", "skeleton", "ghost", "demon", "giant", "golem", "archer", "mage", "witch", "priest", "warrior", "bandit", "thief", "hunter",
              "beast", "bird", "fish", "frog", "boar", "tiger", "lion", "horse", "dog", "cat", "player", "hero", "npc", "enemy", "monster", "minion",
              "elite", "heavy", "light", "small", "large", "big", "old", "young", "male", "female", "body", "head", "armor", "sword", "shield", "spear",
              "axe", "bow", "staff", "fire", "ice", "dark", "blood", "stone", "iron", "bone", "tree", "brigand", "captive", "prisoner", "swordman")
    words = [w for n in list(names)[:300] for w in _WORD.findall(re.sub(r"([a-z])([A-Z])", r"\1 \2", base_key(n)).lower())]
    if len(words) < 12:
        return False
    hit = sum(1 for w in words if w in common)
    return hit / float(len(words)) < 0.12


def build_prompt(names: Sequence[str], game_hint: str = "") -> str:
    game = ("the video game \"%s\"" % game_hint) if game_hint else "a video game"
    return ("Below are internal asset names of character/creature meshes from %s. The names are often NOT English words: they may be "
            "romanized Chinese (pinyin, e.g. 'Lang' = 狼 wolf, 'daoshi' = 道士 Taoist priest, 'TuFei' = 土匪 bandit), romanized Japanese or Korean, "
            "or abbreviations, with studio prefixes (letters before the first underscore) and variant numbers.\n"
            "For EACH name, decode what creature or character it most likely is. Use your knowledge of this game (official character names) when you can.\n"
            "Answer with JSON only, one entry per given name, keys exactly as given:\n"
            "{\"<name>\": {\"zh\": \"<original-language word(s) or empty>\", \"en\": \"<English meaning or official English name, a few words>\", "
            "\"ko\": \"<Korean meaning or official Korean name>\", \"kind\": \"boss|enemy|npc|player|prop|unknown\", \"sure\": 0.0-1.0}}\n"
            "If you cannot decode a name, still include it with en \"\" and sure 0.\n"
            "Names:\n%s") % (game, "\n".join(names))


def _extract_json(text: str):
    t = (text or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except ValueError:
        pass
    a, b = t.find("{"), t.rfind("}")
    if 0 <= a < b:
        try:
            return json.loads(t[a:b + 1])
        except ValueError:
            return None
    return None


def parse(text: str, names: Sequence[str]) -> Dict[str, dict]:
    obj = _extract_json(text)
    if not isinstance(obj, dict):
        return {}
    lower = {n.lower(): n for n in names}
    out: Dict[str, dict] = {}
    for k, v in obj.items():
        n = lower.get(str(k).strip().lower())
        if not n or not isinstance(v, dict):
            continue
        e = {f: str(v.get(f) or "").strip()[:80] for f in ("zh", "en", "ko", "kind")}
        try:
            e["sure"] = max(0.0, min(1.0, float(v.get("sure", 0.5))))
        except (TypeError, ValueError):
            e["sure"] = 0.5
        out[n] = e
    return out


def _safe(s: str) -> str:
    return re.sub(r"[^\w\-]+", "_", str(s)).strip("_")[:60] or "game"


class Glossary:
    def __init__(self, game: str = "", path: Optional[str] = None) -> None:
        self.game = game
        self.path = path or os.path.join(bridge.CONFIG_DIR, "glossary", _safe(game) + ".json")
        self.entries: Dict[str, dict] = {}       # 대표 이름(base_key) -> 풀이

    @classmethod
    def load(cls, game: str, path: Optional[str] = None) -> "Glossary":
        g = cls(game, path)
        try:
            with open(g.path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("entries"), dict):
                g.entries = {str(k): v for k, v in d["entries"].items() if isinstance(v, dict)}
        except (OSError, ValueError):
            pass
        return g

    def save(self) -> bool:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"game": self.game, "updated": time.time(), "entries": self.entries}, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False

    def get(self, name: str) -> Optional[dict]:
        return self.entries.get(base_key(name))

    def meaning(self, name: str) -> str:
        """'늑대 / wolf (狼)' 같은 짧은 뜻. 풀이가 없으면 빈 글자."""
        e = self.get(name)
        if not e or not (e.get("en") or e.get("ko")):
            return ""
        s = " / ".join(x for x in (e.get("en"), e.get("ko")) if x)
        return s + ((" (%s)" % e["zh"]) if e.get("zh") else "")

    def missing(self, names: Iterable[str]) -> List[str]:
        """아직 풀이가 없는 대표 이름들 (순서 유지, 중복 없이)."""
        out, seen = [], set()
        for n in names:
            k = base_key(n)
            if k not in self.entries and k not in seen:
                seen.add(k)
                out.append(k)
        return out

    def annotate(self, name: str) -> str:
        """AI 에게 줄 목록의 한 항목: '이름 (뜻)'. 답은 괄호 앞부분만 쓰게 한다."""
        m = self.meaning(name)
        return "%s (%s)" % (name, m) if m else name

    def search(self, query: str, names: Sequence[str], limit: int = 8) -> List[Tuple[str, float, str]]:
        """한국어/영어/한자 이름이나 외형 설명으로 names 중 뜻이 맞는 것을 찾는다. [(이름, 점수 0~1, 이유)]"""
        q = (query or "").strip().lower()
        if len(q) < 2:
            return []
        qn = re.sub(r"\s+", "", q)
        qwords = {w for w in _WORD.findall(q) if w not in STOP}
        best: Dict[str, Tuple[float, str, str]] = {}
        for n in names:
            e = self.get(n)
            if not e:
                continue
            s, why = 0.0, ""
            for f in ("ko", "zh", "en"):
                t = (e.get(f) or "").lower()
                tn = re.sub(r"\s+", "", t)
                if len(tn) < 2:
                    continue
                if tn == qn:
                    c = 1.0
                elif tn in qn or qn in tn:
                    c = 0.85 * min(len(tn), len(qn)) / float(max(len(tn), len(qn))) + 0.1
                else:
                    r = difflib.SequenceMatcher(None, qn, tn).ratio()
                    c = r * 0.8 if r >= 0.75 else 0.0
                if f == "en" and qwords:
                    tw = {w for w in _WORD.findall(t) if w not in STOP}
                    if tw:
                        c = max(c, 0.75 * len(qwords & tw) / float(len(tw)))
                if c > s:
                    s, why = c, "뜻 풀이 '%s'" % (e.get(f) or "")
            if s >= 0.3:
                k = base_key(n)
                if k not in best or s > best[k][0]:
                    best[k] = (s, why, n)
        out = [(n, round(s * 0.9, 3), why) for s, why, n in best.values()]
        out.sort(key=lambda x: -x[1])
        return out[:limit]


def generate(ask_text: Callable[[str], str], names: Sequence[str], game_hint: str, glossary: Glossary,
             on_progress: Optional[Callable[[int, int], None]] = None, chunk: int = CHUNK, max_names: int = MAX_NAMES) -> Tuple[int, List[str]]:
    """아직 풀이가 없는 이름을 AI 에게 물어 채운다 (묶음마다 저장). (새로 풀린 개수, 오류 목록)을 돌려준다."""
    todo = glossary.missing(names)[:max_names]
    done, errors = 0, []
    for i in range(0, len(todo), chunk):
        part = todo[i:i + chunk]
        if on_progress:
            on_progress(i, len(todo))
        try:
            got = parse(ask_text(build_prompt(part, game_hint)), part)
        except Exception as e:  # noqa: BLE001 - 한 묶음 실패가 전체를 막지 않게
            errors.append(str(e)[:200])
            if len(errors) >= 2:
                break
            continue
        for k in part:          # 답에 빠진 이름도 '물어봤음'으로 남겨 다시 묻지 않는다
            glossary.entries[k] = got.get(k) or {"zh": "", "en": "", "ko": "", "kind": "unknown", "sure": 0.0}
        done += sum(1 for k in part if got.get(k, {}).get("en") or got.get(k, {}).get("ko"))
        glossary.save()
    return done, errors


def build_pick_prompt(description: str, in_game_name: str, lines: Sequence[str], game_hint: str = "") -> str:
    who = []
    if in_game_name:
        who.append("its in-game display name is \"%s\"" % in_game_name)
    if description:
        who.append("it looks like: %s" % description)
    return ("A character appears in %s; %s.\n"
            "Below is the closed list of this game's internal character asset names, each with a decoded meaning in parentheses "
            "(names are often romanized Chinese/pinyin or abbreviations). Pick up to 5 entries most likely to be this character, best first. "
            "Translate the display name into the game's original language if that helps. Use ONLY names from the list (the part before the parenthesis).\n"
            "Answer with JSON only: {\"matches\": [{\"name\": \"<name>\", \"confidence\": 0.0-1.0, \"reason\": \"<short>\"}]}\n"
            "List:\n%s") % (("the video game \"%s\"" % game_hint) if game_hint else "a video game", " and ".join(who) or "no details are known", "\n".join(lines))


def pick(ask_text: Callable[[str], str], description: str, in_game_name: str, names: Sequence[str], glossary: Glossary,
         game_hint: str = "", limit: int = 400) -> List[Tuple[str, float, str]]:
    """외형 설명/게임 속 이름으로 AI 에게 목록에서 고르게 한다. 목록에 없는 이름은 버린다. [(이름, 확신, 이유)]"""
    reps: Dict[str, str] = {}
    for n in names:
        reps.setdefault(base_key(n), n)
    items = list(reps.values())[:limit]
    if not items or not (description or in_game_name):
        return []
    obj = _extract_json(ask_text(build_pick_prompt(description, in_game_name, [glossary.annotate(n) for n in items], game_hint)))
    if isinstance(obj, dict):
        obj = obj.get("matches") or []
    if not isinstance(obj, list):
        return []
    lower = {n.lower(): n for n in items}
    lower.update({base_key(n).lower(): n for n in items})
    out: List[Tuple[str, float, str]] = []
    for it in obj:
        if isinstance(it, str):
            it = {"name": it}
        if not isinstance(it, dict):
            continue
        raw = str(it.get("name") or "").split(" (")[0].strip().lower()
        n = lower.get(raw) or lower.get(base_key(raw).lower())      # 'SK_' 를 붙이거나 변형 번호를 바꿔 답해도 받아 준다
        if not n or any(n == o[0] for o in out):
            continue
        try:
            c = float(it.get("confidence", 0.4))
        except (TypeError, ValueError):
            c = 0.4
        out.append((n, max(0.0, min(1.0, c / 100.0 if c > 1.0 else c)), str(it.get("reason") or "")[:120]))
    return out[:5]


def strip_annotation(name: str) -> str:
    """AI 가 '이름 (뜻)' 을 통째로 답했을 때 이름만 남긴다."""
    return str(name or "").split(" (")[0].strip()
