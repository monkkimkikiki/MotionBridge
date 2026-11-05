"""애니메이션 가공: 늘리기/fps/자르기/루프/키 압축 + 내보내기 연결(GLB 희소 키, JSON, FBX 단순화 계수)."""
import json
import os
import stat
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge.core import animops as A  # noqa: E402
from motionbridge.core import quat as Q  # noqa: E402
from motionbridge.core.io_gltf import read_gltf, write_glb  # noqa: E402
from motionbridge.core.model import Animation, fk  # noqa: E402
from motionbridge.core.pipeline import ExportOptions, Project  # noqa: E402
import synth  # noqa: E402
import test_pipeline as TP  # noqa: E402


def toy(T=61, J=4):
    t = np.linspace(0, 1, T)
    rot = np.zeros((T, J, 4))
    for j in range(J):
        rot[:, j] = Q.from_axis_angle(np.array([0, 0, 1.0]), np.sin(t * np.pi * 2) * 0.5 * (j + 1))
    rot[:, J - 1] = Q.identity((T,))  # 정지 관절
    pos = np.zeros((T, J, 3))
    pos[:, 0, 0] = t * 100
    pos[:, 1, 2] = np.sin(t * 6) * 3
    return Animation("toy", 30.0, rot, pos)


def ang(a, b):
    return float(np.degrees(Q.angle_between(a, b)).max())


def test_resample():
    an = toy()
    r = A.resample(an, speed=2.0)
    assert r.n_frames == 31 and r.fps == 30.0
    assert ang(r.rot[0], an.rot[0]) < 1e-6 and ang(r.rot[-1], an.rot[-1]) < 1e-6, "처음/끝 포즈 유지"
    assert abs(r.duration - 31 / 30.0) < 1e-9
    assert A.resample(an, frames=121).n_frames == 121
    r = A.resample(an, fps=60)
    assert r.fps == 60 and r.n_frames == 121 and abs((r.n_frames - 1) / 60 - 2.0) < 1e-9
    assert A.resample(an, seconds=4).n_frames == 120
    # 2배로 늘렸다가 원래대로: 직선/사인 곡선이라 오차가 작다
    back = A.resample(A.resample(an, frames=121), frames=61)
    assert ang(back.rot, an.rot) < 0.2 and np.abs(back.pos - an.pos).max() < 0.05
    # 속도에 따라 이동량(루트 위치)이 보존
    assert abs(A.resample(an, speed=0.5).pos[-1, 0, 0] - 100) < 1e-9
    one = A.resample(Animation("x", 30.0, an.rot[:1], an.pos[:1]), fps=60)
    assert one.n_frames == 1 and one.fps == 60
    for bad in (dict(speed=0), dict(speed=-1), dict(fps=-5)):
        try:
            A.resample(an, **bad)
            raise AssertionError("오류가 나야 함")
        except ValueError:
            pass


def test_trim_loop():
    an = toy()
    t = A.trim(an, 10, 29)
    assert t.n_frames == 20 and ang(t.rot[0], an.rot[10]) < 1e-9
    assert A.trim(an, -5, 999).n_frames == an.n_frames
    assert A.trim(an, 50, 10).n_frames == 1
    parents = np.array([-1, 0, 1, 2])
    l = A.loop_blend(an, 10, parents)
    assert ang(l.rot[-1], l.rot[0]) < 1e-6, "마지막 프레임이 첫 포즈와 같아야 함"
    assert abs(l.pos[-1, 0, 0] - an.pos[-1, 0, 0]) < 1e-12, "루트 이동은 유지"
    assert np.allclose(l.pos[-1, 1], an.pos[0, 1]), "루트가 아닌 관절 위치는 첫 포즈로"
    assert ang(l.rot[:51], an.rot[:51]) < 1e-9, "앞부분은 그대로"


def test_compress():
    an = toy()
    ks = A.compress(an, 0.5, 0.1)
    assert all(k[0] == 0 and k[-1] == an.n_frames - 1 for k in ks.rot + ks.pos)
    assert len(ks.rot[3]) == 2 and len(ks.pos[3]) == 2, "정지 관절은 첫/끝 키만"
    rec = A.reconstruct(an, ks)
    assert ang(rec.rot, an.rot) <= 0.5 + 1e-6 and np.linalg.norm(rec.pos - an.pos, axis=-1).max() <= 0.1 + 1e-9
    assert ks.total() < 0.35 * (2 * an.n_frames * 4)
    # 허용치 0 이면 사실상 모든 곡선 키를 보존해서 원본과 같아야 함
    tight = A.compress(an, 1e-9, 1e-9)
    assert ang(A.reconstruct(an, tight).rot, an.rot) < 1e-3
    # 허용치가 클수록 키가 줄어든다
    assert A.compress(an, 5.0, 1.0).total() < ks.total()
    mr, mp = ks.mask()
    assert mr.shape == (61, 4) and mr.sum() == sum(len(k) for k in ks.rot)


def test_apply_edit():
    an = toy()
    a, k, info = A.apply_edit(an, None)
    assert a is an and k is None and info.frames_after == 61
    a, k, info = A.apply_edit(an, A.EditOptions(trim_start=10, trim_end=50, speed=2.0, loop_frames=5, compress=True), 100.0, np.array([-1, 0, 1, 2]))
    assert a.n_frames == 21 and k is not None and k.n_frames == 21
    assert info.frames_before == 61 and info.frames_after == 21 and info.keys_after < info.keys_before
    assert info.max_rot_err_deg <= 0.5 + 1e-6 and "키" in info.summary()
    assert not A.EditOptions().active() and A.EditOptions(fps=60).active()
    _, _, info = A.apply_edit(an, A.EditOptions(speed=4.0), 100.0)
    assert info.notes, "큰 속도 변경 경고"
    # 단위: 위치 허용치는 cm 기준이라 단위가 미터면 100배 작아진다
    _, k_cm, _ = A.apply_edit(an, A.EditOptions(compress=True, pos_tol_cm=1.0), 100.0)
    _, k_m, _ = A.apply_edit(an, A.EditOptions(compress=True, pos_tol_cm=1.0), 1.0)
    assert k_m.total() >= k_cm.total()


def test_glb_sparse():
    with tempfile.TemporaryDirectory() as d:
        sk, meta = synth.build_skeleton("mixamo", "y_up_z", 1.0, "T", "aligned")
        an, _ = synth.walk_animation(sk, meta, n_frames=60)
        ks = A.compress(an, 0.5, 0.005)
        rec = A.reconstruct(an, ks)
        dense = os.path.join(d, "dense.glb")
        sparse = os.path.join(d, "sparse.glb")
        write_glb(dense, sk, an)
        write_glb(sparse, sk, rec, keys=ks)
        assert os.path.getsize(sparse) < 0.8 * os.path.getsize(dense), (os.path.getsize(sparse), os.path.getsize(dense))
        a1 = read_gltf(dense, fps=30.0).animations[0]  # 읽기 쪽 좌표 변환이 같도록 압축 안 한 GLB 와 비교
        a2 = read_gltf(sparse, fps=30.0).animations[0]
        assert a2.n_frames == 60 and a1.n_frames == 60
        assert ang(a2.rot, a1.rot) < 0.6, "희소 키를 다시 읽어 보간해도 허용 오차 안"
        assert np.abs(a2.pos - a1.pos).max() < 0.0051


def test_pipeline_edit():
    with tempfile.TemporaryDirectory() as d:
        src, src2, tgt, (sk, an), (tk, tmeta) = TP.make_inputs(d)
        p = Project()
        p.load_source(src)
        p.load_target(tgt)
        p.automap()
        out = os.path.join(d, "out")
        opt = ExportOptions(out, presets=["blender"], formats=["glb", "bvh"],
                            edit=A.EditOptions(speed=0.5, fps=60, compress=True, rot_tol_deg=1.0, pos_tol_cm=0.5))
        files, warns = p.export(opt)
        assert p.edit_info is not None and p.edit_info.frames_after == 157 and p.edit_info.fps_after == 60, p.edit_info.summary()
        g = read_gltf([f for f in files if f.endswith(".glb")][0], fps=60.0)
        assert g.animations[0].n_frames == 157
        from motionbridge.core.io_bvh import read_bvh
        b = read_bvh([f for f in files if f.endswith(".bvh")][0])
        assert b.animations[0].n_frames == 157
        # 커브 JSON: 희소 키
        out2 = os.path.join(d, "out2")
        files, _ = p.export(ExportOptions(out2, include_skeleton=False, edit=A.EditOptions(compress=True, rot_tol_deg=1.0)))
        js = json.load(open(files[0], encoding="utf-8"))
        assert js["sparse"] and js["frames"] == 40
        j0 = next(iter(js["joints"].values()))
        assert len(j0["rotation"]) == len(j0["rotation_frames"]) <= 40
        # 가공 없이는 기존 형식 그대로
        files, _ = p.export(ExportOptions(os.path.join(d, "out3"), include_skeleton=False))
        js = json.load(open(files[0], encoding="utf-8"))
        assert "sparse" not in js and len(next(iter(js["joints"].values()))["rotation"]) == 40
        # FBX: 허용 오차가 Blender 단순화 계수로 전달된다
        if sys.platform == 'win32':  # 셔뱅 가짜 실행파일은 Windows 에서 못 쓴다 (test_pipeline 과 같은 이유)
            print('FBX 단순화 계수 전달: Windows 에서는 건너뜀')
            return
        fb = os.path.join(d, "fakeblender")
        with open(fb, "w") as f:
            f.write(TP.FAKE_BLENDER.format(py=sys.executable))
        os.chmod(fb, os.stat(fb).st_mode | stat.S_IEXEC)
        log = os.path.join(d, "log.txt")
        os.environ["FAKE_LOG"] = log
        ftgt = os.path.join(d, "mymodel.fbx")
        open(ftgt, "wb").write(open(tgt, "rb").read())
        p2 = Project()
        p2.load_source(src)
        p2.load_target(tgt)
        p2.automap()
        p2.tgt.path = ftgt
        p2.blender = fb
        p2.export(ExportOptions(os.path.join(d, "out4"), presets=["blender"], formats=["fbx"], blender=fb,
                                edit=A.EditOptions(compress=True, rot_tol_deg=1.0, pos_tol_cm=0.5)))
        apply_calls = [json.loads(x) for x in open(log).read().splitlines() if "blender_apply.py" in x]
        assert apply_calls, "FBX 적용 호출이 있어야 함"
        simp = json.loads(apply_calls[0][4])["simplify"]
        assert 0.001 < simp < 0.01, simp


def test_psa_edit_and_cli():
    from motionbridge.core import extractor
    from motionbridge.core.io_psa import read_psa_exact, write_psa
    with tempfile.TemporaryDirectory() as d:
        sk, meta = synth.build_skeleton("ue", "z_up_x", 100.0, "A", "bone_x")
        an, _ = synth.walk_animation(sk, meta, n_frames=41, name="w")
        f = os.path.join(d, "w.psa")
        write_psa(f, sk, [an])
        infos = []
        made, warns = extractor.edit_psa_files([f, os.path.join(d, "none.psa"), os.path.join(d, "x.txt")],
                                               A.EditOptions(speed=2.0, compress=True), on_info=lambda p, i: infos.append(i))
        assert made == [os.path.join(d, "w_edit.psa")] and not warns, (made, warns)
        m = read_psa_exact(made[0])
        assert m.animations[0].n_frames == 21 and infos[0].keys_after < infos[0].keys_before
        assert m.skeleton.names == sk.names and os.path.isfile(f), "원본은 그대로"
        assert extractor.edit_psa_files([f], A.EditOptions()) == ([], []), "가공 없으면 아무것도 안 함"
        bad = os.path.join(d, "bad.psa")
        open(bad, "wb").write(b"not a psa")
        made, warns = extractor.edit_psa_files([bad], A.EditOptions(fps=60))
        assert not made and "가공 실패" in warns[0]
        # CLI
        src, src2, tgt, _a, _b = TP.make_inputs(d)
        out = os.path.join(d, "o")
        rc, txt = TP.run_cli(["retarget", "--source", src, "--target", tgt, "--out", out, "--formats", "glb", "--speed", "2", "--compress", "--rot-tol", "1"])
        assert rc == 0 and "가공(" in txt and "키" in txt, txt
        g = read_gltf([os.path.join(out, x) for x in os.listdir(out) if x.endswith(".glb")][0], fps=30.0)
        assert g.animations[0].n_frames == 21, g.animations[0].n_frames


def test_retime_segment():
    an = toy(100)
    r = A.retime_segment(an, 0, 42, 30)  # 1~43프레임을 30프레임으로 줄이기
    assert r.n_frames == 100 - 43 + 30
    assert ang(r.rot[29], an.rot[42]) < 1e-9 and ang(r.rot[30], an.rot[43]) < 1e-9, "구간 끝 포즈와 그 다음 프레임이 이어짐"
    assert ang(r.rot[0], an.rot[0]) < 1e-9 and abs(r.pos[29, 0, 0] - an.pos[42, 0, 0]) < 1e-9
    assert abs(A.speed_of(A.Retime(0, 42, 30)) - 42 / 29) < 1e-9
    r = A.retime_segment(an, 40, 99, 120)  # 끝 구간 늘리기
    assert r.n_frames == 160 and abs(r.pos[-1, 0, 0] - an.pos[-1, 0, 0]) < 1e-9
    assert A.retime_segment(an, 10, 20, 11).n_frames == 100 and ang(A.retime_segment(an, 10, 20, 11).rot, an.rot) < 1e-9
    assert A.retime_segment(an, 10, 20, 1).n_frames == 90, "길이 1 = 구간을 한 포즈로"
    assert A.retime_segment(an, 5, 5, 4).n_frames == 103, "프레임 하나 늘리기"
    assert A.retime_segment(an, -5, 999, 50).n_frames == 50, "범위는 자동으로 맞춰짐"
    r = A.apply_retimes(an, [A.Retime(0, 42, 30), A.Retime(0, 29, 60)])
    assert r.n_frames == 100 - 43 + 30 - 30 + 60, r.n_frames
    assert A.apply_retimes(an, []) is an
    back = A.retime_segment(A.retime_segment(an, 0, 99, 199), 0, 198, 100)  # 두 배로 늘렸다가 되돌리면 거의 같다
    assert ang(back.rot, an.rot) < 0.2


def test_project_retimes():
    with tempfile.TemporaryDirectory() as d:
        src, src2, tgt, _a, _b = TP.make_inputs(d)
        p = Project()
        p.load_source(src)
        p.load_target(tgt)
        p.automap()
        T0 = p.src.animations[0].n_frames  # 40
        assert p.anim is p.src.animations[0]
        p.pending = (0, A.Retime(0, 19, 10))
        assert p.anim.n_frames == T0 - 10 and p.edited(0, with_pending=False).n_frames == T0
        res = p.run(analyze_quality=False)
        assert res.anim.n_frames == T0 - 10, "리타깃도 편집된 모션으로"
        p.pending = (1, A.Retime(0, 19, 10))
        assert p.anim.n_frames == T0, "다른 클립의 미리보기는 적용 안 됨"
        p.pending = (0, A.Retime(0, 19, 10))
        assert p.commit_pending() and p.pending is None and len(p.retimes[0]) == 1 and p.anim.n_frames == T0 - 10
        assert not p.commit_pending()
        p.pending = (0, A.Retime(0, 9, 30))
        assert p.undo_retime() and p.pending is None and len(p.retimes[0]) == 1, "미리보기가 있으면 그것부터 버림"
        assert p.undo_retime() and not p.retimes[0] and not p.undo_retime()
        p.pending = (0, A.Retime(0, 19, 10))
        p.commit_pending()
        p.pending = (0, A.Retime(0, 9, 5))  # 내보내기는 보이는 그대로(확정 + 미리보기)
        files, _w = p.export(ExportOptions(os.path.join(d, "o"), include_skeleton=False))
        js = json.load(open(files[0], encoding="utf-8"))
        assert js["frames"] == T0 - 10 - 5, js["frames"]
        p.reset_retimes()
        assert p.anim.n_frames == T0 and p.pending is None
        p.pending = (0, A.Retime(0, 5, 2))
        p.load_source(src)
        assert p.pending is None and not p.retimes, "새 소스를 열면 편집 초기화"


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
    print("animops 통과")
