"""타임라인 편집기: 원본 영상을 보면서 직접 클립을 만들고, AI 책갈피로 바로 이동한다."""
import copy
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QProgressBar, QPushButton, QSplitter,
                               QVBoxLayout, QWidget)

from ..core import bookmarks as BM
from ..core import library, media
from ..core.segment import make_manual_clip
from ..core.settings import label_names

from .widgets import Job, muted

SPEEDS = [("0.1배", 0.1), ("0.25배", 0.25), ("0.5배", 0.5), ("1배", 1.0), ("2배", 2.0)]
KIND_COLORS = {"auto": "#ffb547", "cine": "#c792ff", "weak": "#7d8597", "user": "#4fd1c5"}


def fmt_t(sec):
    sec = max(0.0, sec)
    m, s = divmod(sec, 60)
    return f"{int(m):02d}:{s:06.3f}"


class Timeline(QWidget):
    """위: 책갈피(썸네일) 줄 / 가운데: 영상 막대(기존 클립, 선택 구간, 재생 위치) / 아래: 시간 눈금.
    마우스 휠로 확대/축소, 클릭으로 이동, 시작/끝 표시와 책갈피는 드래그로 옮긴다."""
    seek = Signal(float)
    rangeChanged = Signal()
    bookmarkClicked = Signal(str)
    bookmarkMoved = Signal(str, float)
    bookmarkMenu = Signal(str, object)
    handleMoved = Signal(float)   # 시작/끝 선을 끄는 중: 그 위치의 프레임을 보여주기 위해
    handleGrabbed = Signal()      # 시작/끝 선을 잡는 순간 (되돌리기용 기록 시점)

    LANE = 46   # 책갈피 줄 높이
    BAR = 30    # 영상 막대 높이
    GRAB = 10   # 시작/끝 선을 잡을 수 있는 좌우 범위(픽셀)
    MIN_LEN = 0.1

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(self.LANE + self.BAR + 30)
        self.setMouseTracking(True)
        self.dur = 1.0
        self.pos = 0.0
        self.v0, self.v1 = 0.0, 1.0
        self.inp = None
        self.out = None
        self.clips = []       # (a, b) 이미 만든 클립
        self.marks = []       # 책갈피 dict
        self.show_hidden = False
        self._pix = {}
        self._hits = []
        self._drag = None
        self.fps = 30.0
        self._hover = None    # 마우스가 올라가 있는 선: "in" / "out"

    def _snap(self, t):
        f = self.fps or 30.0
        return round(t * f) / f

    def _hit_handle(self, pos):
        """시작(초록)/끝(빨강) 선 근처인지. 책갈피 줄 아래쪽부터 전체 높이에서 잡힌다."""
        if pos.y() < self.LANE - 4:
            return None
        found = []
        for name, t in (("in", self.inp), ("out", self.out)):
            if t is not None:
                d = abs(self.x_of(t) - pos.x())
                if d <= self.GRAB:
                    found.append((d, name, self.x_of(t)))
        if not found:
            return None
        if len(found) == 2 and abs(found[0][0] - found[1][0]) < 1e-6:   # 두 선이 겹쳐 있으면 누른 쪽으로
            return "in" if pos.x() < found[0][2] else "out"
        return min(found)[1]

    # ---------------------------------------------------------------- 좌표
    def set_duration(self, d):
        self.dur = max(0.1, d)
        self.v0, self.v1 = 0.0, self.dur
        self.update()

    def x_of(self, t):
        return (t - self.v0) / max(1e-6, self.v1 - self.v0) * self.width()

    def t_of(self, x):
        return max(0.0, min(self.dur, self.v0 + x / max(1, self.width()) * (self.v1 - self.v0)))

    def set_pos(self, t):
        self.pos = t
        span = self.v1 - self.v0
        if span < self.dur and not (self.v0 <= t <= self.v1):  # 재생 위치를 따라 화면 이동
            self.v0 = max(0.0, min(self.dur - span, t - span * 0.2))
            self.v1 = self.v0 + span
        self.update()

    def visible_marks(self):
        return [b for b in self.marks if self.show_hidden or not b.get("hidden")]

    def _pixmap(self, path):
        if path not in self._pix:
            pm = QPixmap(path) if path else QPixmap()
            self._pix[path] = pm.scaledToHeight(30, Qt.TransformationMode.SmoothTransformation) if not pm.isNull() else pm
        return self._pix[path]

    # ---------------------------------------------------------------- 그리기
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, lane, bar = self.width(), self.LANE, self.BAR
        by = lane + 4
        p.fillRect(self.rect(), QColor("#15171c"))
        p.fillRect(QRectF(0, by, w, bar), QColor("#232733"))

        for a, b in self.clips:  # 이미 만든 클립
            x0, x1 = self.x_of(a), self.x_of(b)
            if x1 >= 0 and x0 <= w:
                p.fillRect(QRectF(x0, by + 3, max(2.0, x1 - x0), bar - 6), QColor(91, 124, 250, 120))

        if self.inp is not None and self.out is not None:  # 선택 구간
            x0, x1 = self.x_of(min(self.inp, self.out)), self.x_of(max(self.inp, self.out))
            p.fillRect(QRectF(x0, by, max(2.0, x1 - x0), bar), QColor(255, 181, 71, 90))
        active = self._drag[0] if self._drag and self._drag[0] in ("in", "out") else self._hover
        for name, t, color in (("in", self.inp, "#46c37b"), ("out", self.out, "#ff6b6b")):
            if t is not None:
                x = self.x_of(t)
                on = active == name
                p.setPen(QPen(QColor(color), 4 if on else 3))
                p.drawLine(QPointF(x, by - 2), QPointF(x, by + bar + 2))
                # 잡는 손잡이: 선 가운데의 둥근 막대 (시작은 오른쪽, 끝은 왼쪽으로 꺾어서 구간 안쪽을 가리킴)
                gw, gh = (14 if on else 12), 22
                grip = QRectF(x - gw / 2, by + bar / 2 - gh / 2, gw, gh)
                p.setBrush(QColor(color))
                p.setPen(QPen(QColor("#ffffff"), 2 if on else 1))
                p.drawRoundedRect(grip, 3, 3)
                p.setPen(QPen(QColor("#ffffff"), 1))
                for dx in (-2, 2):
                    p.drawLine(QPointF(x + dx, grip.top() + 5), QPointF(x + dx, grip.bottom() - 5))
        p.setBrush(Qt.BrushStyle.NoBrush)

        # 눈금
        span = self.v1 - self.v0
        step = next((s for s in (0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 600) if span / s <= 12), 1200)
        p.setPen(QColor("#6b7387"))
        f = QFont(p.font())
        f.setPointSize(8)
        p.setFont(f)
        t = (int(self.v0 / step)) * step
        while t <= self.v1:
            x = self.x_of(t)
            p.drawLine(QPointF(x, by + bar), QPointF(x, by + bar + 5))
            p.drawText(QPointF(x + 2, by + bar + 16), fmt_t(t)[:-4] if step >= 1 else fmt_t(t)[:-2])
            t += step

        # 책갈피
        self._hits = []
        last_thumb_x = -1e9
        for b in self.visible_marks():
            x = self.x_of(b.get("_preview", b["t"]))
            if x < -40 or x > w + 40:
                continue
            color = QColor(KIND_COLORS.get(b.get("kind"), "#ffb547"))
            if b.get("hidden"):
                color.setAlpha(90)
            p.setPen(QPen(color, 2))
            p.drawLine(QPointF(x, lane - 8), QPointF(x, by + bar))
            p.setBrush(color)
            p.drawEllipse(QPointF(x, lane - 8), 4, 4)
            rect = QRectF(x - 5, 0, 10, lane)
            pm = self._pixmap(b.get("thumb", ""))
            if not pm.isNull() and x - last_thumb_x > pm.width() + 4:
                r = QRectF(x - pm.width() / 2, 2, pm.width(), pm.height())
                p.drawPixmap(r.toRect(), pm)
                p.setPen(QPen(color, 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRect(r)
                rect = rect.united(r)
                last_thumb_x = x
            self._hits.append((rect, b["id"]))

        x = self.x_of(self.pos)  # 재생 위치
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.drawLine(QPointF(x, lane), QPointF(x, by + bar + 4))
        if span < self.dur:  # 확대 중 표시
            p.fillRect(QRectF(self.v0 / self.dur * w, self.height() - 3, span / self.dur * w, 3), QColor("#5b7cfa"))
        p.end()

    # ---------------------------------------------------------------- 마우스
    def _hit_mark(self, pos):
        for rect, bid in reversed(self._hits):
            if rect.contains(QPointF(pos)):
                return bid
        return None

    def mousePressEvent(self, e):
        pos = e.position()
        bid = self._hit_mark(pos)
        if e.button() == Qt.MouseButton.RightButton:
            if bid:
                self.bookmarkMenu.emit(bid, e.globalPosition().toPoint())
            return
        handle = self._hit_handle(pos)
        if handle:   # 시작/끝 선이 책갈피보다 우선
            self.handleGrabbed.emit()
            self._drag = (handle,)
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            self.update()
            return
        if bid:
            self._drag = ("mark", bid, pos.x(), False)
            return
        self._drag = ("seek",)
        self.seek.emit(self.t_of(pos.x()))

    def mouseMoveEvent(self, e):
        if not self._drag:
            handle = self._hit_handle(e.position())
            if handle != self._hover:
                self._hover = handle
                self.update()
            if handle:
                self.setCursor(Qt.CursorShape.SizeHorCursor)
                self.setToolTip(("초록 선 = 시작점" if handle == "in" else "빨간 선 = 끝점")
                                + "\n좌우로 드래그해서 조절 (프레임 단위로 붙어요)")
                return
            bid = self._hit_mark(e.position())
            self.setCursor(Qt.CursorShape.PointingHandCursor if bid else Qt.CursorShape.ArrowCursor)
            if bid:
                b = next((m for m in self.marks if m["id"] == bid), None)
                if b:
                    self.setToolTip(f"{BM.KIND_NAMES.get(b.get('kind'), '')}  {fmt_t(b['t'])}\n{b.get('note', '')}\n"
                                    "클릭: 이동 · 드래그: 옮기기 · 우클릭: 메뉴")
            else:
                self.setToolTip("")
            return
        x = e.position().x()
        kind = self._drag[0]
        if kind == "seek":
            self.seek.emit(self.t_of(x))
        elif kind in ("in", "out"):
            t = self._snap(self.t_of(x))
            if kind == "in":
                if self.out is not None:   # 서로 넘어가지 않게
                    t = min(t, self.out - self.MIN_LEN)
                self.inp = max(0.0, t)
                t = self.inp
            else:
                if self.inp is not None:
                    t = max(t, self.inp + self.MIN_LEN)
                self.out = min(self.dur, t)
                t = self.out
            self.rangeChanged.emit()
            self.handleMoved.emit(t)
            self.update()
        elif kind == "mark":
            _, bid, x0, _moved = self._drag
            if abs(x - x0) > 4:
                self._drag = ("mark", bid, x0, True)
                for b in self.marks:
                    if b["id"] == bid:
                        b["_preview"] = self.t_of(x)
                self.update()

    def mouseReleaseEvent(self, e):
        d, self._drag = self._drag, None
        if d and d[0] in ("in", "out"):
            self._hover = self._hit_handle(e.position())
            self.setCursor(Qt.CursorShape.SizeHorCursor if self._hover else Qt.CursorShape.ArrowCursor)
            self.update()
        if d and d[0] == "mark":
            _, bid, _x0, moved = d
            if moved:
                b = next((m for m in self.marks if m["id"] == bid), None)
                if b and "_preview" in b:
                    self.bookmarkMoved.emit(bid, b.pop("_preview"))
            else:
                self.bookmarkClicked.emit(bid)

    def wheelEvent(self, e):
        t = self.t_of(e.position().x())
        factor = 0.8 if e.angleDelta().y() > 0 else 1.25
        span = max(1.0, min(self.dur, (self.v1 - self.v0) * factor))
        ratio = (t - self.v0) / max(1e-6, self.v1 - self.v0)
        self.v0 = max(0.0, min(self.dur - span, t - span * ratio))
        self.v1 = self.v0 + span
        self.update()


class ClipEditor(QDialog):
    def __init__(self, path, main, start_at=None, edit_meta=None, offset=0.0, is_source=True):
        super().__init__(main)
        self.main = main
        self.path = str(path)
        self.edit_meta = edit_meta      # 라이브러리 클립 수정 모드
        self.offset = float(offset)
        self.is_source = is_source
        self.changed = False
        self.preview = False
        self.job = None
        self._undo = []     # 되돌리기 목록: {"kind": "range"|"marks", ...}
        self._redo = []
        self._grab = None   # 선을 잡았을 때의 구간 (실제로 움직였을 때만 기록)
        if edit_meta:
            self.setWindowTitle(f"클립 수정 —{Path(edit_meta['_json']).stem}")
        else:
            self.setWindowTitle(f"타임라인에서 직접 자르기 — {Path(path).name}")
        self.resize(1280, 860)
        try:
            self.info = media.probe(self.path)
        except Exception as e:
            QMessageBox.warning(main, "영상 열기 실패", str(e))
            self.info = media.VideoInfo(1920, 1080, 30.0, 0.0)
        self.fps = self.info.fps or 30.0

        self.video = QVideoWidget()
        self.video.setStyleSheet("background: black;")
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setMuted(True)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self._on_pos)
        self.player.durationChanged.connect(lambda d: self.timeline.set_duration(d / 1000.0) if d > 0 else None)
        self.player.playbackStateChanged.connect(self._on_state)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 10)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(self.video)

        side = QWidget()
        sl = QVBoxLayout(side)
        sl.setContentsMargins(8, 0, 0, 0)
        sl.addWidget(QLabel("🔖 책갈피 (AI가 찾은 동작)"))
        self.mark_list = QListWidget()
        self.mark_list.itemClicked.connect(lambda it: self.goto_mark(it.data(Qt.ItemDataRole.UserRole)))
        self.mark_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.mark_list.customContextMenuRequested.connect(self._list_menu)
        sl.addWidget(self.mark_list, 1)
        self.ck_hidden = QCheckBox("숨긴 책갈피도 보기")
        self.ck_hidden.toggled.connect(self._toggle_hidden)
        self.ck_weak = QCheckBox("약한 후보(효과음 없음)도 보기")
        self.ck_weak.setChecked(True)
        self.ck_weak.toggled.connect(lambda _: self.reload_marks())
        sl.addWidget(self.ck_weak)
        sl.addWidget(self.ck_hidden)
        brow = QHBoxLayout()
        self.btn_detect = QPushButton("🔍 모션 찾기")
        self.btn_detect.setToolTip("이 영상을 분석해서 동작이 있는 곳에 책갈피를 달아요 (클립은 만들지 않음)")
        self.btn_detect.clicked.connect(self.detect)
        b_add = QPushButton("＋ 여기에 책갈피 (B)")
        b_add.clicked.connect(self.add_mark)
        brow.addWidget(self.btn_detect)
        brow.addWidget(b_add)
        sl.addLayout(brow)
        self.detect_bar = QProgressBar()
        self.detect_bar.setRange(0, 1000)
        self.detect_bar.hide()
        sl.addWidget(self.detect_bar)
        sl.addWidget(QLabel("✂ 이 영상에서 만든 클립"))
        self.clip_list = QListWidget()
        self.clip_list.itemClicked.connect(self._clip_clicked)
        sl.addWidget(self.clip_list, 1)
        split.addWidget(side)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

        # 목록·체크박스가 키보드 포커스를 잡으면 Space 등이 창 단축키 대신 그쪽으로 가므로, 마우스로만 쓰게 한다
        for w in (self.mark_list, self.clip_list, self.ck_weak, self.ck_hidden):
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.timeline = Timeline()
        self.timeline.fps = self.fps
        self.timeline.seek.connect(lambda t: self.player.setPosition(int(t * 1000)))
        self.timeline.handleMoved.connect(self._handle_moved)
        self.timeline.handleGrabbed.connect(self._handle_grabbed)
        self.timeline.rangeChanged.connect(self._range_changed)
        self.timeline.bookmarkClicked.connect(self.goto_mark)
        self.timeline.bookmarkMoved.connect(self._mark_moved)
        self.timeline.bookmarkMenu.connect(self._mark_menu)
        root.addWidget(self.timeline)

        ctr = QHBoxLayout()
        for text, fn in [("⏪ 1초", lambda: self.jump(-1.0)), ("◀ 1프레임", lambda: self.step(-1))]:
            ctr.addWidget(self._btn(text, fn))
        self.btn_play = self._btn("▶ 재생", self.toggle_play)
        self.btn_play.setMinimumWidth(110)
        ctr.addWidget(self.btn_play)
        for text, fn in [("1프레임 ▶", lambda: self.step(1)), ("1초 ⏩", lambda: self.jump(1.0))]:
            ctr.addWidget(self._btn(text, fn))
        ctr.addWidget(QLabel("속도"))
        self.speed = QComboBox()
        self.speed.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for n, v in SPEEDS:
            self.speed.addItem(n, v)
        self.speed.setCurrentIndex(3)
        self.speed.currentIndexChanged.connect(lambda i: self.player.setPlaybackRate(self.speed.itemData(i)))
        ctr.addWidget(self.speed)
        ck = QCheckBox("소리")
        ck.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        ck.toggled.connect(lambda v: self.audio.setMuted(not v))
        ctr.addWidget(ck)
        ctr.addSpacing(12)
        self.btn_undo = self._btn("↶ 되돌리기", self.undo)
        self.btn_undo.setToolTip("Ctrl+Z — 시작/끝 선, 책갈피 변경을 되돌려요")
        self.btn_redo = self._btn("↷ 복구", self.redo)
        self.btn_redo.setToolTip("Ctrl+Shift+Z (또는 Ctrl+Y) — 되돌린 것을 다시 적용")
        ctr.addWidget(self.btn_undo)
        ctr.addWidget(self.btn_redo)
        ctr.addStretch(1)
        self.time_label = QLabel("")
        ctr.addWidget(self.time_label)
        root.addLayout(ctr)

        mk = QHBoxLayout()
        mk.addWidget(self._btn("[ 시작점 (I)", self.set_in))
        mk.addWidget(self._btn("끝점 (O) ]", self.set_out))
        self.sel_label = QLabel("시작점과 끝점을 지정하세요")
        mk.addWidget(self.sel_label, 1)
        mk.addWidget(self._btn("▶ 구간 미리보기 (P)", self.toggle_preview))
        mk.addWidget(QLabel("분류"))
        self.label_combo = QComboBox()
        self.label_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.label_combo.addItem("(AI가 판정)", None)
        for n in label_names(main.s):
            self.label_combo.addItem(n, n)
        mk.addWidget(self.label_combo)
        self.btn_make = QPushButton("✂ 클립 만들기 (Enter)")
        self.btn_make.setObjectName("primary")
        self.btn_make.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_make.clicked.connect(self.make_clip)
        mk.addWidget(self.btn_make)
        if edit_meta:
            self.btn_make.setText("💾 수정 저장 (Enter)")
            self.btn_make.clicked.disconnect()
            self.btn_make.clicked.connect(self.save_edit)
            mk.insertWidget(mk.count() - 1, self._btn("＋ 새 클립으로 저장", self.save_as_new))
            mk.insertWidget(mk.count() - 1, self._btn("✂ 재생 위치에서 나누기 (S)", self.split_here))
            cur = edit_meta.get("label_manual")
            i = self.label_combo.findData(cur) if cur else 0
            if cur and i < 0:
                self.label_combo.addItem(cur, cur)
                i = self.label_combo.count() - 1
            self.label_combo.setCurrentIndex(max(0, i))
        root.addLayout(mk)
        root.addWidget(muted("Space 재생/정지 · Ctrl+Z 되돌리기 · Ctrl+Shift+Z 복구 · ← → 1프레임 · Shift+← → 1초 · "
                             "I 시작점 · O 끝점 · P 구간 미리보기 · "
                             + ("Enter 수정 저장 · S 재생 위치에서 나누기"
                                if edit_meta else "Enter 클립 만들기 · B 책갈피 추가")
                             + " · 초록 선(시작)·빨간 선(끝)을 마우스로 끌어 조절 · 타임라인에서 마우스 휠로 확대/축소"))

        for key, fn in [("Space", self.toggle_play), ("Left", lambda: self.step(-1)), ("Right", lambda: self.step(1)),
                        ("Shift+Left", lambda: self.jump(-1.0)), ("Shift+Right", lambda: self.jump(1.0)),
                        ("I", self.set_in), ("O", self.set_out), ("P", self.toggle_preview),
                        ("Return", self._primary), ("Enter", self._primary), ("B", self.add_mark),
                        ("S", self.split_here),
                        ("Ctrl+Z", self.undo), ("Ctrl+Shift+Z", self.redo), ("Ctrl+Y", self.redo)]:
            QShortcut(QKeySequence(key), self, activated=fn)
        self._sync_undo()

        self.timeline.set_duration(self.info.duration or 1.0)
        self.reload_marks()
        self.reload_clips()
        self.player.setSource(QUrl.fromLocalFile(self.path))
        if edit_meta:
            from ..core.edit import clip_range_in

            a, b = clip_range_in(edit_meta, self.offset, is_source)
            self.timeline.inp, self.timeline.out = a, b
            if is_source:  # 클립 주변만 확대해서 보여줌
                tl = self.timeline
                tl.v0, tl.v1 = max(0.0, a - 3.0), min(tl.dur, b + 3.0)
            else:
                self.btn_detect.setEnabled(False)
            self._range_changed()
            if not is_source:
                self.sel_label.setText(self.sel_label.text() + "   (원본 영상이 없어서 클립 안에서만 줄이거나 나눌 수 있어요)")
            start_at = a
        if start_at is not None:
            self.player.setPosition(int(max(0.0, start_at) * 1000))
        self.player.pause()

    def _btn(self, text, fn):
        b = QPushButton(text)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.clicked.connect(fn)
        return b

    # ---------------------------------------------------------------- 되돌리기 / 복구
    UNDO_MAX = 100

    def _range_state(self):
        tl = self.timeline
        return {"kind": "range", "inp": tl.inp, "out": tl.out}

    def _marks_state(self):
        items = copy.deepcopy(self.bm["items"])
        for b in items:
            b.pop("_preview", None)
        return {"kind": "marks", "items": items, "deleted": list(self.bm.get("deleted", []))}

    def _push(self, st):
        """바꾸기 '직전'의 상태를 기록한다. 새로 바꾸면 복구 목록은 비운다."""
        self._undo.append(st)
        del self._undo[:-self.UNDO_MAX]
        self._redo.clear()
        self._sync_undo()

    def _sync_undo(self):
        self.btn_undo.setEnabled(bool(self._undo))
        self.btn_redo.setEnabled(bool(self._redo))

    def _apply_state(self, st):
        self.preview = False
        if st["kind"] == "range":
            self.timeline.inp, self.timeline.out = st["inp"], st["out"]
            self._range_changed()
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.bm["items"] = copy.deepcopy(st["items"])
            self.bm["deleted"] = list(st["deleted"])
            for b in self.bm["items"]:   # 지웠다 되살린 책갈피는 미리보기 이미지가 없으니 다시 만든다
                th = b.get("thumb")
                if not th or not Path(th).exists():
                    b["thumb"] = BM.make_thumb(self.main.s, self.path, b["t"])
            BM.save(self.main.s, self.path, self.bm)
        finally:
            QApplication.restoreOverrideCursor()
        self.timeline._pix.clear()
        self.reload_marks()
        self.sel_label.setText("책갈피 변경을 적용했어요.")

    def _step_history(self, src, dst):
        """src의 마지막 기록을 적용하고, 적용하기 전 상태를 dst에 쌓는다. 바뀌는 게 없는 기록은 건너뛴다."""
        if self.job is not None and self.job.isRunning():
            self.sel_label.setText("모션 찾기가 끝난 뒤에 되돌릴 수 있어요.")
            return False
        while src:
            st = src.pop()
            cur = self._range_state() if st["kind"] == "range" else self._marks_state()
            if cur == st:
                continue
            dst.append(cur)
            self._apply_state(st)
            return True
        return False

    def undo(self):
        self._step_history(self._undo, self._redo)
        self._sync_undo()

    def redo(self):
        self._step_history(self._redo, self._undo)
        self._sync_undo()

    def _handle_grabbed(self):
        self._grab = self._range_state()

    # ---------------------------------------------------------------- 재생
    def cur_t(self):
        return self.player.position() / 1000.0

    def toggle_play(self):
        self.preview = False
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def step(self, n):
        self.preview = False
        self.player.pause()
        self.player.setPosition(max(0, int(self.player.position() + n * 1000.0 / self.fps)))

    def jump(self, sec):
        self.player.setPosition(max(0, int(self.player.position() + sec * 1000)))

    def _on_pos(self, ms):
        t = ms / 1000.0
        self.timeline.set_pos(t)
        self.time_label.setText(f"{fmt_t(t)} / {fmt_t(self.timeline.dur)}   (프레임 {int(round(t * self.fps))})")
        tl = self.timeline
        if self.preview and tl.inp is not None and tl.out is not None and t >= max(tl.inp, tl.out):
            self.player.setPosition(int(min(tl.inp, tl.out) * 1000))

    def _handle_moved(self, t):
        """시작/끝 선을 끄는 동안 그 위치의 프레임을 화면에 보여준다."""
        if self._grab is not None:   # 잡기만 하고 안 움직인 경우는 기록하지 않음
            self._push(self._grab)
            self._grab = None
        self.preview = False
        self.player.pause()
        self.player.setPosition(int(t * 1000))

    def _on_state(self, st):
        self.btn_play.setText("⏸ 일시정지" if st == QMediaPlayer.PlaybackState.PlayingState else "▶ 재생")

    def toggle_preview(self):
        tl = self.timeline
        if tl.inp is None or tl.out is None:
            return
        self.preview = not self.preview
        if self.preview:
            self.player.setPosition(int(min(tl.inp, tl.out) * 1000))
            self.player.play()
        else:
            self.player.pause()

    # ---------------------------------------------------------------- 구간/클립
    def set_in(self):
        self._push(self._range_state())
        self.timeline.inp = self.cur_t()
        if self.timeline.out is not None and self.timeline.out <= self.timeline.inp:
            self.timeline.out = None
        self._range_changed()

    def set_out(self):
        self._push(self._range_state())
        self.timeline.out = self.cur_t()
        if self.timeline.inp is not None and self.timeline.inp >= self.timeline.out:
            self.timeline.inp = None
        self._range_changed()

    def _range_changed(self):
        tl = self.timeline
        if tl.inp is not None and tl.out is not None:
            a, b = min(tl.inp, tl.out), max(tl.inp, tl.out)
            self.sel_label.setText(f"선택: {fmt_t(a)} ~ {fmt_t(b)}  ({b - a:.2f}초, {int(round((b - a) * self.fps))}프레임)")
        elif tl.inp is not None:
            self.sel_label.setText(f"시작점 {fmt_t(tl.inp)} · 이제 끝점(O)을 지정하세요")
        else:
            self.sel_label.setText("시작점과 끝점을 지정하세요")
        tl.update()

    def _primary(self):
        if self.edit_meta:
            self.save_edit()
        else:
            self.make_clip()

    def _sel(self):
        tl = self.timeline
        if tl.inp is None or tl.out is None:
            QMessageBox.information(self, "구간 필요", "먼저 시작점(I)과 끝점(O)을 지정하세요.")
            return None
        a, b = min(tl.inp, tl.out), max(tl.inp, tl.out)
        if b - a < 0.1:
            QMessageBox.information(self, "구간 필요", "구간이 너무 짧아요.")
            return None
        return a, b

    def _release_file(self):
        """클립 파일 자체를 편집 중이면 덮어쓰기 전에 재생을 멈추고 파일을 놓는다 (Windows 파일 잠금)."""
        if not self.is_source:
            self.player.stop()
            self.player.setSource(QUrl())

    def _run_edit(self, fn, *args, close=True):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            fn(*args)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "수정 실패", str(e))
            if not self.is_source:
                self.player.setSource(QUrl.fromLocalFile(self.path))
            return False
        QApplication.restoreOverrideCursor()
        self.changed = True
        if close:
            self.accept()
        return True

    def save_edit(self):
        from ..core.edit import replace_range

        sel = self._sel()
        if not sel:
            return
        self._release_file()
        self._run_edit(replace_range, self.edit_meta, self.path, self.offset, sel[0], sel[1],
                       copy.deepcopy(self.main.s), self.label_combo.currentData(), self.main.log)

    def save_as_new(self):
        from ..core.edit import new_from_range

        sel = self._sel()
        if not sel:
            return
        if self._run_edit(new_from_range, self.edit_meta, self.path, self.offset, sel[0], sel[1],
                          copy.deepcopy(self.main.s), self.label_combo.currentData(), self.main.log, close=False):
            self.sel_label.setText("✅ 새 클립으로 저장했어요. 원래 클립은 그대로예요.")

    def split_here(self):
        if not self.edit_meta:
            return
        from ..core.edit import split

        sel = self._sel()
        if not sel:
            return
        t = self.cur_t()
        if not (sel[0] + 0.1 < t < sel[1] - 0.1):
            QMessageBox.information(self, "나누기", "재생 위치를 클립 구간(선택 영역) 안으로 옮긴 뒤 눌러 주세요.")
            return
        self._release_file()
        self._run_edit(split, self.edit_meta, self.path, self.offset, sel[0], t, sel[1],
                       copy.deepcopy(self.main.s), self.main.log)

    def make_clip(self):
        tl = self.timeline
        if tl.inp is None or tl.out is None:
            QMessageBox.information(self, "클립 만들기", "먼저 시작점(I)과 끝점(O)을 지정하세요.")
            return
        a, b = min(tl.inp, tl.out), max(tl.inp, tl.out)
        if b - a < 0.1:
            QMessageBox.information(self, "클립 만들기", "구간이 너무 짧아요.")
            return
        from .progress_dialog import ProgressDialog

        dlg = ProgressDialog(self, "클립 만드는 중", make_manual_clip,
                             (self.path, a, b, copy.deepcopy(self.main.s), self.label_combo.currentData()),
                             intro=f"{fmt_t(a)} ~ {fmt_t(b)} ({b - a:.1f}초) 구간을 잘라 클립으로 저장해요.")
        dlg.job.log.connect(self.main.log)
        dlg.exec()
        if dlg.cancelled:
            self.sel_label.setText("클립 만들기를 취소했어요.")
            return
        if dlg.error:
            QMessageBox.warning(self, "클립 만들기 실패", dlg.error)
            return
        self.changed = True
        self.reload_clips()
        self.sel_label.setText(f"✅ 클립을 만들었어요 ({b - a:.2f}초). 라이브러리에서 볼 수 있어요.")
        self._push(self._range_state())   # 구간 선택만 되돌릴 수 있고, 이미 만든 클립은 라이브러리에서 지워야 해요
        tl.inp = tl.out = None
        tl.update()

    def reload_clips(self):
        clips = [m for m in library.load_clips(library.game_dir(self.main.s))
                 if m.get("source_video") == self.path or m.get("source_name") == Path(self.path).name]
        self.timeline.clips = [(float(m.get("start_in_source", 0)),
                                float(m.get("start_in_source", 0)) + float(m.get("duration", 0))) for m in clips]
        self.clip_list.clear()
        for m in sorted(clips, key=lambda m: m.get("start_in_source", 0)):
            a = float(m.get("start_in_source", 0))
            it = QListWidgetItem(f"{fmt_t(a)}  {library.final_label(m)}  ({m.get('duration', 0):.1f}초)"
                                 + ("  ✋" if m.get("manual") else ""))
            it.setData(Qt.ItemDataRole.UserRole, (a, a + float(m.get("duration", 0))))
            self.clip_list.addItem(it)
        self.timeline.update()

    def _clip_clicked(self, it):
        a, b = it.data(Qt.ItemDataRole.UserRole)
        self._push(self._range_state())
        self.timeline.inp, self.timeline.out = a, b
        self._range_changed()
        self.player.setPosition(int(a * 1000))

    # ---------------------------------------------------------------- 책갈피
    def reload_marks(self):
        self.bm = BM.load(self.main.s, self.path)
        items = [b for b in self.bm["items"] if self.ck_weak.isChecked() or b.get("kind") != "weak"]
        self.timeline.marks = items
        self.timeline.show_hidden = self.ck_hidden.isChecked()
        self.mark_list.clear()
        for b in items:
            if b.get("hidden") and not self.ck_hidden.isChecked():
                continue
            it = QListWidgetItem(f"{fmt_t(b['t'])}  {BM.KIND_NAMES.get(b.get('kind'), '')}"
                                 + (f"  · {b['note']}" if b.get("note") else "") + ("  (숨김)" if b.get("hidden") else ""))
            pm = self.timeline._pixmap(b.get("thumb", ""))
            if not pm.isNull():
                it.setIcon(QIcon(pm))
            it.setData(Qt.ItemDataRole.UserRole, b["id"])
            self.mark_list.addItem(it)
        if not self.bm["items"]:
            it = QListWidgetItem("책갈피가 없어요. [🔍 모션 찾기]를 눌러 보세요.")
            it.setFlags(Qt.ItemFlag.NoItemFlags)
            self.mark_list.addItem(it)
        self.timeline.update()

    def _mark(self, bid):
        return next((b for b in self.bm["items"] if b["id"] == bid), None)

    def goto_mark(self, bid):
        b = self._mark(bid) if bid else None
        if not b:
            return
        pre, post = float(self.main.s.get("pre_pad", 1.0)), float(self.main.s.get("post_pad", 1.2))
        self._push(self._range_state())
        self.timeline.inp = max(0.0, b["t0"] - pre)
        self.timeline.out = min(self.timeline.dur, b["t1"] + post)
        self._range_changed()
        self.player.pause()
        self.player.setPosition(int(self.timeline.inp * 1000))

    def add_mark(self):
        self._push(self._marks_state())
        self.bm["items"].append(BM.new_item(self.main.s, self.path, self.cur_t(), kind="user", note="직접 추가"))
        BM.save(self.main.s, self.path, self.bm)
        self.reload_marks()

    def _mark_moved(self, bid, t):
        self._push(self._marks_state())
        BM.move_item(self.main.s, self.path, self.bm, bid, t)
        self.timeline._pix.clear()
        self.reload_marks()

    def _set_hidden(self, bid, hidden):
        b = self._mark(bid)
        if b:
            self._push(self._marks_state())
            b["hidden"] = hidden
            BM.save(self.main.s, self.path, self.bm)
            self.reload_marks()

    def _delete_mark(self, bid):
        self._push(self._marks_state())
        BM.delete_item(self.main.s, self.path, self.bm, bid)
        self.reload_marks()

    def _menu_for(self, bid, global_pos):
        b = self._mark(bid)
        if not b:
            return
        menu = QMenu(self)
        menu.addAction("▶ 이 위치로 이동", lambda: self.goto_mark(bid))
        menu.addAction("✂ 이 구간으로 바로 클립 만들기", lambda: (self.goto_mark(bid), self.make_clip()))
        menu.addAction("📍 현재 재생 위치로 옮기기", lambda: self._mark_moved(bid, self.cur_t()))
        if b.get("hidden"):
            menu.addAction("👁 다시 보이기", lambda: self._set_hidden(bid, False))
        else:
            menu.addAction("🙈 숨기기", lambda: self._set_hidden(bid, True))
        menu.addSeparator()
        menu.addAction("🗑 삭제", lambda: self._delete_mark(bid))
        menu.exec(global_pos)

    def _mark_menu(self, bid, global_pos):
        self._menu_for(bid, global_pos)

    def _list_menu(self, pos):
        it = self.mark_list.itemAt(pos)
        if it and it.data(Qt.ItemDataRole.UserRole):
            self._menu_for(it.data(Qt.ItemDataRole.UserRole), self.mark_list.viewport().mapToGlobal(pos))

    def _toggle_hidden(self, _v):
        self.reload_marks()

    def detect(self):
        if self.main.busy or (self.job is not None and self.job.isRunning()):
            QMessageBox.information(self, "모션 찾기", "다른 분석이 진행 중이에요. 끝난 뒤에 다시 눌러 주세요.")
            return
        self.job = Job("모션 찾기", BM.detect, (self.path, copy.deepcopy(self.main.s)))
        self.job.log.connect(self.main.log)
        self.job.progress.connect(self._detect_progress)
        self.job.succeeded.connect(self._detected)
        self.job.failed.connect(lambda msg, c: QMessageBox.warning(self, "모션 찾기", msg) if not c else None)
        self.job.finished.connect(lambda: (self.detect_bar.hide(), self.btn_detect.setEnabled(True),
                                           self.btn_detect.setText("🔍 모션 찾기")))
        self.btn_detect.setText("■ 찾기 취소")
        self.btn_detect.clicked.disconnect()
        self.btn_detect.clicked.connect(self._cancel_or_detect)
        self.detect_bar.setValue(0)
        self.detect_bar.show()
        self.job.start()

    def _detected(self, n):
        if n:   # 아직 다시 읽기 전이라 self.bm은 찾기 전 상태 → 되돌리면 찾기 전으로 돌아감
            self._push(self._marks_state())
        self.reload_marks()
        self.sel_label.setText(f"🔖 책갈피 {n}개를 새로 달았어요.")

    def _cancel_or_detect(self):
        if self.job is not None and self.job.isRunning():
            self.job.cancel.set()
            self.btn_detect.setEnabled(False)
        else:
            self.detect()

    def _detect_progress(self, f, text):
        if f < 0:
            self.detect_bar.setRange(0, 0)
        else:
            self.detect_bar.setRange(0, 1000)
            self.detect_bar.setValue(int(f * 1000))
        self.detect_bar.setToolTip(text)
        self.sel_label.setText(text)

    def done(self, r):
        if self.job is not None and self.job.isRunning():
            self.job.cancel.set()
            self.job.wait(10000)
        self.player.stop()
        self.player.setSource(QUrl())
        super().done(r)
