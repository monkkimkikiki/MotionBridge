"""원본 영상 제목(표시 이름)과 '제목 00:00:00~00:00:10' 클립 이름."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
fails = []


def check(c, m):
    if not c:
        fails.append(m)
        print("FAIL", m)


with tempfile.TemporaryDirectory() as d:
    os.environ["APPDATA"] = d
    os.environ["HOME"] = d
    os.environ["USERPROFILE"] = d
    from motionbridge.animref.core import library, segment, settings as S, sources, titles

    v = os.path.join(d, "2026-11-09 14-03-22.mp4")
    open(v, "wb").write(b"x")
    check(titles.title_of(v) == "" and titles.display(v) == "2026-11-09 14-03-22", "제목 없으면 파일 이름")
    titles.set_title(v, "  이름없는자 보스전 ")
    check(titles.title_of(v) == "이름없는자 보스전" and titles.display(v) == "이름없는자 보스전", "제목 저장(앞뒤 공백 정리)")
    check(titles.title_of(v.upper() if os.name == "nt" else v) == "이름없는자 보스전", "같은 경로면 같은 제목")
    check(titles.hms(0) == "00:00:00" and titles.hms(10) == "00:00:10" and titles.hms(3725.4) == "01:02:05", "시간 표기: %s" % titles.hms(3725.4))
    m = {"source_video": v, "start_in_source": 0.0, "duration": 10.0}
    check(titles.clip_title(m) == "이름없는자 보스전 00:00:00~00:00:10", "클립 이름: %s" % titles.clip_title(m))
    m2 = {"source_video": v, "start_in_source": 61.2, "duration": 4.0}
    check(titles.clip_title(m2) == "이름없는자 보스전 00:01:01~00:01:05", "클립 이름(시작 위치 반영): %s" % titles.clip_title(m2))
    titles.set_title(v, "")
    check(titles.title_of(v) == "" and titles.clip_title(m).startswith("2026-11-09 14-03-22 "), "제목을 비우면 파일 이름으로 복귀")
    check(titles.clip_title({"duration": 3}) == "클립 00:00:00~00:00:03", "원본 정보가 없는 클립도 이름 있음")
    # 영상 목록에도 제목이 실린다
    s = S.load()
    s["obs_record_dir"] = d
    s["library_dir"] = os.path.join(d, "lib")
    titles.set_title(v, "이름없는자 보스전")
    found = [x for x in sources.list_sources(s) if x["path"] == v]
    check(found and found[0]["title"] == "이름없는자 보스전" and found[0]["name"] == "2026-11-09 14-03-22.mp4", "원본 목록에 제목 포함, 파일 이름은 그대로")
    check(os.path.isfile(v), "영상 파일은 건드리지 않음")

    # 폴더 가져오기: 하위 폴더까지 영상만 모아 이름순으로
    fd = os.path.join(d, "vids")
    os.makedirs(os.path.join(fd, "sub"))
    for nm in ("b.mp4", "a.MKV", "memo.txt", os.path.join("sub", "c.mov")):
        open(os.path.join(fd, nm), "wb").write(b"x")
    got = [os.path.relpath(x, fd).replace("\\", "/") for x in sources.videos_in_folder(fd)]
    check(got == ["a.MKV", "b.mp4", "sub/c.mov"], "폴더에서 영상만(하위 폴더 포함, 이름순): %s" % got)
    check([os.path.basename(x) for x in sources.videos_in_folder(fd, recursive=False)] == ["a.MKV", "b.mp4"], "하위 폴더 제외 옵션")
    check(sources.videos_in_folder(os.path.join(d, "없는폴더")) == [] and len(sources.videos_in_folder(fd, limit=2)) == 2, "없는 폴더/개수 제한")

print("모두 통과" if not fails else "실패 %d개" % len(fails))
sys.exit(1 if fails else 0)
