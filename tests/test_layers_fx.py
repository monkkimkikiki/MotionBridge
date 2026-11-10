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
    assert st.top == 2 and st.current() is b and st.active == 1
    st.move(1, -1)
    assert st.layers[0] is b and st.top == 1 and st.current() is b       # 선택(상태)이 레이어를 따라간다
    st.remove(0)
    assert len(st) == 1 and st.top == 0 and st.current() is None         # 보던 레이어를 지우면 그 아래 상태(원본)로
    st.set_top(1)
    assert st.current() is a
    print("layer stack OK")


def test_stack_states():
    """스택 상태: 원본/레이어1/레이어1+2 를 오갈 수 있고, 위 레이어는 지워지지 않은 채 잠시 안 보인다. 새 레이어는 현재 상태 위에 쌓인다."""
    with tempfile.TemporaryDirectory() as d:
        pr = project(d)
        j = pr.tgt_slots["L_LowerArm"]
        k = pr.tgt_slots["R_LowerArm"]
        st = pr.layer_stack()
        l1 = st.add("하나")
        l1.set_key(j, 0, rot=Q.from_axis_angle(np.array([0, 0, 1.0]), np.radians(30)))
        l2 = st.add("둘")
        l2.set_key(k, 0, rot=Q.from_axis_angle(np.array([0, 0, 1.0]), np.radians(40)))
        base = pr.run(0)
        assert st.top == 2
        full = base.anim.rot.copy()
        st.set_top(0)
        r0 = pr.run(0).anim.rot.copy()
        assert pr.result.base_anim is None
        st.set_top(1)
        r1 = pr.run(0).anim.rot.copy()
        assert not np.allclose(r1[:, j], r0[:, j]) and np.allclose(r1[:, k], r0[:, k])       # 1번까지: 왼팔만
        st.set_top(2)
        assert np.allclose(pr.run(0).anim.rot, full)                                           # 다시 맨 위: 전부
        # 중간 상태에서 새 레이어는 그 위에 끼워진다
        st.set_top(1)
        l3 = st.add("끼움")
        assert [l.name for l in st.layers] == ["하나", "끼움", "둘"] and st.top == 2 and st.current() is l3
        assert len(st) == 3
        # 스냅샷(실행 취소)에 상태가 들어 있다
        sn = st.snapshot()
        st.set_top(0)
        assert LayerStack.from_snapshot(sn).top == 2 and LayerStack.from_snapshot(sn).snapshot() == sn
    print("stack states OK")


def test_merge():
    """합치기: 합친 레이어 하나의 결과가 합치기 전과 (허용 오차 안에서) 같고, 가중치가 값에 구워진다. 꺼진 레이어가 있으면 거부."""
    with tempfile.TemporaryDirectory() as d:
        pr = project(d)
        j = pr.tgt_slots["L_LowerArm"]
        hips = pr.tgt_slots["Hips"]
        T = pr.run(0).anim.n_frames
        st = pr.layer_stack()
        a = st.add("A")
        a.set_key(j, 0, rot=Q.from_axis_angle(np.array([0, 0, 1.0]), np.radians(20)))
        a.set_key(j, 20, rot=Q.from_axis_angle(np.array([0, 0, 1.0]), np.radians(60)))
        a.set_key(hips, 5, pos=np.array([0, 3.0, 0]))
        b = st.add("B")
        b.weight = 0.6
        b.set_key(j, 8, rot=Q.from_axis_angle(np.array([1.0, 0, 0]), np.radians(50)))
        b.set_key(j, 30, rot=Q.from_axis_angle(np.array([0, 1.0, 0]), np.radians(-30)))
        b.set_key(hips, 12, pos=np.array([2.0, 0, 0]))
        before = pr.run(0).anim.copy()
        why = st.merge_down(1, T)
        assert why is None and len(st) == 1 and st.top == 1 and st.layers[0].name == "A" and st.layers[0].weight == 1.0, (why, len(st))
        after = pr.run(0).anim
        err = np.degrees(Q.angle_between(before.rot, after.rot)).max()
        assert err < 0.2, err                                              # 회전: 0.1° 허용 오차 근처
        assert np.abs(before.pos - after.pos).max() < 1e-3
        assert st.layers[0].n_keys() < 2 * T                              # 모든 프레임에 키를 찍지 않는다
        # 꺼진 레이어가 있으면 합치지 않고 이유를 알려 준다
        st.add("C").set_key(j, 3, rot=Q.from_axis_angle(np.array([0, 0, 1.0]), np.radians(10)))
        st.layers[1].mute = True
        why = st.merge_down(1, T)
        assert why and "꺼진" in why and len(st) == 2
        st.layers[1].mute = False
        assert st.merge_range(0, 1, T) is None and len(st) == 1
        assert st.merge_down(0, T)           # 아래가 없다
    print("merge OK")


def test_curve_drag():
    """그래프에서 끈 값 = 그 프레임의 최종 채널 값. 다른 채널은 그대로, 끌기 중 여러 번 움직여도 겹쳐 더해지지 않는다."""
    from motionbridge.core import curves
    for weight in (1.0, 0.5):
        with tempfile.TemporaryDirectory() as d:
            pr = project(d)
            j = pr.tgt_slots["L_LowerArm"]
            hips = pr.tgt_slots["Hips"]
            lay = pr.layer_stack().add()
            lay.weight = weight
            f = 10
            lay.set_key(j, f)
            lay.set_key(hips, f)
            res = pr.run(0)
            ch0 = curves.channel_values(res.anim, j)
            drag = curves.begin_drag(lay, res.anim, j, f)
            assert drag is not None and curves.begin_drag(lay, res.anim, j, f + 1) is None      # 키 없는 곳은 끌 수 없다
            for v in (ch0["ry"][f] + 5, ch0["ry"][f] + 20, ch0["ry"][f] + 33.0):               # 한 번의 끌기: 같은 기준에서 매번 다시 계산
                assert curves.drag_to(lay, drag, "ry", v)
            res2 = pr.run(0)
            ch1 = curves.channel_values(res2.anim, j)
            assert abs(ch1["ry"][f] - (ch0["ry"][f] + 33.0)) < 1e-4, (weight, ch1["ry"][f], ch0["ry"][f] + 33.0)
            assert abs(ch1["rx"][f] - ch0["rx"][f]) < 1e-4 and abs(ch1["rz"][f] - ch0["rz"][f]) < 1e-4, "다른 채널은 그대로"
            # 위치 채널
            pc0 = curves.channel_values(res2.anim, hips)
            dh = curves.begin_drag(lay, res2.anim, hips, f)
            curves.drag_to(lay, dh, "py", pc0["py"][f] + 4.0)
            curves.drag_to(lay, dh, "py", pc0["py"][f] + 7.0)
            pc1 = curves.channel_values(pr.run(0).anim, hips)
            assert abs(pc1["py"][f] - (pc0["py"][f] + 7.0)) < 1e-6 and abs(pc1["px"][f] - pc0["px"][f]) < 1e-9, weight
            assert curves.add_key(lay, j, f + 4) and (f + 4) in lay.keys[j]
    # 오일러 곡선은 360° 로 튀지 않고 이어진다
    q = np.stack([Q.from_axis_angle(np.array([0, 1.0, 0]), np.radians(a)) for a in np.linspace(0, 350, 40)])
    e = curves.euler_curve(q)
    assert np.abs(np.diff(e[:, 1])).max() < 60, np.abs(np.diff(e[:, 1])).max()
    print("curve drag OK")


def test_auto_flatten():
    """접지 구간의 회전 떨림을 새 레이어로 평탄화한다: 떨림이 줄고, 접지 밖은 그대로이며, 레이어를 끄면 원래대로."""
    from motionbridge.core import autoflat
    with tempfile.TemporaryDirectory() as d:
        pr = project(d)
        res = pr.run(0)
        assert res.plans and any(p.contact.any() for p in res.plans), "시험 데이터에 접지 구간이 있어야 한다"
        # 접지 구간의 다리 관절에 잔떨림을 얹어 둔다 (IK 전환 톱니 흉내)
        rng = np.random.default_rng(3)
        noisy = res.anim.copy()
        ch = res.chains[0]
        pl = res.plans[0]
        idx = np.where(pl.contact)[0]
        for j in (ch.upper, ch.lower, ch.foot):
            n = Q.from_axis_angle(np.array([1.0, 0, 0]), np.radians(rng.normal(scale=3.0, size=len(idx))))
            noisy.rot[idx, j] = Q.mul(n, noisy.rot[idx, j])
        lay, msg = autoflat.flatten_layer(noisy, res.chains, res.plans, sigma=2.5)
        assert lay is not None and "감소" in msg, msg
        a, b = int(idx.min()), int(idx.max())
        from motionbridge.core.layers import LayerStack, apply_layers
        st = LayerStack()
        st.layers.append(lay)
        st.top = 1
        flat = apply_layers(noisy, st)
        for j in (ch.upper, ch.lower, ch.foot):
            assert autoflat.jitter(flat.rot[:, j], a, b) < 0.6 * autoflat.jitter(noisy.rot[:, j], a, b), (j,)
        # 접지 구간 밖(여유 프레임 바깥)은 변하지 않는다
        far = np.ones(noisy.n_frames, bool)
        for p in res.plans:
            for aa, bb in autoflat._runs(p.contact):
                far[max(0, aa - 4):bb + 5] = False
        if far.any():
            assert np.allclose(flat.rot[far], noisy.rot[far], atol=1e-9)
        assert lay.n_keys() < noisy.n_frames * 3 * len(res.plans), "모든 프레임에 키를 찍지 않는다"
        # 레이어를 끄면 원래대로
        lay.mute = True
        assert apply_layers(noisy, st) is noisy
        # 접지 구간이 없으면 안내
        none_plans = [type("P", (), {"contact": np.zeros(noisy.n_frames, bool)})() for _ in res.plans]
        l2, m2 = autoflat.flatten_layer(noisy, res.chains, none_plans)
        assert l2 is None and "없" in m2
        # 이미 매끄러운 모션이면 바꿀 게 없다고 알려 준다
        l3, m3 = autoflat.flatten_layer(res.anim, res.chains, res.plans, sigma=2.5)
        assert (l3 is None and "이미" in m3) or l3 is not None
    print("auto flatten OK")


def test_project_auto_flatten():
    with tempfile.TemporaryDirectory() as d:
        pr = project(d)
        rng = np.random.default_rng(5)
        res = pr.run(0)
        # 잔떨림을 리타깃 결과 대신 소스에 심을 수 없으니, 설정으로는 평탄화할 게 있는지만 본다 (없거나 있거나 둘 다 정상 응답)
        lay, msg = pr.auto_flatten(2.5)
        assert isinstance(msg, str) and msg
        if lay is not None:
            st = pr.layer_stack()
            assert st.layers[-1] is lay and st.top == len(st) and pr.run(0).base_anim is not None
        pr.settings.fx_mirror = True
        l2, m2 = pr.auto_flatten()
        assert l2 is None and "반전" in m2
    print("project auto flatten OK")


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
    test_stack_states()
    test_merge()
    test_curve_drag()
    test_auto_flatten()
    test_project_auto_flatten()
    test_spikes_smooth_parts()
    test_mirror()
    test_profile_keeps_fx()
    print("test_layers_fx ALL OK")
