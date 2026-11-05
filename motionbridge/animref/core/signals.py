"""영상에서 '전투 단서'와 '연출 단서'를 뽑는다.

- 타격음: 오디오 음량이 아주 짧은 시간에 급격히 커지는 순간 (효과음의 특징)
- 번쩍임: 화면 밝기가 순간적으로 확 바뀌는 순간 (타격 이펙트/스킬 섬광)
- 레터박스: 화면 위아래 검은 띠 (컷신의 특징)
- 컷 전환: 장면이 통째로 바뀌는 순간 (연출 카메라)
"""
import subprocess

import numpy as np

from . import media


def audio_onsets(path, sr=16000, hop=0.02, min_rise_db=6.0, with_strength=False):
    """타격음 후보 시각(초) 배열. 오디오가 없으면 None. with_strength=True면 (시각, 세기)."""
    args = [media.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    try:
        r = subprocess.run(args, capture_output=True, creationflags=media._NO_WINDOW, timeout=1800)
    except Exception:
        return None
    raw = r.stdout
    if len(raw) < sr * 2:  # 1초 미만 / 오디오 없음
        return None
    x = np.frombuffer(raw[: len(raw) // 2 * 2], np.int16).astype(np.float32) / 32768.0
    n = int(sr * hop)
    frames = x[: len(x) // n * n].reshape(-1, n)
    e = 10.0 * np.log10((frames ** 2).mean(axis=1) + 1e-10)
    if e.max() < -60:  # 사실상 무음
        return None
    rise = np.maximum(0.0, e - np.concatenate([[e[0]], e[:-1]]))
    loud = e > np.percentile(e, 40)
    thr = max(min_rise_db, float(np.percentile(rise, 98)))
    idx = np.where((rise >= thr) & loud)[0]
    # 0.1초 안에 몰린 것은 하나로
    out, strength, last = [], [], -1e9
    for i in idx:
        t = i * hop
        if t - last >= 0.1:
            out.append(t)
            strength.append(float(rise[i]))
            last = t
        elif strength and rise[i] > strength[-1]:
            strength[-1] = float(rise[i])
    times = np.array(out, dtype=np.float32)
    if with_strength:
        return times, np.array(strength, dtype=np.float32)
    return times


class FrameCues:
    """분석용으로 디코딩한 프레임마다 번쩍임/레터박스/컷 전환을 기록."""

    def __init__(self):
        self.t, self.flash, self.letterbox, self.cut = [], [], [], []
        self._prev_mean = None
        self._prev_hist = None

    def add(self, t, frame_bgr, gray_small):
        import cv2

        h = frame_bgr.shape[0]
        m = float(gray_small.mean())
        flash = self._prev_mean is not None and abs(m - self._prev_mean) > 18.0
        self._prev_mean = m

        band = max(2, int(h * 0.09))
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        top, bot, mid = gray[:band], gray[-band:], gray[band:-band]
        letterbox = (top.mean() < 14 and bot.mean() < 14 and top.std() < 6 and bot.std() < 6
                     and mid.mean() > 30)

        hist = cv2.calcHist([gray_small], [0], None, [32], [0, 256])
        cv2.normalize(hist, hist)
        cut = False
        if self._prev_hist is not None:
            cut = cv2.compareHist(self._prev_hist, hist, cv2.HISTCMP_CORREL) < 0.5
        self._prev_hist = hist

        self.t.append(t)
        self.flash.append(flash)
        self.letterbox.append(bool(letterbox))
        self.cut.append(bool(cut))

    def arrays(self):
        return (np.array(self.t, np.float32), np.array(self.flash, bool),
                np.array(self.letterbox, bool), np.array(self.cut, bool))


def segment_hints(t0, t1, cues, onsets, pad=0.4):
    t, flash, lb, cut = cues
    w = (t >= t0 - pad) & (t <= t1 + pad)
    inside = (t >= t0) & (t <= t1)
    n_on = 0 if onsets is None else int(((onsets >= t0 - pad) & (onsets <= t1 + pad)).sum())
    return {
        "impact_sounds": n_on,
        "has_audio": onsets is not None,
        "flashes": int(flash[w].sum()) if len(t) else 0,
        "letterbox": round(float(lb[inside].mean()), 2) if inside.any() else 0.0,
        "cuts": int(cut[w].sum()) if len(t) else 0,
    }


def is_combat_candidate(h):
    """효과음 또는 번쩍임이 있거나, 연출 단서가 있으면 후보로 남긴다.
    오디오가 없는 영상은 소리로 거르지 않는다."""
    if h["letterbox"] >= 0.5 or h["cuts"] >= 1:
        return True
    if h["flashes"] >= 1:
        return True
    if not h["has_audio"]:
        return True
    return h["impact_sounds"] >= 1


def merge_for_full(segs, gap=0.3, max_s=4.0):
    """전체 화면 클립용: 여러 캐릭터의 겹치는 구간을 하나로 합친다 (같은 장면 중복 방지)."""
    out = []
    for sg in sorted(segs, key=lambda x: x["t0"]):
        if out and sg["t0"] <= out[-1]["t1"] + gap and (max(out[-1]["t1"], sg["t1"]) - out[-1]["t0"]) <= max_s:
            o = out[-1]
            o["t1"] = max(o["t1"], sg["t1"])
            o["tids"].append(sg["tid"])
            if sg["peak"] > o["peak"]:
                o["peak"], o["tid"], o["box"] = sg["peak"], sg["tid"], sg["box"]
        else:
            out.append(dict(sg, tids=[sg["tid"]]))
    return out


def merge_hits(times, strengths=None, min_gap=0.3):
    """가까이 붙은 타격 후보(휘두르는 소리+맞는 소리 등)를 하나로. 더 센 쪽을 남긴다."""
    if strengths is None:
        strengths = [1.0] * len(times)
    out = []
    for t, s in sorted(zip(map(float, times), map(float, strengths))):
        if out and t - out[-1][0] < min_gap:
            if s > out[-1][1]:
                out[-1] = (t, s)
        else:
            out.append((t, s))
    return [t for t, _ in out]


def energy_peaks(points, t0, t1, thr, min_gap=0.35):
    """소리가 없을 때: 캐릭터 움직임 에너지의 정점들을 타격 순간으로 본다."""
    pts = [p for p in (points or []) if t0 <= p["t"] <= t1]
    peaks = []
    for i in range(1, len(pts) - 1):
        e = pts[i]["e"]
        if e > thr and e >= pts[i - 1]["e"] and e >= pts[i + 1]["e"]:
            peaks.append((pts[i]["t"], e))
    return merge_hits([p[0] for p in peaks], [p[1] for p in peaks], min_gap)


def hit_windows(seg, hits, pre_max=0.8, post_max=0.8, min_len=1.0, duration=None):
    """연속 동작 구간을 한 타씩 나눈다. 이웃한 타격과의 중간 지점에서 끊어서 서로 겹치지 않게 하고,
    너무 짧으면(아주 빠른 연타) 최소 길이까지만 늘린다."""
    hs = sorted(hits)
    out = []
    for i, h in enumerate(hs):
        prev = hs[i - 1] if i > 0 else None
        nxt = hs[i + 1] if i < len(hs) - 1 else None
        a = h - pre_max if prev is None else max(h - pre_max, (prev + h) / 2)
        b = h + post_max if nxt is None else min(h + post_max, (h + nxt) / 2)
        if b - a < min_len:
            extra = (min_len - (b - a)) / 2
            a, b = a - extra, b + extra
        a = max(0.0, a)
        if duration:
            b = min(duration, b)
        w = dict(seg)
        w.update(t0=float(a), t1=float(b), hit_t=round(float(h), 3), hit_index=i + 1, hit_count=len(hs), exact=True)
        out.append(w)
    return out
