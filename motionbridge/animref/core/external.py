"""외부 모션 AI 서비스(DeepMotion, Move.ai, Rokoko Vision, Plask 등)와 오가기.

  1) export_for_external : 서비스에 올릴 영상 만들기 (대상을 따라가며 확대한 영상, 또는 원본 그대로)
  2) import_motion       : 서비스가 준 BVH/FBX 를 AnimRef 모션으로 가져오기 -> 이후 Blender/3ds Max 로 보내기는 똑같아요"""
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import Cancelled, media, noop
from .library import file_of, save_clip
from .settings import resource_path

EXPORT_SIDE = 1024            # 서비스에 올리는 확대 영상의 한 변 (서비스는 해상도가 클수록 유리하다)
FBX_TIMEOUT = 300


def _load(name):
    path = resource_path(f"motion_ai/{name}.py")
    spec = importlib.util.spec_from_file_location(f"animref_{name}", str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _motion_dir(s, meta, sub):
    from .mocap import motion_root
    d = motion_root(s) / Path(meta["_json"]).stem / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- 1) 서비스에 올릴 영상
def export_for_external(meta, s, follow=True, log=noop, progress=noop, cancel=None):
    """반환: {"path": 영상 경로, "kind": "follow"|"original", "folder": 폴더}"""
    from . import characters
    video = str(file_of(meta, "video"))
    base = Path(meta["_json"]).stem
    out_dir = _motion_dir(s, meta, "for_external")
    subj = meta.get("subject") or {}
    progress(0.05, "영상 준비 중...")
    if follow and subj.get("boxes"):
        out = out_dir / f"{base}_follow.mp4"
        characters.render_follow_crop(video, subj["boxes"], out, log=log, cancel=cancel, out=EXPORT_SIDE)
        kind = "follow"
        log(f"대상({subj.get('character') or '직접 고른 사람'})을 따라가며 확대한 영상을 만들었어요: {out}")
    else:
        out = out_dir / f"{base}_original{Path(video).suffix or '.mp4'}"
        shutil.copy2(video, out)
        kind = "original"
        log("대상이 정해져 있지 않아 원본 영상을 그대로 복사했어요." if follow else f"원본 영상을 복사했어요: {out}")
    progress(1.0, "완료")
    return {"path": str(out), "kind": kind, "folder": str(out_dir), "size": out.stat().st_size}


# ---------------------------------------------------------------- 2) 서비스가 준 모션 가져오기
def fbx_to_bvh(blender_exe, fbx, out_bvh, log=noop, cancel=None):
    if not blender_exe or not Path(blender_exe).exists():
        raise RuntimeError("FBX 는 Blender 로 바꿔서 읽어요. 모션 AI 창의 'Blender' 경로를 먼저 지정해 주세요. "
                           "(서비스에서 BVH 로 받을 수 있으면 BVH 가 더 간단해요.)")
    script = resource_path("motion_ai/fbx2bvh.py")
    cmd = [str(blender_exe), "--background", "--python", str(script), "--", str(fbx), str(out_bvh)]
    log("Blender 로 FBX 를 BVH 로 바꾸는 중...")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=FBX_TIMEOUT,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise RuntimeError("Blender 가 FBX 변환에 너무 오래 걸려서 멈췄어요.")
    out = (r.stdout or "") + (r.stderr or "")
    err = next((l for l in out.splitlines() if l.startswith("ANIMREF_ERROR")), None)
    if err:
        raise RuntimeError(err[len("ANIMREF_ERROR "):])
    if r.returncode != 0 or not Path(out_bvh).exists():
        raise RuntimeError("FBX 를 BVH 로 바꾸지 못했어요: " + " / ".join(out.strip().splitlines()[-3:]))


def read_text_any(path):
    data = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-16", "cp949", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeError:
            continue
    return data.decode("latin-1", "replace")


def import_motion(meta, s, path, in_place=True, markers=True, log=noop, progress=noop, cancel=None):
    """외부 BVH/FBX -> 클립의 모션(.motion.json/.motion.txt/.bvh). 반환: 모션 정보 dict (mocap.extract 와 같은 모양)."""
    from . import mocap
    path = Path(path)
    if not path.exists():
        raise RuntimeError(f"파일을 찾지 못했어요: {path}")
    ext = path.suffix.lower()
    out_dir = mocap.motion_root(s) / Path(meta["_json"]).stem
    out_dir.mkdir(parents=True, exist_ok=True)
    base = Path(meta["_json"]).stem + "_ext"
    tmp_bvh = None
    progress(0.05, "모션 파일 읽는 중...")
    try:
        if ext == ".bvh":
            text = read_text_any(path)
        elif ext == ".fbx":
            tmp_bvh = out_dir / f"{base}.from_fbx.bvh"
            fbx_to_bvh(s.get("blender_exe", ""), path, tmp_bvh, log, cancel)
            text = read_text_any(tmp_bvh)
        else:
            raise RuntimeError("BVH(.bvh) 또는 FBX(.fbx) 파일만 가져올 수 있어요.")
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        b2m = _load("bvh2motion")
        w2m = _load("wham2motion")
        progress(0.3, "뼈대를 사람 구조로 맞추는 중...")
        try:
            motion = b2m.convert_bvh(text, in_place=in_place, log=log)
        except b2m.BvhError as e:
            raise RuntimeError(str(e))
    finally:
        if tmp_bvh is not None:
            try:
                tmp_bvh.unlink(missing_ok=True)
            except OSError:
                pass
    fps = float(motion["fps"])
    ms = mocap.hit_markers(meta, s, log, cancel) if markers else []
    progress(0.8, "AnimRef 모션 파일 만드는 중...")
    base_path = str(out_dir / base)
    w2m.write_bvh(base_path + ".bvh", motion["rest"], motion["R_local"], motion["root_pos"], fps)
    src = {"engine": "external", "file": path.name, "in_place": bool(in_place), "mapping": motion.get("mapping", {})}
    w2m.write_json(base_path + ".motion.json", motion, fps, ms, src)
    w2m.write_txt(base_path + ".motion.txt", motion, fps, ms)
    mocap.prepare_scripts(mocap.motion_root(s))
    dur = float(meta.get("duration", 0.0) or 0.0)
    notes = list(motion["notes"])
    n = int(motion["R_local"].shape[0])
    if dur and abs(n / fps - dur) > max(0.5, dur * 0.15):
        notes.append(f"모션 길이({n / fps:.1f}초)가 클립 길이({dur:.1f}초)와 달라요. 서비스에 올린 영상과 같은 클립인지 확인하세요.")
    rec = {"dir": str(out_dir), "name": base, "bvh": base_path + ".bvh", "json": base_path + ".motion.json",
           "txt": base_path + ".motion.txt", "frames": n, "fps": fps, "notes": notes, "markers": ms,
           "subject_method": "external", "engine": "external", "source_file": path.name, "character": (meta.get("subject") or {}).get("character", ""),
           "created": datetime.now().isoformat(timespec="seconds")}
    meta["motion"] = rec
    save_clip(meta)
    progress(1.0, "완료")
    log(f"외부 모션을 가져왔어요: {rec['bvh']}")
    for t in notes:
        log("· " + t)
    return rec
