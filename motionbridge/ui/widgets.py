"""작은 재사용 위젯들."""
from __future__ import annotations

import traceback

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QSlider, QWidget


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
