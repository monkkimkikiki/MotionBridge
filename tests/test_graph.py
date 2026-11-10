"""곡선 그래프 좌표 계산."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.ui import graph_math as G


def test_map():
    gm = G.GraphMap(400, 200, 0, 99, -10, 30)
    for f in (0, 17.5, 99):
        assert abs(gm.f_of(gm.x_of(f)) - f) < 1e-9
    for v in (-10, 3.3, 30):
        assert abs(gm.v_of(gm.y_of(v)) - v) < 1e-9
    assert gm.y_of(30) < gm.y_of(-10)                 # 값이 클수록 화면 위
    assert abs(gm.x_of(0) - gm.left) < 1e-9 and abs(gm.x_of(99) - (gm.w - gm.right)) < 1e-9
    x = float(gm.x_of(40))
    gm.zoom_frames(0.5, x)
    assert abs(float(gm.x_of(40)) - x) < 1e-9 and abs((gm.f1 - gm.f0) - 49.5) < 1e-9     # 가리킨 프레임은 제자리, 범위는 절반
    y = float(gm.y_of(10))
    gm.zoom_values(2.0, y)
    assert abs(float(gm.y_of(10)) - y) < 1e-9 and abs((gm.v1 - gm.v0) - 80) < 1e-9
    print("graph map OK")


def test_fit_ticks_hit():
    lo, hi = G.fit_range([np.array([0, 10.0]), np.array([-5, 2.0])])
    assert lo < -5 and hi > 10
    lo, hi = G.fit_range([np.zeros(5)])
    assert hi - lo >= 1.0                              # 평평해도 보이게
    assert G.fit_range([]) == (-1.0, 1.0)
    t = G.nice_ticks(-13, 47, 5)
    assert t[0] >= -13 and t[-1] <= 47 and len(t) >= 3 and abs((t[1] - t[0]) - 20) < 1e-9 and str(t[0]) == '0.0', t
    gm = G.GraphMap(400, 200, 0, 9, 0, 10)
    pts = [(2, 3.0, "a"), (5, 8.0, "b")]
    assert G.nearest_point(gm, float(gm.x_of(5)) + 3, float(gm.y_of(8.0)) - 2, pts) == "b"
    assert G.nearest_point(gm, float(gm.x_of(3.5)), float(gm.y_of(1.0)), pts) is None
    vals = np.linspace(0, 9, 10)
    assert G.curve_hit(gm, float(gm.x_of(4)), float(gm.y_of(4.0)) + 3, vals) == 4
    assert G.curve_hit(gm, float(gm.x_of(4)), float(gm.y_of(4.0)) + 40, vals) is None
    print("graph fit/ticks/hit OK")


if __name__ == "__main__":
    test_map()
    test_fit_ticks_hit()
    print("test_graph ALL OK")
