"""처음 쓰는 사람용 안내 단계 계산."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import guide as G


def state(g):
    return {k: s for k, _n, s in g.states}


def test_steps():
    g = G.compute(False, False, False)
    assert g.step == "motion" and g.action == "open_source" and state(g)["motion"] == "now" and state(g)["export"] == "todo"
    g = G.compute(True, False, False)
    assert g.step == "model" and g.action == "open_target" and state(g)["motion"] == "done" and state(g)["model"] == "now"
    g = G.compute(True, True, False)
    assert g.step == "check" and g.action == "goto_mapping" and "맞춰" in g.hint
    g = G.compute(True, True, True, n_issues=3)
    assert g.step == "check" and g.action == "goto_export" and "3군데" in g.hint and state(g)["model"] == "done" and state(g)["check"] == "now"
    g = G.compute(True, True, True, exported=True)
    assert g.step == "export" and g.action == "open_output" and all(v == "done" for v in state(g).values()), state(g)
    assert [k for k, _n, _s in G.compute(False, False, False).states] == ["motion", "model", "check", "export"]
    print("guide steps OK")


if __name__ == "__main__":
    test_steps()
    print("test_guide ALL OK")
