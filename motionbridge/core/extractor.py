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
from typing import Callable, Dict, List, Optional, Tuple

from . import animops, bridge, layout, meshconv, safesave
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


def inspect_assets(helper: str, game: str, ue: str, aes: Optional[str], usmap: Optional[str], paths: List[str], on_event=None,
                   chunk: int = 400, cancel=None):
    """에셋 파일을 실제로 열어 종류와 연결(스켈레톤, 프레임/트랙)을 읽는다. 돌려주는 값: (기록 목록, 검사한 경로 집합).

    한 번에 chunk 개씩 도우미를 부른다(너무 오래 걸리면 중간에 취소/저장할 수 있게). cancel 은 threading.Event.
    """
    from .skelgraph import Rec
    recs: List[Rec] = []
    seen: set = set()
    for i in range(0, len(paths), chunk):
        if cancel is not None and cancel.is_set():
            break
        part = paths[i:i + chunk]
        fd, plist = tempfile.mkstemp(prefix="mb_inspect_", suffix=".txt")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("\n".join(part))
            args = ["inspect"] + _base_args(game, ue, aes, usmap) + ["--paths-file", plist]
            for ev in run_helper(helper, args):
                if ev.get("type") == "node":
                    recs.append(Rec.from_event(ev))
                    seen.add(ev.get("path", ""))
        finally:
            try:
                os.remove(plist)
            except OSError:
                pass
        seen.update(part)
        if on_event:
            on_event({"type": "progress", "done": min(i + chunk, len(paths)), "total": len(paths)})
    return recs, seen


def find_players(helper: str, game: str, ue: str, aes: Optional[str], usmap: Optional[str], on_event=None, filter_regex: Optional[str] = None) -> dict:
    """게임 데이터에서 '플레이어가 조종하는 폰' 단서를 읽는다 (게임 설정 .ini 의 기본 게임모드, 게임모드의 DefaultPawnClass, 폰이 쓰는 메시/카메라/AutoPossess).

    돌려주는 값: {"configs": [{file,key,value}], "pawns": [{path,name,parent,meshes,anim_classes,camera,auto_possess,default_pawn,controller}]}
    """
    args = ["players"] + _base_args(game, ue, aes, usmap)
    if filter_regex:
        args += ["--filter", filter_regex]
    evs = run_helper(helper, args, on_event)
    return {"configs": [e for e in evs if e.get("type") == "config"], "pawns": [e for e in evs if e.get("type") == "pawn"]}


def _export_events(helper, game, ue, aes, usmap, paths, out_dir, on_event, kind, mesh_format, textures) -> List[dict]:
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
        return run_helper(helper, args, on_event)
    finally:
        try:
            os.remove(plist)
        except OSError:
            pass


def _exported(events: List[dict]) -> List[Tuple[str, str]]:
    """[(에셋 경로, 만든 파일)]"""
    return [(e.get("path") or "", e["file"]) for e in events if e.get("type") == "exported" and e.get("file")]


def _stage_dir(out_dir: str) -> str:
    return tempfile.mkdtemp(prefix=".mb_stage_", dir=out_dir if os.path.isdir(out_dir) else None)


def _move_into(src: str, dest_dir: str, taken: set, lay: Optional["layout.Layout"] = None) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    dst = os.path.join(dest_dir, layout.unique_name(dest_dir, os.path.basename(src), taken))
    if lay is not None:      # 같은 이름이 이미 디스크에 있으면 설정대로 (이전 파일 보관 / 다른 이름 / 덮어쓰기)
        dst, bk = safesave.claim(dst, lay.on_exist)
        if bk:
            lay.backups.append(bk)
        taken.add(dst.lower())
    os.replace(src, dst)
    return dst


def _drop_stage(stage: str) -> None:
    shutil.rmtree(stage, ignore_errors=True)


def export_assets(helper: str, game: str, ue: str, aes: Optional[str], usmap: Optional[str], paths: List[str], out_dir: str,
                  on_event=None, kind: str = "anim", mesh_format: str = "glb", textures: bool = False,
                  lay: Optional["layout.Layout"] = None) -> List[str]:
    """선택한 에셋을 내보내고 만든 파일 경로 목록을 돌려준다.

    kind="anim": 애니메이션 -> PSA.  kind="mesh": 스켈레탈 메시 -> mesh_format("glb" | "psk"), textures=True 면 머티리얼/텍스처도(glb).
    lay 를 주면 파일을 '게임/캐릭터/Animations|Models' 폴더로 옮겨 담는다 (이때 out_dir 은 임시 작업 위치의 부모).
    """
    if lay is None:
        return [f for _p, f in _exported(_export_events(helper, game, ue, aes, usmap, paths, out_dir, on_event, kind, mesh_format, textures))]
    os.makedirs(lay.out, exist_ok=True)
    stage = _stage_dir(lay.out)
    try:
        ev = _exported(_export_events(helper, game, ue, aes, usmap, paths, stage, on_event, kind, mesh_format, textures))
        taken: set = set()
        return [_move_into(f, lay.dest(kind, p), taken, lay) for p, f in ev if os.path.isfile(f)]
    finally:
        _drop_stage(stage)


def anim_dir(out_dir: str) -> str:
    """애니메이션(PSA)은 출력 폴더 아래 Animations 에 모은다 (처음 보는 사람도 구분되게)."""
    return os.path.join(out_dir, "Animations")


def models_dir(out_dir: str) -> str:
    """모델링(메시) 결과는 Models 에, 원본(PSKX/GLB)은 Models/_raw 에 둔다."""
    return os.path.join(out_dir, "Models")


def export_meshes(helper: str, game: str, ue: str, aes: Optional[str], usmap: Optional[str], paths: List[str], out_dir: str,
                  formats: List[str], variants: List[str], preset: str = "max", blender: Optional[str] = None, textures: bool = False,
                  keep_raw: bool = True, on_event=None, on_log: Optional[Callable[[str], None]] = None,
                  lay: Optional["layout.Layout"] = None) -> List[str]:
    """메시를 뽑아서 바로 쓸 수 있는 형식으로 바꾼다.

    1) 도우미로 원본을 뽑는다 (기본 PSKX, textures=True 면 머티리얼/텍스처가 든 GLB).
    2) formats(fbx/obj/glb) x variants(rigged/static) 로 변환한다.
    formats 가 비어 있으면 원본만 돌려준다. keep_raw=False 면 변환이 성공한 뒤 원본을 지운다.
    lay 가 없으면 out_dir/Models(원본은 Models/_raw), 있으면 '게임/캐릭터/Models' 에 모두 한 폴더로 놓는다.
    """
    log = on_log or (lambda m: None)
    kw = dict(kind="mesh", mesh_format="glb" if textures else "psk", textures=textures)
    if lay is None:
        out_dir = models_dir(out_dir)
        raw_dir = os.path.join(out_dir, "_raw")
        raw = export_assets(helper, game, ue, aes, usmap, paths, raw_dir, on_event, **kw)
        raw_items = [(None, f) for f in raw if meshconv.is_mesh_file(f)]
        stage = None
    else:
        os.makedirs(lay.out, exist_ok=True)
        stage = _stage_dir(lay.out)
        raw_items = [(p, f) for p, f in _exported(_export_events(helper, game, ue, aes, usmap, paths, stage, on_event, **kw))
                     if meshconv.is_mesh_file(f)]
    try:
        if not formats:
            if lay is None:
                return [f for _p, f in raw_items]
            taken: set = set()
            return [_move_into(f, lay.model_dest(p), taken, lay) for p, f in raw_items]
        made: List[str] = []
        errors: List[str] = []
        taken = set()
        for p, f in raw_items:
            dest = out_dir if lay is None else lay.model_dest(p)
            work = dest if lay is None else os.path.join(stage, "conv")
            try:
                conv = meshconv.convert_mesh(f, work, formats, variants, preset, blender, log)
            except (meshconv.MeshConvError, bridge.BlenderError) as e:
                errors.append(str(e))
                log(str(e))
                continue
            made += conv if lay is None else [_move_into(c, dest, taken, lay) for c in conv]
        if not made and errors:
            raise ExtractError(errors[0])
        if keep_raw:
            made += [f for _p, f in raw_items] if lay is None else [_move_into(f, lay.model_dest(p), taken, lay) for p, f in raw_items if os.path.isfile(f)]
        elif lay is None:
            for _p, f in raw_items:
                try:
                    os.remove(f)
                except OSError:
                    pass
        return made
    finally:
        if stage:
            _drop_stage(stage)


def export_locres(helper: str, game: str, ue: str, aes: Optional[str], usmap: Optional[str], out_dir: str, on_event=None) -> List[str]:
    """게임 안 글자 사전(.locres)을 out_dir 에 꺼낸다. 만든 파일 경로 목록."""
    os.makedirs(out_dir, exist_ok=True)
    args = ["locres"] + _base_args(game, ue, aes, usmap) + ["--out", out_dir]
    return [e["file"] for e in run_helper(helper, args, on_event) if e.get("type") == "exported" and e.get("file")]


def edit_psa_files(files: List[str], opt: "animops.EditOptions", suffix: str = "_edit", units_per_meter: float = 100.0,
                   on_info: Optional[Callable[[str, "animops.EditInfo"], None]] = None) -> Tuple[List[str], List[str]]:
    """추출한 PSA 를 읽어 늘리기/fps/자르기/루프/압축을 적용하고 `<이름><suffix>.psa` 로 옆에 저장한다(원본은 그대로).

    반환: (만든 파일, 경고). PSA 는 프레임마다 값을 담는 형식이라 압축은 '키로 정리한(군더더기를 뺀) 모션'으로 저장된다.
    Unreal 의 길이 단위는 cm 이므로 units_per_meter 기본값은 100.
    """
    from .io_psa import read_psa_exact, write_psa

    made: List[str] = []
    warns: List[str] = []
    if opt is None or not opt.active():
        return made, warns
    for f in files:
        if not f.lower().endswith(".psa") or not os.path.isfile(f):
            continue
        try:
            m = read_psa_exact(f)
            out_anims = []
            for a in m.animations:
                a2, _keys, info = animops.apply_edit(a, opt, units_per_meter, m.skeleton.parents)
                out_anims.append(a2)
                warns += ["%s: %s" % (os.path.basename(f), n) for n in info.notes]
                if on_info:
                    on_info(f, info)
            dst = os.path.splitext(f)[0] + suffix + ".psa"
            write_psa(dst, m.skeleton, out_anims)
            made.append(dst)
        except Exception as e:  # 한 파일이 실패해도 나머지는 계속
            warns.append("%s: 가공 실패 — %s" % (os.path.basename(f), e))
    return made, warns
