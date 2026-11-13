"""작은 재사용 위젯들."""
from __future__ import annotations

import threading
import traceback

from PySide6.QtCore import QEvent, QObject, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import QAbstractSlider, QApplication, QMenu, QPlainTextEdit
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QProgressBar, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QSlider, QSpinBox, QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import animops
from ..core.pathtree import Node, build_tree
from ..core import filekind
from .uikit import HelpIcon


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
            if getattr(e, "code", ""):        # 추출 도우미가 원인 코드를 알려 준 오류: 원인과 할 일을 먼저 보여 준다
                try:
                    from ..core import extractor as _ex
                    detail = _ex.explain(e)
                except Exception:  # noqa: BLE001
                    pass
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
            self.slider.setProperty("noWheel", True)       # 패널을 스크롤하다 파란 슬라이더 값이 바뀌지 않게 (WheelGuard)
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

    # -- 뷰포트로 끌어다 놓기 (추출 탭의 '에셋 찾기 / 추출' 목록): 놓으면 그 에셋을 바로 추출해 뷰포트에 연다
    ASSET_MIME = "application/x-motionbridge-asset"
    drag_kind = None  # 종류("anim"|"mesh")를 돌려주는 함수. None 이면 끌기 꺼짐

    def enable_asset_drag(self, kind_fn) -> None:
        self.drag_kind = kind_fn
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)

    def drag_payload(self, items) -> dict:
        """끌려 가는 항목(파일/폴더) -> {"kind", "paths"} (폴더는 안의 파일 전부, 중복 없이 목록 순서대로)."""
        got = set()
        for it in items:
            got.update(self._leaves(it))
        kind = "anim"
        if callable(self.drag_kind):
            kind = "anim" if self.drag_kind() == "anim" else "mesh"
        return {"kind": kind, "paths": [p for p in self._paths if p in got]}

    def mimeData(self, items):  # noqa: N802
        import json

        from PySide6.QtCore import QMimeData

        md = QMimeData()
        if callable(self.drag_kind):
            md.setData(self.ASSET_MIME, json.dumps(self.drag_payload(items)).encode("utf-8"))
        return md

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


class CollapsibleBox(QGroupBox):
    """제목 줄을 누르면 접고 펴는 그룹 박스 (제목 왼쪽에 ▼ 펼침 / ▶ 접힘 화살표).

    - 제목 줄(맨 위 한 줄)을 누르면 접히고, 다시 누르면 펴집니다. 제목 줄에서 오른쪽 클릭하면 이 창의 섹션을 모두 접기/펴기.
    - 접힘 상태는 제목을 열쇠로 cfg["collapsed"] 에 저장하고 다음 실행 때 복원합니다 (bind() 로 연결).
    - 접으면 안의 위젯을 숨기고 높이를 제목 줄만큼으로 줄여 남는 자리를 돌려줍니다. 원래 숨겨져 있던 위젯은 펼칠 때 계속 숨김.
    """

    store = None        # {제목: True}  — MainWindow 가 cfg["collapsed"] 를 연결
    on_change = None    # 상태가 바뀔 때 부르는 함수(저장용)
    OPEN, CLOSED = "\u25bc ", "\u25b6 "

    @classmethod
    def bind(cls, store, on_change=None):
        cls.store, cls.on_change = store, on_change

    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self._base = title
        self._collapsed = False
        self._restored = False
        self._hidden_before = []
        self.setMouseTracking(True)
        self._refresh_title()

    # --- 제목 -------------------------------------------------------------
    def setTitle(self, title):       # noqa: N802 (Qt 이름)
        self._base = title
        self._refresh_title()

    def title(self):
        return self._base

    def _refresh_title(self):
        text = (self.CLOSED if self._collapsed else self.OPEN) + self._base
        try:
            QGroupBox.setTitle(self, text)
        except (TypeError, AttributeError):   # 시험용 가짜 Qt
            self._shown_title = text

    # --- 접기/펴기 ----------------------------------------------------------
    def header_height(self) -> int:
        return int(self.fontMetrics().height() + 12)

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, v: bool, save: bool = True) -> None:
        v = bool(v)
        if v == self._collapsed:
            return
        self._collapsed = v
        kids = [w for w in self.children() if isinstance(w, QWidget)]
        if v:
            self._hidden_before = [w for w in kids if w.isHidden()]
            for w in kids:
                w.setVisible(False)
            self.setMaximumHeight(self.header_height())
        else:
            for w in kids:
                if w not in self._hidden_before:
                    w.setVisible(True)
            self._hidden_before = []
            self.setMaximumHeight(16777215)
        self._refresh_title()
        if save and CollapsibleBox.store is not None:
            if v:
                CollapsibleBox.store[self._base] = True
            else:
                CollapsibleBox.store.pop(self._base, None)
            if CollapsibleBox.on_change:
                try:
                    CollapsibleBox.on_change()
                except Exception:
                    pass

    def toggle(self) -> None:
        self.set_collapsed(not self._collapsed)

    def _in_header(self, y) -> bool:
        return y <= self.header_height()

    def showEvent(self, e):          # noqa: N802
        if not self._restored:       # 안의 위젯이 다 들어간 뒤(처음 보일 때) 저장된 상태를 적용
            self._restored = True
            if CollapsibleBox.store and CollapsibleBox.store.get(self._base):
                self.set_collapsed(True, save=False)
        super().showEvent(e)

    def mousePressEvent(self, e):    # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and self._in_header(e.position().y()):
            self.toggle()
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):     # noqa: N802
        self.setCursor(Qt.CursorShape.PointingHandCursor if self._in_header(e.position().y()) else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(e)

    def contextMenuEvent(self, e):   # noqa: N802
        if not self._in_header(e.pos().y()):
            super().contextMenuEvent(e)
            return
        m = QMenu(self)
        a1 = m.addAction("이 창의 섹션 모두 접기")
        a2 = m.addAction("이 창의 섹션 모두 펴기")
        act = m.exec(e.globalPos())
        if act in (a1, a2):
            for b in self.window().findChildren(CollapsibleBox):
                b.set_collapsed(act is a1)


class EditPanel(CollapsibleBox):
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
        hi = HelpIcon("모션 전체 길이(재생 시간)를 바꿉니다.\n· 속도 배율: 2 = 2배 빠르게(길이 절반), 0.5 = 2배 느리게\n· 프레임 수 / 길이(초): 클립 전체를 그 길이에 맞춰 늘리거나 줄임\n"
                      "뷰어 아래 '프레임 편집' 막대로 구간을 직접 자르고 늘리는 것과 같은 일을, 여기서는 숫자로 한꺼번에(추출할 때 자동) 하는 것입니다.")
        g.addWidget(hi, r, 3)
        self._hidden += [self.value, hi]
        r += 1
        g.addWidget(QLabel("fps"), r, 0)
        self.fps = QDoubleSpinBox()
        self.fps.setDecimals(1)
        self.fps.setRange(0.0, 480.0)
        self.fps.setSpecialValueText("그대로")
        self.fps.setToolTip("0 = 그대로. 30 → 60 처럼 바꾸면 길이(초)는 유지하고 프레임 수만 바뀝니다.")
        g.addWidget(self.fps, r, 1)
        g.addWidget(HelpIcon("1초에 몇 장면(프레임)으로 저장할지. 그대로(0)면 원본 그대로입니다. 30 → 60 으로 바꾸면 움직임은 같고 중간 프레임만 늘어납니다."), r, 3)
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
        hi2 = HelpIcon("모션의 일부만 쓰고 싶을 때 시작~끝 프레임을 적습니다 (0부터 셈, 끝 칸 '끝까지' = 마지막까지).\n"
                       "뷰어 아래 '프레임 편집' 막대에서 구간을 표시해 자르는 것과 같은 기능입니다.")
        g.addWidget(hi2, r, 3)
        self._hidden += [self.start, self.end, hi2]
        r += 1
        g.addWidget(QLabel("루프 이음"), r, 0)
        self.loop = QSpinBox()
        self.loop.setRange(0, 999)
        self.loop.setSuffix(" 프레임")
        self.loop.setToolTip("0 = 끔. 끝 N프레임을 첫 포즈로 서서히 이어서 반복 재생이 튀지 않게 합니다(루트 이동량은 유지).")
        g.addWidget(self.loop, r, 1)
        g.addWidget(HelpIcon("반복 재생(루프)할 때 끝에서 처음으로 돌아가며 튀지 않게, 끝부분 N프레임을 첫 포즈로 서서히 이어 줍니다. 0 이면 쓰지 않습니다."), r, 3)
        r += 1
        self.compress = QCheckBox("키프레임 압축")
        self.compress.setToolTip("오차 허용치 안에서 군더더기 키를 줄입니다. GLB/JSON 은 줄어든 키만 저장하고, FBX 는 Blender 단순화로 줄이며,\nBVH/Biped/PSA 는 프레임마다 값을 쓰는 형식이라 '정리된 모션'으로 저장됩니다.")
        g.addWidget(self.compress, r, 0, 1, 3)
        g.addWidget(HelpIcon("프레임마다 저장된 값 중 거의 변하지 않는 군더더기 키를 줄여 파일을 작게 만듭니다. 아래 '허용 오차' 안에서만 줄이므로 눈에 띄게 달라지지 않습니다."), r, 3)
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
        g.addWidget(HelpIcon("키프레임 압축이 '이 정도 차이까지는 같은 값으로 봐도 된다'고 보는 한계입니다.\n· 왼쪽(°): 회전이 이만큼(도)까지 달라져도 키를 줄임\n· 오른쪽(cm): 위치가 이만큼까지 달라져도 키를 줄임\n"
                             "작을수록 원본에 가깝고 파일이 큽니다. 기본값(0.5° / 0.1cm)은 눈으로 구분하기 어려운 수준입니다."), r, 3)
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

    def __init__(self, title: str, parent=None, closable: bool = True):
        super().__init__(parent)
        self.closable = closable  # False: 취소할 수 없는 작업(파일 읽기·추출 등). 취소 버튼·X·Esc 없이 '작업 중'만 보여 준다
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(440)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint
                            | Qt.WindowType.WindowCloseButtonHint)
        self._done = False
        self._stopping = False
        self._title = title
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 14)
        v.setSpacing(10)
        self.text = QLabel(title + " …")
        self.text.setWordWrap(True)
        v.addWidget(self.text)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)  # 몇 개 중 몇 번째인지 모르면 움직이는 막대
        v.addWidget(self.bar)
        # 로딩바 아래 '자세히' 화살표: 열면 지금까지의 추론/진행 현황이 줄줄이 쌓여 보인다
        self.btn_log = QPushButton("▸ 자세히 — 어떻게 추론 중인지 보기")
        self.btn_log.setFlat(True)
        self.btn_log.setCheckable(True)
        self.btn_log.setStyleSheet("QPushButton{text-align:left;border:none;color:#8a93a6;padding:0}QPushButton:checked{color:#aab3c8}")
        self.btn_log.toggled.connect(self._toggle_log)
        v.addWidget(self.btn_log)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setMinimumHeight(150)
        self.log.setVisible(False)
        self._last_log = ""
        v.addWidget(self.log)
        self._log_line(0, 1, "작업 시작 — " + title)   # 보고가 드문 작업이라도 비어 보이지 않게
        row = QHBoxLayout()
        self.hint = QLabel("오른쪽 위 ✕ 로도 취소할 수 있어요" if closable else "작업이 끝나면 자동으로 닫혀요")
        self.hint.setEnabled(False)
        row.addWidget(self.hint, 1)
        self.btn = QPushButton("✕ 취소")
        self.btn.clicked.connect(lambda *_a: self.reject())
        self.btn.setVisible(closable)
        row.addWidget(self.btn)
        v.addLayout(row)
        self._timer = None
        if not closable:
            self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint)

    def show_later(self, ms: int = 300) -> None:
        """짧게 끝나는 작업에 창이 깜빡이지 않도록, ms 뒤에도 작업 중일 때만 보여 준다."""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._maybe_show)
        self._timer.start(int(ms))

    def _maybe_show(self) -> None:
        if not self._done:
            self.show()

    def _toggle_log(self, on: bool) -> None:
        self.btn_log.setText(("▾" if on else "▸") + " 자세히 — 어떻게 추론 중인지 보기")
        self.log.setVisible(bool(on))
        self.adjustSize()

    def _log_line(self, i: int, n: int, name: str) -> None:
        """진행 메시지를 아래 로그에 쌓는다 — 접혀 있어도 쌓여서, 나중에 열면 지난 과정이 다 보인다."""
        if not name or name == self._last_log:
            return
        self._last_log = name
        try:
            import time as _t
            head = ("[%d/%d] " % (min(i + 1, n), n)) if n > 1 else ""
            self.log.appendPlainText("%s  %s%s" % (_t.strftime("%H:%M:%S"), head, name))
            sb = self.log.verticalScrollBar()
            sb.setValue(sb.maximum())
        except Exception:  # noqa: BLE001 - 로그는 보조
            pass

    def set_progress(self, i: int, n: int, name: str, label: str = "") -> None:
        self._log_line(i, n, name)
        if n > 1:
            self.bar.setRange(0, int(n))
            self.bar.setValue(int(min(max(i, 0), n)))
            head = "%s (%d/%d)" % (label, min(i + 1, n), n) if label else "%d/%d" % (min(i + 1, n), n)
        else:
            self.bar.setRange(0, 0)
            head = label
        name = "" if name == label else name  # 같은 말이 두 번 나오지 않게
        self.text.setText((head + "\n" if head and name else head) + name)

    def set_stopping(self) -> None:
        """취소를 눌렀지만 하던 단계가 아직 끝나지 않은 상태. 창을 닫지 않고 '멈추는 중'을 분명하게 보여 준다."""
        self._stopping = True
        self.setWindowTitle(self._title + " — 멈추는 중")
        self.text.setText("⏹ 멈추는 중입니다…\n지금 처리 중이던 단계가 끝나는 대로 이 창이 닫혀요. 잠시만 기다려 주세요.")
        self.text.setStyleSheet("color:#e0a050;font-weight:bold")
        self.bar.setRange(0, 0)
        self.btn.setText("멈추는 중…")
        self.btn.setEnabled(False)
        self.hint.setText("끝나기 전에는 다른 조작을 할 수 없어요")

    @property
    def stopping(self) -> bool:
        return self._stopping

    def finish(self) -> None:
        """작업이 끝났을 때(또는 멈춤이 끝났을 때) 취소 신호 없이 닫는다."""
        self._done = True
        self._stopping = False
        if self._timer is not None:
            self._timer.stop()
        self.hide()

    def reject(self) -> None:
        if not self.closable and not self._done:  # 취소할 수 없는 작업: Esc/X 무시
            return
        if self._stopping:  # 이미 멈추는 중: 다시 취소하거나 닫지 않는다
            return
        if not self._done:
            self._done = True
            self.cancelled.emit()
            if self._stopping:  # 취소 신호를 받은 쪽이 '멈추는 중' 표시로 바꿨다 -> 닫지 않고 기다린다
                return
        super().reject()

    def closeEvent(self, ev) -> None:
        if not self.closable and not self._done:
            ev.ignore()
            return
        if self._stopping:
            ev.ignore()
            return
        self.reject()
        if self._stopping:
            ev.ignore()
        else:
            ev.accept()


class WheelGuard(QObject):
    """슬라이더(파란 선) 휠 잠금 — 켜 두면 리타겟 조절의 슬라이더 위에서 휠을 굴려도 값이 바뀌지 않고, 휠이 그대로 바깥 패널 스크롤이 된다.
    (패널을 스크롤하다 배율 같은 값이 실수로 바뀌는 것을 막는다. 슬라이더는 끌어서, 값은 옆 숫자칸에 입력해서 바꾸거나, Ctrl 을 누른 채 휠을 굴리면 된다.)
    숫자칸·콤보박스·프레임 슬라이더 등 다른 위젯은 건드리지 않는다 (NumField 의 슬라이더에만 noWheel 표시가 있다)."""

    def __init__(self, lock: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.lock = bool(lock)

    def watched(self, obj) -> bool:
        if isinstance(obj, QAbstractSlider):
            try:
                return bool(obj.property("noWheel"))
            except Exception:  # noqa: BLE001
                return False
        return False

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        try:
            if self.lock and ev.type() == QEvent.Type.Wheel and self.watched(obj):
                if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    return False                       # Ctrl+휠: 잠금을 잠깐 풀고 슬라이더 값을 바꾼다
                ev.ignore()
                par = obj.parentWidget()
                if par is not None:
                    QApplication.sendEvent(par, ev)      # 바깥 스크롤 패널이 받아 스크롤
                return True
        except Exception:  # noqa: BLE001
            pass
        return False
