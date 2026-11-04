"""3D 뷰어의 계산 부분 (Qt 비의존). 궤도 카메라, 투영, 장면 구성.

뷰어는 항상 '표준 프레임'(Y-up, +Z 전방)에서 타깃 단위로 그린다.
  * 타깃(내 모델)  : fk(타깃 스켈레톤, 리타깃 결과) 를 Ft 로 돌린 위치
  * 소스(고스트)   : 소스 표준 프레임 위치 * k  (같은 스케일로 겹쳐 보기)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

from ..core import quat as Q
from ..core.model import fk


class OrbitCamera:
    def __init__(self) -> None:
        self.yaw = np.radians(35.0)  # 모델 앞(+Z)을 비스듬히 보는 각도
        self.pitch = np.radians(15.0)
        self.dist = 4.0
        self.target = np.array([0.0, 0.9, 0.0])
        self.fov = np.radians(40.0)

    def frame(self, center: np.ndarray, size: float) -> None:
        self.target = np.asarray(center, float).copy()
        self.dist = max(size, 1e-6) * 2.2

    def eye(self) -> np.ndarray:
        cp = np.cos(self.pitch)
        return self.target + self.dist * np.array([cp * np.sin(self.yaw), np.sin(self.pitch), cp * np.cos(self.yaw)])

    def basis(self):
        """(right, up, forward) 월드 벡터."""
        f = self.target - self.eye()
        f /= np.linalg.norm(f)
        r = np.cross(f, [0.0, 1.0, 0.0])
        n = np.linalg.norm(r)
        r = r / n if n > 1e-9 else np.array([1.0, 0.0, 0.0])
        u = np.cross(r, f)
        return r, u, f

    def orbit(self, dx: float, dy: float) -> None:
        self.yaw -= dx * 0.01
        self.pitch = float(np.clip(self.pitch + dy * 0.01, np.radians(-89), np.radians(89)))

    def pan(self, dx: float, dy: float, height_px: float) -> None:
        r, u, _ = self.basis()
        k = 2 * self.dist * np.tan(self.fov / 2) / max(height_px, 1.0)
        self.target = self.target - r * dx * k + u * dy * k

    def zoom(self, factor: float) -> None:
        self.dist = float(np.clip(self.dist * factor, 1e-4, 1e7))

    def project(self, pts: np.ndarray, w: float, h: float):
        """(N,3) -> (xy (N,2) 픽셀, depth (N,)). depth<=0 은 카메라 뒤."""
        pts = np.asarray(pts, float).reshape(-1, 3)
        r, u, f = self.basis()
        d = pts - self.eye()
        x, y, z = d @ r, d @ u, d @ f
        s = (h / 2) / np.tan(self.fov / 2)
        zz = np.where(z > 1e-9, z, np.nan)
        xy = np.stack([w / 2 + s * x / zz, h / 2 - s * y / zz], axis=1)
        return xy, z

    def project_segments(self, seg: np.ndarray, w: float, h: float) -> np.ndarray:
        """(N,2,3) -> (M,4) [x0,y0,x1,y1]. 카메라 뒤쪽은 근평면에서 자른다."""
        if len(seg) == 0:
            return np.zeros((0, 4))
        r, u, f = self.basis()
        e = self.eye()
        near = 0.02 * self.dist
        a, b = seg[:, 0].copy(), seg[:, 1].copy()
        za, zb = (a - e) @ f, (b - e) @ f
        keep = (za > near) | (zb > near)
        a, b, za, zb = a[keep], b[keep], za[keep], zb[keep]
        t = np.where((za <= near) & (zb > near), (near - za) / (zb - za + 1e-12), 0.0)
        a = a + (b - a) * t[:, None]
        t2 = np.where((zb <= near) & (za > near), (near - zb) / (za - zb + 1e-12), 0.0)
        b = b + (a - b) * t2[:, None]
        pa, _ = self.project(a, w, h)
        pb, _ = self.project(b, w, h)
        return np.concatenate([pa, pb], axis=1)


def bone_segments(skel_parents: np.ndarray, pos: np.ndarray, skip_zero: bool = True) -> np.ndarray:
    """pos (J,3) -> (K,2,3)."""
    idx = np.flatnonzero(skel_parents >= 0)
    a = pos[skel_parents[idx]]
    b = pos[idx]
    seg = np.stack([a, b], axis=1)
    if skip_zero:
        seg = seg[np.linalg.norm(b - a, axis=1) > 1e-9]
    return seg


def grid_segments(center_xz: Tuple[float, float], y: float, size: float, step: float) -> np.ndarray:
    n = int(np.ceil(size / step))
    cx = round(center_xz[0] / step) * step
    cz = round(center_xz[1] / step) * step
    ks = np.arange(-n, n + 1) * step
    segs = []
    for k in ks:
        segs.append([[cx + k, y, cz - n * step], [cx + k, y, cz + n * step]])
        segs.append([[cx - n * step, y, cz + k], [cx + n * step, y, cz + k]])
    return np.array(segs)


def nice_step(size: float) -> float:
    raw = size / 6.0
    e = 10 ** np.floor(np.log10(max(raw, 1e-9)))
    for m in (1, 2, 5, 10):
        if raw <= m * e:
            return float(m * e)
    return float(10 * e)


@dataclass
class ViewData:
    """프레임별 위치 캐시(표준 프레임). Project 상태에서 만든다."""

    tgt_pos: Optional[np.ndarray] = None  # (T,Jt,3)
    tgt_parents: Optional[np.ndarray] = None
    ghost_pos: Optional[np.ndarray] = None  # (T,Js,3) 타깃 단위로 스케일됨
    ghost_parents: Optional[np.ndarray] = None
    tgt_wr: Optional[np.ndarray] = None  # 메시 스키닝용 (파일 프레임)
    tgt_wp: Optional[np.ndarray] = None
    Ft: Optional[np.ndarray] = None
    contact: Dict[str, np.ndarray] = field(default_factory=dict)  # 라벨 -> (T,) bool
    ik_target: Dict[str, np.ndarray] = field(default_factory=dict)  # 라벨 -> (T,3)
    bad: Dict[str, np.ndarray] = field(default_factory=dict)  # 라벨 -> (T,) 다리 닿지 않음
    n_frames: int = 1
    fps: float = 30.0
    height: float = 1.0
    center: np.ndarray = field(default_factory=lambda: np.zeros(3))
    ground_y: float = 0.0

    @staticmethod
    def build(pr) -> "ViewData":
        vd = ViewData()
        res = pr.result
        if pr.tgt is not None:
            tk = pr.tgt.skeleton
            if res is not None:
                wr, wp = fk(tk, res.anim.rot, res.anim.pos)
                Ft = res.frame_tgt[0]
                vd.n_frames, vd.fps = res.anim.n_frames, res.anim.fps
                vd.ghost_pos = res.src_canon_pos * res.k
                vd.ghost_parents = pr.src.skeleton.parents
                for p in res.plans:
                    vd.contact[p.label] = p.contact
                    vd.ik_target[p.label] = p.target
                    vd.bad[p.label] = p.fail
            else:
                from ..core.mapping import DEFAULT_FRAME, detect_frame

                det = detect_frame(tk, pr.tgt_slots)
                Ft = det[0] if det else DEFAULT_FRAME
                # 애니메이션이 없으면 바인드 포즈
                bwr, bwp = tk.bind_world()
                wr, wp = bwr[None], bwp[None]
                vd.n_frames = 1
            vd.Ft = Ft
            vd.tgt_wr, vd.tgt_wp = wr, wp
            vd.tgt_pos = Q.rotate(Ft, wp)
            vd.tgt_parents = tk.parents
        elif pr.src is not None:
            sk = pr.src.skeleton
            from ..core.mapping import DEFAULT_FRAME, detect_frame

            det = detect_frame(sk, pr.src_slots)
            Fs = det[0] if det else DEFAULT_FRAME
            a = pr.anim
            wr, wp = fk(sk, a.rot, a.pos)
            vd.ghost_pos = Q.rotate(Fs, wp)
            vd.ghost_parents = sk.parents
            vd.n_frames, vd.fps = a.n_frames, a.fps
        main = vd.tgt_pos if vd.tgt_pos is not None else vd.ghost_pos
        if main is not None:
            b = main[0]
            vd.ground_y = float(main[..., 1].min())
            vd.height = float(max(np.ptp(b[:, 1]), np.ptp(b, axis=0).max(), 1e-6))
            vd.center = np.array([b[:, 0].mean(), vd.ground_y + vd.height * 0.5, b[:, 2].mean()])
        return vd

    def focus(self, frame: int) -> np.ndarray:
        """프레임 f 에서 캐릭터 중심의 수평 위치 (follow 카메라용). 높이는 고정."""
        main = self.tgt_pos if self.tgt_pos is not None else self.ghost_pos
        if main is None:
            return self.center.copy()
        p = main[min(frame, main.shape[0] - 1)]
        return np.array([p[:, 0].mean(), self.center[1], p[:, 2].mean()])

    def mesh_points(self, pr, frame: int, max_points: int = 8000) -> Optional[np.ndarray]:
        if not (pr.tgt and pr.tgt.meshes and self.tgt_wr is not None):
            return None
        f = min(frame, self.tgt_wr.shape[0] - 1)
        pts = []
        for m in pr.tgt.meshes:
            v = m.skin(pr.tgt.skeleton, self.tgt_wr[f], self.tgt_wp[f])
            step = max(1, int(np.ceil(len(v) * len(pr.tgt.meshes) / max_points)))
            pts.append(v[::step])
        return Q.rotate(self.Ft, np.concatenate(pts))


def build_scene(vd: ViewData, pr, frame: int, show_ghost=True, show_mesh=True, show_ik=True) -> Dict[str, np.ndarray]:
    """프레임 하나의 3D 요소: role -> 배열. 'seg_*' 는 (N,2,3), 'pt_*' 는 (N,3)."""
    sc: Dict[str, np.ndarray] = {}
    f = int(np.clip(frame, 0, max(vd.n_frames - 1, 0)))
    if vd.tgt_pos is not None:
        ft = min(f, vd.tgt_pos.shape[0] - 1)
        sc["seg_target"] = bone_segments(vd.tgt_parents, vd.tgt_pos[ft])
        sc["pt_target_joints"] = vd.tgt_pos[ft]
        if show_mesh:
            m = vd.mesh_points(pr, ft)
            if m is not None:
                sc["pt_mesh"] = m
    if show_ghost and vd.ghost_pos is not None:
        fg = min(f, vd.ghost_pos.shape[0] - 1)
        sc["seg_ghost"] = bone_segments(vd.ghost_parents, vd.ghost_pos[fg])
    if show_ik:
        ok, bad, goal = [], [], []
        for lab, c in vd.contact.items():
            if f < len(c) and c[f]:
                (bad if vd.bad[lab][f] else ok).append(vd.ik_target[lab][f])
            if f < len(c):
                goal.append(vd.ik_target[lab][f])
        if ok:
            sc["pt_plant"] = np.array(ok)
        if bad:
            sc["pt_bad"] = np.array(bad)
    sz = vd.height * 3.0
    sc["seg_grid"] = grid_segments((vd.center[0], vd.center[2]), vd.ground_y, sz, nice_step(sz))
    return sc


COLORS = {
    "seg_grid": (70, 76, 88),
    "seg_ghost": (240, 160, 60),
    "seg_target": (90, 200, 255),
    "pt_target_joints": (255, 255, 255),
    "pt_mesh": (150, 160, 180),
    "pt_plant": (90, 230, 120),
    "pt_bad": (255, 80, 80),
}
DRAW_ORDER = ["seg_grid", "pt_mesh", "seg_ghost", "seg_target", "pt_target_joints", "pt_plant", "pt_bad"]
