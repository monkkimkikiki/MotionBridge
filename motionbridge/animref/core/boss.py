"""보스전 자동 감지: 화면 속 '보스 체력바'가 나타났다/사라졌다를 보고 보스전 시작·끝을 판단한다.

방법 (무료 오픈소스 OpenCV, Apache-2.0 / 글자 인식(OCR) 없이):
  1) 보스 체력바는 화면 같은 자리에 같은 모양의 틀로 나온다. 사용자가 한 번 '체력바 영역'을 지정하면(보스전 화면 한 장) 그 틀의 윤곽(Canny)을
     기준 그림으로 저장하고, 이후 화면마다 같은 자리의 윤곽이 얼마나 닮았는지(matchTemplate)로 '있다/없다'를 정한다.
  2) 체력바 안의 색(채워진 부분)을 읽어 남은 체력 비율(hp, 참고용)도 구한다. 바가 0 근처로 줄었다가 사라지면 '처치'로 본다.
  3) 연출(컷신)은 위아래 검은 띠(레터박스)가 생기는 것으로 알아내서, 연출 중에는 '사라진 시간'을 세지 않는다.
시작/종료 판단(BossWatcher)은 화면 인식과 분리해 두어서 시간 값만 바꿔 조정할 수 있고, 가짜 시간으로 시험할 수 있다.

주의: 실제 게임 화면으로 확인하기 전에는 '이 게임에서 잘 맞는다'고 말할 수 없다. [영상으로 감지 시험]으로 녹화본에서 먼저 검증한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

NORM_W = 960   # 화면은 이 너비로 줄여서 본다 (해상도가 달라도 같은 기준)

DEFAULTS: Dict[str, object] = {
    "roi": [],                 # [x, y, w, h] 화면 대비 비율(0~1). 비어 있으면 아직 지정 안 함
    "ref": "",                 # 기준 그림(체력바 틀 윤곽) 파일
    "hsv": [],                 # [h_min, h_max, s_min, v_min] 체력바 채움 색 범위 (OpenCV HSV, h 0~179)
    "full_cols": 0,            # 기준 화면에서 채워진 칸 수 (남은 체력 계산의 100%)
    "match": 0.55,             # 틀이 닮았다고 보는 기준 (0~1, 높을수록 엄격)
    "poll": 0.7,               # 화면을 살펴보는 간격(초)
    "start_hold": 2.0,         # 체력바가 이만큼 계속 보이면 보스전 시작으로 본다(초)
    "end_grace": 60.0,         # 체력바가 이만큼 계속 안 보이면 보스전 끝으로 본다(초) — 페이즈 연출 등을 기다려 주는 시간
    "defeat_grace": 15.0,      # 체력이 0 근처에서 사라졌을 때(처치)는 이만큼만 기다린다(초)
    "defeat_hp": 0.04,         # 이 비율 이하로 줄었다가 사라지면 처치로 본다
    "letterbox_pause": True,   # 위아래 검은 띠(연출) 중에는 사라진 시간을 세지 않는다
    "max_minutes": 25.0,       # 안전장치: 이보다 길면 강제로 끝낸다
    "min_seconds": 20.0,       # 이보다 짧은 녹화는 보스전으로 치지 않고 버린다(잠깐 스친 화면)
    "prebuffer": True,         # OBS 리플레이 버퍼가 켜져 있으면, 감지된 순간 직전 영상도 함께 저장
}


def cfg(s: dict) -> dict:
    out = dict(DEFAULTS)
    b = s.get("boss") if isinstance(s, dict) else None
    if isinstance(b, dict):
        for k, v in b.items():
            if k in DEFAULTS and (isinstance(v, type(DEFAULTS[k])) or (isinstance(DEFAULTS[k], float) and isinstance(v, int))):
                out[k] = v
    return out


def _cv2():
    import cv2
    return cv2


def normalize(frame: np.ndarray) -> np.ndarray:
    """BGR 화면을 너비 NORM_W 로 줄인다."""
    cv2 = _cv2()
    h, w = frame.shape[:2]
    if w == NORM_W:
        return frame
    return cv2.resize(frame, (NORM_W, max(int(round(h * NORM_W / w)), 1)), interpolation=cv2.INTER_AREA)


def decode_image(data: bytes) -> Optional[np.ndarray]:
    cv2 = _cv2()
    arr = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def crop_frac(img: np.ndarray, roi, pad: float = 0.0) -> np.ndarray:
    h, w = img.shape[:2]
    x, y, rw, rh = roi
    px, py = pad * rw, pad * rh
    x0, y0 = max(int((x - px) * w), 0), max(int((y - py) * h), 0)
    x1, y1 = min(int(np.ceil((x + rw + px) * w)), w), min(int(np.ceil((y + rh + py) * h)), h)
    return img[y0:y1, x0:x1]


def _edges(img: np.ndarray) -> np.ndarray:
    cv2 = _cv2()
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    g = cv2.GaussianBlur(g, (3, 3), 0)
    return cv2.Canny(g, 50, 140)


def learn_hsv(sample_roi: np.ndarray) -> List[int]:
    """체력바 영역 그림에서 '채워진 색'을 배운다: 색이 선명한 픽셀의 색상 범위."""
    cv2 = _cv2()
    hsv = cv2.cvtColor(sample_roi, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0].astype(int), hsv[..., 1].astype(int), hsv[..., 2].astype(int)
    m = (s > 90) & (v > 70)
    if m.sum() < 20:
        return []
    hs = h[m]
    # 색상은 원형(0 과 179 가 이어짐): 빨강 계열이 양쪽에 걸리면 한쪽으로 모아서 본다
    if (hs < 12).any() and (hs > 165).any():
        hs = np.where(hs < 90, hs + 180, hs)
    lo, hi = np.percentile(hs, 5), np.percentile(hs, 95)
    return [int(lo - 6) % 180, int(hi + 6) % 180, int(max(np.percentile(s[m], 5) - 25, 50)), int(max(np.percentile(v[m], 5) - 30, 40))]


def hsv_mask(roi_img: np.ndarray, hsv: List[int]) -> np.ndarray:
    cv2 = _cv2()
    if len(hsv) != 4:
        return np.zeros(roi_img.shape[:2], dtype=bool)
    x = cv2.cvtColor(roi_img, cv2.COLOR_BGR2HSV)
    h0, h1, smin, vmin = hsv
    if h0 <= h1:
        mh = (x[..., 0] >= h0) & (x[..., 0] <= h1)
    else:  # 빨강처럼 0 을 넘나드는 범위
        mh = (x[..., 0] >= h0) | (x[..., 0] <= h1)
    return mh & (x[..., 1] >= smin) & (x[..., 2] >= vmin)


def fill_columns(mask: np.ndarray) -> np.ndarray:
    """가로 한 칸마다 '채워졌나'(그 칸의 40% 이상이 채움 색)."""
    if mask.size == 0:
        return np.zeros(0, dtype=bool)
    return mask.mean(axis=0) > 0.4


@dataclass
class Calibration:
    roi: List[float]
    ref_edges: np.ndarray            # 체력바 영역(여백 없음)의 윤곽 그림
    hsv: List[int]
    full_cols: int                   # 기준 화면에서 채워진 칸 수 (체력 100% 로 본다)

    def to_settings(self, ref_path: str) -> dict:
        return {"roi": [round(float(x), 4) for x in self.roi], "ref": ref_path, "hsv": [int(x) for x in self.hsv], "full_cols": int(self.full_cols)}


def calibrate(frame: np.ndarray, roi, ) -> Calibration:
    """보스전 화면 한 장과 체력바 영역(비율)으로 기준을 만든다."""
    img = normalize(frame)
    crop = crop_frac(img, roi)
    if crop.shape[0] < 4 or crop.shape[1] < 16:
        raise ValueError("영역이 너무 작아요. 체력바 전체를 감싸게 다시 지정해 주세요.")
    ed = _edges(crop)
    if (ed > 0).mean() < 0.004:
        raise ValueError("이 영역에서 체력바의 테두리(윤곽)를 찾지 못했어요. 체력바의 틀까지 포함하도록 조금 크게 지정해 주세요.")
    hsv = learn_hsv(crop)
    cols = int(fill_columns(hsv_mask(crop, hsv)).sum()) if hsv else 0
    return Calibration(list(roi), ed, hsv, cols)


class Detector:
    """화면 한 장 -> (체력바가 있나(0~1 점수), 남은 체력 비율, 연출 중인가)."""

    def __init__(self, roi, ref_edges: np.ndarray, hsv: List[int], match: float = 0.55, full_cols: int = 0) -> None:
        self.roi = list(roi)
        self.ref = ref_edges
        self.hsv = list(hsv)
        self.match = float(match)
        self.full_cols = int(full_cols)

    def score(self, frame: np.ndarray) -> Tuple[float, Optional[float]]:
        cv2 = _cv2()
        img = normalize(frame)
        search = crop_frac(img, self.roi, pad=0.12)         # 조금 여유를 두고 찾는다 (화면 흔들림·미세한 위치 차이)
        e = _edges(search)
        th, tw = self.ref.shape[:2]
        if e.shape[0] < th or e.shape[1] < tw or self.ref.max() == 0:
            return 0.0, None
        res = cv2.matchTemplate(e, self.ref, cv2.TM_CCOEFF_NORMED)
        sc = float(res.max())
        hp = None
        if self.hsv and self.full_cols > 0:
            _, _, _, loc = cv2.minMaxLoc(res)
            bar = search[loc[1]:loc[1] + th, loc[0]:loc[0] + tw]
            hp = float(min(fill_columns(hsv_mask(bar, self.hsv)).sum() / max(self.full_cols, 1), 1.0))
        return max(sc, 0.0), hp

    def present(self, frame: np.ndarray) -> Tuple[bool, float, Optional[float]]:
        sc, hp = self.score(frame)
        return sc >= self.match, sc, hp


def make_detector(c: dict) -> Optional["Detector"]:
    """설정(cfg 결과)에서 판단기를 만든다. 기준을 아직 안 만들었거나 그림 파일이 없으면 None."""
    import os
    cv2 = _cv2()
    if len(c.get("roi") or []) != 4 or not c.get("ref") or not os.path.isfile(c["ref"]):
        return None
    ref = cv2.imread(c["ref"], cv2.IMREAD_GRAYSCALE)
    if ref is None:
        return None
    return Detector(c["roi"], ref, c.get("hsv") or [], float(c["match"]), int(c.get("full_cols") or 0))


def save_calibration(cal: "Calibration", ref_path: str) -> dict:
    """기준 그림(윤곽)을 파일로 저장하고 설정에 넣을 값을 돌려준다."""
    import os
    cv2 = _cv2()
    os.makedirs(os.path.dirname(os.path.abspath(ref_path)), exist_ok=True)
    cv2.imwrite(ref_path, cal.ref_edges)
    return cal.to_settings(ref_path)


def letterbox(frame: np.ndarray, band: float = 0.07) -> bool:
    """위아래가 검은 띠이고 가운데는 밝은 화면이면 연출(컷신)으로 본다."""
    g = frame if frame.ndim == 2 else frame.mean(axis=2)
    h = g.shape[0]
    b = max(int(h * band), 2)
    top, bot, mid = g[:b].mean(), g[-b:].mean(), g[h // 3: 2 * h // 3].mean()
    return top < 14 and bot < 14 and mid > 28


# ---------------------------------------------------------------- 시작/종료 판단
@dataclass
class BossWatcher:
    start_hold: float = 2.0
    end_grace: float = 60.0
    defeat_grace: float = 15.0
    defeat_hp: float = 0.04
    letterbox_pause: bool = True
    max_seconds: float = 25 * 60.0
    state: str = "idle"                      # idle | active
    _present_since: Optional[float] = None
    _absent_since: Optional[float] = None
    _started: float = 0.0
    last_hp: Optional[float] = None
    min_hp: Optional[float] = None
    in_cutscene: bool = False
    _hp_before_absent: Optional[float] = None

    @staticmethod
    def from_cfg(c: dict) -> "BossWatcher":
        return BossWatcher(float(c["start_hold"]), float(c["end_grace"]), float(c["defeat_grace"]), float(c["defeat_hp"]),
                           bool(c["letterbox_pause"]), float(c["max_minutes"]) * 60.0)

    def update(self, t: float, present: bool, hp: Optional[float] = None, cutscene: bool = False) -> List[Tuple[str, dict]]:
        ev: List[Tuple[str, dict]] = []
        self.in_cutscene = bool(cutscene)
        if self.state == "idle":
            if present:
                if self._present_since is None:
                    self._present_since = t
                if t - self._present_since >= self.start_hold:
                    self.state = "active"
                    self._started = self._present_since
                    self._absent_since = None
                    self.last_hp = self.min_hp = hp
                    ev.append(("start", {"t": self._started}))
            else:
                self._present_since = None
            return ev
        # active
        if present:
            self._absent_since = None
            if hp is not None:
                self.last_hp = hp
                self.min_hp = hp if self.min_hp is None else min(self.min_hp, hp)
        else:
            if self.letterbox_pause and cutscene:
                self._absent_since = None            # 연출 중: 사라진 시간을 세지 않는다
            elif self._absent_since is None:
                self._absent_since = t
                self._hp_before_absent = self.last_hp
        if self._absent_since is not None:
            defeated = self._hp_before_absent is not None and self._hp_before_absent <= self.defeat_hp
            grace = self.defeat_grace if defeated else self.end_grace
            if t - self._absent_since >= grace:
                ev.append(self._end("defeat" if defeated else "bar_lost", t))
        if self.state == "active" and t - self._started >= self.max_seconds:
            ev.append(self._end("max_time", t))
        return ev

    def _end(self, reason: str, t: float) -> Tuple[str, dict]:
        started = self._started
        self.reset()
        return ("end", {"reason": reason, "t": t, "started": started})

    def reset(self) -> None:
        self.state = "idle"
        self._present_since = self._absent_since = None
        self.last_hp = self.min_hp = self._hp_before_absent = None

    def abort(self) -> None:
        """밖(수동 조작)에서 녹화를 멈췄을 때: 이번 보스전은 더 다루지 않는다 (체력바가 사라져야 다시 시작할 수 있다)."""
        self.reset()
        self._present_since = float("inf")      # 지금 보이는 바로는 다시 시작하지 않는다

    def status_text(self, t: float) -> str:
        if self.state == "idle":
            return "보스 체력바를 기다리는 중" if self._present_since in (None, float("inf")) else "체력바가 보여요 — 확인 중…"
        hp = ("  · 남은 체력 약 %d%%" % round(self.last_hp * 100)) if self.last_hp is not None else ""
        if self.in_cutscene:
            return "보스전 녹화 중 · 연출 중(종료 대기 멈춤)" + hp
        if self._absent_since is not None:
            defeated = self._hp_before_absent is not None and self._hp_before_absent <= self.defeat_hp
            left = (self.defeat_grace if defeated else self.end_grace) - (t - self._absent_since)
            return "보스전 녹화 중 · 체력바 안 보임 — %d초 뒤 종료%s" % (max(int(left), 0), "(처치로 판단)" if defeated else "")
        return "보스전 녹화 중" + hp


def fix_abort(w: BossWatcher, present: bool) -> None:
    """abort 뒤 바가 사라졌으면 다시 시작할 수 있게 푼다."""
    if not present and w._present_since == float("inf"):
        w._present_since = None


# ---------------------------------------------------------------- 녹화본으로 시험
@dataclass
class Segment:
    start: float
    end: float
    reason: str
    min_hp: Optional[float] = None


def scan_video(path: str, det: Detector, wcfg: dict, step: float = 0.5, progress=None, cancel=None) -> Tuple[List[Segment], dict]:
    """녹화된 영상을 훑어서 보스전 구간을 찾는다. (구간 목록, 통계) — 실제 녹화와 같은 판단기(BossWatcher)를 쓴다."""
    cv2 = _cv2()
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError("영상을 열 수 없어요: " + str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    dur = total / fps if fps else 0
    w = BossWatcher.from_cfg(wcfg)
    segs: List[Segment] = []
    scores: List[float] = []
    n_present = 0
    n = 0
    t = 0.0
    cur_start = None
    while True:
        if cancel is not None and cancel.is_set():
            break
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
        ok, fr = cap.read()
        if not ok:
            break
        present, sc, hp = det.present(fr)
        scores.append(sc)
        n += 1
        n_present += 1 if present else 0
        for kind, info in w.update(t, present, hp, letterbox(normalize(fr)) if wcfg.get("letterbox_pause") else False):
            if kind == "start":
                cur_start = info["t"]
            elif kind == "end":
                segs.append(Segment(info["started"], info["t"], info["reason"], w.min_hp))
                cur_start = None
        if progress and dur:
            progress(min(t / dur, 1.0))
        t += step
    if w.state == "active" and cur_start is not None:
        segs.append(Segment(cur_start, t, "영상 끝", w.min_hp))
    cap.release()
    stats = {"frames": n, "present": n_present, "score_max": max(scores) if scores else 0.0, "score_median": float(np.median(scores)) if scores else 0.0,
             "duration": dur}
    return segs, stats
