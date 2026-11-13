"""에셋 신호만으로 캐릭터를 분류한다: 보스 / 일반 몬스터 / 플레이어블 / NPC.

웹·AI 없이 파일에서 바로 읽는 신호:
  · 플레이어블: 경로/이름에 player·pc·hero 표시 (assetidx.is_player_path)
  · NPC: NPC 폴더 아래 또는 이름에 npc
  · 보스: 전용 애니메이션이 많다(수백 개) + 전용 연출(Die_Seq·JieZhan 처형·컷신) — 잡몹은 수십 개뿐
  · 나머지: 일반 몬스터
실측(오공): 보스급 — 미친 호랑이 209 · 호선봉 255 · 황미 495 / 잡몹 — 늑대 변형 30~80.
"""
from __future__ import annotations

import re
from typing import Dict, Sequence

from . import assetidx
from .identify import namefind
from .identify.entity_store import norm as nname

KINDS = ("boss", "mob", "player", "npc")
LABEL = {"boss": "보스", "mob": "일반 몬스터", "player": "플레이어블", "npc": "NPC"}
_NPC = re.compile(r"(?i)/npcs?/|(^|_)npc(_|$)")


def folder_of(path: str) -> str:
    """메시 경로 -> 캐릭터 폴더 이름 (namefind.folder_stats 와 같은 규칙)."""
    parts = [x for x in (path or "").replace("\\", "/").split("/") if x]
    if len(parts) < 2:
        return parts[-1] if parts else ""
    folder = parts[-2]
    if folder.lower() in namefind._STRUCT_DIRS and len(parts) >= 3:
        folder = parts[-3]
    return folder


def journal_types(entries: Sequence[Sequence[str]]) -> Dict[str, str]:
    """게임 도감(locres)의 '공식 분류'를 읽는다: norm(표시 이름) -> 종류.

    오공 실측: 키가 CardDesc.<장><분류 2자리><번호>.UnitName 꼴이고
    분류 01=잡요괴(mob), 02=두목(보스로 취급), 03=요왕(boss), 04=인물(npc).
    (Mad Tiger=203003→요왕, Wolf Soldier=101011→잡요괴 로 검증.) 다른 게임엔 없으면 빈 사전."""
    out: Dict[str, str] = {}
    for e in entries or ():
        if len(e) < 4:
            continue
        m = re.match(r"CardDesc\.(\d{4,7})\.UnitName$", str(e[2] or ""))
        if not m:
            continue
        kind = {1: "mob", 2: "boss", 3: "boss", 4: "npc"}.get((int(m.group(1)) // 1000) % 100)
        nm = nname(str(e[3] or ""))
        if kind and nm:
            out.setdefault(nm, kind)
    return out


def classify(mesh_paths: Sequence[str], anim_paths: Sequence[str] = ()) -> Dict[str, str]:
    """캐릭터 폴더 -> 종류(boss|mob|player|npc|unknown). 경로 표시(플레이어·NPC)만 스스로 판정한다.

    보스/일반 몬스터 구분은 '게임이 데이터로 적어 둔 것'(도감 공식 분류, 뜻 풀이 사전)이 덮어쓸 때만 붙는다.
    애니 수로 추정하던 방식은 뺐다 — 오공 실측에서 잡몹도 애니 100~300개라 늑대가 보스로 숨는 오분류가 났다.
    'unknown' 은 어떤 종류 체크에도 걸러지지 않는다 (틀리게 숨기는 것보다 보여 주는 게 낫다)."""
    stats = namefind.folder_stats(mesh_paths, anim_paths)
    out: Dict[str, str] = {}
    for folder, s in stats.items():
        paths = s.get("meshes") or []
        joined = " ".join(paths).lower()
        if any(assetidx.is_player_path(p) for p in paths[:6]):
            out[folder] = "player"
        elif _NPC.search(joined) or _NPC.search("_" + folder.lower() + "_"):
            out[folder] = "npc"
        else:
            out[folder] = "unknown"
    return out


def passes(kind, want) -> bool:
    """종류 체크 필터: 모르는(unknown/빈) 종류는 항상 통과 — 확실할 때만 거른다."""
    return not want or kind in (None, "", "unknown") or kind in want
