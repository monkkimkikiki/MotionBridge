"""보정 레이어 패널 — 모션빌더의 애니메이션 레이어처럼, 원본은 그대로 두고 키프레임으로 고치는 층을 쌓는다 (core/layers, core/layeredit)."""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from ..core import layeredit
from .widgets import CollapsibleBox, NumField

TITLE = "보정 레이어 — 키프레임으로 고치기 (원본은 그대로)"


class LayerPanel(CollapsibleBox):
    def __init__(self, app) -> None:
        super().__init__(TITLE)
        self.app = app
        self._busy = False
        self._warned = ""
        v = QVBoxLayout(self)
        note = QLabel("리타깃 결과 위에 얹는 수정 층입니다. 레이어를 만들고 → 켠 채 '뷰포트에서 끌면 키로 기록'을 체크한 뒤 → 프레임을 고르고 "
                      "본(🦴 본 편집)을 끌면 그 프레임에 키가 찍힙니다. 키 사이는 자동으로 이어지고, 레이어를 끄거나 지우면 원래 결과로 돌아갑니다.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#8aa")
        v.addWidget(note)
        self.lst = QListWidget()
        self.lst.setMaximumHeight(110)
        self.lst.setToolTip("체크 = 켜짐. 아래에 있는 레이어부터 차례로 쌓입니다. 클릭하면 그 레이어를 편집합니다.")
        self.lst.currentRowChanged.connect(self._row_changed)
        self.lst.itemChanged.connect(self._item_changed)
        v.addWidget(self.lst)
        row = QHBoxLayout()
        for text, tip, fn in (("＋ 새 레이어", "새 보정 레이어를 만들고 편집 대상으로 고릅니다", self.add_layer),
                              ("이름 바꾸기", "", self.rename_layer), ("삭제", "고른 레이어와 그 키를 모두 지웁니다", self.delete_layer),
                              ("↑", "위로(나중에 적용)", lambda: self.move_layer(-1)), ("↓", "아래로(먼저 적용)", lambda: self.move_layer(1))):
            b = QPushButton(text)
            if tip:
                b.setToolTip(tip)
            b.clicked.connect(lambda _c=False, f=fn: f())
            row.addWidget(b)
        v.addLayout(row)
        wr = QHBoxLayout()
        wr.addWidget(QLabel("가중치"))
        self.weight = NumField(0.0, 1.0, 0.05, 1.0, 2)
        self.weight.setToolTip("이 레이어가 얼마나 반영될지 (1 = 전부, 0 = 안 보임). 끄지 않고 세기만 줄여 비교할 때 씁니다.")
        self.weight.changed.connect(self._weight_changed)
        wr.addWidget(self.weight, 1)
        v.addLayout(wr)
        self.rec = QCheckBox("🔴 뷰포트에서 끌면 이 레이어에 키로 기록")
        self.rec.setToolTip("켜면 본 편집의 이동/회전이 '본별 보정(전체 구간)' 대신 이 레이어의 현재 프레임 키로 기록됩니다. 크기(R)는 레이어에 기록되지 않습니다.")
        self.rec.toggled.connect(self._rec_toggled)
        v.addWidget(self.rec)
        row = QHBoxLayout()
        for text, tip, fn in (("◀ 이전 키", "선택한 본의 이전 키 프레임으로", lambda: self.jump(-1)), ("다음 키 ▶", "선택한 본의 다음 키 프레임으로", lambda: self.jump(1)),
                              ("지금 프레임에 키", "선택한 본에 지금 보이는 값 그대로 키를 찍습니다 (이 프레임을 고정할 때)", self.key_now),
                              ("키 지우기", "선택한 본의 지금 프레임 키를 지웁니다", self.delete_key),
                              ("이 본 키 전부 지우기", "이 레이어에서 선택한 본의 키를 모두 지웁니다", self.clear_joint)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _c=False, f=fn: f())
            row.addWidget(b)
        v.addLayout(row)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color:#ffdc50")
        v.addWidget(self.info)
        row = QHBoxLayout()
        for text, tip, fn in (("레이어 저장…", "이 클립의 보정 레이어를 파일로 저장 (본 이름 기준)", self.save), ("레이어 불러오기…", "저장한 보정 레이어를 이 클립에 불러옵니다", self.load)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _c=False, f=fn: f())
            row.addWidget(b)
        v.addLayout(row)
        self.refresh()

    # ---- 상태
    def stack(self):
        return self.app.pr.layer_stack()

    def layer(self):
        return self.stack().current()

    def recording(self) -> bool:
        return bool(self.rec.isChecked())

    def _frame(self) -> int:
        return int(self.app.viewport.frame)

    def _joint(self) -> Optional[int]:
        j = self.app.viewport.selected_joint
        return None if j is None else int(j)

    def refresh(self) -> None:
        self._busy = True
        try:
            st = self.stack()
            self.lst.clear()
            for i, l in enumerate(st.layers):
                it = QListWidgetItem("%s  (키 %d · 가중치 %.2f)" % (l.name, l.n_keys(), l.weight))
                try:
                    it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                except TypeError:       # 가짜 Qt(시험용)에서는 플래그 연산이 안 된다
                    pass
                it.setCheckState(Qt.CheckState.Unchecked if l.mute else Qt.CheckState.Checked)
                self.lst.addItem(it)
            if 0 <= st.active < self.lst.count():
                self.lst.setCurrentRow(st.active)
            cur = st.current()
            self.weight.set_value(cur.weight if cur else 1.0)
            self.weight.setEnabled(cur is not None)
        finally:
            self._busy = False
        self._update_info()

    def _update_info(self) -> None:
        cur = self.layer()
        if cur is None:
            self.info.setText("레이어가 없습니다. '＋ 새 레이어'를 눌러 만드세요.")
            return
        j = self._joint()
        if j is None:
            self.info.setText("레이어 '%s' · 키 %d개 — 본을 고르면 그 본의 키 프레임이 여기에 보입니다." % (cur.name, cur.n_keys()))
            return
        fr = cur.frames(j)
        nm = self.app._bone_label(j)
        self.info.setText("레이어 '%s' · %s: 키 %s · 지금 프레임 %d" % (cur.name, nm, ("프레임 " + ", ".join(str(x + 1) for x in fr[:12]) + (" …" if len(fr) > 12 else "")) if fr else "없음", self._frame() + 1))

    def _changed(self) -> None:
        """키·레이어가 바뀌었다: 목록/표시를 새로고치고 결과를 다시 계산한다."""
        self.refresh()
        self.app.viewport.edited_joints = self.app._edited_set()
        self.app._sync_layer_edited()
        self.app.schedule()

    # ---- 목록 조작
    def add_layer(self) -> None:
        if self.app.pr.src is None:
            self.app.statusBar().showMessage("먼저 소스 모션을 여세요")
            return
        self.stack().add()
        self._changed()

    def rename_layer(self) -> None:
        cur = self.layer()
        if cur is None:
            return
        name, ok = QInputDialog.getText(self, "레이어 이름", "새 이름", text=cur.name)
        if ok and name.strip():
            cur.name = name.strip()
            self._changed()

    def delete_layer(self) -> None:
        st = self.stack()
        cur = st.current()
        if cur is None:
            return
        if cur.n_keys() and QMessageBox.question(self, "레이어 삭제", "'%s' 레이어와 키 %d개를 지울까요? (Ctrl+Z 로 되돌릴 수 있어요)" % (cur.name, cur.n_keys())) != QMessageBox.StandardButton.Yes:
            return
        st.remove(st.active)
        self._changed()

    def move_layer(self, d: int) -> None:
        st = self.stack()
        if st.current() is not None:
            st.move(st.active, d)
            self._changed()

    def _row_changed(self, row: int) -> None:
        if self._busy:
            return
        self.stack().active = int(row)
        cur = self.layer()
        self._busy = True
        self.weight.set_value(cur.weight if cur else 1.0)
        self.weight.setEnabled(cur is not None)
        self._busy = False
        self._update_info()

    def _item_changed(self, it) -> None:
        if self._busy:
            return
        i = self.lst.row(it)
        st = self.stack()
        if 0 <= i < len(st.layers):
            st.layers[i].mute = it.checkState() != Qt.CheckState.Checked
            self.app.schedule()

    def _weight_changed(self, v: float) -> None:
        if self._busy:
            return
        cur = self.layer()
        if cur is not None:
            cur.weight = float(v)
            self.refresh()
            self.app.schedule()

    def _rec_toggled(self, on: bool) -> None:
        if on:
            if self.layer() is None:
                self.add_layer()
            if not self.app.cb_bone_edit.isChecked():
                self.app.cb_bone_edit.setChecked(True)
            if self.app.viewport.bone_tool in ("select", "scale"):
                self.app.set_bone_tool("rotate")
            self.app.statusBar().showMessage("키 기록 켜짐 — 프레임을 고르고 본을 끌어 보세요")

    # ---- 키 조작
    def key_now(self) -> None:
        cur, j = self.layer(), self._joint()
        why = layeredit.editable(cur)
        if why or j is None:
            self.app.statusBar().showMessage(why or "먼저 본을 고르세요")
            return
        cur.set_key(j, self._frame())
        self._changed()

    def delete_key(self) -> None:
        cur, j = self.layer(), self._joint()
        if cur is not None and j is not None and cur.remove_key(j, self._frame()):
            self._changed()

    def clear_joint(self) -> None:
        cur, j = self.layer(), self._joint()
        if cur is not None and j is not None and cur.frames(j):
            cur.clear_joint(j)
            self._changed()

    def jump(self, step: int) -> None:
        cur, j = self.layer(), self._joint()
        if cur is None or j is None:
            return
        f = cur.next_key(j, self._frame(), step)
        if f is not None:
            self.app.frame_slider.setValue(int(f))

    # ---- 뷰포트 끌기 -> 키
    def _ctx(self, j: int):
        vd = self.app.viewport.vd
        if vd is None or vd.tgt_wr is None or vd.Ft is None or self.app.pr.tgt is None:
            return None
        f = min(self._frame(), vd.tgt_wr.shape[0] - 1)
        par = int(self.app.pr.tgt.skeleton.parents[j])
        return f, vd.Ft, (vd.tgt_wr[f][par] if par >= 0 else None)

    def _warn(self, why: str) -> None:
        if why != self._warned:
            self._warned = why
            self.app.statusBar().showMessage(why)

    def on_rot_drag(self, j: int, q_view) -> None:
        cur = self.layer()
        why = layeredit.editable(cur)
        ctx = self._ctx(j)
        if why or ctx is None:
            self._warn(why or "리타깃 결과가 있어야 레이어를 고칠 수 있어요 (소스 모션과 타깃 모델을 모두 여세요)")
            return
        f, Ft, Wp = ctx
        if layeredit.rot_edit(cur, j, f, q_view, Ft, Wp):
            self._after_drag()

    def on_pos_drag(self, j: int, dview) -> None:
        cur = self.layer()
        why = layeredit.editable(cur)
        ctx = self._ctx(j)
        if why or ctx is None:
            self._warn(why or "리타깃 결과가 있어야 레이어를 고칠 수 있어요 (소스 모션과 타깃 모델을 모두 여세요)")
            return
        f, Ft, Wp = ctx
        if layeredit.pos_edit(cur, j, f, dview, Ft, Wp):
            self._after_drag()

    def _after_drag(self) -> None:
        self._warned = ""
        self.app.viewport.edited_joints = self.app._edited_set()
        self._update_info()
        self.app.schedule_live()

    # ---- 파일
    def save(self) -> None:
        if self.app.pr.tgt is None or not self.stack().total_keys():
            self.app.statusBar().showMessage("저장할 레이어 키가 없습니다")
            return
        path, _ = QFileDialog.getSaveFileName(self, "보정 레이어 저장", "layers.json", "MotionBridge 레이어 (*.json)")
        if path:
            self.app.pr.save_layers(path)
            self.app.statusBar().showMessage("레이어 저장: " + path)

    def load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "보정 레이어 불러오기", "", "MotionBridge 레이어 (*.json)")
        if not path:
            return
        try:
            warns = self.app.pr.load_layers(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "불러오기 실패", str(e))
            return
        if warns:
            QMessageBox.information(self, "일부 키를 건너뜀", "\n".join(warns[:12]))
        self._changed()
