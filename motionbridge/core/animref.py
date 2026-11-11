"""AnimRef(게임 모션 자동 분류 프로그램)가 만든 클립 폴더를 읽는다.

AnimRef 의 내부 저장 형식은 모르므로, 흔히 있을 법한 단서를 순서대로 본다.
  1) 클립 옆의 같은 이름 .json (label / ai / category / 분류 중 하나)
  2) 클립 폴더(또는 상위)의 목록 파일 library.json / clips.json / index.json / meta.json
  3) '폴더로 내보내기'가 만든 분류/묶음 폴더 이름
  4) 파일 이름 속 분류 단어, '[2/3타]' 같은 타수 표시
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from .assetidx import KNOWN_LABELS
from .frames import VIDEO_EXTS

LABEL_KEYS = ("label_manual", "label", "ai", "category", "class", "분류", "ai_label", "user_label")  # AnimRef 는 손으로 고친 label_manual 이 우선
LIST_FILES = ("library.json", "clips.json", "index.json", "meta.json", "metadata.json")
_HIT = re.compile(r"(\d+)\s*[/_-]\s*(\d+)\s*타")


@dataclass
class ClipInfo:
    path: str
    name: str
    label: str = ""             # 분류 (예: '타격', '피격', '스킬')
    group: str = ""             # 묶음 폴더 이름 (있으면)
    hit_index: Optional[int] = None
    hit_total: Optional[int] = None


def _pick_label(d) -> str:
    if isinstance(d, str):
        return d if d in KNOWN_LABELS else ""
    if isinstance(d, dict):
        for k in LABEL_KEYS:
            v = d.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return ""


def _labels_from_json(obj) -> Dict[str, str]:
    """목록 파일에서 '파일 이름(확장자 제외) -> 분류'를 모은다."""
    out: Dict[str, str] = {}
    if isinstance(obj, dict):
        items = obj.get("clips") if isinstance(obj.get("clips"), list) else None
        if items is None:
            for k, v in obj.items():
                lab = _pick_label(v)
                if lab:
                    out[os.path.splitext(os.path.basename(k))[0]] = lab
            return out
        obj = items
    if isinstance(obj, list):
        for it in obj:
            if not isinstance(it, dict):
                continue
            nm = it.get("name") or it.get("file") or it.get("path") or it.get("clip")
            lab = _pick_label(it)
            if isinstance(nm, str) and lab:
                out[os.path.splitext(os.path.basename(nm))[0]] = lab
    return out


def _read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def scan_clips(root: str, limit: int = 2000) -> List[ClipInfo]:
    if os.path.isfile(root):
        root_files, base = [root], os.path.dirname(root)
    else:
        base = root
        root_files = []
        for dp, dn, fn in os.walk(root):
            dn.sort()
            for f in sorted(fn):
                if f.lower().endswith(VIDEO_EXTS):
                    root_files.append(os.path.join(dp, f))
                    if len(root_files) >= limit:
                        break
            if len(root_files) >= limit:
                break
    listed: Dict[str, str] = {}
    seen_dirs = set()
    for p in root_files:
        d = os.path.dirname(p)
        for _ in range(3):  # 클립 폴더와 그 위 두 단계까지 목록 파일을 찾는다
            if d in seen_dirs or not d:
                break
            seen_dirs.add(d)
            for lf in LIST_FILES:
                fp = os.path.join(d, lf)
                if os.path.isfile(fp):
                    listed.update(_labels_from_json(_read_json(fp)))
            nd = os.path.dirname(d)
            if nd == d or len(d) <= len(base):
                break
            d = nd
    out: List[ClipInfo] = []
    for p in root_files:
        stem = os.path.splitext(os.path.basename(p))[0]
        label = ""
        side = os.path.splitext(p)[0] + ".json"
        sd = _read_json(side) if os.path.isfile(side) else None
        if sd is not None:
            label = _pick_label(sd)
        if not label:
            label = listed.get(stem, "")
        group = ""
        rel = os.path.relpath(os.path.dirname(p), base) if os.path.isdir(base) else ""
        parts = [x for x in rel.replace("\\", "/").split("/") if x and x != "."]
        if not label:
            for part in parts:
                if part in KNOWN_LABELS:
                    label = part
                    group = "/".join(parts[parts.index(part) + 1:])
                    break
        if not label:
            for w in KNOWN_LABELS:
                if w in stem:
                    label = w
                    break
        m = _HIT.search(stem)
        hi, ht = (int(m.group(1)), int(m.group(2))) if m else (None, None)
        if hi is None and isinstance(sd, dict):  # AnimRef 의 hit_index / hit_count
            if isinstance(sd.get("hit_index"), int) and isinstance(sd.get("hit_count"), int):
                hi, ht = sd["hit_index"], sd["hit_count"]
        out.append(ClipInfo(p, stem, label, group, hi, ht))
    return out


def summarize(clips: List[ClipInfo]) -> str:
    if not clips:
        return "영상 클립이 없습니다."
    by: Dict[str, int] = {}
    for c in clips:
        by[c.label or "분류 없음"] = by.get(c.label or "분류 없음", 0) + 1
    return "클립 %d개 — " % len(clips) + ", ".join("%s %d" % (k, v) for k, v in sorted(by.items(), key=lambda kv: -kv[1]))


def motion_file(path: str) -> str:
    """영상/클립 하나에 짝이 되는 3D 동작 파일(AnimRef AI 모캡이 만든 .bvh)을 찾는다. 없으면 빈 글자.

    찾는 순서: ① 클립 옆 <이름>.json 의 motion.bvh 가 가리키는 파일 ② 클립 옆의 <이름>.bvh ③ <이름>.motion/ 같은 옆 폴더 안의 .bvh"""
    import json
    if not path:
        return ""
    base = os.path.splitext(path)[0]
    meta = base + ".json" if not path.lower().endswith(".json") else path
    try:
        if os.path.isfile(meta):
            with open(meta, "r", encoding="utf-8") as f:
                d = json.load(f)
            bvh = ((d or {}).get("motion") or {}).get("bvh") if isinstance(d, dict) else ""
            if bvh and os.path.isfile(bvh):
                return str(bvh)
    except (OSError, ValueError):
        pass
    for cand in (base + ".bvh",):
        if os.path.isfile(cand):
            return cand
    return ""
