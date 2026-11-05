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

from . import bridge, meshconv
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
                  on_event=None, kind: str = "anim", mesh_format: str = "glb", textures: bool = False) -> List[str]:
    """선택한 에셋을 out_dir 에 내보내고 만든 파일 경로 목록을 돌려준다.

    kind="anim": 애니메이션 -> PSA.  kind="mesh": 스켈레탈 메시 -> mesh_format("glb" | "psk"), textures=True 면 머티리얼/텍스처도(glb).
    """
    if kind not in ("anim", "mesh"):
        raise ExtractError("알 수 없는 종류: " + kind)
    if mesh_format not in ("glb", "psk"):
        raise ExtractError("알 수 없는 메시 형식: " + mesh_format)
    if not paths:
        raise ExtractError("내보낼 에셋이 없습니다.")
    os.makedirs(out_dir, exist_ok=True)
    fd, plist = tempfile.mkstemp(prefix="mb_paths_", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(paths))
        args = ["export"] + _base_args(game, ue, aes, usmap) + ["--out", out_dir, "--paths-file", plist, "--kind", kind]
        if kind == "mesh":
            args += ["--format", mesh_format] + (["--textures"] if textures else [])
        events = run_helper(helper, args, on_event)
    finally:
        try:
            os.remove(plist)
        except OSError:
            pass
    files = [e["file"] for e in events if e.get("type") == "exported" and e.get("file")]
    return files


def export_meshes(helper: str, game: str, ue: str, aes: Optional[str], usmap: Optional[str], paths: List[str], out_dir: str,
                  formats: List[str], variants: List[str], preset: str = "max", blender: Optional[str] = None, textures: bool = False,
                  keep_raw: bool = True, on_event=None, on_log: Optional[Callable[[str], None]] = None) -> List[str]:
    """메시를 뽑아서 바로 쓸 수 있는 형식으로 바꾼다.

    1) 도우미로 원본을 out_dir/_raw 에 뽑는다 (기본 PSKX, textures=True 면 머티리얼/텍스처가 든 GLB).
    2) formats(fbx/obj/glb) x variants(rigged/static) 로 변환해 out_dir 에 놓는다.
    formats 가 비어 있으면 원본만 돌려준다. keep_raw=False 면 변환이 성공한 뒤 원본을 지운다.
    """
    log = on_log or (lambda m: None)
    raw_dir = os.path.join(out_dir, "_raw")
    raw = export_assets(helper, game, ue, aes, usmap, paths, raw_dir, on_event, kind="mesh",
                        mesh_format="glb" if textures else "psk", textures=textures)
    raw = [f for f in raw if meshconv.is_mesh_file(f)]
    if not formats:
        return raw
    made: List[str] = []
    errors: List[str] = []
    for f in raw:
        try:
            made += meshconv.convert_mesh(f, out_dir, formats, variants, preset, blender, log)
        except (meshconv.MeshConvError, bridge.BlenderError) as e:
            errors.append(str(e))
            log(str(e))
    if not made and errors:
        raise ExtractError(errors[0])
    if keep_raw:
        made += raw
    else:
        for f in raw:
            try:
                os.remove(f)
            except OSError:
                pass
    return made
