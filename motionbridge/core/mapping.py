"""휴머노이드 본 자동 매핑과 좌표 프레임(상방/전방) 추정.

이름 기반 자동 매핑은 Mixamo / Unreal Mannequin / 3ds Max Biped / Rigify / CC 계열 이름을 대상으로 한다.
틀린 곳은 UI 에서 사람이 고치는 것을 전제로 한다.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import quat as Q
from .model import Skeleton

# ---------------------------------------------------------------------------- 슬롯
_BODY = ["Hips", "Spine", "Spine1", "Spine2", "Neck", "Head"]
_ARM = ["Shoulder", "UpperArm", "LowerArm", "Hand"]
_LEG = ["UpLeg", "LowLeg", "Foot", "Toe"]
FINGERS = ["Thumb", "Index", "Middle", "Ring", "Pinky"]

SLOT_ORDER: List[str] = list(_BODY)
for _side in ("L", "R"):
    SLOT_ORDER += ["%s_%s" % (_side, p) for p in _ARM]
for _side in ("L", "R"):
    SLOT_ORDER += ["%s_%s" % (_side, p) for p in _LEG]
for _side in ("L", "R"):
    for _f in FINGERS:
        SLOT_ORDER += ["%s_%s%d" % (_side, _f, i) for i in (1, 2, 3)]

_KO = {
    "Hips": "골반(Hips)", "Spine": "척추1", "Spine1": "척추2", "Spine2": "가슴(척추 끝)", "Neck": "목", "Head": "머리",
    "Shoulder": "쇄골", "UpperArm": "위팔", "LowerArm": "아래팔", "Hand": "손",
    "UpLeg": "허벅지", "LowLeg": "종아리", "Foot": "발", "Toe": "발가락(볼)",
}
_KO_F = {"Thumb": "엄지", "Index": "검지", "Middle": "중지", "Ring": "약지", "Pinky": "새끼"}


def slot_label(slot: str) -> str:
    if "_" in slot:
        side, part = slot.split("_", 1)
        sd = "왼쪽 " if side == "L" else "오른쪽 "
        m = re.match(r"([A-Za-z]+)(\d)$", part)
        if m and m.group(1) in _KO_F:
            return "%s%s %s마디" % (sd, _KO_F[m.group(1)], m.group(2))
        return sd + _KO.get(part, part)
    return _KO.get(slot, slot)


# ---------------------------------------------------------------------------- 이름 분류
_PREFIX = re.compile(r"^(bip\d*|mixamorig\d*|cc_base|def|org|mch|j_bip_[a-z]|j_adj)[_:\- ]+", re.I)
_TOKEN = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")
_SKIP = {"twist", "roll", "helper", "ik", "pole", "nub", "end", "top", "tip", "corrective", "tweak", "jiggle", "ctrl", "null", "dummy", "target", "proxy"}
_FINGER_WORDS = {"thumb": "Thumb", "index": "Index", "middle": "Middle", "ring": "Ring", "pinky": "Pinky", "little": "Pinky"}


def _tokens(name: str) -> List[str]:
    name = name.split(":")[-1]
    name = _PREFIX.sub("", name)
    return [t.lower() for t in _TOKEN.findall(name)]


def _split_side(toks: List[str]) -> Tuple[Optional[str], List[str]]:
    side = None
    rest = []
    for t in toks:
        if t in ("l", "left") and side is None:
            side = "L"
        elif t in ("r", "right") and side is None:
            side = "R"
        else:
            rest.append(t)
    return side, rest


def classify_name(name: str):
    """이름 -> (side, part, finger_digit). part: Hips/Spine/Neck/Head/Shoulder/UpperArm/LowerArm/Hand/UpLeg/LowLeg/Foot/Toe/PlainLeg/Finger:<이름>."""
    toks = _tokens(name)
    side, ts = _split_side(toks)
    tset = set(ts)
    if tset & _SKIP:
        return None
    digits = [int(t) for t in ts if t.isdigit()]
    # 손가락
    for w, fname in _FINGER_WORDS.items():
        if w in tset:
            if side is None:
                return None
            return side, "Finger:" + fname, (digits[-1] if digits else None)
    if side is None:
        if tset & {"hips", "pelvis", "hip"}:
            return None, "Hips", None
        if tset & {"spine", "chest", "abdomen", "torso"}:
            return None, "Spine", (digits[-1] if digits else 0)
        if "neck" in tset:
            return None, "Neck", (digits[-1] if digits else 0)
        if "head" in tset:
            return None, "Head", None
        return None
    # 좌/우가 있는 부위
    if tset & {"shoulder", "clavicle", "collar"}:
        return side, "Shoulder", None
    if tset & {"lowerarm", "forearm", "elbow"} or ({"fore", "arm"} <= tset) or ({"lower", "arm"} <= tset):
        return side, "LowerArm", None
    if "upperarm" in tset or ({"upper", "arm"} <= tset) or "arm" in tset:
        return side, "UpperArm", None
    if "hand" in tset or "wrist" in tset:
        return side, "Hand", None
    if tset & {"toe", "toes", "ball"}:
        return side, "Toe", None
    if tset & {"foot", "ankle"}:
        return side, "Foot", None
    if tset & {"calf", "shin", "lowleg", "lowerleg", "knee"} or ({"lower", "leg"} <= tset) or ({"low", "leg"} <= tset):
        return side, "LowLeg", None
    if tset & {"thigh", "upleg", "upperleg"} or ({"up", "leg"} <= tset) or ({"upper", "leg"} <= tset):
        return side, "UpLeg", None
    if "leg" in tset:
        return side, "PlainLeg", None
    return None


def auto_map(skel: Skeleton) -> Dict[str, int]:
    """슬롯 이름 -> 관절 인덱스."""
    cands: Dict[Tuple[Optional[str], str], List[Tuple[int, Optional[int]]]] = {}
    for j, nm in enumerate(skel.names):
        c = classify_name(nm)
        if c is None:
            continue
        side, part, dg = c
        cands.setdefault((side, part), []).append((j, dg))

    def shallowest(lst):
        return min(lst, key=lambda x: (skel.depth(x[0]), x[0]))[0]

    out: Dict[str, int] = {}
    if (None, "Hips") in cands:
        out["Hips"] = shallowest(cands[(None, "Hips")])
    sp = sorted(cands.get((None, "Spine"), []), key=lambda x: (skel.depth(x[0]), x[0]))
    if sp:
        idxs = [x[0] for x in sp]
        if len(idxs) == 1:
            out["Spine"] = idxs[0]
        elif len(idxs) == 2:
            out["Spine"], out["Spine2"] = idxs[0], idxs[1]
        else:
            out["Spine"], out["Spine1"], out["Spine2"] = idxs[0], idxs[len(idxs) // 2], idxs[-1]
    for part in ("Neck", "Head"):
        if (None, part) in cands:
            out[part] = shallowest(cands[(None, part)])
    for side in ("L", "R"):
        has_up = (side, "UpLeg") in cands
        for part in ("Shoulder", "UpperArm", "LowerArm", "Hand", "UpLeg", "LowLeg", "Foot", "Toe"):
            key = (side, part)
            lst = list(cands.get(key, []))
            if part == "LowLeg" and (side, "PlainLeg") in cands and has_up:
                lst += cands[(side, "PlainLeg")]
            if part == "UpLeg" and (side, "PlainLeg") in cands and not (side, "LowLeg") in cands and not has_up:
                lst += cands[(side, "PlainLeg")]
            if lst:
                out["%s_%s" % (side, part)] = shallowest(lst)
        for f in FINGERS:
            lst = cands.get((side, "Finger:" + f), [])
            if not lst:
                continue
            with_d = [(j, d) for j, d in lst if d is not None]
            if with_d:
                mn = min(d for _, d in with_d)
                shift = 1 if mn == 0 else 0
                for j, d in with_d:
                    di = d + shift
                    if 1 <= di <= 3:
                        key = "%s_%s%d" % (side, f, di)
                        if key not in out or skel.depth(j) < skel.depth(out[key]):
                            out[key] = j
            else:
                chain = sorted(lst, key=lambda x: skel.depth(x[0]))[:3]
                for n, (j, _) in enumerate(chain, 1):
                    out["%s_%s%d" % (side, f, n)] = j
    return out


def pairs_from_slots(src_map: Dict[str, int], tgt_map: Dict[str, int]) -> List[Tuple[int, int]]:
    pairs = []
    used_t = set()
    for slot in SLOT_ORDER:
        if slot in src_map and slot in tgt_map and tgt_map[slot] not in used_t:
            pairs.append((src_map[slot], tgt_map[slot]))
            used_t.add(tgt_map[slot])
    return pairs


# ---------------------------------------------------------------------------- 좌표 프레임
AXES = {
    "+X": np.array([1.0, 0, 0]), "-X": np.array([-1.0, 0, 0]),
    "+Y": np.array([0, 1.0, 0]), "-Y": np.array([0, -1.0, 0]),
    "+Z": np.array([0, 0, 1.0]), "-Z": np.array([0, 0, -1.0]),
}


def _snap(v: np.ndarray, tol_deg: float = 15.0) -> np.ndarray:
    v = v / max(np.linalg.norm(v), 1e-12)
    best, bd = None, -2.0
    for a in AXES.values():
        d = float(np.dot(v, a))
        if d > bd:
            best, bd = a, d
    return best.copy() if np.degrees(np.arccos(np.clip(bd, -1, 1))) < tol_deg else v


def frame_from_vectors(left: np.ndarray, up: np.ndarray, fwd: np.ndarray) -> np.ndarray:
    """월드 -> 표준 프레임 회전. 표준: X=캐릭터 왼쪽, Y=위, Z=앞."""
    m = np.stack([left, up, fwd], axis=1)  # 열 = 표준 축의 월드 방향
    return Q.from_matrix(m.T)


def frame_from_axes(up: str, fwd: str) -> np.ndarray:
    u, f = AXES[up], AXES[fwd]
    if abs(float(np.dot(u, f))) > 1e-6:
        raise ValueError("상방축과 전방축은 서로 수직이어야 합니다: %s, %s" % (up, fwd))
    left = np.cross(u, f)
    return frame_from_vectors(left, u, f)


def detect_frame(skel: Skeleton, slots: Dict[str, int]) -> Optional[Tuple[np.ndarray, str]]:
    """(월드->표준 쿼터니언, 설명) 또는 None. 골반/머리/양쪽 허벅지가 필요하다."""
    need_up = "Hips" in slots and any(k in slots for k in ("Head", "Neck", "Spine2"))
    need_lr = ("L_UpLeg" in slots and "R_UpLeg" in slots) or ("L_UpperArm" in slots and "R_UpperArm" in slots)
    if not (need_up and need_lr):
        return None
    _, bwp = skel.bind_world()
    top = slots.get("Head", slots.get("Neck", slots.get("Spine2")))
    up = bwp[top] - bwp[slots["Hips"]]
    if np.linalg.norm(up) < 1e-9:
        return None
    up = _snap(up)
    if "L_UpLeg" in slots and "R_UpLeg" in slots:
        left = bwp[slots["L_UpLeg"]] - bwp[slots["R_UpLeg"]]
    else:
        left = bwp[slots["L_UpperArm"]] - bwp[slots["R_UpperArm"]]
    left = left - up * float(np.dot(left, up))
    if np.linalg.norm(left) < 1e-9:
        return None
    left = _snap(left / np.linalg.norm(left))
    left = left - up * float(np.dot(left, up))
    left /= np.linalg.norm(left)
    fwd = np.cross(left, up)
    names = {tuple(np.round(v).astype(int)): k for k, v in AXES.items()}

    def nm(v):
        key = tuple(np.round(v).astype(int))
        return names.get(key, "(%.2f,%.2f,%.2f)" % tuple(v))

    return frame_from_vectors(left, up, fwd), "상방 %s / 전방 %s" % (nm(up), nm(fwd))


DEFAULT_FRAME = frame_from_axes("+Y", "+Z")
