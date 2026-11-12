"""내보낸 MaxScript(.ms)를 3ds Max 로 자동 실행한다 (Windows 전용).

우선순위: 사용자가 지정한 실행 파일 > 3dsmaxbatch.exe(화면 없이 실행) > 3dsmax.exe(-silent, 끝나면 스스로 종료).
스크립트는 결과를 로그 파일에 쓰고(`SAVED: <경로>` 줄), 이 모듈이 그 로그를 읽어 돌려준다.
※ 실제 3ds Max 가 없는 환경에서는 실행해 볼 수 없다 — 명령줄 조립과 로그 해석만 시험된다.
"""
from __future__ import annotations

import glob
import os
import subprocess
from typing import Callable, Dict, List, Optional, Sequence

BATCH = "3dsmaxbatch.exe"
GUI = "3dsmax.exe"


def find_max(preferred: str = "", environ: Optional[Dict[str, str]] = None, globber: Callable[[str], List[str]] = glob.glob,
             exists: Callable[[str], bool] = os.path.isfile) -> str:
    """3ds Max 실행 파일 경로(없으면 ""). preferred 가 폴더면 그 안에서 찾는다."""
    env = os.environ if environ is None else environ
    cands: List[str] = []
    if preferred:
        if exists(preferred):
            return preferred
        for n in (BATCH, GUI):
            cands.append(os.path.join(preferred, n))
    for k, v in sorted(env.items(), reverse=True):          # Autodesk 설치기가 ADSK_3DSMAX_x64_2025 같은 변수를 만든다
        if k.upper().startswith("ADSK_3DSMAX") and v:
            for n in (BATCH, GUI):
                cands.append(os.path.join(v, n))
    for pf in (env.get("ProgramFiles", ""), "C:\\Program Files"):
        if pf:
            for n in (BATCH, GUI):
                cands += sorted(globber(os.path.join(pf, "Autodesk", "3ds Max *", n)), reverse=True)
    for c in cands:
        if exists(c):
            return c
    return ""


def is_batch(exe: str) -> bool:
    return exe.replace("\\", "/").rsplit("/", 1)[-1].lower().startswith("3dsmaxbatch")


def build_command(exe: str, script: str) -> List[str]:
    if is_batch(exe):
        return [exe, script]
    return [exe, "-q", "-silent", "-U", "MAXScript", script]


def parse_log(text: str) -> Dict[str, object]:
    """스크립트가 쓴 로그 -> {"saved": [경로], "warnings": [⚠ 줄], "text": 전체}"""
    saved, warns = [], []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("SAVED:"):
            saved.append(s[len("SAVED:"):].strip())
        elif s.startswith("⚠"):
            warns.append(s)
    return {"saved": saved, "warnings": warns, "text": text}


def read_log(path: str) -> str:
    """MaxScript 의 format to:file 은 UTF-8 이 아니라 시스템 ANSI(한국어 Windows 는 cp949)로 쓰는 경우가 있다."""
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "cp949", "mbcs" if os.name == "nt" else "cp949"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def make_wrapper(script: str, quit_after: bool) -> str:
    """자동 실행 전용 임시 .ms: 메시지 창을 끄고(mbAutoOverride) 본 스크립트를 실행한다. 3ds Max 를 직접 띄운 경우에만 끝나고 종료.

    내보낸 *_retarget.ms 자체에는 이 설정이 없다 — 사용자가 그 파일을 열린 3ds Max 에서 손으로 실행해도 3ds Max 가 꺼지지 않는다."""
    import tempfile
    p = script.replace("\\", "/").replace('"', "")
    fd, path = tempfile.mkstemp(prefix="mb_auto_", suffix=".ms")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("global mbAutoOverride = true\nglobal mbQuitOverride = %s\nfileIn \"%s\"\nmbAutoOverride = false\nmbQuitOverride = false\n"
                % ("true" if quit_after else "false", p))
    return path


def run(exe: str, script: str, log: str, timeout: float = 900.0, runner: Callable = subprocess.run) -> Dict[str, object]:
    """3ds Max 로 script 를 실행. 반환: {"ok", "saved", "warnings", "log", "error"}. 로그는 실행 전에 지운다."""
    out: Dict[str, object] = {"ok": False, "saved": [], "warnings": [], "log": "", "error": ""}
    try:
        if os.path.exists(log):
            os.remove(log)
    except OSError:
        pass
    cmd = build_command(exe, script)
    try:
        cp = runner(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        out["error"] = "3ds Max 가 %d초 안에 끝나지 않았습니다 (대화 상자가 떠 있을 수 있습니다)." % int(timeout)
        return out
    except (OSError, ValueError) as e:
        out["error"] = "3ds Max 를 실행하지 못했습니다: %s" % e
        return out
    text = ""
    if os.path.exists(log):
        text = read_log(log)
    info = parse_log(text)
    out.update(saved=info["saved"], warnings=info["warnings"], log=text)
    if cp.returncode not in (0, None) and not info["saved"]:
        tail = (getattr(cp, "stderr", b"") or b"")[-400:].decode("utf-8", "replace") if isinstance(getattr(cp, "stderr", b""), (bytes, bytearray)) else str(cp.stderr)[-400:]
        out["error"] = "3ds Max 가 오류 코드 %s 로 끝났습니다. %s" % (cp.returncode, tail.strip())
    elif not info["saved"]:
        out["error"] = "3ds Max 가 끝났지만 저장 결과가 로그에 없습니다 (스크립트가 중간에 멈춘 것 같습니다). 로그: %s" % (text.strip()[-400:] or "(비어 있음)")
    else:
        out["ok"] = True
    return out
