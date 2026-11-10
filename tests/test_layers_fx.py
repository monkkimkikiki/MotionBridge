"""보정 레이어(비파괴 키프레임 수정)와 모션빌더식 후처리(부위 세기·튀는 프레임·부드럽게·좌우 반전) 시험."""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge.core import motionfx
from motionbridge.core import quat as Q
from motionbridge.core.layers import combine_effective, Layer, LayerStack, apply_layers
from motionbridge.core.model import Animation, fk
from motionbridge.core.pipeline import Project

# 이 파일의 예전 시험들은 '끝 키 값 유지(fade 0)' 를 가정한다. 퍼짐(fade) 동작은 test_layer_fade / UI 시험이 따로 본다.
_add_orig = LayerStack.add


def _add_hold(self, name=None):
    lay = _add_orig(self, name)
    lay.fade = 0
    return lay


LayerStack.add = _add_hold
import test_pipeline as TP


def project(d):
    src, _s2, tgt, _a, _b = TP.make_inputs(d)
    pr = Project()
    pr.load_source(src)
    pr.load_target(tgt)
    return pr


def test_base_cache():
    """레이어만 바뀌면 리타겟을 다시 계산하지 않고(같은 결과 재사용), 설정/매핑/프레임 편집이 바뀌면 다시 계산한다."""
    import time
    with tempfile.TemporaryDirectory() as d:
        pr = project(d)
        r0 = pr.run(0)
        base_id = id(pr._base_cache[1])
        lay = pr.layer_stack().add()
        lay.set_key(pr.tgt_slots["Hips"], 3, pos=np.array([0.0, 4.0, 0.0]))
        r1 = pr.run(0)
        assert id(pr._base_cache[1]) == base_id, "레이어만 바뀌었는데 다시 계산함"
        assert not np.allclose(r1.anim.pos, r0.anim.pos) and np.allclose(r1.base_anim.rot, r0.anim.rot), "결과는 레이어가 반영돼야 함"
        assert r0.warnings is not r1.warnings
        pr.settings.root_yaw_deg = 30.0
        pr.run(0)
        assert id(pr._base_cache[1]) != base_id, "설정이 바뀌면 다시 계산해야 함"
        pr.settings.root_yaw_deg = 0.0
        t0 = time.perf_counter(); pr.run(0); t_hit = time.perf_counter() - t0
        pr._base_cache = None
        t0 = time.perf_counter(); pr.run(0); t_miss = time.perf_counter() - t0
    print("base cache OK (%.1fms vs %.1fms)" % (t_hit * 1000, t_miss * 1000))


def test_layer_fade():
    """키를 하나만 찍으면 그 프레임만 고쳐지고 앞뒤는 fade 프레임에 걸쳐 원래 값으로 돌아간다 (맥스 바이패드 레이어처럼)."""
    X = np.array([1.0, 0, 0])
    lay = Layer("a")                                 # 기본 fade
    lay.set_key(3, 20, rot=Q.from_axis_angle(X, np.radians(30)))
    r, _ = lay.eval(3, 60)
    ang = np.degrees(Q.angle_between(r, np.tile(np.array([1.0, 0, 0, 0]), (60, 1))))
    assert abs(ang[20] - 30) < 1e-6
    assert ang[0] < 1e-6 and ang[60 - 1] < 1e-6 and ang[20 - 8] < 1e-6 and ang[20 + 8] < 1e-6, "fade 프레임 뒤에는 원래 값"
    assert 0 < ang[24] < 30 and ang[22] > ang[24] > ang[27], "서서히 돌아감"
    # 앞뒤에 키를 찍어 두면 그 사이만 블렌드되고, 20 이후는 원래 각도
    lay2 = Layer("b")
    lay2.set_key(3, 0, rot=np.array([1.0, 0, 0, 0]))
    lay2.set_key(3, 40, rot=np.array([1.0, 0, 0, 0]))
    lay2.set_key(3, 20, rot=Q.from_axis_angle(X, np.radians(30)))
    r2, _ = lay2.eval(3, 60)
    a2 = np.degrees(Q.angle_between(r2, np.tile(np.array([1.0, 0, 0, 0]), (60, 1))))
    assert abs(a2[20] - 30) < 1e-6 and a2[0] < 1e-6 and a2[40] < 1e-6 and a2[55] < 1e-6 and 0 < a2[10] < 30
    # 저장/실행취소 상태에 fade 가 들어간다
    from motionbridge.core.layers import LayerStack
    st = LayerStack()
    st.push(lay)
    lay.fade = 3
    snap = st.snapshot()
    assert LayerStack.from_snapshot(snap).layers[0].fade == 3
    assert Layer.from_dict(lay.to_dict(["a"] * 5), {"a": 3}).fade == 3
    assert Layer.from_dict({"name": "old", "keys": {}}, {}).fade == 0, "예전 파일은 끝 키 값 유지"
    # 합쳐도 같은 모양 (퍼짐 구간까지 키로 구워 넣음)
    from motionbridge.core.layers import merge_layers
    lay.fade = 8
    la = Layer("c")
    la.set_key(3, 30, rot=Q.from_axis_angle(X, np.radians(20)))
    m = merge_layers([lay, la], 60)
    rm, _ = m.effective(3, 60)
    rr, _ = combine_effective([lay, la], 3, 60)
    assert float(np.max(Q.angle_between(rm, rr))) < np.radians(0.2), "합친 결과가 같아야 함"
    print("layer fade OK")


def test_layer_basics():
    lay = Layer("a", fade=0)                      # fade 0 = 끝 키 값을 계속 유지
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


def test_spikes_smooth_parts():
    with tempfile.TemporaryDirectory() as d:
        pr = project(d)
        base = pr.run(0).anim
        a = base.copy()
        j = pr.tgt_slots["L_UpperArm"]
        a.rot[10, j] = Q.mul(Q.from_axis_angle(np.array([1.0, 0, 0]), np.radians(120)), a.rot[10, j])
        fixed, n = motionfx.remove_spikes(a, 40)
        assert n >= 1 and np.degrees(Q.angle_between(fixed.rot[10, j], base.rot[10, j])) < 10, n
        # 여러 프레임 연속으로 이상한 각도로 꺾인 구간(IK 가 억지로 따라가며 생기는 것) — 앞뒤 정상 프레임 사이로 메운다
        b = base.copy()
        for f in (20, 21, 22, 23):
            b.rot[f, j] = Q.mul(Q.from_axis_angle(np.array([0.0, 0, 1.0]), np.radians(70)), b.rot[f, j])
        e0 = max(np.degrees(Q.angle_between(b.rot[f, j], base.rot[f, j])) for f in (20, 21, 22, 23))
        fx4, n4 = motionfx.remove_spikes(b, 30, max_len=6)
        e1 = max(np.degrees(Q.angle_between(fx4.rot[f, j], base.rot[f, j])) for f in (20, 21, 22, 23))
        assert n4 >= 4 and e0 > 60 and e1 < 15, (n4, e0, e1)
        fx_short, n_s = motionfx.remove_spikes(b, 30, max_len=2)
        assert max(np.degrees(Q.angle_between(fx_short.rot[f, j], base.rot[f, j])) for f in (20, 21, 22, 23)) > 60, "최대 길이보다 긴 구간은 건드리지 않는다"
        # 진짜 빠른 움직임(한쪽으로 계속 도는 것)은 그대로
        q = base.copy()
        for f in range(q.n_frames):
            q.rot[f, j] = Q.mul(Q.from_axis_angle(np.array([0.0, 1.0, 0]), np.radians(12.0 * f)), base.rot[f, j])
        same, n_same = motionfx.remove_spikes(q, 30, max_len=6)
        assert n_same == 0 and np.allclose(same.rot, Q.make_continuous(Q.normalize(q.rot)), atol=1e-9), n_same
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
    test_layer_fade()
    test_base_cache()
    test_new_key_starts_from_visible_value()
    test_apply_non_destructive_and_fk()
    test_position_layer_and_export()
    test_layers_per_clip_and_reset()
    test_stack_states()
    test_merge()
    test_curve_drag()
    test_spikes_smooth_parts()
    test_mirror()
    test_profile_keeps_fx()
    print("test_layers_fx ALL OK")
