"""3D 뷰포트 위젯 (QPainter 소프트웨어 렌더링). 계산은 viewer_math 에 있다."""
from __future__ import annotations

from typing import Optional

import numpy as np
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtCore import QLineF
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from .viewer_math import (COLORS, DRAW_ORDER, OrbitCamera, ViewData, build_display_mesh, build_scene, render_surface,
                          skin_display, wire_segments)

_POINT_RADIUS = {"pt_mesh": 0.0, "pt_target_joints": 2.0, "pt_plant": 6.0, "pt_bad": 6.0}

# 메시 표시 방식 (키, 이름)
MESH_MODES = [("off", "표시 안 함"), ("points", "점"), ("wire", "와이어"), ("translucent", "반투명"), ("solid", "솔리드"), ("material", "머티리얼")]


def decode_texture(data: bytes):
    """PNG/JPEG 바이트 -> (H,W,4) uint8. 아주 큰 텍스처는 1024 이하로 줄여서 쓴다. 실패하면 None."""
    img = QImage()
    if not img.loadFromData(data):
        return None
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    if w < 1 or h < 1:
        return None
    arr = np.frombuffer(img.constBits(), dtype=np.uint8, count=h * img.bytesPerLine()).reshape(h, img.bytesPerLine())[:, : w * 4].reshape(h, w, 4).copy()
    step = max(1, max(w, h) // 1024)
    return arr[::step, ::step] if step > 1 else arr


class Viewport(QWidget):
    camera_moved = Signal()
    key_action = Signal(str)  # 뷰포트에 포커스가 있을 때의 키: prev next first last play

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
        self.mesh_mode = "solid"
        self.show_bones = True
        self.show_ik = True
        self._dm = {}  # (메시 id, 종류) -> DisplayMesh
        self._dm_owner = None
        self._qimg_ref = None
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
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

    # --------------------------------------------------------------- 메시
    def _display_meshes(self, kind: str):
        """kind: 'full'(솔리드/머티리얼) 또는 'wire'. 타깃이 바뀌면 캐시를 비운다."""
        tgt = self.pr.tgt
        if tgt is not self._dm_owner:
            self._dm.clear()
            self._dm_owner = tgt
        out = []
        for m in tgt.meshes:
            key = (id(m), kind)
            if key not in self._dm:
                if kind == "wire":
                    self._dm[key] = build_display_mesh(m, decode_texture, max_tris=7000, n_samples=8, want_edges=True)
                else:
                    self._dm[key] = build_display_mesh(m, decode_texture)
            if self._dm[key] is not None:
                out.append(self._dm[key])
        return out

    def _draw_mesh(self, p: QPainter, w: float, h: float) -> str:
        """표면/와이어 메시를 그린다. 상태 문구(없으면 빈 문자열)를 돌려준다."""
        mode, vd, pr = self.mesh_mode, self.vd, self.pr
        if mode in ("off", "points") or pr.tgt is None or not pr.tgt.meshes or vd.tgt_wr is None:
            return ""
        f = min(self.frame, vd.tgt_wr.shape[0] - 1)
        dms = self._display_meshes("wire" if mode == "wire" else "full")
        items = [(dm, skin_display(dm, pr.tgt.skeleton, vd.tgt_wr[f], vd.tgt_wp[f], vd.Ft)) for dm in dms]
        if not items:
            return ""
        if mode == "wire":
            segs = np.concatenate([wire_segments(dm, v) for dm, v in items])
            p.setPen(QPen(QColor(150, 162, 188, 150), 1.0))
            p.drawLines([QLineF(float(a), float(b), float(c), float(d)) for a, b, c, d in self.cam.project_segments(segs, w, h) if np.isfinite(a + b + c + d)])
            return ""
        img = render_surface(items, self.cam, int(w), int(h), material=(mode == "material"))
        if img is not None:
            self._qimg_ref = img
            qimg = QImage(img.data, img.shape[1], img.shape[0], img.shape[1] * 4, QImage.Format.Format_RGBA8888)
            p.setOpacity(0.38 if mode == "translucent" else 1.0)
            p.drawImage(0, 0, qimg)
            p.setOpacity(1.0)
        if mode == "material" and not any(dm.has_material for dm in dms):
            return "머티리얼 정보가 없는 메시라 솔리드로 표시 중 (GLB 추출 시 '머티리얼/텍스처 포함' 체크)"
        return ""

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
            sc = build_scene(self.vd, self.pr, self.frame, self.show_ghost, self.mesh_mode == "points", self.show_ik)
        except Exception as e:  # noqa: BLE001 - 그리기 오류가 앱을 멈추지 않게
            p.setPen(QColor(255, 120, 120))
            p.drawText(12, 40, "표시 오류: %s" % e)
            p.end()
            return
        note = ""
        for role in DRAW_ORDER:
            if role == "pt_mesh":  # 격자 다음, 뼈대 앞에 메시 표면을 그린다
                try:
                    note = self._draw_mesh(p, w, h)
                except Exception as e:  # noqa: BLE001
                    note = "메시 표시 오류: %s" % e
            if not self.show_bones and role in ("seg_target", "pt_target_joints"):
                continue
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
        p.drawText(12, int(h) - 10, "좌클릭 드래그: 회전   우/휠클릭 드래그: 이동   휠: 확대/축소   F: 화면 맞추기   1~5: 메시 보기   B: 뼈대")
        if note:
            p.setPen(QColor(230, 180, 90))
            p.drawText(12, int(h) - 28, note)
        p.end()

    # --------------------------------------------------------------- 마우스 / 키
    def keyPressEvent(self, ev):
        key = ev.key()
        K = Qt.Key
        name = {K.Key_Left: "prev", K.Key_Right: "next", K.Key_Home: "first", K.Key_End: "last", K.Key_Space: "play"}.get(key)
        if name:
            self.key_action.emit(name)
        else:
            super().keyPressEvent(ev)

    def mousePressEvent(self, ev):
        self.setFocus()
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
