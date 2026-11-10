"""장면 고르기 시험: 검은/정지 화면 제외, 서로 다른 장면 고르기. 점수 계산은 합성 이미지로, 영상 뽑기는 ffmpeg 가 있을 때만."""
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from motionbridge.core import framepick as fp
from motionbridge.core import frames as F

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("FAIL", msg)


rng = np.random.default_rng(1)


def scene(seed, bright=170):
    """게임 장면 흉내: 어두운 배경 + 밝은 모양들 (윤곽선이 많고 명암 편차가 큼)."""
    import cv2
    r = np.random.default_rng(seed)
    im = np.full((360, 640, 3), 18, np.uint8)
    for _ in range(40):
        c = (int(r.integers(0, 640)), int(r.integers(0, 360)))
        cv2.circle(im, c, int(r.integers(8, 40)), (int(bright * r.random()), int(bright * r.random()), bright), -1)
        cv2.line(im, c, (int(r.integers(0, 640)), int(r.integers(0, 360))), (bright, bright, bright), 2)
    return im


def small(im):
    import cv2
    return cv2.resize(cv2.cvtColor(im, cv2.COLOR_BGR2GRAY), (160, 90), interpolation=cv2.INTER_AREA).astype(np.float32)


# ---- 점수 (움직임 정보 없이)
black = np.zeros((360, 640, 3), np.uint8)
dim = np.full((360, 640, 3), 14, np.uint8)
dim[100:200, 100:300] = 30                      # 거의 안 보이는 장면
play = scene(1)
check(not fp.measure(black).ok and fp.measure(black).why, "검은 화면은 탈락")
check(not fp.measure(dim).ok, "너무 어두운 화면도 탈락")
mp = fp.measure(play)
check(mp.ok and mp.score > 0.4, "게임 장면 통과: %.2f" % mp.score)
menu = (play * 0.45).astype(np.uint8)           # 같은 그림이 더 어둡고 명암 낮음 = 메뉴 흉내
check(fp.measure(menu).score < mp.score, "어둡고 밋밋한 장면은 점수가 더 낮음")

# ---- 움직임: 0.5초 사이 거의 안 변하면 정지 화면(메뉴·일시정지)
a, b = small(play), small(scene(2))
still = fp.measure(play, a, a)
moving = fp.measure(play, a, b)
check(still.motion is not None and still.motion < fp.STATIC_DIFF and "정지" in still.why, "정지 화면 표시: %s %s" % (still.motion, still.why))
check(moving.motion is not None and moving.motion > 3 and moving.score > still.score * 1.5, "움직이는 장면이 정지 화면보다 확실히 높음: %.2f vs %.2f" % (moving.score, still.score))

# ---- 고르기: 점수순 + 서로 다른 장면 + 시간 간격 + 시간순 반환
def info(t, score, h, ok=True):
    return fp.Info("f%d" % t, float(t), score, ok=ok, hash=h)


H1, H2, H3 = 0, (1 << 64) - 1, int("1010" * 16, 2)
cand = [info(1, 0.9, H1), info(2, 0.85, H1), info(30, 0.6, H2), info(60, 0.5, H3), info(90, 0.0, 5, ok=False), info(95, 0.3, H2 ^ 0b111)]
got = fp.pick(cand, 3, 100.0)
check([i.t for i in got] == [1.0, 30.0, 60.0], "비슷한 그림(t=2)·탈락(t=90)은 건너뛰고 시간 순으로: %s" % [i.t for i in got])
check(len(fp.pick(cand, 5, 100.0)) == 5 and all(i.ok for i in fp.pick(cand, 5, 100.0)), "모자라면 비슷한 것으로 채우되 탈락한 것은 쓰지 않음")
check(fp.pick([info(1, 0.0, 1, ok=False)], 3) == [], "쓸 만한 장면이 없으면 빈 목록")

# ---- ffmpeg 로 실제 영상: 검은 4초 | 정지 메뉴 5초 | 움직이는 게임 장면 14초 | 검은 2초
ff = F.find_ffmpeg()
if not ff or shutil.which("ffmpeg") is None and not os.path.isfile(ff):
    print("ffmpeg 없음: 영상 시험 건너뜀")
else:
    import cv2
    d = tempfile.mkdtemp(prefix="fp_test_")
    try:
        pngs = []
        for name, im in (("menu", (scene(7) * 0.6).astype(np.uint8)), ("g1", scene(11)), ("g2", scene(12))):
            p = os.path.join(d, name + ".png")
            cv2.imwrite(p, cv2.resize(im, (1024, 576)))
            pngs.append(p)
        mp4 = os.path.join(d, "t.mp4")
        fc = ("[1:v]scale=1024:576,setsar=1[m];"
              "[2:v]scale=1280:720,crop=1024:576:'(in_w-1024)*t/8':'(in_h-576)/2',setsar=1[g1];"
              "[3:v]scale=1280:720,crop=1024:576:'(in_w-1024)*(1-t/6)':'(in_h-576)/2',setsar=1[g2];"
              "[0:v][m][g1][g2][4:v]concat=n=5:v=1:a=0[o]")
        cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=1024x576:d=4:r=24",
               "-loop", "1", "-t", "5", "-framerate", "24", "-i", pngs[0], "-loop", "1", "-t", "8", "-framerate", "24", "-i", pngs[1],
               "-loop", "1", "-t", "6", "-framerate", "24", "-i", pngs[2], "-f", "lavfi", "-i", "color=c=black:s=1024x576:d=2:r=24",
               "-filter_complex", fc, "-map", "[o]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", mp4]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not os.path.isfile(mp4):
            print("영상 만들기 실패(건너뜀):", r.stderr[-200:])
        else:
            out = os.path.join(d, "out")
            got = fp.sample_best(ff, mp4, out, per=4)
            ts = [round(f.t, 1) for f in got]
            check(len(got) == 4 and all(os.path.isfile(f.path) for f in got), "4장을 뽑음: %s" % ts)
            check(all(9.0 <= t <= 23.0 for t in ts), "검은 화면(0~4초)·메뉴(4~9초)·끝 검은 화면(23~25초)을 피하고 게임 구간에서만: %s" % ts)
            res = fp.sample_sources_best(ff, [mp4], os.path.join(d, "out2"), 4, 24)
            check(len(res) == 4, "sample_sources_best 도 4장")
            # 이미지 파일은 예전 방식 그대로
            im_out = fp.sample_sources_best(ff, [pngs[1]], os.path.join(d, "out3"), 4, 24)
            check(len(im_out) == 1, "이미지는 한 장")
            # 짧은 영상(3초 미만)은 균등 뽑기로
            short = os.path.join(d, "s.mp4")
            subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", mp4, "-t", "2", "-c", "copy", short], capture_output=True)
            if os.path.isfile(short):
                check(len(fp.sample_best(ff, short, os.path.join(d, "out4"), per=3)) >= 1, "짧은 영상도 처리")
            # 전부 검은 영상: 쓸 장면이 없어도 균등 뽑기로 돌아가 멈추지 않는다
            blk = os.path.join(d, "b.mp4")
            subprocess.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=640x360:d=10:r=24", "-c:v", "libx264", "-pix_fmt", "yuv420p", blk], capture_output=True)
            if os.path.isfile(blk):
                check(len(fp.sample_best(ff, blk, os.path.join(d, "out5"), per=3)) == 3, "전부 검은 영상은 균등 뽑기로 대체")
    finally:
        shutil.rmtree(d, ignore_errors=True)

if fails:
    print("\n실패 %d개" % len(fails))
    sys.exit(1)
print("test_framepick ALL OK")
