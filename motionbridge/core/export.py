"""내보내기: Max / Maya / Blender 프리셋, BVH, GLB, 커브 JSON, 스켈레톤 JSON.

핵심 개념
  * reframe  : 스켈레톤+애니메이션을 표준 프레임(Y-up, +Z 전방) 또는 프리셋 프레임/단위로 옮긴다.
               루트 관절만 바꾸면 되므로 하위 관절의 로컬 데이터는 단위 스케일만 곱한다.
  * to_world_aligned : BVH 는 '모든 관절의 바인드 축이 월드와 정렬'인 형태만 표현할 수 있어서 변환이 필요하다.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import quat as Q
from .io_bvh import write_bvh
from .io_gltf import write_glb
from .model import Animation, Mesh, Skeleton, fk


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    up: str  # 'Y' | 'Z'
    units_per_meter: float  # 출력 단위: 1 = 미터, 100 = 센티미터
    euler_order: str
    fbx_forward: str  # Blender FBX 내보내기 축 옵션
    fbx_up: str
    note: str = ""


PRESETS: Dict[str, Preset] = {
    "maya": Preset("maya", "Maya", "Y", 100.0, "XYZ", "-Z", "Y", "Y-up, cm, 캐릭터는 +Z 를 바라봄"),
    "max": Preset("max", "3ds Max", "Z", 100.0, "ZXY", "Y", "Z", "Z-up, cm, 캐릭터는 -Y 를 바라봄"),
    "blender": Preset("blender", "Blender", "Y", 1.0, "ZXY", "-Z", "Y", "BVH: Y-up/미터(Blender BVH 가져오기 기본 축 -Z/Y), FBX: Blender 기본 변환"),
}


def preset_frame(preset: Preset) -> np.ndarray:
    """표준 프레임(Y-up, +Z 전방) -> 프리셋 프레임 회전."""
    if preset.up == "Y":
        return Q.identity(())
    return Q.from_axis_angle(np.array([1.0, 0.0, 0.0]), np.radians(90.0))  # Y->Z, Z->-Y


def guess_units_per_meter(skel: Skeleton, frame: np.ndarray) -> float:
    """바인드 포즈 키로 단위 추정: 5 초과면 센티미터(100), 아니면 미터(1)."""
    _, bwp = skel.bind_world()
    p = Q.rotate(frame, bwp)
    height = float(np.ptp(p[:, 1]))
    if height > 20.0:
        return 100.0
    if height > 5.0:
        return 100.0  # 5~20 은 애매하지만 미터일 수 없으므로 cm 쪽
    return 1.0


def reframe(skel: Skeleton, anim: Optional[Animation], rot: np.ndarray, scale: float, meshes: Optional[List[Mesh]] = None):
    """월드 회전 rot 과 균일 스케일 scale 을 적용한 새 (skeleton, animation, meshes). 루트만 변환하면 된다."""
    offsets = skel.offsets * scale
    rest = skel.rest_rot.copy()
    for r in skel.roots():
        rest[r] = Q.mul(rot, rest[r])
        offsets[r] = Q.rotate(rot, skel.offsets[r] * scale)
    sk2 = Skeleton(skel.names, skel.parents.copy(), offsets, rest, skel.name)
    an2 = None
    if anim is not None:
        arot = anim.rot.copy()
        apos = anim.pos * scale
        for r in skel.roots():
            arot[:, r] = Q.mul(rot, anim.rot[:, r])
            apos[:, r] = Q.rotate(rot, anim.pos[:, r] * scale)
        an2 = Animation(anim.name, anim.fps, arot, apos)
    ms2 = None
    if meshes:
        ms2 = [Mesh(m.name, Q.rotate(rot, m.vertices * scale), m.faces, m.joints, m.weights, m.uv) for m in meshes]
    return sk2, an2, ms2


def to_world_aligned(skel: Skeleton, anim: Animation) -> Tuple[Skeleton, Animation, List[str]]:
    """바인드 포즈에서 모든 관절의 축이 월드와 정렬된(rest_rot=항등) 표현으로 변환."""
    warns: List[str] = []
    wr, wp = fk(skel, anim.rot, anim.pos)
    bwr, bwp = skel.bind_world()
    D = Q.mul(wr, Q.conj(bwr)[None])  # (T,J,4) 바인드 대비 월드 회전 변화
    J = skel.n
    T = anim.n_frames
    rot0 = np.empty_like(D)
    pos0 = np.empty((T, J, 3))
    off0 = np.empty((J, 3))
    for j in range(J):
        p = skel.parents[j]
        if p < 0:
            rot0[:, j] = D[:, j]
            pos0[:, j] = wp[:, j]
            off0[j] = bwp[j]
        else:
            rot0[:, j] = Q.mul(Q.conj(D[:, p]), D[:, j])
            pos0[:, j] = Q.rotate(Q.conj(D[:, p]), wp[:, j] - wp[:, p])
            off0[j] = bwp[j] - bwp[p]
    sk0 = Skeleton(skel.names, skel.parents.copy(), off0, None, skel.name)
    return sk0, Animation(anim.name, anim.fps, Q.make_continuous(rot0), pos0), warns


def _safe(name: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in name) or "clip"


def export_bvh(path: str, skel: Skeleton, anim: Animation, frame_to_canon: np.ndarray, preset: Preset, units_per_meter: float) -> List[str]:
    """skel/anim 은 원본 파일 프레임. frame_to_canon: 원본->표준 프레임 회전. 반환: 경고 목록."""
    rot = Q.mul(preset_frame(preset), frame_to_canon)
    scale = preset.units_per_meter / units_per_meter
    sk, an, _ = reframe(skel, anim, rot, scale)
    sk0, an0, warns = to_world_aligned(sk, an)
    write_bvh(path, sk0, an0, order=preset.euler_order)
    return warns


def export_glb(path: str, skel: Skeleton, anim: Optional[Animation], frame_to_canon: np.ndarray, units_per_meter: float, meshes: Optional[List[Mesh]] = None) -> None:
    """glTF 규격(Y-up, 미터)으로 변환해서 쓴다."""
    sk, an, ms = reframe(skel, anim, frame_to_canon, 1.0 / units_per_meter, meshes)
    write_glb(path, sk, an, ms)


def curves_dict(skel: Skeleton, anim: Animation) -> dict:
    """스켈레톤 구조 없이 애니메이션 커브만. 관절 이름 기준으로 이름이 같은 리그에 적용할 때 쓴다."""
    return {
        "format": "motionbridge-curves-1",
        "name": anim.name,
        "fps": anim.fps,
        "frames": anim.n_frames,
        "rotation_order": "wxyz quaternion, local",
        "joints": {
            skel.names[j]: {
                "rotation": np.round(anim.rot[:, j], 6).tolist(),
                "position": np.round(anim.pos[:, j], 6).tolist() if (skel.parents[j] < 0 or np.abs(anim.pos[:, j] - skel.offsets[j]).max() > 1e-6) else None,
            }
            for j in range(skel.n)
        },
    }


def skeleton_dict(skel: Skeleton) -> dict:
    bwr, bwp = skel.bind_world()
    return {
        "format": "motionbridge-skeleton-1",
        "name": skel.name,
        "joints": [
            {
                "name": skel.names[j],
                "parent": (skel.names[skel.parents[j]] if skel.parents[j] >= 0 else None),
                "offset": np.round(skel.offsets[j], 6).tolist(),
                "rest_rotation_wxyz": np.round(skel.rest_rot[j], 6).tolist(),
                "bind_world_position": np.round(bwp[j], 6).tolist(),
            }
            for j in range(skel.n)
        ],
    }


def write_json(path: str, obj: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def write_blender_payload(path: str, skel: Skeleton, anim: Animation, name: str) -> None:
    """Blender 적용 스크립트용 NPZ: 월드 회전/위치(원본 파일 프레임)와 바인드."""
    wr, wp = fk(skel, anim.rot, anim.pos)
    bwr, bwp = skel.bind_world()
    np.savez_compressed(
        path,
        names=np.array(skel.names, dtype=object),
        wr=wr,
        wp=wp,
        bind_wr=bwr,
        bind_wp=bwp,
        fps=np.array(anim.fps),
        clip=np.array(name, dtype=object),
    )
