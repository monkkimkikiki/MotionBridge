"""Blender 연동: FBX/.blend/.dae 읽기(→GLB) 와 FBX 내보내기.

Blender 가 설치되어 있어야 하는 기능은 이 모듈에만 모여 있다. 나머지(BVH/GLB/JSON)는 Blender 없이 동작한다.
Blender 는 백그라운드(-b)로 실행하고, 스크립트가 출력하는 `MB_RESULT {json}` / `MB_ERROR msg` / `MB_WARN msg` 줄을 해석한다.
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Optional, Tuple

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".motionbridge")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")


class BlenderError(Exception):
    pass


# ----------------------------------------------------------------------------- 설정
def load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def scripts_dir() -> str:
    """blender_scripts 폴더 위치(소스 실행 / PyInstaller 번들 모두)."""
    cands = []
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        cands += [os.path.join(base, "blender_scripts"), os.path.join(os.path.dirname(sys.executable), "blender_scripts")]
    here = os.path.dirname(os.path.abspath(__file__))
    cands.append(os.path.join(here, "..", "..", "blender_scripts"))
    cands.append(os.path.join(here, "..", "blender_scripts"))
    for c in cands:
        c = os.path.normpath(c)
        if os.path.isfile(os.path.join(c, "blender_apply.py")):
            return c
    raise BlenderError("blender_scripts 폴더를 찾을 수 없습니다.")


def _version_key(path: str):
    m = re.findall(r"(\d+)\.(\d+)", path)
    return tuple(int(x) for x in m[-1]) if m else (0, 0)


def find_blender(explicit: Optional[str] = None) -> Optional[str]:
    """Blender 실행 파일 경로를 찾는다. 우선순위: 인자 > 환경변수 > 설정 파일 > PATH > 흔한 설치 위치(가장 최신 버전)."""
    cands: List[str] = []
    if explicit:
        cands.append(explicit)
    if os.environ.get("MOTIONBRIDGE_BLENDER"):
        cands.append(os.environ["MOTIONBRIDGE_BLENDER"])
    cfg = load_config().get("blender")
    if cfg:
        cands.append(cfg)
    for c in cands:
        if c and os.path.isfile(c):
            return c
    w = shutil.which("blender")
    if w:
        return w
    patterns = []
    if os.name == "nt":
        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if base:
                patterns.append(os.path.join(base, "Blender Foundation", "Blender*", "blender.exe"))
                patterns.append(os.path.join(base, "Programs", "Blender Foundation", "Blender*", "blender.exe"))
        patterns += [r"C:\Program Files\Blender Foundation\Blender*\blender.exe", r"D:\Program Files\Blender Foundation\Blender*\blender.exe",
                     r"C:\Program Files (x86)\Steam\steamapps\common\Blender\blender.exe"]
    elif sys.platform == "darwin":
        patterns.append("/Applications/Blender*.app/Contents/MacOS/Blender")
    else:
        patterns += ["/usr/bin/blender", "/usr/local/bin/blender", "/opt/blender*/blender", os.path.expanduser("~/blender*/blender"), "/snap/bin/blender"]
    found: List[str] = []
    for p in patterns:
        found += glob.glob(p)
    found = [f for f in found if os.path.isfile(f)]
    if found:
        return sorted(found, key=_version_key)[-1]
    return None


# ----------------------------------------------------------------------------- 실행
class RunResult:
    def __init__(self, returncode: int, stdout: str, stderr: str):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.result: Dict = {}
        self.warnings: List[str] = []
        self.error: Optional[str] = None
        for ln in stdout.splitlines():
            if ln.startswith("MB_RESULT "):
                try:
                    self.result = json.loads(ln[len("MB_RESULT "):])
                except ValueError:
                    pass
            elif ln.startswith("MB_WARN"):
                self.warnings.append(ln[len("MB_WARN"):].lstrip(": ").strip())
            elif ln.startswith("MB_ERROR"):
                self.error = ln[len("MB_ERROR"):].strip()


def build_command(blender: str, script: str, args: List[str]) -> List[str]:
    return [blender, "-b", "--python-exit-code", "1", "--python", script, "--"] + [str(a) for a in args]


def run_script(script_name: str, args: List[str], blender: Optional[str] = None, timeout: float = 900.0) -> RunResult:
    exe = find_blender(blender)
    if not exe:
        raise BlenderError("Blender 를 찾을 수 없습니다. Blender 를 설치하거나 설정에서 blender 실행 파일 경로를 지정하세요. (FBX 입출력에만 필요)")
    script = os.path.join(scripts_dir(), script_name)
    cmd = build_command(exe, script, args)
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace", **kw)
    except subprocess.TimeoutExpired:
        raise BlenderError("Blender 가 %d초 안에 끝나지 않았습니다." % int(timeout))
    except OSError as e:
        raise BlenderError("Blender 를 실행하지 못했습니다: %s" % e)
    r = RunResult(p.returncode, p.stdout or "", p.stderr or "")
    if p.returncode != 0 or r.error:
        tail = "\n".join((r.stdout + "\n" + r.stderr).strip().splitlines()[-12:])
        raise BlenderError("Blender 작업 실패: %s\n%s" % (r.error or "종료 코드 %d" % p.returncode, tail))
    return r


# ----------------------------------------------------------------------------- 고수준
NEEDS_BLENDER = {".fbx", ".blend", ".dae"}


def convert_to_glb(src: str, dst: Optional[str] = None, blender: Optional[str] = None) -> Tuple[str, RunResult]:
    """FBX/.blend/.dae -> GLB. dst 가 없으면 임시 폴더에 만든다."""
    if dst is None:
        d = tempfile.mkdtemp(prefix="mb_")
        dst = os.path.join(d, os.path.splitext(os.path.basename(src))[0] + ".glb")
    r = run_script("blender_convert.py", [os.path.abspath(src), dst], blender)
    if not os.path.isfile(dst):
        raise BlenderError("Blender 가 GLB 를 만들지 않았습니다.")
    return dst, r


def apply_and_export_fbx(model_path: str, payload_npz: str, out_fbx: str, options: Optional[dict] = None, blender: Optional[str] = None) -> RunResult:
    """모델 파일을 열어 payload 의 월드 포즈를 키프레임으로 굽고 FBX 로 저장."""
    r = run_script("blender_apply.py", [os.path.abspath(model_path), payload_npz, out_fbx, json.dumps(options or {})], blender)
    if not os.path.isfile(out_fbx):
        raise BlenderError("Blender 가 FBX 를 만들지 않았습니다.")
    return r
