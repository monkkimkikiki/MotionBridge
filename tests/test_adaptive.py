"""좁은 화면 버튼 줄(AdaptiveRow 계산)과 라이브러리 버튼/메뉴 글 일치."""
import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import qt_stub  # noqa: E402

qt_stub.install()
for _n in ("QtMultimedia", "QtMultimediaWidgets"):  # 가짜 Qt 에는 없는 모듈 (플레이어 대화상자가 가져온다)
    sys.modules["PySide6." + _n] = qt_stub._mod("PySide6." + _n)
from motionbridge.animref.ui import library_tab as L  # noqa: E402
from motionbridge.animref.ui import widgets as W  # noqa: E402


def test_split_icon():
    assert W.split_icon("🤖 AI 분류 실행") == ("🤖", "AI 분류 실행")
    assert W.split_icon("저장 폴더") == ("", "저장 폴더")
    assert W.split_icon("⟳") == ("", "⟳")


def test_needed_width():
    adv = lambda t: 10 * len(t)  # noqa: E731
    assert W.needed_width(["ab", "cde"], adv, pad=30, gap=8) == (20 + 38) + (30 + 38)
    assert W.needed_width([], adv) == 0


def test_labels_shared():
    src = inspect.getsource(L.LibraryTab)
    assert "LBL_FIND" in src and "LBL_MOCAP" in src and src.count("LBL_MOCAP") >= 2 and src.count("LBL_FIND") >= 2
    assert L.LBL_FIND != L.LBL_MOCAP and W.split_icon(L.LBL_FIND)[0] and W.split_icon(L.LBL_MOCAP)[0]
    assert "모션 AI" not in src and "모션 뽑기" not in src, "예전 이름이 남아 있으면 헷갈림"


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("ok", k)
