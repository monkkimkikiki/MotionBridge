"""화면 곳곳에서 같이 쓰는 작은 부품: ? 도움말 아이콘 / 끌어서 크기 조절 / 접기 화살표."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QToolButton, QToolTip, QVBoxLayout, QWidget

BIG = 16777215


class HelpIcon(QLabel):
    """작은 '?' 동그라미. 마우스를 올리거나 누르면 설명이 뜬다 (긴 설명을 화면에 늘어놓지 않기 위한 것)."""

    STYLE = "QLabel{border:1px solid #8a93a6;border-radius:8px;color:#8a93a6;font-weight:700;font-size:11px}"

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__("?", parent)
        self._help = text
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

    def mousePressEvent(self, ev) -> None:  # noqa: N802
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
