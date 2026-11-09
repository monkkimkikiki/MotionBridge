"""PySide6 가 없는 환경에서 UI '로직'을 시험하기 위한 가짜 Qt.

실제 Qt 의 그리기/레이아웃은 흉내 내지 않는다. 시그널/콤보/스핀/체크박스/스레드 등 앱 로직이 의존하는 동작만 구현한다.
Qt API 사용법 자체(메서드 이름·인자)가 맞는지는 이 스텁으로 확인되지 않는다.
"""
import sys
import types

SIGNAL_NAMES = {
    "valueChanged", "currentIndexChanged", "toggled", "clicked", "timeout", "itemClicked", "triggered", "textChanged",
    "stateChanged", "activated", "finished", "started", "returnPressed", "itemDoubleClicked", "itemSelectionChanged", "linkActivated", "anchorClicked", "currentChanged",
}


class _Enum:
    def __init__(self, name="enum"):
        self._name = name
        self._kids = {}

    def __getattr__(self, n):
        if n.startswith("__"):
            raise AttributeError(n)
        return self._kids.setdefault(n, _Enum(n))

    def __or__(self, o):
        return self

    __and__ = __or__

    def __bool__(self):
        return False

    def __repr__(self):
        return "<enum %s>" % self._name


class _Meta(type):
    def __getattr__(cls, n):
        if n.startswith("__"):
            raise AttributeError(n)
        v = _Enum(n)
        setattr(cls, n, v)
        return v


class _Bound:
    def __init__(self, owner):
        self.owner = owner
        self.slots = []

    def connect(self, fn):
        self.slots.append(fn)

    def disconnect(self, *a):
        self.slots.clear()

    def emit(self, *a):
        if getattr(self.owner, "_blocked", False):
            return
        for fn in list(self.slots):
            fn(*a)


class Signal:
    def __init__(self, *types):
        self.name = None

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, obj, typ=None):
        if obj is None:
            return self
        return obj.__dict__.setdefault("_sig_" + self.name, _Bound(obj))


class Stub(metaclass=_Meta):
    def __init__(self, *a, **k):
        self._blocked = False

    def __getattr__(self, n):
        if n.startswith("__") or n.startswith("_sig_"):
            raise AttributeError(n)
        if n in SIGNAL_NAMES:
            return self.__dict__.setdefault("_sig_" + n, _Bound(self))
        return lambda *a, **k: Stub()

    def blockSignals(self, b):
        old = self.__dict__.get("_blocked", False)
        self._blocked = bool(b)
        return old


def _sig(obj, name):
    return obj.__dict__.setdefault("_sig_" + name, _Bound(obj))


class QWidget(Stub):
    def __init__(self, *a, **k):
        super().__init__()
        self._enabled = True
        self._visible = True

    def setEnabled(self, v):
        self._enabled = bool(v)

    def isEnabled(self):
        return self._enabled

    def setVisible(self, v):
        self._visible = bool(v)

    def isVisible(self):
        return self._visible

    def width(self):
        return 800

    def height(self):
        return 600

    def update(self):
        pass


class QObject(Stub):
    pass


class QDialog(QWidget):
    def reject(self):  # super().reject() 를 부르는 하위 클래스용 (Stub.__getattr__ 은 super() 에는 안 통한다)
        pass

    def accept(self):
        pass


class QCheckBox(QWidget):
    def __init__(self, text="", *a):
        super().__init__()
        self._text, self._checked = text, False

    def setChecked(self, v):
        v = bool(v)
        if v != self._checked:
            self._checked = v
            _sig(self, "toggled").emit(v)

    def isChecked(self):
        return self._checked

    def text(self):
        return self._text


QRadioButton = QCheckBox


class QPushButton(QCheckBox):
    def setCheckable(self, v):
        pass

    def setText(self, t):
        self._text = t


class QLabel(QWidget):
    def __init__(self, text="", *a):
        super().__init__()
        self._text = text

    def setText(self, t):
        self._text = t

    def text(self):
        return self._text


class QLineEdit(QLabel):
    def setText(self, t):
        changed = t != self._text
        self._text = t
        if changed:
            _sig(self, "textChanged").emit(t)


class QPlainTextEdit(QWidget):
    def __init__(self, *a):
        super().__init__()
        self._t = []

    def appendPlainText(self, t):
        self._t.append(t)

    def clear(self):
        self._t = []

    def toPlainText(self):
        return "\n".join(self._t)

    def setPlainText(self, t):
        self._t = [t] if t else []


class _Num(QWidget):
    def __init__(self, *a):
        super().__init__()
        self._lo, self._hi, self._v = 0, 99, 0

    def setRange(self, lo, hi):
        self._lo, self._hi = lo, hi
        self._v = min(max(self._v, lo), hi)

    def setValue(self, v):
        v = min(max(v, self._lo), self._hi)
        if v != self._v:
            self._v = v
            _sig(self, "valueChanged").emit(v)

    def value(self):
        return self._v


class QDoubleSpinBox(_Num):
    def __init__(self, *a):
        super().__init__()
        self._lo, self._hi, self._v = 0.0, 99.99, 0.0


class QSpinBox(_Num):
    pass


class QSlider(_Num):
    def __init__(self, *a):
        super().__init__()


class QProgressBar(_Num):
    pass


class QComboBox(QWidget):
    def __init__(self, *a):
        super().__init__()
        self._items, self._cur = [], -1

    def addItem(self, text, data=None):
        self._items.append((text, data))
        if len(self._items) == 1:
            self._cur = 0
            _sig(self, "currentIndexChanged").emit(0)

    def addItems(self, texts):
        for t in texts:
            self.addItem(t)

    def clear(self):
        self._items = []
        if self._cur != -1:
            self._cur = -1
            _sig(self, "currentIndexChanged").emit(-1)

    def count(self):
        return len(self._items)

    def currentIndex(self):
        return self._cur

    def setCurrentIndex(self, i):
        if i != self._cur and -1 <= i < len(self._items):
            self._cur = i
            _sig(self, "currentIndexChanged").emit(i)

    def currentData(self):
        return self._items[self._cur][1] if 0 <= self._cur < len(self._items) else None

    def itemData(self, i):
        return self._items[i][1] if 0 <= i < len(self._items) else None

    def findData(self, d):
        for i, (_, x) in enumerate(self._items):
            if x == d:
                return i
        return -1

    def currentText(self):
        return self._items[self._cur][0] if 0 <= self._cur < len(self._items) else ""


class _Item(Stub):
    def __init__(self, text="", *a):
        super().__init__()
        self._text, self._data = text, {}

    def text(self):
        return self._text

    def setText(self, t):
        self._text = t

    def setData(self, role, v):
        self._data[id(role)] = v

    def data(self, role):
        return self._data.get(id(role))

    _hidden = False
    _owner = None

    def setHidden(self, h):
        self._hidden = bool(h)

    def isHidden(self):
        return self._hidden

    def isSelected(self):
        return self._owner is not None and self in self._owner._sel

    def setSelected(self, v):
        if self._owner is None:
            return
        if v and self not in self._owner._sel:
            self._owner._sel.append(self)
        elif not v and self in self._owner._sel:
            self._owner._sel.remove(self)


QTableWidgetItem = QListWidgetItem = _Item


class QTableWidget(QWidget):
    def __init__(self, *a):
        super().__init__()
        self._rows, self._items, self._widgets = 0, {}, {}

    def setRowCount(self, n):
        self._rows = n
        self._items, self._widgets = {}, {}

    def rowCount(self):
        return self._rows

    def setItem(self, r, c, it):
        self._items[(r, c)] = it

    def item(self, r, c):
        return self._items.get((r, c))

    def setCellWidget(self, r, c, w):
        self._widgets[(r, c)] = w

    def cellWidget(self, r, c):
        return self._widgets.get((r, c))

    def currentRow(self):
        return getattr(self, "_cur", -1)

    def removeRow(self, r):
        n = self._rows
        new_i, new_w = {}, {}
        for (i, c), v in self._items.items():
            if i != r:
                new_i[(i - (1 if i > r else 0), c)] = v
        for (i, c), v in self._widgets.items():
            if i != r:
                new_w[(i - (1 if i > r else 0), c)] = v
        self._items, self._widgets, self._rows = new_i, new_w, n - 1
        if getattr(self, "_cur", -1) >= self._rows:
            self._cur = self._rows - 1

    def selectRow(self, r):
        self._cur = r


class QListWidget(QWidget):
    def __init__(self, *a):
        super().__init__()
        self._items = []
        self._sel = []

    def addItem(self, it):
        self._items.append(it)
        try:
            it._owner = self
        except AttributeError:
            pass

    def clear(self):
        self._items = []
        self._sel = []

    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]

    def selectAll(self):
        self._sel = list(self._items)

    def selectedItems(self):
        return list(self._sel)

    def clearSelection(self):
        self._sel = []

    def selectItems(self, idx):
        self._sel = [self._items[i] for i in idx]


class QTreeWidgetItem:
    def __init__(self, texts=None):
        self._t = list(texts or [""])
        self._d = {}
        self._ch = []
        self.expanded = False
        self.tip = ""

    def setData(self, c, role, v):
        self._d[(c, role)] = v

    def data(self, c, role):
        return self._d.get((c, role))

    def setToolTip(self, c, t):
        self.tip = t

    def addChild(self, ch):
        self._ch.append(ch)

    def childCount(self):
        return len(self._ch)

    def child(self, i):
        return self._ch[i]

    def text(self, c=0):
        return self._t[c]

    def setExpanded(self, v):
        self.expanded = bool(v)


class _RootItem:
    """QTreeWidget.invisibleRootItem() 대역: addChild 가 최상위 항목 추가."""

    def __init__(self, tree):
        self._tree = tree

    def addChild(self, it):
        self._tree._top.append(it)


class QTreeWidget(QWidget):
    def __init__(self, *a):
        super().__init__()
        self._top = []
        self._sel = []
        self.cur = None

    def invisibleRootItem(self):
        return _RootItem(self)

    def setHeaderHidden(self, v):
        pass

    def setCurrentItem(self, it):
        self.cur = it
        self._sel = [it]

    def clear(self):
        self._top = []
        self._sel = []

    def addTopLevelItem(self, it):
        self._top.append(it)

    def topLevelItemCount(self):
        return len(self._top)

    def topLevelItem(self, i):
        return self._top[i]

    def _walk(self, its):
        for it in its:
            yield it
            yield from self._walk(it._ch)

    def expandAll(self):
        for it in self._walk(self._top):
            it.expanded = True

    def collapseAll(self):
        for it in self._walk(self._top):
            it.expanded = False

    def selectedItems(self):
        return list(self._sel)

    def selectAll(self):
        self._sel = list(self._top)

    def select_where(self, fn):
        self._sel = [it for it in self._walk(self._top) if fn(it)]


class QThread(QObject):
    _running = False

    def start(self):
        self._running = True
        try:
            self.run()
        finally:
            self._running = False
            self.finished.emit()  # 진짜 QThread 처럼 run 이 끝나면 finished

    def isRunning(self):
        return self._running


class QTimer(QObject):
    def __init__(self, *a):
        super().__init__()
        self.active = False

    def start(self, *a):
        self.active = True

    def stop(self):
        self.active = False

    def isActive(self):
        return self.active


class _StatusBar(Stub):
    def showMessage(self, m, *a):
        self.last = m


class QMainWindow(QWidget):
    def __init__(self, *a):
        super().__init__()
        self._sb = _StatusBar()

    def statusBar(self):
        return self._sb

    def close(self):
        pass


class QInputDialog(Stub):
    item_ret = ("", False)
    calls = []

    @staticmethod
    def getItem(parent, title, label, items, current=0, editable=False, *a, **k):
        QInputDialog.calls.append((title, label, list(items), current))
        return QInputDialog.item_ret

    text_ret = ("", False)

    @staticmethod
    def getText(parent, title, label, *a, **k):
        return QInputDialog.text_ret


class QFileDialog(Stub):
    open_ret = ("", "")
    save_ret = ("", "")
    dir_ret = ""
    open_names_ret = ([], "")

    @staticmethod
    def getOpenFileNames(*a, **k):
        return QFileDialog.open_names_ret

    @staticmethod
    def getOpenFileName(*a, **k):
        return QFileDialog.open_ret

    @staticmethod
    def getSaveFileName(*a, **k):
        return QFileDialog.save_ret

    @staticmethod
    def getExistingDirectory(*a, **k):
        return QFileDialog.dir_ret


class QMessageBox(Stub):
    calls = []
    class _B(str):
        def __or__(self, o):
            return QMessageBox._B(str(self) + "|" + str(o))
    StandardButton = type("StandardButton", (), {"Yes": _B("Yes"), "No": _B("No"), "Cancel": _B("Cancel")})

    @staticmethod
    def warning(parent, title, text, *a):
        QMessageBox.calls.append(("warning", title, text))

    @staticmethod
    def critical(parent, title, text, *a):
        QMessageBox.calls.append(("critical", title, text))

    @staticmethod
    def information(parent, title, text, *a):
        QMessageBox.calls.append(("information", title, text))

    question_yes = True

    @staticmethod
    def question(parent, title, text, *a):
        QMessageBox.calls.append(("question", title, text))
        return QMessageBox.StandardButton.Yes if QMessageBox.question_yes else QMessageBox.StandardButton.No


class QAction(QObject):
    def __init__(self, text="", *a):
        super().__init__()


class QPointF:
    def __init__(self, x=0.0, y=0.0):
        self._x, self._y = x, y

    def x(self):
        return self._x

    def y(self):
        return self._y


class QPainter(Stub):
    drawn = 0

    def __init__(self, *a):
        super().__init__()

    def drawLine(self, *a):
        QPainter.drawn += 1

    def drawEllipse(self, *a):
        QPainter.drawn += 1

    def drawPoints(self, *a):
        QPainter.drawn += 1


class QApplication(Stub):
    @staticmethod
    def instance():
        return None


_GENERIC = {}


def _mod(name):
    m = types.ModuleType(name)

    def getattr_(n):
        if n.startswith("__"):
            raise AttributeError(n)
        if n in globals() and isinstance(globals()[n], (type, _Enum)) and n not in ("Stub",):
            return globals()[n]
        if n == "Qt":
            return _QT
        if n == "Signal":
            return Signal
        return _GENERIC.setdefault(n, type(n, (QWidget,), {}))

    m.__getattr__ = getattr_
    return m


_QT = _Enum("Qt")


def install():
    root = types.ModuleType("PySide6")
    for sub in ("QtCore", "QtGui", "QtWidgets"):
        m = _mod("PySide6." + sub)
        setattr(root, sub, m)
        sys.modules["PySide6." + sub] = m
    sys.modules["PySide6"] = root
