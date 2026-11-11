"""파이프라인/CLI 엔드투엔드: GLB 소스 + GLB 타깃 -> 리타겟 -> BVH/GLB/JSON/FBX(가짜 Blender) 내보내기."""
import io
import json
import os
import stat
import sys
import tempfile
from contextlib import redirect_stdout

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge import cli  # noqa: E402
from motionbridge.core import quat as Q  # noqa: E402
from motionbridge.core.io_bvh import read_bvh  # noqa: E402
from motionbridge.core.io_gltf import read_gltf, write_glb  # noqa: E402
from motionbridge.core.model import Mesh, fk  # noqa: E402
from motionbridge.core.pipeline import ExportOptions, Project  # noqa: E402
import synth  # noqa: E402

FAKE_BLENDER = """#!{py}
import sys, json, shutil, os
a = sys.argv
script = a[a.index("--python") + 1]
args = a[a.index("--") + 1:]
open(os.environ["FAKE_LOG"], "a").write(json.dumps([os.path.basename(script)] + args) + "\\n")
if script.endswith("blender_convert.py"):
    shutil.copy(args[0], args[1])
    print("MB_RESULT " + json.dumps({{"out": args[1], "actions": ["walk"]}}))
elif script.endswith("blender_apply.py"):
    import numpy as np
    d = np.load(args[1], allow_pickle=True)
    open(args[2], "wb").write(b"FBX")
    print("MB_WARN: fake warn")
    print("MB_RESULT " + json.dumps({{"n": len(d["names"]), "frames": int(d["wr"].shape[0])}}))
else:
    print("MB_ERROR unknown script"); sys.exit(1)
"""


def make_mesh(sk):
    rng = np.random.default_rng(0)
    bwr, bwp = sk.bind_world()
    V = 90
    j0 = rng.integers(0, sk.n, V)
    verts = bwp[j0] + rng.normal(scale=0.01, size=(V, 3)) * float(np.abs(bwp).max())
    joints = np.stack([j0, j0, j0, j0], 1)
    weights = np.stack([np.ones(V), np.zeros(V), np.zeros(V), np.zeros(V)], 1)
    faces = np.stack([np.arange(0, V, 3), np.arange(1, V, 3), np.arange(2, V, 3)], 1)
    return Mesh("body", verts, faces, joints, weights, rng.uniform(size=(V, 2)))


def make_inputs(d):
    sk, meta = synth.build_skeleton("mixamo", "y_up_z", 1.0, "T", "aligned")
    an, _ = synth.walk_animation(sk, meta, n_frames=40)
    an2, _ = synth.walk_animation(sk, meta, n_frames=24, name="walk_b", speed=1.3)
    src = os.path.join(d, "motion.glb")
    write_glb(src, sk, an)  # 클립 하나만 쓸 수 있으므로 두 번째 클립은 별도 파일
    src2 = os.path.join(d, "motion_b.glb")
    write_glb(src2, sk, an2)
    tk, tmeta = synth.build_skeleton("ue", "z_up_x", 100.0, "A", "bone_x", height=1.15, leg_scale=0.92)
    tgt = os.path.join(d, "mymodel.glb")
    write_glb(tgt, tk, None, [make_mesh(tk)])
    return src, src2, tgt, (sk, an), (tk, tmeta)


def run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(argv)
    return rc, buf.getvalue()


def test_cli_end_to_end():
    with tempfile.TemporaryDirectory() as d:
        src, src2, tgt, (sk, an), (tk, tmeta) = make_inputs(d)
        out = os.path.join(d, "out")
        rc, txt = run_cli(["retarget", "--source", src, "--target", tgt, "--out", out,
                           "--presets", "maya", "max", "blender", "--formats", "bvh", "glb", "--skeleton-json"])
        assert rc == 0, txt
        names = sorted(os.listdir(out))
        bvhs = [n for n in names if n.endswith(".bvh")]
        assert len(bvhs) == 3, names
        assert any(n.endswith(".glb") for n in names) and any(n.endswith("_skeleton.json") for n in names), names
        # 단위/축 프리셋 확인
        by = {}
        for n in bvhs:
            m = read_bvh(os.path.join(out, n))
            wr, wp = fk(m.skeleton, m.animations[0].rot, m.animations[0].pos)
            by[n.rsplit("_", 1)[1][:-4]] = (m, wp)
        hm = by["maya"][1]
        hb = by["blender"][1]
        hx = by["max"][1]
        height_maya = np.ptp(hm[..., 1])
        height_blender = np.ptp(hb[..., 1])
        height_max_z = np.ptp(hx[..., 2])
        assert 90 < height_maya < 250 and 0.9 < height_blender < 2.5, (height_maya, height_blender)
        assert abs(height_maya / height_blender - 100) < 1.0, "maya(cm)/blender(m) 단위 비가 100 이 아님"
        assert height_max_z > 90, "Max 는 Z-up 이어야 함: %g" % height_max_z
        # 걷기 방향: Maya/Blender 는 +Z 로 전진, Max 는 -Y 로 전진
        pel = by["maya"][0].skeleton.find("pelvis")
        trav = lambda wp: wp[-1, pel] - wp[0, pel]
        assert trav(hm)[2] > 100 and abs(trav(hm)[0]) < 1, "Maya 전진 방향(+Z)"
        assert trav(hx)[1] < -100 and abs(trav(hx)[0]) < 1, "Max 전진 방향(-Y)"
        # GLB 를 다시 읽어서 메시가 따라왔는지
        g = read_gltf(os.path.join(out, [n for n in names if n.endswith(".glb")][0]))
        assert len(g.meshes) == 1 and g.animations[0].n_frames == 40
        # 스키닝된 메시가 뼈를 따라가는지 (프레임 20 에서 정점이 루트 이동만큼 이동)
        wr, wp = fk(g.skeleton, g.animations[0].rot, g.animations[0].pos)
        v0 = g.meshes[0].skin(g.skeleton, wr[0], wp[0])
        v20 = g.meshes[0].skin(g.skeleton, wr[20], wp[20])
        assert np.linalg.norm(v20.mean(0) - v0.mean(0)) > 0.3
    print("cli end-to-end OK")


def test_curves_only_and_source():
    with tempfile.TemporaryDirectory() as d:
        src, src2, tgt, _, _ = make_inputs(d)
        out = os.path.join(d, "o")
        rc, txt = run_cli(["retarget", "--source", src, "--target", tgt, "--out", out, "--no-skeleton", "--presets", "maya", "max"])
        assert rc == 0, txt
        fs = os.listdir(out)
        assert fs == ["walk_curves.json"] or [f.endswith("_curves.json") for f in fs] == [True], fs
        j = json.load(open(os.path.join(out, fs[0])))
        assert j["format"] == "motionbridge-curves-1" and len(j["joints"]) > 20
        assert not any(f.endswith((".bvh", ".glb")) for f in fs), "본 구조 없이 모드인데 스켈레톤 파일이 나옴"
        # 소스 그대로 내보내기 (타깃 없이)
        out2 = os.path.join(d, "o2")
        rc, txt = run_cli(["retarget", "--source", src, "--what", "source", "--out", out2, "--presets", "blender", "--formats", "bvh"])
        assert rc == 0, txt
        m = read_bvh(os.path.join(out2, [f for f in os.listdir(out2) if f.endswith(".bvh")][0]))
        core = lambda names: {n for n in names if not n.endswith('_End')}
        assert core(m.skeleton.names) == core(read_gltf(src).skeleton.names)
    print("curves-only/source OK")


def sk_count(path):
    return read_gltf(path).skeleton.n


def test_profile_roundtrip_and_settings():
    with tempfile.TemporaryDirectory() as d:
        src, src2, tgt, _, _ = make_inputs(d)
        pr = Project()
        pr.load_source(src)
        pr.load_target(tgt)
        assert len(pr.pairs()) >= 15 and len([c for c in pr.chains() if c.kind == 'foot']) == 2
        pr.settings.scale_mult = 1.07
        pr.settings.hip_offset = (0.0, 0.03, 0.0)
        pr.settings.rot_offsets = {pr.tgt_slots["Spine1"]: (5.0, 0.0, -3.0)}
        pr.settings.com_joint = pr.tgt_slots["Hips"]
        pr.settings.pos_offsets = {pr.tgt_slots["Head"]: (0.0, 0.02, 0.01)}
        pr.settings.scale_offsets = {pr.tgt_slots["L_Hand"]: 1.5}
        pr.settings.ik_offsets = {pr.tgt_slots["L_Foot"]: (0.01, 0.02, 0.0)}
        pr.use_chain["R"] = False
        p = os.path.join(d, "prof.json")
        pr.save_profile(p)
        r1 = pr.run()
        pr2 = Project()
        pr2.load_source(src)
        pr2.load_target(tgt)
        assert pr2.load_profile(p) == []
        assert pr2.settings.scale_mult == 1.07 and pr2.settings.hip_offset == (0.0, 0.03, 0.0)
        assert pr2.settings.rot_offsets == pr.settings.rot_offsets and pr2.settings.com_joint == pr.settings.com_joint
        assert len([c for c in pr2.chains() if c.kind == 'foot']) == 1
        assert pr2.settings.pos_offsets == pr.settings.pos_offsets and pr2.settings.scale_offsets == pr.settings.scale_offsets
        assert pr2.settings.ik_offsets == pr.settings.ik_offsets, "발 IK 목표 이동도 프로필에 저장"
        r2 = pr2.run()
        assert np.allclose(r1.anim.rot, r2.anim.rot) and np.allclose(r1.anim.pos, r2.anim.pos)
        # 다른 이름의 타깃에는 경고만 내고 적용
        pr3 = Project()
        pr3.load_source(src)
        pr3.tgt = None
        d2 = json.load(open(p))
        d2["tgt_slots"]["Hips"] = "없는본"
        pr3.load_target(tgt)
        w = pr3.apply_profile(d2)
        assert any("없는본" in x for x in w) and "Hips" not in pr3.tgt_slots
        # 실시간 편집 효과: 제자리 옵션이 수평 이동을 없앤다
        pr.use_chain["R"] = True
        pr.settings.transfer_root_motion = False
        r = pr.run()
        wr, wp = fk(pr.tgt.skeleton, r.anim.rot, r.anim.pos)
        hips = pr.tgt_slots["Hips"]
        std = Q.rotate(r.frame_tgt[0], wp[:, hips])
        assert np.ptp(std[:, 2]) < 1e-3 * 100 and np.ptp(std[:, 0]) < 1e-3 * 100, "제자리가 아님"
    print("profile OK")


def test_fbx_via_fake_blender():
    if os.name == "nt":
        print("test_fbx_via_fake_blender: Windows 에서는 건너뜀 (셔뱅 가짜 실행파일 사용)")
        return
    with tempfile.TemporaryDirectory() as d:
        src, src2, tgt, _, _ = make_inputs(d)
        # .fbx 확장자를 가진 척하는 GLB 파일들
        fsrc, ftgt = os.path.join(d, "motion.fbx"), os.path.join(d, "mymodel.fbx")
        open(fsrc, "wb").write(open(src, "rb").read())
        open(ftgt, "wb").write(open(tgt, "rb").read())
        fb = os.path.join(d, "fakeblender")
        with open(fb, "w") as f:
            f.write(FAKE_BLENDER.format(py=sys.executable))
        os.chmod(fb, os.stat(fb).st_mode | stat.S_IEXEC)
        log = os.path.join(d, "log.txt")
        os.environ["FAKE_LOG"] = log
        out = os.path.join(d, "out")
        rc, txt = run_cli(["retarget", "--source", fsrc, "--target", ftgt, "--out", out, "--presets", "max", "blender",
                           "--formats", "fbx", "--blender", fb])
        assert rc == 0, txt
        fs = sorted(os.listdir(out))
        assert fs == ["walk_blender.fbx", "walk_max.fbx"] or len(fs) == 2 and all(f.endswith(".fbx") for f in fs), fs
        lines = [json.loads(x) for x in open(log).read().splitlines()]
        conv = [x for x in lines if x[0] == "blender_convert.py"]
        app = [x for x in lines if x[0] == "blender_apply.py"]
        assert len(conv) == 2 and len(app) == 2, lines  # 소스, 타깃 각각 변환 / 프리셋 2개 적용
        opts = {json.loads(x[4])["axis_up"] for x in app}
        assert opts == {"Z", "Y"}, opts
        assert all(x[1].endswith("mymodel.fbx") for x in app), "FBX 적용은 타깃 원본 파일을 열어야 함"
        assert "경고: fake warn" in txt
        # Blender 가 없을 때: 친절한 오류
        os.environ["PATH"] = ""
        os.environ.pop("MOTIONBRIDGE_BLENDER", None)
        from motionbridge.core import bridge
        old = bridge.CONFIG_PATH
        bridge.CONFIG_PATH = os.path.join(d, "nonexistent.json")
        try:
            rc, txt = run_cli(["info", fsrc])
        finally:
            bridge.CONFIG_PATH = old
        assert rc == 1 and "Blender" in txt, txt
    print("fbx via fake blender OK")


def test_multiclip_batch():
    with tempfile.TemporaryDirectory() as d:
        src, src2, tgt, _, _ = make_inputs(d)
        pr = Project()
        pr.load_source(src)
        # 같은 소스에 클립을 하나 더 붙여 배치 내보내기 확인
        pr.src.animations.append(pr.src.animations[0].copy())
        pr.src.animations[1].name = "walk"  # 이름 충돌 -> 번호 붙어야 함
        pr.load_target(tgt)
        files, warns = pr.export(ExportOptions(out_dir=os.path.join(d, "o"), presets=["blender"], formats=["bvh"], clips=[0, 1]))
        assert sorted(os.path.basename(f) for f in files) == ["walk_2_blender.bvh", "walk_blender.bvh"], files
    print("multiclip OK")


def test_organized_export():
    """캐릭터별 폴더 정리: 출력/캐릭터/Animations(모션) · Models(메시가 든 GLB, 본 구조 JSON)."""
    with tempfile.TemporaryDirectory() as d:
        src, _s2, tgt, _, _ = make_inputs(d)
        pr = Project()
        pr.load_source(src)
        pr.load_target(tgt)
        pr.automap()
        o = os.path.join(d, "o")
        # 추출 폴더 규칙(…/캐릭터/Animations/파일)에 있던 모션이면 그 캐릭터 폴더
        pr.src.path = "/out/Game/KnightLady/Animations/A_KnightLady_Attacks_Ability_Stab.psa"
        assert pr.export_char(0) == "KnightLady"
        files, _w = pr.export(ExportOptions(out_dir=o, presets=["blender"], formats=["bvh", "glb"], include_mesh=True, write_skeleton_json=True, organize=True))
        rel = sorted(os.path.relpath(f, o).replace("\\", "/") for f in files)
        assert any(r.startswith("KnightLady/Animations/") and r.endswith(".bvh") for r in rel), rel
        assert any(r.startswith("KnightLady/Models/") and r.endswith("_skeleton.json") for r in rel), rel
        assert all(r.startswith("KnightLady/") for r in rel), rel
        # 이름을 직접 정하면 그 이름 / 정리를 끄면 예전처럼 한 폴더
        files, _w = pr.export(ExportOptions(out_dir=o, presets=["blender"], formats=["bvh"], organize=True, char_name="내 캐릭터"))
        assert os.path.relpath(files[0], o).replace("\\", "/").startswith("내 캐릭터/Animations/")
        files, _w = pr.export(ExportOptions(out_dir=os.path.join(d, "flat"), presets=["blender"], formats=["bvh"]))
        assert os.path.dirname(files[0]) == os.path.join(d, "flat")
        # 경로에 규칙이 없으면 클립 이름에서
        pr.src.path = "/x/mocap.bvh"
        pr.src.animations[0].name = "A_ParasiteGolem_Locomotion_Walk_Rwd"
        assert pr.export_char(0) == "ParasiteGolem"
    print("organized export OK")


if __name__ == "__main__":
    test_organized_export()
    test_cli_end_to_end()
    test_curves_only_and_source()
    test_profile_roundtrip_and_settings()
    test_fbx_via_fake_blender()
    test_multiclip_batch()
    print("test_pipeline ALL OK")
