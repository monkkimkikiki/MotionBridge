"""프레임 조각 편집(core/frameedit.py): 영상 편집기식 구간 속도 / 자르기 / 삭제 / 복제 / 순서 / 정지."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge.core import frameedit as F  # noqa: E402
from motionbridge.core.model import Animation  # noqa: E402
from motionbridge.core import quat as Q  # noqa: E402


def ramp_anim(T=61):
    """x 위치가 프레임 번호와 같은 모션 -> 결과의 위치를 읽으면 어느 원본 프레임인지 바로 안다."""
    rot = Q.identity((T, 2))
    pos = np.zeros((T, 2, 3))
    pos[:, 0, 0] = np.arange(T)
    return Animation("r", 30.0, rot, pos)


def srcs(an, e):
    return F.render(an, e).pos[:, 0, 0]


def test_untouched_is_identity():
    an = ramp_anim()
    e = F.FrameEdit(an.n_frames)
    assert e.is_untouched() and e.total() == 61
    assert F.render(an, e) is an
    assert np.allclose(e.positions(), np.arange(61))


def test_speed_range():
    an = ramp_anim()
    e = F.FrameEdit(61)
    r = e.set_speed(20, 40, 2.0)           # 20~40 프레임 구간을 2배 빠르게
    assert r == (20, 30), r
    assert e.total() == 51
    x = srcs(an, e)
    assert x[0] == 0 and x[20] == 20 and x[30] == 40 and x[-1] == 60
    assert np.all(np.diff(x) > 0)
    e.undo()
    assert e.is_untouched() and e.total() == 61
    e.redo()
    assert e.total() == 51
    # 느리게 (0.5배) 도 구간 안에서 길이만 늘어난다
    e2 = F.FrameEdit(61)
    e2.set_speed(10, 20, 0.5)
    assert e2.total() == 71 and np.isclose(srcs(an, e2)[30], 20)


def test_speed_clamped_and_edges():
    e = F.FrameEdit(61)
    e.set_speed(0, 60, 100)               # 상한 8배
    assert e.total() == int(round(60 / 8)) + 1
    e = F.FrameEdit(61)
    assert e.set_speed(30, 30, 2) is None  # 빈 구간
    assert e.is_untouched()
    assert e.set_speed(10, 999, 2) is None


def test_split_delete_duplicate_move():
    an = ramp_anim()
    e = F.FrameEdit(61)
    assert e.split(30) and not e.split(30) and not e.split(0)
    assert len(e.pieces) == 2 and e.total() == 61
    assert np.allclose(srcs(an, e), np.arange(61))     # 나누기만으로는 결과가 같다
    e.duplicate(10)                                    # 앞 조각(0~30) 복제
    assert e.total() == 91
    x = srcs(an, e)
    assert x[30] == 0 and x[59] == 29
    e.move(10, +1)                                     # 앞 조각을 뒤로
    assert len(e.pieces) == 3
    e.undo(); e.undo(); e.undo()
    assert e.is_untouched()
    e.split(30)
    assert e.delete(40)                                # 뒤 조각 삭제
    assert e.total() == 31 and np.isclose(srcs(an, e)[-1], 30)
    assert not e.delete(5)                             # 마지막 하나는 못 지움


def test_delete_range():
    an = ramp_anim()
    e = F.FrameEdit(61)
    assert e.delete(20, 40)
    x = srcs(an, e)
    assert e.total() == 41 and x[19] == 19 and x[20] == 40 and x[-1] == 60
    assert not F.FrameEdit(61).delete(0, 60)           # 전부 지우기 금지


def test_hold():
    an = ramp_anim()
    e = F.FrameEdit(61)
    st = e.add_hold(30, 10)
    assert st == 30 and e.total() == 71
    x = srcs(an, e)
    assert np.allclose(x[30:40], 30) and x[40] == 30 and x[41] == 31
    assert e.set_hold(35, 20) and e.total() == 81
    # 정지 구간도 속도 변경 때 그대로 유지
    e.set_speed(0, 10, 2.0)
    assert np.allclose(srcs(an, e)[-1], 60)


def test_ramp_monotonic():
    an = ramp_anim(121)
    e = F.FrameEdit(121)
    r = e.ramp(20, 100, 0.5, 2.0)
    assert r is not None
    x = srcs(an, e)
    assert np.all(np.diff(x) > 0) and x[-1] == 120
    d = np.diff(x[r[0] : r[1] + 1])
    assert d[0] < d[-1]                                # 점점 빨라진다


def test_length():
    e = F.FrameEdit(61)
    r = e.set_length(10, 30, 40)
    assert r == (10, 50) and e.total() == 61 - 20 + 40


def test_layout_and_lookup():
    e = F.FrameEdit(61)
    e.split(20); e.set_speed(20, 60, 2.0)
    lay = e.layout()
    assert lay[0][0] == 0 and lay[-1][1] == e.total()
    assert e.index_at(0) == 0 and e.index_at(e.total() - 1) == len(e.pieces) - 1
    assert abs(e.src_pos(e.total() - 1) - 60) < 1e-9
    assert "조각" in e.describe()


def test_reset():
    e = F.FrameEdit(61)
    e.set_speed(10, 20, 3)
    assert e.reset() and e.is_untouched()
    assert e.undo() and not e.is_untouched()


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("ok", k)
