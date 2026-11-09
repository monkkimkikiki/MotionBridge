"""직접 자르기용 원본 영상 찾기: OBS 녹화 폴더, 분석했던 원본, 분석 목록에 있는 영상을 모은다."""
import hashlib
import os
from datetime import datetime
from pathlib import Path

from . import media, titles
from .library import file_of, game_dir, load_clips
from .settings import app_dir

MAX_PER_DIR = 300


def videos_folder():
    for p in (Path.home() / "Videos", Path.home() / "동영상"):
        if p.is_dir():
            return p
    return Path.home()


def candidate_dirs(s, extra=()):
    """원본이 있을 만한 폴더 (우선순위 순)."""
    dirs = []
    for d in [s.get("obs_record_dir"), *extra, s.get("last_dir"), str(videos_folder())]:
        if d and Path(d).is_dir() and str(Path(d)) not in dirs:
            dirs.append(str(Path(d)))
    return dirs


def best_start_dir(s, sources=()):
    """파일 선택 창을 처음 열 폴더."""
    for d in candidate_dirs(s):
        return d
    for src in sources:
        if Path(src["path"]).parent.is_dir():
            return str(Path(src["path"]).parent)
    return str(Path.home())


def list_sources(s, queue_paths=()):
    """[{path, name, mtime, size, clips, marks, thumb, where}] 최근 순."""
    from . import bookmarks

    found = {}

    def add(p, where):
        p = Path(p)
        if p.suffix.lower() not in media.VIDEO_EXTS or not p.is_file():
            return
        key = os.path.normcase(str(p.resolve()))
        if key not in found:
            st = p.stat()
            found[key] = {"path": str(p), "name": p.name, "mtime": st.st_mtime, "size": st.st_size,
                          "clips": 0, "marks": 0, "thumb": "", "where": where}

    for q in queue_paths:
        add(q, "분석 목록")
    clips = load_clips(game_dir(s))
    for m in clips:
        if m.get("source_video"):
            add(m["source_video"], "분석한 영상")
    obs_dir = s.get("obs_record_dir")
    for d in candidate_dirs(s):
        try:
            files = sorted(Path(d).iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)[:MAX_PER_DIR]
        except OSError:
            continue
        where = "OBS 녹화 폴더" if obs_dir and Path(d) == Path(obs_dir) else "폴더"
        for f in files:
            add(f, where)

    by_name = {}
    for m in clips:
        by_name.setdefault(m.get("source_name"), []).append(m)
    for src in found.values():
        mine = by_name.get(src["name"], [])
        src["clips"] = len(mine)
        thumb = next((str(file_of(m, "thumb")) for m in mine if file_of(m, "thumb").exists()), "")
        try:
            bm = bookmarks.load(s, src["path"])
            vis = [b for b in bm["items"] if not b.get("hidden")]
            src["marks"] = len(vis)
            if not thumb:
                thumb = next((b["thumb"] for b in vis if b.get("thumb") and Path(b["thumb"]).exists()), "")
        except Exception:
            pass
        src["thumb"] = thumb
        src["title"] = titles.title_of(src["path"])
    return sorted(found.values(), key=lambda x: x["mtime"], reverse=True)


def cached_thumb(path):
    """목록용 썸네일 (없으면 영상 10% 지점에서 만들어 캐시)."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return ""
    key = hashlib.md5(f"{p.resolve()}|{st.st_mtime}|{st.st_size}".encode("utf-8")).hexdigest()[:16]
    d = app_dir() / "thumb_cache"
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{key}.jpg"
    if out.exists():
        return str(out)
    try:
        dur = media.probe(p).duration
    except Exception:
        dur = 0
    return str(out) if media.thumbnail(p, dur * 0.1 if dur else 1.0, out, width=240) else ""


def describe(src):
    t = datetime.fromtimestamp(src["mtime"]).strftime("%Y-%m-%d %H:%M")
    gb = src["size"] / 1024 ** 3
    size = f"{gb:.1f}GB" if gb >= 1 else f"{src['size'] / 1024 ** 2:.0f}MB"
    info = [t, size, src["where"]]
    if src["clips"]:
        info.append(f"클립 {src['clips']}개")
    if src["marks"]:
        info.append(f"책갈피 {src['marks']}개")
    return "  ·  ".join(info)
