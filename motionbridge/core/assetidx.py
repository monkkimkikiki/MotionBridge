"""게임 에셋 이름 색인과 후보 고르기 ('영상으로 찾기'의 두뇌). Qt/네트워크에 의존하지 않는다.

  * 메시(SK_*) 경로 -> 캐릭터 이름/단어, 애니메이션(A_*) 경로 -> 단어
  * 이미지 인식이 낸 추측("Dwarf Warrior" 같은 글)을 메시 후보로 순위 매기기
  * 확정한 메시에 맞는 애니메이션 후보를 (이름 일치 + AnimRef 분류 + 타수) 점수로 정렬
  * 검수자가 고친 결과를 기억해 다음 추측의 첫 후보로 올리기
"""
from __future__ import annotations

import difflib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import textmatch

# 이름에서 의미 없는 단어 (접두사/폴더 잡음)
STOP = {
    "sk", "skm", "sm", "skel", "skeletal", "skeleton", "mesh", "meshes", "a", "am", "as", "anim", "anims", "animation",
    "animations", "montage", "game", "content", "characters", "character", "enemies", "enemy", "monsters", "monster",
    "models", "model", "assets", "asset", "art", "base", "lod", "physics", "physicsasset", "the", "of", "and", "mb",
}
DESC_STOP = {"with", "has", "wearing", "holding", "large", "small", "big", "tall", "very", "looks", "like", "some"}
UNKNOWN_WORDS = {"", "unknown", "none", "n/a", "unidentified", "?", "모름", "알수없음"}

# AnimRef 분류(한글) -> 내부 범주
LABEL_CATEGORY = {
    "타격": "attack", "공격": "attack", "강공격": "attack", "약공격": "attack", "콤보": "attack", "연속기": "attack",
    "피격": "hit", "스킬": "skill", "연출": "cinematic", "회피": "evade", "사망": "death",
    "걷기": "walk", "달리기": "run", "대기": "idle",
}
CATEGORY_KEYWORDS = {
    "attack": ["attack", "atk", "slash", "swing", "strike", "combo", "melee", "slam", "swipe", "punch", "kick", "bite",
               "claw", "stab", "thrust", "chop", "bash", "lunge", "light", "heavy"],
    "hit": ["hitreact", "hit_react", "hurt", "damage", "stagger", "flinch", "knockback", "reaction", "react", "impact", "stun"],
    "skill": ["skill", "ability", "cast", "special", "spell", "ultimate", "charge", "summon", "buff", "magic", "roar", "scream"],
    "cinematic": ["cinematic", "intro", "spawn", "entrance", "execution", "finisher", "cutscene", "emerge", "taunt", "victory", "phase"],
    "evade": ["dodge", "evade", "roll", "dash", "sidestep", "backstep", "retreat", "leap", "jump"],
    "death": ["death", "die", "dead", "dying", "kill"],
    "walk": ["walk"],
    "run": ["run", "sprint"],
    "idle": ["idle", "stand", "breath"],
}
CATEGORY_NAME = {"attack": "공격", "hit": "피격", "skill": "스킬", "cinematic": "연출", "evade": "회피", "death": "사망",
                 "walk": "걷기", "run": "달리기", "idle": "대기"}
KNOWN_LABELS = tuple(LABEL_CATEGORY)

# 플레이어가 조종하는 캐릭터(플레이어블)의 경로/이름에 흔히 들어가는 낱말. 플레이어는 몸(메시)이 여러 벌이어도 스켈레톤·애니메이션을 공유하므로
# 몬스터처럼 '메시 이름과 비슷한 애니메이션'으로 찾지 않고, 이런 낱말이 든 (공용) 애니메이션 경로로 찾는다.
PLAYER_WORDS = {"player", "pc", "playable", "hero", "protagonist", "avatar", "mc", "fallen"}


def is_player_path(path: str) -> bool:
    """메시/애니메이션 경로가 플레이어블 캐릭터 것으로 보이는지 (폴더 이름 포함)."""
    return bool(set(tokens((path or "").replace("\\", "/"), drop_stop=False)) & PLAYER_WORDS)

KIND_MONSTER, KIND_PLAYER = "monster", "player"


def path_kind(path: str) -> str:
    """'player'(플레이어블 캐릭터 것으로 보임) 또는 'monster'(그 밖의 몬스터·보스·NPC 등)."""
    return KIND_PLAYER if is_player_path(path) else KIND_MONSTER


# 게임 안 표시 이름과 리소스 이름이 다를 때를 위한 비슷한 말 (같은 묶음끼리 같은 뜻으로 본다)
SYNONYMS = [
    {"captive", "prisoner", "convict", "inmate", "hostage", "detainee", "thrall", "slave", "jailed"},
    {"nameless", "unnamed", "anonymous", "faceless", "nobody", "noname"},
    {"skeleton", "skeletal", "skel", "bone", "bones", "revenant", "undead"},
    {"zombie", "ghoul", "husk", "walker", "undead", "rotten"},
    {"knight", "soldier", "warrior", "guard", "sentinel", "guardian", "footman"},
    {"dwarf", "dwarven", "dwarves"},
    {"golem", "construct", "colossus", "statue"},
    {"spider", "arachnid", "crawler", "hexapod"},
    {"wolf", "hound", "dog", "beast"},
    {"demon", "fiend", "devil", "imp"},
    {"giant", "titan", "ogre", "brute"},
    {"priest", "cleric", "monk", "acolyte", "cultist"},
    {"archer", "bowman", "ranger", "hunter"},
    {"mage", "wizard", "sorcerer", "witch", "caster"},
    {"king", "lord", "queen", "ruler"},
    {"player", "playable", "hero", "protagonist", "avatar", "pc"},
]
# 한국어 낱말 -> 영어 후보 (공백을 뺀 글자에서 찾는다)
KO_TERMS = {
    "이름없는": ["nameless", "unnamed"], "무명": ["nameless", "unnamed"], "포로": ["captive", "prisoner"], "죄수": ["prisoner", "convict"],
    "수감자": ["prisoner", "inmate"], "해골": ["skeleton"], "스켈레톤": ["skeleton"], "좀비": ["zombie"], "구울": ["ghoul"],
    "기사": ["knight"], "병사": ["soldier"], "전사": ["warrior"], "수호자": ["guardian"], "파수꾼": ["sentinel"],
    "거미": ["spider"], "늑대": ["wolf"], "골렘": ["golem"], "드워프": ["dwarf"], "난쟁이": ["dwarf"], "악마": ["demon"],
    "거인": ["giant"], "사제": ["priest"], "광신도": ["cultist"], "궁수": ["archer"], "마법사": ["mage"], "왕": ["king"],
    "플레이어": ["player"], "플레이어블": ["playable", "player"], "주인공": ["protagonist", "hero", "player"], "조종캐릭터": ["player"],
}
# 이름 짓기에서 뜻이 없는 낱말 (찾을 때 무시)
NAME_NOISE = {"the", "a", "an", "of", "boss", "elite", "minor", "major", "lesser", "greater", "old", "young", "new", "mini", "unique"}
_SYN_OF: Dict[str, set] = {}
for _g in SYNONYMS:
    for _w in _g:
        _SYN_OF.setdefault(_w, set()).update(_g - {_w})


def name_words(text: str) -> Dict[str, float]:
    """표시 이름 -> {단어: 가중치}. 원래 단어 1.0, 한국어 번역/비슷한 말은 0.75."""
    out: Dict[str, float] = {}
    for w in tokens(text or "", drop_stop=True):
        if w not in NAME_NOISE:
            out[w] = 1.0
    ko = re.sub(r"[^\uac00-\ud7a3]", "", text or "")
    for k in sorted(KO_TERMS, key=len, reverse=True):
        if k in ko:
            for w in KO_TERMS[k]:
                out.setdefault(w, 0.9)
            ko = ko.replace(k, "")
    for w in list(out):
        for syn in _SYN_OF.get(w, ()):
            out.setdefault(syn, 0.75)
    return out


_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_PREFIX = re.compile(r"^(sk[a-z]{0,3}|sm)_", re.I)
_SUFFIX = re.compile(r"(_lod\d+|_mesh|_skeletalmesh|_physicsasset)$", re.I)


def tokens(text: str, drop_stop: bool = True) -> List[str]:
    """영문/숫자 덩어리를 소문자 단어로. CamelCase, 밑줄, 폴더 구분을 모두 나눈다."""
    out: List[str] = []
    for chunk in re.split(r"[^A-Za-z0-9]+", text or ""):
        if not chunk:
            continue
        for w in _CAMEL.sub(" ", chunk).split():
            w = w.lower()
            if len(w) < 2 or w.isdigit():
                continue
            if drop_stop and w in STOP:
                continue
            out.append(w)
    return out


def norm_key(text: str) -> str:
    return " ".join(sorted(set(tokens(text))))


def mesh_name(path: str) -> str:
    """'/Game/.../SK_Dwarf_Warrior.SK_Dwarf_Warrior' -> 'Dwarf_Warrior'."""
    base = os.path.basename(path.replace("\\", "/")).split(".")[0]
    base = _PREFIX.sub("", base)
    base = _SUFFIX.sub("", base)
    return base or os.path.basename(path)


def _dir_parts(path: str) -> List[str]:
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    return [p.lower() for p in parts[:-1]]


@dataclass
class MeshEntry:
    path: str
    name: str
    toks: frozenset
    dirs: List[str] = field(default_factory=list)

    @staticmethod
    def of(path: str) -> "MeshEntry":
        n = mesh_name(path)
        return MeshEntry(path, n, frozenset(tokens(n)), _dir_parts(path))


@dataclass
class MeshCandidate:
    path: str
    name: str
    score: float
    why: str = ""


@dataclass
class AnimCandidate:
    path: str
    score: float
    why: str = ""


def hit_count(name: str) -> Optional[int]:
    """'..._3Hit_Combo', 'Attack_Combo2' 같은 이름에서 타수를 읽는다."""
    low = os.path.basename(name).lower()
    for pat in (r"(\d)\s*_?hit", r"hit_?(\d)", r"combo_?(\d)", r"(\d)_?combo", r"attack_?(\d)\b", r"atk_?(\d)\b"):
        m = re.search(pat, low)
        if m:
            return int(m.group(1))
    return None


def category_scores(name: str) -> Dict[str, float]:
    low = os.path.basename(name.replace("\\", "/")).split(".")[0].lower()
    ts = set(tokens(os.path.basename(name), drop_stop=False))
    res: Dict[str, float] = {}
    for cat, kws in CATEGORY_KEYWORDS.items():
        s = 0.0
        for k in kws:
            if k in ts:
                s = max(s, 1.0)
            elif k in low:
                s = max(s, 0.6)
        if s:
            res[cat] = s
    if re.search(r"\dhit|hit\d", low) and "attack" not in res:
        res["attack"] = 0.7  # 3Hit_Combo 처럼 hit 이 타수를 뜻하는 경우
    return res


class MatchMemory:
    """검수자가 확정한 '추측 -> 메시' 짝을 파일에 기억한다. 같은 추측이 다시 나오면 맨 앞 후보로 올린다."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path
        self.data: Dict[str, Dict[str, int]] = {}
        if path and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict):
                    self.data = {k: {a: int(c) for a, c in v.items()} for k, v in d.items() if isinstance(v, dict)}
            except (OSError, ValueError, AttributeError):
                self.data = {}

    @staticmethod
    def key(guess: str, description: str = "") -> str:
        g = (guess or "").strip().lower()
        if g in UNKNOWN_WORDS:
            words = sorted({w for w in tokens(description) if w not in DESC_STOP})[:6]
            return "desc:" + " ".join(words) if words else ""
        return norm_key(guess)

    def best(self, guess: str, description: str = "") -> Optional[str]:
        k = self.key(guess, description)
        d = self.data.get(k)
        if not d:
            return None
        return max(d.items(), key=lambda kv: kv[1])[0]

    def remember(self, guess: str, description: str, asset: str) -> None:
        k = self.key(guess, description)
        if not k or not asset:
            return
        d = self.data.setdefault(k, {})
        d[asset] = d.get(asset, 0) + 1

    def save(self) -> None:
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
        except OSError:
            pass


class AssetIndex:
    def __init__(self, mesh_paths: Iterable[str] = (), anim_paths: Iterable[str] = ()) -> None:
        self.meshes: List[MeshEntry] = [MeshEntry.of(p) for p in dict.fromkeys(mesh_paths)]
        self.anims: List[str] = list(dict.fromkeys(anim_paths))
        self._anim_toks: Dict[str, frozenset] = {}
        self._by_path: Dict[str, MeshEntry] = {m.path: m for m in self.meshes}
        self._kind: Dict[str, str] = {}

    def kind(self, path: str) -> str:
        """경로의 종류(player/monster). 한 번 계산하면 기억해서 수만 개를 거를 때도 빠르다."""
        k = self._kind.get(path)
        if k is None:
            k = self._kind[path] = path_kind(path)
        return k

    def anims_of_kinds(self, kinds) -> List[str]:
        """kinds(예: {'player'}) 에 맞는 애니메이션 경로만. kinds 가 None/빈 값이거나 둘 다면 전체."""
        ks = set(kinds or ())
        if not ks or ks >= {KIND_MONSTER, KIND_PLAYER}:
            return list(self.anims)
        return [a for a in self.anims if self.kind(a) in ks]

    def __bool__(self) -> bool:
        return bool(self.meshes)

    def mesh(self, path: str) -> Optional[MeshEntry]:
        return self._by_path.get(path)

    def names(self, limit: Optional[int] = None) -> List[str]:
        """이미지 인식에 알려줄 캐릭터 이름 목록 (중복 제거, 정렬)."""
        seen: Dict[str, str] = {}
        for m in self.meshes:
            seen.setdefault(m.name.lower(), m.name)
        out = sorted(seen.values(), key=str.lower)
        if limit and len(out) > limit:  # 줄여야 하면 플레이어블 이름은 꼭 남긴다 (AI 가 플레이어도 알아보게)
            pl = [n for n in out if is_player_path(n)][: max(limit // 4, 1)]
            keep = set(pl)
            out = sorted(pl + [n for n in out if n not in keep][: limit - len(pl)], key=str.lower)
            return out
        return out[:limit] if limit else out

    def player_meshes(self) -> List[MeshEntry]:
        return [m for m in self.meshes if is_player_path(m.path)]

    def player_rig_meshes(self) -> List[MeshEntry]:
        """플레이어블 캐릭터 메시 후보: 경로에 player/pc/hero 같은 낱말이 든 메시 + 그 메시와 같은 폴더에 있는 메시
        (같은 리그를 쓰는 의상·장비 변형은 보통 같은 폴더에 있다). 경로 순으로 정렬."""
        base = self.player_meshes()
        folders = {os.path.dirname(m.path.replace("\\", "/")) for m in base}
        seen, out = set(), []
        for m in self.meshes:
            if is_player_path(m.path) or os.path.dirname(m.path.replace("\\", "/")) in folders:
                if m.path not in seen:
                    seen.add(m.path)
                    out.append(m)
        out.sort(key=lambda m: m.path.lower())
        return out

    # ------------------------------------------------------------------ 메시 후보
    def rank_meshes(self, guess: str, description: str = "", limit: int = 8, memory: Optional[MatchMemory] = None) -> List[MeshCandidate]:
        g = (guess or "").strip()
        unknown = g.lower() in UNKNOWN_WORDS
        gtoks = set() if unknown else set(tokens(g))
        dtoks = set(tokens(description))
        gnorm = "" if unknown else re.sub(r"[^a-z0-9]", "", g.lower())
        remembered = memory.best(guess, description) if memory else None
        scored: List[MeshCandidate] = []
        for m in self.meshes:
            s, why = 0.0, ""
            if m.path == remembered:
                s, why = 2.0, "이전에 확정한 매칭"
            else:
                mnorm = re.sub(r"[^a-z0-9]", "", m.name.lower())
                if gnorm and mnorm == gnorm:
                    s, why = 1.0, "이름 일치"
                else:
                    cov_g = (len(m.toks & gtoks) / len(m.toks)) if (m.toks and gtoks) else 0.0
                    cov_d = (len(m.toks & dtoks) / len(m.toks)) if (m.toks and dtoks) else 0.0
                    sim = difflib.SequenceMatcher(None, gnorm, mnorm).ratio() if gnorm else 0.0
                    s = 0.55 * cov_g + 0.35 * sim + 0.10 * cov_d
                    if gtoks and (m.toks & gtoks):
                        why = "이름 단어 일치: " + ", ".join(sorted(m.toks & gtoks))
                    elif sim >= 0.6:
                        why = "이름이 비슷함"
                    elif m.toks & dtoks:
                        why = "설명에 나온 단어: " + ", ".join(sorted(m.toks & dtoks))
            if s > 0.08:
                scored.append(MeshCandidate(m.path, m.name, round(s, 3), why))
        scored.sort(key=lambda c: (-c.score, len(c.name), c.name.lower()))
        return scored[:limit]

    def search(self, text: str, limit: int = 30) -> List[MeshCandidate]:
        """검수 표에서 직접 검색: 부분 문자열 우선, 없으면 낱말·비슷한 말로 찾는다."""
        q = (text or "").strip().lower()
        if not q:
            return [MeshCandidate(m.path, m.name, 0.0) for m in self.meshes[:limit]]
        if "," in q:  # 쉼표: 모두 포함 (예: character,attack)
            ps = textmatch.parts(q)
            hits = [MeshCandidate(m.path, m.name, 0.5) for m in self.meshes if all(t in m.path.lower() for t in ps)]
            hits.sort(key=lambda c: (len(c.name), c.name.lower()))
            return hits[:limit]
        hits = [MeshCandidate(m.path, m.name, 1.0 if m.name.lower() == q else 0.5) for m in self.meshes if q in m.path.lower()]
        hits.sort(key=lambda c: (-c.score, len(c.name), c.name.lower()))
        return hits[:limit] if hits else self.find_by_name(text, limit)

    def find_by_name(self, text: str, limit: int = 8) -> List[MeshCandidate]:
        """게임 속 표시 이름('The Nameless Captive', '이름 없는 포로')으로 메시 후보 찾기.

        붙여 쓴 이름(NamelessCaptive), 낱말 일치, 비슷한 말(captive~prisoner), 오타(ratio>=0.85), 폴더 이름까지 본다.
        """
        qw = name_words(text)
        compact = re.sub(r"[^a-z0-9]", "", re.sub(r"^\s*the\s+", "", (text or "").lower()))
        if not qw and len(compact) < 4:
            return []
        orig = [w for w, v in qw.items() if v >= 1.0] or list(qw)
        out: List[MeshCandidate] = []
        for m in self.meshes:
            mnorm = re.sub(r"[^a-z0-9]", "", m.name.lower())
            toks = set(m.toks)
            folder = {t for d in m.dirs[-2:] for t in tokens(d)} - toks
            hit_w: Dict[str, float] = {}
            for t in toks | folder:
                best, via = 0.0, ""
                for w, wt in qw.items():
                    if t == w:
                        v = wt
                    elif len(t) >= 5 and len(w) >= 5 and difflib.SequenceMatcher(None, t, w).ratio() >= 0.8:
                        v = 0.8 * wt
                    else:
                        continue
                    if t in folder:
                        v *= 0.7
                    if v > best:
                        best, via = v, w
                if best:
                    hit_w[t] = best
            if not hit_w and not (len(compact) >= 5 and (compact in mnorm or (len(mnorm) >= 5 and mnorm in compact))):
                continue
            cov_m = sum(v for t, v in hit_w.items() if t in toks) / max(len(toks), 1)
            matched_q = {w for w in orig if any(t == w or (t in _SYN_OF.get(w, ())) for t in hit_w)}
            cov_q = len(matched_q) / max(len(orig), 1)
            sc = 0.5 * cov_m + 0.5 * cov_q
            why = "낱말 일치: " + ", ".join(sorted(hit_w))
            if compact and mnorm == compact:
                sc, why = 1.0, "이름 일치"
            elif len(compact) >= 5 and (compact in mnorm or (len(mnorm) >= 5 and mnorm in compact)):
                sc, why = max(sc, 0.95), "이름에 포함"
            syn = sorted(t for t in hit_w if t not in qw or qw[t] < 1.0)
            if syn and "이름 일치" not in why:
                why += " (비슷한 말: %s)" % ", ".join(syn)
            out.append(MeshCandidate(m.path, m.name, round(min(sc, 1.0), 3), why))
        out.sort(key=lambda c: (-c.score, len(c.name), c.name.lower()))
        return out[:limit]

    # ------------------------------------------------------------------ 애니메이션 후보
    def _toks_of_anim(self, p: str) -> frozenset:
        t = self._anim_toks.get(p)
        if t is None:
            t = frozenset(tokens(p.replace("\\", "/")))
            self._anim_toks[p] = t
        return t

    def char_score(self, mesh: MeshEntry, anim_path: str) -> Tuple[float, str]:
        at = self._toks_of_anim(anim_path)
        overlap = (len(mesh.toks & at) / len(mesh.toks)) if mesh.toks else 0.0
        ad = _dir_parts(anim_path)
        common = 0
        for a, b in zip(mesh.dirs, ad):
            if a != b:
                break
            common += 1
        dirscore = common / max(len(mesh.dirs), 1)
        s = 0.7 * overlap + 0.3 * dirscore
        why = ""
        if overlap:
            why = "이름 일치 %d%%" % round(overlap * 100)
        elif dirscore >= 0.5:
            why = "같은 폴더 계열"
        return s, why

    def player_score(self, anim_path: str) -> Tuple[float, str]:
        """플레이어블 캐릭터 애니메이션 점수: 경로(폴더 포함)에 player/pc/hero 같은 낱말이 있으면 1."""
        hit = set(tokens(anim_path.replace("\\", "/"), drop_stop=False)) & PLAYER_WORDS
        return (1.0, "플레이어 공용 (%s)" % ", ".join(sorted(hit))) if hit else (0.0, "")

    def rank_anims(self, mesh_path: str, label: Optional[str] = None, hits: Optional[int] = None, limit: int = 80,
                   min_char: float = 0.3, player: Optional[bool] = None) -> List[AnimCandidate]:
        """player=None 이면 메시 경로로 자동 판단, True/False 는 강제."""
        m = self.mesh(mesh_path) or MeshEntry.of(mesh_path)
        if player is None:
            player = is_player_path(mesh_path)
        cat = LABEL_CATEGORY.get((label or "").strip()) if label else None
        out: List[AnimCandidate] = []
        for a in self.anims:
            cs, why = self.player_score(a) if player else self.char_score(m, a)
            if cs < min_char:
                continue
            reasons = [why] if why else []
            total = cs
            if cat:
                scores = category_scores(a)
                top = max(scores.values()) if scores else 0.0
                own = max(scores, key=scores.get) if scores else None
                ls = 0.0
                if scores.get(cat, 0.0) >= top and top > 0:  # 동점이면 둘 다 본래 분류로 본다
                    ls = 1.0
                elif cat in scores:
                    ls = 0.5
                elif scores:
                    ls = -0.2  # 다른 분류로 읽히는 이름
                total = 0.55 * cs + 0.45 * max(ls, 0.0) + (0.1 * ls if ls < 0 else 0.0)
                if ls >= 1.0:
                    reasons.append("분류 '%s' 단어 포함" % CATEGORY_NAME[cat])
                elif ls > 0:
                    reasons.append("분류 '%s' 단어가 섞임" % CATEGORY_NAME[cat])
                elif ls < 0:
                    reasons.append("다른 분류('%s')로 보임" % CATEGORY_NAME[own])
            if hits:
                n = hit_count(a)
                if n is not None:
                    if n == hits:
                        total += 0.2
                        reasons.append("%d타 일치" % hits)
                    else:
                        total -= 0.08
            out.append(AnimCandidate(a, round(total, 3), ", ".join(reasons)))
        out.sort(key=lambda c: (-c.score, c.path.lower()))
        return out[:limit]

    # ------------------------------------------------------------------ 캐시
    def to_json(self, game_key: str = "") -> dict:
        return {"game": game_key, "saved": int(time.time()), "meshes": [m.path for m in self.meshes], "anims": self.anims}

    @staticmethod
    def from_json(d: dict) -> "AssetIndex":
        return AssetIndex(d.get("meshes") or [], d.get("anims") or [])


def save_index(path: str, idx: AssetIndex, game_key: str = "") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(idx.to_json(game_key), f, ensure_ascii=False)


def load_index(path: str, game_key: str = "") -> Optional[AssetIndex]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or (game_key and d.get("game") != game_key):
        return None
    idx = AssetIndex.from_json(d)
    return idx if idx.meshes or idx.anims else None


def filter_paths(paths: Sequence[str], pattern: str) -> List[str]:
    if not pattern.strip():
        return list(paths)
    rx = re.compile(textmatch.and_regex(pattern))  # 쉼표로 나누면 모두 포함
    return [p for p in paths if rx.search(p)]
