"""클립 만들기 진행 현황: ffmpeg 진행률 전달, 취소, 수동 클립 저장 단계."""
import os
import subprocess
import sys
import tempfile
import threading

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
    from motionbridge.animref.core import Cancelled, media, segment, settings as S

    try:
        ff = media.ffmpeg_exe()
    except RuntimeError:
        ff = None
    if not ff:
        print("ffmpeg 없음: 건너뜀")
        sys.exit(0)
    src = os.path.join(d, "src.mp4")
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15", "-f", "lavfi", "-i", "sine=frequency=440",
                    "-t", "6", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-c:a", "aac", "-shortest", src], check=True)
    # ffmpeg 진행률이 0~1 로 단조롭게 올라가고 마지막에 1.0
    got = []
    out = os.path.join(d, "a.mp4")
    media.cut(src, 1.0, 3.0, out, on_progress=got.append)
    check(os.path.isfile(out) and got and abs(got[-1] - 1.0) < 1e-9, "진행률 마지막 1.0: %s" % got[-3:])
    check(all(b >= a - 1e-9 for a, b in zip(got, got[1:])) and all(0 <= x <= 1 for x in got), "진행률은 0~1 로 줄지 않음")
    # 진행 콜백 없이도 예전처럼 동작
    out2 = os.path.join(d, "b.mp4")
    media.cut(src, 0.0, 2.0, out2)
    check(os.path.isfile(out2), "콜백 없이도 자르기")
    # 취소: 시작 전에 켜 두면 Cancelled, 결과 파일 남기지 않음
    ev = threading.Event()
    ev.set()
    out3 = os.path.join(d, "c.mp4")
    try:
        media.cut(src, 0.0, 5.0, out3, on_progress=lambda f: None, cancel=ev)
        check(False, "취소돼야 함")
    except Cancelled:
        check(not os.path.exists(out3), "취소하면 반쪽짜리 파일을 남기지 않음")
    # 잘못된 입력은 오류 메시지
    try:
        media.cut(os.path.join(d, "없음.mp4"), 0, 1, os.path.join(d, "e.mp4"), on_progress=lambda f: None)
        check(False, "없는 파일은 오류")
    except RuntimeError as e:
        check("클립 저장 실패" in str(e), "오류 메시지: %s" % e)
    # 수동 클립: 단계별 진행 알림 + 결과 json
    s = S.load()
    s["game"] = "테스트"
    s["library_dir"] = os.path.join(d, "lib")
    steps, logs = [], []
    jp = segment.make_manual_clip(src, 1.0, 4.0, s, None, logs.append, lambda f, t: steps.append((round(f, 2), t)))
    check(os.path.isfile(jp) and steps[0][0] <= 0.05 and steps[-1] == (1.0, "완료!"), "수동 클립 진행 단계: %s" % steps[:2] + str(steps[-1:]))
    check(any("자르는 중" in t for _f, t in steps) and any("저장" in t for _f, t in steps), "단계 글: 자르는 중/저장")
    check([f for f, _ in steps] == sorted(f for f, _ in steps), "진행률 단조 증가")

print("모두 통과" if not fails else "실패 %d개" % len(fails))
sys.exit(1 if fails else 0)
