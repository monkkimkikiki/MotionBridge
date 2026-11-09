"""애니메이션 가공: 구간 자르기 / 늘리기·줄이기 / fps 변환 / 루프 이음 / 키프레임 압축.

모든 함수는 `Animation`(고정 fps, rot (T,J,4), pos (T,J,3))을 받아 새 객체를 돌려준다(원본 불변).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np

from . import quat as Q
from .model import Animation


# ------------------------------------------------------------------------------------------ 옵션
@dataclass
class EditOptions:
    """내보내기 직전에 적용하는 가공 옵션. 기본값은 아무것도 하지 않는다."""

    trim_start: int = 0  # 시작 프레임(0 기준)
    trim_end: Optional[int] = None  # 끝 프레임(포함). None: 끝까지
    speed: float = 1.0  # 재생 속도 배율. 2.0 = 2배 빠르게(길이 절반)
    frames: Optional[int] = None  # 목표 프레임 수(speed 보다 우선)
    seconds: Optional[float] = None  # 목표 길이(초). frames 가 없을 때
    fps: Optional[float] = None  # 목표 fps. None: 그대로
    loop_frames: int = 0  # >0: 끝 N프레임을 첫 포즈로 이어 붙여 루프가 되게 함
    compress: bool = False  # 키프레임 압축
    rot_tol_deg: float = 0.5  # 압축 허용 오차: 회전(도)
    pos_tol_cm: float = 0.1  # 압축 허용 오차: 위치(cm)

    def active(self) -> bool:
        return bool(
            self.trim_start > 0
            or self.trim_end is not None
            or abs(self.speed - 1.0) > 1e-9
            or self.frames
            or self.seconds
            or self.fps
            or self.loop_frames > 0
            or self.compress
        )

    def describe(self) -> str:
        parts = []
        if self.trim_start > 0 or self.trim_end is not None:
            parts.append("구간 %d~%s" % (self.trim_start, "끝" if self.trim_end is None else self.trim_end))
        if self.frames:
            parts.append("%d프레임으로" % self.frames)
        elif self.seconds:
            parts.append("%.2f초로" % self.seconds)
        elif abs(self.speed - 1.0) > 1e-9:
            parts.append("속도 x%.2f" % self.speed)
        if self.fps:
            parts.append("%g fps" % self.fps)
        if self.loop_frames > 0:
            parts.append("루프 이음 %d프레임" % self.loop_frames)
        if self.compress:
            parts.append("키 압축(%.2f°, %.2fcm)" % (self.rot_tol_deg, self.pos_tol_cm))
        return ", ".join(parts) or "가공 없음"


@dataclass
class KeySet:
    """관절마다 남긴 키 프레임 번호(오름차순, 항상 첫/끝 프레임 포함)."""

    rot: List[np.ndarray]
    pos: List[np.ndarray]
    n_frames: int

    def mask(self) -> Tuple[np.ndarray, np.ndarray]:
        """(T,J) 불리언 마스크 두 개(회전, 위치)."""
        J = len(self.rot)
        mr = np.zeros((self.n_frames, J), dtype=bool)
        mp = np.zeros((self.n_frames, J), dtype=bool)
        for j in range(J):
            mr[self.rot[j], j] = True
            mp[self.pos[j], j] = True
        return mr, mp

    def total(self) -> int:
        return int(sum(len(k) for k in self.rot) + sum(len(k) for k in self.pos))


@dataclass
class EditInfo:
    """가공 결과 요약(사용자에게 보여줄 통계)."""

    frames_before: int = 0
    frames_after: int = 0
    fps_before: float = 0.0
    fps_after: float = 0.0
    keys_before: int = 0
    keys_after: int = 0
    max_rot_err_deg: float = 0.0
    max_pos_err: float = 0.0  # 스켈레톤 단위
    notes: List[str] = field(default_factory=list)

    def summary(self) -> str:
        s = "%d프레임@%gfps → %d프레임@%gfps" % (self.frames_before, self.fps_before, self.frames_after, self.fps_after)
        if self.keys_before:
            pct = 100.0 * self.keys_after / max(self.keys_before, 1)
            s += " · 키 %s → %s (%.0f%%) · 최대 오차 회전 %.2f°" % (format(self.keys_before, ","), format(self.keys_after, ","), pct, self.max_rot_err_deg)
        return s


# ------------------------------------------------------------------------------------------ 자르기 / 리샘플
def trim(anim: Animation, start: int = 0, end: Optional[int] = None) -> Animation:
    T = anim.n_frames
    a = int(np.clip(start, 0, T - 1))
    b = T - 1 if end is None else int(np.clip(end, a, T - 1))
    return anim.slice(a, b + 1)


def _sample(anim: Animation, src_idx: np.ndarray) -> Animation:
    """부동소수 프레임 위치에서 회전은 slerp, 위치는 선형으로 샘플."""
    T = anim.n_frames
    i0 = np.clip(np.floor(src_idx).astype(int), 0, T - 1)
    i1 = np.clip(i0 + 1, 0, T - 1)
    t = np.clip(src_idx - i0, 0.0, 1.0)
    rot = Q.slerp(anim.rot[i0], anim.rot[i1], t[:, None])
    pos = anim.pos[i0] * (1.0 - t)[:, None, None] + anim.pos[i1] * t[:, None, None]
    return rot, pos


def resample(anim: Animation, speed: float = 1.0, frames: Optional[int] = None, seconds: Optional[float] = None, fps: Optional[float] = None) -> Animation:
    """첫~끝 프레임 구간을 새 길이/fps 로 다시 샘플링한다.

    우선순위: frames > seconds > speed. fps 만 주면 길이(초)를 유지하고 프레임 수만 바꾼다.
    """
    T = anim.n_frames
    fps_out = float(fps) if fps else float(anim.fps)
    if fps_out <= 0 or anim.fps <= 0:
        raise ValueError("fps 는 0보다 커야 합니다.")
    if T < 2:
        return Animation(anim.name, fps_out, anim.rot.copy(), anim.pos.copy())
    span = (T - 1) / float(anim.fps)  # 첫 키에서 마지막 키까지 걸리는 시간
    if frames:
        N = max(int(frames), 1)
    elif seconds:
        N = max(int(round(float(seconds) * fps_out)), 1)
    else:
        if speed <= 0:
            raise ValueError("속도 배율은 0보다 커야 합니다.")
        N = max(int(round(span / float(speed) * fps_out)) + 1, 1)
    if N == 1:
        return Animation(anim.name, fps_out, anim.rot[:1].copy(), anim.pos[:1].copy())
    if N == T and abs(fps_out - anim.fps) < 1e-9:
        return anim.copy()
    src = np.arange(N, dtype=float) * ((T - 1) / float(N - 1))  # 새 프레임 k 가 가리키는 원본 프레임 위치
    rot, pos = _sample(anim, src)
    return Animation(anim.name, fps_out, Q.make_continuous(rot), pos)


def loop_blend(anim: Animation, n: int, skel_parents: Optional[np.ndarray] = None) -> Animation:
    """끝 n프레임을 첫 프레임 포즈로 서서히 이어서 마지막 프레임 = 첫 프레임이 되게 한다.

    루트 관절의 위치(이동량)는 그대로 둔다 — 이동하는 모션이 제자리로 끌려가는 것을 막기 위해.
    """
    T = anim.n_frames
    n = int(min(max(n, 0), T - 1))
    if n < 1:
        return anim.copy()
    out = anim.copy()
    w = np.linspace(0.0, 1.0, n + 1)[1:]
    w = w * w * (3.0 - 2.0 * w)  # smoothstep
    seg = slice(T - n, T)
    first_r = np.repeat(anim.rot[:1], n, axis=0)
    out.rot[seg] = Q.slerp(anim.rot[seg], first_r, w[:, None])
    first_p = np.repeat(anim.pos[:1], n, axis=0)
    blended = anim.pos[seg] * (1.0 - w)[:, None, None] + first_p * w[:, None, None]
    if skel_parents is not None:
        roots = np.flatnonzero(np.asarray(skel_parents) < 0)
        blended[:, roots] = anim.pos[seg][:, roots]
    out.pos[seg] = blended
    out.rot = Q.make_continuous(out.rot)
    return out


# ------------------------------------------------------------------------------------------ 구간 리타이밍
@dataclass
class Retime:
    """프레임 start~end(0부터, 끝 포함)를 length 프레임으로 줄이거나 늘린다. 구간 밖은 그대로 이어 붙인다."""

    start: int
    end: int
    length: int

    def span(self) -> int:
        return self.end - self.start + 1


def retime_segment(anim: Animation, start: int, end: int, length: int) -> Animation:
    """구간만 새 길이로 다시 샘플링한다. 구간의 첫/끝 포즈는 그대로라 앞뒤와 이음매가 생기지 않는다.

    전체 길이는 T - (end-start+1) + length 가 된다. length=1 이면 구간이 첫 포즈 하나로 합쳐진다.
    """
    T = anim.n_frames
    a = int(np.clip(start, 0, T - 1))
    b = int(np.clip(end, a, T - 1))
    m = max(int(length), 1)
    n = b - a + 1
    if m == n:
        return anim.copy()
    if n < 2:  # 프레임 하나를 늘리면 같은 포즈를 m 번 (줄이기는 그대로)
        idx = np.full(m, float(a))
    elif m == 1:
        idx = np.array([float(a)])
    else:
        idx = np.linspace(float(a), float(b), m)
    seg_rot, seg_pos = _sample(anim, idx)
    rot = np.concatenate([anim.rot[:a], seg_rot, anim.rot[b + 1:]], axis=0)
    pos = np.concatenate([anim.pos[:a], seg_pos, anim.pos[b + 1:]], axis=0)
    return Animation(anim.name, anim.fps, Q.make_continuous(rot), pos)


def apply_retimes(anim: Animation, edits) -> Animation:
    """편집을 차례로 적용한다. 각 편집의 프레임 번호는 '그 직전까지 적용한 결과' 기준이다."""
    out = anim
    for e in edits:
        out = retime_segment(out, e.start, e.end, e.length)
    return out


def speed_of(edit: Retime) -> float:
    """구간의 재생 속도 배율 (2 = 2배 빠르게). 길이 1 이하는 0 으로."""
    n, m = edit.span(), edit.length
    if n < 2 or m < 2:
        return 0.0
    return (n - 1) / float(m - 1)


# ------------------------------------------------------------------------------------------ 키 압축
def _simplify(err_fn, T: int, tol: float) -> np.ndarray:
    """Ramer–Douglas–Peucker 방식. err_fn(a, b) -> 구간 (a,b) 안쪽 프레임들의 오차 배열(길이 b-a-1)."""
    if T <= 2:
        return np.arange(T)
    keep = {0, T - 1}
    stack = [(0, T - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        e = err_fn(a, b)
        k = int(np.argmax(e))
        if e[k] > tol:
            m = a + 1 + k
            keep.add(m)
            stack.append((a, m))
            stack.append((m, b))
    return np.array(sorted(keep), dtype=int)


def _rot_err(q: np.ndarray, a: int, b: int) -> np.ndarray:
    """q (T,4) 에서 a,b 를 키로 하고 사이를 slerp 로 복원했을 때 각 프레임의 오차(도)."""
    t = (np.arange(a + 1, b) - a) / float(b - a)
    rec = Q.slerp(np.repeat(q[a:a + 1], len(t), axis=0), np.repeat(q[b:b + 1], len(t), axis=0), t)
    return np.degrees(Q.angle_between(rec, q[a + 1:b]))


def _pos_err(p: np.ndarray, a: int, b: int) -> np.ndarray:
    t = ((np.arange(a + 1, b) - a) / float(b - a))[:, None]
    rec = p[a] * (1.0 - t) + p[b] * t
    return np.linalg.norm(rec - p[a + 1:b], axis=-1)


def compress(anim: Animation, rot_tol_deg: float = 0.5, pos_tol: float = 0.1) -> KeySet:
    """관절·채널(회전/위치)마다 오차 허용치 안에서 키를 솎아낸다. pos_tol 은 스켈레톤 단위."""
    T, J = anim.n_frames, anim.rot.shape[1]
    rot_keys, pos_keys = [], []
    for j in range(J):
        q = anim.rot[:, j]
        p = anim.pos[:, j]
        rot_keys.append(_simplify(lambda a, b: _rot_err(q, a, b), T, rot_tol_deg))
        pos_keys.append(_simplify(lambda a, b: _pos_err(p, a, b), T, pos_tol))
    return KeySet(rot_keys, pos_keys, T)


def reconstruct(anim: Animation, keys: KeySet) -> Animation:
    """남긴 키만으로 모든 프레임을 다시 채운다(키 사이는 slerp/선형). 프레임 수는 그대로."""
    T, J = anim.n_frames, anim.rot.shape[1]
    t_all = np.arange(T)
    rot = np.empty_like(anim.rot)
    pos = np.empty_like(anim.pos)
    for j in range(J):
        kr = keys.rot[j]
        i1 = np.clip(np.searchsorted(kr, t_all, side="right"), 1, len(kr) - 1) if len(kr) > 1 else np.zeros(T, int)
        if len(kr) > 1:
            a, b = kr[i1 - 1], kr[i1]
            w = np.clip((t_all - a) / np.maximum(b - a, 1), 0.0, 1.0)
            rot[:, j] = Q.slerp(anim.rot[a, j], anim.rot[b, j], w[:, None])
        else:
            rot[:, j] = anim.rot[kr[0], j]
        kp = keys.pos[j]
        if len(kp) > 1:
            i1 = np.clip(np.searchsorted(kp, t_all, side="right"), 1, len(kp) - 1)
            a, b = kp[i1 - 1], kp[i1]
            w = np.clip((t_all - a) / np.maximum(b - a, 1), 0.0, 1.0)[:, None]
            pos[:, j] = anim.pos[a, j] * (1.0 - w) + anim.pos[b, j] * w
        else:
            pos[:, j] = anim.pos[kp[0], j]
    return Animation(anim.name, anim.fps, Q.make_continuous(rot), pos)


def merge_edit(a: Optional[EditOptions], b: Optional[EditOptions]) -> Optional[EditOptions]:
    """두 가공 옵션을 합친다. 같은 항목이면 b 가 기본값이 아닐 때 b 를 쓰고, 아니면 a 를 쓴다."""
    if a is None or not a.active():
        return b
    if b is None or not b.active():
        return a
    d = EditOptions()
    return EditOptions(
        trim_start=b.trim_start if b.trim_start > 0 else a.trim_start,
        trim_end=b.trim_end if b.trim_end is not None else a.trim_end,
        speed=b.speed if abs(b.speed - d.speed) > 1e-9 else a.speed,
        frames=b.frames or a.frames,
        seconds=b.seconds or a.seconds,
        fps=b.fps or a.fps,
        loop_frames=b.loop_frames if b.loop_frames > 0 else a.loop_frames,
        compress=bool(a.compress or b.compress),
        rot_tol_deg=b.rot_tol_deg if b.compress else a.rot_tol_deg,
        pos_tol_cm=b.pos_tol_cm if b.compress else a.pos_tol_cm,
    )


# ------------------------------------------------------------------------------------------ 한 번에 적용
def apply_edit(anim: Animation, opt: Optional[EditOptions], units_per_meter: float = 100.0,
               skel_parents: Optional[np.ndarray] = None) -> Tuple[Animation, Optional[KeySet], EditInfo]:
    """순서: 자르기 → 늘리기/fps → 루프 이음 → 압축. (애니메이션, 압축 키(없으면 None), 요약)

    압축했으면 돌려주는 애니메이션은 '키만으로 복원한' 것이라 BVH/Biped 처럼 프레임마다 값을 쓰는
    형식에도 정리된 모션이 나가고, KeySet 은 GLB/FBX/JSON 처럼 키를 따로 쓰는 형식이 쓴다.
    """
    info = EditInfo(frames_before=anim.n_frames, fps_before=float(anim.fps))
    if opt is None or not opt.active():
        info.frames_after, info.fps_after = anim.n_frames, float(anim.fps)
        return anim, None, info
    a = anim
    if opt.trim_start > 0 or opt.trim_end is not None:
        a = trim(a, opt.trim_start, opt.trim_end)
    if opt.frames or opt.seconds or opt.fps or abs(opt.speed - 1.0) > 1e-9:
        a = resample(a, speed=opt.speed, frames=opt.frames, seconds=opt.seconds, fps=opt.fps)
        if abs(opt.speed - 1.0) > 2.0 and not (opt.frames or opt.seconds):
            info.notes.append("속도를 크게 바꿨습니다. 발 접지가 어긋나면 리타깃의 발 고정 옵션을 확인하세요.")
    if opt.loop_frames > 0:
        a = loop_blend(a, opt.loop_frames, skel_parents)
    info.frames_after, info.fps_after = a.n_frames, float(a.fps)
    keys = None
    if opt.compress and a.n_frames > 2:
        pos_tol = opt.pos_tol_cm * units_per_meter / 100.0
        keys = compress(a, opt.rot_tol_deg, pos_tol)
        rec = reconstruct(a, keys)
        J = a.rot.shape[1]
        info.keys_before = 2 * a.n_frames * J
        info.keys_after = keys.total()
        info.max_rot_err_deg = float(np.degrees(Q.angle_between(rec.rot, a.rot)).max())
        info.max_pos_err = float(np.linalg.norm(rec.pos - a.pos, axis=-1).max())
        a = rec
    return a, keys, info
