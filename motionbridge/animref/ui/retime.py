"""속도·편집 화면: 부분 배속/슬로모션(타임 스트레치), 부드러운 가속·감속, 조각 자르기·삭제·복제·순서 바꾸기,
정지 프레임, 다른 클립 이어붙이기. 위쪽 영상으로 미리 보고, 아래 '조각 줄'이 결과 영상의 구성이다."""
import copy
from pathlib import Path

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QDialog, QDoubleSpinBox, QFileDialog,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPushButton, QVBoxLayout, QWidget)

from ..core import library, media
from ..core import retime as R

from .widgets import muted

PRESETS = [0.25, 0.5, 1.0, 2.0, 4.0]


def fmt_speed(v):
    return f"×{v:g}"


class PieceStrip(QWidget):
    """결과 영상을 조각 블록으로 보여주는 줄. 블록 너비 = 결과 영상에서 차지하는 시간.
    파랑 = 느리게, 주황 = 빠르게, 회색 = 정지 프레임. 클릭하면 그 조각의 그 위치로 이동한다."""
    picked = Signal(int, float)

    H = 96

    def __init__(self, model):
        super().__init__()
        self.model = model
        self.sel = 0
        self.frac = 0.0
        self.marks = (None, None)      # 선택한 조각 안의 시작/끝 표시 (0~1 비율)
        self._rects = []
        self.setMinimumHeight(self.H)

    @staticmethod
    def color_of(p):
        if p.hold > 0:
            return QColor(120, 128, 145)
        if abs(p.speed - 1.0) < 1e-6:
            return QColor(88, 104, 150)
        if p.speed < 1.0:   # 느릴수록 진한 파랑
            k = min(1.0, (1.0 - p.speed) / 0.9)
            return QColor(int(70 - 30 * k), int(140 - 50 * k), int(235 - 20 * k))
        k = min(1.0, (p.speed - 1.0) / 3.0)   # 빠를수록 진한 주황
        return QColor(int(235), int(170 - 60 * k), int(70 - 30 * k))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#15171c"))
        pieces = self.model.pieces
        n, gap, minw = len(pieces), 3, 30.0
        avail = max(10.0, self.width() - gap * (n + 1))
        total = max(1e-6, self.model.total())
        widths = [max(minw, avail * pc.out_len() / total) for pc in pieces]
        if sum(widths) > avail:
            f = avail / sum(widths)
            widths = [w * f for w in widths]
        self._rects = []
        f = QFont(p.font())
        f.setPointSize(9)
        p.setFont(f)
        x = float(gap)
        for i, (pc, w) in enumerate(zip(pieces, widths)):
            r = QRectF(x, 8, w, self.H - 34)
            self._rects.append(r)
            p.setBrush(self.color_of(pc))
            p.setPen(QPen(QColor("#ffffff"), 3) if i == self.sel else QPen(QColor(0, 0, 0, 90), 1))
            p.drawRoundedRect(r, 5, 5)
            p.setPen(QColor("#ffffff"))
            label = f"⏸ {pc.hold:g}초" if pc.hold > 0 else fmt_speed(pc.speed)
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), label)
            p.setPen(QColor("#9aa3b5"))
            p.drawText(QRectF(x, self.H - 24, max(w, 40), 18), int(Qt.AlignmentFlag.AlignLeft),
                       f"{pc.out_len():.1f}초")
            if i == self.sel and pc.hold <= 0:
                a, b = self.marks
                if a is not None:
                    p.setPen(QPen(QColor("#46c37b"), 3))
                    xa = r.left() + r.width() * a
                    p.drawLine(QPointF(xa, r.top() - 4), QPointF(xa, r.bottom() + 4))
                if b is not None:
                    p.setPen(QPen(QColor("#ff6b6b"), 3))
                    xb = r.left() + r.width() * b
                    p.drawLine(QPointF(xb, r.top() - 4), QPointF(xb, r.bottom() + 4))
                if a is not None and b is not None:
                    p.fillRect(QRectF(r.left() + r.width() * min(a, b), r.top(), r.width() * abs(b - a), r.height()),
                               QColor(255, 255, 255, 50))
                p.setPen(QPen(QColor("#ffffff"), 2))
                xp = r.left() + r.width() * self.frac
                p.drawLine(QPointF(xp, r.top() - 6), QPointF(xp, r.bottom() + 6))
            x += w + gap
        p.end()

    def mousePressEvent(self, e):
        pos = e.position()
        for i, r in enumerate(self._rects):
            if r.left() - 1 <= pos.x() <= r.right() + 1:
                self.picked.emit(i, min(1.0, max(0.0, (pos.x() - r.left()) / max(1.0, r.width()))))
                return


class ClipPicker(QDialog):
    """붙일 클립 고르기: 라이브러리의 클립(여러 개 선택 가능) 또는 컴퓨터의 영상 파일."""

    def __init__(self, main, parent=None):
        super().__init__(parent)
        self.main = main
        self.items = []     # [{"path", "name"}]
        self.setWindowTitle("이어붙일 클립 고르기")
        self.resize(560, 520)
        lay = QVBoxLayout(self)
        lay.addWidget(muted("라이브러리 클립을 골라서 [붙이기]를 누르세요. (Ctrl/Shift로 여러 개 선택 · 고른 순서가 아니라 목록 순서로 붙어요)"))
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for m in sorted(library.load_clips(library.game_dir(main.s)), key=lambda m: Path(m["_json"]).stem):
            it = QListWidgetItem(f"{library.final_label(m)}   ·   {float(m.get('duration', 0)):.1f}초   ·   {Path(m['_json']).stem}")
            it.setData(Qt.ItemDataRole.UserRole, m)
            self.list.addItem(it)
        self.list.itemDoubleClicked.connect(lambda _it: self.accept_selected())
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        b_file = QPushButton("📁 컴퓨터의 영상 파일에서…")
        b_file.clicked.connect(self.pick_files)
        b_ok = QPushButton("붙이기")
        b_ok.setObjectName("primary")
        b_ok.clicked.connect(self.accept_selected)
        b_cancel = QPushButton("취소")
        b_cancel.clicked.connect(self.reject)
        row.addWidget(b_file)
        row.addStretch(1)
        row.addWidget(b_cancel)
        row.addWidget(b_ok)
        lay.addLayout(row)

    def accept_selected(self):
        for it in self.list.selectedItems():
            m = it.data(Qt.ItemDataRole.UserRole)
            self.items.append({"path": str(library.file_of(m, "video")), "name": Path(m["_json"]).stem})
        if not self.items:
            QMessageBox.information(self, "클립 고르기", "붙일 클립을 먼저 선택하세요.")
            return
        self.accept()

    def pick_files(self):
        exts = " ".join(f"*{e}" for e in sorted(media.VIDEO_EXTS))
        start = self.main.s.get("last_dir") or str(Path.home())
        files, _ = QFileDialog.getOpenFileNames(self, "붙일 영상 선택", start, f"영상 ({exts})")
        for f in files:
            self.items.append({"path": f, "name": Path(f).name})
        if self.items:
            self.accept()


class RetimeDialog(QDialog):
    def __init__(self, main, metas):
        super().__init__(main)
        self.main = main
        self.metas = list(metas)
        self.base = self.metas[0]
        self.changed = False
        self.sel = 0
        self.t = 0.0                 # 지금 보고 있는 원본 시각
        self.seq = False             # 조각을 이어서 재생 중
        self._token = 0
        self._pending = None
        self._want_play = False
        self._loaded = None
        self.mark_in = self.mark_out = None

        srcs = []
        for m in self.metas:
            path = str(library.file_of(m, "video"))
            info = media.probe(path)
            srcs.append({"path": path, "duration": info.duration, "fps": info.fps, "name": Path(m["_json"]).stem})
        self.model = R.EditModel(srcs)
        self.setWindowTitle("속도·편집 — " + (srcs[0]["name"] if len(srcs) == 1 else f"클립 {len(srcs)}개 이어붙이기"))
        self.resize(1200, 880)

        self.video = QVideoWidget()
        self.video.setStyleSheet("background: black;")
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setMuted(True)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self._on_pos)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.mediaStatusChanged.connect(self._on_status)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 10)
        root.addWidget(self.video, 1)

        self.strip = PieceStrip(self.model)
        self.strip.picked.connect(lambda i, f: (self._drop_focus(), self.select(i, f)))
        root.addWidget(self.strip)
        self.info = QLabel("")
        root.addWidget(self.info)

        # ---- 재생
        r1 = QHBoxLayout()
        self.btn_play = self._btn("▶ 이 조각부터 재생", self.toggle_play)
        self.btn_play.setMinimumWidth(150)
        r1.addWidget(self.btn_play)
        r1.addWidget(self._btn("⏮ 처음부터", lambda: self.play_from(0)))
        r1.addWidget(self._btn("◀ 1프레임", lambda: self.step(-1)))
        r1.addWidget(self._btn("1프레임 ▶", lambda: self.step(1)))
        r1.addWidget(self._btn("↑ 이전 조각", lambda: self.select(self.sel - 1)))
        r1.addWidget(self._btn("다음 조각 ↓", lambda: self.select(self.sel + 1)))
        ck = QCheckBox("소리")
        ck.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        ck.toggled.connect(lambda v: self.audio.setMuted(not v))
        r1.addWidget(ck)
        r1.addStretch(1)
        root.addLayout(r1)

        # ---- 속도
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("속도"))
        for v in PRESETS:
            r2.addWidget(self._btn(fmt_speed(v), lambda _=False, v=v: self.apply_speed(v)))
        self.speed = QDoubleSpinBox()
        self.speed.setRange(R.SPEED_MIN, R.SPEED_MAX)
        self.speed.setDecimals(2)
        self.speed.setSingleStep(0.05)
        self.speed.setValue(0.5)
        self.speed.setPrefix("×")
        r2.addWidget(self.speed)
        r2.addWidget(self._btn("적용", lambda: self.apply_speed(self.speed.value())))
        r2.addWidget(self._btn("모든 조각에", self.apply_all_speed))
        r2.addSpacing(12)
        r2.addWidget(QLabel("부드럽게 바꾸기"))
        self.ramp_a = QDoubleSpinBox()
        self.ramp_b = QDoubleSpinBox()
        for sp, v in ((self.ramp_a, 1.0), (self.ramp_b, 0.25)):
            sp.setRange(R.SPEED_MIN, R.SPEED_MAX)
            sp.setDecimals(2)
            sp.setSingleStep(0.05)
            sp.setValue(v)
            sp.setPrefix("×")
        r2.addWidget(self.ramp_a)
        r2.addWidget(QLabel("→"))
        r2.addWidget(self.ramp_b)
        r2.addWidget(self._btn("적용", self.apply_ramp))
        r2.addStretch(1)
        root.addLayout(r2)

        # ---- 구간 표시 + 편집
        r3 = QHBoxLayout()
        r3.addWidget(self._btn("[ 구간 시작 (I)", self.set_in))
        r3.addWidget(self._btn("구간 끝 (O) ]", self.set_out))
        r3.addWidget(self._btn("표시 지우기", self.clear_marks))
        self.mark_label = muted("")
        r3.addWidget(self.mark_label, 1)
        root.addLayout(r3)

        r4 = QHBoxLayout()
        self.btn_split = self._btn("✂ 여기서 나누기 (S)", self.split_here)
        self.btn_del = self._btn("🗑 조각 삭제 (Del)", self.delete_piece)
        self.btn_dup = self._btn("⧉ 복제 (D)", self.duplicate_piece)
        self.btn_left = self._btn("◀ 앞으로", lambda: self.move_piece(-1))
        self.btn_right = self._btn("뒤로 ▶", lambda: self.move_piece(1))
        for b in (self.btn_split, self.btn_del, self.btn_dup, self.btn_left, self.btn_right):
            r4.addWidget(b)
        r4.addSpacing(12)
        r4.addWidget(QLabel("정지"))
        self.hold = QDoubleSpinBox()
        self.hold.setRange(0.1, 10.0)
        self.hold.setDecimals(1)
        self.hold.setSingleStep(0.1)
        self.hold.setValue(1.0)
        self.hold.setSuffix("초")
        r4.addWidget(self.hold)
        r4.addWidget(self._btn("⏸ 여기에 정지 프레임", self.add_hold))
        self.btn_hold_set = self._btn("시간 바꾸기", self.change_hold)
        r4.addWidget(self.btn_hold_set)
        r4.addStretch(1)
        self.btn_undo = self._btn("↶ 되돌리기", self.undo)
        self.btn_undo.setToolTip("Ctrl+Z")
        self.btn_redo = self._btn("↷ 복구", self.redo)
        self.btn_redo.setToolTip("Ctrl+Shift+Z (또는 Ctrl+Y)")
        r4.addWidget(self.btn_undo)
        r4.addWidget(self.btn_redo)
        root.addLayout(r4)

        r5 = QHBoxLayout()
        r5.addWidget(self._btn("＋ 다른 클립 이어붙이기…", self.add_clips))
        r5.addStretch(1)
        self.btn_over = self._btn("덮어쓰기", self.save_overwrite)
        self.btn_over.setEnabled(True)
        r5.addWidget(self.btn_over)
        self.btn_new = QPushButton("💾 새 클립으로 저장 (Enter)")
        self.btn_new.setObjectName("primary")
        self.btn_new.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_new.clicked.connect(self.save_new)
        r5.addWidget(self.btn_new)
        r5.addWidget(self._btn("닫기", self.reject))
        root.addLayout(r5)
        root.addWidget(muted("Space 재생/정지 · ←→ 1프레임 · ↑↓ 조각 이동 · I/O 구간 표시(구간만 속도 적용) · S 나누기 · D 복제 · "
                             "Delete 삭제 · Ctrl+Z 되돌리기 · Ctrl+Shift+Z 복구 · Enter 새 클립으로 저장.  "
                             "미리보기는 근사치예요. 정확한 결과는 저장한 클립을 재생해서 확인하세요."))

        for key, fn in [("Space", self.toggle_play), ("Left", lambda: self.step(-1)), ("Right", lambda: self.step(1)),
                        ("Up", lambda: self.select(self.sel - 1)), ("Down", lambda: self.select(self.sel + 1)),
                        ("I", self.set_in), ("O", self.set_out), ("S", self.split_here), ("D", self.duplicate_piece),
                        ("Delete", self.delete_piece), ("Ctrl+Z", self.undo), ("Ctrl+Y", self.redo),
                        ("Ctrl+Shift+Z", self.redo), ("Return", self.save_new), ("Enter", self.save_new)]:
            QShortcut(QKeySequence(key), self, activated=fn)

        # 숫자 입력칸이 키보드 포커스를 잡고 있으면 Qt가 Space·Ctrl+Z 같은 키를 '글자 입력/편집'으로 먼저 가져가서
        # 창 단축키가 안 먹는다. 그래서 (1) 창이 열릴 때 입력칸으로 자동 포커스가 가지 않게 클릭했을 때만 포커스를 받게 하고,
        # (2) 입력칸에 포커스가 있어도 Space·되돌리기·복구는 창 단축키가 처리하게 한다.
        for sp in (self.speed, self.ramp_a, self.ramp_b, self.hold):
            sp.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            sp.lineEdit().installEventFilter(self)

        self.select(0)

    def _btn(self, text, fn):
        b = QPushButton(text)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # 입력칸에 숫자를 치다가 버튼을 눌러도, 그다음 단축키(Space, I/O, ←→ …)가 다시 먹도록 입력칸 포커스를 놓는다
        b.clicked.connect(lambda *_: (self._drop_focus(), fn()))
        return b

    def _drop_focus(self):
        w = QApplication.focusWidget()
        if w is not None and w is not self and self.isAncestorOf(w):
            w.clearFocus()

    def eventFilter(self, obj, e):
        if isinstance(obj, QLineEdit):
            t = e.type()
            if t == QEvent.Type.ShortcutOverride:
                k, ctrl = e.key(), bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
                if k == Qt.Key.Key_Space or (ctrl and k in (Qt.Key.Key_Z, Qt.Key.Key_Y)):
                    e.ignore()          # 입력칸이 가져가지 말고 창 단축키(재생/되돌리기/복구)로 보냄
                    return True
                if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                    e.accept()          # 숫자 입력 후 Enter는 '값 확정'. 새 클립 저장으로 넘어가지 않게 함
                    return False
            elif t == QEvent.Type.KeyPress and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                QTimer.singleShot(0, self._drop_focus)   # 값을 확정한 뒤 포커스를 놓아 단축키가 다시 먹게
        return super().eventFilter(obj, e)

    @property
    def piece(self):
        return self.model.pieces[self.sel]

    # ---------------------------------------------------------------- 선택/재생
    def _rate(self, p):
        return max(0.1, min(8.0, p.speed)) if p.hold <= 0 else 1.0

    def _ensure_source(self, k):
        if self._loaded == k:
            return False
        self._loaded = k
        self.player.setSource(QUrl.fromLocalFile(self.model.sources[k]["path"]))
        return True

    def select(self, i, frac=0.0, play=False):
        i = max(0, min(int(i), len(self.model.pieces) - 1))
        if i != self.sel:
            self.clear_marks(refresh=False)
        self.sel = i
        self._token += 1
        p = self.piece
        t = p.a + (p.b - p.a) * frac
        self.t = t
        self.strip.sel, self.strip.frac = i, frac
        self.player.setPlaybackRate(self._rate(p))
        if self._ensure_source(p.src):
            self._pending, self._want_play = t, play
        else:
            self.player.setPosition(int(t * 1000))
            if play:
                self._start_playing()
            else:
                self.player.pause()
        self._refresh()

    def _start_playing(self):
        p = self.piece
        if p.hold > 0:   # 정지 프레임: 영상을 멈춘 채로 지정한 시간만 기다린 뒤 다음 조각
            self.player.pause()
            token = self._token
            QTimer.singleShot(int(p.hold * 1000), lambda: self._hold_done(token))
        else:
            self.player.play()

    def _hold_done(self, token):
        if token == self._token and self.seq:
            self._advance()

    def _advance(self):
        if self.sel + 1 >= len(self.model.pieces):
            self.seq = False
            self.player.pause()
            self._refresh()
            return
        self.select(self.sel + 1, 0.0, play=True)

    def play_from(self, i, frac=0.0):
        self.seq = True
        self.select(i, frac, play=True)

    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState or (self.seq and self.piece.hold > 0):
            self.seq = False
            self._token += 1
            self.player.pause()
        else:
            f = self.strip.frac
            self.play_from(self.sel, 0.0 if f > 0.98 else f)
        self._refresh()

    def step(self, n):
        self.seq = False
        self.player.pause()
        p = self.piece
        fps = float(self.model.sources[p.src].get("fps") or 30.0)
        t = max(p.a, min(p.b, self.t + n / fps))
        self.t = t
        self.player.setPosition(int(t * 1000))

    def _on_pos(self, ms):
        if self._pending is not None:
            return
        t = ms / 1000.0
        p = self.piece
        self.t = t
        if p.hold <= 0:
            self.strip.frac = min(1.0, max(0.0, (t - p.a) / max(1e-6, p.b - p.a)))
            if self.seq and t >= p.b - 0.03:
                self._advance()
                return
        self.strip.update()
        self._refresh_info()

    def _on_state(self, _st):
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.btn_play.setText("⏸ 일시정지" if playing or (self.seq and self.piece.hold > 0) else "▶ 이 조각부터 재생")

    def _on_status(self, st):
        MS = QMediaPlayer.MediaStatus
        if st in (MS.LoadedMedia, MS.BufferedMedia) and self._pending is not None:
            t, self._pending = self._pending, None
            self.player.setPosition(int(t * 1000))
            if self._want_play:
                self._start_playing()
            else:
                self.player.pause()
        elif st == MS.EndOfMedia and self.seq:
            self._advance()

    # ---------------------------------------------------------------- 표시
    def _refresh_info(self):
        m = self.model
        a, b = m.layout()[self.sel]
        p = self.piece
        what = f"정지 프레임 {p.hold:g}초" if p.hold > 0 else f"원본 {p.a:.2f}~{p.b:.2f}초 · {fmt_speed(p.speed)}"
        src = m.sources[p.src]["name"] if len(m.sources) > 1 else ""
        self.info.setText(f"결과 {a:.2f}~{b:.2f}초 / 전체 {m.total():.2f}초   ·   조각 {self.sel + 1}/{len(m.pieces)}   ·   {what}"
                          + (f"   ·   {src}" if src else ""))

    def _refresh(self):
        self._refresh_info()
        m, p = self.model, self.piece
        self.player.setPlaybackRate(self._rate(p))   # 속도를 바꾸면 미리보기에도 바로 반영
        hold = p.hold > 0
        self.btn_split.setEnabled(not hold)
        self.btn_del.setEnabled(len(m.pieces) > 1)
        self.btn_left.setEnabled(self.sel > 0)
        self.btn_right.setEnabled(self.sel < len(m.pieces) - 1)
        self.btn_hold_set.setEnabled(hold)
        self.btn_undo.setEnabled(bool(m._undo))
        self.btn_redo.setEnabled(bool(m._redo))
        self.btn_over.setEnabled(len(self.metas) == 1)
        self.btn_over.setToolTip("" if len(self.metas) == 1 else "여러 클립을 이어붙일 때는 새 클립으로만 저장할 수 있어요")
        a, b = self.mark_in, self.mark_out
        if a is not None and b is not None:
            self.mark_label.setText(f"구간 {a:.2f}~{b:.2f}초 (원본 시각) — 속도를 고르면 이 구간에만 적용돼요")
        elif a is not None:
            self.mark_label.setText(f"구간 시작 {a:.2f}초 — 이제 끝(O)을 지정하세요")
        else:
            self.mark_label.setText("구간을 표시하면 그 부분만 속도를 바꿀 수 있어요. 표시가 없으면 선택한 조각 전체에 적용돼요.")
        self.strip.marks = self._mark_fracs()
        self._on_state(None)
        self.strip.update()

    def _mark_fracs(self):
        p = self.piece
        if p.hold > 0 or p.b <= p.a:
            return (None, None)
        f = lambda t: None if t is None else min(1.0, max(0.0, (t - p.a) / (p.b - p.a)))
        return (f(self.mark_in), f(self.mark_out))

    def _say(self, text):
        self.mark_label.setText(text)

    # ---------------------------------------------------------------- 구간 표시
    def set_in(self):
        p = self.piece
        if p.hold > 0:
            return
        self.mark_in = max(p.a, min(p.b, self.t))
        if self.mark_out is not None and self.mark_out <= self.mark_in:
            self.mark_out = None
        self._refresh()

    def set_out(self):
        p = self.piece
        if p.hold > 0:
            return
        self.mark_out = max(p.a, min(p.b, self.t))
        if self.mark_in is not None and self.mark_in >= self.mark_out:
            self.mark_in = None
        self._refresh()

    def clear_marks(self, refresh=True):
        self.mark_in = self.mark_out = None
        if refresh:
            self._refresh()

    def _range(self):
        if self.mark_in is not None and self.mark_out is not None:
            return min(self.mark_in, self.mark_out), max(self.mark_in, self.mark_out)
        return None

    # ---------------------------------------------------------------- 편집 동작
    def apply_speed(self, v):
        if self.piece.hold > 0:
            self._say("정지 프레임에는 속도가 없어요. [시간 바꾸기]를 쓰세요.")
            return
        rng = self._range()
        if rng:
            mid = self.model.range_speed(self.sel, rng[0], rng[1], v)
            if mid < 0:
                self._say("구간이 너무 짧거나 조각이 너무 많아요.")
                return
            self.clear_marks(refresh=False)
            self.select(mid)
        else:
            self.model.set_speed(self.sel, v)
            self._refresh()

    def apply_all_speed(self):
        self.model.set_all_speed(self.speed.value())
        self._refresh()

    def apply_ramp(self):
        if self.piece.hold > 0:
            return
        s0, s1 = self.ramp_a.value(), self.ramp_b.value()
        rng = self._range()
        if rng:
            mid = self.model.range_ramp(self.sel, rng[0], rng[1], s0, s1)
            ok = mid >= 0
        else:
            ok = self.model.ramp(self.sel, s0, s1)
            mid = self.sel
        if not ok:
            self._say("부드럽게 바꾸기에는 구간이 너무 짧거나 조각이 너무 많아요.")
            return
        self.clear_marks(refresh=False)
        self.select(mid)

    def split_here(self):
        p = self.piece
        if p.hold > 0:
            return
        if not self.model.split(self.sel, self.t):
            self._say("나눌 위치가 조각의 시작이나 끝에 너무 가까워요. 재생 위치를 조각 안쪽으로 옮겨 주세요.")
            return
        self.clear_marks(refresh=False)
        self.select(self.sel, 1.0)   # 앞 조각의 끝에 머무른다 (다음 위치로 이동해 또 나누기 쉽게)

    def delete_piece(self):
        if self.model.delete(self.sel):
            self.clear_marks(refresh=False)
            self.select(min(self.sel, len(self.model.pieces) - 1))
        else:
            self._say("마지막 남은 조각은 지울 수 없어요.")

    def duplicate_piece(self):
        if self.model.duplicate(self.sel):
            self.select(self.sel + 1)

    def move_piece(self, d):
        j = self.model.move(self.sel, d)
        if j != self.sel:
            self.sel = j
            self.select(j, self.strip.frac)

    def add_hold(self):
        p = self.piece
        if p.hold > 0:
            self._say("정지 프레임은 일반 조각에서만 넣을 수 있어요.")
            return
        k = self.model.add_hold(self.sel, self.t, self.hold.value())
        if k < 0:
            self._say("정지 프레임을 넣을 수 없어요. (조각이 너무 많아요)")
            return
        self.select(k)

    def change_hold(self):
        if self.model.set_hold(self.sel, self.hold.value()):
            self._refresh()

    def undo(self):
        if self.model.undo():
            self.clear_marks(refresh=False)
            self.select(min(self.sel, len(self.model.pieces) - 1))

    def redo(self):
        if self.model.redo():
            self.clear_marks(refresh=False)
            self.select(min(self.sel, len(self.model.pieces) - 1))

    def add_clips(self):
        dlg = ClipPicker(self.main, self)
        if not dlg.exec():
            return
        for it in dlg.items:
            try:
                info = media.probe(it["path"])
            except Exception as e:
                QMessageBox.warning(self, "영상 열기 실패", f"{it['name']}\n{e}")
                continue
            if info.duration <= 0.1:
                continue
            if not self.model.append_source(it["path"], info.duration, it["name"]):
                QMessageBox.information(self, "이어붙이기", f"조각이 너무 많아요 (최대 {R.MAX_PIECES}개).")
                break
            self.model.sources[-1]["fps"] = info.fps
        self.select(len(self.model.pieces) - 1)

    # ---------------------------------------------------------------- 저장
    def _check_changed(self):
        if self.model.is_untouched():
            QMessageBox.information(self, "저장", "아직 바뀐 게 없어요. 속도를 바꾸거나 자르고 붙인 뒤에 저장하세요.")
            return False
        return True

    def _release(self):
        self.seq = False
        self._token += 1
        self.player.stop()
        self.player.setSource(QUrl())
        self._loaded = None

    def save_new(self):
        if not self._check_changed():
            return
        suffix = "join" if len(self.model.sources) > 1 else "retime"
        self.seq = False
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            jp = R.save_new(self.base, self.model, copy.deepcopy(self.main.s), suffix=suffix, log=self.main.log)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "저장 실패", str(e))
            return
        QApplication.restoreOverrideCursor()
        self.changed = True
        self._say(f"✅ 새 클립으로 저장했어요 ({Path(jp).stem}). 라이브러리에서 볼 수 있어요. 계속 편집해도 돼요.")

    def save_overwrite(self):
        if len(self.metas) != 1 or not self._check_changed():
            return
        if QMessageBox.question(self, "덮어쓰기", "이 클립을 편집한 결과로 덮어쓸까요?\n원래 속도와 길이로는 되돌릴 수 없어요. "
                                "(원본 영상이 남아 있어도 이 클립은 원본 기준으로 늘릴 수 없게 돼요)\n\n"
                                "원래 클립을 남기려면 [새 클립으로 저장]을 쓰세요.") != QMessageBox.StandardButton.Yes:
            return
        self._release()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            R.save_overwrite(self.base, self.model, copy.deepcopy(self.main.s), log=self.main.log)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "저장 실패", str(e))
            self.select(self.sel)
            return
        QApplication.restoreOverrideCursor()
        self.changed = True
        self.accept()

    def done(self, r):
        self.seq = False
        self._token += 1
        self.player.stop()
        self.player.setSource(QUrl())
        super().done(r)
