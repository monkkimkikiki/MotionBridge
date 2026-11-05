"""glTF / GLB 읽기와 쓰기 (스켈레톤, 스킨드 메시, 애니메이션).

읽기에서 노드 계층의 상위(비-관절) 변환(예: Blender 가 만든 Armature 노드의 스케일/회전)은
루트 관절에 굽는다. 위치는 누적 균일 스케일을 곱해 월드 단위로 저장한다.
"""
from __future__ import annotations

import base64
import json
import os
import struct
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import quat as Q
from .model import Animation, Mesh, Model, Skeleton, fk, topo_order

_COMP = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}


class GLTFError(Exception):
    pass


# --------------------------------------------------------------------------- 읽기
def _load_container(path: str):
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as f:
        raw = f.read()
    buffers: List[bytes] = []
    if ext == ".glb" or raw[:4] == b"glTF":
        magic, version, length = struct.unpack_from("<4sII", raw, 0)
        if magic != b"glTF":
            raise GLTFError("GLB 헤더가 올바르지 않습니다.")
        off = 12
        gltf = None
        binchunk = b""
        while off < len(raw):
            clen, ctype = struct.unpack_from("<II", raw, off)
            body = raw[off + 8 : off + 8 + clen]
            if ctype == 0x4E4F534A:
                gltf = json.loads(body.decode("utf-8"))
            elif ctype == 0x004E4942 and not binchunk:
                binchunk = bytes(body)
            off += 8 + clen
        if gltf is None:
            raise GLTFError("GLB 에 JSON 청크가 없습니다.")
        for b in gltf.get("buffers", []):
            if "uri" not in b:
                buffers.append(binchunk)
            else:
                buffers.append(_read_uri(b["uri"], os.path.dirname(path)))
    else:
        gltf = json.loads(raw.decode("utf-8"))
        for b in gltf.get("buffers", []):
            buffers.append(_read_uri(b["uri"], os.path.dirname(path)))
    return gltf, buffers


def _read_uri(uri: str, base: str) -> bytes:
    if uri.startswith("data:"):
        return base64.b64decode(uri.split(",", 1)[1])
    from urllib.parse import unquote

    with open(os.path.join(base, unquote(uri)), "rb") as f:
        return f.read()


def _accessor(gltf, buffers, idx: int) -> np.ndarray:
    acc = gltf["accessors"][idx]
    dtype = np.dtype(_COMP[acc["componentType"]])
    n = _NCOMP[acc["type"]]
    count = acc["count"]
    if "bufferView" not in acc:
        return np.zeros((count, n), dtype=np.float64)
    bv = gltf["bufferViews"][acc["bufferView"]]
    buf = buffers[bv["buffer"]]
    off = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = bv.get("byteStride") or n * dtype.itemsize
    if stride == n * dtype.itemsize:
        arr = np.frombuffer(buf, dtype=dtype, count=count * n, offset=off).reshape(count, n)
    else:
        arr = np.ndarray((count, n), dtype=dtype, buffer=buf, offset=off, strides=(stride, dtype.itemsize))
    arr = np.array(arr)
    if acc.get("normalized") and dtype.kind in "iu":
        arr = arr.astype(np.float64) / float(np.iinfo(dtype).max)
    return arr


def _material_of(gltf, buffers, prim, base_dir: str):
    """프리미티브의 머티리얼에서 (기본 색 RGBA 또는 None, 기본 색 텍스처 바이트 또는 None). 읽지 못하면 None."""
    try:
        mi = prim.get("material")
        if mi is None:
            return None, None, None
        mat = gltf["materials"][mi]
        pbr = mat.get("pbrMetallicRoughness", {})
        col = pbr.get("baseColorFactor")
        color = tuple(float(x) for x in col) if col and len(col) == 4 else None
        tex = None
        bt = pbr.get("baseColorTexture")
        if bt is not None:
            img = gltf["images"][gltf["textures"][bt["index"]]["source"]]
            if "bufferView" in img:
                bv = gltf["bufferViews"][img["bufferView"]]
                off = bv.get("byteOffset", 0)
                tex = bytes(buffers[bv["buffer"]][off:off + bv["byteLength"]])
            elif "uri" in img:
                tex = _read_uri(img["uri"], base_dir)
        return color, tex, mat.get("name")
    except (KeyError, IndexError, OSError, ValueError, TypeError):
        return None, None, None


def _trs_matrix(node) -> np.ndarray:
    if "matrix" in node:
        return np.array(node["matrix"], dtype=float).reshape(4, 4).T
    t = np.array(node.get("translation", [0, 0, 0]), dtype=float)
    r = node.get("rotation", [0, 0, 0, 1])
    s = np.array(node.get("scale", [1, 1, 1]), dtype=float)
    q = Q.normalize(np.array([r[3], r[0], r[1], r[2]], dtype=float))
    m = np.eye(4)
    m[:3, :3] = Q.to_matrix(q) * s[None, :]
    m[:3, 3] = t
    return m


def _decompose(m: np.ndarray):
    """4x4 -> (이동, 회전 쿼터니언(wxyz), 균일 스케일 근사)."""
    t = m[:3, 3].copy()
    cols = m[:3, :3]
    s = np.linalg.norm(cols, axis=0)
    s_mean = float(np.mean(s)) if np.all(s > 1e-12) else 1.0
    rm = cols / np.where(s < 1e-12, 1.0, s)[None, :]
    if np.linalg.det(rm) < 0:  # 반사 보정
        rm[:, 0] *= -1
    return t, Q.from_matrix(rm), s_mean


def _node_trs_parts(node):
    if "matrix" in node:
        t, q, s = _decompose(_trs_matrix(node))
        return t, q, s
    t = np.array(node.get("translation", [0, 0, 0]), dtype=float)
    r = node.get("rotation", [0, 0, 0, 1])
    q = Q.normalize(np.array([r[3], r[0], r[1], r[2]], dtype=float))
    sc = np.array(node.get("scale", [1, 1, 1]), dtype=float)
    return t, q, float(np.mean(np.abs(sc)))


def _resample(times: np.ndarray, values: np.ndarray, grid: np.ndarray, kind: str, interp: str) -> np.ndarray:
    if interp == "CUBICSPLINE":
        values = values[1::3]
    if len(times) == 1:
        return np.repeat(values[:1], len(grid), axis=0)
    idx = np.clip(np.searchsorted(times, grid, side="right") - 1, 0, len(times) - 2)
    t0, t1 = times[idx], times[idx + 1]
    u = np.clip((grid - t0) / np.where(t1 - t0 < 1e-12, 1.0, t1 - t0), 0.0, 1.0)
    a, b = values[idx], values[idx + 1]
    if interp == "STEP":
        out = a.copy()
        out[grid >= times[-1]] = values[-1]
        return out
    if kind == "rotation":
        return Q.slerp(a, b, u)
    return a + (b - a) * u[:, None]


def read_gltf(path: str, fps: Optional[float] = None) -> Model:
    gltf, buffers = _load_container(path)
    nodes = gltf.get("nodes", [])
    notes: List[str] = []
    if not nodes:
        raise GLTFError("노드가 없습니다.")

    parent_of: Dict[int, int] = {}
    for i, nd in enumerate(nodes):
        for c in nd.get("children", []):
            parent_of[c] = i

    skins = gltf.get("skins", [])
    if skins:
        skin_idx = int(np.argmax([len(s.get("joints", [])) for s in skins]))
        joint_nodes = list(skins[skin_idx]["joints"])
        if len(skins) > 1:
            notes.append("스킨이 %d개입니다. 관절이 가장 많은 스킨 #%d 를 사용합니다." % (len(skins), skin_idx))
    else:
        # 스킨이 없으면 애니메이션이 가리키는 노드 + 그 조상 중 계층을 이루는 노드를 관절로 본다.
        skin_idx = -1
        targeted = set()
        for an in gltf.get("animations", []):
            for ch in an.get("channels", []):
                if "node" in ch["target"]:
                    targeted.add(ch["target"]["node"])
        if not targeted:
            raise GLTFError("스킨도 애니메이션도 없어 스켈레톤을 만들 수 없습니다.")
        keep = set(targeted)
        for n in list(targeted):
            while n in parent_of:
                n = parent_of[n]
                keep.add(n)
        joint_nodes = sorted(keep)
        notes.append("스킨이 없어 애니메이션 대상 노드로 스켈레톤을 구성했습니다.")
    joint_set = set(joint_nodes)

    # 관절의 가장 가까운 관절 조상
    def joint_parent(n: int) -> int:
        while n in parent_of:
            n = parent_of[n]
            if n in joint_set:
                return n
        return -1

    jp = {n: joint_parent(n) for n in joint_nodes}
    # 위상 정렬
    node_list = list(joint_nodes)
    pidx = [(-1 if jp[n] < 0 else node_list.index(jp[n])) for n in node_list]
    order = topo_order(pidx)
    node_order = [node_list[i] for i in order]
    pos_in_new = {n: i for i, n in enumerate(node_order)}
    parents = [(-1 if jp[n] < 0 else pos_in_new[jp[n]]) for n in node_order]

    # 노드 월드 변환 (정적)
    world_cache: Dict[int, np.ndarray] = {}

    def world(n: int) -> np.ndarray:
        if n in world_cache:
            return world_cache[n]
        m = _trs_matrix(nodes[n])
        if n in parent_of:
            m = world(parent_of[n]) @ m
        world_cache[n] = m
        return m

    J = len(node_order)
    s_cum = np.ones(J)  # 각 관절 월드 행렬의 균일 스케일
    stat_t = np.zeros((J, 3))
    stat_q = Q.identity((J,))
    names: List[str] = []
    root_anc: Dict[int, np.ndarray] = {}
    for j, n in enumerate(node_order):
        nd = nodes[n]
        nm = nd.get("name", "node%d" % n)
        if nm in names:
            nm = "%s_%d" % (nm, n)
        names.append(nm)
        t, q, s = _node_trs_parts(nd)
        if parents[j] < 0:
            anc = world(parent_of[n]) if n in parent_of else np.eye(4)
            root_anc[j] = anc
            ta, qa, sa = _decompose(anc)
            s_cum[j] = sa * s
            stat_q[j] = Q.mul(qa, q)
            stat_t[j] = ta + Q.rotate(qa, sa * t)
        else:
            s_cum[j] = s_cum[parents[j]] * s
            stat_q[j] = q
            stat_t[j] = s_cum[parents[j]] * t
    skel = Skeleton(names, parents, stat_t, stat_q, name=os.path.splitext(os.path.basename(path))[0])

    # 애니메이션
    anims: List[Animation] = []
    for ai, an in enumerate(gltf.get("animations", [])):
        samplers = an.get("samplers", [])
        chans: Dict[Tuple[int, str], Tuple[np.ndarray, np.ndarray, str]] = {}
        tmax = 0.0
        dts: List[float] = []
        for ch in an.get("channels", []):
            tg = ch["target"]
            node = tg.get("node")
            path_ = tg.get("path")
            if node not in pos_in_new or path_ not in ("translation", "rotation"):
                continue
            sp = samplers[ch["sampler"]]
            tin = _accessor(gltf, buffers, sp["input"])[:, 0].astype(float)
            val = _accessor(gltf, buffers, sp["output"]).astype(float)
            if path_ == "rotation":
                val = np.concatenate([val[:, 3:4], val[:, 0:3]], axis=1)  # xyzw -> wxyz
            chans[(pos_in_new[node], path_)] = (tin, val, sp.get("interpolation", "LINEAR"))
            if len(tin):
                tmax = max(tmax, float(tin[-1]))
                if len(tin) > 1:
                    dts.append(float(np.median(np.diff(tin))))
        if not chans:
            continue
        use_fps = fps
        if use_fps is None:
            if dts:
                cand = 1.0 / max(min(dts), 1e-6)
                use_fps = min([24.0, 25.0, 30.0, 48.0, 50.0, 60.0, 120.0], key=lambda f: abs(f - cand))
            else:
                use_fps = 30.0
        nfr = max(int(round(tmax * use_fps)) + 1, 1)
        grid = np.arange(nfr, dtype=float) / use_fps
        rot = np.repeat(stat_q[None], nfr, axis=0)
        pos = np.repeat(stat_t[None], nfr, axis=0)
        for (j, kind), (tin, val, interp) in chans.items():
            if kind == "rotation":
                r = _resample(tin, val, grid, "rotation", interp)
                r = Q.normalize(r)
                if parents[j] < 0:
                    qa = _decompose(root_anc[j])[1]
                    r = Q.mul(qa, r)
                rot[:, j] = r
            else:
                tv = _resample(tin, val, grid, "translation", interp)
                if parents[j] < 0:
                    ta, qa, sa = _decompose(root_anc[j])
                    pos[:, j] = ta + Q.rotate(qa, sa * tv)
                else:
                    pos[:, j] = s_cum[parents[j]] * tv
        nm = an.get("name") or "anim%d" % ai
        anims.append(Animation(nm, float(use_fps), Q.make_continuous(rot), pos))

    # 메시
    meshes: List[Mesh] = []
    if skin_idx >= 0:
        skin = skins[skin_idx]
        skin_joint_to_idx = np.array([pos_in_new[n] for n in skin["joints"]], dtype=int)
        bwr, bwp = skel.bind_world()
        for ni, nd in enumerate(nodes):
            if "mesh" not in nd or nd.get("skin") != skin_idx:
                continue
            gmesh = gltf["meshes"][nd["mesh"]]
            for pi, prim in enumerate(gmesh.get("primitives", [])):
                at = prim.get("attributes", {})
                if "POSITION" not in at or "JOINTS_0" not in at or "WEIGHTS_0" not in at:
                    continue
                if prim.get("mode", 4) != 4:
                    continue
                v = _accessor(gltf, buffers, at["POSITION"]).astype(float)
                jn = _accessor(gltf, buffers, at["JOINTS_0"]).astype(int)
                wt = _accessor(gltf, buffers, at["WEIGHTS_0"]).astype(float)
                if "indices" in prim:
                    idx = _accessor(gltf, buffers, prim["indices"])[:, 0].astype(np.int64)
                else:
                    idx = np.arange(len(v), dtype=np.int64)
                faces = idx[: len(idx) // 3 * 3].reshape(-1, 3)
                wsum = wt.sum(axis=1, keepdims=True)
                wt = wt / np.where(wsum < 1e-9, 1.0, wsum)
                raw_jn = np.clip(jn, 0, len(skin_joint_to_idx) - 1)  # skin.joints 기준 인덱스
                jn_skel = skin_joint_to_idx[raw_jn]  # 스켈레톤 인덱스
                # 바인드 포즈 월드 좌표: sum(w * 관절월드 * IBM * v). IBM 이 없으면 메시 노드 월드 행렬.
                vh = np.concatenate([v, np.ones((len(v), 1))], axis=1)
                if "inverseBindMatrices" in skin:
                    ibm = _accessor(gltf, buffers, skin["inverseBindMatrices"]).astype(float).reshape(-1, 4, 4).transpose(0, 2, 1)
                    jw = np.stack([world(n) for n in skin["joints"]])
                    vw = np.zeros((len(v), 3))
                    for k in range(raw_jn.shape[1]):
                        sj = raw_jn[:, k]
                        m = jw[sj] @ ibm[sj]
                        vw += wt[:, k : k + 1] * np.einsum("nij,nj->ni", m, vh)[:, :3]
                else:
                    vw = (world(ni) @ vh.T).T[:, :3]
                uv = _accessor(gltf, buffers, at["TEXCOORD_0"]).astype(float) if "TEXCOORD_0" in at else None
                mcolor, mtex, mname = _material_of(gltf, buffers, prim, os.path.dirname(path))
                meshes.append(Mesh(nd.get("name", "mesh%d" % ni) + ("_%d" % pi if pi else ""), vw, faces, jn_skel, wt, uv, mcolor, mtex, mname))
    return Model(path=path, skeleton=skel, animations=anims, meshes=meshes, notes=notes)


# --------------------------------------------------------------------------- 쓰기
class _Buf:
    def __init__(self):
        self.data = bytearray()
        self.views: List[dict] = []
        self.accessors: List[dict] = []

    def add(self, arr: np.ndarray, comp_type: int, typ: str, target: Optional[int] = None, minmax: bool = False) -> int:
        raw = np.ascontiguousarray(arr).tobytes()
        while len(self.data) % 4:
            self.data.append(0)
        view = {"buffer": 0, "byteOffset": len(self.data), "byteLength": len(raw)}
        if target:
            view["target"] = target
        self.data.extend(raw)
        self.views.append(view)
        acc = {"bufferView": len(self.views) - 1, "componentType": comp_type, "count": int(arr.shape[0]), "type": typ}
        if minmax:
            a2 = arr.reshape(arr.shape[0], -1)
            acc["min"] = [float(x) for x in a2.min(axis=0)]
            acc["max"] = [float(x) for x in a2.max(axis=0)]
        self.accessors.append(acc)
        return len(self.accessors) - 1


def write_glb(
    path: str,
    skel: Skeleton,
    anim: Optional[Animation],
    meshes: Optional[List[Mesh]] = None,
    translation_threshold: float = 1e-6,
    keys=None,
) -> None:
    """스켈레톤 + (선택) 애니메이션 + (선택) 스킨드 메시를 GLB 로 쓴다. Y-up/미터 가정은 호출자 책임."""
    J = skel.n
    buf = _Buf()
    nodes: List[dict] = []
    for j in range(J):
        q = skel.rest_rot[j]
        nodes.append(
            {
                "name": skel.names[j],
                "translation": [float(x) for x in skel.offsets[j]],
                "rotation": [float(q[1]), float(q[2]), float(q[3]), float(q[0])],
            }
        )
    for j in range(J):
        p = skel.parents[j]
        if p >= 0:
            nodes[p].setdefault("children", []).append(j)
    root_nodes = [j for j in range(J) if skel.parents[j] < 0]

    bwr, bwp = skel.bind_world()
    ibm = np.zeros((J, 4, 4), dtype=np.float32)
    for j in range(J):
        m = np.eye(4)
        m[:3, :3] = Q.to_matrix(bwr[j])
        m[:3, 3] = bwp[j]
        ibm[j] = np.linalg.inv(m).T  # 열 우선 저장을 위해 전치
    ibm_acc = buf.add(ibm.reshape(J, 16), 5126, "MAT4")
    skin = {"joints": list(range(J)), "inverseBindMatrices": ibm_acc, "skeleton": root_nodes[0]}

    gl_meshes: List[dict] = []
    materials: List[dict] = []
    textures: List[dict] = []
    images: List[dict] = []
    scene_nodes = list(root_nodes)
    if meshes:
        for mi, m in enumerate(meshes):
            at = {
                "POSITION": buf.add(m.vertices.astype(np.float32), 5126, "VEC3", 34962, minmax=True),
            }
            K = m.joints.shape[1]
            j4 = np.zeros((len(m.vertices), 4), dtype=np.uint16)
            w4 = np.zeros((len(m.vertices), 4), dtype=np.float32)
            j4[:, : min(K, 4)] = m.joints[:, :4]
            w4[:, : min(K, 4)] = m.weights[:, :4]
            s = w4.sum(axis=1, keepdims=True)
            w4 = w4 / np.where(s < 1e-9, 1.0, s)
            at["JOINTS_0"] = buf.add(j4, 5123, "VEC4", 34962)
            at["WEIGHTS_0"] = buf.add(w4, 5126, "VEC4", 34962)
            if m.uv is not None:
                at["TEXCOORD_0"] = buf.add(m.uv.astype(np.float32), 5126, "VEC2", 34962)
            idx = buf.add(m.faces.astype(np.uint32).reshape(-1, 1), 5125, "SCALAR", 34963)
            prim = {"attributes": at, "indices": idx, "mode": 4}
            if m.color is not None or m.texture is not None:
                pbr: dict = {"metallicFactor": 0.0, "roughnessFactor": 1.0}
                if m.color is not None:
                    pbr["baseColorFactor"] = [float(x) for x in m.color]
                if m.texture is not None and m.uv is not None:
                    while len(buf.data) % 4:
                        buf.data.append(0)
                    buf.views.append({"buffer": 0, "byteOffset": len(buf.data), "byteLength": len(m.texture)})
                    buf.data.extend(m.texture)
                    mime = "image/png" if m.texture[:4] == b"\x89PNG" else "image/jpeg"
                    images.append({"bufferView": len(buf.views) - 1, "mimeType": mime})
                    textures.append({"source": len(images) - 1})
                    pbr["baseColorTexture"] = {"index": len(textures) - 1}
                materials.append({"name": m.name + "_mat", "pbrMetallicRoughness": pbr, "doubleSided": True})
                prim["material"] = len(materials) - 1
            gl_meshes.append({"name": m.name, "primitives": [prim]})
            nodes.append({"name": m.name, "mesh": mi, "skin": 0})
            scene_nodes.append(len(nodes) - 1)

    animations = []
    if anim is not None and anim.n_frames > 0:
        T = anim.n_frames
        times = (np.arange(T, dtype=np.float32) / np.float32(anim.fps)).reshape(T, 1)
        t_acc = buf.add(times, 5126, "SCALAR", minmax=True)
        samplers, channels = [], []

        def time_acc(idx: np.ndarray) -> int:
            """키 목록이면 그 프레임들만의 시간 accessor, 아니면 전체 프레임 공용 accessor."""
            if keys is None or len(idx) == T:
                return t_acc
            return buf.add((idx.astype(np.float32) / np.float32(anim.fps)).reshape(-1, 1), 5126, "SCALAR", minmax=True)

        for j in range(J):
            ir = np.arange(T) if keys is None else np.asarray(keys.rot[j])
            r = anim.rot[ir, j]
            r_xyzw = np.concatenate([r[:, 1:4], r[:, 0:1]], axis=1).astype(np.float32)
            a = buf.add(r_xyzw, 5126, "VEC4")
            samplers.append({"input": time_acc(ir), "output": a, "interpolation": "LINEAR"})
            channels.append({"sampler": len(samplers) - 1, "target": {"node": j, "path": "rotation"}})
            moving = (skel.parents[j] < 0) or (np.abs(anim.pos[:, j] - anim.pos[0, j]).max() > translation_threshold) or (
                np.abs(anim.pos[0, j] - skel.offsets[j]).max() > translation_threshold
            )
            if moving:
                ip = np.arange(T) if keys is None else np.asarray(keys.pos[j])
                a = buf.add(anim.pos[ip, j].astype(np.float32), 5126, "VEC3")
                samplers.append({"input": time_acc(ip), "output": a, "interpolation": "LINEAR"})
                channels.append({"sampler": len(samplers) - 1, "target": {"node": j, "path": "translation"}})
        animations.append({"name": anim.name, "samplers": samplers, "channels": channels})

    doc = {
        "asset": {"version": "2.0", "generator": "MotionBridge"},
        "scene": 0,
        "scenes": [{"nodes": scene_nodes}],
        "nodes": nodes,
        "skins": [skin],
        "buffers": [{"byteLength": len(buf.data)}],
        "bufferViews": buf.views,
        "accessors": buf.accessors,
    }
    if gl_meshes:
        doc["meshes"] = gl_meshes
    if materials:
        doc["materials"] = materials
    if textures:
        doc["textures"] = textures
        doc["images"] = images
    if animations:
        doc["animations"] = animations

    js = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    while len(js) % 4:
        js += b" "
    binb = bytes(buf.data)
    while len(binb) % 4:
        binb += b"\x00"
    total = 12 + 8 + len(js) + 8 + len(binb)
    with open(path, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<II", len(js), 0x4E4F534A))
        f.write(js)
        f.write(struct.pack("<II", len(binb), 0x004E4942))
        f.write(binb)
