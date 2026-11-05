"""작은 재사용 위젯들."""
from __future__ import annotations

import traceback

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QAbstractItemView, QDoubleSpinBox, QHBoxLayout, QSlider, QTreeWidget, QTreeWidgetItem, QWidget

from ..core.pathtree import Node, build_tree
from ..core import filekind


def wrappable(text: str) -> str:
    """긴 경로가 좁은 칸에서 줄바꿈되도록 역슬래시와 슬래시 뒤에 보이지 않는 공백을 넣는다 (넣지 않으면 경로 전체가 한 덩어리라 패널이 넓어진다)."""
    return (text or "").replace("\\", "\\\u200b").replace("/", "/\u200b")


def fit_widths(root) -> None:
    """콤보 상자가 가장 긴 항목 길이만큼 넓어져 패널을 밀어내지 않게 하고, 긴 설명 글은 줄바꿈되게 한다."""
    from PySide6.QtWidgets import QComboBox, QLabel

    try:
        combos = list(root.findChildren(QComboBox))
        labels = list(root.findChildren(QLabel))
    except TypeError:  # 시험용 가짜 Qt
        return
    for cb in combos:
        cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        cb.setMinimumContentsLength(8)
    for lb in labels:
        if len(lb.text()) > 24:
            lb.setWordWrap(True)


class Worker(QThread):
    """무거운 작업(파일 읽기, Blender 호출, 내보내기)을 UI 밖에서 실행."""

    finished_ok = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int, str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.finished_ok.emit(self.fn(self))
        except Exception as e:  # noqa: BLE001 - UI 에 그대로 보여준다
            detail = "".join(traceback.format_exception_only(type(e), e)).strip()
            self.failed.emit(detail + "\n\n" + traceback.format_exc())


class NumField(QWidget):
    """슬라이더 + 숫자 입력. 값이 바뀌면 changed(float) 방출 (set_value 는 방출하지 않음)."""

    changed = Signal(float)

    def __init__(self, lo, hi, step, value, decimals=2, suffix="", slider=True, parent=None):
        super().__init__(parent)
        self.lo, self.hi, self.step = float(lo), float(hi), float(step)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.slider = None
        if slider:
            self.slider = QSlider(Qt.Orientation.Horizontal)
            lay.addWidget(self.slider, 1)
        self.spin = QDoubleSpinBox()
        self.spin.setDecimals(decimals)
        if suffix:
            self.spin.setSuffix(suffix)
        lay.addWidget(self.spin)
        self._apply_range()
        self.set_value(value)
        self.spin.valueChanged.connect(self._from_spin)
        if self.slider is not None:
            self.slider.valueChanged.connect(self._from_slider)

    def _apply_range(self):
        self.spin.setRange(self.lo, self.hi)
        self.spin.setSingleStep(self.step)
        if self.slider is not None:
            self.slider.setRange(0, max(1, int(round((self.hi - self.lo) / self.step))))

    def set_range(self, lo, hi, step):
        self.lo, self.hi, self.step = float(lo), float(hi), float(step)
        v = self.value()
        self.spin.blockSignals(True)
        if self.slider is not None:
            self.slider.blockSignals(True)
        self._apply_range()
        self.spin.blockSignals(False)
        if self.slider is not None:
            self.slider.blockSignals(False)
        self.set_value(v)

    def value(self) -> float:
        return float(self.spin.value())

    def set_value(self, v):
        v = min(max(float(v), self.lo), self.hi)
        self.spin.blockSignals(True)
        self.spin.setValue(v)
        self.spin.blockSignals(False)
        if self.slider is not None:
            self.slider.blockSignals(True)
            self.slider.setValue(int(round((v - self.lo) / self.step)))
            self.slider.blockSignals(False)

    def _from_spin(self, v):
        if self.slider is not None:
            self.slider.blockSignals(True)
            self.slider.setValue(int(round((v - self.lo) / self.step)))
            self.slider.blockSignals(False)
        self.changed.emit(float(v))

    def _from_slider(self, i):
        v = self.lo + i * self.step
        self.spin.blockSignals(True)
        self.spin.setValue(v)
        self.spin.blockSignals(False)
        self.changed.emit(float(self.spin.value()))


class PathTree(QTreeWidget):
    """경로 목록을 폴더 트리로 보여준다. 폴더는 화살표로 접고 펼치며, 폴더를 고르면 안의 파일 전부가 선택된 것으로 친다."""

    ROLE = Qt.ItemDataRole.UserRole
    AUTO_EXPAND_MAX = 40  # 이 개수 이하면 처음부터 모두 펼친다

    def __init__(self, parent=None, strip_ext: bool = False, tag_kinds: bool = False):
        super().__init__(parent)
        self.strip_ext = strip_ext
        self.tag_kinds = tag_kinds  # True 면 파일 종류 이름표([모델링 · 리깅 포함] 등)와 폴더 설명을 붙인다
        self._paths = []
        self.setHeaderHidden(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setUniformRowHeights(True)

    # -- 채우기
    def set_paths(self, paths) -> None:
        self.clear()
        self._paths = list(dict.fromkeys(paths))
        for n in build_tree(self._paths, strip_ext=self.strip_ext):
            self.addTopLevelItem(self._make(n))
        if len(self._paths) <= self.AUTO_EXPAND_MAX:
            self.expandAll()
        else:
            for i in range(self.topLevelItemCount()):
                self.topLevelItem(i).setExpanded(True)  # 최상위 폴더만 연다

    KIND_COLORS = {"model": "#2e9e6a", "anim": "#3a7bd5"}

    def _make(self, n: Node) -> QTreeWidgetItem:
        if n.is_leaf:
            text, tip = n.name, n.path
            kind = None
            if self.tag_kinds:
                tag, kind, desc = filekind.describe(n.path)
                text, tip = "%s  %s" % (tag, n.name), "%s\n%s" % (desc, n.path)
            it = QTreeWidgetItem([text])
            it.setData(0, self.ROLE, n.path)
            it.setToolTip(0, tip)
            col = self.KIND_COLORS.get(kind or "")
            if col:
                try:
                    from PySide6.QtGui import QColor
                    it.setForeground(0, QColor(col))
                except Exception:  # noqa: BLE001
                    pass
            return it
        label = n.name
        if self.tag_kinds:
            h = filekind.folder_hint(n.name)
            if h:
                label = "%s  — %s" % (n.name, h)
        it = QTreeWidgetItem(["%s  (%d)" % (label, n.count)])
        for c in n.children:
            it.addChild(self._make(c))
        return it

    # -- 읽기
    def count(self) -> int:
        return len(self._paths)

    def all_paths(self):
        return list(self._paths)

    @staticmethod
    def _leaves(it):
        p = it.data(0, PathTree.ROLE)
        if p is not None:
            return [p]
        out = []
        for i in range(it.childCount()):
            out += PathTree._leaves(it.child(i))
        return out

    def selected_paths(self):
        """선택된 파일 + 선택된 폴더 아래의 모든 파일 (중복 없이, 목록 순서대로)."""
        got = set()
        for it in self.selectedItems():
            got.update(self._leaves(it))
        return [p for p in self._paths if p in got]

    def first_path(self):
        return self._paths[0] if self._paths else None

    def set_all_expanded(self, v: bool) -> None:
        self.expandAll() if v else self.collapseAll()
