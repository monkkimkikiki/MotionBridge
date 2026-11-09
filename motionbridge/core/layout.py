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
