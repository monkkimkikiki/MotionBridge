"""'영상으로 찾기' 탭: 영상 속 캐릭터를 추측 -> 검수자가 고침 -> 메시/애니메이션 후보 -> 선택 추출.

흐름
  1) 영상/이미지(또는 AnimRef 클립 폴더)를 넣는다.
  2) 인식 방식(Ollama / Claude)을 고르고 [캐릭터 추측]. 게임 에셋 이름 목록 안에서 고르게 시킨다.
  3) 검수 표: 장면 이미지와 추측 이름이 보이고, 틀리면 후보 목록/검색으로 메시를 바꾼다. 고친 결과는 기억한다.
  4) [검수 확정]: 메시 목록과, 그 캐릭터의 애니메이션 후보(이름 일치 + 분류 + 타수 점수)가 나온다.
  5) 체크한 메시/애니메이션을 기존 추출 흐름(Models/, Animations/)으로 뽑는다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from typing import Dict, List, Optional

from PySide6.QtCore import QMimeData, QRect, QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QInputDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QListWidget, QRadioButton,
    QHeaderView, QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import __version__
from ..core import animlink, animref, assetidx, bridge, charcatalog, extractor, framepick, frames, gamenames, layout, loader, motionmatch, motionsearch, movecatalog, ollama_setup as setup_mod, pairing, panel, screenname, skelgraph, textmatch, vision
from .viewer import ASSET_MIME
from .widgets import CollapsibleBox, EditPanel, fit_widths, wrappable

INDEX_CACHE = os.path.join(bridge.CONFIG_DIR, "assets.json")
MEMORY_FILE = os.path.join(bridge.CONFIG_DIR, "matches.json")
NAMES_CACHE = os.path.join(bridge.CONFIG_DIR, "names.json")
LOCRES_DIR = os.path.join(bridge.CONFIG_DIR, "locres")
FRAME_DIR = os.path.join(bridge.CONFIG_DIR, "vf_frames")
MESH_RX = re.compile(r"(?i)/sk[a-z]*_[^/]*$")
ANIM_RX = re.compile(r"(?i)anim")
LIST_PATTERN = r"(?i)(/sk[a-z]*_|/sm[a-z]{0,2}_|anim)"
STATIC_RX = re.compile(r"(?i)/sm[a-z]{0,2}_[^/]*$")
class _Vocab(list):
    """AI 에게 줄 이름 목록. ids=True 면 각 줄이 '<ID> = <게임 속 이름> [종류]' (캐릭터 카탈로그)."""
    ids = False


MONSTER_DIRS = re.compile(r"(?i)/(monsters?|enemies|enemy|bosses|boss|creatures?|npcs?)/")
LABEL_CHOICES = ["타격", "피격", "스킬", "연출", "회피", "사망", "걷기", "달리기", "대기"]
PICK_FILTER = "영상/이미지 (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.png *.jpg *.jpeg *.webp);;모든 파일 (*)"


class _Row:
    """검수 표의 한 줄."""

    def __init__(self, group: vision.Group, combo, search, exclude, base: List[assetidx.MeshCandidate], sure: bool = True) -> None:
        self.group, self.combo, self.search, self.exclude, self.base, self.sure = group, combo, search, exclude, base, sure

    def chosen(self) -> Optional[str]:
        if self.exclude.isChecked():
            return None
        return self.combo.currentData() or None


def _thumb(frame: str, box) -> Optional[object]:
    """장면 이미지(캐릭터 위치가 있으면 그 부분)를 작은 그림으로. 읽지 못하면 None."""
    if not frame or not os.path.isfile(frame):
        return None
    try:
        pm = QPixmap(frame)
        if pm.isNull() is True:
            return None
        if box:
            w, h = pm.width(), pm.height()
            x0, y0, x1, y1 = box
            r = QRect(int(x0 * w), int(y0 * h), max(int((x1 - x0) * w), 8), max(int((y1 - y0) * h), 8))
            pm = pm.copy(r)
        return pm.scaled(QSize(168, 96), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    except Exception:  # noqa: BLE001 - 썸네일은 없어도 된다
        return None


MAX_PANEL_NAMES = 150  # AI 회의에 알려 주는 애니메이션 이름 수 (많으면 작은 모델이 헷갈린다)
MAX_MOTION_RANK = 40  # 동작 비교로 한 번에 임시로 뽑아 비교하는 최대 개수 (뽑는 데 시간이 든다)


class _AssetList(QListWidget):
    """고른 메시/애니메이션을 뷰포트로 끌어다 놓을 수 있는 목록 (항목의 UserRole = 게임 안 경로). 놓으면 영상찾기가 직접 열고 탭은 옮기지 않는다."""

    def __init__(self, kind: str) -> None:
        super().__init__()
        self.drag_kind = kind                       # "mesh" | "anim"
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)

    def mimeData(self, items):  # noqa: N802
        md = QMimeData()
        paths: List[str] = []
        for it in items:
            if it.isHidden():
                continue
            p = it.data(Qt.ItemDataRole.UserRole)
            if p and p not in paths:
                paths.append(p)
        md.setData(ASSET_MIME, json.dumps({"kind": self.drag_kind, "paths": paths, "origin": "vidfind"}).encode("utf-8"))
        return md


class VidFindTab(QWidget):
    def __init__(self, win) -> None:
        super().__init__()
        self.win = win
        self.cfg = win.cfg
        self.index: Optional[assetidx.AssetIndex] = None
        self.memory = assetidx.MatchMemory(MEMORY_FILE)
        self.rows: List[_Row] = []
        self.confirmed: List[str] = []
        self.char_mode = False  # True 면 영상 추측과 상관없이 플레이어블 캐릭터 메시/애니메이션 전체 목록을 보여 주는 중
        self.clip_labels: Dict[str, str] = {}
        self.link_query: Optional[dict] = None  # AnimRef 가 보낸 요청 (답을 보내기 전까지)
        self.last_files: List[str] = []         # 방금 추출한 파일 (AnimRef 결과에 넣는다)
        self._build()

    # ================================================================== 화면
    def _build(self) -> None:
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        inner = QWidget()
        sc.setWidget(inner)
        lay = QVBoxLayout(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(sc)

        intro = QLabel("영상(또는 장면 이미지)에 나온 캐릭터를 AI가 추측하고, 검수자가 고친 뒤 그 캐릭터의 메시와 애니메이션을 골라 추출합니다.\n"
                       "먼저 '0. 게임에서 추출' 탭에서 게임 폴더를 검사해 두세요.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.link_banner = QLabel("")
        self.link_banner.setWordWrap(True)
        self.link_banner.setStyleSheet("color:#5ab0ff")
        lay.addWidget(self.link_banner)

        # 1) 입력
        g = CollapsibleBox("1) 영상 / 이미지 / AnimRef 클립")
        v = QVBoxLayout(g)
        self.src_list = QListWidget()
        self.src_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.src_list.setMinimumHeight(90)
        self.src_list.setToolTip("고르지 않으면 목록 전체를 씁니다. 원본이 많으면 고르게 솎아서 씁니다.")
        v.addWidget(self.src_list)
        h = QHBoxLayout()
        b = QPushButton("영상/이미지 추가…")
        b.clicked.connect(self.add_files)
        h.addWidget(b)
        b = QPushButton("AnimRef 클립 폴더…")
        b.setToolTip("AnimRef 가 자른 클립 폴더. 분류(타격/피격/스킬…)를 함께 읽습니다.")
        b.clicked.connect(self.add_animref)
        h.addWidget(b)
        b = QPushButton("AnimRef query.json 열기…")
        b.setToolTip("AnimRef 가 '추출 툴로 보내기' 때 만든 query.json 을 직접 고릅니다 (실시간 연결이 안 될 때). 메뉴 파일 > AnimRef에서 가져오기 와 같습니다.")
        b.clicked.connect(lambda *_a: self.open_query_file())
        h.addWidget(b)
        b = QPushButton("목록 비우기")
        b.clicked.connect(self.clear_sources)
        h.addWidget(b)
        h.addStretch(1)
        h.addWidget(QLabel("원본당 장면 수"))
        self.per = QSpinBox()
        self.per.setRange(1, 12)
        self.per.setValue(int(self.cfg.get("vf_per", 4)))
        h.addWidget(self.per)
        v.addLayout(h)
        self.src_note = QLabel("")
        self.src_note.setWordWrap(True)
        v.addWidget(self.src_note)
        lay.addWidget(g)

        # 2) 인식 방식
        g = CollapsibleBox("2) 인식 방식 (검수자가 고릅니다)")
        v = QVBoxLayout(g)
        h = QHBoxLayout()
        self.engine = QComboBox()
        self.engine.addItem(vision.OllamaProvider.label, "ollama")
        self.engine.addItem(vision.GptOssProvider.label, "gptoss")
        self.engine.addItem(vision.ClaudeProvider.label, "claude")
        self.engine.addItem(vision.OpenAICompatProvider.label, "openai")
        self.engine.setCurrentIndex(max(self.engine.findData(self.cfg.get("vf_engine", "ollama")), 0))
        self.engine.currentIndexChanged.connect(self._engine_changed)
        self.engine.activated.connect(self._engine_picked)   # 사용자가 직접 골랐을 때만 (설치 안내)
        h.addWidget(self.engine, 1)
        h.addWidget(QLabel("모델"))
        self.model = QLineEdit()
        self.model.setToolTip("Ollama 예: %s\nClaude 예: %s\n다른 AI: 이미지를 볼 수 있는 모델 이름 (gpt-4o, gemini-2.5-flash …)" % (", ".join(vision.OLLAMA_MODELS), ", ".join(vision.CLAUDE_MODELS)))
        h.addWidget(self.model, 1)
        b = QPushButton("연결 확인")
        b.clicked.connect(self.check_engine)
        h.addWidget(b)
        self.btn_pull = QPushButton("모델 받기")
        self.btn_pull.setToolTip("Ollama 에 위 모델을 내려받습니다 (약 6GB, 한 번만). Ollama 가 켜져 있어야 합니다.")
        self.btn_pull.clicked.connect(self.pull_model)
        h.addWidget(self.btn_pull)
        self.btn_setup = QPushButton("자동 설치·준비")
        self.btn_setup.setToolTip("Ollama 가 없으면 설치하고, 꺼져 있으면 실행하고, 모델이 없으면 받습니다 (필요한 것만 순서대로). 처음엔 수 GB 를 받으니 시간이 걸립니다.")
        self.btn_setup.clicked.connect(self.setup_ollama)
        h.addWidget(self.btn_setup)
        v.addLayout(h)
        self.oa_row = QWidget()  # 다른 AI(OpenAI 호환) 전용: 서비스 고르기 + 서버 주소
        oh = QHBoxLayout(self.oa_row)
        oh.setContentsMargins(0, 0, 0, 0)
        oh.addWidget(QLabel("서비스"))
        self.oa_preset = QComboBox()
        for nm, _u, _m, _e in vision.OPENAI_PRESETS:
            self.oa_preset.addItem(nm, nm)
        self.oa_preset.setCurrentIndex(max(self.oa_preset.findData(self.cfg.get("vf_oa_preset", vision.OPENAI_PRESETS[0][0])), 0))
        self.oa_preset.currentIndexChanged.connect(self._preset_changed)
        oh.addWidget(self.oa_preset)
        oh.addWidget(QLabel("서버 주소"))
        self.oa_url = QLineEdit(self.cfg.get("vf_oa_url", ""))
        self.oa_url.setPlaceholderText("비워 두면 서비스의 기본 주소")
        oh.addWidget(self.oa_url, 1)
        self.btn_models = QPushButton("모델 목록")
        self.btn_models.setToolTip("서버에서 쓸 수 있는 모델 이름을 불러와 고릅니다 (이미지를 볼 수 있어 보이는 것이 위쪽). 무료 모델은 자주 바뀌므로 여기서 확인하세요.")
        self.btn_models.clicked.connect(lambda *_a: self.load_models())
        oh.addWidget(self.btn_models)
        v.addWidget(self.oa_row)
        self.oa_info = QLabel("")
        self.oa_info.setWordWrap(True)
        self.oa_info.setOpenExternalLinks(True)
        self.oa_info.setStyleSheet("color:#8a93a6")
        v.addWidget(self.oa_info)
        self._keys = {"claude": self.cfg.get("vf_claude_key", ""), "openai": self.cfg.get("vf_openai_key", "")}
        self.key_row = QWidget()
        kh = QHBoxLayout(self.key_row)
        kh.setContentsMargins(0, 0, 0, 0)
        self.key_label = QLabel("Claude API 키")
        kh.addWidget(self.key_label)
        self.key = QLineEdit(self._keys["claude"])
        self.key.textChanged.connect(self._remember_key)
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("sk-ant-… (비워 두면 환경변수 ANTHROPIC_API_KEY 사용)")
        kh.addWidget(self.key, 1)
        self.save_key = QCheckBox("이 PC에 저장")
        self.save_key.setChecked(bool(self.cfg.get("vf_save_key", False)))
        self.save_key.setToolTip("체크하면 키가 사용자 폴더의 설정 파일에 평문으로 저장됩니다. 공용 PC에서는 체크하지 마세요.")
        kh.addWidget(self.save_key)
        v.addWidget(self.key_row)
        h = QHBoxLayout()
        h.addWidget(QLabel("게임 이름(선택)"))
        self.hint = QLineEdit(self.cfg.get("vf_hint", ""))
        self.hint.setPlaceholderText("예: Mortal Shell 2 — 적으면 인식에 도움이 됩니다")
        h.addWidget(self.hint, 1)
        h.addWidget(QLabel("이름 필터(정규식)"))
        self.filter = QLineEdit(self.cfg.get("vf_filter", ""))
        self.filter.setPlaceholderText("예: (?i)(monster|boss) — 비우면 자동")
        self.filter.setToolTip("AI에게 알려줄 후보 이름을 경로로 줄입니다. 무기/소품이 섞이면 정확도가 떨어집니다.")
        h.addWidget(self.filter, 1)
        v.addLayout(h)
        v.addWidget(QLabel("아는 정보 (선택) — 이 영상에 나오는 캐릭터를 한 줄에 하나씩: 게임 속 이름 — 메모"))
        self.known = QPlainTextEdit()
        self.known.setPlainText(self.cfg.get("vf_known", ""))
        self.known.setPlaceholderText("예)\nThe Nameless Captive — 보스, 쇠사슬에 묶인 인간형\nDwarf Warrior\n(비워도 됩니다. 적으면 이름이 달라도 리소스를 먼저 찾아 AI에게 알려 주고, AI가 놓친 캐릭터도 표에 넣어 줍니다)")
        self.known.setMaximumHeight(90)
        v.addWidget(self.known)
        self.engine_note = QLabel("")
        self.engine_note.setWordWrap(True)
        v.addWidget(self.engine_note)
        lay.addWidget(g)
        self._engine_changed()

        # 3) 추측
        g = CollapsibleBox("3) 캐릭터 추측")
        v = QVBoxLayout(g)
        h = QHBoxLayout()
        self.min_conf = QSpinBox()
        self.min_conf.setRange(0, 100)
        self.min_conf.setSuffix("%")
        self.min_conf.setValue(int(self.cfg.get("vf_min_conf", 60)))
        self.min_conf.setToolTip("AI 확신이 이 값보다 낮으면 메시를 자동으로 고르지 않고 '직접 고르세요'로 둡니다 (엉뚱한 캐릭터로 넘어가는 것을 막음).")
        r = QHBoxLayout()
        self.rb_mode_monster = QRadioButton("몬스터 찾기 (영상으로 AI 추측)")
        self.rb_mode_char = QRadioButton("캐릭터 메쉬 찾기 (캐릭터 모델링 전체 검색)")
        self.rb_mode_monster.setToolTip("영상 속 몬스터를 AI가 게임의 메시 이름 안에서 추측합니다 (아래 4) 검수 표).")
        self.rb_mode_char.setToolTip("플레이어블·NPC 등 캐릭터 모델링을 AI 없이 이름으로 전체 검색합니다. 리깅된 것(SK_)과 리깅 없는 것(SM_)을 나눠 볼 수 있고, "
                                     "고른 캐릭터에 해당하는 애니메이션 클립만 보여 줍니다.")
        on_char = bool(self.cfg.get("vf_mode_char", False))
        self.rb_mode_char.setChecked(on_char)
        self.rb_mode_monster.setChecked(not on_char)
        r.addWidget(self.rb_mode_monster)
        r.addWidget(self.rb_mode_char)
        r.addStretch(1)
        v.addLayout(r)
        self.char_panel = QWidget()
        cp = QVBoxLayout(self.char_panel)
        cp.setContentsMargins(0, 0, 0, 0)
        ch = QHBoxLayout()
        self.char_q = QLineEdit()
        self.char_q.setPlaceholderText("캐릭터 메쉬 이름 검색 (쉼표/띄어쓰기 = 모두 포함, 예: knight,armor). 비우고 검색하면 전체")
        self.char_q.returnPressed.connect(self.show_char_meshes)
        ch.addWidget(self.char_q, 1)
        self.char_rig = QComboBox()
        for lab, key in (("리깅 전체", "all"), ("리깅됨 (SK_ 스켈레탈)", "rigged"), ("리깅 안 됨 (SM_ 스태틱)", "static")):
            self.char_rig.addItem(lab, key)
        self.char_rig.currentIndexChanged.connect(lambda *_a: self.show_char_meshes())
        ch.addWidget(self.char_rig)
        self.ck_char_player = QCheckBox("플레이어블만")
        self.ck_char_player.setToolTip("player/pc/hero 이름이 든 메시와 같은 폴더의 같은 리그 변형만. 끄면 몬스터·NPC 등 캐릭터 전체.")
        self.ck_char_player.toggled.connect(self._char_player_toggled)
        ch.addWidget(self.ck_char_player)
        b = QPushButton("검색")
        b.clicked.connect(lambda *_a: self.show_char_meshes())
        ch.addWidget(b)
        cp.addLayout(ch)
        self.char_note = QLabel("")
        self.char_note.setWordWrap(True)
        cp.addWidget(self.char_note)
        v.addWidget(self.char_panel)
        self.char_panel.setVisible(on_char)
        self.rb_mode_char.toggled.connect(self._mode_changed)
        self.ck_fast = QCheckBox("빠르게 (후보 5개)")
        self.ck_fast.setChecked(bool(self.cfg.get("vf_fast", True)))
        self.ck_fast.setToolTip("켜면 시간을 크게 줄입니다: ① 영상마다 장면 시트 한 장으로 AI 에게 한 번만 묻고(장면마다 묻지 않음), ② AI 에게 알리는 이름 목록을 %d개로 줄이고, "
                                "③ 하나로 못 박지 않고 AI 가 꼽은 후보를 합쳐 검수 칸에 후보 5개를 보여 줍니다. 끄면 장면마다 정밀하게 묻습니다(느림)." % vision.FAST_VOCAB)
        self.ck_fast.toggled.connect(lambda v: (self.cfg.__setitem__("vf_fast", bool(v)), bridge.save_config(self.cfg)))
        self.btn_guess = QPushButton("캐릭터 추측 시작")
        self.btn_guess.setMinimumHeight(32)
        if self.rb_mode_char.isChecked():
            self.btn_guess.setText("캐릭터 메쉬 목록 열기")
        self.btn_guess.clicked.connect(lambda *_a: self.start_guess(False))
        h.addWidget(self.ck_fast)
        h.addWidget(self.btn_guess, 1)
        self.btn_guess_panel = QPushButton("🧑‍⚖ AI 회의로 추측")
        self.btn_guess_panel.setMinimumHeight(32)
        self.btn_guess_panel.setToolTip("영상마다 장면 시트를 만들어, 이미지를 보는 AI(Qwen-VL·Claude·Gemini …)가 직접 보고 캐릭터 후보와 생김새를 말하면,\n"
                                        "글 전용 AI(GPT-OSS)가 그 묘사를 읽고 같은 이름 목록에서 의견을 냅니다. 서로의 이유를 보며 고치고, 합의된 캐릭터가 아래 검수 표에 들어갑니다.\n"
                                        "목록에 없는 캐릭터(이름 없는 보스 등)는 '(모름)' 줄에 생김새만 적어 둡니다. 토론 기록도 창으로 볼 수 있습니다.")
        self.btn_guess_panel.clicked.connect(lambda *_a: self.start_guess_panel())
        h.addWidget(self.btn_guess_panel, 1)
        h.addWidget(QLabel("자동 선택 최소 확신"))
        h.addWidget(self.min_conf)
        b = QPushButton("에셋 목록 새로 읽기")
        b.setToolTip("게임 파일에서 메시/애니메이션 이름 목록을 다시 만듭니다 (처음 한 번 만들면 저장해 두고 씁니다).")
        b.clicked.connect(lambda *_a: self.start_guess(True))
        h.addWidget(b)
        v.addLayout(h)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        v.addWidget(self.status)
        lay.addWidget(g)

        # 4) 검수
        g = CollapsibleBox("4) 검수 — 틀린 것은 고쳐 주세요")
        v = QVBoxLayout(g)
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["장면", "AI 추측", "메시 (후보 / 검색)", "제외"])
        self.table.setIconSize(QSize(168, 96))
        self.table.setMinimumHeight(260)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for c, wd in ((0, 176), (1, 150), (3, 60)):
            self.table.setColumnWidth(c, wd)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setStretchLastSection(False)
        v.addWidget(self.table)
        # 게임 속 이름으로 찾기: 리소스 이름이 화면에 보이는 이름과 다를 때
        h = QHBoxLayout()
        h.addWidget(QLabel("게임 속 이름"))
        self.ingame = QLineEdit()
        self.ingame.setPlaceholderText("예: The Nameless Captive / 이름 없는 포로 — 위 표에서 줄을 고른 뒤")
        self.ingame.setToolTip("화면에 보이는 이름과 파일 이름이 다를 때, 게임 글자 사전·비슷한 말·AI 로 메시를 찾아 선택한 줄에 채웁니다")
        self.ingame.returnPressed.connect(self.find_by_ingame_name)
        h.addWidget(self.ingame, 1)
        self.ingame_ai = QCheckBox("AI도 사용")
        self.ingame_ai.setChecked(True)
        h.addWidget(self.ingame_ai)
        b = QPushButton("이 이름으로 찾기")
        b.clicked.connect(lambda *_a: self.find_by_ingame_name())
        h.addWidget(b)
        b = QPushButton("글자 사전 만들기")
        b.setToolTip("게임 파일의 .locres(번역 글자)를 읽어 '표시 이름 → 내부 이름표'를 만듭니다. 한 번만 하면 됩니다.")
        b.clicked.connect(lambda *_a: self.build_name_book())
        h.addWidget(b)
        v.addLayout(h)
        self.ingame_note = QLabel("")
        self.ingame_note.setWordWrap(True)
        v.addWidget(self.ingame_note)
        # 특징·패턴으로 찾기: 이름이 다 틀려도 "7연속 스핀 공격" 같은 동작으로 애니메이션 이름을 뒤져 캐릭터를 거꾸로 찾는다
        h = QHBoxLayout()
        h.addWidget(QLabel("특징·패턴"))
        self.motion_text = QLineEdit()
        self.motion_text.setPlaceholderText("예: 7연속 스핀 공격 / 땅 내려찍기 / 점프 후 돌진 — 위 표에서 줄을 고른 뒤")
        self.motion_text.setToolTip("이름으로 못 찾을 때: 그 캐릭터의 특징적인 동작을 적으면 spin·slam·charge 같은 낱말로 애니메이션 이름을 검색하고,\n"
                                    "그 애니메이션이 속한 캐릭터를 후보로 올립니다.")
        self.motion_text.returnPressed.connect(self.find_by_motion)
        h.addWidget(self.motion_text, 1)
        self.motion_ai = QCheckBox("AI도 사용")
        self.motion_ai.setToolTip("동작 설명을 영어 낱말로 바꾸는 데만 씁니다(에셋 이름은 만들지 않음). 끄면 내장 낱말표만 씁니다.")
        self.motion_ai.setChecked(True)
        h.addWidget(self.motion_ai)
        b = QPushButton("특징으로 캐릭터 찾기")
        b.clicked.connect(lambda *_a: self.find_by_motion())
        h.addWidget(b)
        v.addLayout(h)
        self.motion_note = QLabel("")
        self.motion_note.setWordWrap(True)
        v.addWidget(self.motion_note)
        h = QHBoxLayout()
        b = QPushButton("캐릭터 줄 직접 추가")
        b.setToolTip("AI가 놓친 캐릭터를 직접 넣습니다. 줄의 검색 칸에서 메시를 찾으세요.")
        b.clicked.connect(self.add_blank_row)
        h.addWidget(b)
        b = QPushButton("선택한 줄 삭제")
        b.setToolTip("표에서 줄을 하나 고른 뒤 누르면 그 줄을 지웁니다. 직접 추가했거나 잘못 인식된 줄을 치울 때 쓰세요.")
        b.clicked.connect(lambda *_a: self.delete_selected_row())
        h.addWidget(b)
        b = QPushButton("모두 지우기")
        b.clicked.connect(lambda *_a: self.clear_rows())
        h.addWidget(b)
        h.addStretch(1)
        self.btn_confirm = QPushButton("검수 확정 → 애니메이션 후보 보기")
        self.btn_confirm.setMinimumHeight(30)
        self.btn_confirm.clicked.connect(self.confirm)
        h.addWidget(self.btn_confirm)
        v.addLayout(h)
        lay.addWidget(g)

        # 5) 후보 / 추출
        g = CollapsibleBox("5) 후보에서 골라 추출")
        v = QVBoxLayout(g)
        v.addWidget(QLabel("메시 (확정한 캐릭터 / 캐릭터 메쉬 검색 결과)"))
        self.mesh_list = _AssetList("mesh")
        self.mesh_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.mesh_list.setMinimumHeight(70)
        self.mesh_list.itemSelectionChanged.connect(self._mesh_sel_changed)
        self.mesh_list.itemDoubleClicked.connect(self._mesh_double_clicked)
        self.mesh_list.setToolTip("더블클릭 또는 오른쪽 뷰포트로 끌어다 놓기: 이 메시를 바로 뷰포트(타깃)로 엽니다 — 리깅(스켈레톤·스킨)이 든 GLB 로 뽑아서 바로 불러와요. 탭은 그대로 있습니다.")
        v.addWidget(self.mesh_list)
        v.addWidget(QLabel("↑ 더블클릭하거나 뷰포트로 끌어다 놓으면 그 메시가 바로 뷰포트에 열려요 (추출 없이 확인)"))
        h = QHBoxLayout()
        h.addWidget(QLabel("애니메이션 분류"))
        self.label = QComboBox()
        self.label.addItem("상관없음", "")
        for x in LABEL_CHOICES:
            self.label.addItem(x, x)
        self.label.currentIndexChanged.connect(self._refill_anims)
        h.addWidget(self.label)
        h.addWidget(QLabel("타수"))
        self.hits = QSpinBox()
        self.hits.setRange(0, 9)
        self.hits.setSpecialValueText("상관없음")
        self.hits.valueChanged.connect(self._refill_anims)
        h.addWidget(self.hits)
        h.addStretch(1)
        v.addLayout(h)
        h = QHBoxLayout()
        h.addWidget(QLabel("설명으로 찾기"))
        self.anim_q = QLineEdit()
        self.anim_q.setPlaceholderText("예: 스핀 공격 / 내려찍기 / 점프 — 쉼표로 나누면 모두 맞는 것만 (예: 점프,내려찍기). 비우고 누르면 기본 목록으로")
        self.anim_q.setToolTip("동작 설명을 애니메이션 이름 낱말(spin, slam…)로 바꿔 검색합니다. 메시를 고르면 그 캐릭터 안에서, 안 고르면 전체에서 찾습니다.")
        self.anim_q.returnPressed.connect(self.search_anims_by_text)
        h.addWidget(self.anim_q, 1)
        self.anim_all = QCheckBox("모든 캐릭터에서")
        self.anim_all.setToolTip("끄면 위에서 고른(확정한) 캐릭터의 애니메이션 안에서만 찾습니다.")
        h.addWidget(self.anim_all)
        b = QPushButton("검색")
        b.clicked.connect(lambda *_a: self.search_anims_by_text())
        h.addWidget(b)
        v.addLayout(h)
        h = QHBoxLayout()
        h.addWidget(QLabel("찾는 종류"))
        self.ck_k_monster = QCheckBox("몬스터·보스·NPC")
        self.ck_k_player = QCheckBox("플레이어블 캐릭터")
        for ck, key in ((self.ck_k_monster, "vf_kind_monster"), (self.ck_k_player, "vf_kind_player")):
            ck.setChecked(bool(self.cfg.get(key, True)))
            ck.toggled.connect(self._kinds_changed)
            h.addWidget(ck)
        self.ck_k_monster.setToolTip("경로에 player/pc/hero 같은 낱말이 없는 애니메이션. 끄면 몬스터 것은 검색·목록에서 빠집니다.")
        self.ck_k_player.setToolTip("경로에 player/pc/hero/fallen 같은 낱말이 든 공용 애니메이션. 이것만 켜면 플레이어 것만 훑어서 더 빠릅니다.")
        h.addStretch(1)
        v.addLayout(h)
        h = QHBoxLayout()
        h.addWidget(QLabel("목록에서 거르기"))
        self.anim_filter = QLineEdit()
        self.anim_filter.setPlaceholderText("🔍 지금 목록 안에서 글자로 바로 거르기 (쉼표·띄어쓰기 = 모두 포함, 예: character,attack)")
        self.anim_filter.setClearButtonEnabled(True)
        self.anim_filter.textChanged.connect(lambda *_a: self._apply_anim_filter())
        h.addWidget(self.anim_filter, 1)
        self.anim_count = QLabel("")
        self.anim_count.setStyleSheet("color:#8a93a6")
        h.addWidget(self.anim_count)
        v.addLayout(h)
        self.anim_list = _AssetList("anim")
        self.anim_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.anim_list.setMinimumHeight(200)
        v.addWidget(self.anim_list)
        self.ck_player = QCheckBox("플레이어블 캐릭터 애니메이션으로 찾기")
        self.ck_player.setToolTip("플레이어가 조종하는 캐릭터는 몸(메시)이 여러 벌이어도 애니메이션을 공유해서, 메시 이름이 아니라 player/pc/hero 같은 "
                                  "이름이 든 공용 애니메이션을 보여줍니다. 메시 경로에 그런 낱말이 있으면 자동으로 켜지고, 아니면 여기서 직접 켜세요.")
        self.ck_player.toggled.connect(self._refill_anims)
        v.addWidget(self.ck_player)
        self.anim_note = QLabel("")
        self.anim_note.setWordWrap(True)
        v.addWidget(self.anim_note)
        self.btn_verify = QPushButton("스켈레톤으로 확인 (정확도↑)")
        self.btn_verify.setToolTip("고른 메쉬와 후보 애니메이션 파일을 실제로 열어, 같은 스켈레톤에 연결되고 재생 가능한 것만 앞에 두고 아닌 것은 뺍니다.")
        self.btn_verify.clicked.connect(lambda *_a: self.verify_links())
        v.addWidget(self.btn_verify)
        h = QHBoxLayout()
        b = QPushButton("전체 선택")
        b.clicked.connect(lambda *_a: self._select_visible_anims())
        h.addWidget(b)
        b = QPushButton("선택 해제")
        b.clicked.connect(lambda *_a: self.anim_list.clearSelection())
        h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)
        self.btn_mrank = QPushButton("🎬 영상 속 동작과 비슷한 순으로 정렬…")
        self.btn_mrank.setToolTip("영상에서 뽑은 3D 동작 파일(AnimRef 의 AI 모캡 .bvh 등)을 고르면, 지금 목록 위쪽 %d개를 임시로 뽑아서 팔다리 움직임이 얼마나 비슷한지로\n"
                                  "다시 줄 세웁니다. 이름이 비슷해도 동작이 다른 것, 이름이 달라도 동작이 같은 것을 가려냅니다. 좌우 반전(거울) 동작도 같이 봅니다." % MAX_MOTION_RANK)
        self.btn_mrank.clicked.connect(lambda *_a: self.rank_by_motion())
        v.addWidget(self.btn_mrank)
        self.react_only = QCheckBox("🥊 피격·그로기·처형 계열만 보기")
        self.react_only.setToolTip("이름에 hit/react/stagger/groggy/execute/finisher/victim 같은 낱말이 든 애니메이션만 남깁니다 (상대 캐릭터의 '맞는 모션'을 찾을 때).")
        self.react_only.toggled.connect(lambda *_a: self._apply_anim_filter())
        v.addWidget(self.react_only)
        ph = QHBoxLayout()
        self.btn_pair = QPushButton("🔗 고른 모션의 짝 찾기 (상대 쪽 모션 목록으로)")
        self.btn_pair.setToolTip("목록에서 모션 하나(예: 보스가 처형당하는 모션)를 고르고 누르면, 반대편 캐릭터(보스 ↔ 플레이어)의 모션 중\n"
                                 "길이가 비슷한(기본 ±30프레임) 것과 이름이 겹치는 것을 점수순으로 목록에 올립니다. 서로 맞물리는 연출(처형·패리 피니셔·잡기)에서 통합니다.\n"
                                 "필요하면 먼저 파일 안을 읽어 길이를 알아냅니다(처음엔 시간이 걸림).")
        self.btn_pair.clicked.connect(lambda *_a: self.find_pair())
        ph.addWidget(self.btn_pair, 1)
        ph.addWidget(QLabel("길이 오차(프레임)"))
        self.pair_tol = QSpinBox()
        self.pair_tol.setRange(1, 300)
        self.pair_tol.setValue(int(self.cfg.get("vf_pair_tol", 30)))
        ph.addWidget(self.pair_tol)
        v.addLayout(ph)
        self.btn_panel = QPushButton("🧑‍⚖ AI 회의로 찾기… (여러 AI 가 영상과 목록을 보며 토론)")
        self.btn_panel.setToolTip("고른 영상 장면을 한 장의 시트로 붙여, 이미지를 보는 AI(Qwen-VL·Claude·Gemini …)가 직접 보고, 글 전용 AI(GPT-OSS)는 그 묘사를 읽고,\n"
                                  "모두 지금 목록(위쪽 %d개)에서 맞는 애니메이션을 고르고 서로의 이유를 보며 고칩니다. 합의 순위로 목록을 다시 줄 세웁니다." % MAX_PANEL_NAMES)
        self.btn_panel.clicked.connect(lambda *_a: self.open_panel())
        v.addWidget(self.btn_panel)
        self.btn_view = QPushButton("선택한 애니메이션을 뷰포트로 열기 (리타겟 작업)")
        self.btn_view.setMinimumHeight(34)
        self.btn_view.setToolTip("고른 애니메이션(여러 개 가능)을 PSA 로 뽑아 소스 모션으로 엽니다. 뷰포트로 끌어다 놓아도 같고, 탭은 옮기지 않습니다. 클립 목록에서 바꿔 가며 볼 수 있습니다.\n"
                                 "같은 스켈레톤(같은 캐릭터)의 애니메이션만 함께 열립니다.")
        self.btn_view.clicked.connect(lambda *_a: self.open_in_viewport())
        v.addWidget(self.btn_view)
        self.anim_list.itemDoubleClicked.connect(self._anim_double_clicked)
        self.edit_panel = EditPanel("추출할 애니메이션 가공 (선택) — 늘리기 · fps · 구간 · 루프 · 키 압축", self.cfg, "vf_edit")
        self.edit_panel.setToolTip("켜면 추출한 PSA 옆에 '<이름>_edit.psa' 를 하나 더 만듭니다(원본은 그대로). 아무것도 안 고르면 가공하지 않습니다.")
        v.addWidget(self.edit_panel)
        h = QHBoxLayout()
        self.btn_extract_mesh = QPushButton("메시만 추출")
        self.btn_extract_mesh.setMinimumHeight(34)
        self.btn_extract_mesh.setToolTip("위에서 고른 메시만 뽑습니다 (애니메이션은 뽑지 않음).")
        self.btn_extract_mesh.clicked.connect(lambda *_a: self.extract("mesh"))
        h.addWidget(self.btn_extract_mesh)
        self.btn_extract_anim = QPushButton("애니메이션만 추출")
        self.btn_extract_anim.setMinimumHeight(34)
        self.btn_extract_anim.setToolTip("고른 애니메이션만 뽑습니다 (메시는 뽑지 않음). 가공 옵션을 켜면 _edit.psa 도 만듭니다.")
        self.btn_extract_anim.clicked.connect(lambda *_a: self.extract("anim"))
        h.addWidget(self.btn_extract_anim)
        self.btn_extract = QPushButton("메시 + 애니메이션 추출")
        self.btn_extract.setMinimumHeight(34)
        self.btn_extract.setToolTip("0번 탭의 형식/리깅/대상 프로그램/출력 폴더 설정을 그대로 씁니다. 파일로만 저장합니다.")
        self.btn_extract.clicked.connect(lambda *_a: self.extract("both"))
        h.addWidget(self.btn_extract)
        v.addLayout(h)
        lay.addWidget(g)
        lay.addStretch(1)
        fit_widths(inner)

    # ================================================================== 입력
    def _add_source(self, path: str, label: str = "", title: str = "") -> None:
        tag = "[%s] " % label if label else ""
        it = QListWidgetItem(tag + animlink.display_name(path, title))   # 'video.mp4' 같은 뜻 없는 파일 이름 대신 클립 이름
        it.setData(Qt.ItemDataRole.UserRole, path)
        it.setToolTip(path)
        self.src_list.addItem(it)
        self.clip_labels[path] = label

    def source_paths(self, selected_only: bool = True) -> List[str]:
        its = self.src_list.selectedItems() if selected_only else []
        if not its:
            its = [self.src_list.item(i) for i in range(self.src_list.count())]
        return [it.data(Qt.ItemDataRole.UserRole) for it in its]

    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "영상 / 이미지 선택", self.cfg.get("last_dir", ""), PICK_FILTER)
        have = set(self.source_paths(False))
        for p in paths or []:
            if p not in have and frames.kind_of(p):
                self._add_source(p)
        self._update_src_note()

    def add_animref(self) -> None:
        start = animlink.default_clip_dir() or self.cfg.get("animref_dir", "")  # AnimRef 설정의 라이브러리/게임/clips
        d = QFileDialog.getExistingDirectory(self, "AnimRef 클립 폴더", start)
        if not d:
            return
        self.cfg["animref_dir"] = d
        bridge.save_config(self.cfg)
        clips = animref.scan_clips(d)
        have = set(self.source_paths(False))
        for c in clips:
            if c.path not in have:
                self._add_source(c.path, c.label)
        labels = {c.label for c in clips if c.label}
        if len(labels) == 1:  # 클립이 전부 같은 분류면 애니메이션 분류도 그걸로
            self.label.setCurrentIndex(max(self.label.findData(next(iter(labels))), 0))
        self._update_src_note(prefix=animref.summarize(clips))

    def clear_sources(self) -> None:
        self.src_list.clear()
        self.clip_labels = {}
        self._update_src_note()

    def _update_src_note(self, prefix: str = "") -> None:
        n = self.src_list.count()
        self.src_note.setText((prefix + " — " if prefix else "") + ("원본 %d개" % n if n else "영상/이미지를 추가하세요."))

    # ================================================================== 인식 방식
    def _provider_kind(self) -> str:
        return self.engine.currentData() or "ollama"

    MODEL_KEY = {"claude": "vf_claude_model", "openai": "vf_oa_model", "ollama": "vf_ollama_model", "gptoss": "vf_gptoss_model"}

    def _preset(self):
        nm = self.oa_preset.currentData()
        return next((p for p in vision.OPENAI_PRESETS if p[0] == nm), vision.OPENAI_PRESETS[0])

    def _remember_key(self, *_a) -> None:
        kind = self._provider_kind()
        if kind in self._keys and not getattr(self, "_switching", False):
            self._keys[kind] = self.key.text()

    def _preset_changed(self, *_a) -> None:
        """서비스를 바꾸면 모델을 그 서비스의 기본 모델로 (직접 고친 모델이 다른 서비스 기본값이 아닐 때만 유지)."""
        if self._provider_kind() != "openai":
            return
        defaults = {p[2] for p in vision.OPENAI_PRESETS}
        if self.model.text().strip() in defaults | {""}:
            self.model.setText(self._preset()[2])
        self.oa_url.setPlaceholderText(self._preset()[1])
        self._engine_note_update()

    def _preset_info_text(self) -> str:
        nm = self._preset()[0]
        note, url = vision.PRESET_INFO.get(nm, ("", ""))
        return (note + ((' · <a href="%s">API 키 받는 곳</a>' % url) if url else "")) if note else ""

    def load_models(self) -> None:
        """서버의 모델 목록을 불러와 고르게 한다 (OpenAI 호환 서비스 전용)."""
        prov = self.provider()
        if prov.key != "openai":
            self.engine_note.setText("모델 목록은 '다른 AI' 방식에서 쓸 수 있습니다. Ollama 는 'ollama list' 로 확인하세요.")
            return
        self.win.run_async(lambda w: prov.list_models(), self._after_models, "모델 목록 불러오는 중")

    def _after_models(self, names) -> None:
        if not names:
            self.engine_note.setText("✖ 서버가 모델 목록을 주지 않았습니다. 서비스 사이트에서 모델 이름을 확인해 직접 입력하세요.")
            return
        ordered, likely = vision.sort_models(names)
        shown = [("👁 " if i < likely else "") + n for i, n in enumerate(ordered)]
        cur = self.model.text().strip()
        pick, ok = QInputDialog.getItem(self, "모델 고르기", "이미지를 볼 수 있어 보이는 모델(👁)이 위에 있습니다 — 이름으로만 추정이라 틀릴 수 있어요.\n전체 %d개" % len(ordered),
                                        shown, max(ordered.index(cur), 0) if cur in ordered else 0, False)
        if ok and pick:
            self.model.setText(pick.replace("👁 ", "", 1))
            self.engine_note.setText("모델을 '%s' 로 정했습니다. [연결 확인]으로 확인하세요." % self.model.text())

    def _engine_note_update(self) -> None:
        kind = self._provider_kind()
        if kind == "claude":
            note = "영상 이미지가 인터넷으로 Anthropic 에 전송되고 호출한 만큼 요금이 듭니다."
        elif kind == "openai":
            nm, url, _m, env = self._preset()
            local = "localhost" in (self.oa_url.text() or url)
            note = ("내 서버(%s)로 보냅니다 — 이미지를 볼 수 있는 모델이어야 해요." % (self.oa_url.text().strip() or url)) if local else \
                   "영상 이미지가 인터넷으로 %s 에 전송되고 호출한 만큼 요금이 듭니다. 이미지를 볼 수 있는 모델을 고르세요. (키는 환경변수 %s 로도 가능)" % (nm, env or "-")
        elif kind == "gptoss":
            note = ("OpenAI 의 열린 모델 GPT-OSS-20B 를 내 PC(Ollama)에서 돌립니다 — 무료, 인터넷으로 아무것도 보내지 않아요. 글 전용이라 '게임 속 이름/동작 설명으로 찾기'와 "
                    "AI 낱말 변환에 쓰이고, 영상 장면 인식에는 쓸 수 없습니다. 메모리 16GB 이상 권장, 모델 약 %dGB. [자동 설치·준비]로 한 번에 준비하세요." % vision.GPTOSS_SIZE_GB)
        else:
            note = "내 PC에서만 처리됩니다. Ollama 설치 후 'ollama pull %s' 로 모델을 받으세요. 그래픽카드가 약하면 느리고 덜 정확합니다." % vision.OLLAMA_MODELS[0]
        self.engine_note.setText(note)
        if getattr(self, "oa_info", None) is not None:
            self.oa_info.setText(self._preset_info_text() if kind == "openai" else "")
            self.oa_info.setVisible(kind == "openai")

    def _engine_changed(self, *_a) -> None:
        kind = self._provider_kind()
        self._switching = True
        try:
            if kind == "openai":
                self.model.setText(self.cfg.get("vf_oa_model") or self._preset()[2])
                self.oa_url.setPlaceholderText(self._preset()[1])
            elif kind == "claude":
                self.model.setText(self.cfg.get("vf_claude_model") or vision.CLAUDE_MODELS[0])
            elif kind == "gptoss":
                self.model.setText(self.cfg.get("vf_gptoss_model") or vision.GPTOSS_MODEL)
            else:
                self.model.setText(self.cfg.get("vf_ollama_model") or vision.OLLAMA_MODELS[0])
            if kind in self._keys:
                self.key.setText(self._keys[kind])
                self.key_label.setText("Claude API 키" if kind == "claude" else "API 키")
                self.key.setPlaceholderText("sk-ant-… (비워 두면 환경변수 ANTHROPIC_API_KEY 사용)" if kind == "claude"
                                            else "서비스에서 받은 키 (내 서버는 비워도 됨)")
        finally:
            self._switching = False
        self.key_row.setVisible(kind in ("claude", "openai"))
        self.oa_row.setVisible(kind == "openai")
        self.btn_pull.setVisible(kind in ("ollama", "gptoss"))
        self.btn_setup.setVisible(kind in ("ollama", "gptoss"))
        self._engine_note_update()

    def _save_settings(self) -> None:
        kind = self._provider_kind()
        self._remember_key()
        self.cfg["vf_engine"] = kind
        self.cfg[self.MODEL_KEY.get(kind, "vf_ollama_model")] = self.model.text().strip()
        self.cfg["vf_oa_preset"] = self.oa_preset.currentData() or vision.OPENAI_PRESETS[0][0]
        self.cfg["vf_oa_url"] = self.oa_url.text().strip()
        self.cfg["vf_save_key"] = bool(self.save_key.isChecked())
        for k, cfgkey in (("claude", "vf_claude_key"), ("openai", "vf_openai_key")):
            if self.save_key.isChecked() and self._keys.get(k, "").strip():
                self.cfg[cfgkey] = self._keys[k].strip()
            else:
                self.cfg.pop(cfgkey, None)
        self.cfg["vf_per"] = int(self.per.value())
        self.cfg["vf_hint"] = self.hint.text().strip()
        self.cfg["vf_known"] = self.known.toPlainText()
        self.cfg["vf_filter"] = self.filter.text().strip()
        self.cfg["vf_min_conf"] = int(self.min_conf.value())
        bridge.save_config(self.cfg)

    def provider(self) -> vision.Provider:
        self._save_settings()
        kind = self._provider_kind()
        cfg = dict(self.cfg)
        cfg["vf_claude_key"] = self._keys.get("claude", "").strip()  # 저장 여부와 상관없이 지금 입력한 키를 쓴다
        cfg["vf_openai_key"] = self._keys.get("openai", "").strip()
        cfg[self.MODEL_KEY.get(kind, "vf_ollama_model")] = self.model.text().strip()
        return vision.make_provider(kind, cfg)

    def check_engine(self) -> None:
        prov = self.provider()
        self.win.run_async(lambda w: prov.check(), self._after_check, "연결 확인 중")

    def _after_check(self, res) -> None:
        ok, msg, _names = res
        self.engine_note.setText(("✔ " if ok else "✖ ") + msg)

    def _engine_picked(self, *_a) -> None:
        """GPT-OSS/Ollama 를 직접 고르면, 아직 준비 안 됐을 때 바로 설치할지 묻는다."""
        if self._provider_kind() not in ("ollama", "gptoss"):
            return
        prov = self.provider()

        def job(w):
            return (prov.check()[0], bool(setup_mod.find_ollama()))

        self.win.run_async(job, self._after_pick_check, "AI 준비 상태 확인 중")

    def _after_pick_check(self, res) -> None:
        ready, _installed = res
        if ready:
            self.engine_note.setText("✔ 이미 준비되어 있습니다.")
            return
        r = QMessageBox.question(self, "AI 설치", "이 AI 를 쓰려면 Ollama 와 모델이 필요하고 아직 준비되지 않았습니다.\n"
                                 "지금 자동으로 설치할까요? (필요한 것만 받습니다 — 모델은 수 GB, 한 번만)",
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if r == QMessageBox.StandardButton.Yes:
            self.setup_ollama()

    def setup_ollama(self) -> None:
        prov = self.provider()
        if not isinstance(prov, vision.OllamaProvider):
            return

        def job(w):
            return setup_mod.ensure_ready(prov, lambda pct, st: w.progress.emit(pct, 100, st[:60]))

        self.win.run_async(job, lambda m: self.engine_note.setText("✔ " + m), "AI 설치·준비 중 (%s)" % prov.model)

    def pull_model(self) -> None:
        prov = self.provider()
        if not isinstance(prov, vision.OllamaProvider):
            return

        def job(w):
            ok, msg, _n = prov.check()
            if ok:
                return "이미 받아져 있습니다: " + prov.model
            if "연결하지 못했습니다" in msg:
                raise RuntimeError(msg)
            prov.pull(lambda pct, st: w.progress.emit(pct, 100, "모델 받는 중 %s" % st[:40]))
            return "모델을 받았습니다: " + prov.model

        self.win.run_async(job, lambda m: self.engine_note.setText("✔ " + m), "모델 내려받는 중 (%s)" % prov.model)

    # ================================================================== 추측
    def _ensure_index(self, a, w, force: bool) -> assetidx.AssetIndex:
        helper, game, ue, aes, usmap = a
        if not force:
            idx = self.index or assetidx.load_index(INDEX_CACHE, game)
            if idx:
                return idx
        w.progress.emit(0, 1, "게임 파일에서 이름 목록 읽는 중 (처음 한 번은 오래 걸립니다)")
        paths = extractor.list_assets(helper, game, ue, aes, usmap, LIST_PATTERN, 200000)
        meshes = [p for p in paths if MESH_RX.search(p)]
        # 리깅 없는 캐릭터 모델링(SM_*): 게임 전체 소품까지 담으면 너무 커서 캐릭터 폴더/플레이어 이름이 든 것만
        statics = [p for p in paths if STATIC_RX.search(p) and (set(assetidx._dir_parts(p)) & layout.MARKERS or assetidx.is_player_path(p))]
        ms = set(meshes) | set(statics)
        anims = [p for p in paths if p not in ms and ANIM_RX.search(p) and not STATIC_RX.search(p)]
        if not meshes:
            raise RuntimeError("메시(SK_*)를 하나도 찾지 못했습니다. 0번 탭에서 UE 버전/mappings 를 확인하세요.")
        idx = assetidx.AssetIndex(meshes, anims, statics)
        assetidx.save_index(INDEX_CACHE, idx, game)
        return idx

    def _catalog(self, idx: assetidx.AssetIndex) -> "charcatalog.Catalog":
        """에셋 목록(+ 있으면 글자 사전)으로 만든 캐릭터 카탈로그. 같은 목록이면 다시 만들지 않는다."""
        try:
            mt = os.path.getmtime(NAMES_CACHE) if os.path.isfile(NAMES_CACHE) else 0.0
        except OSError:
            mt = 0.0
        key = (id(idx), mt)
        c = self.__dict__.get("_charcat_memo")
        if c and c[0] == key:
            return c[1]
        book = self.name_book()
        cat = charcatalog.Catalog.build([m.path for m in idx.meshes], idx.anims, book.entries if book else ())
        self._charcat_memo = (key, cat)
        return cat

    def _owned_anims(self, idx: assetidx.AssetIndex, mesh_path: str):
        """캐릭터 카탈로그에서 이 메시가 어느 캐릭터인지 알면, 그 캐릭터 폴더의 애니메이션 경로들 (모르면 None -> 예전처럼 이름 유사도만)."""
        try:
            cat = self._catalog(idx)
            if len(cat.all()) < charcatalog.MIN_CHARS:
                return None
            ch = cat.of_path(mesh_path)
        except Exception:  # noqa: BLE001 - 카탈로그가 안 만들어져도 찾기는 계속
            return None
        return list(ch.anims) if ch is not None and ch.anims else None

    def _moves(self, idx: assetidx.AssetIndex, mesh_path: str):
        """이 메시의 캐릭터 폴더 애니메이션을 '동작' 단위로 묶은 목록 (캐릭터를 모르면 None)."""
        try:
            cat = self._catalog(idx)
            if len(cat.all()) < charcatalog.MIN_CHARS:
                return None
            ch = cat.of_path(mesh_path)
            if ch is None or not ch.anims:
                return None
            return movecatalog.build(ch.anims, [ch.leaf])
        except Exception:  # noqa: BLE001 - 묶기에 실패해도 예전 목록은 그대로 보여 준다
            return None

    @staticmethod
    def _group_by_moves(cands, mc, label_cat=None):
        """후보를 동작 묶음 순서로 다시 세운다: 몽타주 사본·재생 불가 파일은 빼고, 같은 동작의 시작/반복/끝·번호를 붙여 놓고, 짝(플레이어 쪽)을 표시한다.
        분류(label_cat)를 골랐으면 그 분류로 읽히는 동작을 먼저. 캐릭터 폴더 밖의 후보는 그 뒤에 원래 순서로."""
        hidden = mc.hidden()
        by = {c.path: c for c in cands}
        order = [p for p in mc.order() if p in by]
        if label_cat:
            hit = [p for p in order if assetidx.category_scores(p).get(label_cat, 0.0) > 0]
            order = hit + [p for p in order if p not in set(hit)]
        out = []
        for p in order:
            c, clip = by[p], mc.clips[p]
            why = "동작 〔%s〕" % clip.label()
            pr = mc.partners.get(p)
            if pr:
                why += " · 짝: " + os.path.basename(pr[0]).split(".")[0]
            out.append(assetidx.AnimCandidate(p, c.score, why))
        inside = set(order) | hidden
        out += [c for c in cands if c.path not in inside]
        return out, len([c for c in cands if c.path in hidden])

    def _vocab(self, idx: assetidx.AssetIndex, limit: int, pat: str, style: str = "raw"):
        """AI에게 알려줄 이름 목록과 안내 문구.
        style: 'lines' = 캐릭터 카탈로그 줄('<ID> = <게임 속 이름> [종류]', 결과의 ids 가 True) / 'names' = 카탈로그의 대표 메시 이름 / 'raw' = 예전 방식(메시 이름 알파벳순).
        카탈로그는 메시 413종을 캐릭터 100여 명으로 묶어 자르지 않고 전부 알린다 (예전엔 120종만 알려서 뒤쪽 캐릭터는 AI 가 아예 볼 수 없었다)."""
        notes: List[str] = []
        if style in ("lines", "names"):
            try:
                cat = self._catalog(idx)
                chars = cat.all()
            except Exception as e:  # noqa: BLE001 - 카탈로그가 안 만들어져도 예전 방식으로 계속
                chars, cat = [], None
                notes.append("캐릭터 카탈로그를 만들지 못해 예전 방식으로 진행합니다: %s" % e)
            if cat is not None and len(chars) >= charcatalog.MIN_CHARS:
                if pat:
                    try:
                        chars = [c for c in chars if assetidx.filter_paths(c.meshes, pat)]
                    except re.error as e:
                        notes.append("이름 필터가 올바르지 않아 무시했습니다: %s" % e)
                if len(chars) > limit:
                    notes.append("캐릭터 %d명 중 %d명만 AI에게 알렸습니다 (보스 > 미니보스 > 일반 > NPC 순). '이름 필터'로 줄이면 정확해집니다." % (len(chars), limit))
                    chars = chars[:limit]
                named = sum(1 for c in chars if c.names)
                notes.append("캐릭터 %d명을 전부 AI에게 알렸습니다 (게임 속 이름이 있는 캐릭터 %d명)." % (len(chars), named) if len(chars) <= limit else "")
                out = _Vocab(cat.vocab_lines(chars=chars) if style == "lines" else cat.vocab_names(chars=chars))
                out.ids = style == "lines"
                return out, [n for n in notes if n]
        meshes = [m.path for m in idx.meshes]
        if pat:
            try:
                meshes = assetidx.filter_paths(meshes, pat)
            except re.error as e:
                notes.append("이름 필터가 올바르지 않아 무시했습니다: %s" % e)
        names = assetidx.AssetIndex(meshes, []).names()
        if not pat and len(names) > limit:
            narrowed = assetidx.AssetIndex([p for p in meshes if MONSTER_DIRS.search(p)], []).names()
            if len(narrowed) >= 3:
                names = narrowed
                notes.append("메시가 많아 캐릭터 폴더(monster/enemy/boss)만 후보로 썼습니다 (%d종)." % len(names))
        if len(names) > limit:
            notes.append("이름 %d종 중 %d종만 AI에게 알렸습니다. '이름 필터'로 줄이면 정확해집니다." % (len(names), limit))
        return names, notes

    def _mode_changed(self, *_a) -> None:
        on = bool(self.rb_mode_char.isChecked())
        self.cfg["vf_mode_char"] = on
        self.char_panel.setVisible(on)
        self.btn_guess.setText("캐릭터 메쉬 목록 열기" if on else "캐릭터 추측 시작")
        self.btn_guess_panel.setVisible(not on)
        if on:
            if self._load_index_cached():
                self.show_char_meshes()
            else:
                self.status.setText("캐릭터 메쉬 찾기: 에셋 이름 목록이 아직 없어요. '캐릭터 메쉬 목록 열기'를 누르면 게임 파일에서 읽어 옵니다.")
        elif self.char_mode:
            self.char_mode = False
            self.confirmed = []
            self.mesh_list.clear()
            self.anim_list.clear()
            for ck in (self.ck_k_monster, self.ck_k_player):  # 종류 체크도 다시 둘 다
                ck.blockSignals(True)
                ck.setChecked(True)
                ck.blockSignals(False)
            self.status.setText("몬스터 찾기로 돌아왔습니다.")

    def _char_player_toggled(self, on: bool) -> None:
        for ck, v in ((self.ck_k_monster, not on), (self.ck_k_player, True)):  # 애니메이션 종류도 같이
            ck.blockSignals(True)
            ck.setChecked(v)
            ck.blockSignals(False)
        self.show_char_meshes()

    def start_chars(self, force_index: bool = False) -> None:
        """AI 추측 없이 캐릭터 메쉬 검색 목록을 연다 (에셋 이름 목록이 없으면 먼저 읽는다)."""
        if not force_index and self._load_index_cached():
            self.show_char_meshes()
            return
        a = self.win._extract_args()
        if a is None:
            return

        def job(w):
            return self._ensure_index(a, w, force_index)

        def done(idx):
            self.index = idx
            self.show_char_meshes()

        self.win.run_async(job, done, "에셋 이름 목록 읽는 중")

    def start_guess(self, force_index: bool = False) -> None:
        if self.rb_mode_char.isChecked():  # 캐릭터 메쉬 찾기: AI 추측 없이 이름 검색
            self.start_chars(force_index)
            return
        srcs = self.source_paths()
        if not srcs:
            QMessageBox.warning(self, "캐릭터 추측", "영상/이미지를 먼저 추가하세요.")
            return
        a = self.win._extract_args()
        if a is None:
            return
        prov = self.provider()
        if not prov.supports_images:
            QMessageBox.warning(self, "인식 방식", "%s 는 글 전용이라 영상 장면 인식에는 쓸 수 없습니다.\n장면 인식은 Ollama(비전 모델)·Claude·다른 AI 로 바꿔 주세요.\n(이름·설명으로 찾기 / AI 낱말 변환에는 그대로 쓸 수 있어요.)" % prov.label)
            return
        ok, msg, _ = prov.check() if prov.key in ("claude", "openai") else (True, "", [])
        if not ok:
            QMessageBox.warning(self, "인식 방식", msg)
            return
        per, hint, pat = int(self.per.value()), self.hint.text().strip(), self.filter.text().strip()
        hint = hint or (self.win.ex_gname.text().strip() if hasattr(self.win, "ex_gname") else "")  # 비우면 0번 탭의 게임 이름
        known = gamenames.parse_known(self.known.toPlainText())
        book = self.name_book() if known else None
        ff = frames.find_ffmpeg(self.cfg.get("ffmpeg"))
        w_cap = 24
        fast = bool(self.ck_fast.isChecked())

        class _Stopped(Exception):
            pass

        def stop_check(w):
            if w.cancel.is_set():
                raise _Stopped()

        def job(w):
            if isinstance(prov, vision.OllamaProvider):  # 모델이 없으면 긴 작업(목록 읽기, 장면 뽑기) 전에 바로 알린다
                ok2, msg2, _n = prov.check()
                if not ok2:
                    raise RuntimeError(msg2)
            idx = self._ensure_index(a, w, force_index)
            stop_check(w)
            try:
                shutil.rmtree(FRAME_DIR, ignore_errors=True)
            except OSError:
                pass

            def sampling(i, n, name):
                stop_check(w)
                w.progress.emit(i, n, "장면 뽑는 중 " + name)

            fr = framepick.sample_sources_best(ff, srcs, FRAME_DIR, max(per, 6) if fast else per, w_cap, sampling)   # 검은/정지(메뉴) 화면 제외, 서로 다른 장면 위주
            stop_check(w)
            vocab, notes = self._vocab(idx, prov.vocab_limit(), pat, style="lines")   # 카탈로그면 자르지 않고 전부 (잘리면 보스 > 미니보스 > 일반 > NPC 순으로 남김)
            ids = bool(getattr(vocab, "ids", False))
            cat = self._catalog(idx) if ids else None
            read_text = bool(self.cfg.get("vf_read_text", True)) and cat is not None
            if not ids and fast and len(vocab) > vision.FAST_VOCAB:
                vocab = vocab[:vision.FAST_VOCAB]
            if known:  # 알려 준 이름을 리소스 이름으로 바꿔 두고, 이름 목록에 꼭 들어가게 한다
                gamenames.resolve_known(known, idx, book)
                for k in known:
                    if k.top and not any(v == k.top.name or v.startswith(k.top.name + " ") for v in vocab):
                        vocab = [k.top.name] + list(vocab)
                    notes.append("아는 정보: %s → %s" % (k.name, (k.top.name + " (%d%%)" % round(k.top.score * 100)) if k.top else "리소스를 못 찾음"))
            klines = [k.prompt_line() for k in known]
            dets: List[vision.Detection] = []
            errors: List[str] = []
            if fast:   # 영상마다 장면 시트 한 장 -> AI 호출 1번 (클라우드 AI 는 영상 여러 개를 동시에)
                by_src: Dict[str, List[str]] = {}
                for f in fr:
                    by_src.setdefault(f.source, []).append(f.path)
                notes.append("빠르게 모드: 영상 %d개 × AI 호출 1번 (이름 목록 %d개)" % (len(by_src), len(vocab)))
                jobs = list(by_src.items())

                def one(si_src):
                    si, (src, paths) = si_src
                    stop_check(w)
                    sheet = panel.make_sheet(paths, os.path.join(FRAME_DIR, "guess_sheet_%02d.jpg" % si), cols=4, cell_w=384, max_frames=9)
                    found: List[vision.Detection] = []
                    if read_text:   # 화면에 이름이 적혀 있으면(보스 체력바 등) 추측하지 않고 글자로 바로 찾는다
                        found = screenname.read_names(prov, paths, cat, 3, on_error=lambda e, _s=src: errors.append("%s(글자 읽기): %s" % (os.path.basename(_s), e)))
                    return found + prov.analyze_sheet(sheet, paths[0], vocab, hint, klines, 0 if ids else vision.FAST_VOCAB, ids=ids)

                workers = 3 if prov.key in ("claude", "openai") else 1
                done_n = 0
                if workers == 1:
                    for it in enumerate(jobs):
                        w.progress.emit(done_n, len(jobs), "캐릭터 인식 중 " + os.path.basename(it[1][0]))
                        try:
                            dets += one(it)
                        except vision.VisionError as e:
                            errors.append("%s: %s" % (os.path.basename(it[1][0]), e))
                            if len(errors) >= 3 and not dets:
                                break
                        done_n += 1
                else:
                    from concurrent.futures import ThreadPoolExecutor
                    with ThreadPoolExecutor(max_workers=workers) as ex:
                        futs = [(it, ex.submit(one, it)) for it in enumerate(jobs)]
                        for it, fu in futs:
                            w.progress.emit(done_n, len(jobs), "캐릭터 인식 중 " + os.path.basename(it[1][0]))
                            try:
                                dets += fu.result()
                            except vision.VisionError as e:
                                errors.append("%s: %s" % (os.path.basename(it[1][0]), e))
                            done_n += 1
                if cat is not None:
                    screenname.canonicalize(dets, cat)   # AI 가 낸 이름(ID/줄 통째/영·한 이름) -> 대표 메시 이름
                return {"idx": idx, "groups": gamenames.merge_known(vision.aggregate(dets), known), "errors": errors, "notes": notes, "frames": len(fr), "vocab": len(vocab)}
            if read_text:
                dets += screenname.read_names(prov, [f.path for f in fr], cat, 3, on_error=lambda e: errors.append("글자 읽기: %s" % e))
            for i, f in enumerate(fr):
                stop_check(w)  # 취소하면 다음 장면부터 안 한다
                w.progress.emit(i, len(fr), "캐릭터 인식 중 " + os.path.basename(f.path))
                try:
                    dets += prov.analyze(f.path, vocab, hint, klines, ids)
                except vision.VisionError as e:
                    errors.append("%s: %s" % (os.path.basename(f.path), e))
                    if len(errors) >= 3 and not dets:
                        break  # 계속 실패하면 멈춘다 (키/연결 문제)
            if cat is not None:
                screenname.canonicalize(dets, cat)
            return {"idx": idx, "groups": gamenames.merge_known(vision.aggregate(dets), known), "errors": errors, "notes": notes, "frames": len(fr), "vocab": len(vocab)}

        self.win.run_async(job, self._after_guess, "캐릭터 추측 중", cancellable=True,
                           on_cancel=lambda: self.status.setText("캐릭터 추측을 취소했습니다. 인식 방식을 바꾸고 다시 시작할 수 있어요."))

    def _after_guess(self, res) -> None:
        self.index = res["idx"]
        groups: List[vision.Group] = res["groups"]
        self.fill_table(groups)
        lines = ["장면 %d장 분석, 캐릭터 %d종 추측 (AI에게 알린 이름 %d종, 에셋 메시 %d / 애니메이션 %d)" % (
            res["frames"], len(groups), res["vocab"], len(self.index.meshes), len(self.index.anims))]
        lines += res["notes"]
        if res["errors"]:
            lines.append("인식 오류 %d건: %s" % (len(res["errors"]), res["errors"][0][:200]))
        self.status.setText("\n".join(lines))
        if not groups:
            QMessageBox.warning(self, "캐릭터 추측", "캐릭터를 찾지 못했습니다.\n\n" + ("\n".join(res["errors"][:3]) or "다른 장면이나 인식 방식을 시도하세요. '캐릭터 줄 직접 추가'로 직접 넣을 수도 있습니다."))

    # ================================================================== 검수 표
    def _merge_cands(self, idx, base, alts, n: int = 5):
        """랭킹 상위 + AI 가 꼽은 다른 후보(각각 가장 비슷한 메시)를 섞어 n개 (중복 없이)."""
        out = list(base[:max(1, n - min(len(alts), n - 1) if alts else n)])
        have = {c.path for c in out}
        for al in alts:
            for c in idx.rank_meshes(al, "", 1, None):
                if c.path not in have and len(out) < n:
                    out.append(c)
                    have.add(c.path)
        for c in base:
            if len(out) >= n:
                break
            if c.path not in have:
                out.append(c)
                have.add(c.path)
        return out

    def _cand_text(self, c: assetidx.MeshCandidate) -> str:
        return "%s   [%d%%] %s" % (c.name, round(min(c.score, 1.0) * 100), c.why)

    def _fill_combo(self, combo, cands: List[assetidx.MeshCandidate], pick_first: bool = True) -> None:
        """후보를 채운다. pick_first=False 면 맨 위에 '직접 고르세요'를 두고 그것이 선택된 채로 시작한다."""
        combo.clear()
        if not pick_first and cands:
            combo.addItem("(확신이 낮아 자동 선택 안 함 — 후보에서 고르거나 아래 칸에서 검색)", None)
        for c in cands:
            combo.addItem(self._cand_text(c), c.path)
        if not cands:
            combo.addItem("(후보 없음 — 아래 칸에서 이름으로 검색)", None)

    def fill_table(self, groups: List[vision.Group]) -> None:
        self.rows = []
        self.table.setRowCount(0)
        self.table.setRowCount(len(groups))
        for r, g in enumerate(groups):
            self._make_row(r, g)

    def _make_row(self, r: int, g: vision.Group) -> None:
        idx = self.index or assetidx.AssetIndex()
        base = idx.rank_meshes(g.name, g.description, 8, self.memory)
        if g.alts or self.ck_fast.isChecked():   # 후보군: AI 가 꼽은 다른 이름들도 합쳐 5개
            base = self._merge_cands(idx, base, g.alts, 5)
        remembered = bool(base) and base[0].why.startswith("이전에 확정")
        sure = remembered or (bool(g.name) and g.confidence >= self.min_conf.value() / 100.0)
        it = QTableWidgetItem("")
        pm = _thumb(g.frame, g.box)
        if pm is not None:
            it.setIcon(QIcon(pm))
        self.table.setItem(r, 0, it)
        name = g.name or "(모름)"
        warn = "⚠ 확신 낮음 — 직접 확인\n" if g.name and not sure else ""
        txt = "%s%s\n확신 %d%% · %d장면\n%s" % (warn, name, round(g.confidence * 100), g.seen, g.description[:80])
        self.table.setItem(r, 1, QTableWidgetItem(txt))
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(2, 2, 2, 2)
        combo = QComboBox()
        self._fill_combo(combo, base, pick_first=sure)
        search = QLineEdit()
        search.setPlaceholderText("다른 메시 찾기: 이름 일부를 입력")
        bl.addWidget(combo)
        bl.addWidget(search)
        self.table.setCellWidget(r, 2, box)
        ex = QCheckBox("제외")
        self.table.setCellWidget(r, 3, ex)
        row = _Row(g, combo, search, ex, base, sure)
        search.textChanged.connect(lambda text, rw=row: self._on_search(rw, text))
        self.rows.append(row)
        self.table.setRowHeight(r, 108)

    def _on_search(self, row: _Row, text: str) -> None:
        text = (text or "").strip()
        if not text:
            self._fill_combo(row.combo, row.base, pick_first=row.sure)
            return
        idx = self.index
        self._fill_combo(row.combo, idx.search(text) if idx else [])

    # ================================================================== 게임 속 이름으로 찾기
    def _load_index_cached(self) -> Optional[assetidx.AssetIndex]:
        if self.index is None:
            idx = assetidx.load_index(INDEX_CACHE, self.cfg.get("game_dir", ""))
            if idx is not None:
                self.index = idx
        return self.index

    def name_book(self) -> Optional[gamenames.NameBook]:
        return gamenames.NameBook.load(NAMES_CACHE, self.cfg.get("game_dir", ""))

    def build_name_book(self) -> None:
        a = self.win._extract_args()
        if a is None:
            return

        def job(w):
            w.progress.emit(0, 1, "게임 파일에서 글자 사전(.locres) 꺼내는 중")
            shutil.rmtree(LOCRES_DIR, ignore_errors=True)
            files = extractor.export_locres(*a, LOCRES_DIR)
            book = gamenames.build_book(files)
            if len(book):
                book.save(NAMES_CACHE, self.cfg.get("game_dir", "") or a[1])
            return len(files), len(book)

        def done(res):
            nfiles, n = res
            if n:
                msg = "글자 사전 완성: 글자 %d개 (파일 %d개)" % (n, nfiles)
            elif nfiles:
                msg = "글자 사전 파일은 있지만 읽을 수 있는 글자가 없었습니다 (%d개 파일)." % nfiles
            else:
                msg = "이 게임에서 .locres 를 찾지 못했습니다. 비슷한 말 사전과 AI 로만 찾습니다."
            self.ingame_note.setText(msg)
            self.win.statusBar().showMessage(msg)

        self.win.run_async(job, done, "글자 사전 만드는 중")

    def find_by_ingame_name(self) -> None:
        name = self.ingame.text().strip()
        if not name:
            QMessageBox.warning(self, "게임 속 이름", "게임 속 이름을 입력하세요 (예: The Nameless Captive).")
            return
        idx = self._load_index_cached()
        if idx is None:
            QMessageBox.warning(self, "게임 속 이름", "에셋 이름 목록이 아직 없습니다. 한 번 '캐릭터 추측 시작'을 실행하세요.")
            return
        if not self.rows:
            self.add_blank_row()
        cur = self.table.currentRow()
        row = self.rows[cur] if 0 <= cur < len(self.rows) else self.rows[-1]
        book = self.name_book()
        use_ai = bool(self.ingame_ai.isChecked())
        prov = self.provider() if use_ai else None
        desc = row.group.description or ""

        def job(w):
            w.progress.emit(0, 1, "이름으로 찾는 중")
            try:                                   # 1) 캐릭터 카탈로그: 게임 속 이름(영/한)이 정확히 맞으면 그 캐릭터 폴더의 메시를 바로 (AI·비슷한 이름 후보 없이)
                cat = self._catalog(idx)
                hit = cat.find(name, 1) if len(cat.all()) >= charcatalog.MIN_CHARS else []
            except Exception:  # noqa: BLE001
                hit = []
            if hit and hit[0].score >= 0.85 and hit[0].char.meshes:
                ch = hit[0].char
                body = ch.body_mesh()
                order = ([body] if body else []) + [m for m in sorted(ch.meshes) if m != body]
                cands = [assetidx.MeshCandidate(m, assetidx.mesh_name(m), 1.0 if m == body else 0.9, "게임 속 이름 → %s (캐릭터 폴더)" % ch.display()) for m in order]
                return cands, ["캐릭터 '%s': 메시 %d개 · 애니메이션 %d개 (같은 폴더)" % (ch.display(), len(ch.meshes), len(ch.anims))]
            return gamenames.find_meshes(name, idx, book, prov, desc)   # 2) 카탈로그에 없으면 예전 방식(글자 사전 + 비슷한 이름 + AI)

        def done(res):
            cands, notes = res
            if cands:
                row.base = cands
                row.sure = True
                self._fill_combo(row.combo, cands, pick_first=True)
                if row.search.text():
                    row.search.setText("")
                if not row.group.name or row.group.name.lower() in assetidx.UNKNOWN_WORDS:
                    row.group.name = name  # 확정하면 '이 이름 → 이 메시'로 기억된다
            ri = self.rows.index(row)
            self.table.setItem(ri, 1, QTableWidgetItem("게임 속 이름: %s\n%s" % (name, ("후보 %d개 — 맨 위가 가장 유력" % len(cands)) if cands else "후보 없음")))
            self.ingame_note.setText(" ".join(notes) if notes else "후보 %d개를 선택한 줄에 채웠습니다. 맞는지 확인하세요." % len(cands))

        self.win.run_async(job, done, "게임 속 이름으로 찾는 중", cancellable=True, on_cancel=lambda: self.status.setText("검색을 취소했습니다."))

    def _motion_index(self):
        return self.index or self._load_index_cached()

    def _motion_extra(self, text: str, use_ai: bool):
        """(AI 영어 낱말, 안내). AI 가 없거나 실패해도 내장 낱말표로 계속한다."""
        if not use_ai:
            return [], ""
        try:
            return vision.motion_keywords(self.provider(), text), ""
        except Exception as e:  # 네트워크/모델 오류는 검색을 막지 않는다
            return [], "AI 낱말 변환 실패(내장 낱말표만 사용): %s" % str(e)[:120]

    def find_by_motion(self) -> None:
        text = self.motion_text.text().strip()
        if not text:
            QMessageBox.warning(self, "특징·패턴", "캐릭터의 특징적인 동작을 적으세요 (예: 7연속 스핀 공격).")
            return
        idx = self._motion_index()
        if idx is None:
            QMessageBox.warning(self, "특징·패턴", "에셋 이름 목록이 아직 없습니다. 한 번 '캐릭터 추측 시작'을 실행하세요.")
            return
        if not self.rows:
            self.add_blank_row()
        cur = self.table.currentRow()
        row = self.rows[cur] if 0 <= cur < len(self.rows) else self.rows[-1]
        use_ai = bool(self.motion_ai.isChecked())
        kinds = self._kinds()

        def job(w):
            w.progress.emit(0, 1, "동작으로 찾는 중")
            extra, warn = self._motion_extra(text, use_ai)
            res = motionsearch.find_meshes_by_motion(idx, text, extra, kinds=kinds)
            return res, extra, warn

        def done(out):
            (cands, mons, hits, q), extra, warn = out
            if cands:
                row.base = cands
                row.sure = True
                self._fill_combo(row.combo, cands, pick_first=True)
                if row.search.text():
                    row.search.setText("")
            ri = self.rows.index(row)
            self.table.setItem(ri, 1, QTableWidgetItem("특징: %s\n%s" % (text, ("캐릭터 후보 %d종 — 맨 위가 가장 유력" % len(mons)) if mons else "후보 없음")))
            if mons:
                top = ", ".join("%s(%d%%)" % (m.name, round(m.score * 100)) for m in mons[:4])
                ex = ", ".join(os.path.basename(h.path).split(".")[0] for h in mons[0].anims[:3])
                note = "알아들은 동작: %s%s. 유력: %s. %s 의 맞는 애니메이션 예: %s" % (q.describe(), (" + AI: " + ", ".join(extra)) if extra else "", top, mons[0].name, ex)
            else:
                note = "알아들은 동작: %s — 애니메이션 이름에서 맞는 것을 못 찾았습니다. 다른 낱말(영어 포함)로 적어 보세요." % q.describe()
            self.motion_note.setText((note + " " + warn).strip())

        self.win.run_async(job, done, "특징·패턴으로 찾는 중", cancellable=True, on_cancel=lambda: self.status.setText("특징·패턴 검색을 취소했습니다."))

    def search_anims_by_text(self) -> None:
        text = self.anim_q.text().strip()
        if not text:
            self._refill_anims()
            return
        idx = self._motion_index()
        if idx is None:
            QMessageBox.warning(self, "설명으로 찾기", "에셋 이름 목록이 아직 없습니다. 한 번 '캐릭터 추측 시작'을 실행하세요.")
            return
        restrict = None
        chosen = self._chosen_rigged() if self.char_mode else self._selected_mesh_paths()
        if not self.anim_all.isChecked() and chosen:
            restrict = [layout.character_of(p) for p in chosen]
        use_ai = bool(self.motion_ai.isChecked())
        kinds = self._kinds()

        def job(w):
            w.progress.emit(0, 1, "애니메이션 찾는 중")
            extra, warn = self._motion_extra(text, use_ai)
            hits, q = motionsearch.search_anims(idx, text, extra, restrict, kinds=kinds)
            return hits, q, extra, warn

        def done(out):
            hits, q, extra, warn = out
            self.anim_list.clear()
            for h in hits:
                it = QListWidgetItem("%3d%%  %s · %s    %s" % (round(min(max(h.score, 0.0), 1.0) * 100), h.monster, os.path.basename(h.path).split(".")[0], h.why))
                it.setData(Qt.ItemDataRole.UserRole, h.path)
                it.setToolTip(h.path)
                self.anim_list.addItem(it)
            where = "고른 캐릭터 안" if restrict else "전체"
            msg = "'%s' → %s%s. %s에서 애니메이션 %d개." % (text, q.describe(), (" + AI: " + ", ".join(extra)) if extra else "", where, len(hits))
            if not hits and restrict:
                msg += " '모든 캐릭터에서'를 켜 보세요."
            if kinds is not None:
                msg += " (종류: %s만)" % ("플레이어블" if kinds == {assetidx.KIND_PLAYER} else "몬스터·NPC")
            self.anim_note.setText((msg + " " + warn).strip())
            self._apply_anim_filter()

        self.win.run_async(job, done, "애니메이션 찾는 중", cancellable=True, on_cancel=lambda: self.status.setText("애니메이션 검색을 취소했습니다."))

    def delete_selected_row(self) -> None:
        cur = self.table.currentRow()
        if not (0 <= cur < len(self.rows)):
            QMessageBox.warning(self, "줄 삭제", "지울 줄을 표에서 먼저 클릭하세요.")
            return
        self.table.removeRow(cur)
        self.rows.pop(cur)
        if self.rows:  # 다음 줄을 이어서 지울 수 있게 선택을 옮긴다
            self.table.selectRow(min(cur, len(self.rows) - 1))
        self.status.setText("줄 삭제: 남은 캐릭터 %d종" % len(self.rows))

    def clear_rows(self) -> None:
        if not self.rows:
            return
        if QMessageBox.question(self, "모두 지우기", "검수 표의 %d줄을 모두 지울까요? (직접 고친 내용도 사라집니다)" % len(self.rows),
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.rows = []
        self.table.setRowCount(0)
        self.status.setText("검수 표를 비웠습니다.")

    def add_blank_row(self) -> None:
        if self.index is None:
            idx = assetidx.load_index(INDEX_CACHE, self.cfg.get("game_dir", ""))
            if idx is None:
                QMessageBox.warning(self, "캐릭터 줄 추가", "에셋 이름 목록이 아직 없습니다. 한 번 '캐릭터 추측 시작'을 실행하세요.")
                return
            self.index = idx
        n = len(self.rows)
        self.table.setRowCount(n + 1)
        self._make_row(n, vision.Group("", "", 0, 0.0, "", None, []))

    # ================================================================== 확정 -> 후보
    def chosen_meshes(self) -> List[str]:
        out: List[str] = []
        for r in self.rows:
            p = r.chosen()
            if p and p not in out:
                out.append(p)
        return out

    def _mesh_sel_changed(self, *_a) -> None:
        self._refill_anims()

    def _chosen_rigged(self) -> List[str]:
        """캐릭터 메쉬 찾기에서 고른 메시 중 리깅된 것 (애니메이션은 리깅된 메시에만 있다)."""
        idx = self.index
        out = []
        for it in self.mesh_list.selectedItems():
            pth = it.data(Qt.ItemDataRole.UserRole)
            m = idx.mesh(pth) if idx else None
            if m is None or m.rigged:
                out.append(pth)
        return out

    def show_char_meshes(self, *_a) -> bool:
        """캐릭터 메쉬 찾기: 캐릭터 모델링 전체를 이름으로 검색해 (리깅됨 먼저, 리깅 안 됨 다음) 나열한다."""
        idx = self.index or self._load_index_cached()
        if idx is None or not idx:
            QMessageBox.warning(self, "캐릭터 메쉬 찾기", "에셋 이름 목록이 아직 없습니다. '캐릭터 메쉬 목록 열기'로 게임 파일에서 한 번 읽어 오세요.")
            return False
        meshes = idx.character_meshes(self.char_q.text(), self.char_rig.currentData() or "all", bool(self.ck_char_player.isChecked()))
        self.char_mode = True
        self.confirmed = [m.path for m in meshes]
        self.mesh_list.clear()
        shown = meshes[: self.CHAR_LIST_MAX]
        for m in shown:
            it = QListWidgetItem("[%s] %s    (%s)" % ("리깅됨" if m.rigged else "리깅 없음", m.name, m.path))
            it.setData(Qt.ItemDataRole.UserRole, m.path)
            it.setToolTip(m.path)
            self.mesh_list.addItem(it)
        n_r = sum(1 for m in meshes if m.rigged)
        note = "캐릭터 메쉬 %d개 — 리깅됨 %d · 리깅 없음 %d" % (len(meshes), n_r, len(meshes) - n_r)
        if len(meshes) > len(shown):
            note += " (앞 %d개만 표시 — 검색어로 좁히세요)" % len(shown)
        if not idx.statics_known:
            note += ". 이 이름 목록에는 리깅 없는 메시가 없어요: '에셋 목록 새로 읽기'를 누르면 포함됩니다"
        note += ". 메시를 고르면 그 캐릭터에 해당하는 애니메이션만 아래에 나옵니다 (고르지 않으면 전체)."
        self.char_note.setText(note)
        self.status.setText("캐릭터 메쉬 찾기: " + note.split(" — ")[0] + "개")
        self._refill_anims()
        return True

    CHAR_LIST_MAX = 3000

    def confirm(self) -> None:
        self.char_mode = False
        meshes = self.chosen_meshes()
        if not meshes:
            QMessageBox.warning(self, "검수 확정", "메시가 정해진 캐릭터가 없습니다. 후보에서 고르거나 검색 칸에서 찾으세요.")
            return
        for r in self.rows:  # 검수자가 확정한 짝을 기억 -> 다음에 같은 추측이 나오면 맨 앞 후보
            p = r.chosen()
            if p:
                self.memory.remember(r.group.name, r.group.description, p)
        self.memory.save()
        self.confirmed = meshes
        self.mesh_list.clear()
        idx = self.index
        for p in meshes:
            m = idx.mesh(p) if idx else None
            it = QListWidgetItem("%s    (%s)" % (m.name if m else assetidx.mesh_name(p), p))
            it.setData(Qt.ItemDataRole.UserRole, p)
            self.mesh_list.addItem(it)
        self.mesh_list.selectAll()
        self._refill_anims()

    def _selected_mesh_paths(self) -> List[str]:
        sel = self.mesh_list.selectedItems()
        return [it.data(Qt.ItemDataRole.UserRole) for it in sel] if sel else list(self.confirmed)

    # ------------------------------------------------------------------ 종류 / 글자 거르기
    def _kinds(self) -> Optional[set]:
        """체크된 종류. 둘 다 켜져 있거나 둘 다 꺼져 있으면 None(전체)."""
        m, p = self.ck_k_monster.isChecked(), self.ck_k_player.isChecked()
        if m == p:
            return None
        return {assetidx.KIND_MONSTER} if m else {assetidx.KIND_PLAYER}

    def _kinds_changed(self, *_a) -> None:
        self.cfg["vf_kind_monster"] = bool(self.ck_k_monster.isChecked())
        self.cfg["vf_kind_player"] = bool(self.ck_k_player.isChecked())
        self._refill_anims()

    def _apply_anim_filter(self, *_a) -> None:
        terms = textmatch.terms(self.anim_filter.text())  # 쉼표·공백 모두 '모두 포함'
        n = self.anim_list.count()
        shown = 0
        for i in range(n):
            it = self.anim_list.item(i)
            hay = (it.text() + " " + str(it.data(Qt.ItemDataRole.UserRole) or "")).lower()
            ok = all(t in hay for t in terms) and (not self.react_only.isChecked() or pairing.is_reaction(os.path.basename(str(it.data(Qt.ItemDataRole.UserRole) or it.text()))))
            it.setHidden(not ok)
            if not ok and it.isSelected():
                it.setSelected(False)  # 가려진 것은 선택에서도 뺀다 (모르는 사이에 추출되지 않게)
            shown += 1 if ok else 0
        self.anim_count.setText(("%d / %d개 표시" % (shown, n)) if (terms or self.react_only.isChecked()) else ("%d개" % n if n else ""))

    def _select_visible_anims(self) -> None:
        for i in range(self.anim_list.count()):
            it = self.anim_list.item(i)
            if not it.isHidden():
                it.setSelected(True)

    def _picked_anims(self) -> List[str]:
        return [it.data(Qt.ItemDataRole.UserRole) for it in self.anim_list.selectedItems() if not it.isHidden()]

    PLAYER_LIST_MAX = 30000

    def _fill_all_anims(self) -> None:
        """캐릭터 메쉬 찾기에서 메시를 고르지 않았을 때: 종류 체크에 맞는 애니메이션을 이름순으로 전부 (점수·개수 제한 없음)."""
        idx = self.index
        paths = idx.anims_of_kinds(self._kinds() or {assetidx.KIND_PLAYER, assetidx.KIND_MONSTER}) if idx else []
        paths = sorted(paths, key=lambda a: (os.path.basename(a).lower(), a.lower()))
        for a in paths[: self.PLAYER_LIST_MAX]:
            it = QListWidgetItem("%s    %s" % (os.path.basename(a).split(".")[0], os.path.dirname(a.replace("\\", "/"))))
            it.setData(Qt.ItemDataRole.UserRole, a)
            it.setToolTip(a)
            self.anim_list.addItem(it)
        msg = "애니메이션 전체 %d개 (이름순)." % len(paths)
        if len(paths) > self.PLAYER_LIST_MAX:
            msg += " 너무 많아 앞 %d개만 보입니다. 거르기로 좁히세요." % self.PLAYER_LIST_MAX
        self.anim_note.setText(msg + " 위에서 메시를 고르면 그 캐릭터에 해당하는 클립만 남습니다. 목록 거르기(쉼표=모두 포함)·설명으로 찾기도 쓸 수 있어요.")
        self._apply_anim_filter()

    def _refill_anims(self, *_a) -> None:
        self.anim_list.clear()
        idx = self.index
        if self.char_mode and idx is not None and not self._chosen_rigged():
            if self.mesh_list.selectedItems():
                self.anim_note.setText("리깅 없는 메시(SM_)에는 애니메이션이 없습니다. 리깅된 메시를 고르거나 선택을 풀어 전체 목록을 보세요.")
            else:
                self._fill_all_anims()
            return
        if idx is None or not self.confirmed:
            return
        label = self.label.currentData() or None
        hits = int(self.hits.value()) or None
        total = 0
        empty: List[str] = []
        players: List[str] = []
        kinds = self._kinds()
        force = True if (self.ck_player.isChecked() or kinds == {assetidx.KIND_PLAYER}) else None
        hidden_kind = 0
        n_drop = 0
        skel_state: Dict[str, int] = {}
        move_notes: List[str] = []
        for p in (self._chosen_rigged() if self.char_mode else self._selected_mesh_paths()):
            m = idx.mesh(p)
            nm = m.name if m else assetidx.mesh_name(p)
            if force or assetidx.is_player_path(p):
                players.append(nm)
            cands = idx.rank_anims(p, label, hits, 60, player=force, owned=None if (force or assetidx.is_player_path(p)) else self._owned_anims(idx, p))
            cands, st, dropped = self._refine(p, cands)
            n_drop += dropped
            skel_state[st] = skel_state.get(st, 0) + 1
            mc = None if (force or assetidx.is_player_path(p)) else self._moves(idx, p)
            if mc is not None and cands:
                cands, _n_hidden = self._group_by_moves(cands, mc, assetidx.LABEL_CATEGORY.get((label or "").strip()) if label else None)
                move_notes.append("%s: %s" % (nm, mc.summary()))
            if not cands:
                empty.append(nm)
            for c in cands:
                if kinds is not None and idx.kind(c.path) not in kinds:
                    hidden_kind += 1
                    continue
                it = QListWidgetItem("%3d%%  %s · %s    %s" % (round(min(max(c.score, 0.0), 1.0) * 100), nm, os.path.basename(c.path), c.why))
                it.setData(Qt.ItemDataRole.UserRole, c.path)
                it.setToolTip(c.path)
                self.anim_list.addItem(it)
                total += 1
        msg = "애니메이션 후보 %d개 (점수 높은 순)." % total
        if move_notes:
            msg = "애니메이션 후보 %d개 — 같은 캐릭터 폴더의 것을 동작별로 묶어 앞에 두었습니다 (%s). 짝이 있는 모션은 '짝:' 으로 표시됩니다." % (total, "; ".join(move_notes[:3]))
        if players:
            msg += " 플레이어블 캐릭터(%s)는 몸이 아니라 player/pc/hero 이름이 든 공용 애니메이션을 보여줍니다." % ", ".join(players[:3])
        if empty:
            msg += " 이름이 맞는 애니메이션을 못 찾은 캐릭터: %s — 0번 탭에서 직접 검색해 보세요." % ", ".join(empty)
        if hidden_kind:
            msg += " 종류 체크로 %d개를 뺐습니다." % hidden_kind
        if skel_state.get("linked"):
            msg += " ✔ 스켈레톤 연결 확인된 재생 가능 애니메이션을 앞에 두었고" + (", 다른 스켈레톤이거나 재생 못 하는 %d개는 뺐습니다." % n_drop if n_drop else ".")
        elif skel_state.get("mesh_unknown") or skel_state.get("no_index"):
            msg += " 아직 파일 안을 읽어 확인하지 않아 이름 점수만 보입니다 — [스켈레톤으로 확인]을 누르면 실제 연결로 걸러냅니다."
        self.anim_note.setText(msg)
        self._apply_anim_filter()

    def _skel_index(self):
        try:
            return self.win.char_panel._index()
        except Exception:  # noqa: BLE001
            return None

    def _refine(self, mesh_path: str, cands):
        sidx = self._skel_index()
        if sidx is None:
            return cands, "no_index", 0
        return skelgraph.refine_by_skeleton(sidx.all(), mesh_path, cands, lambda p, sc, why: assetidx.AnimCandidate(p, sc, why))

    def verify_links(self) -> None:
        """고른 메쉬와 그 후보 애니메이션의 파일 안을 읽어, 실제로 같은 스켈레톤에 연결되고 재생 가능한지 확인한다."""
        a = self.win._extract_args()
        idx = self.index
        if a is None or idx is None:
            return
        meshes = self._chosen_rigged() if self.char_mode else self._selected_mesh_paths()
        if not meshes:
            QMessageBox.warning(self, "스켈레톤 확인", "메쉬를 먼저 고르세요.")
            return
        sidx = self._skel_index()
        if sidx is None:
            return
        label = self.label.currentData() or None
        hits = int(self.hits.value()) or None
        todo = set(meshes)
        for p in meshes:
            todo.update(c.path for c in idx.rank_anims(p, label, hits, 60, player=None, owned=self._owned_anims(idx, p)))
        todo = [p for p in sorted(todo) if skelgraph.worth_inspecting(p)]

        def job(w):
            self.win.char_panel._run_inspect(w, a, sidx, sidx.pending(todo))
            return len(todo)

        self.win.run_async(job, lambda n: self._refill_anims(), "스켈레톤 연결 확인 중 (파일 안을 읽는 중)", cancellable=True)

    def _char_map(self, anims: List[str]) -> Dict[str, str]:
        """고른 메시가 둘 이상일 때, 애니메이션마다 이름이 가장 어울리는 캐릭터 폴더를 정한다 (못 정하면 경로로)."""
        meshes = [it.data(Qt.ItemDataRole.UserRole) for it in self.mesh_list.selectedItems()]
        idx = self.index
        if len(meshes) < 2 or idx is None:
            return {}
        ents = [m for m in (idx.mesh(p) for p in meshes) if m is not None]
        out: Dict[str, str] = {}
        for a in anims:
            best = max(((idx.char_score(m, a)[0], m) for m in ents), key=lambda t: t[0], default=(0.0, None))
            if best[1] is not None and best[0] > 0.2:
                out[a] = layout.character_of(best[1].path)
        return out

    def _single_char(self) -> Optional[str]:
        """고른 메시가 한 캐릭터뿐이면 애니메이션도 그 캐릭터 폴더에 모은다 (애니 경로가 달라도)."""
        names = {layout.character_of(it.data(Qt.ItemDataRole.UserRole)) for it in self.mesh_list.selectedItems()}
        return names.pop() if len(names) == 1 else None

    # ================================================================== 짝 찾기 (상대 모션 -> 맞물리는 모션)
    def find_pair(self) -> None:
        sel = [it for it in self.anim_list.selectedItems() if not it.isHidden()]
        if len(sel) != 1:
            QMessageBox.warning(self, "짝 찾기", "기준이 될 모션을 목록에서 하나만 고르세요 (예: 보스가 처형당하는 모션).")
            return
        idx = self.index
        sidx = self._skel_index()
        a = self.win._extract_args()
        if idx is None or sidx is None or a is None:
            QMessageBox.warning(self, "짝 찾기", "에셋 이름 목록과 게임 설정이 필요합니다. 0번 탭에서 게임을 설정하고 '캐릭터 추측'이나 '에셋 목록 읽기'를 먼저 하세요.")
            return
        base = sel[0].data(Qt.ItemDataRole.UserRole)
        want = assetidx.KIND_MONSTER if assetidx.path_kind(base) == assetidx.KIND_PLAYER else assetidx.KIND_PLAYER   # 반대편 캐릭터
        pool = sorted(idx.anims_of_kinds({want}))[: self.PLAYER_LIST_MAX]
        # 1) 이름이 정확히 짝인 것 (X <-> X_Player / X_Enemy): 길이를 재지 않아도 확실하다. 보스 폴더 안에 플레이어 쪽 모션이 같이 들어 있는 게임이 많다
        exact = movecatalog.exact_partners(base, idx.anims)

        def add_exact():
            for p in exact:
                it = QListWidgetItem("100%%  %s    이름이 정확히 짝 (%s 와 역할 낱말만 다름)" % (os.path.basename(p).split(".")[0], os.path.basename(base).split(".")[0]))
                it.setData(Qt.ItemDataRole.UserRole, p)
                it.setToolTip(p)
                self.anim_list.addItem(it)

        if exact:
            self.anim_list.clear()
            add_exact()
            self.anim_note.setText("기준 '%s' 의 짝을 이름으로 바로 찾았습니다: %d개 (맨 위). 길이가 비슷한 다른 후보도 이어서 찾는 중…" % (os.path.basename(base).split(".")[0], len(exact)))
            pool = [p for p in pool if p not in set(exact)]
        if not pool and exact:
            return
        if not pool:
            QMessageBox.warning(self, "짝 찾기", "반대편 캐릭터의 애니메이션 이름이 목록에 없습니다.")
            return
        tol = int(self.pair_tol.value())
        self.cfg["vf_pair_tol"] = tol
        bridge.save_config(self.cfg)

        def lens(paths):
            out = {}
            for r in sidx.all():
                if r.path in paths and r.kind in ("anim", "montage") and r.length > 0 and r.frames > 0:
                    out[r.path] = (r.frames, r.length)
            return out

        def job(w):
            need = [p for p in [base] + pool if skelgraph.worth_inspecting(p)]
            todo = sidx.pending(need)
            if todo:
                w.progress.emit(0, len(todo), "길이를 알아내려고 파일 안을 읽는 중 (%d개, 처음 한 번만)" % len(todo))
                self.win.char_panel._run_inspect(w, a, sidx, todo)
            known = lens(set([base] + pool))
            if base not in known:
                raise RuntimeError("기준 모션의 길이를 읽지 못했습니다 (몽타주이거나 읽을 수 없는 파일일 수 있어요).")
            fr, ln = known[base]
            cands = [(p, *known.get(p, (-1, -1.0))) for p in pool]
            return pairing.pair_rank(base, fr, ln, cands, tol), (fr, ln), len(known) - 1

        def done(out):
            hits, (fr, ln), n_known = out
            self.anim_list.clear()
            add_exact()
            for h in hits:
                it = QListWidgetItem("%3d%%  %s    %s" % (round(h.score * 100), os.path.basename(h.path).split(".")[0], h.why))
                it.setData(Qt.ItemDataRole.UserRole, h.path)
                it.setToolTip(h.path)
                self.anim_list.addItem(it)
            self.anim_note.setText((("이름이 정확히 짝인 모션 %d개를 맨 위에 두었습니다. " % len(exact)) if exact else "") +
                                   "기준 '%s' (%d프레임, %.2f초) → %s 쪽 모션 %d개 중 길이 ±%d프레임 안 %d개 (길이를 읽은 것 %d개). "
                                   "이제 [AI 회의로 찾기]로 영상과 맞는 것을 고르세요." % (os.path.basename(base).split(".")[0], fr, ln, "플레이어" if want == assetidx.KIND_PLAYER else "몬스터·보스",
                                                                           len(pool), tol, len(hits), n_known))
            self._apply_anim_filter()

        self.win.run_async(job, done, "짝 찾는 중", cancellable=True, on_cancel=lambda: self.anim_note.setText("짝 찾기를 취소했습니다 (목록은 그대로)."))

    # ================================================================== AI 회의
    def _panel_members(self, picked: Dict[str, bool], second_model: str):
        """고른 참가자마다 Provider 를 만든다 (모델/키는 위 '인식 방식' 설정을 따른다)."""
        self._save_settings()
        cfg = dict(self.cfg)
        cfg["vf_claude_key"] = self._keys.get("claude", "").strip()
        cfg["vf_openai_key"] = self._keys.get("openai", "").strip()
        out = []
        if picked.get("ollama"):
            out.append(panel.Member("Ollama 비전 (%s)" % (cfg.get("vf_ollama_model") or vision.OLLAMA_MODELS[0]), vision.make_provider("ollama", cfg)))
        if picked.get("ollama2") and second_model.strip():
            c2 = dict(cfg)
            c2["vf_ollama_model"] = second_model.strip()
            out.append(panel.Member("Ollama 비전 (%s)" % second_model.strip(), vision.make_provider("ollama", c2)))
        if picked.get("gptoss"):
            out.append(panel.Member("GPT-OSS", vision.make_provider("gptoss", cfg)))
        if picked.get("claude"):
            out.append(panel.Member("Claude", vision.make_provider("claude", cfg)))
        if picked.get("openai"):
            out.append(panel.Member("다른 AI (%s)" % (cfg.get("vf_oa_model") or self._preset()[2]), vision.make_provider("openai", cfg)))
        return out

    def _ask_panel_members(self, title: str, info: str):
        """회의 참가자·라운드 고르는 창. 취소/잘못 고르면 None, 아니면 (members, rounds)."""
        d = QDialog(self)
        d.setWindowTitle(title)
        lay = QVBoxLayout(d)
        intro = QLabel("회의에 참가할 AI 를 고르세요. 이미지를 보는 AI 가 한 명 이상 필요합니다. 모델과 키는 위 '인식 방식' 설정을 따릅니다.\n" + info)
        intro.setWordWrap(True)
        lay.addWidget(intro)
        opts = [("ollama", "Ollama 비전 모델 (설정의 모델, 예: qwen2.5vl:7b) — 이미지 봄", True),
                ("ollama2", "Ollama 두 번째 비전 모델 — 이미지 봄", False),
                ("gptoss", "GPT-OSS-20B — 글 전용 (다른 AI 의 묘사를 읽고 추론)", True),
                ("claude", "Claude (API 키) — 이미지 봄", False),
                ("openai", "다른 AI (Gemini·GPT·Groq … 설정의 서비스) — 이미지 모델이어야 함", False)]
        boxes = {}
        for k, t, on in opts:
            cb = QCheckBox(t)
            cb.setChecked(bool(self.cfg.get("vf_panel_" + k, on)))
            boxes[k] = cb
            lay.addWidget(cb)
        h = QHBoxLayout()
        h.addWidget(QLabel("두 번째 비전 모델"))
        second = QLineEdit(self.cfg.get("vf_panel_model2", "gemma3:12b"))
        h.addWidget(second, 1)
        h.addWidget(QLabel("라운드"))
        rounds = QSpinBox()
        rounds.setRange(1, 4)
        rounds.setValue(int(self.cfg.get("vf_panel_rounds", 2)))
        h.addWidget(rounds)
        lay.addLayout(h)
        bh = QHBoxLayout()
        ok, cancel = QPushButton("회의 시작"), QPushButton("취소")
        ok.clicked.connect(d.accept)
        cancel.clicked.connect(d.reject)
        bh.addStretch(1)
        bh.addWidget(ok)
        bh.addWidget(cancel)
        lay.addLayout(bh)
        if not d.exec():
            return None
        picked = {k: bool(cb.isChecked()) for k, cb in boxes.items()}
        for k, v_ in picked.items():
            self.cfg["vf_panel_" + k] = v_
        self.cfg["vf_panel_model2"] = second.text().strip()
        self.cfg["vf_panel_rounds"] = int(rounds.value())
        bridge.save_config(self.cfg)
        members = self._panel_members(picked, second.text())
        if not members:
            QMessageBox.warning(self, "AI 회의", "참가자를 한 명 이상 고르세요.")
            return None
        if not any(m.sees for m in members):
            QMessageBox.warning(self, "AI 회의", "이미지를 볼 수 있는 AI 를 한 명 이상 고르세요 (GPT-OSS 는 그림을 못 봅니다).")
            return None
        for m in members:
            ok_, msg, _n = m.provider.check() if m.provider.key in ("claude", "openai", "ollama", "gptoss") else (True, "", [])
            if not ok_:
                QMessageBox.warning(self, "AI 회의 — %s" % m.name, msg)
                return None
        return members, int(rounds.value())

    @staticmethod
    def _transcript_text(verdict) -> str:
        lines = []
        for t in verdict.transcript:
            lines.append("[라운드 %d] %s\n%s\n" % (t.round, t.speaker, t.text))
        lines.append("— 합의 순위 —")
        for i, (nm_, sc, who) in enumerate(verdict.ranking[:10], 1):
            lines.append("%d. %s  (점수 %d%%, 지지: %s)" % (i, nm_, round(sc * 100), ", ".join(who)))
        if verdict.errors:
            lines.append("\n⚠ " + " / ".join(verdict.errors))
        return "\n".join(lines)

    def _show_text_dialog(self, title: str, text: str) -> None:
        d = QDialog(self)
        d.setWindowTitle(title)
        lay = QVBoxLayout(d)
        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setPlainText(text)
        box.setMinimumSize(720, 480)
        lay.addWidget(box)
        b = QPushButton("닫기")
        b.clicked.connect(d.accept)
        lay.addWidget(b)
        d.exec()

    # ---- 캐릭터 추측을 AI 회의로
    def start_guess_panel(self) -> None:
        """영상(원본)마다 장면 시트를 만들어 비전 AI 와 글 전용 AI(GPT-OSS)가 토론해 캐릭터를 정한다. 결과는 같은 검수 표에 들어간다."""
        if self.rb_mode_char.isChecked():
            QMessageBox.warning(self, "AI 회의", "'캐릭터 메쉬 찾기' 모드는 AI 없이 이름으로 찾습니다. '몬스터 찾기' 모드로 바꿔 주세요.")
            return
        srcs = self.source_paths()
        if not srcs:
            QMessageBox.warning(self, "AI 회의", "영상/이미지를 먼저 추가하세요.")
            return
        a = self.win._extract_args()
        if a is None:
            return
        got = self._ask_panel_members("AI 회의로 캐릭터 추측", "영상마다 장면 시트를 한 장 만들어 회의합니다. 영상이 많으면 AI 호출이 그만큼 늘어납니다.")
        if got is None:
            return
        members, n_rounds = got
        per, hint, pat = int(self.per.value()), self.hint.text().strip(), self.filter.text().strip()
        hint = hint or (self.win.ex_gname.text().strip() if hasattr(self.win, "ex_gname") else "")
        known = gamenames.parse_known(self.known.toPlainText())
        book = self.name_book() if known else None
        ff = frames.find_ffmpeg(self.cfg.get("ffmpeg"))
        limit = min(m.provider.vocab_limit() for m in members)
        if self.ck_fast.isChecked():
            limit = min(limit, vision.FAST_VOCAB)

        def job(w):
            idx = self._ensure_index(a, w, False)
            shutil.rmtree(FRAME_DIR, ignore_errors=True)

            def sampling(i, n, name):
                if w.cancel.is_set():
                    raise panel.PanelStopped()
                w.progress.emit(i, n, "장면 뽑는 중 " + name)

            fr = framepick.sample_sources_best(ff, srcs, FRAME_DIR, max(per, 6), 24, sampling)
            vocab, notes = self._vocab(idx, limit, pat, style="names")
            if known:
                gamenames.resolve_known(known, idx, book)
                for k in known:
                    if k.top and k.top.name not in vocab:
                        vocab = [k.top.name] + list(vocab)
                    notes.append("아는 정보: %s → %s" % (k.name, (k.top.name + " (%d%%)" % round(k.top.score * 100)) if k.top else "리소스를 못 찾음"))
            by_src: Dict[str, List[str]] = {}
            for f in fr:
                by_src.setdefault(f.source, []).append(f.path)
            results = panel.run_char_panel(members, by_src, vocab, hint, [k.prompt_line() for k in known], n_rounds, FRAME_DIR,
                                           on_progress=lambda m: w.progress.emit(0, 1, m), stop=lambda: w.cancel.is_set())
            dets = [d for r in results for d in r.detections]
            text = "\n\n".join("===== %s =====\n%s" % (os.path.basename(r.source), self._transcript_text(r.verdict)) for r in results)
            errs = [e for r in results for e in r.verdict.errors]
            return {"idx": idx, "groups": gamenames.merge_known(vision.aggregate(dets), known), "errors": errs, "notes": notes + ["AI 회의로 추측 (참가: %s, %d라운드)" % (", ".join(m.name for m in members), n_rounds)],
                    "frames": len(fr), "vocab": len(vocab), "panel_text": text}

        def done(res):
            self._after_guess(res)
            self.last_panel_text = res.get("panel_text", "")
            if self.last_panel_text:
                self._show_text_dialog("AI 회의 결과 — 캐릭터 추측 (표는 검수 칸에 채워졌습니다)", self.last_panel_text)

        self.win.run_async(job, done, "AI 회의로 캐릭터 추측 중 (AI 가 느리면 몇 분 걸립니다)", cancellable=True,
                           on_cancel=lambda: self.status.setText("AI 회의를 취소했습니다."))

    def open_panel(self) -> None:
        names = []
        for i in range(self.anim_list.count()):
            it = self.anim_list.item(i)
            if not it.isHidden():
                names.append(os.path.splitext(os.path.basename(str(it.data(Qt.ItemDataRole.UserRole))))[0])
        names = names[:MAX_PANEL_NAMES]
        if not names:
            QMessageBox.warning(self, "AI 회의", "회의에 올릴 애니메이션 목록이 비어 있습니다. 먼저 캐릭터를 고르거나 검색해서 후보를 만드세요.")
            return
        if not self.source_paths(True):
            QMessageBox.warning(self, "AI 회의", "영상/이미지를 먼저 추가하세요.")
            return
        got = self._ask_panel_members("AI 회의로 찾기", "회의에 올리는 애니메이션: 지금 목록 위쪽 %d개." % len(names))
        if got is None:
            return
        members, rounds = got
        srcs = self.source_paths(True)
        hint = self.hint.text().strip() or (self.win.ex_gname.text().strip() if hasattr(self.win, "ex_gname") else "")
        ff = frames.find_ffmpeg(self.cfg.get("ffmpeg"))
        n_rounds = int(rounds)

        def job(w):
            shutil.rmtree(FRAME_DIR, ignore_errors=True)
            w.progress.emit(0, 1, "장면 시트 만드는 중")
            fr = frames.sample_sources(ff, srcs, FRAME_DIR, 12, 12)
            sheet = panel.make_sheet([f.path for f in fr], os.path.join(FRAME_DIR, "panel_sheet.jpg"))
            return panel.run_panel(members, sheet, names, hint, n_rounds, lambda m: w.progress.emit(0, 1, m)), sheet

        self.win.run_async(job, self._after_panel, "AI 회의 중 (AI 가 느리면 몇 분 걸립니다)", cancellable=True,
                           on_cancel=lambda: self.anim_note.setText("AI 회의를 취소했습니다 (목록은 그대로)."))

    def _after_panel(self, out) -> None:
        verdict, sheet = out
        text = self._transcript_text(verdict)
        rank = {nm_: (sc, who) for nm_, sc, who in verdict.ranking}
        keep = []
        for i in range(self.anim_list.count()):
            it = self.anim_list.item(i)
            pth = it.data(Qt.ItemDataRole.UserRole)
            base = os.path.splitext(os.path.basename(str(pth)))[0]
            keep.append((rank.get(base), it.text(), pth, str(it.toolTip() or "")))
        keep.sort(key=lambda t: -(t[0][0] if t[0] else -1.0))
        self.anim_list.clear()
        for r, txt, pth, tip in keep:
            it = QListWidgetItem(("🧑‍⚖ %3d%%  " % round(r[0] * 100) if r else "") + txt)
            it.setData(Qt.ItemDataRole.UserRole, pth)
            it.setToolTip(tip + (("\nAI 회의 지지: " + ", ".join(r[1])) if r else ""))
            self.anim_list.addItem(it)
        self.last_panel_text = text
        self.anim_note.setText("AI 회의 완료 — 합의 1위: %s. 목록을 합의 순으로 다시 줄 세웠습니다. 눈으로 확인하고 고르세요." % (verdict.ranking[0][0] if verdict.ranking else "없음"))
        self._show_text_dialog("AI 회의 결과", text)

    # ================================================================== 동작으로 비교해 정렬
    def rank_by_motion(self, query_path: Optional[str] = None) -> None:
        """영상에서 뽑은 3D 동작(BVH 등)과 목록의 애니메이션을 실제 움직임으로 비교해 비슷한 순으로 다시 줄 세운다."""
        rows = [i for i in range(self.anim_list.count()) if not self.anim_list.item(i).isHidden()][:MAX_MOTION_RANK]
        if not rows:
            QMessageBox.warning(self, "동작으로 정렬", "정렬할 애니메이션 목록이 비어 있습니다. 먼저 후보를 만들거나 검색하세요.")
            return
        win = self.win
        if query_path is None:
            start = self.cfg.get("vf_motion_dir", "") or ""
            query_path, _f = QFileDialog.getOpenFileName(self, "영상에서 뽑은 3D 동작 파일 (AnimRef AI 모캡 .bvh 등)", start,
                                                         "모션 (*.bvh *.glb *.gltf *.fbx *.psa);;모든 파일 (*)")
        if not query_path:
            return
        self.cfg["vf_motion_dir"] = os.path.dirname(query_path)
        a = win._extract_args()
        if a is None:
            return
        paths = [self.anim_list.item(i).data(Qt.ItemDataRole.UserRole) for i in rows]
        tmp = os.path.join(tempfile.gettempdir(), "motionbridge_motionrank")
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp, exist_ok=True)
        lay = layout.Layout(tmp, "cmp", force_char=self._single_char())
        lay.chars.update(self._char_map(paths))
        total = self.anim_list.count()
        skips: List[str] = []

        def job(w):
            w.progress.emit(0, 1, "영상 동작 파일 읽는 중")
            q = loader.load_model(query_path)
            if not q.animations:
                raise RuntimeError("이 파일에는 동작(애니메이션)이 없습니다: " + os.path.basename(query_path))

            def ev(e):
                if e.get("type") == "skip":
                    skips.append(os.path.basename(e.get("path", "")))
                if e.get("type") in ("exported", "skip"):
                    w.progress.emit(0, len(paths), "후보 뽑는 중 " + os.path.basename(e.get("path", "")))

            files = extractor.export_assets(*a, paths, tmp, ev, kind="anim", lay=lay)
            psa = [f for f in files if f.lower().endswith(".psa")]
            cands = []
            for i, f in enumerate(psa):
                if w.cancel.is_set():
                    return {}, len(psa)
                w.progress.emit(i, len(psa), "동작 비교 중 " + os.path.basename(f))
                try:
                    m = loader.load_model(f)
                except Exception as e:  # noqa: BLE001 - 읽지 못한 것은 건너뛴다
                    skips.append("%s (%s)" % (os.path.basename(f), str(e)[:40]))
                    continue
                if m.animations:
                    cands.append((os.path.splitext(os.path.basename(f))[0].lower(), m.skeleton, m.animations[0]))
            ranked = motionmatch.rank((q.skeleton, q.animations[0]), cands)
            if not ranked and cands:
                raise RuntimeError("영상 동작 파일의 본 이름을 사람 몸으로 알아보지 못했습니다 (골반·허벅지·팔 본이 필요). AnimRef AI 모캡의 .bvh 를 쓰세요.")
            return {k: sc for k, sc in ranked}, len(psa)

        def done(out):
            res, n = out
            if not res:
                self.anim_note.setText("동작 비교를 하지 못했습니다." + ((" 건너뜀: " + ", ".join(skips[:3])) if skips else ""))
                return
            keep = []
            for i in range(self.anim_list.count()):
                it = self.anim_list.item(i)
                pth = it.data(Qt.ItemDataRole.UserRole)
                sc = res.get(os.path.basename(str(pth)).split(".")[0].lower())
                keep.append((sc, it.text(), pth, str(pth)))  # 항목 툴팁은 경로였다
            keep.sort(key=lambda t: -(t[0].score if t[0] else -1.0))
            self.anim_list.clear()
            for sc, text, pth, tip in keep:
                head = ("동작 %3d%%%s  " % (round(sc.score * 100), " ↔" if sc.mirrored else "")) if sc else "동작 비교 안 됨  "
                it = QListWidgetItem(head + text)
                it.setData(Qt.ItemDataRole.UserRole, pth)
                it.setToolTip(tip + ("\n좌우 반전(거울)했을 때 더 비슷합니다 (평균 %.1f°)" % sc.deg if sc and sc.mirrored else ("\n평균 각도 차 %.1f°" % sc.deg if sc else "")))
                self.anim_list.addItem(it)
            top = max(res.values(), key=lambda s: s.score)
            more = (" (위쪽 %d개만 비교, 나머지는 뒤에)" % len(paths)) if total > len(paths) else ""
            self.anim_note.setText("영상 동작과 비슷한 순으로 정렬했습니다 — %d개 비교, 가장 비슷한 점수 %d%%%s. 점수는 팔다리·몸통 뼈 방향이 얼마나 같은지(시간 맞춤, 좌우 반전 포함)이고, ↔ 는 거울 반전입니다.%s"
                                   % (len(res), round(top.score * 100), more, (" 건너뜀 %d" % len(skips)) if skips else ""))
            self._apply_anim_filter()

        win.run_async(job, done, "동작 비교 중", cancellable=True, on_cancel=lambda: self.anim_note.setText("동작 비교를 취소했습니다 (목록은 그대로)."))

    # ================================================================== 뷰포트로 열기
    def _anim_double_clicked(self, item, *_a) -> None:
        self.open_in_viewport([item.data(Qt.ItemDataRole.UserRole)])

    def open_in_viewport(self, anims: Optional[List[str]] = None) -> None:
        if anims is None:
            anims = self._picked_anims()
        if not anims:
            QMessageBox.warning(self, "뷰포트로 열기", "열 애니메이션을 목록에서 고르세요 (Ctrl/Shift 로 여러 개, 더블클릭은 하나).")
            return
        a = self.win._extract_args()
        if a is None:
            return
        win = self.win
        out = win.ex_out.text().strip()
        if not out:
            return
        lay = win.make_layout(self._single_char())
        lay.chars.update(self._char_map(anims))
        bridge.save_config(self.cfg)
        win._ex_skips = []

        def job(w):
            def ev(e):
                if e.get("type") == "skip":
                    win._ex_skips.append("%s — %s" % (os.path.basename(e.get("path", "")), e.get("reason", "")))
                if e.get("type") in ("exported", "skip"):
                    w.progress.emit(0, len(anims), os.path.basename(e.get("path", "")))
            files = extractor.export_assets(*a, anims, out, ev, kind="anim", lay=lay)
            psa = [f for f in files if f.lower().endswith(".psa")]
            if not psa:
                raise RuntimeError("애니메이션을 뽑지 못했습니다. " + " / ".join(win._ex_skips[:3]))
            w.progress.emit(0, 1, "모션 읽는 중")
            added, skipped = win.pr.add_motions(psa)      # 지금 목록에 이어 붙인다 (소스를 갈아엎지 않음)
            return files, added, skipped

        def done(res):
            files, added, skipped = res
            n = len(added)
            self.last_files = list(files)
            win.ex_files.set_paths(files)
            win._added_done([f for f in files if f.lower().endswith(".psa")], (added, skipped), play=True)  # 목록에 더하고 바로 재생 (탭은 옮기지 않는다 — 이 탭에 머물며 더 고를 수 있게)
            msg = "소스 모션 %d개를 뷰포트로 열었습니다 — 왼쪽 '모션 목록'에 올라갔습니다" % n
            if skipped:
                msg += " (건너뜀 %d: %s)" % (len(skipped), skipped[0][:80])
                if added:
                    QMessageBox.information(self, "뷰포트로 열기", "일부는 열지 못했습니다:\n" + "\n".join(skipped)[:700])
            win.statusBar().showMessage(msg)

        win.run_async(job, done, "애니메이션 뽑는 중")

    def open_dropped(self, kind: str, paths: List[str]) -> None:
        """이 탭의 목록에서 뷰포트로 끌어다 놓았을 때: 더블클릭과 같은 방식으로 열고, 탭은 그대로 둔다 (파일·매핑 탭으로 이동하지 않음)."""
        if not paths:
            return
        if kind == "mesh":
            if len(paths) > 1:
                self.win.statusBar().showMessage("메시는 한 번에 하나만 열 수 있어 첫 번째(%s)를 열었습니다" % assetidx.mesh_name(paths[0]))
            self.open_mesh_in_viewport(paths[0])
        else:
            self.open_in_viewport(list(paths))

    def _mesh_double_clicked(self, item, *_a) -> None:
        self.open_mesh_in_viewport(item.data(Qt.ItemDataRole.UserRole))

    def open_mesh_in_viewport(self, mesh: str) -> None:
        """고른 메시를 리깅(스켈레톤+스킨)이 든 GLB 로 뽑아 바로 뷰포트의 타깃으로 연다 (Blender 필요 없음)."""
        a = self.win._extract_args()
        if a is None:
            return
        win = self.win
        out = win.ex_out.text().strip()
        if not out:
            return
        lay = win.make_layout(layout.character_of(mesh))
        tex = bool(win.ex_tex.isChecked())
        bridge.save_config(self.cfg)
        win._ex_skips = []
        name = assetidx.mesh_name(mesh)

        def job(w):
            def ev(e):
                if e.get("type") == "skip":
                    win._ex_skips.append("%s — %s" % (os.path.basename(e.get("path", "")), e.get("reason", "")))
                if e.get("type") in ("exported", "skip"):
                    w.progress.emit(0, 1, os.path.basename(e.get("path", "")))
            files = extractor.export_assets(*a, [mesh], out, ev, kind="mesh", mesh_format="glb", textures=tex, lay=lay)
            glb = next((f for f in files if f.lower().endswith(".glb")), None)
            if not glb:
                raise RuntimeError("메시를 뽑지 못했습니다. " + " / ".join(win._ex_skips[:3]))
            w.progress.emit(0, 1, "메시 읽는 중")
            m = win.pr.load_target(glb)
            return files, glb, m.skeleton.n

        def done(res):
            files, glb, nbones = res
            self.last_files = list(files)
            win.ex_files.set_paths(files)
            win._after_load("target")   # 탭은 옮기지 않는다
            win.statusBar().showMessage("메시를 뷰포트로 열었습니다: %s (본 %d개)" % (name, nbones) if nbones else
                                        "메시를 열었지만 스켈레톤(리깅)이 없어요: %s — 다른 후보를 써 보세요" % name)

        win.run_async(job, done, "메시 여는 중 (%s)" % name)

    # ================================================================== 추출
    def extract(self, what: str = "both") -> None:
        """what: 'mesh' 메시만 / 'anim' 애니메이션만 / 'both' 둘 다."""
        what = what if what in ("mesh", "anim") else "both"
        meshes = [it.data(Qt.ItemDataRole.UserRole) for it in self.mesh_list.selectedItems()] if what != "anim" else []
        anims = self._picked_anims() if what != "mesh" else []
        if not meshes and not anims:
            QMessageBox.warning(self, "추출", {"mesh": "추출할 메시를 목록에서 고르세요.", "anim": "추출할 애니메이션을 목록에서 고르세요."}.get(what, "추출할 메시나 애니메이션을 목록에서 고르세요."))
            return
        a = self.win._extract_args()
        if a is None:
            return
        win = self.win
        out = win.ex_out.text().strip()
        if not out:
            return
        fmts, variants, preset = win._mesh_settings()
        tex, keep_raw = bool(win.ex_tex.isChecked()), bool(win.ex_keep_raw.isChecked())
        if meshes:
            if not fmts:
                QMessageBox.warning(self, "추출", "0번 탭에서 형식(FBX / OBJ / GLB)을 하나 이상 고르세요.")
                return
            if not win._mesh_blender_ok(fmts):
                return
        lay = win.make_layout(self._single_char())
        lay.chars.update(self._char_map(anims))
        edit = self.edit_panel.options()
        self.edit_panel.save()
        bridge.save_config(self.cfg)
        win._ex_skips = []
        total = len(meshes) + len(anims)

        def job(w):
            done = [0]

            def ev(e):
                if e.get("type") == "skip":
                    win._ex_skips.append("%s — %s" % (os.path.basename(e.get("path", "")), e.get("reason", "")))
                elif e.get("type") == "log":
                    win._ex_skips.append(e.get("message", ""))
                if e.get("type") in ("exported", "skip"):
                    done[0] += 1
                    w.progress.emit(min(done[0] - 1, total - 1), total, os.path.basename(e.get("path", "")))
            files: List[str] = []
            if meshes:
                files += extractor.export_meshes(*a, meshes, out, fmts, variants, preset, win.pr.blender, tex, keep_raw, ev, win._ex_skips.append, lay=lay)
            if anims:
                got = extractor.export_assets(*a, anims, out, ev, kind="anim", lay=lay)
                files += got
                if edit.active():  # 늘리기/압축 등: 추출한 PSA 옆에 가공본을 만든다
                    infos: List[str] = []
                    made, warns = extractor.edit_psa_files(got, edit, on_info=lambda f, i: infos.append("%s — %s" % (os.path.basename(f), i.summary())))
                    files += made
                    win._ex_skips += ["가공(%s): %s" % (edit.describe(), x) for x in infos[:3]] + warns
            return files

        win.run_async(job, self._after_extract, "추출 중")

    def _after_extract(self, files) -> None:
        win = self.win
        self.last_files = list(files or [])
        win.ex_files.set_paths(files)
        if not files:
            why = [x for x in getattr(win, "_ex_skips", []) if x][:6]
            QMessageBox.warning(self, "추출", "추출된 파일이 없습니다.\n\n" + ("\n".join(why) if why else "도우미가 이유를 알려주지 않았습니다."))
            return
        win.statusBar().showMessage("추출 완료: %d개 — '0. 게임에서 추출' 탭 4) 에서 폴더를 열 수 있습니다" % len(files))
        win.tabs.setCurrentIndex(0)

    # ================================================================== AnimRef 연결
    def open_query_file(self, path: str = "") -> None:
        """AnimRef 가 만든 query.json 을 골라 같은 화면을 채운다 (실시간 연결과 같은 동작)."""
        if not path:
            lib = animlink.animref_settings().get("library_dir") or ""
            start = os.path.join(lib, "handoff") if lib else self.cfg.get("last_dir", "")
            path, _ = QFileDialog.getOpenFileName(self, "AnimRef query.json 선택", self.cfg.get("animref_query_dir") or start, "query.json (query.json *.json)")
        if not path:
            return
        try:
            q = animlink.parse_query(path)
        except animlink.LinkError as e:
            QMessageBox.warning(self, "AnimRef 가져오기", str(e))
            return
        self.cfg["animref_query_dir"] = os.path.dirname(os.path.dirname(path))
        bridge.save_config(self.cfg)
        self.load_query(q)
        self.win.tabs.setCurrentIndex(self.win.tabs.indexOf(self))

    def load_query(self, q: dict) -> None:
        """AnimRef 가 보낸 요청으로 화면을 채운다 (UI 스레드에서 불린다)."""
        self.link_query = q
        self.clear_sources()
        for p in q.get("paths", []):
            self._add_source(p, q.get("label", ""), q.get("title", ""))
        if q.get("label") in LABEL_CHOICES:
            self.label.setCurrentIndex(max(self.label.findData(q["label"]), 0))
        hits = q.get("hits") or []
        if hits:  # 타격 시각 개수 = 타수 (AnimRef 가 소리·번쩍임으로 찾은 값이라 틀릴 수 있음)
            self.hits.setValue(min(len(hits), 9))
        ch = q.get("character") or {}
        if ch.get("name"):  # AnimRef 에서 유저가 정한 캐릭터 -> '아는 정보'에 넣어 이름으로 리소스를 먼저 찾는다
            have = [k.name.lower() for k in gamenames.parse_known(self.known.toPlainText())]
            if ch["name"].lower() not in have:
                cur = self.known.toPlainText().strip()
                self.known.setPlainText((cur + "\n" if cur else "") + ch["name"] + (" — %s" % ch["kind"] if ch.get("kind") else ""))
        self.rows, self.confirmed = [], []
        self.char_mode = False
        self.table.setRowCount(0)
        self.mesh_list.clear()
        self.anim_list.clear()
        facts = []
        if q.get("label"):
            facts.append("분류 %s" % q["label"])
        if hits:
            facts.append("타격 시각 %s초 (AnimRef 추정, 틀릴 수 있음)" % ", ".join("%g" % t for t in hits))
        if ch.get("name"):
            facts.append("캐릭터 %s" % ch["name"])
        self._update_src_note(prefix=" · ".join(facts))
        n = len(q.get("paths", []))
        missing = [p for p in q.get("paths", []) if not os.path.isfile(p)]
        nm = animlink.display_name((q.get("paths") or [""])[0], q.get("title", "")) if n else ""
        self.link_banner.setText("AnimRef 요청%s — %s 를 받았어요%s."
                                 % (" #%s" % q["id"] if q.get("id") else "", ("'%s'" % nm) if n == 1 else "영상/클립 %d개" % n, " · " + " · ".join(facts) if facts else ""))
        if missing:
            self.link_banner.setText(self.link_banner.text() + "\n⚠ 영상 파일이 없습니다: " + wrappable(missing[0]))
        if n and not missing:  # 받기만 한다 — 캐릭터 추측은 자동으로 시작하지 않는다 (인식 방식을 고른 뒤 직접 시작)
            sc = self.win.scan
            ready = sc is not None and sc.found and sc.root == self.win.ex_game.text().strip()
            self.link_banner.setText(self.link_banner.text() + ("\n(인식 방식을 확인하고 '캐릭터 추측 시작'을 눌러 주세요.)" if ready else
                                     "\n(먼저 '0. 게임에서 추출' 탭에서 게임 폴더를 검사한 뒤 '캐릭터 추측 시작'을 누르세요.)"))
