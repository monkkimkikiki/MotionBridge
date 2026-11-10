"""추출 결과를 한눈에 찾기 쉬운 폴더로 정리한다.

    출력 폴더 / 게임 이름 / 캐릭터 이름 / Animations 또는 Models / 파일

게임 안의 깊은 경로(Game/Content/Characters/…)는 따라가지 않는다. Qt 에 의존하지 않는다.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

ANIM_DIR = "Animations"
MODEL_DIR = "Models"
MISC = "기타"

# 모션 파일은 클립 하나당 폴더 하나에 프로그램(3ds Max / Blender / Maya) 구분 없이 함께 둔다:  출력 폴더 / 캐릭터 / Animations / <클립 이름> /
# 파일 이름 끝(_max · _blender · _maya)과 확장자로 어느 프로그램용인지 알 수 있고, 폴더 안의 '사용법.txt' 가 풀어서 설명한다.
GUIDE_NAME = "사용법.txt"
GUIDE_HEAD = "이 폴더의 파일 — 무엇을 어디서 여는지\n"
GUIDES = {
    "max": ("3ds Max 용 모션\n"
            "- 이름_retarget.max : 스키닝된 .max 에 모션을 자동으로 넣어 저장한 결과 (3ds Max 에서 그냥 열면 됩니다. 원본 .max 폴더에 저장됩니다)\n"
            "- 이름_retarget.ms : 3ds Max 의 Scripting > Run Script 로 실행하면 같은 일을 합니다 (자동 적용이 꺼졌거나 실패했을 때)\n"
            "- 이름_retarget.mbbip / .mbbones : .ms 가 읽는 모션 데이터 — .ms 와 같은 폴더에 두세요\n"
            "- 이름_retarget_max.log : 자동 적용 결과 기록 (문제가 있을 때 확인)\n"),
    "blender": ("Blender 용 모션\n"
                "- 이름_blender.fbx : File > Import > FBX 로 불러옵니다 (타깃 모델에 모션이 구워진 파일)\n"
                "- 이름_blender.bvh : File > Import > Motion Capture (.bvh) — 본 구조만 있는 모션\n"),
    "maya": ("Maya 용 모션\n"
             "- 이름_maya.fbx : File > Import 로 불러옵니다 (타깃 모델에 모션이 구워진 파일)\n"
             "- 이름_maya.bvh : File > Import 에서 BVH 를 고릅니다 — 본 구조만 있는 모션\n"),
    "etc": ("기타\n"
            "- .glb : 대부분의 뷰어/Blender 에서 열 수 있는 미리보기용\n"
            "- _curves.json : 키 곡선 데이터 (본 구조를 내보내지 않을 때)\n"),
}


def clip_dir(anim_root: str, clip: str) -> str:
    """모션 폴더(…/Animations) 아래의 클립 폴더."""
    return os.path.join(anim_root, safe_name(clip))


def guide_key(path: str) -> Optional[str]:
    """파일이 어느 프로그램용인지 (max | blender | maya | etc), 모르면 None (사용법 파일 자신 포함)."""
    n = os.path.basename(path).lower()
    if n == GUIDE_NAME.lower():
        return None
    stem, ext = os.path.splitext(n)
    if ext in (".ms", ".max", ".mbbip", ".mbbones") or stem.endswith("_retarget_max"):
        return "max"
    if stem.endswith("_blender"):
        return "blender"
    if stem.endswith("_maya"):
        return "maya"
    if ext == ".glb":
        return "etc"
    return None


def write_guide(folder: str, key: str) -> None:
    """클립 폴더의 '사용법.txt' 에 그 프로그램 설명을 (아직 없으면) 덧붙인다. 여러 프로그램이 한 폴더에 있어도 한 파일에 차례로 쌓인다."""
    if key not in GUIDES:
        return
    p = os.path.join(folder, GUIDE_NAME)
    try:
        old = ""
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                old = f.read()
        marker = GUIDES[key].split("\n", 1)[0]
        if marker in old:
            return
        with open(p, "w", encoding="utf-8") as f:
            f.write((old.rstrip("\n") + "\n\n" if old else GUIDE_HEAD + "\n") + GUIDES[key])
    except OSError:
        pass


def remove_empty_fbm(*roots: str) -> List[str]:
    """FBX 내보내기가 남기는 비어 있는 '<이름>.fbm' 폴더를 지운다 (텍스처가 있으면 건드리지 않음). 지운 폴더 목록을 돌려준다."""
    gone: List[str] = []
    for root in dict.fromkeys(r for r in roots if r):
        for cur, dirs, _files in os.walk(root, topdown=False):
            for d in dirs:
                if d.lower().endswith(".fbm"):
                    p = os.path.join(cur, d)
                    try:
                        if not os.listdir(p):
                            os.rmdir(p)
                            gone.append(p)
                    except OSError:
                        pass
    return gone

# 이 폴더 바로 아래가 캐릭터 폴더인 경우가 많다
MARKERS = {"characters", "character", "enemies", "enemy", "monsters", "monster", "bosses", "boss", "npcs", "npc",
           "creatures", "creature", "mobs", "mob", "pawns", "units", "humanoids"}
# 캐릭터 폴더 '안쪽'의 하위 폴더 이름 (여기부터는 캐릭터 이름이 아니다)
SUBFOLDERS = {"meshes", "mesh", "animations", "animation", "anims", "anim", "montages", "montage", "materials", "textures",
              "skeleton", "skeletons", "rig", "rigs", "physics", "locomotion", "combat", "attacks", "attack", "common",
              "shared", "blueprints", "bp", "vfx", "sounds", "audio", "models", "model", "skeletalmeshes", "lods",
              "art", "asset", "assets", "resources", "gameplay", "ai", "fx", "effects", "sfx", "weapons", "weapon", "equipment",
              "props", "data", "sequences", "clips", "poses", "ragdoll", "cloth", "hair", "rigging", "animbp", "animblueprints"}
PROJECT_DIRS = {"game", "content", "engine", "plugins"}
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_PREFIX = re.compile(r"^(sk[a-z]{0,3}|sm|am|as|a|abp|bp)_", re.I)


def safe_name(s: str, default: str = MISC) -> str:
    s = _BAD.sub("_", (s or "").strip()).strip(" .")
    return s[:60] or default


def detect_game_name(root: str) -> str:
    """스팀 게임이면 appmanifest 의 진짜 이름(Mortal Shell 2), 아니면 폴더 이름."""
    root = os.path.normpath(root or "")
    base = os.path.basename(root)
    common = os.path.dirname(root)
    if os.path.basename(common).lower() == "common":
        apps = os.path.dirname(common)
        try:
            for fn in sorted(os.listdir(apps)):
                if not (fn.startswith("appmanifest_") and fn.endswith(".acf")):
                    continue
                with open(os.path.join(apps, fn), "r", encoding="utf-8", errors="replace") as f:
                    txt = f.read()
                inst = re.search(r'"installdir"\s+"([^"]*)"', txt)
                name = re.search(r'"name"\s+"([^"]*)"', txt)
                if inst and name and inst.group(1).lower() == base.lower() and name.group(1).strip():
                    return safe_name(name.group(1))
        except OSError:
            pass
    return safe_name(base, "Game")


def _pretty(name: str) -> str:
    return safe_name(name)


def character_of(asset_path: str) -> str:
    """에셋 경로에서 캐릭터 이름을 짐작한다. 못 찾으면 파일 이름에서, 그래도 없으면 '기타'."""
    parts = [p for p in re.split(r"[/\\]+", asset_path or "") if p]
    if not parts:
        return MISC
    stem = os.path.splitext(parts[-1])[0]
    dirs = parts[:-1]
    low = [d.lower() for d in dirs]
    # 마지막 표식 폴더(Characters/Enemies/…) 뒤에서, 하위 폴더 이름이 나오기 전까지가 캐릭터 폴더
    start = -1
    for i, d in enumerate(low):
        if d in MARKERS:
            start = i
    if start >= 0:
        names: List[str] = []
        for d, dl in zip(dirs[start + 1:], low[start + 1:]):
            if dl in SUBFOLDERS or dl in MARKERS:
                break
            names.append(d)
        if names:
            return _pretty(names[-1])
    # 파일 이름에서: SK_/A_/AM_ 접두사를 뗀 첫 덩어리
    base = _PREFIX.sub("", stem)
    first = re.split(r"[_\-. ]+", base)[0] if base else ""
    if first and not first.isdigit() and first.lower() not in SUBFOLDERS:
        return _pretty(first)
    return MISC


@dataclass
class Layout:
    out: str
    game: str
    force_char: Optional[str] = None         # 정해 주면 모든 에셋을 이 캐릭터 폴더로
    chars: Dict[str, str] = field(default_factory=dict)   # 에셋 경로 -> 캐릭터 (직접 지정)
    on_exist: str = "overwrite"              # 같은 이름의 파일이 이미 있을 때: overwrite | keep_old | new_name (core.safesave)
    backups: List[str] = field(default_factory=list)      # 이번에 보관해 둔 이전 파일들 (keep_old)

    def char(self, asset_path: str) -> str:
        return safe_name(self.chars.get(asset_path) or self.force_char or character_of(asset_path))

    def base(self, asset_path: str) -> str:
        return os.path.join(self.out, safe_name(self.game, "Game"), self.char(asset_path))

    def anim_dest(self, asset_path: str) -> str:
        return os.path.join(self.base(asset_path), ANIM_DIR)

    def model_dest(self, asset_path: str) -> str:
        return os.path.join(self.base(asset_path), MODEL_DIR)

    def dest(self, kind: str, asset_path: str) -> str:
        return self.model_dest(asset_path) if kind == "mesh" else self.anim_dest(asset_path)


def unique_name(dest_dir: str, name: str, taken: set) -> str:
    """같은 배치 안에서 이름이 겹치면 _2, _3 … 을 붙인다 (이미 디스크에 있는 같은 에셋 결과는 덮어쓴다)."""
    stem, ext = os.path.splitext(name)
    cand, n = name, 1
    while os.path.join(dest_dir, cand).lower() in taken:
        n += 1
        cand = "%s_%d%s" % (stem, n, ext)
    taken.add(os.path.join(dest_dir, cand).lower())
    return cand
