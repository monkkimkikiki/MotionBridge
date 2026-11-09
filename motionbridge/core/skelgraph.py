"""파일 안의 실제 데이터로 '캐릭터 > 메쉬 / 애니메이션' 을 묶는다 (이름·폴더만 보고 묶지 않는다).

도우미(mbextract inspect)가 파일을 열어 알려 주는 것:
  mesh      스켈레탈 메쉬 + 쓰는 스켈레톤 경로          anim     애니메이션 시퀀스 + 쓰는 스켈레톤, 프레임/길이/트랙 수
  skeleton  스켈레톤 자산                                  montage  몽타주/컴포지트 + 참조하는 시퀀스
  static    리깅 없는 메쉬                                 unreadable  열지 못함

묶는 기준 = 스켈레톤. 같은 스켈레톤을 쓰는 메쉬와 애니메이션이 한 캐릭터.
'재생 가능' = 시퀀스이고, 스켈레톤이 확인되고, 프레임·길이가 0보다 크고, 키(트랙)가 있는 것. 그 밖은 목록에서 뺀다(이유는 남긴다).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

# 이름만 보고 '볼 필요 없는' 자산을 미리 거른다 (텍스처/머티리얼/사운드/블루프린트 등). 파일 안을 열기 전의 속도용 거르개일 뿐, 종류 판정은 하지 않는다.
SKIP_PREFIX = re.compile(r"(?i)(^|/)(T|TX|M|MI|MF|MPC|SM_?Mat|SFX|S|SC|SW|SA|NS|PS|P|FX|BP|WBP|ABP|AB|ST|DT|CT|E|F|PA|PHYS|PM|RT|LV|MPC)_[^/]*$")
SKIP_KEYWORD = re.compile(r"(?i)/(textures?|materials?|sounds?|audio|ui|niagara|vfx|fx|blueprints?|widgets?|maps?|levels?|movies?)/")


def norm_key(p: str) -> str:
    """'/Game/Characters/X/Y.Y' 와 'Proj/Content/Characters/X/Y.uasset' 를 같은 키 'characters/x/y' 로."""
    s = (p or "").replace("\\", "/").strip()
    if not s:
        return ""
    s = s.split(" ")[-1] if " " in s and "/" in s.split(" ")[-1] else s  # 'Class /Game/..' 형태
    low = s.lower()
    i = low.find("/content/")
    if i >= 0:
        s = s[i + len("/content/"):]
    elif low.startswith("/game/"):
        s = s[len("/game/"):]
    elif low.startswith("game/"):
        s = s[len("game/"):]
    else:
        s = s.lstrip("/")
    s = re.sub(r"\.(uasset|umap|uexp)$", "", s, flags=re.I)
    if "." in os.path.basename(s):  # '경로.오브젝트이름'
        s = s.rsplit(".", 1)[0]
    return s.lower().strip("/")


def worth_inspecting(path: str) -> bool:
    return not (SKIP_PREFIX.search(path) or SKIP_KEYWORD.search(path.replace("\\", "/")))


@dataclass
class Rec:
    path: str                    # 게임 안 에셋 경로 (목록에서 쓰는 형태)
    kind: str                    # mesh static skeleton anim montage unreadable
    name: str = ""
    skeleton: str = ""           # 쓰는 스켈레톤 (원본 문자열)
    frames: int = -1
    length: float = -1.0
    tracks: int = 0
    compressed: bool = False
    bones: int = 0
    sequences: List[str] = field(default_factory=list)
    reason: str = ""

    @property
    def key(self) -> str:
        return norm_key(self.path)

    @property
    def skel_key(self) -> str:
        return norm_key(self.skeleton)

    def problems(self) -> List[str]:
        """재생할 수 없는 이유 (비어 있으면 재생 가능). 시퀀스/몽타주에만 의미가 있다."""
        out: List[str] = []
        if self.kind == "anim":
            if not self.skel_key:
                out.append("스켈레톤 연결 확인 안 됨")
            if self.frames == 0 or (self.frames < 0 and self.length <= 0):
                out.append("프레임 없음")
            if self.length == 0:
                out.append("길이 0")
            if self.tracks == 0 and not self.compressed:   # -1 = 도우미가 키 수를 못 읽음(모름) -> 프레임/길이가 있으면 통과
                out.append("키(트랙) 데이터 없음")
        elif self.kind == "montage":
            if not self.sequences:
                out.append("참조한 시퀀스 없음")
            if not self.skel_key:
                out.append("스켈레톤 연결 확인 안 됨")
        else:
            out.append("애니메이션이 아님")
        return out

    def playable(self) -> bool:
        return not self.problems()

    def to_json(self) -> dict:
        return {"path": self.path, "kind": self.kind, "name": self.name, "skeleton": self.skeleton, "frames": self.frames, "length": self.length,
                "tracks": self.tracks, "compressed": self.compressed, "bones": self.bones, "sequences": self.sequences, "reason": self.reason}

    @staticmethod
    def from_event(e: dict) -> "Rec":
        return Rec(path=e.get("path", ""), kind=e.get("kind", "unreadable"), name=e.get("name", "") or "", skeleton=e.get("skeleton", "") or "",
                   frames=int(e.get("frames", -1) if e.get("frames") is not None else -1), length=float(e.get("length", -1) if e.get("length") is not None else -1),
                   tracks=int(e.get("tracks", 0) or 0), compressed=bool(e.get("compressed", False)), bones=int(e.get("bones", 0) or 0),
                   sequences=list(e.get("sequences") or []), reason=e.get("reason", "") or "")

    @staticmethod
    def from_json(d: dict) -> "Rec":
        return Rec.from_event(d)


@dataclass
class Group:
    """캐릭터 하나 = 스켈레톤 하나."""
    skeleton: str                # 스켈레톤 키 (norm_key)
    skeleton_path: str = ""      # 사람이 볼 경로
    name: str = ""
    meshes: List[Rec] = field(default_factory=list)       # 리깅된 메쉬
    skeleton_assets: List[Rec] = field(default_factory=list)
    anims: List[Rec] = field(default_factory=list)        # 재생 가능한 것만
    rejected: List[Tuple[Rec, str]] = field(default_factory=list)  # (애니, 이유) — 재생 불가라 뺀 것
    statics: List[Rec] = field(default_factory=list)
    player: bool = False         # 플레이어블 캐릭터인가 (직접 표시했거나, 이름/경로로 추정)
    player_why: str = ""         # 그렇게 본 이유 (화면 툴팁에 보여 준다)
    player_manual: Optional[bool] = None   # 사람이 직접 정했으면 True/False, 자동 판정이면 None
    player_src: str = ""         # 근거: "manual"(직접 표시) / "data"(게임 데이터로 확인) / "name"(이름·폴더 추정) / ""(플레이어 아님)


# 플레이어 쪽 / 몬스터 쪽 이름 조각. 폴더·파일 이름을 '_ . - /' 와 숫자로 쪼갠 토막이 아래 낱말과 같을 때만 센다 (예: 'Player_Hero' 의 player, hero).
_PLAYER_WORDS = {"player", "players", "playable", "pc", "pcs", "hero", "heroes", "protagonist", "mainchar", "maincharacter",
                 "playerchar", "playercharacter", "playerpawn", "pcharacter"}
_MONSTER_WORDS = {"monster", "monsters", "enemy", "enemies", "boss", "bosses", "npc", "npcs", "creature", "creatures", "mob", "mobs",
                  "beast", "beasts", "minion", "minions", "foe", "foes", "animal", "animals", "elite", "miniboss"}


def _words(path: str) -> List[str]:
    base = re.sub(r"\.[A-Za-z0-9]+$", "", path or "")
    out: List[str] = []
    for tok in re.split(r"[/\\_.\-\s]+", base):
        for part in re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", tok).split("_"):      # 'PlayerKnight' -> player, knight
            part = re.sub(r"\d+$", "", part).lower()                                # 'Player01' -> 'player'
            if part:
                out.append(part)
    return out


def player_guess(g: Group) -> Tuple[bool, str]:
    """이름·폴더만 보고 플레이어블 캐릭터인지 추정한다 (확실하지 않으면 False). (결과, 이유).

    메쉬·스켈레톤 경로에 player/pc/hero/protagonist 같은 말이 있고 monster/enemy/boss/npc 같은 말이 없으면 플레이어블.
    메쉬가 없을 때는 애니메이션 경로의 절반 넘게가 그렇고 몬스터 말이 없으면 플레이어블로 본다.
    """
    own = [g.skeleton_path] + [r.path for r in g.meshes] + [r.path for r in g.skeleton_assets]
    own = [p for p in own if p]
    pl = sorted({w for p in own for w in _words(p) if w in _PLAYER_WORDS})
    mo = sorted({w for p in own for w in _words(p) if w in _MONSTER_WORDS})
    if pl and not mo:
        return True, "경로·이름에 '%s' 가 있음" % "', '".join(pl)
    if pl and mo:
        return False, "플레이어('%s')와 몬스터('%s') 말이 섞여 있어 자동으로는 정하지 않음" % ("', '".join(pl), "', '".join(mo))
    if mo:
        return False, "경로·이름에 '%s' 가 있음 (몬스터 쪽)" % "', '".join(mo)
    if g.anims:
        hit = sum(1 for r in g.anims if any(w in _PLAYER_WORDS for w in _words(r.path)))
        bad = sum(1 for r in g.anims if any(w in _MONSTER_WORDS for w in _words(r.path)))
        if hit * 2 > len(g.anims) and not bad:
            return True, "애니메이션 경로 %d/%d 개에 player 계열 이름이 있음" % (hit, len(g.anims))
    return False, ""


def _clean_name(s: str) -> str:
    s = re.sub(r"(?i)^(SK_?M?|SKEL|SKEL?ETON|SM|A|AS|AM|ABP)_", "", s or "")
    s = re.sub(r"(?i)_(skeleton|skel|mesh|rig|physicsasset)$", "", s)
    return s.strip("_ ")


def guess_group_name(g: Group) -> str:
    cands = [m.name or os.path.basename(m.path) for m in g.meshes] + [r.name or os.path.basename(r.path) for r in g.skeleton_assets]
    cands = [_clean_name(os.path.splitext(c)[0]) for c in cands if c]
    if cands:
        cands.sort(key=len)
        return cands[0] or os.path.basename(g.skeleton_path)
    base = os.path.basename(g.skeleton_path) or g.skeleton
    return _clean_name(os.path.splitext(base)[0]) or g.skeleton


def build_groups(records: Iterable[Rec], query: str = "", touch=None, marks: Optional[Dict[str, bool]] = None,
                 proofs: Optional[Dict[str, str]] = None) -> List[Group]:
    """records -> 스켈레톤별 그룹. query(쉼표 = 모두 포함, 대소문자 무시)가 있으면 그 말이 이름/경로에 든 그룹만.

    touch: 이번 검색에 걸린 에셋 경로 집합. 주면 '걸린 파일이 하나라도 든 그룹'도 보인다 (검색어가 애니메이션 이름에만 있어도 그 캐릭터가 나오게).
    marks: 스켈레톤 키 -> True/False. 사람이 직접 '플레이어블이다/아니다' 로 정한 것 (자동 추정보다 우선).
    proofs: 스켈레톤 키 -> 이유. 게임 데이터(기본 게임모드의 플레이어 폰 등)에서 플레이어블이라고 확인된 것. 이름 추정보다 우선.
    플레이어블 캐릭터는 항상 맨 위로 올린다 (직접 표시 / 데이터로 확인 → 이름으로 추정 순).
    """
    groups: Dict[str, Group] = {}
    by_key: Dict[str, Rec] = {}
    recs = list(records)
    for r in recs:
        by_key[r.key] = r
    for r in recs:
        if r.kind == "skeleton":
            g = groups.setdefault(r.key, Group(r.key, r.path))
            g.skeleton_assets.append(r)
    for r in recs:
        sk = r.skel_key
        if r.kind == "mesh":
            if not sk:
                continue
            g = groups.setdefault(sk, Group(sk, r.skeleton))
            if not g.skeleton_path:
                g.skeleton_path = r.skeleton
            g.meshes.append(r)
        elif r.kind in ("anim", "montage"):
            if not sk:
                continue
            g = groups.setdefault(sk, Group(sk, r.skeleton))
            if not g.skeleton_path:
                g.skeleton_path = r.skeleton
            probs = r.problems()
            if probs:
                g.rejected.append((r, ", ".join(probs)))
            else:
                g.anims.append(r)
        elif r.kind == "static":
            pass
    # 몽타주가 참조한 시퀀스가 이미 목록에 있으면 중복을 줄이기 위해 몽타주는 그대로 두되 표시만 구분한다 (여기서는 제거하지 않음)
    out = []
    terms = [t.strip().lower() for t in (query or "").split(",") if t.strip()]
    for g in groups.values():
        g.name = guess_group_name(g)
        g.meshes.sort(key=lambda m: m.path.lower())
        g.anims.sort(key=lambda m: m.path.lower())
        if terms:
            hay = " ".join([g.name, g.skeleton_path] + [m.path for m in g.meshes] + [m.path for m in g.skeleton_assets]).lower()  # 애니 경로는 보지 않는다(폴더가 달라도 데이터로 묶이므로)
            hit = all(t in hay for t in terms)
            if not hit and touch:
                members = [m.path for m in g.meshes] + [m.path for m in g.skeleton_assets] + [a.path for a in g.anims] + [r.path for r, _w in g.rejected]
                hit = any(m in touch for m in members)
            if not hit:
                continue
        if g.meshes or g.anims or g.skeleton_assets:
            out.append(g)
    marks = marks or {}
    proofs = proofs or {}
    for g in out:
        m = marks.get(g.skeleton)
        if m is not None:
            g.player, g.player_manual = bool(m), bool(m)
            g.player_src = "manual" if m else ""
            g.player_why = "직접 표시함" if m else "직접 '플레이어블 아님' 으로 정함"
        elif g.skeleton in proofs:
            g.player, g.player_manual, g.player_src, g.player_why = True, None, "data", proofs[g.skeleton]
        else:
            g.player, g.player_why = player_guess(g)
            g.player_manual, g.player_src = None, ("name" if g.player else "")
    rank = {"manual": 0, "data": 0, "name": 1, "": 2}
    out.sort(key=lambda g: (rank.get(g.player_src, 2), -(len(g.meshes) > 0), -len(g.anims), g.name.lower()))
    return out


def anims_for_mesh(records: Iterable[Rec], mesh_path: str) -> Tuple[List[Rec], Optional[Rec]]:
    """이 메쉬의 스켈레톤에 연결된 '재생 가능한' 애니메이션. (애니 목록, 찾은 메쉬 기록). 메쉬를 못 찾으면 ([], None)."""
    recs = list(records)
    mk = norm_key(mesh_path)
    mesh = next((r for r in recs if r.kind == "mesh" and r.key == mk), None)
    if mesh is None or not mesh.skel_key:
        return [], mesh
    sk = mesh.skel_key
    return [r for r in recs if r.kind in ("anim", "montage") and r.skel_key == sk and r.playable()], mesh


def summarize(groups: List[Group]) -> str:
    n_a = sum(len(g.anims) for g in groups)
    n_r = sum(len(g.rejected) for g in groups)
    n_p = sum(1 for g in groups if g.player)
    return "캐릭터 %d명%s · 재생 가능한 애니메이션 %d개%s" % (len(groups), (" (🎮 플레이어블 %d명)" % n_p) if n_p else "", n_a, (" (재생 불가라 뺀 것 %d개)" % n_r) if n_r else "")


def _pkg_key(path: str) -> str:
    """'/Game/A/BP_X.BP_X_C' 와 'Proj/Content/A/BP_X.uasset' 를 같은 패키지 키로."""
    return norm_key(path)


def player_proofs(pawns: List[dict], configs: List[dict], records: Iterable[Rec]) -> Dict[str, str]:
    """게임 데이터에서 플레이어가 조종하는 폰을 찾아, 그 폰이 쓰는 스켈레톤을 {스켈레톤 키: 이유} 로 돌려준다.

    근거(강한 것부터):
      1. 게임모드 블루프린트의 DefaultPawnClass 가 가리키는 폰 (게임 설정의 GlobalDefaultGameMode 가 있으면 그 게임모드의 것만)
         - 그 폰의 부모 클래스, 그 폰을 물려받은 자식 폰의 메시도 포함 (기본 폰의 변형까지)
      2. AutoPossessPlayer(플레이어 0 이 자동으로 조종) 로 설정된 폰
      3. 카메라/스프링암 컴포넌트가 달린 폰 (조종하는 캐릭터는 카메라가 붙는다)
    폰이 쓰는 스켈레탈 메시 -> 검사 기록(records) 의 메시가 연결된 스켈레톤으로 바꾼다. 검사 기록에 없는 메시는 건너뛴다.
    """
    by_key = {_pkg_key(p.get("path", "")): p for p in pawns if p.get("path")}
    mesh_skel: Dict[str, str] = {}
    for r in records:
        if r.kind == "mesh" and r.skel_key:
            mesh_skel[r.key] = r.skel_key

    def chain_up(node: dict) -> List[dict]:
        out, seen, cur = [], set(), node
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            out.append(cur)
            cur = by_key.get(_pkg_key(cur.get("parent", ""))) if cur.get("parent") else None
        return out

    def family(node: dict) -> List[dict]:
        """이 폰 + 부모들 + 이 폰을 물려받은 자식들."""
        fam = {id(n): n for n in chain_up(node)}
        for other in pawns:
            if any(a is node or _pkg_key(a.get("path", "")) == _pkg_key(node.get("path", "")) for a in chain_up(other)):
                fam[id(other)] = other
        return list(fam.values())

    gm_keys = set()
    for c in configs:
        if str(c.get("key", "")).lower() == "globaldefaultgamemode" and c.get("value"):
            gm_keys.add(_pkg_key(c["value"]))

    out: Dict[str, str] = {}

    def add(nodes: List[dict], why: str, gm: str = "") -> None:
        for n in nodes:
            for m in n.get("meshes", []):
                sk = mesh_skel.get(norm_key(m))
                if sk and sk not in out:
                    out[sk] = why.format(pawn=os.path.basename((n.get("path", "") or "").split(".")[0]), gm=gm)

    # 1) 기본 폰
    gms = [n for n in pawns if n.get("default_pawn")]
    if gm_keys:
        gms_cfg = [n for n in gms if _pkg_key(n.get("path", "")) in gm_keys or any(_pkg_key(a.get("path", "")) in gm_keys for a in chain_up(n))]
        gms = gms_cfg or gms
    for gm in gms:
        node = by_key.get(_pkg_key(gm["default_pawn"]))
        if node is not None:
            add(family(node), "게임 데이터 확인: 게임모드({gm})의 기본 플레이어 폰 {pawn} 이(가) 쓰는 메시", os.path.basename(gm.get("path", "").split(".")[0]))
    # 2) 자동 빙의
    add([n for n in pawns if "player" in str(n.get("auto_possess", "")).lower()], "게임 데이터 확인: AutoPossessPlayer 로 플레이어가 조종하는 폰 {pawn} 이(가) 쓰는 메시")
    # 3) 카메라
    add([n for n in pawns if n.get("camera") and n.get("meshes")], "게임 데이터 확인: 카메라가 달린 폰 {pawn} 이(가) 쓰는 메시 (조종하는 캐릭터로 추정)")
    return out


# ---------------------------------------------------------------- 캐시
class SkelIndex:
    """검사한 기록을 모아 두는 곳 (게임 폴더마다 하나). 한 번 검사한 파일은 다시 읽지 않는다."""

    def __init__(self, path: str = "") -> None:
        self.path = path
        self.recs: Dict[str, List[Rec]] = {}   # 에셋 경로 -> 기록들 (한 파일에 여러 개체가 있을 수 있음)
        self.unreadable: Dict[str, str] = {}
        self.seen: set = set()                 # 검사를 마친 에셋 경로 (알아볼 개체가 없던 파일 포함)
        self.rechecked: set = set()            # 스켈레톤 연결이 비어 있어서 지금 도우미로 다시 읽어 본 파일 (한 번만 다시 읽는다)
        self.marks: Dict[str, bool] = {}       # 스켈레톤 키 -> 사람이 직접 정한 '플레이어블 여부' (검사 기록을 지워도 남긴다)
        self.proofs: Dict[str, str] = {}       # 스켈레톤 키 -> 게임 데이터로 플레이어블임을 확인한 이유 (검사 기록을 지우면 같이 지움)

    def add(self, r: Rec) -> None:
        if r.kind == "unreadable":
            self.unreadable[r.path] = r.reason
            return
        self.recs.setdefault(r.path, []).append(r)

    def done(self, path: str) -> bool:
        return path in self.seen or path in self.recs or path in self.unreadable

    def all(self) -> List[Rec]:
        return [r for v in self.recs.values() for r in v]

    def stale(self, path: str) -> bool:
        """예전 도우미가 스켈레톤 연결을 못 읽고 남긴 기록인가 (연결이 비어 있는 애니/메쉬/몽타주, 시퀀스가 전부 빈 몽타주)."""
        if path in self.rechecked:
            return False
        for r in self.recs.get(path, ()):
            if r.kind in ("anim", "mesh", "montage") and not r.skel_key:
                return True
            if r.kind == "montage" and not any(r.sequences):
                return True
        return False

    def pending(self, paths: Iterable[str]) -> List[str]:
        """아직 안 읽은 파일 + 연결을 못 읽었던 옛 기록(한 번만 다시 읽음)."""
        return [p for p in paths if not self.done(p) or self.stale(p)]

    def begin(self, paths: Iterable[str]) -> None:
        """이 파일들을 다시 읽기 전에 옛 기록을 지운다 (같은 파일이 두 번 들어가 개수가 늘지 않게)."""
        for p in paths:
            if p in self.recs:
                self.rechecked.add(p)
                del self.recs[p]
            self.unreadable.pop(p, None)

    def save(self) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"v": 1, "recs": [r.to_json() for r in self.all()], "unreadable": self.unreadable, "seen": sorted(self.seen),
                       "rechecked": sorted(self.rechecked), "marks": self.marks, "proofs": self.proofs}, f, ensure_ascii=False)

    @staticmethod
    def load(path: str) -> "SkelIndex":
        idx = SkelIndex(path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            for x in d.get("recs", []):
                idx.add(Rec.from_json(x))
            idx.unreadable.update(d.get("unreadable", {}))
            idx.seen.update(d.get("seen", []))
            idx.rechecked.update(d.get("rechecked", []))
            idx.marks.update({str(k): bool(v) for k, v in (d.get("marks") or {}).items()})
            idx.proofs.update({str(k): str(v) for k, v in (d.get("proofs") or {}).items()})
        except (OSError, ValueError):
            pass
        return idx


def refine_by_skeleton(records: Iterable[Rec], mesh_path: str, cands: list, make, limit: int = 400):
    """이름으로 뽑은 애니메이션 후보를, 파일 안 데이터로 걸러서 다시 정한다.

    - 이 메쉬의 스켈레톤에 연결되고 재생 가능한 애니메이션 -> 맨 앞 ('스켈레톤 확인 ✔'). 이름 점수가 있으면 그 점수로 정렬.
    - 이미 검사했는데 다른 스켈레톤이거나 재생 불가 -> 후보에서 뺀다.
    - 아직 검사 안 한 후보 -> 뒤에 '미확인' 으로 남긴다.
    make(path, score, why) 로 후보 객체를 만든다. 돌려주는 값: (새 후보 목록, 상태, 뺀 개수).
    상태: 'linked' (데이터로 확인함) / 'mesh_unknown' (이 메쉬를 검사한 적 없음) / 'no_index' (검사 기록 없음).
    """
    recs = list(records)
    if not recs:
        return cands, "no_index", 0
    linked, mesh = anims_for_mesh(recs, mesh_path)
    if mesh is None:
        return cands, "mesh_unknown", 0
    inspected = {r.key: r for r in recs if r.kind in ("anim", "montage")}
    inspected_keys = set(inspected) | {norm_key(p) for p in ()}
    name_score = {norm_key(c.path): c for c in cands}
    out = []
    seen = set()
    for r in linked:
        k = r.key
        c = name_score.get(k)
        out.append(make(r.path, c.score if c is not None else 0.0, "스켈레톤 연결 확인 ✔" + ((" · " + c.why) if c is not None and c.why else "")))
        seen.add(k)
    out.sort(key=lambda c: (-c.score, os.path.basename(c.path).lower()))
    out = out[:limit]
    dropped = 0
    for c in cands:
        k = norm_key(c.path)
        if k in seen:
            continue
        if k in inspected_keys:
            dropped += 1  # 검사했더니 이 스켈레톤이 아니거나 재생 불가
            continue
        out.append(make(c.path, c.score, ((c.why + " · ") if c.why else "") + "미확인(스켈레톤 검사 전)"))
    return out, "linked", dropped
