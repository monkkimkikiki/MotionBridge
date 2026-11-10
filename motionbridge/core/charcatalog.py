"""캐릭터 카탈로그: 에셋을 '캐릭터 한 명' 단위로 묶고, 게임 안 표시 이름(영/한)과 연결한다. Qt/네트워크에 의존하지 않는다.

왜 필요한가
  * 메시 이름(SK_Swordman, SK_Swordman_Skeleton, SK_Swordman_ArmL_Detached ...)은 한 캐릭터에 여러 개라 AI 에게 그대로 주면
    목록이 부풀고(413종), 잘라 내면 뒤쪽 캐릭터가 영영 안 보인다. 캐릭터 폴더 단위로 묶으면 100여 종으로 줄어 통째로 줄 수 있다.
  * 게임 안 표시 이름은 .locres 의 글자 표(ST_Core_Bosses / MiniBosses / NpcNames / Shells)에 있고, 키가 폴더 이름과 이어진다
    (예: Boss_Swordman = '이름 없는 포로' / 'The Nameless Captive'). 화면에 이름이 뜨면(보스 체력바 등) AI 추측 없이 바로 찾는다.
  * 캐릭터가 정해지면 메시와 애니메이션은 '이름이 비슷한 것'이 아니라 '같은 폴더 소속'으로 정확히 모은다.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from . import assetidx
from .assetidx import tokens

# 글자 표 이름 -> 종류. 키가 폴더 이름과 이어지는 표만 쓴다 (아이템·대사 표는 제외).
NAME_TABLES = {"ST_Core_Bosses": "boss", "ST_Core_MiniBosses": "miniboss", "ST_Core_NpcNames": "npc", "ST_Core_Shells": "shell"}
KIND_ORDER = {"boss": 0, "miniboss": 1, "enemy": 2, "npc": 3, "shell": 4}
_KIND_RANK = {"enemy": 1, "npc": 1, "shell": 1, "miniboss": 2, "boss": 3}   # 더 높은 쪽이 이긴다
_KIND_CAT = {"boss": "boss", "miniboss": "enemy", "npc": "npc", "shell": "shell"}

_CHAR_RX = re.compile(r"(?i)(?:^|/)(?P<root>[^/]+)/Characters/(?P<cat>Bosses|Boss|Enemies|Enemy|NPCs?|Shells?)/(?P<rest>.+)$")
# 캐릭터 폴더 '안'에 흔한 구조 폴더 (이게 보이면 그 위 폴더가 캐릭터)
_STRUCT = {"art", "animation", "animations", "anim", "mesh", "meshes", "model", "models", "cinematic", "cinematics", "actions", "attacks", "skeleton",
           "materials", "textures", "vfx", "fx", "audio", "sounds", "blueprints", "bp", "ai", "physics", "weapon", "weapons", "sk", "matfix", "lod", "_shared"}
_GROUPS = {"bosses", "boss", "enemies", "enemy", "animations", "animation"}   # 캐릭터가 아니라 묶음 폴더
# 메시 이름 꼬리표: 몸통이 아니라 부품/변형
_VARIANT_TAIL = re.compile(r"(?i)(_skeleton|_physics|_postphysics|_physicsasset|_detached|_weapon|_weap|_combined|_hidden|_lod\d+|_matfix|_armor|_armour|"
                           r"_arm[lr]|_leg[lr]|_helmet|_shoes|_cape|_cloth|_decap\w*|_proxy|_cinematic|_cine)(_.*)?$")
_NOISE_KEY = {"boss", "miniboss", "mini", "name", "names", "phase", "killer", "dungeon", "headless", "alt", "skin", "the", "base", "ms", "ms1", "legacy"}
_ALIASES = {"boss_final": "FinalBoss", "boss_head": "HeadBoss"}   # 글자 표 키 -> 폴더 이름 (이름이 달라 자동으로 못 이을 때만)
_SKIP_SHELL_KEY = re.compile(r"(?i)(tagline|helmet|skin|^withoutshell$|^switchshell|^solomon$)")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9가-힣]", "", (s or "").lower())


def _core(text: str, drop=("headless",)) -> str:
    """키/폴더 이름 -> 비교용 글자: 'Phase2' 꼬리와 잡음 낱말(Boss, MiniBoss, MS1 ...)을 뺀 소문자 붙임."""
    t = re.sub(r"(?i)phase\d*", "", (text or "").replace(".", "_"))
    return "".join(w for w in tokens(t, drop_stop=False) if w not in _NOISE_KEY and w not in drop)


def norm_text(s: str) -> str:
    """화면 글자/표시 이름 비교용: 소문자, 글자·숫자·한글만, 앞의 The 제거."""
    return _norm(re.sub(r"^\s*the\s+", "", (s or "").lower()))


MIN_CHARS = 8      # 카탈로그가 이보다 작으면(= 이 게임의 폴더 규칙을 못 읽은 것) 쓰지 않고 예전 방식으로 간다


@dataclass
class Character:
    id: str                       # 폴더 이름 (예: 'Swordman', 'Brigands/BrigBase')
    root: str                     # 'Sparta' / 'MS1' / 'Legacy'
    folder: str                   # 경로 접두 (예: 'MortalShell2/Content/Sparta/Characters/Bosses/Swordman')
    cat: str                      # boss | enemy | npc | shell  (폴더 위치)
    kind: str = ""                # boss | miniboss | enemy | npc | shell  (글자 표가 있으면 그쪽이 우선)
    legacy: bool = False          # Sparta 가 아닌 옛 게임(MS1/Legacy) 쪽 에셋
    en: List[str] = field(default_factory=list)
    ko: List[str] = field(default_factory=list)
    meshes: List[str] = field(default_factory=list)
    anims: List[str] = field(default_factory=list)

    @property
    def leaf(self) -> str:
        return self.id.split("/")[-1]

    @property
    def names(self) -> List[str]:
        return self.en + self.ko

    def display(self) -> str:
        return " / ".join(dict.fromkeys(self.en[:1] + self.ko[:1])) or self.leaf

    def body_mesh(self) -> Optional[str]:
        """대표 몸통 메시: 이름이 폴더와 같은 것 > 부품·변형 꼬리표가 없는 것 > 가장 짧은 것."""
        want = _norm(self.leaf)
        rigged = [p for p in self.meshes if assetidx.is_rigged_path(p)] or self.meshes
        if not rigged:
            return None

        def key(p: str):
            n = assetidx.mesh_name(p)
            return (_norm(n) != want, bool(_VARIANT_TAIL.search(n)), len(n), p)
        return min(rigged, key=key)

    def body_name(self) -> str:
        p = self.body_mesh()
        return assetidx.mesh_name(p) if p else self.leaf


@dataclass
class Found:
    char: Character
    score: float
    why: str = ""


def split_path(path: str) -> Optional[Tuple[str, str, List[str]]]:
    """경로 -> (root, 분류, 분류 아래 폴더들). 캐릭터 경로가 아니면 None."""
    m = _CHAR_RX.search((path or "").replace("\\", "/"))
    if not m:
        return None
    segs = [s for s in m.group("rest").split("/") if s]
    c = m.group("cat").lower()
    cat = {"bosses": "boss", "boss": "boss", "enemies": "enemy", "enemy": "enemy"}.get(c, "shell" if c.startswith("shell") else "npc")
    return m.group("root"), cat, segs[:-1]    # 마지막은 파일 이름


def _is_struct(seg: str) -> bool:
    return seg.lower() in _STRUCT


def _families(parsed: Sequence[Tuple[str, str, List[str]]]) -> Dict[Tuple[str, str], Set[str]]:
    """'가족 폴더' 찾기: 그 아래에 구조 폴더가 아닌 폴더가 둘 이상이면 (예: Brigands/BrigBase, Brigands/Heavy) 캐릭터는 한 단계 더 아래다."""
    kids: Dict[Tuple[str, str, str], Set[str]] = {}
    direct: Set[Tuple[str, str, str]] = set()
    for root, c, segs in parsed:
        i = 0
        while i < len(segs) and segs[i].lower() in _GROUPS:
            i += 1
        if i + 1 >= len(segs):
            continue
        k = (root, c, segs[i])
        (direct.add(k) if _is_struct(segs[i + 1]) else kids.setdefault(k, set()).add(segs[i + 1]))
    out: Dict[Tuple[str, str], Set[str]] = {}
    for (root, c, top), ks in kids.items():
        if len(ks) >= 2 and (root, c, top) not in direct:
            out.setdefault((root, c), set()).add(top)
    return out


def _char_id(segs: Sequence[str], family: Set[str]) -> str:
    i = 0
    while i < len(segs) and segs[i].lower() in _GROUPS:
        i += 1
    if i >= len(segs) or _is_struct(segs[i]):
        return ""
    top = segs[i]
    if top in family:
        return top + "/" + segs[i + 1] if i + 1 < len(segs) and not _is_struct(segs[i + 1]) else ""
    return top


class Catalog:
    def __init__(self) -> None:
        self.chars: Dict[Tuple[str, str], Character] = {}      # (root, id) -> Character
        self._fams: Dict[Tuple[str, str], Set[str]] = {}

    # ------------------------------------------------------------------ 만들기
    @staticmethod
    def build(mesh_paths: Iterable[str], anim_paths: Iterable[str] = (), name_entries: Iterable[Sequence[str]] = ()) -> "Catalog":
        meshes, anims = list(dict.fromkeys(mesh_paths)), list(dict.fromkeys(anim_paths))
        items = [(p, True, split_path(p)) for p in meshes] + [(p, False, split_path(p)) for p in anims]
        items = [(p, is_mesh, sp) for p, is_mesh, sp in items if sp]
        cat = Catalog()
        cat._fams = _families([sp for _p, _m, sp in items])
        # '옛 게임(MS1/Legacy) 쪽 에셋' 구분은 Mortal Shell II 전용 규칙: 'Sparta' 루트가 있을 때만 그 밖의 루트를 legacy 로 본다.
        # (다른 게임은 루트 이름이 달라 전부 legacy 로 숨겨져 목록이 0명이 되는 일을 막는다.)
        has_primary = any(root.lower() == "sparta" for root, _c, _s in (sp for _p, _m, sp in items))
        for p, is_mesh, (root, c, segs) in items:
            cid = _char_id(segs, cat._fams.get((root, c), set()))
            if not cid:
                continue
            ch = cat.chars.get((root, cid))
            if ch is None:
                low = p.replace("\\", "/")
                at = low.find("/" + cid + "/", max(low.lower().find("/characters/"), 0))
                ch = cat.chars[(root, cid)] = Character(cid, root, low[: at + 1 + len(cid)] if at >= 0 else "", c, c,
                                                        legacy=has_primary and root.lower() != "sparta")
            (ch.meshes if is_mesh else ch.anims).append(p)
        cat.link_names(name_entries)
        return cat

    # ------------------------------------------------------------------ 표시 이름 연결
    def link_names(self, entries: Iterable[Sequence[str]]) -> int:
        """locres 항목 [언어, 표, 키, 글자] 에서 캐릭터 이름 표만 골라 폴더에 잇는다. 이은 키 수를 돌려준다."""
        rows: Dict[Tuple[str, str], Dict[str, str]] = {}
        for e in entries:
            if len(e) >= 4 and e[1] in NAME_TABLES and e[0] in ("en", "ko"):
                rows.setdefault((e[1], e[2]), {})[e[0]] = str(e[3]).strip()
        n = 0
        for (table, key), tx in rows.items():
            if table == "ST_Core_Shells" and _SKIP_SHELL_KEY.search(key):
                continue   # 플레이어 스킨/문구 항목은 캐릭터 이름이 아니다
            kind = NAME_TABLES[table]
            ch = self._char_for_key(key, kind)
            if ch is None:
                continue
            if _KIND_RANK[kind] > _KIND_RANK.get(ch.kind, 0):
                ch.kind = kind
            primary = self._exact_key(key, ch)
            for lang, lst in (("en", ch.en), ("ko", ch.ko)):
                t = tx.get(lang)
                if t and t not in lst:
                    lst.insert(0, t) if primary else lst.append(t)
            n += 1
        return n

    @staticmethod
    def _exact_key(key: str, ch: Character) -> bool:
        """키가 'Boss_<폴더>' / '<폴더>_MiniBoss' 처럼 폴더 이름과 정확히 같은가 (변형·페이즈 이름이 아니라 대표 이름)."""
        k = _core(key)
        return bool(k) and k == _core(ch.leaf) and not re.search(r"(?i)phase\d|headless|dungeon|winter", key)

    def _char_for_key(self, key: str, kind: str) -> Optional[Character]:
        alias = _ALIASES.get(key.lower())
        if alias:
            hit = [c for c in self.chars.values() if _norm(c.leaf) == _norm(alias)]
            if hit:
                return hit[0]
        kn = _core(key)
        if not kn:
            return None
        want_cat = _KIND_CAT[kind]
        best: Optional[Tuple[float, Character]] = None
        for ch in self.chars.values():
            cn = _core(ch.leaf)
            if not cn:
                continue
            if kn == cn:
                sc = 1.0
            elif kind in ("boss", "miniboss") and min(len(cn), len(kn)) >= 5 and (kn.startswith(cn) or cn.startswith(kn)):
                sc = 0.88   # 같은 몸을 쓰는 변형·줄임 (Grisha ← GrishaCaged, Offspring ← Offspring_Fire, Hutchback ← HutchbackCarrier)
            else:
                r = difflib.SequenceMatcher(None, kn, cn).ratio()
                sc = r if r >= 0.85 else 0.0   # 오타·줄임말(LadyOffTheWoods ~ LadyOfTheWoods, BrigandElite ~ BrigElite) 정도만 허용
            if sc <= 0:
                continue
            sc += 0.05 if ch.cat == want_cat else 0.0
            sc += 0.02 if _norm(ch.leaf) == kn else 0.0   # 'MS1_Grisha' 보다 'Grisha' 를 먼저
            sc -= 0.1 if ch.legacy else 0.0
            if best is None or sc > best[0]:
                best = (sc, ch)
        return best[1] if best and best[0] >= 0.85 else None

    # ------------------------------------------------------------------ 쓰기 좋은 모양
    def all(self, include_legacy: bool = False) -> List[Character]:
        """몸(리깅된 메시)이 있는 캐릭터. 보스 > 미니보스 > 일반 > NPC > 셸 순, 이름순."""
        out = [c for c in self.chars.values() if (include_legacy or not c.legacy) and c.meshes]
        return sorted(out, key=lambda c: (KIND_ORDER.get(c.kind, 9), c.id.lower()))

    @staticmethod
    def line(c: Character) -> str:
        nm = c.display()
        return "%s = %s [%s]" % (c.body_name(), nm, c.kind) if nm != c.leaf else "%s [%s]" % (c.body_name(), c.kind)

    def vocab_lines(self, include_legacy: bool = False, chars: Optional[Sequence[Character]] = None) -> List[str]:
        """AI 에게 줄 이름 목록 (전부, 자르지 않는다): '<id> = <영어 이름> / <한국어 이름> [종류]'."""
        return [self.line(c) for c in (self.all(include_legacy) if chars is None else chars)]

    def vocab_names(self, include_legacy: bool = False, chars: Optional[Sequence[Character]] = None) -> List[str]:
        return [c.body_name() for c in (self.all(include_legacy) if chars is None else chars)]

    def by_name(self, text: str) -> Optional[Character]:
        """메시 이름/폴더 이름/표시 이름이 정확히 일치하는 캐릭터 (Sparta 쪽 우선)."""
        n = norm_text(text)
        if not n:
            return None
        hits = [c for c in self.chars.values()
                if n in (_norm(c.leaf), _norm(c.body_name())) or n in {norm_text(x) for x in c.names}]
        return sorted(hits, key=lambda c: (c.legacy, bool(c.meshes) is False, len(c.id)))[0] if hits else None

    def find(self, text: str, limit: int = 5) -> List[Found]:
        """화면에서 읽은 글자(영/한, 오타 조금 허용)로 캐릭터 찾기. 표시 이름이 우선이고 폴더 이름은 보조."""
        q = norm_text(text)
        if len(q) < 2:
            return []
        out: List[Found] = []
        for c in self.chars.values():
            best, why = 0.0, ""
            for nm in c.names:
                n = norm_text(nm)
                if len(n) < 2:
                    continue
                if q == n:
                    s = 1.0
                elif len(n) >= 3 and n in q:
                    s = 0.9 * min(1.0, 0.5 + len(n) / max(len(q), 1))
                elif len(q) >= 3 and q in n:
                    s = 0.8 * len(q) / len(n)
                else:
                    r = difflib.SequenceMatcher(None, q, n).ratio()
                    s = r * 0.95 if r >= 0.78 else 0.0
                if s > best:
                    best, why = s, "게임 글자 '%s'" % nm
            if best == 0.0:
                ln = _norm(c.leaf)
                if len(ln) >= 5 and ln in q:
                    best, why = 0.75, "리소스 이름 '%s'" % c.leaf
            if best > 0:
                out.append(Found(c, round(best * (0.9 if c.legacy else 1.0), 3), why))
        out.sort(key=lambda f: (-f.score, f.char.legacy, len(f.char.id)))
        return out[:limit]

    def resolve(self, answer: str) -> Optional[Character]:
        """AI 가 낸 이름(목록 줄 통째/ID/영어·한국어 이름/오타)을 캐릭터로. 못 이으면 None."""
        a = (answer or "").strip()
        if not a:
            return None
        head = re.split(r"\s*(?:=|\[)", a, maxsplit=1)[0].strip()   # '<id> = ...' 줄이 통째로 온 경우
        c = self.by_name(head) or self.by_name(a) or self.by_name(assetidx.mesh_name(head))   # 'SK_Swordman' 처럼 접두가 붙어 와도
        if c:
            return c
        f = self.find(head, 1)
        return f[0].char if f and f[0].score >= 0.85 else None

    def of_path(self, path: str) -> Optional[Character]:
        """메시/애니메이션 경로가 속한 캐릭터."""
        sp = split_path(path)
        if not sp:
            return None
        root, c, segs = sp
        cid = _char_id(segs, self._fams.get((root, c), set()))
        return self.chars.get((root, cid)) if cid else None
