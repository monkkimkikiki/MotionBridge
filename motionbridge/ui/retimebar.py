"""타임라인 아래 '프레임 편집' 줄: 영상 편집기(AnimRef 속도·편집)처럼 모션을 조각 블록으로 보고 편집한다.

추출한 모션은 모든 프레임이 키이므로, 키 하나하나가 아니라 '프레임 구간' 단위로 만진다.
  · 줄을 클릭/드래그 = 그 프레임으로 이동   · [시작]/[끝] = 지금 프레임을 구간 표시로 (둘 다 없으면 지금 조각 전체)
  · 속도 ×0.25 ~ ×4 / 직접 입력 = 그 구간만 느리게·빠르게   · 부드럽게 = 속도를 서서히 바꾸기(가속·감속)
  · 자르기 · 삭제 · 복제 · 앞/뒤로 옮기기 · 정지 프레임 넣기 · 되돌리기/다시하기
블록 너비 = 결과에서 차지하는 프레임 수. 파랑 = 느리게, 주황 = 빠르게, 회색 = 정지 프레임. 편집은 바로 화면과 내보내기에 적용된다.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget

from ..core import frameedit

PRESETS = [0.25, 0.5, 1.0, 2.0, 4.0]
GAP, MINW = 3, 26.0


def fmt_speed(v: float) -> str:
    return "×%g" % round(v, 2)


def piece_rgb(p) -> Tuple[int, int, int]:
    if p.hold:
        return 120, 128, 145
    sp = p.speed
    if abs(sp - 1.0) < 0.02:
        return 88, 104, 150
    if sp < 1.0:  # 느릴수록 진한 파랑
        k = min(1.0, (1.0 - sp) / 0.9)
        return int(70 - 30 * k), int(140 - 50 * k), int(235 - 20 * k)
    k = min(1.0, (sp - 1.0) / 3.0)  # 빠를수록 진한 주황
    return 235, int(170 - 60 * k), int(70 - 30 * k)


class FrameStrip(QWidget):
    """결과 모션의 조각 줄. 위치 계산(프레임 <-> x)은 화면과 무관한 일반 함수라 그대로 시험할 수 있다."""

    seek = Signal(int)            # 사용자가 줄을 눌러/끌어 프레임을 옮김
    marks_moved = Signal()        # 시작/끝 표시를 끌어서 옮김

    H = 58

    def __init__(self, getter):
        super().__init__()
        self._get = getter                    # () -> FrameEdit | None
        self.frame = 0
        self.m_in: Optional[int] = None
        self.m_out: Optional[int] = None
        self._drag: Optional[str] = None      # "play" | "in" | "out"
        self._width = None
        self.setMinimumHeight(self.H)
        self.setFixedHeight(self.H)

    # ---- 위치 계산
    def _w(self) -> float:
        return float(self._width if self._width is not None else self.width())

    def blocks(self) -> List[Tuple[float, float]]:
        """조각마다 (왼쪽 x, 너비). 너비는 결과 프레임 수에 비례(최소 너비 보장)."""
        e = self._get()
        if e is None:
            return []
        n = len(e.pieces)
        avail = max(10.0, self._w() - GAP * (n + 1))
        lens = [max(b - a, 1) for a, b in e.layout()]
        widths = [max(MINW, avail * ln / max(sum(lens), 1)) for ln in lens]
        if sum(widths) > avail:
            f = avail / sum(widths)
            widths = [w * f for w in widths]
        out, x = [], float(GAP)
        for w in widths:
            out.append((x, w))
            x += w + GAP
        return out

    def x_of(self, r: int) -> float:
        e = self._get()
        if e is None:
            return 0.0
        i = e.index_at(r)
        s, t = e.layout()[i]
        x0, w = self.blocks()[i]
        return x0 + w * (r - s + 0.5) / max(t - s, 1)

    def frame_at(self, x: float) -> int:
        e = self._get()
        if e is None:
            return 0
        bl = self.blocks()
        lay = e.layout()
        for i, (x0, w) in enumerate(bl):
            if x < x0 + w + GAP / 2.0 or i == len(bl) - 1:
                s, t = lay[i]
                f = (x - x0) / max(w, 1.0)
                return int(min(max(s + int(f * (t - s)), s), t - 1))
        return 0

    # ---- 그리기
    def paintEvent(self, _ev) -> None:
        e = self._get()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#15171c"))
        if e is None:
            p.end()
            return
        f = QFont(p.font())
        f.setPointSize(9)
        p.setFont(f)
        top, h = 6, self.H - 22
        sel = e.index_at(self.frame)
        lay = e.layout()
        for i, ((x0, w), pc) in enumerate(zip(self.blocks(), e.pieces)):
            r, g, b = piece_rgb(pc)
            p.setBrush(QColor(r, g, b))
            p.setPen(QPen(QColor("#ffffff"), 2) if i == sel else QPen(QColor(0, 0, 0, 90), 1))
            p.drawRoundedRect(int(x0), top, int(w), h, 5, 5)
            p.setPen(QColor("#ffffff"))
            txt = ("⏸ %d" % pc.n) if pc.hold else fmt_speed(pc.speed)
            p.drawText(int(x0), top, int(w), h, int(Qt.AlignmentFlag.AlignCenter), txt)
            p.setPen(QColor("#9aa3b5"))
            p.drawText(int(x0), top + h + 1, int(max(w, 44)), 14, int(Qt.AlignmentFlag.AlignLeft), "%d" % (lay[i][1] - lay[i][0]))
        if self.m_in is not None and self.m_out is not None:
            xa, xb = sorted((self.x_of(self.m_in), self.x_of(self.m_out)))
            p.fillRect(int(xa), top, max(int(xb - xa), 1), h, QColor(255, 255, 255, 50))
        for m, col in ((self.m_in, "#46c37b"), (self.m_out, "#ff6b6b")):
            if m is not None:
                p.setPen(QPen(QColor(col), 3))
                x = int(self.x_of(m))
                p.drawLine(x, top - 4, x, top + h + 4)
        p.setPen(QPen(QColor("#ffffff"), 2))
        x = int(self.x_of(self.frame))
        p.drawLine(x, 0, x, self.H - 14)
        p.end()

    # ---- 마우스: 시작/끝 표시 근처를 잡으면 표시를 끌고, 아니면 프레임 이동
    def _hit(self, x: float) -> str:
        for name, m in (("in", self.m_in), ("out", self.m_out)):
            if m is not None and abs(self.x_of(m) - x) <= 6:
                return name
        return "play"

    def press_at(self, x: float) -> None:
        self._drag = self._hit(x)
        self.drag_to(x)

    def drag_to(self, x: float) -> None:
        r = self.frame_at(x)
        if self._drag == "in":
            self.m_in = r
            self.marks_moved.emit()
        elif self._drag == "out":
            self.m_out = r
            self.marks_moved.emit()
        else:
            self.seek.emit(r)
        self.update()

    def mousePressEvent(self, ev) -> None:
        self.press_at(ev.position().x())

    def mouseMoveEvent(self, ev) -> None:
        if self._drag:
            self.drag_to(ev.position().x())

    def mouseReleaseEvent(self, _ev) -> None:
        self._drag = None


class RetimeBar(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 0)
        v.setSpacing(3)
        self.strip = FrameStrip(self._edit)
        self.strip.seek.connect(self._strip_seek)
        self.strip.marks_moved.connect(self._update_info)
        v.addWidget(self.strip)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        v.addWidget(self.info)

        r1 = QHBoxLayout()
        lab = QLabel("<b>프레임 편집</b>")
        lab.setToolTip("영상 편집기처럼 모션을 조각으로 보고 구간별로 속도를 바꾸거나 자르고 지우고 복제합니다. 모든 프레임이 키라서 프레임 단위로 편집하면 됩니다.")
        r1.addWidget(lab)
        self.btn_in = self._btn("[ 시작", lambda: self.mark_in(), "지금 프레임을 구간의 시작으로 (표시가 없으면 지금 조각 전체가 대상)")
        self.btn_out = self._btn("끝 ]", lambda: self.mark_out(), "지금 프레임을 구간의 끝으로")
        self.btn_clear = self._btn("표시 지우기", lambda: self.clear_marks(), "시작/끝 표시를 지우고 '지금 조각 전체'를 대상으로")
        r1.addWidget(self.btn_in)
        r1.addWidget(self.btn_out)
        r1.addWidget(self.btn_clear)
        r1.addSpacing(10)
        r1.addWidget(QLabel("속도"))
        self.btn_presets = []
        for sp in PRESETS:
            self.btn_presets.append(self._btn(fmt_speed(sp), lambda sp=sp: self.apply_speed(sp), "대상 구간을 %s 속도로 (1 = 원래 속도)" % fmt_speed(sp)))
            r1.addWidget(self.btn_presets[-1])
        self.speed = QDoubleSpinBox()
        self.speed.setRange(frameedit.SPEED_MIN, frameedit.SPEED_MAX)
        self.speed.setDecimals(2)
        self.speed.setSingleStep(0.05)
        self.speed.setValue(0.5)
        self.speed.setPrefix("×")
        r1.addWidget(self.speed)
        self.btn_apply = self._btn("적용", lambda: self.apply_speed(self.speed.value()), "직접 입력한 속도를 대상 구간에")
        r1.addWidget(self.btn_apply)
        r1.addStretch(1)
        v.addLayout(r1)

        r2 = QHBoxLayout()
        self.btn_split = self._btn("✂ 자르기", lambda: self.split(), "지금 프레임에서 조각을 둘로 나눕니다 (모션은 그대로, 따로 만질 수 있게)")
        self.btn_del = self._btn("🗑 삭제", lambda: self.delete(), "대상 구간을 지웁니다 (앞뒤가 이어짐)")
        self.btn_dup = self._btn("⧉ 복제", lambda: self.duplicate(), "지금 조각을 바로 뒤에 한 번 더 붙입니다")
        self.btn_left = self._btn("◀ 앞으로", lambda: self.move(-1), "지금 조각을 한 칸 앞으로")
        self.btn_right = self._btn("뒤로 ▶", lambda: self.move(1), "지금 조각을 한 칸 뒤로")
        for b in (self.btn_split, self.btn_del, self.btn_dup, self.btn_left, self.btn_right):
            r2.addWidget(b)
        r2.addSpacing(10)
        self.hold_n = QSpinBox()
        self.hold_n.setRange(1, frameedit.HOLD_MAX)
        self.hold_n.setValue(15)
        self.hold_n.setSuffix(" 프레임")
        r2.addWidget(self.hold_n)
        self.btn_hold = self._btn("⏸ 정지", lambda: self.hold(), "지금 프레임의 포즈를 입력한 만큼 멈춥니다")
        r2.addWidget(self.btn_hold)
        r2.addSpacing(10)
        r2.addWidget(QLabel("부드럽게"))
        self.ramp_a = QDoubleSpinBox()
        self.ramp_b = QDoubleSpinBox()
        for sb, val in ((self.ramp_a, 1.0), (self.ramp_b, 2.0)):
            sb.setRange(frameedit.SPEED_MIN, frameedit.SPEED_MAX)
            sb.setDecimals(2)
            sb.setSingleStep(0.1)
            sb.setPrefix("×")
            sb.setValue(val)
        self.ramp_a.setToolTip("구간 시작 속도")
        self.ramp_b.setToolTip("구간 끝 속도")
        r2.addWidget(self.ramp_a)
        r2.addWidget(QLabel("→"))
        r2.addWidget(self.ramp_b)
        self.btn_ramp = self._btn("적용", lambda: self.ramp(), "대상 구간의 속도를 시작 속도에서 끝 속도까지 서서히 바꿉니다 (가속·감속)")
        r2.addWidget(self.btn_ramp)
        r2.addStretch(1)
        self.btn_undo = self._btn("↶ 되돌리기", lambda: self.undo(), "방금 편집을 취소")
        self.btn_redo = self._btn("↷ 다시", lambda: self.redo(), "취소한 편집을 다시")
        self.btn_reset = self._btn("모두 원본으로", lambda: self.reset(), "이 클립의 편집을 전부 버립니다 (되돌리기로 복구 가능)")
        for b in (self.btn_undo, self.btn_redo, self.btn_reset):
            r2.addWidget(b)
        v.addLayout(r2)
        self._ctl = [self.btn_in, self.btn_out, self.btn_clear, self.speed, self.btn_apply, self.btn_split, self.btn_del, self.btn_dup,
                     self.btn_left, self.btn_right, self.hold_n, self.btn_hold, self.ramp_a, self.ramp_b, self.btn_ramp,
                     self.btn_undo, self.btn_redo, self.btn_reset] + self.btn_presets
        self.refresh()

    @staticmethod
    def _btn(text, fn, tip="") -> QPushButton:
        b = QPushButton(text)
        if tip:
            b.setToolTip(tip)
        b.clicked.connect(lambda *_a: fn())
        return b

    # ---- 상태
    @property
    def pr(self):
        return self.win.pr

    def _edit(self) -> Optional[frameedit.FrameEdit]:
        pr = self.pr
        if pr.src is None:
            return None
        try:
            return pr.frame_edit()
        except (IndexError, ValueError):
            return None

    def _cur(self) -> int:
        e = self._edit()
        return int(min(max(self.win.frame_slider.value(), 0), (e.total() - 1) if e else 0))

    def range(self) -> Optional[Tuple[int, int]]:
        """편집 대상 구간 (결과 프레임). 시작/끝 표시가 둘 다 있으면 그것, 아니면 지금 프레임이 속한 조각 전체."""
        e = self._edit()
        if e is None:
            return None
        a, b = self.strip.m_in, self.strip.m_out
        if a is not None and b is not None and a != b:
            return int(min(a, b)), int(max(a, b))
        i = e.index_at(self._cur())
        s, t = e.layout()[i]
        return s, s + e.pieces[i].n

    def refresh(self) -> None:
        """모션이 바뀌었을 때(불러오기, 클립 변경, 편집 후) 표시를 맞춘다."""
        e = self._edit()
        key = (id(self.pr.src), self.pr.clip)
        if key != getattr(self, "_key", None):  # 다른 파일/클립이면 구간 표시는 지운다
            self._key = key
            self.strip.m_in = self.strip.m_out = None
        if e is not None:
            t = e.total()
            for nm in ("m_in", "m_out"):
                m = getattr(self.strip, nm)
                if m is not None and m > t - 1:
                    setattr(self.strip, nm, t - 1)
        for w in self._ctl:
            w.setEnabled(e is not None)
        self.set_frame(self.win.frame_slider.value())
        self._update_info()

    def set_frame(self, f: int) -> None:
        self.strip.frame = int(f)
        self.strip.update()

    def _update_info(self) -> None:
        pr = self.pr
        e = self._edit()
        if e is None:
            self.info.setText("소스 모션을 열면 구간별 속도·자르기·삭제·복제를 할 수 있습니다.")
            return
        n0 = int(pr.src.animations[pr.clip].n_frames)
        rg = self.range()
        txt = "원본 %d → 결과 %d프레임" % (n0, e.total())
        if not e.is_untouched():
            txt += " (%s)" % e.describe()
        if rg:
            marked = self.strip.m_in is not None and self.strip.m_out is not None and self.strip.m_in != self.strip.m_out
            txt += "  |  대상: %s %d~%d프레임 (%d프레임 길이)" % ("표시한 구간" if marked else "지금 조각", rg[0] + 1, rg[1] + 1, rg[1] - rg[0])
        self.info.setText(txt)
        self.btn_undo.setEnabled(bool(e._undo))
        self.btn_redo.setEnabled(bool(e._redo))
        self.btn_reset.setEnabled(not e.is_untouched())

    # ---- 표시 / 이동
    def mark_in(self) -> None:
        self.strip.m_in = self._cur()
        if self.strip.m_out is not None and self.strip.m_out < self.strip.m_in:
            self.strip.m_out = None
        self.strip.update()
        self._update_info()

    def mark_out(self) -> None:
        self.strip.m_out = self._cur()
        if self.strip.m_in is not None and self.strip.m_in > self.strip.m_out:
            self.strip.m_in = None
        self.strip.update()
        self._update_info()

    def clear_marks(self) -> None:
        self.strip.m_in = self.strip.m_out = None
        self.strip.update()
        self._update_info()

    def _strip_seek(self, r: int) -> None:
        self.win.frame_slider.setValue(int(r))
        self.set_frame(int(r))
        self._update_info()

    # ---- 편집 (모두 바로 화면에 반영)
    def _after(self, goto: Optional[int] = None, marks: Optional[Tuple[int, int]] = None) -> None:
        if marks is not None:
            self.strip.m_in, self.strip.m_out = marks
        self.win.recompute()  # 프레임 수가 바뀌므로 즉시 반영 (refresh 도 호출됨)
        if goto is not None:
            e = self._edit()
            self.win.frame_slider.setValue(int(min(max(goto, 0), (e.total() - 1) if e else 0)))
        self.refresh()

    def _had_marks(self) -> bool:
        return self.strip.m_in is not None and self.strip.m_out is not None and self.strip.m_in != self.strip.m_out

    def apply_speed(self, sp: float) -> bool:
        e, rg = self._edit(), self.range()
        if e is None or rg is None:
            return False
        new = e.set_speed(rg[0], rg[1], sp)
        if new is None:
            return False
        self._after(goto=new[0], marks=new if self._had_marks() else (None, None))
        return True

    def ramp(self) -> bool:
        e, rg = self._edit(), self.range()
        if e is None or rg is None:
            return False
        new = e.ramp(rg[0], rg[1], self.ramp_a.value(), self.ramp_b.value())
        if new is None:
            return False
        self._after(goto=new[0], marks=new if self._had_marks() else (None, None))
        return True

    def split(self) -> bool:
        e = self._edit()
        if e is None or not e.split(self._cur()):
            return False
        self._after(goto=self._cur())
        return True

    def delete(self) -> bool:
        e, rg = self._edit(), self.range()
        if e is None or rg is None:
            return False
        if not e.delete(rg[0], rg[1]):
            return False
        self._after(goto=rg[0], marks=(None, None))
        return True

    def duplicate(self) -> bool:
        e = self._edit()
        if e is None:
            return False
        at = e.duplicate(self._cur())
        self._after(goto=at, marks=(None, None))
        return True

    def move(self, d: int) -> bool:
        e = self._edit()
        if e is None:
            return False
        cur = self._cur()
        i = e.index_at(cur)
        if not (0 <= i + d < len(e.pieces)):
            return False
        at = e.move(cur, d)
        self._after(goto=at, marks=(None, None))
        return True

    def hold(self) -> bool:
        e = self._edit()
        if e is None:
            return False
        at = e.add_hold(self._cur(), int(self.hold_n.value()))
        self._after(goto=at, marks=(None, None))
        return True

    def undo(self) -> bool:
        e = self._edit()
        if e is None or not e.undo():
            return False
        self._after(marks=(None, None))
        return True

    def redo(self) -> bool:
        e = self._edit()
        if e is None or not e.redo():
            return False
        self._after(marks=(None, None))
        return True

    def reset(self) -> bool:
        e = self._edit()
        if e is None or not e.reset():
            return False
        self._after(goto=0, marks=(None, None))
        return True
