"""게임 에셋 추출: 도우미 프로그램(mbextract, CUE4Parse 기반)을 호출한다.

역할 분담
  * 이 모듈(Python): 도우미 찾기, 인자 구성, JSON 줄 프로토콜 해석, 오류 변환
  * mbextract(C#, tools/mbextract): 컨테이너 열기, 애니메이션 목록, ActorX(PSA) 내보내기

도우미 프로토콜 (표준 출력, 한 줄에 JSON 하나)
  list   : {"type":"asset","path":"..."} ... {"type":"done","count":N}
  export : {"type":"exported","path":"...","file":"..."} / {"type":"skip","path":"...","reason":"..."} ... {"type":"done","count":N}
  오류   : {"type":"error","message":"..."}  (종료 코드 1)
  진행   : {"type":"log","message":"..."}
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, Dict, List, Optional

from . import bridge
from .gamescan import normalize_aes_key

HELPER_NAMES = ["mbextract.exe", "mbextract"]


class ExtractError(Exception):
    pass


def find_helper(explicit: Optional[str] = None) -> Optional[str]:
    cands: List[str] = []
    if explicit:
        cands.append(explicit)
    if os.environ.get("MOTIONBRIDGE_HELPER"):
        cands.append(os.environ["MOTIONBRIDGE_HELPER"])
    cfg = bridge.load_config().get("helper")
    if cfg:
        cands.append(cfg)
    here = os.path.dirname(os.path.abspath(__file__))
    bases = []
    if getattr(sys, "frozen", False):
        bases += [getattr(sys, "_MEIPASS", ""), os.path.dirname(sys.executable)]
    bases += [os.path.normpath(os.path.join(here, "..", "..")), os.path.normpath(os.path.join(here, "..", "..", "tools", "mbextract", "out"))]
    for b in bases:
        for sub in ("tools", "tools/mbextract", ""):
            for n in HELPER_NAMES:
                cands.append(os.path.join(b, sub, n))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    for n in HELPER_NAMES:
        w = shutil.which(n)
        if w:
            return w
    return None


def _base_args(game: str, ue: str, aes: Optional[str], usmap: Optional[str]) -> List[str]:
    a = ["--game", game, "--ue", ue]
    if aes:
        a += ["--aes", normalize_aes_key(aes)]
    if usmap:
        if not os.path.isfile(usmap):
            raise ExtractError("usmap 파일이 없습니다: " + usmap)
        a += ["--usmap", usmap]
    return a


def run_helper(helper: str, args: List[str], on_event: Optional[Callable[[dict], None]] = None, timeout: float = 3600.0) -> List[dict]:
    """도우미를 실행하고 JSON 줄을 모두 모아 돌려준다. error 이벤트나 비정상 종료는 ExtractError."""
    cmd = [helper] + [str(a) for a in args]
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", **kw)
    except OSError as e:
        raise ExtractError("추출 도우미를 실행하지 못했습니다: %s" % e)
    events: List[dict] = []
    error: Optional[str] = None
    try:
        assert p.stdout is not None
        for line in p.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            events.append(ev)
            if ev.get("type") == "error":
                error = ev.get("message", "알 수 없는 오류")
            if on_event:
                on_event(ev)
        err_text = p.stderr.read() if p.stderr else ""
        rc = p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        raise ExtractError("추출 도우미가 시간 안에 끝나지 않았습니다.")
    if error or rc != 0:
        raise ExtractError(error or ("도우미가 종료 코드 %d 로 끝났습니다.\n%s" % (rc, err_text.strip()[-600:])))
    return events


def list_assets(helper: str, game: str, ue: str, aes: Optional[str], usmap: Optional[str], pattern: str = "(?i)anim",
                limit: int = 5000, on_event=None) -> List[str]:
    args = ["list"] + _base_args(game, ue, aes, usmap) + ["--filter", pattern, "--limit", str(limit)]
    return [e["path"] for e in run_helper(helper, args, on_event) if e.get("type") == "asset"]


def export_assets(helper: str, game: str, ue: str, aes: Optional[str], usmap: Optional[str], paths: List[str], out_dir: str,
                  on_event=None) -> List[str]:
    """선택한 애니메이션을 out_dir 에 PSA 로 내보내고 만든 파일 경로 목록을 돌려준다."""
    if not paths:
        raise ExtractError("내보낼 에셋이 없습니다.")
    os.makedirs(out_dir, exist_ok=True)
    fd, plist = tempfile.mkstemp(prefix="mb_paths_", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(paths))
        args = ["export"] + _base_args(game, ue, aes, usmap) + ["--out", out_dir, "--paths-file", plist]
        events = run_helper(helper, args, on_event)
    finally:
        try:
            os.remove(plist)
        except OSError:
            pass
    files = [e["file"] for e in events if e.get("type") == "exported" and e.get("file")]
    return files
