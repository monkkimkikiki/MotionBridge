"""작은 재사용 위젯들."""
from __future__ import annotations

import threading
import traceback

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QProgressBar, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QSlider, QSpinBox, QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import animops
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
        self.cancel = threading.Event()  # 취소 요청 (작업 함수가 중간중간 is_set() 으로 확인한다)

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
    on_set = None  # 목록이 새로 채워질 때 부르는 함수(paths) — 앱이 '마지막 추출 폴더'를 기억하는 데 쓴다

    def set_paths(self, paths) -> None:
        self.clear()
        self._paths = list(dict.fromkeys(paths))
        if self.on_set is not None:
            try:
                self.on_set(list(self._paths))
            except Exception:  # noqa: BLE001 (목록 표시는 이 기억 때문에 막히면 안 됨)
                pass
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


class EditPanel(QGroupBox):
    """애니메이션 가공 옵션(늘리기/줄이기, fps, 구간, 루프, 키 압축). options() 로 animops.EditOptions 를 얻는다.

    값은 cfg 에 `<prefix>_...` 로 기억한다.
    """

    KEYS = ("mode", "value", "fps", "start", "end", "loop", "compress", "rot", "pos")

    def __init__(self, title="애니메이션 가공 (늘리기·압축)", cfg=None, prefix="edit", parent=None, simple=False):
        """simple=True: 길이/구간 칸을 숨긴다(길이·구간은 뷰어 아래 '프레임 편집'에서 보면서 조절하므로)."""
        super().__init__(title, parent)
        self.cfg, self.prefix, self.simple = cfg if cfg is not None else {}, prefix, bool(simple)
        g = QGridLayout(self)
        r = 0
        self._hidden = []
        lab_len = QLabel("길이")
        self._hidden.append(lab_len)
        g.addWidget(lab_len, r, 0)
        self.mode = QComboBox()
        for text, key in (("그대로", "none"), ("속도 배율", "speed"), ("프레임 수", "frames"), ("길이(초)", "seconds")):
            self.mode.addItem(text, key)
        self.mode.setToolTip("속도 배율: 2 = 2배 빠르게(길이 절반), 0.5 = 2배 느리게.  프레임 수/길이(초): 클립 전체를 그 길이에 맞춰 늘리거나 줄입니다.")
        g.addWidget(self.mode, r, 1)
        self._hidden += [self.mode]
        self.value = QDoubleSpinBox()
        self.value.setDecimals(2)
        self.value.setRange(0.01, 99999.0)
        self.value.setValue(1.0)
        g.addWidget(self.value, r, 2)
        self._hidden += [self.value]
        r += 1
        g.addWidget(QLabel("fps"), r, 0)
        self.fps = QDoubleSpinBox()
        self.fps.setDecimals(1)
        self.fps.setRange(0.0, 480.0)
        self.fps.setSpecialValueText("그대로")
        self.fps.setToolTip("0 = 그대로. 30 → 60 처럼 바꾸면 길이(초)는 유지하고 프레임 수만 바뀝니다.")
        g.addWidget(self.fps, r, 1)
        r += 1
        lab_rng = QLabel("구간(프레임)")
        self._hidden.append(lab_rng)
        g.addWidget(lab_rng, r, 0)
        self.start = QSpinBox()
        self.start.setRange(0, 999999)
        self.end = QSpinBox()
        self.end.setRange(0, 999999)
        self.end.setSpecialValueText("끝까지")
        self.end.setToolTip("0 = 끝까지. 시작/끝 프레임은 0부터 셉니다(포함).")
        g.addWidget(self.start, r, 1)
        g.addWidget(self.end, r, 2)
        self._hidden += [self.start, self.end]
        r += 1
        g.addWidget(QLabel("루프 이음"), r, 0)
        self.loop = QSpinBox()
        self.loop.setRange(0, 999)
        self.loop.setSuffix(" 프레임")
        self.loop.setToolTip("0 = 끔. 끝 N프레임을 첫 포즈로 서서히 이어서 반복 재생이 튀지 않게 합니다(루트 이동량은 유지).")
        g.addWidget(self.loop, r, 1)
        r += 1
        self.compress = QCheckBox("키프레임 압축")
        self.compress.setToolTip("오차 허용치 안에서 군더더기 키를 줄입니다. GLB/JSON 은 줄어든 키만 저장하고, FBX 는 Blender 단순화로 줄이며,\nBVH/Biped/PSA 는 프레임마다 값을 쓰는 형식이라 '정리된 모션'으로 저장됩니다.")
        g.addWidget(self.compress, r, 0, 1, 3)
        r += 1
        g.addWidget(QLabel("허용 오차"), r, 0)
        self.rot = QDoubleSpinBox()
        self.rot.setDecimals(2)
        self.rot.setRange(0.0, 45.0)
        self.rot.setSuffix(" °")
        self.rot.setValue(0.5)
        self.pos = QDoubleSpinBox()
        self.pos.setDecimals(2)
        self.pos.setRange(0.0, 100.0)
        self.pos.setSuffix(" cm")
        self.pos.setValue(0.1)
        g.addWidget(self.rot, r, 1)
        g.addWidget(self.pos, r, 2)
        if self.simple:
            for w in self._hidden:
                w.setVisible(False)
            note = QLabel("길이·구간은 뷰어 아래 '프레임 편집'에서 보면서 조절하세요 (여기서는 fps·루프·압축만).")
            note.setWordWrap(True)
            g.addWidget(note, r + 1, 0, 1, 3)
        self.load()

    # ---- 값 <-> 설정
    def options(self) -> "animops.EditOptions":
        mode, v = self.mode.currentData() or "none", float(self.value.value())
        end = int(self.end.value())
        if self.simple:
            mode, v, end = "none", 1.0, 0
        return animops.EditOptions(
            trim_start=(0 if self.simple else int(self.start.value())), trim_end=(end if end > 0 else None),
            speed=(v if mode == "speed" else 1.0),
            frames=(int(round(v)) if mode == "frames" else None),
            seconds=(v if mode == "seconds" else None),
            fps=(float(self.fps.value()) or None),
            loop_frames=int(self.loop.value()),
            compress=bool(self.compress.isChecked()),
            rot_tol_deg=float(self.rot.value()), pos_tol_cm=float(self.pos.value()),
        )

    def save(self) -> None:
        c, p = self.cfg, self.prefix
        c[p + "_mode"] = self.mode.currentData() or "none"
        c[p + "_value"] = float(self.value.value())
        c[p + "_fps"] = float(self.fps.value())
        c[p + "_start"], c[p + "_end"], c[p + "_loop"] = int(self.start.value()), int(self.end.value()), int(self.loop.value())
        c[p + "_compress"] = bool(self.compress.isChecked())
        c[p + "_rot"], c[p + "_pos"] = float(self.rot.value()), float(self.pos.value())

    def load(self) -> None:
        c, p = self.cfg, self.prefix
        i = self.mode.findData(c.get(p + "_mode", "none"))
        self.mode.setCurrentIndex(max(i, 0))
        self.value.setValue(float(c.get(p + "_value", 1.0)))
        self.fps.setValue(float(c.get(p + "_fps", 0.0)))
        self.start.setValue(int(c.get(p + "_start", 0)))
        self.end.setValue(int(c.get(p + "_end", 0)))
        self.loop.setValue(int(c.get(p + "_loop", 0)))
        self.compress.setChecked(bool(c.get(p + "_compress", False)))
        self.rot.setValue(float(c.get(p + "_rot", 0.5)))
        self.pos.setValue(float(c.get(p + "_pos", 0.1)))


class BusyDialog(QDialog):
    """오래 걸리는 작업(캐릭터 추측 등) 동안 뜨는 작은 진행 창. 그동안 다른 조작은 못 하므로 모달이고,
    오른쪽 위 X 나 [취소] 나 Esc 가 모두 '작업 취소'다 (cancelled 신호)."""

    cancelled = Signal()

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint
                            | Qt.WindowType.WindowCloseButtonHint)
        self._done = False
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 14)
        v.setSpacing(10)
        self.text = QLabel(title + " …")
        self.text.setWordWrap(True)
        v.addWidget(self.text)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)  # 몇 개 중 몇 번째인지 모르면 움직이는 막대
        v.addWidget(self.bar)
        row = QHBoxLayout()
        self.hint = QLabel("오른쪽 위 ✕ 로도 취소할 수 있어요")
        self.hint.setEnabled(False)
        row.addWidget(self.hint, 1)
        self.btn = QPushButton("✕ 취소")
        self.btn.clicked.connect(lambda *_a: self.reject())
        row.addWidget(self.btn)
        v.addLayout(row)

    def set_progress(self, i: int, n: int, name: str, label: str = "") -> None:
        if n > 1:
            self.bar.setRange(0, int(n))
            self.bar.setValue(int(min(max(i, 0), n)))
            head = "%s (%d/%d)" % (label, min(i + 1, n), n) if label else "%d/%d" % (min(i + 1, n), n)
        else:
            self.bar.setRange(0, 0)
            head = label
        self.text.setText((head + "\n" if head else "") + name)

    def finish(self) -> None:
        """작업이 끝났을 때 취소 신호 없이 닫는다."""
        self._done = True
        self.hide()

    def reject(self) -> None:
        if not self._done:
            self._done = True
            self.cancelled.emit()
        super().reject()

    def closeEvent(self, ev) -> None:
        self.reject()
        ev.accept()
