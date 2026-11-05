"""크롬식 탭: 눌러서 전환 / 탭을 끌어내면 별도 창 / 다른 창의 탭 줄에 놓으면 합쳐짐 / 별도 창을 닫으면 메인으로 복귀.

구성
  TabHub      : 모든 탭 화면(페이지)의 '원래 순서'와 제목을 기억하고, 분리·합치기를 맡는다.
  ChromeTabs  : 탭 줄 + 화면 한 장. 메인 창에 하나, 분리된 창마다 하나씩.
  FloatingTabs: 분리된 창(최상위 창) 하나.

기존 코드는 QTabWidget 처럼 `tabs.setCurrentIndex(1)` 로 화면을 고르는데, 사용자가 탭 순서를 바꾸거나 분리해도
그대로 동작하도록 '번호'를 화면이 처음 추가된 순서(home index)로 해석한다.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtCore import QMimeData, QPoint, Qt, Signal
from PySide6.QtGui import QCursor, QDrag
from PySide6.QtWidgets import QApplication, QMenu, QStackedWidget, QTabBar, QVBoxLayout, QWidget

MIME = "application/x-motionbridge-tab"
STYLE = (
    "QTabBar::tab{background:#2b2f36;color:#aeb4c0;padding:6px 10px;border:1px solid #444a55;border-bottom:none;"
    "border-top-left-radius:8px;border-top-right-radius:8px;margin-right:2px;min-width:36px;}"
    "QTabBar::tab:selected{background:#3b4252;color:#ffffff;border-color:#5ab0ff;font-weight:bold;}"
    "QTabBar::tab:hover:!selected{background:#343a45;color:#ffffff;}"
)
TIP = "누르면 전환 · 끌어서 순서 변경 · 탭 줄 밖으로 끌어내면 별도 창 · 다른 창의 탭 줄에 놓으면 합쳐짐 · 오른쪽 클릭 메뉴"


class _Bar(QTabBar):
    """탭 줄. 직접 드래그를 처리한다 (같은 줄 안: 순서 바꾸기 / 줄 밖: 분리 / 다른 창의 줄: 합치기)."""

    def __init__(self, owner: "ChromeTabs") -> None:
        super().__init__()
        self.owner = owner
        self._press = None
        self.setMovable(False)
        self.setAcceptDrops(True)
        self.setExpanding(False)
        self.setUsesScrollButtons(False)  # 화살표 대신 탭이 줄어든다(글자는 …로 줄임)
        self.setElideMode(Qt.TextElideMode.ElideRight)
        self.setDrawBase(False)
        self.setStyleSheet(STYLE)
        self.setToolTip(TIP)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            p = e.position().toPoint()
            self._press = (self.tabAt(p), p)
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._press is not None and (e.buttons() & Qt.MouseButton.LeftButton) and self._press[0] >= 0:
            if (e.position().toPoint() - self._press[1]).manhattanLength() >= QApplication.startDragDistance() * 2:
                i = self._press[0]
                self._press = None
                self.owner.start_drag(i)
                return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        self._press = None
        super().mouseReleaseEvent(e)

    def _slot(self, p: QPoint) -> int:
        i = self.tabAt(p)
        if i < 0:
            return self.count()
        return i + 1 if p.x() > self.tabRect(i).center().x() else i

    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasFormat(MIME) and ChromeTabs._drag is not None:
            e.acceptProposedAction()

    dragMoveEvent = dragEnterEvent  # noqa: N815

    def dropEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasFormat(MIME):
            self.owner.accept_drop(self._slot(e.position().toPoint()))
            e.acceptProposedAction()

    def contextMenuEvent(self, e) -> None:  # noqa: N802
        i = self.tabAt(e.pos())
        if i >= 0:
            self.owner.context_menu(i, e.globalPos())


class ChromeTabs(QWidget):
    """탭 줄 + 화면 한 장. 메인 창에 있는 것이 기존 QTabWidget 자리를 대신한다 (is_main)."""

    currentChanged = Signal(int)  # 메인에서 보이는 화면이 바뀜 (home index)
    _drag = None  # 끌고 있는 (출발 ChromeTabs, 화면)

    def __init__(self, hub: Optional["TabHub"] = None, is_main: Optional[bool] = None, parent=None) -> None:
        super().__init__(parent)
        self.hub = hub if hub is not None else TabHub()
        self.is_main = (hub is None) if is_main is None else bool(is_main)
        self.pages: List[QWidget] = []
        self._cur: Optional[QWidget] = None
        self._sync = False
        self.win: Optional["FloatingTabs"] = None  # 분리된 창이면 그 창
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.bar = _Bar(self)
        v.addWidget(self.bar)
        self.stack = QStackedWidget()
        v.addWidget(self.stack, 1)
        self.bar.currentChanged.connect(self._bar_changed)
        if self.is_main:
            self.hub.main = self
            self.hub.views.append(self)

    # ------------------------------------------------------------------ 화면 넣고 빼기
    def insert(self, page: QWidget, index: int) -> None:
        index = max(0, min(int(index), len(self.pages)))
        self.pages.insert(index, page)
        self.stack.addWidget(page)
        self._sync = True
        try:
            self.bar.insertTab(index, self.hub.titles.get(id(page), ""))
            self.bar.setTabToolTip(index, self.hub.tips.get(id(page), ""))
        finally:
            self._sync = False
        page.show()
        if self._cur is None:
            self.set_current(page)

    def remove(self, page: QWidget) -> None:
        i = self.pages.index(page)
        was_cur = self._cur is page
        self.pages.pop(i)
        self._sync = True
        try:
            self.bar.removeTab(i)
        finally:
            self._sync = False
        self.stack.removeWidget(page)
        if was_cur:
            self._cur = None
            if self.pages:
                self.set_current(self.pages[min(i, len(self.pages) - 1)])

    def set_current(self, page: QWidget) -> None:
        if page not in self.pages:
            return
        changed = page is not self._cur
        self._cur = page
        self.stack.setCurrentWidget(page)
        self._sync = True
        try:
            self.bar.setCurrentIndex(self.pages.index(page))
        finally:
            self._sync = False
        if changed and self.is_main:
            self.currentChanged.emit(self.hub.home_index(page))

    def current_page(self) -> Optional[QWidget]:
        return self._cur

    def _bar_changed(self, i: int) -> None:  # 탭을 눌렀을 때
        if self._sync or not (0 <= i < len(self.pages)):
            return
        self.set_current(self.pages[i])

    def set_title(self, page: QWidget, text: str) -> None:
        if page in self.pages:
            self.bar.setTabText(self.pages.index(page), text)

    # ------------------------------------------------------------------ 끌기 / 메뉴
    def start_drag(self, i: int) -> None:
        if not (0 <= i < len(self.pages)):
            return
        page = self.pages[i]
        if self.is_main and len(self.pages) <= 1:
            return  # 메인 창은 비워 둘 수 없다
        mime = QMimeData()
        mime.setData(MIME, b"1")
        ChromeTabs._drag = (self, page)
        drag = QDrag(self.bar)
        drag.setMimeData(mime)
        try:
            pm = self.bar.grab(self.bar.tabRect(i))
            drag.setPixmap(pm)
            drag.setHotSpot(QPoint(pm.width() // 2, pm.height() // 2))
        except Exception:  # noqa: BLE001 - 미리보기 그림이 안 되어도 끌기는 계속
            pass
        drag.exec(Qt.DropAction.MoveAction)
        if ChromeTabs._drag is not None:  # 어느 탭 줄도 받지 않았다 = 줄 밖에 놓음 -> 별도 창
            ChromeTabs._drag = None
            self.hub.detach(page, QCursor.pos())

    def accept_drop(self, index: int) -> None:
        d = ChromeTabs._drag
        ChromeTabs._drag = None
        if d is not None:
            self.hub.move(d[1], self, index)

    def context_menu(self, i: int, global_pos) -> None:
        page = self.pages[i]
        m = QMenu(self)
        if self.is_main:
            a = m.addAction("별도 창으로 분리")
            a.setEnabled(len(self.pages) > 1)
            a.triggered.connect(lambda *_a: self.hub.detach(page, None))
        else:
            a = m.addAction("메인 창으로 합치기")
            a.triggered.connect(lambda *_a: self.hub.dock(page))
        m.exec(global_pos)

    # ------------------------------------------------------------------ 기존 QTabWidget 호환 (메인 전용; 번호 = 처음 추가된 순서)
    def addTab(self, widget: QWidget, title: str, tip: str = "") -> int:  # noqa: N802
        self.hub.register(widget, title, tip or title)
        self.insert(widget, len(self.pages))
        return self.hub.home_index(widget)

    def setCurrentIndex(self, i: int) -> None:  # noqa: N802
        if 0 <= int(i) < len(self.hub.home):
            self.hub.show(self.hub.home[int(i)])

    def currentIndex(self) -> int:  # noqa: N802
        return self.hub.home_index(self._cur) if self._cur is not None else -1

    def indexOf(self, widget: QWidget) -> int:  # noqa: N802
        return self.hub.home_index(widget)

    def count(self) -> int:
        return len(self.hub.home)

    def widget(self, i: int) -> Optional[QWidget]:
        return self.hub.home[i] if 0 <= i < len(self.hub.home) else None

    def setTabText(self, i: int, text: str) -> None:  # noqa: N802
        if 0 <= i < len(self.hub.home):
            self.hub.set_title(self.hub.home[i], text)

    def tabText(self, i: int) -> str:  # noqa: N802
        return self.hub.titles.get(id(self.hub.home[i]), "") if 0 <= i < len(self.hub.home) else ""

    def setEnabled(self, on: bool) -> None:  # noqa: N802
        super().setEnabled(on)
        for w in self.hub.floats:
            w.setEnabled(on)


class FloatingTabs(QWidget):
    """분리된 창. 닫으면 안의 화면이 메인 창으로 돌아간다."""

    def __init__(self, hub: "TabHub") -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.hub = hub
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        self.view = ChromeTabs(hub, is_main=False)
        self.view.win = self
        v.addWidget(self.view)
        hub.views.append(self.view)
        self.resize(900, 700)

    def closeEvent(self, e) -> None:  # noqa: N802
        self.hub.redock_window(self)
        e.accept()


class TabHub:
    """화면들의 원래 순서/제목, 메인 탭 줄, 분리된 창 목록."""

    def __init__(self) -> None:
        self.home: List[QWidget] = []
        self.titles: Dict[int, str] = {}
        self.tips: Dict[int, str] = {}
        self.main: Optional[ChromeTabs] = None
        self.views: List[ChromeTabs] = []
        self.floats: List[FloatingTabs] = []

    # ---- 조회
    def register(self, page: QWidget, title: str, tip: str = "") -> None:
        if page not in self.home:
            self.home.append(page)
        self.titles[id(page)] = title
        self.tips[id(page)] = tip

    def home_index(self, page) -> int:
        return self.home.index(page) if page in self.home else -1

    def view_of(self, page: QWidget) -> Optional[ChromeTabs]:
        for v in self.views:
            if page in v.pages:
                return v
        return None

    def is_detached(self, page: QWidget) -> bool:
        v = self.view_of(page)
        return v is not None and not v.is_main

    def set_title(self, page: QWidget, text: str) -> None:
        self.titles[id(page)] = text
        v = self.view_of(page)
        if v is not None:
            v.set_title(page, text)

    # ---- 동작
    def show(self, page: QWidget) -> None:
        """그 화면을 앞으로. 분리된 창에 있으면 그 창을 띄운다."""
        v = self.view_of(page)
        if v is None:
            return
        v.set_current(page)
        if not v.is_main:
            w = v.win
            w.show()
            w.raise_()
            w.activateWindow()

    def detach(self, page: QWidget, global_pos) -> Optional[FloatingTabs]:
        src = self.view_of(page)
        if src is None or (src.is_main and len(src.pages) <= 1):
            return None
        src.remove(page)
        win = FloatingTabs(self)
        win.view.insert(page, 0)
        win.setWindowTitle(self.titles.get(id(page), "MotionBridge"))
        self.floats.append(win)
        if global_pos is not None:
            win.move(int(global_pos.x()) - 60, int(global_pos.y()) - 20)
        win.show()
        win.raise_()
        self._clean(src)
        return win

    def move(self, page: QWidget, dest: ChromeTabs, index: int) -> None:
        """page 를 dest 의 index 자리로 (같은 줄이면 순서 바꾸기)."""
        src = self.view_of(page)
        if src is None:
            return
        if src is dest:
            i = dest.pages.index(page)
            j = index - 1 if index > i else index
            if j == i:
                return
            dest.remove(page)
            dest.insert(page, j)
        else:
            src.remove(page)
            dest.insert(page, index)
            self._clean(src)
        dest.set_current(page)

    def dock(self, page: QWidget) -> None:
        """분리된 화면을 메인 창으로 (원래 순서에 맞는 자리로)."""
        if self.main is None:
            return
        src = self.view_of(page)
        if src is None or src is self.main:
            return
        h = self.home_index(page)
        pos = len(self.main.pages)
        for k, p in enumerate(self.main.pages):
            if self.home_index(p) > h:
                pos = k
                break
        src.remove(page)
        self.main.insert(page, pos)
        self.main.set_current(page)
        self._clean(src)

    def redock_window(self, win: FloatingTabs) -> None:
        for p in list(win.view.pages):
            self.dock(p)
        self._drop(win)

    def _clean(self, view: ChromeTabs) -> None:
        """비어 버린 분리 창은 닫는다."""
        if not view.is_main and not view.pages:
            win = view.win
            if win is not None:
                self._drop(win)
                win.hide()
                win.deleteLater()

    def _drop(self, win: FloatingTabs) -> None:
        if win in self.floats:
            self.floats.remove(win)
        if win.view in self.views:
            self.views.remove(win.view)
