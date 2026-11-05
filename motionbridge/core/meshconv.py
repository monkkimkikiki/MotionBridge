"""추출한 메시(.psk/.pskx/.glb)를 바로 쓸 수 있는 형식(FBX / OBJ / GLB)으로 바꾼다.

  리깅 포함 판(rigged): 스켈레톤 + 스킨 가중치가 들어 있다.  리깅 없는 판(static): 메시만.
  PSK/PSKX 는 여기서 직접 읽어(`psk.read_psk`) Blender 에 넘기고, GLB 는 Blender 가 읽는다.
  OBJ(리깅 없음) 는 PSK 라면 Blender 없이도 만들 수 있다.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from typing import Callable, List, Optional, Sequence

from . import bridge, psk
from .export import PRESETS

FORMATS = ("fbx", "obj", "glb")
VARIANTS = ("rigged", "static")
PSK_EXTS = (".psk", ".pskx")
MESH_EXTS = PSK_EXTS + (".glb", ".gltf")


class MeshConvError(Exception):
    pass


def is_mesh_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in MESH_EXTS


def convert_mesh(src: str, out_dir: str, formats: Sequence[str] = ("fbx",), variants: Sequence[str] = ("rigged", "static"),
                 preset: str = "max", blender: Optional[str] = None, on_log: Optional[Callable[[str], None]] = None) -> List[str]:
    """src 하나를 변환해 만든 파일 목록을 돌려준다. 경고(스켈레톤 없음 등)는 on_log 로 알린다."""
    formats = [f for f in formats if f in FORMATS]
    variants = [v for v in variants if v in VARIANTS]
    if not formats:
        raise MeshConvError("내보낼 형식을 하나 이상 고르세요 (FBX / OBJ / GLB).")
    if not variants:
        raise MeshConvError("리깅 포함 / 리깅 없음 중 하나 이상을 고르세요.")
    if not os.path.isfile(src):
        raise MeshConvError("파일이 없습니다: " + src)
    if formats == ["obj"] and "static" not in variants:
        raise MeshConvError("OBJ 는 리깅을 담을 수 없습니다. '리깅 없음'을 고르거나 FBX/GLB 도 함께 고르세요.")
    log = on_log or (lambda m: None)
    ext = os.path.splitext(src)[1].lower()
    base = os.path.splitext(os.path.basename(src))[0]
    if preset not in PRESETS:
        raise MeshConvError("알 수 없는 대상 프로그램: " + preset)
    p = PRESETS[preset]
    os.makedirs(out_dir, exist_ok=True)
    made: List[str] = []

    mesh = None
    if ext in PSK_EXTS:
        try:
            mesh = psk.read_psk(src)
        except psk.PskError as e:
            raise MeshConvError("%s: %s" % (os.path.basename(src), e))
        # Blender 없이 가능한 부분: 리깅 없는 OBJ
        if "static" in variants and "obj" in formats and (not bridge.find_blender(blender) or formats == ["obj"]):
            made.append(psk.write_obj(mesh, os.path.join(out_dir, base + "_static.obj")))
            formats = [f for f in formats if f != "obj"]
            if not formats:
                if "rigged" in variants and len(variants) == 1:
                    log("OBJ 는 리깅을 담을 수 없어 '리깅 없음' 판만 만들었습니다")
                return made
        if not mesh.has_skeleton and "rigged" in variants:
            log("%s: 스켈레톤/스킨 정보가 없어 '리깅 포함' 판은 만들 수 없습니다" % os.path.basename(src))
            variants = [v for v in variants if v != "rigged"]
            if not variants:
                variants = ["static"]
    elif ext not in (".glb", ".gltf"):
        raise MeshConvError("지원하지 않는 메시 형식: " + ext)

    if not formats:
        return made
    if not bridge.find_blender(blender):
        raise MeshConvError("FBX/GLB 변환에는 Blender 가 필요합니다. '파일 → Blender 경로 지정…' 으로 지정하세요. "
                            "(리깅 없는 OBJ 는 PSK/PSKX 라면 Blender 없이 만들어집니다)")

    tmp = tempfile.mkdtemp(prefix="mb_mesh_")
    try:
        inp = src
        if mesh is not None:
            inp = os.path.join(tmp, base + ".npz")
            psk.save_payload(inp, psk.to_blender(mesh, keep_bones="rigged" in variants))
        import json
        opts = {"formats": formats, "variants": variants, "axis_forward": p.fbx_forward, "axis_up": p.fbx_up}
        r = bridge.run_script("blender_mesh.py", [os.path.abspath(inp), os.path.abspath(out_dir), base, json.dumps(opts)], blender)
        for w in r.warnings:
            log(w)
        files = r.result.get("files") or []
        files = [f for f in files if os.path.isfile(f)]
        if not files:
            raise MeshConvError("Blender 가 파일을 만들지 않았습니다.")
        made += files
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return made
