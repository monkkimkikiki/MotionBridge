"""구간 편집을 고른 축(채널)에만 적용: ry 만 일직선으로 만들면 rx·rz·위치는 그대로, 축을 안 고르면(None) 예전처럼 전체."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from motionbridge.core import curves  # noqa: E402
from motionbridge.core import quat as Q  # noqa: E402
from motionbridge.core import rangeedit as R  # noqa: E402
from motionbridge.core.layers import LayerStack, apply_layers  # noqa: E402
from motionbridge.core.model import Animation  # noqa: E402

T = 50


def make():
    t = np.arange(T)
    e = np.stack([20 * np.sin(t / 6.0), 30 * np.sin(t / 5.0 + 1.0), 15 * np.cos(t / 7.0)], axis=1)     # 도
    rot = Q.from_euler(e, "XYZ")[:, None, :]
    pos = np.stack([np.sin(t / 4.0), np.cos(t / 5.0), 0.1 * t], axis=1)[:, None, :]
    return Animation("a", 30.0, rot.copy(), pos.copy())


def run(chans):
    base = make()
    st = LayerStack()
    lay = st.add("L")
    below = apply_layers(base, st, upto=0)
    final = apply_layers(base, st)
    n, msg = R.rewrite(lay, below, final, [0], 10, 30, "flat", chans=chans)
    return base, apply_layers(base, st), n, msg


def main():
    base, out, n, msg = run(["ry"])
    assert n == 1, msg
    e0, e1 = curves.channel_values(base, 0), curves.channel_values(out, 0)
    seg = slice(10, 31)
    assert np.ptp(e1["ry"][seg]) < 0.3, ("ry 는 평평해야 함", np.ptp(e1["ry"][seg]))
    assert np.max(np.abs(e1["rx"] - e0["rx"])) < 0.3 and np.max(np.abs(e1["rz"] - e0["rz"])) < 0.3, "고르지 않은 회전 축은 그대로"
    assert np.max(np.abs(e1["px"] - e0["px"])) < 1e-3 and np.max(np.abs(e1["pz"] - e0["pz"])) < 1e-3, "위치도 그대로"
    assert "축" in msg, msg
    out_of = np.r_[0:9, 33:T]
    assert max(np.max(np.abs(e1[c][out_of] - e0[c][out_of])) for c in ("rx", "ry", "rz")) < 0.3, "고친 구간 밖(앞뒤 한 프레임 너머)은 원래 값 — 새 레이어가 끝 값을 끌고 가면 안 됨"
    base, out, n, msg = run(["px"])
    e0, e1 = curves.channel_values(base, 0), curves.channel_values(out, 0)
    assert np.ptp(e1["px"][seg]) < 1e-3 and np.max(np.abs(e1["ry"] - e0["ry"])) < 0.3, "위치 X 만"
    base, out, n, msg = run(None)
    e1 = curves.channel_values(out, 0)
    assert all(np.ptp(e1[c][seg]) < 0.3 for c in ("rx", "ry", "rz")) and np.ptp(e1["px"][seg]) < 1e-3, "축을 안 고르면 전체"
    print("test_rangeedit_axes ALL OK")


main()
