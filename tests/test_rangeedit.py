"""구간 편집 (지우고 블렌드 / 일직선 / 떨림 정리)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import quat as Q
from motionbridge.core import rangeedit as R
from motionbridge.core.layers import Layer, apply_layers, LayerStack
from motionbridge.core.model import Animation

T, J = 60, 3


def base_anim():
    rot = np.tile(np.array([1.0, 0, 0, 0]), (T, J, 1))
    t = np.arange(T)
    for j in range(J):
        rot[:, j] = Q.from_axis_angle(np.array([0, 1.0, 0]), np.radians(0.5 * t))       # 천천히 도는 기본 동작
    return Animation("a", 30.0, rot, np.zeros((T, J, 3)))


def with_layer(base, j, noise):
    st = LayerStack()
    lay = st.add("L")
    lay.fade = 0
    for f, deg in noise.items():
        lay.set_key(j, f, rot=Q.from_axis_angle(np.array([1.0, 0, 0]), np.radians(deg)))
    return st, lay


def ang(a, b):
    return np.degrees(Q.angle_between(a, b))


def test_blend():
    base = base_anim()
    st, lay = with_layer(base, 1, {0: 0, 19: 0, 20: 40, 21: 55, 22: 38, 23: 0, 59: 0})       # 20~22 가 튀는 구간
    below = apply_layers(base, st, upto=0)
    final = apply_layers(base, st)
    assert ang(final.rot[21, 1], base.rot[21, 1]) > 40
    n, msg = R.rewrite(lay, below, final, [1], 20, 22, "blend")
    assert n == 1, msg
    out = apply_layers(base, st)
    # 튀던 구간이 앞(19)과 뒤(23) 값 사이로 매끈하게 이어짐
    for f in (20, 21, 22):
        u = (f - 19) / 4.0
        want = Q.slerp(final.rot[19, 1], final.rot[23, 1], u)
        assert ang(out.rot[f, 1], want) < 0.3, (f, ang(out.rot[f, 1], want))
    assert ang(out.rot[19, 1], final.rot[19, 1]) < 0.1 and ang(out.rot[23, 1], final.rot[23, 1]) < 0.1, "기준 프레임은 그대로"
    assert ang(out.rot[40, 1], final.rot[40, 1]) < 0.1, "다른 프레임은 그대로"
    assert np.allclose(out.rot[:, 0], base.rot[:, 0]), "다른 본은 건드리지 않음"
    print("blend OK", msg)


def test_line_and_smooth():
    base = base_anim()
    rng = np.random.default_rng(3)
    jit = {f: float(rng.normal(0, 3.0)) for f in range(10, 40)}
    st, lay = with_layer(base, 2, jit)
    below, final = apply_layers(base, st, upto=0), apply_layers(base, st)
    def wig(an):
        r = Q.make_continuous(an.rot[10:41, 2])
        return float(np.degrees(Q.angle_between(r[1:-1], Q.slerp(r[:-2], r[2:], 0.5))).mean())
    w0 = wig(final)
    n, _ = R.rewrite(lay, below, final, [2], 15, 30, "line")
    out = apply_layers(base, st)
    for f in (15, 30):
        assert ang(out.rot[f, 2], final.rot[f, 2]) < 0.2, "직선의 양 끝은 그대로"
    mid = Q.slerp(final.rot[15, 2], final.rot[30, 2], (22 - 15) / 15.0)
    assert ang(out.rot[22, 2], mid) < 0.3
    st2, lay2 = with_layer(base, 2, jit)
    n2, msg2 = R.rewrite(lay2, apply_layers(base, st2, upto=0), apply_layers(base, st2), [2], 12, 36, "smooth", sigma=3.0)
    out2 = apply_layers(base, st2)
    assert n2 == 1 and wig(out2) < 0.5 * w0, (wig(out2), w0)
    assert ang(out2.rot[0, 2], final.rot[0, 2]) < 0.1 and ang(out2.rot[55, 2], final.rot[55, 2]) < 0.1, "구간 밖은 그대로"
    # 일직선(평평하게): 구간이 한 값으로 쭉 평평 — 시작 값 기준이면 구간 시작 프레임 값, 평균 기준이면 구간 평균. 대각선이 아니다.
    for anchor in ("start", "mean"):
        stf, layf = with_layer(base, 2, {f: float(f - 10) * 0.8 for f in range(10, 40)})        # 점점 기울어지는 곡선 (대각선 모양)
        bf_, ff_ = apply_layers(base, stf, upto=0), apply_layers(base, stf)
        nf, msgf = R.rewrite(layf, bf_, ff_, [2], 15, 30, "flat", anchor=anchor)
        of = apply_layers(base, stf)
        seg = Q.make_continuous(of.rot[15:31, 2])
        spread = float(np.degrees(Q.angle_between(seg, np.repeat(seg[:1], len(seg), axis=0))).max())
        assert nf == 1 and spread < 0.35, (anchor, spread, msgf)
        if anchor == "start":
            assert ang(of.rot[22, 2], ff_.rot[15, 2]) < 0.35, "시작 값에 맞춰 평평"
        else:
            rr = Q.make_continuous(ff_.rot[15:31, 2])
            assert ang(of.rot[22, 2], Q.normalize(rr.mean(axis=0, keepdims=True))[0]) < 0.4, "평균 값에 맞춰 평평"
        assert "일직선" in msgf
    assert R.rewrite(lay3 if False else Layer("y"), bf_, ff_, [2], 20, 20, "flat")[0] == 0, "한 프레임짜리는 평평하게 할 수 없음"
    # 가중치가 낮은 레이어에서도 보이는 값이 목표가 된다
    st3, lay3 = with_layer(base, 1, {0: 0, 20: 30, 40: 0})
    lay3.weight = 0.5
    below3, final3 = apply_layers(base, st3, upto=0), apply_layers(base, st3)
    R.rewrite(lay3, below3, final3, [1], 5, 30, "line")
    o3 = apply_layers(base, st3)
    mid3 = Q.slerp(final3.rot[5, 1], final3.rot[30, 1], 0.5)
    assert ang(o3.rot[17, 1], Q.slerp(final3.rot[5, 1], final3.rot[30, 1], 12 / 25.0)) < 0.5, ang(o3.rot[17, 1], mid3)
    # 편집 불가/잘못된 요청
    lay3.mute = True
    assert R.rewrite(lay3, below3, final3, [1], 5, 30, "line")[0] == 0
    assert R.rewrite(Layer("x"), below3, final3, [1], 0, 59, "blend")[0] == 0
    print("line/smooth OK")


if __name__ == "__main__":
    test_blend()
    test_line_and_smooth()
    print("test_rangeedit ALL OK")
