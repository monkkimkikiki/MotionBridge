"""추출/변환 결과 파일이 '무엇인지' 한눈에 알 수 있게 이름표를 붙인다 (Qt 없이).

처음 보는 사람도 목록에서 모델링(메시) / 애니메이션 / 기타를 구분하고, 모델링은 리깅(스켈레톤+스킨) 유무까지 보이게 한다.
"""
from __future__ import annotations

import os
from typing import Tuple

# (이름표, 분류, 설명)  분류: model | anim | other
_RIG = "[모델링 · 리깅 포함]"
_STATIC = "[모델링 · 리깅 없음]"


def describe(path: str) -> Tuple[str, str, str]:
    """(이름표, 분류, 툴팁 설명)"""
    base, ext = os.path.splitext(os.path.basename(path))
    ext, low = ext.lower(), base.lower()
    if ext == ".psa":
        return "[애니메이션]", "anim", "애니메이션(모션) 데이터 — 소스 모션으로 열 수 있습니다"
    if ext in (".psk", ".pskx"):
        return "[모델링 원본]", "model", "게임에서 뽑은 모델링 원본(ActorX). 스켈레톤+스킨 포함. FBX 등으로 변환해서 쓰세요"
    if ext in (".fbx", ".glb", ".gltf", ".obj", ".blend", ".dae"):
        if low.endswith("_rigged"):
            return _RIG, "model", "모델링(메시) + 스켈레톤 + 스킨 가중치. 애니메이션을 입힐 수 있는 판"
        if low.endswith("_static"):
            return _STATIC, "model", "모델링(메시)만. 스켈레톤/스킨 없음 — 배경/소품/모델링 참고용"
        if ext == ".obj":
            return _STATIC, "model", "모델링(메시)만. OBJ 는 스켈레톤을 담을 수 없습니다"
        if "_" in base and any(base.endswith(s) for s in ("_blender", "_max", "_maya")):
            return "[애니메이션 FBX]", "anim", "내보낸 애니메이션(모션) FBX"
        return "[3D 파일]", "other", "3D 파일 (모델링 또는 애니메이션)"
    if ext in (".bvh",):
        return "[애니메이션 BVH]", "anim", "모션 캡처 형식 애니메이션"
    if ext == ".ms":
        return "[Max 스크립트]", "other", "3ds Max 에서 실행(파일 → Run Script)하는 Biped 모션 스크립트"
    if ext == ".mbbip":
        return "[Biped 데이터]", "other", "Biped 스크립트가 읽는 모션 데이터"
    if ext in (".png", ".jpg", ".jpeg", ".tga", ".dds"):
        return "[텍스처]", "other", "텍스처 이미지"
    return "[기타]", "other", ext or "알 수 없는 파일"


# 폴더 이름 -> 설명 (마지막 폴더 이름이 이것이면 뒤에 덧붙여 보여준다)
FOLDER_HINTS = {
    "models": "모델링 데이터",
    "animations": "애니메이션 데이터",
    "_raw": "원본(PSKX/GLB)",
}


def folder_hint(name: str) -> str:
    return FOLDER_HINTS.get(name.split("/")[-1].lower(), "")
