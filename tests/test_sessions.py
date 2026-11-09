"""라이브러리: 날짜 > 녹화 세션 > 전체/분류 (원본 녹화 기준)."""
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from motionbridge.animref.core import library, sessions as S  # noqa: E402

fails = []


def check(c, m):
    if not c:
        fails.append(m)
        print("FAIL", m)


check(S.parse_name_time("2026-11-09 14-03-22.mkv") == datetime(2026, 11, 9, 14, 3, 22), "OBS 기본 파일 이름")
check(S.parse_name_time("rec_20261109_140322.mp4") == datetime(2026, 11, 9, 14, 3, 22), "붙여 쓴 시각")
check(S.parse_name_time("boss.mp4") is None, "시각 없는 이름")


def mk(d, name, video, label, created="2026-11-09T15:00:00", manual=None):
    cdir = Path(d) / "clips"
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / (name + ".mp4")).write_bytes(b"x")
    meta = {"source_video": video, "source_name": Path(video).name, "label_manual": label, "ai_label": None, "created": created, "group": None}
    (cdir / (name + ".json")).write_text(json.dumps(meta), encoding="utf-8")


with tempfile.TemporaryDirectory() as d:
    v1 = os.path.join(d, "2026-11-09 14-03-22.mkv")
    v2 = os.path.join(d, "2026-11-09 16-10-00.mkv")
    v3 = os.path.join(d, "2026-11-08 21-00-00.mkv")
    for v, mt in ((v1, datetime(2026, 11, 9, 14, 25, 10)), (v2, datetime(2026, 11, 9, 16, 40, 0)), (v3, datetime(2026, 11, 8, 21, 30, 0))):
        open(v, "wb").write(b"v")
        ts = mt.timestamp()
        os.utime(v, (ts, ts))
    gdir = Path(d) / "game"
    mk(gdir, "a1", v1, "타격")
    mk(gdir, "a2", v1, "타격")
    mk(gdir, "a3", v1, "피격")
    mk(gdir, "b1", v2, "피격")
    mk(gdir, "c1", v3, "타격")
    mk(gdir, "gone", os.path.join(d, "삭제된 영상.mp4"), "타격", created="2026-11-07T10:00:00")  # 원본이 없는 클립
    clips = library.load_clips(gdir)
    check(len(clips) == 6, "클립 6개 읽음")
    ss = S.Sessions(gdir)
    tree = S.build_tree(clips, ss, ["타격", "피격"], library.final_label)
    dates = [t["date"] for t in tree]
    check(dates == ["2026-11-09", "2026-11-08", "2026-11-07"], "최신 날짜 먼저: %s" % dates)
    day = tree[0]
    check(day["count"] == 4 and [x["number"] for x in day["sessions"]] == [1, 2] and day["sessions"][0]["name"] == "녹화 세션 1", "하루 두 세션, 시작 시각순 번호")
    s1 = day["sessions"][0]
    check(s1["start"].startswith("2026-11-09T14:03:22") and s1["end"].startswith("2026-11-09T14:25:10"), "시작/종료 시각: %s %s" % (s1["start"], s1["end"]))
    check(list(s1["labels"]) == ["타격", "피격"] and len(s1["labels"]["타격"]) == 2 and len(s1["clips"]) == 3, "세션 안 분류 + 전체")
    check("14:03:22 ~ 14:25:10" in S.session_title(s1) and "클립 3개" in S.session_title(s1), "제목: " + S.session_title(s1))
    gone = tree[2]["sessions"][0]
    check(gone["approx"] and "추정" in S.session_title(gone), "원본 없으면 클립 만든 시각으로 추정 표시")

    # 이름 바꾸기 + 저장
    ss.rename_session(s1["key"], "보스전 1트")
    ss.rename_date("2026-11-09", "11월 9일 플레이")
    ss.save()
    ss2 = S.Sessions(gdir)
    t2 = S.build_tree(clips, ss2, ["타격", "피격"], library.final_label)
    check(t2[0]["date_name"] == "11월 9일 플레이" and t2[0]["sessions"][0]["name"] == "보스전 1트" and t2[0]["sessions"][0]["custom"], "이름은 저장되고 다시 읽힘")
    ss2.rename_session(s1["key"], "")
    check(S.build_tree(clips, ss2, [], library.final_label)[0]["sessions"][0]["name"] == "녹화 세션 1", "비우면 기본 이름")

    # 원본을 지워도 기록은 남는다
    ss.ensure(clips[0])
    ss.save()
    os.remove(v1)
    t3 = S.build_tree(library.load_clips(gdir), S.Sessions(gdir), [], library.final_label)
    s1b = [x for x in t3[0]["sessions"] if x["key"] == s1["key"]][0]
    check(s1b["start"].startswith("2026-11-09T14:03:22") and not s1b["approx"], "원본 삭제 후에도 시각 유지")

    # 범위 거르기
    for m in clips:
        m["_session_date"] = S.date_of(ss.ensure(m)["start"])
    key1 = S.key_of(v1)
    sel = [m for m in clips if S.matches_scope(m, ("2026-11-09", key1, "타격"), library.final_label)]
    check(len(sel) == 2 and all(library.final_label(m) == "타격" for m in sel), "세션+분류 범위")
    check(len([m for m in clips if S.matches_scope(m, ("2026-11-09", None, None), library.final_label)]) == 4, "날짜 범위")
    check(len([m for m in clips if S.matches_scope(m, None, library.final_label)]) == 6, "범위 없음 = 전체")

    # 내보내기: 날짜 / 세션 / 분류
    out = os.path.join(d, "out")
    sx = S.Sessions(gdir)
    sx.rename_session(key1, "첫 보스")
    library.export(clips, out, log=lambda *a: None, sess=sx)
    got = sorted(str(p.relative_to(out)).replace("\\", "/") for p in Path(out).rglob("*.mp4"))
    check(any(g.startswith("11월_9일_플레이/첫_보스/타격/") for g in got) and any("/녹화_세션_2/피격/" in g for g in got), "내보내기 폴더: %s" % got[:4])

# 클립 만들 때 기록
with tempfile.TemporaryDirectory() as d:
    v = os.path.join(d, "2026-11-09 10-00-00.mkv")
    open(v, "wb").write(b"v")
    _t = datetime(2026, 11, 9, 10, 10, 0).timestamp()
    os.utime(v, (_t, _t))
    S.record(d, {"source_video": v, "source_name": "x"}, 600.0)
    j = json.loads((Path(d) / "sessions.json").read_text(encoding="utf-8"))
    check(S.key_of(v) in j["sessions"] and j["sessions"][S.key_of(v)]["start"].startswith("2026-11-09T10:00:00"), "클립 만들 때 세션 기록")

print("모두 통과" if not fails else "실패 %d개" % len(fails))
sys.exit(1 if fails else 0)
