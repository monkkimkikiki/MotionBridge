"""UI 와 CLI 가 함께 쓰는 작업 단위: 소스/타깃 불러오기, 매핑, 리타겟 실행, 프로필 저장, 내보내기."""
from __future__ import annotations

import copy
import dataclasses
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import animops, bridge, biped, frameedit, layout, maxbones, maxrun, motionfx, safesave, export as X
from .layers import LayerStack, apply_layers
from .loader import load_model
from .mapping import DEFAULT_FRAME, SLOT_ORDER, auto_map, detect_frame, pairs_from_slots
from .model import Animation, Model
from .quality import Issue, analyze
from .rigmatch import RigMatch, explain_rig, is_humanoid
from .retarget import IKChain, RetargetResult, RetargetSettings, default_chains, retarget


def _safe(name: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in name).strip("._") or "clip"


@dataclass
class ExportOptions:
    out_dir: str
    presets: Sequence[str] = ("blender",)  # maya | max | blender
    formats: Sequence[str] = ("bvh",)  # bvh | glb | fbx | bip (3ds Max Biped 스크립트)
    include_skeleton: bool = True  # False: 본 구조 없이 애니메이션 커브(JSON)만
    what: str = "retargeted"  # retargeted | source
    clips: Optional[Sequence[int]] = None  # None: 현재 클립만
    include_mesh: bool = True  # GLB 에 타깃 메시를 함께 넣기
    write_skeleton_json: bool = False  # 본 구조 JSON 을 따로 저장
    blender: Optional[str] = None
    fbx_options: Dict = field(default_factory=dict)
    edit: Optional[animops.EditOptions] = None  # 자르기/늘리기/fps/루프/키 압축
    organize: bool = False  # True: out_dir / 캐릭터 / Animations(모션 파일) · Models(메시·본 구조) 로 나눠 담는다
    char_name: str = ""     # organize 일 때 캐릭터 폴더 이름 (비우면 소스 경로/클립 이름에서 자동)
    biped_template: str = ""      # Biped(.ms) 내보내기: 스키닝된 템플릿 .max (있으면 Biped 를 새로 만들지 않고 이 파일 안의 Biped 에 키를 굽는다)
    max_method: str = "biped"     # 스키닝된 .max 에 모션을 넣는 방식: biped (Biped 에 키) | bones (이름이 같은 일반 본/Maya 식 관절에 키)
    max_auto: bool = False        # True: 내보낸 뒤 3ds Max 를 직접 실행해 .max 에 모션을 적용·저장 (False: .ms 만 만들고 사용자가 실행)
    max_exe: str = ""             # 3ds Max 실행 파일(3dsmaxbatch.exe / 3dsmax.exe) 또는 설치 폴더. 비우면 자동으로 찾는다
    max_timeout: float = 900.0    # 자동 실행 제한 시간(초)
    on_exist: str = "overwrite"   # 같은 이름의 파일이 이미 있을 때: overwrite | keep_old | new_name (core.safesave)
    name: str = ""                # 다른 이름으로 저장: 파일 이름(확장자 제외). 클립이 여러 개면 '이름_클립이름'


def _free_base(base: str, dirs: Sequence[str]) -> str:
    """dirs 안의 기존 파일(이름이 base 이거나 base_ 로 시작)과 겹치지 않는 base, base_2, base_3 …"""
    names = set()
    for d in dict.fromkeys(dirs):
        if os.path.isdir(d):
            names |= {os.path.splitext(n)[0].lower() for n in os.listdir(d)}

    def clash(b: str) -> bool:
        b = b.lower()
        return any(n == b or n.startswith(b + "_") for n in names)

    cand, n = base, 1
    while clash(cand):
        n += 1
        cand = "%s_%d" % (base, n)
    return cand


class Project:
    def __init__(self) -> None:
        self.src: Optional[Model] = None
        self.backups: List[str] = []   # 마지막 내보내기에서 보관해 둔 이전 파일들
        self.tgt: Optional[Model] = None
        self.src_slots: Dict[str, int] = {}
        self.tgt_slots: Dict[str, int] = {}
        self.settings = RetargetSettings()
        self.use_chain: Dict[str, bool] = {"L": True, "R": True}
        self._clip = 0
        self._stash: Dict[int, RetargetSettings] = {}      # 지금 보는 클립이 아닌 클립들의 리타겟 설정 (클립마다 따로 가진다)
        self._import_undo: Dict[int, List[dict]] = {}      # 클립별 '다른 모션 세팅 가져오기' 되돌리기 기록
        self.result: Optional[RetargetResult] = None
        self.issues: List[Issue] = []
        self.blender: Optional[str] = None
        self.log: List[str] = []
        self.edit_info: Optional[animops.EditInfo] = None
        self.edits: Dict[int, frameedit.FrameEdit] = {}  # 클립별 프레임 조각 편집(영상 편집기식)
        self.layers: Dict[int, LayerStack] = {}  # 클립별 보정 레이어(비파괴 키프레임 수정) — 리타겟 결과 위에 얹는다
        self._rig_cache: Tuple[int, int, Optional[RigMatch], str] = (0, 0, None, "")
        self._base_cache = None   # (입력 키, 레이어 적용 전 리타겟 결과)
        self.rig_note = ""   # 같은 스켈레톤으로 못 알아본 이유 (이름은 거의 같은데 모양이 안 맞을 때 화면에 알린다)

    # ------------------------------------------------------------------ 클립별 설정
    # 리타겟 설정(스케일·IK·발·다듬기·본별 보정 등)은 클립마다 따로 가진다. 처음 여는 클립은 그때 보고 있던 설정을 복사해 시작하고(예전처럼 같은 값),
    # 그 뒤로는 서로 영향을 주지 않는다. 다른 클립 설정은 import_clip_state 로 필요한 부분만 가져온다.
    @property
    def clip(self) -> int:
        return self._clip

    @clip.setter
    def clip(self, v: int) -> None:
        v = int(v)
        if v == self._clip:
            return
        old = self.settings
        self._stash[self._clip] = old
        nxt = self._stash.pop(v, None)
        if nxt is None:
            nxt = copy.deepcopy(old)
        nxt.ik_ranges = old.ik_ranges             # IK 구간은 이미 '클립 번호 → 구간' 모양이라 한 곳에 모아 둔다
        self.settings = nxt
        self._clip = v

    def settings_of(self, ci: int) -> Optional[RetargetSettings]:
        """클립 ci 의 리타겟 설정 (아직 한 번도 열지 않은 클립이면 None)."""
        if ci == self._clip:
            return self.settings
        return self._stash.get(ci)

    def reset_clip_settings(self) -> None:
        self._stash = {}
        self._import_undo = {}
        self._clip = 0

    def _each_settings(self):
        yield self.settings
        for st in self._stash.values():
            yield st

    def clip_touched(self, ci: int) -> bool:
        """그 클립에 사용자가 손댄 것(본별 보정·레이어·IK 구간·프레임 편집)이 있는가 — 목록 표시용."""
        st = self.settings_of(ci)
        if st is not None and (st.rot_offsets or st.pos_offsets or st.scale_offsets):
            return True
        ls = self.layers.get(ci)
        if ls is not None and len(ls) and ls.total_keys():
            return True
        if self.settings.ik_ranges.get(ci):
            return True
        e = self.edits.get(ci)
        return bool(e is not None and not e.is_untouched())

    IMPORT_PARTS = (("settings", "리타겟 기본 설정 (스케일·IK·발·다듬기 등)"), ("offsets", "본별 보정 (회전·위치·크기)"),
                    ("layers", "보정 레이어"), ("ik", "IK 구간 직접 지정"))

    def _state_snapshot(self) -> dict:
        ci = self._clip
        ls = self.layers.get(ci)
        return {"settings": copy.deepcopy(self.settings), "layers": ls.snapshot() if ls is not None else None,
                "ik": copy.deepcopy(self.settings.ik_ranges.get(ci))}

    def import_clip_state(self, from_clip: int, parts: Sequence[str]) -> str:
        """다른 클립(from_clip)의 세팅 중 parts("settings"/"offsets"/"layers"/"ik")를 지금 클립으로 가져온다. 되돌리기용 기록을 남기고 요약을 돌려준다."""
        if self.src is None or not (0 <= from_clip < len(self.src.animations)) or from_clip == self._clip:
            raise ValueError("가져올 다른 모션을 고르세요.")
        parts = [p for p in parts if p in dict(self.IMPORT_PARTS)]
        if not parts:
            raise ValueError("가져올 항목을 하나 이상 고르세요.")
        src_st = self.settings_of(from_clip)
        got: List[str] = []
        snap = self._state_snapshot()
        cur = self.settings
        if "settings" in parts and src_st is not None:
            keep = {"rot_offsets", "pos_offsets", "scale_offsets", "com_joint", "ik_ranges"}
            for f in dataclasses.fields(RetargetSettings):
                if f.name not in keep:
                    setattr(cur, f.name, copy.deepcopy(getattr(src_st, f.name)))
            got.append("기본 설정")
        if "offsets" in parts and src_st is not None:
            cur.rot_offsets = copy.deepcopy(src_st.rot_offsets)
            cur.pos_offsets = copy.deepcopy(src_st.pos_offsets)
            cur.scale_offsets = copy.deepcopy(src_st.scale_offsets)
            cur.com_joint = src_st.com_joint
            got.append("본별 보정")
        if "layers" in parts:
            ls = self.layers.get(from_clip)
            if ls is not None and len(ls):
                self.layers[self._clip] = LayerStack.from_snapshot(ls.snapshot())
                got.append("보정 레이어 %d개" % len(ls))
            else:
                self.layers.pop(self._clip, None)
                got.append("보정 레이어(없음 → 비움)")
        if "ik" in parts:
            r = self.settings.ik_ranges.get(from_clip)
            if r:
                self.settings.ik_ranges[self._clip] = copy.deepcopy(r)
                got.append("IK 구간")
            else:
                self.settings.ik_ranges.pop(self._clip, None)
                got.append("IK 구간(없음 → 비움)")
        name = self.src.animations[from_clip].name
        snap["note"] = "'%s' 에서 가져옴: %s" % (name, ", ".join(got))
        self._import_undo.setdefault(self._clip, []).append(snap)
        self.result = None
        return snap["note"]

    def import_history(self, ci: Optional[int] = None) -> List[str]:
        return [h["note"] for h in self._import_undo.get(self._clip if ci is None else ci, [])]

    def undo_import(self) -> Optional[str]:
        """마지막으로 가져온 것을 빼고 가져오기 전 상태로 되돌린다. 되돌릴 것이 없으면 None."""
        hist = self._import_undo.get(self._clip) or []
        if not hist:
            return None
        h = hist.pop()
        ik = self.settings.ik_ranges
        h["settings"].ik_ranges = ik
        self.settings = h["settings"]
        if h["layers"] is not None:
            self.layers[self._clip] = LayerStack.from_snapshot(h["layers"])
        else:
            self.layers.pop(self._clip, None)
        if h["ik"]:
            ik[self._clip] = h["ik"]
        else:
            ik.pop(self._clip, None)
        self.result = None
        return h["note"]

    # ------------------------------------------------------------------ 불러오기
    def load_source(self, path: str) -> Model:
        m = load_model(path, self.blender)
        if not m.animations:
            raise ValueError("이 파일에는 애니메이션이 없습니다: " + path)
        self.src = m
        self.src_slots = auto_map(m.skeleton)
        self.reset_clip_settings()
        self.edits = {}
        self.layers = {}
        self.result = None
        self.log += m.notes
        return m

    def load_source_many(self, paths: List[str]):
        """여러 모션 파일(PSA 등)을 하나의 소스로 연다. 스켈레톤이 같은 파일의 클립은 한 목록에 합치고, 다른 것은 건너뛴다.

        돌려주는 값: (모델, 건너뛴 이유 목록). 클립 이름은 파일 이름으로 맞춘다(한 파일에 클립이 하나일 때).
        """
        base: Optional[Model] = None
        skipped: List[str] = []
        for p in paths:
            nm = os.path.basename(p)
            try:
                m = load_model(p, self.blender)
            except Exception as e:  # noqa: BLE001 - 한 파일이 깨져도 나머지는 연다
                skipped.append("%s: %s" % (nm, e))
                continue
            if not m.animations:
                skipped.append("%s: 애니메이션이 없습니다" % nm)
                continue
            if len(m.animations) == 1:
                m.animations[0].name = os.path.splitext(nm)[0]
            if base is None:
                base = m
            elif list(m.skeleton.names) == list(base.skeleton.names) and np.array_equal(m.skeleton.parents, base.skeleton.parents):
                base.animations += m.animations
                base.notes += m.notes
            else:
                skipped.append("%s: 스켈레톤이 달라 함께 열 수 없습니다 (처음 파일과 다른 캐릭터)" % nm)
        if base is None:
            raise ValueError("열 수 있는 모션이 없습니다.\n" + "\n".join(skipped))
        self.src = base
        self.src_slots = auto_map(base.skeleton)
        self.reset_clip_settings()
        self.edits = {}
        self.layers = {}
        self.result = None
        self.log += base.notes
        return base, skipped

    def load_target(self, path: str) -> Model:
        m = load_model(path, self.blender)
        self.tgt = m
        self.tgt_slots = auto_map(m.skeleton)
        for _st in self._each_settings():
            _st.rot_offsets = {}
            _st.pos_offsets = {}
            _st.scale_offsets = {}
            _st.com_joint = -1
        self._import_undo = {}
        self.result = None
        self.log += m.notes
        return m

    def unload_target(self) -> None:
        """타깃 모델을 뺀다 (소스 모션은 그대로)."""
        self.tgt, self.tgt_slots = None, {}
        for _st in self._each_settings():
            _st.rot_offsets = {}
            _st.pos_offsets = {}
            _st.scale_offsets = {}
            _st.com_joint = -1
        self._import_undo = {}
        self.result, self.issues = None, []

    def unload_source(self) -> None:
        """소스 모션을 뺀다 (타깃 모델은 그대로)."""
        self.src, self.src_slots = None, {}
        self.reset_clip_settings()
        self.edits = {}
        self.layers = {}
        self.result, self.issues = None, []

    # ------------------------------------------------------------------ 매핑
    def automap(self) -> None:
        if self.src:
            self.src_slots = auto_map(self.src.skeleton)
        if self.tgt:
            self.tgt_slots = auto_map(self.tgt.skeleton)

    def rig(self) -> Optional[RigMatch]:
        """소스와 타깃이 같은 스켈레톤(본 이름·계층·모양이 같음)이고 사람 모양이 아니면 그 정보 (몬스터 등을 본 이름대로 그대로 적용). 설정에서 끄면 None."""
        if not (self.settings.same_rig and self.src and self.tgt):
            return None
        if is_humanoid(self.src_slots) and is_humanoid(self.tgt_slots):
            return None      # 양쪽 다 사람이면 사람 리타겟(발 IK 포함)을 쓴다. 같은 이름 규칙이어도 프로필/매핑을 바꿔 쓸 수 있게
        a, b, m, note = self._rig_cache
        if a != id(self.src.skeleton) or b != id(self.tgt.skeleton):
            m, note = explain_rig(self.src.skeleton, self.tgt.skeleton)
            self._rig_cache = (id(self.src.skeleton), id(self.tgt.skeleton), m, note)
        self.rig_note = note if (m is None and "본 이름은 같지만" in note) else ""
        return m

    def pairs(self) -> List[Tuple[int, int]]:
        r = self.rig()
        if r is not None:
            return list(r.pairs)
        return pairs_from_slots(self.src_slots, self.tgt_slots)

    def chains(self) -> List[IKChain]:
        if self.rig() is not None:
            return []
        out = []
        for c in default_chains(self.src_slots, self.tgt_slots):
            side = "L" if c.label.startswith("왼") else "R"
            if c.kind == "hand" or self.use_chain.get(side, True):   # 손 IK 는 settings.hand_ik 로만 켜고 끈다
                out.append(c)
        return out

    def unmapped_slots(self) -> List[str]:
        return [s for s in SLOT_ORDER if (s in self.src_slots) != (s in self.tgt_slots)]

    # ------------------------------------------------------------------ 실행
    # ---- 프레임 편집(영상 편집기식 조각 줄): 클립마다 하나. 편집은 바로 적용된다.
    def frame_edit(self, ci: Optional[int] = None) -> frameedit.FrameEdit:
        """클립 ci(기본: 현재 클립)의 편집 모델 (없으면 만든다). 소스가 없으면 오류."""
        ci = self.clip if ci is None else ci
        n = self.src.animations[ci].n_frames
        e = self.edits.get(ci)
        if e is None or e.T != n:
            e = self.edits[ci] = frameedit.FrameEdit(n)
        return e

    def layer_stack(self, ci: Optional[int] = None) -> LayerStack:
        """클립 ci(기본: 현재 클립)의 보정 레이어 묶음 (없으면 만든다)."""
        ci = self.clip if ci is None else ci
        return self.layers.setdefault(ci, LayerStack())

    def save_layers(self, path: str, ci: Optional[int] = None) -> None:
        names = self.tgt.skeleton.names if self.tgt else []
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"format": "motionbridge-layers-1", "layers": self.layer_stack(ci).to_list(names)}, f, ensure_ascii=False, indent=1)

    def load_layers(self, path: str, ci: Optional[int] = None) -> List[str]:
        if not self.tgt:
            raise ValueError("타깃 모델을 먼저 불러오세요.")
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        if d.get("format") != "motionbridge-layers-1":
            raise ValueError("MotionBridge 보정 레이어 파일이 아닙니다.")
        warns: List[str] = []
        self.layers[self.clip if ci is None else ci] = LayerStack.from_list(d.get("layers", []), self.tgt.skeleton.names, warns)
        self.result = None
        return warns

    def edited(self, ci: int) -> Animation:
        """소스 클립 ci 에 프레임 편집을 적용한 모션 (편집하지 않았으면 원본 그대로)."""
        base = self.src.animations[ci]
        e = self.edits.get(ci)
        return frameedit.render(base, e) if e is not None and e.T == base.n_frames else base

    def is_edited(self, ci: Optional[int] = None) -> bool:
        e = self.edits.get(self.clip if ci is None else ci)
        return e is not None and not e.is_untouched()

    @property
    def anim(self) -> Animation:
        return self.edited(self.clip)

    def ready(self) -> bool:
        return bool(self.src and self.tgt and self.pairs())

    def _base_key(self):
        """레이어를 얹기 전 리타겟 결과가 달라지는 모든 입력 (이것이 같으면 결과를 그대로 재사용한다)."""
        e = self.edits.get(self.clip)
        return (self.clip, id(self.src), id(self.tgt), id(self.src.animations[self.clip]), repr(self.settings),
                repr(e.pieces) if e is not None else None, tuple(self.pairs()), repr(self.chains()),
                tuple(sorted(self.src_slots.items())), tuple(sorted(self.tgt_slots.items())))

    def run(self, clip: Optional[int] = None, analyze_quality: bool = True) -> RetargetResult:
        if not (self.src and self.tgt):
            raise ValueError("소스 모션과 타깃 모델을 먼저 불러오세요.")
        if clip is not None:
            self.clip = clip
        # 레이어만 바뀐 경우(키를 끌 때)에는 무거운 리타겟 계산을 다시 하지 않고, 레이어를 얹기 전의 결과를 재사용한다.
        key = self._base_key()
        if self._base_cache is not None and self._base_cache[0] == key:
            res0 = self._base_cache[1]
        else:
            res0 = retarget(self.src.skeleton, self.anim, self.tgt.skeleton, self.pairs(), self.settings, self.chains(), self.src_slots, self.tgt_slots, rig=self.rig(), manual_ik=self.settings.ik_ranges.get(self.clip))
            motionfx.apply(res0, self.tgt.skeleton, self.tgt_slots, self.settings)
            self._base_cache = (key, res0)
        res = dataclasses.replace(res0, warnings=list(res0.warnings))
        st = self.layers.get(self.clip)
        if st is not None and st.has_effect():
            res.base_anim = res.anim
            res.anim = apply_layers(res.anim, st)
            res.warnings.append("ℹ 보정 레이어 %d개의 키 %d개가 적용됨" % (sum(1 for l in st.layers if not l.mute and l.weight > 0 and l.n_keys()), st.total_keys()))
        self.result = res
        self.issues = analyze(self.tgt.skeleton, res, self.settings) if analyze_quality else []
        return res

    # ------------------------------------------------------------------ 프로필 (이름 기준이라 같은 리그에 재사용 가능)
    def profile_dict(self) -> dict:
        st = dataclasses.asdict(self.settings)
        tn = self.tgt.skeleton.names if self.tgt else []
        st["rot_offsets"] = {tn[j]: list(v) for j, v in self.settings.rot_offsets.items() if j < len(tn)}
        st["pos_offsets"] = {tn[j]: list(v) for j, v in self.settings.pos_offsets.items() if j < len(tn)}
        st["scale_offsets"] = {tn[j]: float(v) for j, v in self.settings.scale_offsets.items() if j < len(tn)}
        st["com_joint"] = tn[self.settings.com_joint] if (self.tgt and 0 <= self.settings.com_joint < len(tn)) else None
        st["hip_offset"] = list(self.settings.hip_offset)
        st.pop("ik_ranges", None)     # 프레임 번호는 그 클립에만 맞는 값이라 프로필(여러 클립에 재사용)에는 넣지 않는다
        sn = self.src.skeleton.names if self.src else []
        return {
            "format": "motionbridge-profile-1",
            "source_root": sn[0] if sn else None,
            "target_root": tn[0] if tn else None,
            "src_slots": {k: sn[v] for k, v in self.src_slots.items() if v < len(sn)},
            "tgt_slots": {k: tn[v] for k, v in self.tgt_slots.items() if v < len(tn)},
            "use_chain": dict(self.use_chain),
            "settings": st,
        }

    def save_profile(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.profile_dict(), f, ensure_ascii=False, indent=1)

    def load_profile(self, path: str) -> List[str]:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return self.apply_profile(d)

    def apply_profile(self, d: dict) -> List[str]:
        """이름으로 찾아서 적용한다. 못 찾은 항목은 경고로 돌려준다."""
        if d.get("format") != "motionbridge-profile-1":
            raise ValueError("MotionBridge 프로필이 아닙니다.")
        warns: List[str] = []

        def by_name(model: Optional[Model]):
            return {n: i for i, n in enumerate(model.skeleton.names)} if model else {}

        sidx, tidx = by_name(self.src), by_name(self.tgt)
        if self.src:
            new = {}
            for slot, nm in d.get("src_slots", {}).items():
                if nm in sidx:
                    new[slot] = sidx[nm]
                else:
                    warns.append("소스에 '%s' 본이 없어 %s 매핑을 건너뜀" % (nm, slot))
            self.src_slots = new
        if self.tgt:
            new = {}
            for slot, nm in d.get("tgt_slots", {}).items():
                if nm in tidx:
                    new[slot] = tidx[nm]
                else:
                    warns.append("타깃에 '%s' 본이 없어 %s 매핑을 건너뜀" % (nm, slot))
            self.tgt_slots = new
        self.use_chain.update(d.get("use_chain", {}))
        st = dict(d.get("settings", {}))
        ro = st.pop("rot_offsets", {})
        po = st.pop("pos_offsets", {})
        so = st.pop("scale_offsets", {})
        com = st.pop("com_joint", None)
        fields = {f.name for f in dataclasses.fields(RetargetSettings)}
        base = dataclasses.asdict(RetargetSettings())
        for k, v in st.items():
            if k in fields and k not in ("rot_offsets", "pos_offsets", "scale_offsets", "com_joint", "ik_ranges"):
                base[k] = tuple(v) if k in ("hip_offset", "place_offset") else v
        base["rot_offsets"] = {}
        for nm, v in ro.items():
            if nm in tidx:
                base["rot_offsets"][tidx[nm]] = tuple(v)
            else:
                warns.append("회전 보정의 '%s' 본이 타깃에 없음" % nm)
        base["pos_offsets"], base["scale_offsets"] = {}, {}
        for nm, v in po.items():
            if nm in tidx:
                base["pos_offsets"][tidx[nm]] = tuple(v)
            else:
                warns.append("위치 보정의 '%s' 본이 타깃에 없음" % nm)
        for nm, v in so.items():
            if nm in tidx:
                base["scale_offsets"][tidx[nm]] = float(v)
            else:
                warns.append("크기 보정의 '%s' 본이 타깃에 없음" % nm)
        base["com_joint"] = tidx.get(com, -1) if com else -1
        base["ik_ranges"] = self.settings.ik_ranges      # 직접 지정한 IK 구간은 프로필이 아니라 클립에 속하므로 그대로 둔다
        self.settings = RetargetSettings(**base)
        self.result = None
        return warns

    # ------------------------------------------------------------------ 내보내기
    def export_char(self, ci: int, opt: Optional[ExportOptions] = None) -> str:
        """내보낼 때 쓰는 캐릭터 폴더 이름: 직접 정한 이름 > 추출 폴더 규칙(…/캐릭터/Animations/파일)의 캐릭터 > 클립 이름 > 파일 경로."""
        if opt is not None and (opt.char_name or "").strip():
            return layout.safe_name(opt.char_name)
        parts = [x for x in (self.src.path or "").replace("\\", "/").split("/") if x] if self.src else []
        if len(parts) >= 3 and parts[-2].lower() == layout.ANIM_DIR.lower():
            return layout.safe_name(parts[-3])
        name = self.src.animations[ci].name if self.src and 0 <= ci < len(self.src.animations) else ""
        c = layout.character_of(name) if name else layout.MISC
        if c == layout.MISC and self.src:
            c = layout.character_of(self.src.path or "")
        return c

    def export(self, opt: ExportOptions, progress=None) -> Tuple[List[str], List[str]]:
        """(만든 파일 목록, 경고 목록)."""
        if not self.src:
            raise ValueError("먼저 소스 모션을 불러오세요.")
        if opt.what == "retargeted" and not self.tgt:
            raise ValueError("리타겟 결과를 내보내려면 타깃 모델이 필요합니다.")
        os.makedirs(opt.out_dir, exist_ok=True)
        self.backups = []
        clips = list(opt.clips) if opt.clips is not None else [self.clip]
        files: List[str] = []
        warns: List[str] = []
        unknown = [p for p in opt.presets if p not in X.PRESETS]
        if unknown:
            raise ValueError("알 수 없는 프리셋: %s" % unknown)
        used: Dict[str, int] = {}
        for n, ci in enumerate(clips):
            if progress:
                progress(n, len(clips), self.src.animations[ci].name)
            anim_src = self.edited(ci)
            base = _safe(anim_src.name)
            if (opt.name or "").strip():      # 다른 이름으로 저장
                base = _safe(opt.name.strip()) if len(clips) == 1 else "%s_%s" % (_safe(opt.name.strip()), base)
            used[base] = used.get(base, 0) + 1
            if used[base] > 1:
                base = "%s_%d" % (base, used[base])
            if opt.what == "retargeted":
                res = self.run(ci, analyze_quality=False)
                warns += ["[%s] %s" % (anim_src.name, w) for w in res.warnings]
                skel, anim, F = self.tgt.skeleton, res.anim, res.frame_tgt[0]
                meshes = self.tgt.meshes if opt.include_mesh else None
                model_path = self.tgt.path
            else:
                skel, anim = self.src.skeleton, anim_src
                det = detect_frame(skel, self.src_slots)
                F = det[0] if det else DEFAULT_FRAME
                meshes = self.src.meshes if opt.include_mesh else None
                model_path = self.src.path
            upm = X.guess_units_per_meter(skel, F)
            char = self.export_char(ci, opt) if opt.organize else None
            aroot = os.path.join(opt.out_dir, char, layout.ANIM_DIR) if char else opt.out_dir
            mdir = os.path.join(opt.out_dir, char, layout.MODEL_DIR) if char else opt.out_dir
            keys = None
            if opt.edit is not None and opt.edit.active():
                anim, keys, info = animops.apply_edit(anim, opt.edit, upm, skel.parents)
                self.edit_info = info
                warns += ["[%s] %s" % (anim_src.name, w) for w in info.notes]
            policy = safesave.norm(opt.on_exist)
            if policy == safesave.NEW_NAME:     # 이미 있는 파일/클립 폴더와 겹치지 않는 이름(base_2 …)을 먼저 고른다
                base = _free_base(base, [aroot, mdir])
            adir = layout.clip_dir(aroot, base) if char else opt.out_dir      # 정리 켬: 출력 / 캐릭터 / Animations / <클립 이름> /  (맥스·블렌더·마야 파일을 함께)
            os.makedirs(adir, exist_ok=True)
            if policy == safesave.KEEP_OLD:     # 임시 폴더에 만들고, 이미 있는 파일은 '_old_날짜' 로 보관한 뒤 제자리로 옮긴다
                stage = tempfile.mkdtemp(prefix="mb_exp_", dir=opt.out_dir)
                try:
                    sa, sm = os.path.join(stage, "a"), os.path.join(stage, "m")
                    os.makedirs(sa)
                    os.makedirs(sm)
                    made = self._export_one(opt, base, skel, anim, F, upm, meshes, model_path, warns, keys, sa, sm)
                    for f in made:
                        under_a = os.path.commonpath([os.path.abspath(f), sa]) == sa
                        root = sa if under_a else sm
                        dst = os.path.join(adir if under_a else mdir, os.path.relpath(f, root))
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        dst, bk = safesave.claim(dst, policy)
                        if bk:
                            self.backups.append(bk)
                        os.replace(f, dst)
                        files.append(dst)
                        k = layout.guide_key(dst)
                        if opt.organize and k and under_a:
                            layout.write_guide(os.path.dirname(dst), k)
                finally:
                    shutil.rmtree(stage, ignore_errors=True)
            else:
                files += self._export_one(opt, base, skel, anim, F, upm, meshes, model_path, warns, keys, adir, mdir)
            layout.remove_empty_fbm(adir, mdir)     # Blender FBX 내보내기가 남기는 빈 .fbm 폴더 정리
        if opt.write_skeleton_json:
            skel = self.tgt.skeleton if opt.what == "retargeted" else self.src.skeleton
            sdir = os.path.join(opt.out_dir, self.export_char(clips[-1] if clips else self.clip, opt), layout.MODEL_DIR) if opt.organize else opt.out_dir
            os.makedirs(sdir, exist_ok=True)
            p, bk = safesave.claim(os.path.join(sdir, _safe(skel.name) + "_skeleton.json"), opt.on_exist)
            if bk:
                self.backups.append(bk)
            X.write_json(p, X.skeleton_dict(skel))
            files.append(p)
        return files, warns

    def _pdir(self, opt, adir: str, key: str) -> str:
        """모션 파일을 둘 폴더: 클립 폴더 하나에 프로그램 구분 없이 함께 둔다 (adir). 정리가 켜져 있으면 그 프로그램 설명을 '사용법.txt' 에 덧붙인다."""
        os.makedirs(adir, exist_ok=True)
        if opt.organize:
            layout.write_guide(adir, key)
        return adir

    def _export_one(self, opt, base, skel, anim, F, upm, meshes, model_path, warns, keys=None, adir=None, mdir=None) -> List[str]:
        out: List[str] = []
        adir = adir or opt.out_dir   # 모션 파일(BVH/FBX/Biped/커브)
        mdir = mdir or opt.out_dir   # 메시가 든 GLB
        if not opt.include_skeleton:
            p = os.path.join(adir, base + "_curves.json")
            X.write_json(p, X.curves_dict(skel, anim, keys))
            return [p]
        fmts = [f.lower() for f in opt.formats]
        if keys is not None and "bip" in fmts:
            warns.append("Biped 스크립트는 프레임마다 키를 찍는 방식이라 키 수는 줄지 않고, 정리(압축)된 모션으로 나갑니다.")
        if "bip" in fmts:  # 3ds Max (Biped 또는 일반 본): 스크립트(.ms) + 데이터. 프리셋과 무관하게 3ds Max 프레임 고정
            slots = self.tgt_slots if opt.what == "retargeted" else self.src_slots
            mxdir = self._pdir(opt, adir, "max")
            auto = {"log": os.path.join(mxdir, base + "_retarget_max.log"), "quit": False} if opt.max_auto and opt.biped_template else None
            exe = ""
            if auto is not None:
                exe = maxrun.find_max(opt.max_exe)
                if not exe:
                    auto = None
                    warns.append("3ds Max(3dsmaxbatch.exe / 3dsmax.exe)를 찾지 못해 자동 적용을 건너뜁니다. '3ds Max 경로 지정…' 을 하거나, 만들어진 *_retarget.ms 를 3ds Max 에서 직접 실행하세요.")
                else:
                    auto["quit"] = not maxrun.is_batch(exe)
            try:
                if opt.max_method == "bones":
                    files, w = maxbones.export_bones(mxdir, base, skel, anim, slots, F, upm, opt.biped_template, auto)
                else:
                    files, w = biped.export_biped(mxdir, base, skel, anim, slots, F, upm, opt.biped_template, auto)
                out += files
                warns += w
                if auto is not None:
                    wrapper = maxrun.make_wrapper(files[0], bool(auto["quit"]))
                    try:
                        r = maxrun.run(exe, wrapper, auto["log"], opt.max_timeout)
                    finally:
                        try:
                            os.remove(wrapper)
                        except OSError:
                            pass
                    out += [p for p in r["saved"] if os.path.exists(p)]
                    if r["ok"]:
                        warns.append("3ds Max 자동 적용 완료 (%s): %s" % ("일반 본" if opt.max_method == "bones" else "Biped", ", ".join(os.path.basename(p) for p in r["saved"])))
                        warns += r["warnings"]
                    else:
                        warns.append("3ds Max 자동 적용 실패: %s  (*_retarget.ms 를 3ds Max 에서 직접 실행하면 같은 일을 합니다)" % r["error"])
                    if os.path.exists(auto["log"]):
                        out.append(auto["log"])
            except ValueError as e:
                warns.append(str(e))
        for preset_key in opt.presets:
            preset = X.PRESETS[preset_key]
            pdir = self._pdir(opt, adir, preset_key if preset_key in layout.GUIDES else "etc")
            if "bvh" in fmts:
                p = os.path.join(pdir, "%s_%s.bvh" % (base, preset_key))
                warns += X.export_bvh(p, skel, anim, F, preset, upm)
                out.append(p)
            if "fbx" in fmts:
                if opt.what != "retargeted":
                    warns.append("FBX 는 리타겟 결과(타깃 모델 파일 기준)에서만 지원합니다. 소스는 BVH/GLB 를 쓰세요.")
                else:
                    p = os.path.join(pdir, "%s_%s.fbx" % (base, preset_key))
                    with tempfile.TemporaryDirectory(prefix="mb_") as d:
                        npz = os.path.join(d, "payload.npz")
                        X.write_blender_payload(npz, skel, anim, base)
                        o = {"axis_forward": preset.fbx_forward, "axis_up": preset.fbx_up}
                        if keys is not None and opt.edit is not None:
                            # FBX 내보내기는 프레임마다 굽고 나서 줄이므로, 허용 오차를 Blender 의 단순화 계수로 옮겨 준다.
                            o["simplify"] = float(max(min(np.sin(np.radians(opt.edit.rot_tol_deg) / 2.0), opt.edit.pos_tol_cm / 100.0), 1e-5))
                        o.update(opt.fbx_options)
                        r = bridge.apply_and_export_fbx(model_path, npz, p, o, opt.blender or self.blender)
                    warns += r.warnings
                    out.append(p)
        if "glb" in fmts:
            p = os.path.join(mdir if meshes else self._pdir(opt, adir, "etc"), base + ".glb")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            X.export_glb(p, skel, anim, F, upm, meshes, keys)
            out.append(p)
        return out
