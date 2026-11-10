"""모션 리타깃 엔진.

방식 (표준 프레임 = Y-up, +Z 전방, +X 왼쪽 에서 계산)
  1. 매핑된 각 본의 '월드 회전 변화량' D(t) = Rs(t) * Rs_bind^-1 을 구해 타깃 바인드 회전에 적용한다.
     R_target(t) = D(t) * C * R_target_bind
     C 는 타깃/소스 바인드 포즈(예: A포즈 vs T포즈)의 본 방향 차이를 맞추는 월드 회전이다.
  2. 골반(COM)은 위치 변화량에 스케일 k 를 곱해서 옮긴다. 사용자 오프셋/루트 회전/인플레이스 지원.
  3. 발 IK: 소스에서 발이 땅에 붙은 구간(높이+속도 기준)을 찾아 그 구간 동안 타깃 발을 같은 월드 위치에 고정하고,
     2본 IK 로 다리를 맞춘다. 다리가 닿지 않으면 골반을 낮춘다. 지면 아래로 파고드는 발은 올린다.
  4. 매핑되지 않은 본은 바인드 로컬 회전을 유지한다(부모를 따라감).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import quat as Q
from .mapping import DEFAULT_FRAME, SPINE_SLOTS, detect_creature_frame, detect_frame, estimate_up, frame_from_axes, refine_frame_with_anim, spine_arc_mix, spine_blend, spine_path
from .model import Animation, Skeleton, fk


@dataclass
class IKChain:
    """발 IK 체인: 소스 발 관절(궤적 기준) + 타깃 2본 체인(상단/하단/발)."""

    src_foot: int
    upper: int
    lower: int
    foot: int
    label: str = ""
    kind: str = "foot"   # "foot": 발 IK(접지) / "hand": 손 IK (소스 손이 바닥 높이 0 이하로 닿았을 때만)


@dataclass
class RetargetSettings:
    # 스케일 / 위치
    auto_scale: bool = True
    scale_mult: float = 1.0  # 자동 스케일에 곱하는 배율
    manual_scale: float = 1.0  # auto_scale=False 일 때 사용하는 절대 스케일
    hip_offset: Tuple[float, float, float] = (0.0, 0.0, 0.0)  # (좌우, 높이, 앞뒤) 타깃 단위
    root_yaw_deg: float = 0.0  # 모션 전체를 시작 위치 기준으로 돌린다
    place_offset: Tuple[float, float] = (0.0, 0.0)  # 모션 전체의 바닥면 이동 (좌우 X, 앞뒤 Z; 타깃 단위). 몸 전체(골반+발 IK)가 같이 움직인다
    transfer_root_motion: bool = True  # False 면 제자리(수평 이동 제거)
    # 게임 모션은 월드 좌표(원점에서 수 m 떨어진 곳)에서 시작하는 일이 많다. True 면 첫 프레임의 수평 위치를 타깃 바인드 위치에 맞추고
    # 그 뒤의 이동량만 옮긴다(골반이 모델에서 멀리 떨어져 어긋나 보이는 것을 막는다). 높이는 바인드 기준 그대로.
    start_at_origin: bool = True
    com_joint: int = -1  # 타깃 COM 관절. -1 이면 자동
    # 발 IK
    foot_ik: bool = True
    ik_follow: float = 1.0  # 1: IK 목표가 항상 '스케일된 소스 발 위치'를 따라감 / 0: 접지 구간에서만 IK
    ground_clamp: bool = True
    ground_offset: float = 0.0
    contact_height: float = 0.06  # 다리 길이 대비 비율
    contact_speed: float = 0.6  # 다리 길이/초
    blend_frames: int = 4
    # 손 IK: 소스의 손 높이가 바닥(0) 근처이거나 그 아래로 내려갔을 때만 그쪽 팔에 IK 를 걸어 손을 바닥에 고정하고, 0 에서 벗어나면 풀어 준다
    hand_ik: bool = True
    spine_mix: bool = True              # 척추 본 개수가 소스와 다르면 곡선 구간 비율로 중간값을 섞는다 (끄면 이름이 맞는 본만 1:1)
    hand_contact_height: float = 0.05   # 팔 길이 대비: 손 높이가 이 값 이하(바닥에 닿거나 파고듦)이면 접촉
    hand_release_height: float = 0.12   # 팔 길이 대비: 한번 닿은 뒤 이 값을 넘어 떨어져야 IK 가 풀림 (경계에서 깜빡이지 않게)
    hip_drop: bool = True
    max_hip_drop: float = 0.15  # 다리 길이 대비 비율
    # 좌표 프레임 ('auto' 또는 '+X'..'-Z')
    src_up: str = "auto"
    src_fwd: str = "auto"
    tgt_up: str = "auto"
    tgt_fwd: str = "auto"
    # 본별 회전 보정: 타깃 관절 -> (x,y,z) 도. 표준 프레임 축(X=좌우, Y=위, Z=앞) 기준, 하위 본 전체에 적용.
    rot_offsets: Dict[int, Tuple[float, float, float]] = field(default_factory=dict)
    # 본별 위치 보정: 타깃 관절 -> (x,y,z) 타깃 단위. 바인드 포즈 기준 표준 프레임 축(X=좌우, Y=위, Z=앞)으로 그 본(과 하위 본 전체)을 옮기고, 모션 중에는 부모를 따라간다.
    pos_offsets: Dict[int, Tuple[float, float, float]] = field(default_factory=dict)
    # 본별 크기 보정: 타깃 관절 -> 배율(1=그대로). 그 본을 기준점으로 하위 본 위치와 그 본들에 붙은 메시를 키운다/줄인다.
    scale_offsets: Dict[int, float] = field(default_factory=dict)
    # 소스와 타깃이 같은 스켈레톤(본 이름·계층·모양이 같음)이고 사람 모양이 아니면 사람 본 매핑·좌표축 추정을 거치지 않고 본 이름대로 그대로 적용한다 (core/rigmatch)
    same_rig: bool = True
    # 모션빌더식 후처리 (core/motionfx): 부위별 모션 세기(부위 -> 0..1.5), 튀는 프레임 제거 기준(도, 0=끔), 부드럽게(프레임, 0=끔), 좌우 반전
    fx_parts: Dict[str, float] = field(default_factory=dict)
    fx_spike_deg: float = 0.0
    fx_smooth: float = 0.0
    fx_mirror: bool = False


@dataclass
class ChainPlan:
    label: str
    contact: np.ndarray  # (T,) bool
    weight: np.ndarray  # (T,) 0..1
    target: np.ndarray  # (T,3) 표준 프레임, IK 목표 발목 위치
    fail: np.ndarray  # (T,) bool 다리가 닿지 않은 프레임
    leg_length: float = 0.0
    applied: bool = False  # 이 체인에 IK 가 실제로 적용됐는가 (꺼져 있거나 손이 바닥에 안 닿으면 False — 뷰포트 IK 표시도 이때는 그리지 않는다)


@dataclass
class RetargetResult:
    anim: Animation
    k: float
    frame_src: Tuple[np.ndarray, str]
    frame_tgt: Tuple[np.ndarray, str]
    plans: List[ChainPlan]
    drop: np.ndarray
    warnings: List[str]
    pairs: List[Tuple[int, int]]
    com: int
    corr: Dict[int, np.ndarray]
    src_canon_pos: np.ndarray  # (T,Js,3) 소스 표준 프레임 위치(원본 단위)
    chains: List["IKChain"] = field(default_factory=list)  # 실제로 쓰인 IK 체인
    primary: Dict[int, int] = field(default_factory=dict)  # 타깃 본 -> 방향 기준이 된 매핑된 자식
    ik_applied: bool = False
    ghost_pos: Optional[np.ndarray] = None  # (T,Js,3) 타깃 표준 프레임·타깃 단위의 소스 고스트(타깃에 겹치도록 배치·회전 반영)
    scale_cum: Optional[np.ndarray] = None  # (Jt,) 본별 누적 크기 배율 (크기 보정이 없으면 None). 메시 스키닝이 쓴다
    base_anim: Optional[Animation] = None  # 보정 레이어를 얹기 전의 결과 (레이어가 없으면 None)


# --------------------------------------------------------------------------- 유틸
def _smoothstep(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def _runs(mask: np.ndarray) -> List[Tuple[int, int]]:
    out, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(mask) - 1))
    return out


def clean_mask(mask: np.ndarray, min_len: int = 3, max_gap: int = 2) -> np.ndarray:
    m = mask.copy()
    # 짧은 빈틈은 메운다
    runs = _runs(~m)
    for a, b in runs:
        if a > 0 and b < len(m) - 1 and (b - a + 1) <= max_gap:
            m[a : b + 1] = True
    # 짧은 접지는 지운다
    for a, b in _runs(m):
        if (b - a + 1) < min_len:
            m[a : b + 1] = False
    return m


def two_bone_ik(A, B, C, T, l1, l2, pole_fallback=(0.0, 0.0, 1.0)):
    """벡터화된 2본 IK. A(상단), B(중간), C(끝), T(목표) 모두 (N,3). 반환: B_new, C_new, 도달실패 마스크."""
    AT = T - A
    d = np.linalg.norm(AT, axis=1)
    dirAT = AT / np.maximum(d, 1e-9)[:, None]
    reach_max = (l1 + l2) * 0.9999
    reach_min = abs(l1 - l2) * 1.0001 + 1e-6
    dc = np.clip(d, reach_min, reach_max)
    fail = d > (l1 + l2) * 1.0005
    cur = B - A
    perp = cur - dirAT * np.sum(cur * dirAT, axis=1, keepdims=True)
    pn = np.linalg.norm(perp, axis=1)
    pf = np.asarray(pole_fallback, dtype=float)[None, :]
    fb = pf - dirAT * np.sum(pf * dirAT, axis=1, keepdims=True)
    fbn = np.linalg.norm(fb, axis=1)
    perp_dir = np.where((pn > 1e-6)[:, None], perp / np.maximum(pn, 1e-9)[:, None], fb / np.maximum(fbn, 1e-9)[:, None])
    a = np.clip((l1 * l1 + dc * dc - l2 * l2) / (2 * l1 * dc), -1.0, 1.0)
    Bn = A + dirAT * (l1 * a)[:, None] + perp_dir * (l1 * np.sqrt(1 - a * a))[:, None]
    dBT = T - Bn
    dBT = dBT / np.maximum(np.linalg.norm(dBT, axis=1), 1e-9)[:, None]
    Cn = Bn + dBT * l2
    return Bn, Cn, fail


def _resolve_frame(skel: Skeleton, up: str, fwd: str, slots: Optional[Dict[str, int]], anim=None):
    if up != "auto" and fwd != "auto":
        return frame_from_axes(up, fwd), "수동 상방 %s / 전방 %s" % (up, fwd)
    if slots:
        det = detect_frame(skel, slots)
        if det is not None:
            return det
        if "Hips" not in slots:         # 사람 본 이름이 없는 몬스터류: 앞쪽을 추정해 정면을 보게 한다
            cr = detect_creature_frame(skel, anim)
            if cr is not None:
                return cr
    elif slots is not None and anim is not None:
        cr = detect_creature_frame(skel, anim)
        if cr is not None:
            return cr
    return DEFAULT_FRAME.copy(), "기본(상방 +Y / 전방 +Z)"


# --------------------------------------------------------------------------- 메인
def retarget(
    src: Skeleton,
    sanim: Animation,
    tgt: Skeleton,
    pairs: Sequence[Tuple[int, int]],
    st: Optional[RetargetSettings] = None,
    chains: Optional[Sequence[IKChain]] = None,
    src_slots: Optional[Dict[str, int]] = None,
    tgt_slots: Optional[Dict[str, int]] = None,
    rig=None,
) -> RetargetResult:
    """rig: core.rigmatch.RigMatch. 주면 '같은 스켈레톤' 모드 — pairs 는 rig.pairs 를 쓰고, 소스→타깃 좌표축·스케일은 rig 에서 가져오며
    본 방향 보정은 하지 않고 발 IK 도 쓰지 않는다 (사람이 아닌 캐릭터에서 사람 기준 추정이 엉뚱하게 돌아가는 것을 막는다)."""
    st = st or RetargetSettings()
    chains = [] if rig is not None else list(chains or [])
    warnings: List[str] = []
    if rig is not None:
        pairs = list(rig.pairs)
        warnings.append("ℹ " + rig.describe())
    T = sanim.n_frames
    Jt = tgt.n
    fps = float(sanim.fps)
    if T < 1:
        raise ValueError("소스 애니메이션에 프레임이 없습니다.")

    pair_of: Dict[int, int] = {}
    for s, t in pairs:
        pair_of[t] = s
    # 척추 본 개수/구성이 다르면(예: 소스 3개, 타깃 2개) 골반~목 사이를 하나의 곡선으로 보고 구간 길이 비율로 섞는다.
    # 매핑에서 빠진 척추 본(타깃에 실제로는 있는데 슬롯이 없는 본)도 같이 움직인다.
    spine_mix: Dict[int, List[Tuple[int, float]]] = {}
    spine_note = ""
    if rig is None and st.spine_mix and src_slots and tgt_slots:
        arc = spine_arc_mix(src, tgt, src_slots, tgt_slots, set(pair_of))
        if arc:
            spine_mix = {tj: list(parts) for tj, parts in arc.items()}
            ns_, nt_ = len(spine_path(src, src_slots)[0]), len(spine_path(tgt, tgt_slots)[0])
            spine_note = "척추 곡선 기준으로 소스 %d개 → 타깃 %d개 본" % (ns_, nt_)
        else:
            for name, parts in spine_blend(src_slots, tgt_slots).items():
                if name in tgt_slots:
                    spine_mix[tgt_slots[name]] = [(src_slots[sn], w) for sn, w in parts]
            if spine_mix:
                spine_note = "소스 %d개 → 타깃 %d개 본" % (sum(1 for x in SPINE_SLOTS if x in src_slots), sum(1 for x in SPINE_SLOTS if x in tgt_slots))
        for tj, parts in spine_mix.items():
            pair_of[tj] = max(parts, key=lambda x: x[1])[0]   # 방향 보정 등은 가장 비중 큰 소스 본 기준
        if spine_mix:
            pairs = [(s_, t_) for s_, t_ in pairs if t_ not in spine_mix] + [(pair_of[t_], t_) for t_ in spine_mix]
            warnings.append("ℹ 소스/타깃의 척추 본 구성이 달라 척추 움직임을 비율로 섞어 적용했습니다 (%s)" % spine_note)
    mapped_t = sorted(pair_of)
    if not mapped_t:
        raise ValueError("매핑된 본이 없습니다.")
    mapped_set = set(mapped_t)

    wr_s, wp_s = fk(src, sanim.rot, sanim.pos)
    bwr_s, bwp_s = src.bind_world()
    bwr_t, bwp_t = tgt.bind_world()
    if rig is not None:
        # 같은 스켈레톤: 타깃은 파일이 가진 그대로 쓰고(보통 Y-up), 소스는 겹쳐 본 회전으로 타깃 좌표축에 맞춘다
        Ft, desc_t = _resolve_frame(tgt, st.tgt_up, st.tgt_fwd, None)
        Fs, desc_s = Q.mul(Ft, rig.q), "같은 스켈레톤(타깃 좌표축에 맞춤)"
    else:
        Fs, desc_s = _resolve_frame(src, st.src_up, st.src_fwd, src_slots, sanim)
        if st.src_up == "auto" and st.src_fwd == "auto" and src_slots:
            Fs, desc_s, note = refine_frame_with_anim(src, sanim, src_slots, Fs, desc_s)
            if note:
                warnings.append(note)
        Ft, desc_t = _resolve_frame(tgt, st.tgt_up, st.tgt_fwd, tgt_slots)
    FtI = Q.conj(Ft)

    rs = Q.mul(Fs, wr_s)
    ps = Q.rotate(Fs, wp_s)
    brs = Q.mul(Fs, bwr_s)
    bps = Q.rotate(Fs, bwp_s)
    brt = Q.mul(Ft, bwr_t)
    bpt = Q.rotate(Ft, bwp_t)

    # ---- 바인드 포즈 차이 보정 C
    def primary_child(t: int) -> Optional[int]:
        """t 에서 가장 가까운 매핑된 자손. 후보가 여럿이면 부모→t 방향과 가장 이어지는 쪽, 방향을 모르면 하위 트리가 큰 쪽."""
        frontier = list(tgt.children[t])
        p = tgt.parents[t]
        v_in = (bpt[t] - bpt[p]) if p >= 0 else None
        if v_in is not None and np.linalg.norm(v_in) < 1e-9:
            v_in = None
        while frontier:
            cands = [c for c in frontier if c in mapped_set]
            if cands:
                if len(cands) == 1:
                    return cands[0]
                if v_in is not None:
                    vi = v_in / np.linalg.norm(v_in)

                    def score(c):
                        dv = bpt[c] - bpt[t]
                        n = np.linalg.norm(dv)
                        return float(np.dot(dv / n, vi)) if n > 1e-9 else -2.0

                    return max(cands, key=score)
                return max(cands, key=lambda c: tgt.subtree_size(c))
            nxt: List[int] = []
            for c in frontier:
                nxt.extend(tgt.children[c])
            frontier = nxt
        return None

    def nearest_mapped_ancestor(t: int) -> Optional[int]:
        p = tgt.parents[t]
        while p >= 0:
            if p in mapped_set:
                return p
            p = tgt.parents[p]
        return None

    corr: Dict[int, np.ndarray] = {}
    primary: Dict[int, int] = {}
    for t in mapped_t:
        if rig is not None:       # 같은 바인드 포즈라 보정이 필요 없다
            corr[t] = Q.identity(())
            continue
        d = primary_child(t)
        if d is not None:
            dt = bpt[d] - bpt[t]
            ds = bps[pair_of[d]] - bps[pair_of[t]]
            if np.linalg.norm(dt) > 1e-9 and np.linalg.norm(ds) > 1e-9:
                corr[t] = Q.between(dt, ds)
                primary[t] = d
                continue
        a = nearest_mapped_ancestor(t)
        corr[t] = corr[a].copy() if a is not None else Q.identity(())

    # ---- COM / 위치 구동 관절
    top_mapped = [t for t in mapped_t if not any(a in mapped_set for a in tgt.ancestors(t))]
    com = -1
    if st.com_joint >= 0 and st.com_joint in mapped_set:
        com = st.com_joint
    elif tgt_slots and tgt_slots.get("Hips", -1) in mapped_set:
        com = tgt_slots["Hips"]
    elif top_mapped:
        com = top_mapped[0]
    posdriven = set(top_mapped)
    if com >= 0:
        posdriven.add(com)

    # ---- 스케일
    if rig is not None:
        k = rig.scale * st.scale_mult if st.auto_scale else st.manual_scale
    elif st.auto_scale:
        # 스케일은 '몸 자체의 위쪽'을 기준으로 잰다. 사용자가 고른 표준 상방(Y)으로 재면, 눕히기/세우기/위아래 반전으로 프레임을 돌릴 때
        # 높이가 바뀌어 스케일이 멋대로 커지거나 작아진다 (눕히기·세우기는 방향만 바꿔야 한다).
        hs = ht = None
        up_s = estimate_up(bwp_s, src_slots) if src_slots else None
        up_t = estimate_up(bwp_t, tgt_slots) if tgt_slots else None
        if up_s is not None and up_t is not None:
            us = Q.rotate(Fs, np.asarray(up_s, float))
            ut = Q.rotate(Ft, np.asarray(up_t, float))
            gs_ = min(float(bps[pair_of[t]] @ us) for t in mapped_t)
            gt_ = min(float(bpt[t] @ ut) for t in mapped_t)
            hs = float(bps[pair_of[com]] @ us) - gs_
            ht = float(bpt[com] @ ut) - gt_
        if hs is not None and hs > 1e-6 and ht > 1e-6:
            k = ht / hs
        else:
            # 몸 위쪽을 못 구하면 방향에 상관없는 크기(매핑된 관절이 무게중심에서 퍼진 정도)의 비율
            cs = np.array([bps[pair_of[t]] for t in mapped_t])
            ct = np.array([bpt[t] for t in mapped_t])
            rad_s = float(np.sqrt(((cs - cs.mean(axis=0)) ** 2).sum(axis=1).mean()))
            rad_t = float(np.sqrt(((ct - ct.mean(axis=0)) ** 2).sum(axis=1).mean()))
            k = rad_t / rad_s if rad_s > 1e-9 else 1.0
            warnings.append("몸의 위쪽 방향(골반/발/머리)을 찾지 못해 전체 크기 비율로 스케일을 구했습니다.")
        k *= st.scale_mult
    else:
        k = st.manual_scale

    # ---- 수평 루트 이동 보정 (소스 표준 프레임, 원본 단위): 골반뿐 아니라 발 IK 목표에도 같은 값을 뺀다
    rootoff = np.zeros((T, 3))
    if com >= 0:
        s0 = pair_of[com]
        d0 = ps[:, s0] - bps[s0]
        if not st.transfer_root_motion:
            rootoff[:, [0, 2]] = d0[:, [0, 2]]  # 제자리: 수평 이동 전부 제거
        elif st.start_at_origin:
            rootoff[:, [0, 2]] = d0[0, [0, 2]]  # 시작 위치만 모델 원점으로

    # ---- 목표 월드 회전
    yaw_q = Q.from_axis_angle(np.array([0.0, 1.0, 0.0]), np.radians(st.root_yaw_deg))
    rot_des: Dict[int, np.ndarray] = {}
    for t in mapped_t:
        s = pair_of[t]
        D = Q.mul(rs[:, s], Q.conj(brs[s]))
        if t in spine_mix and len(spine_mix[t]) > 1:
            D, wsum = None, 0.0
            for sb, wb in spine_mix[t]:
                Db = Q.mul(rs[:, sb], Q.conj(brs[sb]))
                wsum += wb
                D = Db if D is None else Q.slerp(D, Db, wb / wsum)
        rot_des[t] = Q.mul(D, Q.mul(corr[t], brt[t]))
    for tj, eul in st.rot_offsets.items():
        if not any(abs(x) > 1e-9 for x in eul) or tj < 0 or tj >= Jt:
            continue
        qo = Q.from_euler(np.array(eul, dtype=float), "XYZ")
        for u in [tj] + tgt.descendants(tj):
            if u in rot_des:
                rot_des[u] = Q.mul(qo, rot_des[u])
    for t in rot_des:
        rot_des[t] = Q.mul(yaw_q, rot_des[t])
    rot_world = {t: Q.mul(FtI, R) for t, R in rot_des.items()}

    # ---- 목표 월드 위치 (위치 구동 관절)
    hip_off = np.array(st.hip_offset, dtype=float)
    place = np.array([st.place_offset[0], 0.0, st.place_offset[1]], dtype=float)
    pos_world: Dict[int, np.ndarray] = {}
    for t in posdriven:
        s = pair_of[t]
        delta = ps[:, s] - bps[s] - rootoff
        pc = bpt[t] + Q.rotate(yaw_q, k * delta) + place
        if t == com:
            pc = pc + hip_off
        pos_world[t] = Q.rotate(FtI, pc)

    rest_b = np.repeat(tgt.rest_rot[None], T, axis=0)

    def build(drop: np.ndarray):
        Wr = np.empty((T, Jt, 4))
        Wp = np.empty((T, Jt, 3))
        Lr = np.empty((T, Jt, 4))
        Lp = np.empty((T, Jt, 3))
        dvec = Q.rotate(FtI, np.stack([np.zeros(T), -drop, np.zeros(T)], axis=1))
        for j in range(Jt):
            p = tgt.parents[j]
            Rw = rot_world.get(j)
            if Rw is None:
                Rl = rest_b[:, j]
                Rw = Q.mul(Wr[:, p], Rl) if p >= 0 else Rl
            else:
                Rl = Q.mul(Q.conj(Wr[:, p]), Rw) if p >= 0 else Rw
            if j in pos_world:
                Pw = pos_world[j] + (dvec if j == com else 0.0)
                Pl = Pw if p < 0 else Q.rotate(Q.conj(Wr[:, p]), Pw - Wp[:, p])
            else:
                Pl = np.repeat(tgt.offsets[j][None], T, axis=0)
                Pw = Pl if p < 0 else Wp[:, p] + Q.rotate(Wr[:, p], Pl)
            Wr[:, j], Wp[:, j], Lr[:, j], Lp[:, j] = Rw, Pw, Rl, Pl
        return Wr, Wp, Lr, Lp

    # ---- 발 IK 계획
    valid_chains: List[IKChain] = []
    for ch in chains:
        if not (0 <= ch.upper < Jt and 0 <= ch.lower < Jt and 0 <= ch.foot < Jt and 0 <= ch.src_foot < src.n):
            warnings.append("IK 체인 '%s': 관절 인덱스가 범위를 벗어났습니다." % ch.label)
            continue
        if tgt.parents[ch.lower] != ch.upper or tgt.parents[ch.foot] != ch.lower:
            warnings.append("IK 체인 '%s': 상단→하단→발이 부모-자식으로 바로 이어지지 않아 건너뜁니다." % ch.label)
            continue
        if ch.upper not in mapped_set or ch.lower not in mapped_set or ch.foot not in mapped_set:
            warnings.append("IK 체인 '%s': 세 관절이 모두 매핑되어 있어야 합니다." % ch.label)
            continue
        if ch.kind == "hand" and not st.hand_ik:
            continue
        valid_chains.append(ch)

    # 소스 바닥 높이(표준 프레임 Y): 매핑된 본 바인드 위치 중 가장 낮은 곳 — 손 높이 0 의 기준
    ground_src = float(min(bps[pair_of[t], 1] for t in mapped_t))
    ground_tgt = float(min(bpt[t, 1] for t in mapped_t))

    def hand_contact(h: np.ndarray, lo: float, hi: float) -> np.ndarray:
        """높이 h(바닥=0) 로 접촉 판정. lo 이하로 내려가면 접촉 시작, hi 를 넘어서 떨어져야 접촉 끝 (히스테리시스)."""
        m = np.zeros(len(h), bool)
        on = False
        for i, v in enumerate(h):
            if not on and v <= lo:
                on = True
            elif on and v > hi:
                on = False
            m[i] = on
        return clean_mask(m, min_len=2, max_gap=1)

    def chain_lengths(ch: IKChain):
        return float(np.linalg.norm(bpt[ch.lower] - bpt[ch.upper])), float(np.linalg.norm(bpt[ch.foot] - bpt[ch.lower]))

    def plan_chains(Wp: np.ndarray) -> List[ChainPlan]:
        plans: List[ChainPlan] = []
        for ch in valid_chains:
            l1, l2 = chain_lengths(ch)
            Lleg = l1 + l2
            Ls = Lleg / max(k, 1e-9)  # 소스 단위 다리(팔) 길이 추정
            sc = ps[:, ch.src_foot]
            ymin = sc[:, 1].min()
            if T > 1:
                vel = np.gradient(sc, axis=0) * fps
            else:
                vel = np.zeros_like(sc)
            sp = np.linalg.norm(vel, axis=1)
            if ch.kind == "hand":
                # 손: 높이(소스 바닥=0)가 0 근처이거나 0 아래일 때만 접촉. 속도는 보지 않는다 (바닥을 짚고 끄는 동작도 있다)
                contact = hand_contact(sc[:, 1] - ground_src, st.hand_contact_height * Ls, st.hand_release_height * Ls)
            else:
                contact = clean_mask((sc[:, 1] - ymin < st.contact_height * Ls) & (sp < st.contact_speed * Ls))
            fkc = Q.rotate(Ft, Wp[:, ch.foot])
            Dc = bpt[ch.foot] + place + k * Q.rotate(yaw_q, sc - bps[ch.src_foot] - rootoff)  # 스케일된 소스 발 위치 (루트 이동 보정 포함)
            def ramp_weights(mask: np.ndarray):
                """접촉 구간마다 앞뒤를 부드럽게 올리고 내리는 가중치와 (a, b, n) 목록."""
                ww = np.zeros(T)
                runs = []
                for a, b in _runs(mask):
                    n = b - a + 1
                    r = min(int(st.blend_frames), n // 2)
                    ramp = np.ones(n)
                    for i in range(r):
                        ramp[i] = ramp[n - 1 - i] = float(_smoothstep(np.array((i + 1) / (r + 1))))
                    ww[a : b + 1] = ramp
                    runs.append((a, b, ramp))
                return ww, runs

            if ch.kind == "hand":
                # 손은 '바인드 포즈 대비 이동량'이 아니라 '바닥 대비 높이'로 계산한다.
                # (소스 A 포즈 / 타깃 T 포즈처럼 바인드 때 손 높이가 다르면 그 차이가 목표 높이에 더해져 손이 공중으로 끌려갔다)
                #  - 높이: 타깃 바닥 + 소스 손의 바닥 대비 높이 x 스케일  (바닥 아래로는 내려가지 않는다)
                #  - 수평: IK 없이 나온 손 위치(fkc)를 그대로 따르다가, 손이 수평으로 거의 안 움직이는 구간만 한 점에 고정
                w, _runs_w = ramp_weights(contact)
                hs = sc[:, 1] - ground_src
                vh = np.linalg.norm(vel[:, [0, 2]], axis=1)
                still = clean_mask(contact & (vh < st.contact_speed * Ls), min_len=2, max_gap=1)
                goal = fkc.copy()
                _pw, still_runs = ramp_weights(still)
                for a, b, ramp in still_runs:
                    plant = fkc[a : b + 1][:, [0, 2]].mean(axis=0)
                    goal[a : b + 1, 0] = fkc[a : b + 1, 0] * (1 - ramp) + plant[0] * ramp
                    goal[a : b + 1, 2] = fkc[a : b + 1, 2] * (1 - ramp) + plant[1] * ramp
                floor_y = ground_tgt + st.ground_offset
                goal[:, 1] = floor_y + k * np.maximum(hs, 0.0)
                target = fkc * (1 - w)[:, None] + goal * w[:, None]
                target[:, 1] = np.where(w > 1e-9, np.maximum(target[:, 1], floor_y), target[:, 1])
                plans.append(ChainPlan(ch.label, contact, w, target, np.zeros(T, bool), Lleg))
                continue

            goal = Dc.copy()
            w, runs_w = ramp_weights(contact)
            for a, b, ramp in runs_w:
                plant = Dc[a : b + 1].mean(axis=0)
                goal[a : b + 1] = Dc[a : b + 1] * (1 - ramp)[:, None] + plant[None, :] * ramp[:, None]
            follow = float(np.clip(st.ik_follow, 0.0, 1.0))
            amt = np.maximum(follow, w)  # IK 를 얼마나 적용할지
            target = fkc * (1 - amt)[:, None] + goal * amt[:, None]
            if st.ground_clamp:
                target[:, 1] = np.maximum(target[:, 1], bpt[ch.foot, 1] + st.ground_offset)
            plans.append(ChainPlan(ch.label, contact, w, target, np.zeros(T, bool), Lleg))
        return plans

    zero = np.zeros(T)
    Wr, Wp, Lr, Lp = build(zero)
    plans: List[ChainPlan] = plan_chains(Wp) if valid_chains else []
    drop = zero
    hand_on = any(c.kind == "hand" for c in valid_chains)
    any_applied = False
    if valid_chains and (st.foot_ik or hand_on):
        if st.foot_ik and st.hip_drop and com >= 0:
            need = np.zeros(T)
            for ch, pl in zip(valid_chains, plans):
                if ch.kind != "foot":
                    continue
                A = Q.rotate(Ft, Wp[:, ch.upper])
                v = pl.target[:, 1] - A[:, 1]
                h = np.linalg.norm((pl.target - A)[:, [0, 2]], axis=1)
                dist = np.linalg.norm(pl.target - A, axis=1)
                nd = -np.sqrt(np.maximum(pl.leg_length ** 2 - h ** 2, 0.0)) - v
                nd = np.where(dist > pl.leg_length * 0.995, nd, 0.0)
                need = np.maximum(need, np.clip(nd, 0.0, st.max_hip_drop * pl.leg_length))
            if need.max() > 1e-9:
                ker = np.ones(5) / 5.0
                pad = np.pad(need, 2, mode="edge")
                drop = np.maximum(np.convolve(pad, ker, mode="valid"), need * 0.0)
                Wr, Wp, Lr, Lp = build(drop)
                plans = plan_chains(Wp)
        # 2본 IK 적용
        applied = False
        for ch, pl in zip(valid_chains, plans):
            if ch.kind == "foot" and not st.foot_ik:
                continue
            if ch.kind == "hand" and not pl.contact.any():
                continue          # 손이 바닥에 닿은 적이 없으면 손 IK 는 아무것도 바꾸지 않는다
            applied = True
            any_applied = True
            pl.applied = True
            l1, l2 = chain_lengths(ch)
            A = Q.rotate(Ft, Wp[:, ch.upper])
            B = Q.rotate(Ft, Wp[:, ch.lower])
            Cc = Q.rotate(Ft, Wp[:, ch.foot])
            Bn, Cn, fail = two_bone_ik(A, B, Cc, pl.target, l1, l2)
            pl.fail = fail
            q1 = Q.between(B - A, Bn - A)
            Cpre = Bn + Q.rotate(q1, Cc - B)
            q2 = Q.between(Cpre - Bn, Cn - Bn)
            Ru = Q.mul(Ft, Wr[:, ch.upper])
            Rl_ = Q.mul(Ft, Wr[:, ch.lower])
            Ru2 = Q.mul(q1, Ru)
            Rl2 = Q.mul(q2, Q.mul(q1, Rl_))
            Ruw = Q.mul(FtI, Ru2)
            Rlw = Q.mul(FtI, Rl2)
            Rfw = Wr[:, ch.foot]
            pu = tgt.parents[ch.upper]
            nu = Q.mul(Q.conj(Wr[:, pu]), Ruw) if pu >= 0 else Ruw
            nl = Q.mul(Q.conj(Ruw), Rlw)
            nf = Q.mul(Q.conj(Rlw), Rfw)
            if ch.kind == "hand":
                # 손 IK 는 바닥에 닿은(전환 포함) 프레임에서만 — 벗어난 프레임은 원래 값 그대로 (완전히 풀림)
                on = (pl.weight > 1e-9)[:, None]
                nu = np.where(on, nu, Lr[:, ch.upper])
                nl = np.where(on, nl, Lr[:, ch.lower])
                nf = np.where(on, nf, Lr[:, ch.foot])
                Ruw = np.where(on, Ruw, Wr[:, ch.upper])
                Rlw = np.where(on, Rlw, Wr[:, ch.lower])
            Lr[:, ch.upper], Lr[:, ch.lower], Lr[:, ch.foot] = nu, nl, nf
            Wr[:, ch.upper], Wr[:, ch.lower] = Ruw, Rlw
        if any(pl.fail.any() for ch, pl in zip(valid_chains, plans) if ch.kind == "foot"):
            warnings.append("일부 프레임에서 다리가 목표 발 위치에 닿지 않습니다(다리가 완전히 펴짐).")
        if any(pl.fail.any() for ch, pl in zip(valid_chains, plans) if ch.kind == "hand"):
            warnings.append("일부 프레임에서 팔이 바닥에 고정할 손 위치에 닿지 않습니다(팔이 완전히 펴짐).")
        hands_used = [(ch, pl) for ch, pl in zip(valid_chains, plans) if ch.kind == "hand" and pl.contact.any()]
        for ch, pl in hands_used:
            hs = ps[:, ch.src_foot, 1] - ground_src
            arm_s = pl.leg_length / max(k, 1e-9)
            warnings.append("ℹ 손 IK(%s): 바닥에 닿은 %d프레임에 적용 · 소스 손 최저 높이 %.1f%%(팔 길이 대비) — 접촉 판정 %.0f%% 이하"
                            % (ch.label, int(pl.contact.sum()), 100 * float(hs.min()) / max(arm_s, 1e-9), 100 * st.hand_contact_height))
    Lr = Q.make_continuous(Q.normalize(Lr))
    scale_cum = None
    if st.scale_offsets or st.pos_offsets:
        Lp = Lp.copy()
        # 크기 보정: 본 j 의 배율은 j 아래 전체(j 의 자식 위치, 그 아래 메시)에 걸린다 -> 누적 배율 x 로컬 위치
        if st.scale_offsets:
            cum = np.ones(Jt)
            for j in range(Jt):
                f_j = float(st.scale_offsets.get(j, 1.0)) if 0 <= j < Jt else 1.0
                p = int(tgt.parents[j])
                cum[j] = f_j * (cum[p] if p >= 0 else 1.0)
            for j in range(Jt):
                p = int(tgt.parents[j])
                if p >= 0:
                    Lp[:, j] *= cum[p]
            scale_cum = cum
        # 위치 보정: 바인드 포즈 기준 표준 프레임 이동량을 부모 본의 로컬 벡터로 바꿔 더한다 -> 모션 중에도 부모를 따라 움직인다 (루트는 월드 이동, Yaw 반영)
        for j, d in st.pos_offsets.items():
            if not (0 <= j < Jt) or not any(abs(x) > 1e-12 for x in d):
                continue
            dv = np.asarray(d, dtype=float)
            p = int(tgt.parents[j])
            vec = Q.rotate(FtI, Q.rotate(yaw_q, dv)) if p < 0 else Q.rotate(Q.conj(bwr_t[p]), Q.rotate(FtI, dv))
            Lp[:, j] += vec
    anim = Animation(sanim.name, fps, Lr, Lp)

    # ---- 소스 고스트: 소스 COM 의 바인드 위치를 타깃 COM 의 바인드 위치에 올려서 겹쳐 보이게 한다(루트 보정·회전·배치 포함). 높이는 소스 바닥 기준 그대로.
    ghost = ps * k
    if com >= 0 and com in pair_of:
        g = Q.rotate(yaw_q, k * (ps - bps[pair_of[com]] - rootoff[:, None, :]))
        g[..., 0] += bpt[com][0] + place[0] + hip_off[0]
        g[..., 2] += bpt[com][2] + place[2] + hip_off[2]
        g[..., 1] = ps[..., 1] * k + hip_off[1]   # 골반 높이 보정도 같이 반영해서, 소스와 타깃을 겹쳐 볼 때 어긋나 보이지 않게
        ghost = g
    return RetargetResult(
        anim, float(k), (Fs, desc_s), (Ft, desc_t), plans, drop, warnings, list(pairs), com, corr, ps,
        chains=valid_chains, primary=primary, ik_applied=any_applied, ghost_pos=ghost, scale_cum=scale_cum,
    )


def default_chains(src_slots: Dict[str, int], tgt_slots: Dict[str, int]) -> List[IKChain]:
    out = []
    for side in ("L", "R"):
        need = ["%s_UpLeg" % side, "%s_LowLeg" % side, "%s_Foot" % side]
        if all(n in tgt_slots for n in need) and ("%s_Foot" % side) in src_slots:
            out.append(IKChain(src_slots["%s_Foot" % side], tgt_slots[need[0]], tgt_slots[need[1]], tgt_slots[need[2]], "%s 다리" % ("왼쪽" if side == "L" else "오른쪽")))
    for side in ("L", "R"):    # 손 IK 체인: 소스 손이 바닥에 닿을 때만 쓰인다
        need = ["%s_UpperArm" % side, "%s_LowerArm" % side, "%s_Hand" % side]
        if all(n in tgt_slots for n in need) and ("%s_Hand" % side) in src_slots:
            out.append(IKChain(src_slots["%s_Hand" % side], tgt_slots[need[0]], tgt_slots[need[1]], tgt_slots[need[2]], "%s 팔" % ("왼쪽" if side == "L" else "오른쪽"), "hand"))
    return out


def source_ghost(res: RetargetResult, src_positions_canon: Optional[np.ndarray] = None) -> np.ndarray:
    """소스 관절 위치를 타깃 월드 프레임/스케일로 옮긴 (T,Js,3). 뷰어 오버레이용."""
    ps = res.src_canon_pos if src_positions_canon is None else src_positions_canon
    return Q.rotate(Q.conj(res.frame_tgt[0]), ps * res.k)
