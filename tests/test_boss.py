"""보스전 감지: 체력바 틀 인식, 체력 읽기, 시작/종료 판단(연출·페이즈 유예), 녹화본 훑기. 합성 화면으로 시험한다."""
import os
import sys
import tempfile
import threading

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import cv2  # noqa: E402
from motionbridge.animref.core import boss as B  # noqa: E402

fails = []


def check(c, m):
    if not c:
        fails.append(m)
        print("FAIL", m)


rng = np.random.default_rng(3)
W, H = 1280, 720
ROI = [0.29, 0.85, 0.42, 0.07]   # 화면 아래 가운데 (체력바 틀까지 감싸게)


def scene(bar=None, hud=True, noise=True, letterbox=False, seed=0):
    """게임 화면 비슷한 배경 + (선택) 보스 체력바. bar = 남은 체력 비율 또는 None."""
    r = np.random.default_rng(seed)
    base = np.full((H, W, 3), (60, 80, 70), np.uint8)
    # 배경: 완만한 색 + 거친 질감(풀숲/바닥 같은)
    base[:, :, 1] = np.linspace(50, 110, W, dtype=np.uint8)[None, :]
    if noise:
        base = np.clip(base.astype(int) + r.integers(-25, 25, base.shape), 0, 255).astype(np.uint8)
    cv2.circle(base, (400, 300), 90, (200, 190, 180), -1)       # 캐릭터
    cv2.rectangle(base, (60, 40), (330, 70), (40, 160, 60), -1)  # 플레이어 체력바(초록, 다른 자리)
    if bar is not None:
        x0, y0, x1, y1 = int(W * .30), int(H * .86), int(W * .70), int(H * .91)
        cv2.rectangle(base, (x0 - 4, y0 - 4), (x1 + 4, y1 + 4), (230, 230, 230), 2)   # 틀
        cv2.rectangle(base, (x0, y0), (x1, y1), (25, 25, 25), -1)                       # 빈 바
        cv2.rectangle(base, (x0, y0), (x0 + int((x1 - x0) * bar), y1), (30, 30, 200), -1)  # 빨간 채움
    if letterbox:
        base[: int(H * .12)] = 0
        base[-int(H * .12):] = 0
    return base


# ---------- 기준 만들기 + 있다/없다
full = scene(bar=1.0, seed=1)
cal = B.calibrate(full, ROI)
check(len(cal.hsv) == 4 and cal.full_cols > 100, "채움 색 학습/체력 100%% 기준: %s %s" % (cal.hsv, cal.full_cols))
det = B.Detector(cal.roi, cal.ref_edges, cal.hsv, 0.55, cal.full_cols)
for k, (bar, want) in enumerate([(1.0, True), (0.6, True), (0.1, True), (None, False)]):
    pres, sc, hp = det.present(scene(bar=bar, seed=10 + k))
    check(pres == want, "체력 %s -> 있음=%s (점수 %.2f)" % (bar, pres, sc))
_, _, hp60 = det.present(scene(bar=0.6, seed=20))
_, _, hp10 = det.present(scene(bar=0.1, seed=21))
check(hp60 is not None and abs(hp60 - 0.6) < 0.08 and abs(hp10 - 0.1) < 0.08, "남은 체력 읽기: %s %s" % (hp60, hp10))
# 다른 해상도(1920x1080)에서도 같은 기준
big = cv2.resize(scene(bar=0.8, seed=30), (1920, 1080))
check(det.present(big)[0], "해상도가 달라도 인식")
# 틀이 안 보이게 지정하면 안내
try:
    B.calibrate(scene(bar=None, noise=False), [0.40, 0.30, 0.10, 0.05])
    check(False, "윤곽 없는 영역은 거절돼야 함")
except ValueError as e:
    check("테두리" in str(e), "윤곽 없는 영역 안내")
# 틀이 조금 어긋나도(여유 12%) 인식, 엉뚱한 곳의 밝은 가로줄은 속지 않음
fake = scene(bar=None, seed=40)
cv2.rectangle(fake, (200, 100), (700, 140), (230, 230, 230), 2)
check(not det.present(fake)[0], "다른 자리 사각형은 보스 체력바가 아님")
check(not det.present(scene(bar=None, noise=False, seed=41))[0], "단색 배경도 아님")

# 배경이 확 달라져 윤곽 모양이 안 맞아도, 채움 색 띠로 체력바를 알아본다
def other_bg(bar):
    f = scene(bar=bar, noise=False, seed=50)
    f[:, :, :] = np.clip(f.astype(int) * 0 + np.random.default_rng(7).integers(90, 255, f.shape), 0, 255).astype(np.uint8)   # 아주 밝고 어지러운 배경
    x0, y0, x1, y1 = int(W * .30), int(H * .86), int(W * .70), int(H * .91)
    cv2.rectangle(f, (x0, y0), (x1, y1), (25, 25, 25), -1)
    cv2.rectangle(f, (x0, y0), (x0 + int((x1 - x0) * bar), y1), (30, 30, 200), -1)
    return f


det2 = B.Detector(cal.roi, cal.ref_edges, cal.hsv, 0.55, cal.full_cols, cal.fill_rows)
check(cal.fill_rows >= 4, "채움 색 두께 학습: %s" % cal.fill_rows)
p_bg, sc_bg, hp_bg = det2.present(other_bg(0.9))
check(p_bg and det2.last_color >= B.COLOR_PRESENT, "윤곽이 안 맞는 배경에서도 색 띠로 인식 (윤곽 %.2f 색 %.2f)" % (det2.last_edge, det2.last_color))
check(hp_bg is not None and abs(hp_bg - 0.9) < 0.12, "그때도 남은 체력 읽기: %s" % hp_bg)
fake_red = scene(bar=None, seed=60)
cv2.rectangle(fake_red, (100, 400), (180, 420), (30, 30, 200), -1)       # 작은 빨간 조각(피 효과 등)은 체력바가 아님
check(not det2.present(fake_red)[0], "작은 빨간 조각은 체력바 아님")

# ---------- 연출(레터박스)
check(B.letterbox(scene(bar=None, letterbox=True)) and not B.letterbox(scene(bar=1.0)), "레터박스 감지")

# ---------- 시작/종료 판단 (가짜 시간)
def run(timeline, **kw):
    w = B.BossWatcher(**kw)
    evs = []
    for t, present, hp, cut in timeline:
        for e in w.update(t, present, hp, cut):
            evs.append((round(t, 1), e[0], e[1].get("reason")))
    return evs, w


def tl(parts, step=1.0):
    out, t = [], 0.0
    for dur, present, hp, cut in parts:
        n = int(dur / step)
        for i in range(n):
            out.append((t, present, hp(i / max(n - 1, 1)) if callable(hp) else hp, cut))
            t += step
    return out


# 1) 잠깐(1초) 스친 체력바는 시작 아님
evs, _ = run(tl([(1, True, 1.0, False), (30, False, None, False)]), start_hold=2.0)
check(evs == [], "스친 화면은 시작 아님: %s" % evs)
# 2) 시작 -> 처치(체력 0 근처에서 사라짐) -> 짧은 유예 후 종료
evs, _ = run(tl([(5, True, 1.0, False), (60, True, lambda x: 1 - x, False), (40, False, None, False)]), start_hold=2.0, defeat_grace=15.0, end_grace=60.0)
kinds = [(k, r) for _, k, r in evs]
check(kinds == [("start", None), ("end", "defeat")], "처치 -> 종료: %s" % evs)
check(abs(evs[1][0] - (5 + 60 + 15)) <= 2, "처치 유예 15초 뒤 종료 (t=%s)" % evs[1][0])
# 3) 페이즈 변경: 체력바가 40초 사라졌다 다시 나타나면 같은 보스전(종료 아님), 이후 사라지면 60초 뒤 종료
evs, w = run(tl([(10, True, 0.7, False), (40, False, None, False), (20, True, 0.5, False), (90, False, None, False)]), start_hold=2.0, end_grace=60.0)
check([(k, r) for _, k, r in evs] == [("start", None), ("end", "bar_lost")], "페이즈 연출(40초) 중에는 안 끝남: %s" % evs)
check(abs(evs[1][0] - (10 + 40 + 20 + 60)) <= 2, "마지막 사라진 뒤 60초 (t=%s)" % evs[1][0])
# 4) 연출(레터박스)이 길어도 그동안은 세지 않는다
evs, _ = run(tl([(10, True, 0.7, False), (120, False, None, True), (30, True, 0.3, False), (5, False, None, False)]), start_hold=2.0, end_grace=60.0)
check([k for _, k, _ in evs] == ["start"], "2분 연출 동안 종료되지 않음: %s" % evs)
# 5) 연출 끝나고 안 나타나면 그때부터 60초
evs, _ = run(tl([(10, True, 0.7, False), (100, False, None, True), (80, False, None, False)]), start_hold=2.0, end_grace=60.0)
check(len(evs) == 2 and abs(evs[1][0] - (10 + 100 + 60)) <= 2, "연출 뒤부터 유예: %s" % evs)
# 6) 최대 시간
evs, _ = run(tl([(200, True, 0.9, False)]), start_hold=2.0, max_seconds=100.0)
check([(k, r) for _, k, r in evs][:2] == [("start", None), ("end", "max_time")], "최대 시간: %s" % evs)
# 7) 수동으로 끝낸 뒤: 같은 체력바로는 다시 시작하지 않고, 바가 사라진 뒤에야 다시 감지
w = B.BossWatcher(start_hold=1.0)
w.update(0, True, 1.0); w.update(2, True, 1.0)
w.abort()
r1 = w.update(10, True, 1.0) + w.update(20, True, 1.0)
check(w.state == "idle" and not r1, "수동 종료 뒤 같은 바로 재시작 안 함")
B.fix_abort(w, False)
w.update(30, False)
w.update(31, True, 1.0)
r2 = w.update(33, True, 1.0)
check(r2 and r2[0][0] == "start", "바가 사라졌다 다시 나오면 새 보스전")
# 8) 설정 읽기
c = B.cfg({"boss": {"end_grace": 90, "match": "x", "zzz": 1}})
check(c["end_grace"] == 90 and c["match"] == 0.55 and "zzz" not in c, "설정 합치기(타입 틀리면 기본값)")
ww = B.BossWatcher.from_cfg(c)
check(ww.end_grace == 90 and ww.max_seconds == 25 * 60, "설정 -> 판단기")
check("기다리는" in ww.status_text(0), "상태 문구")

# ---------- 녹화본 훑기 (영상 파일)
with tempfile.TemporaryDirectory() as d:
    vp = os.path.join(d, "t.mp4")
    fps = 4
    vw = cv2.VideoWriter(vp, cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    # 0~9s 평상시, 10~49s 보스(체력 1.0 -> 0.0), 50~64s 사라짐(처치 유예 15s), 65~74s 평상시
    for i in range(75 * fps):
        t = i / fps
        if t < 10:
            f = scene(None, seed=i)
        elif t < 50:
            f = scene(1 - (t - 10) / 40, seed=i)
        else:
            f = scene(None, seed=i)
        vw.write(f)
    vw.release()
    segs, st = B.scan_video(vp, det, dict(B.DEFAULTS, defeat_grace=10.0, end_grace=30.0), step=1.0)
    check(len(segs) == 1 and abs(segs[0].start - 10) <= 2 and segs[0].reason == "defeat", "녹화본에서 보스전 구간 찾기: %s" % segs)
    check(segs and abs(segs[0].end - 60) <= 3, "끝 시각 (처치 후 유예): %s" % (segs[0].end if segs else None))
    check(st["score_max"] > 0.6 and st["present"] > 20, "통계: %s" % st)
    ev = threading.Event(); ev.set()
    s2, _ = B.scan_video(vp, det, dict(B.DEFAULTS), step=1.0, cancel=ev)
    check(s2 == [], "취소하면 바로 멈춤")

# ---------- 실행부 (가짜 OBS)
from motionbridge.animref.core import bossrun as R  # noqa: E402

with tempfile.TemporaryDirectory() as d:
    st = B.save_calibration(cal, os.path.join(d, "ref.png"))
    s = {"boss": dict(st, start_hold=2.0, end_grace=10.0, defeat_grace=5.0)}
    frames = {"v": scene(bar=0.9, seed=7)}

    class FakeObs:
        closed = False
        def screenshot(self, w=960):
            ok, buf = cv2.imencode(".jpg", frames["v"])
            return buf.tobytes()
        def close(self):
            FakeObs.closed = True
    clock = {"t": 100.0}
    sm = R.BossSampler(s, FakeObs, clock=lambda: clock["t"])
    check(sm.ready(), "기준이 있으면 준비됨")
    a = sm.sample()
    check(a.present and a.hp is not None and not a.error, "가짜 OBS 화면에서 체력바 인식: %s" % a)
    frames["v"] = scene(bar=None, seed=8)
    check(not sm.sample().present, "없을 때 없음")
    check(R.BossSampler({}, FakeObs).sample().error != "", "기준 없으면 안내")

    run = R.BossRun(s)
    seq = []
    def feed(t, bar, other=False, cut=False):
        frames["v"] = scene(bar=bar, seed=int(t), letterbox=cut)
        clock["t"] = t
        smp = sm.sample()
        seq.extend(run.apply(smp, other))
    for t in (0, 1, 2, 3):
        feed(t, 0.9)
    check([k for k, _ in seq] == ["start"] and run.auto_recording, "체력바 2초 -> 자동 녹화 시작: %s" % seq)
    for t in (4, 5, 6):
        feed(t, 0.02)
    for t in range(7, 20):
        feed(t, None)
    check([k for k, _ in seq] == ["start", "end"] and seq[1][1]["reason"] == "defeat" and not run.auto_recording, "처치 후 종료: %s" % seq)

    # 수동 녹화 중에는 건드리지 않음
    run2 = R.BossRun(s)
    seq2 = []
    for t in (200, 201, 202, 203):
        frames["v"] = scene(bar=0.8, seed=t); clock["t"] = t
        seq2.extend(run2.apply(sm.sample(), other_recording=True))
    check([k for k, _ in seq2] == ["skip"] and not run2.auto_recording and "수동" in run2.status(sm.sample()), "수동 녹화 중이면 자동 시작 안 함: %s" % seq2)
    # 자동 녹화를 사용자가 직접 멈추면 같은 체력바로 다시 시작하지 않음
    run3 = R.BossRun(s)
    for t in (300, 301, 302, 303):
        frames["v"] = scene(bar=0.8, seed=t); clock["t"] = t
        run3.apply(sm.sample(), False)
    run3.manual_stopped()
    seq3 = []
    for t in (304, 306, 310):
        frames["v"] = scene(bar=0.8, seed=t); clock["t"] = t
        seq3.extend(run3.apply(sm.sample(), False))
    check(seq3 == [] and not run3.auto_recording, "직접 멈춘 뒤 재시작 안 함")
    # OBS 오류는 상태로만 알림
    class Bad:
        def screenshot(self, w=960): raise RuntimeError("refused")
        def close(self): pass
    e = R.BossSampler(s, Bad).sample()
    check("OBS 연결 실패" in e.error and run.status(e).startswith("⚠"), "OBS 오류 안내")

# 직전 영상 이어붙이기 (ffmpeg 가 있을 때만)
try:
    from motionbridge.animref.core import media as _m
    _ff = _m.ffmpeg_exe()
except Exception:
    _ff = None
if _ff:
    with tempfile.TemporaryDirectory() as d:
        import subprocess
        for nm in ("pre", "main"):
            subprocess.run([_ff, "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10", "-t", "1", "-pix_fmt", "yuv420p", "-c:v", "libx264", os.path.join(d, nm + ".mp4")], capture_output=True)
        out = R.merge_prebuffer(os.path.join(d, "pre.mp4"), os.path.join(d, "main.mp4"))
        check(out is not None and out.endswith("main_boss.mp4") and abs(_m.probe(out).duration - 2.0) < 0.3, "직전 영상 + 본 녹화 이어붙이기: %s" % out)
else:
    print("ffmpeg 없음: 이어붙이기 시험 건너뜀")

print("모두 통과" if not fails else "실패 %d개" % len(fails))
sys.exit(1 if fails else 0)
