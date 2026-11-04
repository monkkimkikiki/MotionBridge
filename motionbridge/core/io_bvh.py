"""BVH 읽기/쓰기.

읽기: HIERARCHY 의 ROOT/JOINT/End Site 를 모두 관절로 보존한다(End Site 는 '<부모>_End').
쓰기: 이미 '월드 정렬(zero-rest)' 형태로 변환된 스켈레톤/애니메이션을 받는다(export.to_world_aligned).
"""
from __future__ import annotations

from typing import List

import numpy as np

from . import quat as Q
from .model import Animation, Model, Skeleton


class BVHError(Exception):
    pass


def read_bvh(path: str) -> Model:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = [ln.strip() for ln in f.read().splitlines()]
    lines = [ln for ln in lines if ln]
    if not lines or not lines[0].upper().startswith("HIERARCHY"):
        raise BVHError("BVH 파일이 아닙니다: HIERARCHY 로 시작해야 합니다.")

    names: List[str] = []
    parents: List[int] = []
    offsets: List[np.ndarray] = []
    channels: List[List[str]] = []
    is_end: List[bool] = []

    stack: List[int] = []
    pending = None  # (kind, name)
    i = 1
    motion_line = -1
    while i < len(lines):
        ln = lines[i]
        tok = ln.split()
        key = tok[0].upper()
        if key in ("ROOT", "JOINT"):
            pending = ("joint", " ".join(tok[1:]))
        elif key == "END":  # End Site
            pending = ("end", None)
        elif key == "{":
            kind, nm = pending
            parent = stack[-1] if stack else -1
            if kind == "end":
                nm = names[parent] + "_End"
            names.append(nm)
            parents.append(parent)
            offsets.append(np.zeros(3))
            channels.append([])
            is_end.append(kind == "end")
            stack.append(len(names) - 1)
        elif key == "}":
            stack.pop()
        elif key == "OFFSET":
            offsets[stack[-1]] = np.array([float(x) for x in tok[1:4]])
        elif key == "CHANNELS":
            n = int(tok[1])
            channels[stack[-1]] = tok[2 : 2 + n]
        elif key == "MOTION":
            motion_line = i
            break
        i += 1
    if motion_line < 0:
        raise BVHError("MOTION 섹션이 없습니다.")

    frames = None
    frame_time = 1.0 / 30.0
    j = motion_line + 1
    while j < len(lines):
        low = lines[j].lower()
        if low.startswith("frames:"):
            frames = int(lines[j].split(":")[1])
        elif low.startswith("frame time:"):
            frame_time = float(lines[j].split(":")[1])
            j += 1
            break
        j += 1
    data_lines = lines[j:]
    total_ch = sum(len(c) for c in channels)
    values = np.array([[float(x) for x in ln.split()] for ln in data_lines if ln], dtype=float)
    if values.ndim != 2 or values.shape[1] != total_ch:
        raise BVHError("모션 열 개수(%s)가 채널 합계(%d)와 다릅니다." % (values.shape[1] if values.ndim == 2 else "?", total_ch))
    if frames is not None and values.shape[0] != frames:
        values = values[:frames]
    T = values.shape[0]
    J = len(names)

    skel_off = np.array(offsets)
    rot = Q.identity((T, J))
    pos = np.repeat(skel_off[None], T, axis=0)
    col = 0
    for jn in range(J):
        chs = channels[jn]
        if not chs:
            continue
        cols = values[:, col : col + len(chs)]
        col += len(chs)
        euler_axes, euler_vals = "", []
        for c, name in enumerate(chs):
            nm = name.lower()
            if nm.endswith("position"):
                pos[:, jn, "xyz".index(nm[0])] = cols[:, c]
            elif nm.endswith("rotation"):
                euler_axes += nm[0].upper()
                euler_vals.append(cols[:, c])
        if len(euler_axes) == 3:
            rot[:, jn] = Q.from_euler(np.stack(euler_vals, -1), euler_axes, degrees=True)
        elif euler_axes:
            raise BVHError("회전 채널이 3개가 아닌 관절이 있습니다: %s" % names[jn])

    # 위치 채널이 있고 OFFSET 이 0 인 관절(모캡 BVH 의 루트 등)은 첫 프레임 위치를 바인드 위치로 쓴다.
    pos_joints = [jn for jn in range(J) if any(c.lower().endswith("position") for c in channels[jn])]
    size = max(float(np.abs(skel_off).max()), 1e-6)
    changed = False
    for jn in pos_joints:
        if float(np.abs(skel_off[jn]).max()) < 1e-6 * max(size, 1.0):
            skel_off[jn] = pos[0, jn]
            changed = True
    skel = Skeleton(names, parents, skel_off, None, name=names[0] if names else "bvh")
    if changed:
        skel = _stand_on_floor(skel, [jn for jn in pos_joints if parents[jn] < 0 or parents[jn] in pos_joints or True])
    fps = 1.0 / frame_time if frame_time > 0 else 30.0
    anim = Animation(path.replace("\\", "/").split("/")[-1].rsplit(".", 1)[0], round(fps, 3), Q.make_continuous(rot), pos)
    return Model(path=path, skeleton=skel, animations=[anim], notes=[])


def _stand_on_floor(skel: Skeleton, pos_joints) -> Skeleton:
    """바인드 포즈의 가장 낮은 관절이 바닥(상방축 0)에 오도록 가장 위쪽 위치 채널 관절의 바인드 오프셋을 보정한다.
    상방축을 못 찾으면 그대로 둔다."""
    from .mapping import auto_map, detect_frame

    det = detect_frame(skel, auto_map(skel))
    if det is None or not pos_joints:
        return skel
    F = det[0]
    _, bwp = skel.bind_world()
    pc = Q.rotate(F, bwp)
    lift = -float(pc[:, 1].min())
    height = float(np.ptp(pc[:, 1]))
    if abs(lift) < 0.02 * max(height, 1e-9):
        return skel
    top = min(pos_joints, key=lambda j: skel.depth(j))
    off = skel.offsets.copy()
    off[top] = off[top] + Q.rotate(Q.conj(F), np.array([0.0, lift, 0.0]))
    return Skeleton(skel.names, skel.parents.copy(), off, skel.rest_rot.copy(), skel.name)


def write_bvh(path: str, skel: Skeleton, anim: Animation, order: str = "ZXY", precision: int = 5) -> None:
    """zero-rest 스켈레톤(rest_rot=항등)과 애니메이션을 BVH 로 쓴다."""
    if not np.allclose(skel.rest_rot, Q.identity((skel.n,)), atol=1e-6):
        raise BVHError("write_bvh 는 rest_rot 가 항등인 스켈레톤만 받습니다(to_world_aligned 먼저).")
    order = order.upper()
    fmt = "%%.%df" % precision
    # 이동이 있는 관절(루트 포함)은 위치 채널을 쓴다. 이때 OFFSET 은 0 으로 두고 채널에 전체 로컬 위치를 담는다.
    # (OFFSET 을 더하는 프로그램과 대체하는 프로그램 모두에서 같은 결과가 나오게 하기 위함)
    size = max(float(np.abs(skel.offsets).max()), 1e-6)
    has_pos = np.zeros(skel.n, dtype=bool)
    for j in range(skel.n):
        has_pos[j] = skel.parents[j] < 0 or float(np.abs(anim.pos[:, j] - skel.offsets[j][None]).max()) > 1e-5 * size

    def is_end_name(j):
        return skel.names[j].endswith("_End") and skel.is_leaf(j)

    # 쓸 관절(End 마커 제외)
    real = [j for j in range(skel.n) if not is_end_name(j)]
    ch_rot = " ".join(c + "rotation" for c in order)
    out: List[str] = ["HIERARCHY"]

    def default_end_offset(j):
        o = skel.offsets[j]
        n = np.linalg.norm(o)
        if n > 1e-9:
            return o / n * min(n * 0.5, max(n * 0.25, 1e-3)) if n > 0 else np.zeros(3)
        return np.array([0.0, 0.01, 0.0])

    emitted: List[int] = []  # 헤더에 나온 순서 = 모션 열 순서

    def emit(j, depth):
        emitted.append(j)
        pad = "\t" * depth
        kind = "ROOT" if skel.parents[j] < 0 else "JOINT"
        out.append("%s%s %s" % (pad, kind, skel.names[j].replace(" ", "_")))
        out.append(pad + "{")
        o = np.zeros(3) if has_pos[j] else skel.offsets[j]
        out.append("%s\tOFFSET %s" % (pad, " ".join(fmt % v for v in o)))
        if has_pos[j]:
            out.append("%s\tCHANNELS 6 Xposition Yposition Zposition %s" % (pad, ch_rot))
        else:
            out.append("%s\tCHANNELS 3 %s" % (pad, ch_rot))
        kids = [c for c in skel.children[j]]
        end_kids = [c for c in kids if is_end_name(c)]
        real_kids = [c for c in kids if not is_end_name(c)]
        for c in real_kids:
            emit(c, depth + 1)
        if not real_kids:
            eo = skel.offsets[end_kids[0]] if end_kids else default_end_offset(j)
            out.append(pad + "\tEnd Site")
            out.append(pad + "\t{")
            out.append("%s\t\tOFFSET %s" % (pad, " ".join(fmt % v for v in eo)))
            out.append(pad + "\t}")
        out.append(pad + "}")

    for r in skel.roots():
        emit(r, 0)

    T = anim.n_frames
    cols = []
    for j in emitted:
        if has_pos[j]:
            cols.append(anim.pos[:, j])
        ang = Q.to_euler(Q.make_continuous(anim.rot[:, j]), order, degrees=False)
        ang = np.degrees(np.unwrap(ang, axis=0))
        cols.append(ang)
    data = np.concatenate(cols, axis=1)
    out.append("MOTION")
    out.append("Frames: %d" % T)
    out.append("Frame Time: %.8f" % (1.0 / anim.fps))
    for t in range(T):
        out.append(" ".join(fmt % v for v in data[t]))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
