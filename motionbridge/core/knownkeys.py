"""게임별 공개 AES 키 목록 (배포용 시드 + 로컬/팀 공유 파일).

커뮤니티에 공개된 키를 시드로 담아 두고, 게임 폴더를 검사할 때 이름으로 매칭해 자동으로 넣어 본다.
키는 '실제로 게임이 열리는지' 검증(pick_working_key)을 통과해야만 쓰므로 틀린 항목이 있어도 무해하다.

시드 말고도 두 파일에서 추가로 읽는다 (코드 수정 없이 키를 더하는 길 — 만약 공개 저장소에 키를 둔 것이
문제가 되면(테이크다운 통지 등) KNOWN 의 keys 만 비우고 이 파일 배포로 전환하면 된다):
  ① 내 컴퓨터:  ~/.motionbridge/known_keys.json
  ② 팀 공유 폴더(설정의 preset_share_dir)/known_keys.json

파일 형식 (게임 표시 이름은 자유, match 는 소문자·특수문자 제거 기준으로 부분 일치):
{
  "Some Game": {"match": ["somegame", "sg_project"], "keys": ["0x...64자리..."]}
}

(개별 게임을 한 번이라도 연 키는 gamepresets 가 따로 기억·공유한다 — 이 목록은 '아직 안 열어 본 게임'용 시드다.)
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List

from . import bridge

FILE_NAME = "known_keys.json"
KNOWN: Dict[str, dict] = {
    "Black Myth: Wukong": {
        "match": ["blackmythwukong", "b1wukong", "wukong", "검은신화오공"],
        "keys": ["0xA896068444F496956900542A215367688B49B19C2537FCD2743D8585BA1EB128"],
    },
    "BattleCore Arena": {   # FModel/Unreal-Game-Keys 공개 저장소
        "match": ["battlecorearena"],
        "keys": ["0x8A75AFDF1C74AB55B79DC1DD4ABE4B01360A059D77F243EF4EFADA41A59D71A0"],
    },
    "Tower of Fantasy": {   # Gildor 포럼 공개 글 (초기 버전 기준 — 업데이트로 바뀌었으면 검증에서 걸러짐)
        "match": ["toweroffantasy", "hotta"],
        "keys": ["0xA16F419DD3E55493653AC5CDBA71963296E46792E54900C05A1BA5B50C3BC2B0"],
    },
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9가-힣]", "", (s or "").lower())


def _load(path: str) -> Dict[str, dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return {str(k): v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _tables(extra_dirs=()) -> List[Dict[str, dict]]:
    out = [KNOWN]
    out.append(_load(os.path.join(bridge.CONFIG_DIR, FILE_NAME)))
    for d in extra_dirs or ():
        if d:
            out.append(_load(os.path.join(d, FILE_NAME)))
    return out


def keys_for(*names: str, extra_dirs=()) -> List[str]:
    """게임 이름/설치 폴더/프로젝트 이름 아무거나 주면, 매칭되는 키 후보들 (없으면 빈 목록)."""
    hay = [_norm(n) for n in names if n and _norm(n)]
    out: List[str] = []
    for table in _tables(extra_dirs):
        for info in table.values():
            toks = [_norm(t) for t in (info.get("match") or [])]
            if any(t and (t in h or h in t) for t in toks for h in hay if len(h) >= 3 or len(t) >= 3):
                for k in info.get("keys") or []:
                    if isinstance(k, str) and k and k not in out:
                        out.append(k)
    return out
