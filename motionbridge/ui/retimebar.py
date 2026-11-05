"""타임라인 아래 '구간 편집' 줄: 프레임 구간을 골라, 보면서 줄이거나 늘린다.

흐름: ① 시작/끝 프레임을 정한다(슬라이더를 움직여 [현재 프레임] 으로 찍거나 숫자 입력)
      ② '새 길이' 를 바꾸면 화면의 모션이 바로 그 길이로 바뀐다(미리보기, 재생해서 확인)
      ③ 마음에 들면 [확정]. 여러 번 반복 가능, [마지막 취소] / [모두 되돌리기]
내보낼 때는 보이는 그대로(확정 + 미리보기 중인 것) 적용된다.
"""
from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSpinBox, QVBoxLayout, QWidget

from ..core import animops
from .widgets import NumField


class RetimeBar(QWidget):
    def __init__(self, win, parent=None):
        super().__init__(parent)
        self.win = win
        self._busy = False
        self._n = 1  # 지금 보이는 모션의 전체 프레임 수
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 0)
        v.setSpacing(2)
        r1 = QHBoxLayout()
        lab = QLabel("<b>구간 편집</b>")
        lab.setToolTip("프레임 구간을 골라 그 부분만 줄이거나 늘립니다. 구간 밖은 그대로 이어집니다.")
        r1.addWidget(lab)
        r1.addWidget(QLabel("시작"))
        self.start = QSpinBox()
        self.start.setRange(1, 1)
        self.start.setToolTip("구간의 첫 프레임 (1부터)")
        r1.addWidget(self.start)
        b = QPushButton("현재")
        b.setToolTip("지금 보고 있는 프레임을 시작으로")
        b.clicked.connect(lambda *_a: self.start.setValue(self.win.frame_slider.value() + 1))
        r1.addWidget(b)
        r1.addWidget(QLabel("끝"))
        self.end = QSpinBox()
        self.end.setRange(1, 1)
        self.end.setToolTip("구간의 마지막 프레임 (포함)")
        r1.addWidget(self.end)
        b = QPushButton("현재")
        b.setToolTip("지금 보고 있는 프레임을 끝으로")
        b.clicked.connect(lambda *_a: self.end.setValue(self.win.frame_slider.value() + 1))
        r1.addWidget(b)
        r1.addWidget(QLabel("새 길이"))
        self.length = NumField(1, 2, 1, 1, 0, " 프레임", slider=True)
        self.length.setToolTip("구간을 몇 프레임으로 만들지. 움직이면 화면의 모션이 바로 바뀝니다 (재생해서 확인하세요)")
        r1.addWidget(self.length, 1)
        v.addLayout(r1)
        r2 = QHBoxLayout()
        self.info = QLabel("")
        self.info.setWordWrap(True)
        r2.addWidget(self.info, 1)
        self.btn_ok = QPushButton("확정")
        self.btn_ok.setToolTip("지금 보이는 편집을 확정합니다 (이어서 다른 구간을 또 편집할 수 있음)")
        self.btn_ok.clicked.connect(lambda *_a: self.commit())
        r2.addWidget(self.btn_ok)
        self.btn_undo = QPushButton("마지막 취소")
        self.btn_undo.clicked.connect(lambda *_a: self.undo())
        r2.addWidget(self.btn_undo)
        self.btn_reset = QPushButton("모두 되돌리기")
        self.btn_reset.clicked.connect(lambda *_a: self.reset())
        r2.addWidget(self.btn_reset)
        v.addLayout(r2)
        self.start.valueChanged.connect(self._range_changed)
        self.end.valueChanged.connect(self._range_changed)
        self.length.changed.connect(self._length_changed)
        self.refresh()

    # ---- 상태
    @property
    def pr(self):
        return self.win.pr

    def _span(self) -> int:
        return max(int(self.end.value()) - int(self.start.value()) + 1, 1)

    def _set_busy(self):
        class _G:
            def __enter__(s):
                s.prev, self._busy = self._busy, True

            def __exit__(s, *a):
                self._busy = s.prev
        return _G()

    def refresh(self) -> None:
        """모션이 바뀌었을 때(불러오기, 클립 변경, 편집 후) 범위와 표시를 맞춘다."""
        pr = self.pr
        has = pr.src is not None
        for w in (self.start, self.end, self.length, self.btn_ok, self.btn_undo, self.btn_reset):
            w.setEnabled(has)
        if not has:
            self.info.setText("소스 모션을 열면 구간을 골라 줄이거나 늘릴 수 있습니다.")
            return
        n = max(int(pr.edited(pr.clip, with_pending=False).n_frames), 1)  # 범위는 확정한 편집까지만 기준(미리보기가 범위를 흔들지 않게)
        key = (id(pr.src), pr.clip)
        new_clip = key != getattr(self, "_key", None)  # 다른 파일/클립이면 구간을 전체로 되돌린다
        full = self.end.value() >= self._n  # 지금까지 전체 구간을 잡고 있었으면 길이가 바뀌어도 전체를 따라간다
        self._key, self._n = key, n
        with self._set_busy():
            self.start.setRange(1, n)
            self.end.setRange(1, n)
            if new_clip:
                self.start.setValue(1)
                self.end.setValue(n)
            elif full or self.end.value() > n or self.end.value() < self.start.value():
                self.end.setValue(n)
            if self.start.value() > n:
                self.start.setValue(1)
            if pr.pending is None:
                self.length.set_range(1, max(self._span() * 3, 2), 1)
                self.length.set_value(self._span())
        self._update_info()

    def _update_info(self) -> None:
        pr = self.pr
        n0 = int(pr.src.animations[pr.clip].n_frames) if pr.src else 0
        done = len(pr.retimes.get(pr.clip, []))
        e = pr.pending[1] if (pr.pending and pr.pending[0] == pr.clip) else None
        txt = "구간 %d~%d (%d프레임)" % (self.start.value(), self.end.value(), self._span())
        if e is not None:
            sp = animops.speed_of(e)
            txt += " → %d프레임" % e.length
            if sp > 0:
                txt += " · %s" % (("%.2f배 빠르게" % sp) if sp > 1.0001 else ("%.2f배 느리게" % sp) if sp < 0.9999 else "속도 그대로")
            txt += "  (미리보기 중 — [확정] 을 눌러야 굳습니다)"
        else:
            txt += " — '새 길이'를 움직여 보세요"
        shown = int(pr.anim.n_frames) if pr.src else 0
        txt += "  |  전체 %d프레임" % (shown if e is not None else self._n)
        if shown != n0 or done:
            txt += " (원본 %d)" % n0
        if done:
            txt += " · 확정한 편집 %d개" % done
        self.info.setText(txt)
        self.btn_ok.setEnabled(e is not None)
        self.btn_undo.setEnabled(e is not None or done > 0)
        self.btn_reset.setEnabled(e is not None or done > 0)

    # ---- 동작
    def _range_changed(self, *_a) -> None:
        if self._busy:
            return
        if self.end.value() < self.start.value():
            with self._set_busy():
                self.end.setValue(self.start.value())
        with self._set_busy():
            self.length.set_range(1, max(self._span() * 3, 2), 1)
            self.length.set_value(self._span())
        if self.pr.pending is not None:  # 구간을 바꾸면 미리보기는 버린다
            self.pr.pending = None
            self.win.schedule()
        self._update_info()

    def _length_changed(self, v: float) -> None:
        if self._busy or self.pr.src is None:
            return
        m = int(round(v))
        if m == self._span():
            self.pr.pending = None
        else:
            self.pr.pending = (self.pr.clip, animops.Retime(self.start.value() - 1, self.end.value() - 1, m))
        self.win.schedule()  # 화면(뷰포트)이 편집된 모션으로 다시 그려진다
        self._update_info()

    def commit(self) -> None:
        pr = self.pr
        if pr.pending is None:
            return
        e = pr.pending[1]
        pr.commit_pending()
        with self._set_busy():  # 편집한 구간이 새 길이로 남아 있으니 선택도 그 길이로
            self.start.setValue(e.start + 1)
            self.end.setValue(e.start + e.length)
        self.win.schedule()
        self.win.recompute()  # 프레임 수가 바뀌었으므로 즉시 반영(refresh 호출됨)

    def undo(self) -> None:
        if self.pr.undo_retime():
            self.win.recompute()

    def reset(self) -> None:
        self.pr.reset_retimes()
        with self._set_busy():
            self.start.setValue(1)
        self.win.recompute()
