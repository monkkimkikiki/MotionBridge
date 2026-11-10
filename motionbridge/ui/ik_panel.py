"""IK 구간 직접 지정 — 자동 접지 판정이 틀렸을 때, 체인(왼발·오른발·손)별로 'IK 를 걸 프레임'과 'IK 를 풀 프레임'을 직접 정한다 (core/ikranges)."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QSpinBox, QVBoxLayout

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
                      "· IK 켜기 = 그 프레임들에서 발(손)을 한 자리에 고정해 미끄러지지 않게 함\n"
                      "· IK 끄기 = 그 프레임들에서는 IK 없이 원래 동작 그대로 (공중에 뜬 발인데 땅에 붙어 끌려갈 때)")
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
        r = QHBoxLayout()
        r.addWidget(QLabel("프레임"))
        self.a = QSpinBox()
        self.a.setRange(1, 1)
        self.b = QSpinBox()
        self.b.setRange(1, 1)
        r.addWidget(self.a)
        bn = QPushButton("지금")
        bn.setToolTip("지금 보고 있는 프레임을 시작으로")
        bn.clicked.connect(lambda _c=False: self.a.setValue(self._now()))
        r.addWidget(bn)
        r.addWidget(QLabel("~"))
        r.addWidget(self.b)
        bn = QPushButton("지금")
        bn.setToolTip("지금 보고 있는 프레임을 끝으로")
        bn.clicked.connect(lambda _c=False: self.b.setValue(self._now()))
        r.addWidget(bn)
        v.addLayout(r)
        r = QHBoxLayout()
        b1 = QPushButton("✅ 이 구간 IK 켜기")
        b1.setToolTip("선택한 부위를 이 프레임 구간에서 한 자리에 고정합니다")
        b1.clicked.connect(lambda _c=False: self.add(True))
        b2 = QPushButton("🚫 이 구간 IK 끄기")
        b2.setToolTip("선택한 부위를 이 프레임 구간에서는 IK 없이 원래 동작대로 둡니다")
        b2.clicked.connect(lambda _c=False: self.add(False))
        r.addWidget(b1)
        r.addWidget(b2)
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

    def _chain_changed(self, *_a) -> None:
        if not self._busy:
            self.refresh()

    # ---- 상태
    def _T(self) -> int:
        a = self.app.pr.anim if self.app.pr.src is not None else None
        return int(a.n_frames) if a is not None else 0

    def _now(self) -> int:
        return int(self.app.viewport.frame) + 1

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
            T = max(self._T(), 1)
            self.a.setRange(1, T)
            self.b.setRange(1, T)
            self.lst.clear()
            lab = self._label()
            T0 = self._T()
            for a, b, on in (ikranges.normalize(self._ranges(lab), T0) if lab else []):
                self.lst.addItem(QListWidgetItem("%s  %s" % ("IK 켜기 " if on else "IK 끄기 ", ikranges.describe([(a, b)]) + " 프레임")))
            lines = []
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
    def add(self, on: bool) -> None:
        lab, T = self._label(), self._T()
        if lab is None or T <= 0:
            self.app.statusBar().showMessage("먼저 소스 모션·타깃 모델을 열고 대상 부위를 고르세요")
            return
        a, b = sorted((int(self.a.value()) - 1, int(self.b.value()) - 1))
        self._store(lab, ikranges.add(self._ranges(lab), a, b, on, T))

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
