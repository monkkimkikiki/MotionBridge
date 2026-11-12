"""추출기(mbextract)만 따로 업데이트 (Qt 없이).

추출기가 어떤 게임을 읽을 수 있는지는 그 안의 CUE4Parse(오픈소스)가 정한다. CUE4Parse 는 새 게임 지원이 계속 추가되므로,
GitHub 가 매주 최신 CUE4Parse 로 추출기를 다시 빌드해 'mbextract-latest' 릴리스에 올리고(.github/workflows/mbextract-weekly.yml),
앱은 그 파일만 받아 바꿔 끼운다 — 앱 전체를 다시 설치하지 않아도 새 게임 지원이 따라온다.

받은 파일은 <설정 폴더>/helper/<버전>/ 에 풀고, 실제로 실행해 '지원 게임 목록'이 나오는지 확인한 뒤에만 쓴다.
확인에 실패하면 지금 쓰던 추출기를 그대로 둔다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.request
import zipfile
from typing import Callable, List, Optional

from . import bridge

REPO = "monkkimkikiki/MotionBridge"
TAG = "mbextract-latest"
API_URL = "https://api.github.com/repos/%s/releases/tags/%s" % (REPO, TAG)
ZIP_NAME = "mbextract-win-x64.zip"
INFO_NAME = "mbextract.json"
EXE_NAME = "mbextract.exe"


class UpdateError(RuntimeError):
    pass


def helper_root() -> str:
    return os.path.join(bridge.CONFIG_DIR, "helper")


def _http(url: str, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "MotionBridge", "Accept": "application/octet-stream, application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:     # noqa: S310 - 고정된 GitHub 주소만 연다
        return r.read()


def latest_info(fetch: Callable[[str], bytes] = _http) -> dict:
    """올라와 있는 최신 추출기 정보: {version, cue4parse, built, games, zip_url}. 못 읽으면 UpdateError."""
    try:
        rel = json.loads(fetch(API_URL).decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise UpdateError("업데이트 정보를 받지 못했습니다 (인터넷 연결 또는 GitHub 접속 확인): %s" % e) from e
    assets = {a.get("name"): a.get("browser_download_url") for a in (rel.get("assets") or []) if isinstance(a, dict)}
    if ZIP_NAME not in assets:
        raise UpdateError("아직 올라온 추출기가 없습니다 (주간 자동 빌드가 한 번 돌면 생깁니다).")
    info: dict = {}
    if INFO_NAME in assets:
        try:
            info = json.loads(fetch(assets[INFO_NAME]).decode("utf-8-sig"))
        except Exception:  # noqa: BLE001 - 정보 파일은 없어도 받을 수는 있다
            info = {}
    info = info if isinstance(info, dict) else {}
    info.setdefault("version", str(rel.get("name") or rel.get("published_at") or "unknown"))
    info["zip_url"] = assets[ZIP_NAME]
    return info


def installed_info(helper_path: Optional[str]) -> dict:
    """지금 쓰는 추출기 옆의 mbextract.json (업데이트로 받은 것에만 있다). 없으면 {}."""
    if not helper_path:
        return {}
    try:
        with open(os.path.join(os.path.dirname(helper_path), INFO_NAME), "r", encoding="utf-8-sig") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _safe_dirname(v: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(v))[:60] or "latest"


def _list_games(exe: str) -> List[str]:
    kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
    r = subprocess.run([exe, "list-games"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, **kw)
    for line in (r.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("["):
            return [str(x) for x in json.loads(line) if isinstance(x, str)]
    return []


def install(info: dict, fetch: Callable[[str], bytes] = _http, verify: Callable[[str], List[str]] = _list_games, root: Optional[str] = None) -> dict:
    """info(latest_info 결과)의 추출기를 받아 풀고 확인한다. {path, version, games} 를 돌려준다. 실패하면 UpdateError (쓰던 것은 그대로)."""
    root = root or helper_root()
    ver = _safe_dirname(info.get("version") or "latest")
    dest = os.path.join(root, ver)
    tmp = dest + ".part"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp, exist_ok=True)
    try:
        try:
            data = fetch(info["zip_url"])
        except Exception as e:  # noqa: BLE001
            raise UpdateError("추출기 파일을 받지 못했습니다: %s" % e) from e
        zpath = os.path.join(tmp, ZIP_NAME)
        with open(zpath, "wb") as f:
            f.write(data)
        try:
            with zipfile.ZipFile(zpath) as z:
                base = os.path.abspath(tmp)
                for n in z.namelist():           # 압축 안의 경로가 폴더 밖으로 나가지 못하게
                    if not os.path.abspath(os.path.join(tmp, n)).startswith(base + os.sep):
                        raise UpdateError("받은 파일이 올바르지 않습니다 (경로 이상).")
                z.extractall(tmp)
        except zipfile.BadZipFile as e:
            raise UpdateError("받은 파일이 깨졌습니다. 다시 시도하세요.") from e
        os.remove(zpath)
        exe = os.path.join(tmp, EXE_NAME)
        if not os.path.isfile(exe):
            raise UpdateError("받은 파일 안에 %s 가 없습니다." % EXE_NAME)
        try:
            games = verify(exe)
        except Exception as e:  # noqa: BLE001
            raise UpdateError("받은 추출기가 실행되지 않습니다: %s" % e) from e
        if not games:
            raise UpdateError("받은 추출기가 지원 게임 목록을 내놓지 않습니다. 쓰던 추출기를 그대로 둡니다.")
        with open(os.path.join(tmp, INFO_NAME), "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in info.items() if k != "zip_url"}, f, ensure_ascii=False, indent=1)
        shutil.rmtree(dest, ignore_errors=True)
        os.replace(tmp, dest)
        return {"path": os.path.join(dest, EXE_NAME), "version": info.get("version"), "games": games}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def new_games(old: List[str], new: List[str]) -> List[str]:
    """업데이트로 새로 생긴 게임 전용 항목."""
    o = set(old or [])
    return [g for g in (new or []) if g not in o and g.startswith("GAME_")]
