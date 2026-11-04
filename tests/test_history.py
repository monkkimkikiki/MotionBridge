import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core.history import History  # noqa: E402


def test_history():
    h = History(limit=3)
    s = {"a": 1}
    h.reset(s)
    assert not h.can_undo() and h.undo(s) is None and h.redo() is None
    s = {"a": 2}
    assert h.commit(s) and h.can_undo()
    assert not h.commit({"a": 2}), "같은 상태는 기록하지 않음"
    s["a"] = 3  # 확정 전에 외부에서 값이 바뀌어도 기록은 깨지지 않는다 (deepcopy)
    assert h.undo(s) == {"a": 2}, "확정하지 않은 변경 3 은 먼저 확정되고 한 단계 되돌림"
    assert h.undo({"a": 2}) == {"a": 1}
    assert h.undo({"a": 1}) is None
    assert h.redo() == {"a": 2} and h.redo() == {"a": 3} and h.redo() is None
    # 새 편집은 다시 실행 기록을 지운다
    assert h.undo({"a": 3}) == {"a": 2}
    assert h.commit({"a": 9}) and not h.can_redo()
    # 한도
    for i in range(10):
        h.commit({"a": 100 + i})
    assert len(h.undo_stack) == 3
    h.reset({"x": 1})
    assert not h.can_undo() and not h.can_redo()
    print("history OK")


if __name__ == "__main__":
    test_history()
    print("test_history ALL OK")
