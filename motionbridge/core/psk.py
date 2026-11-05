"""ActorX 메시(.psk / .pskx) 읽기 + OBJ 쓰기. Qt/Blender 없이 numpy 만 쓴다.

CUE4Parse 가 스켈레탈 메시를 ActorX 로 내보내면 이 형식이 된다.
  청크: ID[20] + 형식(int32) + 크기(int32) + 개수(int32) + 데이터
  PNTS0000 점(float3)      VTXW0000/VTXW3200 웨지(점 번호, UV, 재질)   FACE0000/FACE3200 삼각형(웨지 3개, 재질)
  MATT0000 재질 이름       REFSKELT 본(이름, 부모, 회전, 위치)          RAWWEIGHTS 가중치(값, 점, 본)

좌표: 언리얼은 왼손 좌표계(Z-up, 앞 +X). Blender/FBX 로 보낼 때는 Y 를 뒤집어 오른손계로 만들고, 캐릭터가 -Y 를 보게 돌린다
(`to_blender`). 본 회전의 저장 방식(켤레 여부)은 파일마다 달라 보여서, 본 위치가 자기 정점 근처에 오는 쪽을 자동으로 고른다.
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np


class PskError(Exception):
    pass


@dataclass
class PskMesh:
    name: str
    points: np.ndarray  # (P,3) 언리얼 좌표(cm)
    wedge_point: np.ndarray  # (W,) 웨지 -> 점
    wedge_uv: np.ndarray  # (W,2)
    faces: np.ndarray  # (F,3) 웨지 번호
    face_mat: np.ndarray  # (F,)
    materials: List[str]
    bone_names: List[str] = field(default_factory=list)
    bone_parent: List[int] = field(default_factory=list)  # -1 = 루트
    bone_quat: np.ndarray = field(default_factory=lambda: np.zeros((0, 4)))  # (B,4) x,y,z,w (파일에 저장된 그대로)
    bone_pos: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))  # (B,3) 부모 기준 위치
    weights: np.ndarray = field(default_factory=lambda: np.zeros((0, 3)))  # (N,3): 가중치, 점 번호, 본 번호

    @property
    def has_skeleton(self) -> bool:
        return len(self.bone_names) > 0 and len(self.weights) > 0


def _cstr(b: bytes) -> str:
    return b.split(b"\0", 1)[0].decode("latin-1", errors="replace").strip()


def read_psk(path: str) -> PskMesh:
    with open(path, "rb") as f:
        data = f.read()
    pos, n = 0, len(data)
    chunks: Dict[str, Tuple[bytes, int, int]] = {}
    while pos + 32 <= n:
        cid = _cstr(data[pos:pos + 20])
        _tf, size, count = struct.unpack_from("<iii", data, pos + 20)
        pos += 32
        if size < 0 or count < 0 or pos + size * count > n:
            raise PskError("청크 크기가 이상합니다 (%s) — PSK 파일이 아니거나 손상됨" % cid)
        chunks.setdefault(cid, (data[pos:pos + size * count], size, count))
        pos += size * count
    if "ACTRHEAD" not in chunks and "PNTS0000" not in chunks:
        raise PskError("ActorX 메시 파일이 아닙니다: " + os.path.basename(path))

    def need(cid):
        if cid not in chunks:
            raise PskError("%s 청크가 없습니다" % cid)
        return chunks[cid]

    raw, size, count = need("PNTS0000")
    points = np.frombuffer(raw, "<f4").reshape(count, 3).astype(np.float64) if count else np.zeros((0, 3))

    wid = "VTXW3200" if "VTXW3200" in chunks else "VTXW0000"
    raw, size, count = need(wid)
    if wid == "VTXW3200":
        wdt = np.dtype([("p", "<u4"), ("u", "<f4"), ("v", "<f4"), ("m", "u1"), ("r", "u1"), ("pad2", "<u2")])
    else:
        wdt = np.dtype([("p", "<u2"), ("pad", "<u2"), ("u", "<f4"), ("v", "<f4"), ("m", "u1"), ("r", "u1"), ("pad2", "<u2")])
    if wdt.itemsize != size:
        raise PskError("웨지 크기가 예상(%d)과 다릅니다: %d" % (wdt.itemsize, size))
    w = np.frombuffer(raw, wdt)
    wedge_point = w["p"].astype(np.int64)
    wedge_uv = np.stack([w["u"], w["v"]], 1).astype(np.float64) if count else np.zeros((0, 2))

    fid = "FACE3200" if "FACE3200" in chunks else "FACE0000"
    raw, size, count = need(fid)
    if fid == "FACE3200":
        fdt = np.dtype([("w", "<u4", 3), ("m", "u1"), ("a", "u1"), ("s", "<u4")])
    else:
        fdt = np.dtype([("w", "<u2", 3), ("m", "u1"), ("a", "u1"), ("s", "<u4")])
    if fdt.itemsize != size:
        raise PskError("삼각형 크기가 예상(%d)과 다릅니다: %d" % (fdt.itemsize, size))
    fc = np.frombuffer(raw, fdt)
    faces = fc["w"].astype(np.int64).reshape(-1, 3)
    face_mat = fc["m"].astype(np.int64)

    mats: List[str] = []
    if "MATT0000" in chunks:
        raw, size, count = chunks["MATT0000"]
        for i in range(count):
            mats.append(_cstr(raw[i * size:i * size + 64]) or "material_%d" % i)
    nm = int(face_mat.max()) + 1 if len(face_mat) else 0
    while len(mats) < nm:
        mats.append("material_%d" % len(mats))

    mesh = PskMesh(os.path.splitext(os.path.basename(path))[0], points, wedge_point, wedge_uv, faces, face_mat, mats)

    if "REFSKELT" in chunks:
        raw, size, count = chunks["REFSKELT"]
        if size < 120:
            raise PskError("본 청크 크기가 이상합니다: %d" % size)
        names, parents, quats, poss = [], [], [], []
        for i in range(count):
            b = raw[i * size:(i + 1) * size]
            names.append(_cstr(b[:64]) or "bone_%d" % i)
            _flags, _nch, par = struct.unpack_from("<Iii", b, 64)
            q = struct.unpack_from("<4f", b, 76)
            p = struct.unpack_from("<3f", b, 92)
            parents.append(par if 0 <= par < count and par != i else -1)
            quats.append(q)
            poss.append(p)
        mesh.bone_names, mesh.bone_parent = names, parents
        mesh.bone_quat, mesh.bone_pos = np.array(quats, float).reshape(-1, 4), np.array(poss, float).reshape(-1, 3)
    if "RAWWEIGHTS" in chunks and mesh.bone_names:
        raw, size, count = chunks["RAWWEIGHTS"]
        wt = np.frombuffer(raw, np.dtype([("w", "<f4"), ("p", "<i4"), ("b", "<i4")]))
        keep = (wt["p"] >= 0) & (wt["p"] < len(points)) & (wt["b"] >= 0) & (wt["b"] < len(mesh.bone_names)) & (wt["w"] > 0)
        mesh.weights = np.stack([wt["w"][keep], wt["p"][keep], wt["b"][keep]], 1).astype(np.float64)
    return mesh


# ----------------------------------------------------------------------------- 좌표 변환
def _quat_to_mat(q: np.ndarray) -> np.ndarray:
    x, y, z, w = q / (np.linalg.norm(q) or 1.0)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def _world_positions(mesh: PskMesh, conj_root: bool, conj_rest: bool) -> Tuple[np.ndarray, np.ndarray]:
    """본의 월드 회전(B,3,3)과 위치(B,3) (언리얼 좌표). 켤레 여부 두 가지를 바꿔 가며 시험한다."""
    B = len(mesh.bone_names)
    rot = np.zeros((B, 3, 3))
    pos = np.zeros((B, 3))
    done = [False] * B

    def solve(i):
        if done[i]:
            return
        p = mesh.bone_parent[i]
        q = mesh.bone_quat[i].copy()
        if (i == 0 and conj_root) or (i != 0 and conj_rest):
            q[:3] = -q[:3]
        R = _quat_to_mat(q)
        if p < 0:
            rot[i], pos[i] = R, mesh.bone_pos[i]
        else:
            solve(p)
            rot[i] = rot[p] @ R
            pos[i] = pos[p] + rot[p] @ mesh.bone_pos[i]
        done[i] = True

    import sys
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, B + 100))
    try:
        for i in range(B):
            solve(i)
    finally:
        sys.setrecursionlimit(old)
    return rot, pos


def _fit_error(mesh: PskMesh, pos: np.ndarray) -> float:
    """본 위치가 자기 가중치 정점들에 얼마나 가까운가 (작을수록 맞음). 정점 수가 많아도 되도록 본마다 최대 400개만 쓴다."""
    w = mesh.weights
    tot, cnt = 0.0, 0
    order = np.argsort(w[:, 2], kind="stable")
    bsorted = w[order, 2].astype(int)
    starts = np.searchsorted(bsorted, np.arange(len(pos) + 1))
    for b in range(len(pos)):
        seg = order[starts[b]:starts[b + 1]]
        seg = seg[w[seg, 0] > 0.3]
        if len(seg) == 0:
            continue
        if len(seg) > 400:
            seg = seg[:: len(seg) // 400 + 1]
        v = mesh.points[w[seg, 1].astype(int)]
        tot += float(np.min(np.linalg.norm(v - pos[b], axis=1)))
        cnt += 1
    return tot / cnt if cnt else 0.0


def solve_skeleton(mesh: PskMesh) -> Tuple[np.ndarray, np.ndarray, str]:
    """(회전, 위치, 고른 방식 설명). 위치가 가장 잘 맞는 켤레 조합을 고른다."""
    best = None
    for cr in (False, True):
        for cs in (False, True):
            rot, pos = _world_positions(mesh, cr, cs)
            err = _fit_error(mesh, pos)
            if best is None or err < best[0] - 1e-9:
                best = (err, rot, pos, "루트 켤레=%s, 나머지 켤레=%s, 오차 %.2f" % (cr, cs, err))
    return best[1], best[2], best[3]


# 언리얼(왼손 Z-up, 앞 +X, 오른쪽 +Y) -> 오른손 Z-up, 앞 -Y (Blender 규약). p' = (-y, -x, z), 단위 cm -> m
_T = np.array([[0.0, -1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
SCALE = 0.01


def to_blender(mesh: PskMesh, keep_bones: bool = True) -> Dict[str, np.ndarray]:
    """Blender 스크립트가 읽는 배열 묶음. 점/본은 미터, Z-up, 캐릭터는 -Y 를 본다."""
    verts = (mesh.points @ _T.T) * SCALE
    wf = mesh.faces
    # 삼각형 방향은 좌표계가 뒤집혔으므로 반대로 뒤집은 뒤, 바깥쪽을 향하는지 검사해 필요하면 되돌린다
    tri = wf[:, [0, 2, 1]]
    pidx = mesh.wedge_point[tri]
    a, b, c = verts[pidx[:, 0]], verts[pidx[:, 1]], verts[pidx[:, 2]]
    nrm = np.cross(b - a, c - a)
    ctr = (a + b + c) / 3.0 - verts.mean(0)
    flipped = False
    if len(tri) and float(np.sum(np.einsum("ij,ij->i", nrm, ctr))) < 0:
        tri = wf  # 반대로 뒤집는 편이 바깥쪽이었다
        flipped = True
    out: Dict[str, np.ndarray] = {
        "verts": verts.astype(np.float32),
        "tris": tri.astype(np.int64),  # 웨지 번호
        "wedge_point": mesh.wedge_point.astype(np.int64),
        "wedge_uv": mesh.wedge_uv.astype(np.float32),
        "face_mat": mesh.face_mat.astype(np.int64),
        "materials": np.array(mesh.materials, dtype=object),
        "name": np.array(mesh.name),
        "flipped": np.array(flipped),
    }
    if keep_bones and mesh.has_skeleton:
        rot, pos, _how = solve_skeleton(mesh)
        B = len(mesh.bone_names)
        mats = np.tile(np.eye(4), (B, 1, 1))
        for i in range(B):
            mats[i, :3, :3] = _T @ rot[i] @ _T.T
            mats[i, :3, 3] = (_T @ pos[i]) * SCALE
        out.update({
            "bone_names": np.array(mesh.bone_names, dtype=object),
            "bone_parent": np.array(mesh.bone_parent, dtype=np.int64),
            "bone_world": mats.astype(np.float32),
            "weights": mesh.weights.astype(np.float64),
        })
    return out


def save_payload(path: str, payload: Dict[str, np.ndarray]) -> None:
    np.savez(path, **payload)  # 문자열 배열은 object 라 pickle 이 필요하다 (Blender 쪽에서 allow_pickle=True)


# ----------------------------------------------------------------------------- OBJ (Blender 없이)
def write_obj(mesh: PskMesh, path: str) -> str:
    """리깅 없는 OBJ (Z-up, 미터, 캐릭터가 -Y 를 봄). 재질은 .mtl 이름만 만든다(텍스처 없음)."""
    p = to_blender(mesh, keep_bones=False)
    verts, tris = p["verts"], p["tris"]
    base = os.path.splitext(os.path.basename(path))[0]
    mtl = os.path.splitext(path)[0] + ".mtl"
    with open(mtl, "w", encoding="utf-8") as f:
        for m in mesh.materials:
            f.write("newmtl %s\nKd 0.8 0.8 0.8\n\n" % m)
    with open(path, "w", encoding="utf-8") as f:
        f.write("# MotionBridge\nmtllib %s.mtl\no %s\n" % (base, base))
        for v in verts:
            f.write("v %.6f %.6f %.6f\n" % tuple(v))
        for w in mesh.wedge_uv:
            f.write("vt %.6f %.6f\n" % (w[0], 1.0 - w[1]))
        wp = mesh.wedge_point
        last = -1
        for t, m in zip(tris, mesh.face_mat):
            if m != last:
                f.write("usemtl %s\n" % mesh.materials[int(m)])
                last = m
            f.write("f " + " ".join("%d/%d" % (wp[w] + 1, w + 1) for w in t) + "\n")
    return path
