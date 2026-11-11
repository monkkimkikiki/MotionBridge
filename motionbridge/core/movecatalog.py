"""동작 묶음 목록: 한 캐릭터 폴더의 애니메이션을 '동작 한 개' 단위로 묶는다 (Qt 없음).

이름의 낱말을 사전으로 추측하지 않고, 파일 이름·폴더의 구조만 읽는다.
  A_Swordman_Attacks_Plow_Start / _Loop / _End        -> 동작 '공격 · Plow' 의 시작/반복/끝
  A_Swordman_Attacks_Overhead_01 / _02                -> 동작 '공격 · Overhead' 의 1, 2
  A_Swordman_Attacks_Spins_Montage                    -> A_Swordman_Attacks_Spins 의 사본(몽타주) — 목록에서 뺀다
  BS_Swordman / BT_PairedAnimReaction_Swordman        -> 재생할 클립이 아님 — 뺀다
  A_Swordman_Riposte_01  <->  A_Swordman_Riposte_01_Player   -> 서로 짝 (보스 쪽 / 플레이어 쪽)
분류는 Animation/ 아래 첫 폴더(Attacks, Locomotion, HitReactions…)에서 읽고, 폴더가 없으면 이름의 첫 낱말을 쓴다.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

NON_CLIP_PREFIX = ("bs", "bt", "abp", "ao", "bp", "cr", "ans", "an")     # 블렌드 스페이스·비헤이비어 트리·애님 블루프린트·노티파이 등
TYPE_PREFIX = {"a", "as", "am", "anim", "seq", "animseq"}
ROLE_PLAYER = {"player", "attacker", "pc"}
ROLE_OTHER = {"enemy", "victim", "boss"}
PHASE = {"start": 0, "begin": 0, "enter": 0, "in": 0, "intro": 0, "loop": 1, "end": 2, "exit": 2, "out": 2, "finish": 2, "outro": 2}
PHASE_KO = {0: "시작", 1: "반복", 2: "끝"}
_DIR = re.compile(r"^(fwd|forward|front|back|backward|bwd|left|right)+\d*$")
_ANIM_DIRS = {"animation", "animations", "anim", "anims"}
CATEGORY_KO = {"attack": "공격", "locomotion": "이동", "hitreaction": "피격", "hit": "피격", "death": "사망", "riposte": "반격·처형", "idle": "대기",
               "dodge": "회피", "evade": "회피", "spawn": "등장", "intro": "등장", "taunt": "도발", "stagger": "경직", "stun": "기절", "turn": "돌기",
               "jump": "점프", "block": "방어", "parry": "패리", "execution": "처형", "finisher": "처형", "cinematic": "연출", "misc": "기타"}
CATEGORY_ORDER = ["attack", "riposte", "execution", "finisher", "hitreaction", "hit", "stagger", "stun", "death", "dodge", "evade", "block", "parry",
                  "spawn", "intro", "taunt", "turn", "jump", "locomotion", "idle", "cinematic"]


def _norm(w: str) -> str:
    w = w.lower()
    return w[:-1] if w.endswith("s") and len(w) > 3 else w


def _stem(path: str) -> str:
    return os.path.splitext(os.path.basename(str(path).replace("\\", "/")))[0]


@dataclass
class Clip:
    path: str
    category: str = "misc"          # 정규화한 분류 (attack, locomotion …)
    cat_raw: str = ""               # 폴더/이름에 적힌 그대로 (Attacks)
    move: str = ""                  # 동작 이름 (Plow)
    variant: str = ""               # 01 / Start / FwdLeft …
    phase: int = -1                 # 0 시작, 1 반복, 2 끝, -1 없음
    role: str = ""                  # 'player' | 'other' | ''
    montage: bool = False           # 몽타주만 있고 원본 시퀀스가 없는 것
    pair_key: Tuple[str, ...] = ()

    def label(self) -> str:
        cat = CATEGORY_KO.get(self.category, self.cat_raw or self.category)
        s = cat if (not self.move or _norm(self.move) == self.category) else "%s · %s" % (cat, self.move)
        bits = [b for b in (PHASE_KO.get(self.phase, ""), self.variant if self.phase < 0 else "", "플레이어 쪽" if self.role == "player" else "",
                            "몽타주" if self.montage else "") if b]
        return s + ((" (" + ", ".join(bits) + ")") if bits else "")


@dataclass
class Move:
    category: str
    name: str
    clips: List[Clip] = field(default_factory=list)


@dataclass
class MoveCatalog:
    moves: List[Move] = field(default_factory=list)
    clips: Dict[str, Clip] = field(default_factory=dict)       # 보여 줄 클립: 경로 -> Clip
    duplicates: List[str] = field(default_factory=list)        # 원본 시퀀스가 따로 있는 _Montage 사본
    non_clips: List[str] = field(default_factory=list)         # BS_/BT_ 등 재생할 클립이 아닌 것
    partners: Dict[str, List[str]] = field(default_factory=dict)   # 경로 -> 짝 경로들 (보스 쪽 <-> 플레이어 쪽)

    def hidden(self) -> set:
        return set(self.duplicates) | set(self.non_clips)

    def order(self) -> List[str]:
        return [c.path for m in self.moves for c in m.clips]

    def summary(self) -> str:
        s = "동작 %d개 · 클립 %d개" % (len(self.moves), len(self.clips))
        if self.duplicates or self.non_clips:
            s += " (몽타주 사본 %d개 · 재생 불가 %d개는 뺌)" % (len(self.duplicates), len(self.non_clips))
        return s


def parse(path: str, char_names: Iterable[str] = ()) -> Optional[Clip]:
    """경로 하나를 읽는다. 재생할 클립이 아니면(BS_/BT_ 등) None. 몽타주 여부는 clip.montage 에 표시만 한다 (사본인지는 build 가 정한다)."""
    stem = _stem(path)
    toks = [t for t in stem.split("_") if t]
    if not toks:
        return None
    if toks[0].lower() in NON_CLIP_PREFIX:
        return None
    if toks[0].lower() in TYPE_PREFIX:
        toks = toks[1:]
    names = {n.lower() for n in char_names if n}
    toks = [t for t in toks if t.lower() not in names]
    montage = bool(toks) and toks[-1].lower() == "montage"
    if montage:
        toks = toks[:-1]
    role = ""
    rest = []
    for t in toks:
        tl = t.lower()
        if tl in ROLE_PLAYER:
            role = "player"
        elif tl in ROLE_OTHER:
            role = role or "other"
        else:
            rest.append(t)
    pair_key = tuple(t.lower() for t in rest)
    # 분류: Animation/ 아래 첫 폴더 -> 없으면 첫 낱말
    parts = str(path).replace("\\", "/").split("/")[:-1]
    cat_raw = ""
    for i, seg in enumerate(parts):
        if seg.lower() in _ANIM_DIRS and i + 1 < len(parts):
            cat_raw = parts[i + 1]
            break
    if cat_raw and rest and _norm(rest[0]) == _norm(cat_raw):
        rest = rest[1:]
    elif not cat_raw and len(rest) > 1:
        cat_raw, rest = rest[0], rest[1:]
    elif not cat_raw and rest:
        cat_raw = rest[0]
    category = _norm(cat_raw) if cat_raw else "misc"
    phase, variant, name = -1, [], []
    for t in rest:
        tl = t.lower()
        if tl in PHASE and name:                 # 'End' 가 동작 이름 맨 앞이면 이름으로 둔다
            phase = PHASE[tl] if phase < 0 else phase
            variant.append(t)
        elif tl.isdigit() or _DIR.match(tl):
            variant.append(t)
        elif phase >= 0 or (variant and any(v.lower() in PHASE for v in variant)):
            variant.append(t)                    # 단계 낱말 뒤에 붙은 것(End_Uppercut)은 같은 동작의 변형
        else:
            name.append(t)
    return Clip(path, category, cat_raw, "_".join(name), "_".join(variant), phase, role, montage, pair_key)


def build(anims: Iterable[str], char_names: Iterable[str] = ()) -> MoveCatalog:
    cat = MoveCatalog()
    parsed: Dict[str, Clip] = {}
    by_stem: Dict[str, str] = {}
    for p in dict.fromkeys(anims):
        c = parse(p, char_names)
        if c is None:
            cat.non_clips.append(p)
            continue
        parsed[p] = c
        by_stem[_stem(p).lower()] = p
    for p, c in parsed.items():
        if c.montage:
            base = _stem(p).lower()[: -len("_montage")]
            if base in by_stem:                   # 원본 시퀀스가 있는 몽타주 = 사본
                cat.duplicates.append(p)
                continue
        cat.clips[p] = c
    groups: Dict[Tuple[str, str], Move] = {}
    for p, c in cat.clips.items():
        key = (c.category, c.move.lower())
        groups.setdefault(key, Move(c.category, c.move)).clips.append(c)
    for m in groups.values():
        m.clips.sort(key=lambda c: (c.montage, c.role == "player", c.phase if c.phase >= 0 else 0.5, c.variant.lower(), c.path.lower()))

    def cat_rank(m: Move):
        return (CATEGORY_ORDER.index(m.category) if m.category in CATEGORY_ORDER else len(CATEGORY_ORDER), m.category, m.name.lower())

    cat.moves = sorted(groups.values(), key=cat_rank)
    # 짝: 역할 낱말(Player/Enemy)만 다르고 나머지가 같은 클립
    by_key: Dict[Tuple[str, ...], Dict[str, List[str]]] = {}
    for p, c in cat.clips.items():
        if c.montage:
            continue
        by_key.setdefault(c.pair_key, {}).setdefault("player" if c.role == "player" else "other", []).append(p)
    for sides in by_key.values():
        if sides.get("player") and sides.get("other"):
            for p in sides["player"]:
                cat.partners[p] = sorted(sides["other"])
            for p in sides["other"]:
                cat.partners[p] = sorted(sides["player"])
    return cat


def exact_partners(base: str, anims: Iterable[str]) -> List[str]:
    """base 와 같은 폴더 묶음(캐릭터) 안에서, 역할 낱말(Player/Enemy…)만 다르고 이름이 같은 짝 클립들. 몽타주·재생 불가 파일은 뺀다."""
    b = parse(base)
    if b is None:
        return []
    out = []
    for p in anims:
        if p == base:
            continue
        c = parse(p)
        if c is None or c.montage or c.pair_key != b.pair_key:
            continue
        if (c.role == "player") != (b.role == "player"):
            out.append(p)
    return sorted(out)
