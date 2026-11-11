"""3D 뷰포트 위젯 (QPainter 소프트웨어 렌더링). 계산은 viewer_math 에 있다."""
from __future__ import annotations

import time

from typing import Optional

import numpy as np
import json

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtCore import QLineF
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from ..core import quat as Q
from . import gizmo_math as GZ
from . import viewer_math as VM
from .viewer_math import (QUALITY, pick_quality, COLORS, DRAW_ORDER, OrbitCamera, ViewData, build_display_mesh, build_scene, drag_placement, overlay_names, render_surface,
                          skin_display, wire_segments)

ASSET_MIME = "application/x-motionbridge-asset"
_POINT_RADIUS = {"pt_mesh": 0.0, "pt_target_joints": 2.0, "pt_plant": 6.0, "pt_bad": 6.0}

# 메시 표시 방식 (키, 이름)
MESH_MODES = [("off", "표시 안 함"), ("points", "점"), ("wire", "와이어"), ("translucent", "반투명"), ("solid", "솔리드"), ("material", "머티리얼 (재질별 색/텍스처)")]


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
    placement_dragged = Signal(float, float, float)  # 드래그로 바뀐 애니메이션 배치 (dx, dz, dyaw°)
    asset_dropped = Signal(str, list)  # 에셋 목록에서 끌어다 놓음: (종류 "anim"|"mesh", 게임 안 경로들)
    asset_dropped_from = Signal(str, str, list)  # 출처를 밝힌 채 끌어다 놓음 (예: "vidfind"): (출처, 종류, 경로들) — 출처가 있으면 asset_dropped 는 보내지 않는다
    bone_picked = Signal(int)  # 본 편집 모드에서 뷰포트의 본을 클릭함: 타깃 본 번호
    bone_ctrl_clicked = Signal(int)  # 본 편집 모드에서 Ctrl 을 누른 채 본을 클릭함: 그 본에 지금 프레임 키를 찍어 달라는 요청 (선택은 그대로)
    bone_dragged = Signal(int, object)  # 본을 끌어 돌림: (타깃 본 번호, 화면 기준 회전 쿼터니언 (w,x,y,z) — 표준 프레임)
    bone_drag_started = Signal()
    bone_moved = Signal(int, object)   # 본을 끌어 옮김: (타깃 본 번호, 월드 이동 벡터 — 표준 프레임, 타깃 단위)
    bone_scaled = Signal(int, float)   # 본을 끌어 키움/줄임: (타깃 본 번호, 이번 이동의 배율 곱)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 360)
        self.setMouseTracking(False)
        self.cam = OrbitCamera()
        self.pr = None
        self.vd: Optional[ViewData] = None
        self.frame = 0
        self.follow = True
        self.drag_mode = "camera"  # 왼쪽 드래그: camera(회전) | move(애니메이션 이동) | rotate(애니메이션 회전). Shift=이동, Ctrl=회전 은 언제나 우선
        self.paused = False  # 백그라운드 작업 중에는 데이터를 건드리지 않는다
        self.show_ghost = True
        self.mesh_mode = "solid"
        self.mesh_quality = "auto"  # auto | low | mid | high — 무거운 메시를 뷰포트에서만 가볍게 그린다 (원본 데이터는 그대로)
        self.lod = False  # 재생 중이면 True: 표면 샘플을 솎아서 그린다
        self._mesh_ms = 0.0  # 최근 메시 그리기 시간(이동평균, ms). 느리면 자동으로 더 솎는다
        self.quality_note = ""
        self.show_bones = True
        self.show_ik = True
        self.bone_edit = False          # 켜면 왼쪽 클릭으로 본을 고르고, 고른 본을 끌어 돌린다
        self.gizmo_space = "world"      # 기즈모 좌표계: world | local | screen (3ds Max 의 참조 좌표계)
        self.bone_tool = "rotate"       # 본 편집 도구 (3ds Max 와 같은 키): select(Q) | move(W) | rotate(E) | scale(R)
        self._drag_axis = -1            # 축 핸들을 잡고 끄는 중이면 0/1/2 (X/Y/Z)
        self.pickable = set()           # 고를 수 있는 타깃 본 번호 (매핑된 본)
        self.selected_joint = None
        self.joint_names = {}           # 번호 -> 화면에 보여 줄 이름
        self.edited_joints = set()      # 회전 보정이 걸린 본 (표시용)
        self._dm = {}  # (메시 id, 종류) -> DisplayMesh
        self._dm_owner = None
        self._qimg_ref = None
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setAcceptDrops(True)
        self._last = None
        self._base_target = None  # follow 모드에서 카메라 높이 기준

    # --------------------------------------------------------------- 에셋 끌어다 놓기
    def dragEnterEvent(self, e) -> None:  # noqa: N802
        if e.mimeData().hasFormat(ASSET_MIME):
            e.acceptProposedAction()

    dragMoveEvent = dragEnterEvent  # noqa: N815

    def dropEvent(self, e) -> None:  # noqa: N802
        md = e.mimeData()
        if not md.hasFormat(ASSET_MIME):
            return
        try:
            d = json.loads(bytes(md.data(ASSET_MIME)).decode("utf-8"))
            kind, paths = str(d.get("kind", "")), [str(x) for x in d.get("paths", [])]
            origin = str(d.get("origin", ""))
        except (ValueError, AttributeError):
            return
        if kind in ("anim", "mesh") and paths:
            e.acceptProposedAction()
            if origin:
                self.asset_dropped_from.emit(origin, kind, paths)
            else:
                self.asset_dropped.emit(kind, paths)

    # --------------------------------------------------------------- 데이터
    def set_data(self, pr, vd: Optional[ViewData], reframe: bool = False) -> None:
        self.pr, self.vd = pr, vd
        if vd is not None and reframe:
            self.cam.frame(vd.center, vd.height)
        self.update()

    def set_frame(self, f: int) -> None:
        self.frame = int(f)
        self.update()

    def preview_rot(self, j: int, q_view) -> None:
        """본을 끄는 즉시 화면의 현재 프레임만 바로 갱신한다 (전체 계산은 뒤따라 오면서 같은 값으로 덮어쓴다)."""
        if self.vd is not None and self.pr is not None and self.pr.tgt is not None:
            if VM.preview_rot(self.vd, min(self.frame, self.vd.n_frames - 1), self.pr.tgt.skeleton.parents, int(j), q_view):
                self.update()

    def preview_move(self, j: int, dview) -> None:
        if self.vd is not None and self.pr is not None and self.pr.tgt is not None:
            if VM.preview_move(self.vd, min(self.frame, self.vd.n_frames - 1), self.pr.tgt.skeleton.parents, int(j), dview):
                self.update()

    # --------------------------------------------------------------- 메시
    def _display_meshes(self, kind: str):
        """kind: 'full'(솔리드/머티리얼) 또는 'wire'. 타깃이 바뀌거나 품질이 바뀌면 캐시를 다시 만든다."""
        tgt = self.pr.tgt
        if tgt is not self._dm_owner:
            self._dm.clear()
            self._dm_owner = tgt
        q = pick_quality(sum(len(m.faces) for m in tgt.meshes), self.mesh_quality)
        self.quality_note = ("무거운 메시라 자동으로 낮은 품질로 표시 중 (보기 → 메시 품질에서 변경, 데이터는 그대로)" if (self.mesh_quality == "auto" and q != "high") else "")
        tris, samples, wire_tris = QUALITY[q]
        out = []
        for mi, m in enumerate(tgt.meshes):
            key = (id(m), kind, q)
            if key not in self._dm:
                for k in [k for k in self._dm if k[0] == id(m) and k[1] == kind]:
                    del self._dm[k]  # 같은 메시의 다른 품질 캐시는 버린다
                if kind == "wire":
                    self._dm[key] = build_display_mesh(m, decode_texture, max_tris=wire_tris, n_samples=8, want_edges=True, color_index=mi)
                else:
                    self._dm[key] = build_display_mesh(m, decode_texture, max_tris=tris, n_samples=samples, color_index=mi)
            if self._dm[key] is not None:
                out.append(self._dm[key])
        return out

    def _sample_step(self) -> int:
        """움직이는 동안(재생/드래그)에는 표면 샘플을 솎고, 그리기가 느리면 더 솎는다."""
        step = 2 if (self.lod or self._last is not None) else 1
        if self._mesh_ms > 90.0:
            step = max(step, 4)
        elif self._mesh_ms > 55.0:
            step = max(step, 3)
        return step

    def _draw_mesh(self, p: QPainter, w: float, h: float) -> str:
        """표면/와이어 메시를 그린다. 상태 문구(없으면 빈 문자열)를 돌려준다."""
        mode, vd, pr = self.mesh_mode, self.vd, self.pr
        if mode in ("off", "points") or pr.tgt is None or not pr.tgt.meshes or vd.tgt_wr is None:
            return ""
        f = min(self.frame, vd.tgt_wr.shape[0] - 1)
        dms = self._display_meshes("wire" if mode == "wire" else "full")
        items = [(dm, skin_display(dm, pr.tgt.skeleton, vd.tgt_wr[f], vd.tgt_wp[f], vd.Ft, vd.tgt_sc)) for dm in dms]
        if not items:
            return ""
        if mode == "wire":
            segs = np.concatenate([wire_segments(dm, v) for dm, v in items])
            p.setPen(QPen(QColor(150, 162, 188, 150), 1.0))
            p.drawLines([QLineF(float(a), float(b), float(c), float(d)) for a, b, c, d in self.cam.project_segments(segs, w, h) if np.isfinite(a + b + c + d)])
            return ""
        t0 = time.perf_counter()
        img = render_surface(items, self.cam, int(w), int(h), material=(mode == "material"), sample_step=self._sample_step())
        ms = (time.perf_counter() - t0) * 1000.0
        self._mesh_ms = ms if self._mesh_ms == 0.0 else 0.7 * self._mesh_ms + 0.3 * ms
        if img is not None:
            self._qimg_ref = img
            qimg = QImage(img.data, img.shape[1], img.shape[0], img.shape[1] * 4, QImage.Format.Format_RGBA8888)
            p.setOpacity(0.38 if mode == "translucent" else 1.0)
            p.drawImage(0, 0, qimg)
            p.setOpacity(1.0)
        if mode == "material" and not any(dm.real_material for dm in dms):
            return "이 메시에는 텍스처/색 정보가 없어 머티리얼(재질) 이름별 구분 색으로 표시 중 — 진짜 텍스처는 추출 시 '머티리얼/텍스처 포함'(GLB)"
        return self.quality_note

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
        if self.follow and getattr(self, "_drag", "camera") == "camera":  # 애니메이션을 끄는 동안은 카메라를 고정해야 손을 따라온다
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
        self._draw_root_marker(p, w, h, sc, cam)
        if self.bone_edit:
            self._draw_bone_edit(p, w, h)
        # 범례
        p.setFont(QFont("", 9))
        y0 = 16
        for text, col in (("타깃(내 모델)", COLORS["seg_target"]), ("소스(원본 모션)", COLORS["seg_ghost"]), ("발 고정", COLORS["pt_plant"]), ("다리 안 닿음", COLORS["pt_bad"])):
            p.setPen(QColor(*col))
            p.drawText(12, y0, "● " + text)
            y0 += 16
        p.setPen(QColor(255, 120, 220))
        p.drawText(12, y0, "◎ 루트 = 모션 기준점 (점선은 바닥 위치)")
        y0 += 16
        y0 += 6
        p.setPen(QColor(220, 226, 238))
        for line in overlay_names(self.pr):  # 지금 재생 중인 애니메이션 / 메시 이름
            p.drawText(12, y0, line)
            y0 += 16
        p.setPen(QColor(120, 128, 145))
        p.drawText(12, int(h) - 10, "좌클릭 드래그: 회전   우/휠클릭 드래그: 이동   휠: 확대/축소   Shift+드래그: 애니메이션 이동   Ctrl+드래그: 애니메이션 회전   F: 화면 맞추기")
        if note:
            p.setPen(QColor(230, 180, 90))
            p.drawText(12, int(h) - 28, note)
        p.end()

    # --------------------------------------------------------------- 본 편집 (고르기 / 끌어서 돌리기)
    def _joint_screen(self, w: float, h: float):
        """고를 수 있는 본의 화면 좌표: ([번호...], (N,2))"""
        vd = self.vd
        if vd is None or vd.tgt_pos is None or not self.pickable:
            return [], np.zeros((0, 2))
        f = min(self.frame, vd.tgt_pos.shape[0] - 1)
        ids = [j for j in sorted(self.pickable) if j < vd.tgt_pos.shape[1]]
        if not ids:
            return [], np.zeros((0, 2))
        xy, z = self.cam.project(vd.tgt_pos[f][ids], w, h)
        ok = (z > 0) & np.isfinite(xy[:, 0]) & np.isfinite(xy[:, 1])
        return [j for j, o in zip(ids, ok) if o], xy[ok]

    def pick_joint(self, x: float, y: float, radius: float = 14.0):
        """화면 좌표 (x,y) 근처의 본 번호 (없으면 None). 가까운 본이 여러 개면 카메라에 가까운 것을 우선."""
        ids, xy = self._joint_screen(float(self.width()), float(self.height()))
        if not ids:
            return None
        d = np.hypot(xy[:, 0] - x, xy[:, 1] - y)
        near = np.where(d <= radius)[0]
        if len(near) == 0:
            return None
        f = min(self.frame, self.vd.tgt_pos.shape[0] - 1)
        eye = self.cam.eye()
        depth = np.linalg.norm(self.vd.tgt_pos[f][[ids[i] for i in near]] - eye, axis=1)
        return int(ids[int(near[int(np.argmin(depth + d[near] * 1e-3))])])

    def _draw_root_marker(self, p: QPainter, w: float, h: float, sc, cam) -> None:
        """스켈레톤의 루트(맨 위 본)를 분홍 고리 + 바닥까지 점선으로 표시한다. 흰 점은 모든 관절이고, 이 고리가 있는 점이 '루트'다.
        모션의 이동(수평 이동·루트 회전·배치)은 이 점을 기준으로 적용된다 (루트가 골반과 같은 스켈레톤이 많다)."""
        a, g = sc.get("pt_root"), sc.get("pt_root_ground")
        if a is None or g is None:
            return
        try:
            xy, z = cam.project(a, w, h)
            gy, gz = cam.project(g, w, h)
            if not (z[0] > 0 and np.isfinite(xy[0, 0] + xy[0, 1] + gy[0, 0] + gy[0, 1])):
                return
            pen = QPen(QColor(255, 120, 220), 1.5)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(float(xy[0, 0]), float(xy[0, 1])), QPointF(float(gy[0, 0]), float(gy[0, 1])))
            p.setPen(QPen(QColor(255, 120, 220), 2.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(float(xy[0, 0]), float(xy[0, 1])), 9.0, 9.0)
            p.drawLine(QPointF(float(gy[0, 0]) - 6, float(gy[0, 1])), QPointF(float(gy[0, 0]) + 6, float(gy[0, 1])))
            p.drawLine(QPointF(float(gy[0, 0]), float(gy[0, 1]) - 6), QPointF(float(gy[0, 0]), float(gy[0, 1]) + 6))
            p.setFont(QFont("", 9))
            p.drawText(QPointF(float(xy[0, 0]) + 13, float(xy[0, 1]) - 8), "루트 (기준점)")
        except Exception:  # noqa: BLE001 - 표시용이라 오류가 앱을 멈추지 않게
            pass

    def _draw_bone_edit(self, p: QPainter, w: float, h: float) -> None:
        ids, xy = self._joint_screen(w, h)
        for j, (x, y) in zip(ids, xy):
            sel = (j == self.selected_joint)
            if sel:
                p.setPen(QPen(QColor(255, 220, 80), 2.5))
                p.setBrush(QColor(255, 220, 80, 90))
                p.drawEllipse(QPointF(float(x), float(y)), 9.0, 9.0)
            else:
                edited = j in self.edited_joints
                p.setPen(QPen(QColor(110, 220, 255) if edited else QColor(150, 160, 180), 1.5))
                p.setBrush(QColor(110, 220, 255, 120) if edited else Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(float(x), float(y)), 5.0, 5.0)
        self._draw_gizmo(p, w, h)
        if self.selected_joint is not None:
            p.setPen(QColor(255, 220, 80))
            p.setFont(QFont("", 10))
            hint = {"select": "Q 선택: 점을 눌러 고르기", "move": "W 이동: 화살표=한 축 · 중심=자유 이동", "rotate": "E 회전: 고리=한 축 · 점 끌기=자유 회전 (Alt=비틀기)",
                    "scale": "R 크기: 오른쪽/위로 끌면 커짐"}.get(self.bone_tool, "")
            p.drawText(12, int(h) - 46, "선택한 본: %s   [%s] · 좌표계: %s   (빈 곳 끌기: 카메라)" % (self.joint_names.get(self.selected_joint, str(self.selected_joint)), hint, GZ.SPACE_NAMES.get(self.gizmo_space, "")))

    def _selected_pos(self):
        vd = self.vd
        if self.selected_joint is None or vd is None or vd.tgt_pos is None:
            return None
        f = min(self.frame, vd.tgt_pos.shape[0] - 1)
        j = int(self.selected_joint)
        return vd.tgt_pos[f][j] if j < vd.tgt_pos.shape[1] else None

    def _gizmo_axes(self):
        """지금 좌표계에서의 기즈모 세 축 (표준 프레임, 행 3개)."""
        local = None
        vd = self.vd
        if self.gizmo_space == "local" and vd is not None and vd.tgt_wr is not None and vd.Ft is not None and self.selected_joint is not None:
            f = min(self.frame, vd.tgt_wr.shape[0] - 1)
            j = int(self.selected_joint)
            if j < vd.tgt_wr.shape[1]:
                local = Q.mul(vd.Ft, vd.tgt_wr[f][j])
        return GZ.space_axes(self.gizmo_space, self.cam, local)

    def _draw_gizmo(self, p: QPainter, w: float, h: float) -> None:
        """선택한 본에 이동/회전/크기 기즈모를 그린다 (축 색: X 빨강 · Y 초록 · Z 파랑, 잡고 있는 축은 노랑)."""
        pos = self._selected_pos()
        tool = self.bone_tool
        if pos is None or tool not in ("move", "rotate", "scale"):
            return
        cols = [QColor(235, 80, 80), QColor(90, 210, 100), QColor(90, 140, 245)]
        active = self._drag_axis if getattr(self, "_drag", "camera").startswith("bone") else -1
        axes = self._gizmo_axes()
        c, tips, L = GZ.handle_tips(self.cam, pos, w, h, axes=axes)
        if not np.all(np.isfinite(c)):
            return
        if tool == "rotate":
            R = GZ.world_len(self.cam, pos, GZ.RING_PX, w, h)
            for i in range(3):
                xy, _z = self.cam.project(GZ.ring_world(pos, i, R, axes=axes), w, h)
                p.setPen(QPen(QColor(255, 220, 80) if i == active else cols[i], 2.2 if i == active else 1.6))
                pts = [QPointF(float(a), float(b)) for a, b in xy if np.isfinite(a) and np.isfinite(b)]
                if len(pts) > 1:
                    p.drawPolyline(QPolygonF(pts))
            return
        for i in range(3):
            if not np.all(np.isfinite(tips[i])):
                continue
            col = QColor(255, 220, 80) if i == active else cols[i]
            p.setPen(QPen(col, 2.4))
            p.drawLine(QPointF(float(c[0]), float(c[1])), QPointF(float(tips[i][0]), float(tips[i][1])))
            p.setBrush(col)
            if tool == "move":      # 화살촉
                v = tips[i] - c
                n = float(np.hypot(*v)) or 1.0
                v, o = v / n, np.array([-v[1], v[0]]) / n
                a = tips[i]
                tri = QPolygonF([QPointF(float(a[0]), float(a[1])), QPointF(float(a[0] - v[0] * 12 + o[0] * 5), float(a[1] - v[1] * 12 + o[1] * 5)),
                                 QPointF(float(a[0] - v[0] * 12 - o[0] * 5), float(a[1] - v[1] * 12 - o[1] * 5))])
                p.setPen(Qt.PenStyle.NoPen)
                p.drawPolygon(tri)
            else:                   # 크기: 끝의 네모
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRect(float(tips[i][0]) - 4, float(tips[i][1]) - 4, 8.0, 8.0)
            p.setPen(col)
            p.drawText(QPointF(float(tips[i][0]) + 6, float(tips[i][1]) - 4), GZ.AXIS_NAMES[i])
        p.setPen(QPen(QColor(255, 220, 80), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(float(c[0]) - 5, float(c[1]) - 5, 10.0, 10.0)

    def _bone_delta(self, dx: float, dy: float, roll: bool):
        """마우스 이동량 -> 표준 프레임에서의 회전 쿼터니언 (카메라 위/오른쪽 축 기준 트랙볼 식)."""
        r, u, f = self.cam.basis()
        k = 0.6   # 픽셀당 도
        if roll:
            return Q.from_axis_angle(-f, np.radians(dx * k))
        return Q.mul(Q.from_axis_angle(u, np.radians(dx * k)), Q.from_axis_angle(r, np.radians(dy * k)))

    def set_gizmo_space(self, space: str) -> None:
        if space in GZ.SPACES:
            self.gizmo_space = space
            self.update()

    def keyPressEvent(self, ev):
        key = ev.key()
        K = Qt.Key
        name = {K.Key_Left: "prev", K.Key_Right: "next", K.Key_Home: "first", K.Key_End: "last", K.Key_Space: "play"}.get(key)
        if not name and not (ev.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
            name = {K.Key_Q: "tool_select", K.Key_W: "tool_move", K.Key_E: "tool_rotate", K.Key_R: "tool_scale", K.Key_K: "key_selected"}.get(key)   # 3ds Max 와 같은 키 (K = 지금 프레임에 키 찍기)
        if name:
            self.key_action.emit(name)
        else:
            super().keyPressEvent(ev)

    def mousePressEvent(self, ev):
        self.setFocus()
        self._last = ev.position()
        self._drag = self._left_mode(ev.button(), ev.modifiers())
        if (self.bone_edit and ev.button() == Qt.MouseButton.LeftButton and (ev.modifiers() & Qt.KeyboardModifier.ControlModifier)
                and self.vd is not None and self.vd.tgt_pos is not None):
            jc = self.pick_joint(ev.position().x(), ev.position().y())      # Ctrl+클릭한 본에 키만 찍는다 (선택·카메라는 그대로)
            if jc is not None:
                self._drag = "bone_select"
                self.bone_ctrl_clicked.emit(int(jc))
                self.update()
                return
        if (self.bone_edit and self._drag == "camera" and ev.button() == Qt.MouseButton.LeftButton
                and self.vd is not None and self.vd.tgt_pos is not None):
            x, y = ev.position().x(), ev.position().y()
            w, h = float(self.width()), float(self.height())
            tool, pos = self.bone_tool, self._selected_pos()
            self._drag_axis = -1
            if pos is not None and tool in ("move", "rotate", "scale"):      # 이미 고른 본의 기즈모 핸들부터 잡는다
                axes = self._gizmo_axes()
                if tool == "move":
                    ax = GZ.hit_axis(self.cam, pos, x, y, w, h, axes=axes)
                    mode = "bone_move_axis"
                elif tool == "rotate":
                    ax = GZ.hit_ring(self.cam, pos, x, y, w, h, axes=axes)
                    mode = "bone_rot_axis"
                else:
                    ax = GZ.hit_axis(self.cam, pos, x, y, w, h, axes=axes)
                    mode = "bone_scale"
                if ax >= 0:
                    self._drag, self._drag_axis = mode, ax
                    self.bone_drag_started.emit()
                    self.update()
                    return
            j = self.pick_joint(x, y)
            if j is not None:
                self.selected_joint = j
                self._drag = {"select": "bone_select", "move": "bone_move_free", "rotate": "bone", "scale": "bone_scale"}.get(tool, "bone")
                self.bone_picked.emit(j)
                if tool != "select":
                    self.bone_drag_started.emit()
                self.update()

    def _left_mode(self, button, mods) -> str:
        if button != Qt.MouseButton.LeftButton:
            return "camera"
        if mods & Qt.KeyboardModifier.ShiftModifier:
            return "move"
        if mods & Qt.KeyboardModifier.ControlModifier:
            return "rotate"
        return self.drag_mode if self.drag_mode in ("move", "rotate") else "camera"

    def mouseReleaseEvent(self, ev):
        self._last = None
        self._drag = "camera"
        self._drag_axis = -1
        self._mesh_ms = 0.0
        self.update()  # 드래그가 끝나면 전체 품질로 다시 그린다

    def mouseMoveEvent(self, ev):
        if self._last is None:
            return
        pos = ev.position()
        dx, dy = pos.x() - self._last.x(), pos.y() - self._last.y()
        self._last = pos
        btn = ev.buttons()
        mode = getattr(self, "_drag", "camera")
        if mode.startswith("bone") and (btn & Qt.MouseButton.LeftButton) and self.selected_joint is not None:
            if not (dx or dy):
                return
            j = int(self.selected_joint)
            pos3 = self._selected_pos()
            w, h = float(self.width()), float(self.height())
            if mode == "bone":
                roll = bool(ev.modifiers() & Qt.KeyboardModifier.AltModifier)
                self.bone_dragged.emit(j, self._bone_delta(dx, dy, roll))
            elif pos3 is not None and mode == "bone_rot_axis" and self._drag_axis >= 0:
                axes = self._gizmo_axes()
                ang = GZ.ring_angle(self.cam, pos3, self._drag_axis, pos.x() - dx, pos.y() - dy, pos.x(), pos.y(), w, h, axes=axes)
                if ang:
                    self.bone_dragged.emit(j, Q.from_axis_angle(axes[self._drag_axis], ang))
            elif pos3 is not None and mode == "bone_move_axis" and self._drag_axis >= 0:
                self.bone_moved.emit(j, GZ.axis_move_delta(self.cam, pos3, self._drag_axis, dx, dy, w, h, axes=self._gizmo_axes()))
            elif pos3 is not None and mode == "bone_move_free":
                self.bone_moved.emit(j, GZ.plane_move_delta(self.cam, pos3, dx, dy, w, h))
            elif mode == "bone_scale":
                self.bone_scaled.emit(j, GZ.scale_factor(dx, dy))
            self.update()
            return
        if (btn & Qt.MouseButton.LeftButton) and mode in ("move", "rotate") and self.vd is not None:
            vd = self.vd
            ddx, ddz, dyaw = drag_placement(self.cam, mode, (pos.x() - dx, pos.y() - dy), (pos.x(), pos.y()), float(self.width()), float(self.height()), vd.ground_y, (vd.center[0], vd.center[2]))
            if ddx or ddz or dyaw:
                self.placement_dragged.emit(ddx, ddz, dyaw)
            return
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
