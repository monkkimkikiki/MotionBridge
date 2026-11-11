"""IK 구간 직접 지정 — 자동 접지 판정이 틀렸을 때, 체인(왼발·오른발·손)별로 'IK 를 걸 프레임'과 'IK 를 풀 프레임'을 직접 정한다 (core/ikranges)."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QVBoxLayout

from ..core import ikranges
from .widgets import CollapsibleBox

TITLE = "IK 구간 직접 지정 — 이 프레임에서만 발/손을 땅에 붙이기"


class IKRangePanel(CollapsibleBox):
    def __init__(self, app) -> None:
        super().__init__(TITLE)
        self.app = app
        self._busy = False
        v = QVBoxLayout(self)
        note = QLabel("프로그램이 '발이 땅에 닿았다'고 자동으로 판정한 구간이 틀릴 때 고칩니다.\n"
                      "쓰는 법: 재생바로 시작할 프레임에 가서 [IK 켜기]를 한 번 누르고, 끝낼 프레임으로 가서 [IK 켜기]를 한 번 더 누르면 그 사이가 지정됩니다.\n"
                      "· IK 켜기 = 그 구간에서 발(손)을 한 자리에 고정해 미끄러지지 않게 함\n"
                      "· IK 끄기 = 그 구간에서는 IK 없이 원래 동작 그대로 (공중에 뜬 발인데 땅에 붙어 끌려갈 때)")
        note.setWordWrap(True)
        note.setStyleSheet("color:#8aa")
        v.addWidget(note)
        r = QHBoxLayout()
        r.addWidget(QLabel("대상"))
        self.chain = QComboBox()
        self.chain.setToolTip("IK 를 걸 부위: 왼발·오른발·왼손·오른손")
        self.chain.currentIndexChanged.connect(self._chain_changed)
        r.addWidget(self.chain, 1)
        v.addLayout(r)
        self._pending = None                 # (켬 여부, 시작 프레임 0부터, 체인 라벨) — 첫 번째 누름 뒤 끝 프레임을 기다리는 중
        r = QHBoxLayout()
        self.b_on = QPushButton("")
        self.b_on.clicked.connect(lambda _c=False: self.mark(True))
        self.b_off = QPushButton("")
        self.b_off.clicked.connect(lambda _c=False: self.mark(False))
        r.addWidget(self.b_on)
        r.addWidget(self.b_off)
        self.b_cancel = QPushButton("취소")
        self.b_cancel.setToolTip("시작만 찍어 둔 지정을 없앱니다")
        self.b_cancel.clicked.connect(lambda _c=False: self.cancel())
        r.addWidget(self.b_cancel)
        v.addLayout(r)
        self.lst = QListWidget()
        self.lst.setMaximumHeight(90)
        self.lst.setToolTip("직접 지정한 구간 목록 (뒤에 지정한 것이 앞의 것과 겹치면 뒤의 것이 이깁니다)")
        v.addWidget(self.lst)
        r = QHBoxLayout()
        b3 = QPushButton("선택한 구간 지우기")
        b3.clicked.connect(lambda _c=False: self.remove_selected())
        b4 = QPushButton("이 부위 직접 지정 전부 지우기 (자동으로 되돌리기)")
        b4.clicked.connect(lambda _c=False: self.clear_chain())
        r.addWidget(b3)
        r.addWidget(b4)
        v.addLayout(r)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color:#ffdc50")
        v.addWidget(self.info)
        self.refresh()

    def open_here(self) -> None:
        """패널을 펴고, 들어 있는 스크롤 영역에서 보이는 자리로 옮긴다 (발·접지 박스의 [IK 구간 직접 지정 열기] 버튼용)."""
        self.set_collapsed(False, save=False)
        self.setVisible(True)
        w = self.parentWidget()
        while w is not None and not isinstance(w, QScrollArea):
            w = w.parentWidget()
        if w is not None:
            try:
                w.ensureWidgetVisible(self, 0, 20)
            except Exception:  # noqa: BLE001
                pass
        self.refresh()

    def _chain_changed(self, *_a) -> None:
        if not self._busy:
            self._pending = None
            self.refresh()
            self._select_chain_bone()

    def _chain_by_label(self, label):
        pr = self.app.pr
        if label is None or pr.src is None or pr.tgt is None:
            return None
        return next((c for c in pr.chains() if c.label == label), None)

    def _select_chain_bone(self) -> None:
        """대상 부위를 고르면 그 부위의 끝 본(발/손)이 뷰포트에서 자동으로 골라지고 본 편집이 켜진다. (이후 다른 본을 직접 골라도 된다)"""
        ch = self._chain_by_label(self._label())
        if ch is None:
            return
        if not self.app.cb_bone_edit.isChecked():
            self.app.cb_bone_edit.setChecked(True)
        self.app._select_bone(int(ch.foot))

    def sync_from_bone(self, j) -> None:
        """뷰포트에서 다른 본을 골랐을 때, 그 본이 어떤 팔다리 체인에 속하면 '대상'을 그 체인으로 맞춘다 (본 선택은 건드리지 않는다)."""
        pr = self.app.pr
        if j is None or pr.src is None or pr.tgt is None or self._busy:
            return
        for c in pr.chains():
            if int(j) in (int(c.upper), int(c.lower), int(c.foot)):
                i = self.chain.findData(c.label)
                if i >= 0 and self.chain.currentIndex() != i:
                    self._busy = True
                    try:
                        self.chain.setCurrentIndex(i)
                    finally:
                        self._busy = False
                    self._pending = None
                    self.refresh()
                return

    def chain_for_bone(self, j):
        pr = self.app.pr
        if j is None or pr.src is None or pr.tgt is None:
            return None
        for c in pr.chains():
            if int(j) in (int(c.upper), int(c.lower), int(c.foot)):
                return c.label
        return None

    def boxes(self, label=None) -> list:
        """그래프에 그릴 직접 지정 구간 [[a, b, on], ...] (겹침을 푼 상태)."""
        lab = label or self._label()
        return ikranges.normalize(self._ranges(lab), self._T()) if lab else []

    def edit_range(self, label, index: int, a: int, b: int) -> None:
        """index 번째 구간을 a..b 로 옮기거나 늘이고 줄인다 (그래프에서 Ctrl+끌기). 옮긴 구간이 다른 구간과 겹치면 옮긴 쪽이 이긴다."""
        T = self._T()
        r = ikranges.normalize(self._ranges(label), T)
        if not (0 <= index < len(r)) or T <= 0:
            return
        on = bool(r[index][2])
        del r[index]
        a, b = sorted((int(a), int(b)))
        self._store(label, ikranges.add(r, max(a, 0), min(b, T - 1), on, T))

    def remove_index(self, label, index: int) -> None:
        self._store(label, ikranges.remove(self._ranges(label), index, self._T()))

    def move_pending(self, f: int) -> None:
        """찍어 둔 시작 프레임을 옮긴다 (그래프의 표시선을 Ctrl+끌기)."""
        if self._pending is not None:
            on, _f0, lab = self._pending
            self._pending = (on, min(max(int(f), 0), max(self._T() - 1, 0)), lab)
            self.refresh()
            self._refresh_graph()

    def pending_frame(self, label=None):
        """[IK 켜기/끄기]로 시작을 찍어 둔 프레임 (0부터) — 그래프·프레임바에 표시선을 그리기 위해."""
        pd = self._pending
        lab = label or self._label()
        return None if pd is None or pd[2] != lab else int(pd[1])

    # ---- 상태
    def _T(self) -> int:
        a = self.app.pr.anim if self.app.pr.src is not None else None
        return int(a.n_frames) if a is not None else 0

    def _now(self) -> int:
        """지금 보고 있는 프레임 (0부터)."""
        return int(self.app.viewport.frame)

    def _label(self):
        return self.chain.currentData()

    def _ranges(self, label=None) -> list:
        pr = self.app.pr
        return list(pr.settings.ik_ranges.get(pr.clip, {}).get(label or self._label(), []))

    def _store(self, label: str, rngs: list) -> None:
        pr = self.app.pr
        d = pr.settings.ik_ranges.setdefault(pr.clip, {})
        if rngs:
            d[label] = rngs
        else:
            d.pop(label, None)
        if not d:
            pr.settings.ik_ranges.pop(pr.clip, None)
        self.app.schedule()
        self.refresh()
        self._refresh_graph()

    def refresh(self) -> None:
        pr = self.app.pr
        self._busy = True
        try:
            keep = self._label()
            chains = pr.chains() if (pr.src is not None and pr.tgt is not None) else []
            self.chain.clear()
            for c in chains:
                self.chain.addItem(c.label, c.label)
            i = self.chain.findData(keep) if keep is not None else -1
            if i >= 0:
                self.chain.setCurrentIndex(i)
            self.lst.clear()
            lab = self._label()
            T0 = self._T()
            for a, b, on in (ikranges.normalize(self._ranges(lab), T0) if lab else []):
                self.lst.addItem(QListWidgetItem("%s  %s" % ("IK 켜기 " if on else "IK 끄기 ", ikranges.describe([(a, b)]) + " 프레임")))
            self._buttons()
            lines = []
            if self._pending is not None:
                on, f0, _l = self._pending
                lines.append("▶ %d 프레임부터 IK %s 지정 중 — 끝낼 프레임으로 가서 [%s]를 한 번 더 누르세요" % (f0 + 1, "켜기" if on else "끄기", "IK 켜기" if on else "IK 끄기"))
            res = pr.result
            if lab and res is not None:
                for c, pl in zip(res.chains, res.plans):
                    if c.label == lab:
                        lines.append("자동 판정(접지): " + ikranges.describe(ikranges.runs(pl.auto_contact if pl.auto_contact is not None else pl.contact)))
                        if self._ranges(lab):
                            lines.append("직접 지정 반영 후: " + ikranges.describe(ikranges.runs(pl.contact)))
                        if not pl.applied and pl.contact.any():
                            lines.append("⚠ 이 부위는 IK 가 꺼져 있어 적용되지 않습니다 (발·접지 박스의 'IK 사용'을 켜세요)")
            if not chains:
                lines.append("소스 모션과 타깃 모델을 열고 본을 연결하면 쓸 수 있습니다.")
            self.info.setText("\n".join(lines))
        finally:
            self._busy = False

    # ---- 조작
    def _buttons(self) -> None:
        """버튼 글자: 시작을 찍기 전엔 '여기서부터', 찍은 뒤엔 '여기까지'."""
        pend = self._pending
        for btn, on in ((self.b_on, True), (self.b_off, False)):
            name = "IK 켜기" if on else "IK 끄기"
            icon = "✅" if on else "🚫"
            if pend is not None and pend[0] == on:
                btn.setText("%s 여기까지 %s (끝 프레임 지정)" % (icon, name))
                btn.setToolTip("지금 보고 있는 프레임을 끝으로 해서 %d 프레임부터 여기까지를 지정합니다" % (pend[1] + 1))
            else:
                btn.setText("%s 여기서부터 %s" % (icon, name))
                btn.setToolTip("지금 보고 있는 프레임을 시작으로 찍습니다. 끝낼 프레임으로 가서 한 번 더 누르면 구간이 지정돼요"
                               + ("" if on else " (공중에 뜬 발인데 땅에 붙을 때)"))
        self.b_cancel.setEnabled(pend is not None)

    def mark(self, on: bool) -> None:
        """(이름 주의: toggle 은 CollapsibleBox 의 접기/펴기 이름이라 쓰면 제목 줄을 눌러도 안 열린다)
        한 번 누르면 지금 프레임이 시작, 같은 버튼을 한 번 더 누르면 지금 프레임이 끝이 되어 구간이 지정된다."""
        lab, T = self._label(), self._T()
        if lab is None or T <= 0:
            self.app.statusBar().showMessage("먼저 소스 모션·타깃 모델을 열고 대상 부위를 고르세요")
            return
        f = min(max(self._now(), 0), T - 1)
        pend = self._pending
        if pend is None or pend[0] != on or pend[2] != lab:
            self._pending = (bool(on), f, lab)                      # 시작 찍기 (다른 쪽 버튼이었으면 새로 시작)
            self.app.statusBar().showMessage("%d 프레임부터 IK %s — 끝낼 프레임으로 가서 같은 버튼을 한 번 더 누르세요" % (f + 1, "켜기" if on else "끄기"))
            self.refresh()
            self._refresh_graph()
            return
        self._pending = None
        a, b = sorted((pend[1], f))
        self.app.statusBar().showMessage("IK %s: %d~%d 프레임" % ("켜기" if on else "끄기", a + 1, b + 1))
        self._store(lab, ikranges.add(self._ranges(lab), a, b, on, T))

    def cancel(self) -> None:
        self._pending = None
        self.refresh()
        self._refresh_graph()

    def _refresh_graph(self) -> None:
        ce = self.app.__dict__.get("curve_editor")
        if ce is not None:
            ce.refresh()

    def add(self, on: bool, a: int, b: int) -> None:
        """프레임 a..b(0부터, 끝 포함)를 직접 지정 (프로그램/시험용)."""
        lab, T = self._label(), self._T()
        if lab is None or T <= 0:
            return
        self._store(lab, ikranges.add(self._ranges(lab), min(a, b), max(a, b), on, T))

    def remove_selected(self) -> None:
        lab, T = self._label(), self._T()
        i = self.lst.currentRow()
        if lab is None or i < 0:
            return
        self._store(lab, ikranges.remove(self._ranges(lab), i, T))

    def clear_chain(self) -> None:
        lab = self._label()
        if lab is not None:
            self._store(lab, [])
