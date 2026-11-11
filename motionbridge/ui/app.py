"""MotionBridge 메인 창 (PySide6)."""
from __future__ import annotations

import copy
import dataclasses
import os
import sys
from typing import Dict, List, Optional

import numpy as np

from PySide6.QtCore import QObject, QTimer, QUrl, Qt, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QRadioButton,
    QScrollArea, QSlider, QSplitter, QSpinBox, QStackedWidget, QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout,
    QWidget, QLineEdit, QButtonGroup, QAbstractItemView, QTreeWidget, QTreeWidgetItem,
)

from .. import __version__
from ..core import quat as Q
from ..core import animlink, animops, bridge, extractor, gamepresets, gamescan, layout, meshconv, motionlib, osopen, safesave, scalefit, textmatch
from ..core.export import PRESETS
from ..core import guide as guide_mod
from ..core.history import History
from ..core.layers import LayerStack
from ..core import motionfx
from ..core.loader import FILE_FILTER
from ..core.mapping import FINGERS, SLOT_ORDER, slot_label
from ..core.pipeline import ExportOptions, Project
from . import gizmo_math as GZ
from .help_text import EXTRACT_HELP, FIRST_STEPS
from .curve_editor import CurveEditor
from .ik_panel import IKRangePanel
from .layer_panel import LayerPanel
from .viewer import MESH_MODES, Viewport
from .retimebar import RetimeBar
from .viewer_math import QUALITY_NAMES, ViewData
from .vidfind_tab import VidFindTab
from .mocap_tab import MocapTab
from .char_assets import CharAssetPanel
from .animreftab import AnimRefTab
from .chrometabs import ChromeTabs
from ..core import posik
from .clip_panel import ClipPanel
from . import theme as _theme
from .uikit import HelpIcon, Resizable, ViewportOverlay, arrow_button, label_with_help, with_help
from .help_text import EXTRACT_SEARCH_TIPS
from .widgets import BusyDialog, CollapsibleBox, EditPanel, NumField, PathTree, WheelGuard, Worker, fit_widths, wrappable

AXES = ["auto", "+X", "-X", "+Y", "-Y", "+Z", "-Z"]
AXIS_LABEL = {"auto": "자동"}


def _is_finger(slot: str) -> bool:
    return any(f in slot for f in FINGERS)


class _LinkSignals(QObject):
    """서버 스레드에서 받은 AnimRef 요청을 UI 스레드로 안전하게 넘기는 통로 (Qt 가 큐로 전달한다)."""

    opened = Signal(object)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MotionBridge %s — 모션 리타겟" % __version__)
        self.pr = Project()
        self.cfg = bridge.load_config()
        if not isinstance(self.cfg.get("collapsed"), dict):
            self.cfg["collapsed"] = {}
        CollapsibleBox.bind(self.cfg["collapsed"], lambda: bridge.save_config(self.cfg))   # 섹션 접힘 상태 기억
        if not isinstance(self.cfg.get("sizes"), dict):
            self.cfg["sizes"] = {}
        Resizable.bind(self.cfg["sizes"], lambda: bridge.save_config(self.cfg))             # 끌어서 바꾼 상자 높이 기억
        self.pr.blender = self.cfg.get("blender")
        self.vd: Optional[ViewData] = None
        self._worker: Optional[Worker] = None
        self._fields: Dict[str, object] = {}  # settings 키 -> 위젯 (프로필 불러올 때 동기화)
        self._syncing = False
        self._playing = False
        self._last_status = ""
        self.scan: Optional[gamescan.GameScan] = None
        self.link: Optional[animlink.LinkServer] = None
        self._link_sig = _LinkSignals()
        self._link_sig.opened.connect(self._on_link_open)

        self.recompute_timer = QTimer(self)
        self.recompute_timer.setSingleShot(True)
        self.recompute_timer.setInterval(120)
        self.recompute_timer.timeout.connect(self._timer_fire)
        # 뷰포트에서 본을 끌 때: 마우스를 놓기 전에도 결과가 따라오도록 쓰는 짧은 간격의 계산 (끝나면 위의 느린 타이머가 전체 계산·실행 취소 기록을 마무리)
        self.live_timer = QTimer(self)
        self.live_timer.setSingleShot(True)
        self.live_timer.timeout.connect(self._live_fire)
        self._live_pending = False
        self._ik_drag = None            # 손·발 IK 끌기: [본, 시작 위치, 끈 합]
        self._live_gap = 0.0      # 직전 계산이 오래 걸렸으면 그 절반만큼 쉬었다가 다음 계산 (무거운 모델에서 화면이 밀리지 않게)
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
        # 휠 잠금: 숫자칸·슬라이더·콤보 위 휠이 값을 바꾸지 않게 (기본 켜짐, Ctrl+L)
        self.wheel_guard = WheelGuard(bool(self.cfg.get("wheel_lock", True)), self)
        _app = QApplication.instance()
        if _app is not None:
            _app.installEventFilter(self.wheel_guard)
        self.wheel_cb = QCheckBox("🔒 슬라이더 휠 잠금")
        self.wheel_cb.setToolTip("켜 두면 리타겟 조절의 슬라이더(파란 선) 위에서 마우스 휠을 굴려도 값이 바뀌지 않고 패널만 스크롤됩니다. (단축키 Ctrl+L)\nCtrl 을 누른 채 굴리면 잠겨 있어도 슬라이더 값이 바뀝니다.\n숫자칸·콤보박스·3D 뷰의 휠은 그대로 동작합니다. 곡선 그래프·타임바는 Ctrl+휠일 때만 확대/축소(조각 늘이기)가 됩니다.")
        self.wheel_cb.setChecked(self.wheel_guard.lock)
        self.wheel_cb.toggled.connect(lambda on: self.set_wheel_lock(on))
        self.statusBar().addPermanentWidget(self.wheel_cb)
        self._zombies: list = []  # 취소했지만 아직 끝나지 않은 작업 스레드 (끝날 때까지 붙들어 둔다)

    # ================================================================== AnimRef 연결
    def start_link(self, directory: Optional[str] = None) -> bool:
        """AnimRef 가 요청을 보낼 로컬 서버를 연다 (실행 중에만). 실패해도 프로그램은 계속 동작한다."""
        if self.link is not None:
            return True
        try:
            srv = animlink.LinkServer(self._link_sig.opened.emit, __version__, directory)
            srv.start()
        except animlink.LinkBusy as e:
            self.statusBar().showMessage(str(e) + " — 이 창에서는 [파일 > AnimRef에서 가져오기]로 query.json 을 열 수 있습니다.")
            return False
        except OSError as e:
            self.statusBar().showMessage("AnimRef 연결을 열지 못했습니다: %s" % e)
            return False
        self.link = srv
        return True

    def stop_link(self) -> None:
        if self.link is not None:
            self.link.stop()
            self.link = None

    def closeEvent(self, ev) -> None:  # noqa: N802
        if not self.animref.can_close():  # OBS 녹화/분석이 진행 중이면 물어본다
            ev.ignore()
            return
        self.animref.shutdown()
        self.stop_link()
        for w in list(self.tabs.hub.floats):  # 분리된 창도 같이 닫는다
            w.hide()
        super().closeEvent(ev)

    def import_animref(self) -> None:
        """메뉴: AnimRef 가 만든 query.json 을 골라 '영상으로 찾기' 탭에 넣는다 (실시간 연결이 안 될 때)."""
        self.set_mode("motionbridge")
        self.tabs.setCurrentIndex(self.tabs.indexOf(self.vidfind))
        self.vidfind.open_query_file()

    def handoff(self, query_path: str) -> None:
        """시작할 때 --handoff <query.json> 으로 받은 요청을 처리한다."""
        try:
            q = animlink.parse_query(query_path)
        except animlink.LinkError as e:
            self.statusBar().showMessage("AnimRef 요청을 읽지 못했습니다: %s" % e)
            return
        self._on_link_open(q)

    def _on_link_open(self, query) -> None:
        """UI 스레드: AnimRef 요청을 받으면 '영상으로 찾기' 탭에 후보 화면을 띄운다."""
        self.vidfind.load_query(query)
        self.set_mode("motionbridge")
        self.tabs.setCurrentIndex(self.tabs.indexOf(self.vidfind))
        self.show()
        self.raise_()
        self.activateWindow()

    # ================================================================== 메뉴
    # 단축키 표: (메뉴, 키, 이름, 동작 메서드, 단축키들). 메뉴 항목이 곧 단축키 등록이다.
    def _menu_spec(self):
        mesh_items = [("mesh_" + k, "메시: " + n, "mesh:" + k, ("%d" % i,) if i <= 5 else ()) for i, (k, n) in enumerate(MESH_MODES) if True]
        spec = [
            ("파일", "open_source", "소스 모션 열기…", "open_source", ("Ctrl+O",)),
            ("파일", "open_target", "타깃 모델 열기…", "open_target", ("Ctrl+Shift+O",)),
            ("파일", "unload_source", "소스 모션 빼기", "unload_source", ()),
            ("파일", "unload_target", "타깃 모델 빼기", "unload_target", ()),
            ("파일", "import_animref", "AnimRef에서 가져오기…", "import_animref", ("Ctrl+Shift+A",)),
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
            ("프레임 편집", "mark_in", "구간 시작 표시 [", "retime_mark_in", ("I",)),
            ("프레임 편집", "mark_out", "구간 끝 표시 ]", "retime_mark_out", ("O",)),
            ("프레임 편집", "mark_clear", "구간 선택 해제", "retime_clear", ("Ctrl+D",)),
            ("프레임 편집", "retime_del", "선택 구간 삭제", "retime_delete", ("Delete",)),
            ("프레임 편집", "retime_split", "지금 프레임에서 자르기", "retime_split", ("S",)),
            ("프레임 편집", "retime_longer", "대상 구간 1프레임 길게 (느리게)", "retime_len:1", ("=",)),
            ("프레임 편집", "retime_shorter", "대상 구간 1프레임 짧게 (빠르게)", "retime_len:-1", ("-",)),
            ("편집", "automap", "자동 매핑 다시 하기", "_automap", ("Ctrl+M",)),
            ("편집", "reset_settings", "리타겟 설정 모두 기본값으로", "_reset_settings", ("Ctrl+Shift+R",)),
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
            ("보기", "theme", "밝은 / 어두운 테마 전환", "toggle_theme", ("Ctrl+Shift+T",)),
            ("보기", "clip_panel", "왼쪽 모션 목록 접기/펴기", "toggle_clip_panel", ("Ctrl+B",)),
            ("보기", "wheel_lock", "슬라이더 휠 잠금 켜기/끄기 (파란 선이 휠로 안 바뀜)", "toggle_wheel_lock", ("Ctrl+L",)),
            ("탭", "tab0", "0. 게임에서 추출", "goto_tab:0", ("Ctrl+1",)),
            ("탭", "tab1", "1. 파일·매핑", "goto_tab:1", ("Ctrl+2",)),
            ("탭", "tab2", "2. 리타겟 조절", "goto_tab:2", ("Ctrl+3",)),
            ("탭", "tab3", "3. 품질 검수", "goto_tab:3", ("Ctrl+4",)),
            ("탭", "tab4", "4. 내보내기", "goto_tab:4", ("Ctrl+5",)),
            ("탭", "tab5", "영상으로 찾기", "goto_tab:5", ("Ctrl+6",)),
            ("탭", "tab8", "AI 모캡", "goto_tab:8", ("Ctrl+7",)),
            ("도움말", "first_steps", "처음 쓰는 방법", "show_first_steps", ()),
            ("도움말", "toggle_guide", "안내 줄 보이기/숨기기", "toggle_guide", ("F2",)),
            ("도움말", "shortcuts", "단축키 목록", "show_shortcuts", ("F1",)),
            ("도움말", "about", "정보", "show_about", ()),
        ]
        return spec

    def retime_mark_in(self) -> None:
        self.retime_bar.mark_in()

    def retime_mark_out(self) -> None:
        self.retime_bar.mark_out()

    def retime_clear(self) -> None:
        self.retime_bar.clear_marks()

    def retime_delete(self) -> None:
        self.retime_bar.delete()

    def retime_split(self) -> None:
        self.retime_bar.split()

    def retime_len(self, d: int) -> None:
        self.retime_bar.wheel_adjust(int(d))

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
        QMessageBox.information(self, "MotionBridge", "MotionBridge %s\n게임 모션 → 내 모델 리타겟 / Max·Maya·Blender 내보내기" % __version__)

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
        if name.startswith("tool_"):
            self.set_bone_tool(name[5:], enable=True)
            return
        {"prev": lambda: self.step_frame(-1), "next": lambda: self.step_frame(1), "first": lambda: self.goto_frame("first"),
         "last": lambda: self.goto_frame("last"), "play": self.toggle_play, "key_selected": self.layer_panel.key_selected}[name]()

    # ---- 실행 취소 / 다시 실행
    def _edit_state(self) -> dict:
        """실행 취소 기록 한 칸: 설정 + 본 매핑 + 클립별 프레임 편집(조각 목록; 손대지 않은 것은 뺀다)."""
        edits = {ci: copy.deepcopy(e.pieces) for ci, e in self.pr.edits.items() if not e.is_untouched()}
        layers = {ci: st.snapshot() for ci, st in self.pr.layers.items() if len(st)}
        return {"settings": copy.deepcopy(self.pr.settings), "src": dict(self.pr.src_slots), "tgt": dict(self.pr.tgt_slots), "edits": edits, "layers": layers,
                "clip": self.pr.clip, "others": {ci: copy.deepcopy(st) for ci, st in self.pr._stash.items()}}

    def _apply_edit_state(self, st: dict) -> None:
        self.pr.settings = copy.deepcopy(st["settings"])
        if "clip" in st:                              # 클립별 설정까지 함께 되돌림 (다른 클립에서 한 일이라도 그 클립 설정으로)
            self.pr._clip = int(st["clip"])
            self.pr._stash = {ci: copy.deepcopy(o) for ci, o in st.get("others", {}).items()}
            for o in self.pr._stash.values():
                o.ik_ranges = self.pr.settings.ik_ranges
        self.pr.src_slots = dict(st["src"])
        self.pr.tgt_slots = dict(st["tgt"])
        self.pr.layers = {ci: LayerStack.from_snapshot(sn) for ci, sn in st.get("layers", {}).items()}
        want = st.get("edits", {})
        before = {ci: e.pieces for ci, e in self.pr.edits.items() if not e.is_untouched()}
        for ci in list(self.pr.edits):
            if ci not in want:
                del self.pr.edits[ci]
        if self.pr.src is not None:
            for ci, pcs in want.items():
                if 0 <= ci < len(self.pr.src.animations):
                    self.pr.frame_edit(ci).pieces = copy.deepcopy(pcs)
        if before != {ci: e.pieces for ci, e in self.pr.edits.items() if not e.is_untouched()}:
            self.retime_bar.clear_marks()  # 조각이 바뀌었으니 구간 선택은 풀어 둔다
        self._syncing = True
        self._sync_settings_ui()
        self._select_clip_widgets()
        self._syncing = False
        if self.pr.src is not None or self.pr.tgt is not None:
            self._fill_mapping()
        self.recompute_timer.stop()
        self.recompute()

    def schedule_live(self) -> None:
        """끄는 동안 바로 보여 주기: 가벼운 계산을 이어서(쉬는 간격은 직전 계산 시간에 맞춤) 하고, 손을 멈추면 평소처럼 전체 계산."""
        if self._syncing:
            return
        self.recompute_timer.start()                 # 끝난 뒤 한 번 더 전체 계산 + 실행 취소 기록
        self._live_pending = True
        if not self.live_timer.isActive():
            self.live_timer.start(int(self._live_gap))

    def _live_fire(self) -> None:
        if not self._live_pending:
            return
        self._live_pending = False
        import time as _t
        t0 = _t.perf_counter()
        self.recompute(live=True)
        ms = (_t.perf_counter() - t0) * 1000.0
        self._live_gap = min(80.0, 0.5 * ms)

    def _timer_fire(self) -> None:
        self.history.commit(self._edit_state())  # 조절이 잠깐 멈추면 한 단계로 확정
        self.recompute()
        self._refresh_clip_list()                # ✎ 표시 갱신

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

        self.tabs = ChromeTabs()  # 크롬식 탭 줄: 누르면 전환 · 끌어내면 별도 창 · 다른 창 탭 줄에 놓으면 합쳐짐
        self.tabs.addTab(self._tab_extract(), "추출", "0. 게임에서 추출")
        self.tabs.addTab(self._tab_files(), "파일·매핑", "1. 파일·매핑")
        self.tabs.addTab(self._tab_retarget(), "리타겟", "2. 리타겟 조절")
        self.tabs.addTab(self._tab_quality(), "검수", "3. 품질 검수")
        self.tabs.addTab(self._tab_export(), "내보내기", "4. 내보내기")
        self.vidfind = VidFindTab(self)
        self.tabs.addTab(self.vidfind, "영상찾기", "영상으로 찾기")
        self.tabs.addTab(self._tab_help(), "도우미", "추출 도우미")
        self.mocap = MocapTab(self)
        self.tabs.addTab(self.mocap, "AI모캡", "AI 모캡 — 영상에서 사람 3D 모션 (GVHMR / WHAM)")
        # 탭 줄 순서: 추출 다음이 영상찾기 (번호·단축키는 그대로, 분리했다 합쳐도 이 자리로 돌아온다)
        self.tabs.hub.set_rank(self.vidfind, 0.5)
        self.tabs.hub.move(self.vidfind, self.tabs, 1)
        self.tabs.setCurrentIndex(0)
        # AnimRef 는 탭이 아니라 최상단 선택(AnimRef | MotionBridge)의 한 화면이다. 처음 열 때 만들어진다 (시작이 느려지지 않게)
        self.animref = AnimRefTab(self)
        self.tabs.setMinimumWidth(500)
        fit_widths(self.tabs)  # 좁은 패널에서 내용이 잘리지 않게
        # 왼쪽 끝: 리타겟 중인 모션 목록 (눌러서 오가며 작업 / 다른 모션 세팅 가져오기) + 그 오른쪽에 기존 탭
        self.clip_panel = ClipPanel(self)
        self.clip_panel.setMinimumWidth(150)
        left = QSplitter(Qt.Orientation.Horizontal)          # 모션 목록 폭도 끌어서 조절
        left.addWidget(self.clip_panel)
        left.addWidget(self.tabs)
        try:
            left.setStretchFactor(0, 0)
            left.setStretchFactor(1, 1)
            left.setCollapsible(1, False)
            left.setSizes([int(self.cfg.get("clip_panel_w", 210)), 700])
        except (TypeError, AttributeError):
            pass
        self._left_split = left
        mv_ = getattr(left, "splitterMoved", None)
        if mv_ is not None and hasattr(mv_, "connect"):
            mv_.connect(self._left_split_moved)
        # 왼쪽 모션 목록 접기/펴기 화살표 (얇은 세로 막대)
        left_wrap = QWidget()
        lw = QHBoxLayout(left_wrap)
        lw.setContentsMargins(0, 0, 0, 0)
        lw.setSpacing(0)
        self.btn_clip_fold = arrow_button("\u25c0", "모션 목록 접기 / 펴기 (Ctrl+B)")
        self.btn_clip_fold.clicked.connect(lambda *_: self.toggle_clip_panel())
        lw.addWidget(self.btn_clip_fold)
        lw.addWidget(left, 1)
        split.addWidget(left_wrap)
        if self.cfg.get("clip_panel_hidden"):
            QTimer.singleShot(0, lambda: self.set_clip_panel_visible(False))

        right = QWidget()
        self.right_box = right
        outer = QVBoxLayout(right)
        outer.setContentsMargins(0, 0, 0, 0)
        self.vsplit = QSplitter(Qt.Orientation.Vertical)     # 위: 뷰포트 / 아래: 조절 줄·타임라인·곡선 그래프 — 사이 줄을 끌어서 크기 조절
        outer.addWidget(self.vsplit)
        top = QWidget()
        rl = QVBoxLayout(top)
        rl.setContentsMargins(0, 0, 0, 0)
        self.viewport = Viewport()
        self.viewport.key_action.connect(self._viewport_key)
        self.viewport.asset_dropped.connect(lambda kind, paths: self.extract_for_char(paths, kind))  # 에셋 목록에서 끌어다 놓으면 바로 열기
        self.viewport.asset_dropped_from.connect(self._asset_dropped_from)
        self.guide_box = QWidget()
        gl = QVBoxLayout(self.guide_box)
        gl.setContentsMargins(6, 4, 6, 4)
        gl.setSpacing(3)
        self.guide_steps = QLabel("")
        self.guide_steps.setTextFormat(Qt.TextFormat.RichText)
        gl.addWidget(self.guide_steps)
        grow = QHBoxLayout()
        self.guide_hint = QLabel("")
        self.guide_hint.setWordWrap(True)
        grow.addWidget(self.guide_hint, 1)
        self.guide_btn = QPushButton("")
        self.guide_btn.clicked.connect(lambda *_a: self._guide_action())
        grow.addWidget(self.guide_btn)
        b = QPushButton("？ 처음이세요?")
        b.setToolTip("MotionBridge 가 무엇을 하는지, 처음 쓰는 순서를 쉬운 말로 설명합니다")
        b.clicked.connect(lambda *_a: self.show_first_steps())
        grow.addWidget(b)
        self.guide_close = QPushButton("숨기기")
        self.guide_close.setToolTip("안내 줄을 숨깁니다 (F2 또는 아래쪽 [ℹ 안내 줄] 버튼으로 다시 켤 수 있어요)")
        self.guide_close.clicked.connect(lambda *_a: self.set_guide_visible(False))
        grow.addWidget(self.guide_close)
        gl.addLayout(grow)
        self.guide_box.setStyleSheet("QWidget#guide{background:#242a36;border:1px solid #3a4560;border-radius:6px}")
        self.guide_box.setObjectName("guide")
        try:
            self.guide_box.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)   # 테두리·배경이 그려지게
        except (AttributeError, TypeError):
            pass
        self._exported = False
        self.guide_box.setVisible(bool(self.cfg.get("show_guide", True)))
        rl.addWidget(self.guide_box)
        rl.addWidget(self.viewport, 1)
        self.viewport.setMinimumHeight(180)
        self.vsplit.addWidget(top)
        bottom = QScrollArea()                               # 아래쪽은 좁아지면 스크롤
        bottom.setWidgetResizable(True)
        bw = QWidget()
        rl = QVBoxLayout(bw)                                 # 이 아래 줄들(보기 옵션·본 편집·타임라인·곡선 그래프)은 아래쪽 영역에 쌓인다
        rl.setContentsMargins(0, 0, 0, 0)
        bottom.setWidget(bw)
        self.vsplit.addWidget(bottom)
        try:
            self.vsplit.setStretchFactor(0, 1)
            self.vsplit.setStretchFactor(1, 0)
            self.vsplit.setCollapsible(0, False)
            self.vsplit.setSizes([int(1000 * (1 - float(self.cfg.get("vsplit_bottom", 0.42)))), int(1000 * float(self.cfg.get("vsplit_bottom", 0.42)))])
        except (TypeError, AttributeError, ValueError):
            pass
        mv2_ = getattr(self.vsplit, "splitterMoved", None)
        if mv2_ is not None and hasattr(mv2_, "connect"):
            mv2_.connect(self._vsplit_moved)
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
        self.guide_toggle = QPushButton("ℹ 안내 줄")
        self.guide_toggle.setCheckable(True)
        self.guide_toggle.setChecked(bool(self.cfg.get("show_guide", True)))
        self.guide_toggle.setToolTip("위쪽 안내 줄(① 모션 열기 → ② 모델 열기 …)을 숨기거나 다시 보이게 합니다. 단축키 F2")
        self.guide_toggle.clicked.connect(lambda checked=False: self.set_guide_visible(bool(self.guide_toggle.isChecked())))
        opts.addWidget(self.guide_toggle)
        opts.addWidget(QLabel("메시"))
        self.mesh_combo = QComboBox()
        for k, n in MESH_MODES:
            self.mesh_combo.addItem(n, k)
        self.mesh_combo.setToolTip("점 / 와이어 / 반투명 / 솔리드 / 머티리얼(색·텍스처).  단축키 0~5, M 으로 순환")
        self.mesh_combo.setCurrentIndex(max(self.mesh_combo.findData(self.cfg.get("mesh_mode", "solid")), 0))
        self.viewport.mesh_mode = self.mesh_combo.currentData() or "solid"
        self.mesh_combo.currentIndexChanged.connect(self._mesh_mode_changed)
        opts.addWidget(self.mesh_combo)
        opts.addWidget(QLabel("품질"))
        self.quality_combo = QComboBox()
        for k, n in QUALITY_NAMES:
            self.quality_combo.addItem(n, k)
        self.quality_combo.setToolTip("무거운 메시가 버벅이면 낮추세요. 화면에서만 단순화하고 내보내기 데이터는 그대로입니다. 자동 = 삼각형 수로 선택")
        self.quality_combo.setCurrentIndex(max(self.quality_combo.findData(self.cfg.get("mesh_quality", "auto")), 0))
        self.viewport.mesh_quality = self.quality_combo.currentData() or "auto"
        self.quality_combo.currentIndexChanged.connect(self._quality_changed)
        opts.addWidget(self.quality_combo)
        b = QPushButton("화면 맞추기")
        b.clicked.connect(self._reframe)
        opts.addWidget(b)
        opts.addStretch(1)
        rl.addLayout(opts)
        # ---- 본 편집: 파츠를 목록에서 고르거나 뷰포트에서 클릭해 고르고, 끌어서 돌린다 (값은 '본별 회전 보정'에 그대로 기록)
        bl = QHBoxLayout()
        self.cb_bone_edit = QPushButton("🦴 본 편집  OFF")
        self.cb_bone_edit.setToolTip("켜면 뷰포트에서 본(점)을 클릭해 고르고, 3ds Max 처럼 Q 선택 / W 이동 / E 회전 / R 크기 키로 도구를 바꿔 직접 고칩니다.\n"
                                     "하위 본 전체가 같이 움직이고, 값은 [리타겟]의 '본별 보정'(회전·위치·크기)에 기록돼 미리보기·내보내기에 똑같이 반영됩니다 (Ctrl+Z 로 되돌리기).\n"
                                     "기즈모의 축 핸들을 끌면 한 축으로만, 점/중심을 끌면 자유롭게 바뀝니다. 빈 곳에서 끌면 카메라가 돕니다.\n"
                                     "Ctrl 을 누른 채 다른 본(점)을 클릭하면 선택은 그대로 두고 그 본에 지금 프레임의 키만 찍습니다 (레이어가 없으면 새로 만들어요).")
        self.cb_bone_edit.setCheckable(True)
        self.cb_bone_edit.setMinimumHeight(28)
        self.cb_bone_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cb_bone_edit.setStyleSheet(
            "QPushButton{font-size:13px;font-weight:700;padding:3px 12px;border:2px solid #4a5570;border-radius:8px;background:#222836;color:#aab3c8}"
            "QPushButton:hover{border-color:#8fb0ff;color:#ffffff}"
            "QPushButton:checked{background:#2f7d4a;border-color:#7ff0a0;color:#ffffff}"
            "QPushButton:checked:hover{background:#38905a}")
        self.cb_bone_edit.toggled.connect(self._bone_edit_toggled)
        bl.addWidget(self.cb_bone_edit)
        self.cb_pos_only = QCheckBox("📍 포지션 분리 (이 본만 움직이기)")
        self.cb_pos_only.setChecked(bool(self.cfg.get("pos_only", False)))
        self.cb_pos_only.setToolTip("W 이동으로 손·발을 끌 때의 방식입니다 (팔도 다리와 똑같이 적용됩니다).\n"
                                    "해제(기본) = 3ds Max 바이패드처럼 팔·다리가 IK 로 자연스럽게 펴지고 접히며 손/발이 따라옵니다 (위팔·아래팔이 돌아감, 길이는 그대로).\n"
                                    "체크(포지션 분리) = 그 본만 위치를 옮깁니다 (팔다리는 그대로, 그 본이 몸에서 떨어지거나 늘어난 모양이 될 수 있어요).\n"
                                    "손·발 끝이 아닌 본(골반·몸통·팔꿈치 등)은 항상 그 본만 움직입니다.")
        self.cb_pos_only.toggled.connect(self._pos_only_toggled)
        bl2 = QHBoxLayout()          # 둘째 줄: 포지션 분리 · 오토키 · 고른 본 · 초기화 (뷰포트 안 도구 줄)
        bl2.addWidget(self.cb_pos_only)
        self.cb_autokey = QCheckBox("🔑 오토키")
        self.cb_autokey.setChecked(bool(self.cfg.get("autokey", False)))
        self.cb_autokey.setToolTip("켜면 본을 돌리거나 옮길 때마다 지금 프레임에 자동으로 키가 찍힙니다 (보정 레이어가 없으면 '오토키' 레이어를 만들어요).\n"
                                   "끄면 편집이 전체 구간 '본별 보정'으로 들어갑니다 (레이어가 골라져 있고 🔴 기록이 켜져 있으면 그 레이어에 기록).\n"
                                   "오토키와 상관없이 K 키를 누르면 고른 본에 지금 프레임의 키를 찍습니다 (뷰포트 위에서).")
        self.cb_autokey.toggled.connect(self._autokey_toggled)
        bl2.addWidget(self.cb_autokey)
        self.tool_buttons: Dict[str, QPushButton] = {}
        for tool, text, tip in (("select", "Q 선택", "점을 눌러 본을 고릅니다 (끌어도 아무것도 바뀌지 않음)"),
                                ("move", "W 이동", "화살표(X 빨강/Y 초록/Z 파랑)를 끌면 그 축으로만, 중심 네모를 끌면 자유롭게 본(과 하위 본 전체)을 옮깁니다"),
                                ("rotate", "E 회전", "고리를 끌면 그 축으로만 돌고, 점을 끌면 자유롭게 돕니다 (Alt+끌기=비틀기)"),
                                ("scale", "R 크기", "끌면 그 본을 기준으로 하위 본 위치와 메시가 커지거나 작아집니다 (오른쪽/위=커짐)")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setToolTip(tip + "\n뷰포트에서 같은 키(Q/W/E/R)를 눌러도 바뀝니다 — 본 편집이 꺼져 있으면 W/E/R 이 켭니다.")
            b.clicked.connect(lambda _c=False, t=tool: self.set_bone_tool(t, enable=True))
            self.tool_buttons[tool] = b
            bl.addWidget(b)
        self.tool_buttons["rotate"].setChecked(True)
        bl.addWidget(QLabel("좌표계"))
        self.space_combo = QComboBox()
        for k in GZ.SPACES:
            self.space_combo.addItem(GZ.SPACE_NAMES[k], k)
        self.space_combo.setToolTip("기즈모 축의 기준 (3ds Max 의 참조 좌표계와 같습니다).\n"
                                    "월드(World): 화면의 X·Y·Z 축 고정 / 로컬(Local): 선택한 본 자신의 축을 따라 / 화면(Screen): 카메라가 보는 화면의 가로·세로 기준.\n"
                                    "축 핸들로 이동·회전할 때 적용되고, 점·중심을 끌어 자유롭게 바꾸는 동작은 영향받지 않습니다.")
        self.space_combo.setCurrentIndex(max(self.space_combo.findData(self.cfg.get("gizmo_space", "world")), 0))
        self.viewport.set_gizmo_space(self.space_combo.currentData() or "world")
        self.space_combo.currentIndexChanged.connect(self._space_changed)
        bl.addWidget(self.space_combo)
        self.bone_sel_label = QLabel("")
        self.bone_sel_label.setStyleSheet("color:#ffdc50")
        bl.addStretch(1)
        bl2.addWidget(self.bone_sel_label, 1)
        self.btn_bone_reset = QPushButton("이 본 초기화")
        self.btn_bone_reset.setToolTip("선택한 본의 수정을 처음 상태로 되돌립니다.\n레이어를 고른 상태면 그 레이어에서 이 본의 키를 모두 지우고, '원본' 상태(또는 그 레이어에 키가 없을 때)면 전체 구간 '본별 보정'(회전·위치·크기)을 지웁니다.\n"
                                        "손·발을 끌어 옮긴 경우 위/아래 팔다리에 기록된 값도 함께 되돌립니다.")
        self.btn_bone_reset.clicked.connect(self._bone_reset_selected)
        self.btn_bone_reset.setEnabled(False)
        bl2.addWidget(self.btn_bone_reset)
        self.btn_place_reset = QPushButton("배치 초기화")
        self.btn_place_reset.setToolTip("끌어서 옮긴 위치·회전을 0으로 되돌립니다 (모델이 서 있는 자리에 모션 시작을 맞춤)")
        self.btn_place_reset.clicked.connect(self._place_reset)
        bl2.addWidget(self.btn_place_reset)
        bl2.addWidget(HelpIcon("배치 초기화 ≠ 본 초기화\n"
                              "· 이 본 초기화: 고른 '본 하나'를 손본 것(회전·위치·크기, 레이어 키)을 되돌립니다.\n"
                              "· 배치 초기화: 모션 '전체'를 바닥에서 옮기거나 돌린 값(Shift+드래그 이동, Ctrl+드래그 회전, 루트 회전)을 0으로 되돌립니다.\n"
                              "본 수정은 그대로 두고 자리/방향만 처음으로 돌리고 싶을 때 씁니다."))
        # 본 편집 도구(좌표계·포지션 분리·오토키·초기화)는 뷰포트 안 아래쪽에 겹쳐 둔다 — 뷰포트를 보면서 바로 누를 수 있게
        self.vp_tools = ViewportOverlay(self.viewport)
        self.vp_tools.add_row(bl)
        self.vp_tools.add_row(bl2)
        self.vp_tools.finish()
        self.bone_tree = QTreeWidget()
        self.bone_tree.setHeaderHidden(True)
        self.bone_tree.setMinimumHeight(60)
        self.bone_tree.setToolTip("몸 부위(파츠) 목록입니다. 클릭하면 그 본이 선택되고, 뷰포트에서 노란 원으로 표시돼요. ● 는 회전 보정이 걸린 본.")
        self.bone_tree.itemClicked.connect(self._bone_tree_clicked)
        self.bone_tree.hide()
        # 파츠 목록: 제목 줄의 ▼/▶ 로 접고 펴고, 아래 손잡이로 높이를 조절
        self.bone_tree_box = QWidget()
        btl = QVBoxLayout(self.bone_tree_box)
        btl.setContentsMargins(0, 0, 0, 0)
        btl.setSpacing(0)
        self.btn_tree_fold = QPushButton("\u25bc 파츠 목록")
        self.btn_tree_fold.setFlat(True)
        self.btn_tree_fold.setToolTip("눌러서 파츠 목록을 접거나 폅니다")
        self.btn_tree_fold.setStyleSheet("QPushButton{text-align:left;font-weight:700;border:none;padding:2px 4px}")
        self.btn_tree_fold.clicked.connect(lambda *_a: self._set_tree_open(not bool(self.cfg.get("bone_tree_open", True))))
        btl.addWidget(self.btn_tree_fold)
        btl.addWidget(Resizable(self.bone_tree, "bone_tree_h", 60))
        self.bone_tree_box.setVisible(False)
        rl.addWidget(self.bone_tree_box)
        self.viewport.bone_picked.connect(self._bone_picked)
        self.viewport.bone_ctrl_clicked.connect(self._bone_ctrl_clicked)
        self.viewport.bone_dragged.connect(self._bone_dragged)
        self.viewport.bone_drag_started.connect(self._bone_drag_started)
        self.viewport.bone_moved.connect(self._bone_moved)
        self.viewport.bone_scaled.connect(self._bone_scaled)
        pl = QHBoxLayout()
        pl.addWidget(QLabel("왼쪽 드래그"))
        self.drag_combo = QComboBox()
        for k, n in (("camera", "카메라 회전"), ("move", "애니메이션 이동"), ("rotate", "애니메이션 회전")):
            self.drag_combo.addItem(n, k)
        self.drag_combo.setToolTip("애니메이션 이동/회전: 바닥 위에서 모션 전체를 끌어 위치·방향을 맞춥니다.\n어느 모드든 Shift+드래그=이동, Ctrl+드래그=회전은 항상 됩니다.")
        self.drag_combo.currentIndexChanged.connect(lambda *_a: setattr(self.viewport, "drag_mode", self.drag_combo.currentData() or "camera"))
        pl.addWidget(self.drag_combo)
        self.place_label = QLabel("")
        self.place_label.setStyleSheet("color:#8a93a6")
        pl.addWidget(self.place_label, 1)
        rl.addLayout(pl)
        self.viewport.placement_dragged.connect(self._placement_dragged)
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
        self.retime_bar = RetimeBar(self)
        rl.addWidget(self.retime_bar)
        self.curve_editor = CurveEditor(self)
        rl.addWidget(self.curve_editor)
        split.addWidget(right)
        # 기본 비율: 왼쪽(탭) 47% : 오른쪽(뷰포트) 53%. 창 크기를 바꿔도 이 비율을 유지하고, 경계를 끌어 바꾸면 그 비율을 기억한다.
        self._split = split
        ratio = self.cfg.get("split_ratio", 0.47)
        self._split_ratio = min(max(float(ratio), 0.25), 0.70) if isinstance(ratio, (int, float)) else 0.47
        split.setStretchFactor(0, 47)
        split.setStretchFactor(1, 53)
        split.setSizes([int(1000 * self._split_ratio), int(1000 * (1 - self._split_ratio))])
        self._split_applied = False
        moved = getattr(split, "splitterMoved", None)
        if moved is not None and hasattr(moved, "connect"):
            moved.connect(self._split_moved)

        # ---- 최상단: [AnimRef] [MotionBridge] — 전체 화면을 고른다. 시작은 AnimRef.
        root = QWidget()
        rv = QVBoxLayout(root)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(0)
        bar = QHBoxLayout()
        bar.setContentsMargins(8, 6, 8, 0)
        bar.setSpacing(4)
        self.mode_btns: Dict[str, QPushButton] = {}
        for key, text, tip in (("animref", "AnimRef", "게임 영상에서 모션 클립을 모으고 정리합니다"),
                               ("motionbridge", "MotionBridge", "추출 · 영상찾기 · 매핑 · 리타겟 · 검수 · 내보내기")):
            b_ = QPushButton(text)
            b_.setCheckable(True)
            b_.setToolTip(tip)
            b_.setMinimumHeight(34)
            b_.setMinimumWidth(130)
            b_.setStyleSheet("QPushButton{font-size:14px;font-weight:600;padding:4px 18px;border:1px solid #3a4050;border-bottom:none;"
                             "border-top-left-radius:8px;border-top-right-radius:8px;background:#262b36;color:#9aa3b5}"
                             "QPushButton:checked{background:#3b4a6b;color:#ffffff}")
            b_.clicked.connect(lambda _c=False, k=key: self.set_mode(k))
            bar.addWidget(b_)
            self.mode_btns[key] = b_
        bar.addStretch(1)
        rv.addLayout(bar)
        self.mode_stack = QStackedWidget()
        self.mode_stack.addWidget(self.animref)
        self.mode_stack.addWidget(split)
        rv.addWidget(self.mode_stack, 1)
        self.setCentralWidget(root)
        self.set_mode("animref")

    MODES = ("animref", "motionbridge")

    def set_mode(self, key: str) -> None:
        """최상단 선택: 'animref' 또는 'motionbridge'."""
        if key not in self.MODES:
            return
        self.mode_stack.setCurrentIndex(self.MODES.index(key))
        for k, b_ in self.mode_btns.items():
            b_.setChecked(k == key)
        if key == "motionbridge" and hasattr(self, "_split") and not getattr(self, "_split_applied", True):
            self._apply_split_ratio()
            self._split_applied = True

    def mode(self) -> str:
        return self.MODES[self.mode_stack.currentIndex()]

    def _apply_split_ratio(self) -> None:
        w = max(int(self._split.width()), 200)
        self._split.setSizes([int(w * self._split_ratio), w - int(w * self._split_ratio)])

    def _vsplit_moved(self, *_a) -> None:
        a, b = self.vsplit.sizes()[:2]
        if a + b > 0 and b > 0:
            self.cfg["vsplit_bottom"] = round(min(max(b / (a + b), 0.10), 0.85), 3)

    def set_clip_panel_visible(self, on: bool) -> None:
        cp = getattr(self, "clip_panel", None)
        if cp is None:
            return
        cp.setVisible(bool(on))
        try:
            self.btn_clip_fold.setText("\u25c0" if on else "\u25b6")
        except Exception:  # noqa: BLE001
            pass
        self.cfg["clip_panel_hidden"] = (not on)
        bridge.save_config(self.cfg)

    def toggle_clip_panel(self) -> None:
        cp = getattr(self, "clip_panel", None)
        if cp is None:
            return
        vis = self.cfg.get("clip_panel_hidden")       # 숨겨져 있으면 펴고, 아니면 접는다
        self.set_clip_panel_visible(bool(vis))

    def toggle_theme(self) -> None:
        new = "light" if _theme.mode() == "dark" else "dark"
        _theme.apply_theme(QApplication.instance(), new)
        self.cfg["theme"] = new
        bridge.save_config(self.cfg)
        self.statusBar().showMessage("밝은 테마" if new == "light" else "어두운 테마")

    def _left_split_moved(self, *_a) -> None:
        a = self._left_split.sizes()[0]
        if a > 0:
            self.cfg["clip_panel_w"] = int(a)

    def _split_moved(self, *_a) -> None:
        if not getattr(self, "_split_applied", False):
            return
        a, b = self._split.sizes()[:2]
        if a + b > 0:
            self._split_ratio = min(max(a / (a + b), 0.25), 0.70)
            self.cfg["split_ratio"] = round(self._split_ratio, 3)

    def showEvent(self, ev) -> None:  # noqa: N802
        super().showEvent(ev)
        if not getattr(self, "_split_applied", True) and hasattr(self, "_split"):
            self._apply_split_ratio()  # 처음 보일 때 실제 창 너비에 비율을 맞춘다
            self._split_applied = True

    # ------------------------------------------------------------------ 탭 0 (게임에서 추출)
    def _tab_extract(self) -> QWidget:
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        w = QWidget()
        sc.setWidget(w)
        lay = QVBoxLayout(w)

        g = CollapsibleBox("1) 게임 폴더")
        v = QVBoxLayout(g)
        h = QHBoxLayout()
        self.ex_game = QLineEdit(self.cfg.get("game_dir", ""))
        self.ex_game.setPlaceholderText(r"예: D:\SteamLibrary\steamapps\common\Sparta")
        h.addWidget(self.ex_game, 1)
        b = QPushButton("찾아보기…")
        b.clicked.connect(self._pick_game)
        h.addWidget(b)
        self.ex_scan_btn = QPushButton("검사")
        self.ex_scan_btn.clicked.connect(self._scan_game)
        h.addWidget(self.ex_scan_btn)
        self.ex_game.textChanged.connect(lambda *_a: self._update_scan_gate())
        v.addLayout(h)
        h = QHBoxLayout()
        h.addWidget(QLabel("게임 이름"))
        self.ex_gname = QLineEdit(self.cfg.get("game_name", ""))
        self.ex_gname.setPlaceholderText("검사하면 자동으로 채워집니다 (예: Mortal Shell 2) — 추출 폴더 이름이 됩니다")
        self.ex_gname.setToolTip("추출 결과는 '출력 폴더 / 게임 이름 / 캐릭터 이름 / Animations 또는 Models' 에 정리됩니다")
        h.addWidget(self.ex_gname, 1)
        v.addLayout(h)
        self.ex_summary = QLabel("① 게임 설치 폴더를 고르고 ② 주황색 [검사]를 먼저 누르세요. 암호화 여부를 확인하고, 그 뒤에 아래 설정이 켜집니다.")
        self.ex_summary.setWordWrap(True)
        v.addWidget(self.ex_summary)
        # 추출 가능 여부 3단계 판정 (🟢 가능 / 🟡 조건부 / 🔴 불가) + 불가일 때 영상 쪽으로 가는 버튼
        vh = QHBoxLayout()
        self.ex_verdict = QLabel("")
        self.ex_verdict.setWordWrap(True)
        vh.addWidget(self.ex_verdict, 1)
        self.btn_to_animref = QPushButton("AnimRef / AI 모캡으로 가기")
        self.btn_to_animref.setToolTip("직접 추출이 안 되는 게임은 플레이 영상에서 모션을 얻습니다.")
        self.btn_to_animref.clicked.connect(lambda *_a: self.set_mode("animref"))
        self.btn_to_animref.setVisible(False)
        vh.addWidget(self.btn_to_animref)
        v.addLayout(vh)
        lay.addWidget(g)

        g = CollapsibleBox("2) 추출 설정")
        self._scan_gated = [g]
        f = QFormLayout(g)
        self.ex_aes = QLineEdit()
        self.ex_aes.setPlaceholderText("검사 후, 암호화된 게임일 때만 입력")
        self.ex_aes.setEnabled(False)
        ah = QHBoxLayout()
        ah.addWidget(self.ex_aes, 1)
        self.btn_keys = QPushButton("키 후보 검증…")
        self.btn_keys.setToolTip("인터넷에서 찾은 글(키가 여러 개 섞여 있어도 됨)을 통째로 붙여 넣으면, 키처럼 생긴 것을 모두 뽑아 이 게임에 하나씩 넣어 보고 실제로 열리는 키만 채워 줍니다.")
        self.btn_keys.clicked.connect(lambda *_a: self._verify_key_candidates())
        ah.addWidget(self.btn_keys)
        f.addRow("AES 키", ah)
        self.ex_ue = QComboBox()
        for v_ in gamescan.merge_versions([]):
            self.ex_ue.addItem(v_, v_)
        self.ex_ue.setCurrentIndex(max(self.ex_ue.findData(self.cfg.get("ue", "GAME_UE5_4")), 0))
        self.ex_ue.setToolTip("정확한 버전을 모르면 최신부터 시도하세요. 안 읽히면 다른 버전으로 바꿔 다시 검색합니다.")
        try:
            self.ex_ue.setEditable(True)       # 목록이 길어서(게임 전용 항목 수백 개) 글자를 쳐서 찾을 수 있게
            self.ex_ue.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        except Exception:  # noqa: BLE001
            pass
        f.addRow("UE 버전", with_help(self.ex_ue,
                                    "게임이 만들어진 언리얼 버전입니다. 게임 폴더를 검사하면 실행 파일 버전을 읽어 자동으로 골라 줍니다.\n"
                                    "목록 아래쪽에는 '게임 전용 항목'(GAME_BlackMythWukong 등)이 있습니다. 엔진을 고쳐 쓴 게임은 일반 버전으로는 안 열리고 전용 항목을 골라야 합니다 — "
                                    "검사할 때 폴더 이름으로 전용 항목을 찾으면 자동으로 고릅니다. 칸에 글자를 쳐서 찾을 수도 있습니다 (예: wukong)."))
        self.ex_links = QLineEdit()
        self.ex_links.setPlaceholderText("이 게임의 키/매핑을 구한 곳 주소나 메모 (선택) — 게임과 함께 기억됩니다")
        f.addRow("참고 링크·메모", self.ex_links)
        self.ex_share = QLineEdit(self.cfg.get("preset_share_dir", ""))
        self.ex_share.setPlaceholderText("(선택) 팀 공유 폴더 — 여기에 game_presets.json 을 같이 써서 팀원과 게임 설정을 공유")
        self.ex_share.textChanged.connect(lambda *_a: (self.cfg.__setitem__("preset_share_dir", self.ex_share.text().strip()), bridge.save_config(self.cfg)))
        f.addRow("공유 폴더", self.ex_share)
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

        g = CollapsibleBox("3) 에셋 찾기 / 추출")
        self._scan_gated.append(g)
        v = QVBoxLayout(g)
        h = QHBoxLayout()
        h.addWidget(QLabel("종류"))
        self.ex_kind = QComboBox()
        for label, key in (("애니메이션 (PSA)", "anim"), ("메시 (FBX / OBJ / GLB 로 변환)", "mesh")):
            self.ex_kind.addItem(label, key)
        self.ex_kind.currentIndexChanged.connect(self._kind_changed)
        h.addWidget(self.ex_kind, 1)
        h.addWidget(HelpIcon(EXTRACT_SEARCH_TIPS))
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
        self.ex_filter.setClearButtonEnabled(True)
        self.ex_filter.setToolTip("에셋 경로에 대한 정규식. 예: (?i)(walk|run|attack)\n쉼표(,)로 나누면 모두 포함하는 것만 찾습니다. 예: player,attack (대소문자 무시)")
        h.addWidget(self.ex_filter, 1)
        b = QPushButton("검색")
        b.clicked.connect(self._search_anims)
        h.addWidget(b)
        h.addWidget(HelpIcon("에셋 경로에서 찾을 글자입니다 (정규식 가능). 쉼표로 나누면 모두 들어간 것만 찾습니다.\n"
                             "어떤 글자로 찾으면 좋은지는 '종류' 줄 오른쪽 ? 를 보세요."))
        v.addLayout(h)
        self.ex_list = PathTree(strip_ext=True)  # 폴더 구조대로 접고 펼치는 트리
        self.ex_list.setMinimumHeight(110)
        self.ex_list.itemSelectionChanged.connect(self._update_ex_count)
        self.ex_list.enable_asset_drag(lambda: "anim" if self.ex_kind.currentData() == "anim" else "mesh")  # 뷰포트로 끌어다 놓으면 바로 추출해 연다
        self.ex_list.setToolTip("파일을 골라 오른쪽 뷰포트로 끌어다 놓으면 바로 추출해서 엽니다")
        v.addWidget(Resizable(self.ex_list, "ex_list_h", 110))
        self.ex_count = QLabel("")
        self.ex_count.setWordWrap(True)
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
        self.ex_exist = self._make_exist_combo()
        self.ex_exist.setToolTip("추출한 파일이 이미 있을 때의 처리입니다. 기본은 같은 이름의 파일을 덮어씁니다 (이름_old_날짜 같은 중복 파일을 만들지 않습니다).")
        v.addWidget(self.ex_exist)
        lb = QLabel(safesave.EXPLAIN[safesave.DEFAULT])
        lb.setWordWrap(True)
        lb.setStyleSheet("color:#8a93a6")
        self.__dict__.setdefault("_exist_labels", []).append(lb)
        v.addWidget(lb)
        self.btn_extract = QPushButton("선택한 애니메이션 추출 (PSA)")
        self._kind_changed()
        self.btn_extract.setMinimumHeight(34)
        self.btn_extract.clicked.connect(self._extract_selected)
        v.addWidget(self.btn_extract)
        lay.addWidget(g)

        g = CollapsibleBox("3-2) 에셋 찾기 · 캐릭터/스켈레톤 보기 (재생 가능한 애니메이션만)")
        self._scan_gated.append(g)
        v = QVBoxLayout(g)
        self.char_panel = CharAssetPanel(self)
        v.addWidget(self.char_panel)
        lay.addWidget(g)

        g = CollapsibleBox("4) 추출된 파일 열기")
        v = QVBoxLayout(g)
        self.ex_dir_label = QLabel("")
        self.ex_dir_label.setWordWrap(True)
        self.ex_dir_label.setTextFormat(Qt.TextFormat.RichText)
        self.ex_dir_label.setToolTip("클릭하면 탐색기에서 이 폴더가 열립니다")
        self.ex_dir_label.linkActivated.connect(lambda *_a: self._open_out_folder())
        v.addWidget(self.ex_dir_label)
        self.ex_files = PathTree(tag_kinds=True)  # [모델링 · 리깅 포함] / [애니메이션] 같은 이름표를 붙인다
        self.ex_files.on_set = self.note_extracted  # 추출하면 그 폴더를 기억 -> 모델/모션 열기 창이 거기서 시작
        self.ex_files.setMinimumHeight(60)
        self.ex_files.itemDoubleClicked.connect(self._ex_files_double_clicked)
        v.addWidget(Resizable(self.ex_files, "ex_files_h", 60))
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
        b = QPushButton("📚 같은 종류 모션 모으기 (라이브러리 폴더 만들기)")
        b.setToolTip("추출된 모션(PSA 등)을 '종류/동작' 폴더로 복사해 모읍니다 — attack 은 attack 끼리, run 은 run 끼리.\n"
                     "예) 출력 폴더/Library/attack/Plow/…psa , Library/locomotion/Run/…psa\n"
                     "목록에서 고른 파일(안 골랐으면 목록 전체, 목록이 비었으면 출력 폴더 안의 모든 모션)을 모으고, 원본은 그대로 둡니다.\n"
                     "종류·동작은 파일 이름과 폴더 구조에서 읽습니다. 이름이 겹치고 내용이 다르면 캐릭터 이름을 붙여 둘 다 남깁니다.")
        b.clicked.connect(self._collect_extracted)
        v.addWidget(b)
        b = QPushButton("선택한 GLB 를 타깃 모델로 열기")
        b.setToolTip("게임에서 뽑은 메시(GLB)를 타깃(내 모델) 자리에 불러옵니다. 소스 모션과 같은 캐릭터면 그대로 확인할 수 있습니다.")
        b.clicked.connect(self._open_extracted_as_target)
        v.addWidget(b)
        self.btn_convert = QPushButton("이미 뽑은 메시를 FBX/OBJ/GLB 로 변환")
        self.btn_convert.setToolTip("3) 에서 고른 형식/리깅/대상 프로그램 설정으로 변환합니다. 이미 뽑아 둔 PSKX 도 변환할 수 있습니다.")
        self.btn_convert.clicked.connect(self._convert_selected_meshes)
        v.addWidget(with_help(self.btn_convert,
                              "왜 있나요?  '3) 에셋 찾기 / 추출'에서 메시를 뽑을 때는 그 자리에서 곧바로 FBX/OBJ/GLB 로 변환되어 저장됩니다.\n"
                              "이 버튼은 그때 변환하지 않고 PSK/PSKX(언리얼 원본 형식)로만 뽑아 두었거나, 다른 때 받아 둔 메시를 나중에 3ds Max·Maya 용으로 바꾸고 싶을 때 씁니다.\n"
                              "위 목록에서 메시 파일을 고르고 누르면, 3) 에서 정해 둔 형식·리깅·대상 프로그램으로 변환합니다.\n"
                              "그런 경우가 없다면 쓰지 않아도 됩니다."))
        lay.addWidget(g)
        self.ex_out.textChanged.connect(self._update_dir_label)
        self._update_dir_label()
        lay.addStretch(1)
        self._update_scan_gate()
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
        self.ex_dir_label.setText('출력 폴더: <a href="open">%s</a> (클릭하면 탐색기로 열림)' % wrappable(d).replace("&", "&amp;").replace("<", "&lt;") if d else "")

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
            self.ex_helper_label.setText(wrappable(p))
            self.ex_helper_label.setStyleSheet("")
        else:
            self.ex_helper_label.setText("찾지 못했습니다. tools/mbextract 를 빌드하거나 경로를 지정하세요 (README 참고).")
            self.ex_helper_label.setStyleSheet("color:#e0a050")

    SCAN_BTN_TODO = ("QPushButton{background:#ff7a00;color:white;font-weight:bold;font-size:14px;"
                     "padding:6px 18px;border:2px solid #ffb066;border-radius:6px;}"
                     "QPushButton:hover{background:#ff9230;}")
    SCAN_BTN_DONE = ("QPushButton{background:#2e8b57;color:white;font-weight:bold;padding:4px 12px;"
                     "border-radius:6px;}QPushButton:hover{background:#3aa66a;}")

    def _scan_ok(self) -> bool:
        """현재 게임 폴더 칸의 경로가 이미 검사를 마친 폴더인가."""
        sc = self.__dict__.get("scan")
        return bool(sc is not None and sc.found and sc.root == self.ex_game.text().strip())

    def _update_scan_gate(self) -> None:
        """검사를 마치기 전에는 2)·3) 설정과 에셋 찾기를 잠그고, [검사] 버튼을 눈에 띄게 칠한다."""
        ok = self._scan_ok()
        for box in self.__dict__.get("_scan_gated", []):
            box.setEnabled(ok)
            box.setToolTip("" if ok else "먼저 위의 [검사] 버튼을 눌러야 쓸 수 있습니다.")
        b = self.__dict__.get("ex_scan_btn")
        if b is not None:
            if ok:
                b.setText("✔ 검사 완료 (다시 검사)")
                b.setStyleSheet(self.SCAN_BTN_DONE)
                b.setToolTip("이 게임 폴더는 검사를 마쳤습니다. 폴더가 바뀌었으면 다시 누르세요.")
            else:
                b.setText("▶ 검사 (먼저 누르세요)")
                b.setStyleSheet(self.SCAN_BTN_TODO)
                b.setToolTip("게임 폴더를 검사해야 아래 설정과 추출이 켜집니다.")

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
            auto = layout.detect_game_name(sc.root)
            if not self.ex_gname.text().strip() or self.ex_gname.text().strip() == self.cfg.get("game_name_auto", ""):
                self.ex_gname.setText(auto)  # 직접 고친 이름은 덮어쓰지 않는다
            self.cfg["game_name_auto"] = auto
            bridge.save_config(self.cfg)
        try:
            self._apply_game_preset(sc)
        except Exception as e:  # noqa: BLE001 - 자동 채우기 실패가 검사를 막지 않게
            self.statusBar().showMessage("게임 설정 자동 채우기 실패: %s" % e)
        self._update_scan_gate()

    def _verify_key_candidates(self, text: Optional[str] = None) -> None:
        """붙여 넣은 글에서 AES 키 후보를 뽑아 실제로 열리는 키를 찾아 채운다."""
        game = self.ex_game.text().strip()
        helper = extractor.find_helper(self.cfg.get("helper"))
        if not game or not helper:
            QMessageBox.information(self, "키 후보 검증", "게임 폴더를 먼저 고르고 검사하세요." if helper else "추출 도우미(mbextract)를 찾지 못했습니다.")
            return
        if text is None:
            text, ok = QInputDialog.getMultiLineText(self, "키 후보 검증", "AES 키가 들어 있는 글을 통째로 붙여 넣으세요 (여러 개여도 됩니다):", self.ex_aes.text())
            if not ok:
                return
        cands = gamescan.extract_key_candidates(text)
        if not cands:
            QMessageBox.information(self, "키 후보 검증", "붙여 넣은 글에서 AES 키(64자리 16진수)를 찾지 못했습니다.")
            return
        ue = self.ex_ue.currentData() or "GAME_UE5_4"
        usmap = self.ex_usmap.text().strip() or None

        def done(res) -> None:
            key, tried = res
            if key:
                self.ex_aes.setEnabled(True)
                self.ex_aes.setText(key)
                self.statusBar().showMessage("후보 %d개 중 맞는 키를 찾아 채웠습니다 (%d번째)" % (len(cands), len(tried)))
            else:
                why = (tried[-1].get("message") or "") if tried else ""
                QMessageBox.warning(self, "키 후보 검증", "후보 %d개를 모두 넣어 봤지만 이 게임을 여는 키가 없습니다.\n"
                                    "UE 버전(게임 전용 항목)이 틀려도 이렇게 나올 수 있습니다.\n\n마지막 결과: %s" % (len(cands), why))

        self.run_async(lambda w: extractor.pick_working_key(helper, game, ue, usmap, cands), done, "키 후보 %d개 확인 중" % len(cands))

    def _sync_ue_list(self) -> list:
        """UE 버전 목록을 추출 도우미가 실제로 지원하는 전체 목록과 맞춘다 (고른 값은 유지)."""
        games = extractor.list_games(extractor.find_helper(self.cfg.get("helper")))
        want = gamescan.merge_versions(games)
        have = [self.ex_ue.itemData(i) for i in range(self.ex_ue.count())]
        if want != have:
            cur = self.ex_ue.currentData() or self.cfg.get("ue", "GAME_UE5_4")
            self.ex_ue.blockSignals(True)
            self.ex_ue.clear()
            for v_ in want:
                self.ex_ue.addItem(v_, v_)
            self.ex_ue.setCurrentIndex(max(self.ex_ue.findData(cur), 0))
            self.ex_ue.blockSignals(False)
        return games or want

    def _set_ue(self, name: str) -> bool:
        i = self.ex_ue.findData(name)
        if i >= 0:
            self.ex_ue.setCurrentIndex(i)
        return i >= 0

    def _apply_game_preset(self, sc) -> None:
        """검사 직후: 예전에 쓴 설정(AES 키·UE 버전·usmap·링크)을 자동으로 채우고, 처음 보는 게임이면 UE 버전을 추천하고, 3단계 판정을 보여 준다."""
        games = self._sync_ue_list()
        share = self.cfg.get("preset_share_dir", "") or None
        pre = gamepresets.lookup(sc, share) if sc.found or sc.engine else None
        notes = []
        if pre:
            if pre.get("ue") and self._set_ue(pre["ue"]):
                notes.append("UE 버전 " + pre["ue"])
            if pre.get("aes") and sc.encrypted:
                self.ex_aes.setText(pre["aes"])
                notes.append("AES 키")
            if pre.get("usmap") and os.path.isfile(pre["usmap"]):
                self.ex_usmap.setText(pre["usmap"])
                notes.append("매핑 파일")
            if pre.get("links") or pre.get("note"):
                self.ex_links.setText(" ".join(pre.get("links") or []) + ((" " + pre["note"]) if pre.get("note") else ""))
        else:
            self.ex_aes.setText("")
            self.ex_links.setText("")
            name, why = gamescan.suggest_ue(sc, games, self.ex_gname.text())
            if name and self._set_ue(name):
                notes.append("UE 버전 %s (%s)" % (name, why))
        cur_um = self.ex_usmap.text().strip()
        if sc.found and sc.is_unreal5 and (not cur_um or not os.path.isfile(cur_um) or (not pre and self.cfg.get("usmap_game") != sc.root)):
            um = gamepresets.find_usmap_near(sc, [share] if share else None)     # 게임 폴더·공유 폴더에 놓아 둔 .usmap
            if um:
                self.ex_usmap.setText(um)
                notes.append("매핑 파일(%s)" % os.path.basename(um))
            elif not pre and self.cfg.get("usmap_game") not in (None, "", sc.root):
                self.ex_usmap.setText("")          # 다른 게임의 매핑 파일이 남아 있으면 비운다 (맞지 않는 매핑은 오류의 원인)
        self.cfg["usmap_game"] = sc.root
        has_key = False
        try:
            has_key = bool(gamescan.normalize_aes_key(self.ex_aes.text()))
        except ValueError:
            pass
        um = self.ex_usmap.text().strip()
        state, title, desc = gamescan.verdict(sc, has_key, bool(um and os.path.isfile(um)), pre)
        icon = {"green": "🟢", "yellow": "🟡", "red": "🔴"}[state]
        color = {"green": "#5ab0ff", "yellow": "#e0a050", "red": "#e0a050"}[state]
        head = ("예전 설정을 불러왔습니다: " if pre else "자동으로 맞춘 것: ") + ", ".join(notes) if notes else ""
        eng = {"unreal": "언리얼", "unity": "유니티", "re": "RE 엔진", "godot": "고도"}.get(sc.engine, "알 수 없음")
        facts = "엔진: %s%s%s" % (eng, (" · 실행 파일 버전 " + sc.exe_version) if sc.exe_version else "", (" · 스팀 AppID " + sc.appid) if sc.appid else "")
        self.ex_verdict.setText("%s %s — %s\n%s%s" % (icon, title, desc, facts, ("\n" + head) if head else ""))
        self.ex_verdict.setStyleSheet("color:%s;font-weight:700" % color)
        self.btn_to_animref.setVisible(state == "red")
        self._verdict_state = state

    def _remember_game(self, status: str = "ok", reason: str = "") -> None:
        """추출/검색이 실제로 성공한 설정을 게임과 함께 기억한다 (다음에 같은 게임 폴더를 고르면 자동으로 채움)."""
        sc = self.__dict__.get("scan")
        if sc is None or not sc.found:
            return
        try:
            aes = gamescan.normalize_aes_key(self.ex_aes.text()) if sc.encrypted else ""
        except ValueError:
            aes = ""
        txt = self.ex_links.text().strip()
        links = [w for w in txt.split() if w.lower().startswith(("http://", "https://"))]
        note = " ".join(w for w in txt.split() if w not in links)
        try:
            gamepresets.remember(sc, self.cfg.get("preset_share_dir", "") or None, name=self.ex_gname.text().strip(),
                                 ue=self.ex_ue.currentData() or "", aes=aes, usmap=self.ex_usmap.text().strip(),
                                 links=links, note=note, status=status, reason=reason)
        except Exception as e:  # noqa: BLE001
            self.statusBar().showMessage("게임 설정 저장 실패: %s" % e)

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

    # ---- 같은 이름의 파일이 이미 있을 때 (추출·내보내기 공통 설정)
    def _make_exist_combo(self) -> QComboBox:
        cb = QComboBox()
        for k in safesave.CHOICES:
            cb.addItem(safesave.LABELS[k], k)
        cb.setCurrentIndex(max(cb.findData(safesave.choice(self.cfg.get("on_exist"))), 0))
        cb.currentIndexChanged.connect(lambda *_a, _cb=cb: self._exist_changed(_cb))
        self.__dict__.setdefault("_exist_combos", []).append(cb)
        return cb

    def _exist_policy(self) -> str:
        cbs = self.__dict__.get("_exist_combos") or []
        return safesave.choice(cbs[0].currentData() if cbs else self.cfg.get("on_exist"))

    def _exist_changed(self, src=None) -> None:
        cbs = self.__dict__.get("_exist_combos", [])
        pol = safesave.choice((src or cbs[0]).currentData())      # 바꾼 콤보의 값이 기준
        for cb in cbs:                                          # 다른 쪽도 같이 (하나의 설정)
            if cb is not src and cb.currentData() != pol:
                cb.blockSignals(True)
                cb.setCurrentIndex(max(cb.findData(pol), 0))
                cb.blockSignals(False)
        self.cfg["on_exist"] = pol
        bridge.save_config(self.cfg)
        self._update_exist_texts()

    def _update_exist_texts(self) -> None:
        pol = self._exist_policy()
        for lb in self.__dict__.get("_exist_labels", []):
            lb.setText(safesave.EXPLAIN[pol])
        if getattr(self, "btn_export", None) is not None:
            self.btn_export.setText("내보내기 (%s)" % safesave.SHORT[pol])

    def make_layout(self, force_char: Optional[str] = None) -> "layout.Layout":
        """추출 폴더 규칙: 출력 폴더 / 게임 이름 / 캐릭터 이름 / Animations 또는 Models."""
        name = self.ex_gname.text().strip() or layout.detect_game_name(self.ex_game.text().strip())
        self.cfg["game_name"] = name
        lay = layout.Layout(self.ex_out.text().strip(), name, force_char=force_char, on_exist=self._exist_policy())
        self._last_layout = lay
        return lay

    def _search_anims(self) -> None:
        a = self._extract_args()
        if a is None:
            return
        pattern = textmatch.and_regex(self.ex_filter.text().strip() or self.KIND_FILTERS[self.ex_kind.currentData() or "anim"])  # 쉼표 = 모두 포함

        self._ex_logs = []

        def on_event(e, w=None):
            if e.get("type") == "log":
                self._ex_logs.append(e.get("message", ""))

        def job(w: Worker):
            return extractor.list_assets(*a, pattern, 5000, on_event)

        self.run_async(job, self._after_search, "에셋 검색 중")

    def _after_search(self, paths) -> None:
        self.ex_list.set_paths(paths)
        if paths:
            self._remember_game("ok")       # 실제로 열린 설정만 기억한다
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
        self._extract_paths(paths)

    def _asset_dropped_from(self, origin: str, kind: str, paths) -> None:
        """출처가 있는 에셋을 뷰포트에 놓았을 때. 영상찾기 목록에서 온 것은 영상찾기가 직접 열고, 탭은 옮기지 않는다."""
        if origin == "vidfind":
            self.vidfind.open_dropped(kind, list(paths))
        else:
            self.extract_for_char(paths, kind)

    def extract_for_char(self, paths, kind: str) -> None:
        """캐릭터 보기에서 고른 에셋을 바로 추출하고 뷰포트에 연다 (애니메이션 = PSA, 메쉬 = 타깃으로 열 GLB 포함)."""
        i = self.ex_kind.findData("anim" if kind == "anim" else "mesh")
        if i >= 0:
            self.ex_kind.setCurrentIndex(i)
        if kind == "mesh" and not self.ex_glb.isChecked():
            self.ex_glb.setChecked(True)  # 타깃으로 열려면 GLB 가 필요
        self._extract_paths(list(paths))

    def set_extract_list(self, paths, kind: str) -> None:
        i = self.ex_kind.findData("anim" if kind == "anim" else "mesh")
        if i >= 0:
            self.ex_kind.setCurrentIndex(i)
        self.ex_list.set_paths(list(paths))
        self.ex_list.selectAll()
        self._update_ex_count()
        self.statusBar().showMessage("추출 목록에 %d개를 올렸습니다 — 위 [선택한 … 추출]을 누르세요" % len(paths))

    def _extract_paths(self, paths) -> None:
        a = self._extract_args()
        if a is None:
            return
        out = self.ex_out.text().strip()
        if not out:
            return

        self._ex_skips = []
        lay = self.make_layout()
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
                return extractor.export_meshes(*a, paths, out, fmts, variants, preset, self.pr.blender, tex, keep_raw, ev, self._ex_skips.append, lay=lay)
            return extractor.export_assets(*a, paths, out, ev, kind="anim", lay=lay)

        self.run_async(job, self._after_extract, "추출 중")

    def extract_to_library(self, paths) -> None:
        """에셋 찾기에서 고른 애니메이션을 추출하고, 끝나면 종류별 라이브러리 폴더로도 모은다."""
        self._collect_after = True
        i = self.ex_kind.findData("anim")
        if i >= 0:
            self.ex_kind.setCurrentIndex(i)
        self._extract_paths(list(paths))

    def _after_extract(self, files) -> None:
        collect = bool(self.__dict__.pop("_collect_after", False))
        self.ex_files.set_paths(files)
        if collect and files:
            rep = self.collect_motions([f for f in files if f.lower().endswith(motionlib.ANIM_EXT)])
            self.log_collect = rep
        if not files:
            why = [x for x in getattr(self, "_ex_skips", []) if x][:6]
            self.statusBar().showMessage("추출된 파일이 없습니다")
            QMessageBox.warning(self, "추출", "추출된 파일이 없습니다. 화면의 모션은 바뀌지 않습니다.\n\n" + ("\n".join(why) if why else "도우미가 이유를 알려주지 않았습니다. 종류(애니메이션/메시)와 선택한 에셋을 확인하세요."))
            return
        msg = "추출 완료: %d개 — 위 '출력 폴더' 링크를 누르면 폴더가 열립니다" % len(files)
        kept = list(getattr(getattr(self, "_last_layout", None), "backups", []) or [])
        if kept:
            msg += " · 같은 이름의 이전 파일 %d개를 보관했습니다" % len(kept)
        # 방금 추출한 모션/메시를 바로 화면에 보여준다 (여러 개면 첫 번째. 나머지는 아래 목록에서 더블클릭)
        psa = next((f for f in files if f.lower().endswith(".psa")), None)
        glb = next((f for f in files if f.lower().endswith(".glb") and "_static" not in f), None) or next((f for f in files if f.lower().endswith(".glb")), None)
        self.statusBar().showMessage(msg)
        if psa:
            # 방금 추출한 모션은 왼쪽 모션 목록에 더하고(이미 있으면 그 모션으로 이동), 바로 처음부터 재생한다
            psas = [f for f in files if f.lower().endswith(".psa")]
            self._add_source_files(psas, play=True, sync=True, prefix=msg)
        elif glb:
            self._open_path_as_target(glb)

    def library_dir(self) -> str:
        return os.path.join(self.ex_out.text().strip() or os.path.expanduser("~"), "Library")

    def collect_motions(self, files) -> "motionlib.Report":
        """모션 파일들을 종류별 라이브러리 폴더로 모은다 (복사). 결과를 상태줄에 알린다."""
        lib = self.library_dir()
        rep = motionlib.collect(list(files), lib)
        self.statusBar().showMessage("모션 라이브러리: %s — %s" % (lib, rep.summary()))
        return rep

    def _collect_extracted(self) -> None:
        files = [f for f in (self.ex_files.selected_paths() or self.ex_files.all_paths()) if f.lower().endswith(motionlib.ANIM_EXT)]
        if not files:
            root = self.ex_out.text().strip()
            files = motionlib.scan(root, self.library_dir()) if root and os.path.isdir(root) else []
        if not files:
            QMessageBox.information(self, "모션 모으기", "모을 모션 파일이 없습니다. 먼저 애니메이션을 추출하거나 '출력 폴더 내용 불러오기'를 누르세요.")
            return
        rep = self.collect_motions(files)
        lines = [rep.summary(), "", "폴더: " + rep.lib]
        if rep.skipped:
            lines += ["", "건너뜀 (앞 6개):"] + ["- %s — %s" % (os.path.basename(p), why) for p, why in rep.skipped[:6]]
        QMessageBox.information(self, "모션 모으기", "\n".join(lines))
        if rep.copied:
            osopen.open_in_explorer(rep.lib)

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
        self.run_async(lambda w: self.pr.load_target(path), lambda m: self._after_load("target"), "타깃 읽는 중")  # 열어도 탭은 그대로 (파일·매핑으로 자동 이동 안 함)

    def _open_path(self, path: str) -> None:
        self._remember_dir(path)
        self._add_source_files([path], play=True)

    def _add_source_files(self, paths, play: bool = False, sync: bool = False, prefix: str = "") -> None:
        """뷰포트에 소스 모션으로 여는 파일은 전부 왼쪽 모션 목록에 자동으로 더한다 (이미 열린 모션은 그대로 두고 뒤에 붙인다).
        이미 목록에 있는 파일이면 그 모션으로 이동한다. 뼈대가 다른 캐릭터의 모션도 그대로 목록에 올라가고, 고르면 소스 구조가 자동으로 바뀐다.
        play=True 면 열린 모션을 처음부터 바로 재생한다. sync=True 면 일하는 스레드 없이 바로 읽는다(다른 작업의 완료 콜백 안에서 쓸 때)."""
        paths = [p for p in paths if p]
        if not paths:
            return
        if sync:
            self._added_done(paths, self.pr.add_motions(paths), play, prefix)
            return
        self.run_async(lambda w: self.pr.add_motions(paths), lambda res: self._added_done(paths, res, play, prefix), "소스 읽는 중")

    def _added_done(self, paths, res, play: bool = False, prefix: str = "") -> None:
        added, skipped = res
        diff = []     # 본 구조가 달라도 이제 그대로 목록에 올라간다 (클립을 고르면 소스 구조가 자동 전환)
        hit = None
        if not added and self.pr.src is not None:          # 이미 목록에 있는 모션이면 새로 더하지 않고 그 모션으로 간다
            hit = next((self.pr.clip_of_path(p) for p in paths if self.pr.clip_of_path(p) is not None), None)
        bad = [x for x in skipped if "이미 목록에 있습니다" not in x]
        if not added and hit is None and bad and not diff:
            QMessageBox.warning(self, "소스 모션 열기", "열지 못했습니다:\n" + "\n".join(bad)[:700])
        self.clip_panel.added_ui(added, skipped, select_first=True, select_existing=hit)
        if prefix:
            self.statusBar().showMessage(prefix + " · " + self.__dict__.get("_last_status", ""))
        if play and (added or hit is not None) and self.pr.src is not None:
            self._play_from_start()

    def _play_from_start(self) -> None:
        """지금 모션을 처음 프레임부터 재생한다 (이미 재생 중이면 새 모션의 fps 로 다시 시작)."""
        self.frame_slider.setValue(0)
        if self.btn_play.isChecked():
            self._toggle_play(True)
        else:
            self.btn_play.setChecked(True)

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
        self.btn_unload_src = QPushButton("소스 빼기")
        self.btn_unload_src.setToolTip("열어 둔 소스 모션을 화면에서 뺍니다 (타깃 모델은 그대로)")
        self.btn_unload_src.clicked.connect(lambda *_a: self.unload_source())
        self.btn_unload_tgt = QPushButton("타깃 빼기")
        self.btn_unload_tgt.setToolTip("열어 둔 타깃 모델을 화면에서 뺍니다 (소스 모션은 그대로). 다른 모델로 바꿔 보기 전에 비울 때 쓰세요.")
        self.btn_unload_tgt.clicked.connect(lambda *_a: self.unload_target())
        row.addWidget(self.btn_unload_src)
        row.addWidget(self.btn_unload_tgt)
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
        b.setToolTip("매핑과 리타겟 설정을 이름 기준으로 저장 — 같은 리그에 다시 쓸 수 있습니다")
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

        g = CollapsibleBox("모션 맞춤 — 크기 · 위치 · 방향 (소스 모션을 내 모델에 맞춤)")
        f = QFormLayout(g)
        f.addRow(with_help(QLabel("여기서 바꾸는 것은 '리타겟 결과'입니다. 원본 파일(소스 모션·내 모델)은 건드리지 않습니다."),
                           "이 칸들은 소스(원본) 모션을 내 모델에 입힐 때 크기·위치·방향을 어떻게 맞출지 정합니다.\n"
                           "· 원본 모션 파일과 내 모델 파일은 바뀌지 않고, 결과(미리보기·내보내기)에만 적용됩니다.\n"
                           "· 모델의 크기를 바꾸는 것이 아니라, 모션의 이동 거리·발 위치를 모델 크기에 맞추는 것입니다."))
        f.addRow(self._chk("auto_scale", "자동 스케일 (다리 길이 기준)", "소스 대비 타깃의 체형 비율로 이동량을 맞춥니다"))
        f.addRow("모션 크기 배율", self._num("scale_mult", 0.05, 5.0, 0.01, 2, tip="자동 스케일에 곱하는 값. 보폭/이동 거리를 키우거나 줄입니다"))
        self.scale_basis = QComboBox()
        for key, text in scalefit.BASES:
            self.scale_basis.addItem(text, key)
        self.scale_basis.setToolTip("모션 크기를 모델에 맞출 때 무엇의 비율을 기준으로 할지.\n골반 높이=걸음 폭/접지에 가장 안정적, 전체 키=겉보기 크기, 다리 길이=보폭 기준")
        self.btn_fit_scale = QPushButton("모델에 맞춰 스케일 자동 맞춤")
        self.btn_fit_scale.setToolTip("소스 모션과 타깃 모델의 크기를 재서, 모션 스케일이 모델에 맞도록 '모션 크기 배율'을 자동으로 정합니다.")
        self.btn_fit_scale.clicked.connect(lambda *_a: self.fit_scale())
        row = QHBoxLayout()
        row.addWidget(self.scale_basis, 1)
        row.addWidget(self.btn_fit_scale)
        f.addRow("크기 기준", row)
        self.scale_note = QLabel("")
        self.scale_note.setWordWrap(True)
        f.addRow(self.scale_note)
        self.manual_scale = self._num("manual_scale", 0.0001, 1000.0, 0.0001, 4, slider=False, tip="자동 스케일을 끄면 이 값을 씁니다")
        f.addRow("수동 크기 배율", self.manual_scale)
        self.hip_fields = []
        for i, lab in enumerate(("골반 위치 보정 좌우(X)", "골반 위치 보정 높이(Y)", "골반 위치 보정 앞뒤(Z)")):
            nf = NumField(-100, 100, 0.1, 0.0, 2, slider=True)
            nf.changed.connect(lambda v, ax=i: self._hip_changed(ax, v))
            self.hip_fields.append(nf)
            f.addRow(lab, nf)
        self.place_fields = []
        for i, lab in enumerate(("모션 놓을 자리 좌우(X)", "모션 놓을 자리 앞뒤(Z)")):
            nf = NumField(-100, 100, 0.1, 0.0, 2, slider=True)
            nf.setToolTip("모션 전체(골반과 발)를 바닥 위에서 옮깁니다. 뷰포트에서 Shift+드래그로도 조절됩니다")
            nf.changed.connect(lambda v, ax=i: self._place_changed(ax, v))
            self.place_fields.append(nf)
            f.addRow(lab, nf)
        f.addRow("모션 전체 회전 (루트 Yaw °)", self._num("root_yaw_deg", -180, 180, 1, 0, " °"))
        f.addRow(self._chk("transfer_root_motion", "제자리(인플레이스) — 수평 이동 제거", "체크하면 캐릭터가 앞으로 나아가지 않고 제자리에서 움직입니다", invert=True))
        f.addRow(self._chk("same_rig", "같은 스켈레톤이면 본 이름대로 그대로 적용 (사람 모양이 아닌 캐릭터)", "몬스터처럼 사람 본(골반·팔·다리)이 아닌 캐릭터는 사람 기준 매핑·좌표축 추정이 엉뚱하게 돌아갑니다. 소스 모션과 타깃 메시가 같은 스켈레톤(본 이름·계층·모양이 같음)이면 그 추정을 건너뛰고 본 이름대로 1:1 로 적용합니다. 꺼 두면 항상 사람 기준 리타겟을 씁니다."))
        f.addRow(self._chk("start_at_origin", "시작 위치를 모델 원점에 맞춤", "게임 모션은 월드 좌표에서 시작하는 경우가 많습니다. 켜면 첫 프레임의 수평 위치를 모델이 서 있는 자리로 옮기고 이동량만 따라갑니다. 끄면 원본 절대 위치를 그대로 씁니다."))
        self.com_combo = QComboBox()
        self.com_combo.currentIndexChanged.connect(self._com_changed)
        f.addRow("COM(위치 기준) 본", self.com_combo)
        lay.addWidget(g)

        g = CollapsibleBox("척추")
        f = QFormLayout(g)
        f.addRow(self._chk("spine_mix", "척추 개수가 다르면 중간값으로 맞추기",
                           "소스 척추가 3개(척추1·척추2·가슴)인데 타깃 모델은 2개뿐일 때처럼 짝이 없는 본이 생기면, 골반~목 사이를 하나의 곡선으로 보고 "
                           "길이 비율대로 소스 값을 섞어 타깃 본에 넣습니다. 매핑에서 빠진 척추 본도 같이 움직입니다.\n"
                           "끄면 이름이 맞는 본만 1:1 로 적용하고 나머지는 움직이지 않습니다 (직접 매핑을 맞춰 쓰는 경우)."))
        lay.addWidget(g)

        g = CollapsibleBox("발 · 접지 (IK)")
        f = QFormLayout(g)
        f.addRow(self._chk("foot_ik", "발 IK 사용", "접지 구간에서 발이 미끄러지지 않게 다리를 풉니다"))
        f.addRow(self._chk("hand_ik", "손 IK 사용 (바닥 짚기)", "원본 손이 바닥에 닿은 구간에서만 그 팔에 IK 를 걸고 벗어나면 풉니다. 높이는 '타깃 바닥 + 소스 손의 바닥 대비 높이' 로 계산하고, 손이 거의 안 움직이는 구간만 한 점에 고정합니다"))
        f.addRow("IK 추종(0~1)", self._num("ik_follow", 0, 1, 0.05, 2, tip="1: 항상 소스 발 위치(스케일 적용)를 따라감 / 0: 접지 구간에서만 IK"))
        f.addRow(self._chk("ground_clamp", "바닥 아래로 못 내려가게"))
        f.addRow("바닥 높이 보정", self._num("ground_offset", -50, 50, 0.1, 2, tip="타깃 단위. 발목 기준 바닥 높이를 올리거나 내림"))
        self.ground_field = self._fields["ground_offset"][0]
        f.addRow(self._chk("hip_drop", "다리가 모자라면 골반 낮추기"))
        f.addRow("무릎·팔꿈치 방향 고정", self._num("ik_knee_stable", 0, 100, 5, 0, " %", tip="다리가 거의 펴졌을 때 IK 가 무릎을 옆이나 뒤로 돌려 이상한 각도로 꺾는 것을 막고, 무릎이 원래 향하던 쪽(앞)을 유지시킵니다.\n"
                                                                    "100% = 항상 그쪽을 유지 (기본) · 50% = 반쯤 · 0% = 막지 않음 (원본 자세에서 읽은 방향 그대로. 몬스터처럼 무릎이 옆으로 꺾이는 모델은 낮추세요)"))
        f.addRow("무릎·팔꿈치 과하게 접힘 방지", self._num("ik_knee_fold", 0, 100, 5, 0, " %", tip="발이 골반 바로 아래·안쪽으로 끌려올 때 무릎이 완전히 접혀 뒤집히는 것을 막는 정도입니다.\n"
                                                                    "100% = 안쪽 각도 20° 아래로는 못 접힘 (기본) · 0% = 제한 없음"))
        f.addRow(self._chk("ik_no_reverse", "무릎·팔꿈치 역꺾임 방지 (사람형)", "무릎이 뒤로, 팔꿈치가 앞으로 꺾이는 것처럼 사람에게 나올 수 없는 방향으로 꺾인 프레임을 찾아, 이 클립에서 주로 꺾이는 쪽으로 다시 꺾어 줍니다.\n"
                                                    "발/손 위치는 그대로 두고 무릎/팔꿈치만 반대편으로 넘깁니다. IK 가 적용되는 다리·팔에만 듣습니다 (발 IK 를 꺼 두면 다리에는 적용되지 않음).\n"
                                                    "원래 반대로 꺾이는 다리를 가진 몬스터는 그 다리의 '주된 방향'을 기준으로 하므로 대개 문제없지만, 이상하면 끄세요."))
        self.knee_status = QLabel("")
        self.knee_status.setWordWrap(True)
        self.knee_status.setStyleSheet("color:#ffdc50")
        self.knee_status.setToolTip("위 두 보정이 이 클립에서 실제로 몇 프레임에 걸렸는지 보여 줍니다. 0 프레임이면 이 클립에서는 보정이 필요한 장면이 없어서 슬라이더를 바꿔도 달라지지 않습니다.")
        f.addRow(self.knee_status)
        adv = CollapsibleBox("접지 판정 세부값 — 보통 건드릴 필요 없음")
        adv.setToolTip("발이 땅에 닿았는지 판정하는 기준과 골반 낮추기 한계입니다. 기본값으로 대부분 맞고, 발이 땅에서 뜨거나 미끄러지는 클립이 있을 때만 만져 보세요.")
        fa = QFormLayout(adv)
        fa.addRow("골반 최대 하강(다리 길이 비)", self._num("max_hip_drop", 0, 0.4, 0.01, 2, tip="다리가 모자랄 때 골반을 낮출 수 있는 최대치"))
        fa.addRow("접지 높이 임계(다리 길이 비)", self._num("contact_height", 0.005, 0.3, 0.005, 3, tip="소스 발이 가장 낮은 높이에서 이 비율 이내면 '땅에 닿음' 후보"))
        fa.addRow("접지 속도 임계(다리 길이/초)", self._num("contact_speed", 0.05, 3.0, 0.05, 2, tip="발 속도가 이 값보다 느려야 '땅에 닿음'으로 판정"))
        bf = QSpinBox()
        bf.setRange(0, 16)
        bf.setValue(int(self.pr.settings.blend_frames))
        bf.valueChanged.connect(lambda v: self._set("blend_frames", int(v)))
        self._fields["blend_frames"] = (bf, False)
        fa.addRow("접지 전환 프레임", bf)
        b_ik = QPushButton("🎯 IK 구간 직접 지정 열기…")
        b_ik.setToolTip("발이 땅에 붙는 구간을 프레임으로 직접 정합니다 (아래 'IK 구간 직접 지정' 칸이 펴집니다)")
        b_ik.clicked.connect(lambda _c=False: self.ik_panel.open_here())
        f.addRow(b_ik)
        f.addRow(adv)
        try:
            adv.set_collapsed(True, save=False)      # 처음에는 접어 둔다 (사용자가 펴 두면 그 상태를 기억)
        except TypeError:                            # 시험용 가짜 Qt
            pass
        lay.addWidget(g)

        self.ik_panel = IKRangePanel(self)
        lay.addWidget(self.ik_panel)

        g = CollapsibleBox("방향 맞추기 — 위쪽 · 앞쪽 (자동 인식이 틀릴 때만)")
        f = QFormLayout(g)
        f.addRow(with_help(QLabel("모션이 옆으로 눕거나 뒤돌아 있을 때만 손대세요."),
                           "프로그램마다 '위'와 '앞'이 되는 축이 다릅니다 (3ds Max·언리얼은 Z가 위, Maya·Blender 일부는 Y가 위).\n"
                           "그래서 원본 모션과 내 모델이 각각 '어느 축이 위쪽이고 어느 축이 앞쪽인지'를 알아야 방향을 맞춰 입힐 수 있습니다.\n"
                           "보통은 자동 인식이 맞으니 건드리지 않아도 됩니다. 캐릭터가 누워 있거나 뒤돌아 있거나 옆으로 걸으면 아래 [소스 빠른 수정] 버튼부터 눌러 보세요.\n"
                           "· 위쪽 방향: 머리가 향하는 쪽\n· 앞 방향: 얼굴/가슴이 향하는 쪽"))
        for key, lab in (("src_up", "원본 모션의 위쪽"), ("src_fwd", "원본 모션의 앞쪽"), ("tgt_up", "내 모델의 위쪽"), ("tgt_fwd", "내 모델의 앞쪽")):
            cb = QComboBox()
            for a in AXES:
                cb.addItem(AXIS_LABEL.get(a, a), a)
            cb.currentIndexChanged.connect(lambda i, k=key, c=cb: self._set(k, c.itemData(i)))
            self._fields[key] = (cb, False)
            f.addRow(lab, cb)
        # 빠른 수정: 상방/전방을 직접 고르지 않아도 버튼으로 돌리거나 뒤집는다 (자동 인식 값에서 시작)
        self.axis_notes = {}
        for side, title in (("src", "소스"), ("tgt", "타깃")):
            row = QHBoxLayout()
            # 타깃은 빠른 수정 버튼(돌리기·눕히기)을 두지 않는다: 타깃은 '내 모델이 원래 어느 방향으로 서 있는지'를 읽는 값이라, 눌러서 바꾸면
            # 오히려 멀쩡한 모델이 누워 보였다 (눕히기/세우기). 틀렸을 때는 위의 상방/전방 선택으로 직접 고르거나 [자동]으로 되돌린다.
            ops = (("yaw90", "↻ 90° 돌리기", "위를 축으로 앞 방향을 90° 돌립니다 (옆을 보고 있을 때). 여러 번 누르면 계속 돌아요."),
                                  ("yaw180", "↔ 앞뒤 반전", "앞과 뒤를 바꿉니다 (뒤돌아 있을 때)"),
                                  ("flip_up", "⇅ 위아래 반전", "위와 아래를 바꿉니다 (거꾸로 서 있을 때)"),
                                  ("pitch90", "⤴ 눕히기/세우기 90°", "좌우 축 기준으로 90° 굴립니다 (Z-up 모델이 누워 보일 때)"))
            for op, text, tip in (ops if side == "src" else ()):
                b = QPushButton(text)
                b.setToolTip(title + ": " + tip)
                b.clicked.connect(lambda *_a, sd=side, o=op: self.axis_quick(sd, o))
                row.addWidget(b)
            b = QPushButton("자동" if side == "src" else "자동으로 되돌리기")
            b.setToolTip("자동 인식으로 되돌립니다" + ("" if side == "src" else " (타깃이 이상하게 서 있거나 누워 보일 때 먼저 눌러 보세요)"))
            b.clicked.connect(lambda *_a, sd=side: self.axis_quick(sd, "auto"))
            row.addWidget(b)
            f.addRow(("원본 모션 빠른 수정" if side == "src" else "내 모델 방향"), row)
            lb = QLabel("")
            lb.setWordWrap(True)
            lb.setTextFormat(Qt.TextFormat.RichText)
            self.axis_notes[side] = lb
            f.addRow("", lb)
        lay.addWidget(g)

        g = CollapsibleBox("본별 보정 — 회전 · 위치 · 크기 (하위 본 전체에 적용)")
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
        f = QFormLayout()
        self.pos_fields = []
        for i, lab in enumerate(("위치 X (좌우)", "위치 Y (위)", "위치 Z (앞)")):
            nf = NumField(-100000, 100000, 0.01, 0, 3, "", slider=False)
            nf.setToolTip("선택한 본(과 하위 본 전체)을 바인드 포즈 기준의 이 축 방향으로 옮깁니다 (타깃 모델 단위). 모션 중에는 부모 본을 따라갑니다.")
            nf.changed.connect(lambda val, ax=i: self._pos_changed(ax, val))
            self.pos_fields.append(nf)
            f.addRow(lab, nf)
        self.scale_field = NumField(0.05, 20.0, 0.01, 1.0, 3, "", slider=False)
        self.scale_field.setToolTip("선택한 본을 기준점으로 하위 본 위치와 그 본들에 붙은 메시를 키우거나 줄입니다 (1 = 그대로). 내보낼 때는 본 위치에 반영되고, 메시 자체 크기는 뷰포트에서만 바뀝니다.")
        self.scale_field.changed.connect(self._scale_changed)
        f.addRow("크기 (배율)", self.scale_field)
        v.addLayout(f)
        b = QPushButton("본별 보정 모두 초기화 (회전·위치·크기)")
        b.clicked.connect(self._rot_reset)
        v.addWidget(b)
        lay.addWidget(g)

        self.layer_panel = LayerPanel(self)
        lay.addWidget(self.layer_panel)

        g = CollapsibleBox("모션 다듬기 (좌우 반전 · 떨림 줄이기 · 튀는 구간 제거 · 부위별 세기)")
        f = QFormLayout(g)
        f.addRow(self._chk("fx_mirror", "좌우 반전 (Mirror)", "모션을 거울처럼 뒤집습니다. 왼팔이 하던 동작을 오른팔이 하고 몸 전체가 반대로 움직입니다. 타깃 모델이 좌우 대칭이어야 해요.\n"
                           "※ 화면의 발 고정(IK) 표시와 소스 고스트는 반전 전 기준 그대로 보입니다."))
        f.addRow("떨림 줄이기 (0 = 안 함)", self._num("fx_smooth", 0.0, 10.0, 0.5, 1, "", tip="모션이 자글자글 떨릴 때(AI 모캡 등) 앞뒤 몇 프레임을 평균 내서 떨림을 줄입니다.\n"
                                                                              "0 = 끔 · 1~2 = 미세한 떨림만 · 3~5 = 눈에 띄는 떨림 · 6 이상 = 많이 뭉개짐 (빠른 동작이 둔해지고 발이 조금 미끄러질 수 있어요)"))
        f.addRow("튀는 구간 제거 — 기준 각도 (0 = 안 함)", self._num("fx_spike_deg", 0.0, 180.0, 5.0, 0, " °", tip="앞뒤 정상 프레임을 잇는 선에서 이 각도 이상 벗어나 튀는 구간(최대 아래 '튀는 구간 길이' 프레임)을 앞뒤 값으로 메웁니다. "
                                                                                     "모캡의 순간 튐과, IK 가 원래 동작을 억지로 따라가다 몇 프레임 이상하게 꺾이는 것 둘 다 잡습니다. 0 = 끔. 처음엔 25~40° 로 시작해 보세요."))
        f.addRow("튀는 구간 제거 — 최대 길이(프레임)", self._num("fx_spike_len", 1, 20, 1, 0, slider=False, tip="이 프레임 수까지 이어서 튀는 구간을 고칩니다. 동작이 원래 빠르면 짧게 두세요."))
        lb = QLabel("부위별 모션 세기 — 100% = 그대로, 0% = 기준 자세로 고정, 100% 초과 = 과장")
        lb.setWordWrap(True)
        lb.setStyleSheet("color:#8aa")
        f.addRow(lb)
        self.fx_part_fields = {}
        for key in motionfx.PART_ORDER:
            nf = NumField(0.0, 1.5, 0.05, self.pr.settings.fx_parts.get(key, 1.0), 2, slider=True)
            nf.setToolTip("%s의 움직임 세기. 예: 팔 0%% = 팔은 가만히, 몸통 50%% = 허리를 덜 비틀기." % motionfx.PARTS[key][0])
            nf.changed.connect(lambda v, k=key: self._fx_part_changed(k, v))
            self.fx_part_fields[key] = nf
            f.addRow(motionfx.PARTS[key][0], nf)
        lay.addWidget(g)

        # 영상 찾기 탭의 '추출할 애니메이션 가공'과 같은 기능 — 내보낼 때 리타겟 결과에 적용된다
        self.rt_edit = EditPanel("애니메이션 가공 (선택) — 늘리기 · fps · 구간 · 루프 · 키 압축", self.cfg, "rt_edit")
        self.rt_edit.setToolTip("내보낼 때 리타겟 결과(또는 소스 모션)에 적용됩니다. 아무것도 안 고르면 가공하지 않습니다. "
                                "내보내기 탭의 '추가 가공'과 함께 쓰면 두 설정이 합쳐집니다(같은 항목은 내보내기 탭 값이 우선).")
        self.rt_edit_note = QLabel("")
        self.rt_edit_note.setWordWrap(True)
        self.rt_edit_note.setStyleSheet("color:#8aa")
        for wd, sig in ((self.rt_edit.mode, "currentIndexChanged"), (self.rt_edit.value, "valueChanged"), (self.rt_edit.fps, "valueChanged"),
                        (self.rt_edit.start, "valueChanged"), (self.rt_edit.end, "valueChanged"), (self.rt_edit.loop, "valueChanged"),
                        (self.rt_edit.compress, "toggled"), (self.rt_edit.rot, "valueChanged"), (self.rt_edit.pos, "valueChanged")):
            getattr(wd, sig).connect(lambda *_a: self._rt_edit_changed())
        lay.addWidget(self.rt_edit)
        lay.addWidget(self.rt_edit_note)
        self._rt_edit_changed()

        b = QPushButton("모든 설정을 기본값으로")
        b.clicked.connect(self._reset_settings)
        lay.addWidget(b)
        lay.addStretch(1)
        return sc

    def _rt_edit_changed(self) -> None:
        """리타겟 탭 가공 패널: 값 기억 + 현재 클립이 어떻게 바뀌는지 한 줄로 보여준다."""
        try:
            self.rt_edit.save()
        except Exception:
            pass
        opt = self.rt_edit.options()
        if not opt.active():
            self.rt_edit_note.setText("")
            return
        txt = "가공 켜짐 — 내보낼 때 적용됩니다."
        try:
            if self.pr.src is not None:
                a = self.pr.anim
                opt2 = dataclasses.replace(opt, compress=False)
                b, _k, info = animops.apply_edit(a, opt2, 100.0, self.pr.src.skeleton.parents)
                txt = "현재 클립: %d프레임 · %.1f fps → %d프레임 · %.1f fps%s (내보낼 때 적용)" % (
                    info.frames_before, info.fps_before, info.frames_after, info.fps_after, " · 키 압축" if opt.compress else "")
        except Exception:
            pass
        self.rt_edit_note.setText(txt)

    def _export_edit(self):
        """리타겟 탭 가공 + 내보내기 탭 가공을 합친 옵션."""
        return animops.merge_edit(self.rt_edit.options(), self.edit_panel.options())

    # ------------------------------------------------------------------ 탭 3
    def _tab_quality(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.quality_summary = QLabel("리타겟 결과가 있으면 문제 구간을 자동으로 찾아 줍니다.")
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

        g = CollapsibleBox("출력 폴더")
        h = QHBoxLayout(g)
        self.out_edit = QLineEdit(self.cfg.get("out_dir") or os.path.join(os.path.expanduser("~"), "MotionBridge_out"))
        h.addWidget(self.out_edit, 1)
        b = QPushButton("찾아보기…")
        b.clicked.connect(self._pick_out)
        h.addWidget(b)
        lay.addWidget(g)

        g = CollapsibleBox("폴더 정리")
        h = QHBoxLayout(g)
        self.cb_organize = QCheckBox("캐릭터별 폴더로 정리 (캐릭터 / Animations / 클립 이름 / 맥스·블렌더 파일 함께, Models)")
        self.cb_organize.setChecked(bool(self.cfg.get("export_organize", True)))
        self.cb_organize.setToolTip("끄면 출력 폴더에 파일이 한꺼번에 쌓입니다. 켜면 '출력 폴더 / 캐릭터 이름 / Animations / 클립 이름' 폴더 하나에 "
                                    "3ds Max·Blender·Maya 파일을 함께 넣고(파일 이름 끝 _max · _blender · _maya 로 구분, 폴더 안 '사용법.txt' 에 무엇을 어디서 여는지 적힙니다), "
                                    "메시가 든 GLB·본 구조 JSON 은 '… / Models' 에 둡니다.")
        h.addWidget(self.cb_organize)
        h.addWidget(QLabel("캐릭터 폴더 이름"))
        self.char_edit = QLineEdit(self.cfg.get("export_char", ""))
        self.char_edit.setPlaceholderText("비우면 자동 (추출 폴더의 캐릭터 / 클립 이름에서)")
        h.addWidget(self.char_edit, 1)
        lay.addWidget(g)

        g = CollapsibleBox("같은 이름의 파일이 이미 있을 때")
        v = QVBoxLayout(g)
        self.out_exist = self._make_exist_combo()
        v.addWidget(self.out_exist)
        lb = QLabel(safesave.EXPLAIN[safesave.DEFAULT])
        lb.setWordWrap(True)
        lb.setStyleSheet("color:#8a93a6")
        self.__dict__.setdefault("_exist_labels", []).append(lb)
        v.addWidget(lb)
        lay.addWidget(g)

        g = CollapsibleBox("무엇을 내보낼까")
        v = QVBoxLayout(g)
        self.rb_retarget = QRadioButton("리타겟 결과 (내 모델용)")
        self.rb_source = QRadioButton("원본 모션 그대로 (리타겟 없음 — 크리처/구조물용)")
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

        g = CollapsibleBox("대상 프로그램")
        h = QHBoxLayout(g)
        self.cb_preset = {}
        for key, p in PRESETS.items():
            cb = QCheckBox(p.label)
            cb.setToolTip(p.note)
            cb.setChecked(key == "blender")
            self.cb_preset[key] = cb
            h.addWidget(cb)
        lay.addWidget(g)

        g = CollapsibleBox("파일 형식")
        h = QHBoxLayout(g)
        self.cb_fmt = {}
        for key, text, tip in (("bvh", "BVH", "Max/Maya/Blender 모두 읽는 모션 파일 (대상 프로그램별 축·단위 적용)"),
                               ("glb", "GLB (glTF)", "메시·스킨·애니메이션 포함 가능"),
                               ("bip", "3ds Max Biped (.ms)", "FBX 를 거치지 않고 Biped 에 바로 넣는 MaxScript + 데이터. 3ds Max 에서 스크립트를 실행하면 Biped 를 만들고 .bip 로 저장합니다 (대상 프로그램 선택과 무관, 3ds Max 전용)"),
                               ("fbx", "FBX (Blender 필요)", "내 모델 파일을 Blender 로 열어 모션을 입힌 뒤 FBX 로 저장 — 리타겟 결과 전용")):
            cb = QCheckBox(text)
            cb.setToolTip(tip)
            cb.setChecked(key == "bvh")
            self.cb_fmt[key] = cb
            h.addWidget(cb)
        lay.addWidget(g)
        h2 = QHBoxLayout()
        h2.addWidget(QLabel("스키닝된 .max (선택)"))
        self.biped_tpl = QLineEdit(self.cfg.get("biped_template", ""))
        self.biped_tpl.setPlaceholderText("비워 두면 새 Biped 를 만들어 .bip 만 저장 · 스키닝된 .max 를 고르고 추출하면 고른 방식(Biped/일반 본)으로 모션을 적용해 같은 폴더에 *_retarget.max 로 저장")
        self.biped_tpl.setToolTip("3ds Max Biped 형식으로 내보낼 때만 쓰입니다.\n"
                                  "· 비워 둘 때: 3ds Max 에서 *_retarget.ms 를 실행하면 새 Biped 를 만들고 모션을 넣어 같은 폴더에 *_retarget.bip 로 저장합니다.\n"
                                  "· 스키닝된 .max 를 고를 때: *_retarget.ms 를 실행하면 그 파일을 열어 안의 Biped(refbone ▶ bip 구조)에 모션을 적용하고,\n"
                                  "  그 .max 가 있는 폴더에 *_retarget.max 와 *_retarget.bip 로 다른 이름 저장합니다. 원본 .max 는 그대로, 같은 이름이 있으면 _2, _3 을 붙입니다.\n"
                                  "저장 위치는 묻지 않습니다. 모든 프레임에 키를 넣습니다.")
        h2.addWidget(self.biped_tpl, 1)
        b2 = QPushButton("찾아보기…")
        b2.clicked.connect(self._pick_biped_template)
        h2.addWidget(b2)
        lay.addLayout(h2)
        h3 = QHBoxLayout()
        h3.addWidget(QLabel("적용 방식"))
        self.max_method = QComboBox()
        self.max_method.addItem("Biped (refbone ▶ bip 구조에 키)", "biped")
        self.max_method.addItem("일반 본 (Maya 식 관절 — 이름이 같은 본에 키)", "bones")
        i = self.max_method.findData(self.cfg.get("max_method", "biped"))
        self.max_method.setCurrentIndex(max(i, 0))
        self.max_method.setToolTip("스키닝된 .max 를 골랐을 때 모션을 넣는 방식을 검수자가 정합니다.\n"
                                   "· Biped (권장): .max 안의 Biped 에 키를 넣습니다 (본 이름과 무관, 부위로 찾음). 작업용 모션은 Biped 에 키가 찍혀야 해서 이쪽을 씁니다.\n"
                                   "· 일반 본: Biped 가 아닌 평범한 본/관절 계층. 타깃 모델의 본 이름과 같은 이름의 본(대소문자·공백·_·네임스페이스 무시)에 회전 키를 넣고,\n"
                                   "  골반/루트처럼 위치가 움직이는 본에는 위치 키도 넣습니다.")
        h3.addWidget(self.max_method)
        self.cb_max_auto = QCheckBox("내보낸 뒤 3ds Max 를 실행해 자동 적용")
        self.cb_max_auto.setChecked(bool(self.cfg.get("max_auto", True)))
        self.cb_max_auto.setToolTip("켜면 추출(내보내기) 직후 3ds Max 를 화면 없이 실행해 .max 에 모션을 적용하고 *_retarget.max 로 저장합니다.\n"
                                    "끄면 *_retarget.ms 만 만들고, 3ds Max 에서 직접 실행합니다. 3ds Max 가 설치된 Windows 에서만 동작합니다.")
        h3.addWidget(self.cb_max_auto)
        lay.addLayout(h3)
        h4 = QHBoxLayout()
        h4.addWidget(QLabel("3ds Max 경로"))
        self.max_exe = QLineEdit(self.cfg.get("max_exe", ""))
        self.max_exe.setPlaceholderText("비워 두면 설치된 3ds Max(3dsmaxbatch.exe)를 자동으로 찾습니다")
        h4.addWidget(self.max_exe, 1)
        b4 = QPushButton("찾아보기…")
        b4.clicked.connect(self._pick_max_exe)
        h4.addWidget(b4)
        lay.addLayout(h4)
        self.blender_label = QLabel("")
        self.blender_label.setWordWrap(True)
        lay.addWidget(self.blender_label)
        b = QPushButton("Blender 경로 지정…")
        b.clicked.connect(self.pick_blender)
        lay.addWidget(b)
        self._update_blender_label()

        self.edit_panel = EditPanel("내보낼 때 추가 가공 (선택) — fps · 루프 · 키 압축", self.cfg, "ex_edit", simple=True)
        lay.addWidget(self.edit_panel)

        g = CollapsibleBox("클립")
        v = QVBoxLayout(g)
        self.rb_clip_cur = QRadioButton("현재 클립만")
        self.rb_clip_all = QRadioButton("소스의 모든 클립")
        self.rb_clip_cur.setChecked(True)
        v.addWidget(self.rb_clip_cur)
        v.addWidget(self.rb_clip_all)
        self.clip_hint = QLabel("")
        self.clip_hint.setWordWrap(True)
        self.clip_hint.setStyleSheet("color:#ffdc50;font-weight:700")
        self.clip_hint.setToolTip("왼쪽 '모션 목록'에서 모션 앞의 체크박스를 켜면, 체크한 모션이 전부 내보내집니다 (위 선택보다 우선).")
        v.addWidget(self.clip_hint)
        lay.addWidget(g)

        self.btn_export = QPushButton("내보내기")
        self.btn_export.setMinimumHeight(38)
        self.btn_export.setToolTip("위 '같은 이름의 파일이 이미 있을 때' 설정대로 저장합니다. 덮어쓰기를 고르면 같은 이름의 이전 파일이 사라집니다.")
        self.btn_export.clicked.connect(lambda *_a: self.do_export())
        lay.addWidget(self.btn_export)
        self.btn_export_as = QPushButton("다른 이름으로 내보내기…")
        self.btn_export_as.setToolTip("파일 이름을 직접 정해 내보냅니다 (클립이 여러 개면 '이름_클립이름'). 같은 이름이 있을 때의 처리는 위 설정을 따릅니다.")
        self.btn_export_as.clicked.connect(self.do_export_as)
        lay.addWidget(self.btn_export_as)
        self._update_exist_texts()
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
        tb.setOpenLinks(False)  # 링크는 직접 처리: 웹 링크는 Chrome 으로 연다
        tb.anchorClicked.connect(self._open_link)
        tb.setHtml(EXTRACT_HELP)
        return tb

    def _open_link(self, url) -> None:
        text = url.toString() if hasattr(url, "toString") else str(url)
        if not osopen.open_url(text):  # Chrome 이 없거나 웹 링크가 아니면 기본 프로그램으로
            QDesktopServices.openUrl(url if hasattr(url, "toString") else QUrl(text))

    # ================================================================== 비동기 작업
    def run_async(self, fn, on_done, label: str, cancellable: bool = False, on_cancel=None) -> None:
        if self._worker is not None:
            return
        self._zombies = [z for z in self._zombies if z.isRunning()]
        if self._zombies:
            self.statusBar().showMessage("이전 작업을 멈추는 중입니다. 잠시 뒤에 다시 눌러 주세요.")
            return
        self._set_busy(True, label)
        self._on_done, self._busy_label, self._on_cancel = on_done, label, on_cancel
        self._busy_dlg = None
        # 모든 작업에 작은 '작업 중' 창을 띄운다. 취소할 수 있는 작업은 X/취소/Esc 가 곧 취소, 아닌 작업은 끝날 때까지 안내만.
        # 짧은 작업에 창이 깜빡이지 않게, 취소할 수 없는 작업은 0.3초 넘게 걸릴 때만 보인다.
        self._busy_dlg = BusyDialog(label, self, closable=cancellable)
        if cancellable:
            self._busy_dlg.cancelled.connect(self.cancel_async)
            self._busy_dlg.show()
        else:
            self._busy_dlg.show_later(300)
        wk = Worker(fn, self)
        self._worker = wk
        # 바인딩된 메서드로 연결해야 콜백이 GUI 스레드에서 실행된다
        wk.finished_ok.connect(self._worker_done)
        wk.failed.connect(self._worker_failed)
        wk.progress.connect(self._worker_progress)
        wk.start()

    def cancel_async(self) -> bool:
        """진행 중인 작업을 멈춘다: 작업 함수에 취소를 알리고 결과는 버린다.
        하던 단계(네트워크 요청 등)가 실제로 끝날 때까지 화면은 잠겨 있고, 진행 창은 '멈추는 중'으로 바뀌어 남는다."""
        wk = self._worker
        if wk is None:
            return False
        wk.cancel.set()
        for sig in (wk.finished_ok, wk.failed, wk.progress):
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
        self._worker = None
        self._zombies.append(wk)
        self._stop_wk = wk
        self.statusBar().showMessage("⏹ 멈추는 중… 하던 단계가 끝나면 풀립니다")
        dlg = getattr(self, "_busy_dlg", None)
        if dlg is not None:
            dlg.set_stopping()
        wk.finished.connect(self._stopped)
        if not wk.isRunning():  # 연결하기 전에 이미 끝났으면 신호가 오지 않는다
            self._stopped()
        return True

    def _stopped(self) -> None:
        """멈추던 작업 스레드가 끝났다 -> 화면을 풀고 알린다 (한 번만)."""
        wk = getattr(self, "_stop_wk", None)
        if wk is None:
            return
        self._stop_wk = None
        self._zombies = [z for z in self._zombies if z is not wk and z.isRunning()]
        self._set_busy(False)
        self.statusBar().showMessage("작업을 취소했습니다")
        cb = getattr(self, "_on_cancel", None)
        if cb:
            cb()

    def _worker_done(self, res) -> None:
        cb = self._on_done
        self._worker = None
        self._set_busy(False)
        cb(res)

    def _worker_failed(self, msg: str) -> None:
        self._worker = None
        self._set_busy(False)
        first = msg.split("\n\n")[0]
        QMessageBox.critical(self, "오류", first[len("RuntimeError: "):] if first.startswith("RuntimeError: ") else first)
        self.export_log.appendPlainText(msg)

    def _worker_progress(self, i: int, n: int, name: str) -> None:
        if getattr(self, "_busy_dlg", None) is not None:
            self._busy_dlg.set_progress(i, n, name, self._busy_label)
        self.statusBar().showMessage("%s (%d/%d) %s" % (self._busy_label, i + 1, n, name))

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self.tabs.setEnabled(not busy)
        self.right_box.setEnabled(not busy)
        self.viewport.paused = busy
        self.viewport.update()
        self.status_progress.setVisible(busy)
        if not busy and getattr(self, "_busy_dlg", None) is not None:
            self._busy_dlg.finish()
            self._busy_dlg = None
        if busy:
            self.status_progress.setRange(0, 0)
            self.statusBar().showMessage(label + " …")
        else:
            self.statusBar().showMessage("준비됨")

    # ================================================================== 열기 / 저장
    def _remember_dir(self, path: str) -> None:
        self.cfg["last_dir"] = os.path.dirname(path)
        bridge.save_config(self.cfg)

    MESH_EXTS = (".glb", ".gltf", ".fbx", ".obj", ".psk", ".pskx")

    def note_extracted(self, files) -> None:
        """방금 추출/불러온 파일들이 든 폴더를 기억한다: 메시는 Models, 모션은 Animations (원본 _raw/변환 중간 폴더는 제외)."""
        found = {}
        for f in files or []:
            low = f.lower().replace("\\", "/")
            if "/_raw/" in low or "/_stage" in low:
                continue
            kind = "mesh" if low.endswith(self.MESH_EXTS) else "anim" if low.endswith(".psa") else None
            if kind and kind not in found:
                found[kind] = os.path.dirname(f)
        if found:
            self.cfg.update({"last_mesh_dir": found.get("mesh", self.cfg.get("last_mesh_dir", "")),
                             "last_anim_dir": found.get("anim", self.cfg.get("last_anim_dir", ""))})
            bridge.save_config(self.cfg)

    def _open_start_dir(self, kind: str) -> str:
        """열기 창이 시작할 폴더: 마지막으로 추출한 그 종류(mesh/anim)의 폴더, 없으면 마지막으로 연 폴더."""
        d = self.cfg.get("last_mesh_dir" if kind == "mesh" else "last_anim_dir", "")
        return d if d and os.path.isdir(d) else self.cfg.get("last_dir", "")

    def open_source(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "소스 모션 열기 (여러 개 가능 — 열면 왼쪽 모션 목록에 자동으로 더해집니다)", self._open_start_dir("anim"), FILE_FILTER)
        if paths:
            self._remember_dir(paths[0])
            self._add_source_files(list(paths))

    def open_target(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "타깃 모델 열기", self._open_start_dir("mesh"), FILE_FILTER)
        if path:
            self._remember_dir(path)
            self.run_async(lambda w: self.pr.load_target(path), lambda m: self._after_load("target"), "타깃 읽는 중")

    def unload_target(self) -> None:
        if self.pr.tgt is None:
            return
        name = os.path.basename(self.pr.tgt.path)
        self.pr.unload_target()
        self._after_unload("target")
        self.statusBar().showMessage("타깃 모델을 뺐습니다: " + name)

    def unload_source(self) -> None:
        if self.pr.src is None:
            return
        name = os.path.basename(self.pr.src.path)
        self.pr.unload_source()
        self._after_unload("source")
        self.statusBar().showMessage("소스 모션을 뺐습니다: " + name)

    def _after_unload(self, which: str) -> None:
        self._syncing = True
        if which == "source":
            self._exported = False
            self.clip_combo.clear()
            self._refresh_clip_list()
        else:
            self.rot_combo.clear()
            self.com_combo.clear()
            self.com_combo.addItem("(자동)", -1)
        self._syncing = False
        self._fill_mapping()
        self.recompute(reframe=True)
        self.history.reset(self._edit_state())

    def _after_load(self, which: str) -> None:
        self._syncing = True
        if which == "source":
            self.clip_combo.clear()
            for i, a in enumerate(self.pr.src.animations):
                self.clip_combo.addItem("%d: %s (%d프레임, %.0ffps)" % (i, a.name, a.n_frames, a.fps))
            self._refresh_clip_list()
        else:
            self._fill_joint_combos()
            self._sync_settings_ui()
            self._update_hip_ranges()
        self._syncing = False
        if which == "source" and self.pr.tgt is not None:
            self._fill_joint_combos()      # 같은 스켈레톤 모드가 켜지면 보정 대상 본 목록이 바뀐다
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

    def _fx_part_changed(self, key: str, v: float) -> None:
        if self._syncing:
            return
        d = dict(self.pr.settings.fx_parts)
        if abs(float(v) - 1.0) < 1e-6:
            d.pop(key, None)
        else:
            d[key] = round(float(v), 3)
        self.pr.settings.fx_parts = d
        self.schedule()

    # ---- 좌표축 빠른 수정
    def _frame_now(self, side: str):
        """지금 그 쪽(src/tgt)에 쓰이는 프레임(월드->표준). 결과가 있으면 그 값, 없으면 직접 감지."""
        from ..core.mapping import DEFAULT_FRAME
        from ..core.retarget import _resolve_frame

        res = self.pr.result
        if res is not None:
            return (res.frame_src if side == "src" else res.frame_tgt)[0]
        m = self.pr.src if side == "src" else self.pr.tgt
        slots = self.pr.src_slots if side == "src" else self.pr.tgt_slots
        if m is None:
            return DEFAULT_FRAME
        st = self.pr.settings
        return _resolve_frame(m.skeleton, st.src_up if side == "src" else st.tgt_up, st.src_fwd if side == "src" else st.tgt_fwd, slots,
                              self.pr.anim if side == "src" else None)[0]

    def axis_quick(self, side: str, op: str) -> bool:
        """축 빠른 수정: op 는 yaw90 / yaw180 / flip_up / pitch90 / auto. 지금 쓰이는 축에서 시작해 한 번 돌린다."""
        from ..core.mapping import axes_of_frame, turn_axes

        m = self.pr.src if side == "src" else self.pr.tgt
        if m is None:
            self.statusBar().showMessage("먼저 %s을(를) 여세요" % ("소스 모션" if side == "src" else "타깃 모델"))
            return False
        st = self.pr.settings
        ku, kf = side + "_up", side + "_fwd"
        if op == "auto":
            setattr(st, ku, "auto")
            setattr(st, kf, "auto")
        else:
            up, fwd = getattr(st, ku), getattr(st, kf)
            if up == "auto" or fwd == "auto":
                up, fwd = axes_of_frame(self._frame_now(side))
            setattr(st, ku, turn_axes(up, fwd, op)[0])
            setattr(st, kf, turn_axes(up, fwd, op)[1])
        self._sync_settings_ui()
        self.recompute()
        return True

    def _update_axis_notes(self) -> None:
        """좌표축 설명 줄: 지금 적용된 축 + 자동 인식이 틀리기 쉬운 이유."""
        if not getattr(self, "axis_notes", None):
            return
        from ..core.mapping import diagnose_frame

        res, st = self.pr.result, self.pr.settings
        for side in ("src", "tgt"):
            m = self.pr.src if side == "src" else self.pr.tgt
            if m is None:
                self.axis_notes[side].setText("")
                continue
            parts = []
            if res is not None:
                desc = (res.frame_src if side == "src" else res.frame_tgt)[1]
                manual = getattr(st, side + "_up") != "auto" and getattr(st, side + "_fwd") != "auto"
                parts.append("<b>%s</b> 적용 축: %s%s" % ("원본 모션" if side == "src" else "내 모델", desc, "" if manual else " (자동)"))
            if getattr(st, side + "_up") == "auto" or getattr(st, side + "_fwd") == "auto":
                slots = self.pr.src_slots if side == "src" else self.pr.tgt_slots
                for n in diagnose_frame(m.skeleton, slots):
                    parts.append("<span style='color:#d08a2e'>⚠ %s</span>" % n)
            self.axis_notes[side].setText("<br>".join(parts))

    def fit_scale(self) -> None:
        """소스와 타깃의 크기를 재서 스케일 배율을 정한다 (자동 스케일을 켜고 배율만 바꿈)."""
        if not self.pr.ready():
            QMessageBox.warning(self, "스케일 맞춤", "소스 모션, 타깃 모델, 본 매핑이 모두 있어야 합니다.")
            return
        rep = scalefit.measure(self.pr, self.scale_basis.currentData() or "hip")
        if rep.chosen is None:
            self.scale_note.setText(rep.text())
            return
        self.pr.settings.auto_scale = True
        self.pr.settings.scale_mult = float(min(max(rep.mult, 0.05), 5.0))
        self._sync_settings_ui()
        self.scale_note.setText(rep.text())
        self.statusBar().showMessage("스케일 자동 맞춤: 최종 x%.3g" % rep.chosen.ratio)
        self.schedule()

    def _hip_changed(self, axis: int, value: float) -> None:
        if self._syncing:
            return
        h = list(self.pr.settings.hip_offset)
        h[axis] = value
        self.pr.settings.hip_offset = tuple(h)
        self.schedule()

    def _place_changed(self, axis: int, value: float) -> None:
        if self._syncing:
            return
        p = list(self.pr.settings.place_offset)
        p[axis] = value
        self.pr.settings.place_offset = (p[0], p[1])
        self._update_place_label()
        self.schedule()

    def _placement_dragged(self, dx: float, dz: float, dyaw: float) -> None:
        """뷰포트에서 끌어 바꾼 애니메이션 배치를 설정에 더한다."""
        st = self.pr.settings
        st.place_offset = (st.place_offset[0] + dx, st.place_offset[1] + dz)
        y = st.root_yaw_deg + dyaw
        st.root_yaw_deg = float((y + 180.0) % 360.0 - 180.0)
        self._sync_settings_ui()
        self.schedule()

    def _place_reset(self) -> None:
        st = self.pr.settings
        st.place_offset = (0.0, 0.0)
        st.root_yaw_deg = 0.0
        self._sync_settings_ui()
        self.schedule()

    def _update_place_label(self) -> None:
        st = self.pr.settings
        moved = abs(st.place_offset[0]) > 1e-9 or abs(st.place_offset[1]) > 1e-9 or abs(st.root_yaw_deg) > 1e-9
        self.place_label.setText(("배치: 좌우 %.3g · 앞뒤 %.3g · 회전 %.1f°" % (st.place_offset[0], st.place_offset[1], st.root_yaw_deg)) if moved else "")

    def _update_hip_ranges(self) -> None:
        if self.pr.tgt is None:
            return
        _, bwp = self.pr.tgt.skeleton.bind_world()
        size = float(max(bwp.max(axis=0) - bwp.min(axis=0)))
        step = max(size / 400.0, 1e-4)
        for nf in self.hip_fields:
            nf.set_range(-size * 0.5, size * 0.5, step)
        for nf in self.place_fields:
            nf.set_range(-size * 3.0, size * 3.0, step)
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
        for k, nf in getattr(self, "fx_part_fields", {}).items():
            nf.set_value(st.fx_parts.get(k, 1.0))
        for i, nf in enumerate(self.hip_fields):
            nf.set_value(st.hip_offset[i])
        for i, nf in enumerate(self.place_fields):
            nf.set_value(st.place_offset[i])
        self._update_place_label()
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

    def _editable_joints(self):
        """본 편집/보정 대상 [(표시 이름, 본 번호)]. 사람 모양이면 사람 슬롯에 매핑된 본, 같은 스켈레톤 모드(몬스터 등)나 사람 매핑이 없으면 모든 본."""
        if self.pr.tgt is None:
            return []
        names = self.pr.tgt.skeleton.names
        rig = self.pr.rig() if (self.pr.src is not None) else None
        if rig is not None:
            return [(names[t], t) for t in sorted({t for _s, t in rig.pairs})]
        mapped = self._mapped_tgt_joints()
        if len(mapped) >= 8:
            return [("%s (%s)" % (slot_label(sl), names[j]), j) for sl, j in mapped]
        return [(n, i) for i, n in enumerate(names)]

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
        for label, j in self._editable_joints():
            mark = " ●" if self._edited(j) else ""
            self.rot_combo.addItem("%s%s" % (label, mark), j)
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

    def _edited(self, j: int) -> bool:
        s = self._edited_set()
        if j in s:
            return True
        # 손·발 끝은 옮기면 위/아래 팔다리에 기록된다(IK 방식) -> 그쪽에 수정이 있어도 '이 본 초기화'를 누를 수 있어야 한다
        ch = posik.end_chain(self.pr.tgt_slots, j) if (self.pr.tgt is not None and j is not None) else None
        return ch is not None and (ch[0] in s or ch[1] in s)

    def _edited_set(self) -> set:
        st = self.pr.settings
        out = set(st.rot_offsets) | set(st.pos_offsets) | set(st.scale_offsets) | set(st.ik_offsets)
        ls = self.pr.layers.get(self.pr.clip)
        if ls is not None:
            for lay in ls.layers:
                out.update(lay.joints())
        return out

    def _sync_bone_fields(self, j) -> None:
        """선택한 본의 회전/위치/크기 보정 값을 왼쪽 입력칸에 채운다 (신호는 보내지 않는다)."""
        st = self.pr.settings
        prev = self._syncing
        self._syncing = True
        z = (0.0, 0.0, 0.0)
        for nf, x in zip(self.rot_fields, st.rot_offsets.get(j, z) if j is not None else z):
            nf.set_value(x)
        for nf, x in zip(self.pos_fields, st.pos_offsets.get(j, z) if j is not None else z):
            nf.set_value(x)
        self.scale_field.set_value(st.scale_offsets.get(j, 1.0) if j is not None else 1.0)
        self._syncing = prev

    def _rot_joint_changed(self, *_a) -> None:
        self._sync_bone_fields(self.rot_combo.currentData())

    def _pos_changed(self, axis: int, value: float) -> None:
        if self._syncing:
            return
        j = self.rot_combo.currentData()
        if j is None:
            return
        v = list(self.pr.settings.pos_offsets.get(j, (0.0, 0.0, 0.0)))
        v[axis] = value
        if any(abs(x) > 1e-12 for x in v):
            self.pr.settings.pos_offsets[j] = tuple(v)
        else:
            self.pr.settings.pos_offsets.pop(j, None)
        self._after_bone_value_edit(j)

    def _scale_changed(self, value: float) -> None:
        if self._syncing:
            return
        j = self.rot_combo.currentData()
        if j is None:
            return
        if abs(value - 1.0) > 1e-6:
            self.pr.settings.scale_offsets[j] = float(value)
        else:
            self.pr.settings.scale_offsets.pop(j, None)
        self._after_bone_value_edit(j)

    def _after_bone_value_edit(self, j: int) -> None:
        self.viewport.edited_joints = self._edited_set()
        self.btn_bone_reset.setEnabled(self._edited(j))
        self.schedule()

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
        self._after_bone_value_edit(j)

    def _autokey_toggled(self, on: bool) -> None:
        self.cfg["autokey"] = bool(on)
        try:
            bridge.save_config(self.cfg)
        except Exception:  # noqa: BLE001
            pass
        if on:
            if not self.cb_bone_edit.isChecked():
                self.cb_bone_edit.setChecked(True)
            self.statusBar().showMessage("오토키 켜짐 — 본을 고치면 지금 프레임에 키가 찍힙니다 (K = 고른 본에 지금 값으로 키)")

    def _set_tree_open(self, on: bool) -> None:
        self.cfg["bone_tree_open"] = bool(on)
        self.bone_tree.setVisible(bool(on) and self.cb_bone_edit.isChecked())
        try:
            self.btn_tree_fold.setText(("\u25bc" if on else "\u25b6") + " 파츠 목록")
        except Exception:  # noqa: BLE001
            pass
        try:
            bridge.save_config(self.cfg)
        except Exception:  # noqa: BLE001
            pass

    # ---- 뷰포트 본 편집 (파츠 목록 / 클릭 선택 / 끌어서 회전)
    def _bone_edit_toggled(self, on: bool) -> None:
        try:
            self.cb_bone_edit.setText("🦴 본 편집  ON" if on else "🦴 본 편집  OFF")
        except Exception:  # noqa: BLE001
            pass
        self.viewport.bone_edit = bool(on)
        self.bone_tree_box.setVisible(bool(on))
        self.bone_tree.setVisible(bool(on) and bool(self.cfg.get("bone_tree_open", True)))
        self._bone_sig = None
        self._maybe_refresh_bone_ui()
        self.viewport.update()

    # ---- 처음 쓰는 사람용 안내 줄
    def set_guide_visible(self, on: bool) -> None:
        self.guide_box.setVisible(bool(on))
        self.cfg["show_guide"] = bool(on)
        gt = getattr(self, "guide_toggle", None)
        if gt is not None and gt.isChecked() != bool(on):
            gt.setChecked(bool(on))
        if not on:
            self.statusBar().showMessage("안내 줄을 숨겼습니다 — 다시 보려면 F2 또는 아래 [ℹ 안내 줄] 버튼")
        try:
            bridge.save_config(self.cfg)
        except Exception:
            pass

    def current_guide(self):
        pr = self.pr
        n_issues = len(pr.issues) if pr.issues else 0
        return guide_mod.compute(pr.src is not None, pr.tgt is not None, bool(pr.src and pr.tgt and pr.ready()), n_issues, bool(self._exported))

    def _update_guide(self) -> None:
        if not hasattr(self, "guide_steps"):
            return
        g = self.current_guide()
        col = {"done": "#7ad07a", "now": "#ffdc50", "todo": "#7b8496"}
        mark = {"done": "✔ ", "now": "▶ ", "todo": ""}
        self.guide_steps.setText("  ›  ".join('<span style="color:%s;font-weight:%s">%s%s</span>' % (col[st], "bold" if st == "now" else "normal", mark[st], nm) for _k, nm, st in g.states))
        self.guide_hint.setText(g.hint)
        self.guide_btn.setText(g.button)
        self.guide_btn.setVisible(bool(g.button))

    def _guide_action(self) -> None:
        a = self.current_guide().action
        if a == "open_source":
            self.open_source()
        elif a == "open_target":
            self.open_target()
        elif a == "goto_mapping":
            self.goto_tab(1)
        elif a == "goto_export":
            self.goto_tab(4)
        elif a == "open_output":
            out = self.out_edit.text().strip() if hasattr(self, "out_edit") else ""
            if out and os.path.isdir(out):
                osopen.open_in_explorer(out)
            else:
                self.statusBar().showMessage("출력 폴더가 아직 없습니다")

    # ---- 휠 잠금
    def set_wheel_lock(self, on: bool) -> None:
        self.wheel_guard.lock = bool(on)
        self.cfg["wheel_lock"] = bool(on)
        cb = getattr(self, "wheel_cb", None)
        if cb is not None and cb.isChecked() != bool(on):
            cb.setChecked(bool(on))
        self.statusBar().showMessage("슬라이더 휠 잠금 %s — 슬라이더(파란 선) 위에서 휠을 굴려도 %s" % (("켜짐", "값이 바뀌지 않습니다") if on else ("꺼짐", "값이 바뀝니다")))
        try:
            bridge.save_config(self.cfg)
        except Exception:  # noqa: BLE001
            pass

    def toggle_wheel_lock(self) -> None:
        self.set_wheel_lock(not self.wheel_guard.lock)

    def toggle_guide(self) -> None:
        self.set_guide_visible(not self.guide_box.isVisible())

    def show_first_steps(self) -> None:
        QMessageBox.information(self, "처음 쓰는 방법", FIRST_STEPS)

    def _space_changed(self, *_a) -> None:
        sp = self.space_combo.currentData() or "world"
        self.viewport.set_gizmo_space(sp)
        self.cfg["gizmo_space"] = sp
        try:
            bridge.save_config(self.cfg)
        except Exception:
            pass

    def _sync_layer_edited(self) -> None:
        self._bone_sig = None
        self._maybe_refresh_bone_ui()

    def _bone_label(self, j: int) -> str:
        names = self.pr.tgt.skeleton.names if self.pr.tgt else []
        lab = dict((jj, l) for l, jj in self._editable_joints()).get(j)
        if lab:
            return lab
        slot = next((s for s, jj in self.pr.tgt_slots.items() if jj == j), None) if self.pr.tgt else None
        return "%s (%s)" % (slot_label(slot), names[j]) if slot and j < len(names) else (names[j] if j < len(names) else str(j))

    def _refresh_bone_ui(self) -> None:
        """파츠 목록(계층 구조)과 뷰포트의 선택 가능 본/이름/보정 표시를 최신으로 맞춘다."""
        if self.pr.tgt is None:
            self.viewport.pickable, self.viewport.joint_names, self.viewport.edited_joints = set(), {}, set()
            self.bone_tree.clear()
            return
        edj = self._editable_joints()
        joints = [j for _l, j in edj]
        labels = {j: l for l, j in edj}
        jset = set(joints)
        par = self.pr.tgt.skeleton.parents
        self.viewport.pickable = jset
        self.viewport.joint_names = dict(labels)
        self.viewport.edited_joints = self._edited_set()
        sel = self.viewport.selected_joint
        self.bone_tree.blockSignals(True)
        self.bone_tree.clear()
        items: Dict[int, QTreeWidgetItem] = {}

        def parent_of(j: int) -> int:
            a = int(par[j])
            while a >= 0 and a not in jset:
                a = int(par[a])
            return a

        for j in joints:   # 부모가 먼저 오도록 번호 순서가 아니라 계층을 따라 만든다
            chain = []
            k = j
            while k >= 0 and k not in items:
                chain.append(k)
                k = parent_of(k)
            for k in reversed(chain):
                p = parent_of(k)
                it = QTreeWidgetItem([labels.get(k, str(k)) + (" ●" if self._edited(k) else "")])
                it.setData(0, Qt.ItemDataRole.UserRole, k)
                (items[p] if p in items else self.bone_tree.invisibleRootItem()).addChild(it)
                items[k] = it
        self.bone_tree.expandAll()
        if sel in items:
            self.bone_tree.setCurrentItem(items[sel])
        self.bone_tree.blockSignals(False)
        self._update_bone_sel_label()

    def _maybe_refresh_bone_ui(self) -> None:
        """본 편집 패널은 켜져 있을 때만, 타깃/매핑/보정 표시가 바뀌었을 때만 다시 만든다 (끌기 중 매 프레임 갱신 방지)."""
        if not getattr(self, "cb_bone_edit", None) or not self.cb_bone_edit.isChecked():
            return
        sig = (id(self.pr.tgt), tuple(sorted(self.pr.tgt_slots.items())), tuple(sorted(self._edited_set())), self.pr.rig() is not None if self.pr.src else None)
        if sig != getattr(self, "_bone_sig", None):
            self._bone_sig = sig
            self._refresh_bone_ui()
        self.viewport.edited_joints = self._edited_set()

    def _update_bone_sel_label(self) -> None:
        j = self.viewport.selected_joint
        on = j is not None and self.pr.tgt is not None
        self.bone_sel_label.setText("선택: " + self._bone_label(j) if on else "파츠를 고르세요 (목록 또는 뷰포트 점 클릭)")
        self.btn_bone_reset.setEnabled(bool(on and self._edited(j)))

    def _select_bone(self, j: int, from_tree: bool = False) -> None:
        """파츠 선택: 뷰포트 표시, 파츠 목록, 왼쪽 '본별 회전 보정' 콤보를 모두 같은 본으로 맞춘다."""
        self.viewport.selected_joint = j
        i = self.rot_combo.findData(j)
        if i >= 0 and self.rot_combo.currentIndex() != i:
            self.rot_combo.setCurrentIndex(i)
        self._sync_bone_fields(j)
        if not from_tree:
            for k in range(self.bone_tree.topLevelItemCount()):
                self._tree_select(self.bone_tree.topLevelItem(k), j)
        self._update_bone_sel_label()
        ikp = self.__dict__.get("ik_panel")
        if ikp is not None:
            ikp.sync_from_bone(j)                # 고른 본이 팔다리 체인에 속하면 'IK 구간 직접 지정'의 대상도 같이 맞춘다
        if getattr(self, "curve_editor", None) is not None:
            self.curve_editor.refresh()
        self.viewport.update()

    def _tree_select(self, it, j: int) -> bool:
        if it.data(0, Qt.ItemDataRole.UserRole) == j:
            self.bone_tree.blockSignals(True)
            self.bone_tree.setCurrentItem(it)
            self.bone_tree.blockSignals(False)
            return True
        return any(self._tree_select(it.child(c), j) for c in range(it.childCount()))

    def _bone_tree_clicked(self, item, _col=0) -> None:
        j = item.data(0, Qt.ItemDataRole.UserRole)
        if j is not None:
            self._select_bone(int(j), from_tree=True)

    def _bone_ctrl_clicked(self, j: int) -> None:
        self.layer_panel.key_bone(int(j))

    def _bone_picked(self, j: int) -> None:
        self._select_bone(int(j))

    def _bone_drag_started(self) -> None:
        self._bone_drag_open = True
        self._ik_drag = None

    def _bone_dragged(self, j: int, q_view) -> None:
        """뷰포트에서 본을 끌어 돌린 만큼을 '본별 회전 보정'에 더한다 (루트 Yaw 가 있으면 그만큼 되돌려서 화면에 보이는 방향대로 돈다).
        보정 레이어 키 기록이 켜져 있으면 대신 그 레이어의 현재 프레임 키로 기록한다."""
        if self.layer_panel.recording():
            if self.layer_panel.on_rot_drag(j, q_view):
                self.viewport.preview_rot(j, q_view)       # 전체 계산을 기다리지 않고 지금 프레임을 바로 갱신
            return
        self.layer_panel.note_base_edit()
        self.viewport.preview_rot(j, q_view)
        st = self.pr.settings
        yaw = Q.from_axis_angle(np.array([0.0, 1.0, 0.0]), np.radians(st.root_yaw_deg))
        d = Q.mul(Q.conj(yaw), Q.mul(np.asarray(q_view, float), yaw))
        cur = Q.from_euler(np.array(st.rot_offsets.get(j, (0.0, 0.0, 0.0)), float), "XYZ")
        eul = np.asarray(Q.to_euler(Q.mul(d, cur), "XYZ"), float)
        eul = (eul + 180.0) % 360.0 - 180.0
        if np.max(np.abs(eul)) > 1e-4:
            st.rot_offsets[j] = tuple(float(round(x, 3)) for x in eul)
        else:
            st.rot_offsets.pop(j, None)
        self.viewport.edited_joints = self._edited_set()
        self._sync_bone_fields(j)
        self.btn_bone_reset.setEnabled(self._edited(j))
        self.schedule_live()

    def _bone_view_frame(self):
        """지금 보이는 프레임의 타깃 월드 회전(파일 프레임)과 표준 프레임 변환 Ft (본 이동 끌기를 로컬 벡터로 바꿀 때 쓴다)."""
        vd = self.viewport.vd
        if vd is None or vd.tgt_wr is None or vd.Ft is None:
            return None
        f = min(self.viewport.frame, vd.tgt_wr.shape[0] - 1)
        return vd.tgt_wr[f], vd.Ft

    def _pos_only_toggled(self, on: bool) -> None:
        self.cfg["pos_only"] = bool(on)
        self._ik_drag = None

    def _foot_ik_on(self, end: int) -> bool:
        """본 end 가 'IK 가 실제로 걸리는 팔다리'의 끝인가 (발: 발 IK 가 켜짐 / 손: 손 IK 가 켜져 있고 바닥에 닿는 구간이 있음).
        이런 끝을 위/아래 팔다리 회전으로 옮기면 바로 뒤의 IK 가 원래 목표로 되돌려 버리므로 IK 목표 자체를 옮겨야 한다."""
        if not self.pr.ready():
            return False
        try:
            for c in self.pr.chains():
                if int(c.foot) != int(end):
                    continue
                if c.kind == "foot":
                    return bool(self.pr.settings.foot_ik)
                if c.kind == "hand" and self.pr.settings.hand_ik:
                    res = self.pr.result
                    return any(getattr(p, "applied", False) and p.label == c.label for p in (getattr(res, "plans", None) or []))
            return False
        except Exception:  # noqa: BLE001
            return False

    def _pos_ik_move(self, j: int, dview) -> bool:
        """손/발 끝을 옮기면 위팔·아래팔(허벅지·정강이)을 돌려 IK 처럼 따라오게 한다 (바이패드식). 해당 안 되면 False — 그 본만 옮기는 기존 방식으로."""
        if self.pr.tgt is None or self.cb_pos_only.isChecked():
            return False
        ch = posik.end_chain(self.pr.tgt_slots, j)
        vd = self.viewport.vd
        if ch is None or vd is None or vd.tgt_pos is None:
            return False
        up, lo, end, _name = ch
        if not self.layer_panel.recording() and self._foot_ik_on(end):
            # '원본' 상태 + 발 IK 가 켜진 다리: 다리 회전 보정으로 넣으면 바로 뒤의 발 IK 가 발을 원래 목표로 되돌린다 -> IK 목표 자체를 옮긴다 (전체 프레임)
            self.layer_panel.note_base_edit()
            self.viewport.preview_move(j, dview)
            st = self.pr.settings
            new = np.array(st.ik_offsets.get(end, (0.0, 0.0, 0.0)), dtype=float) + np.asarray(dview, dtype=float)
            if np.max(np.abs(new)) > 1e-9:
                st.ik_offsets[end] = tuple(float(round(x, 5)) for x in new)
            else:
                st.ik_offsets.pop(end, None)
            self.viewport.edited_joints = self._edited_set()
            self.btn_bone_reset.setEnabled(self._edited(j))
            self.schedule_live()
            return True
        f = min(self.viewport.frame, vd.tgt_pos.shape[0] - 1)
        P = vd.tgt_pos[f]
        if self._ik_drag is None or self._ik_drag[0] != j:
            self._ik_drag = [int(j), np.array(P[end], dtype=float), np.zeros(3)]   # 끌기를 시작한 순간의 끝 위치 + 지금까지 끈 합 = 절대 목표
        self._ik_drag[2] = self._ik_drag[2] + np.asarray(dview, dtype=float)
        T = self._ik_drag[1] + self._ik_drag[2]
        sol = posik.solve(P[up], P[lo], P[end], T)
        if sol is None:
            return False
        q1, q2, reach = sol
        self._bone_dragged(up, q1)
        self._bone_dragged(lo, q2)
        if not reach:
            self.statusBar().showMessage("팔다리가 다 펴져서 더 멀리는 못 가요")
        return True

    def _bone_moved(self, j: int, dview) -> None:
        """뷰포트에서 본을 끌어 옮긴 만큼(표준 프레임 월드 이동)을 '본별 위치 보정'(바인드 포즈 기준)에 더한다."""
        if self.pr.tgt is None:
            return
        if self._pos_ik_move(j, dview):
            return
        if self.layer_panel.recording():
            if self.layer_panel.on_pos_drag(j, dview):
                self.viewport.preview_move(j, dview)
            return
        self.layer_panel.note_base_edit()
        self.viewport.preview_move(j, dview)
        st = self.pr.settings
        dview = np.asarray(dview, dtype=float)
        vf = self._bone_view_frame()
        sk = self.pr.tgt.skeleton
        p = int(sk.parents[j])
        yaw = Q.from_axis_angle(np.array([0.0, 1.0, 0.0]), np.radians(st.root_yaw_deg))
        if vf is None:
            d_bind = dview
        else:
            Wr, Ft = vf
            dw = Q.rotate(Q.conj(Ft), dview)                       # 파일 프레임 월드 이동
            if p < 0:
                d_bind = Q.rotate(Q.conj(yaw), dview)               # 루트: 월드 이동 (Yaw 되돌림)
            else:
                local = Q.rotate(Q.conj(Wr[p]), dw)                 # 지금 프레임의 부모 로컬 벡터
                bwr, _ = sk.bind_world()
                d_bind = Q.rotate(Ft, Q.rotate(bwr[p], local))      # 바인드 포즈 기준 표준 프레임 벡터
        cur = np.array(st.pos_offsets.get(j, (0.0, 0.0, 0.0)), dtype=float)
        new = cur + d_bind
        if np.max(np.abs(new)) > 1e-9:
            st.pos_offsets[j] = tuple(float(round(x, 5)) for x in new)
        else:
            st.pos_offsets.pop(j, None)
        self.viewport.edited_joints = self._edited_set()
        self._sync_bone_fields(j)
        self.btn_bone_reset.setEnabled(self._edited(j))
        self.schedule_live()

    def _bone_scaled(self, j: int, factor: float) -> None:
        if self.layer_panel.recording():
            self.statusBar().showMessage("크기(R)는 보정 레이어에 기록되지 않습니다 — 키 기록을 끄면 '본별 크기 보정'으로 적용돼요")
            return
        st = self.pr.settings
        new = float(np.clip(st.scale_offsets.get(j, 1.0) * float(factor), 0.05, 20.0))
        if abs(new - 1.0) > 1e-4:
            st.scale_offsets[j] = round(new, 4)
        else:
            st.scale_offsets.pop(j, None)
        self.viewport.edited_joints = self._edited_set()
        self._sync_bone_fields(j)
        self.btn_bone_reset.setEnabled(self._edited(j))
        self.schedule_live()

    def set_bone_tool(self, tool: str, enable: bool = False) -> None:
        """본 편집 도구 바꾸기 (3ds Max 키: Q 선택 · W 이동 · E 회전 · R 크기). enable=True 면 본 편집이 꺼져 있을 때 켠다."""
        if tool not in ("select", "move", "rotate", "scale"):
            return
        self.viewport.bone_tool = tool
        for k, b in self.tool_buttons.items():
            b.setChecked(k == tool)
        if enable and not self.cb_bone_edit.isChecked():
            self.cb_bone_edit.setChecked(True)
        self.viewport.update()

    def _bone_reset_selected(self) -> None:
        j = self.viewport.selected_joint
        if j is None:
            return
        # 손·발 끝은 옮길 때 위/아래 팔다리 회전으로 기록되므로(IK 방식) 그 두 본도 같이 되돌린다
        ch = posik.end_chain(self.pr.tgt_slots, j) if self.pr.tgt is not None else None
        js = [int(j)] + ([int(ch[0]), int(ch[1])] if ch is not None else [])
        cur = self.layer_panel.layer()
        n_keys = 0
        if cur is not None:                       # 레이어를 보고 있으면 먼저 그 레이어의 키를 지운다
            for b in js:
                n_keys += len(cur.frames(b))
                cur.clear_joint(b)
        n_base = 0
        if cur is None or n_keys == 0:            # '원본' 상태이거나 이 레이어에 그 본의 키가 없으면 전체 구간 '본별 보정'을 지운다
            for d in (self.pr.settings.rot_offsets, self.pr.settings.pos_offsets, self.pr.settings.scale_offsets, self.pr.settings.ik_offsets):
                for b in js:
                    n_base += 1 if d.pop(b, None) is not None else 0
        if n_keys:
            self.statusBar().showMessage("본 초기화: 레이어 '%s' 에서 키 %d개를 지웠습니다" % (cur.name, n_keys))
        elif n_base:
            self.statusBar().showMessage("본 초기화: 전체 구간 '본별 보정'을 지웠습니다")
        else:
            others = [l.name for l in self.pr.layer_stack().layers if l is not cur and any(l.frames(b) for b in js)]
            self.statusBar().showMessage(("이 본의 수정은 다른 레이어(%s)에 있습니다 — 그 레이어를 고른 뒤 초기화하세요" % ", ".join(others)) if others else "이 본에는 되돌릴 수정이 없습니다")
        self._fill_joint_combos()
        self.viewport.edited_joints = self._edited_set()
        self._sync_bone_fields(j)
        self._refresh_bone_ui()
        self.schedule()

    def _rot_reset(self) -> None:
        self.pr.settings.rot_offsets = {}
        self.pr.settings.pos_offsets = {}
        self.pr.settings.scale_offsets = {}
        self.pr.settings.ik_offsets = {}
        self.viewport.edited_joints = self._edited_set()
        self._fill_joint_combos()
        self.schedule()

    # ================================================================== 재계산 / 표시
    def schedule(self) -> None:
        if not self._syncing:
            self.recompute_timer.start()

    def _refresh_knee_status(self) -> None:
        """무릎 보정 두 슬라이더 아래에 '이 클립에서 몇 프레임에 걸렸나'를 적는다."""
        lab = getattr(self, "knee_status", None)
        if lab is None:
            return
        res = self.pr.result
        st = self.pr.settings
        if res is None or not getattr(res, "plans", None):
            lab.setText("")
            return
        lines = []
        for c, pl in zip(res.chains, res.plans):
            if c.kind != "foot" and not pl.applied:
                continue
            if not pl.applied:
                continue
            lines.append("%s: 방향 고정 %d프레임 · 접힘 방지 %d프레임 · 역꺾임 고침 %d프레임" % (c.label, pl.knee_stable_frames, pl.knee_fold_frames, getattr(pl, "knee_reverse_frames", 0)))
        if not lines:
            lab.setText("지금 IK 가 적용된 다리/팔이 없어 무릎 보정도 쓰이지 않습니다 (IK 사용을 켜 보세요).")
        else:
            tot = sum(pl.knee_stable_frames + pl.knee_fold_frames for pl in res.plans if pl.applied)
            lines.append("→ 지금 클립에서 보정이 걸린 프레임 수입니다." + ("" if tot else " 0 이면 이 클립엔 보정이 필요한 장면이 없다는 뜻이라 슬라이더를 바꿔도 달라지지 않아요."))
            lab.setText("\n".join(lines))

    def recompute(self, reframe: bool = False, live: bool = False) -> None:
        """live=True: 본을 끄는 중의 가벼운 갱신 (품질 분석·목록/안내 갱신을 건너뛴다)."""
        err = None
        if self.pr.ready():
            try:
                self.pr.run(analyze_quality=not live)
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
        if getattr(self, "curve_editor", None) is not None:
            self.curve_editor.refresh()
        if live:
            return
        self._update_guide()
        self.retime_bar.refresh()
        if getattr(self, "layer_panel", None) is not None:
            self.layer_panel.refresh()
        if getattr(self, "ik_panel", None) is not None:
            self.ik_panel.refresh()
        self._refresh_knee_status()
        self._update_info(err)
        self._update_quality()
        self._maybe_refresh_bone_ui()

    def _update_info(self, err: Optional[str]) -> None:
        self._update_axis_notes()
        if getattr(self, "btn_unload_src", None) is not None:
            self.btn_unload_src.setEnabled(self.pr.src is not None)
            self.btn_unload_tgt.setEnabled(self.pr.tgt is not None)
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
            if getattr(self.pr, "rig_note", ""):
                warns.append(self.pr.rig_note)
        elif self.pr.src and self.pr.tgt and not self.pr.pairs():
            lines.append("<span style='color:#e0a050'>자동 매핑된 본이 없습니다. 아래 표에서 직접 연결하세요.</span>")
        if err:
            warns.append("리타겟 실패: " + err)
        self.info_label.setText("<br>".join(lines))
        self.warn_label.setText("\n".join(w if w.startswith("ℹ") else "⚠ " + w for w in warns))

    def _update_quality(self) -> None:
        self.issue_list.clear()
        if self.pr.result is None:
            self.quality_summary.setText("리타겟 결과가 있으면 문제 구간을 자동으로 찾아 줍니다.")
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

    def _refill_clip_combo(self) -> None:
        """pr.src.animations 에 맞춰 클립 콤보박스와 모션 목록을 다시 채운다 (보던 클립 번호는 pr.clip)."""
        prev = self._syncing
        self._syncing = True
        try:
            self.clip_combo.clear()
            if self.pr.src is not None:
                for i, a in enumerate(self.pr.src.animations):
                    self.clip_combo.addItem("%d: %s (%d프레임, %.0ffps)" % (i, a.name, a.n_frames, a.fps))
                self.clip_combo.setCurrentIndex(min(max(self.pr.clip, 0), self.clip_combo.count() - 1))
        finally:
            self._syncing = prev
        self._refresh_clip_list()

    def _refresh_clip_list(self) -> None:
        cp = getattr(self, "clip_panel", None)
        if cp is not None:
            cp.refresh()

    def _select_clip_widgets(self) -> None:
        """pr.clip 에 맞춰 콤보박스·목록 줄을 맞춘다 (시그널 없이)."""
        prev = self._syncing
        self._syncing = True
        try:
            if self.pr.src is not None and 0 <= self.pr.clip < self.clip_combo.count():
                self.clip_combo.setCurrentIndex(self.pr.clip)
        finally:
            self._syncing = prev
        cp = getattr(self, "clip_panel", None)
        if cp is not None:
            cp.refresh()

    def _clip_changed(self, i: int) -> None:
        if self._syncing or i < 0 or self.pr.src is None:
            return
        skel_before = self.pr.src.skeleton
        self.pr.clip = i
        self._syncing = True
        self._sync_settings_ui()                      # 설정은 클립마다 따로라 화면의 값도 그 클립 것으로
        self._select_clip_widgets()
        self._syncing = False
        new_skel = self.pr.src.skeleton is not skel_before        # 본 구조가 다른 몬스터의 모션으로 넘어감: 소스 매핑 표·정보를 그 구조로
        if new_skel:
            self._fill_mapping()
        self.recompute(reframe=new_skel)
        if new_skel:
            self.statusBar().showMessage("다른 본 구조의 모션입니다 — 소스 본 %d개 · 매핑도 이 모션 것으로 바뀌었습니다" % self.pr.src.skeleton.n)

    # ---- 타임라인
    def _frame_changed(self, f: int) -> None:
        n = self.vd.n_frames if self.vd else 1
        fps = self.vd.fps if self.vd else 30.0
        self.viewport.set_frame(f)
        if getattr(self, "retime_bar", None) is not None:
            self.retime_bar.set_frame(f)
        if getattr(self, "curve_editor", None) is not None:
            self.curve_editor.set_frame(f)
        self.frame_label.setText("%d / %d  (%.2fs)" % (f + 1, n, f / fps if fps else 0.0))

    def _toggle_play(self, on: bool) -> None:
        self.btn_play.setText("⏸ 정지" if on else "▶ 재생")
        self.viewport.lod = bool(on)  # 재생 중에는 메시를 더 가볍게 그리고, 멈추면 전체 품질로 다시 그린다
        self.viewport._mesh_ms = 0.0
        self.viewport.update()
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

    def _quality_changed(self, *_a) -> None:
        self.viewport.mesh_quality = self.quality_combo.currentData() or "auto"
        self.cfg["mesh_quality"] = self.viewport.mesh_quality
        bridge.save_config(self.cfg)
        self.viewport.update()

    def _reframe(self) -> None:
        if self.vd is not None:
            self.viewport.cam.frame(self.vd.center, self.vd.height)
            self.viewport.update()

    # ================================================================== 내보내기
    def _pick_biped_template(self) -> None:
        f, _x = QFileDialog.getOpenFileName(self, "스키닝된 3ds Max 파일 (Biped 가 든 템플릿)", self.biped_tpl.text() or self.cfg.get("last_dir", ""), "3ds Max (*.max);;모든 파일 (*)")
        if f:
            self.biped_tpl.setText(f)

    def _pick_max_exe(self) -> None:
        f, _x = QFileDialog.getOpenFileName(self, "3ds Max 실행 파일 (3dsmaxbatch.exe 또는 3dsmax.exe)", self.max_exe.text() or "C:/Program Files/Autodesk", "실행 파일 (*.exe);;모든 파일 (*)")
        if f:
            self.max_exe.setText(f)

    def _pick_out(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "출력 폴더", self.out_edit.text())
        if d:
            self.out_edit.setText(d)

    def do_export_as(self) -> None:
        if self.pr.src is None:
            QMessageBox.warning(self, "내보내기", "먼저 소스 모션을 여세요.")
            return
        cur = self.pr.src.animations[self.pr.clip].name if self.pr.src.animations else "motion"
        name, ok = QInputDialog.getText(self, "다른 이름으로 내보내기", "파일 이름 (확장자 제외):", text=cur)
        if ok and name.strip():
            self.do_export(name.strip())

    def _export_clips(self) -> list:
        """내보낼 클립 번호들: 왼쪽 모션 목록에서 체크한 모션이 있으면 그것 전부, 없으면 라디오 선택(현재 클립 / 모든 클립)."""
        n_clips = len(self.pr.src.animations) if self.pr.src is not None else 0
        cp = self.__dict__.get("clip_panel")
        checked = cp.checked_clips() if cp is not None else []
        if checked:
            return [i for i in checked if i < n_clips]
        return list(range(n_clips)) if self.rb_clip_all.isChecked() else [self.pr.clip]

    def _update_clip_export_hint(self) -> None:
        lb = self.__dict__.get("clip_hint")
        if lb is None:
            return
        cp = self.__dict__.get("clip_panel")
        n = len(cp.checked_clips()) if cp is not None else 0
        lb.setText(("☑ 모션 목록에서 체크한 %d개를 내보냅니다 (아래 선택보다 우선)" % n) if n else "")

    def do_export(self, name: str = "") -> None:
        if self.pr.src is None:
            QMessageBox.warning(self, "내보내기", "먼저 소스 모션을 여세요.")
            return
        what = "retargeted" if self.rb_retarget.isChecked() else "source"
        if what == "retargeted" and not self.pr.ready():
            QMessageBox.warning(self, "내보내기", "리타겟하려면 타깃 모델과 매핑이 필요합니다.")
            return
        presets = [k for k, cb in self.cb_preset.items() if cb.isChecked()]
        fmts = [k for k, cb in self.cb_fmt.items() if cb.isChecked()]
        tpl_max = self.biped_tpl.text().strip()
        if tpl_max and "bip" not in fmts:
            fmts.append("bip")   # 스키닝된 .max 를 골랐으면 그 파일에 모션을 적용하는 단계를 함께 한다 (검수자가 고른 방식으로)
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
        self.cfg["export_organize"] = bool(self.cb_organize.isChecked())
        self.cfg["export_char"] = self.char_edit.text().strip()
        bridge.save_config(self.cfg)
        clips = self._export_clips()
        opt = ExportOptions(
            out_dir=out, presets=presets or ["blender"], formats=fmts or ["bvh"], include_skeleton=skel, what=what, clips=clips,
            include_mesh=self.cb_mesh.isChecked(), write_skeleton_json=self.cb_skel_json.isChecked(), blender=self.pr.blender,
            edit=self._export_edit(), organize=bool(self.cb_organize.isChecked()), char_name=self.char_edit.text().strip(),
            on_exist=self._exist_policy(), name=name if isinstance(name, str) else "", biped_template=self.biped_tpl.text().strip(),
            max_method=self.max_method.currentData() or "biped", max_auto=bool(self.cb_max_auto.isChecked()), max_exe=self.max_exe.text().strip(),
        )
        self.cfg["biped_template"] = self.biped_tpl.text().strip()
        self.cfg["max_method"] = opt.max_method
        self.cfg["max_auto"] = opt.max_auto
        self.cfg["max_exe"] = opt.max_exe
        self.edit_panel.save()
        self.rt_edit.save()
        bridge.save_config(self.cfg)
        self.pr.edit_info = None
        clip0 = self.pr.clip
        self.export_log.clear()

        def job(w: Worker):
            return self.pr.export(opt, progress=lambda i, n, nm: w.progress.emit(i, n, nm))

        def done(res):
            files, warns = res
            self.pr.clip = clip0
            self.export_log.appendPlainText("완료: 파일 %d개" % len(files))
            if opt.edit is not None and opt.edit.active() and self.pr.edit_info is not None:  # 마지막 클립 기준 요약
                self.export_log.appendPlainText("가공(%s): %s" % (opt.edit.describe(), self.pr.edit_info.summary()))
            for f in files:
                self.export_log.appendPlainText("  " + f)
            for bk in self.pr.backups:
                self.export_log.appendPlainText("이전 파일 보관: " + bk)
            for wmsg in warns:
                self.export_log.appendPlainText("경고: " + wmsg)
            self._exported = bool(files)
            self.recompute()
            self.statusBar().showMessage("내보내기 완료: %d개" % len(files))

        self.run_async(job, done, "내보내는 중")


def _dark_palette(app: QApplication, mode: str = "dark") -> None:
    _theme.apply_theme(app, mode)


def _old_dark_palette(app: QApplication) -> None:   # 예전 구현 (참고용, 쓰지 않음)
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


def run_gui(handoff: str = "") -> int:
    if handoff and animlink.forward_open(handoff):  # 이미 켜진 MotionBridge 가 있으면 그 창에 넘기고 끝낸다
        return 0
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("MotionBridge")
    try:
        _dark_palette(app, str(bridge.load_config().get("theme", "dark")))
    except Exception:  # noqa: BLE001
        _dark_palette(app)
    w = MainWindow()
    w.resize(1600, 920)
    w.show()
    w.start_link()
    app.aboutToQuit.connect(w.stop_link)
    if handoff:
        QTimer.singleShot(0, lambda: w.handoff(handoff))
    return app.exec()


if __name__ == "__main__":
    sys.exit(run_gui())
