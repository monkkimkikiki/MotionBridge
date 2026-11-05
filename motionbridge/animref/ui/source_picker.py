"""직접 자르기: 원본 영상 고르기 화면 (최근 녹화본/분석했던 영상이 썸네일로 바로 보임)."""
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QPushButton, QVBoxLayout)

from ..core import media, sources

from .widgets import muted, open_path

ROLE = Qt.ItemDataRole.UserRole


class ThumbLoader(QThread):
    ready = Signal(str, str)  # 영상 경로, 썸네일 경로

    def __init__(self, paths):
        super().__init__()
        self.paths = list(paths)
        self.stop = False

    def run(self):
        for p in self.paths:
            if self.stop:
                return
            t = sources.cached_thumb(p)
            if t:
                self.ready.emit(p, t)


class SourcePicker(QDialog):
    def __init__(self, main, queue_paths=()):
        super().__init__(main)
        self.main = main
        self.chosen = None
        self.loader = None
        self.setWindowTitle("직접 자를 원본 영상 고르기")
        self.resize(900, 640)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        t = QLabel("✂ 어떤 영상에서 자를까요?")
        t.setObjectName("cardTitle")
        root.addWidget(t)
        self.where = muted("")
        root.addWidget(self.where)

        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 파일 이름으로 찾기")
        self.search.textChanged.connect(self.apply_filter)
        root.addWidget(self.search)

        self.list = QListWidget()
        self.list.setIconSize(QSize(160, 90))
        self.list.setSpacing(4)
        self.list.itemDoubleClicked.connect(lambda it: self.accept_item(it))
        self.list.itemActivated.connect(lambda it: self.accept_item(it))
        root.addWidget(self.list, 1)

        self.empty = muted("")
        self.empty.hide()
        root.addWidget(self.empty)

        row = QHBoxLayout()
        b_browse = QPushButton("📁 다른 영상 찾아보기...")
        b_browse.clicked.connect(self.browse)
        row.addWidget(b_browse)
        self.b_folder = QPushButton("녹화 폴더 열기")
        self.b_folder.clicked.connect(self._open_folder)
        row.addWidget(self.b_folder)
        row.addStretch(1)
        cancel = QPushButton("취소")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.b_open = QPushButton("✂ 이 영상 열기")
        self.b_open.setObjectName("primary")
        self.b_open.clicked.connect(lambda: self.accept_item(self.list.currentItem()))
        row.addWidget(self.b_open)
        root.addLayout(row)

        self.items = sources.list_sources(main.s, queue_paths)
        dirs = sources.candidate_dirs(main.s)
        obs_dir = main.s.get("obs_record_dir")
        if obs_dir and Path(obs_dir).is_dir():
            self.where.setText(f"OBS 녹화 폴더: {obs_dir}")
        elif dirs:
            self.where.setText(f"찾아본 폴더: {', '.join(dirs[:2])}   (OBS를 연결하면 녹화 폴더를 자동으로 찾아요)")
        self.b_folder.setEnabled(bool(dirs))
        self.fill()

    def fill(self):
        self.list.clear()
        need_thumb = []
        for src in self.items:
            it = QListWidgetItem(f"{src['name']}\n{sources.describe(src)}")
            it.setData(ROLE, src["path"])
            it.setToolTip(src["path"])
            if src["thumb"]:
                it.setIcon(QIcon(QPixmap(src["thumb"])))
            else:
                need_thumb.append(src["path"])
            self.list.addItem(it)
        if self.items:
            self.list.setCurrentRow(0)  # 가장 최근 영상
            self.list.setFocus()
            self.empty.hide()
        else:
            self.empty.setText("찾은 영상이 없어요. [📁 다른 영상 찾아보기]로 직접 골라 주세요.")
            self.empty.show()
        self.b_open.setEnabled(bool(self.items))
        if need_thumb:
            self.loader = ThumbLoader(need_thumb[:60])
            self.loader.ready.connect(self._thumb_ready)
            self.loader.start()

    def _thumb_ready(self, path, thumb):
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.data(ROLE) == path:
                it.setIcon(QIcon(QPixmap(thumb)))

    def apply_filter(self, text):
        q = text.strip().lower()
        first = None
        for i in range(self.list.count()):
            it = self.list.item(i)
            hide = bool(q) and q not in Path(it.data(ROLE)).name.lower()
            it.setHidden(hide)
            if not hide and first is None:
                first = i
        if first is not None:
            self.list.setCurrentRow(first)

    def accept_item(self, it):
        if it is None or it.isHidden():
            return
        self.chosen = it.data(ROLE)
        self.accept()

    def browse(self):
        start = sources.best_start_dir(self.main.s, self.items)
        exts = " ".join(f"*{x}" for x in sorted(media.VIDEO_EXTS))
        path, _ = QFileDialog.getOpenFileName(self, "원본 영상 선택", start, f"영상 파일 ({exts});;모든 파일 (*)")
        if path:
            self.chosen = path
            self.accept()

    def _open_folder(self):
        dirs = sources.candidate_dirs(self.main.s)
        if dirs:
            open_path(dirs[0])

    def done(self, r):
        if self.loader is not None and self.loader.isRunning():
            self.loader.stop = True
            self.loader.wait(5000)
        if self.chosen:
            self.main.s["last_dir"] = str(Path(self.chosen).parent)
            self.main.save_settings()
        super().done(r)
