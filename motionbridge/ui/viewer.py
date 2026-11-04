"""3D 뷰포트 위젯 (QPainter 소프트웨어 렌더링). 계산은 viewer_math 에 있다."""
from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from .viewer_math import COLORS, DRAW_ORDER, OrbitCamera, ViewData, build_scene

_POINT_RADIUS = {"pt_mesh": 0.0, "pt_target_joints": 2.0, "pt_plant": 6.0, "pt_bad": 6.0}


class Viewport(QWidget):
    camera_moved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 360)
        self.setMouseTracking(False)
        self.cam = OrbitCamera()
        self.pr = None
        self.vd: Optional[ViewData] = None
        self.frame = 0
        self.follow = True
        self.paused = False  # 백그라운드 작업 중에는 데이터를 건드리지 않는다
        self.show_ghost = True
        self.show_mesh = True
        self.show_ik = True
        self._last = None
        self._base_target = None  # follow 모드에서 카메라 높이 기준

    # --------------------------------------------------------------- 데이터
    def set_data(self, pr, vd: Optional[ViewData], reframe: bool = False) -> None:
        self.pr, self.vd = pr, vd
        if vd is not None and reframe:
            self.cam.frame(vd.center, vd.height)
        self.update()

    def set_frame(self, f: int) -> None:
        self.frame = int(f)
        self.update()

    # --------------------------------------------------------------- 그리기
    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), QColor(28, 31, 38))
        w, h = float(self.width()), float(self.height())
        if self.paused or self.vd is None or self.pr is None:
            p.setPen(QColor(140, 148, 165))
            p.setFont(QFont("", 11))
            msg = "작업 중…" if self.paused else "소스 모션과 타깃 모델을 열면 여기에 표시됩니다"
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, msg)
            p.end()
            return
        cam = self.cam
        if self.follow:
            foc = self.vd.focus(self.frame)
            cam_t = cam.target.copy()
            cam.target = np.array([foc[0], cam_t[1], foc[2]])
        try:
            sc = build_scene(self.vd, self.pr, self.frame, self.show_ghost, self.show_mesh, self.show_ik)
        except Exception as e:  # noqa: BLE001 - 그리기 오류가 앱을 멈추지 않게
            p.setPen(QColor(255, 120, 120))
            p.drawText(12, 40, "표시 오류: %s" % e)
            p.end()
            return
        for role in DRAW_ORDER:
            arr = sc.get(role)
            if arr is None or len(arr) == 0:
                continue
            r, g, b = COLORS[role]
            if role.startswith("seg_"):
                width = 1.0 if role == "seg_grid" else 2.0
                p.setPen(QPen(QColor(r, g, b), width))
                for x0, y0, x1, y1 in cam.project_segments(arr, w, h):
                    if np.isfinite(x0 + y0 + x1 + y1):
                        p.drawLine(QPointF(float(x0), float(y0)), QPointF(float(x1), float(y1)))
            else:
                xy, z = cam.project(arr, w, h)
                ok = (z > 0) & np.isfinite(xy[:, 0]) & np.isfinite(xy[:, 1])
                xy = xy[ok]
                rad = _POINT_RADIUS.get(role, 2.0)
                if rad <= 0.0:
                    p.setPen(QPen(QColor(r, g, b), 1.5))
                    p.drawPoints(QPolygonF([QPointF(float(x), float(y)) for x, y in xy]))
                else:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(r, g, b))
                    for x, y in xy:
                        p.drawEllipse(QPointF(float(x), float(y)), rad, rad)
        # 범례
        p.setFont(QFont("", 9))
        y0 = 16
        for text, col in (("타깃(내 모델)", COLORS["seg_target"]), ("소스(원본 모션)", COLORS["seg_ghost"]), ("발 고정", COLORS["pt_plant"]), ("다리 안 닿음", COLORS["pt_bad"])):
            p.setPen(QColor(*col))
            p.drawText(12, y0, "● " + text)
            y0 += 16
        p.setPen(QColor(120, 128, 145))
        p.drawText(12, int(h) - 10, "좌클릭 드래그: 회전   우/휠클릭 드래그: 이동   휠: 확대/축소")
        p.end()

    # --------------------------------------------------------------- 마우스
    def mousePressEvent(self, ev):
        self._last = ev.position()

    def mouseReleaseEvent(self, ev):
        self._last = None

    def mouseMoveEvent(self, ev):
        if self._last is None:
            return
        pos = ev.position()
        dx, dy = pos.x() - self._last.x(), pos.y() - self._last.y()
        self._last = pos
        btn = ev.buttons()
        if btn & Qt.MouseButton.LeftButton:
            self.cam.orbit(dx, dy)
        elif btn & (Qt.MouseButton.RightButton | Qt.MouseButton.MiddleButton):
            if self.follow:  # 이동하면 follow 는 높이만 영향받는다
                self.cam.target[1] += 0.0
            self.cam.pan(dx, dy, float(self.height()))
        self.camera_moved.emit()
        self.update()

    def wheelEvent(self, ev):
        d = ev.angleDelta().y()
        self.cam.zoom(0.9 ** (d / 120.0))
        self.update()
