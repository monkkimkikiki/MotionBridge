"""간단 구간 선택: 영상을 보면서 '여기부터 여기까지만 분석'할 구간을 하나 이상 골라 분석한다."""
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QMessageBox, QPushButton, QSlider, QVBoxLayout)

from ..core import pipeline

from .widgets import muted


def fmt(t):
    t = max(0.0, float(t))
    return "%d:%02d.%d" % (int(t) // 60, int(t) % 60, int((t - int(t)) * 10))


class RangeDialog(QDialog):
    """영상에서 분석할 구간(들)을 고른다. 결과는 self.ranges (초 단위 [(시작, 끝)])."""

    def __init__(self, main, path):
        super().__init__(main)
        self.main = main
        self.path = str(path)
        self.ranges = []
        self.mark_in = None
        self.dur = 0.0
        self.setWindowTitle("원하는 구간만 분석 — " + Path(path).name)
        self.resize(1000, 720)
        root = QVBoxLayout(self)
        root.addWidget(muted("영상을 보면서 ① 시작할 곳에서 [시작 표시] ② 끝낼 곳에서 [끝 표시 + 구간 추가]. 여러 구간을 추가할 수 있어요. "
                             "③ [선택한 구간만 분석]. 구간을 하나도 안 넣으면 영상 전체를 분석해요. (원본 파일은 그대로 남아요)"))
        self.video = QVideoWidget()
        self.video.setStyleSheet("background: black;")
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setMuted(True)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self._on_pos)
        self.player.durationChanged.connect(self._on_dur)
        self.player.setSource(QUrl.fromLocalFile(self.path))
        root.addWidget(self.video, 1)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.slider.sliderMoved.connect(self.player.setPosition)
        root.addWidget(self.slider)
        row = QHBoxLayout()
        self.btn_play = QPushButton("▶ 재생")
        self.btn_play.clicked.connect(self.toggle)
        row.addWidget(self.btn_play)
        for text, d in (("◀ 5초", -5000), ("◀ 1초", -1000), ("1초 ▶", 1000), ("5초 ▶", 5000)):
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, dd=d: self.player.setPosition(max(0, self.player.position() + dd)))
            row.addWidget(b)
        self.time = QLabel("0:00.0 / 0:00.0")
        row.addWidget(self.time)
        row.addStretch(1)
        self.b_in = QPushButton("[ 시작 표시")
        self.b_in.clicked.connect(self.set_in)
        row.addWidget(self.b_in)
        self.b_out = QPushButton("끝 표시 + 구간 추가 ]")
        self.b_out.clicked.connect(self.add_range)
        row.addWidget(self.b_out)
        root.addLayout(row)
        self.mark = muted("시작 표시: 아직 없음")
        root.addWidget(self.mark)
        self.listw = QListWidget()
        self.listw.setMaximumHeight(110)
        self.listw.itemDoubleClicked.connect(lambda it: self.player.setPosition(int(self.ranges[self.listw.row(it)][0] * 1000)))
        self.listw.setToolTip("더블클릭하면 그 구간의 시작으로 이동해요")
        root.addWidget(self.listw)
        row2 = QHBoxLayout()
        b = QPushButton("선택한 구간 지우기")
        b.clicked.connect(self.remove_range)
        row2.addWidget(b)
        row2.addStretch(1)
        c = QPushButton("취소")
        c.clicked.connect(self.reject)
        row2.addWidget(c)
        self.b_go = QPushButton("▶ 영상 전체 분석")
        self.b_go.setObjectName("primary")
        self.b_go.clicked.connect(self.go)
        row2.addWidget(self.b_go)
        root.addLayout(row2)

    # ---- 재생
    def toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.btn_play.setText("▶ 재생")
        else:
            self.player.play()
            self.btn_play.setText("⏸ 일시정지")

    def _on_dur(self, d):
        self.dur = d / 1000.0
        self.slider.setRange(0, int(d))

    def _on_pos(self, p):
        if not self.slider.isSliderDown():
            self.slider.setValue(int(p))
        self.time.setText("%s / %s" % (fmt(p / 1000.0), fmt(self.dur)))

    def pos_s(self):
        return self.player.position() / 1000.0

    # ---- 구간
    def set_in(self):
        self.mark_in = self.pos_s()
        self.mark.setText("시작 표시: %s — 이제 끝낼 곳에서 [끝 표시 + 구간 추가]" % fmt(self.mark_in))

    def add_range(self):
        if self.mark_in is None:
            QMessageBox.information(self, "구간", "먼저 시작할 곳에서 [시작 표시]를 눌러 주세요.")
            return
        self._add(self.mark_in, self.pos_s())
        self.mark_in = None
        self.mark.setText("시작 표시: 아직 없음")

    def _add(self, a, b):
        rs = pipeline.clean_ranges(self.ranges + [(a, b)], self.dur)
        if len(rs) == len(self.ranges) and rs == self.ranges:
            QMessageBox.information(self, "구간", "구간은 1초 이상이어야 해요 (이미 포함된 구간이면 합쳐져요).")
        self.ranges = rs
        self._refresh()

    def remove_range(self):
        i = self.listw.currentRow()
        if 0 <= i < len(self.ranges):
            del self.ranges[i]
            self._refresh()

    def _refresh(self):
        self.listw.clear()
        for a, b in self.ranges:
            self.listw.addItem("%s ~ %s   (%d초)" % (fmt(a), fmt(b), round(b - a)))
        n = len(self.ranges)
        self.b_go.setText("▶ 선택한 구간만 분석 (%d개)" % n if n else "▶ 영상 전체 분석")

    def go(self):
        self.player.pause()
        self.accept()

    def done(self, r):
        self.player.stop()
        super().done(r)
