"""타수별로 나누기: 길게 잘린 클립에서 타격 지점(소리·번쩍임)을 찾아 저해상도 영상으로 보여주고,
그 자리에서 바로 고친 뒤(시작/끝 끌기, 타격 추가/빼기) 1타·2타·3타… 클립으로 나눠 저장한다.

- 위: 큰 미리보기 (선택한 타를 반복 재생)
- 가운데 줄: 소리 크기 곡선 위에 타별 구간(색 칸)과 타격(세로선). 칸의 양 끝을 끌어서 조절
- 아래 카드: 타마다 움직이는 작은 미리보기 + 사용 체크 + 1프레임 조절
미리보기는 영상 플레이어 대신 미리 디코딩한 저해상도 프레임을 그리므로 프레임 단위로 정확하고, 파일을 잡고 있지 않는다."""
import copy
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QCheckBox, QDialog, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPushButton, QScrollArea, QSplitter,
                               QVBoxLayout, QWidget)

from ..core import hitsplit as H
from ..core import library

from .widgets import Job, SpinKeyFilter, muted

FILL = [QColor(70, 130, 235, 90), QColor(70, 190, 150, 90)]
EDGE_PX = 7          # 칸 끝을 잡을 수 있는 거리
MAX_CACHE = 3        # 미리보기를 기억해 둘 클립 수 (메모리 제한)
UNDO_MAX = 100


def fmt_t(t):
    m, s = divmod(max(0.0, float(t)), 60)
    return f"{int(m)}:{s:05.2f}"


def load_preview(path, log=None, progress=None, cancel=None):
    """Job용: 저해상도 프레임을 QImage 목록으로 (작업 스레드에서 만들어도 되는 QImage만 사용)."""
    d = H.preview_data(path, progress=progress or (lambda *a: None))
    fr = d.pop("frames")
    w, h = d["w"], d["h"]
    d["imgs"] = [QImage(f.tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888).copy() for f in fr]
    return d


def pick_edge(pieces, x_of, x, sel_key=None, px=EDGE_PX):
    """마우스 x에서 잡을 칸 끝 (key, 'a'|'b') 또는 None.
    붙어 있는 두 칸의 경계에서는 선택한 칸 → 마우스가 있는 쪽 칸 순으로 고른다."""
    best = None
    for p in pieces:
        if not p["on"]:
            continue
        for side in ("a", "b"):
            d = abs(x_of(p[side]) - x)
            if d > px:
                continue
            inward = (side == "a" and x >= x_of(p["a"])) or (side == "b" and x <= x_of(p["b"]))
            score = (round(d, 1), 0 if p["key"] == sel_key else 1, 0 if inward else 1)
            if best is None or score < best[0]:
                best = (score, p["key"], side)
    return None if best is None else (best[1], best[2])


def pick_piece(pieces, x_of, x, px=EDGE_PX + 1):
    """마우스 x의 타: 타격선 가까이 → 그 타, 아니면 x를 품은 사용 중인 칸."""
    near = [(abs(x_of(p["t"]) - x), p) for p in pieces]
    near = [n for n in near if n[0] <= px]
    if near:
        return min(near, key=lambda n: n[0])[1]
    inside = [p for p in pieces if p["on"] and x_of(p["a"]) <= x <= x_of(p["b"])]
    return min(inside, key=lambda p: abs(x_of(p["t"]) - x)) if inside else None


# ---------------------------------------------------------------------------- 위젯
class FrameView(QWidget):
    """저해상도 프레임을 비율 유지해서 그린다."""

    def __init__(self, min_h=200):
        super().__init__()
        self.img = None
        self.text = ""
        self.dim = False
        self.setMinimumHeight(min_h)

    def show_img(self, img, text=""):
        self.img, self.text = img, text
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#000000"))
        if self.img is not None and not self.img.isNull():
            iw, ih = self.img.width(), self.img.height()
            k = min(self.width() / iw, self.height() / ih)
            w, h = iw * k, ih * k
            p.drawImage(QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h), self.img)
            if self.dim:
                p.fillRect(self.rect(), QColor(20, 22, 28, 150))
        if self.text:
            p.setPen(QColor("#ffffff"))
            p.drawText(QRectF(8, 6, self.width() - 16, 22), int(Qt.AlignmentFlag.AlignLeft), self.text)
        p.end()


class EditStrip(QWidget):
    """소리 크기 곡선 + 타별 구간. 칸 끝 드래그 = 시작/끝 조절, 클릭 = 이동·선택, 더블클릭 = 타격 추가, 우클릭 = 메뉴."""
    seek = Signal(float)
    picked = Signal(str, float)            # key(문자열), 시각
    grabbed = Signal()
    edgeMoved = Signal(str, str, float)    # key, side, 시각
    released = Signal()
    addAt = Signal(float)
    menuAt = Signal(str, object)

    L = 8

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(110)
        self.setMouseTracking(True)
        self.dur, self.pieces, self.sel, self.env, self.pos = 1.0, [], None, None, 0.0
        self._drag = None

    def set_data(self, dur, pieces, sel, env):
        self.dur, self.pieces, self.sel, self.env = max(0.1, dur), pieces, sel, env
        self.update()

    def set_pos(self, t):
        self.pos = t
        self.update()

    def x_of(self, t):
        return self.L + (self.width() - 2 * self.L) * min(1.0, max(0.0, t / self.dur))

    def t_of(self, x):
        return min(1.0, max(0.0, (x - self.L) / max(1.0, self.width() - 2 * self.L))) * self.dur

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#15171c"))
        top, h = 12, self.height() - 38
        p.fillRect(QRectF(self.L, top, self.width() - 2 * self.L, h), QColor("#1e222c"))
        if self.env is not None and len(self.env):   # 소리 크기 곡선 (아래에서 위로)
            p.setPen(QPen(QColor(150, 160, 185, 120), 1))
            n = len(self.env)
            step = max(1, int(n / max(1, self.width())))
            for i in range(0, n, step):
                x = self.x_of(i * self.dur / n)
                v = float(self.env[i:i + step].max())
                p.drawLine(QPointF(x, top + h), QPointF(x, top + h - v * (h - 4)))
        f = QFont(p.font())
        f.setPointSize(9)
        p.setFont(f)
        k = 0
        for pc in self.pieces:
            if not pc["on"]:
                continue
            r = QRectF(self.x_of(pc["a"]), top, max(1.0, self.x_of(pc["b"]) - self.x_of(pc["a"])), h)
            p.fillRect(r, FILL[k % 2])
            sel = pc["key"] == self.sel
            p.setPen(QPen(QColor("#ffffff") if sel else QColor(255, 255, 255, 60), 2 if sel else 1))
            p.drawRect(r)
            p.setPen(QColor("#dfe6ff"))
            p.drawText(r.adjusted(0, 0, 0, -2), int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom),
                       f"{k + 1}타" + (" ✎" if pc["edited"] else ""))
            for side, col in (("a", "#46c37b"), ("b", "#ff6b6b")):   # 잡을 수 있는 손잡이
                if sel or (self._drag and self._drag[0] == "edge" and self._drag[1] == pc["key"]):
                    x = self.x_of(pc[side])
                    p.fillRect(QRectF(x - 3, top + h / 2 - 9, 6, 18), QColor(col))
            k += 1
        for pc in self.pieces:   # 타격선
            x = self.x_of(pc["t"])
            pen = QPen(QColor("#46c37b") if pc["on"] else QColor("#7a8194"), 3 if pc["on"] else 1)
            if not pc["on"]:
                pen.setStyle(Qt.PenStyle.DashLine)
            if pc["kind"] == "직접":
                pen.setColor(QColor("#ffb347") if pc["on"] else QColor("#8a7a5a"))
            p.setPen(pen)
            p.drawLine(QPointF(x, top - 5), QPointF(x, top + h + 3))
        p.setPen(QPen(QColor("#ffffff"), 2))
        x = self.x_of(self.pos)
        p.drawLine(QPointF(x, top - 8), QPointF(x, top + h + 6))
        p.setPen(QColor("#9aa3b5"))
        p.drawText(QRectF(self.L, self.height() - 22, 120, 18), int(Qt.AlignmentFlag.AlignLeft), "0:00")
        p.drawText(QRectF(self.width() - 128, self.height() - 22, 120, 18), int(Qt.AlignmentFlag.AlignRight),
                   fmt_t(self.dur))
        p.drawText(QRectF(130, self.height() - 22, self.width() - 260, 18), int(Qt.AlignmentFlag.AlignHCenter),
                   "칸 끝을 끌어 조절 · 더블클릭: 타격 추가 · 우클릭: 메뉴")
        p.end()

    def mousePressEvent(self, e):
        x = e.position().x()
        if e.button() == Qt.MouseButton.RightButton:
            pc = pick_piece(self.pieces, self.x_of, x)
            if pc:
                self.menuAt.emit(str(pc["key"]), e.globalPosition().toPoint())
            return
        hit = pick_edge(self.pieces, self.x_of, x, self.sel)
        if hit:
            self._drag = ("edge", hit[0], hit[1])
            self.grabbed.emit()
            self.setCursor(Qt.CursorShape.SizeHorCursor)
            return
        pc = pick_piece(self.pieces, self.x_of, x)
        if pc:
            self.picked.emit(str(pc["key"]), self.t_of(x))
        else:
            self.seek.emit(self.t_of(x))
        self._drag = ("seek",)

    def mouseMoveEvent(self, e):
        x = e.position().x()
        if not self._drag:
            self.setCursor(Qt.CursorShape.SizeHorCursor if pick_edge(self.pieces, self.x_of, x, self.sel)
                           else Qt.CursorShape.ArrowCursor)
            return
        if self._drag[0] == "edge":
            self.edgeMoved.emit(str(self._drag[1]), self._drag[2], self.t_of(x))
        else:
            self.seek.emit(self.t_of(x))

    def mouseReleaseEvent(self, _e):
        if self._drag and self._drag[0] == "edge":
            self.released.emit()
        self._drag = None
        self.update()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and not pick_edge(self.pieces, self.x_of, e.position().x(), self.sel):
            self.addAt.emit(self.t_of(e.position().x()))


class PieceCard(QFrame):
    """타 하나: 움직이는 작은 미리보기 + 사용 체크 + 시작/끝 1프레임 조절."""

    def __init__(self, dlg):
        super().__init__()
        self.dlg = dlg
        self.p = None
        self.setFixedWidth(214)
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(4)
        self.view = FrameView(min_h=112)
        self.view.setFixedHeight(112)
        lay.addWidget(self.view)
        top = QHBoxLayout()
        self.title = QLabel("")
        top.addWidget(self.title, 1)
        self.ck = QCheckBox("사용")
        self.ck.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.ck.toggled.connect(lambda v: self.p and self.dlg.set_on(self.p["key"], v))
        top.addWidget(self.ck)
        lay.addLayout(top)
        self.rng = muted("")
        lay.addWidget(self.rng)
        for side, name in (("a", "시작"), ("b", "끝")):
            row = QHBoxLayout()
            row.addWidget(QLabel(name))
            for txt, n, tip in (("◀", -1, "1프레임 앞으로"), ("▶", 1, "1프레임 뒤로")):
                b = QPushButton(txt)
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                b.setFixedWidth(34)
                b.setToolTip(f"{name}을 {tip}")
                b.clicked.connect(lambda _=False, s=side, n=n: self.p and self.dlg.nudge(self.p["key"], s, n))
                row.addWidget(b)
            row.addStretch(1)
            lay.addLayout(row)
        self.btn_reset = QPushButton("↺ 자동 구간으로")
        self.btn_reset.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_reset.clicked.connect(lambda: self.p and self.dlg.reset_piece(self.p["key"]))
        lay.addWidget(self.btn_reset)

    def set_piece(self, p, label, selected):
        self.p = p
        self.title.setText(f"<b>{label}</b>  {fmt_t(p['t'])}  <span style='color:#9aa3b5'>({p['kind']})</span>")
        self.rng.setText(f"{fmt_t(p['a'])} ~ {fmt_t(p['b'])}  ({p['b'] - p['a']:.2f}초)" + ("  ✎ 고침" if p["edited"] else ""))
        self.ck.blockSignals(True)
        self.ck.setChecked(p["on"])
        self.ck.blockSignals(False)
        self.btn_reset.setEnabled(p["edited"])
        self.view.dim = not p["on"]
        if getattr(self, "_selected", None) != selected:   # 스타일 다시 계산은 비싸서 바뀔 때만
            self._selected = selected
            self.setStyleSheet("#card { border: 2px solid #5b7cfa; border-radius: 8px; }" if selected
                               else "#card { border: 1px solid #2a2f3a; border-radius: 8px; }")

    def tick(self, phase):
        """phase: 초 단위 경과 시간 → 이 타의 구간 안에서 반복."""
        p = self.p
        if not p:
            return
        span = max(0.05, p["b"] - p["a"])
        self.view.show_img(self.dlg.frame_at(p["a"] + phase % span))

    def mousePressEvent(self, _e):
        if self.p:
            self.dlg.select_piece(self.p["key"], play=True)

    def contextMenuEvent(self, e):
        if self.p:
            self.dlg.piece_menu(self.p["key"], e.globalPos())


# ---------------------------------------------------------------------------- 창
class HitSplitDialog(QDialog):
    def __init__(self, main, metas):
        super().__init__(main)
        self.main = main
        self.metas = list(metas)
        self.changed = False
        self.job = None          # 찾기 / 저장
        self._pjobs = []         # 끝나지 않은 미리보기 작업 (창 닫을 때 기다림)
        self.states = []         # H.ClipEdit
        self.cur = -1
        self.sel = None          # 선택한 타 key
        self.previews = {}       # 클립 정보 파일 경로 → 미리보기 자료
        self._order = []
        self.pos, self.playing, self.phase = 0.0, False, 0.0
        self._undo, self._redo, self._grab = [], [], None
        self._cards = []
        s = main.s
        self.setWindowTitle("타수별로 나누기" + (f" — {Path(self.metas[0]['_json']).stem}" if len(self.metas) == 1
                                            else f" — 클립 {len(self.metas)}개"))
        self.resize(1280, 940)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 10)
        self.head = QLabel("타격 지점을 찾는 중...")
        self.head.setWordWrap(True)
        root.addWidget(self.head)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.left = QWidget()
        ll = QVBoxLayout(self.left)
        ll.setContentsMargins(0, 0, 8, 0)
        ll.addWidget(QLabel("클립"))
        self.clip_list = QListWidget()
        self.clip_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clip_list.currentRowChanged.connect(self._select_clip)
        ll.addWidget(self.clip_list, 1)
        split.addWidget(self.left)
        self.view = FrameView(min_h=240)
        split.addWidget(self.view)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 5)
        root.addWidget(split, 1)

        self.strip = EditStrip()
        self.strip.seek.connect(self._seek)
        self.strip.picked.connect(lambda k, t: (self.select_piece(float(k)), self._seek(t)))
        self.strip.grabbed.connect(self._grabbed)
        self.strip.edgeMoved.connect(lambda k, side, t: self._drag_edge(float(k), side, t))
        self.strip.released.connect(self._released)
        self.strip.addAt.connect(self.add_hit)
        self.strip.menuAt.connect(lambda k, pos: self.piece_menu(float(k), pos))
        root.addWidget(self.strip)

        r1 = QHBoxLayout()
        self.btn_play = self._btn("▶ 재생 (Space)", self.toggle_play)
        self.btn_play.setMinimumWidth(120)
        r1.addWidget(self.btn_play)
        r1.addWidget(self._btn("◀ 1프레임", lambda: self.step(-1)))
        r1.addWidget(self._btn("1프레임 ▶", lambda: self.step(1)))
        self.ck_loop = QCheckBox("선택한 타만 반복")
        self.ck_loop.setChecked(True)
        self.ck_loop.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        r1.addWidget(self.ck_loop)
        r1.addSpacing(10)
        r1.addWidget(self._btn("＋ 여기에 타격 추가 (A)", lambda: self.add_hit(self.pos)))
        r1.addWidget(self._btn("[ 시작 = 여기 (I)", lambda: self.set_edge_here("a")))
        r1.addWidget(self._btn("끝 = 여기 (O) ]", lambda: self.set_edge_here("b")))
        r1.addSpacing(10)
        self.btn_undo = self._btn("↶ 되돌리기", self.undo)
        self.btn_undo.setToolTip("Ctrl+Z")
        self.btn_redo = self._btn("↷ 복구", self.redo)
        self.btn_redo.setToolTip("Ctrl+Shift+Z (또는 Ctrl+Y)")
        r1.addWidget(self.btn_undo)
        r1.addWidget(self.btn_redo)
        r1.addStretch(1)
        self.time_label = QLabel("")
        r1.addWidget(self.time_label)
        root.addLayout(r1)

        self.cards_box = QWidget()
        self.cards_lay = QHBoxLayout(self.cards_box)
        self.cards_lay.setContentsMargins(0, 0, 0, 0)
        self.cards_lay.setSpacing(8)
        self.cards_lay.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setWidget(self.cards_box)
        sc.setFixedHeight(300)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cards_scroll = sc
        root.addWidget(sc)

        r2 = QHBoxLayout()
        self.sp_pre = self._spin(float(s.get("hit_pre", 0.8)), 0.1, 3.0)
        self.sp_post = self._spin(float(s.get("hit_post", 0.8)), 0.1, 3.0)
        self.sp_gap = self._spin(float(s.get("hit_min_gap", 0.3)), 0.1, 2.0)
        for text, sp, tip in (("자동 구간: 타격 전 여유", self.sp_pre, "타격 앞쪽을 최대 이만큼 남겨요 (이웃한 타와는 중간에서 끊어요)"),
                              ("타격 후 여유", self.sp_post, "타격 뒤쪽을 최대 이만큼 남겨요"),
                              ("이보다 가까운 소리는 한 타로 봄", self.sp_gap,
                               "한 번 휘두를 때 소리가 두 번 나서 한 타가 둘로 나뉘면 이 값을 늘리세요")):
            lab = QLabel(text)
            lab.setToolTip(tip)
            sp.setToolTip(tip)
            r2.addWidget(lab)
            r2.addWidget(sp)
            r2.addSpacing(10)
            sp.valueChanged.connect(lambda _v: self._params_changed())
        r2.addStretch(1)
        root.addLayout(r2)
        self._keys = SpinKeyFilter(self, (self.sp_pre, self.sp_post, self.sp_gap))

        r3 = QHBoxLayout()
        self.ck_del = QCheckBox("나눈 뒤 원래 긴 클립은 지우기 (기본: 남겨둠)")
        self.ck_del.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        r3.addWidget(self.ck_del)
        r3.addStretch(1)
        self.sum_label = muted("")
        r3.addWidget(self.sum_label)
        self.btn_save = QPushButton("💾 타수별로 나눠 저장")
        self.btn_save.setObjectName("primary")
        self.btn_save.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_save.clicked.connect(self.save)
        r3.addWidget(self.btn_save)
        r3.addWidget(self._btn("닫기", self.reject))
        root.addLayout(r3)
        root.addWidget(muted("Space 재생/정지 · ←→ 1프레임 · ↑↓ 이전/다음 타 · I/O 선택한 타의 시작/끝을 지금 위치로 · "
                             "A 타격 추가 · Delete 선택한 타 빼기/다시 사용 · R 자동 구간으로 · Ctrl+Z 되돌리기 · "
                             "Ctrl+Shift+Z 복구.  타격은 타격음과 화면 번쩍임으로 찾아요. 소리 없는 클립은 덜 정확해요."))

        for key, fn in [("Space", self.toggle_play), ("Left", lambda: self.step(-1)), ("Right", lambda: self.step(1)),
                        ("Up", lambda: self.select_rel(-1)), ("Down", lambda: self.select_rel(1)),
                        ("I", lambda: self.set_edge_here("a")), ("O", lambda: self.set_edge_here("b")),
                        ("A", lambda: self.add_hit(self.pos)), ("Delete", self.toggle_selected),
                        ("R", lambda: self.sel is not None and self.reset_piece(self.sel)),
                        ("Ctrl+Z", self.undo), ("Ctrl+Shift+Z", self.redo), ("Ctrl+Y", self.redo)]:
            QShortcut(QKeySequence(key), self, activated=fn)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self._set_enabled(False)
        self._sync_undo()

        # 찾기와 첫 클립 미리보기를 동시에 시작 → 열자마자 영상이 보인다
        self.job = Job("타격 지점 찾기", H.analyze, (self.metas, copy.deepcopy(s)))
        self.job.log.connect(main.log)
        self.job.progress.connect(lambda f, t: self.head.setText(t))
        self.job.succeeded.connect(self._analyzed)
        self.job.failed.connect(self._analyze_failed)
        self.job.start()
        self._request_preview(self.metas[0])

    # ---------------------------------------------------------------- 도우미
    def _btn(self, text, fn):
        b = QPushButton(text)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.clicked.connect(lambda *_: (self._keys.drop_focus() if hasattr(self, "_keys") else None, fn()))
        return b

    @staticmethod
    def _spin(v, lo, hi):
        sp = QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setDecimals(2)
        sp.setSingleStep(0.1)
        sp.setValue(v)
        sp.setSuffix("초")
        return sp

    def _set_enabled(self, on):
        for w in (self.clip_list, self.sp_pre, self.sp_post, self.sp_gap, self.ck_del, self.btn_save, self.strip,
                  self.cards_box):
            w.setEnabled(on)

    @property
    def st(self):
        return self.states[self.cur] if 0 <= self.cur < len(self.states) else None

    # ---------------------------------------------------------------- 미리보기 자료
    def _cur_key(self):
        st = self.st
        if st is not None:
            return st.meta["_json"]
        return self.metas[0]["_json"] if self.metas else None

    def _request_preview(self, meta):
        k = meta["_json"]
        if k in self.previews or any(j.isRunning() and j.key == k for j in self._pjobs):
            return
        job = Job("미리보기", load_preview, (str(library.file_of(meta, "video")),))
        job.key = k
        job.succeeded.connect(lambda d, k=k: self._preview_ready(k, d))
        job.failed.connect(lambda msg, c, k=k: self._preview_failed(k, msg))
        self._pjobs = [j for j in self._pjobs if j.isRunning()] + [job]
        job.start()

    def _preview_ready(self, k, d):
        self.previews[k] = d
        self._order = [x for x in self._order if x != k] + [k]
        while len(self._order) > MAX_CACHE:   # 오래 안 본 클립의 미리보기는 버려 메모리를 아낀다
            old = self._order.pop(0)
            if old != self._cur_key():
                self.previews.pop(old, None)
        if k == self._cur_key():
            self._start_timer()
            self._refresh()
            self._show_frame()

    def _preview_failed(self, k, msg):
        self.main.log(f"미리보기 실패: {msg}")
        if k == self._cur_key():
            self.view.show_img(None, "미리보기를 만들지 못했어요 (나누기는 할 수 있어요)")

    @property
    def pv(self):
        return self.previews.get(self._cur_key())

    def frame_at(self, t):
        pv = self.pv
        if not pv or not pv["imgs"]:
            return None
        return pv["imgs"][min(len(pv["imgs"]) - 1, max(0, int(round(t * pv["fps"]))))]

    def _start_timer(self):
        pv = self.pv
        fps = pv["fps"] if pv else 15.0
        self.timer.start(int(1000 / max(4.0, min(30.0, fps))))

    # ---------------------------------------------------------------- 찾기 결과
    def _analyze_failed(self, msg, cancelled):
        if not cancelled:
            QMessageBox.warning(self, "타수 나누기", msg)
        self.reject()

    def _analyzed(self, res):
        s = self.main.s
        pre, post, gap = self.sp_pre.value(), self.sp_post.value(), self.sp_gap.value()
        self.states = [H.ClipEdit(r["meta"], r["name"], r["duration"], r["cands"], r["has_audio"], pre, post, gap,
                                  float(s.get("hit_min_len", 1.0)), float(r["meta"].get("fps") or 30.0)) for r in res]
        if not self.states:
            QMessageBox.information(self, "타수 나누기", "클립을 분석하지 못했어요. 로그를 확인해 주세요.")
            self.reject()
            return
        self.metas = [st.meta for st in self.states]   # 분석에 실패한 클립은 빠진다
        self._set_enabled(True)
        self.clip_list.blockSignals(True)
        self.clip_list.clear()
        for _st in self.states:
            self.clip_list.addItem(QListWidgetItem(""))
        self.clip_list.blockSignals(False)
        for i in range(len(self.states)):
            self._refresh_row(i)
        if len(self.states) == 1:
            self.left.hide()
        self.clip_list.setCurrentRow(0)
        if self.cur != 0:   # 신호가 안 왔으면 직접
            self._select_clip(0)

    def _refresh_row(self, i):
        st = self.states[i]
        n, total = len(st.on_pieces()), len(st.pieces())
        tag = f"{n}타로 나눔" if n >= H.MIN_HITS else ("나눌 게 없음" if total < H.MIN_HITS else "2타 이상 사용 필요")
        it = self.clip_list.item(i)
        if it is not None:
            it.setText(f"{st.name}\n   {tag}" + ("" if st.has_audio else " · 소리 없음"))

    def _select_clip(self, row):
        if not (0 <= row < len(self.states)):
            return
        self.cur = row
        self.playing = False
        on = self.st.on_pieces()
        self.sel = on[0]["key"] if on else None
        self.pos = on[0]["a"] if on else 0.0
        if self.pv is not None:
            self._start_timer()
        else:
            self.view.show_img(None, "미리보기 만드는 중...")
            self._request_preview(self.st.meta)
        self._refresh()
        self._show_frame()

    # ---------------------------------------------------------------- 화면 갱신
    def _refresh(self):
        st = self.st
        if st is None:
            return
        ps = st.pieces()
        if self.sel is not None and all(p["key"] != self.sel for p in ps):
            self.sel = None
        pv = self.pv
        self.strip.set_data(st.duration, ps, self.sel, pv.get("env") if pv else None)
        labels = self._labels(ps)
        keys = [p["key"] for p in ps]
        if keys != [c.p["key"] for c in self._cards if c.p]:
            self._rebuild_cards(len(ps))
        for c, p in zip(self._cards, ps):
            c.set_piece(p, labels[p["key"]], p["key"] == self.sel)
        self.head.setText(self._head_text(st, ps))
        self._refresh_row(self.cur)
        self._update_summary()
        self._sync_undo()

    @staticmethod
    def _labels(ps):
        out, k = {}, 0
        for p in ps:
            if p["on"]:
                k += 1
                out[p["key"]] = f"{k}타"
            else:
                out[p["key"]] = "뺀 타격"
        return out

    def _rebuild_cards(self, n):
        for c in self._cards:
            self.cards_lay.removeWidget(c)
            c.deleteLater()
        self._cards = []
        for _ in range(n):
            c = PieceCard(self)
            self.cards_lay.insertWidget(self.cards_lay.count() - 1, c)
            self._cards.append(c)

    def _head_text(self, st, ps):
        n = sum(1 for p in ps if p["on"])
        if not ps:
            return (f"{st.name}: 타격 지점을 찾지 못했어요. "
                    + ("뚜렷한 타격음이 없어요." if st.has_audio else "소리가 없고 번쩍임도 없어요.")
                    + " 줄을 더블클릭하거나 A 키로 타격을 직접 추가할 수 있어요.")
        if n < H.MIN_HITS:
            return f"{st.name}: 타격 {len(ps)}개 중 {n}개 사용 중이에요. 2개 이상 사용해야 나눌 수 있어요."
        edited = sum(1 for p in ps if p["on"] and p["edited"])
        return (f"{st.name}: 타격 {len(ps)}개 중 {n}개 사용 → 한 타 클립 {n}개"
                + (f" (직접 고친 구간 {edited}개)" if edited else ""))

    def _update_summary(self):
        ready = [st for st in self.states if st.ready()]
        made = sum(len(st.on_pieces()) for st in ready)
        self.sum_label.setText(f"저장하면: 클립 {len(ready)}개 → 한 타 클립 {made}개")
        self.btn_save.setEnabled(bool(ready))

    def _show_frame(self):
        st = self.st
        img = self.frame_at(self.pos)
        if st is None:
            self.view.show_img(img, "")
            return
        p = self._sel_piece()
        info = fmt_t(self.pos)
        if p is not None:
            lab = self._labels(st.pieces()).get(p["key"], "")
            info += f"   ·   {lab}  {fmt_t(p['a'])} ~ {fmt_t(p['b'])}"
        self.view.show_img(img, info)
        self.strip.set_pos(self.pos)
        self.time_label.setText(f"{fmt_t(self.pos)} / {fmt_t(st.duration)}")
        self.btn_play.setText("⏸ 정지 (Space)" if self.playing else "▶ 재생 (Space)")

    def _sel_piece(self):
        st = self.st
        if st is None or self.sel is None:
            return None
        return next((p for p in st.pieces() if p["key"] == self.sel), None)

    # ---------------------------------------------------------------- 재생
    def _tick(self):
        pv = self.pv
        dt = 1.0 / (pv["fps"] if pv else 15.0)
        self.phase += dt
        for c in self._cards:
            c.tick(self.phase)
        if self.playing and self.st is not None:
            p = self._sel_piece()
            if self.ck_loop.isChecked() and p is not None:
                a, b = p["a"], p["b"]
                self.pos = self.pos + dt
                if self.pos >= b or self.pos < a - 1e-6:
                    self.pos = a
            else:
                self.pos += dt
                if self.pos >= self.st.duration:
                    self.pos = 0.0
            self._show_frame()

    def toggle_play(self):
        if self.st is None:
            return
        self.playing = not self.playing
        self._show_frame()

    def step(self, n):
        st = self.st
        if st is None:
            return
        self.playing = False
        fps = self.pv["fps"] if self.pv else st.fps
        self.pos = min(st.duration, max(0.0, self.pos + n / fps))
        self._show_frame()

    def _seek(self, t):
        self._keys.drop_focus()
        self.playing = False
        self.pos = float(t)
        self._show_frame()

    def select_piece(self, key, play=False):
        self._keys.drop_focus()
        self.sel = key
        p = self._sel_piece()
        if p is not None and play:
            self.pos = p["a"]
            self.playing = True
        self._refresh()
        self._show_frame()
        i = next((n for n, c in enumerate(self._cards) if c.p and c.p["key"] == key), None)
        if i is not None:
            self.cards_scroll.ensureWidgetVisible(self._cards[i])

    def select_rel(self, d):
        st = self.st
        if st is None:
            return
        ps = st.pieces()
        if not ps:
            return
        keys = [p["key"] for p in ps]
        i = keys.index(self.sel) + d if self.sel in keys else 0
        self.select_piece(keys[max(0, min(len(keys) - 1, i))], play=True)

    # ---------------------------------------------------------------- 편집 (모두 되돌리기 가능)
    def _edit(self, fn):
        st = self.st
        if st is None:
            return False
        before = st.snapshot()
        if not fn(st):
            return False
        self._push((self.cur, before))
        self._refresh()
        self._show_frame()
        return True

    def _push(self, rec):
        self._undo.append(rec)
        del self._undo[:-UNDO_MAX]
        self._redo.clear()
        self._sync_undo()

    def _sync_undo(self):
        self.btn_undo.setEnabled(bool(self._undo))
        self.btn_redo.setEnabled(bool(self._redo))

    def _history(self, src, dst):
        if self.job is not None and self.job.isRunning():
            return
        while src:
            i, snap = src.pop()
            if not (0 <= i < len(self.states)):
                continue
            st = self.states[i]
            if st.snapshot() == snap:
                continue
            dst.append((i, st.snapshot()))
            st.restore(snap)
            if i != self.cur:
                self.clip_list.setCurrentRow(i)
                if self.cur != i:
                    self._select_clip(i)
            self._refresh()
            self._show_frame()
            break
        self._sync_undo()

    def undo(self):
        self._history(self._undo, self._redo)

    def redo(self):
        self._history(self._redo, self._undo)

    def set_on(self, key, on):
        return self._edit(lambda st: st.set_on(key, on))

    def toggle_selected(self):
        p = self._sel_piece()
        if p is not None:
            self.set_on(p["key"], not p["on"])

    def nudge(self, key, side, n):
        self.sel = key
        if self._edit(lambda st: st.nudge(key, side, n)):
            p = self._sel_piece()
            if p:   # 바꾼 끝의 프레임을 보여줌
                self.playing = False
                self.pos = p[side] - (1e-3 if side == "b" else 0.0)
                self._show_frame()

    def reset_piece(self, key):
        return self._edit(lambda st: st.reset(key))

    def set_edge_here(self, side):
        p = self._sel_piece()
        if p is None or not p["on"]:
            return
        self._edit(lambda st: st.set_edge(p["key"], side, self.pos))

    def add_hit(self, t):
        res = {}

        def fn(st):
            res["k"] = st.add_hit(t)
            return res["k"] is not None

        if self._edit(fn):
            if any(p["key"] == res["k"] for p in self.st.pieces()):
                self.select_piece(res["k"])

    def remove_added(self, key):
        return self._edit(lambda st: st.remove_added(key))

    def _grabbed(self):
        self._keys.drop_focus()
        self.playing = False
        self._grab = (self.cur, self.st.snapshot()) if self.st else None

    def _drag_edge(self, key, side, t):
        st = self.st
        if st is None:
            return
        self.sel = key
        if st.set_edge(key, side, t):
            if self._grab is not None:   # 실제로 움직였을 때만 한 번 기록
                self._push(self._grab)
                self._grab = None
            self._refresh()
        p = self._sel_piece()
        if p:
            self.pos = p[side] - (1e-3 if side == "b" else 0.0)
            self._show_frame()

    def _released(self):
        self._grab = None

    def piece_menu(self, key, global_pos):
        st = self.st
        p = next((x for x in st.pieces() if x["key"] == key), None) if st else None
        if p is None:
            return
        self.select_piece(key)
        m = QMenu(self)
        m.addAction("▶ 이 타만 반복 재생", lambda: self.select_piece(key, play=True))
        m.addAction("🙈 이 타격 빼기" if p["on"] else "✅ 다시 사용", lambda: self.set_on(key, not p["on"]))
        if p["edited"]:
            m.addAction("↺ 자동 구간으로 되돌리기", lambda: self.reset_piece(key))
        if p["kind"] == "직접":
            m.addAction("🗑 직접 추가한 타격 지우기", lambda: self.remove_added(key))
        m.exec(global_pos)

    def _params_changed(self):
        for st in self.states:
            st.set_params(self.sp_pre.value(), self.sp_post.value(), self.sp_gap.value())
        self._refresh()
        self._show_frame()

    # ---------------------------------------------------------------- 저장
    def save(self):
        items = [st.to_item() for st in self.states if st.ready()]
        skipped = [st.name for st in self.states if not st.ready()]
        if not items:
            QMessageBox.information(self, "타수 나누기", "타격이 2개 이상 사용 중인 클립이 없어요.")
            return
        keep = not self.ck_del.isChecked()
        if not keep and QMessageBox.question(
                self, "원래 클립 지우기",
                f"나눈 뒤 원래 긴 클립 {len(items)}개를 지워요. 되돌릴 수 없어요. 계속할까요?") != QMessageBox.StandardButton.Yes:
            return
        self.playing = False
        self._set_enabled(False)
        self.head.setText("한 타씩 잘라서 저장하는 중...")
        self.job = Job("타수별로 나누기", H.apply, (items, copy.deepcopy(self.main.s)), {"keep_original": keep})
        self.job.log.connect(self.main.log)
        self.job.progress.connect(lambda f, t: self.head.setText(t))
        self.job.succeeded.connect(lambda r: self._saved(r, skipped))
        self.job.failed.connect(self._save_failed)
        self.job.start()

    def _saved(self, r, skipped):
        self.changed = r["made"] > 0 or self.changed
        msg = f"클립 {r['clips']}개를 한 타 클립 {r['made']}개로 나눴어요."
        if r["removed"]:
            msg += f"\n원래 긴 클립 {r['removed']}개는 지웠어요."
        if skipped:
            msg += f"\n\n건너뜀 (사용 중인 타격이 2개 미만): {', '.join(skipped[:5])}" + (" …" if len(skipped) > 5 else "")
        if r["failed"]:
            msg += "\n\n문제가 있었어요:\n" + "\n".join(f"· {n}: {why[:120]}" for n, why in r["failed"][:5])
        QMessageBox.information(self, "타수 나누기", msg)
        self.accept()

    def _save_failed(self, msg, cancelled):
        if not cancelled:
            QMessageBox.warning(self, "타수 나누기", msg)
        self._set_enabled(True)
        self._refresh()

    def done(self, r):
        self.timer.stop()
        if self.job is not None and self.job.isRunning():
            self.job.cancel.set()
            self.job.wait(15000)
        for j in self._pjobs:
            if j.isRunning():
                j.wait(15000)
        super().done(r)
