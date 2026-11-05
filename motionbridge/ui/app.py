"""MotionBridge 메인 창 (PySide6)."""
from __future__ import annotations

import copy
import os
import sys
from typing import Dict, List, Optional

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QAction, QColor, QDesktopServices, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton,
    QScrollArea, QSlider, QSplitter, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout,
    QWidget, QLineEdit, QButtonGroup, QAbstractItemView,
)

from .. import __version__
from ..core import bridge, extractor, gamescan, meshconv, osopen
from ..core.export import PRESETS
from ..core.history import History
from ..core.loader import FILE_FILTER
from ..core.mapping import FINGERS, SLOT_ORDER, slot_label
from ..core.pipeline import ExportOptions, Project
from .help_text import EXTRACT_HELP
from .viewer import MESH_MODES, Viewport
from .viewer_math import ViewData
from .widgets import NumField, PathTree, Worker

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
        self.recompute_timer.timeout.connect(self._timer_fire)
        self.history = History()
        self._actions: Dict[str, tuple] = {}  # 키 -> (QAction, 단축키 목록)
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
    # 단축키 표: (메뉴, 키, 이름, 동작 메서드, 단축키들). 메뉴 항목이 곧 단축키 등록이다.
    def _menu_spec(self):
        mesh_items = [("mesh_" + k, "메시: " + n, "mesh:" + k, ("%d" % i,) if i <= 5 else ()) for i, (k, n) in enumerate(MESH_MODES) if True]
        spec = [
            ("파일", "open_source", "소스 모션 열기…", "open_source", ("Ctrl+O",)),
            ("파일", "open_target", "타깃 모델 열기…", "open_target", ("Ctrl+Shift+O",)),
            ("파일", None, None, None, ()),
            ("파일", "load_profile", "프로필 불러오기…", "load_profile", ("Ctrl+Shift+P",)),
            ("파일", "save_profile", "프로필 저장…", "save_profile", ("Ctrl+S",)),
            ("파일", None, None, None, ()),
            ("파일", "export", "내보내기", "do_export", ("Ctrl+E",)),
            ("파일", "pick_blender", "Blender 경로 지정…", "pick_blender", ()),
            ("파일", None, None, None, ()),
            ("파일", "quit", "종료", "close", ("Ctrl+Q",)),
            ("편집", "undo", "실행 취소", "undo", ("Ctrl+Z",)),
            ("편집", "redo", "다시 실행", "redo", ("Ctrl+Y", "Ctrl+Shift+Z")),
            ("편집", None, None, None, ()),
            ("편집", "automap", "자동 매핑 다시 하기", "_automap", ("Ctrl+M",)),
            ("편집", "reset_settings", "리타깃 설정 모두 기본값으로", "_reset_settings", ("Ctrl+Shift+R",)),
            ("보기", "play", "재생 / 정지", "toggle_play", ("Space",)),
            ("보기", "prev", "이전 프레임", "step_frame:-1", (",",)),
            ("보기", "next", "다음 프레임", "step_frame:1", (".",)),
            ("보기", "back10", "10프레임 뒤로", "step_frame:-10", ("Ctrl+Left",)),
            ("보기", "fwd10", "10프레임 앞으로", "step_frame:10", ("Ctrl+Right",)),
            ("보기", "first", "처음 프레임", "goto_frame:first", ("Ctrl+Home",)),
            ("보기", "last", "마지막 프레임", "goto_frame:last", ("Ctrl+End",)),
            ("보기", None, None, None, ()),
            ("보기", "reframe", "화면 맞추기", "_reframe", ("F",)),
            ("보기", "bones", "뼈대 표시 켬/끔", "toggle_view:bones", ("B",)),
            ("보기", "ghost", "소스 겹쳐 보기 켬/끔", "toggle_view:ghost", ("G",)),
            ("보기", "follow", "카메라 따라가기 켬/끔", "toggle_view:follow", ("T",)),
            ("보기", None, None, None, ()),
        ]
        spec += [("보기", k, n, a, sc) for k, n, a, sc in mesh_items]
        spec += [
            ("보기", "mesh_cycle", "메시 보기 방식 순환", "mesh_cycle", ("M",)),
            ("탭", "tab0", "0. 게임에서 추출", "goto_tab:0", ("Ctrl+1",)),
            ("탭", "tab1", "1. 파일·매핑", "goto_tab:1", ("Ctrl+2",)),
            ("탭", "tab2", "2. 리타깃 조절", "goto_tab:2", ("Ctrl+3",)),
            ("탭", "tab3", "3. 품질 검수", "goto_tab:3", ("Ctrl+4",)),
            ("탭", "tab4", "4. 내보내기", "goto_tab:4", ("Ctrl+5",)),
            ("도움말", "shortcuts", "단축키 목록", "show_shortcuts", ("F1",)),
            ("도움말", "about", "정보", "show_about", ()),
        ]
        return spec

    def _dispatch(self, spec: str) -> None:
        name, _, arg = spec.partition(":")
        fn = getattr(self, name)
        if arg:
            fn(int(arg) if arg.lstrip("-").isdigit() else arg)
        else:
            fn()

    def _build_menu(self) -> None:
        menus: Dict[str, object] = {}
        for menu, key, text, action, shortcuts in self._menu_spec():
            if menu not in menus:
                menus[menu] = self.menuBar().addMenu(menu)
            m = menus[menu]
            if key is None:
                m.addSeparator()
                continue
            qa = QAction(text, self)
            if shortcuts:
                qa.setShortcuts([QKeySequence(x) for x in shortcuts])
            qa.triggered.connect(lambda checked=False, sp=action: self._dispatch(sp))
            m.addAction(qa)
            self._actions[key] = (qa, tuple(shortcuts), action, text)

    def shortcut_table(self) -> List[tuple]:
        """[(이름, 단축키 문자열)] — 단축키 목록 창과 테스트에서 쓴다."""
        return [(v[3], " / ".join(v[1])) for v in self._actions.values() if v[1]]

    def show_shortcuts(self) -> None:
        rows = ["%-26s %s" % (n, k) for n, k in self.shortcut_table()]
        rows += ["", "3D 뷰에 포커스가 있을 때: ← → 프레임 이동, Home/End 처음/끝, Space 재생"]
        QMessageBox.information(self, "단축키", "\n".join(rows))

    def show_about(self) -> None:
        QMessageBox.information(self, "MotionBridge", "MotionBridge %s\n게임 모션 → 내 모델 리타깃 / Max·Maya·Blender 내보내기" % __version__)

    # ---- 단축키 동작
    def toggle_play(self) -> None:
        self.btn_play.setChecked(not self.btn_play.isChecked())

    def step_frame(self, d: int) -> None:
        n = self.vd.n_frames if self.vd else 1
        self.frame_slider.setValue(min(max(self.frame_slider.value() + int(d), 0), max(n - 1, 0)))

    def goto_frame(self, where: str) -> None:
        n = self.vd.n_frames if self.vd else 1
        self.frame_slider.setValue(0 if where == "first" else max(n - 1, 0))

    def goto_tab(self, i: int) -> None:
        self.tabs.setCurrentIndex(int(i))

    def toggle_view(self, what: str) -> None:
        cb = self._view_checks.get(what)
        if cb is not None:
            cb.setChecked(not cb.isChecked())

    def mesh(self, key: str) -> None:
        i = self.mesh_combo.findData(key)
        if i >= 0:
            self.mesh_combo.setCurrentIndex(i)

    def mesh_cycle(self) -> None:
        n = self.mesh_combo.count()
        self.mesh_combo.setCurrentIndex((self.mesh_combo.currentIndex() + 1) % max(n, 1))

    def _viewport_key(self, name: str) -> None:
        {"prev": lambda: self.step_frame(-1), "next": lambda: self.step_frame(1), "first": lambda: self.goto_frame("first"),
         "last": lambda: self.goto_frame("last"), "play": self.toggle_play}[name]()

    # ---- 실행 취소 / 다시 실행
    def _edit_state(self) -> dict:
        return {"settings": copy.deepcopy(self.pr.settings), "src": dict(self.pr.src_slots), "tgt": dict(self.pr.tgt_slots)}

    def _apply_edit_state(self, st: dict) -> None:
        self.pr.settings = copy.deepcopy(st["settings"])
        self.pr.src_slots = dict(st["src"])
        self.pr.tgt_slots = dict(st["tgt"])
        self._syncing = True
        self._sync_settings_ui()
        self._syncing = False
        if self.pr.src is not None or self.pr.tgt is not None:
            self._fill_mapping()
        self.recompute_timer.stop()
        self.recompute()

    def _timer_fire(self) -> None:
        self.history.commit(self._edit_state())  # 조절이 잠깐 멈추면 한 단계로 확정
        self.recompute()

    def undo(self) -> None:
        st = self.history.undo(self._edit_state())
        if st is None:
            self.statusBar().showMessage("되돌릴 내용이 없습니다")
            return
        self._apply_edit_state(st)
        self.statusBar().showMessage("실행 취소 (남은 %d단계)" % len(self.history.undo_stack))

    def redo(self) -> None:
        self.history.commit(self._edit_state())
        st = self.history.redo()
        if st is None:
            self.statusBar().showMessage("다시 실행할 내용이 없습니다")
            return
        self._apply_edit_state(st)
        self.statusBar().showMessage("다시 실행")

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
        self.viewport.key_action.connect(self._viewport_key)
        rl.addWidget(self.viewport, 1)
        opts = QHBoxLayout()
        self._view_checks: Dict[str, object] = {}
        for text, attr, default, key in (("뼈대", "show_bones", True, "bones"), ("소스 겹쳐 보기", "show_ghost", True, "ghost"),
                                         ("발 고정 표시", "show_ik", True, "ik"), ("카메라 따라가기", "follow", True, "follow")):
            cb = QCheckBox(text)
            cb.setChecked(default)
            cb.toggled.connect(lambda v, a=attr: (setattr(self.viewport, a, bool(v)), self.viewport.update()))
            opts.addWidget(cb)
            self._view_checks[key] = cb
        cb = QCheckBox("보조 본")
        cb.setToolTip("IK·카메라·워프·얼굴·천 등 매핑되지 않은 본도 그립니다. 끄면 몸(매핑된 본)만 보입니다")
        cb.setChecked(False)
        cb.toggled.connect(self._helpers_toggled)
        opts.addWidget(cb)
        self._view_checks["helpers"] = cb
        opts.addWidget(QLabel("메시"))
        self.mesh_combo = QComboBox()
        for k, n in MESH_MODES:
            self.mesh_combo.addItem(n, k)
        self.mesh_combo.setToolTip("점 / 와이어 / 반투명 / 솔리드 / 머티리얼(색·텍스처).  단축키 0~5, M 으로 순환")
        self.mesh_combo.setCurrentIndex(max(self.mesh_combo.findData(self.cfg.get("mesh_mode", "solid")), 0))
        self.viewport.mesh_mode = self.mesh_combo.currentData() or "solid"
        self.mesh_combo.currentIndexChanged.connect(self._mesh_mode_changed)
        opts.addWidget(self.mesh_combo)
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
        for label, key in (("애니메이션 (PSA)", "anim"), ("메시 (FBX / OBJ / GLB 로 변환)", "mesh")):
            self.ex_kind.addItem(label, key)
        self.ex_kind.currentIndexChanged.connect(self._kind_changed)
        h.addWidget(self.ex_kind, 1)
        v.addLayout(h)

        self.mesh_opts = QWidget()
        mv = QVBoxLayout(self.mesh_opts)
        mv.setContentsMargins(0, 0, 0, 0)
        h = QHBoxLayout()
        h.addWidget(QLabel("형식"))
        self.ex_fbx = QCheckBox("FBX")
        self.ex_fbx.setChecked(True)
        self.ex_obj = QCheckBox("OBJ (리깅 없음만)")
        self.ex_glb = QCheckBox("GLB")
        for cb in (self.ex_fbx, self.ex_obj, self.ex_glb):
            h.addWidget(cb)
        h.addStretch(1)
        mv.addLayout(h)
        h = QHBoxLayout()
        h.addWidget(QLabel("리깅"))
        self.ex_rig = QComboBox()
        for label, key in (("리깅 포함 + 리깅 없음 (둘 다)", "both"), ("리깅 포함만 (스켈레톤+스킨)", "rigged"), ("리깅 없음만 (메시만)", "static")):
            self.ex_rig.addItem(label, key)
        h.addWidget(self.ex_rig, 1)
        h.addWidget(QLabel("대상 프로그램"))
        self.ex_target = QComboBox()
        for k, pr_ in PRESETS.items():
            self.ex_target.addItem(pr_.label, k)
        tgt = self.cfg.get("mesh_target", "max")
        i = self.ex_target.findData(tgt)
        self.ex_target.setCurrentIndex(i if i >= 0 else 0)
        self.ex_target.setToolTip("축/단위 설정. 3ds Max: Z-up, Maya: Y-up")
        h.addWidget(self.ex_target)
        mv.addLayout(h)
        h = QHBoxLayout()
        self.ex_tex = QCheckBox("머티리얼/텍스처 포함 (GLB 경유, 느림)")
        h.addWidget(self.ex_tex)
        self.ex_keep_raw = QCheckBox("원본(PSKX/GLB)도 목록에 남기기")
        self.ex_keep_raw.setChecked(True)
        h.addWidget(self.ex_keep_raw)
        h.addStretch(1)
        mv.addLayout(h)
        v.addWidget(self.mesh_opts)
        h = QHBoxLayout()
        self.ex_filter = QLineEdit(self.KIND_FILTERS["anim"])
        self.ex_filter.setToolTip("에셋 경로에 대한 정규식. 예: (?i)(walk|run|attack)")
        h.addWidget(self.ex_filter, 1)
        b = QPushButton("검색")
        b.clicked.connect(self._search_anims)
        h.addWidget(b)
        v.addLayout(h)
        self.ex_list = PathTree(strip_ext=True)  # 폴더 구조대로 접고 펼치는 트리
        self.ex_list.setMinimumHeight(220)
        self.ex_list.itemSelectionChanged.connect(self._update_ex_count)
        v.addWidget(self.ex_list)
        self.ex_count = QLabel("")
        v.addWidget(self.ex_count)
        h = QHBoxLayout()
        b = QPushButton("전체 선택")
        b.clicked.connect(lambda: self.ex_list.selectAll())
        h.addWidget(b)
        b = QPushButton("모두 펼치기")
        b.clicked.connect(lambda: self.ex_list.set_all_expanded(True))
        h.addWidget(b)
        b = QPushButton("모두 접기")
        b.clicked.connect(lambda: self.ex_list.set_all_expanded(False))
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
        self.ex_dir_label = QLabel("")
        self.ex_dir_label.setTextFormat(Qt.TextFormat.RichText)
        self.ex_dir_label.setToolTip("클릭하면 탐색기에서 이 폴더가 열립니다")
        self.ex_dir_label.linkActivated.connect(lambda *_a: self._open_out_folder())
        v.addWidget(self.ex_dir_label)
        self.ex_files = PathTree(tag_kinds=True)  # [모델링 · 리깅 포함] / [애니메이션] 같은 이름표를 붙인다
        self.ex_files.setMinimumHeight(130)
        self.ex_files.itemDoubleClicked.connect(self._ex_files_double_clicked)
        v.addWidget(self.ex_files)
        h = QHBoxLayout()
        b = QPushButton("선택 항목 위치 열기")
        b.setToolTip("선택한 파일/폴더가 있는 폴더를 탐색기로 엽니다 (아무것도 안 골랐으면 출력 폴더)")
        b.clicked.connect(self._open_selected_location)
        h.addWidget(b)
        b = QPushButton("출력 폴더 내용 불러오기")
        b.setToolTip("출력 폴더에 이미 있는 PSA/PSK/GLB/FBX/OBJ 를 목록에 올립니다 (이전에 뽑은 PSKX 를 변환할 때)")
        b.clicked.connect(self._load_out_folder)
        h.addWidget(b)
        v.addLayout(h)
        b = QPushButton("선택한 파일을 소스 모션으로 열기")
        b.clicked.connect(self._open_extracted)
        v.addWidget(b)
        b = QPushButton("선택한 GLB 를 타깃 모델로 열기")
        b.setToolTip("게임에서 뽑은 메시(GLB)를 타깃(내 모델) 자리에 불러옵니다. 소스 모션과 같은 캐릭터면 그대로 확인할 수 있습니다.")
        b.clicked.connect(self._open_extracted_as_target)
        v.addWidget(b)
        self.btn_convert = QPushButton("선택한 메시(PSK/PSKX/GLB)를 위 형식으로 변환")
        self.btn_convert.setToolTip("3) 에서 고른 형식/리깅/대상 프로그램 설정으로 변환합니다. 이미 뽑아 둔 PSKX 도 변환할 수 있습니다.")
        self.btn_convert.clicked.connect(self._convert_selected_meshes)
        v.addWidget(self.btn_convert)
        lay.addWidget(g)
        self.ex_out.textChanged.connect(self._update_dir_label)
        self._update_dir_label()
        lay.addStretch(1)
        return sc

    # ---- 추출 탭 동작
    KIND_FILTERS = {"anim": "(?i)anim", "mesh": r"(?i)/sk[a-z]*_"}

    def _kind_changed(self, *_a) -> None:
        key = self.ex_kind.currentData() or "anim"
        cur = self.ex_filter.text().strip()
        if cur in self.KIND_FILTERS.values() or not cur:
            self.ex_filter.setText(self.KIND_FILTERS[key])
        self.mesh_opts.setVisible(key == "mesh")
        self.btn_extract.setText("선택한 애니메이션 추출 (PSA)" if key == "anim" else "선택한 메시 추출 + 변환")

    # ---- 폴더 열기
    def _update_dir_label(self, *_a) -> None:
        d = self.ex_out.text().strip()
        self.ex_dir_label.setText('출력 폴더: <a href="open">%s</a> (클릭하면 탐색기로 열림)' % d.replace("&", "&amp;").replace("<", "&lt;") if d else "")

    def _open_out_folder(self) -> None:
        d = self.ex_out.text().strip()
        if not d:
            return
        os.makedirs(d, exist_ok=True)
        if not osopen.open_in_explorer(d):
            QMessageBox.warning(self, "폴더 열기", "폴더를 열지 못했습니다: " + d)

    def _open_selected_location(self) -> None:
        sel = self.ex_files.selected_paths()
        if not sel:
            self._open_out_folder()
            return
        target = sel[0] if len(sel) == 1 else os.path.commonpath([os.path.dirname(x) for x in sel])
        if not osopen.open_in_explorer(target):
            QMessageBox.warning(self, "폴더 열기", "열지 못했습니다: " + target)

    def _ex_files_double_clicked(self, it, col=0) -> None:
        if it.childCount() > 0:
            # 폴더 줄을 더블클릭하면 그 폴더를 연다 (펼침/접힘은 화살표로)
            leaves = self.ex_files._leaves(it)
            if leaves:
                osopen.open_in_explorer(os.path.commonpath([os.path.dirname(x) for x in leaves]))
            return
        p = it.data(0, self.ex_files.ROLE)
        if p and p.lower().endswith(".psa"):
            self._open_extracted()
        elif p and p.lower().endswith((".glb", ".gltf")):
            self._open_extracted_as_target()
        elif p:
            osopen.open_in_explorer(p)

    def _load_out_folder(self) -> None:
        d = self.ex_out.text().strip()
        if not os.path.isdir(d):
            QMessageBox.information(self, "불러오기", "출력 폴더가 아직 없습니다: " + d)
            return
        exts = (".psa", ".psk", ".pskx", ".glb", ".fbx", ".obj")
        found = []
        for root, _dirs, names in os.walk(d):
            found += [os.path.join(root, n) for n in names if n.lower().endswith(exts)]
        self.ex_files.set_paths(sorted(found))
        self.statusBar().showMessage("출력 폴더에서 %d개를 불러왔습니다" % len(found))

    def _mesh_settings(self):
        fmts = [k for k, cb in (("fbx", self.ex_fbx), ("obj", self.ex_obj), ("glb", self.ex_glb)) if cb.isChecked()]
        rig = self.ex_rig.currentData() or "both"
        variants = {"both": ["rigged", "static"], "rigged": ["rigged"], "static": ["static"]}[rig]
        preset = self.ex_target.currentData() or "max"
        self.cfg["mesh_target"] = preset
        return fmts, variants, preset

    def _mesh_blender_ok(self, fmts) -> bool:
        if any(f in ("fbx", "glb") for f in fmts) and not bridge.find_blender(self.pr.blender):
            QMessageBox.warning(self, "Blender 필요", "FBX / GLB 로 변환하려면 Blender 가 필요합니다.\n'파일 → Blender 경로 지정…' 에서 blender 실행 파일을 지정하세요.\n(OBJ 만 고르면 PSK/PSKX 는 Blender 없이 변환됩니다.)")
            return False
        return True

    def _convert_selected_meshes(self) -> None:
        sel = [p for p in (self.ex_files.selected_paths() or self.ex_files.all_paths()) if meshconv.is_mesh_file(p)]
        # 이미 변환된 결과(_rigged/_static)는 원본이 아니다
        sel = [p for p in sel if not os.path.splitext(os.path.basename(p))[0].endswith(("_rigged", "_static"))]
        if not sel:
            QMessageBox.warning(self, "변환", "변환할 메시(PSK/PSKX/GLB)를 목록에서 고르세요. 이전에 뽑은 파일은 '출력 폴더 내용 불러오기'로 올릴 수 있습니다.")
            return
        fmts, variants, preset = self._mesh_settings()
        if not fmts:
            QMessageBox.warning(self, "변환", "형식(FBX / OBJ / GLB)을 하나 이상 고르세요.")
            return
        if not self._mesh_blender_ok(fmts):
            return
        out = self.ex_out.text().strip()
        if not out:
            return
        self._ex_skips = []

        def job(w: Worker):
            made = []
            for i, f in enumerate(sel):
                w.progress.emit(i, len(sel), os.path.basename(f))
                try:
                    made += meshconv.convert_mesh(f, extractor.models_dir(out), fmts, variants, preset, self.pr.blender, self._ex_skips.append)
                except (meshconv.MeshConvError, bridge.BlenderError) as e:
                    self._ex_skips.append(str(e))
            return made

        self.run_async(job, self._after_convert, "메시 변환 중")

    def _after_convert(self, made) -> None:
        if not made:
            why = [x for x in self._ex_skips if x][:6]
            self.statusBar().showMessage("변환된 파일이 없습니다")
            QMessageBox.warning(self, "변환", "변환된 파일이 없습니다.\n\n" + "\n".join(why))
            return
        have = self.ex_files.all_paths()
        self.ex_files.set_paths(list(dict.fromkeys(have + list(made))))
        self.statusBar().showMessage("변환 완료: %d개 — 위 '출력 폴더' 링크로 폴더를 열 수 있습니다" % len(made))
        if self._ex_skips:
            QMessageBox.information(self, "변환", "일부 안내:\n" + "\n".join(x for x in self._ex_skips if x)[:800])

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
        self.ex_list.set_paths(paths)
        logs = getattr(self, "_ex_logs", [])
        self.ex_count.setText("%d개" % len(paths) + (" — 폴더 왼쪽 화살표로 열고 닫기, 폴더를 고르면 안의 파일 전부 추출" if paths else " — UE 버전, mappings(.usmap), 검색식을 확인해 보세요.\n도우미 로그: " + " / ".join(logs[-4:])))

    def _update_ex_count(self) -> None:
        n, sel = self.ex_list.count(), len(self.ex_list.selected_paths())
        if n:
            self.ex_count.setText("%d개 중 %d개 선택" % (n, sel) if sel else "%d개 — 폴더 왼쪽 화살표로 열고 닫기, 폴더를 고르면 안의 파일 전부 추출" % n)

    def _extract_selected(self) -> None:
        paths = self.ex_list.selected_paths()
        if not paths:
            QMessageBox.warning(self, "추출", "추출할 항목을 목록에서 고르세요.")
            return
        a = self._extract_args()
        if a is None:
            return
        out = self.ex_out.text().strip()
        if not out:
            return

        self._ex_skips = []
        key = self.ex_kind.currentData() or "anim"
        kind = "anim" if key == "anim" else "mesh"
        fmts, variants, preset = self._mesh_settings() if kind == "mesh" else ([], [], "max")
        tex = bool(self.ex_tex.isChecked()) and kind == "mesh"
        keep_raw = bool(self.ex_keep_raw.isChecked())
        if kind == "mesh":
            if not fmts:
                QMessageBox.warning(self, "추출", "형식(FBX / OBJ / GLB)을 하나 이상 고르세요.")
                return
            if not self._mesh_blender_ok(fmts):
                return
            bridge.save_config(self.cfg)

        def job(w: Worker):
            n = [0]

            def ev(e):
                if e.get("type") == "skip":
                    self._ex_skips.append("%s — %s" % (os.path.basename(e.get("path", "")), e.get("reason", "")))
                elif e.get("type") == "log":
                    self._ex_skips.append(e.get("message", ""))
                if e.get("type") in ("exported", "skip"):
                    n[0] += 1
                    w.progress.emit(n[0] - 1, len(paths), os.path.basename(e.get("path", "")))
            if kind == "mesh":
                return extractor.export_meshes(*a, paths, out, fmts, variants, preset, self.pr.blender, tex, keep_raw, ev, self._ex_skips.append)
            return extractor.export_assets(*a, paths, extractor.anim_dir(out), ev, kind="anim")

        self.run_async(job, self._after_extract, "추출 중")

    def _after_extract(self, files) -> None:
        self.ex_files.set_paths(files)
        if not files:
            why = [x for x in getattr(self, "_ex_skips", []) if x][:6]
            self.statusBar().showMessage("추출된 파일이 없습니다")
            QMessageBox.warning(self, "추출", "추출된 파일이 없습니다. 화면의 모션은 바뀌지 않습니다.\n\n" + ("\n".join(why) if why else "도우미가 이유를 알려주지 않았습니다. 종류(애니메이션/메시)와 선택한 에셋을 확인하세요."))
            return
        msg = "추출 완료: %d개 — 위 '출력 폴더' 링크를 누르면 폴더가 열립니다" % len(files)
        # 방금 추출한 모션/메시를 바로 화면에 보여준다 (여러 개면 첫 번째. 나머지는 아래 목록에서 더블클릭)
        psa = next((f for f in files if f.lower().endswith(".psa")), None)
        glb = next((f for f in files if f.lower().endswith(".glb") and "_static" not in f), None) or next((f for f in files if f.lower().endswith(".glb")), None)
        self.statusBar().showMessage(msg)
        if psa:
            if len(files) > 1:
                self.statusBar().showMessage(msg + " (첫 번째 모션을 열었습니다)")
            self._open_path(psa)
        elif glb:
            self._open_path_as_target(glb)

    def _open_extracted(self) -> None:
        sel = self.ex_files.selected_paths() or ([self.ex_files.first_path()] if self.ex_files.count() else [])
        if sel:
            self._open_path(sel[0])

    def _open_extracted_as_target(self) -> None:
        sel = [p for p in self.ex_files.selected_paths() if p.lower().endswith(".glb")]
        if not sel:
            sel = [p for p in self.ex_files.all_paths() if p.lower().endswith(".glb")][:1]
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
                               ("bip", "3ds Max Biped (.ms)", "FBX 를 거치지 않고 Biped 에 바로 넣는 MaxScript + 데이터. 3ds Max 에서 스크립트를 실행하면 Biped 를 만들고 .bip 로 저장합니다 (대상 프로그램 선택과 무관, 3ds Max 전용)"),
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
        self.history.reset(self._edit_state())  # 새 파일을 열면 실행 취소 기록은 새로 시작

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
        self.history.commit(self._edit_state())  # 프로필 불러오기도 실행 취소 가능
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
        self.vd = ViewData.build(self.pr, show_helpers=bool(getattr(self, "_show_helpers", False))) if (self.pr.src or self.pr.tgt) else None
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

    def _helpers_toggled(self, v) -> None:
        self._show_helpers = bool(v)
        self.recompute()

    def _mesh_mode_changed(self, *_a) -> None:
        self.viewport.mesh_mode = self.mesh_combo.currentData() or "solid"
        self.cfg["mesh_mode"] = self.viewport.mesh_mode
        bridge.save_config(self.cfg)
        self.viewport.update()

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
        needs_preset = any(k in ("bvh", "fbx") for k in fmts)
        if skel and (not fmts or (needs_preset and not presets)):
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
