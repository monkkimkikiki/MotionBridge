"""운영체제의 파일 탐색기로 폴더/파일 위치를 연다 (Qt 없이)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import List, Optional


def folder_of(path: str) -> str:
    """파일이면 그 폴더, 폴더면 자기 자신."""
    return path if os.path.isdir(path) else os.path.dirname(path)


def open_command(path: str, platform: Optional[str] = None) -> List[str]:
    """path(파일이면 탐색기에서 그 파일을 선택, 폴더면 그 폴더를 연다)를 여는 명령. Windows 의 startfile 은 명령이 없으므로 [] 를 준다."""
    pf = platform or sys.platform
    if pf.startswith("win"):
        if os.path.isfile(path):
            return ["explorer", "/select,", os.path.normpath(path)]
        return []
    if pf == "darwin":
        return ["open", "-R", path] if os.path.isfile(path) else ["open", path]
    return ["xdg-open", folder_of(path)]


def open_in_explorer(path: str) -> bool:
    """열기에 성공했으면 True. 없는 경로면 가장 가까운 상위 폴더를 연다."""
    p = path
    while p and not os.path.exists(p):
        up = os.path.dirname(p)
        if up == p:
            return False
        p = up
    if not p:
        return False
    try:
        cmd = open_command(p)
        if cmd:
            subprocess.Popen(cmd)
        elif hasattr(os, "startfile"):
            os.startfile(p)  # type: ignore[attr-defined]
        else:
            return False
        return True
    except OSError:
        return False


def chrome_command(url: str, platform: Optional[str] = None, env: Optional[dict] = None, exists=os.path.isfile) -> Optional[List[str]]:
    """Chrome 으로 url 을 여는 명령. Chrome 을 못 찾으면 None."""
    pf = platform or sys.platform
    e = env if env is not None else os.environ
    if pf.startswith("win"):
        for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = e.get(k)
            if base:
                c = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
                if exists(c):
                    return [c, url]
        return None
    if pf == "darwin":
        return ["open", "-a", "Google Chrome", url]
    for n in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        w = shutil.which(n)
        if w:
            return [w, url]
    return None


def open_url(url: str) -> bool:
    """웹 링크(http/https)를 Chrome 으로 연다. Chrome 이 없으면 False (호출한 쪽이 기본 브라우저로 넘긴다)."""
    if not url.lower().startswith(("http://", "https://")):
        return False
    cmd = chrome_command(url)
    if not cmd:
        return False
    try:
        subprocess.Popen(cmd)
        return True
    except OSError:
        return False
