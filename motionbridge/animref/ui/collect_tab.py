"""① 수집하기 탭: OBS 녹화 버튼 / 영상 드래그앤드롭."""
import copy
import time
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QInputDialog, QMessageBox, QPushButton, QSlider, QVBoxLayout, QWidget)

from ..core import bossrun, cleanup, library, media, pipeline, sources, titles
from ..core.obs import ObsRecorder, friendly_error
from ..core.settings import SPEED_NAMES

from .widgets import card, muted, restyle
from .boss_panel import BossPanel
from .widgets import open_path, reveal

OBS_HELP = """<h3>OBS 연결 방법 (처음 한 번만)</h3>
<ol>
<li>OBS를 실행합니다.</li>
<li>메뉴 <b>도구 → WebSocket 서버 설정</b>을 엽니다.</li>
<li><b>WebSocket 서버 활성화</b>에 체크합니다.</li>
<li><b>연결 정보 표시</b>를 눌러 비밀번호를 확인하고, 이 프로그램의 <b>[설정] 탭 → OBS 비밀번호</b>에 붙여 넣습니다.
(인증을 끄면 비밀번호 없이도 됩니다)</li>
<li>OBS <b>소스</b>에 <b>게임 캡처</b>를 추가해서 게임 화면이 OBS에 보이게 합니다.</li>
<li>더 좋은 레퍼런스를 원하면 OBS <b>설정 → 영상</b>에서 FPS를 <b>60</b>으로 맞추세요.</li>
</ol>
<p>준비가 끝나면 이 화면의 <b>[연결 확인]</b>을 누르세요.</p>"""


class _ThumbLoader(QThread):
    ready = Signal(str, str)

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


class DropZone(QFrame):
    filesDropped = Signal(list)

    def __init__(self):
        super().__init__()
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(90)
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QLabel("🎞")
        icon.setObjectName("big")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        text = QLabel("영상 파일(또는 폴더)을 여기에 끌어다 놓으세요")
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = muted("또는 클릭해서 파일 선택  ·  mp4, mkv, mov 등")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for w in (icon, text, sub):
            lay.addWidget(w)

    def _active(self, on):
        self.setProperty("active", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e):
        exts = " ".join(f"*{x}" for x in sorted(media.VIDEO_EXTS))
        w = self
        while w is not None and not hasattr(w, "collect"):  # AnimRef 메인 화면 (창이든 탭이든)
            w = w.parent()
        start = sources.best_start_dir(w.s) if w is not None else ""
        files, _ = QFileDialog.getOpenFileNames(self, "분석할 영상 선택", start, f"영상 파일 ({exts});;모든 파일 (*)")
        if files:
            self.filesDropped.emit(files)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._active(True)

    def dragLeaveEvent(self, e):
        self._active(False)

    def dropEvent(self, e):
        self._active(False)
        out = []
        for u in e.mimeData().urls():
            p = Path(u.toLocalFile())
            if p.is_dir():
                out += [str(x) for x in sorted(p.iterdir()) if x.suffix.lower() in media.VIDEO_EXTS]
            elif p.suffix.lower() in media.VIDEO_EXTS:
                out.append(str(p))
        if out:
            self.filesDropped.emit(out)


class CollectTab(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setObjectName("page")
        self.rec = None
        self.rec_start = None
        self.recorded = set()  # AnimRef로 OBS 녹화한 파일 (분석 목록에 들어간 경우)
        self.rec_timer = QTimer(self)
        self.rec_timer.timeout.connect(self._tick)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        intro = muted("영상 속 캐릭터(아군·적 모두)의 공격·피격·스킬 동작을 AI가 찾아서 클립으로 잘라 줍니다. "
                      "아래 두 방법 중 편한 쪽을 쓰세요.")
        root.addWidget(intro)

        row = QHBoxLayout()
        row.setSpacing(16)
        root.addLayout(row, 1)

        # --- 녹화 카드
        c1, l1 = card("🎮  플레이하면서 녹화", "OBS로 녹화하세요. 녹화한 영상은 오른쪽 '원본 영상 목록'에 쌓이고, 골라서 분석해요.")
        srow = QHBoxLayout()
        self.obs_status = QLabel("OBS 상태 확인 전")
        self.obs_status.setObjectName("muted")
        btn_check = QPushButton("연결 확인")
        btn_check.clicked.connect(lambda: self.check_obs(show_error=True))
        btn_help = QPushButton("연결 방법")
        btn_help.setObjectName("link")
        btn_help.clicked.connect(self.show_obs_help)
        srow.addWidget(self.obs_status, 1)
        srow.addWidget(btn_help)
        srow.addWidget(btn_check)
        l1.addLayout(srow)
        l1.addStretch(1)
        self.rec_label = QLabel("")
        self.rec_label.setObjectName("rec")
        self.rec_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.rec_label.hide()
        l1.addWidget(self.rec_label)
        self.btn_rec = QPushButton("●  녹화 시작")
        self.btn_rec.setObjectName("primary")
        self.btn_rec.setMinimumHeight(54)
        self.btn_rec.clicked.connect(self.toggle_record)
        l1.addWidget(self.btn_rec)
        self.hotkey_hint = muted("")
        self.hotkey_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l1.addWidget(self.hotkey_hint)
        self.boss = BossPanel(self)
        l1.addWidget(self.boss)
        row.addWidget(c1, 1)

        # --- 파일 카드
        c2, l2 = card("📁  녹화해 둔 영상 분석", "이미 녹화한 영상이 있다면 넣기만 하세요.")
        self.drop = DropZone()
        self.drop.filesDropped.connect(self.add_files)
        l2.addWidget(self.drop, 1)
        prow2 = QHBoxLayout()
        b_files = QPushButton("📄 영상 파일 선택…")
        b_files.setToolTip("탐색기에서 영상 파일을 직접 골라요 (여러 개 가능). 끌어다 놓지 않아도 돼요.")
        b_files.clicked.connect(self._pick_files)
        b_folder = QPushButton("📁 폴더 가져오기…")
        b_folder.setToolTip("폴더를 골라 그 안의 영상(하위 폴더 포함)을 한꺼번에 분석 목록에 넣어요.")
        b_folder.clicked.connect(self._pick_folder)
        prow2.addWidget(b_files)
        prow2.addWidget(b_folder)
        l2.addLayout(prow2)
        self.queue = QListWidget()
        self.queue.setMaximumHeight(110)
        self.queue.itemDoubleClicked.connect(lambda it: self.main.open_editor(it.data(Qt.ItemDataRole.UserRole)))
        self.queue.setToolTip("더블클릭하면 타임라인 편집기로 열어요")
        self.queue.hide()
        l2.addWidget(self.queue)
        qrow = QHBoxLayout()
        self.btn_clear = QPushButton("목록 비우기")
        self.btn_clear.clicked.connect(self.clear_queue)
        self.btn_clear.hide()
        self.btn_analyze = QPushButton("▶  분석 시작")
        self.btn_analyze.setObjectName("primary")
        self.btn_analyze.setMinimumHeight(54)
        self.btn_analyze.setEnabled(False)
        self.btn_analyze.clicked.connect(self.start_analyze)
        qrow.addWidget(self.btn_clear)
        qrow.addWidget(self.btn_analyze, 1)
        l2.addLayout(qrow)
        l2.addWidget(QLabel("📼  원본 영상 목록  (녹화한 영상이 여기 쌓여요)"))
        self.src_list = QListWidget()
        self.src_list.setIconSize(QSize(128, 72))
        self.src_list.setSpacing(2)
        self.src_list.setMinimumHeight(190)
        self.src_list.setToolTip("영상을 고르고 아래 버튼을 누르세요. 더블클릭하면 원하는 구간만 골라 분석하는 창이 열려요.")
        self.src_list.itemDoubleClicked.connect(lambda it: self.analyze_range(it.data(Qt.ItemDataRole.UserRole)))
        l2.addWidget(self.src_list, 1)
        self.src_empty = muted("")
        l2.addWidget(self.src_empty)
        self.clips_title = muted("이 영상의 클립")
        l2.addWidget(self.clips_title)
        self.src_clips = QListWidget()
        self.src_clips.setMaximumHeight(110)
        self.src_clips.setToolTip("이 영상으로 만든 클립이에요. 더블클릭하면 편집기에서 그 위치를 열어요.")
        self.src_clips.itemDoubleClicked.connect(self._open_src_clip)
        l2.addWidget(self.src_clips)
        self.src_list.currentItemChanged.connect(lambda *_a: self._fill_src_clips())
        from PySide6.QtGui import QKeySequence, QShortcut
        QShortcut(QKeySequence("F2"), self.src_list, activated=lambda: self.rename_source(self._cur_src()),
                  context=Qt.ShortcutContext.WidgetShortcut)
        srow3 = QHBoxLayout()
        self.btn_src_all = QPushButton("▶ 전체 분석")
        self.btn_src_all.setToolTip("고른 영상 전체를 분석해요")
        self.btn_src_all.clicked.connect(lambda: self.analyze_source(self._cur_src()))
        self.btn_src_name = QPushButton("✏ 이름")
        self.btn_src_name.setToolTip("영상 제목을 붙여요 (예: 이름없는자 보스전). 그 영상으로 만든 클립이 '제목 00:00:00~00:00:10' 처럼 보여요.\n표시 이름만 바뀌고 영상 파일은 그대로예요. (F2)")
        self.btn_src_name.clicked.connect(lambda: self.rename_source(self._cur_src()))
        self.btn_src_range = QPushButton("✂ 구간만 분석…")
        self.btn_src_range.setToolTip("영상을 보면서 원하는 부분(여러 구간 가능)만 골라 분석해요")
        self.btn_src_range.clicked.connect(lambda: self.analyze_range(self._cur_src()))
        b_ref = QPushButton("↻")
        b_ref.setToolTip("목록 새로고침")
        b_ref.setMaximumWidth(36)
        b_ref.clicked.connect(self.refresh_sources)
        b_dir = QPushButton("📂")
        b_dir.setToolTip("고른 영상 파일이 있는 폴더를 열어요 (탐색기에서 그 파일이 선택돼요). 고른 영상이 없으면 녹화 폴더를 열어요.")
        b_dir.setMaximumWidth(36)
        b_dir.clicked.connect(self._reveal_source)
        for b in (self.btn_src_all, self.btn_src_range, self.btn_src_name, b_ref, b_dir):
            srow3.addWidget(b)
        l2.addLayout(srow3)
        self._thumbs = None
        erow = QHBoxLayout()
        b_edit = QPushButton("✂ 타임라인에서 직접 자르기")
        b_edit.setToolTip("원본 영상을 보면서 시작/끝을 지정해 클립을 직접 만들어요. AI가 찾은 동작은 책갈피로 표시돼요.")
        b_edit.clicked.connect(self._open_editor)
        erow.addWidget(b_edit, 1)
        l2.addLayout(erow)
        row.addWidget(c2, 1)

        # --- 옵션 카드
        c3, l3 = card("✂  어떻게 잘라낼까요?")
        g = QHBoxLayout()
        g.setSpacing(28)
        sens_box = QVBoxLayout()
        sens_box.addWidget(QLabel("클립 양"))
        srow2 = QHBoxLayout()
        srow2.addWidget(muted("적게\n(큰 동작만)"))
        self.sens = QSlider(Qt.Orientation.Horizontal)
        self.sens.setRange(0, 100)
        self.sens.setMinimumWidth(220)
        self.sens.valueChanged.connect(lambda v: self._set("sensitivity", v))
        srow2.addWidget(self.sens, 1)
        srow2.addWidget(muted("많이\n(작은 동작까지)"))
        sens_box.addLayout(srow2)
        g.addLayout(sens_box, 2)

        sp_box = QVBoxLayout()
        sp_box.addWidget(QLabel("분석 속도"))
        self.speed = QComboBox()
        for k, v in SPEED_NAMES.items():
            self.speed.addItem(v, k)
        self.speed.currentIndexChanged.connect(lambda i: self._set("speed", self.speed.itemData(i)))
        sp_box.addWidget(self.speed)
        sp_box.addWidget(QLabel("클립 나누기"))
        self.split = QComboBox()
        for k, v in [("both", "긴 동작 + 한 타씩 (둘 다)"), ("combo", "긴 동작(연속기)으로만"), ("hit", "한 타씩만")]:
            self.split.addItem(v, k)
        self.split.setToolTip("한 타씩: 타격음·번쩍임이 난 순간마다 따로 잘라요. 연속기도 1타·2타·3타로 나뉘어요.")
        self.split.currentIndexChanged.connect(lambda i: self._set("split_mode", self.split.itemData(i)))
        sp_box.addWidget(self.split)
        g.addLayout(sp_box, 1)

        ck_box = QVBoxLayout()
        self.ck_crop = QCheckBox("캐릭터 확대 클립도 저장 (캐릭터 주변만 잘라냄)")
        self.ck_full = QCheckBox("전체 화면 클립 저장")
        self.ck_ai = QCheckBox("잘라낸 뒤 AI로 자동 판정 (타격/피격/스킬/연출...)")
        self.ck_autorec = QCheckBox("녹화를 끝내면 바로 분석 (끄면 원본 영상 목록에 넣어 두고, 골라서 분석)")
        self.ck_autorec.setToolTip("기본은 꺼짐: 녹화한 영상은 목록에 쌓이고, 필요한 영상/구간만 골라 분석해요.")
        self.ck_autorec.toggled.connect(lambda v: self._set("auto_analyze_recording", v))
        self.ck_combat = QCheckBox("효과음·번쩍임이 있는 구간만 (걷기·이동 장면 줄이기)")
        self.ck_combat.toggled.connect(lambda v: self._set("combat_only", v))
        self.ck_move = QCheckBox("걷기·달리기 같은 이동 장면도 포함")
        self.ck_move.setToolTip("켜면 전투 단서(효과음·번쩍임)가 없어도 캐릭터가 움직이는 장면을 모두 클립으로 만들고,\n"
                                "AI가 걷기/달리기/이동으로 판정해도 지우지 않아요. 클립이 많아지니 분석 후 '걷기·달리기' 분류로 골라 보세요.")
        self.ck_move.toggled.connect(self._on_move)
        self.ck_crop.toggled.connect(lambda v: self._set("save_crop", v))
        self.ck_full.toggled.connect(lambda v: self._set("save_full", v))
        self.ck_ai.toggled.connect(lambda v: self._set("auto_classify", v))
        for w in (self.ck_full, self.ck_crop, self.ck_move, self.ck_combat, self.ck_ai, self.ck_autorec):
            ck_box.addWidget(w)
        g.addLayout(ck_box, 2)
        l3.addLayout(g)
        prow = QHBoxLayout()
        self.policy_label = muted("")
        prow.addWidget(self.policy_label, 1)
        btn_policy = QPushButton("바꾸기")
        btn_policy.setObjectName("link")
        btn_policy.clicked.connect(lambda: self.main.tabs.setCurrentWidget(self.main.settings_tab))
        prow.addWidget(btn_policy)
        l3.addLayout(prow)
        root.addWidget(c3)

        self.load_from_settings()
        QTimer.singleShot(800, lambda: self.check_obs(show_error=False))
        QTimer.singleShot(1200, self.refresh_sources)

    # ---------------------------------------------------------------- 설정 연동
    def load_from_settings(self):
        s = self.main.s
        self._loading = True
        self.sens.setValue(int(s["sensitivity"]))
        self.split.setCurrentIndex(max(0, self.split.findData(s.get("split_mode", "both"))))
        i = self.speed.findData(s.get("speed", "normal"))
        self.speed.setCurrentIndex(max(0, i))
        self.ck_crop.setChecked(bool(s["save_crop"]))
        self.ck_full.setChecked(bool(s["save_full"]))
        self.ck_ai.setChecked(bool(s["auto_classify"]))
        self.ck_combat.setChecked(bool(s.get("combat_only", True)))
        self.ck_autorec.setChecked(bool(s.get("auto_analyze_recording", False)))
        self.ck_move.setChecked(bool(s.get("include_move", False)))
        self._sync_combat()
        self.hotkey_hint.setText(f"게임 중에는 키보드 {s['hotkey'].upper()} 키로 녹화 시작/종료")
        pn = cleanup.POLICY_NAMES
        self.policy_label.setText(
            f"🧹 분석이 끝난 원본:  OBS 녹화본 → {pn.get(s['source_policy_recorded'], '그대로 두기')}  ·  "
            f"직접 넣은 영상 → {pn.get(s['source_policy_dropped'], '그대로 두기')}   (클립이 만들어졌을 때만)")
        self._loading = False

    COMBAT_TEXT = "효과음·번쩍임이 있는 구간만 (걷기·이동 장면 줄이기)"

    def _sync_combat(self):
        """'이동 장면도 포함'이 켜져 있으면 전투 단서 필터는 쓰이지 않는다. 눌러도 안 바뀌는 이유를 글로 알려 준다."""
        move = self.ck_move.isChecked()
        self.ck_combat.setEnabled(not move)
        self.ck_combat.setText(self.COMBAT_TEXT + ("  ← 아래 '이동 장면도 포함'이 켜져 있어서 지금은 바꿀 수 없어요" if move else ""))
        self.ck_combat.setToolTip("끄려면 먼저 '걷기·달리기 같은 이동 장면도 포함'을 꺼 주세요." if move else "")

    def _on_move(self, v):
        self._sync_combat()
        self._set("include_move", v)

    def _set(self, key, value):
        if getattr(self, "_loading", False):
            return
        self.main.s[key] = value
        if key in ("save_crop", "save_full") and not (self.main.s["save_crop"] or self.main.s["save_full"]):
            self.ck_full.setChecked(True)  # 최소 하나는 저장
        self.main.save_settings()

    def set_busy(self, busy):
        self.btn_analyze.setEnabled(not busy and self.queue.count() > 0)
        if hasattr(self, "btn_src_all"):
            has = self.src_list.count() > 0
            self.btn_src_all.setEnabled(has and not busy)
            self.btn_src_range.setEnabled(has and not busy)
            self.btn_src_name.setEnabled(has)
        if self.rec is None:
            self.btn_rec.setEnabled(not busy)

    # ---------------------------------------------------------------- 파일 분석
    def add_files(self, files):
        existing = {self.queue.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.queue.count())}
        for f in files:
            if f in existing:
                continue
            self.queue.addItem(Path(f).name)
            it = self.queue.item(self.queue.count() - 1)
            it.setData(Qt.ItemDataRole.UserRole, f)
            it.setToolTip(f)
        self._queue_changed()

    def _open_editor(self):
        it = self.queue.currentItem()
        self.main.open_editor(it.data(Qt.ItemDataRole.UserRole) if it else None)

    def clear_queue(self):
        self.queue.clear()
        self._queue_changed()

    def _queue_changed(self):
        has = self.queue.count() > 0
        self.queue.setVisible(has)
        self.btn_clear.setVisible(has)
        self.btn_analyze.setEnabled(has and not self.main.busy)
        self.btn_analyze.setText(f"▶  분석 시작 ({self.queue.count()}개)" if has else "▶  분석 시작")

    def start_analyze(self):
        files = [self.queue.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.queue.count())]
        if files and self._analyze(files):
            self.clear_queue()

    def _origin(self, f):
        """OBS 로 녹화한 영상이면 RECORDED(원본 정리 설정이 '녹화본' 쪽), 아니면 DROPPED."""
        if f in self.recorded:
            return pipeline.RECORDED
        d = self.main.s.get("obs_record_dir")
        try:
            if d and Path(f).resolve().parent == Path(d).resolve():
                return pipeline.RECORDED
        except OSError:
            pass
        return pipeline.DROPPED

    def _analyze(self, files):
        origins = {f: self._origin(f) for f in files}
        s = self.main.s
        dropped = [f for f in files if origins[f] == pipeline.DROPPED]
        pol = s["source_policy_dropped"]
        if dropped and pol != cleanup.KEEP:
            what = "휴지통으로 옮겨요" if pol == cleanup.TRASH else "완전히 삭제해요 (복구 불가)"
            if QMessageBox.question(
                    self, "원본 정리 확인",
                    f"분석이 끝나고 클립이 만들어지면 넣은 영상 원본 {len(dropped)}개를 {what}.\n계속할까요?\n\n"
                    "(원본을 남기려면 [설정] → 원본 영상 정리에서 '그대로 두기'로 바꾸세요)"
            ) != QMessageBox.StandardButton.Yes:
                return False
        if self.main.start_job("영상 분석", pipeline.process_videos, (files, copy.deepcopy(s)),
                               on_done=self.main.on_clips_made, kwargs={"origins": origins}):
            self.recorded -= set(files)
            return True
        return False

    # ---------------------------------------------------------------- 원본 영상 목록
    def _cur_src(self):
        it = self.src_list.currentItem()
        if it is None:
            QMessageBox.information(self, "원본 영상", "목록에서 영상을 먼저 골라 주세요.")
            return None
        return it.data(Qt.ItemDataRole.UserRole)

    def rename_source(self, path):
        """원본 영상 제목 붙이기/바꾸기. 클립은 이 제목을 앞에 달고 '00:00:00~00:00:10' 으로 보인다."""
        if not path:
            return
        cur = titles.title_of(path)
        text, ok = QInputDialog.getText(self, "영상 제목", "영상 제목 (비우면 파일 이름으로 돌아가요. 영상 파일은 그대로예요):",
                                        text=cur or Path(path).stem)
        if not ok:
            return
        t = text.strip()
        titles.set_title(path, "" if t == Path(path).stem else t)
        self.refresh_sources()
        try:
            self.main.library.refresh()
        except Exception:  # noqa: BLE001
            pass
        self.main.flash("영상 제목을 바꿨어요: " + titles.display(path))

    def _clips_of(self, path):
        try:
            clips = library.load_clips(library.game_dir(self.main.s))
        except Exception:  # noqa: BLE001
            return []
        name = Path(path).name
        mine = [m for m in clips if m.get("source_video") == path or m.get("source_name") == name]
        return sorted(mine, key=lambda m: float(m.get("start_in_source", 0) or 0))

    def _fill_src_clips(self):
        self.src_clips.clear()
        it = self.src_list.currentItem()
        path = it.data(Qt.ItemDataRole.UserRole) if it else None
        if not path:
            self.clips_title.setText("이 영상의 클립")
            return
        mine = self._clips_of(path)
        self.clips_title.setText(f"이 영상의 클립 ({len(mine)}개)" if mine else "이 영상의 클립 — 아직 없어요")
        for m in mine:
            a = float(m.get("start_in_source", 0) or 0)
            row = QListWidgetItem(f"{titles.clip_title(m)}   {library.final_label(m)}")
            row.setData(Qt.ItemDataRole.UserRole, (path, a))
            self.src_clips.addItem(row)

    def _open_src_clip(self, it):
        path, a = it.data(Qt.ItemDataRole.UserRole)
        self.main.open_editor(path, a)

    def analyze_source(self, path):
        if path and self._analyze([path]):
            self.refresh_sources()

    def analyze_range(self, path):
        """영상을 보면서 원하는 구간만 골라 분석 (구간을 안 고르면 전체)."""
        if not path:
            return
        if not Path(path).exists():
            QMessageBox.information(self, "원본 없음", "영상을 찾을 수 없어요:\n" + str(path))
            return
        from .range_dialog import RangeDialog
        dlg = RangeDialog(self.main, path)
        if not dlg.exec():
            return
        if not dlg.ranges:
            self.analyze_source(path)
            return
        self.main.start_job("구간 분석", pipeline.process_ranges, (path, list(dlg.ranges), copy.deepcopy(self.main.s)),
                            on_done=self.main.on_clips_made)

    def _reveal_source(self):
        """고른 원본 영상의 파일 위치를 연다 (고른 게 없으면 녹화 폴더)."""
        it = self.src_list.currentItem()
        path = it.data(Qt.ItemDataRole.UserRole) if it else None
        if path and Path(path).exists():
            reveal(path)
        elif path:
            QMessageBox.information(self, "원본 없음", "영상 파일이 그 자리에 없어요 (옮겼거나 지웠어요):\n" + str(path))
        else:
            self._open_rec_dir()

    def _pick_files(self):
        exts = " ".join(f"*{x}" for x in sorted(media.VIDEO_EXTS))
        files, _ = QFileDialog.getOpenFileNames(self, "분석할 영상 선택", sources.best_start_dir(self.main.s),
                                                f"영상 파일 ({exts});;모든 파일 (*)")
        if files:
            self.add_files(files)
            self.refresh_sources()

    def _pick_folder(self):
        """폴더를 골라 그 안의 영상(하위 폴더 포함)을 모두 분석 목록에 넣는다."""
        d = QFileDialog.getExistingDirectory(self, "영상이 들어 있는 폴더 선택", sources.best_start_dir(self.main.s))
        if not d:
            return
        files = sources.videos_in_folder(d, recursive=True)
        if not files:
            QMessageBox.information(self, "폴더", "이 폴더(하위 폴더 포함)에서 영상 파일을 찾지 못했어요.\n" + d)
            return
        self.main.s["last_dir"] = d
        self.main.save_settings()
        self.add_files(files)
        self.refresh_sources()
        self.main.flash("영상 %d개를 분석 목록에 넣었어요 (%s)" % (len(files), d))

    def _open_rec_dir(self):
        dirs = sources.candidate_dirs(self.main.s)
        if dirs:
            open_path(dirs[0])

    def refresh_sources(self):
        q = [self.queue.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.queue.count())]
        try:
            items = sources.list_sources(self.main.s, list(q) + sorted(self.recorded))[:100]
        except Exception:  # noqa: BLE001
            items = []
        cur = self.src_list.currentItem().data(Qt.ItemDataRole.UserRole) if self.src_list.currentItem() else None
        self.src_list.clear()
        need = []
        for src in items:
            it = QListWidgetItem(f"{src.get('title') or src['name']}\n{sources.describe(src)}")
            it.setData(Qt.ItemDataRole.UserRole, src["path"])
            it.setToolTip(src["path"] + ("\n(파일 이름: %s — 제목은 표시 이름이라 파일은 그대로예요)" % src["name"] if src.get("title") else ""))
            if src["thumb"]:
                it.setIcon(QIcon(QPixmap(src["thumb"])))
            else:
                need.append(src["path"])
            self.src_list.addItem(it)
            if cur and src["path"] == cur:
                self.src_list.setCurrentItem(it)
        if items and self.src_list.currentItem() is None:
            self.src_list.setCurrentRow(0)
        self.src_empty.setText("" if items else "아직 영상이 없어요. 녹화를 하거나 위에 영상 파일을 끌어다 놓으세요.")
        self.btn_src_all.setEnabled(bool(items) and not self.main.busy)
        self.btn_src_range.setEnabled(bool(items) and not self.main.busy)
        self.btn_src_name.setEnabled(bool(items))
        self._fill_src_clips()
        if self._thumbs is not None and self._thumbs.isRunning():
            self._thumbs.stop = True
            self._thumbs.wait(3000)
        if need:
            self._thumbs = _ThumbLoader(need[:60])
            self._thumbs.ready.connect(self._thumb_ready)
            self._thumbs.start()

    def _thumb_ready(self, path, thumb):
        for i in range(self.src_list.count()):
            it = self.src_list.item(i)
            if it.data(Qt.ItemDataRole.UserRole) == path:
                it.setIcon(QIcon(QPixmap(thumb)))

    # ---------------------------------------------------------------- OBS 녹화
    def show_obs_help(self):
        box = QMessageBox(self)
        box.setWindowTitle("OBS 연결 방법")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(OBS_HELP)
        box.exec()

    def _set_status(self, text, ok):
        self.obs_status.setText(text)
        restyle(self.obs_status, "statusOk" if ok else "statusBad")

    def check_obs(self, show_error=False):
        try:
            r = ObsRecorder(self.main.s["obs"])
            v = r.version()
            rec = r.is_recording()
            self._save_obs_dir(r.record_dir())
            r.close()
            self._set_status(f"● OBS 연결됨 (v{v})" + (" · 녹화 중" if rec else ""), True)
            return True
        except Exception as e:
            self._set_status("● OBS에 연결되지 않음", False)
            if show_error:
                QMessageBox.warning(self, "OBS 연결", friendly_error(e) + "\n\n[연결 방법]을 눌러 설정 방법을 확인하세요.")
            return False

    def _save_obs_dir(self, d):
        if d and Path(d).is_dir() and self.main.s.get("obs_record_dir") != str(d):
            self.main.s["obs_record_dir"] = str(d)
            self.main.save_settings()

    def remember_obs_dir(self):
        """OBS가 켜져 있으면 녹화 폴더 위치를 가져와 저장 (꺼져 있으면 저장된 값 사용)."""
        try:
            r = ObsRecorder(self.main.s["obs"])
            self._save_obs_dir(r.record_dir())
            r.close()
        except Exception:
            pass

    def toggle_record(self):
        if getattr(self, "_rec_busy", False):  # 단축키 연타/대화상자 중복 방지
            return
        self._rec_busy = True
        try:
            if self.rec is None:
                self.start_record()
            else:
                self.stop_record()      # 사람이 직접 끝냄 (자동으로 시작한 녹화여도)
        finally:
            self._rec_busy = False

    def start_record(self, auto=False):
        """auto=True: 보스전 감지가 시작한 녹화 (실패해도 대화상자를 띄우지 않는다)."""
        if self.main.busy:
            self.main.flash("다른 작업이 끝난 뒤에 녹화를 시작할 수 있어요.")
            return
        try:
            self.rec = ObsRecorder(self.main.s["obs"])
            self.rec.start()
        except Exception as e:
            self.rec = None
            self._set_status("● OBS에 연결되지 않음", False)
            if auto:
                self.main.flash("보스전 자동 녹화를 시작하지 못했어요: %s" % friendly_error(e))
            else:
                QMessageBox.warning(self, "녹화 시작 실패", friendly_error(e))
            return
        self.rec_auto = bool(auto)
        self.rec_start = time.time()
        self.rec_timer.start(500)
        self._tick()
        self.rec_label.show()
        self.btn_rec.setText("■  녹화 끝내고 분석하기" if self.main.s.get("auto_analyze_recording") else "■  녹화 끝내기")
        restyle(self.btn_rec, "danger")
        self._set_status("● OBS 녹화 중" + (" (보스전 자동)" if auto else ""), True)
        if not auto:
            self.main.flash("녹화를 시작했어요. 게임을 플레이하세요!")

    def stop_record(self, auto=False, prebuffer=None, min_seconds=0.0, took=0.0):
        """auto=True: 보스전 감지가 끝낸 녹화. 아니면 사람이 누른 것 -> 자동 감지에는 '직접 멈춤'을 알린다."""
        if not auto and getattr(self, "rec_auto", False):
            self.boss.manual_stopped()
        self.rec_auto = False
        path, err = None, None
        try:
            path = self.rec.stop()
        except Exception as e:
            err = e
        finally:
            if self.rec:
                self.rec.close()
            self.rec = None
            self.rec_timer.stop()
            self.rec_label.hide()
            self.btn_rec.setText("●  녹화 시작")
            restyle(self.btn_rec, "primary")
            self._set_status("● OBS 연결됨", True)
            self.set_busy(self.main.busy)
        if err is not None:
            QMessageBox.warning(self, "녹화 종료 실패", friendly_error(err))
            return
        if not path:
            return
        self._save_obs_dir(str(Path(path).parent))
        if auto and took and took < float(min_seconds):
            self.main.flash("보스전 녹화가 %d초로 너무 짧아 분석하지 않았어요 (영상은 그대로 남겨요)." % int(took))
            return
        if prebuffer and Path(prebuffer).exists():
            merged = bossrun.merge_prebuffer(prebuffer, path)     # 원본 두 파일은 지우지 않는다
            if merged:
                path = merged
        self.recorded.add(path)
        if not self.main.s.get("auto_analyze_recording", False):
            # 기본: 바로 분석하지 않고 '원본 영상 목록'에 쌓아 둔다. 목록에서 영상/구간을 골라 분석한다.
            self.refresh_sources()
            for i in range(self.src_list.count()):
                if self.src_list.item(i).data(Qt.ItemDataRole.UserRole) == path:
                    self.src_list.setCurrentRow(i)
            self.main.flash("녹화한 영상을 '원본 영상 목록'에 넣었어요. 목록에서 골라 분석하세요.")
            return
        if self.main.busy:
            self.add_files([path])
            self.main.flash("다른 작업이 진행 중이라 녹화 파일을 분석 목록에 추가했어요.")
            return
        self.main.start_job("녹화 영상 분석", pipeline.process_recording, (path, copy.deepcopy(self.main.s)),
                            on_done=self.main.on_clips_made)

    def _tick(self):
        if self.rec_start:
            sec = int(time.time() - self.rec_start)
            self.rec_label.setText(f"● 녹화 중  {sec // 60:02d}:{sec % 60:02d}")
