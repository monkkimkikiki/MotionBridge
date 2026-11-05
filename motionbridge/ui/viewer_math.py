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
from ..core.model import Mesh, fk


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


def reduced_parents(parents: np.ndarray, slots: Optional[dict], max_full: int = 0) -> np.ndarray:
    """사람 슬롯에 매핑된 본만 이어서 그린다(IK·카메라·워프·얼굴·천 같은 보조 본은 숨김). max_full 이하 본 수의 스켈레톤은 그대로 그린다.

    게임 리그에는 카메라/IK/워프 보조 본이 몸에서 멀리 떨어져 있어서 그대로 그리면 '팔이 날아간 것'처럼 보인다.

    각 매핑된 본의 부모를 '가장 가까운 매핑된 조상'으로 바꾸고, 나머지 본은 부모 -1(그리지 않음)로 둔다.
    """
    par = np.asarray(parents)
    if len(par) <= max_full or not slots or len(set(slots.values())) < 10:
        return par
    keep = set(int(v) for v in slots.values())
    out = np.full(len(par), -1, dtype=par.dtype)
    for j in keep:
        a = int(par[j])
        while a >= 0 and a not in keep:
            a = int(par[a])
        out[j] = a
    return out


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
    _pt_cache: Dict[Tuple[int, int], Mesh] = field(default_factory=dict, repr=False)  # (메시 id, 최대 점 수) -> 간추린 정점 부분집합

    @staticmethod
    def build(pr, show_helpers: bool = False) -> "ViewData":
        mf = 10 ** 9 if show_helpers else 0  # show_helpers: 매핑 안 된 보조 본도 그린다
        vd = ViewData()
        res = pr.result
        if pr.tgt is not None:
            tk = pr.tgt.skeleton
            if res is not None:
                wr, wp = fk(tk, res.anim.rot, res.anim.pos)
                Ft = res.frame_tgt[0]
                vd.n_frames, vd.fps = res.anim.n_frames, res.anim.fps
                vd.ghost_pos = res.src_canon_pos * res.k
                vd.ghost_parents = reduced_parents(pr.src.skeleton.parents, pr.src_slots, mf)
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
            vd.tgt_parents = reduced_parents(tk.parents, pr.tgt_slots, mf)
        elif pr.src is not None:
            sk = pr.src.skeleton
            from ..core.mapping import DEFAULT_FRAME, detect_frame

            det = detect_frame(sk, pr.src_slots)
            Fs = det[0] if det else DEFAULT_FRAME
            a = pr.anim
            wr, wp = fk(sk, a.rot, a.pos)
            vd.ghost_pos = Q.rotate(Fs, wp)
            vd.ghost_parents = reduced_parents(sk.parents, pr.src_slots, mf)
            vd.n_frames, vd.fps = a.n_frames, a.fps
        main = vd.tgt_pos if vd.tgt_pos is not None else vd.ghost_pos
        if main is not None:
            mp = vd.tgt_parents if vd.tgt_pos is not None else vd.ghost_parents
            if mp is not None and len(mp) == main.shape[1]:  # 화면 맞추기/높이는 그려지는 본만으로 계산 (카메라 본 등 제외)
                vis = (mp >= 0) | np.isin(np.arange(len(mp)), mp[mp >= 0])
                if vis.sum() >= 2:
                    main = main[:, vis]
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

    def _point_subset(self, m: Mesh, total: int, max_points: int) -> Mesh:
        """점 표시용으로 정점을 미리 간추려 둔다. 무거운 메시(수십만 정점)를 매 프레임 통째로 스키닝하지 않기 위함."""
        key = (id(m), max_points)
        sub = self._pt_cache.get(key)
        if sub is None:
            step = max(1, int(np.ceil(total / max_points)))
            idx = np.arange(0, len(m.vertices), step)
            sub = Mesh(m.name, m.vertices[idx], m.faces[:0], m.joints[idx], m.weights[idx])
            self._pt_cache[key] = sub
        return sub

    def mesh_points(self, pr, frame: int, max_points: int = 8000) -> Optional[np.ndarray]:
        if not (pr.tgt and pr.tgt.meshes and self.tgt_wr is not None):
            return None
        f = min(frame, self.tgt_wr.shape[0] - 1)
        total = int(sum(len(m.vertices) for m in pr.tgt.meshes))
        pts = [self._point_subset(m, total, max_points).skin(pr.tgt.skeleton, self.tgt_wr[f], self.tgt_wp[f]) for m in pr.tgt.meshes]
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



# ===================================================================== 메시 표면 표시
# 솔리드/머티리얼: 삼각형을 칠하지 않고 표면을 촘촘한 점으로 찍어(깊이 정렬 + 음영) 이미지로 만든다.
# 파이썬 루프 없이 numpy 로만 처리해서 큰 메시도 재생 중에 돌릴 수 있다.
DEFAULT_SURFACE_RGB = (0.74, 0.78, 0.86)


@dataclass
class DisplayMesh:
    sub: Mesh  # 축소된 정점(스킨 정보 포함, 바인드 포즈)
    tris: np.ndarray  # (T,3) sub 기준
    samp_tri: np.ndarray  # (N,) 표면 샘플이 속한 삼각형
    samp_bary: np.ndarray  # (N,3)
    samp_rgb: np.ndarray  # (N,3) 머티리얼 색(0..1)
    has_material: bool
    n_full_faces: int
    edges: Optional[np.ndarray] = None  # (E,2) 와이어 표시용
    orient: float = 0.0  # +1/-1: 삼각형 법선이 바깥/안쪽(닫힌 메시일 때), 0: 알 수 없음 -> 뒷면 제거 안 함


def _reduce_mesh(verts: np.ndarray, faces: np.ndarray, max_tris: int):
    """정점 군집화로 삼각형 수를 max_tris 이하로 줄인다. (대표 정점 인덱스, 삼각형) 을 돌려준다."""
    faces = np.asarray(faces, dtype=np.int64)
    if len(faces) > max_tris:
        lo, hi = verts.min(axis=0), verts.max(axis=0)
        diag = float(np.linalg.norm(hi - lo)) or 1.0
        best = None
        a, b = diag / 3000.0, diag / 3.0
        for _ in range(9):  # 로그 스케일 이분 탐색: 삼각형이 max_tris 이하인 가장 작은 칸 크기
            c = float(np.sqrt(a * b))
            cid, tri = _cluster(verts, faces, lo, c)
            if len(tri) <= max_tris:
                best, b = (cid, tri), c
            else:
                a = c
        if best is None:
            best = _cluster(verts, faces, lo, b)
        cid, tri = best
        nclu = int(cid.max()) + 1
        cnt = np.bincount(cid, minlength=nclu).astype(float)
        mean = np.stack([np.bincount(cid, weights=verts[:, k], minlength=nclu) for k in range(3)], axis=1) / np.maximum(cnt, 1)[:, None]
        d = np.linalg.norm(verts - mean[cid], axis=1)
        order = np.lexsort((d, cid))
        first = np.ones(len(order), bool)
        first[1:] = cid[order][1:] != cid[order][:-1]
        rep = np.full(nclu, -1, dtype=np.int64)
        rep[cid[order][first]] = order[first]
    else:
        used = np.unique(faces)
        rep = used
        remap = np.full(len(verts), -1, dtype=np.int64)
        remap[used] = np.arange(len(used))
        tri = remap[faces]
    keep = np.unique(tri)
    remap = np.full(len(rep), -1, dtype=np.int64)
    remap[keep] = np.arange(len(keep))
    return rep[keep], remap[tri]


def _cluster(verts: np.ndarray, faces: np.ndarray, lo: np.ndarray, cell: float):
    q = np.floor((verts - lo) / cell).astype(np.int64)
    n = q.max(axis=0) + 1
    key = (q[:, 0] * n[1] + q[:, 1]) * n[2] + q[:, 2]
    _, cid = np.unique(key, return_inverse=True)
    cid = cid.astype(np.int64)
    t = cid[faces]
    ok = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
    t = t[ok]
    if len(t) == 0:
        return cid, t
    R = int(cid.max()) + 1
    k = np.sort(t, axis=1)
    code = (k[:, 0] * R + k[:, 1]) * R + k[:, 2]
    _, idx = np.unique(code, return_index=True)
    return cid, t[np.sort(idx)]


# 메시 품질 -> (솔리드 삼각형 수, 표면 샘플 수, 와이어 삼각형 수)
QUALITY = {"low": (4000, 15000, 3000), "mid": (12000, 40000, 5000), "high": (40000, 90000, 7000)}
QUALITY_NAMES = [("auto", "자동"), ("low", "낮음"), ("mid", "보통"), ("high", "높음")]


def pick_quality(n_faces: int, setting: str = "auto") -> str:
    """'auto' 면 삼각형 수로 고른다: 15만 초과 low, 5만 초과 mid, 그 이하 high."""
    if setting in QUALITY:
        return setting
    return "low" if n_faces > 150000 else ("mid" if n_faces > 50000 else "high")


def build_display_mesh(mesh: Mesh, decode=None, max_tris: int = 40000, n_samples: int = 90000, seed: int = 1, want_edges: bool = False) -> Optional[DisplayMesh]:
    """decode(bytes) -> (H,W,3|4) uint8 또는 None. 텍스처 해독은 Qt 쪽에서 넣어 준다."""
    if len(mesh.faces) == 0 or len(mesh.vertices) == 0:
        return None
    rep, tris = _reduce_mesh(mesh.vertices, mesh.faces, max_tris)
    if len(tris) == 0:
        return None
    uv = mesh.uv[rep] if mesh.uv is not None and len(mesh.uv) == len(mesh.vertices) else None
    sub = Mesh(mesh.name, mesh.vertices[rep], tris, mesh.joints[rep], mesh.weights[rep], uv)
    v = sub.vertices
    area = 0.5 * np.linalg.norm(np.cross(v[tris[:, 1]] - v[tris[:, 0]], v[tris[:, 2]] - v[tris[:, 0]]), axis=1)
    if not np.isfinite(area).all() or area.sum() <= 0:
        area = np.ones(len(tris))
    rng = np.random.default_rng(seed)
    cum = np.cumsum(area) / area.sum()
    st = np.minimum(np.searchsorted(cum, rng.random(n_samples)), len(tris) - 1)
    r1, r2 = np.sqrt(rng.random(n_samples)), rng.random(n_samples)
    bary = np.stack([1 - r1, r1 * (1 - r2), r1 * r2], axis=1)
    base = np.array(mesh.color[:3] if mesh.color is not None else DEFAULT_SURFACE_RGB, float)
    rgb = np.tile(base, (n_samples, 1))
    has_mat = mesh.color is not None
    tex = None
    if mesh.texture is not None and uv is not None and decode is not None:
        tex = decode(mesh.texture)
    if tex is not None and tex.ndim == 3 and tex.shape[0] > 0 and tex.shape[1] > 0:
        suv = np.einsum("nk,nkc->nc", bary, uv[tris[st]])
        H, W = tex.shape[:2]
        px = np.clip(((suv[:, 0] % 1.0) * W).astype(int), 0, W - 1)
        py = np.clip(((suv[:, 1] % 1.0) * H).astype(int), 0, H - 1)
        rgb = tex[py, px, :3].astype(float) / 255.0 * base
        has_mat = True
    edges = None
    if want_edges:
        e = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
        e.sort(axis=1)
        R = len(v)
        _, ui = np.unique(e[:, 0] * R + e[:, 1], return_index=True)
        edges = e[ui]
    # 닫힌 메시의 바깥 방향: 법선과 (삼각형 중심 - 메시 중심) 의 내적 합으로 판정. 열린 메시(천 등)는 0.
    cn = np.cross(v[tris[:, 1]] - v[tris[:, 0]], v[tris[:, 2]] - v[tris[:, 0]])
    cc = v[tris].mean(axis=1) - v.mean(axis=0)
    dots = np.einsum("ij,ij->i", cn, cc)
    denom = float(np.abs(dots).sum())
    orient = 0.0
    if denom > 1e-12 and abs(float(dots.sum())) / denom > 0.5:
        orient = 1.0 if dots.sum() > 0 else -1.0
    return DisplayMesh(sub, tris, st, bary, np.clip(rgb, 0.0, 1.0), has_mat, len(mesh.faces), edges, orient)


def skin_display(dm: DisplayMesh, skel, wr: np.ndarray, wp: np.ndarray, Ft: np.ndarray) -> np.ndarray:
    """한 프레임의 축소 메시 정점(표준 프레임)."""
    return Q.rotate(Ft, dm.sub.skin(skel, wr, wp))


def render_surface(items, cam: "OrbitCamera", w: int, h: int, material: bool, sample_step: int = 1) -> Optional[np.ndarray]:
    """items = [(DisplayMesh, 정점(R,3))]. 깊이 순서대로 점을 찍은 (h,w,4) uint8 RGBA 이미지. 덮이지 않은 곳은 알파 0.

    sample_step > 1 이면 표면 샘플을 그 간격으로 솎아서 그린다(회전/재생 중 가벼운 표시). 점 크기는 밀도에 맞춰 자동으로 커진다."""
    if w < 2 or h < 2 or not items:
        return None
    Ps, Ns, Cs, Os = [], [], [], []
    sl = slice(None, None, max(1, int(sample_step)))
    for dm, v in items:
        tv = v[dm.tris]
        n = np.cross(tv[:, 1] - tv[:, 0], tv[:, 2] - tv[:, 0])
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
        st = dm.samp_tri[sl]
        Ps.append(np.einsum("nk,nkc->nc", dm.samp_bary[sl], tv[st]))
        Ns.append(n[st])
        Cs.append(dm.samp_rgb[sl] if material and dm.has_material else np.tile(DEFAULT_SURFACE_RGB, (len(st), 1)))
        Os.append(np.full(len(st), dm.orient))
    P, N, C, O = np.concatenate(Ps), np.concatenate(Ns), np.concatenate(Cs), np.concatenate(Os)
    xy, z = cam.project(P, float(w), float(h))
    ix, iy = np.floor(xy[:, 0]), np.floor(xy[:, 1])
    ok = (z > 0) & np.isfinite(ix) & np.isfinite(iy) & (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
    if not ok.any():
        return None
    view = cam.eye() - cam.target
    view = view / max(float(np.linalg.norm(view)), 1e-12)
    sndv = (N @ view) * O  # 닫힌 메시에서 음수면 뒷면
    ok &= sndv > -0.12  # 뒷면은 그리지 않는다(앞면 틈으로 비쳐 보이는 것 방지). O==0 이면 항상 통과
    if not ok.any():
        return None
    ix, iy, z, N, C = ix[ok].astype(np.int64), iy[ok].astype(np.int64), z[ok], N[ok], C[ok]
    ndv = np.abs(N @ view)
    shade = 0.30 + 0.70 * ndv
    col = np.clip(C * shade[:, None], 0.0, 1.0)
    rgba = np.empty((len(z), 4), np.uint8)
    rgba[:, :3] = (col * 255.0 + 0.5).astype(np.uint8)
    rgba[:, 3] = 255
    order = np.argsort(-z, kind="stable")  # 먼 것부터 찍어서 가까운 것이 덮는다
    ix, iy, rgba = ix[order], iy[order], rgba[order]
    # 칠해진 면적을 8px 칸 점유율로 추정하고, 면적당 점 수 x 점 크기^2 >= 4 가 되게 점 크기를 정한다(틈이 거의 안 남는 기준).
    cells = np.unique((ix // 8) * ((h + 7) // 8 + 1) + iy // 8)
    dens = len(ix) / max(len(cells) * 64.0, 1.0)
    k = int(np.clip(np.ceil(np.sqrt(4.0 / max(dens, 1e-9))), 1, 4))
    img = np.zeros((h, w, 4), np.uint8)
    flat = img.reshape(-1, 4)
    lo = -(k // 2)
    for dy in range(lo, lo + k):
        for dx in range(lo, lo + k):
            x2, y2 = ix + dx, iy + dy
            m = (x2 >= 0) & (x2 < w) & (y2 >= 0) & (y2 < h)
            flat[y2[m] * w + x2[m]] = rgba[m]
    _fill_holes(img, int(ix.min()) + lo, int(ix.max()) + lo + k + 1, int(iy.min()) + lo, int(iy.max()) + lo + k + 1)
    return img


def _fill_holes(img: np.ndarray, x0: int, x1: int, y0: int, y1: int, passes: int = 4) -> None:
    """점 사이의 틈을 이웃 색으로 메운다 (이웃 8칸 중 3칸 이상이 칠해진 빈 픽셀만, 최대 4번 반복)."""
    h, w = img.shape[:2]
    x0, y0, x1, y1 = max(x0 - 1, 0), max(y0 - 1, 0), min(x1 + 1, w), min(y1 + 1, h)
    if x1 - x0 < 3 or y1 - y0 < 3:
        return
    sub = img[y0:y1, x0:x1]
    for _ in range(passes):
        cov = (sub[..., 3] > 0).astype(np.float32)
        pad_c = np.pad(cov, 1)
        pad_rgb = np.pad(sub[..., :3].astype(np.float32) * cov[..., None], ((1, 1), (1, 1), (0, 0)))
        hh, ww = cov.shape
        cnt = np.zeros_like(cov)
        acc = np.zeros(sub.shape[:2] + (3,), np.float32)
        for dy in (0, 1, 2):
            for dx in (0, 1, 2):
                if dy == 1 and dx == 1:
                    continue
                cnt += pad_c[dy:dy + hh, dx:dx + ww]
                acc += pad_rgb[dy:dy + hh, dx:dx + ww]
        holes = (cov == 0) & (cnt >= 3)
        if not holes.any():
            break
        sub[holes, :3] = (acc[holes] / cnt[holes][:, None] + 0.5).astype(np.uint8)
        sub[holes, 3] = 255


def wire_segments(dm: DisplayMesh, v: np.ndarray) -> np.ndarray:
    """와이어프레임 선분 (E,2,3). dm 은 want_edges=True 로 만든 것이어야 한다."""
    e = dm.edges if dm.edges is not None else np.zeros((0, 2), int)
    return np.stack([v[e[:, 0]], v[e[:, 1]]], axis=1) if len(e) else np.zeros((0, 2, 3))
