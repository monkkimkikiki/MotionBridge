"""IK 구간 직접 지정 계산."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import ikranges as K


def test_ranges():
    r = K.add([], 10, 20, True, 60)
    assert r == [[10, 20, True]]
    r = K.add(r, 15, 25, False, 60)                         # 나중 지정이 이긴다: 겹친 부분만 덮어씀
    assert r == [[10, 14, True], [15, 25, False]], r
    r = K.add(r, 12, 17, True, 60)
    assert r == [[10, 17, True], [18, 25, False]], r        # 이어진 같은 종류는 합쳐짐
    assert K.add([], 50, 90, True, 60) == [[50, 59, True]], "범위 밖은 잘림"
    assert K.add([], 30, 10, False, 60) == [[10, 30, False]], "뒤집힌 값은 바로잡음"
    assert K.remove(r, 0, 60) == [[18, 25, False]] and K.remove(r, 5, 60) == r
    m = np.zeros(20, bool)
    m[3:6] = True
    m[10] = True
    assert K.runs(m) == [(3, 5), (10, 10)] and K.runs(None) == [] and K.runs(np.zeros(4, bool)) == []
    assert K.describe([(3, 5), (10, 10)]) == "4~6, 11" and K.describe([]) == "없음"
    print("ikranges OK")


if __name__ == "__main__":
    test_ranges()
    print("test_ikranges ALL OK")
