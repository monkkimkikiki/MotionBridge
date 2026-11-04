"""명령줄 인터페이스 (GUI 없이 배치/자동화/테스트용).

  python -m motionbridge info 파일.fbx
  python -m motionbridge retarget --source 모션.fbx --target 내모델.fbx --out 결과 \
        --presets maya max blender --formats bvh glb fbx --clip all
"""
from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from . import __version__
from .core import bridge, extractor, gamescan
from .core.export import PRESETS
from .core.loader import load_model
from .core.mapping import SLOT_ORDER, auto_map, detect_frame, slot_label
from .core.pipeline import ExportOptions, Project
from .core.retarget import RetargetSettings


def _p(*a):
    print(*a, flush=True)


def cmd_info(args) -> int:
    m = load_model(args.file, args.blender)
    sk = m.skeleton
    _p("파일:", m.path)
    _p("본 %d개, 루트: %s" % (sk.n, ", ".join(sk.names[r] for r in sk.roots())))
    slots = auto_map(sk)
    det = detect_frame(sk, slots)
    _p("자동 인식한 좌표계:", det[1] if det else "(인식 실패 — 수동 지정 필요)")
    _p("자동 매핑 %d/%d 슬롯" % (len(slots), len(SLOT_ORDER)))
    for s in SLOT_ORDER:
        if s in slots and not s.startswith(("L_Thumb", "R_Thumb", "L_Index", "R_Index", "L_Middle", "R_Middle", "L_Ring", "R_Ring", "L_Pinky", "R_Pinky")):
            _p("   %-10s <- %s" % (s, sk.names[slots[s]]))
    for i, a in enumerate(m.animations):
        _p("클립 %d: %s  (%d프레임, %.1ffps, %.2f초)" % (i, a.name, a.n_frames, a.fps, a.duration))
    if m.meshes:
        _p("메시 %d개 (정점 %d)" % (len(m.meshes), sum(len(x.vertices) for x in m.meshes)))
    for n in m.notes:
        _p("참고:", n)
    return 0


def cmd_retarget(args) -> int:
    pr = Project()
    pr.blender = args.blender
    pr.load_source(args.source)
    if args.what == "retargeted" or args.target:
        if not args.target:
            _p("오류: 리타깃하려면 --target 이 필요합니다.")
            return 2
        pr.load_target(args.target)
    if args.profile:
        for w in pr.load_profile(args.profile):
            _p("프로필 경고:", w)
    st = pr.settings
    if args.scale is not None:
        st.scale_mult = args.scale
    if args.hip_offset:
        st.hip_offset = tuple(args.hip_offset)
    if args.yaw is not None:
        st.root_yaw_deg = args.yaw
    if args.in_place:
        st.transfer_root_motion = False
    if args.no_ik:
        st.foot_ik = False
    if args.ground_offset is not None:
        st.ground_offset = args.ground_offset
    for k in ("src_up", "src_fwd", "tgt_up", "tgt_fwd"):
        v = getattr(args, k)
        if v:
            setattr(st, k, v)
    if args.what == "retargeted":
        unm = pr.unmapped_slots()
        _p("매핑 %d쌍%s" % (len(pr.pairs()), (" (한쪽만 있는 슬롯: %s)" % ", ".join(unm[:8])) if unm else ""))
        if not pr.pairs():
            _p("오류: 자동 매핑된 본이 없습니다. 프로필(--profile)로 매핑을 지정하세요.")
            return 2
    n = len(pr.src.animations)
    if args.clip == "all":
        clips = list(range(n))
    else:
        clips = [int(args.clip)]
        if not 0 <= clips[0] < n:
            _p("오류: 클립 번호는 0..%d" % (n - 1))
            return 2
    pr.clip = clips[0]
    if args.what == "retargeted":
        res = pr.run(clips[0])
        _p("스케일 k=%.4f, 소스 좌표계 %s, 타깃 좌표계 %s, IK=%s" % (res.k, res.frame_src[1], res.frame_tgt[1], "켬" if res.ik_applied else "끔"))
        for w in res.warnings:
            _p("경고:", w)
        for i in pr.issues:
            if i.severity != "info" or args.verbose:
                _p("  [%s/%s] %d-%d: %s" % (i.kind, i.severity, i.start, i.end, i.message))
    if args.save_profile:
        pr.save_profile(args.save_profile)
        _p("프로필 저장:", args.save_profile)
    opt = ExportOptions(
        out_dir=args.out, presets=args.presets, formats=args.formats, include_skeleton=not args.no_skeleton,
        what=args.what, clips=clips, include_mesh=not args.no_mesh, write_skeleton_json=args.skeleton_json, blender=args.blender,
    )
    files, warns = pr.export(opt, progress=lambda i, n, nm: _p("[%d/%d] %s" % (i + 1, n, nm)))
    for w in warns:
        _p("경고:", w)
    _p("완료: 파일 %d개" % len(files))
    for f in files:
        _p("  ", f)
    return 0


def cmd_extract(args) -> int:
    sc = gamescan.scan_game(args.game)
    if args.action == "scan":
        _p(sc.summary())
        return 0 if sc.found else 1
    if not sc.found:
        _p(sc.summary())
        return 1
    if sc.encrypted and not args.aes:
        _p("이 게임은 암호화돼 있습니다. --aes 0x... 로 AES 키를 주세요.")
        if sc.key_guids:
            _p("키 GUID:", ", ".join(sorted(sc.key_guids)))
        return 2
    if not args.aes and sc.is_unreal5 and not args.usmap:
        _p("참고: 언리얼 5 게임은 보통 --usmap 이 필요합니다. 없으면 애니메이션을 못 읽을 수 있습니다.")
    helper = extractor.find_helper(args.helper)
    if not helper:
        _p("추출 도우미(mbextract)를 찾지 못했습니다. tools/mbextract 를 빌드하거나 --helper 로 경로를 주세요.")
        return 3
    def on_ev(e):
        if e.get("type") == "log":
            _p("  ", e.get("message"))
        elif e.get("type") == "skip":
            _p("  건너뜀:", e.get("path"), "-", e.get("reason"))
    paths = extractor.list_assets(helper, args.game, args.ue, args.aes, args.usmap, args.filter, args.limit, on_ev)
    if args.action == "list":
        for p in paths:
            _p(p)
        _p("%d개" % len(paths))
        return 0
    _p("대상 %d개 내보내는 중…" % len(paths))
    files = extractor.export_assets(helper, args.game, args.ue, args.aes, args.usmap, paths, args.out, on_ev)
    _p("완료: PSA %d개 -> %s" % (len(files), args.out))
    return 0


def cmd_blender(args) -> int:
    p = bridge.find_blender(args.set)
    if args.set:
        if not p:
            _p("그 경로에 Blender 가 없습니다:", args.set)
            return 2
        cfg = bridge.load_config()
        cfg["blender"] = p
        bridge.save_config(cfg)
        _p("저장했습니다:", p)
        return 0
    _p(p or "Blender 를 찾지 못했습니다. (FBX 가져오기/내보내기에만 필요)  설정: python -m motionbridge blender --set <blender 경로>")
    return 0 if p else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="motionbridge", description="MotionBridge %s — 모션 리타깃/내보내기" % __version__)
    sub = ap.add_subparsers(dest="cmd")

    a = sub.add_parser("info", help="파일의 본/클립/자동 매핑 확인")
    a.add_argument("file")
    a.add_argument("--blender")
    a.set_defaults(fn=cmd_info)

    r = sub.add_parser("retarget", help="모션을 내 모델에 리타깃하고 내보내기")
    r.add_argument("--source", required=True, help="모션이 들어 있는 파일 (fbx/glb/bvh/blend)")
    r.add_argument("--target", help="내 모델 파일 (what=retargeted 일 때 필수)")
    r.add_argument("--out", required=True, help="출력 폴더")
    r.add_argument("--what", choices=["retargeted", "source"], default="retargeted", help="source: 리타깃 없이 원본 모션만 변환")
    r.add_argument("--clip", default="0", help="클립 번호 또는 all")
    r.add_argument("--presets", nargs="+", default=["blender"], choices=list(PRESETS))
    r.add_argument("--formats", nargs="+", default=["bvh"], choices=["bvh", "glb", "fbx"])
    r.add_argument("--no-skeleton", action="store_true", help="본 구조 없이 애니메이션 커브(JSON)만")
    r.add_argument("--no-mesh", action="store_true")
    r.add_argument("--skeleton-json", action="store_true", help="본 구조를 JSON 으로도 저장")
    r.add_argument("--profile", help="저장해 둔 매핑/설정 프로필(JSON)")
    r.add_argument("--save-profile")
    r.add_argument("--scale", type=float, help="자동 스케일에 곱할 배율")
    r.add_argument("--hip-offset", type=float, nargs=3, metavar=("X", "Y", "Z"))
    r.add_argument("--yaw", type=float, help="루트 회전(도)")
    r.add_argument("--in-place", action="store_true", help="제자리 모션으로 (수평 이동 제거)")
    r.add_argument("--no-ik", action="store_true", help="발 IK 끄기")
    r.add_argument("--ground-offset", type=float)
    for k in ("src-up", "src-fwd", "tgt-up", "tgt-fwd"):
        r.add_argument("--" + k, dest=k.replace("-", "_"), help="좌표축 수동 지정 (+X..-Z), 기본 자동")
    r.add_argument("--blender")
    r.add_argument("-v", "--verbose", action="store_true")
    r.set_defaults(fn=cmd_retarget)

    x = sub.add_parser("extract", help="게임 폴더 검사 / 애니메이션 목록 / PSA 추출")
    x.add_argument("action", choices=["scan", "list", "export"])
    x.add_argument("--game", required=True, help="게임 설치 폴더")
    x.add_argument("--ue", default="GAME_UE5_4", help="UE 버전 이름 (예: GAME_UE5_4)")
    x.add_argument("--aes", help="AES 키 (암호화된 게임만)")
    x.add_argument("--usmap", help="mappings(.usmap) 파일 (언리얼 5)")
    x.add_argument("--filter", default="(?i)anim", help="에셋 경로 정규식")
    x.add_argument("--limit", type=int, default=5000)
    x.add_argument("--out", default="extracted", help="export 출력 폴더")
    x.add_argument("--helper", help="mbextract 경로")
    x.set_defaults(fn=cmd_extract)

    b = sub.add_parser("blender", help="Blender 경로 확인/설정")
    b.add_argument("--set")
    b.set_defaults(fn=cmd_blender)

    g = sub.add_parser("gui", help="GUI 실행")
    g.set_defaults(fn=None)
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if args.cmd in (None, "gui"):
        from .ui.app import run_gui

        return run_gui()
    try:
        return args.fn(args)
    except (ValueError, bridge.BlenderError, extractor.ExtractError, OSError) as e:
        _p("오류:", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
