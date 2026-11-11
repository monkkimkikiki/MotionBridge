"""보정 레이어 패널 — 모션빌더의 애니메이션 레이어처럼, 원본은 그대로 두고 키프레임으로 고치는 층을 쌓는다 (core/layers, core/layeredit)."""
from __future__ import annotations

from typing import Optional

import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QVBoxLayout,
)

from ..core import layeredit, rangeedit
from ..core.layers import DEFAULT_FADE, apply_layers
from .viewer_math import subtree_mask
from .uikit import HelpIcon, Resizable
from .widgets import CollapsibleBox, NumField

TITLE = "보정 레이어 스택 — 키프레임으로 고치기 (원본은 그대로)"


class LayerPanel(CollapsibleBox):
    def __init__(self, app) -> None:
        super().__init__(TITLE)
        self.app = app
        self._busy = False
        self._warned = ""
        self._msg = ""
        v = QVBoxLayout(self)
        note = QLabel("리타겟 결과 위에 레이어를 차곡차곡 쌓습니다. 아래 목록에서 한 줄을 고르면 '그 레이어까지만 적용된 상태'가 화면·곡선·내보내기에 그대로 반영됩니다 "
                      "('원본'을 고르면 레이어가 하나도 적용되지 않은 리타겟 결과). 위쪽 레이어는 지워지지 않고 회색으로 남아, 다시 고르면 돌아옵니다.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#8aa")
        v.addWidget(note)
        self.lst = QListWidget()
        self.lst.setMinimumHeight(60)
        self.lst.setToolTip("위에서 아래로 쌓인 순서입니다. 줄을 고르면 그 줄까지만 적용된 상태를 봅니다 (회색 줄 = 지금은 적용 안 됨). 체크 = 켜짐.")
        self.lst.currentRowChanged.connect(self._row_changed)
        self.lst.itemChanged.connect(self._item_changed)
        v.addWidget(Resizable(self.lst, "layer_list_h", 60))
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
        row = QHBoxLayout()
        for text, tip, fn in (("⤓ 아래와 합치기", "고른 레이어를 바로 아래 레이어와 하나로 합칩니다 (세기(가중치)가 곡선에 구워지고, 키는 0.1° 오차 안으로 다시 맞춥니다)", self.merge_down),
                              ("⤓⤓ 여기까지 모두 합치기", "원본 위로 쌓인, 지금 보이는 레이어를 전부 하나로 합칩니다 (위쪽 회색 레이어는 그대로)", self.merge_all)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _c=False, f=fn: f())
            row.addWidget(b)
        v.addLayout(row)
        wr = QHBoxLayout()
        wr.addWidget(QLabel("레이어 세기"))
        self.weight = NumField(0.0, 1.0, 0.05, 1.0, 2)
        self.weight.setToolTip("이 레이어의 수정이 얼마나 반영될지 (1 = 100% 전부, 0.5 = 절반만, 0 = 안 보임). 끄지 않고 세기만 줄여 비교할 때 씁니다.")
        self.weight.changed.connect(self._weight_changed)
        wr.addWidget(self.weight, 1)
        wr.addWidget(HelpIcon("레이어 세기 (이전 이름: 가중치)\n이 레이어에 찍은 키의 효과를 얼마나 섞을지입니다.\n"
                              "· 1 = 레이어의 수정이 100% 적용\n· 0.5 = 수정의 절반만 적용 (원본과 반반)\n· 0 = 적용 안 됨 (레이어를 끄지 않고 효과만 없앰)\n"
                              "키를 다시 찍지 않고도 '조금만 과장'하거나 '살짝만 줄이기'를 할 때 슬라이더로 조절합니다."))
        v.addLayout(wr)
        fd = QHBoxLayout()
        fd.addWidget(QLabel("퍼짐(프레임)"))
        self.fade = NumField(0, 60, 1, DEFAULT_FADE, 0, slider=False)
        self.fade.setToolTip("0 (기본) = 맥스 레이어처럼 첫 키 앞/마지막 키 뒤에서도 그 키 값을 계속 유지합니다 — 키를 하나만 고치면 모션 전체가 그만큼 바뀝니다.\n"
                             "일부 구간만 바꾸려면 그 앞뒤 프레임에 '🔑 아래 값으로 키 (원본 가져오기)'를 찍으세요. 그 키부터 원본으로 돌아갑니다.\n"
                             "0 보다 크게 주면, 키를 따로 찍지 않아도 끝 키에서 이 프레임 수에 걸쳐 서서히 원본으로 돌아갑니다.")
        self.fade.changed.connect(self._fade_changed)
        fd.addWidget(self.fade, 1)
        v.addLayout(fd)
        self.rec = QCheckBox("🔴 레이어를 고른 동안 본 편집을 이 레이어의 키로 기록 (끄면 전체 구간 '본별 보정'으로 들어가며 레이어가 아닙니다)")
        self.rec.setChecked(True)
        self.rec.setToolTip("켜 두면, 레이어가 선택된 상태에서 뷰포트로 본을 돌리거나 옮길 때 현재 프레임에 키가 찍히고 앞뒤로 블렌드됩니다.\n"
                            "'원본' 줄이 선택돼 있거나 레이어가 없으면 전체 구간 '본별 보정'에 기록됩니다 (레이어를 꺼도 남는 값). 크기(R)는 레이어에 기록되지 않습니다.")
        self.rec.toggled.connect(self._rec_toggled)
        v.addWidget(self.rec)
        # ---- 키 찍기 / 지우기
        sc = QHBoxLayout()
        sc.addWidget(QLabel("대상 본"))
        self.scope = QComboBox()
        for key, name in (("one", "선택한 본만"), ("tree", "선택한 본 + 아래 본 전부"), ("all", "모든 본")):
            self.scope.addItem(name, key)
        self.scope.setToolTip("아래 '키 찍기·지우기'와 '구간 편집'이 어느 본에 적용될지. 뷰포트나 본 목록에서 본을 먼저 고르세요.")
        sc.addWidget(self.scope, 1)
        v.addLayout(sc)
        row = QHBoxLayout()
        for text, tip, fn in (("◀ 이전 키", "선택한 본의 이전 키 프레임으로", lambda: self.jump(-1)), ("다음 키 ▶", "선택한 본의 다음 키 프레임으로", lambda: self.jump(1))):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _c=False, f=fn: f())
            row.addWidget(b)
        v.addLayout(row)
        row = QHBoxLayout()
        for text, tip, fn in (("🔑 지금 보이는 값으로 키", "지금 프레임에 '지금 보이는 모습 그대로' 키를 찍어 고정합니다 (앞뒤 키와 블렌드되기 전에 이 프레임을 못 박을 때)", self.key_now),
                              ("🔑 아래 값으로 키 (원본 가져오기)", "지금 프레임의 키를 '이 레이어 아래 상태(원본)의 값'으로 찍습니다 = 변화 0.\n"
                                                           "키를 하나만 고치면 모션 전체가 바뀝니다. 0프레임에 원본 키 → 10프레임에서 각도 수정 → 15프레임에 다시 원본 키를 찍으면 0~15 사이만 바뀌고 나머지는 원본 그대로입니다.", self.key_original)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _c=False, f=fn: f())
            row.addWidget(b)
        v.addLayout(row)
        row = QHBoxLayout()
        for text, tip, fn in (("키 지우기", "지금 프레임의 키를 지웁니다 (앞뒤 키 사이가 자동으로 이어져요)", self.delete_key),
                              ("이 본 키 전부 지우기", "이 레이어에서 대상 본의 키를 모두 지웁니다", self.clear_joint)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _c=False, f=fn: f())
            row.addWidget(b)
        v.addLayout(row)
        # ---- 구간 편집 (튀는 부분 정리)
        lab = QLabel("구간 편집 — 튀거나 떨리는 부분 정리 (대상 본, 맨 위 레이어에 기록)")
        lab.setStyleSheet("font-weight:600")
        v.addWidget(lab)
        rr = QHBoxLayout()
        rr.addWidget(QLabel("프레임"))
        self.rng_a = QSpinBox()
        self.rng_a.setRange(1, 1)
        self.rng_b = QSpinBox()
        self.rng_b.setRange(1, 1)
        rr.addWidget(self.rng_a)
        bn = QPushButton("지금")
        bn.setToolTip("지금 프레임을 구간 시작으로")
        bn.clicked.connect(lambda _c=False: self.rng_a.setValue(self._frame() + 1))
        rr.addWidget(bn)
        rr.addWidget(QLabel("~"))
        rr.addWidget(self.rng_b)
        bn = QPushButton("지금")
        bn.setToolTip("지금 프레임을 구간 끝으로")
        bn.clicked.connect(lambda _c=False: self.rng_b.setValue(self._frame() + 1))
        rr.addWidget(bn)
        v.addLayout(rr)
        rr = QHBoxLayout()
        for text, tip, mode in (("🧽 지우고 블렌드", "이 구간의 프레임을 지우고, 바로 앞·뒤 프레임 값을 자연스럽게 이어서 채웁니다 (수치로 못 고치는 튀는 프레임 제거용)", "blend"),
                                ("📏 일직선으로", "구간 전체를 한 값으로 평평하게 만듭니다 (가로 직선 = 값이 변하지 않음). 어떤 값으로 평평하게 할지는 아래 '평평하게 할 값'에서 고릅니다", "flat"),
                                ("↘ 시작-끝 이어서", "구간의 시작·끝 값은 그대로 두고 그 사이를 대각선으로 이어 흔들림을 없앱니다 (시작과 끝 값이 다르면 비스듬해져요)", "line"),
                                ("〰 떨림 정리", "구간의 자글자글한 떨림만 펴고, 구간 앞뒤는 서서히 원래 값으로 이어 붙입니다", "smooth")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda _c=False, m=mode: self.range_action(m))
            rr.addWidget(b)
        v.addLayout(rr)
        rr = QHBoxLayout()
        rr.addWidget(QLabel("평평하게 할 값"))
        self.flat_anchor = QComboBox()
        self.flat_anchor.addItem("구간 시작 프레임 값 (앞에서 이어짐)", "start")
        self.flat_anchor.addItem("구간 평균 값", "mean")
        self.flat_anchor.setToolTip("📏 일직선으로 를 누를 때 구간을 어떤 값에 맞출지.\n시작 값: 앞쪽은 자연스럽게 이어지고 끝에서 원래 값으로 서서히 돌아옵니다 / 평균 값: 위아래로 흔들리던 곳의 가운데 높이")
        self.flat_anchor.currentIndexChanged.connect(self._anchor_changed)
        rr.addWidget(self.flat_anchor, 1)
        v.addLayout(rr)
        rr = QHBoxLayout()
        rr.addWidget(QLabel("떨림 정리 세기"))
        self.smooth_strength = NumField(0.5, 8.0, 0.5, 2.0, 1, slider=False)
        self.smooth_strength.setToolTip("클수록 더 많이 펴집니다 (앞뒤 몇 프레임을 평균 낼지). 보통 2~3")
        rr.addWidget(self.smooth_strength)
        v.addLayout(rr)
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
        """본 편집을 레이어 키로 기록할지: 레이어가 골라져 있고 기록이 켜져 있을 때."""
        if not self.rec.isChecked():
            return False
        return self.stack().current() is not None or self.autokey_on()

    def autokey_on(self) -> bool:
        """뷰포트의 '오토키' 체크박스. 켜져 있으면 본을 고칠 때 (레이어가 없거나 '원본'을 보고 있어도) 알아서 레이어를 만들어 키를 찍는다."""
        cb = self.app.__dict__.get("cb_autokey")
        return bool(cb is not None and cb.isChecked())

    def _auto_layer(self):
        """오토키: 지금 고른 레이어가 없으면 '오토키' 레이어를 새로 만들어 쌓고 고른다."""
        cur = self.layer()
        if cur is None and self.autokey_on() and self.app.pr.src is not None:
            cur = self.stack().add("오토키")
            self.refresh()
        return cur

    def note_base_edit(self) -> None:
        """본 편집이 레이어가 아닌 '본별 보정'으로 들어갈 때 알려 준다 (레이어를 꺼도 남기 때문에 헷갈리기 쉽다)."""
        st = self.stack()
        if len(st):
            self._warn("⚠ 이 편집은 레이어가 아닌 '본별 보정'(전체 구간)에 기록됩니다. 레이어에 기록하려면 목록에서 레이어를 고르고 🔴 기록을 켜세요.")

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
            it = QListWidgetItem("원본 (리타겟 결과 · 레이어 없음)")
            self.lst.addItem(it)
            for i, l in enumerate(st.layers):
                it = QListWidgetItem("%d. %s  (키 %d · 세기 %.2f)" % (i + 1, l.name, l.n_keys(), l.weight))
                try:
                    it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                except TypeError:       # 가짜 Qt(시험용)에서는 플래그 연산이 안 된다
                    pass
                it.setCheckState(Qt.CheckState.Unchecked if l.mute else Qt.CheckState.Checked)
                if i >= st.top:
                    try:
                        it.setForeground(QColor("#667"))
                    except Exception:  # noqa: BLE001
                        pass
                self.lst.addItem(it)
            self.lst.setCurrentRow(st.top)
            cur = st.current()
            self.weight.set_value(cur.weight if cur else 1.0)
            self.weight.setEnabled(cur is not None)
            self.fade.set_value(cur.fade if cur else DEFAULT_FADE)
            self.fade.setEnabled(cur is not None)
            self.sync_range()
        finally:
            self._busy = False
        self._update_info()

    def _update_info(self) -> None:
        st = self.stack()
        cur = self.layer()
        head = ("지금 보이는 상태: " + ("원본 (레이어 없음)" if cur is None else "레이어 %d '%s' 까지 적용" % (st.top, cur.name)) +
                " · 내보내기도 이 상태로 나갑니다.")
        if st.top < len(st.layers):
            head += " (위쪽 %d개는 잠시 숨김)" % (len(st.layers) - st.top)
        head += "\n본 편집 기록 위치: " + ("레이어 '%s' 의 키 (프레임별, 앞뒤로 블렌드)" % cur.name if self.recording() else
                                            "전체 구간 '본별 보정' — 레이어가 아니라서 레이어를 꺼도 남습니다")
        if self._msg:
            head += "\n" + self._msg
        if cur is None:
            self.info.setText(head + "\n레이어가 없습니다. '＋ 새 레이어'를 눌러 만드세요." if not len(st) else head)
            return
        j = self._joint()
        if j is None:
            self.info.setText(head + "\n레이어 '%s' · 키 %d개 — 본을 고르면 그 본의 키 프레임이 여기에 보입니다." % (cur.name, cur.n_keys()))
            return
        fr = cur.frames(j)
        nm = self.app._bone_label(j)
        self.info.setText(head + "\n레이어 '%s' · %s: 키 %s · 지금 프레임 %d" % (cur.name, nm, ("프레임 " + ", ".join(str(x + 1) for x in fr[:12]) + (" …" if len(fr) > 12 else "")) if fr else "없음", self._frame() + 1))

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
        self._msg = ""
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
        """목록 줄 선택 = 그 상태로 이동 (0 = 원본, k = k번째 레이어까지 적용)."""
        if self._busy or row < 0:
            return
        st = self.stack()
        if st.top == int(row):
            return
        st.set_top(int(row))
        self._msg = ""
        cur = self.layer()
        self._busy = True
        self.weight.set_value(cur.weight if cur else 1.0)
        self.weight.setEnabled(cur is not None)
        self._busy = False
        self._changed()

    def _item_changed(self, it) -> None:
        if self._busy:
            return
        i = self.lst.row(it) - 1            # 0번 줄은 '원본'
        st = self.stack()
        if 0 <= i < len(st.layers):
            on = it.checkState() == Qt.CheckState.Checked
            st.layers[i].mute = not on
            if on and i >= st.top:
                # 체크하는 순간 그 레이어까지 적용된 상태로 본다 (예전에는 회색 줄을 따로 눌러 골라야 화면에 나왔다)
                st.set_top(i + 1)
                self._msg = ""
                self._changed()
                return
            self.app.schedule()

    def _weight_changed(self, v: float) -> None:
        if self._busy:
            return
        cur = self.layer()
        if cur is not None:
            cur.weight = float(v)
            self.refresh()
            self.app.schedule()

    def _fade_changed(self, v: float) -> None:
        if self._busy:
            return
        cur = self.layer()
        if cur is not None:
            cur.fade = int(round(v))
            self.app.schedule()

    def _rec_toggled(self, on: bool) -> None:
        if on:
            if not self.app.cb_bone_edit.isChecked():
                self.app.cb_bone_edit.setChecked(True)
            if self.app.viewport.bone_tool in ("select", "scale"):
                self.app.set_bone_tool("rotate")
            self.app.statusBar().showMessage("키 기록 켜짐 — 프레임을 고르고 본을 끌어 보세요" if self.layer() is not None else "레이어를 고르거나 '＋ 새 레이어'를 만들면 그 레이어에 기록됩니다")

    # ---- 합치기
    def _T(self) -> int:
        a = self.app.pr.anim if self.app.pr.src is not None else None
        return int(a.n_frames) if a is not None else 0

    def _merge(self, a: int, b: int) -> None:
        st = self.stack()
        if self._T() <= 0:
            self.app.statusBar().showMessage("소스 모션이 있어야 합칠 수 있어요")
            return
        why = st.merge_range(a, b, self._T())
        if why:
            self.app.statusBar().showMessage(why)
            self._msg = why
            self._update_info()
            return
        self._msg = "레이어를 합쳤습니다 (Ctrl+Z 로 되돌릴 수 있어요)."
        self._changed()

    def merge_down(self) -> None:
        st = self.stack()
        if st.top < 2:
            self.app.statusBar().showMessage("합치려면 레이어를 둘 이상 쌓고, 위쪽 것을 골라 주세요")
            return
        self._merge(st.top - 2, st.top - 1)

    def merge_all(self) -> None:
        st = self.stack()
        if st.top < 2:
            self.app.statusBar().showMessage("지금 보이는 레이어가 둘 이상이어야 합칠 수 있어요")
            return
        self._merge(0, st.top - 1)

    # ---- 키 조작
    def _targets(self) -> list:
        """'대상 본' 설정에 따른 본 번호 목록."""
        pr = self.app.pr
        if pr.tgt is None:
            return []
        kind = self.scope.currentData() or "one"
        j = self._joint()
        if kind == "all":
            pk = getattr(self.app.viewport, "pickable", None)
            return sorted(int(x) for x in pk) if pk else list(range(pr.tgt.skeleton.n))
        if j is None:
            return []
        if kind == "tree":
            return [int(x) for x in np.flatnonzero(subtree_mask(pr.tgt.skeleton.parents, j))]
        return [j]

    def _need_targets(self):
        cur, js = self.layer(), self._targets()
        why = layeredit.editable(cur)
        if why or not js:
            self.app.statusBar().showMessage(why or "먼저 본을 고르세요 (뷰포트의 점 또는 본 목록)")
            return None, []
        return cur, js

    def key_now(self) -> None:
        cur, js = self._need_targets()
        if cur is None:
            return
        for j in js:
            cur.set_key(j, self._frame())
        self._msg = "지금 보이는 값으로 키 %d개를 찍었습니다 (프레임 %d)." % (len(js), self._frame() + 1)
        self._changed()

    def key_bone(self, j: int) -> None:
        """뷰포트에서 Ctrl+클릭한 본 j 에 지금 프레임의 키를 지금 보이는 값 그대로 찍는다. 레이어가 없으면 하나 만든다."""
        pr = self.app.pr
        if pr.tgt is None or pr.src is None:
            self.app.statusBar().showMessage("먼저 소스 모션·타깃 모델을 열어 주세요")
            return
        st = self.stack()
        cur = st.current()
        if cur is None:
            cur = st.add("키 찍기")
        why = layeredit.editable(cur)
        if why:
            self.app.statusBar().showMessage(why)
            return
        cur.set_key(int(j), self._frame())
        name = self.app._bone_label(int(j)) if hasattr(self.app, "_bone_label") else str(j)
        self._msg = "Ctrl+클릭: '%s' 에 프레임 %d 키를 찍었습니다 (레이어 '%s')." % (name, self._frame() + 1, cur.name)
        self.app.statusBar().showMessage(self._msg)
        self._changed()

    def key_selected(self) -> None:
        """K 키: 고른 본(없으면 '대상 본' 설정)에 지금 프레임의 키를 지금 보이는 값 그대로 찍는다. 레이어가 없으면 새로 만든다."""
        j = self._joint()
        if j is None:
            self.app.statusBar().showMessage("키를 찍을 본을 먼저 고르세요 (뷰포트의 점 또는 본 목록)")
            return
        self.key_bone(j)

    def key_original(self) -> None:
        """지금 프레임의 키를 '이 레이어 아래 상태(원본)의 값' = 변화 0 으로 찍는다."""
        cur, js = self._need_targets()
        if cur is None:
            return
        for j in js:
            cur.set_key(j, self._frame(), rot=np.array([1.0, 0.0, 0.0, 0.0]), pos=np.zeros(3))
        self._msg = "아래 값(원본)으로 키 %d개를 찍었습니다 (프레임 %d)." % (len(js), self._frame() + 1)
        self._changed()

    def delete_key(self) -> None:
        cur, js = self.layer(), self._targets()
        n = sum(1 for j in js if cur is not None and cur.remove_key(j, self._frame()))
        if n:
            self._changed()

    def clear_joint(self) -> None:
        cur, js = self.layer(), self._targets()
        n = 0
        for j in js:
            if cur is not None and cur.frames(j):
                cur.clear_joint(j)
                n += 1
        if n:
            self._changed()

    # ---- 구간 편집
    def sync_range(self) -> None:
        T = max(self._T(), 1)
        self.rng_a.setRange(1, T)
        self.rng_b.setRange(1, T)

    def _anchor_changed(self, *_a) -> None:
        ce = getattr(self.app, "curve_editor", None)
        if ce is not None and getattr(ce, "anchor", None) is not None and ce.anchor.currentIndex() != self.flat_anchor.currentIndex():
            ce.anchor.setCurrentIndex(self.flat_anchor.currentIndex())

    def range_action(self, mode: str, a: Optional[int] = None, b: Optional[int] = None, chans=None) -> None:
        """대상 본의 프레임 구간(a..b, 0부터)을 mode(blend/line/smooth)대로 고친다. 레이어가 없으면 새로 만든다."""
        pr = self.app.pr
        js = self._targets()
        if pr.tgt is None or pr.src is None or not js:
            self.app.statusBar().showMessage("먼저 소스 모션·타깃 모델을 열고 본을 고르세요")
            return
        if pr.result is None:
            try:
                pr.run(analyze_quality=False)
            except Exception as e:  # noqa: BLE001
                self.app.statusBar().showMessage("리타겟 결과가 있어야 합니다: %s" % e)
                return
        st = self.stack()
        if st.current() is None:
            st.add("구간 편집")
        cur = st.current()
        res = pr.result
        base = res.base_anim if res.base_anim is not None else res.anim
        below = apply_layers(base, st, upto=st.top - 1)
        final = apply_layers(base, st)
        if a is None or b is None:
            a, b = self.rng_a.value() - 1, self.rng_b.value() - 1
        n, msg = rangeedit.rewrite(cur, below, final, js, a, b, mode, float(self.smooth_strength.value()), anchor=self.flat_anchor.currentData() or "start", chans=chans)
        self._msg = msg
        self.app.statusBar().showMessage(msg)
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

    def on_rot_drag(self, j: int, q_view) -> bool:
        cur = self._auto_layer() or self.layer()
        why = layeredit.editable(cur)
        ctx = self._ctx(j)
        if why or ctx is None:
            self._warn(why or "리타겟 결과가 있어야 레이어를 고칠 수 있어요 (소스 모션과 타깃 모델을 모두 여세요)")
            return False
        f, Ft, Wp = ctx
        if layeredit.rot_edit(cur, j, f, q_view, Ft, Wp):
            self._after_drag()
            return True
        return False

    def on_pos_drag(self, j: int, dview) -> bool:
        cur = self._auto_layer() or self.layer()
        why = layeredit.editable(cur)
        ctx = self._ctx(j)
        if why or ctx is None:
            self._warn(why or "리타겟 결과가 있어야 레이어를 고칠 수 있어요 (소스 모션과 타깃 모델을 모두 여세요)")
            return False
        f, Ft, Wp = ctx
        if layeredit.pos_edit(cur, j, f, dview, Ft, Wp):
            self._after_drag()
            return True
        return False

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
