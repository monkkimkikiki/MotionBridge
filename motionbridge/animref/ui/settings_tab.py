"""③ 설정 탭."""
import copy
from pathlib import Path

import threading

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea,
                               QSlider, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import cleanup, remote, sources, vlm
from ..core import settings as S
from ..core.obs import ObsRecorder, friendly_error

from .widgets import env_info, muted, open_path, restyle


class SettingsTab(QWidget):
    env_checked = Signal(str)   # 분석 환경 확인 결과(다른 스레드에서 옴)

    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setObjectName("page")
        self._loading = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QWidget()
        body.setObjectName("page")
        scroll.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(24, 12, 24, 24)
        root.setSpacing(6)
        root.addWidget(muted("바꾼 내용은 바로 저장돼요. 잘 모르겠으면 그대로 두셔도 됩니다."))

        # --- 저장
        g, f = self._group("💾 저장 위치")
        row = QHBoxLayout()
        self.lib_dir = QLineEdit()
        self.lib_dir.setReadOnly(True)
        b = QPushButton("변경...")
        b.clicked.connect(self._pick_dir)
        o = QPushButton("열기")
        o.clicked.connect(lambda: open_path(self.main.s["library_dir"]))
        row.addWidget(self.lib_dir, 1)
        row.addWidget(b)
        row.addWidget(o)
        f.addRow("클립 저장 폴더", row)
        root.addWidget(g)

        # --- OBS
        g, f = self._group("🎥 OBS 연결")
        self.obs_host = QLineEdit()
        self.obs_port = QSpinBox()
        self.obs_port.setRange(1, 65535)
        self.obs_pw = QLineEdit()
        self.obs_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.obs_pw.setPlaceholderText("OBS [도구 → WebSocket 서버 설정]의 비밀번호")
        pw_row = QHBoxLayout()
        pw_row.addWidget(self.obs_pw, 1)
        show = QCheckBox("보기")
        show.toggled.connect(lambda v: self.obs_pw.setEchoMode(
            QLineEdit.EchoMode.Normal if v else QLineEdit.EchoMode.Password))
        pw_row.addWidget(show)
        test = QPushButton("연결 테스트")
        test.clicked.connect(self._test_obs)
        pw_row.addWidget(test)
        self.hotkey = QComboBox()
        for i in range(1, 13):
            self.hotkey.addItem(f"F{i}", f"f{i}")
        f.addRow("비밀번호", pw_row)
        f.addRow("주소", self.obs_host)
        f.addRow("포트", self.obs_port)
        f.addRow("녹화 시작/종료 단축키", self.hotkey)
        self.obs_host.editingFinished.connect(self._save_obs)
        self.obs_port.valueChanged.connect(self._save_obs)
        self.obs_pw.editingFinished.connect(self._save_obs)
        self.hotkey.currentIndexChanged.connect(self._save_hotkey)
        root.addWidget(g)

        # --- 원본 정리
        g, f = self._group("🧹 원본 영상 정리 (용량 절약)")
        self.pol_rec = QComboBox()
        self.pol_drop = QComboBox()
        for w in (self.pol_rec, self.pol_drop):
            for k, name in cleanup.POLICY_NAMES.items():
                w.addItem(name, k)
        self.pol_rec.currentIndexChanged.connect(lambda i: self._set_policy("source_policy_recorded", self.pol_rec))
        self.pol_drop.currentIndexChanged.connect(lambda i: self._set_policy("source_policy_dropped", self.pol_drop))
        btn_rec = QPushButton("📂 원본 위치 열기")
        btn_rec.setToolTip("OBS로 녹화한 원본 영상이 있는 폴더를 엽니다 (원본은 '그대로 두기'면 지워지지 않아요)")
        btn_rec.clicked.connect(lambda: self._open_original_dir("obs"))
        btn_drop = QPushButton("📂 원본 위치 열기")
        btn_drop.setToolTip("직접 넣었던 영상이 있는 폴더(가장 최근에 연 폴더)를 엽니다")
        btn_drop.clicked.connect(lambda: self._open_original_dir("dropped"))
        self.btn_open_rec, self.btn_open_drop = btn_rec, btn_drop
        for label, combo, btn in (("OBS로 녹화한 원본", self.pol_rec, btn_rec), ("직접 넣은 영상", self.pol_drop, btn_drop)):
            row = QHBoxLayout()
            row.addWidget(combo, 1)
            row.addWidget(btn)
            f.addRow(label, row)
        f.addRow("", muted("분석이 끝까지 성공하고 클립이 1개 이상 저장됐을 때만 정리해요. "
                           "취소·오류가 나거나 클립이 0개면 원본은 그대로 남아요.\n"
                           "※ USB·네트워크 드라이브의 파일은 휴지통 없이 바로 지워질 수 있어요."))
        root.addWidget(g)

        # --- 감지
        g, f = self._group("🔎 캐릭터 감지")
        self.targets = QLineEdit()
        self.targets.editingFinished.connect(lambda: self._set("targets", self.targets.text().strip() or S.DEFAULTS["targets"]))
        f.addRow("찾을 대상 (영어, 쉼표로 구분)", self.targets)
        f.addRow("", muted("예: person, knight, dragon, slime, robot. 게임 캐릭터를 잘 못 찾으면 그 생김새를 영어 단어로 추가하세요."))
        self.conf = QDoubleSpinBox()
        self.conf.setRange(0.05, 0.9)
        self.conf.setSingleStep(0.05)
        self.conf.valueChanged.connect(lambda v: self._set("conf", round(v, 2)))
        f.addRow("감지 확신도 (낮을수록 많이 찾음)", self.conf)
        root.addWidget(g)

        # --- 클립
        g, f = self._group("✂ 클립")
        self.pre = self._dspin(0, 3, 0.1, "pre_pad", "초")
        self.post = self._dspin(0, 3, 0.1, "post_pad", "초")
        self.min_s = self._dspin(0.1, 3, 0.1, "min_s", "초")
        self.max_s = self._dspin(1, 20, 0.5, "max_s", "초")
        self.min_clip = self._dspin(0.5, 10, 0.5, "min_clip_len", "초")
        self.margin = self._dspin(0, 1.5, 0.05, "crop_margin", "")
        f.addRow("동작 시작 전 여유", self.pre)
        f.addRow("동작 끝난 후 여유", self.post)
        f.addRow("이보다 짧은 동작은 버림", self.min_s)
        f.addRow("이보다 긴 동작은 나눔", self.max_s)
        f.addRow("클립 최소 길이 (짧으면 앞뒤로 늘림)", self.min_clip)
        self.hit_pre = self._dspin(0.2, 3, 0.1, "hit_pre", "초")
        self.hit_post = self._dspin(0.2, 3, 0.1, "hit_post", "초")
        self.hit_min = self._dspin(0.3, 5, 0.1, "hit_min_len", "초")
        self.hit_gap = self._dspin(0.1, 1.5, 0.05, "hit_min_gap", "초")
        f.addRow("한 타 클립: 타격 전 최대 여유", self.hit_pre)
        f.addRow("한 타 클립: 타격 후 최대 여유", self.hit_post)
        f.addRow("한 타 클립: 최소 길이", self.hit_min)
        f.addRow("이보다 가까운 타격음은 한 타로 봄", self.hit_gap)
        f.addRow("", muted("한 타 클립은 앞뒤 타격과의 중간 지점에서 끊겨서 서로 겹치지 않아요. "
                           "한 번 휘두를 때 소리가 두 번 나서 한 타가 둘로 나뉘면 마지막 값을 늘리세요."))
        f.addRow("확대 클립 주변 여백 (비율)", self.margin)
        self.crf = QSpinBox()
        self.crf.setRange(12, 28)
        self.crf.valueChanged.connect(lambda v: self._set("crf", v))
        f.addRow("화질 (낮을수록 고화질·큰 파일)", self.crf)
        root.addWidget(g)

        # --- 영상 이해 AI
        g, f = self._group("🧠 AI 판정 (공격·피격·연출 이해)")
        self.engine = QComboBox()
        self.engine.addItem("영상 이해 AI — 정확함 (추천, Ollama 필요)", "vlm")
        self.engine.addItem("기본 분류 (CLIP) — 빠르지만 부정확", "clip")
        self.engine.currentIndexChanged.connect(lambda i: self._set("engine", self.engine.itemData(i)))
        f.addRow("판정 방식", self.engine)
        srow = QHBoxLayout()
        self.vlm_status = QLabel("확인 전")
        self.vlm_status.setWordWrap(True)
        srow.addWidget(self.vlm_status, 1)
        b = QPushButton("상태 확인")
        b.clicked.connect(self.refresh_vlm_status)
        srow.addWidget(b)
        b = QPushButton("Ollama 설치하기")
        b.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(vlm.OLLAMA_DOWNLOAD)))
        srow.addWidget(b)
        f.addRow("상태", srow)
        mrow = QHBoxLayout()
        self.vlm_model = QComboBox()
        self.vlm_model.setEditable(True)
        self.vlm_model.setMinimumWidth(220)
        self.vlm_model.activated.connect(self._save_model)
        self.vlm_model.lineEdit().editingFinished.connect(self._save_model)
        mrow.addWidget(self.vlm_model, 1)
        self.btn_pull = QPushButton("⬇ 모델 내려받기")
        self.btn_pull.clicked.connect(self._pull_model)
        mrow.addWidget(self.btn_pull)
        f.addRow("AI 모델", mrow)
        self.gpu_info = muted("")
        f.addRow("", self.gpu_info)
        self.frames = QSpinBox()
        self.frames.setRange(4, 16)
        self.frames.valueChanged.connect(lambda v: self._set("vlm_frames", v))
        f.addRow("클립당 보여줄 프레임 수", self.frames)
        drow = QHBoxLayout()
        self.auto_discard = QCheckBox("'자동 삭제'로 표시한 분류로 판정되면 클립 삭제")
        self.auto_discard.toggled.connect(lambda v: self._set("auto_discard", v))
        drow.addWidget(self.auto_discard)
        drow.addWidget(muted("  확신도"))
        self.discard_conf = QDoubleSpinBox()
        self.discard_conf.setRange(0.3, 1.0)
        self.discard_conf.setSingleStep(0.05)
        self.discard_conf.valueChanged.connect(lambda v: self._set("discard_min_conf", round(v, 2)))
        drow.addWidget(self.discard_conf)
        drow.addWidget(muted("이상일 때만"))
        drow.addStretch(1)
        f.addRow("비전투 장면", drow)
        f.addRow("", muted("직접 분류를 고친 클립은 자동 삭제되지 않아요. 기본 분류(CLIP)일 때는 지우지 않아요."))

        f.addRow("", muted("분류 항목: 이름 / AI 설명 / 자동 삭제 여부. AI 설명은 영어가 가장 정확하고, 여러 개는 | 로 구분하세요. "
                           "바꾼 뒤 라이브러리에서 [AI 분류 실행]을 누르면 적용돼요."))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["분류 이름", "AI 설명 (영어 권장, | 로 구분)", "자동 삭제"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setMinimumHeight(300)
        self.table.cellChanged.connect(self._table_changed)
        f.addRow(self.table)
        trow = QHBoxLayout()
        for text, fn in [("＋ 분류 추가", self._add_label), ("－ 선택 삭제", self._del_label),
                         ("기본값으로", self._reset_labels)]:
            b = QPushButton(text)
            b.clicked.connect(fn)
            trow.addWidget(b)
        trow.addStretch(1)
        f.addRow(trow)
        root.addWidget(g)

        # --- 묶음
        g, f = self._group("🗂 비슷한 모션 묶기")
        prec = QHBoxLayout()
        prec.addWidget(muted("크게 묶기"))
        self.prec = QSlider(Qt.Orientation.Horizontal)
        self.prec.setRange(0, 100)
        self.prec.valueChanged.connect(lambda v: self._set("group_precision", v))
        prec.addWidget(self.prec, 1)
        prec.addWidget(muted("잘게 묶기"))
        f.addRow("묶음 정밀도", prec)
        self.min_group = QSpinBox()
        self.min_group.setRange(2, 20)
        self.min_group.valueChanged.connect(lambda v: self._set("min_group", v))
        f.addRow("묶음 최소 클립 수", self.min_group)
        root.addWidget(g)

        # --- 분석 환경 (무거운 AI)
        g, f = self._group("🧠 분석 환경 (자동 분석용)")
        f.addRow(muted("캐릭터 탐지 · AI 분류에 쓰는 torch/ultralytics 는 이 프로그램에 들어 있지 않아요. "
                       "AnimRef 를 설치해 둔 폴더의 .venv 가 있으면 자동으로 찾아서, 분석할 때만 그 파이썬을 따로 실행해요."))
        arow = QHBoxLayout()
        self.ed_py = QLineEdit()
        self.ed_py.setPlaceholderText("비워 두면 자동으로 찾기  (예: ...\\AnimRef\\.venv\\Scripts\\python.exe)")
        self.ed_py.editingFinished.connect(self._save_py)
        arow.addWidget(self.ed_py, 1)
        pb = QPushButton("찾아보기…")
        pb.clicked.connect(self._pick_py)
        arow.addWidget(pb)
        cb = QPushButton("다시 확인")
        cb.clicked.connect(lambda: self._check_env(True))
        arow.addWidget(cb)
        f.addRow("python.exe", arow)
        self.env_state = muted("")
        self.env_state.setWordWrap(True)
        f.addRow("상태", self.env_state)
        self.env_checked.connect(self.env_state.setText)
        root.addWidget(g)

        # --- 정보
        g, f = self._group("ℹ 정보")
        self.env = muted("")
        f.addRow("실행 환경", self.env)
        mrow = QHBoxLayout()
        mb = QPushButton("AI 모델 폴더 열기")
        mb.clicked.connect(lambda: open_path(S.models_dir()))
        lb = QPushButton("로그 파일 폴더 열기")
        lb.clicked.connect(lambda: open_path(S.app_dir()))
        rb = QPushButton("모든 설정 초기화")
        rb.clicked.connect(self._reset_all)
        for b in (mb, lb, rb):
            mrow.addWidget(b)
        mrow.addStretch(1)
        f.addRow(mrow)
        f.addRow("버전", muted(S.VERSION))
        root.addWidget(g)
        root.addStretch(1)

    # ---------------------------------------------------------------- helpers
    def _group(self, title):
        g = QGroupBox(title)
        f = QFormLayout(g)
        f.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(10)
        return g, f

    def _dspin(self, lo, hi, step, key, suffix):
        w = QDoubleSpinBox()
        w.setRange(lo, hi)
        w.setSingleStep(step)
        w.setDecimals(2)
        if suffix:
            w.setSuffix(f" {suffix}")
        w.valueChanged.connect(lambda v: self._set(key, round(v, 2)))
        return w

    def _set(self, key, value):
        if self._loading:
            return
        self.main.s[key] = value
        self.main.save_settings()

    def load_from_settings(self):
        s = self.main.s
        self._loading = True
        self.lib_dir.setText(s["library_dir"])
        self.obs_host.setText(s["obs"]["host"])
        self.obs_port.setValue(int(s["obs"]["port"]))
        self.obs_pw.setText(s["obs"]["password"])
        self.hotkey.setCurrentIndex(max(0, self.hotkey.findData(s["hotkey"])))
        self.pol_rec.setCurrentIndex(max(0, self.pol_rec.findData(s["source_policy_recorded"])))
        self.pol_drop.setCurrentIndex(max(0, self.pol_drop.findData(s["source_policy_dropped"])))
        self.targets.setText(s["targets"])
        self.conf.setValue(float(s["conf"]))
        self.pre.setValue(float(s["pre_pad"]))
        self.post.setValue(float(s["post_pad"]))
        self.min_s.setValue(float(s["min_s"]))
        self.max_s.setValue(float(s["max_s"]))
        self.min_clip.setValue(float(s.get("min_clip_len", 2.0)))
        self.hit_pre.setValue(float(s.get("hit_pre", 0.8)))
        self.hit_post.setValue(float(s.get("hit_post", 0.8)))
        self.hit_min.setValue(float(s.get("hit_min_len", 1.0)))
        self.hit_gap.setValue(float(s.get("hit_min_gap", 0.3)))
        self.margin.setValue(float(s["crop_margin"]))
        self.crf.setValue(int(s["crf"]))
        self.prec.setValue(int(s["group_precision"]))
        self.min_group.setValue(int(s["min_group"]))
        self.engine.setCurrentIndex(max(0, self.engine.findData(s.get("engine", "vlm"))))
        self._fill_models()
        self.frames.setValue(int(s.get("vlm_frames", 8)))
        self.auto_discard.setChecked(bool(s.get("auto_discard", True)))
        self.discard_conf.setValue(float(s.get("discard_min_conf", 0.6)))
        self.ed_py.setText(s.get("analysis_python", ""))
        self.table.setRowCount(0)
        for l in s["labels"]:
            self._append_row(l["name"], " | ".join(l.get("prompts", [])), l.get("discard", False))
        self._loading = False
        self.refresh_vlm_status()
        if not self.env.text():
            self.env.setText(env_info() or "-")
        if not self.env_state.text():
            self._check_env()

    def _append_row(self, name, prompts, discard=False):
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(name))
        self.table.setItem(r, 1, QTableWidgetItem(prompts))
        ck = QTableWidgetItem("")
        ck.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        ck.setCheckState(Qt.CheckState.Checked if discard else Qt.CheckState.Unchecked)
        self.table.setItem(r, 2, ck)

    def _table_changed(self, *_):
        if self._loading:
            return
        labels = []
        for r in range(self.table.rowCount()):
            n = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
            p = self.table.item(r, 1).text() if self.table.item(r, 1) else ""
            ck = self.table.item(r, 2)
            d = bool(ck is not None and ck.checkState() == Qt.CheckState.Checked)
            if n:
                labels.append({"name": n, "prompts": [x.strip() for x in p.split("|") if x.strip()], "discard": d})
        if labels:
            self.main.s["labels"] = labels
            self.main.save_settings()

    def _add_label(self):
        self._loading = True
        self._append_row("새 분류", "a character ...")
        self._loading = False
        self._table_changed()
        self.table.editItem(self.table.item(self.table.rowCount() - 1, 0))

    def _del_label(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows or len(rows) >= self.table.rowCount():
            return
        for r in rows:
            self.table.removeRow(r)
        self._table_changed()

    def _reset_labels(self):
        self.main.s["labels"] = copy.deepcopy(S.DEFAULT_LABELS)
        self.main.save_settings()
        self.load_from_settings()

    def _reset_all(self):
        if QMessageBox.question(self, "초기화", "모든 설정을 처음 상태로 되돌릴까요? (클립은 지워지지 않아요)") != QMessageBox.StandardButton.Yes:
            return
        keep = {"library_dir": self.main.s["library_dir"], "game": self.main.s["game"], "welcome_shown": True}
        self.main.s.clear()
        self.main.s.update(copy.deepcopy(S.DEFAULTS))
        self.main.s.update(keep)
        self.main.save_settings()
        self.main.reload_all()

    def _pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "클립 저장 폴더 선택", self.main.s["library_dir"])
        if d:
            self.main.s["library_dir"] = d
            self.main.save_settings()
            self.main.reload_all()

    # ---------------------------------------------------------------- 분석 환경
    def _save_py(self):
        if self._loading:
            return
        self.main.s["analysis_python"] = self.ed_py.text().strip().strip('"')
        self.main.save_settings()
        self._check_env(True)

    def _pick_py(self):
        p, _ = QFileDialog.getOpenFileName(self, "AI 분석용 python.exe (AnimRef 의 .venv\\Scripts)", self.ed_py.text() or "",
                                           "python (python.exe python);;모든 파일 (*)")
        if p:
            self.ed_py.setText(p)
            self._save_py()

    def _check_env(self, force=False):
        """분석 환경을 백그라운드에서 확인한다 (torch 를 불러오느라 몇 초 걸린다)."""
        if remote.local_has_torch():
            self.env_state.setText("✅ 이 프로그램 안에서 바로 분석해요 (torch 있음)")
            return
        self.env_state.setText("확인하는 중...")
        s = dict(self.main.s)

        def work():
            if force:
                remote._CHECKED.clear()
            py, msg = remote.find_env(s)
            if py:
                self.env_checked.emit("✅ 분석 환경: %s  (%s)" % (py, msg))
            else:
                self.env_checked.emit("⚠ 쓸 수 있는 분석 환경이 없어요. %s\n자동 분석을 하려면 AnimRef 의 .venv 의 python.exe 를 위에서 골라 주세요. "
                                      "(영상 보기·직접 자르기·타격 나누기·라이브러리는 이것 없이도 돼요)" % (msg or ""))
        threading.Thread(target=work, daemon=True).start()

    # ---------------------------------------------------------------- 영상 이해 AI
    def _fill_models(self):
        rec, desc = vlm.recommend_model()
        vram, gpu = vlm.gpu_vram_gb()
        self.vlm_model.blockSignals(True)
        self.vlm_model.clear()
        self.vlm_model.addItem(f"자동 추천 ({rec})", "")
        for _need, m, d in vlm.RECOMMENDED:
            self.vlm_model.addItem(f"{m}  —  {d}", m)
        cur = self.main.s.get("vlm_model", "")
        i = self.vlm_model.findData(cur)
        if i < 0 and cur:
            self.vlm_model.addItem(cur, cur)
            i = self.vlm_model.count() - 1
        self.vlm_model.setCurrentIndex(max(0, i))
        self.vlm_model.blockSignals(False)
        if gpu:
            self.gpu_info.setText(f"그래픽카드: {gpu} ({vram:.0f}GB) → 추천 모델 {rec}")
        else:
            self.gpu_info.setText(f"NVIDIA 그래픽카드를 찾지 못했어요 → 가벼운 모델 {rec} 추천 (판정이 느릴 수 있어요)")

    def _save_model(self, *_):
        if self._loading:
            return
        i = self.vlm_model.currentIndex()
        text = self.vlm_model.currentText().strip()
        data = self.vlm_model.itemData(i) if i >= 0 and self.vlm_model.itemText(i) == text else None
        model = data if data is not None else text.split()[0] if text else ""
        if model.startswith("자동"):
            model = ""
        if model == self.main.s.get("vlm_model", ""):
            return  # 바뀐 게 없음 (Enter + activated 중복 호출 방지)
        self.main.s["vlm_model"] = model
        self.main.save_settings()
        self.refresh_vlm_status()

    def refresh_vlm_status(self):
        ok, names, _err = vlm.status(self.main.s)
        model = vlm.model_of(self.main.s)
        if not ok:
            self.vlm_status.setText("● Ollama가 실행 중이 아니에요. 설치 후 실행하세요 (설치하면 자동 실행돼요).")
            restyle(self.vlm_status, "statusBad")
        elif not vlm.has_model(names, model):
            self.vlm_status.setText(f"● Ollama 실행 중 · 모델 {model} 없음 → [모델 내려받기]를 누르세요.")
            restyle(self.vlm_status, "statusBad")
        else:
            self.vlm_status.setText(f"● 준비 완료 · {model}")
            restyle(self.vlm_status, "statusOk")
        return ok

    def _pull_model(self):
        ok, _, _ = vlm.status(self.main.s)
        if not ok:
            QMessageBox.information(self, "Ollama 필요",
                                    "먼저 Ollama를 설치해 주세요.\n\n[Ollama 설치하기]를 눌러 설치 파일을 받고 실행하면 돼요. "
                                    "설치가 끝나면 Ollama가 자동으로 실행됩니다.")
            return
        model = vlm.model_of(self.main.s)
        self.main.start_job("모델 내려받기", vlm.pull, (copy.deepcopy(self.main.s), model),
                            on_done=lambda _: self.refresh_vlm_status())

    def original_dir(self, kind):
        """원본 영상이 있는 폴더. kind: 'obs'(OBS 녹화 폴더) / 'dropped'(직접 넣은 영상: 마지막에 연 폴더). 못 찾으면 ''."""
        st = self.main.s
        if kind == "obs" and not (st.get("obs_record_dir") and Path(st["obs_record_dir"]).is_dir()):
            try:
                self.main.collect.remember_obs_dir()  # OBS 가 켜져 있으면 녹화 폴더를 물어본다
            except Exception:
                pass
        keys = ["obs_record_dir", "last_dir"] if kind == "obs" else ["last_dir", "obs_record_dir"]
        for k in keys:
            d = st.get(k)
            if d and Path(d).is_dir():
                return str(d)
        dirs = sources.candidate_dirs(st)
        return dirs[0] if dirs else ""

    def _open_original_dir(self, kind):
        d = self.original_dir(kind)
        if not d:
            QMessageBox.information(self, "원본 위치", "원본 폴더를 아직 알 수 없어요.\n[수집하기] 탭에서 OBS로 녹화하거나 영상을 한 번 열면 알게 돼요.")
            return
        open_path(d)

    def _set_policy(self, key, combo):
        if self._loading:
            return
        value = combo.currentData()
        if value == cleanup.DELETE and QMessageBox.question(
                self, "완전히 삭제",
                "분석이 끝난 원본을 휴지통을 거치지 않고 바로 삭제해요. 복구할 수 없어요.\n정말 이렇게 설정할까요?"
        ) != QMessageBox.StandardButton.Yes:
            self._loading = True
            combo.setCurrentIndex(max(0, combo.findData(self.main.s[key])))
            self._loading = False
            return
        self.main.s[key] = value
        self.main.save_settings()

    def _save_obs(self, *_):
        if self._loading:
            return
        self.main.s["obs"] = {"host": self.obs_host.text().strip() or "localhost",
                              "port": self.obs_port.value(), "password": self.obs_pw.text()}
        self.main.save_settings()

    def _save_hotkey(self, i):
        if self._loading:
            return
        self.main.s["hotkey"] = self.hotkey.itemData(i)
        self.main.save_settings()
        self.main.update_hotkey()

    def _test_obs(self):
        self._save_obs()
        try:
            r = ObsRecorder(self.main.s["obs"])
            v = r.version()
            r.close()
            QMessageBox.information(self, "OBS 연결", f"연결 성공! (OBS {v})")
        except Exception as e:
            QMessageBox.warning(self, "OBS 연결", friendly_error(e))
