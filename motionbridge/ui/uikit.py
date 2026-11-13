"""화면 곳곳에서 같이 쓰는 작은 부품: ? 도움말 아이콘 / 끌어서 크기 조절 / 접기 화살표."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QToolButton, QToolTip, QVBoxLayout, QWidget

BIG = 16777215


class _HelpPop(QLabel):
    """클릭해서 열어 두는 도움말 말풍선 — 다시 누르거나 말풍선을 누르면 닫힌다."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.setOpenExternalLinks(True)
        self.setMaximumWidth(520)
        self.setMargin(10)
        self.setStyleSheet("QLabel{background:#20242e;color:#dde3f0;border:1px solid #5a6480;border-radius:8px;font-size:12px}")

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        self.hide()


class HelpIcon(QLabel):
    """작은 '?' 동그라미. 마우스를 올리면 짧게, '누르면' 말풍선이 떠서 유지되고 다시 누르면 닫힌다."""

    STYLE = "QLabel{border:1px solid #8a93a6;border-radius:8px;color:#8a93a6;font-weight:700;font-size:11px}"
    _open_pop: "_HelpPop" = None     # 한 번에 하나만 떠 있게

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__("?", parent)
        self._help = text
        self._pop = None
        self.setFixedSize(16, 16)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(self.STYLE)
        self.setToolTip(text)
        try:
            self.setCursor(Qt.CursorShape.WhatsThisCursor)
        except Exception:  # noqa: BLE001
            pass

    def help_text(self) -> str:
        return self._help

    def set_help(self, text: str) -> None:
        self._help = text
        self.setToolTip(text)

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        try:
            if self._pop is not None and self._pop.isVisible():     # 다시 누르면 끄기
                self._pop.hide()
                return
            if HelpIcon._open_pop is not None and HelpIcon._open_pop.isVisible():
                HelpIcon._open_pop.hide()                            # 다른 곳에 떠 있던 도움말은 닫는다
            if self._pop is None:
                self._pop = _HelpPop(self._help)
            self._pop.setText(self._help)
            self._pop.adjustSize()
            self._pop.move(ev.globalPosition().toPoint())
            self._pop.show()
            HelpIcon._open_pop = self._pop
        except Exception:  # noqa: BLE001 - 어떤 환경에서든 도움말 때문에 앱이 멈추지 않게
            try:
                QToolTip.showText(ev.globalPosition().toPoint(), self._help, self)
            except Exception:  # noqa: BLE001
                pass


def with_help(widget, text: str) -> QWidget:
    """widget 오른쪽에 ? 아이콘을 붙인 한 줄 위젯."""
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    lay.addWidget(widget, 1)
    lay.addWidget(HelpIcon(text), 0)
    return box


def label_with_help(text: str, help_text: str) -> QWidget:
    return with_help(QLabel(text), help_text)


def clamp_height(start: int, dy: int, lo: int, hi: int = 4000) -> int:
    """끌어서 바꾼 높이 (최소·최대 안으로)."""
    return int(min(max(start + dy, lo), hi))


class _Grip(QLabel):
    def __init__(self, owner: "Resizable") -> None:
        super().__init__("⋯")          # ⋯ — 아래쪽 손잡이
        self.owner = owner
        self._y0 = None
        self._h0 = 0
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedHeight(10)
        self.setStyleSheet("QLabel{color:#8a93a6;background:transparent}")
        self.setToolTip("끌어서 이 상자의 높이를 조절 (두 번 누르면 원래대로)")
        try:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        except Exception:  # noqa: BLE001
            pass

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        self._y0 = int(ev.globalPosition().y())
        self._h0 = self.owner.inner_height()

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if self._y0 is not None:
            self.owner.set_height(clamp_height(self._h0, int(ev.globalPosition().y()) - self._y0, self.owner.min_h))

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        self._y0 = None

    def mouseDoubleClickEvent(self, ev) -> None:  # noqa: N802
        self.owner.reset()


class Resizable(QWidget):
    """inner(목록·표·글상자 등) 아래에 손잡이를 달아, 끌어서 높이를 바꾸게 한다. 높이는 cfg 에 저장돼 다음 실행에도 유지."""

    store = None        # {키: 높이}  — MainWindow 가 cfg["sizes"] 를 연결
    on_change = None

    @classmethod
    def bind(cls, store, on_change=None) -> None:
        cls.store, cls.on_change = store, on_change

    def __init__(self, inner, key: str, min_h: int = 60, parent=None) -> None:
        super().__init__(parent)
        self.inner, self.key, self.min_h = inner, key, int(min_h)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(inner, 1)
        self.grip = _Grip(self)
        lay.addWidget(self.grip, 0)
        h = (Resizable.store or {}).get(key)
        if isinstance(h, (int, float)) and h >= self.min_h:
            self.set_height(int(h), save=False)

    def inner_height(self) -> int:
        try:
            return int(self.inner.height())
        except Exception:  # noqa: BLE001
            return self.min_h

    def set_height(self, h: int, save: bool = True) -> None:
        h = max(int(h), self.min_h)
        self.inner.setMinimumHeight(h)
        self.inner.setMaximumHeight(h)
        if save and Resizable.store is not None:
            Resizable.store[self.key] = h
            self._changed()

    def reset(self) -> None:
        self.inner.setMinimumHeight(self.min_h)
        self.inner.setMaximumHeight(BIG)
        if Resizable.store is not None and Resizable.store.pop(self.key, None) is not None:
            self._changed()

    def _changed(self) -> None:
        if Resizable.on_change:
            try:
                Resizable.on_change()
            except Exception:  # noqa: BLE001
                pass


def arrow_button(text: str, tip: str, width: int = 14) -> QToolButton:
    """접기/펴기용 가는 세로 화살표 버튼."""
    b = QToolButton()
    b.setText(text)
    b.setToolTip(tip)
    b.setFixedWidth(width)
    b.setAutoRaise(True)
    try:
        b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
    except Exception:  # noqa: BLE001
        pass
    return b


class ViewportOverlay(QWidget):
    """뷰포트 아래쪽에 겹쳐 놓는 도구 줄 (본 편집 버튼·좌표계·초기화 등). 뷰포트 크기가 바뀌면 따라 붙는다.
    뷰포트는 테마와 상관없이 어두운 바탕이므로 이 줄도 항상 어두운 모양을 유지한다 (mbKeepDark)."""

    def __init__(self, host) -> None:
        super().__init__(host)
        self.host = host
        self.setProperty("mbKeepDark", True)
        try:
            self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            self.setStyleSheet("ViewportOverlay{background:rgba(18,20,26,225);border-top:1px solid rgba(255,255,255,45)}")
            from . import theme
            self.setPalette(theme.dark_palette())
            host.installEventFilter(self)
        except Exception:  # noqa: BLE001
            pass
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(6, 3, 6, 3)
        self.box.setSpacing(2)

    def add_row(self, layout) -> None:
        self.box.addLayout(layout)

    def finish(self) -> None:
        """버튼/체크박스가 키보드 초점을 가져가지 않게 (뷰포트의 Q/W/E/R·K 단축키가 계속 듣도록) 하고 자리를 잡는다."""
        try:
            from PySide6.QtWidgets import QAbstractButton
            for w in self.findChildren(QAbstractButton):
                w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        except Exception:  # noqa: BLE001
            pass
        self.place()

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        try:
            if obj is self.host and ev.type() in (QEvent.Type.Resize, QEvent.Type.Show):
                self.place()
        except Exception:  # noqa: BLE001
            pass
        return False

    def place(self) -> None:
        try:
            h = int(self.sizeHint().height())
            self.setGeometry(0, max(int(self.host.height()) - h, 0), int(self.host.width()), h)
            self.raise_()
        except Exception:  # noqa: BLE001
            pass
