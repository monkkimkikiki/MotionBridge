"""모션 AI: 클립 하나에서 3D 동작을 뽑아(WHAM, WSL 안에서 실행) BVH/모션 파일로 만들고,
Blender 또는 3ds Max 의 '이미 세팅된 리그'에 키프레임으로 넣을 수 있게 준비한다.

흐름
  클립(.mp4) --WSL: WHAM--> wham_output.pkl --wham2motion.py--> .bvh / .motion.json / .motion.txt
  .motion.json --Blender: blender_apply.py--> 리그에 키프레임      (blender_command)
  .motion.txt  --3ds Max: max_apply.ms-----> 리그에 키프레임      (prepare_max_folder)"""
import glob
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import Cancelled, media, noop, wsl
from .library import file_of, game_dir, save_clip
from .settings import resource_path

ROOT = "/root/animref_mocap"          # WSL 안의 설치 위치 (install_wham.sh 와 같아야 함)
DISTRO_DEFAULT = "Ubuntu-22.04"
SMPL_NEUTRAL = "dataset/body_models/smpl/SMPL_NEUTRAL.pkl"
GPU_BUSY_MB = 2500                    # 분석 전에 이보다 많이 쓰이고 있으면 경고
IDLE_LIMIT = 600                      # 이 시간(초) 동안 WHAM 출력이 전혀 없으면 멈춘 것으로 본다
MAX_LONG_CLIP = 20.0                  # 이보다 긴 클립은 오래 걸린다고 안내


def _p():
    return {"root": ROOT, "wham": f"{ROOT}/WHAM", "conda_sh": f"{ROOT}/miniforge3/etc/profile.d/conda.sh",
            "ok": f"{ROOT}/installed.ok", "conv": f"{ROOT}/wham2motion.py", "inp": f"{ROOT}/input",
            "out": f"{ROOT}/output"}


def _activate():
    p = _p()
    return f"source {wsl.q(p['conda_sh'])} && conda activate wham"


# ---------------------------------------------------------------- 상태
def gpu_name():
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True,
                           timeout=15, creationflags=wsl.NO_WINDOW)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()[0].strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def gpu_warning(name):
    if not name:
        return "NVIDIA 그래픽카드를 찾지 못했어요. 모션 AI(WHAM)는 NVIDIA 그래픽카드가 필요해요."
    if re.search(r"RTX\s*(40|50)\d\d", name, re.IGNORECASE):
        return (f"{name}은(는) 최신 카드라서 WHAM이 쓰는 오래된 PyTorch(1.11)와 안 맞을 수 있어요. "
                "설치 마지막의 '그래픽카드 동작 확인'에서 막히면 클라우드 서비스(Rokoko 등)를 쓰는 게 빨라요.")
    return ""


def status(distro_pref=DISTRO_DEFAULT):
    """모션 AI가 쓸 수 있는 상태인지. level: ok / partial / not_installed / no_distro / no_wsl / no_windows / wsl_error"""
    st = {"level": "", "distro": None, "message": "", "missing": [], "gpu": None, "gpu_warn": ""}
    if not wsl.is_windows():
        st.update(level="no_windows", message="모션 AI 설치는 Windows에서만 할 수 있어요.")
        return st
    st["gpu"] = gpu_name()
    st["gpu_warn"] = gpu_warning(st["gpu"])
    if not wsl.wsl_exe():
        st.update(level="no_wsl", message="WSL(윈도우 안의 리눅스)이 아직 없어요. [설치 시작]을 누르면 설치돼요.")
        return st
    distro = wsl.pick_distro(wsl.list_distros(), distro_pref)
    if not distro:
        st.update(level="no_distro", message="WSL이나 Ubuntu가 아직 쓸 수 있는 상태가 아니에요. [설치 시작]을 누르면 설치·점검해요.")
        return st
    st["distro"] = distro
    p = _p()
    cmd = (f"echo ENV=$([ -d {wsl.q(p['root'] + '/miniforge3/envs/wham')} ] && echo 1 || echo 0); "
           f"echo DEMO=$([ -f {wsl.q(p['wham'] + '/demo.py')} ] && echo 1 || echo 0); "
           f"echo SMPL=$([ -f {wsl.q(p['wham'] + '/' + SMPL_NEUTRAL)} ] && echo 1 || echo 0); "
           f"echo CKPT=$(ls {wsl.q(p['wham'] + '/checkpoints')} 2>/dev/null | wc -l); "
           f"echo DEPS=$([ -f {wsl.q(p['root'] + '/.deps_ok')} ] && echo 1 || echo 0); "
           f"echo OK=$([ -f {wsl.q(p['ok'])} ] && echo 1 || echo 0)")
    try:
        rc, tail = wsl.run(distro, cmd)
    except Exception as e:  # noqa: BLE001
        st.update(level="wsl_error", message=f"WSL을 실행하지 못했어요: {e}")
        return st
    flags = dict(re.findall(r"^([A-Z]+)=(\d+)\s*$", "\n".join(tail), re.MULTILINE))
    if rc != 0 or not flags:
        st.update(level="wsl_error", message="WSL이 응답하지 않아요. PC를 재시작하거나 WSL을 다시 설치해 보세요.")
        return st
    miss = []
    if flags.get("ENV") != "1":
        miss.append("파이썬 환경")
    if flags.get("DEMO") != "1":
        miss.append("WHAM 코드")
    if flags.get("DEPS") != "1":
        miss.append("WHAM 부품")
    if flags.get("SMPL") != "1":
        miss.append("SMPL 인체 모델")
    if int(flags.get("CKPT", "0")) < 4:
        miss.append("AI 모델 파일")
    if flags.get("OK") != "1" and not miss:
        miss.append("그래픽카드 동작 확인")
    st["missing"] = miss
    if not miss:
        st.update(level="ok", message="모션 AI를 쓸 수 있어요.")
    elif len(miss) >= 4:
        st.update(level="not_installed", message="모션 AI가 아직 설치되지 않았어요. [설치 시작]을 누르세요.")
    else:
        st.update(level="partial", message="설치가 중간까지만 됐어요. 빠진 것: " + ", ".join(miss) + " → [설치 시작]으로 이어서 설치하세요.")
    return st


def install_bat():
    return resource_path("install_motion_ai.bat")


def launch_installer():
    """설치 창(검은 콘솔)을 띄운다. 끝나면 창이 직접 알려 준다."""
    bat = install_bat()
    if not bat.exists():
        raise RuntimeError(f"설치 파일을 찾을 수 없어요: {bat}")
    if not wsl.is_windows():
        raise RuntimeError("모션 AI 설치는 Windows에서만 할 수 있어요.")
    subprocess.Popen(["cmd.exe", "/c", "start", "AnimRef Motion AI setup", "cmd", "/k", str(bat)],
                     cwd=str(bat.parent))


# ---------------------------------------------------------------- 폴더 / 스크립트
def motion_root(s):
    d = game_dir(s) / "motion"
    d.mkdir(parents=True, exist_ok=True)
    return d


def prepare_scripts(folder):
    """Blender/Max 스크립트를 모션 폴더에 복사 (Max 에서 직접 열어 쓰도록)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    out = {}
    for name in ("blender_apply.py", "max_apply.ms"):
        src = resource_path(f"motion_ai/{name}")
        dst = folder / name
        shutil.copyfile(src, dst)
        out[name] = dst
    return out


def hit_markers(meta, s, log=noop, cancel=None):
    """클립 안의 타격 시각들(초). 소리와 번쩍임으로 찾는다. 실패하면 빈 목록."""
    try:
        from . import hitsplit as H

        dur = float(meta.get("duration", 0.0)) or media.probe(file_of(meta, "video")).duration
        cands, _ = H.find_candidates(file_of(meta, "video"), dur, cancel=cancel)
        return [round(t, 3) for t, _k in H.merge_cands(cands, float(s.get("hit_min_gap", 0.3)))]
    except Cancelled:
        raise
    except Exception as e:  # noqa: BLE001
        log(f"타격 시각을 찾지 못했어요 (마커 없이 진행): {e}")
        return []


# ---------------------------------------------------------------- 모션 뽑기
def gpu_memory(distro):
    """WSL 에서 본 그래픽카드 메모리 (사용 MiB, 전체 MiB). 못 읽으면 (None, None)."""
    try:
        rc, tail = wsl.run(distro, "nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits")
        m = re.search(r"(\d+)\s*,\s*(\d+)", "\n".join(tail))
        if rc == 0 and m:
            return int(m.group(1)), int(m.group(2))
    except Exception:  # noqa: BLE001
        pass
    return None, None


def explain_failure(tail):
    """WHAM 출력의 마지막 줄들에서 사람이 이해할 수 있는 원인을 고른다."""
    t = "\n".join(tail).lower()
    if "out of memory" in t:
        return "그래픽카드 메모리가 부족해요. 다른 프로그램을 끄거나 더 짧은 클립으로 해보세요."
    if "no such file" in t and ("checkpoint" in t or "body_models" in t or ".pth" in t):
        return "WHAM 모델 파일이 없어요. [설치 시작]을 다시 눌러 이어서 설치해 주세요."
    if "cuda" in t and ("not available" in t or "no cuda" in t or "driver" in t or "initialization" in t):
        return "WSL에서 그래픽카드를 못 쓰고 있어요. NVIDIA 드라이버를 최신으로 올리고 PC를 재시작한 뒤 다시 해보세요."
    if "no kernel image" in t or "cuda error: no kernel" in t:
        return "그래픽카드가 WHAM의 PyTorch 버전과 안 맞아요 (최신 RTX 시리즈에서 생길 수 있어요). 클라우드 서비스를 쓰는 게 좋아요."
    if "modulenotfounderror" in t or "importerror" in t:
        return "WHAM 부품이 덜 설치됐어요. [설치 시작]을 다시 눌러 이어서 설치해 주세요."
    return "WHAM 실행 중 오류가 났어요: " + " / ".join(x.strip() for x in tail[-4:] if x.strip())[:400]


def extract(meta, s, in_place=False, markers=True, log=noop, progress=noop, cancel=None, use_subject=True, crop_subject=False):
    """Job용. 클립 하나 -> .bvh/.motion.json/.motion.txt. 반환: 결과 dict(경로는 Windows 경로).
    대상이 정해져 있는데 WHAM이 사람을 한 명도 못 찾으면, 대상을 따라가며 확대해서 한 번 더 시도한다."""
    try:
        return _extract_once(meta, s, in_place, markers, log, progress, cancel, use_subject, crop_subject)
    except RuntimeError as e:
        subj = (meta.get("subject") or {}) if use_subject else {}
        if crop_subject or not subj.get("boxes") or "사람이 한 명도" not in str(e):
            raise
        log("모션 AI가 이 화면에서 사람을 찾지 못했어요. 대상을 따라가며 확대해서 다시 시도해요.")
        progress(0.0, "사람을 못 찾아서 대상을 확대해 다시 시도하는 중...")
        return _extract_once(meta, s, in_place, markers, log, progress, cancel, use_subject, True)


def _extract_once(meta, s, in_place=False, markers=True, log=noop, progress=noop, cancel=None, use_subject=True, crop_subject=False):
    st = status()
    if st["level"] != "ok":
        raise RuntimeError(st["message"])
    distro = st["distro"]
    p = _p()
    video = str(file_of(meta, "video"))
    info = media.probe(video)
    if info.duration > MAX_LONG_CLIP:
        log(f"클립이 {info.duration:.0f}초로 긴 편이에요. 오래 걸릴 수 있어요 (짧은 클립을 권장해요).")
    base = Path(meta["_json"]).stem
    out_dir = motion_root(s) / base
    out_dir.mkdir(parents=True, exist_ok=True)
    # 대상 캐릭터(등록한 캐릭터로 골라 둔 추적선)가 있으면 그 사람의 모션만 쓴다
    subject = meta.get("subject") if use_subject else None
    src_video, vsize, boxes = video, (info.w, info.h), None
    crop_tmp = subj_file = None
    if subject and subject.get("boxes"):
        boxes = subject["boxes"]
        log(f"대상 캐릭터: {subject.get('character') or '(직접 고른 사람)'} · 추적선 {subject.get('tid')}번")
        if crop_subject:
            from . import characters
            crop_tmp = out_dir / f"{base}.crop.mp4"
            boxes, vsize, _n = characters.render_follow_crop(video, boxes, crop_tmp, log=log, cancel=cancel)
            src_video = str(crop_tmp)
            in_place = True
            log("대상만 크게 보기: 대상을 따라가며 확대한 영상으로 분석해요. 이동 정보가 정확하지 않아 제자리로 뽑아요.")
        subj_file = out_dir / f"{base}.subject.json"
        subj_file.write_text(json.dumps({"boxes": boxes, "video_size": list(vsize)}), encoding="utf-8")
    seq = "clip_" + time.strftime("%Y%m%d_%H%M%S")
    inp = f"{p['inp']}/{seq}.mp4"
    outdir_w = f"{p['out']}/{seq}"

    progress(0.02, "모션 AI에 영상 보내는 중...")
    wsl.push_file(distro, resource_path("motion_ai/wham2motion.py"), p["conv"])
    rc, tail = wsl.run(distro, f"mkdir -p {wsl.q(p['inp'])} && cp {wsl.q(wsl.win_to_wsl(src_video))} {wsl.q(inp)}", cancel=cancel)
    if rc != 0:
        raise RuntimeError("영상을 WSL로 복사하지 못했어요: " + " / ".join(tail[-3:]))
    try:
        ms = hit_markers(meta, s, log, cancel) if markers else []

        stages = {}

        def on_line(line):
            log(line)
            m = re.search(r"(\d+)\s*/\s*(\d+)", line)
            name = line.split(":")[0].strip()[:20]
            if m and int(m.group(2)) > 0:
                idx = stages.setdefault(name, len(stages))
                frac = 0.06 + 0.78 * min(1.0, (idx + int(m.group(1)) / int(m.group(2))) / 4.0)
                progress(frac, f"모션 AI 분석 중... {name} {m.group(1)}/{m.group(2)}")
            elif line.strip():
                progress(-1, "모션 AI 분석 중... " + line.strip()[:50])

        used, total = gpu_memory(distro)
        if used is not None:
            log(f"그래픽카드 메모리: {used}/{total} MiB 사용 중 (분석 전)")
            if used > GPU_BUSY_MB:
                log(f"⚠ 다른 프로그램(OBS·게임·영상 편집기 등)이 그래픽카드 메모리를 {used}MiB 쓰고 있어요. "
                    "WHAM은 메모리를 많이 써서, 모자라면 극단적으로 느려지거나 멈출 수 있어요. 그 프로그램들을 끄는 걸 권해요.")
                progress(0.05, f"⚠ 그래픽카드 메모리를 다른 프로그램이 {used}MiB 쓰는 중 - 느려질 수 있어요")
        limit = int(min(3600, 900 + info.duration * 90))
        progress(0.06, "모션 AI 분석 중... (처음엔 모델을 불러오느라 1~2분 걸려요)")
        cmd = (f"{_activate()} && cd {wsl.q(p['wham'])} && PYTHONUNBUFFERED=1 timeout {limit} python -u demo.py "
               f"--video {wsl.q(inp)} --output_pth {wsl.q(p['out'])} --save_pkl --estimate_local_only")
        try:
            rc, tail = wsl.run(distro, cmd, log=on_line, cancel=cancel, idle_timeout=IDLE_LIMIT)
        except Cancelled:
            wsl.kill_matching(distro, "demo.py")
            raise
        except wsl.IdleTimeout:
            wsl.kill_matching(distro, "demo.py")
            u2, t2 = gpu_memory(distro)
            raise RuntimeError(f"모션 AI가 {IDLE_LIMIT // 60}분 넘게 멈춰 있어서 중단했어요. "
                               "가장 흔한 원인은 그래픽카드 메모리 부족이에요: OBS·게임·영상 편집기·브라우저 영상을 모두 끄고 다시 해 보세요."
                               + (f" (지금 다른 프로그램이 쓰는 메모리: {u2}MiB)" if u2 is not None else ""))
        if rc == 124:
            wsl.kill_matching(distro, "demo.py")
            raise RuntimeError(f"모션 AI가 제한 시간({limit // 60}분)을 넘겨서 중단했어요. "
                               "다른 프로그램을 끄고 다시 하거나 더 짧은 클립으로 해 보세요.")
        if rc != 0:
            raise RuntimeError(explain_failure(tail))
        pkl = f"{outdir_w}/wham_output.pkl"
        rc, _ = wsl.run(distro, f"test -s {wsl.q(pkl)}")
        if rc != 0:
            raise RuntimeError("WHAM이 결과를 만들지 못했어요. 사람이 크게, 전신이 보이는 클립인지 확인해 주세요. "
                               "(로그 보기에서 자세한 내용을 볼 수 있어요)")

        progress(0.88, "BVH·모션 파일 만드는 중...")
        conv = (f"{_activate()} && python {wsl.q(p['conv'])} {wsl.q(pkl)} --out-dir {wsl.q(wsl.win_to_wsl(out_dir))} "
                f"--name {wsl.q(base)} --fps {info.fps:.4f} --smpl {wsl.q(p['wham'] + '/' + SMPL_NEUTRAL)}"
                + (" --in-place" if in_place else "") + (f" --markers {','.join(str(m) for m in ms)}" if ms else "")
                + (f" --target-boxes {wsl.q(wsl.win_to_wsl(subj_file))} --video-size {vsize[0]}x{vsize[1]}" if subj_file else ""))
        rc, tail = wsl.run(distro, conv, cancel=cancel)
        err = next((l for l in tail if l.startswith("ANIMREF_ERROR")), None)
        res_line = next((l for l in tail if l.startswith("ANIMREF_RESULT ")), None)
        if err or rc != 0 or not res_line:
            raise RuntimeError("변환하지 못했어요: " + (err[len("ANIMREF_ERROR "):] if err else " / ".join(tail[-3:])))
        r = json.loads(res_line.split(" ", 1)[1])

    finally:                                                         # 성공/실패/취소 모두 임시 파일 정리
        try:
            wsl.run(distro, f"rm -rf {wsl.q(inp)} {wsl.q(outdir_w)}")
        except Exception:  # noqa: BLE001
            pass
        for tmp in (crop_tmp, subj_file):
            if tmp is not None:
                try:
                    Path(tmp).unlink(missing_ok=True)
                except OSError:
                    pass
    prepare_scripts(motion_root(s))
    rec = {"dir": str(out_dir), "name": base, "bvh": str(out_dir / f"{base}.bvh"),
           "json": str(out_dir / f"{base}.motion.json"), "txt": str(out_dir / f"{base}.motion.txt"),
           "frames": r["frames"], "fps": r["fps"], "notes": r.get("notes", []), "markers": ms,
           "subject_method": r.get("subject_method", ""), "follow_crop": bool(crop_tmp), "character": (subject or {}).get("character", ""),
           "created": datetime.now().isoformat(timespec="seconds")}
    meta["motion"] = rec
    save_clip(meta)
    progress(1.0, "완료")
    log(f"모션 파일: {rec['bvh']}")
    for n in rec["notes"]:
        log("· " + n)
    return rec


def run_batch(metas, s, in_place=False, markers=True, log=noop, progress=noop, cancel=None, use_subject=True, crop_subject=False):
    """여러 클립을 차례로. 하나가 실패해도 나머지는 계속한다."""
    ok, failed = [], []
    for i, m in enumerate(metas):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        name = Path(m["_json"]).stem
        log(f"── ({i + 1}/{len(metas)}) {name}")
        try:
            ok.append(extract(m, s, in_place, markers, log,
                              lambda f, t, i=i: progress((i + max(0.0, f)) / len(metas) if f >= 0 else -1,
                                                         f"({i + 1}/{len(metas)}) {t}"), cancel, use_subject, crop_subject))
        except Cancelled:
            raise
        except Exception as e:  # noqa: BLE001
            failed.append((name, str(e)))
            log(f"실패: {name}: {e}")
    return {"ok": ok, "failed": failed}


# ---------------------------------------------------------------- Blender / Max
def find_blender():
    """설치된 Blender 실행 파일 (가장 높은 버전). 없으면 ''."""
    p = shutil.which("blender")
    if p:
        return p
    cands = []
    for base in (r"C:\Program Files\Blender Foundation", r"C:\Program Files (x86)\Blender Foundation"):
        cands += glob.glob(base + r"\Blender*\blender.exe")
    cands += glob.glob(r"C:\Program Files\Blender Foundation\Blender*\blender.exe")

    def ver(path):
        m = re.search(r"Blender (\d+)(?:\.(\d+))?", path)
        return (int(m.group(1)), int(m.group(2) or 0)) if m else (0, 0)
    return max(cands, key=ver) if cands else ""


def blender_command(blender_exe, rig_blend, motion_json, save_path, reduce_deg=0.0, markers=True, video=None,
                    in_place=False, map_file=None, armature=None, background=False):
    """Blender로 리그 파일에 모션을 넣고 저장하는 명령. background=True 면 창 없이 실행(결과 글만 받아 옴)."""
    script = resource_path("motion_ai/blender_apply.py")
    cmd = [str(blender_exe)] + (["-b"] if background else []) + [str(rig_blend), "--python", str(script), "--",
                                                                "--motion", str(motion_json), "--save", str(save_path)]
    if reduce_deg and reduce_deg > 0:
        cmd += ["--reduce", f"{reduce_deg:g}"]
    if not markers:
        cmd += ["--no-markers"]
    if video:
        cmd += ["--video", str(video)]
    if in_place:
        cmd += ["--no-move"]
    if map_file:
        cmd += ["--map", str(map_file)]
    if armature:
        cmd += ["--armature", armature]
    return cmd


def blender_save_path(rig_blend, motion_name):
    """원래 리그 파일은 건드리지 않고 옆에 새 이름으로 저장한다."""
    rig = Path(rig_blend)
    return rig.with_name(f"{rig.stem}_{motion_name}.blend")


def apply_with_blender(blender_exe, rig_blend, rec, log=noop, cancel=None, open_after=True, progress=noop, **kw):
    """1) Blender를 창 없이 실행해서 리그에 키를 넣고 새 .blend 로 저장 (뼈 대응 결과/오류를 log 로 받음)
    2) 성공하면 저장된 파일을 Blender 창으로 연다. 원래 리그 파일은 건드리지 않는다. 반환: 저장한 파일 경로."""
    if not Path(blender_exe).exists():
        raise RuntimeError("Blender 실행 파일을 찾을 수 없어요. 경로를 확인해 주세요.")
    if not Path(rig_blend).exists():
        raise RuntimeError("리그 파일(.blend)을 찾을 수 없어요.")
    save = blender_save_path(rig_blend, rec["name"])
    progress(-1, "Blender에서 모션을 넣는 중...")
    cmd = blender_command(blender_exe, rig_blend, rec["json"], save, background=True, **kw)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=wsl.NO_WINDOW)
    error, last = None, []
    import threading
    stop = threading.Event()
    cancelled = threading.Event()

    def watcher():
        while not stop.is_set():
            if cancel is not None and cancel.is_set():
                cancelled.set()
                try:
                    proc.kill()
                except OSError:
                    pass
                return
            stop.wait(0.3)
    threading.Thread(target=watcher, daemon=True).start()
    try:
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            last.append(line)
            if line.startswith("ANIMREF:"):
                msg = line[len("ANIMREF:"):].strip()
                log(msg)
                if msg.startswith("ERROR"):
                    error = msg[len("ERROR"):].strip()
        rc = proc.wait()
    finally:
        stop.set()
        if proc.poll() is None:
            proc.kill()
    if cancelled.is_set():
        raise Cancelled()
    if error or rc != 0 or not save.exists():
        if error:
            raise RuntimeError("Blender에서 모션을 넣지 못했어요: " + error)
        raise RuntimeError("Blender 실행에 실패했어요 (종료 코드 %s): %s" % (rc, " / ".join(x for x in last[-4:] if x.strip())[:300]))
    if open_after:
        subprocess.Popen([str(blender_exe), str(save)])
    return save


MAX_HELP = """AnimRef -> 3ds Max 사용법
=========================
1) 리그를 기본 자세(T자 또는 A자)로 두고, 시간 슬라이더를 0프레임에 둡니다.
2) 리그의 뼈를 하나 선택합니다.
3) Max 메뉴 Scripting > Run Script... 에서 같은 폴더의 max_apply.ms 를 실행합니다.
4) 열린 창에서 [모션 파일 열기] -> {txt} 를 고르고 [적용].

* Biped 도 max_apply.ms 로 넣을 수 있어요(실험): Biped 를 Figure Mode 로 켜 둔 기본 자세에서 [적용]. 손발이 어색하면
  Motion 패널의 IK Blend 를 조절하거나, 안 되면 {bvh} 를 Biped 의 Motion Capture 로 불러오세요.
* 뼈 이름을 못 알아보면 motion.txt 와 같은 폴더에 animref_map.txt 를 만들어 '역할=내뼈이름' 을 한 줄씩 적으세요.
  역할: hips, spine_0, spine_1, spine_2, neck, head, shoulder_L/R, upperarm_L/R, forearm_L/R, hand_L/R,
        thigh_L/R, shin_L/R, foot_L/R, toe_L/R
* max_apply.ms 는 3ds Max 없는 환경에서 작성되어 실행 시험을 못 했어요. 오류가 나면 Listener(F11) 내용을 알려 주세요.
"""


def prepare_max_folder(s, rec):
    """Max용 파일(.motion.txt, .bvh, max_apply.ms, 사용법)이 모인 폴더를 돌려준다."""
    prepare_scripts(motion_root(s))
    help_txt = Path(rec["dir"]) / "Max_사용법.txt"
    help_txt.write_text(MAX_HELP.format(txt=Path(rec["txt"]).name, bvh=Path(rec["bvh"]).name), encoding="utf-8")
    return Path(rec["dir"])
