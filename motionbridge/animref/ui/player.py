"""클립 플레이어: 슬로모션, 프레임 단위 이동, 반복 재생, 빠른 분류 지정."""
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QInputDialog, QLabel,
                               QMessageBox, QPushButton, QSlider, QVBoxLayout)

from ..core import library
from ..core.settings import label_names

from .widgets import muted, open_path, reveal

SPEEDS = [("0.1배", 0.1), ("0.25배", 0.25), ("0.5배", 0.5), ("1배", 1.0)]
NEW_LABEL = "＋ 새 분류 만들기..."


class PlayerDialog(QDialog):
    on_change = None  # 분류를 바꾸는 즉시 라이브러리 목록에 반영하라고 부르는 함수 (플레이어를 띄워 둔 채 쓰므로)

    def __init__(self, clips, index, main):
        super().__init__(main)
        self.main = main
        self.clips = clips
        self.idx = index
        self.changed = False
        self.fps = 30.0
        self.setWindowTitle("클립 보기")
        self.resize(1100, 800)

        self.video = QVideoWidget()
        self.video.setStyleSheet("background: black;")
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setMuted(True)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.setLoops(-1)  # 무한 반복
        self.player.positionChanged.connect(self._on_pos)
        self.player.durationChanged.connect(self._on_dur)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.errorOccurred.connect(self._on_error)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        self.title = QLabel()
        self.title.setObjectName("cardTitle")
        root.addWidget(self.title)
        root.addWidget(self.video, 1)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.slider.sliderMoved.connect(self.player.setPosition)
        root.addWidget(self.slider)

        ctr = QHBoxLayout()
        self.btn_prev_clip = self._btn("⏮ 이전 클립", self.prev_clip)
        self.btn_back = self._btn("◀ 1프레임", lambda: self.step(-1))
        self.btn_play = self._btn("⏸ 일시정지", self.toggle_play)
        self.btn_play.setMinimumWidth(120)
        self.btn_fwd = self._btn("1프레임 ▶", lambda: self.step(1))
        self.btn_next_clip = self._btn("다음 클립 ⏭", self.next_clip)
        for b in (self.btn_prev_clip, self.btn_back, self.btn_play, self.btn_fwd, self.btn_next_clip):
            ctr.addWidget(b)
        ctr.addSpacing(16)
        ctr.addWidget(QLabel("속도"))
        self.speed = QComboBox()
        self.speed.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for name, v in SPEEDS:
            self.speed.addItem(name, v)
        self.speed.setCurrentIndex(int(main.s.get("player_speed_idx", 2)))
        self.speed.currentIndexChanged.connect(self._on_speed)
        ctr.addWidget(self.speed)
        self.ck_sound = QCheckBox("소리")
        self.ck_sound.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.ck_sound.toggled.connect(lambda v: self.audio.setMuted(not v))
        ctr.addWidget(self.ck_sound)
        ctr.addStretch(1)
        self.time_label = QLabel("")
        ctr.addWidget(self.time_label)
        root.addLayout(ctr)

        info = QHBoxLayout()
        info.addWidget(QLabel("분류"))
        self.label_combo = QComboBox()
        self.label_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.label_combo.setMinimumWidth(160)
        self.label_combo.activated.connect(self._on_label)
        info.addWidget(self.label_combo)
        self.ai_label = muted("")
        info.addWidget(self.ai_label, 1)
        info.addWidget(self._btn("✏ 수정 (E)", self.edit_current))
        info.addWidget(self._btn("🎞 속도·편집 (T)", self.retime_current))
        info.addWidget(self._btn("🔪 타수 나누기 (H)", self.split_hits_current))
        info.addWidget(self._btn("탐색기에서 보기", lambda: reveal(library.file_of(self.cur, "video"))))
        info.addWidget(self._btn("다른 플레이어로 열기", lambda: open_path(library.file_of(self.cur, "video"))))
        info.addWidget(self._btn("🗑 삭제", self.delete_current))
        root.addLayout(info)
        root.addWidget(muted("단축키:  Space 재생/정지 · ← → 1프레임 이동 · ↑ ↓ 이전/다음 클립 · "
                             "숫자 1~9 분류 지정 · E 수정 · T 속도·편집 · H 타수 나누기 · Delete 삭제"))

        for key, fn in [("Space", self.toggle_play), ("Left", lambda: self.step(-1)),
                        ("Right", lambda: self.step(1)), ("Up", self.prev_clip),
                        ("Down", self.next_clip), ("Delete", self.delete_current), ("E", self.edit_current),
                        ("T", self.retime_current), ("H", self.split_hits_current)]:
            QShortcut(QKeySequence(key), self, activated=fn)
        for n in range(1, 10):
            QShortcut(QKeySequence(str(n)), self, activated=lambda n=n: self.quick_label(n))

        self.load(self.idx)

    def _btn(self, text, fn):
        b = QPushButton(text)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.clicked.connect(fn)
        return b

    @property
    def cur(self):
        return self.clips[self.idx]

    # ---------------------------------------------------------------- 로드/표시
    def load(self, idx):
        if not self.clips:
            self.accept()
            return
        self.idx = max(0, min(idx, len(self.clips) - 1))
        m = self.cur
        self.fps = float(m.get("fps") or 30.0)
        hit = f"  ·  연속기 {m['hit_count']}타 중 {m['hit_index']}타" if m.get("view") == "hit" and m.get("hit_count") else ""
        self.title.setText(f"{library.final_label(m)}{hit}  ·  {m.get('source_name', '')}  "
                           f"({self.idx + 1} / {len(self.clips)})")
        self._fill_labels()
        top = m.get("ai_top") or []
        if top:
            who = "영상 이해 AI" if m.get("ai_engine") == "vlm" else "기본 분류"
            txt = f"{who}: " + ", ".join(f"{n} {p:.0%}" for n, p in top)
            if m.get("ai_reason"):
                txt += f"  —  {m['ai_reason']}"
            if m.get("group"):
                txt += f"   ·   묶음: {m['group']}"
            self.ai_label.setText(txt)
        else:
            self.ai_label.setText("AI 분류 전 (라이브러리에서 [AI 분류 실행]을 누르세요)")
        self.btn_prev_clip.setEnabled(self.idx > 0)
        self.btn_next_clip.setEnabled(self.idx < len(self.clips) - 1)
        self.player.setSource(QUrl.fromLocalFile(str(library.file_of(m, "video"))))
        self.player.setPlaybackRate(self.speed.currentData())
        self.player.play()

    def _fill_labels(self):
        names = label_names(self.main.s)
        cur = library.final_label(self.cur)
        self.label_combo.clear()
        items = names + ([library.UNLABELED] if library.UNLABELED not in names else [])
        if cur not in items:
            items.append(cur)
        for i, n in enumerate(items):
            self.label_combo.addItem(f"{i + 1}. {n}" if i < 9 else n, n)
        self.label_combo.addItem(NEW_LABEL, NEW_LABEL)
        self.label_combo.setCurrentIndex(items.index(cur))

    # ---------------------------------------------------------------- 재생 제어
    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def step(self, n):
        self.player.pause()
        ms = 1000.0 / self.fps
        pos = max(0, min(self.player.duration(), int(self.player.position() + n * ms)))
        self.player.setPosition(pos)

    def _on_speed(self, i):
        self.player.setPlaybackRate(self.speed.itemData(i))
        self.main.s["player_speed_idx"] = i
        self.main.save_settings()

    def _on_pos(self, pos):
        if not self.slider.isSliderDown():
            self.slider.setValue(pos)
        dur = max(1, self.player.duration())
        f = int(round(pos / 1000.0 * self.fps))
        ft = int(round(dur / 1000.0 * self.fps))
        self.time_label.setText(f"프레임 {f} / {ft}   ·   {pos / 1000:.2f}초 / {dur / 1000:.2f}초")

    def _on_dur(self, d):
        self.slider.setRange(0, d)

    def _on_state(self, st):
        playing = st == QMediaPlayer.PlaybackState.PlayingState
        self.btn_play.setText("⏸ 일시정지" if playing else "▶ 재생")

    def _on_error(self, err, msg=""):
        if err == QMediaPlayer.Error.NoError:
            return
        QMessageBox.warning(self, "재생 오류", f"이 클립을 재생할 수 없어요.\n{msg}\n\n[다른 플레이어로 열기]를 눌러 보세요.")

    def prev_clip(self):
        if self.idx > 0:
            self.load(self.idx - 1)

    def next_clip(self):
        if self.idx < len(self.clips) - 1:
            self.load(self.idx + 1)

    # ---------------------------------------------------------------- 분류/삭제
    def _apply_label(self, name):
        library.set_manual_label(self.cur, name)
        self.changed = True
        self.load(self.idx)
        if self.on_change:
            self.on_change()

    def _on_label(self, i):
        name = self.label_combo.itemData(i)
        if name == NEW_LABEL:
            text, ok = QInputDialog.getText(self, "새 분류", "새 분류 이름 (예: 강공격, 패링, 원거리공격)")
            text = text.strip()
            if not ok or not text:
                self._fill_labels()
                return
            if text not in label_names(self.main.s):
                self.main.s["labels"].append({"name": text, "prompts": []})
                self.main.save_settings()
            name = text
        self._apply_label(name)

    def quick_label(self, n):
        if n - 1 < self.label_combo.count() - 1:
            name = self.label_combo.itemData(n - 1)
            if name and name != NEW_LABEL:
                self._apply_label(name)

    def edit_current(self):
        if not self.clips:
            return
        # 편집 중 파일을 덮어쓸 수 있도록 재생을 멈추고 파일을 놓는다
        self.player.stop()
        self.player.setSource(QUrl())
        if self.main.edit_clip(self.cur):
            self.changed = True
            fresh = library.load_clip(self.cur["_json"])
            if fresh:
                self.clips[self.idx] = fresh
        self.load(self.idx)

    def retime_current(self):
        if not self.clips:
            return
        self.player.stop()
        self.player.setSource(QUrl())
        if self.main.retime_clips([self.cur]):
            self.changed = True
            fresh = library.load_clip(self.cur["_json"])
            if fresh:
                self.clips[self.idx] = fresh
        self.load(self.idx)

    def split_hits_current(self):
        if not self.clips:
            return
        self.player.stop()
        self.player.setSource(QUrl())
        if self.main.split_hits_clips([self.cur]):
            self.changed = True   # 원래 클립을 지웠을 수 있으니 목록은 라이브러리에서 새로 읽는다
            self.accept()
            return
        self.load(self.idx)

    def delete_current(self):
        if not self.clips:
            return
        if QMessageBox.question(self, "삭제", "이 클립을 삭제할까요? 되돌릴 수 없어요.") != QMessageBox.StandardButton.Yes:
            return
        self.player.stop()
        self.player.setSource(QUrl())
        if not library.delete_clip(self.cur):
            QMessageBox.warning(self, "삭제 실패", "다른 프로그램이 이 영상을 열고 있어서 지우지 못했어요. 닫은 뒤 다시 시도해 주세요.")
            self.load(self.idx)
            return
        self.changed = True
        del self.clips[self.idx]
        if not self.clips:
            self.accept()
            return
        self.load(min(self.idx, len(self.clips) - 1))

    def closeEvent(self, e):
        self.player.stop()
        super().closeEvent(e)

    def done(self, r):
        self.player.stop()
        self.player.setSource(QUrl())
        super().done(r)
