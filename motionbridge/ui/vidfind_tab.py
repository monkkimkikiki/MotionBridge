"""'영상으로 찾기' 탭: 영상 속 몬스터를 추측 -> 검수자가 고침 -> 메시/애니메이션 후보 -> 선택 추출.

흐름
  1) 영상/이미지(또는 AnimRef 클립 폴더)를 넣는다.
  2) 인식 방식(Ollama / Claude)을 고르고 [몬스터 추측]. 게임 에셋 이름 목록 안에서 고르게 시킨다.
  3) 검수 표: 장면 이미지와 추측 이름이 보이고, 틀리면 후보 목록/검색으로 메시를 바꾼다. 고친 결과는 기억한다.
  4) [검수 확정]: 메시 목록과, 그 몬스터의 애니메이션 후보(이름 일치 + 분류 + 타수 점수)가 나온다.
  5) 체크한 메시/애니메이션을 기존 추출 흐름(Models/, Animations/)으로 뽑는다.
"""
from __future__ import annotations

import os
import re
import shutil
from typing import Dict, List, Optional

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QHeaderView, QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import animlink, animref, assetidx, bridge, extractor, frames, vision
from .widgets import fit_widths, wrappable

INDEX_CACHE = os.path.join(bridge.CONFIG_DIR, "assets.json")
MEMORY_FILE = os.path.join(bridge.CONFIG_DIR, "matches.json")
FRAME_DIR = os.path.join(bridge.CONFIG_DIR, "vf_frames")
MESH_RX = re.compile(r"(?i)/sk[a-z]*_[^/]*$")
ANIM_RX = re.compile(r"(?i)anim")
LIST_PATTERN = r"(?i)(/sk[a-z]*_|anim)"
MONSTER_DIRS = re.compile(r"(?i)/(monsters?|enemies|enemy|bosses|boss|creatures?|npcs?)/")
LABEL_CHOICES = ["타격", "피격", "스킬", "연출", "회피", "사망", "걷기", "달리기", "대기"]
PICK_FILTER = "영상/이미지 (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.png *.jpg *.jpeg *.webp);;모든 파일 (*)"


class _Row:
    """검수 표의 한 줄."""

    def __init__(self, group: vision.Group, combo, search, exclude, base: List[assetidx.MeshCandidate]) -> None:
        self.group, self.combo, self.search, self.exclude, self.base = group, combo, search, exclude, base

    def chosen(self) -> Optional[str]:
        if self.exclude.isChecked():
            return None
        return self.combo.currentData() or None


def _thumb(frame: str, box) -> Optional[object]:
    """장면 이미지(몬스터 위치가 있으면 그 부분)를 작은 그림으로. 읽지 못하면 None."""
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


class VidFindTab(QWidget):
    def __init__(self, win) -> None:
        super().__init__()
        self.win = win
        self.cfg = win.cfg
        self.index: Optional[assetidx.AssetIndex] = None
        self.memory = assetidx.MatchMemory(MEMORY_FILE)
        self.rows: List[_Row] = []
        self.confirmed: List[str] = []
        self.clip_labels: Dict[str, str] = {}
        self.link_query: Optional[dict] = None  # AnimRef 가 보낸 요청 (답을 보내기 전까지)
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

        intro = QLabel("영상(또는 장면 이미지)에 나온 몬스터를 AI가 추측하고, 검수자가 고친 뒤 그 몬스터의 메시와 애니메이션을 골라 추출합니다.\n"
                       "먼저 '0. 게임에서 추출' 탭에서 게임 폴더를 검사해 두세요.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.link_banner = QLabel("")
        self.link_banner.setWordWrap(True)
        self.link_banner.setStyleSheet("color:#5ab0ff")
        lay.addWidget(self.link_banner)

        # 1) 입력
        g = QGroupBox("1) 영상 / 이미지 / AnimRef 클립")
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
        g = QGroupBox("2) 인식 방식 (검수자가 고릅니다)")
        v = QVBoxLayout(g)
        h = QHBoxLayout()
        self.engine = QComboBox()
        self.engine.addItem(vision.OllamaProvider.label, "ollama")
        self.engine.addItem(vision.ClaudeProvider.label, "claude")
        self.engine.setCurrentIndex(max(self.engine.findData(self.cfg.get("vf_engine", "ollama")), 0))
        self.engine.currentIndexChanged.connect(self._engine_changed)
        h.addWidget(self.engine, 1)
        h.addWidget(QLabel("모델"))
        self.model = QLineEdit()
        self.model.setToolTip("Ollama 예: %s\nClaude 예: %s" % (", ".join(vision.OLLAMA_MODELS), ", ".join(vision.CLAUDE_MODELS)))
        h.addWidget(self.model, 1)
        b = QPushButton("연결 확인")
        b.clicked.connect(self.check_engine)
        h.addWidget(b)
        self.btn_pull = QPushButton("모델 받기")
        self.btn_pull.setToolTip("Ollama 에 위 모델을 내려받습니다 (약 6GB, 한 번만). Ollama 가 켜져 있어야 합니다.")
        self.btn_pull.clicked.connect(self.pull_model)
        h.addWidget(self.btn_pull)
        v.addLayout(h)
        self.key_row = QWidget()
        kh = QHBoxLayout(self.key_row)
        kh.setContentsMargins(0, 0, 0, 0)
        kh.addWidget(QLabel("Claude API 키"))
        self.key = QLineEdit(self.cfg.get("vf_claude_key", ""))
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
        self.engine_note = QLabel("")
        self.engine_note.setWordWrap(True)
        v.addWidget(self.engine_note)
        lay.addWidget(g)
        self._engine_changed()

        # 3) 추측
        g = QGroupBox("3) 몬스터 추측")
        v = QVBoxLayout(g)
        h = QHBoxLayout()
        self.btn_guess = QPushButton("몬스터 추측 시작")
        self.btn_guess.setMinimumHeight(32)
        self.btn_guess.clicked.connect(lambda *_a: self.start_guess(False))
        h.addWidget(self.btn_guess, 1)
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
        g = QGroupBox("4) 검수 — 틀린 것은 고쳐 주세요")
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
        h = QHBoxLayout()
        b = QPushButton("몬스터 줄 직접 추가")
        b.setToolTip("AI가 놓친 몬스터를 직접 넣습니다. 줄의 검색 칸에서 메시를 찾으세요.")
        b.clicked.connect(self.add_blank_row)
        h.addWidget(b)
        h.addStretch(1)
        self.btn_confirm = QPushButton("검수 확정 → 애니메이션 후보 보기")
        self.btn_confirm.setMinimumHeight(30)
        self.btn_confirm.clicked.connect(self.confirm)
        h.addWidget(self.btn_confirm)
        v.addLayout(h)
        lay.addWidget(g)

        # 5) 후보 / 추출
        g = QGroupBox("5) 후보에서 골라 추출")
        v = QVBoxLayout(g)
        v.addWidget(QLabel("메시 (확정한 몬스터)"))
        self.mesh_list = QListWidget()
        self.mesh_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.mesh_list.setMinimumHeight(70)
        self.mesh_list.itemSelectionChanged.connect(self._refill_anims)
        v.addWidget(self.mesh_list)
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
        self.anim_list = QListWidget()
        self.anim_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.anim_list.setMinimumHeight(200)
        v.addWidget(self.anim_list)
        self.anim_note = QLabel("")
        self.anim_note.setWordWrap(True)
        v.addWidget(self.anim_note)
        h = QHBoxLayout()
        b = QPushButton("전체 선택")
        b.clicked.connect(lambda *_a: self.anim_list.selectAll())
        h.addWidget(b)
        b = QPushButton("선택 해제")
        b.clicked.connect(lambda *_a: self.anim_list.clearSelection())
        h.addWidget(b)
        h.addStretch(1)
        v.addLayout(h)
        self.btn_extract = QPushButton("선택한 메시 + 애니메이션 추출")
        self.btn_extract.setMinimumHeight(34)
        self.btn_extract.setToolTip("0번 탭의 형식/리깅/대상 프로그램/출력 폴더 설정을 그대로 씁니다.")
        self.btn_extract.clicked.connect(self.extract)
        v.addWidget(self.btn_extract)
        self.link_box = QWidget()
        lh = QHBoxLayout(self.link_box)
        lh.setContentsMargins(0, 0, 0, 0)
        self.btn_link_ok = QPushButton("AnimRef 에 결과 보내기 (고른 메시·애니메이션)")
        self.btn_link_ok.setMinimumHeight(34)
        self.btn_link_ok.clicked.connect(lambda *_a: self.send_result(False))
        lh.addWidget(self.btn_link_ok, 1)
        self.btn_link_none = QPushButton("맞는 게 없음")
        self.btn_link_none.setMinimumHeight(34)
        self.btn_link_none.clicked.connect(lambda *_a: self.send_result(True))
        lh.addWidget(self.btn_link_none)
        v.addWidget(self.link_box)
        self.link_box.setVisible(False)
        lay.addWidget(g)
        lay.addStretch(1)
        fit_widths(inner)

    # ================================================================== 입력
    def _add_source(self, path: str, label: str = "") -> None:
        tag = "[%s] " % label if label else ""
        it = QListWidgetItem(tag + os.path.basename(path))
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
        d = QFileDialog.getExistingDirectory(self, "AnimRef 클립 폴더", self.cfg.get("animref_dir", ""))
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

    def _engine_changed(self, *_a) -> None:
        kind = self._provider_kind()
        if kind == "claude":
            self.model.setText(self.cfg.get("vf_claude_model") or vision.CLAUDE_MODELS[0])
        else:
            self.model.setText(self.cfg.get("vf_ollama_model") or vision.OLLAMA_MODELS[0])
        self.key_row.setVisible(kind == "claude")
        self.btn_pull.setVisible(kind != "claude")
        self.engine_note.setText("영상 이미지가 인터넷으로 Anthropic 에 전송되고 호출한 만큼 요금이 듭니다." if kind == "claude"
                                 else "내 PC에서만 처리됩니다. Ollama 설치 후 'ollama pull %s' 로 모델을 받으세요. 그래픽카드가 약하면 느리고 덜 정확합니다." % vision.OLLAMA_MODELS[0])

    def _save_settings(self) -> None:
        kind = self._provider_kind()
        self.cfg["vf_engine"] = kind
        self.cfg["vf_claude_model" if kind == "claude" else "vf_ollama_model"] = self.model.text().strip()
        self.cfg["vf_save_key"] = bool(self.save_key.isChecked())
        if self.save_key.isChecked():
            self.cfg["vf_claude_key"] = self.key.text().strip()
        else:
            self.cfg.pop("vf_claude_key", None)
        self.cfg["vf_per"] = int(self.per.value())
        self.cfg["vf_hint"] = self.hint.text().strip()
        self.cfg["vf_filter"] = self.filter.text().strip()
        bridge.save_config(self.cfg)

    def provider(self) -> vision.Provider:
        self._save_settings()
        kind = self._provider_kind()
        cfg = dict(self.cfg)
        if kind == "claude":
            cfg["vf_claude_key"] = self.key.text().strip()  # 저장 여부와 상관없이 지금 입력한 키를 쓴다
            cfg["vf_claude_model"] = self.model.text().strip()
        else:
            cfg["vf_ollama_model"] = self.model.text().strip()
        return vision.make_provider(kind, cfg)

    def check_engine(self) -> None:
        prov = self.provider()
        self.win.run_async(lambda w: prov.check(), self._after_check, "연결 확인 중")

    def _after_check(self, res) -> None:
        ok, msg, _names = res
        self.engine_note.setText(("✔ " if ok else "✖ ") + msg)

    def pull_model(self) -> None:
        prov = self.provider()
        if prov.key != "ollama":
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
        ms = set(meshes)
        anims = [p for p in paths if p not in ms and ANIM_RX.search(p)]
        if not meshes:
            raise RuntimeError("메시(SK_*)를 하나도 찾지 못했습니다. 0번 탭에서 UE 버전/mappings 를 확인하세요.")
        idx = assetidx.AssetIndex(meshes, anims)
        assetidx.save_index(INDEX_CACHE, idx, game)
        return idx

    def _vocab(self, idx: assetidx.AssetIndex, limit: int, pat: str):
        """AI에게 알려줄 이름 목록과 안내 문구."""
        notes: List[str] = []
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
                notes.append("메시가 많아 몬스터 폴더(monster/enemy/boss)만 후보로 썼습니다 (%d종)." % len(names))
        if len(names) > limit:
            notes.append("이름 %d종 중 %d종만 AI에게 알렸습니다. '이름 필터'로 줄이면 정확해집니다." % (len(names), limit))
        return names, notes

    def start_guess(self, force_index: bool = False) -> None:
        srcs = self.source_paths()
        if not srcs:
            QMessageBox.warning(self, "몬스터 추측", "영상/이미지를 먼저 추가하세요.")
            return
        a = self.win._extract_args()
        if a is None:
            return
        prov = self.provider()
        ok, msg, _ = prov.check() if prov.key == "claude" else (True, "", [])
        if not ok:
            QMessageBox.warning(self, "인식 방식", msg)
            return
        per, hint, pat = int(self.per.value()), self.hint.text().strip(), self.filter.text().strip()
        ff = frames.find_ffmpeg(self.cfg.get("ffmpeg"))
        w_cap = 24

        def job(w):
            if prov.key == "ollama":  # 모델이 없으면 긴 작업(목록 읽기, 장면 뽑기) 전에 바로 알린다
                ok2, msg2, _n = prov.check()
                if not ok2:
                    raise RuntimeError(msg2)
            idx = self._ensure_index(a, w, force_index)
            try:
                shutil.rmtree(FRAME_DIR, ignore_errors=True)
            except OSError:
                pass
            fr = frames.sample_sources(ff, srcs, FRAME_DIR, per, w_cap, lambda i, n, name: w.progress.emit(i, n, "장면 뽑는 중 " + name))
            vocab, notes = self._vocab(idx, prov.vocab_limit(), pat)
            dets: List[vision.Detection] = []
            errors: List[str] = []
            for i, f in enumerate(fr):
                w.progress.emit(i, len(fr), "몬스터 인식 중 " + os.path.basename(f.path))
                try:
                    dets += prov.analyze(f.path, vocab, hint)
                except vision.VisionError as e:
                    errors.append("%s: %s" % (os.path.basename(f.path), e))
                    if len(errors) >= 3 and not dets:
                        break  # 계속 실패하면 멈춘다 (키/연결 문제)
            return {"idx": idx, "groups": vision.aggregate(dets), "errors": errors, "notes": notes, "frames": len(fr), "vocab": len(vocab)}

        self.win.run_async(job, self._after_guess, "몬스터 추측 중")

    def _after_guess(self, res) -> None:
        self.index = res["idx"]
        groups: List[vision.Group] = res["groups"]
        self.fill_table(groups)
        lines = ["장면 %d장 분석, 몬스터 %d종 추측 (AI에게 알린 이름 %d종, 에셋 메시 %d / 애니메이션 %d)" % (
            res["frames"], len(groups), res["vocab"], len(self.index.meshes), len(self.index.anims))]
        lines += res["notes"]
        if res["errors"]:
            lines.append("인식 오류 %d건: %s" % (len(res["errors"]), res["errors"][0][:200]))
        self.status.setText("\n".join(lines))
        if not groups:
            QMessageBox.warning(self, "몬스터 추측", "몬스터를 찾지 못했습니다.\n\n" + ("\n".join(res["errors"][:3]) or "다른 장면이나 인식 방식을 시도하세요. '몬스터 줄 직접 추가'로 직접 넣을 수도 있습니다."))

    # ================================================================== 검수 표
    def _cand_text(self, c: assetidx.MeshCandidate) -> str:
        return "%s   [%d%%] %s" % (c.name, round(min(c.score, 1.0) * 100), c.why)

    def _fill_combo(self, combo, cands: List[assetidx.MeshCandidate]) -> None:
        combo.clear()
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
        it = QTableWidgetItem("")
        pm = _thumb(g.frame, g.box)
        if pm is not None:
            it.setIcon(QIcon(pm))
        self.table.setItem(r, 0, it)
        name = g.name or "(모름)"
        txt = "%s\n확신 %d%% · %d장면\n%s" % (name, round(g.confidence * 100), g.seen, g.description[:80])
        self.table.setItem(r, 1, QTableWidgetItem(txt))
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(2, 2, 2, 2)
        combo = QComboBox()
        self._fill_combo(combo, base)
        search = QLineEdit()
        search.setPlaceholderText("다른 메시 찾기: 이름 일부를 입력")
        bl.addWidget(combo)
        bl.addWidget(search)
        self.table.setCellWidget(r, 2, box)
        ex = QCheckBox("제외")
        self.table.setCellWidget(r, 3, ex)
        row = _Row(g, combo, search, ex, base)
        search.textChanged.connect(lambda text, rw=row: self._on_search(rw, text))
        self.rows.append(row)
        self.table.setRowHeight(r, 108)

    def _on_search(self, row: _Row, text: str) -> None:
        text = (text or "").strip()
        if not text:
            self._fill_combo(row.combo, row.base)
            return
        idx = self.index
        self._fill_combo(row.combo, idx.search(text) if idx else [])

    def add_blank_row(self) -> None:
        if self.index is None:
            idx = assetidx.load_index(INDEX_CACHE, self.cfg.get("game_dir", ""))
            if idx is None:
                QMessageBox.warning(self, "몬스터 줄 추가", "에셋 이름 목록이 아직 없습니다. 한 번 '몬스터 추측 시작'을 실행하세요.")
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

    def confirm(self) -> None:
        meshes = self.chosen_meshes()
        if not meshes:
            QMessageBox.warning(self, "검수 확정", "메시가 정해진 몬스터가 없습니다. 후보에서 고르거나 검색 칸에서 찾으세요.")
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

    def _refill_anims(self, *_a) -> None:
        self.anim_list.clear()
        idx = self.index
        if idx is None or not self.confirmed:
            return
        label = self.label.currentData() or None
        hits = int(self.hits.value()) or None
        total = 0
        empty: List[str] = []
        for p in self._selected_mesh_paths():
            cands = idx.rank_anims(p, label, hits, 60)
            m = idx.mesh(p)
            nm = m.name if m else assetidx.mesh_name(p)
            if not cands:
                empty.append(nm)
            for c in cands:
                it = QListWidgetItem("%3d%%  %s · %s    %s" % (round(min(max(c.score, 0.0), 1.0) * 100), nm, os.path.basename(c.path), c.why))
                it.setData(Qt.ItemDataRole.UserRole, c.path)
                it.setToolTip(c.path)
                self.anim_list.addItem(it)
                total += 1
        msg = "애니메이션 후보 %d개 (점수 높은 순)." % total
        if empty:
            msg += " 이름이 맞는 애니메이션을 못 찾은 몬스터: %s — 0번 탭에서 직접 검색해 보세요." % ", ".join(empty)
        self.anim_note.setText(msg)

    # ================================================================== 추출
    def extract(self) -> None:
        meshes = [it.data(Qt.ItemDataRole.UserRole) for it in self.mesh_list.selectedItems()]
        anims = [it.data(Qt.ItemDataRole.UserRole) for it in self.anim_list.selectedItems()]
        if not meshes and not anims:
            QMessageBox.warning(self, "추출", "추출할 메시나 애니메이션을 목록에서 고르세요.")
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
                files += extractor.export_meshes(*a, meshes, out, fmts, variants, preset, win.pr.blender, tex, keep_raw, ev, win._ex_skips.append)
            if anims:
                files += extractor.export_assets(*a, anims, extractor.anim_dir(out), ev, kind="anim")
            return files

        win.run_async(job, self._after_extract, "추출 중")

    def _after_extract(self, files) -> None:
        win = self.win
        win.ex_files.set_paths(files)
        if not files:
            why = [x for x in getattr(win, "_ex_skips", []) if x][:6]
            QMessageBox.warning(self, "추출", "추출된 파일이 없습니다.\n\n" + ("\n".join(why) if why else "도우미가 이유를 알려주지 않았습니다."))
            return
        win.statusBar().showMessage("추출 완료: %d개 — '0. 게임에서 추출' 탭 4) 에서 폴더를 열 수 있습니다" % len(files))
        win.tabs.setCurrentIndex(0)

    # ================================================================== AnimRef 연결
    def load_query(self, q: dict) -> None:
        """AnimRef 가 보낸 요청으로 화면을 채운다 (UI 스레드에서 불린다)."""
        self.link_query = q
        self.clear_sources()
        for p in q.get("paths", []):
            self._add_source(p, q.get("label", ""))
        if q.get("label") in LABEL_CHOICES:
            self.label.setCurrentIndex(max(self.label.findData(q["label"]), 0))
        if q.get("hits"):
            self.hits.setValue(int(q["hits"]))
        self.rows, self.confirmed = [], []
        self.table.setRowCount(0)
        self.mesh_list.clear()
        self.anim_list.clear()
        self._update_src_note()
        self.link_box.setVisible(True)
        n = len(q.get("paths", []))
        self.link_banner.setText("AnimRef 요청%s — 영상/클립 %d개. 몬스터를 추측·검수하고 메시/애니메이션을 고른 뒤 '결과 보내기', 없으면 '맞는 게 없음'을 누르세요."
                                 % (" #%s" % q["id"] if q.get("id") else "", n))
        sc = self.win.scan
        if n and sc is not None and sc.found and sc.root == self.win.ex_game.text().strip():
            self.start_guess()  # 게임 폴더를 이미 검사해 뒀으면 바로 추측 시작
        elif n:
            self.link_banner.setText(self.link_banner.text() + "\n(먼저 '0. 게임에서 추출' 탭에서 게임 폴더를 검사한 뒤 '몬스터 추측 시작'을 누르세요.)")

    def send_result(self, none: bool) -> None:
        q = self.link_query
        if q is None:
            return
        meshes = [it.data(Qt.ItemDataRole.UserRole) for it in self.mesh_list.selectedItems()]
        anims = [it.data(Qt.ItemDataRole.UserRole) for it in self.anim_list.selectedItems()]
        if not none and not meshes and not anims:
            QMessageBox.warning(self, "결과 보내기", "보낼 메시나 애니메이션을 목록에서 고르세요. 맞는 게 없으면 '맞는 게 없음'을 누르세요.")
            return
        try:
            out = animlink.deliver(q, meshes, anims, none)
        except OSError as e:
            QMessageBox.warning(self, "결과 보내기", "result.json 을 쓰지 못했습니다: %s" % e)
            return
        self.link_query = None
        self.link_box.setVisible(False)
        self.link_banner.setText("결과를 보냈습니다: %s — %s" % (wrappable(str(out["file"])), "AnimRef 에 알렸습니다." if out["notified"] else "AnimRef 가 꺼져 있어 result.json 만 저장했습니다."))
        self.win.statusBar().showMessage("AnimRef 결과 저장: " + str(out["file"]))
