"""영상 -> 3D 모션 AI(GVHMR / WHAM) 설치·실행 도우미. Qt 에 의존하지 않는다.

두 엔진은 리눅스용(PyTorch + CUDA) 연구 코드라서 Windows 에서는 WSL2(우분투) 안에 설치해 돌린다. 이 모듈은
  · 설치 스크립트 / 상태 점검 스크립트 / 실행 스크립트(bash)를 만들고,
  · 그것을 WSL(또는 리눅스·맥의 bash)에서 돌려 한 줄씩 로그를 돌려주고,
  · SMPL 모델 파일(라이선스 가입 후 직접 받아야 함)을 정해진 자리에 놓아 준다.
엔진이 돌려준 결과(SMPL 매개변수)는 smplmotion 이 우리 모션으로 바꾼다.

설치 위치(WSL/리눅스 안): ~/motionbridge_ai/{mamba, mamba_root, gvhmr, wham, runs}
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

BASE = "$HOME/motionbridge_ai"

SMPL_LINKS = [
    ("SMPL (몸 모델)", "https://smpl.is.tue.mpg.de/"),
    ("SMPL-X (GVHMR 에 필요)", "https://smpl-x.is.tue.mpg.de/"),
    ("SMPLify (WHAM 의 SMPL 중립 모델)", "https://smplify.is.tue.mpg.de/"),
]


@dataclass
class Engine:
    key: str
    name: str
    blurb: str
    repo: str
    python: str
    smpl_dir: str                      # 저장소 기준 SMPL 모델 폴더 (그 안에 smpl/ smplx/ 가 생긴다)
    need_files: List[Tuple[str, str]]  # (표시 이름, 저장소 기준 필수 파일) — 설치 확인용
    notes: List[str] = field(default_factory=list)


GVHMR = Engine(
    "gvhmr", "GVHMR (권장)",
    "영상 한 편에서 사람 모션을 월드 좌표로 복원합니다(SIGGRAPH Asia 2024). 카메라가 움직여도 되고, 설치가 WHAM 보다 덜 까다롭습니다.",
    "https://github.com/zju3dv/GVHMR", "3.10", "inputs/checkpoints/body_models",
    [("SMPL 중립 모델", "inputs/checkpoints/body_models/smpl/SMPL_NEUTRAL.pkl"),
     ("SMPL-X 중립 모델", "inputs/checkpoints/body_models/smplx/SMPLX_NEUTRAL.npz"),
     ("GVHMR 체크포인트", "inputs/checkpoints/gvhmr/gvhmr_siga24_release.ckpt"),
     ("HMR2 체크포인트", "inputs/checkpoints/hmr2/epoch=10-step=25000.ckpt"),
     ("ViTPose 체크포인트", "inputs/checkpoints/vitpose/vitpose-h-multi-coco.pth"),
     ("YOLO 체크포인트", "inputs/checkpoints/yolo/yolov8x.pt")],
    ["NVIDIA GPU(CUDA 12.1용 PyTorch). RTX 50 시리즈는 안 될 수 있습니다.",
     "코드 라이선스가 교육·연구·비영리 전용입니다 (상업 이용 금지). SMPL 모델도 비상업 라이선스예요."],
)
WHAM = Engine(
    "wham", "WHAM (구형 환경·실험적)",
    "영상에서 사람 모션과 이동을 복원합니다(CVPR 2024). 오래된 PyTorch 1.11 / CUDA 11.3 환경이 필요해서 최신 GPU(RTX 40·50)에서는 설치가 어려울 수 있습니다.",
    "https://github.com/yohanshin/WHAM", "3.9", "dataset/body_models",
    [("SMPL 중립 모델", "dataset/body_models/smpl/SMPL_NEUTRAL.pkl"),
     ("WHAM 체크포인트", "checkpoints/wham_vit_w_3dpw.pth.tar"),
     ("HMR2 체크포인트", "checkpoints/hmr2a.ckpt"),
     ("ViTPose 체크포인트", "checkpoints/vitpose-h-multi-coco.pth"),
     ("YOLO 체크포인트", "checkpoints/yolov8x.pt")],
    ["NVIDIA GPU. 이 엔진은 PyTorch 1.11(CUDA 11.3)이라 RTX 30 시리즈 이하에서만 잘 됩니다.",
     "코드는 MIT 지만 SMPL 모델은 비상업 라이선스입니다. 카메라 이동 추정(DPVO)은 설치하지 않고 '카메라 고정' 방식으로 돌립니다."],
)
ENGINES: Dict[str, Engine] = {e.key: e for e in (GVHMR, WHAM)}


# ------------------------------------------------------------------ 경로 / 실행
def is_windows() -> bool:
    return os.name == "nt"


def to_wsl_path(p: str) -> str:
    """C:\\Users\\a\\b.mp4 -> /mnt/c/Users/a/b.mp4 (이미 리눅스 경로면 그대로)."""
    p = (p or "").strip()
    m = re.match(r"^([A-Za-z]):[\\/]*(.*)$", p)
    if not m:
        return p.replace("\\", "/")
    return "/mnt/%s/%s" % (m.group(1).lower(), m.group(2).replace("\\", "/"))


def sh_quote(s: str) -> str:
    return "'" + str(s).replace("'", "'\\''") + "'"


def _argv_for_script(script_path: str) -> List[str]:
    if is_windows():
        return ["wsl.exe", "--", "bash", "-l", to_wsl_path(script_path)]
    return ["bash", "-l", script_path]


def run_script(script: str, on_line: Optional[Callable[[str], None]] = None, cancel=None, timeout: Optional[float] = None) -> int:
    """bash 스크립트를 WSL(Windows) 또는 bash(리눅스/맥)에서 돌린다. 한 줄 나올 때마다 on_line. 반환: 종료 코드(-1 = 취소/시간 초과)."""
    fd, path = tempfile.mkstemp(prefix="mb_ai_", suffix=".sh")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(script)
    kw = {}
    if is_windows():
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(_argv_for_script(path), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", **kw)
    except (OSError, ValueError) as e:
        if on_line:
            on_line("실행할 수 없습니다: %s" % e)
        return 127
    import threading
    import time

    t0 = time.time()
    killed = {"v": False}

    def watchdog():
        while proc.poll() is None:
            if (cancel is not None and cancel.is_set()) or (timeout is not None and time.time() - t0 > timeout):
                killed["v"] = True
                try:
                    proc.kill()
                except OSError:
                    pass
                return
            time.sleep(0.3)

    threading.Thread(target=watchdog, daemon=True).start()
    try:
        for line in proc.stdout:
            line = line.rstrip("\r\n")
            if on_line and line:
                on_line(line)
    finally:
        rc = proc.wait()
        try:
            os.remove(path)
        except OSError:
            pass
    return -1 if killed["v"] else rc


def run_capture(script: str, timeout: float = 60.0) -> Tuple[int, str]:
    lines: List[str] = []
    rc = run_script(script, lines.append, timeout=timeout)
    return rc, "\n".join(lines)


# ------------------------------------------------------------------ 스크립트 조각
HEADER = """set -e
export MB_BASE="$HOME/motionbridge_ai"
export MAMBA_ROOT_PREFIX="$MB_BASE/mamba_root"
MM="$MB_BASE/mamba/bin/micromamba"
mkdir -p "$MB_BASE"
"""


def _need_check_lines(e: Engine) -> str:
    return "\n".join('[ -e "$R/%s" ] && echo "need:%d=1" || echo "need:%d=0"' % (rel, i, i) for i, (_n, rel) in enumerate(e.need_files))


def probe_script(e: Engine) -> str:
    """상태 점검: 한 줄에 key=value 로 돌려준다."""
    return """MB_BASE="$HOME/motionbridge_ai"
MM="$MB_BASE/mamba/bin/micromamba"
R="$MB_BASE/%(key)s"
echo "wsl=1"
if command -v nvidia-smi >/dev/null 2>&1; then echo "gpu=$(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>/dev/null | head -1)"; else echo "gpu="; fi
command -v curl >/dev/null 2>&1 && echo "curl=1" || echo "curl=0"
command -v bzip2 >/dev/null 2>&1 && echo "bzip2=1" || echo "bzip2=0"
[ -x "$MM" ] && echo "mamba=1" || echo "mamba=0"
[ -d "$R/.git" ] && echo "repo=1" || echo "repo=0"
if [ -x "$MM" ] && MAMBA_ROOT_PREFIX="$MB_BASE/mamba_root" "$MM" env list 2>/dev/null | grep -q "/envs/%(key)s"; then echo "env=1"; else echo "env=0"; fi
%(needs)s
""" % {"key": e.key, "needs": _need_check_lines(e)}


@dataclass
class Status:
    wsl: bool = False
    gpu: str = ""
    curl: bool = True
    bzip2: bool = True
    mamba: bool = False
    repo: bool = False
    env: bool = False
    needs: Dict[int, bool] = field(default_factory=dict)
    raw: str = ""

    def installed(self) -> bool:
        return self.repo and self.env

    def models_ok(self, e: Engine) -> bool:
        return all(self.needs.get(i, False) for i, (n, _r) in enumerate(e.need_files) if "모델" in n)

    def ready(self, e: Engine) -> bool:
        return self.installed() and all(self.needs.get(i, False) for i in range(len(e.need_files)))


def parse_probe(text: str) -> Status:
    st = Status(raw=text or "")
    for line in (text or "").splitlines():
        k, _, v = line.strip().partition("=")
        if k == "wsl":
            st.wsl = v == "1"
        elif k == "gpu":
            st.gpu = v.strip()
        elif k in ("curl", "bzip2", "mamba", "repo", "env"):
            setattr(st, k, v == "1")
        elif k.startswith("need:"):
            try:
                st.needs[int(k[5:])] = v == "1"
            except ValueError:
                pass
    return st


def check_status(e: Engine) -> Status:
    rc, out = run_capture(probe_script(e), timeout=60)
    st = parse_probe(out)
    if rc != 0 and not st.wsl:
        st.raw = out or "WSL 을 실행하지 못했습니다 (rc=%s)" % rc
    return st


def install_script(e: Engine) -> str:
    """환경 만들기 + 소스 받기 + 패키지 설치 + 체크포인트 받기. (SMPL 모델은 라이선스 때문에 사용자가 따로 받아야 한다)"""
    s = HEADER + """
if ! command -v curl >/dev/null 2>&1 || ! command -v bzip2 >/dev/null 2>&1 || ! command -v git >/dev/null 2>&1; then
  echo "!! curl / bzip2 / git 이 필요합니다. WSL 터미널에서 다음을 한 번 실행해 주세요:  sudo apt-get update && sudo apt-get install -y curl bzip2 git"
  exit 10
fi
if [ ! -x "$MM" ]; then
  echo "== [1/5] 환경 관리자(micromamba) 받기"
  mkdir -p "$MB_BASE/mamba"
  curl -Ls https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xj -C "$MB_BASE/mamba" bin/micromamba
fi
"""
    s += 'echo "== [2/5] 파이썬 %s 환경 만들기"\n' % e.python
    if e.key == "gvhmr":
        s += """"$MM" create -y -n gvhmr -c conda-forge python=3.10 pip git ffmpeg
cd "$MB_BASE"
echo "== [3/5] 소스 받기"
[ -d gvhmr/.git ] || git clone --depth 1 https://github.com/zju3dv/GVHMR gvhmr
cd gvhmr
echo "== [4/5] 패키지 설치 (PyTorch 포함 수 GB — 오래 걸립니다)"
"$MM" run -n gvhmr pip install -r requirements.txt
"$MM" run -n gvhmr pip install -e .
echo "== [5/5] 체크포인트 받기 (Google Drive)"
"$MM" run -n gvhmr pip install gdown
mkdir -p inputs/checkpoints
"$MM" run -n gvhmr gdown --folder "https://drive.google.com/drive/folders/1eebJ13FUEXrKBawHpJroW0sNSxLjh9xD" -O inputs/checkpoints || echo "!! 체크포인트 자동 받기 실패(Google Drive 한도일 수 있음). 잠시 뒤 다시 하거나 README 의 링크에서 직접 받아 inputs/checkpoints 에 넣어 주세요."
"""
        for rel in (n for _l, n in e.need_files if "checkpoints/" in n and "body_models" not in n):
            s += 'if [ ! -e "%s" ]; then f="$(find inputs/checkpoints -name %s 2>/dev/null | head -1)"; if [ -n "$f" ]; then mkdir -p "$(dirname %s)"; mv "$f" %s; fi; fi\n' % (
                rel, sh_quote(os.path.basename(rel)), sh_quote(rel), sh_quote(rel))
    else:
        s += """"$MM" create -y -n wham -c conda-forge python=3.9 pip git ffmpeg
cd "$MB_BASE"
echo "== [3/5] 소스 받기"
[ -d wham/.git ] || git clone --depth 1 --recurse-submodules --shallow-submodules https://github.com/yohanshin/WHAM wham
cd wham
echo "== [4/5] 패키지 설치 (PyTorch 1.11 / CUDA 11.3 — 오래 걸립니다)"
"$MM" install -y -n wham -c pytorch -c conda-forge pytorch==1.11.0 torchvision==0.12.0 torchaudio==0.11.0 cudatoolkit=11.3
"$MM" run -n wham pip install -r requirements.txt
"$MM" run -n wham pip install -v -e third-party/ViTPose
echo "== [5/5] 체크포인트·보조 데이터 받기 (Google Drive)"
mkdir -p checkpoints dataset
dl() { "$MM" run -n wham gdown "https://drive.google.com/uc?id=$1&export=download&confirm=t" -O "$2" || echo "!! 받기 실패: $2"; }
dl 1pbmzRbWGgae6noDIyQOnohzaVnX_csUZ dataset/body_models.tar.gz && tar -xf dataset/body_models.tar.gz -C dataset/ && rm -f dataset/body_models.tar.gz
dl 1i7kt9RlCCCNEW2aYaDWVr-G778JkLNcB checkpoints/wham_vit_w_3dpw.pth.tar
dl 1J6l8teyZrL0zFzHhzkC7efRhU0ZJ5G9Y checkpoints/hmr2a.ckpt
dl 1zJ0KP23tXD42D47cw1Gs7zE2BA_V_ERo checkpoints/yolov8x.pt
dl 1xyF7F3I7lWtdq82xmEPVQ5zl4HaasBso checkpoints/vitpose-h-multi-coco.pth
"""
    s += 'echo "== 설치 단계 끝. SMPL 모델 파일이 아직 없으면 \'SMPL 파일 놓기\'로 넣어 주세요."\n'
    return s


SMPL_FILE_MAP = [
    # (찾을 파일 이름 패턴(대소문자 무시), 놓을 이름, 폴더, 필요한 엔진)
    ("SMPL_NEUTRAL.pkl", "SMPL_NEUTRAL.pkl", "smpl", ("gvhmr", "wham")),
    ("basicModel_neutral_lbs_10_207_0_v1.0.0.pkl", "SMPL_NEUTRAL.pkl", "smpl", ("gvhmr", "wham")),
    ("SMPL_FEMALE.pkl", "SMPL_FEMALE.pkl", "smpl", ("gvhmr", "wham")),
    ("basicModel_f_lbs_10_207_0_v1.0.0.pkl", "SMPL_FEMALE.pkl", "smpl", ("gvhmr", "wham")),
    ("SMPL_MALE.pkl", "SMPL_MALE.pkl", "smpl", ("gvhmr", "wham")),
    ("basicmodel_m_lbs_10_207_0_v1.0.0.pkl", "SMPL_MALE.pkl", "smpl", ("gvhmr", "wham")),
    ("SMPLX_NEUTRAL.npz", "SMPLX_NEUTRAL.npz", "smplx", ("gvhmr",)),
    ("SMPLX_FEMALE.npz", "SMPLX_FEMALE.npz", "smplx", ("gvhmr",)),
    ("SMPLX_MALE.npz", "SMPLX_MALE.npz", "smplx", ("gvhmr",)),
]


def place_models_script(e: Engine, src_dir: str) -> str:
    """사용자가 받아 둔 SMPL/SMPL-X 파일(폴더 안 어디에 있든, zip 도 가능)을 엔진이 찾는 자리에 복사한다."""
    dest = "$MB_BASE/%s/%s" % (e.key, e.smpl_dir)
    s = HEADER + 'SRC=%s\nDEST="%s"\nTMP="$(mktemp -d)"\n' % (sh_quote(to_wsl_path(src_dir)), dest)
    s += """for z in "$SRC"/*.zip; do [ -e "$z" ] && python3 -m zipfile -e "$z" "$TMP/$(basename "$z" .zip)" >/dev/null 2>&1 || true; done
n=0
put() { # 패턴 이름 폴더
  f="$(find "$SRC" "$TMP" -iname "$1" 2>/dev/null | head -1)"
  if [ -n "$f" ]; then mkdir -p "$DEST/$3"; cp -f "$f" "$DEST/$3/$2"; echo "놓음: $3/$2  <-  $f"; n=$((n+1)); fi
}
"""
    for pat, name, folder, engines in SMPL_FILE_MAP:
        if e.key in engines:
            s += "put %s %s %s\n" % (sh_quote(pat), sh_quote(name), sh_quote(folder))
    want = "SMPL_NEUTRAL.pkl / SMPLX_NEUTRAL.npz" if e.key == "gvhmr" else "SMPL_NEUTRAL.pkl"
    s += 'rm -rf "$TMP"\necho "복사한 파일 $n개"\n[ "$n" -gt 0 ] || { echo "!! 고른 폴더에서 SMPL 파일을 찾지 못했습니다 (%s 또는 받은 zip)"; exit 4; }\n' % want
    return s


# 엔진 환경 안에서 SMPL 매개변수를 .npz 로 덤프한다 (torch/smplx 가 있는 환경에서만 돈다)
DUMP_PY = '''import sys
import numpy as np
engine, results, models, out = sys.argv[1:5]
fps = float(sys.argv[5]) if len(sys.argv) > 5 else 30.0
import torch
import smplx


def rest_joints(model_type, betas):
    kw = dict(model_path=models, model_type=model_type, gender="neutral", batch_size=1, num_betas=10)
    kw.update(ext="npz" if model_type == "smplx" else "pkl")
    if model_type == "smplx":
        kw.update(use_pca=False)
    m = smplx.create(**kw)
    with torch.no_grad():
        o = m(betas=torch.tensor(np.asarray(betas, dtype=np.float32).reshape(1, -1)))
    return o.joints[0, :22].numpy()


if engine == "gvhmr":
    try:
        pred = torch.load(results, map_location="cpu", weights_only=False)
    except TypeError:
        pred = torch.load(results, map_location="cpu")
    p = pred["smpl_params_global"]
    root = p["global_orient"].float().numpy().reshape(-1, 3)
    body = p["body_pose"].float().numpy().reshape(-1, 21, 3)
    tr = p["transl"].float().numpy().reshape(-1, 3)
    betas = p["betas"].float().numpy().reshape(-1, 10).mean(0)
    joints = rest_joints("smplx", betas)
else:
    import joblib
    res = joblib.load(results)
    sid = max(res, key=lambda k: len(res[k]["pose_world"]))
    r = res[sid]
    pw = np.asarray(r["pose_world"]).reshape(-1, 72)
    root, body = pw[:, :3], pw[:, 3:66].reshape(-1, 21, 3)
    tr = np.asarray(r["trans_world"]).reshape(-1, 3)
    betas = np.asarray(r["betas"]).reshape(-1, 10).mean(0)
    joints = rest_joints("smpl", betas)
np.savez(out, joints=joints, root_orient=root, body_pose=body, transl=tr, fps=fps)
print("saved", out, "frames", len(root))
'''


def run_script_text(e: Engine, video: str, out_npz: str, static_cam: bool = True, fps: Optional[float] = None) -> str:
    """영상 한 편을 돌려 결과를 out_npz(Windows 경로도 가능)에 .npz 로 저장한다."""
    vid, outp = to_wsl_path(video), to_wsl_path(out_npz)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", os.path.splitext(os.path.basename(video))[0]) or "video"
    s = HEADER + 'cd "$MB_BASE/%s"\nRUN="$MB_BASE/runs/%s/%s"\nrm -rf "$RUN"; mkdir -p "$RUN"\n' % (e.key, e.key, stem)
    s += "cat > \"$MB_BASE/mb_smpl_dump.py\" <<'MB_PY_EOF'\n" + DUMP_PY + "MB_PY_EOF\n"
    if e.key == "gvhmr":
        s += 'echo "== GVHMR 실행 (GPU 에서 영상 길이에 따라 수 분)"\n'
        s += '"$MM" run -n gvhmr python tools/demo/demo.py --video %s --output_root "$RUN"%s\n' % (sh_quote(vid), " -s" if static_cam else "")
        s += 'RES="$(find "$RUN" -name hmr4d_results.pt | head -1)"\n'
        s += '[ -n "$RES" ] || { echo "!! 결과(hmr4d_results.pt)가 만들어지지 않았습니다. 위 로그의 오류를 확인하세요."; exit 3; }\n'
        s += 'FPS=%s\n' % (("%g" % fps) if fps else "30")
    else:
        s += 'echo "== WHAM 실행"\n'
        s += '"$MM" run -n wham python demo.py --video %s --output_pth "$RUN" --save_pkl%s\n' % (sh_quote(vid), " --estimate_local_only" if static_cam else "")
        s += 'RES="$(find "$RUN" -name wham_output.pkl | head -1)"\n'
        s += '[ -n "$RES" ] || { echo "!! 결과(wham_output.pkl)가 만들어지지 않았습니다. 위 로그의 오류를 확인하세요."; exit 3; }\n'
        if fps:
            s += "FPS=%g\n" % fps
        else:
            s += 'FPS="$("$MM" run -n wham python -c "import cv2,sys;print(cv2.VideoCapture(sys.argv[1]).get(cv2.CAP_PROP_FPS) or 30)" %s)"\n' % sh_quote(vid)
    s += 'echo "== 결과를 모션 데이터로 변환"\n'
    models = "$MB_BASE/%s/%s" % (e.key, e.smpl_dir)
    s += 'mkdir -p "$(dirname %s)"\n' % sh_quote(outp)
    s += '"$MM" run -n %s python "$MB_BASE/mb_smpl_dump.py" %s "$RES" "%s" %s "$FPS"\n' % (e.key, e.key, models, sh_quote(outp))
    s += 'echo "== 끝"\n'
    return s
