"""원본 영상 제목(표시 이름)과 클립 표시 이름.

영상 파일 이름은 OBS 가 붙인 날짜 같은 것이라 알아보기 어렵다. 그래서 파일은 그대로 두고, 사용자가 붙인 제목을 따로 기억한다
(앱 데이터의 source_titles.json). 클립은 '제목 00:00:00~00:00:10' 처럼 제목 + 원본에서의 위치로 보여 준다.
"""
import json
import os
from pathlib import Path

from .settings import app_dir


def _file() -> Path:
    return app_dir() / "source_titles.json"


def _key(path) -> str:
    try:
        return os.path.normcase(str(Path(path).resolve()))
    except OSError:
        return os.path.normcase(str(path))


def _load() -> dict:
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def title_of(path) -> str:
    """사용자가 붙인 제목 (없으면 빈 문자열)."""
    return str(_load().get(_key(path), "") or "")


def set_title(path, title) -> None:
    """제목을 정한다. 비우면 기본(파일 이름)으로 돌아간다."""
    d = _load()
    t = (title or "").strip()
    if t:
        d[_key(path)] = t
    else:
        d.pop(_key(path), None)
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)


def display(path) -> str:
    """목록에 보여 줄 영상 제목: 붙인 제목, 없으면 확장자를 뺀 파일 이름."""
    return title_of(path) or Path(str(path)).stem


def hms(sec) -> str:
    sec = max(0, int(round(float(sec or 0))))
    return "%02d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60)


def clip_title(meta) -> str:
    """클립 표시 이름: '<영상 제목> 00:00:00~00:00:10' (원본에서의 시작~끝)."""
    src = meta.get("source_video") or meta.get("source_name") or ""
    base = display(src) if src else "클립"
    a = float(meta.get("start_in_source", 0.0) or 0.0)
    b = a + float(meta.get("duration", 0.0) or 0.0)
    return "%s %s~%s" % (base, hms(a), hms(b))
