"""UI 와 CLI 가 함께 쓰는 작업 단위: 소스/타깃 불러오기, 매핑, 리타깃 실행, 프로필 저장, 내보내기."""
from __future__ import annotations

import dataclasses
import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import animops, bridge, biped, frameedit, layout, export as X
from .loader import load_model
from .mapping import DEFAULT_FRAME, SLOT_ORDER, auto_map, detect_frame, pairs_from_slots
from .model import Animation, Model
from .quality import Issue, analyze
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


class Project:
    def __init__(self) -> None:
        self.src: Optional[Model] = None
        self.tgt: Optional[Model] = None
        self.src_slots: Dict[str, int] = {}
        self.tgt_slots: Dict[str, int] = {}
        self.settings = RetargetSettings()
        self.use_chain: Dict[str, bool] = {"L": True, "R": True}
        self.clip = 0
        self.result: Optional[RetargetResult] = None
        self.issues: List[Issue] = []
        self.blender: Optional[str] = None
        self.log: List[str] = []
        self.edit_info: Optional[animops.EditInfo] = None
        self.edits: Dict[int, frameedit.FrameEdit] = {}  # 클립별 프레임 조각 편집(영상 편집기식)

    # ------------------------------------------------------------------ 불러오기
    def load_source(self, path: str) -> Model:
        m = load_model(path, self.blender)
        if not m.animations:
            raise ValueError("이 파일에는 애니메이션이 없습니다: " + path)
        self.src = m
        self.src_slots = auto_map(m.skeleton)
        self.clip = 0
        self.edits = {}
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
        self.clip = 0
        self.edits = {}
        self.result = None
        self.log += base.notes
        return base, skipped

    def load_target(self, path: str) -> Model:
        m = load_model(path, self.blender)
        self.tgt = m
        self.tgt_slots = auto_map(m.skeleton)
        self.settings.rot_offsets = {}
        self.settings.com_joint = -1
        self.result = None
        self.log += m.notes
        return m

    def unload_target(self) -> None:
        """타깃 모델을 뺀다 (소스 모션은 그대로)."""
        self.tgt, self.tgt_slots = None, {}
        self.settings.rot_offsets = {}
        self.settings.com_joint = -1
        self.result, self.issues = None, []

    def unload_source(self) -> None:
        """소스 모션을 뺀다 (타깃 모델은 그대로)."""
        self.src, self.src_slots = None, {}
        self.clip = 0
        self.edits = {}
        self.result, self.issues = None, []

    # ------------------------------------------------------------------ 매핑
    def automap(self) -> None:
        if self.src:
            self.src_slots = auto_map(self.src.skeleton)
        if self.tgt:
            self.tgt_slots = auto_map(self.tgt.skeleton)

    def pairs(self) -> List[Tuple[int, int]]:
        return pairs_from_slots(self.src_slots, self.tgt_slots)

    def chains(self) -> List[IKChain]:
        out = []
        for c in default_chains(self.src_slots, self.tgt_slots):
            side = "L" if c.label.startswith("왼") else "R"
            if self.use_chain.get(side, True):
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

    def run(self, clip: Optional[int] = None, analyze_quality: bool = True) -> RetargetResult:
        if not (self.src and self.tgt):
            raise ValueError("소스 모션과 타깃 모델을 먼저 불러오세요.")
        if clip is not None:
            self.clip = clip
        res = retarget(self.src.skeleton, self.anim, self.tgt.skeleton, self.pairs(), self.settings, self.chains(), self.src_slots, self.tgt_slots)
        self.result = res
        self.issues = analyze(self.tgt.skeleton, res, self.settings) if analyze_quality else []
        return res

    # ------------------------------------------------------------------ 프로필 (이름 기준이라 같은 리그에 재사용 가능)
    def profile_dict(self) -> dict:
        st = dataclasses.asdict(self.settings)
        tn = self.tgt.skeleton.names if self.tgt else []
        st["rot_offsets"] = {tn[j]: list(v) for j, v in self.settings.rot_offsets.items() if j < len(tn)}
        st["com_joint"] = tn[self.settings.com_joint] if (self.tgt and 0 <= self.settings.com_joint < len(tn)) else None
        st["hip_offset"] = list(self.settings.hip_offset)
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
        com = st.pop("com_joint", None)
        fields = {f.name for f in dataclasses.fields(RetargetSettings)}
        base = dataclasses.asdict(RetargetSettings())
        for k, v in st.items():
            if k in fields and k not in ("rot_offsets", "com_joint"):
                base[k] = tuple(v) if k in ("hip_offset", "place_offset") else v
        base["rot_offsets"] = {}
        for nm, v in ro.items():
            if nm in tidx:
                base["rot_offsets"][tidx[nm]] = tuple(v)
            else:
                warns.append("회전 보정의 '%s' 본이 타깃에 없음" % nm)
        base["com_joint"] = tidx.get(com, -1) if com else -1
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
            raise ValueError("리타깃 결과를 내보내려면 타깃 모델이 필요합니다.")
        os.makedirs(opt.out_dir, exist_ok=True)
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
            adir = os.path.join(opt.out_dir, char, layout.ANIM_DIR) if char else opt.out_dir
            mdir = os.path.join(opt.out_dir, char, layout.MODEL_DIR) if char else opt.out_dir
            os.makedirs(adir, exist_ok=True)
            keys = None
            if opt.edit is not None and opt.edit.active():
                anim, keys, info = animops.apply_edit(anim, opt.edit, upm, skel.parents)
                self.edit_info = info
                warns += ["[%s] %s" % (anim_src.name, w) for w in info.notes]
            files += self._export_one(opt, base, skel, anim, F, upm, meshes, model_path, warns, keys, adir, mdir)
        if opt.write_skeleton_json:
            skel = self.tgt.skeleton if opt.what == "retargeted" else self.src.skeleton
            sdir = os.path.join(opt.out_dir, self.export_char(clips[-1] if clips else self.clip, opt), layout.MODEL_DIR) if opt.organize else opt.out_dir
            os.makedirs(sdir, exist_ok=True)
            p = os.path.join(sdir, _safe(skel.name) + "_skeleton.json")
            X.write_json(p, X.skeleton_dict(skel))
            files.append(p)
        return files, warns

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
        if "bip" in fmts:  # 3ds Max Biped: 스크립트(.ms) + 데이터. 프리셋과 무관하게 3ds Max 프레임 고정
            slots = self.tgt_slots if opt.what == "retargeted" else self.src_slots
            try:
                files, w = biped.export_biped(adir, base, skel, anim, slots, F, upm)
                out += files
                warns += w
            except ValueError as e:
                warns.append(str(e))
        for preset_key in opt.presets:
            preset = X.PRESETS[preset_key]
            if "bvh" in fmts:
                p = os.path.join(adir, "%s_%s.bvh" % (base, preset_key))
                warns += X.export_bvh(p, skel, anim, F, preset, upm)
                out.append(p)
            if "fbx" in fmts:
                if opt.what != "retargeted":
                    warns.append("FBX 는 리타깃 결과(타깃 모델 파일 기준)에서만 지원합니다. 소스는 BVH/GLB 를 쓰세요.")
                else:
                    p = os.path.join(adir, "%s_%s.fbx" % (base, preset_key))
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
            p = os.path.join(mdir if meshes else adir, base + ".glb")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            X.export_glb(p, skel, anim, F, upm, meshes, keys)
            out.append(p)
        return out
