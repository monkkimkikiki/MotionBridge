"""MotionBridge 메인 창 (PySide6)."""
from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QColor, QDesktopServices, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton,
    QScrollArea, QSlider, QSplitter, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout,
    QWidget, QLineEdit, QButtonGroup, QAbstractItemView,
)

from .. import __version__
from ..core import bridge, extractor, gamescan
from ..core.export import PRESETS
from ..core.loader import FILE_FILTER
from ..core.mapping import FINGERS, SLOT_ORDER, slot_label
from ..core.pipeline import ExportOptions, Project
from .help_text import EXTRACT_HELP
from .viewer import Viewport
from .viewer_math import ViewData
from .widgets import NumField, Worker

AXES = ["auto", "+X", "-X", "+Y", "-Y", "+Z", "-Z"]
AXIS_LABEL = {"auto": "자동"}


def _is_finger(slot: str) -> bool:
    return any(f in slot for f in FINGERS)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MotionBridge %s — 모션 리타깃" % __version__)
        self.pr = Project()
        self.cfg = bridge.load_config()
        self.pr.blender = self.cfg.get("blender")
        self.vd: Optional[ViewData] = None
        self._worker: Optional[Worker] = None
        self._fields: Dict[str, object] = {}  # settings 키 -> 위젯 (프로필 불러올 때 동기화)
        self._syncing = False
        self._playing = False
        self.scan: Optional[gamescan.GameScan] = None

        self.recompute_timer = QTimer(self)
        self.recompute_timer.setSingleShot(True)
        self.recompute_timer.setInterval(120)
        self.recompute_timer.timeout.connect(self.recompute)
        self.play_timer = QTimer(self)
        self.play_timer.timeout.connect(self._tick)

        self._build_menu()
        self._build_ui()
        self.statusBar().showMessage("소스 모션과 타깃 모델을 열어 주세요.")
        self.status_progress = QProgressBar()
        self.status_progress.setMaximumWidth(220)
        self.status_progress.setVisible(False)
        self.statusBar().addPermanentWidget(self.status_progress)

    # ================================================================== 메뉴
    def _build_menu(self) -> None:
        m = self.menuBar().addMenu("파일")
        for text, fn in (
            ("소스 모션 열기…", self.open_source),
            ("타깃 모델 열기…", self.open_target),
            (None, None),
            ("프로필 불러오기…", self.load_profile),
            ("프로필 저장…", self.save_profile),
            (None, None),
            ("Blender 경로 지정…", self.pick_blender),
            (None, None),
            ("종료", self.close),
        ):
            if text is None:
                m.addSeparator()
            else:
                a = QAction(text, self)
                a.triggered.connect(lambda checked=False, f=fn: f())
                m.addAction(a)
        h = self.menuBar().addMenu("도움말")
        a = QAction("정보", self)
        a.triggered.connect(lambda checked=False: QMessageBox.information(
            self, "MotionBridge", "MotionBridge %s\n게임 모션 → 내 모델 리타깃 / Max·Maya·Blender 내보내기" % __version__))
        h.addAction(a)

    # ================================================================== UI
    def _build_ui(self) -> None:
        split = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(split)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_extract(), "0. 게임에서 추출")
        self.tabs.addTab(self._tab_files(), "1. 파일·매핑")
        self.tabs.addTab(self._tab_retarget(), "2. 리타깃 조절")
        self.tabs.addTab(self._tab_quality(), "3. 품질 검수")
        self.tabs.addTab(self._tab_export(), "4. 내보내기")
        self.tabs.addTab(self._tab_help(), "추출 도우미")
        self.tabs.setMinimumWidth(470)
        split.addWidget(self.tabs)

        right = QWidget()
        self.right_box = right
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.viewport = Viewport()
        rl.addWidget(self.viewport, 1)
        opts = QHBoxLayout()
        for text, attr, default in (("소스 겹쳐 보기", "show_ghost", True), ("메시 표시", "show_mesh", True), ("발 고정 표시", "show_ik", True), ("카메라 따라가기", "follow", True)):
            cb = QCheckBox(text)
            cb.setChecked(default)
            cb.toggled.connect(lambda v, a=attr: (setattr(self.viewport, a, bool(v)), self.viewport.update()))
            opts.addWidget(cb)
        b = QPushButton("화면 맞추기")
        b.clicked.connect(self._reframe)
        opts.addWidget(b)
        opts.addStretch(1)
        rl.addLayout(opts)
        tl = QHBoxLayout()
        self.btn_play = QPushButton("▶ 재생")
        self.btn_play.setCheckable(True)
        self.btn_play.toggled.connect(self._toggle_play)
        tl.addWidget(self.btn_play)
        self.frame_slider = QSlider(Qt.Orientation.Horizontal)
        self.frame_slider.setRange(0, 0)
        self.frame_slider.valueChanged.connect(self._frame_changed)
        tl.addWidget(self.frame_slider, 1)
        self.frame_label = QLabel("0 / 0")
        self.frame_label.setMinimumWidth(130)
        tl.addWidget(self.frame_label)
        rl.addLayout(tl)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([500, 860])

    # ------------------------------------------------------------------ 탭 0 (게임에서 추출)
    def _tab_extract(self) -> QWidget:
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        w = QWidget()
        sc.setWidget(w)
        lay = QVBoxLayout(w)

        g = QGroupBox("1) 게임 폴더")
        v = QVBoxLayout(g)
        h = QHBoxLayout()
        self.ex_game = QLineEdit(self.cfg.get("game_dir", ""))
        self.ex_game.setPlaceholderText(r"예: D:\SteamLibrary\steamapps\common\Sparta")
        h.addWidget(self.ex_game, 1)
        b = QPushButton("찾아보기…")
        b.clicked.connect(self._pick_game)
        h.addWidget(b)
        b = QPushButton("검사")
        b.clicked.connect(self._scan_game)
        h.addWidget(b)
        v.addLayout(h)
        self.ex_summary = QLabel("게임 설치 폴더를 고르고 [검사]를 누르면 암호화 여부를 확인합니다.")
        self.ex_summary.setWordWrap(True)
        v.addWidget(self.ex_summary)
        lay.addWidget(g)

        g = QGroupBox("2) 추출 설정")
        f = QFormLayout(g)
        self.ex_aes = QLineEdit()
        self.ex_aes.setPlaceholderText("검사 후, 암호화된 게임일 때만 입력")
        self.ex_aes.setEnabled(False)
        f.addRow("AES 키", self.ex_aes)
        self.ex_ue = QComboBox()
        for v_ in gamescan.UE_VERSIONS:
            self.ex_ue.addItem(v_, v_)
        self.ex_ue.setCurrentIndex(max(self.ex_ue.findData(self.cfg.get("ue", "GAME_UE5_4")), 0))
        self.ex_ue.setToolTip("정확한 버전을 모르면 최신부터 시도하세요. 안 읽히면 다른 버전으로 바꿔 다시 검색합니다.")
        f.addRow("UE 버전", self.ex_ue)
        h = QHBoxLayout()
        self.ex_usmap = QLineEdit(self.cfg.get("usmap", ""))
        self.ex_usmap.setPlaceholderText("언리얼 5 게임은 보통 필요 (.usmap)")
        h.addWidget(self.ex_usmap, 1)
        b = QPushButton("찾아보기…")
        b.clicked.connect(self._pick_usmap)
        h.addWidget(b)
        wrap = QWidget()
        wrap.setLayout(h)
        f.addRow("mappings", wrap)
        self.ex_helper_label = QLabel("")
        self.ex_helper_label.setWordWrap(True)
        f.addRow("추출 도우미", self.ex_helper_label)
        b = QPushButton("도우미 경로 지정…")
        b.clicked.connect(self._pick_helper)
        f.addRow("", b)
        self._update_helper_label()
        lay.addWidget(g)

        g = QGroupBox("3) 에셋 찾기 / 추출")
        v = QVBoxLayout(g)
        h = QHBoxLayout()
        h.addWidget(QLabel("종류"))
        self.ex_kind = QComboBox()
        for label, key in (("애니메이션 (PSA)", "anim"), ("메시 (GLB, 스켈레톤+스킨)", "mesh_glb"), ("메시 (PSK)", "mesh_psk")):
            self.ex_kind.addItem(label, key)
        self.ex_kind.currentIndexChanged.connect(self._kind_changed)
        h.addWidget(self.ex_kind, 1)
        self.ex_tex = QCheckBox("머티리얼/텍스처 포함 (GLB, 느림)")
        h.addWidget(self.ex_tex)
        v.addLayout(h)
        h = QHBoxLayout()
        self.ex_filter = QLineEdit(self.KIND_FILTERS["anim"])
        self.ex_filter.setToolTip("에셋 경로에 대한 정규식. 예: (?i)(walk|run|attack)")
        h.addWidget(self.ex_filter, 1)
        b = QPushButton("검색")
        b.clicked.connect(self._search_anims)
        h.addWidget(b)
        v.addLayout(h)
        self.ex_list = QListWidget()
        self.ex_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.ex_list.setMinimumHeight(180)
        v.addWidget(self.ex_list)
        self.ex_count = QLabel("")
        v.addWidget(self.ex_count)
        h = QHBoxLayout()
        b = QPushButton("전체 선택")
        b.clicked.connect(lambda: self.ex_list.selectAll())
        h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)
        h = QHBoxLayout()
        self.ex_out = QLineEdit(self.cfg.get("extract_out") or os.path.join(os.path.expanduser("~"), "MotionBridge_extracted"))
        h.addWidget(self.ex_out, 1)
        b = QPushButton("찾아보기…")
        b.clicked.connect(lambda: self._pick_dir_into(self.ex_out))
        h.addWidget(b)
        v.addLayout(h)
        self.btn_extract = QPushButton("선택한 애니메이션 추출 (PSA)")
        self._kind_changed()
        self.btn_extract.setMinimumHeight(34)
        self.btn_extract.clicked.connect(self._extract_selected)
        v.addWidget(self.btn_extract)
        lay.addWidget(g)

        g = QGroupBox("4) 추출된 파일 열기")
        v = QVBoxLayout(g)
        self.ex_files = QListWidget()
        self.ex_files.setMinimumHeight(110)
        self.ex_files.itemDoubleClicked.connect(lambda it: self._open_extracted())
        v.addWidget(self.ex_files)
        b = QPushButton("선택한 파일을 소스 모션으로 열기")
        b.clicked.connect(self._open_extracted)
        v.addWidget(b)
        b = QPushButton("선택한 GLB 를 타깃 모델로 열기")
        b.setToolTip("게임에서 뽑은 메시(GLB)를 타깃(내 모델) 자리에 불러옵니다. 소스 모션과 같은 캐릭터면 그대로 확인할 수 있습니다.")
        b.clicked.connect(self._open_extracted_as_target)
        v.addWidget(b)
        lay.addWidget(g)
        lay.addStretch(1)
        return sc

    # ---- 추출 탭 동작
    KIND_FILTERS = {"anim": "(?i)anim", "mesh_glb": r"(?i)/sk[a-z]*_", "mesh_psk": r"(?i)/sk[a-z]*_"}

    def _kind_changed(self, *_a) -> None:
        key = self.ex_kind.currentData() or "anim"
        cur = self.ex_filter.text().strip()
        if cur in self.KIND_FILTERS.values() or not cur:
            self.ex_filter.setText(self.KIND_FILTERS[key])
        self.ex_tex.setEnabled(key == "mesh_glb")
        self.btn_extract.setText("선택한 애니메이션 추출 (PSA)" if key == "anim" else "선택한 메시 추출 (%s)" % ("GLB" if key == "mesh_glb" else "PSK"))

    def _pick_dir_into(self, edit) -> None:
        d = QFileDialog.getExistingDirectory(self, "폴더 선택", edit.text())
        if d:
            edit.setText(d)

    def _pick_game(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "게임 설치 폴더", self.ex_game.text())
        if d:
            self.ex_game.setText(d)
            self._scan_game()

    def _pick_usmap(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "mappings(.usmap) 선택", self.cfg.get("last_dir", ""), "usmap (*.usmap);;모든 파일 (*)")
        if path:
            self.ex_usmap.setText(path)

    def _pick_helper(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "mbextract 실행 파일 선택", "", "모든 파일 (*)")
        if path:
            self.cfg["helper"] = path
            bridge.save_config(self.cfg)
            self._update_helper_label()

    def _update_helper_label(self) -> None:
        p = extractor.find_helper(self.cfg.get("helper"))
        if p:
            self.ex_helper_label.setText(p)
            self.ex_helper_label.setStyleSheet("")
        else:
            self.ex_helper_label.setText("찾지 못했습니다. tools/mbextract 를 빌드하거나 경로를 지정하세요 (README 참고).")
            self.ex_helper_label.setStyleSheet("color:#e0a050")

    def _scan_game(self) -> None:
        path = self.ex_game.text().strip()
        if not path:
            QMessageBox.warning(self, "게임 검사", "게임 설치 폴더를 먼저 고르세요.")
            return
        self.run_async(lambda w: gamescan.scan_game(path), self._after_scan, "게임 폴더 검사 중")

    def _after_scan(self, sc) -> None:
        self.scan = sc
        self.ex_summary.setText(sc.summary())
        self.ex_aes.setEnabled(bool(sc.encrypted))
        self.ex_aes.setPlaceholderText("이 게임은 AES 키가 필요합니다 (0x + 64자리 16진수)" if sc.encrypted else "필요 없음 (암호화되지 않은 게임)")
        if sc.found:
            self.cfg["game_dir"] = sc.root
            bridge.save_config(self.cfg)

    def _extract_args(self):
        """추출 호출에 필요한 값을 모은다. 조건이 안 맞으면 안내창을 띄우고 None."""
        sc = self.scan
        if sc is None or not sc.found or sc.root != self.ex_game.text().strip():
            QMessageBox.warning(self, "추출", "게임 폴더를 먼저 검사하세요.")
            return None
        aes = None
        if sc.encrypted:
            try:
                aes = gamescan.normalize_aes_key(self.ex_aes.text())
            except ValueError as e:
                extra = ("\n키 GUID: " + ", ".join(sorted(sc.key_guids))) if sc.key_guids else ""
                QMessageBox.warning(self, "AES 키 필요", "이 게임은 암호화돼 있습니다.\n%s%s" % (e, extra))
                return None
        helper = extractor.find_helper(self.cfg.get("helper"))
        if not helper:
            QMessageBox.warning(self, "추출", "추출 도우미(mbextract)를 찾지 못했습니다. README 의 빌드 방법을 보거나 경로를 지정하세요.")
            return None
        usmap = self.ex_usmap.text().strip() or None
        ue = self.ex_ue.currentData() or "GAME_UE5_4"
        self.cfg.update({"ue": ue, "usmap": usmap or "", "extract_out": self.ex_out.text().strip()})
        bridge.save_config(self.cfg)
        if sc.is_unreal5 and not usmap:
            self.statusBar().showMessage("참고: 언리얼 5 게임은 보통 mappings(.usmap) 가 필요합니다.")
        return helper, sc.root, ue, aes, usmap

    def _search_anims(self) -> None:
        a = self._extract_args()
        if a is None:
            return
        pattern = self.ex_filter.text().strip() or self.KIND_FILTERS[self.ex_kind.currentData() or "anim"]

        self._ex_logs = []

        def on_event(e, w=None):
            if e.get("type") == "log":
                self._ex_logs.append(e.get("message", ""))

        def job(w: Worker):
            return extractor.list_assets(*a, pattern, 5000, on_event)

        self.run_async(job, self._after_search, "에셋 검색 중")

    def _after_search(self, paths) -> None:
        self.ex_list.clear()
        for p in paths:
            self.ex_list.addItem(QListWidgetItem(p))
        logs = getattr(self, "_ex_logs", [])
        self.ex_count.setText("%d개" % len(paths) + ("" if paths else " — UE 버전, mappings(.usmap), 검색식을 확인해 보세요.\n도우미 로그: " + " / ".join(logs[-4:])))

    def _extract_selected(self) -> None:
        paths = [it.text() for it in self.ex_list.selectedItems()]
        if not paths:
            QMessageBox.warning(self, "추출", "추출할 항목을 목록에서 고르세요.")
            return
        a = self._extract_args()
        if a is None:
            return
        out = self.ex_out.text().strip()
        if not out:
            return

        def job(w: Worker):
            n = [0]

            def ev(e):
                if e.get("type") in ("exported", "skip"):
                    n[0] += 1
                    w.progress.emit(n[0] - 1, len(paths), os.path.basename(e.get("path", "")))
            return extractor.export_assets(*a, paths, out, ev, kind=kind, mesh_format=fmt, textures=tex)

        key = self.ex_kind.currentData() or "anim"
        kind = "anim" if key == "anim" else "mesh"
        fmt = "psk" if key == "mesh_psk" else "glb"
        tex = bool(self.ex_tex.isChecked()) and key == "mesh_glb"
        self.run_async(job, self._after_extract, "추출 중")

    def _after_extract(self, files) -> None:
        self.ex_files.clear()
        for f in files:
            self.ex_files.addItem(QListWidgetItem(f))
        self.statusBar().showMessage("추출 완료: %d개" % len(files))
        if len(files) == 1:
            if files[0].lower().endswith(".psa"):
                self._open_path(files[0])
            elif files[0].lower().endswith(".glb"):
                self._open_path_as_target(files[0])

    def _open_extracted(self) -> None:
        sel = self.ex_files.selectedItems()
        if not sel and self.ex_files.count():
            sel = [self.ex_files.item(0)]
        if sel:
            self._open_path(sel[0].text())

    def _open_extracted_as_target(self) -> None:
        sel = [it.text() for it in self.ex_files.selectedItems() if it.text().lower().endswith(".glb")]
        if not sel:
            sel = [self.ex_files.item(i).text() for i in range(self.ex_files.count()) if self.ex_files.item(i).text().lower().endswith(".glb")][:1]
        if not sel:
            QMessageBox.warning(self, "열기", "열 수 있는 GLB 가 없습니다. 종류를 '메시 (GLB)'로 추출하세요.")
            return
        self._open_path_as_target(sel[0])

    def _open_path_as_target(self, path: str) -> None:
        self._remember_dir(path)
        self.run_async(lambda w: self.pr.load_target(path), lambda m: (self._after_load("target"), self.tabs.setCurrentIndex(1)), "타깃 읽는 중")

    def _open_path(self, path: str) -> None:
        self._remember_dir(path)
        self.run_async(lambda w: self.pr.load_source(path), lambda m: (self._after_load("source"), self.tabs.setCurrentIndex(1)), "소스 읽는 중")

    # ------------------------------------------------------------------ 탭 1
    def _tab_files(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        b1 = QPushButton("소스 모션 열기…")
        b1.setToolTip("게임에서 뽑은 모션이 들어 있는 파일 (FBX / GLB / BVH / .blend)")
        b1.clicked.connect(self.open_source)
        b2 = QPushButton("타깃 모델 열기…")
        b2.setToolTip("모션을 입힐 내 모델 (스켈레톤이 있는 FBX / GLB / .blend)")
        b2.clicked.connect(self.open_target)
        row.addWidget(b1)
        row.addWidget(b2)
        lay.addLayout(row)
        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        self.info_label.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.info_label)

        crow = QHBoxLayout()
        crow.addWidget(QLabel("클립:"))
        self.clip_combo = QComboBox()
        self.clip_combo.currentIndexChanged.connect(self._clip_changed)
        crow.addWidget(self.clip_combo, 1)
        lay.addLayout(crow)

        mrow = QHBoxLayout()
        b = QPushButton("자동 매핑 다시 하기")
        b.clicked.connect(self._automap)
        mrow.addWidget(b)
        self.cb_fingers = QCheckBox("손가락 보기")
        self.cb_fingers.toggled.connect(lambda v: self._fill_mapping())
        mrow.addWidget(self.cb_fingers)
        mrow.addStretch(1)
        lay.addLayout(mrow)
        self.map_table = QTableWidget(0, 3)
        self.map_table.setHorizontalHeaderLabels(["부위", "소스 본", "타깃 본"])
        hh = self.map_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.map_table, 1)
        tip = QLabel("⚠ 표시: 한쪽에만 본이 연결된 부위. 연결되지 않은 본은 바인드 포즈를 유지합니다.")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        prow = QHBoxLayout()
        b = QPushButton("프로필 저장…")
        b.setToolTip("매핑과 리타깃 설정을 이름 기준으로 저장 — 같은 리그에 다시 쓸 수 있습니다")
        b.clicked.connect(self.save_profile)
        prow.addWidget(b)
        b = QPushButton("프로필 불러오기…")
        b.clicked.connect(self.load_profile)
        prow.addWidget(b)
        lay.addLayout(prow)
        return w

    # ------------------------------------------------------------------ 탭 2
    def _chk(self, key: str, text: str, tip: str = "", invert: bool = False) -> QCheckBox:
        cb = QCheckBox(text)
        if tip:
            cb.setToolTip(tip)
        cb.setChecked(bool(getattr(self.pr.settings, key)) != invert)
        cb.toggled.connect(lambda v, k=key, inv=invert: self._set(k, (not v) if inv else bool(v)))
        self._fields[key] = (cb, invert)
        return cb

    def _num(self, key: str, lo, hi, step, decimals=2, suffix="", slider=True, tip="") -> NumField:
        nf = NumField(lo, hi, step, getattr(self.pr.settings, key), decimals, suffix, slider)
        if tip:
            nf.setToolTip(tip)
        nf.changed.connect(lambda v, k=key: self._set(k, v))
        self._fields[key] = (nf, False)
        return nf

    def _tab_retarget(self) -> QWidget:
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        w = QWidget()
        sc.setWidget(w)
        lay = QVBoxLayout(w)
        self.warn_label = QLabel("")
        self.warn_label.setWordWrap(True)
        self.warn_label.setStyleSheet("color:#e0a050")
        lay.addWidget(self.warn_label)

        g = QGroupBox("크기 · 위치 · 방향")
        f = QFormLayout(g)
        f.addRow(self._chk("auto_scale", "자동 스케일 (다리 길이 기준)", "소스 대비 타깃의 체형 비율로 이동량을 맞춥니다"))
        f.addRow("스케일 배율", self._num("scale_mult", 0.3, 2.5, 0.01, 2, tip="자동 스케일에 곱하는 값. 보폭/이동 거리를 키우거나 줄입니다"))
        self.manual_scale = self._num("manual_scale", 0.0001, 1000.0, 0.0001, 4, slider=False, tip="자동 스케일을 끄면 이 값을 씁니다")
        f.addRow("수동 스케일", self.manual_scale)
        self.hip_fields = []
        for i, lab in enumerate(("골반 좌우(X)", "골반 높이(Y)", "골반 앞뒤(Z)")):
            nf = NumField(-100, 100, 0.1, 0.0, 2, slider=True)
            nf.changed.connect(lambda v, ax=i: self._hip_changed(ax, v))
            self.hip_fields.append(nf)
            f.addRow(lab, nf)
        f.addRow("루트 회전(Yaw °)", self._num("root_yaw_deg", -180, 180, 1, 0, " °"))
        f.addRow(self._chk("transfer_root_motion", "제자리(인플레이스) — 수평 이동 제거", "체크하면 캐릭터가 앞으로 나아가지 않고 제자리에서 움직입니다", invert=True))
        self.com_combo = QComboBox()
        self.com_combo.currentIndexChanged.connect(self._com_changed)
        f.addRow("COM(위치 기준) 본", self.com_combo)
        lay.addWidget(g)

        g = QGroupBox("발 · 접지 (IK)")
        f = QFormLayout(g)
        f.addRow(self._chk("foot_ik", "발 IK 사용", "접지 구간에서 발이 미끄러지지 않게 다리를 풉니다"))
        f.addRow("IK 추종(0~1)", self._num("ik_follow", 0, 1, 0.05, 2, tip="1: 항상 소스 발 위치(스케일 적용)를 따라감 / 0: 접지 구간에서만 IK"))
        f.addRow(self._chk("ground_clamp", "바닥 아래로 못 내려가게"))
        f.addRow("바닥 높이 보정", self._num("ground_offset", -50, 50, 0.1, 2, tip="타깃 단위. 발목 기준 바닥 높이를 올리거나 내림"))
        self.ground_field = self._fields["ground_offset"][0]
        f.addRow(self._chk("hip_drop", "다리가 모자라면 골반 낮추기"))
        f.addRow("골반 최대 하강(다리 길이 비)", self._num("max_hip_drop", 0, 0.4, 0.01, 2))
        f.addRow("접지 높이 임계(다리 길이 비)", self._num("contact_height", 0.005, 0.3, 0.005, 3))
        f.addRow("접지 속도 임계(다리 길이/초)", self._num("contact_speed", 0.05, 3.0, 0.05, 2))
        bf = QSpinBox()
        bf.setRange(0, 16)
        bf.setValue(int(self.pr.settings.blend_frames))
        bf.valueChanged.connect(lambda v: self._set("blend_frames", int(v)))
        self._fields["blend_frames"] = (bf, False)
        f.addRow("접지 전환 프레임", bf)
        lay.addWidget(g)

        g = QGroupBox("좌표축 (자동 인식이 틀릴 때만)")
        f = QFormLayout(g)
        for key, lab in (("src_up", "소스 상방"), ("src_fwd", "소스 전방"), ("tgt_up", "타깃 상방"), ("tgt_fwd", "타깃 전방")):
            cb = QComboBox()
            for a in AXES:
                cb.addItem(AXIS_LABEL.get(a, a), a)
            cb.currentIndexChanged.connect(lambda i, k=key, c=cb: self._set(k, c.itemData(i)))
            self._fields[key] = (cb, False)
            f.addRow(lab, cb)
        lay.addWidget(g)

        g = QGroupBox("본별 회전 보정 (하위 본 전체에 적용)")
        v = QVBoxLayout(g)
        self.rot_combo = QComboBox()
        self.rot_combo.currentIndexChanged.connect(self._rot_joint_changed)
        v.addWidget(self.rot_combo)
        f = QFormLayout()
        self.rot_fields = []
        for i, lab in enumerate(("X (좌우축)", "Y (위축)", "Z (앞축)")):
            nf = NumField(-180, 180, 1, 0, 0, " °")
            nf.changed.connect(lambda val, ax=i: self._rot_changed(ax, val))
            self.rot_fields.append(nf)
            f.addRow(lab, nf)
        v.addLayout(f)
        b = QPushButton("회전 보정 모두 초기화")
        b.clicked.connect(self._rot_reset)
        v.addWidget(b)
        lay.addWidget(g)

        b = QPushButton("모든 설정을 기본값으로")
        b.clicked.connect(self._reset_settings)
        lay.addWidget(b)
        lay.addStretch(1)
        return sc

    # ------------------------------------------------------------------ 탭 3
    def _tab_quality(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.quality_summary = QLabel("리타깃 결과가 있으면 문제 구간을 자동으로 찾아 줍니다.")
        self.quality_summary.setWordWrap(True)
        lay.addWidget(self.quality_summary)
        self.issue_list = QListWidget()
        self.issue_list.itemClicked.connect(self._issue_clicked)
        lay.addWidget(self.issue_list, 1)
        lay.addWidget(QLabel("항목을 클릭하면 해당 프레임으로 이동합니다. 최종 판단은 사람이 눈으로 확인하세요."))
        return w

    # ------------------------------------------------------------------ 탭 4
    def _tab_export(self) -> QWidget:
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        w = QWidget()
        sc.setWidget(w)
        lay = QVBoxLayout(w)

        g = QGroupBox("출력 폴더")
        h = QHBoxLayout(g)
        self.out_edit = QLineEdit(self.cfg.get("out_dir") or os.path.join(os.path.expanduser("~"), "MotionBridge_out"))
        h.addWidget(self.out_edit, 1)
        b = QPushButton("찾아보기…")
        b.clicked.connect(self._pick_out)
        h.addWidget(b)
        lay.addWidget(g)

        g = QGroupBox("무엇을 내보낼까")
        v = QVBoxLayout(g)
        self.rb_retarget = QRadioButton("리타깃 결과 (내 모델용)")
        self.rb_source = QRadioButton("원본 모션 그대로 (리타깃 없음 — 크리처/구조물용)")
        self.rb_retarget.setChecked(True)
        v.addWidget(self.rb_retarget)
        v.addWidget(self.rb_source)
        self.cb_skeleton = QCheckBox("본 구조(스켈레톤) 포함")
        self.cb_skeleton.setChecked(True)
        self.cb_skeleton.setToolTip("끄면 본 구조 없이 애니메이션 커브(JSON)만 저장합니다 — 이미 같은 이름의 본이 세팅된 리그에 쓸 때")
        v.addWidget(self.cb_skeleton)
        self.cb_mesh = QCheckBox("메시도 함께 (GLB)")
        self.cb_mesh.setChecked(True)
        v.addWidget(self.cb_mesh)
        self.cb_skel_json = QCheckBox("본 구조 JSON 도 따로 저장")
        v.addWidget(self.cb_skel_json)
        lay.addWidget(g)

        g = QGroupBox("대상 프로그램")
        h = QHBoxLayout(g)
        self.cb_preset = {}
        for key, p in PRESETS.items():
            cb = QCheckBox(p.label)
            cb.setToolTip(p.note)
            cb.setChecked(key == "blender")
            self.cb_preset[key] = cb
            h.addWidget(cb)
        lay.addWidget(g)

        g = QGroupBox("파일 형식")
        h = QHBoxLayout(g)
        self.cb_fmt = {}
        for key, text, tip in (("bvh", "BVH", "Max/Maya/Blender 모두 읽는 모션 파일 (대상 프로그램별 축·단위 적용)"),
                               ("glb", "GLB (glTF)", "메시·스킨·애니메이션 포함 가능"),
                               ("fbx", "FBX (Blender 필요)", "내 모델 파일을 Blender 로 열어 모션을 입힌 뒤 FBX 로 저장 — 리타깃 결과 전용")):
            cb = QCheckBox(text)
            cb.setToolTip(tip)
            cb.setChecked(key == "bvh")
            self.cb_fmt[key] = cb
            h.addWidget(cb)
        lay.addWidget(g)
        self.blender_label = QLabel("")
        self.blender_label.setWordWrap(True)
        lay.addWidget(self.blender_label)
        b = QPushButton("Blender 경로 지정…")
        b.clicked.connect(self.pick_blender)
        lay.addWidget(b)
        self._update_blender_label()

        g = QGroupBox("클립")
        v = QVBoxLayout(g)
        self.rb_clip_cur = QRadioButton("현재 클립만")
        self.rb_clip_all = QRadioButton("소스의 모든 클립")
        self.rb_clip_cur.setChecked(True)
        v.addWidget(self.rb_clip_cur)
        v.addWidget(self.rb_clip_all)
        lay.addWidget(g)

        self.btn_export = QPushButton("내보내기")
        self.btn_export.setMinimumHeight(38)
        self.btn_export.clicked.connect(self.do_export)
        lay.addWidget(self.btn_export)
        self.export_log = QPlainTextEdit()
        self.export_log.setReadOnly(True)
        self.export_log.setMinimumHeight(140)
        lay.addWidget(self.export_log)
        b = QPushButton("출력 폴더 열기")
        b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.out_edit.text())))
        lay.addWidget(b)
        lay.addStretch(1)
        return sc

    def _tab_help(self) -> QWidget:
        tb = QTextBrowser()
        tb.setOpenExternalLinks(True)
        tb.setHtml(EXTRACT_HELP)
        return tb

    # ================================================================== 비동기 작업
    def run_async(self, fn, on_done, label: str) -> None:
        if self._worker is not None:
            return
        self._set_busy(True, label)
        self._on_done, self._busy_label = on_done, label
        wk = Worker(fn, self)
        self._worker = wk
        # 바인딩된 메서드로 연결해야 콜백이 GUI 스레드에서 실행된다
        wk.finished_ok.connect(self._worker_done)
        wk.failed.connect(self._worker_failed)
        wk.progress.connect(self._worker_progress)
        wk.start()

    def _worker_done(self, res) -> None:
        cb = self._on_done
        self._worker = None
        self._set_busy(False)
        cb(res)

    def _worker_failed(self, msg: str) -> None:
        self._worker = None
        self._set_busy(False)
        QMessageBox.critical(self, "오류", msg.split("\n\n")[0])
        self.export_log.appendPlainText(msg)

    def _worker_progress(self, i: int, n: int, name: str) -> None:
        self.statusBar().showMessage("%s (%d/%d) %s" % (self._busy_label, i + 1, n, name))

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self.tabs.setEnabled(not busy)
        self.right_box.setEnabled(not busy)
        self.viewport.paused = busy
        self.viewport.update()
        self.status_progress.setVisible(busy)
        if busy:
            self.status_progress.setRange(0, 0)
            self.statusBar().showMessage(label + " …")
        else:
            self.statusBar().showMessage("준비됨")

    # ================================================================== 열기 / 저장
    def _remember_dir(self, path: str) -> None:
        self.cfg["last_dir"] = os.path.dirname(path)
        bridge.save_config(self.cfg)

    def open_source(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "소스 모션 열기", self.cfg.get("last_dir", ""), FILE_FILTER)
        if path:
            self._remember_dir(path)
            self.run_async(lambda w: self.pr.load_source(path), lambda m: self._after_load("source"), "소스 읽는 중")

    def open_target(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "타깃 모델 열기", self.cfg.get("last_dir", ""), FILE_FILTER)
        if path:
            self._remember_dir(path)
            self.run_async(lambda w: self.pr.load_target(path), lambda m: self._after_load("target"), "타깃 읽는 중")

    def _after_load(self, which: str) -> None:
        self._syncing = True
        if which == "source":
            self.clip_combo.clear()
            for i, a in enumerate(self.pr.src.animations):
                self.clip_combo.addItem("%d: %s (%d프레임, %.0ffps)" % (i, a.name, a.n_frames, a.fps))
        else:
            self._fill_joint_combos()
            self._sync_settings_ui()
            self._update_hip_ranges()
        self._syncing = False
        self._fill_mapping()
        self.recompute(reframe=True)

    def save_profile(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "프로필 저장", os.path.join(self.cfg.get("last_dir", ""), "profile.json"), "JSON (*.json)")
        if path:
            self.pr.save_profile(path)
            self.statusBar().showMessage("프로필 저장: " + path)

    def load_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "프로필 불러오기", self.cfg.get("last_dir", ""), "JSON (*.json)")
        if not path:
            return
        try:
            warns = self.pr.load_profile(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "프로필 오류", str(e))
            return
        self._syncing = True
        self._sync_settings_ui()
        self._syncing = False
        self._fill_mapping()
        self.recompute()
        if warns:
            QMessageBox.information(self, "프로필 적용됨", "일부 항목을 적용하지 못했습니다:\n" + "\n".join(warns[:15]))

    def pick_blender(self) -> None:
        flt = "Blender (blender.exe blender Blender);;모든 파일 (*)" if os.name == "nt" else "모든 파일 (*)"
        path, _ = QFileDialog.getOpenFileName(self, "Blender 실행 파일 선택", "", flt)
        if path:
            self.cfg["blender"] = path
            self.pr.blender = path
            bridge.save_config(self.cfg)
            self._update_blender_label()

    def _update_blender_label(self) -> None:
        p = bridge.find_blender(self.pr.blender)
        if p:
            self.blender_label.setText("Blender: " + p)
            self.blender_label.setStyleSheet("")
        else:
            self.blender_label.setText("Blender 를 찾지 못했습니다. FBX/.blend 읽기와 FBX 내보내기에만 필요합니다 (BVH/GLB 는 그대로 동작).")
            self.blender_label.setStyleSheet("color:#e0a050")

    # ================================================================== 매핑 표
    def _fill_mapping(self) -> None:
        self._syncing = True
        show_f = self.cb_fingers.isChecked()
        slots = [s for s in SLOT_ORDER if show_f or not _is_finger(s)]
        self.map_table.setRowCount(len(slots))
        for r, s in enumerate(slots):
            self.map_table.setItem(r, 0, QTableWidgetItem(slot_label(s)))
            self.map_table.item(r, 0).setData(Qt.ItemDataRole.UserRole, s)
            for col, side in ((1, "src"), (2, "tgt")):
                model = self.pr.src if side == "src" else self.pr.tgt
                cb = QComboBox()
                cb.setMaxVisibleItems(24)
                cb.addItem("(없음)")
                if model is not None:
                    cb.addItems(list(model.skeleton.names))
                    cb.setCurrentIndex(self._slots(side).get(s, -1) + 1)
                else:
                    cb.setEnabled(False)
                cb.currentIndexChanged.connect(lambda i, sd=side, sl=s: self._map_changed(sd, sl, i))
                self.map_table.setCellWidget(r, col, cb)
        self._syncing = False
        self._flag_rows()

    def _slots(self, side: str) -> Dict[str, int]:
        return self.pr.src_slots if side == "src" else self.pr.tgt_slots

    def _map_changed(self, side: str, slot: str, idx: int) -> None:
        if self._syncing:
            return
        d = self._slots(side)
        if idx <= 0:
            d.pop(slot, None)
        else:
            d[slot] = idx - 1
        self._flag_rows()
        self._fill_joint_combos()
        self.schedule()

    def _flag_rows(self) -> None:
        for r in range(self.map_table.rowCount()):
            it = self.map_table.item(r, 0)
            s = it.data(Qt.ItemDataRole.UserRole)
            both = (s in self.pr.src_slots) == (s in self.pr.tgt_slots)
            it.setText(("" if both else "⚠ ") + slot_label(s))
            it.setForeground(QColor(255, 255, 255) if both else QColor(240, 170, 60))

    def _automap(self) -> None:
        self.pr.automap()
        self._fill_mapping()
        self._fill_joint_combos()
        self.schedule()

    # ================================================================== 설정 동기화
    def _set(self, key: str, value) -> None:
        if self._syncing:
            return
        setattr(self.pr.settings, key, value)
        self.schedule()

    def _hip_changed(self, axis: int, value: float) -> None:
        if self._syncing:
            return
        h = list(self.pr.settings.hip_offset)
        h[axis] = value
        self.pr.settings.hip_offset = tuple(h)
        self.schedule()

    def _update_hip_ranges(self) -> None:
        if self.pr.tgt is None:
            return
        _, bwp = self.pr.tgt.skeleton.bind_world()
        size = float(max(bwp.max(axis=0) - bwp.min(axis=0)))
        step = max(size / 400.0, 1e-4)
        for nf in self.hip_fields:
            nf.set_range(-size * 0.5, size * 0.5, step)
        self.ground_field.set_range(-size * 0.2, size * 0.2, step)

    def _sync_settings_ui(self) -> None:
        """self.pr.settings 값을 위젯에 반영 (시그널 방출 없이)."""
        prev = self._syncing
        self._syncing = True
        st = self.pr.settings
        for key, (wd, inv) in self._fields.items():
            v = getattr(st, key)
            if isinstance(wd, QCheckBox):
                wd.setChecked(bool(v) != inv)
            elif isinstance(wd, NumField):
                wd.set_value(v)
            elif isinstance(wd, QSpinBox):
                wd.setValue(int(v))
            elif isinstance(wd, QComboBox):
                i = wd.findData(v)
                wd.setCurrentIndex(max(i, 0))
        for i, nf in enumerate(self.hip_fields):
            nf.set_value(st.hip_offset[i])
        self._fill_joint_combos()
        self._syncing = prev

    def _reset_settings(self) -> None:
        from ..core.retarget import RetargetSettings

        self.pr.settings = RetargetSettings()
        self._sync_settings_ui()
        self.schedule()

    # ---- COM / 회전 보정
    def _mapped_tgt_joints(self):
        out = []
        for s in SLOT_ORDER:
            if s in self.pr.tgt_slots and not _is_finger(s):
                j = self.pr.tgt_slots[s]
                out.append((s, j))
        return out

    def _fill_joint_combos(self) -> None:
        prev = self._syncing
        self._syncing = True
        if self.pr.tgt is None:
            self._syncing = prev
            return
        names = self.pr.tgt.skeleton.names
        joints = self._mapped_tgt_joints()
        cur_rot = self.rot_combo.currentData()
        self.rot_combo.clear()
        for s, j in joints:
            mark = " ●" if j in self.pr.settings.rot_offsets else ""
            self.rot_combo.addItem("%s (%s)%s" % (slot_label(s), names[j], mark), j)
        if cur_rot is not None:
            i = self.rot_combo.findData(cur_rot)
            if i >= 0:
                self.rot_combo.setCurrentIndex(i)
        self.com_combo.clear()
        self.com_combo.addItem("(자동)", -1)
        for s, j in joints:
            self.com_combo.addItem("%s (%s)" % (slot_label(s), names[j]), j)
        i = self.com_combo.findData(self.pr.settings.com_joint)
        self.com_combo.setCurrentIndex(max(i, 0))
        self._syncing = prev
        self._rot_joint_changed()

    def _com_changed(self, i: int) -> None:
        if self._syncing:
            return
        self.pr.settings.com_joint = int(self.com_combo.itemData(i) if self.com_combo.itemData(i) is not None else -1)
        self.schedule()

    def _rot_joint_changed(self, *_a) -> None:
        j = self.rot_combo.currentData()
        prev = self._syncing
        self._syncing = True
        v = self.pr.settings.rot_offsets.get(j, (0.0, 0.0, 0.0)) if j is not None else (0.0, 0.0, 0.0)
        for nf, x in zip(self.rot_fields, v):
            nf.set_value(x)
        self._syncing = prev

    def _rot_changed(self, axis: int, value: float) -> None:
        if self._syncing:
            return
        j = self.rot_combo.currentData()
        if j is None:
            return
        v = list(self.pr.settings.rot_offsets.get(j, (0.0, 0.0, 0.0)))
        v[axis] = value
        if any(abs(x) > 1e-9 for x in v):
            self.pr.settings.rot_offsets[j] = tuple(v)
        else:
            self.pr.settings.rot_offsets.pop(j, None)
        self.schedule()

    def _rot_reset(self) -> None:
        self.pr.settings.rot_offsets = {}
        self._fill_joint_combos()
        self.schedule()

    # ================================================================== 재계산 / 표시
    def schedule(self) -> None:
        if not self._syncing:
            self.recompute_timer.start()

    def recompute(self, reframe: bool = False) -> None:
        err = None
        if self.pr.ready():
            try:
                self.pr.run()
            except Exception as e:  # noqa: BLE001
                self.pr.result, self.pr.issues, err = None, [], str(e)
        else:
            self.pr.result, self.pr.issues = None, []
        self.vd = ViewData.build(self.pr) if (self.pr.src or self.pr.tgt) else None
        n = self.vd.n_frames if self.vd else 1
        self.frame_slider.blockSignals(True)
        self.frame_slider.setRange(0, max(n - 1, 0))
        if self.frame_slider.value() > n - 1:
            self.frame_slider.setValue(0)
        self.frame_slider.blockSignals(False)
        self.viewport.set_data(self.pr, self.vd, reframe=reframe)
        self._frame_changed(self.frame_slider.value())
        self._update_info(err)
        self._update_quality()

    def _update_info(self, err: Optional[str]) -> None:
        lines = []
        if self.pr.src:
            a = self.pr.anim
            lines.append("<b>소스</b>: %s — 본 %d, 클립 %d개 (현재 %d프레임)" % (os.path.basename(self.pr.src.path), self.pr.src.skeleton.n, len(self.pr.src.animations), a.n_frames))
        if self.pr.tgt:
            lines.append("<b>타깃</b>: %s — 본 %d%s" % (os.path.basename(self.pr.tgt.path), self.pr.tgt.skeleton.n, (", 메시 %d개" % len(self.pr.tgt.meshes)) if self.pr.tgt.meshes else ", 메시 없음"))
        res = self.pr.result
        warns: List[str] = []
        if res is not None:
            lines.append("매핑 %d쌍 · 스케일 k=%.3f · 소스 %s · 타깃 %s · IK %s" % (len(res.pairs), res.k, res.frame_src[1], res.frame_tgt[1], "켬" if res.ik_applied else "끔"))
            warns = list(res.warnings)
        elif self.pr.src and self.pr.tgt and not self.pr.pairs():
            lines.append("<span style='color:#e0a050'>자동 매핑된 본이 없습니다. 아래 표에서 직접 연결하세요.</span>")
        if err:
            warns.append("리타깃 실패: " + err)
        self.info_label.setText("<br>".join(lines))
        self.warn_label.setText("\n".join("⚠ " + w for w in warns))

    def _update_quality(self) -> None:
        self.issue_list.clear()
        if self.pr.result is None:
            self.quality_summary.setText("리타깃 결과가 있으면 문제 구간을 자동으로 찾아 줍니다.")
            return
        issues = self.pr.issues
        counts = {"error": 0, "warn": 0, "info": 0}
        for i in issues:
            counts[i.severity] = counts.get(i.severity, 0) + 1
        self.quality_summary.setText("오류 %d · 경고 %d · 참고 %d" % (counts["error"], counts["warn"], counts["info"]))
        for i in issues:
            it = QListWidgetItem("[%s] 프레임 %d–%d  %s" % (i.kind, i.start, i.end, i.message))
            it.setData(Qt.ItemDataRole.UserRole, i.start)
            it.setForeground(QColor({"error": "#ff6060", "warn": "#e0a050"}.get(i.severity, "#9aa4b8")))
            self.issue_list.addItem(it)

    def _issue_clicked(self, item) -> None:
        self.frame_slider.setValue(int(item.data(Qt.ItemDataRole.UserRole)))

    def _clip_changed(self, i: int) -> None:
        if self._syncing or i < 0 or self.pr.src is None:
            return
        self.pr.clip = i
        self.recompute(reframe=False)

    # ---- 타임라인
    def _frame_changed(self, f: int) -> None:
        n = self.vd.n_frames if self.vd else 1
        fps = self.vd.fps if self.vd else 30.0
        self.viewport.set_frame(f)
        self.frame_label.setText("%d / %d  (%.2fs)" % (f + 1, n, f / fps if fps else 0.0))

    def _toggle_play(self, on: bool) -> None:
        self.btn_play.setText("⏸ 정지" if on else "▶ 재생")
        if on:
            fps = self.vd.fps if self.vd else 30.0
            self.play_timer.start(max(5, int(1000.0 / max(fps, 1.0))))
        else:
            self.play_timer.stop()

    def _tick(self) -> None:
        n = self.vd.n_frames if self.vd else 1
        self.frame_slider.setValue((self.frame_slider.value() + 1) % max(n, 1))

    def _reframe(self) -> None:
        if self.vd is not None:
            self.viewport.cam.frame(self.vd.center, self.vd.height)
            self.viewport.update()

    # ================================================================== 내보내기
    def _pick_out(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "출력 폴더", self.out_edit.text())
        if d:
            self.out_edit.setText(d)

    def do_export(self) -> None:
        if self.pr.src is None:
            QMessageBox.warning(self, "내보내기", "먼저 소스 모션을 여세요.")
            return
        what = "retargeted" if self.rb_retarget.isChecked() else "source"
        if what == "retargeted" and not self.pr.ready():
            QMessageBox.warning(self, "내보내기", "리타깃하려면 타깃 모델과 매핑이 필요합니다.")
            return
        presets = [k for k, cb in self.cb_preset.items() if cb.isChecked()]
        fmts = [k for k, cb in self.cb_fmt.items() if cb.isChecked()]
        skel = self.cb_skeleton.isChecked()
        if skel and (not presets or not fmts):
            QMessageBox.warning(self, "내보내기", "대상 프로그램과 파일 형식을 하나 이상 고르세요.")
            return
        if "fbx" in fmts and skel and not bridge.find_blender(self.pr.blender):
            QMessageBox.warning(self, "내보내기", "FBX 를 쓰려면 Blender 가 필요합니다. 'Blender 경로 지정…' 을 눌러 주세요.")
            return
        out = self.out_edit.text().strip()
        if not out:
            return
        self.cfg["out_dir"] = out
        bridge.save_config(self.cfg)
        n_clips = len(self.pr.src.animations)
        clips = list(range(n_clips)) if self.rb_clip_all.isChecked() else [self.pr.clip]
        opt = ExportOptions(
            out_dir=out, presets=presets or ["blender"], formats=fmts or ["bvh"], include_skeleton=skel, what=what, clips=clips,
            include_mesh=self.cb_mesh.isChecked(), write_skeleton_json=self.cb_skel_json.isChecked(), blender=self.pr.blender,
        )
        clip0 = self.pr.clip
        self.export_log.clear()

        def job(w: Worker):
            return self.pr.export(opt, progress=lambda i, n, nm: w.progress.emit(i, n, nm))

        def done(res):
            files, warns = res
            self.pr.clip = clip0
            self.export_log.appendPlainText("완료: 파일 %d개" % len(files))
            for f in files:
                self.export_log.appendPlainText("  " + f)
            for wmsg in warns:
                self.export_log.appendPlainText("경고: " + wmsg)
            self.recompute()
            self.statusBar().showMessage("내보내기 완료: %d개" % len(files))

        self.run_async(job, done, "내보내는 중")


def _dark_palette(app: QApplication) -> None:
    app.setStyle("Fusion")
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(38, 41, 48))
    p.setColor(QPalette.ColorRole.WindowText, QColor(225, 228, 235))
    p.setColor(QPalette.ColorRole.Base, QColor(28, 31, 38))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(44, 48, 56))
    p.setColor(QPalette.ColorRole.Text, QColor(225, 228, 235))
    p.setColor(QPalette.ColorRole.Button, QColor(52, 56, 66))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(225, 228, 235))
    p.setColor(QPalette.ColorRole.Highlight, QColor(60, 130, 200))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(52, 56, 66))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(225, 228, 235))
    app.setPalette(p)


def run_gui() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("MotionBridge")
    _dark_palette(app)
    w = MainWindow()
    w.resize(1380, 880)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(run_gui())
