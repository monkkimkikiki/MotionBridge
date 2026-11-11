"""모션 라이브러리 정리: 여러 캐릭터에서 뽑은 모션 파일을 '종류별 폴더'로 모은다 (Qt 없음).

    라이브러리 폴더 / <종류> / <동작> / 파일        예)  Library / attack / Plow / A_Swordman_Attacks_Plow_Start.psa
                                                         Library / locomotion / Run / A_Boar_Run_Fwd.psa

종류·동작은 movecatalog 가 파일 이름·폴더 구조에서 읽는다 (낱말 사전 없음). 원본은 지우지 않고 복사한다.
같은 이름의 파일이 이미 있으면: 내용이 같으면 건너뛰고, 다르면 '<캐릭터>__' 를 앞에 붙여 둘 다 남긴다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from . import movecatalog

ANIM_EXT = (".psa", ".fbx", ".bvh", ".anim")
INDEX_NAME = "라이브러리_목록.json"


def _safe(name: str, default: str = "기타") -> str:
    s = re.sub(r'[\\/:*?"<>|]+', "_", str(name)).strip(" ._")
    return s or default


def char_of(path: str) -> str:
    """파일 경로에서 캐릭터 이름: '…/<캐릭터>/Animations/…' 면 Animations 바로 위 폴더, 아니면 바로 위 폴더 이름."""
    parts = str(path).replace("\\", "/").split("/")[:-1]
    for i, seg in enumerate(parts):
        if seg.lower() in ("animations", "animation") and i >= 1:
            return parts[i - 1]
    return parts[-1] if parts else ""


@dataclass
class Report:
    lib: str
    copied: List[Tuple[str, str]] = field(default_factory=list)       # (원본, 새 경로)
    same: List[str] = field(default_factory=list)                     # 이미 같은 내용이 라이브러리에 있어 건너뜀
    renamed: List[Tuple[str, str]] = field(default_factory=list)      # 이름이 겹쳐 캐릭터 이름을 붙임
    skipped: List[Tuple[str, str]] = field(default_factory=list)      # (원본, 이유)
    folders: Dict[str, int] = field(default_factory=dict)             # '종류/동작' -> 파일 수

    def summary(self) -> str:
        cats = {}
        for k, n in self.folders.items():
            cats[k.split("/")[0]] = cats.get(k.split("/")[0], 0) + n
        s = "복사 %d개" % len(self.copied)
        if self.same:
            s += " · 이미 있음 %d개" % len(self.same)
        if self.renamed:
            s += " · 이름 겹쳐 캐릭터명 붙임 %d개" % len(self.renamed)
        if self.skipped:
            s += " · 건너뜀 %d개" % len(self.skipped)
        if cats:
            s += " — 종류: " + ", ".join("%s %d" % (k, n) for k, n in sorted(cats.items(), key=lambda kv: -kv[1])[:8])
        return s


def _digest(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def plan(files: Iterable[str]) -> Tuple[Dict[str, Tuple[str, str]], List[Tuple[str, str]]]:
    """파일 -> (종류 폴더, 동작 폴더). 재생할 클립이 아닌 것·몽타주 사본은 (원본, 이유) 목록으로 돌려준다."""
    by_char: Dict[str, List[str]] = {}
    skipped: List[Tuple[str, str]] = []
    for p in dict.fromkeys(files):
        if os.path.splitext(p)[1].lower() not in ANIM_EXT:
            skipped.append((p, "모션 파일이 아님"))
            continue
        by_char.setdefault(char_of(p), []).append(p)
    dest: Dict[str, Tuple[str, str]] = {}
    for ch, ps in by_char.items():
        cat = movecatalog.build(ps, [ch])
        for p in cat.duplicates:
            skipped.append((p, "몽타주 사본(원본 시퀀스가 따로 있음)"))
        for p in cat.non_clips:
            skipped.append((p, "재생할 클립이 아님(BS_/BT_ 등)"))
        for p, c in cat.clips.items():
            dest[p] = (_safe(c.category or "misc", "misc"), _safe(c.move, "_"))
    return dest, skipped


def collect(files: Iterable[str], lib_root: str, copy: bool = True) -> Report:
    """files 를 lib_root/<종류>/<동작>/ 로 복사(copy=False 면 이동)한다. 라이브러리 안의 파일은 다시 모으지 않는다."""
    lib_root = os.path.abspath(lib_root)
    rep = Report(lib_root)
    inside = lambda p: os.path.abspath(p).startswith(lib_root + os.sep)     # noqa: E731
    todo = [p for p in dict.fromkeys(files) if os.path.isfile(p) and not inside(p)]
    dest, skipped = plan(todo)
    rep.skipped += skipped
    for p, (cat, mv) in dest.items():
        folder = os.path.join(lib_root, cat, mv)
        os.makedirs(folder, exist_ok=True)
        name = os.path.basename(p)
        target = os.path.join(folder, name)
        renamed = False
        if os.path.exists(target):
            if _digest(target) == _digest(p):
                rep.same.append(p)
                continue
            ch = _safe(char_of(p), "char")
            target = os.path.join(folder, "%s__%s" % (ch, name))
            renamed = True
            if os.path.exists(target):
                if _digest(target) == _digest(p):
                    rep.same.append(p)
                    continue
                stem, ext = os.path.splitext(target)
                k = 2
                while os.path.exists("%s_%d%s" % (stem, k, ext)):
                    k += 1
                target = "%s_%d%s" % (stem, k, ext)
        try:
            (shutil.copy2 if copy else shutil.move)(p, target)
        except OSError as e:
            rep.skipped.append((p, "복사 실패: %s" % e))
            continue
        rep.copied.append((p, target))
        if renamed:
            rep.renamed.append((p, target))
        key = "%s/%s" % (cat, mv)
        rep.folders[key] = rep.folders.get(key, 0) + 1
    _write_index(lib_root)
    return rep


def _write_index(lib_root: str) -> None:
    """라이브러리 폴더 전체를 읽어 종류별 개수를 적어 둔다 (나중에 라이브러리로 쓸 때 한눈에 보도록)."""
    idx: Dict[str, Dict[str, List[str]]] = {}
    for cat in sorted(os.listdir(lib_root)):
        cp = os.path.join(lib_root, cat)
        if not os.path.isdir(cp):
            continue
        for mv in sorted(os.listdir(cp)):
            mp = os.path.join(cp, mv)
            if os.path.isdir(mp):
                idx.setdefault(cat, {})[mv] = sorted(f for f in os.listdir(mp) if os.path.isfile(os.path.join(mp, f)))
    try:
        with open(os.path.join(lib_root, INDEX_NAME), "w", encoding="utf-8") as f:
            json.dump({"종류별_개수": {c: sum(len(v) for v in m.values()) for c, m in idx.items()}, "목록": idx}, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def scan(root: str, lib_root: Optional[str] = None) -> List[str]:
    """root 아래의 모든 모션 파일 (라이브러리 폴더 안은 제외)."""
    out: List[str] = []
    lib = os.path.abspath(lib_root) if lib_root else None
    for r, ds, fs in os.walk(root):
        if lib and os.path.abspath(r).startswith(lib):
            ds[:] = []
            continue
        out += [os.path.join(r, f) for f in fs if os.path.splitext(f)[1].lower() in ANIM_EXT]
    return sorted(out)
