"""곡선 그래프 편집 — 고른 본의 회전(오일러 °)·위치 곡선을 보고, 키(네모)를 위아래로 끌어 보정 레이어를 고친다.

· 굵은 선 = 지금 상태의 최종 곡선, 흐린 선 = 맨 위 레이어를 적용하기 전(아래 상태) 곡선, 노란 띠 = IK 접지 구간
· 네모 = 맨 위(편집 중) 레이어의 키: 위아래로 끌면 그 프레임의 최종 값이 끈 값이 되도록 키를 고침 / 빈 곳 더블클릭 = 그 프레임에 키 추가 / 키 우클릭 = 삭제
· 빈 곳 클릭·끌기 = 프레임 이동, 휠 = 가로 확대/축소 (Shift+휠 = 세로), '보기 맞춤' = 전체 보기
계산은 core/curves.py, 좌표는 ui/graph_math.py 에 있고 이 파일은 그리기와 마우스 연결만 한다."""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

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

    H = 190

    def __init__(self) -> None:
        super().__init__()
        self.setMinimumHeight(self.H)
        self.setMouseTracking(True)
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
        self.msg = ""

    # ---- 데이터
    def set_data(self, chans, final, below, key_frames, contact, layer, anim, j, T, fit_key) -> None:
        self.chans, self.final, self.below = tuple(chans), final or {}, below or {}
        self.key_frames, self.contact = list(key_frames or []), contact
        self.layer, self.anim, self.j, self.T = layer, anim, j, int(T)
        if fit_key != self._fit_key and self._mode is None:
            self._fit_key = fit_key
            self.fit()
        self.update()

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
    def press_at(self, x: float, y: float, right: bool = False, double: bool = False) -> None:
        self.msg = ""
        if self.T <= 0 or not self.chans:
            return
        gm = self.geom()
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
        self._mode = "scrub"
        self.scrub.emit(f)

    def drag_to(self, x: float, y: float) -> None:
        if self._mode == "key" and self._drag is not None:
            v = float(self.geom().v_of(y))
            if curves.drag_to(self.layer, self._drag, self._drag_ch, v):
                self.key_dragged.emit()
        elif self._mode == "scrub":
            self.scrub.emit(int(np.clip(round(float(self.geom().f_of(x))), 0, max(self.T - 1, 0))))

    def release(self) -> None:
        was = self._mode
        self._mode, self._drag = None, None
        if was == "key":
            self.key_changed.emit()

    def wheel_zoom(self, delta: float, x: float, y: float, vertical: bool = False) -> None:
        factor = 0.8 if delta > 0 else 1.25
        gm = self.geom()
        (gm.zoom_values(factor, y) if vertical else gm.zoom_frames(factor, x))
        self.view = [gm.f0, gm.f1, gm.v0, gm.v1]
        self.update()

    def mousePressEvent(self, ev) -> None:
        p = ev.position()
        self.press_at(p.x(), p.y(), right=ev.button() == Qt.MouseButton.RightButton)

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
                    p.fillRect(max(xa, x0), y0, max(min(xb, x1) - max(xa, x0), 1), y1 - y0, QColor(255, 220, 80, 38))
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
            p.setPen(QPen(col, 2))
            ys = gm.y_of(self.final[ch])
            for i in range(self.T - 1):
                p.drawLine(int(xs[i]), int(ys[i]), int(xs[i + 1]), int(ys[i + 1]))
        xc = int(gm.x_of(self.frame))
        p.setPen(QPen(QColor("#ffffff"), 1))
        p.drawLine(xc, y0, xc, y1)
        for fq, v, (ch, _f) in self.key_points():
            xx, yy = int(gm.x_of(fq)), int(gm.y_of(v))
            p.setBrush(QColor("#ffdc50"))
            p.setPen(QPen(QColor("#000000"), 1))
            p.drawRect(xx - 4, yy - 4, 8, 8)
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
        self.group.currentIndexChanged.connect(lambda *_a: self.refresh())
        top.addWidget(self.group)
        b = QPushButton("보기 맞춤")
        b.setToolTip("곡선 전체가 보이도록 확대/축소를 되돌립니다")
        b.clicked.connect(lambda _c=False: self.canvas.fit())
        top.addWidget(b)
        v.addLayout(top)
        self.canvas = CurveCanvas()
        self.canvas.setToolTip("네모 = 편집 중 레이어의 키 (위아래로 끌기 · 우클릭 삭제) · 빈 곳 더블클릭 = 키 추가 · 클릭/끌기 = 프레임 이동 · 휠 = 확대(Shift = 세로)\n"
                               "굵은 선 = 지금 상태, 흐린 선 = 맨 위 레이어를 적용하기 전, 노란 띠 = IK 접지 구간")
        self.canvas.scrub.connect(self._scrub)
        self.canvas.key_dragged.connect(self._dragged)
        self.canvas.key_changed.connect(self._changed)
        v.addWidget(self.canvas)
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
            self.lbl.setText("본을 고르세요 (뷰포트의 점 또는 본 목록)" if res is not None else "리타깃 결과가 있으면 곡선이 보입니다")
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
        cv.set_frame(self.app.viewport.frame)

    def set_frame(self, f: int) -> None:
        self.canvas.set_frame(f)

    def _scrub(self, f: int) -> None:
        self.app.frame_slider.setValue(int(f))

    def _dragged(self) -> None:
        self.app.viewport.edited_joints = self.app._edited_set()
        self.app.schedule_live()

    def _changed(self) -> None:
        self.app.layer_panel._changed()
