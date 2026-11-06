"""AI 모캡(GVHMR / WHAM) 결과 -> 이 프로그램의 모션.

두 엔진 모두 SMPL 계열 인체 모델의 매개변수(루트 회전 + 몸 관절 회전 + 이동)를 돌려준다. 엔진 환경 안에서 돌리는 작은 스크립트
(mocapai.DUMP_PY)가 .npz 로 덤프하고, 여기서는 그 .npz 를 우리 스켈레톤/애니메이션으로 바꾼다 (numpy 만 쓴다).

.npz 내용:  joints (22,3) 기본 자세 관절 위치(m) · root_orient (F,3) · body_pose (F,21,3) 축-각 · transl (F,3) · fps
SMPL 의 몸 관절 22개: 골반 · 다리 · 척추 · 목 · 머리 · 쇄골 · 팔. 손가락은 다루지 않는다.
"""
from __future__ import annotations

import os
from typing import Tuple

import numpy as np

from . import quat as Q
from .io_bvh import write_bvh
from .model import Animation, Model, Skeleton

SMPL22_NAMES = [
    "Pelvis", "L_Hip", "R_Hip", "Spine1", "L_Knee", "R_Knee", "Spine2", "L_Ankle", "R_Ankle", "Spine3", "L_Foot", "R_Foot",
    "Neck", "L_Collar", "R_Collar", "Head", "L_Shoulder", "R_Shoulder", "L_Elbow", "R_Elbow", "L_Wrist", "R_Wrist",
]
SMPL22_PARENTS = [-1, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9, 12, 13, 14, 16, 17, 18, 19]
CM = 100.0  # BVH/FBX 쪽에서 흔한 단위(cm)로 내보낸다. 크기는 리타깃에서 자동으로 맞춘다.


def axis_angle_to_quat(aa: np.ndarray) -> np.ndarray:
    """(..., 3) 축-각 벡터 -> (..., 4) 쿼터니언 (w,x,y,z 는 quat 모듈의 규약을 따른다)."""
    aa = np.asarray(aa, dtype=float)
    ang = np.linalg.norm(aa, axis=-1)
    axis = np.where(ang[..., None] > 1e-9, aa / np.maximum(ang[..., None], 1e-9), np.array([1.0, 0.0, 0.0]))
    return Q.from_axis_angle(axis, ang)


def build(joints: np.ndarray, root_orient: np.ndarray, body_pose: np.ndarray, transl: np.ndarray, fps: float = 30.0,
          name: str = "AI_Mocap") -> Model:
    """SMPL 매개변수 -> Model(스켈레톤 1개 + 애니메이션 1개). 위치는 cm."""
    J = np.asarray(joints, dtype=float).reshape(-1, 3)[:22]
    if J.shape[0] != 22:
        raise ValueError("관절 위치가 22개가 아닙니다: %s" % (J.shape,))
    F = int(np.asarray(root_orient).shape[0])
    body = np.asarray(body_pose, dtype=float).reshape(F, -1)[:, :63].reshape(F, 21, 3)
    parents = np.asarray(SMPL22_PARENTS)
    offsets = J.copy()
    offsets[1:] = J[1:] - J[parents[1:]]
    offsets = offsets * CM
    skel = Skeleton(SMPL22_NAMES, parents, offsets, name="SMPL")
    rot = np.zeros((F, 22, 4))
    rot[:, 0] = axis_angle_to_quat(np.asarray(root_orient, dtype=float).reshape(F, 3))
    rot[:, 1:] = axis_angle_to_quat(body)
    pos = np.repeat(offsets[None], F, axis=0)
    pos[:, 0] = (J[0][None] + np.asarray(transl, dtype=float).reshape(F, 3)) * CM
    return Model("", skel, [Animation(name, float(fps), Q.normalize(rot), pos)])


def load_npz(path: str, name: str = "") -> Model:
    d = np.load(path)
    for k in ("joints", "root_orient", "body_pose", "transl"):
        if k not in d.files:
            raise ValueError("%s 에 '%s' 가 없습니다 (엔진 쪽 덤프가 실패했을 수 있어요)." % (os.path.basename(path), k))
    fps = float(d["fps"]) if "fps" in d.files else 30.0
    m = build(d["joints"], d["root_orient"], d["body_pose"], d["transl"], fps, name or os.path.splitext(os.path.basename(path))[0])
    m.path = path
    return m


def npz_to_bvh(npz_path: str, bvh_path: str, name: str = "") -> Tuple[str, int]:
    """덤프(.npz) -> BVH 파일. (경로, 프레임 수)"""
    m = load_npz(npz_path, name)
    os.makedirs(os.path.dirname(os.path.abspath(bvh_path)), exist_ok=True)
    write_bvh(bvh_path, m.skeleton, m.animations[0])
    return bvh_path, m.animations[0].n_frames
