"""클립 편집: 부분 속도 조절(타임 스트레치), 부드러운 가속/감속(램프), 조각 자르기·삭제·복제·순서 바꾸기,
정지 프레임, 여러 클립 이어붙이기.

편집은 '조각(Piece) 목록'으로 표현한다. 조각 하나 = 어떤 영상의 a~b초 구간 + 재생 속도.
목록을 순서대로 이어붙이면 결과 영상이 되고, 렌더링은 ffmpeg 한 번으로 한다."""
import copy
import json
import os
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from . import media, noop
from .library import file_of, save_clip

SPEED_MIN, SPEED_MAX = 0.1, 8.0
MIN_PIECE = 0.05          # 조각 최소 길이(원본 기준 초)
MAX_PIECES = 50           # ffmpeg 명령 길이 한계 때문
ATEMPO_RANGE = (0.25, 4.0)  # 이 범위 밖의 속도에서는 소리를 무음으로 둔다 (음질이 심하게 깨짐)


@dataclass
class Piece:
    src: int                 # sources 목록의 번호
    a: float                 # 원본 시작(초)
    b: float                 # 원본 끝(초)
    speed: float = 1.0       # 1.0 = 원래 속도, 0.5 = 절반 속도(슬로모션), 2.0 = 2배속
    hold: float = 0.0        # 0보다 크면 '정지 프레임': a 시각의 한 장면을 hold초 동안 보여줌

    def out_len(self) -> float:
        return float(self.hold) if self.hold > 0 else (self.b - self.a) / self.speed


def clamp_speed(v) -> float:
    return round(min(SPEED_MAX, max(SPEED_MIN, float(v))), 3)


class EditModel:
    """조각 목록 + 되돌리기. 화면 없이도 동작하므로 따로 시험할 수 있다."""

    def __init__(self, sources):
        # sources: [{"path": str, "duration": float, "name": str}]
        self.sources = list(sources)
        self.pieces = [Piece(i, 0.0, round(float(s["duration"]), 3)) for i, s in enumerate(self.sources)]
        self._undo, self._redo = [], []

    # ---------------------------------------------------------------- 상태
    def _push(self):
        self._undo.append(copy.deepcopy(self.pieces))
        self._undo = self._undo[-100:]
        self._redo.clear()

    def undo(self):
        if not self._undo:
            return False
        self._redo.append(copy.deepcopy(self.pieces))
        self.pieces = self._undo.pop()
        return True

    def redo(self):
        if not self._redo:
            return False
        self._undo.append(copy.deepcopy(self.pieces))
        self.pieces = self._redo.pop()
        return True

    def total(self) -> float:
        return sum(p.out_len() for p in self.pieces)

    def layout(self):
        """각 조각의 결과 영상 안 위치 [(시작, 끝)]"""
        t, out = 0.0, []
        for p in self.pieces:
            out.append((t, t + p.out_len()))
            t += p.out_len()
        return out

    def is_untouched(self) -> bool:
        return all(p.hold == 0 and abs(p.speed - 1.0) < 1e-6 and p.a <= 1e-6 and
                   abs(p.b - self.sources[p.src]["duration"]) < 1e-3 for p in self.pieces) and \
            len(self.pieces) == len(self.sources) and [p.src for p in self.pieces] == list(range(len(self.sources)))

    # ---------------------------------------------------------------- 편집 동작
    def split(self, i, t) -> bool:
        """원본 시각 t에서 i번 조각을 둘로 나눈다."""
        p = self.pieces[i]
        if p.hold > 0 or not (p.a + MIN_PIECE <= t <= p.b - MIN_PIECE) or len(self.pieces) >= MAX_PIECES:
            return False
        self._push()
        t = round(float(t), 3)
        self.pieces[i:i + 1] = [Piece(p.src, p.a, t, p.speed), Piece(p.src, t, p.b, p.speed)]
        return True

    def delete(self, i) -> bool:
        if len(self.pieces) <= 1:
            return False
        self._push()
        del self.pieces[i]
        return True

    def duplicate(self, i) -> bool:
        if len(self.pieces) >= MAX_PIECES:
            return False
        self._push()
        self.pieces.insert(i + 1, copy.deepcopy(self.pieces[i]))
        return True

    def move(self, i, d) -> int:
        """i번 조각을 d칸(-1 앞으로 / +1 뒤로) 옮기고 새 번호를 돌려준다."""
        j = i + d
        if not (0 <= j < len(self.pieces)):
            return i
        self._push()
        self.pieces[i], self.pieces[j] = self.pieces[j], self.pieces[i]
        return j

    def set_speed(self, i, v) -> bool:
        p = self.pieces[i]
        if p.hold > 0:
            return False
        self._push()
        p.speed = clamp_speed(v)
        return True

    def set_all_speed(self, v):
        self._push()
        for p in self.pieces:
            if p.hold <= 0:
                p.speed = clamp_speed(v)

    def set_hold(self, i, seconds) -> bool:
        p = self.pieces[i]
        if p.hold <= 0:
            return False
        self._push()
        p.hold = round(min(10.0, max(0.1, float(seconds))), 2)
        return True

    def add_hold(self, i, t, seconds=1.0) -> int:
        """원본 시각 t의 장면을 seconds초 멈춘다. 새로 생긴 정지 조각의 번호를 돌려준다(-1이면 실패)."""
        p = self.pieces[i]
        if p.hold > 0 or not (p.a <= t <= p.b) or len(self.pieces) >= MAX_PIECES - 1:
            return -1
        self._push()
        t = round(float(t), 3)
        hold = Piece(p.src, t, t, 1.0, hold=round(min(10.0, max(0.1, float(seconds))), 2))
        parts = []
        if t - p.a >= MIN_PIECE:
            parts.append(Piece(p.src, p.a, t, p.speed))
        k = len(parts)
        parts.append(hold)
        if p.b - t >= MIN_PIECE:
            parts.append(Piece(p.src, t, p.b, p.speed))
        self.pieces[i:i + 1] = parts
        return i + k

    @staticmethod
    def _ramp_parts(p, s0, s1, n):
        length = p.b - p.a
        parts = []
        for k in range(n):
            a = p.a + length * k / n
            b = p.a + length * (k + 1) / n if k < n - 1 else p.b
            sp = s0 * (s1 / s0) ** (k / (n - 1))
            parts.append(Piece(p.src, round(a, 3), round(b, 3), clamp_speed(sp)))
        return parts

    @staticmethod
    def _ramp_count(length, n, room):
        n = int(n or max(4, min(10, round(length * 3))))
        n = min(n, room)
        return n if (n >= 2 and length / n >= MIN_PIECE) else 0

    def ramp(self, i, s0, s1, n=None) -> bool:
        """i번 조각을 같은 길이로 n등분하고, 속도를 s0에서 s1까지 매끄럽게(곱셈 비율로) 바꾼다."""
        p = self.pieces[i]
        if p.hold > 0:
            return False
        n = self._ramp_count(p.b - p.a, n, MAX_PIECES - len(self.pieces) + 1)
        if not n:
            return False
        self._push()
        self.pieces[i:i + 1] = self._ramp_parts(p, clamp_speed(s0), clamp_speed(s1), n)
        return True

    # ---- 조각 안의 일부 구간(t0~t1, 원본 시각)만 바꾸기: 타임 스트레치
    def _plan_isolate(self, i, t0, t1):
        p = self.pieces[i]
        if p.hold > 0:
            return None
        t0, t1 = max(p.a, min(float(t0), p.b)), max(p.a, min(float(t1), p.b))
        if t1 - t0 < MIN_PIECE:
            return None
        if t0 - p.a < MIN_PIECE:
            t0 = p.a
        if p.b - t1 < MIN_PIECE:
            t1 = p.b
        extra = (1 if t0 > p.a else 0) + (1 if t1 < p.b else 0)
        if len(self.pieces) + extra > MAX_PIECES:
            return None
        return round(t0, 3), round(t1, 3), extra

    def _do_isolate(self, i, t0, t1):
        p = self.pieces[i]
        parts = []
        if t0 > p.a:
            parts.append(Piece(p.src, p.a, t0, p.speed))
        mid = len(parts)
        parts.append(Piece(p.src, t0, t1, p.speed))
        if t1 < p.b:
            parts.append(Piece(p.src, t1, p.b, p.speed))
        self.pieces[i:i + 1] = parts
        return i + mid

    def range_speed(self, i, t0, t1, speed) -> int:
        """조각 안의 t0~t1 구간만 속도를 바꾼다. 그 구간 조각의 번호를 돌려준다(-1이면 실패)."""
        plan = self._plan_isolate(i, t0, t1)
        if not plan:
            return -1
        self._push()
        mid = self._do_isolate(i, plan[0], plan[1])
        self.pieces[mid].speed = clamp_speed(speed)
        return mid

    def range_ramp(self, i, t0, t1, s0, s1, n=None) -> int:
        """조각 안의 t0~t1 구간만 속도를 s0에서 s1까지 매끄럽게 바꾼다. 첫 조각 번호를 돌려준다(-1이면 실패)."""
        plan = self._plan_isolate(i, t0, t1)
        if not plan:
            return -1
        a, b, extra = plan
        room = MAX_PIECES - (len(self.pieces) + extra) + 1
        cnt = self._ramp_count(b - a, n, room)
        if not cnt:
            return -1
        self._push()
        mid = self._do_isolate(i, a, b)
        mp = self.pieces[mid]
        self.pieces[mid:mid + 1] = self._ramp_parts(mp, clamp_speed(s0), clamp_speed(s1), cnt)
        return mid

    def trim(self, i, a, b) -> bool:
        p = self.pieces[i]
        dur = self.sources[p.src]["duration"]
        a, b = max(0.0, float(a)), min(dur, float(b))
        if p.hold > 0 or b - a < MIN_PIECE:
            return False
        self._push()
        p.a, p.b = round(a, 3), round(b, 3)
        return True

    def append_source(self, path, duration, name=None) -> bool:
        if len(self.pieces) >= MAX_PIECES:
            return False
        self._push()
        self.sources.append({"path": str(path), "duration": float(duration), "name": name or Path(path).name})
        self.pieces.append(Piece(len(self.sources) - 1, 0.0, round(float(duration), 3)))
        return True

    # ---------------------------------------------------------------- 미리보기용
    def locate(self, out_t):
        """결과 영상의 out_t초가 몇 번 조각의 어디인지 (번호, 원본 시각)"""
        for i, (a, b) in enumerate(self.layout()):
            if a <= out_t < b or i == len(self.pieces) - 1:
                p = self.pieces[i]
                frac = 0.0 if b <= a else min(1.0, max(0.0, (out_t - a) / (b - a)))
                return i, p.a + (p.b - p.a) * frac
        return 0, 0.0

    def to_json(self):
        return {"sources": [s.get("name") or Path(s["path"]).name for s in self.sources],
                "pieces": [asdict(p) for p in self.pieces]}


# ====================================================================== 렌더링
def _atempo(speed):
    """속도를 atempo 필터 체인으로. 범위 밖이면 None (무음 처리)."""
    if abs(speed - 1.0) < 1e-6:
        return ""
    if not (ATEMPO_RANGE[0] <= speed <= ATEMPO_RANGE[1]):
        return None
    chain, s = [], speed
    while s > 2.0:
        chain.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        chain.append("atempo=0.5")
        s /= 0.5
    chain.append(f"atempo={s:.5f}")
    return ",".join(chain)


def build_args(sources, pieces, out, crf=23, size=None, fps=None, audio_flags=None):
    """ffmpeg 인자 목록을 만든다. (입력은 조각마다 따로 열어서 -ss로 빠르게 찾아간다)"""
    if not pieces:
        raise ValueError("편집할 조각이 없어요.")
    if len(pieces) > MAX_PIECES:
        raise ValueError(f"조각이 너무 많아요 (최대 {MAX_PIECES}개).")
    if size is None or fps is None:
        info = media.probe(sources[pieces[0].src]["path"])
        size = size or (media.even(info.w), media.even(info.h))
        fps = fps or info.fps
    W, H = size
    fps = round(min(60.0, max(10.0, float(fps))), 3)
    if audio_flags is None:
        audio_flags = [media.has_audio(s["path"]) for s in sources]
    any_audio = any(audio_flags)

    args = ["-y", "-loglevel", "error"]
    chains, n_in = [], 0
    norm = (f"fps={fps},scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p")
    for i, p in enumerate(pieces):
        path = sources[p.src]["path"]
        ln = p.out_len()
        if p.hold > 0:
            args += ["-ss", f"{p.a:.3f}", "-i", str(path)]
            chains.append(f"[{n_in}:v]trim=end_frame=1,setpts=PTS-STARTPTS,"
                          f"tpad=stop_mode=clone:stop_duration={p.hold + 0.5:.3f},{norm},"
                          f"trim=duration={p.hold:.3f},setpts=PTS-STARTPTS[v{i}]")
            n_in += 1
            tempo = None            # 정지 프레임은 소리 없음
            has_a = False
        else:
            args += ["-ss", f"{p.a:.3f}", "-t", f"{p.b - p.a:.3f}", "-i", str(path)]
            chains.append(f"[{n_in}:v]setpts=(PTS-STARTPTS)/{p.speed:.6f},{norm}[v{i}]")
            vin = n_in
            n_in += 1
            tempo = _atempo(p.speed)
            has_a = audio_flags[p.src] and tempo is not None
        if any_audio:
            if has_a:
                chains.append(f"[{vin}:a]asetpts=PTS-STARTPTS" + (f",{tempo}" if tempo else "") +
                              f",aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                              f"apad,atrim=duration={ln:.4f}[a{i}]")
            else:
                args += ["-f", "lavfi", "-t", f"{ln:.4f}", "-i", "anullsrc=r=48000:cl=stereo"]
                chains.append(f"[{n_in}:a]aformat=sample_fmts=fltp:channel_layouts=stereo,"
                              f"atrim=duration={ln:.4f}[a{i}]")
                n_in += 1
    if any_audio:
        cat = "".join(f"[v{i}][a{i}]" for i in range(len(pieces))) + f"concat=n={len(pieces)}:v=1:a=1[v][a]"
    else:
        cat = "".join(f"[v{i}]" for i in range(len(pieces))) + f"concat=n={len(pieces)}:v=1:a=0[v]"
    chains.append(cat)
    args += ["-filter_complex", ";".join(chains), "-map", "[v]"]
    if any_audio:
        args += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", str(out)]
    return args


def render(model_or_sources, pieces=None, out=None, crf=23, log=noop):
    """조각 목록을 한 개의 mp4로 만든다."""
    if isinstance(model_or_sources, EditModel):
        sources, pieces = model_or_sources.sources, model_or_sources.pieces
    else:
        sources = model_or_sources
    args = build_args(sources, pieces, out, crf)
    r = subprocess.run([media.ffmpeg_exe(), "-hide_banner", *map(str, args)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", creationflags=media._NO_WINDOW)
    if r.returncode != 0 or not Path(out).exists():
        raise RuntimeError("영상 만들기 실패: " + r.stderr.strip()[-500:])


# ====================================================================== 라이브러리에 저장
_AI_KEYS = ("vlm_hash", "vlm_fail_hash", "clip_top", "vlm_label", "vlm_conf", "vlm_reason",
            "ai_label", "ai_conf", "ai_top", "ai_reason", "ai_engine", "group",
            "hit_index", "hit_count", "hit_time")


def _invalidate_ai(meta):
    for kind in ("emb", "sheet"):
        try:
            file_of(meta, kind).unlink(missing_ok=True)
        except OSError:
            pass
    for k in ("vlm_hash", "vlm_fail_hash", "clip_top"):
        meta.pop(k, None)


def _unique(cdir, base):
    name, k = base, 2
    while (cdir / f"{name}.mp4").exists() or (cdir / f"{name}.json").exists():
        name, k = f"{base}{k}", k + 1
    return name


def save_new(meta, model, s, suffix="retime", label=None, log=noop):
    """편집 결과를 새 클립으로 저장 (원래 클립은 그대로). meta는 결과 클립의 바탕이 되는 첫 클립."""
    cdir = Path(meta["_json"]).parent
    base_old = Path(meta["_json"]).stem
    name = _unique(cdir, f"{base_old}_{suffix}")
    mp4 = cdir / f"{name}.mp4"
    render(model, out=mp4, crf=s.get("crf", 23), log=log)
    info = media.probe(mp4)
    media.thumbnail(mp4, info.duration / 2, cdir / f"{name}.thumb.jpg")
    new = {k: v for k, v in meta.items() if not k.startswith("_")}
    for k in _AI_KEYS:
        new.pop(k, None)
    new.update(duration=round(info.duration, 3), retimed=True, manual=True, edited_from=base_old,
               label_manual=label if label else meta.get("label_manual"), edit_list=model.to_json(),
               created=datetime.now().isoformat(timespec="seconds"))
    jp = cdir / f"{name}.json"
    jp.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"편집 결과 저장: {name}.mp4 ({info.duration:.2f}초)")
    return str(jp)


def save_overwrite(meta, model, s, log=noop):
    """편집 결과로 이 클립을 덮어쓴다. (속도가 구워지므로 이후엔 원본 기준 늘리기가 안 된다)"""
    mp4 = file_of(meta, "video")
    tmp = mp4.with_name(mp4.stem + ".editing.mp4")
    try:
        render(model, out=tmp, crf=s.get("crf", 23), log=log)
        os.replace(tmp, mp4)
    except OSError as e:
        raise RuntimeError("클립 파일이 다른 프로그램에서 열려 있어서 바꾸지 못했어요. 닫고 다시 시도하세요.") from e
    finally:
        tmp.unlink(missing_ok=True)
    info = media.probe(mp4)
    meta.update(duration=round(info.duration, 3), retimed=True, edit_list=model.to_json(),
                edited=datetime.now().isoformat(timespec="seconds"))
    _invalidate_ai(meta)
    media.thumbnail(mp4, info.duration / 2, file_of(meta, "thumb"))
    save_clip(meta)
    log(f"클립 편집 저장: {mp4.name} ({info.duration:.2f}초)")
    return meta["_json"]
