"""공개적으로 알려진 게임별 pak AES 키 내장 목록.

커뮤니티(FModel Unreal-Game-Keys 저장소, 모딩 포럼)에 공개된 키를 담아 두고,
게임 폴더를 검사할 때 이름으로 매칭해 자동으로 넣어 본다. 키는 '실제로 게임이 열리는지'
검증(pick_working_key)을 통과해야만 쓰이므로, 목록이 틀리거나 게임이 업데이트로 키를 바꿔도 해가 없다.

새 게임 키를 알게 되면 여기 한 줄 추가해 두면 다음 릴리스에서 모든 사용자에게 자동 매칭된다.
(개별 사용자의 키 기억·팀 공유는 gamepresets 가 따로 한다 — 이 파일은 '배포용 시드'다.)
"""
from __future__ import annotations

import re
from typing import List

# 표시 이름: {"match": [게임 이름/폴더/프로젝트를 알아볼 낱말(소문자, 특수문자 제거)], "keys": [후보 키들]}
KNOWN = {
    "Black Myth: Wukong": {
        "match": ["blackmythwukong", "b1wukong", "wukong", "검은신화오공"],
        "keys": ["0xA896068444F496956900542A215367688B49B19C2537FCD2743D8585BA1EB128"],
    },
    "BattleCore Arena": {   # FModel/Unreal-Game-Keys 공개 저장소
        "match": ["battlecorearena"],
        "keys": ["0x8A75AFDF1C74AB55B79DC1DD4ABE4B01360A059D77F243EF4EFADA41A59D71A0"],
    },
    "Tower of Fantasy": {   # Gildor 포럼 공개 글 (UE4.26, 2020 초기 버전 기준 — 업데이트로 바뀌었으면 검증에서 걸러짐)
        "match": ["toweroffantasy", "hotta"],
        "keys": ["0xA16F419DD3E55493653AC5CDBA71963296E46792E54900C05A1BA5B50C3BC2B0"],
    },
}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9가-힣]", "", (s or "").lower())


def keys_for(*names: str) -> List[str]:
    """게임 이름/설치 폴더/프로젝트 이름 아무거나 주면, 매칭되는 내장 키 후보들 (없으면 빈 목록)."""
    hay = [_norm(n) for n in names if n and _norm(n)]
    out: List[str] = []
    for info in KNOWN.values():
        toks = [_norm(t) for t in info.get("match", [])]
        if any(t and (t in h or h in t) for t in toks for h in hay if len(h) >= 3 or len(t) >= 3):
            for k in info.get("keys", []):
                if k not in out:
                    out.append(k)
    return out
