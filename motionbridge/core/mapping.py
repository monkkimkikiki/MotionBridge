"""휴머노이드 본 자동 매핑과 좌표 프레임(상방/전방) 추정.

이름 기반 자동 매핑은 Mixamo / Unreal Mannequin / 3ds Max Biped / Rigify / CC 계열 이름을 대상으로 한다.
틀린 곳은 UI 에서 사람이 고치는 것을 전제로 한다.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import quat as Q
from . import fingermap
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


_VOCAB = {
    "hips", "pelvis", "hip", "spine", "chest", "abdomen", "torso", "neck", "head", "shoulder", "clavicle", "collar", "lowerarm", "forearm",
    "elbow", "fore", "arm", "lower", "upperarm", "upper", "hand", "wrist", "toe", "toes", "ball", "foot", "ankle", "calf", "shin", "lowleg",
    "lowerleg", "knee", "low", "leg", "thigh", "upleg", "upperleg", "up", "l", "r", "left", "right",
} | set(_FINGER_WORDS)


def _extra_tokens(name: str) -> int:
    """이름에서 표준 부위 단어/좌우/숫자가 아닌 토큰 수. 0 에 가까울수록 '표준 이름'이다 (예: upperarm_l 0, Dwarf_l_sheepArmR 3)."""
    n = 0
    for t in _tokens(name):
        if t.isdigit() or t in _VOCAB:
            continue
        n += 1
    return n


def classify_name(name: str):
    """이름 -> (side, part, finger_digit). part: Hips/Spine/Neck/Head/Shoulder/UpperArm/LowerArm/Hand/UpLeg/LowLeg/Foot/Toe/PlainLeg/Finger:<이름>."""
    toks = _tokens(name)
    side, ts = _split_side(toks)
    tset = set(ts)
    if tset & _SKIP:
        return None
    digits = [int(t) for t in ts if t.isdigit()]
    # 손가락 (MiddleToe1 / RingToe2 처럼 발가락 이름에 손가락 낱말이 들어 있는 리그가 있다 — 그건 손가락이 아니다)
    for w, fname in _FINGER_WORDS.items():
        if w in tset:
            if tset & {"toe", "toes"}:
                return None
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
    if "hip" in tset and not (tset & {"shoulder", "clavicle", "collar"}):  # SMPL(AI 모캡) 'L_Hip' = 허벅지 시작
        return side, "UpLeg", None
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

    extra = [_extra_tokens(nm) for nm in skel.names]

    def rank(j):
        # 표준 이름(낯선 단어가 적은 것)을 먼저, 그다음 얕은 것. 'Dwarf_l_sheepArmR' 같은 소품 본이 진짜 팔을 가리지 않게 한다.
        return (extra[j], skel.depth(j), j)

    def shallowest(lst):
        return min(lst, key=lambda x: rank(x[0]))[0]

    out: Dict[str, int] = {}
    if (None, "Hips") in cands:
        out["Hips"] = shallowest(cands[(None, "Hips")])
    sp = sorted(cands.get((None, "Spine"), []), key=lambda x: (skel.depth(x[0]), x[0]))  # 척추는 깊이순이 중요(표준 이름 우선은 shallowest 에서)
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
    # SMPL 처럼 쇄골이 Collar, 어깨(위팔)가 Shoulder 로 불리는 리그: Collar -> 쇄골, Shoulder -> 위팔
    for side in ("L", "R"):
        sh = cands.get((side, "Shoulder"), [])
        if len(sh) >= 2 and (side, "UpperArm") not in cands:
            tk = {j: set(_tokens(skel.names[j])) for j, _d in sh}
            clav = [x for x in sh if tk[x[0]] & {"collar", "clavicle"}]
            arm = [x for x in sh if "shoulder" in tk[x[0]] and not (tk[x[0]] & {"collar", "clavicle"})]
            if clav and arm:
                cands[(side, "Shoulder")] = clav
                cands[(side, "UpperArm")] = arm
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
                        if key not in out or rank(j) < rank(out[key]):
                            out[key] = j
            else:
                chain = sorted(lst, key=lambda x: skel.depth(x[0]))[:3]
                for n, (j, _) in enumerate(chain, 1):
                    out["%s_%s%d" % (side, f, n)] = j
    # 이름에서 손가락을 하나도 못 찾은 손: 손 아래 뼈 사슬의 위치로 엄지~새끼를 정한다 (core/fingermap.py)
    try:
        fingermap.fill_fingers(skel, out)
    except Exception:  # noqa: BLE001 — 손가락 추정이 실패해도 나머지 매핑은 그대로
        pass
    return out


SPINE_SLOTS = ("Spine", "Spine1", "Spine2")


def spine_blend(src_map: Dict[str, int], tgt_map: Dict[str, int]) -> Dict[str, List[Tuple[str, float]]]:
    """소스와 타깃의 척추 본 개수/구성이 다를 때, 타깃 척추 슬롯마다 '소스 척추 슬롯 몇 개를 어떤 비율로 섞을지'.

    각 척추 본이 척추 전체 길이에서 차지하는 구간의 중심끼리 맞춘다 (소스 3개 → 타깃 2개면 타깃 아래쪽 = 소스 1·2번째 사이,
    타깃 위쪽 = 소스 2·3번째 사이). 양 끝 바깥은 가장 가까운 본에 고정. 구성이 같으면 빈 dict (기존 이름 대응 그대로)."""
    S = [x for x in SPINE_SLOTS if x in src_map]
    T = [x for x in SPINE_SLOTS if x in tgt_map]
    if not S or not T or S == T:
        return {}
    ns, nt = len(S), len(T)
    out: Dict[str, List[Tuple[str, float]]] = {}
    for j, name in enumerate(T):
        c = (j + 0.5) / nt                      # 이 타깃 본 구간의 중심(0~1)
        f = c * ns - 0.5                         # 소스 본 번호로 환산(중심 기준)
        f = min(max(f, 0.0), ns - 1.0)
        i0 = int(np.floor(f))
        i1 = min(i0 + 1, ns - 1)
        w = f - i0
        if w < 1e-6 or i0 == i1:
            out[name] = [(S[i0], 1.0)]
        elif w > 1 - 1e-6:
            out[name] = [(S[i1], 1.0)]
        else:
            out[name] = [(S[i0], 1.0 - w), (S[i1], w)]
    return out


def spine_path(skel, slots: Dict[str, int]) -> Optional[Tuple[List[int], int]]:
    """골반(Hips)과 목(Neck) 사이에 실제로 있는 본 전부 (아래 -> 위, 매핑 여부와 상관없이)와 목 본 번호.
    골반이 목의 조상이 아니거나 척추 본이 하나도 없으면 None."""
    h, n = slots.get("Hips", -1), slots.get("Neck", -1)
    if h < 0 or n < 0:
        return None
    chain: List[int] = []
    j = skel.parents[n]
    while j >= 0 and j != h:
        chain.append(int(j))
        j = skel.parents[j]
    if j != h or not chain:
        return None
    chain.reverse()
    return chain, int(n)


def _spine_edges(skel, path: List[int], neck: int) -> np.ndarray:
    """척추 본(구간)들의 곡선 길이 기준 경계 (0..1, 길이 n+1). 각 본은 '자기 위치 -> 다음 본(마지막은 목)' 구간을 맡는다."""
    _wr, wp = skel.bind_world()
    pts = [wp[b] for b in path] + [wp[neck]]
    seg = np.array([float(np.linalg.norm(pts[i + 1] - pts[i])) for i in range(len(path))])
    if seg.sum() < 1e-9:
        seg = np.ones(len(path))
    return np.concatenate([[0.0], np.cumsum(seg) / seg.sum()])


def spine_arc_mix(src, tgt, src_slots: Dict[str, int], tgt_slots: Dict[str, int], tgt_mapped=()) -> Dict[int, List[Tuple[int, float]]]:
    """척추를 골반~목 사이의 '하나의 곡선'으로 보고 타깃 척추 본마다 소스 척추 본을 섞는 비율 (타깃 본 번호 -> [(소스 본 번호, 비율)]).

    · 본 개수가 달라도(3->2, 2->3, 5->3 ...) 각 타깃 본이 맡는 곡선 구간과 겹치는 소스 구간의 비율만큼 섞는다.
      구간 길이로 섞으므로 곡선 전체의 끝 위치(목 밑)가 소스와 거의 같게 유지된다.
    · 매핑에서 빠진 척추 본(예: 슬롯이 3개뿐인데 실제로는 5개)도 같이 움직인다 — 안 그러면 일부 본에만 휨이 몰린다.
    · 개수가 같고 전부 매핑되어 있으면 빈 dict (기존 이름 대응 그대로).
    · 골반/목을 못 찾으면 빈 dict."""
    sp, tp = spine_path(src, src_slots), spine_path(tgt, tgt_slots)
    if sp is None or tp is None:
        return {}
    spath, sneck = sp
    tpath, tneck = tp
    mapped = set(tgt_mapped)
    if len(spath) == len(tpath) and all(b in mapped for b in tpath):
        return {}
    out: Dict[int, List[Tuple[int, float]]] = {}
    if len(spath) == len(tpath):      # 개수는 같은데 일부 본이 매핑에서 빠짐 -> 순서대로 1:1
        for sb, tb in zip(spath, tpath):
            out[tb] = [(sb, 1.0)]
        return out
    es, et = _spine_edges(src, spath, sneck), _spine_edges(tgt, tpath, tneck)
    for j, tb in enumerate(tpath):
        a, b = et[j], et[j + 1]
        parts: List[Tuple[int, float]] = []
        for i, sb in enumerate(spath):
            ov = min(b, es[i + 1]) - max(a, es[i])
            if ov > 1e-6 * max(b - a, 1e-9):
                parts.append((sb, ov))
        if not parts:       # 수치 오차로 겹침이 없으면 가장 가까운 소스 본
            c = 0.5 * (a + b)
            i = int(np.clip(np.searchsorted(es, c) - 1, 0, len(spath) - 1))
            parts = [(spath[i], 1.0)]
        tot = sum(w for _s, w in parts)
        out[tb] = [(sb, w / tot) for sb, w in parts]
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


def _snap_axis(v: np.ndarray, tol_deg: float) -> Optional[np.ndarray]:
    """v 가 좌표축에서 tol_deg 이내면 그 축, 아니면 None."""
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        return None
    v = v / n
    best, bd = None, -2.0
    for a in AXES.values():
        d = float(np.dot(v, a))
        if d > bd:
            best, bd = a, d
    return best.copy() if np.degrees(np.arccos(np.clip(bd, -1, 1))) <= tol_deg else None


def estimate_up(bwp: np.ndarray, slots: Dict[str, int]) -> Optional[np.ndarray]:
    """바인드 포즈 월드 위치로 상방 추정. 구부정한 캐릭터(드워프, 괴물)는 골반→머리 벡터가 앞으로 기울어 있어서(실제 19°),
    그것만 믿으면 좌표계가 기울어 '공격하면서 대각선으로 솟는' 모션이 된다. 서 있는 다리는 거의 수직이므로 다리(발→골반)를 먼저 보고,
    좌표축에 가까우면(25° 이내) 그 축으로 맞춘다. 다리를 못 쓰면 몸통(35° 이내), 그래도 안 되면 몸통 벡터 그대로."""
    hips = slots.get("Hips")
    top = slots.get("Head", slots.get("Neck", slots.get("Spine2")))
    if hips is None:
        return None
    if "L_Foot" in slots and "R_Foot" in slots:
        leg = bwp[hips] - (bwp[slots["L_Foot"]] + bwp[slots["R_Foot"]]) / 2.0
        a = _snap_axis(leg, 25.0)
        if a is not None:
            return a
    if top is None:
        return None
    torso = bwp[top] - bwp[hips]
    a = _snap_axis(torso, 35.0)
    if a is not None:
        return a
    a = _snap_axis(torso, 60.0)  # 게임 리그는 거의 항상 축 정렬이다. 사람이 아닌 몸(육각 괴물 등)의 심하게 기운 몸통도 가장 가까운 축으로
    if a is not None:
        return a
    n = float(np.linalg.norm(torso))
    return torso / n if n > 1e-9 else None


def detect_frame(skel: Skeleton, slots: Dict[str, int]) -> Optional[Tuple[np.ndarray, str]]:
    """(월드->표준 쿼터니언, 설명) 또는 None. 골반/머리/양쪽 허벅지가 필요하다."""
    need_up = "Hips" in slots and any(k in slots for k in ("Head", "Neck", "Spine2"))
    need_lr = ("L_UpLeg" in slots and "R_UpLeg" in slots) or ("L_UpperArm" in slots and "R_UpperArm" in slots)
    if not (need_up and need_lr):
        return None
    _, bwp = skel.bind_world()
    up = estimate_up(bwp, slots)
    if up is None:
        return None
    if "L_UpLeg" in slots and "R_UpLeg" in slots:
        left = bwp[slots["L_UpLeg"]] - bwp[slots["R_UpLeg"]]
    else:
        left = bwp[slots["L_UpperArm"]] - bwp[slots["R_UpperArm"]]
    return _frame_from_up_left(up, left, snap_up=False, left_tol=35.0)


DEFAULT_FRAME = frame_from_axes("+Y", "+Z")


def detect_creature_frame(skel: Skeleton, anim=None) -> Optional[Tuple[np.ndarray, str]]:
    """사람 본 이름이 없는 뼈대(몬스터 등)의 앞쪽을 추정한다. 상방은 +Y 로 두고 앞(+Z 가 되도록)만 정한다. 못 정하면 None.

    1) 좌우축: 기준 자세가 좌우 대칭인 가로축(X 또는 Z) -> 앞뒤축은 그 수직.
    2) 앞/뒤: 클립 동안 몸이 뚜렷하게 이동하면 이동 방향, 아니면 가지가 많이 갈라진 쪽(머리·팔 쪽)을 앞으로 본다.
    ※ 추정이므로 틀릴 수 있다 — 좌표축 빠른 수정(90°/180° 돌리기)으로 바로잡을 수 있다."""
    from .model import fk
    if skel.n < 4:
        return None
    _, bwp = skel.bind_world()
    c = bwp.mean(axis=0)
    pts = bwp - c
    size = float(np.linalg.norm(np.ptp(pts, axis=0)))
    if size < 1e-9:
        return None

    def mirror_err(ax: int) -> float:
        m = pts.copy()
        m[:, ax] *= -1.0
        d = np.linalg.norm(m[:, None, :] - pts[None, :, :], axis=2).min(axis=1)
        return float(d.mean()) / size

    ex, ez = mirror_err(0), mirror_err(2)
    if min(ex, ez) > 0.9 * max(ex, ez) or max(ex, ez) < 1e-6:
        return None                      # 어느 쪽으로도 대칭이 뚜렷하지 않다
    fwd_ax = 2 if ex <= ez else 0        # X 로 대칭이면 좌우 = X, 앞뒤 = Z
    sign = 0.0
    why = ""
    if anim is not None and anim.n_frames >= 2:
        _, wp = fk(skel, anim.rot, anim.pos)
        cen = wp.mean(axis=1)
        mv = cen[-1] - cen[0]
        mv[1] = 0.0
        if float(np.linalg.norm(mv)) > 0.25 * size and abs(mv[fwd_ax]) > 0.5 * float(np.linalg.norm(mv)):
            sign = 1.0 if mv[fwd_ax] > 0 else -1.0
            why = "이동 방향"
    if sign == 0.0:
        leaves = [j for j in range(skel.n) if skel.is_leaf(j)]
        pos = float(sum(1 for j in leaves if pts[j, fwd_ax] > 0.05 * size))
        neg = float(sum(1 for j in leaves if pts[j, fwd_ax] < -0.05 * size))
        if pos == neg:
            return None
        sign = 1.0 if pos > neg else -1.0
        why = "갈라진 끝이 많은 쪽"
    fwd = np.zeros(3)
    fwd[fwd_ax] = sign
    up = np.array([0.0, 1.0, 0.0])
    left = np.cross(up, fwd)
    return frame_from_vectors(left, up, fwd), "상방 +Y / 전방 %s%s (추정: %s)" % ("+" if sign > 0 else "-", "XYZ"[fwd_ax], why)



# ---------------------------------------------------------------------------- 축 빠른 수정 / 진단
def _axis_name(v: np.ndarray) -> str:
    return max(AXES, key=lambda k: float(np.dot(AXES[k], v)))


def axes_of_frame(F: np.ndarray) -> Tuple[str, str]:
    """프레임(월드->표준)이 가리키는, 좌표축에 가장 가까운 (상방, 전방) 축 이름. 서로 수직이 되도록 고른다."""
    inv = Q.conj(np.asarray(F, float))
    up_w = Q.rotate(inv, np.array([0.0, 1.0, 0.0]))
    fwd_w = Q.rotate(inv, np.array([0.0, 0.0, 1.0]))
    up = _axis_name(up_w)
    fwd_c = fwd_w - AXES[up] * float(np.dot(fwd_w, AXES[up]))
    fwd = _axis_name(fwd_c if np.linalg.norm(fwd_c) > 1e-9 else fwd_w)
    return up, fwd


def turn_axes(up: str, fwd: str, op: str) -> Tuple[str, str]:
    """(상방, 전방)을 빠르게 고친다. op: 'yaw90' 위축 기준 90° 돌리기(전방이 옆으로) / 'yaw180' 앞뒤 반전(180°) /
    'flip_up' 위아래 반전 / 'pitch90' 좌우축 기준 90°(Z-up 모델을 Y-up 으로 눕히거나 세우기). 결과는 항상 서로 수직인 축이다."""
    u, f = AXES[up], AXES[fwd]
    if op == "yaw90":
        nu, nf = u, np.cross(u, f)
    elif op == "yaw180":
        nu, nf = u, -f
    elif op == "flip_up":
        nu, nf = -u, f
    elif op == "pitch90":
        nu, nf = f, -u
    else:
        raise ValueError("알 수 없는 축 동작: " + op)
    return _axis_name(nu), _axis_name(nf)


def diagnose_frame(skel: Skeleton, slots: Dict[str, int]) -> List[str]:
    """자동 좌표축 인식이 틀리기 쉬운 이유를 찾아 문장으로 돌려준다 (비어 있으면 특별한 문제 없음)."""
    notes: List[str] = []
    need_up = "Hips" in slots and any(k in slots for k in ("Head", "Neck", "Spine2"))
    lr = ("L_UpLeg" in slots and "R_UpLeg" in slots) or ("L_UpperArm" in slots and "R_UpperArm" in slots)
    if not need_up:
        notes.append("골반(Hips)과 머리/목/가슴 매핑이 없어 상방을 알 수 없습니다 → 기본값(상방 +Y / 전방 +Z)을 씁니다. 파일이 Z-up 이면 눕거나 뒤집혀 보입니다.")
    if not lr:
        notes.append("양쪽 허벅지(또는 위팔) 매핑이 없어 좌우를 알 수 없습니다 → 기본값(상방 +Y / 전방 +Z)을 씁니다.")
    if notes:
        return notes
    _, bwp = skel.bind_world()
    hips = slots["Hips"]
    top = slots.get("Head", slots.get("Neck", slots.get("Spine2")))

    def off_axis(v: np.ndarray) -> float:
        v = v / max(float(np.linalg.norm(v)), 1e-12)
        return float(np.degrees(np.arccos(np.clip(max(float(np.dot(v, a)) for a in AXES.values()), -1, 1))))

    if "L_Foot" in slots and "R_Foot" in slots:
        leg = bwp[hips] - (bwp[slots["L_Foot"]] + bwp[slots["R_Foot"]]) / 2.0
        a = off_axis(leg)
        if a > 25.0:
            notes.append("다리(발→골반) 방향이 좌표축에서 %.0f° 벗어나 있어 다리로 상방을 잡지 못했습니다 (바인드 포즈가 기울었거나 다리 매핑이 틀림)." % a)
    torso = bwp[top] - bwp[hips]
    at = off_axis(torso)
    if at > 35.0:
        notes.append("몸통(골반→머리) 방향이 좌표축에서 %.0f° 기울어 있습니다 (구부정한 괴수/비인간형 리그). 상방이 비스듬하게 잡힐 수 있어요." % at)
    if "L_UpLeg" in slots and "R_UpLeg" in slots:
        left = bwp[slots["L_UpLeg"]] - bwp[slots["R_UpLeg"]]
    else:
        left = bwp[slots["L_UpperArm"]] - bwp[slots["R_UpperArm"]]
    if float(np.linalg.norm(left)) < 1e-9:
        notes.append("왼쪽·오른쪽 본이 같은 위치라 좌우를 알 수 없습니다 (L/R 매핑 확인).")
    else:
        ang = off_axis(left)
        if ang > 35.0:
            notes.append("좌우(왼쪽-오른쪽 본) 방향이 좌표축에서 %.0f° 벗어나 있습니다. 좌우 매핑이 서로 바뀌었거나 리그가 비스듬합니다." % ang)
    return notes


def _frame_from_up_left(up: np.ndarray, left: np.ndarray, snap_up: bool = True, left_tol: float = 15.0) -> Optional[Tuple[np.ndarray, str]]:
    if np.linalg.norm(up) < 1e-9:
        return None
    up = _snap(up) if snap_up else up / np.linalg.norm(up)
    left = left - up * float(np.dot(left, up))
    if np.linalg.norm(left) < 1e-9:
        return None
    left = left / np.linalg.norm(left)
    a = _snap_axis(left, left_tol)
    if a is not None:
        left = a - up * float(np.dot(a, up))  # up 이 축이 아닐 때를 대비해 다시 수직화
    left = left - up * float(np.dot(left, up))
    left /= np.linalg.norm(left)
    fwd = np.cross(left, up)
    names = {tuple(np.round(v).astype(int)): k for k, v in AXES.items()}

    def nm(v):
        key = tuple(np.round(v).astype(int))
        ok = np.allclose(v, np.array(key, float), atol=1e-3)
        return names[key] if ok and key in names else "(%.2f,%.2f,%.2f)" % tuple(v)

    return frame_from_vectors(left, up, fwd), "상방 %s / 전방 %s" % (nm(up), nm(fwd))


def refine_frame_with_anim(skel: Skeleton, anim, slots: Dict[str, int], F: np.ndarray, desc: str,
                           tol_deg: float = 60.0, min_fraction: float = 0.8) -> Tuple[np.ndarray, str, Optional[str]]:
    """바인드 포즈로 잡은 좌표계가 클립의 실제 자세와 어긋나면(몸통이 거의 내내 옆/뒤집혀 있음) 클립의 평균 자세로 다시 잡는다.

    돌려주는 값: (프레임, 설명, 알림 문장 또는 None). 어긋남이 없으면 입력을 그대로 돌려준다.
    """
    from .model import fk

    top = slots.get("Head", slots.get("Neck", slots.get("Spine2")))
    hips = slots.get("Hips")
    if top is None or hips is None or anim.n_frames < 2:
        return F, desc, None
    pair = (("L_UpLeg", "R_UpLeg"), ("L_UpperArm", "R_UpperArm"))
    lr = next(((slots[a], slots[b]) for a, b in pair if a in slots and b in slots), None)
    if lr is None:
        return F, desc, None
    wr, wp = fk(skel, anim.rot, anim.pos)
    up_w = wp[:, top] - wp[:, hips]
    ln = np.linalg.norm(up_w, axis=1)
    ok = ln > 1e-9
    if ok.sum() < 2:
        return F, desc, None
    up_w = up_w[ok] / ln[ok][:, None]
    std_up = Q.rotate(np.broadcast_to(F, (len(up_w), 4)), up_w)  # 표준 프레임에서 본 몸통 방향 (표준 상방 = +Y)
    ang = np.degrees(np.arccos(np.clip(std_up[:, 1], -1, 1)))
    if float(np.mean(ang > tol_deg)) < min_fraction:
        return F, desc, None
    # 쓰러지는/눕는 모션(죽음·다운 등)은 대부분의 프레임이 누워 있어도 정상이다. 시작 자세가 바인드 좌표계에서 서 있으면
    # 좌표계가 틀린 게 아니라 모션이 누우는 것이므로 바인드 기준을 유지한다. (시작부터 눕거나 틀어져 있을 때만 다시 잡는다)
    head = max(1, min(len(ang), int(round(len(ang) * 0.1)), 8))
    if float(np.mean(ang[:head] < tol_deg)) >= 0.5:
        return F, desc, None
    left_w = wp[:, lr[0]] - wp[:, lr[1]]
    ll = np.linalg.norm(left_w, axis=1)
    left_w = (left_w[ok] / np.maximum(ll[ok], 1e-9)[:, None])
    det = _frame_from_up_left(up_w.mean(0), left_w.mean(0))
    if det is None:
        return F, desc, None
    return det[0], det[1] + " (클립 자세 기준)", "바인드 포즈로 잡은 좌표계(%s)가 클립의 실제 자세와 맞지 않아 클립 기준으로 다시 잡았습니다: %s" % (desc, det[1])
