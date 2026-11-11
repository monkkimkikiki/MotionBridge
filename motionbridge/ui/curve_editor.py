"""곡선 그래프 편집 — 고른 본의 회전(오일러 °)·위치 곡선을 보고, 키(네모)를 위아래로 끌어 보정 레이어를 고친다.

· 굵은 선 = 지금 상태의 최종 곡선, 흐린 선 = 맨 위 레이어를 적용하기 전(아래 상태) 곡선, 노란 띠 = IK 접지 구간
· 네모 = 맨 위(편집 중) 레이어의 키: 위아래로 끌면 그 프레임의 최종 값이 끈 값이 되도록 키를 고침 / 빈 곳 더블클릭 = 그 프레임에 키 추가 / 키 우클릭 = 삭제
· 곡선 위를 잡고 위아래로 끌면 그 프레임에 키가 생기며 바로 따라옴 (클릭만 하면 프레임 이동)
· Shift+끌기 = 프레임 구간 선택(파란 띠) -> 아래 버튼: 지우고 블렌드 / 일직선으로(평평하게) / 시작-끝 이어서 / 떨림 정리
· 빈 곳 클릭·끌기 = 프레임 이동, 휠 = 가로 확대/축소 (Shift+휠 = 세로), '보기 맞춤' = 전체 보기
계산은 core/curves.py, 좌표는 ui/graph_math.py 에 있고 이 파일은 그리기와 마우스 연결만 한다."""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..core import curves
from ..core.layers import apply_layers
from . import graph_math as G
from .widgets import CollapsibleBox

TITLE = "곡선 그래프 — 키를 끌어 보정 (회전 · 위치)"
COLORS = {"rx": "#ff6b6b", "ry": "#5fd68a", "rz": "#6ba8ff", "px": "#ff9d9d", "py": "#8be8ad", "pz": "#9cc4ff"}
GROUPS = (("rot", "회전 (°)", curves.ROT), ("pos", "위치", curves.POS))


class CurveCanvas(QWidget):
    scrub = Signal(int)          # 프레임 이동 요청
    key_dragged = Signal()       # 키를 끄는 중 (값이 바뀜 -> 가벼운 재계산)
    key_changed = Signal()       # 키 추가/삭제/끌기 끝 (확정)
    range_picked = Signal(int, int)   # Shift+끌기로 고른 프레임 구간 (a, b, 0부터)
    ik_moved = Signal(int, int, int)  # Ctrl+끌기로 옮기거나 늘인 IK 직접 지정 박스: (번호, 시작, 끝) 0부터
    ik_deleted = Signal(int)          # 고른 IK 박스를 Delete 키로 지움
    ik_pending_moved = Signal(int)    # Ctrl+끌기로 옮긴 'IK 시작 표시선' 프레임
    chans_changed = Signal()          # 곡선을 눌러 고른 축(채널)이 바뀜

    BOX_H = 10                   # IK 직접 지정 박스 높이(px) — 그래프 아래쪽 띠
    EDGE_PX = 6                  # 박스 가장자리를 이만큼(px) 안에서 잡으면 크기 조절

    PULL_PX = 3.0                # 곡선을 이만큼(px) 끌어야 키가 생김 (그냥 클릭은 프레임 이동)

    H = 190

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(self.H)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._size = None                     # 시험용: (w, h) 를 직접 지정
        self.chans: tuple = ()
        self.final: Dict[str, np.ndarray] = {}
        self.below: Dict[str, np.ndarray] = {}
        self.key_frames: List[int] = []
        self.contact: Optional[np.ndarray] = None
        self.layer = None
        self.anim = None
        self.j: Optional[int] = None
        self.frame = 0
        self.T = 0
        self.view = [0.0, 1.0, -1.0, 1.0]     # f0, f1, v0, v1
        self._fit_key = None
        self._mode: Optional[str] = None
        self._drag = None
        self._drag_ch = "rx"
        self._pull = None                     # 곡선 위를 눌렀지만 아직 키가 없는 상태: (채널, 프레임, 누른 y)
        self._rng_anchor = 0
        self.range: Optional[tuple] = None    # 선택한 프레임 구간 (a, b) 0부터
        self.msg = ""
        self.ik_label: Optional[str] = None   # 아래 박스가 속한 팔다리 이름
        self.ik_boxes: List[list] = []        # 직접 지정한 IK 구간 [[a, b, 켬], ...] (0부터, 끝 포함)
        self.ik_sel = -1                      # 고른 박스 번호 (Delete 로 지움)
        self.sel_chans: set = set()           # 구간 편집(일직선 등)을 적용할 축. 비어 있으면 보이는 모든 축
        self.drag_info = ""                   # 키를 끄는 동안 보여 줄 값
        self.ik_pending: Optional[tuple] = None   # (프레임, 켬) — [IK 켜기] 로 시작만 찍어 둔 표시선

    # ---- 데이터
    def set_data(self, chans, final, below, key_frames, contact, layer, anim, j, T, fit_key) -> None:
        self.chans, self.final, self.below = tuple(chans), final or {}, below or {}
        self.key_frames, self.contact = list(key_frames or []), contact
        self.layer, self.anim, self.j, self.T = layer, anim, j, int(T)
        if fit_key != self._fit_key and self._mode is None:
            self._fit_key = fit_key
            self.fit()
        self.update()

    def set_ik(self, label, boxes, pending=None) -> None:
        if label != self.ik_label or len(boxes or []) != len(self.ik_boxes):
            self.ik_sel = -1
        if self._mode and self._mode.startswith("ik"):
            return                                     # 끄는 동안에는 미리보기를 덮어쓰지 않는다
        self.ik_label, self.ik_boxes, self.ik_pending = label, [list(b) for b in (boxes or [])], pending
        self.update()

    def _box_rect_y(self):
        gm = self.geom()
        y1 = int(gm.h - gm.bottom)
        return y1 - self.BOX_H, y1

    def _box_at(self, x: float, y: float):
        """(번호, 'L'|'R'|'M') 또는 None — 띠 위에서 잡은 박스와 잡은 자리 (가장자리=크기 조절, 가운데=통째 이동)."""
        if not self.ik_boxes:
            return None
        ya, yb = self._box_rect_y()
        if not (ya - 3 <= y <= yb + 3):
            return None
        gm = self.geom()
        for i, (a, b, _on) in enumerate(self.ik_boxes):
            xa, xb = float(gm.x_of(a - 0.5)), float(gm.x_of(b + 0.5))
            if xa - 2 <= x <= xb + 2:
                if abs(x - xa) <= self.EDGE_PX and abs(x - xa) <= abs(x - xb):
                    return i, "L"
                if abs(x - xb) <= self.EDGE_PX:
                    return i, "R"
                return i, "M"
        return None

    def picked(self) -> set:
        """구간 편집이 적용될 축: 곡선을 눌러 고른 것 (고른 게 없으면 보이는 모든 축)."""
        sel = {c for c in self.sel_chans if c in self.chans}
        return sel or set(self.chans)

    def set_picked(self, chans) -> None:
        self.sel_chans = {c for c in chans if c in self.chans}
        self.chans_changed.emit()
        self.update()

    def _toggle_pick(self, ch: str, add: bool) -> None:
        cur = {c for c in self.sel_chans if c in self.chans}
        if add:
            cur ^= {ch}
        else:
            cur = {ch}
        self.set_picked(cur)

    def keyPressEvent(self, ev) -> None:
        if ev.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and 0 <= self.ik_sel < len(self.ik_boxes):
            i, self.ik_sel = self.ik_sel, -1
            self.ik_deleted.emit(int(i))
            return
        super().keyPressEvent(ev)

    def set_frame(self, f: int) -> None:
        self.frame = int(f)
        self.update()

    def fit(self) -> None:
        arrs = [self.final[c] for c in self.chans if c in self.final] + [self.below[c] for c in self.chans if c in self.below]
        v0, v1 = G.fit_range(arrs, min_span=1.0 if self.chans and self.chans[0] in curves.ROT else 0.05)
        self.view = [0.0, float(max(self.T - 1, 1)), v0, v1]
        self.update()

    def geom(self) -> G.GraphMap:
        w, h = self._size if self._size else (self.width(), self.height())
        f0, f1, v0, v1 = self.view
        return G.GraphMap(w, h, f0, f1, v0, v1)

    # ---- 점 집기
    def key_points(self):
        pts = []
        for ch in self.chans:
            arr = self.final.get(ch)
            if arr is None:
                continue
            for f in self.key_frames:
                if 0 <= f < len(arr):
                    pts.append((f, float(arr[f]), (ch, f)))
        return pts

    # ---- 마우스 (그림과 무관한 계산 함수들 — 시험에서 직접 부른다)
    def press_at(self, x: float, y: float, right: bool = False, double: bool = False, shift: bool = False, ctrl: bool = False) -> None:
        self.msg = ""
        if self.T <= 0 or not self.chans:
            return
        gm = self.geom()
        if not right and not double:
            if ctrl and self.ik_pending is not None and abs(x - float(gm.x_of(self.ik_pending[0]))) <= self.EDGE_PX:
                self._mode = "ik_pending"                           # Ctrl+끌기: 시작 표시선 옮기기
                return
            hit_box = self._box_at(x, y)
            if hit_box is not None:
                i, part = hit_box
                self.ik_sel = i
                self.update()
                if ctrl:                                            # Ctrl+끌기: 가장자리 = 크기, 가운데 = 통째 이동
                    a, b, _on = self.ik_boxes[i]
                    self._mode = "ik_" + part
                    self._ik_drag = (i, a, b, int(np.clip(round(float(gm.f_of(x))), 0, self.T - 1)))
                else:
                    self.msg = "IK 박스를 골랐습니다 — Delete = 지우기 · Ctrl+끌기 = 옮기기/늘이기"
                return
            if self.ik_sel >= 0:
                self.ik_sel = -1
        if shift and not right and not double:                       # 구간 고르기
            f = int(np.clip(round(float(gm.f_of(x))), 0, self.T - 1))
            self._mode, self._rng_anchor = "range", f
            self.range = (f, f)
            self.range_picked.emit(f, f)
            self.update()
            return
        hit = G.nearest_point(gm, x, y, self.key_points())
        why = None
        if self.layer is None:
            why = "지금은 레이어가 적용되지 않은 '원본' 상태입니다. 레이어 목록에서 레이어를 고르거나 새로 만들면 키를 끌 수 있어요."
        else:
            from ..core.layeredit import editable
            why = editable(self.layer)
        if hit is not None and not right:
            ch, f = hit
            self.scrub.emit(int(f))
            if why:
                self.msg = why
                self.update()
                return
            d = curves.begin_drag(self.layer, self.anim, self.j, f)
            if d is not None:
                self._mode, self._drag, self._drag_ch = "key", d, ch
            return
        if hit is not None and right:
            if why:
                self.msg = why
            elif self.layer.remove_key(self.j, hit[1]):
                self.key_changed.emit()
            self.update()
            return
        f = int(np.clip(round(float(gm.f_of(x))), 0, self.T - 1))
        if double:
            if why:
                self.msg = why
            elif curves.add_key(self.layer, self.j, f):
                self.key_changed.emit()
            self.update()
            return
        if not right:                                                # 곡선 위를 잡았으면: 그 축을 고르고(Ctrl = 여러 축), 끌어야 키가 생김
            for ch in self.chans:
                arr = self.final.get(ch)
                fh = G.curve_hit(gm, x, y, arr) if arr is not None else None
                if fh is not None:
                    self._toggle_pick(ch, ctrl)
                    self.scrub.emit(int(fh))
                    if not why:
                        self._mode, self._pull = "pull", (ch, int(fh), float(y))
                    return
        self._mode = "scrub"
        self.scrub.emit(f)

    def drag_to(self, x: float, y: float) -> None:
        if self._mode == "ik_pending" and self.ik_pending is not None:
            f = int(np.clip(round(float(self.geom().f_of(x))), 0, max(self.T - 1, 0)))
            self.ik_pending = (f, self.ik_pending[1])
            self.update()
            return
        if self._mode in ("ik_L", "ik_R", "ik_M") and getattr(self, "_ik_drag", None) is not None:
            i, a, b, f0 = self._ik_drag
            f = int(np.clip(round(float(self.geom().f_of(x))), 0, max(self.T - 1, 0)))
            if self._mode == "ik_L":
                na, nb = min(f, b), b
            elif self._mode == "ik_R":
                na, nb = a, max(f, a)
            else:
                d = int(np.clip(f - f0, -a, self.T - 1 - b))
                na, nb = a + d, b + d
            self.ik_boxes[i][0], self.ik_boxes[i][1] = int(na), int(nb)
            self.update()
            return
        if self._mode == "key" and self._drag is not None:
            v = float(self.geom().v_of(y))
            if curves.drag_to(self.layer, self._drag, self._drag_ch, v):
                self.drag_info = "프레임 %d · %s %.2f%s" % (self._drag.f + 1, curves.LABELS[self._drag_ch], v, "°" if self._drag_ch in curves.ROT else "")
                self.key_dragged.emit()
        elif self._mode == "range":
            f = int(np.clip(round(float(self.geom().f_of(x))), 0, max(self.T - 1, 0)))
            a, b = sorted((self._rng_anchor, f))
            self.range = (a, b)
            self.range_picked.emit(a, b)
            self.update()
        elif self._mode == "pull" and self._pull is not None:
            ch, f, y0 = self._pull
            if abs(y - y0) >= self.PULL_PX:
                if curves.add_key(self.layer, self.j, f):
                    d = curves.begin_drag(self.layer, self.anim, self.j, f)
                    if d is not None:
                        self._mode, self._drag, self._drag_ch, self._pull = "key", d, ch, None
                        self.key_changed.emit()                      # 키가 생겼으니 목록/그래프 갱신 (네모가 나타남)
                        self.drag_to(x, y)
                        return
                self._mode, self._pull = None, None
        elif self._mode == "scrub":
            self.scrub.emit(int(np.clip(round(float(self.geom().f_of(x))), 0, max(self.T - 1, 0))))

    def release(self) -> None:
        was = self._mode
        self._mode, self._drag, self._pull = None, None, None
        self.drag_info = ""
        if was == "key":
            self.key_changed.emit()
        elif was == "ik_pending" and self.ik_pending is not None:
            self.ik_pending_moved.emit(int(self.ik_pending[0]))
        elif was in ("ik_L", "ik_R", "ik_M") and getattr(self, "_ik_drag", None) is not None:
            i = self._ik_drag[0]
            self._ik_drag = None
            if 0 <= i < len(self.ik_boxes):
                a, b, _on = self.ik_boxes[i]
                self.ik_moved.emit(int(i), int(a), int(b))

    def wheel_zoom(self, delta: float, x: float, y: float, vertical: bool = False) -> None:
        factor = 0.8 if delta > 0 else 1.25
        gm = self.geom()
        (gm.zoom_values(factor, y) if vertical else gm.zoom_frames(factor, x))
        self.view = [gm.f0, gm.f1, gm.v0, gm.v1]
        self.update()

    def mousePressEvent(self, ev) -> None:
        p = ev.position()
        self.setFocus()
        self.press_at(p.x(), p.y(), right=ev.button() == Qt.MouseButton.RightButton,
                      shift=bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier),
                      ctrl=bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier))

    def mouseDoubleClickEvent(self, ev) -> None:
        p = ev.position()
        self.press_at(p.x(), p.y(), double=True)

    def mouseMoveEvent(self, ev) -> None:
        if self._mode:
            p = ev.position()
            self.drag_to(p.x(), p.y())

    def mouseReleaseEvent(self, _ev) -> None:
        self.release()

    def wheelEvent(self, ev) -> None:
        # Ctrl+휠 = 확대/축소 (Ctrl+Shift+휠 = 세로). 그냥 휠은 위쪽 스크롤 영역으로 넘겨 화면이 스크롤되게 한다 (그래프 위에서 휠을 굴리다 줌이 걸리는 사고 방지)
        if not (ev.modifiers() & Qt.KeyboardModifier.ControlModifier):
            ev.ignore()
            return
        d = ev.angleDelta().y() or ev.angleDelta().x()
        if d:
            p = ev.position()
            self.wheel_zoom(d, p.x(), p.y(), vertical=bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier))
            ev.accept()

    # ---- 그리기
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#15171c"))
        if self.T <= 0 or not self.chans or not self.final:
            p.setPen(QColor("#8a93a6"))
            p.drawText(12, 28, "본을 고르면 그 본의 곡선이 여기에 보입니다 (소스 모션과 타깃 모델이 있어야 해요).")
            p.end()
            return
        gm = self.geom()
        f = QFont(p.font())
        f.setPointSize(8)
        p.setFont(f)
        x0, x1 = int(gm.left), int(gm.w - gm.right)
        y0, y1 = int(gm.top), int(gm.h - gm.bottom)
        if self.contact is not None and len(self.contact):
            idx = np.flatnonzero(self.contact)
            if len(idx):
                runs = np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1)
                for r in runs:
                    xa, xb = int(gm.x_of(r[0] - 0.5)), int(gm.x_of(r[-1] + 0.5))
                    p.fillRect(max(xa, x0), y0, max(min(xb, x1) - max(xa, x0), 1), (y1 - y0) // 2, QColor(255, 220, 80, 46))     # 자동 접지 띠는 위쪽 절반만
        if self.range is not None:
            xa, xb = int(gm.x_of(self.range[0] - 0.5)), int(gm.x_of(self.range[1] + 0.5))
            p.fillRect(max(xa, x0), y0, max(min(xb, x1) - max(xa, x0), 2), y1 - y0, QColor(90, 150, 255, 55))
        p.setPen(QPen(QColor("#2a2f3a"), 1))
        for v in G.nice_ticks(gm.v0, gm.v1, 5):
            y = int(gm.y_of(v))
            p.drawLine(x0, y, x1, y)
            p.setPen(QColor("#7f8aa0"))
            p.drawText(2, y + 4, "%g" % round(v, 3))
            p.setPen(QPen(QColor("#2a2f3a"), 1))
        for fr in G.nice_ticks(gm.f0, gm.f1, 8):
            x = int(gm.x_of(fr))
            p.drawLine(x, y0, x, y1)
            p.setPen(QColor("#7f8aa0"))
            p.drawText(x + 2, int(gm.h) - 5, "%d" % (int(fr) + 1))
            p.setPen(QPen(QColor("#2a2f3a"), 1))
        fr_idx = np.arange(self.T)
        xs = gm.x_of(fr_idx)
        for ch in self.chans:
            col = QColor(COLORS[ch])
            if ch in self.below:
                dim = QColor(col)
                dim.setAlpha(80)
                p.setPen(QPen(dim, 1))
                ys = gm.y_of(self.below[ch])
                for i in range(self.T - 1):
                    p.drawLine(int(xs[i]), int(ys[i]), int(xs[i + 1]), int(ys[i + 1]))
            on = ch in self.picked()
            if not on:
                col = QColor(col)
                col.setAlpha(110)
            p.setPen(QPen(col, 3 if (on and self.sel_chans) else 2))
            ys = gm.y_of(self.final[ch])
            for i in range(self.T - 1):
                p.drawLine(int(xs[i]), int(ys[i]), int(xs[i + 1]), int(ys[i + 1]))
        if self.ik_boxes or self.ik_pending is not None:
            ya = y1 - self.BOX_H
            for i, (a, b, on) in enumerate(self.ik_boxes):
                xa, xb = int(gm.x_of(a - 0.5)), int(gm.x_of(b + 0.5))
                xa, xb = max(xa, x0), min(xb, x1)
                col = QColor(70, 200, 120, 190) if on else QColor(230, 90, 90, 190)
                p.fillRect(xa, ya, max(xb - xa, 3), self.BOX_H, col)
                p.setPen(QPen(QColor("#ffffff") if i == self.ik_sel else QColor(0, 0, 0, 160), 2 if i == self.ik_sel else 1))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(xa, ya, max(xb - xa, 3), self.BOX_H)
            if self.ik_pending is not None:
                xp = int(gm.x_of(self.ik_pending[0]))
                p.setPen(QPen(QColor(70, 220, 130) if self.ik_pending[1] else QColor(240, 100, 100), 2, Qt.PenStyle.DashLine))
                p.drawLine(xp, y0, xp, y1)
        xc = int(gm.x_of(self.frame))
        p.setPen(QPen(QColor("#ffffff"), 1))
        p.drawLine(xc, y0, xc, y1)
        kp = self.key_points()
        for fq, v, (ch, _f) in kp:
            xx, yy = int(gm.x_of(fq)), int(gm.y_of(v))
            p.setBrush(QColor("#ffdc50"))
            p.setPen(QPen(QColor("#000000"), 1))
            p.drawRect(xx - 4, yy - 4, 8, 8)
        if kp:                                                       # 키마다 프레임 번호와 값 (키가 많으면 번호만)
            p.setPen(QColor("#ffe98a"))
            few = len({int(f) for f, _v, _i in kp}) <= 14
            for fq, v, (ch, _f) in kp:
                xx, yy = int(gm.x_of(fq)), int(gm.y_of(v))
                p.drawText(xx + 6, yy - 6, ("%d: %.1f" % (int(fq) + 1, v)) if few else "%d" % (int(fq) + 1))
        frames = sorted({int(f) for f in self.key_frames})
        if self.layer is not None:
            head = ("키 %d개 (프레임 %s)" % (len(frames), ", ".join(str(f + 1) for f in frames[:16]) + (" …" if len(frames) > 16 else ""))) if frames else "이 본에는 키가 없습니다"
            p.setPen(QColor("#c8cfdd"))
            p.drawText(x0 + 6, y0 + 28, head)
        if self.drag_info:
            p.setPen(QColor("#ffdc50"))
            p.drawText(x0 + 6, y0 + 42, "▶ 찍는 중: " + self.drag_info)
        if self.msg:
            p.setPen(QColor("#ffdc50"))
            p.drawText(x0 + 6, y0 + 14, self.msg[:90])
        p.end()


class CurveEditor(CollapsibleBox):
    def __init__(self, app) -> None:
        super().__init__(TITLE)
        self.app = app
        self._busy = False
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 4, 6, 4)
        top = QHBoxLayout()
        self.lbl = QLabel("본을 고르세요")
        top.addWidget(self.lbl, 1)
        self.group = QComboBox()
        for key, name, _ch in GROUPS:
            self.group.addItem(name, key)
        self.group.setToolTip("회전은 부모 기준 로컬 오일러 각도(도, XYZ), 위치는 부모 기준 로컬 위치입니다")
        self.group.currentIndexChanged.connect(self._group_changed)
        top.addWidget(self.group)
        b = QPushButton("보기 맞춤")
        b.setToolTip("곡선 전체가 보이도록 확대/축소를 되돌립니다")
        b.clicked.connect(lambda _c=False: self.canvas.fit())
        top.addWidget(b)
        v.addLayout(top)
        axes = QHBoxLayout()
        axes.addWidget(QLabel("구간 편집을 적용할 축"))
        self.axis_cb = {}
        for ch in curves.CHANNELS:
            cb = QCheckBox(curves.LABELS[ch])
            cb.setChecked(True)
            cb.setToolTip("체크한 축에만 아래 버튼(지우고 블렌드 · 일직선 · 시작-끝 이어서 · 떨림 정리)이 적용됩니다. 그래프의 선을 눌러 고르거나(Ctrl+클릭 = 여러 개) 여기서 체크하세요.\n"
                          "전부 체크 = 회전·위치 전체에 적용")
            cb.setStyleSheet("QCheckBox{color:%s;font-weight:700}" % COLORS[ch])
            cb.toggled.connect(self._axis_toggled)
            self.axis_cb[ch] = cb
            axes.addWidget(cb)
        axes.addStretch(1)
        v.addLayout(axes)
        self.canvas = CurveCanvas()
        self.canvas.setToolTip("선을 잡고 위아래로 끌기 = 그 프레임에 키가 생기며 따라옴 · 네모 = 키 (끌기 · 우클릭 삭제) · 빈 곳 더블클릭 = 키 추가 · 빈 곳 클릭/끌기 = 프레임 이동\n"
                               "Shift+끌기 = 고칠 구간 선택 (아래 버튼으로 지우고 블렌드 / 일직선 / 떨림 정리) · Ctrl+휠 = 확대(Shift = 세로)\n"
                               "아래쪽 초록/빨강 박스 = 직접 지정한 IK 구간 (클릭=선택 · Ctrl+끌기=옮기기 · 가장자리 Ctrl+끌기=늘이기/줄이기 · Delete=지우기)\n"
                               "굵은 선 = 지금 상태, 흐린 선 = 맨 위 레이어를 적용하기 전, 노란 띠 = IK 접지 구간")
        self.canvas.range_picked.connect(self._range_picked)
        self.canvas.scrub.connect(self._scrub)
        self.canvas.key_dragged.connect(self._dragged)
        self.canvas.key_changed.connect(self._changed)
        self.canvas.chans_changed.connect(self._canvas_picked)
        self.canvas.ik_moved.connect(self._ik_moved)
        self.canvas.ik_deleted.connect(self._ik_deleted)
        self.canvas.ik_pending_moved.connect(self._ik_pending_moved)
        v.addWidget(self.canvas)
        rb = QHBoxLayout()
        self.btns = {}
        for mode, text, tip in (
            ("blend", "🧽 지우고 블렌드", "고른 구간의 값을 지우고, 구간 바로 앞·뒤 프레임 값을 이어서 자연스럽게 채웁니다 (맥스에서 키를 지웠을 때와 같아요)"),
            ("flat", "📏 일직선으로", "고른 구간 전체를 한 값으로 평평하게(가로 직선) 만듭니다. 어떤 값에 맞출지는 오른쪽 '기준'에서 고르세요"),
            ("line", "↘ 시작-끝 이어서", "고른 구간의 시작·끝 값은 두고 그 사이를 대각선으로 잇습니다 (시작과 끝 값이 다르면 비스듬해져요)"),
            ("smooth", "〰 떨림 정리", "고른 구간의 잔 떨림을 부드럽게 펴고, 앞뒤는 원래 값으로 서서히 이어 붙입니다"),
        ):
            bt = QPushButton(text)
            bt.setToolTip(tip)
            bt.clicked.connect(lambda _c=False, m=mode: self.range_do(m))
            rb.addWidget(bt)
            self.btns[mode] = bt
        rb.addWidget(QLabel("기준"))
        self.anchor = QComboBox()
        self.anchor.addItem("구간 시작 값", "start")
        self.anchor.addItem("구간 평균 값", "mean")
        self.anchor.setToolTip("📏 일직선으로 가 구간을 맞출 값: 구간 시작 프레임 값(앞에서 이어짐) / 구간 평균 값")
        self.anchor.currentIndexChanged.connect(self._anchor_changed)
        rb.addWidget(self.anchor)
        rb.addStretch(1)
        v.addLayout(rb)
        self.range_lbl = QLabel("고칠 구간: 그래프에서 Shift 를 누른 채 끌어 고르세요 (고른 본에만 적용)")
        self.range_lbl.setStyleSheet("color:#8aa")
        v.addWidget(self.range_lbl)
        hint = QLabel("굵은 선 = 지금 상태 · 흐린 선 = 맨 위 레이어 적용 전 · 노란 띠 = IK 접지 구간 · 네모 = 키(위아래로 끌기)")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#8aa")
        v.addWidget(hint)
        self.refresh()

    def chans(self) -> tuple:
        return dict((k, c) for k, _n, c in GROUPS).get(self.group.currentData() or "rot", curves.ROT)

    def refresh(self) -> None:
        pr = self.app.pr
        res = pr.result
        j = self.app.viewport.selected_joint
        cv = self.canvas
        if res is None or j is None or pr.tgt is None:
            cv.set_data((), {}, {}, [], None, None, None, None, 0, None)
            self.lbl.setText("본을 고르세요 (뷰포트의 점 또는 본 목록)" if res is not None else "리타겟 결과가 있으면 곡선이 보입니다")
            return
        j = int(j)
        st = pr.layer_stack()
        lay = st.current()
        final = curves.channel_values(res.anim, j)
        below = {}
        if lay is not None:
            base = res.base_anim if res.base_anim is not None else res.anim
            bel = apply_layers(base, st, upto=st.top - 1)
            below = curves.channel_values(bel, j)
        contact = None
        for ch, pl in zip(res.chains, res.plans):
            if j in (ch.upper, ch.lower, ch.foot):
                c = np.asarray(pl.contact, dtype=bool)
                contact = c if contact is None else (contact | c)
        chans = self.chans()
        self.lbl.setText("%s — %s" % (self.app._bone_label(j), ("레이어 '%s' 편집 중" % lay.name) if lay else "원본 상태 (레이어 없음)"))
        cv.set_data(chans, {c: final[c] for c in chans}, {c: below[c] for c in chans} if below else {}, lay.frames(j) if lay else [], contact,
                    lay, res.anim, j, res.anim.n_frames, (j, self.group.currentData(), res.anim.n_frames, id(pr.tgt)))
        ikp = self.app.__dict__.get("ik_panel")
        lab = ikp.chain_for_bone(j) if ikp is not None else None
        pend = None
        if ikp is not None and lab is not None:
            pf = ikp.pending_frame(lab)
            if pf is not None:
                pend = (pf, bool(ikp._pending[0]))
        cv.set_ik(lab, ikp.boxes(lab) if (ikp is not None and lab is not None) else [], pend)
        cv.set_frame(self.app.viewport.frame)
        self._canvas_picked()

    def set_frame(self, f: int) -> None:
        self.canvas.set_frame(f)

    def _ik_moved(self, i: int, a: int, b: int) -> None:
        ikp = self.app.__dict__.get("ik_panel")
        if ikp is not None and self.canvas.ik_label is not None:
            ikp.edit_range(self.canvas.ik_label, i, a, b)

    def _ik_deleted(self, i: int) -> None:
        ikp = self.app.__dict__.get("ik_panel")
        if ikp is not None and self.canvas.ik_label is not None:
            ikp.remove_index(self.canvas.ik_label, i)

    def _ik_pending_moved(self, f: int) -> None:
        ikp = self.app.__dict__.get("ik_panel")
        if ikp is not None:
            ikp.move_pending(f)

    def _range_picked(self, a: int, b: int) -> None:
        lp = self.app.layer_panel
        lp.rng_a.setValue(int(a) + 1)
        lp.rng_b.setValue(int(b) + 1)
        self.range_lbl.setText("고칠 구간: %d ~ %d 프레임 (%d개)" % (a + 1, b + 1, b - a + 1))

    def _anchor_changed(self, *_a) -> None:
        lp = self.app.layer_panel
        if lp.flat_anchor.currentIndex() != self.anchor.currentIndex():
            lp.flat_anchor.setCurrentIndex(self.anchor.currentIndex())

    def range_do(self, mode: str) -> None:
        """고른 구간(없으면 레이어 패널의 시작·끝 칸)에 지우고 블렌드/일직선/떨림 정리를 적용."""
        a, b = self.canvas.range if self.canvas.range is not None else (None, None)
        sel = self.canvas.picked()
        self.app.layer_panel.range_action(mode, a, b, chans=None if sel >= set(self.canvas.chans) else sorted(sel))

    def _group_changed(self, *_a) -> None:
        self.canvas.sel_chans = set()
        self.refresh()

    def _axis_toggled(self, *_a) -> None:
        if self._busy:
            return
        on = {c for c, cb in self.axis_cb.items() if cb.isChecked()}
        self.canvas.sel_chans = set() if on >= set(self.chans()) else on
        self.canvas.update()

    def _canvas_picked(self) -> None:
        """그래프에서 선을 눌러 축을 골랐다 -> 체크박스도 맞춘다."""
        self._busy = True
        try:
            on = self.canvas.picked()
            for c, cb in self.axis_cb.items():
                cb.setEnabled(c in self.chans())
                cb.setChecked(c in on)
        finally:
            self._busy = False

    def _scrub(self, f: int) -> None:
        self.app.frame_slider.setValue(int(f))

    def _dragged(self) -> None:
        self.app.viewport.edited_joints = self.app._edited_set()
        self.app.schedule_live()

    def _changed(self) -> None:
        self.app.layer_panel._changed()
