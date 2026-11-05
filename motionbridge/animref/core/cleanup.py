"""분석이 끝난 원본 영상 정리: 그대로 두기 / 휴지통으로 / 완전히 삭제.
휴지통은 Windows 기본 기능(SHFileOperationW)을 써서 추가 패키지가 필요 없다."""
import os
import sys
from pathlib import Path

KEEP, TRASH, DELETE = "keep", "trash", "delete"
POLICY_NAMES = {KEEP: "그대로 두기", TRASH: "휴지통으로 옮기기", DELETE: "완전히 삭제"}


def send_to_trash(path) -> bool:
    p = str(Path(path).resolve())
    if sys.platform != "win32":
        return False  # 이 프로그램은 Windows용. 다른 OS에서는 원본을 남긴다.
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = p + "\0"  # 목록은 null 두 개로 끝나야 한다 (ctypes가 하나를 더 붙임)
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return rc == 0 and not op.fAnyOperationsAborted and not os.path.exists(p)


def clips_ok(json_paths) -> bool:
    """남아 있는 클립(자동 삭제되지 않은 것)이 1개 이상 있고, 모두 정상 저장됐는지 확인."""
    alive = [jp for jp in (json_paths or []) if Path(jp).exists()]
    if not alive:
        return False
    for jp in alive:
        mp4 = Path(jp).with_suffix(".mp4")
        if not mp4.exists() or mp4.stat().st_size < 1000:
            return False
    return True


def apply(path, policy, clip_jsons, log=print):
    """조건을 모두 만족할 때만 원본을 정리한다. 정리했으면 True."""
    name = Path(path).name
    if policy not in (TRASH, DELETE):
        return False
    if not clips_ok(clip_jsons):
        log(f"  원본 보존: {name} (만들어진 클립이 없거나 확인되지 않음)")
        return False
    if not Path(path).exists():
        return False
    try:
        if policy == TRASH:
            if not send_to_trash(path):
                log(f"  원본 보존: {name} (휴지통으로 옮기지 못함 — 다른 프로그램이 사용 중일 수 있어요)")
                return False
            log(f"  원본을 휴지통으로 옮김: {name}")
        else:
            Path(path).unlink()
            log(f"  원본 삭제: {name}")
        return True
    except OSError as e:
        log(f"  원본 보존: {name} ({e})")
        return False
