"""확장자에 따라 알맞은 읽기 함수를 고른다. FBX 등은 Blender 로 GLB 변환 후 읽는다."""
from __future__ import annotations

import os
from typing import Optional

from . import bridge
from .io_bvh import read_bvh
from .io_gltf import read_gltf
from .io_psa import read_psa
from .model import Model

SUPPORTED = (".bvh", ".glb", ".gltf", ".psa", ".fbx", ".blend", ".dae")
FILE_FILTER = "모션/모델 (*.fbx *.glb *.gltf *.bvh *.psa *.blend *.dae);;모든 파일 (*)"


def load_model(path: str, blender: Optional[str] = None, fps: Optional[float] = None) -> Model:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".bvh":
        m = read_bvh(path)
    elif ext in (".glb", ".gltf"):
        m = read_gltf(path, fps=fps)
    elif ext == ".psa":
        m = read_psa(path)
    elif ext in bridge.NEEDS_BLENDER:
        glb, r = bridge.convert_to_glb(path, blender=blender)
        m = read_gltf(glb, fps=fps)
        m.path = path
        m.notes.append("Blender 로 변환해서 읽음: " + ", ".join(r.result.get("actions", [])) if r.result.get("actions") else "Blender 로 변환해서 읽음")
        m.notes += r.warnings
    else:
        raise ValueError("지원하지 않는 형식입니다: %s (지원: %s)" % (ext, ", ".join(SUPPORTED)))
    return m
