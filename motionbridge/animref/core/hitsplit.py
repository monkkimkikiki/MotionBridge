"""라이브러리에 이미 있는 (길게 잘린) 클립을 타수별(1타·2타·3타…)로 나눈다.

수집할 때 쓰는 '한 타씩 나누기'와 같은 규칙을 이미 만들어진 클립 하나에 적용한다.
  1) 클립 안의 타격음(효과음)과 화면 번쩍임에서 '타격 후보'를 찾고
  2) 가까이 붙은 후보는 한 타로 합친 뒤
  3) 이웃한 타격과의 중간 지점에서 끊어 한 타씩 구간을 만든다.
찾기(analyze)와 저장(apply)을 나눠 두어서, 사이에 사용자가 결과를 보고 고칠 수 있다."""
from pathlib import Path

from . import Cancelled, media, noop
from .edit import new_from_range, source_for
from .library import delete_clip, file_of, load_clip
from .signals import audio_onsets, hit_windows

MIN_HITS = 2   # 이보다 적게 남으면 나눌 이유가 없다


def flash_times(path, step_fps=15.0, thr=18.0, cancel=None):
    """화면 밝기가 갑자기 확 바뀐 순간(타격 이펙트/섬광) 시각들. 수집 분석의 번쩍임 기준과 같다."""
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return []
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, int(round(fps / step_fps)))
        out, prev, i = [], None, 0
        while cap.grab():
            if i % step == 0:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                ok, fr = cap.retrieve()
                if not ok:
                    break
                g = cv2.cvtColor(cv2.resize(fr, (96, 54)), cv2.COLOR_BGR2GRAY)
                m = float(g.mean())
                if prev is not None and abs(m - prev) > thr:
                    out.append(i / fps)
                prev = m
            i += 1
        return out
    finally:
        cap.release()


def find_candidates(path, duration, cancel=None):
    """(시각, 세기, 종류) 목록. 종류는 '소리' 또는 '번쩍임'. 오디오가 없으면 번쩍임만."""
    cands = []
    res = audio_onsets(path, with_strength=True)
    has_audio = res is not None
    if has_audio:
        for t, w in zip(res[0], res[1]):
            cands.append((float(t), float(w), "소리"))
    for t in flash_times(path, cancel=cancel):
        cands.append((float(t), 0.5, "번쩍임"))   # 소리는 6dB 이상이라 겹치면 소리가 이긴다
    cands = [c for c in cands if 0.0 <= c[0] <= duration]
    return cands, has_audio


def merge_cands(cands, min_gap=0.3):
    """가까이 붙은 후보(휘두르는 소리+맞는 소리 등)를 하나로. 더 센 쪽을 남긴다. [(시각, 종류)]"""
    out = []
    for t, w, kind in sorted(cands):
        if out and t - out[-1][0] < min_gap:
            if w > out[-1][1]:
                out[-1] = (t, w, kind)
        else:
            out.append((t, w, kind))
    return [(t, kind) for t, _w, kind in out]


def windows_for(hits, duration, pre=0.8, post=0.8, min_len=1.0):
    """타격 시각들 → 한 타씩 구간 [(시작, 끝)] (클립 안의 시간)."""
    ws = hit_windows({"t0": 0.0, "t1": duration}, sorted(hits), pre, post, min_len, duration)
    return [(w["t0"], w["t1"]) for w in ws]


def analyze(metas, s, log=noop, progress=noop, cancel=None):
    """선택한 클립마다 타격 후보를 찾는다. 결과는 편집 창에서 사용자가 고칠 수 있게 후보 그대로 돌려준다."""
    out = []
    for n, m in enumerate(metas):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        name = Path(m["_json"]).stem
        progress(n / max(1, len(metas)), f"타격 지점 찾는 중 {n + 1}/{len(metas)}")
        path = str(file_of(m, "video"))
        try:
            dur = float(m.get("duration") or 0) or media.probe(path).duration
            cands, has_audio = find_candidates(path, dur, cancel)
        except Cancelled:
            raise
        except Exception as e:
            log(f"  {name}: 분석 실패 ({e})")
            continue
        out.append({"meta": m, "name": name, "duration": dur, "cands": cands, "has_audio": has_audio})
    progress(1.0, "완료")
    return out


def apply(items, s, keep_original=True, log=noop, progress=noop, cancel=None):
    """items: [{"meta", "duration", "hits":[시각…], "pre", "post", "min_len"}].
    각 클립을 한 타씩 잘라 새 클립으로 저장한다. 중간에 실패하면 그 클립에서 만든 것은 지우고 원본은 그대로 둔다.
    돌려주는 값: {"made": 만든 개수, "clips": 처리한 클립 수, "removed": 지운 원본 수, "failed": [(이름, 이유)]}"""
    res = {"made": 0, "clips": 0, "removed": 0, "failed": []}
    for n, it in enumerate(items):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        m, dur = it["meta"], float(it["duration"])
        name = Path(m["_json"]).stem
        progress(n / max(1, len(items)), f"저장 중 {n + 1}/{len(items)}: {name}")
        if len(it["hits"]) < MIN_HITS:
            continue
        if it.get("wins"):   # 편집 창에서 사용자가 고친 구간 그대로
            pairs = sorted(zip(map(float, it["hits"]), it["wins"]))
            hits = [h for h, _w in pairs]
            wins = [(max(0.0, float(a)), min(dur, float(b))) for _h, (a, b) in pairs]
        else:
            hits = sorted(it["hits"])
            wins = windows_for(hits, dur, it.get("pre", 0.8), it.get("post", 0.8), it.get("min_len", 1.0))
        path, offset, is_source = source_for(m)
        start0 = float(m.get("start_in_source", 0.0))
        shift = start0 if is_source else 0.0   # 원본에서 자르면 클립의 시간에 시작 위치를 더해야 한다
        made = []
        try:
            for i, ((a, b), h) in enumerate(zip(wins, hits), 1):
                if b - a < 0.2:
                    continue
                extra = {"view": "hit", "hit_index": i, "hit_count": len(hits), "hit_time": round(start0 + h, 3),
                         "manual": False, "split_from": name}
                jp = new_from_range(m, path, offset, shift + a, shift + b, s, m.get("label_manual"), log,
                                    suffix=f"hit{i}of{len(hits)}", extra=extra)
                made.append(jp)
        except Exception as e:
            for jp in made:   # 반쯤 만들어진 결과는 치운다
                c = load_clip(jp)
                if c:
                    delete_clip(c)
            log(f"  {name}: 나누기 실패 ({e})")
            res["failed"].append((name, str(e)))
            continue
        res["made"] += len(made)
        res["clips"] += 1
        if made and not keep_original:
            if delete_clip(m):
                res["removed"] += 1
            else:
                res["failed"].append((name, "원본 클립을 지우지 못했어요 (다른 프로그램이 열고 있음). 새 클립은 만들어졌어요."))
        log(f"  {name}: {len(made)}개로 나눔")
    progress(1.0, "완료")
    return res


# ---------------------------------------------------------------------------- 편집 창용 미리보기 자료
def decode_frames(path, width=320, fps_cap=24.0, max_frames=600):
    """저해상도 미리보기 프레임 (n, h, w, 3) RGB와 그 fps. 영상 플레이어 없이 그려서 프레임 단위로 정확히 보여준다.
    길이가 길면 fps를 낮춰 메모리를 제한한다 (최대 max_frames장)."""
    import subprocess

    import numpy as np

    info = media.probe(str(path))
    dur = info.duration or 1.0
    fps = min(fps_cap, info.fps or 30.0)
    if dur * fps > max_frames:
        fps = max(4.0, max_frames / dur)
    w = int(width)
    h = max(2, int(round(w * (info.h or 9) / max(1, info.w or 16) / 2)) * 2)
    args = [media.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-vf", f"fps={fps:.4f},scale={w}:{h}", "-an", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    r = subprocess.run(args, capture_output=True, creationflags=media._NO_WINDOW, timeout=600)
    size = w * h * 3
    n = len(r.stdout) // size
    if n == 0:
        raise RuntimeError("미리보기를 만들지 못했어요: " + r.stderr.decode("utf-8", "replace")[-200:])
    frames = np.frombuffer(r.stdout[: n * size], np.uint8).reshape(n, h, w, 3)
    return {"frames": frames, "fps": float(fps), "w": w, "h": h, "duration": float(dur)}


def audio_envelope(path, hop=0.02):
    """소리 크기 곡선 (0~1, hop초 간격). 타격음이 어디서 나는지 눈으로 볼 수 있게 줄에 그린다. 소리가 없으면 None."""
    import subprocess

    import numpy as np

    sr = 8000
    args = [media.ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i", str(path),
            "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    try:
        r = subprocess.run(args, capture_output=True, creationflags=media._NO_WINDOW, timeout=600)
    except Exception:
        return None
    raw = r.stdout
    n = int(sr * hop)
    if len(raw) < n * 4:
        return None
    x = np.frombuffer(raw[: len(raw) // 2 * 2], np.int16).astype(np.float32) / 32768.0
    fr = x[: len(x) // n * n].reshape(-1, n)
    e = 10.0 * np.log10((fr ** 2).mean(axis=1) + 1e-10)
    top = float(e.max())
    if top < -60:
        return None
    floor = max(float(e.min()), top - 50.0)
    return np.clip((e - floor) / max(1e-6, top - floor), 0.0, 1.0)


def preview_data(path, log=noop, progress=noop, cancel=None):
    """편집 창 미리보기: 프레임 + 소리 곡선 (Job으로 돌릴 수 있는 모양)."""
    progress(0.1, "미리보기 만드는 중...")
    d = decode_frames(path)
    d["env"] = audio_envelope(path)
    d["path"] = str(path)
    return d


# ---------------------------------------------------------------------------- 편집 모델 (Qt와 무관)
class ClipEdit:
    """클립 하나의 나누기 편집 상태.
    - cands: 자동으로 찾은 타격 후보 (시각, 세기, 종류)
    - added: 사용자가 직접 추가한 타격 시각
    - off: 뺀 타격 (key = 시각을 0.01초로 반올림)
    - ov: 사용자가 직접 고친 구간 {key: (시작, 끝)}  — 없으면 자동 계산(이웃 타와 중간에서 끊기)"""

    MIN_PIECE = 0.1

    def __init__(self, meta, name, duration, cands, has_audio, pre=0.8, post=0.8, gap=0.3, min_len=1.0, fps=30.0):
        self.meta, self.name, self.duration = meta, name, float(duration)
        self.cands, self.has_audio = list(cands), has_audio
        self.pre, self.post, self.gap, self.min_len = pre, post, gap, min_len
        self.fps = float(fps or 30.0)
        self.added, self.off, self.ov = [], set(), {}

    @staticmethod
    def key(t):
        return round(float(t), 2)

    def set_params(self, pre=None, post=None, gap=None):
        if pre is not None:
            self.pre = pre
        if post is not None:
            self.post = post
        if gap is not None:
            self.gap = gap

    def hits(self):
        """[(시각, 종류, 사용 여부)] — 직접 추가한 타격은 자동 후보보다 우선한다."""
        allc = self.cands + [(t, 1e9, "직접") for t in self.added]
        return [(t, k, self.key(t) not in self.off) for t, k in merge_cands(allc, self.gap)]

    def pieces(self):
        """화면에 보일 모든 타 (뺀 것 포함). dict: idx, t, kind, on, key, a, b, edited."""
        hs = self.hits()
        on_t = [t for t, _k, on in hs if on]
        auto = windows_for(on_t, self.duration, self.pre, self.post, self.min_len) if on_t else []
        auto_by = {self.key(t): w for t, w in zip(on_t, auto)}
        out = []
        for i, (t, kind, on) in enumerate(hs):
            k = self.key(t)
            if k in self.ov:
                (a, b), edited = self.ov[k], True
            elif on:
                (a, b), edited = auto_by[k], False
            else:   # 뺀 타격: 미리보기용으로 앞뒤 0.5초
                (a, b), edited = (max(0.0, t - 0.5), min(self.duration, t + 0.5)), False
            out.append({"idx": i, "t": t, "kind": kind, "on": on, "key": k, "a": a, "b": b, "edited": edited})
        return out

    def on_pieces(self):
        return [p for p in self.pieces() if p["on"]]

    def ready(self):
        return len(self.on_pieces()) >= MIN_HITS

    def _find(self, key):
        return next((p for p in self.pieces() if p["key"] == key), None)

    def snap(self, t):
        return round(float(t) * self.fps) / self.fps

    def set_edge(self, key, side, t):
        """구간의 시작('a') 또는 끝('b')을 옮긴다. 프레임 단위로 붙고, 최소 길이·클립 범위를 지킨다."""
        p = self._find(key)
        if p is None:
            return False
        a, b = p["a"], p["b"]
        t = self.snap(t)
        if side == "a":
            a = min(max(0.0, t), b - self.MIN_PIECE)
        else:
            b = max(min(self.duration, t), a + self.MIN_PIECE)
        if (round(a, 4), round(b, 4)) == (round(p["a"], 4), round(p["b"], 4)):
            return False   # 바뀐 게 없음
        self.ov[key] = (round(a, 4), round(b, 4))
        return True

    def nudge(self, key, side, frames):
        p = self._find(key)
        return p is not None and self.set_edge(key, side, (p[side] + frames / self.fps))

    def reset(self, key):
        return self.ov.pop(key, None) is not None

    def set_on(self, key, on):
        if on:
            if key not in self.off:
                return False
            self.off.discard(key)
        else:
            if key in self.off:
                return False
            self.off.add(key)
        return True

    def add_hit(self, t):
        """재생 위치에 타격을 추가한다. 이미 가까운(합치기 기준 안) 타격이 있으면 그 타격을 이 위치로 바꾼다."""
        t = round(min(max(0.0, float(t)), self.duration), 3)
        if any(abs(t - x) < 1e-3 for x in self.added):
            return None
        self.added.append(t)
        self.off.discard(self.key(t))
        return self.key(t)

    def remove_added(self, key):
        before = len(self.added)
        self.added = [x for x in self.added if self.key(x) != key]
        self.ov.pop(key, None)
        return len(self.added) != before

    def snapshot(self):
        return (list(self.added), set(self.off), dict(self.ov))

    def restore(self, snap):
        self.added, self.off, self.ov = list(snap[0]), set(snap[1]), dict(snap[2])

    def to_item(self):
        ps = self.on_pieces()
        return {"meta": self.meta, "duration": self.duration, "hits": [p["t"] for p in ps],
                "wins": [(p["a"], p["b"]) for p in ps]}
