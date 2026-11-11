"""게임별 추출 설정 기억 (game_presets.json).

한 번 추출에 성공한(또는 '추출 불가'로 판정한) 게임의 UE 버전·AES 키·usmap 경로·참고 링크·메모를 저장해 두고,
같은 게임 폴더를 다시 고르면 자동으로 채운다. 내 컴퓨터(~/.motionbridge)와, 정해 두었다면 팀 공유 폴더(NAS)에 같이 쓴다.

게임을 알아보는 열쇠(여럿, 하나만 맞아도 같은 게임): 스팀 AppID, 언리얼 프로젝트 폴더 이름, 실행 파일 이름, 설치 폴더 이름.
AES 키는 게임 파일을 여는 키일 뿐 개인 정보가 아니지만, 공유 폴더에 쓸지는 설정으로 끌 수 있다.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, List, Optional

from . import bridge

FILE_NAME = "game_presets.json"
FIELDS = ("name", "ue", "aes", "usmap", "links", "note", "status", "reason", "appid", "project", "exe", "folder", "updated")


def local_path() -> str:
    return os.path.join(bridge.CONFIG_DIR, FILE_NAME)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9가-힣]", "", (s or "").lower())


def keys_for(sc) -> List[str]:
    """이 게임을 알아보는 열쇠들 (확실한 것부터)."""
    out = []
    if getattr(sc, "appid", ""):
        out.append("appid:" + str(sc.appid))
    exe = os.path.basename(getattr(sc, "exe", "") or "")
    if exe:
        out.append("exe:" + _norm(os.path.splitext(exe)[0]))
    folder = os.path.basename(os.path.normpath(getattr(sc, "root", "") or ""))
    if getattr(sc, "project", "") and folder:
        out.append("proj:" + _norm(sc.project) + "@" + _norm(folder))
    if folder:
        out.append("dir:" + _norm(folder))
    return [k for k in out if not k.endswith(":")]


def _read(path: str) -> Dict[str, dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        games = d.get("games") if isinstance(d, dict) else None
        return {str(k): v for k, v in (games or {}).items() if isinstance(v, dict)}
    except (OSError, ValueError):
        return {}


def _write(path: str, games: Dict[str, dict]) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "games": games}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _paths(share_dir: Optional[str]) -> List[str]:
    ps = [local_path()]
    if share_dir and os.path.isdir(share_dir):
        ps.append(os.path.join(share_dir, FILE_NAME))
    return ps


def lookup(sc, share_dir: Optional[str] = None) -> Optional[dict]:
    """저장된 설정 (없으면 None). 내 컴퓨터와 공유 폴더의 기록을 합친다(겹치면 최근 것). 돌려주는 값에 'source'('local'|'share') 가 붙는다."""
    ks = keys_for(sc)
    found = []
    for i, p in enumerate(_paths(share_dir)):
        games = _read(p)
        e = next((games[k] for k in ks if k in games), None)
        if e:
            found.append(dict(e, source="local" if i == 0 else "share"))
    if not found:
        return None
    found.sort(key=lambda e: float(e.get("updated") or 0))        # 오래된 것 위에 최근 것을 덮는다 (한쪽에만 있는 값은 남는다)
    out: dict = {}
    for e in found:
        out.update({k: v for k, v in e.items() if v not in (None, "", [])})
    out["source"] = found[0]["source"] if len(found) == 1 else "local"
    return out


def remember(sc, share_dir: Optional[str] = None, share_keys: bool = True, **info) -> dict:
    """설정을 저장한다 (빈 값은 예전 값을 지우지 않는다). info: name, ue, aes, usmap, links, note, status('ok'|'blocked'), reason."""
    ks = keys_for(sc)
    if not ks:
        return {}
    entry: dict = {}
    for i, p in enumerate(_paths(share_dir)):
        games = _read(p)
        old = next((games[k] for k in ks if k in games), {})
        e = dict(old)
        for f in FIELDS:
            v = info.get(f)
            if v not in (None, "", []):
                e[f] = v
        e.update(appid=getattr(sc, "appid", "") or e.get("appid", ""), project=getattr(sc, "project", "") or e.get("project", ""),
                 exe=os.path.basename(getattr(sc, "exe", "") or "") or e.get("exe", ""),
                 folder=os.path.basename(os.path.normpath(sc.root)), updated=time.time())
        if e.get("status") == "ok":
            e.pop("reason", None)
        if i == 1 and not share_keys:
            e.pop("aes", None)
        if i == 1:
            e.pop("usmap", None) if not _shared_file(e.get("usmap", ""), share_dir) else None   # 내 컴퓨터에만 있는 경로는 남에게 쓸모없다
        for k in ks:
            games[k] = e
        _write(p, games)
        if i == 0:
            entry = e
    return entry


def _shared_file(path: str, share_dir: Optional[str]) -> bool:
    if not path or not share_dir:
        return False
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(share_dir)]) == os.path.abspath(share_dir)
    except ValueError:
        return False


def find_usmap_near(sc, extra_dirs: Optional[List[str]] = None) -> str:
    """게임 폴더·공유 폴더 안에서 .usmap 을 찾는다 (가장 최근 것). 없으면 빈 글자."""
    best, best_t = "", -1.0
    roots = [getattr(sc, "root", "")] + list(extra_dirs or [])
    for r in roots:
        if not r or not os.path.isdir(r):
            continue
        base = os.path.abspath(r).count(os.sep)
        for dp, dn, fn in os.walk(r):
            if dp.count(os.sep) - base >= 5:
                dn[:] = []
            for f in fn:
                if f.lower().endswith(".usmap"):
                    p = os.path.join(dp, f)
                    try:
                        t = os.path.getmtime(p)
                    except OSError:
                        continue
                    if t > best_t:
                        best, best_t = p, t
    return best
