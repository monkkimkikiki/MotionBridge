"""메시의 머티리얼(재질)마다 기본 색 텍스처 파일을 찾아 연결한다 (Qt 없이).

PSK/PSKX 에는 재질 '이름'만 들어 있고 텍스처 연결은 없다. 그래서 변환한 FBX/GLB 가 구분용 임시 색으로만 나왔다.
추출할 때 같이 나온 원본 자료가 근처에 있으면 그것으로 연결한다:
  ① <재질 이름>.mat  — CUE4Parse/UModel 이 쓰는 글 파일. 'Diffuse=T_Body_D' 처럼 슬롯=텍스처 이름이 적혀 있다
  ② <재질 이름>.json — 재질 속성 파일 (Textures 안의 Diffuse/BaseColor/Albedo …)
  ③ 위 둘이 없으면 이름으로 추정: 재질 MI_Boss_Body → 텍스처 T_Boss_Body_D / _BC / _BaseColor / _Diffuse / _Albedo
찾는 범위는 메시 파일이 있는 폴더와 그 위 2단계까지(아래로는 6단계), 파일 3만 개까지만 본다.
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional, Sequence

IMG_EXTS = (".png", ".tga", ".jpg", ".jpeg", ".bmp")
DIFFUSE_KEYS = ("diffuse", "basecolor", "base_color", "albedo", "colormap", "color", "maintex", "main")
_DIFF_SUFFIX = re.compile(r"(?i)(_d|_bc|_basecolor|_base_color|_diffuse|_diff|_albedo|_alb|_color|_col|_c)$")
_OTHER_SUFFIX = re.compile(r"(?i)(_n|_nrm|_normal|_orm|_rma|_mra|_arm|_m|_r|_s|_spec|_e|_emissive|_ao|_mask|_h|_height|_sss|_op|_opacity)$")
_MAT_PREFIX = re.compile(r"(?i)^(mi|m|mat|mtl|material|mic)_")
_TEX_PREFIX = re.compile(r"(?i)^(t|tex|tx)_")


def _index(mesh_path: str, up: int = 2, depth: int = 6, cap: int = 30000):
    """근처의 그림/재질 파일 목록: ({소문자 이름(확장자 없이): 경로} 그림, {…} .mat, {…} .json). 가까운 것이 우선."""
    imgs: Dict[str, str] = {}
    mats: Dict[str, str] = {}
    jsons: Dict[str, str] = {}
    base = os.path.dirname(os.path.abspath(mesh_path))
    roots = [base]
    for _ in range(up):
        parent = os.path.dirname(roots[-1])
        if parent == roots[-1]:
            break
        roots.append(parent)
    seen_dirs = set()
    n = 0
    for root in roots:
        rd = root.rstrip(os.sep).count(os.sep)
        for dp, dn, fn in os.walk(root):
            if dp in seen_dirs:
                dn[:] = []
                continue
            seen_dirs.add(dp)
            if dp.count(os.sep) - rd >= depth:
                dn[:] = []
            for f in fn:
                n += 1
                stem, ext = os.path.splitext(f)
                ext, key = ext.lower(), stem.lower()
                if ext in IMG_EXTS:
                    imgs.setdefault(key, os.path.join(dp, f))
                elif ext == ".mat":
                    mats.setdefault(key, os.path.join(dp, f))
                elif ext == ".json":
                    jsons.setdefault(key, os.path.join(dp, f))
            if n > cap:
                return imgs, mats, jsons
    return imgs, mats, jsons


def parse_mat(path: str) -> Dict[str, str]:
    """.mat 글 파일 → {슬롯(소문자): 텍스처 이름}."""
    out: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() and v.strip():
                        out[k.strip().lower()] = v.strip()
    except OSError:
        pass
    return out


def _json_textures(path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return out

    def walk(o, depth=0):
        if depth > 6:
            return
        if isinstance(o, dict):
            for k, v in o.items():
                if isinstance(v, str) and k.lower() != "name" and ("/" in v or _TEX_PREFIX.match(v)):
                    out.setdefault(str(k).lower(), v)
                else:
                    walk(v, depth + 1)
        elif isinstance(o, list):
            for v in o[:200]:
                walk(v, depth + 1)

    walk(d)
    return out


def _tex_stem(ref: str) -> str:
    """'/Game/X/T_Body_D.T_Body_D' 나 'T_Body_D.png' → 't_body_d'."""
    s = ref.replace("\\", "/").rsplit("/", 1)[-1]
    s = s.split(".")[0] if "." in s else s
    return s.strip().strip("'\"").lower()


def _pick_slot(slots: Dict[str, str]) -> Optional[str]:
    for want in DIFFUSE_KEYS:
        for k, v in slots.items():
            if k == want:
                return v
    for want in DIFFUSE_KEYS:
        for k, v in slots.items():
            if want in k and "normal" not in k:
                return v
    return None


def core_name(material: str) -> str:
    s = _MAT_PREFIX.sub("", material.strip())
    s = re.sub(r"(?i)(_inst|_mat|_material)$", "", s)
    return s.lower()


def guess_by_name(material: str, imgs: Dict[str, str]) -> Optional[str]:
    """재질 이름과 닮은 '기본 색' 텍스처. 노멀/마스크 같은 다른 용도 텍스처는 고르지 않는다."""
    core = core_name(material)
    if len(core) < 3:
        return None
    best, best_score = None, 0.0
    for stem, path in imgs.items():
        t = _TEX_PREFIX.sub("", stem)
        if _OTHER_SUFFIX.search(t):
            continue
        is_diff = bool(_DIFF_SUFFIX.search(t))
        tb = _DIFF_SUFFIX.sub("", t)
        if tb == core:
            score = 3.0
        elif tb.startswith(core) or core.startswith(tb) and len(tb) >= 4:
            score = 2.0 * min(len(tb), len(core)) / max(len(tb), len(core))
        else:
            continue
        score += 1.0 if is_diff else 0.0
        if score > best_score:
            best, best_score = path, score
    return best if best_score >= 2.0 else None


def find_textures(mesh_path: str, materials: Sequence[str]) -> Dict[str, dict]:
    """{재질 이름: {"path": 그림 파일, "how": "mat"|"json"|"name"}} — 찾은 것만."""
    out: Dict[str, dict] = {}
    if not materials or not mesh_path:
        return out
    try:
        imgs, mats, jsons = _index(mesh_path)
    except OSError:
        return out
    if not imgs:
        return out
    for m in materials:
        key = str(m).lower()
        ref = None
        how = ""
        if key in mats:
            ref, how = _pick_slot(parse_mat(mats[key])), "mat"
        if not ref and key in jsons:
            ref, how = _pick_slot(_json_textures(jsons[key])), "json"
        path = imgs.get(_tex_stem(ref)) if ref else None
        if not path:
            path, how = guess_by_name(str(m), imgs), "name"
        if path:
            out[str(m)] = {"path": path, "how": how}
    return out


def texture_list(mesh_path: str, materials: Sequence[str]) -> List[str]:
    """materials 순서대로 텍스처 경로('' = 못 찾음)."""
    found = find_textures(mesh_path, materials)
    return [found.get(str(m), {}).get("path", "") for m in materials]
