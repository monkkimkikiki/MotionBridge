"""보정 레이어(비파괴 키프레임 수정)와 모션빌더식 후처리(부위 세기·튀는 프레임·부드럽게·좌우 반전) 시험."""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge.core import motionfx
from motionbridge.core import quat as Q
from motionbridge.core.layers import Layer, LayerStack, apply_layers
from motionbridge.core.model import Animation, fk
from motionbridge.core.pipeline import Project
import test_pipeline as TP


def project(d):
    src, _s2, tgt, _a, _b = TP.make_inputs(d)
    pr = Project()
    pr.load_source(src)
    pr.load_target(tgt)
    return pr


def test_layer_basics():
    lay = Layer("a")
    lay.set_key(3, 10, rot=Q.from_axis_angle(np.array([1.0, 0, 0]), np.radians(40)))
    lay.set_key(3, 20, rot=Q.from_axis_angle(np.array([1.0, 0, 0]), np.radians(80)))
    r, p = lay.eval(3, 40)
    ang = np.degrees(Q.angle_between(r, np.repeat(Q.identity((1, 4)), 40, 0) if False else np.tile(np.array([1.0, 0, 0, 0]), (40, 1))))
    assert abs(ang[10] - 40) < 1e-6 and abs(ang[20] - 80) < 1e-6 and abs(ang[15] - 60) < 1e-4, ang[[10, 15, 20]]
    assert abs(ang[0] - 40) < 1e-6 and abs(ang[39] - 80) < 1e-6         # 첫/마지막 키 밖은 값을 유지
    assert lay.frames(3) == [10, 20] and lay.next_key(3, 10) == 20 and lay.next_key(3, 10, -1) is None
    lay.weight = 0.5
    re, _ = lay.effective(3, 40)
    assert abs(np.degrees(Q.angle_between(re[20], np.array([1.0, 0, 0, 0]))) - 40) < 1e-4
    lay.mute = True
    re, _ = lay.effective(3, 40)
    assert np.allclose(re[20], [1, 0, 0, 0])
    assert lay.remove_key(3, 10) and not lay.remove_key(3, 10) and lay.frames(3) == [20]
    print("layer basics OK")


def test_new_key_starts_from_visible_value():
    lay = Layer()
    lay.set_key(1, 0, rot=Q.from_axis_angle(np.array([0, 1.0, 0]), np.radians(30)))
    lay.set_key(1, 20, rot=Q.from_axis_angle(np.array([0, 1.0, 0]), np.radians(70)))
    k = lay.set_key(1, 10)          # 사이에 새 키: 보이던 50° 에서 시작해야 튀지 않는다
    assert abs(np.degrees(Q.angle_between(k.rot, np.array([1.0, 0, 0, 0]))) - 50) < 1e-4
    print("layer new key continuity OK")


def test_apply_non_destructive_and_fk():
    with tempfile.TemporaryDirectory() as d:
        pr = project(d)
        base = pr.run(0).anim.copy()
        sk = pr.tgt.skeleton
        j = pr.tgt_slots["L_LowerArm"]
        st = pr.layer_stack()
        lay = st.add("팔 올리기")
        q = Q.from_axis_angle(np.array([0, 0, 1.0]), np.radians(45))
        lay.set_key(j, 0, rot=q)
        res = pr.run(0)
        assert res.base_anim is not None and np.allclose(res.base_anim.rot, base.rot)   # 원본 결과는 그대로
        assert not np.allclose(res.anim.rot[:, j], base.rot[:, j])
        # 그 본의 로컬 회전만 변하고, 자식 본은 FK 로 따라온다 (월드가 바뀜), 형제 본은 변하지 않는다
        other = pr.tgt_slots["R_LowerArm"]
        assert np.allclose(res.anim.rot[:, other], base.rot[:, other], atol=1e-9)
        wr0, _ = fk(sk, base.rot, base.pos)
        wr1, _ = fk(sk, res.anim.rot, res.anim.pos)
        child = sk.children[j][0]
        assert np.max(Q.angle_between(wr0[:, child], wr1[:, child])) > np.radians(40)
        # 가중치 0 / 끄기 -> 결과가 원래와 같다
        lay.weight = 0.0
        assert pr.run(0).base_anim is None
        lay.weight, lay.mute = 1.0, True
        assert pr.run(0).base_anim is None
        # 저장/불러오기 (이름 기준)
        lay.mute = False
        path = os.path.join(d, "layers.json")
        pr.save_layers(path)
        pr.layers = {}
        warns = pr.load_layers(path)
        assert not warns and pr.layer_stack().layers[0].frames(j) == [0], warns
        r2 = pr.run(0)
        assert np.allclose(r2.anim.rot, res.anim.rot, atol=1e-9)
    print("layer apply / non-destructive / save-load OK")


def test_position_layer_and_export():
    with tempfile.TemporaryDirectory() as d:
        pr = project(d)
        hips = pr.tgt_slots["Hips"]
        base = pr.run(0).anim.copy()
        lay = pr.layer_stack().add()
        lay.set_key(hips, 0, pos=np.array([0.0, 5.0, 0.0]))
        res = pr.run(0)
        assert np.allclose(res.anim.pos[:, hips] - base.pos[:, hips], [0, 5, 0])
        from motionbridge.core.pipeline import ExportOptions
        out = os.path.join(d, "out")
        files, _w = pr.export(ExportOptions(out_dir=out, presets=["blender"], formats=["bvh"]))
        b1 = [f for f in files if f.endswith(".bvh")]
        assert b1, files
        pr.layers = {}
        out2 = os.path.join(d, "out2")
        files2, _w = pr.export(ExportOptions(out_dir=out2, presets=["blender"], formats=["bvh"]))
        b0 = [f for f in files2 if f.endswith(".bvh")]
        assert open(b1[0]).read() != open(b0[0]).read()   # 내보내기가 레이어 적용 결과를 쓴다
    print("layer position + export OK")


def test_layers_per_clip_and_reset():
    st = LayerStack()
    a = st.add()
    b = st.add("B")
    assert st.active == 1 and st.current() is b
    st.move(1, -1)
    assert st.layers[0] is b and st.active == 0
    st.remove(0)
    assert len(st) == 1 and st.current() is a
    print("layer stack OK")


# ------------------------------------------------------------------- 후처리
def test_spikes_smooth_parts():
    with tempfile.TemporaryDirectory() as d:
        pr = project(d)
        base = pr.run(0).anim
        a = base.copy()
        j = pr.tgt_slots["L_UpperArm"]
        a.rot[10, j] = Q.mul(Q.from_axis_angle(np.array([1.0, 0, 0]), np.radians(120)), a.rot[10, j])
        fixed, n = motionfx.remove_spikes(a, 40)
        assert n >= 1 and np.degrees(Q.angle_between(fixed.rot[10, j], base.rot[10, j])) < 10, n
        sm = motionfx.smooth(a, 2.0)
        assert np.degrees(Q.angle_between(sm.rot[10, j], base.rot[10, j])) < np.degrees(Q.angle_between(a.rot[10, j], base.rot[10, j]))
        assert np.allclose(np.linalg.norm(sm.rot, axis=-1), 1.0)
        # 부위 세기 0 -> 왼팔 본이 기준 자세로 고정, 다른 부위는 그대로
        sk = pr.tgt.skeleton
        z = motionfx.scale_parts(base, sk, {"arm_L": 0.0}, pr.tgt_slots)
        for jj in (pr.tgt_slots["L_UpperArm"], pr.tgt_slots["L_Hand"]):
            assert np.max(Q.angle_between(z.rot[:, jj], np.tile(sk.rest_rot[jj], (z.n_frames, 1)))) < 1e-6
        assert np.allclose(z.rot[:, pr.tgt_slots["R_UpperArm"]], base.rot[:, pr.tgt_slots["R_UpperArm"]])
        h = motionfx.scale_parts(base, sk, {"arm_L": 0.5}, pr.tgt_slots)
        mid = np.degrees(Q.angle_between(h.rot[:, j], np.tile(sk.rest_rot[j], (h.n_frames, 1)))).max()
        full = np.degrees(Q.angle_between(base.rot[:, j], np.tile(sk.rest_rot[j], (base.n_frames, 1)))).max()
        assert 0.3 * full < mid < 0.7 * full, (mid, full)
    print("spikes / smooth / part strength OK")


def test_mirror():
    with tempfile.TemporaryDirectory() as d:
        pr = project(d)
        res = pr.run(0)
        sk = pr.tgt.skeleton
        Ft = res.frame_tgt[0]
        m, why = motionfx.mirror(res.anim, sk, Ft)
        assert why is None, why
        wr, wp = fk(sk, res.anim.rot, res.anim.pos)
        mr, mp = fk(sk, m.rot, m.pos)
        ps, mps = Q.rotate(Ft, wp), Q.rotate(Ft, mp)
        part, x0 = motionfx.mirror_partners(sk, Ft)
        # 반전된 모션의 반대편 본 위치 = 원래 본 위치의 거울상
        for slot in ("L_Hand", "L_Foot", "L_LowerArm", "Hips"):
            j = pr.tgt_slots[slot]
            p = int(part[j])
            exp = ps[:, j].copy()
            exp[:, 0] = 2 * x0 - exp[:, 0]
            assert np.max(np.abs(mps[:, p] - exp)) < 1e-6 * (1 + float(np.abs(exp).max())), (slot, np.max(np.abs(mps[:, p] - exp)))
        assert part[pr.tgt_slots["L_Hand"]] == pr.tgt_slots["R_Hand"]
        # 두 번 뒤집으면 원래대로
        m2, _ = motionfx.mirror(m, sk, Ft)
        r2, p2 = fk(sk, m2.rot, m2.pos)
        assert np.max(np.abs(Q.rotate(Ft, p2) - ps)) < 1e-6 * (1 + float(np.abs(ps).max()))
        # 설정으로 켜면 파이프라인 결과가 반전된다
        pr.settings.fx_mirror = True
        res2 = pr.run(0)
        assert any("좌우 반전" in w for w in res2.warnings)
        assert not np.allclose(res2.anim.rot, res.anim.rot)
    print("mirror OK")


def test_profile_keeps_fx():
    with tempfile.TemporaryDirectory() as d:
        pr = project(d)
        pr.settings.fx_parts = {"arm_L": 0.5}
        pr.settings.fx_smooth = 1.5
        pr.settings.fx_mirror = True
        p = os.path.join(d, "prof.json")
        pr.save_profile(p)
        pr.settings.fx_parts, pr.settings.fx_smooth, pr.settings.fx_mirror = {}, 0.0, False
        pr.load_profile(p)
        assert pr.settings.fx_parts == {"arm_L": 0.5} and pr.settings.fx_smooth == 1.5 and pr.settings.fx_mirror
    print("profile fx OK")


def test_drag_to_key():
    """뷰포트에서 끈 만큼(표준 프레임 월드 회전/이동)이 키로 바뀌어, 다시 계산하면 정확히 그만큼 움직여 보인다."""
    from motionbridge.core import layeredit
    for weight in (1.0, 0.5):
        with tempfile.TemporaryDirectory() as d:
            pr = project(d)
            sk = pr.tgt.skeleton
            res0 = pr.run(0)
            Ft = res0.frame_tgt[0]
            j = pr.tgt_slots["L_LowerArm"]
            par = int(sk.parents[j])
            f = 12
            lay = pr.layer_stack().add()
            lay.weight = weight
            wr0, wp0 = fk(sk, res0.anim.rot, res0.anim.pos)
            q_view = Q.from_axis_angle(np.array([0.3, 0.9, 0.1]) / np.linalg.norm([0.3, 0.9, 0.1]), np.radians(35))
            assert layeredit.rot_edit(lay, j, f, q_view, Ft, wr0[f, par])
            res1 = pr.run(0)
            wr1, _ = fk(sk, res1.anim.rot, res1.anim.pos)
            d_w = Q.mul(Q.conj(Ft), Q.mul(q_view, Ft))
            want = Q.mul(d_w, wr0[f, j])
            assert np.degrees(Q.angle_between(wr1[f, j], want)) < 1e-4, (weight, np.degrees(Q.angle_between(wr1[f, j], want)))
            assert np.degrees(Q.angle_between(wr1[f, par], wr0[f, par])) < 1e-6    # 부모는 안 움직인다
            # 이동
            hips = pr.tgt_slots["Hips"]
            dv = np.array([0.0, 7.0, 3.0])
            hp = int(sk.parents[hips])
            assert layeredit.pos_edit(lay, hips, f, dv, Ft, wr0[f, hp] if hp >= 0 else None)
            res2 = pr.run(0)
            _, wp2 = fk(sk, res2.anim.rot, res2.anim.pos)
            assert np.max(np.abs(Q.rotate(Ft, wp2[f, hips]) - (Q.rotate(Ft, wp0[f, hips]) + dv))) < 1e-6, weight
        # 막힌 경우
        lay2 = Layer("x")
        lay2.mute = True
        assert layeredit.editable(lay2) and not layeredit.rot_edit(lay2, 1, 0, Q.identity(()), Ft, None)
        assert layeredit.editable(None)
    print("drag -> key OK")


if __name__ == "__main__":
    test_drag_to_key()
    test_layer_basics()
    test_new_key_starts_from_visible_value()
    test_apply_non_destructive_and_fk()
    test_position_layer_and_export()
    test_layers_per_clip_and_reset()
    test_spikes_smooth_parts()
    test_mirror()
    test_profile_keeps_fx()
    print("test_layers_fx ALL OK")
