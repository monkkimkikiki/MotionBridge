"""캐릭터 등록 / 대상 고르기 창.

  CharactersDialog      : 캐릭터 이름 + 참고 이미지를 등록·수정·삭제한다 (AI 모캡이 '누구의 모션을 뽑을지' 찾는 데 쓴다)
  SubjectPickerDialog   : 자동으로 못 정한 클립에서 후보 사람들의 사진을 보고 '이 사람'을 직접 클릭해서 고른다"""
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QListView, QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from ..core import characters as C
from ..core import library
from ..core.library import file_of

from .widgets import Job, muted

IMG_FILTER = "이미지 (*.png *.jpg *.jpeg *.webp *.bmp);;모든 파일 (*)"
TIPS = ("참고 이미지는 3~8장이 좋아요. 캐릭터가 크게, 가려지지 않게 나온 것으로 정면·옆모습·다른 자세를 섞어 주세요. "
        "게임 스크린샷은 캐릭터 주변만 잘라서 넣으면 더 정확해요. (배경이 크면 배경 색에 끌려가요)")


def _icon_from_bytes(data, side=160):
    pm = QPixmap()
    if data and pm.loadFromData(data):
        return QIcon(pm.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
    return QIcon()


def _pixmap_from_bytes(data):
    pm = QPixmap()
    if data:
        pm.loadFromData(data)
    return pm


def _preview_bytes(meta, cand_or_subject, label=""):
    """대상 상자를 그린 장면 (JPEG). 실패해도 화면이 멈추지 않게 빈 값을 돌려준다."""
    try:
        return C.preview_jpeg(str(file_of(meta, "video")), cand_or_subject["boxes"], label)
    except Exception:  # noqa: BLE001
        return b""


CARD_STYLE = ("QPushButton{border:2px solid transparent;border-radius:10px;padding:6px;}"
              "QPushButton:checked{border:3px solid #46c37b;background:rgba(70,195,123,0.18);}")


def _icon_from_file(path, side=120):
    pm = QPixmap(str(path))
    if pm.isNull():
        return QIcon()
    return QIcon(pm.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))


def _create_adapter(s, name, kind, text, paths, log=None, progress=None, cancel=None):
    """Job 은 progress/cancel 도 넘겨 주므로, 그걸 받는 모양으로 감싼다."""
    if progress:
        progress(-1, "참고 이미지를 분석하는 중... (처음엔 분류 AI를 불러오느라 1분쯤 걸려요)")
    return C.create_character(s, name, kind, text, paths, log=log or (lambda *_: None))


# ==================================================================== 캐릭터 관리
class CharactersDialog(QDialog):
    def __init__(self, main, select=None):
        super().__init__(main)
        self.main = main
        self.changed = False
        self.job = None
        self.cur = None            # 편집 중인 캐릭터 dict (새 캐릭터면 None)
        self.pending = []          # 아직 저장 안 한 새 이미지 경로
        self.setWindowTitle("👤 캐릭터 등록")
        self.resize(900, 640)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.addWidget(muted("자주 뽑는 캐릭터를 등록해 두면, 화면에 여러 명이 나와도 그 캐릭터만 골라서 모션을 뽑을 수 있어요."))
        body = QHBoxLayout()
        root.addLayout(body, 1)

        left = QVBoxLayout()
        self.lst = QListWidget()
        self.lst.setFixedWidth(220)
        self.lst.currentRowChanged.connect(self._on_row)
        left.addWidget(self.lst, 1)
        b_new = QPushButton("＋ 새 캐릭터")
        b_new.clicked.connect(self.new_character)
        self.b_del = QPushButton("🗑 캐릭터 삭제")
        self.b_del.clicked.connect(self.delete_character)
        left.addWidget(b_new)
        left.addWidget(self.b_del)
        body.addLayout(left)

        right = QVBoxLayout()
        row = QHBoxLayout()
        row.addWidget(QLabel("이름"))
        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("예: 빨간 기사")
        row.addWidget(self.ed_name, 1)
        self.cb_kind = QComboBox()
        for k, v in C.KINDS.items():
            self.cb_kind.addItem(v, k)
        row.addWidget(self.cb_kind)
        right.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("외형 설명 (영어, 선택)"))
        self.ed_text = QLineEdit()
        self.ed_text.setPlaceholderText("예: knight in red armor  — 비워도 돼요. 채우면 찾기와 AI 확인이 조금 더 정확해져요")
        row.addWidget(self.ed_text, 1)
        right.addLayout(row)
        right.addWidget(muted("크리처(몬스터 등)는 지금의 AI 모캡(사람 체형 기준)로는 정확도가 낮아요. 우선 '인간형'을 권해요."))
        right.addWidget(QLabel("참고 이미지"))
        self.imgs = QListWidget()
        self.imgs.setViewMode(QListView.ViewMode.IconMode)
        self.imgs.setIconSize(QSize(120, 120))
        self.imgs.setResizeMode(QListView.ResizeMode.Adjust)
        self.imgs.setMovement(QListView.Movement.Static)
        self.imgs.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        right.addWidget(self.imgs, 1)
        row = QHBoxLayout()
        b_add = QPushButton("🖼 이미지 추가...")
        b_add.clicked.connect(self.add_images)
        b_rm = QPushButton("선택한 이미지 빼기")
        b_rm.clicked.connect(self.remove_images)
        row.addWidget(b_add)
        row.addWidget(b_rm)
        row.addStretch(1)
        right.addLayout(row)
        right.addWidget(muted(TIPS))
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setVisible(False)
        right.addWidget(self.bar)
        self.lbl = muted("")
        right.addWidget(self.lbl)
        self.b_save = QPushButton("💾 저장")
        self.b_save.setObjectName("primary")
        self.b_save.clicked.connect(self.save)
        right.addWidget(self.b_save)
        body.addLayout(right, 1)

        close = QPushButton("닫기")
        close.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(close)
        root.addLayout(bottom)

        self.reload(select)

    # ------------------------------------------------------------ 목록
    def reload(self, select=None):
        self.chars = C.list_characters(self.main.s)
        self.lst.blockSignals(True)
        self.lst.clear()
        for c in self.chars:
            self.lst.addItem(QListWidgetItem(f"{c['name']}  ({C.KINDS.get(c.get('kind'), '')[:3]})  · 이미지 {c.get('images', 0)}장"))
        self.lst.blockSignals(False)
        idx = next((i for i, c in enumerate(self.chars) if c["name"] == select), 0 if self.chars else -1)
        if idx >= 0:
            self.lst.setCurrentRow(idx)
            self._on_row(idx)
        else:
            self.new_character()

    def _on_row(self, row):
        if row < 0 or row >= len(self.chars):
            return
        self.cur = self.chars[row]
        self.pending = []
        self.ed_name.setText(self.cur["name"])
        self.ed_name.setReadOnly(True)
        self.cb_kind.setCurrentIndex(max(0, self.cb_kind.findData(self.cur.get("kind", "humanoid"))))
        self.ed_text.setText(self.cur.get("detect_text", ""))
        self._fill_images()
        self.b_del.setEnabled(True)
        self.lbl.setText("")

    def new_character(self):
        self.cur = None
        self.pending = []
        self.lst.blockSignals(True)
        self.lst.clearSelection()
        self.lst.setCurrentRow(-1)
        self.lst.blockSignals(False)
        self.ed_name.setReadOnly(False)
        self.ed_name.clear()
        self.ed_text.clear()
        self.cb_kind.setCurrentIndex(0)
        self._fill_images()
        self.b_del.setEnabled(False)
        self.lbl.setText("새 캐릭터: 이름을 쓰고 참고 이미지를 추가한 뒤 [저장]을 누르세요.")

    def _fill_images(self):
        self.imgs.clear()
        if self.cur:
            for p in C.ref_images(self.cur):
                it = QListWidgetItem(_icon_from_file(p), "저장됨")
                it.setData(Qt.ItemDataRole.UserRole, ("saved", str(p)))
                self.imgs.addItem(it)
        for p in self.pending:
            it = QListWidgetItem(_icon_from_file(p), "새로 추가")
            it.setData(Qt.ItemDataRole.UserRole, ("pending", str(p)))
            self.imgs.addItem(it)

    # ------------------------------------------------------------ 이미지 추가/빼기
    def add_images(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "참고 이미지 고르기", self.main.s.get("last_dir", ""), IMG_FILTER)
        if not paths:
            return
        self.pending += [p for p in paths if p not in self.pending]
        self._fill_images()

    def remove_images(self):
        removed_saved = 0
        for it in self.imgs.selectedItems():
            kind, path = it.data(Qt.ItemDataRole.UserRole)
            if kind == "pending":
                self.pending = [p for p in self.pending if p != path]
            elif self.cur is not None:
                try:
                    C.remove_image(self.cur, path)
                    removed_saved += 1
                except ValueError as e:
                    QMessageBox.information(self, "참고 이미지", str(e))
                    break
        if removed_saved:
            self.changed = True
            self.job = None
            self.lbl.setText("이미지를 뺐어요. [저장]을 누르면 특징을 다시 계산해요.")
        self._fill_images()

    # ------------------------------------------------------------ 저장 / 삭제
    def save(self):
        if self.job is not None and self.job.isRunning():
            return
        name = self.ed_name.text().strip()
        if not name:
            QMessageBox.information(self, "캐릭터", "이름을 입력해 주세요.")
            return
        if self.cur is None and any(c["name"] == name for c in self.chars):
            QMessageBox.information(self, "캐릭터", "같은 이름의 캐릭터가 이미 있어요. 왼쪽 목록에서 골라 수정하세요.")
            return
        if self.cur is None and not self.pending:
            QMessageBox.information(self, "캐릭터", "참고 이미지를 1장 이상 추가해 주세요.")
            return
        self.b_save.setEnabled(False)
        self.bar.setVisible(True)
        self.lbl.setText("저장하는 중...")
        self.job = Job("캐릭터 저장", _create_adapter,
                       (self.main.s, name, self.cb_kind.currentData(), self.ed_text.text().strip(), list(self.pending)))
        self.job.log.connect(self.main.log)
        self.job.progress.connect(lambda f, t: self.lbl.setText(t) if t else None)
        self.job.succeeded.connect(lambda c: self._saved(c["name"]))
        self.job.failed.connect(self._failed)
        self.job.finished.connect(lambda: (self.b_save.setEnabled(True), self.bar.setVisible(False)))
        self.job.start()

    def _saved(self, name):
        self.changed = True
        self.pending = []
        self.lbl.setText("✅ 저장했어요.")
        self.reload(name)

    def _failed(self, msg, cancelled):
        self.lbl.setText(("⏹ " if cancelled else "⚠ ") + msg.splitlines()[0])
        if not cancelled:
            QMessageBox.warning(self, "캐릭터", msg)

    def delete_character(self):
        if self.cur is None:
            return
        if QMessageBox.question(self, "캐릭터 삭제", f"'{self.cur['name']}' 캐릭터와 참고 이미지를 지울까요? (이미 뽑은 모션은 그대로예요)") != QMessageBox.StandardButton.Yes:
            return
        C.delete_character(self.cur)
        self.changed = True
        self.reload()

    def done(self, r):
        if self.job is not None and self.job.isRunning():
            self.job.cancel.set()
            self.job.wait(30000)
        super().done(r)


# ==================================================================== 대상 고르기
class SubjectPickerDialog(QDialog):
    """entries: [(meta, locate 결과)] — 클립마다 후보 사진을 보여 주고 직접 고르게 한다."""

    def __init__(self, main, entries, character_name=""):
        super().__init__(main)
        self.main = main
        self.entries = list(entries)
        self.character = character_name or ""
        self.i = -1
        self.saved = 0
        self.group = None
        self.cards = []
        self.setWindowTitle("🎯 이 클립에서 모션을 뽑을 사람 고르기")
        self.resize(980, 760)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        self.head = QLabel("")
        self.head.setWordWrap(True)
        root.addWidget(self.head)
        self.sub = muted("")
        root.addWidget(self.sub)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.holder = QWidget()
        self.row = QHBoxLayout(self.holder)
        self.row.setContentsMargins(6, 6, 6, 6)
        self.scroll.setWidget(self.holder)
        self.scroll.setFixedHeight(300)
        root.addWidget(self.scroll)
        self.big = QLabel("후보를 누르면 영상 장면에 상자로 표시돼요.")
        self.big.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.big.setMinimumHeight(260)
        root.addWidget(self.big, 1)
        btns = QHBoxLayout()
        self.b_ok = QPushButton("✅ 이 사람으로 정하기")
        self.b_ok.setObjectName("primary")
        self.b_ok.clicked.connect(self.confirm)
        self.b_skip = QPushButton("건너뛰기 (나중에)")
        self.b_skip.clicked.connect(self.next)
        self.b_none = QPushButton("이 클립은 자동(가장 오래 나온 사람)으로")
        self.b_none.clicked.connect(self.use_auto)
        for b in (self.b_ok, self.b_skip, self.b_none):
            btns.addWidget(b)
        btns.addStretch(1)
        root.addLayout(btns)
        self.next()

    def _clear(self):
        while self.row.count():
            it = self.row.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        self.cards = []

    def next(self):
        self.i += 1
        if self.i >= len(self.entries):
            self.accept()
            return
        meta, res = self.entries[self.i]
        name = Path(meta["_json"]).stem
        self.head.setText(f"클립 {self.i + 1}/{len(self.entries)}: {name}")
        self.sub.setText(C.describe_status(res["status"], res.get("how", "")) +
                         "  ·  사진을 눌러 고르세요. 점수는 등록한 캐릭터와 닮은 정도예요(높을수록 닮음, 절대 정답은 아니에요).")
        self._clear()
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        cands = sorted(res["candidates"], key=lambda c: -(c["score"] or 0.0) if self.character else -c["n"])
        for k, c in enumerate(cands, 1):
            b = QPushButton()
            b.setCheckable(True)
            b.setIconSize(QSize(170, 170))
            b.setIcon(_icon_from_bytes(c.get("thumb"), 170))
            sc = f"닮음 {c['score']:.2f}" if self.character and c.get("score") is not None else ""
            b.setText(f"후보 {k}\n{c['t0']:.1f}~{c['t1']:.1f}초 · {c['n']}프레임\n{sc}")
            b.setFixedSize(210, 270)
            b.setToolTip("이 사람의 모션을 뽑아요")
            b.setStyleSheet(CARD_STYLE)
            b.setProperty("cand", c)
            b.setProperty("base_text", b.text())
            self.group.addButton(b)
            self.row.addWidget(b)
            self.cards.append(b)
        self.row.addStretch(1)
        self.group.buttonToggled.connect(self._on_toggled)
        if self.cards:
            self.cards[0].setChecked(True)
        self.b_ok.setEnabled(bool(self.cards))

    def _on_toggled(self, btn, checked):
        base = btn.property("base_text") or ""
        btn.setText(("✅ 선택됨\n" + base) if checked else base)
        if not checked:
            return
        meta, _res = self.entries[self.i]
        c = btn.property("cand")
        pm = _pixmap_from_bytes(_preview_bytes(meta, c))
        if pm.isNull():
            self.big.setText("미리보기를 만들지 못했어요. 위 사진을 보고 골라 주세요.")
        else:
            self.big.setPixmap(pm.scaled(self.big.width(), max(220, self.big.height()), Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))

    def confirm(self):
        meta, res = self.entries[self.i]
        b = self.group.checkedButton() if self.group else None
        if b is None:
            return
        c = b.property("cand")
        C.save_subject(meta, c, self.character, "user", res["video_size"])
        self.saved += 1
        self.next()

    def use_auto(self):
        meta, _res = self.entries[self.i]
        C.clear_subject(meta)
        self.next()


# ==================================================================== 자동으로 찾은 결과 확인
class SubjectReviewDialog(QDialog):
    """자동으로 정해진 대상을 영상 장면 위에 상자로 보여 주고, 맞는지 확인받는다."""

    def __init__(self, main, entries, character_name=""):
        super().__init__(main)
        self.main = main
        self.entries = list(entries)
        self.character = character_name or ""
        self.i = 0
        self.redo = []                       # 사용자가 '다른 사람'이라고 한 (meta, res)
        self.setWindowTitle("🎯 찾은 대상 확인")
        self.resize(900, 640)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        self.head = QLabel("")
        self.head.setWordWrap(True)
        root.addWidget(self.head)
        self.img = QLabel("")
        self.img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img.setMinimumHeight(420)
        root.addWidget(self.img, 1)
        row = QHBoxLayout()
        self.b_yes = QPushButton("✅ 맞아요")
        self.b_yes.setObjectName("primary")
        self.b_yes.clicked.connect(self.next)
        self.b_no = QPushButton("❌ 다른 사람이에요 (직접 고르기)")
        self.b_no.clicked.connect(self.reject_one)
        row.addWidget(self.b_yes)
        row.addWidget(self.b_no)
        row.addStretch(1)
        root.addLayout(row)
        self._show()

    def _show(self):
        if self.i >= len(self.entries):
            self.accept()
            return
        meta, res = self.entries[self.i]
        sj = meta.get("subject") or {}
        who = sj.get("character") or "직접 고른 사람"
        how = {"vlm": "AI가 확인했어요", "auto": "닮은 정도로 자동 선택했어요"}.get(sj.get("how"), "")
        self.head.setText(f"<b>클립 {self.i + 1}/{len(self.entries)}</b>: {Path(meta['_json']).stem}<br>"
                          f"초록 상자가 <b>{who}</b>로 정한 사람이에요. {how}")
        pm = _pixmap_from_bytes(_preview_bytes(meta, sj, "TARGET"))
        if pm.isNull():
            self.img.setText("미리보기를 만들지 못했어요.")
        else:
            self.img.setPixmap(pm.scaled(860, 460, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def next(self):
        self.i += 1
        self._show()

    def reject_one(self):
        meta, res = self.entries[self.i]
        C.clear_subject(meta)
        self.redo.append((meta, res))
        self.next()
