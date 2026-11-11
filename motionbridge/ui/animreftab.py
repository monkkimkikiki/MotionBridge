"""AnimRef 탭: 게임 영상 -> 모션 클립 수집기를 MotionBridge 탭 하나로 넣는다.

- 처음 이 탭을 열 때 만든다(앱 시작이 느려지지 않음). 영상 분석 구성요소(opencv 등)가 없으면 이유를 화면에 보여 준다.
- AnimRef 안의 '추출 툴로 보내기'는 같은 프로그램이므로 창구(HTTP) 없이 바로 '영상찾기' 탭으로 넘어간다.
- 무거운 AI(torch/ultralytics)는 설치 파일에 넣지 않는다. 쓸 때 AnimRef 가 필요한 부분만 불러오고, 없으면 안내를 띄운다.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ..core import animlink


class _Bridge(QObject):
    """AnimRef 작업 스레드 -> UI 스레드."""

    query = Signal(object)


class AnimRefTab(QWidget):
    def __init__(self, win) -> None:
        super().__init__()
        self.win = win
        self.inner = None  # animref.ui.main_window.MainWindow
        self.error = ""
        self._tried = False
        self._bridge = _Bridge()
        self._bridge.query.connect(self._deliver)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self.note = QLabel("이 탭을 열면 AnimRef 를 불러옵니다…")
        self.note.setWordWrap(True)
        self.note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lay.addWidget(self.note)

    # ------------------------------------------------------------------ 만들기 (처음 보일 때)
    def showEvent(self, ev) -> None:  # noqa: N802
        super().showEvent(ev)
        self.ensure()

    def ensure(self) -> bool:
        """AnimRef 화면을 만든다. 만들었으면 True. 실패하면 이유를 화면에 쓰고 False."""
        if self.inner is not None:
            return True
        if self._tried:
            return False
        self._tried = True
        try:
            from ..animref.ui import theme
            from ..animref.ui.main_window import MainWindow
        except ImportError as e:  # 분석 구성요소(opencv 등)가 없을 때
            hint = "소스로 실행 중이면 `pip install opencv-python-headless Pillow` 를 하세요." if "cv2" in str(e) or "PIL" in str(e) else "설치 파일로 설치했다면 최신 빌드로 다시 설치해 보세요."
            self._fail("AnimRef 에 필요한 구성요소를 불러오지 못했습니다.\n\n%s\n\n%s" % (e, hint))
            return False
        except Exception as e:  # noqa: BLE001
            self._fail("AnimRef 를 열지 못했습니다: %s: %s" % (type(e).__name__, e))
            return False
        try:
            mw = MainWindow(embedded=True, tool_open=self._send_to_tool)
        except Exception as e:  # noqa: BLE001
            self._fail("AnimRef 화면을 만들지 못했습니다: %s: %s" % (type(e).__name__, e))
            return False
        theme.apply_to(mw)  # 프로그램 전체가 아니라 이 탭에만 AnimRef 모양
        try:      # 밝은/어두운 테마 전환을 AnimRef 화면도 따라가게
            from . import theme as _mbtheme
            _mbtheme.track_palette(mw, theme.palette())
        except Exception:  # noqa: BLE001
            pass
        self.note.setVisible(False)
        self._lay.addWidget(mw, 1)
        self.inner = mw
        return True

    def _fail(self, text: str) -> None:
        self.error = text
        self.note.setText(text)
        self.note.setVisible(True)

    # ------------------------------------------------------------------ 추출 툴로 보내기 (같은 프로그램)
    def _send_to_tool(self, query_path: str) -> None:
        """AnimRef 작업 스레드에서 불린다. query.json 을 읽어 UI 스레드의 '영상찾기' 탭으로 넘긴다."""
        try:
            q = animlink.parse_query(query_path)
        except animlink.LinkError as e:
            raise RuntimeError("MotionBridge 가 요청을 읽지 못했어요: %s" % e)
        self._bridge.query.emit(q)

    def _deliver(self, q) -> None:
        self.win._on_link_open(q)

    # ------------------------------------------------------------------ 종료
    def can_close(self) -> bool:
        return self.inner is None or self.inner.can_close(self.win)

    def shutdown(self) -> None:
        if self.inner is not None:
            self.inner.shutdown()
