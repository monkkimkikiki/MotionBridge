"""소스 모션과 타깃 모델의 크기가 다를 때 스케일 자동 맞춤."""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge.core import scalefit  # noqa: E402
from motionbridge.core.pipeline import Project  # noqa: E402
import test_pipeline as TP  # noqa: E402


def loaded(d):
    src, src2, tgt, _a, _b = TP.make_inputs(d)
    p = Project()
    p.load_source(src)
    p.load_target(tgt)
    p.automap()
    return p


def test_measure_and_apply():
    with tempfile.TemporaryDirectory() as d:
        p = loaded(d)
        rep = scalefit.measure(p, "hip")
        assert set(rep.measures) == {"hip", "height", "leg"}, rep.measures
        assert rep.chosen.key == "hip" and abs(rep.mult - 1.0) < 1e-9
        res = p.run(analyze_quality=False)
        assert abs(res.k - rep.k_auto) < 1e-6, (res.k, rep.k_auto)  # 기존 자동 스케일과 같은 값
        # 다른 기준으로 맞추면 최종 스케일이 그 기준의 비율이 된다
        for basis in ("height", "leg"):
            rep = scalefit.measure(p, basis)
            p.settings.auto_scale = True
            p.settings.scale_mult = rep.mult
            res = p.run(analyze_quality=False)
            assert abs(res.k - rep.chosen.ratio) < 1e-6, (basis, res.k, rep.chosen.ratio)
        assert "골반 높이" in rep.text() and "배율" in rep.text()
        # 합성 소스는 미터, 타깃은 cm — 비율이 약 100 이어도 그대로 맞춰지고 안내만 나온다
        rep = scalefit.measure(p, "hip")
        assert 90 < rep.chosen.ratio < 130 and any("cm↔m" in n for n in rep.notes), (rep.chosen.ratio, rep.notes)
        # 소스를 cm 로 바꾸면 비율이 ~1.07 이 되고 안내가 사라진다
        sk = p.src.skeleton
        sk.offsets *= 100.0
        sk._bind = None
        rep = scalefit.measure(p, "hip")
        assert 0.9 < rep.chosen.ratio < 1.3 and not rep.notes, (rep.chosen.ratio, rep.notes)
        # 체형/크기가 많이 다른 경우 (타깃 10배)
        p.tgt.skeleton.offsets *= 10.0
        p.tgt.skeleton._bind = None
        rep = scalefit.measure(p, "leg")
        assert rep.chosen.ratio > 8 and any("크기 차이가 큽니다" in n for n in rep.notes), (rep.chosen.ratio, rep.notes)


def test_not_ready():
    p = Project()
    rep = scalefit.measure(p)
    assert rep.chosen is None and rep.notes
    with tempfile.TemporaryDirectory() as d:
        src, _s2, tgt, _a, _b = TP.make_inputs(d)
        p.load_source(src)
        rep = scalefit.measure(p)
        assert rep.chosen is None, "타깃이 없으면 재지 않음"


if __name__ == "__main__":
    test_measure_and_apply()
    test_not_ready()
    print("scalefit 통과")
