"""메인 창: 상단 게임 선택, 탭(수집/라이브러리/설정), 하단 작업 진행 패널."""
import os

import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QInputDialog, QLabel, QMainWindow,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QTabWidget,
                               QVBoxLayout, QWidget)

from ..core import library, link, remote
from ..core import settings as S

from .collect_tab import CollectTab
from .library_tab import LibraryTab
from .settings_tab import SettingsTab
from .widgets import Hotkey, Job, muted

STATE_TEXT = {"live": "추출 툴: 🟢 연결됨", "running": "추출 툴: 🟡 실행 중 (실시간 연결 안 됨)",
              "off": "추출 툴: ⚪ 꺼져 있음", "unknown": "추출 툴: ❔ 확인 안 됨"}
STATE_TIP = {"live": "추출 툴과 실시간으로 연결돼 있어요.",
             "running": "추출 툴 프로그램은 켜져 있지만 AnimRef 와 연결하는 창구가 없어요. 보내기는 폴더 방식으로 동작해요.\n(툴이 연동규격 8절을 지원하면 🟢 이 돼요)",
             "off": "추출 툴이 꺼져 있어요. 보내면 실행 명령으로 켜요.",
             "unknown": "툴이 켜져 있는지 알아낼 이름이 없어요. AI 모캡 창의 ②-C 에서 '켜졌는지 확인할 이름'을 적어 주세요."}

NEW_GAME = "＋ 새 게임 추가..."

WELCOME = """<h2>AnimRef에 오신 걸 환영해요 👋</h2>
<p>게임 영상에서 캐릭터의 <b>공격 · 피격 · 스킬</b> 동작을 AI가 찾아 클립으로 잘라 주는 프로그램이에요.</p>
<h3>사용 순서</h3>
<ol>
<li><b>위쪽 [게임]</b>에서 게임 이름을 정하세요. 게임마다 따로 정리돼요.</li>
<li><b>[① 수집하기]</b>에서 녹화해 둔 영상을 끌어다 놓고 <b>[분석 시작]</b>을 누르거나,<br>
OBS를 연결해서 <b>[녹화 시작]</b> → 플레이 → <b>[녹화 끝내기]</b>를 누르세요. 녹화한 영상은 <b>'원본 영상 목록'</b>에 쌓이고,
영상을 골라 <b>[전체 분석]</b>하거나 <b>[구간만 분석…]</b>으로 원하는 부분만 골라 분석할 수 있어요.</li>
<li><b>[② 라이브러리]</b>에서 분류된 클립을 보고, 더블클릭하면 <b>슬로모션·프레임 단위</b>로 볼 수 있어요.</li>
<li>직접 고르고 싶으면 <b>✂ 직접 자르기</b>: 원본 타임라인에서 AI 책갈피를 보며 구간을 지정해 클립을 만들어요.</li>
</ol>
<h3>더 정확한 판정을 원하면 (추천)</h3>
<p><b>[⚙ 설정] → AI 판정</b>에서 <b>[Ollama 설치하기]</b> → 설치 → <b>[모델 내려받기]</b>를 누르세요.<br>
영상 이해 AI가 클립을 보고 <b>타격·피격·스킬·연출</b>을 구분하고, 걷기 같은 비전투 장면은 자동으로 지워 줘요.</p>
<p style="color:#8b93a7">처음 분석할 때는 AI 모델(약 0.5GB)을 내려받느라 몇 분 더 걸릴 수 있어요.</p>"""


class MainWindow(QMainWindow):
    link_result = Signal(str, str)     # 추출 툴이 결과를 보냄 (클립 이름, 표시 문구) — 다른 스레드에서 와도 안전하게 화면 스레드로 전달
    link_focus = Signal(str)           # 추출 툴이 "이 클립 보여줘" 요청
    link_state = Signal(str)           # 추출 툴 상태: live / running / off / unknown

    def __init__(self, embedded=False, tool_open=None):
        """embedded=True: MotionBridge 안의 탭으로 쓴다. 따로 창이 되지 않고, 추출 툴 연결은 tool_open(query_path)로 직접 한다."""
        super().__init__()
        self.embedded = bool(embedded)
        self.s = S.load()
        self.job = None
        self.tool_online = "unknown"
        self.link_server = None
        self._link_checking = False
        self.setWindowTitle(f"AnimRef — 게임 모션 레퍼런스 수집기  v{S.VERSION}")
        icon = S.resource_path("assets/icon.png")
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        if self.embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
            self.setMinimumSize(480, 400)
        else:
            self.resize(1280, 820)
            self.setMinimumSize(1000, 680)

        central = QWidget()
        central.setObjectName("page")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- 헤더
        header = QFrame()
        header.setObjectName("header")
        h = QHBoxLayout(header)
        h.setContentsMargins(24, 12, 24, 12)
        t = QLabel("🎬  AnimRef")
        t.setObjectName("appTitle")
        h.addWidget(t)
        h.addWidget(muted("게임 모션 레퍼런스 수집기"))
        h.addStretch(1)
        self.lbl_link = QLabel("")
        self.lbl_link.setToolTip("게임 애니메이션 추출 툴과의 실시간 연결 상태")
        h.addWidget(self.lbl_link)
        h.addWidget(QLabel("게임"))
        self.game_combo = QComboBox()
        self.game_combo.setMinimumWidth(220)
        self.game_combo.activated.connect(self._game_selected)
        h.addWidget(self.game_combo)
        help_btn = QPushButton("❔ 사용법")
        help_btn.clicked.connect(self.show_welcome)
        h.addWidget(help_btn)
        root.addWidget(header)

        # --- 탭
        self.tabs = QTabWidget()
        self.collect = CollectTab(self)
        self.library = LibraryTab(self)
        self.settings_tab = SettingsTab(self)
        self.tabs.addTab(self.collect, "① 수집하기")
        self.tabs.addTab(self.library, "② 라이브러리")
        self.tabs.addTab(self.settings_tab, "⚙ 설정")
        self.tabs.currentChanged.connect(self._tab_changed)
        root.addWidget(self.tabs, 1)

        # --- 하단 작업 패널
        panel = QFrame()
        panel.setObjectName("taskPanel")
        pv = QVBoxLayout(panel)
        pv.setContentsMargins(24, 10, 24, 10)
        pr = QHBoxLayout()
        self.status = QLabel("준비됨")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.hide()
        self.btn_cancel = QPushButton("취소")
        self.btn_cancel.hide()
        self.btn_cancel.clicked.connect(self.cancel_job)
        self.btn_log = QPushButton("로그 보기")
        self.btn_log.setCheckable(True)
        self.btn_log.toggled.connect(lambda v: self.log_view.setVisible(v))
        pr.addWidget(self.status, 2)
        pr.addWidget(self.progress, 3)
        pr.addWidget(self.btn_cancel)
        pr.addWidget(self.btn_log)
        pv.addLayout(pr)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(170)
        self.log_view.hide()
        pv.addWidget(self.log_view)
        root.addWidget(panel)

        self.hotkey = Hotkey(self.s["hotkey"])
        self.hotkey.pressed.connect(self.collect.toggle_record, Qt.ConnectionType.QueuedConnection)

        self.link_result.connect(self._on_link_result)
        self.link_focus.connect(self._on_link_focus)
        self.link_state.connect(self._on_link_state)
        if self.embedded:
            link.LOCAL_TOOL = tool_open
            self.tool_online = "live" if tool_open else "off"
            self.lbl_link.setText("추출 툴: 🟢 MotionBridge (같은 프로그램)" if tool_open else "")
            self.lbl_link.setToolTip("추출 툴(MotionBridge)과 한 프로그램이라 '추출 툴로 보내기'가 바로 '영상찾기' 탭으로 넘어가요.")
        else:
            self._start_link()

        self.reload_all()
        if not self.s.get("welcome_shown") and not self.embedded:  # 탭 안에서는 자동으로 창을 띄우지 않는다([❔ 사용법]으로 열기)
            QTimer.singleShot(400, self.show_welcome)

    # ---------------------------------------------------------------- 공용
    @property
    def busy(self):
        return self.job is not None and self.job.isRunning()

    def save_settings(self):
        S.save(self.s)

    def log(self, text):
        self.log_view.appendPlainText(str(text).rstrip())

    def flash(self, text, ms=5000):
        if not self.busy:
            self.status.setText(text)
        self.statusBar().showMessage(text, ms)

    # ---------------------------------------------------------------- 추출 툴 실시간 연결
    def _start_link(self):
        self.lbl_link.setText("")
        if not self.s.get("link_enabled", True):
            return
        try:
            self.link_server = link.LinkServer(
                self.s,
                on_result=lambda meta, ga: self.link_result.emit(
                    os.path.splitext(os.path.basename(meta["_json"]))[0], link.handoff.describe(ga)),
                on_focus=lambda clip_id: self.link_focus.emit(clip_id))
            self.link_server.start()
        except Exception as e:  # noqa: BLE001  연결 기능이 안 돼도 앱은 정상 동작해야 해요
            self.link_server = None
            self.lbl_link.setText("추출 툴: 연결 기능 꺼짐")
            self.lbl_link.setToolTip(f"실시간 연결을 시작하지 못했어요: {e}\n(파일 방식은 그대로 쓸 수 있어요)")
            return
        self._link_timer = QTimer(self)
        self._link_timer.timeout.connect(self._poll_tool)
        self._link_timer.start(4000)
        QTimer.singleShot(300, self._poll_tool)

    def _poll_tool(self):
        if self._link_checking:
            return
        self._link_checking = True

        def work():
            try:
                self.link_state.emit(link.tool_status(self.s)[0])
            finally:
                self._link_checking = False
        threading.Thread(target=work, daemon=True).start()

    def _on_link_state(self, state):
        self.tool_online = state
        self.lbl_link.setText(STATE_TEXT.get(state, STATE_TEXT["unknown"]))
        self.lbl_link.setToolTip(STATE_TIP.get(state, ""))

    def _on_link_result(self, clip_name, text):
        self.library.refresh()
        self.flash(f"🔗 추출 툴에서 결과가 왔어요: {clip_name} → {text}", 8000)
        self.log(f"[추출 툴] {clip_name} → {text}")

    def _on_link_focus(self, clip_id):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.tabs.setCurrentWidget(self.library)
        self.flash(f"추출 툴이 클립을 보여 달라고 했어요: {clip_id}", 6000)

    def reload_all(self):
        library.game_dir(self.s)
        self._fill_games()
        self.collect.load_from_settings()
        self.settings_tab.load_from_settings()
        self.library.refresh()
        self.update_hotkey()

    def update_hotkey(self):
        self.hotkey.set_key(self.s["hotkey"])
        self.collect.load_from_settings()

    def show_welcome(self):
        box = QMessageBox(self)
        box.setWindowTitle("사용법")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(WELCOME)
        box.addButton("시작하기", QMessageBox.ButtonRole.AcceptRole)
        box.exec()
        if not self.s.get("welcome_shown"):
            self.s["welcome_shown"] = True
            self.save_settings()

    # ---------------------------------------------------------------- 게임 선택
    def _fill_games(self):
        games = library.list_games(self.s)
        cur = library.safe_name(self.s["game"])
        if cur not in games:
            games.append(cur)
        self.game_combo.blockSignals(True)
        self.game_combo.clear()
        for g in sorted(games):
            self.game_combo.addItem(g, g)
        self.game_combo.addItem(NEW_GAME, NEW_GAME)
        self.game_combo.setCurrentIndex(max(0, self.game_combo.findData(cur)))
        self.game_combo.blockSignals(False)

    def _game_selected(self, i):
        g = self.game_combo.itemData(i)
        if g == NEW_GAME:
            name, ok = QInputDialog.getText(self, "새 게임", "게임 이름을 입력하세요 (예: 엘든링, 로스트아크)")
            name = name.strip()
            if not ok or not name:
                self._fill_games()
                return
            g = name
        self.s["game"] = g
        self.save_settings()
        self.reload_all()
        self.flash(f"게임: {g}")

    def _tab_changed(self, i):
        w = self.tabs.widget(i)
        if w is self.library:
            self.library.refresh()
        elif w is self.collect:
            self.collect.load_from_settings()
        elif w is self.settings_tab:
            self.settings_tab.load_from_settings()

    # ---------------------------------------------------------------- 작업 실행
    def start_job(self, title, fn, args, on_done=None, kwargs=None):
        if self.busy:
            QMessageBox.information(self, "작업 중", "다른 작업이 진행 중이에요. 끝난 뒤에 다시 시도해 주세요.")
            return False
        self._last_job = (title, fn, args, on_done, kwargs)   # 분석 환경을 설치한 뒤 이어서 다시 하려고 기억
        self.job = Job(title, fn, args, kwargs)
        self.job.log.connect(self.log)
        self.job.progress.connect(self._on_progress)
        self.job.succeeded.connect(lambda r: self._on_done(title, r, on_done))
        self.job.failed.connect(lambda msg, cancelled: self._on_failed(title, msg, cancelled))
        self.job.finished.connect(lambda: self._set_busy(False))
        self.log(f"── {title} 시작")
        self.status.setText(f"{title} 준비 중...")
        self.progress.setValue(0)
        self.progress.setRange(0, 0)
        self._set_busy(True)
        self.job.start()
        return True

    def _set_busy(self, busy):
        self.progress.setVisible(busy)
        self.btn_cancel.setVisible(busy)
        self.btn_cancel.setEnabled(True)
        self.collect.set_busy(busy)
        self.library.set_busy(busy)
        self.game_combo.setEnabled(not busy)

    def _on_progress(self, frac, text):
        if frac < 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(frac * 1000))
        if text:
            self.status.setText(text)

    def _on_done(self, title, result, on_done):
        self.log(f"── {title} 완료")
        self.status.setText(f"✅ {title} 완료")
        self.library.refresh()
        try:
            self.collect.refresh_sources()
        except Exception:  # noqa: BLE001
            pass
        if on_done:
            on_done(result)

    def _on_failed(self, title, msg, cancelled):
        self.log(f"── {title}: {msg}")
        self.status.setText(f"{'⏹' if cancelled else '⚠'} {msg.splitlines()[0]}")
        self.library.refresh()
        if not cancelled and remote.is_need_env(msg):
            self.offer_env_install(retry=getattr(self, "_last_job", None))
            return
        if not cancelled:
            QMessageBox.warning(self, f"{title} 실패", msg + "\n\n자세한 내용은 아래 [로그 보기]에서 확인할 수 있어요.")

    def offer_env_install(self, retry=None):
        """AI 분석 환경이 없을 때: 오류만 띄우지 않고 바로 설치할지 묻고, 설치가 끝나면 하던 분석을 이어서 한다."""
        r = QMessageBox.question(
            self, "AI 분석 환경 설치",
            "자동 분석(캐릭터 탐지 · AI 분류)에 쓰는 AI 구성요소(torch · ultralytics)가 아직 이 PC에 없어요.\n\n"
            "지금 자동으로 설치할까요?\n"
            "· 처음 한 번만 설치하면 돼요 (디스크 약 5GB, 인터넷 필요, 10~30분).\n"
            "· 파이썬(3.9~3.12)이 없으면 winget 으로 3.11 을 먼저 설치해요.\n"
            + ("· 설치가 끝나면 방금 하려던 분석을 이어서 자동으로 시작해요.\n" if retry else "· 설치가 끝나면 하려던 작업을 다시 눌러 주세요.\n") +
            "· 중간에 취소해도 다음에 이어서 설치돼요.")
        if r == QMessageBox.StandardButton.Yes:
            self.install_analysis_env(retry)

    def install_analysis_env(self, retry=None):
        from ..core import envsetup
        if remote.local_has_torch():
            QMessageBox.information(self, "분석 환경", "이 프로그램 안에 이미 분석 환경이 있어요.")
            return False

        def job(log=None, progress=None, cancel=None):
            return envsetup.install(log or (lambda *_a: None), progress or (lambda *_a: None), cancel)

        def done(py):
            self.s["analysis_python"] = ""      # 자동으로 찾는 위치에 만들었으니 비워 둔다
            self.save_settings()
            remote._CHECKED.clear()
            try:
                self.settings_tab._check_env(True)
            except Exception:  # noqa: BLE001
                pass
            self.flash("분석 환경을 만들었어요." + (" 하던 분석을 이어서 시작해요." if retry else ""))
            if retry:
                title, fn, args, on_done, kwargs = retry
                QTimer.singleShot(300, lambda: self.start_job(title, fn, args, on_done=on_done, kwargs=kwargs))

        return self.start_job("분석 환경 설치", job, (), on_done=done)

    def cancel_job(self):
        if self.busy:
            self.job.cancel.set()
            self.btn_cancel.setEnabled(False)
            self.status.setText("취소하는 중... (현재 단계가 끝나면 멈춰요)")

    def on_clips_made(self, new):
        n = len(new or [])
        disc = getattr(new, "discarded", 0) or 0
        if n == 0 and disc:
            QMessageBox.information(self, "분석 완료",
                                    f"후보 {disc}개가 모두 비전투 장면(이동/대기 등)으로 판정돼서 지웠어요.\n"
                                    "원본 영상은 그대로 남겨 뒀어요.\n\n"
                                    "AI 판정이 틀린 것 같으면 [설정] → AI 판정에서 '자동 삭제'를 끄고 다시 분석해 보세요.")
            return
        if n == 0:
            QMessageBox.information(self, "분석 완료",
                                    "액션 동작을 찾지 못했어요. (원본 영상은 그대로 남겨 뒀어요)\n\n"
                                    "· [① 수집하기]의 '클립 양'을 '많이' 쪽으로 옮기거나\n"
                                    "· [설정]의 '찾을 대상'에 캐릭터 생김새를 영어로 추가해 보세요.")
            return
        text = f"새 클립 {n}개를 만들었어요! 🎉"
        if disc:
            text += f"\n(비전투 장면으로 판정된 {disc}개는 자동으로 지웠어요)"
        cleaned = getattr(new, "cleaned", ()) or ()
        trashed = sum(1 for _, pol in cleaned if pol == "trash")
        deleted = sum(1 for _, pol in cleaned if pol == "delete")
        if trashed:
            text += f"\n\n원본 영상 {trashed}개는 휴지통으로 옮겼어요. (필요하면 휴지통에서 복원할 수 있어요)"
        if deleted:
            text += f"\n\n원본 영상 {deleted}개를 삭제했어요."
        box = QMessageBox(self)
        box.setWindowTitle("분석 완료")
        box.setText(text)
        go = box.addButton("라이브러리에서 보기", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("닫기", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is go:
            self.tabs.setCurrentWidget(self.library)

    def open_editor(self, path=None, start_at=None):
        """타임라인 편집기 열기. path가 없으면 원본 영상 고르기 화면."""
        from pathlib import Path as _P

        from ..core import classify

        from .editor import ClipEditor

        if not path:
            from .source_picker import SourcePicker

            self.collect.remember_obs_dir()  # OBS가 켜져 있으면 녹화 폴더를 최신으로
            q = self.collect.queue
            queue_paths = [q.item(i).data(Qt.ItemDataRole.UserRole) for i in range(q.count())]
            picker = SourcePicker(self, queue_paths)
            if not picker.exec() or not picker.chosen:
                return
            path = picker.chosen
        if not _P(path).exists():
            QMessageBox.information(self, "원본 없음",
                                    f"원본 영상을 찾을 수 없어요:\n{path}\n\n"
                                    "원본 정리 설정으로 휴지통에 옮겨졌을 수 있어요. 휴지통에서 복원한 뒤 다시 시도하세요.\n"
                                    "(타임라인 편집을 자주 쓰려면 [설정] → 원본 영상 정리를 '그대로 두기'로 바꾸세요)")
            return
        dlg = ClipEditor(path, self, start_at)
        dlg.exec()
        if dlg.changed:
            self.library.refresh()
            if self.s.get("auto_classify", True) and not self.busy:
                import copy as _copy

                self.start_job("AI 분류", classify.run, (_copy.deepcopy(self.s),),
                               on_done=lambda _: self.library.refresh())

    def edit_clip(self, meta):
        """라이브러리 클립 수정 (길이 조절 / 나누기 / 새 클립 저장)."""
        import copy as _copy

        from ..core import classify
        from ..core.edit import source_for

        from .editor import ClipEditor

        if self.busy:
            QMessageBox.information(self, "작업 중", "분석이 끝난 뒤에 수정해 주세요.")
            return False
        path, offset, is_source = source_for(meta)
        dlg = ClipEditor(path, self, edit_meta=meta, offset=offset, is_source=is_source)
        dlg.exec()
        if dlg.changed:
            self.library.refresh()
            if self.s.get("auto_classify", True) and not self.busy:
                self.start_job("AI 분류", classify.run, (_copy.deepcopy(self.s),),
                               on_done=lambda _: self.library.refresh())
        return dlg.changed

    def retime_clips(self, metas):
        """속도 조절(타임 스트레치) · 자르고 붙이기 · 여러 클립 이어붙이기."""
        import copy as _copy

        from ..core import classify

        from .retime import RetimeDialog

        if self.busy:
            QMessageBox.information(self, "작업 중", "분석이 끝난 뒤에 편집해 주세요.")
            return False
        if not metas:
            return False
        try:
            dlg = RetimeDialog(self, metas)
        except Exception as e:
            QMessageBox.warning(self, "클립 열기 실패", str(e))
            return False
        dlg.exec()
        if dlg.changed:
            self.library.refresh()
            if self.s.get("auto_classify", True) and not self.busy:
                self.start_job("AI 분류", classify.run, (_copy.deepcopy(self.s),),
                               on_done=lambda _: self.library.refresh())
        return dlg.changed

    def split_hits_clips(self, metas):
        """길게 잘린 클립을 타수별(1타·2타·3타…)로 나누기."""
        import copy as _copy

        from ..core import classify

        from .hitsplit import HitSplitDialog

        if self.busy:
            QMessageBox.information(self, "작업 중", "분석이 끝난 뒤에 나눠 주세요.")
            return False
        if not metas:
            return False
        try:
            dlg = HitSplitDialog(self, metas)
        except Exception as e:
            QMessageBox.warning(self, "타수 나누기", str(e))
            return False
        dlg.exec()
        if dlg.changed:
            self.library.refresh()
            if self.s.get("auto_classify", True) and not self.busy:
                self.start_job("AI 분류", classify.run, (_copy.deepcopy(self.s),),
                               on_done=lambda _: self.library.refresh())
        return dlg.changed

    def send_clip_to_tool(self, metas):
        """클립 하나를 추출/리타겟 도구(MotionBridge 영상찾기)로 바로 보낸다. 후보 고르기는 그쪽에서 한다."""
        from ..core import handoff
        from .mocap_dialog import _send_tool_job
        if len(metas) != 1:
            QMessageBox.information(self, "게임 모션 찾기", "클립을 한 개만 골라 주세요. (추출 도구에서 클립 하나씩 후보를 고르는 방식이에요)")
            return False
        try:
            qp = handoff.build_query(metas[0], self.s, copy_video=bool(self.s.get("tool_copy_video", False)), log=self.log)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "게임 모션 찾기", str(e))
            return False

        def done(mode):
            if mode == "manual":
                QMessageBox.information(self, "게임 모션 찾기", "추출 도구가 연결돼 있지 않아 연동 파일만 만들었어요:\n" + qp)
            else:
                self.flash("🎯 영상찾기 탭으로 보냈어요 — 후보를 고르고 확정하세요", 6000)
        return self.start_job("게임 모션 찾기", _send_tool_job, (self.s, qp), on_done=done)

    def mocap_clips(self, metas):
        """클립에서 3D 동작(모션)을 뽑아 Blender/3ds Max 로 보내는 창."""
        if not metas:
            QMessageBox.information(self, "AI 모캡", "먼저 클립을 만들어 주세요.")
            return False
        if self.busy:
            QMessageBox.information(self, "작업 중", "분석이 끝난 뒤에 열어 주세요.")
            return False
        try:
            from .mocap_dialog import MocapDialog
            dlg = MocapDialog(self, metas)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "AI 모캡", str(e))
            return False
        dlg.exec()
        return dlg.changed

    def can_close(self, parent=None):
        """종료해도 되는지 묻고(녹화/작업 중이면), 되면 작업을 멈춘다. False 면 종료를 막아야 한다."""
        box = parent or self
        if self.collect.rec is not None:
            if QMessageBox.question(box, "종료", "OBS 녹화 중이에요. 녹화를 멈추지 않고 종료할까요?") != QMessageBox.StandardButton.Yes:
                return False
        if self.busy:
            if QMessageBox.question(box, "종료", "작업이 진행 중이에요. 취소하고 종료할까요?") != QMessageBox.StandardButton.Yes:
                return False
            self.job.cancel.set()
            if not self.job.wait(15000):
                self.shutdown()
                os._exit(0)  # 멈추지 않는 작업이 있으면 강제 종료 (QThread 파괴 크래시 방지)
        return True

    def shutdown(self):
        """단축키/연결 창구를 정리한다 (한 번만 불러도 되고 여러 번 불러도 안전)."""
        self.hotkey.stop()
        try:
            self.collect.boss.stop_watch()       # 보스전 감지 스레드 정리
        except Exception:
            pass
        if self.link_server is not None:
            self.link_server.stop()
            self.link_server = None
        if self.embedded and link.LOCAL_TOOL is not None:
            link.LOCAL_TOOL = None

    def closeEvent(self, e):
        if not self.can_close():
            e.ignore()
            return
        self.shutdown()
        e.accept()
