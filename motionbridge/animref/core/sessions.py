"""녹화 세션: 녹화를 시작했다가 끝낸 한 번 = 세션 하나. 클립은 원본 녹화(source_video)로 세션에 묶인다.

라이브러리는  날짜 > 세션(시작~종료 시각) > 전체 / 분류별  로 보여 준다. 날짜·세션 이름은 사용자가 바꿀 수 있고(이름만 바뀌고 클립 파일은 그대로),
이 정보는 게임 폴더의 sessions.json 에 저장된다. 원본 영상을 지워도 세션 정보는 남는다.
"""
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from .library import game_dir

FILE = "sessions.json"
# OBS 기본 파일 이름: 2026-11-09 14-03-22.mkv  /  2026-11-09_14-03-22  /  20261109_140322
_TS = [re.compile(r"(\d{4})-(\d{2})-(\d{2})[ _T](\d{2})[-:.](\d{2})[-:.](\d{2})"), re.compile(r"(\d{4})(\d{2})(\d{2})[ _-]?(\d{2})(\d{2})(\d{2})")]


def parse_name_time(name: str) -> Optional[datetime]:
    for rx in _TS:
        m = rx.search(name)
        if m:
            try:
                return datetime(*(int(x) for x in m.groups()))
            except ValueError:
                continue
    return None


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def key_of(meta_or_path) -> str:
    """세션 키 = 원본 녹화 파일 경로 (없으면 원본 이름, 그것도 없으면 'unknown')."""
    if isinstance(meta_or_path, dict):
        m = meta_or_path
        return str(m.get("source_video") or m.get("source_name") or "unknown").replace("\\", "/")
    return str(meta_or_path).replace("\\", "/")


def probe_times(path, duration: float = 0.0) -> Optional[Dict[str, str]]:
    """원본 파일에서 녹화 시작/종료 시각을 알아낸다. 파일이 없으면 None."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    end = datetime.fromtimestamp(st.st_mtime)
    start = parse_name_time(p.name)  # 파일 이름의 시각(OBS 기본)이 가장 정확하다
    if start is not None and duration and duration > 0 and start + timedelta(seconds=duration) > end + timedelta(minutes=2):
        start = None  # 이름의 시각이 맞지 않으면(파일을 옮겨 시각이 바뀜 등) 쓰지 않는다
    if start is None and duration and duration > 0:
        start = end - timedelta(seconds=duration)  # 이름에 시각이 없으면 종료 시각 - 길이
    if start is None:
        try:
            start = datetime.fromtimestamp(st.st_ctime)
        except (OSError, ValueError):
            start = end
    if start > end:
        start = end
    return {"start": _iso(start), "end": _iso(end)}


class Sessions:
    def __init__(self, gdir) -> None:
        self.path = Path(gdir) / FILE
        self.data: Dict[str, dict] = {"sessions": {}, "date_names": {}}
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                self.data["sessions"] = dict(d.get("sessions", {}))
                self.data["date_names"] = dict(d.get("date_names", {}))
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ---- 기록
    def ensure(self, meta: dict, duration: float = 0.0, now: Optional[datetime] = None) -> dict:
        """클립이 속한 세션 정보를 돌려준다 (없으면 만들어 저장 대상에 올린다. save() 는 호출한 쪽에서)."""
        k = key_of(meta)
        rec = self.data["sessions"].get(k)
        if rec and rec.get("start"):
            return rec
        rec = dict(rec or {})
        t = probe_times(meta.get("source_video") or "", duration or float(meta.get("source_duration") or 0))
        if t is None:  # 원본이 없다: 클립을 만든 시각으로 대신한다
            c = meta.get("created") or _iso(now or datetime.now())
            t = {"start": c, "end": c, "approx": True}
        rec.update(t)
        rec.setdefault("source", meta.get("source_name") or Path(k).name)
        rec.setdefault("kind", "manual" if meta.get("manual") else "auto")
        self.data["sessions"][k] = rec
        return rec

    def rename_session(self, key: str, name: str) -> None:
        rec = self.data["sessions"].setdefault(key, {})
        if name.strip():
            rec["name"] = name.strip()
        else:
            rec.pop("name", None)

    def rename_date(self, date: str, name: str) -> None:
        if name.strip():
            self.data["date_names"][date] = name.strip()
        else:
            self.data["date_names"].pop(date, None)

    def set_times(self, key: str, start: str, end: str) -> None:
        rec = self.data["sessions"].setdefault(key, {})
        rec["start"], rec["end"] = start, end
        rec.pop("approx", None)


def hhmm(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M:%S")
    except (TypeError, ValueError):
        return "?"


def date_of(iso: str) -> str:
    return (iso or "")[:10] or "날짜 모름"


def build_tree(clips: List[dict], sess: Sessions, label_order: List[str], final_label) -> List[dict]:
    """클립 -> [{date, date_name, sessions:[{key, name, start, end, number, clips, labels:{라벨:[클립]}}]}] (최신 날짜 먼저, 세션은 시작 시각순)."""
    by_key: Dict[str, dict] = {}
    for m in clips:
        rec = sess.ensure(m)
        k = key_of(m)
        s = by_key.setdefault(k, {"key": k, "rec": rec, "clips": []})
        s["clips"].append(m)
    days: Dict[str, List[dict]] = {}
    for k, s in by_key.items():
        days.setdefault(date_of(s["rec"].get("start", "")), []).append(s)
    out = []
    for date in sorted(days, reverse=True):
        ss = sorted(days[date], key=lambda s: s["rec"].get("start", ""))
        sessions = []
        for n, s in enumerate(ss, 1):
            rec = s["rec"]
            labels: Dict[str, List[dict]] = {}
            for m in s["clips"]:
                labels.setdefault(final_label(m), []).append(m)
            ordered = {l: labels[l] for l in label_order if l in labels}
            for l in sorted(labels):
                ordered.setdefault(l, labels[l])
            default = "녹화 세션 %d" % n
            sessions.append({"key": s["key"], "number": n, "name": rec.get("name") or default, "custom": bool(rec.get("name")),
                             "start": rec.get("start", ""), "end": rec.get("end", ""), "approx": bool(rec.get("approx")),
                             "clips": s["clips"], "labels": ordered})
        out.append({"date": date, "date_name": sess.data["date_names"].get(date) or date, "sessions": sessions,
                    "count": sum(len(x["clips"]) for x in sessions)})
    return out


def session_title(s: dict) -> str:
    span = "%s ~ %s" % (hhmm(s["start"]), hhmm(s["end"])) if s.get("start") else "시각 모름"
    if s.get("approx"):
        span += " (추정)"
    return "%s  (%s · 클립 %d개)" % (s["name"], span, len(s["clips"]))


def record(gdir, meta: dict, duration: float = 0.0) -> None:
    """클립을 만들 때 호출: 원본 녹화의 시작/종료 시각을 지금(원본이 있을 때) 기록해 둔다."""
    try:
        ss = Sessions(gdir)
        had = key_of(meta) in ss.data["sessions"]
        ss.ensure(meta, duration)
        if not had:
            ss.save()
    except Exception:  # noqa: BLE001 (세션 기록 때문에 클립 만들기가 실패하면 안 된다)
        pass


def matches_scope(meta: dict, scope, final_label) -> bool:
    """scope = None(전체) 또는 (날짜, 세션키 또는 None, 분류 또는 None)."""
    if not scope:
        return True
    date, key, label = scope
    rec_date = meta.get("_session_date")
    if date and rec_date != date:
        return False
    if key and key_of(meta) != key:
        return False
    if label and final_label(meta) != label:
        return False
    return True
