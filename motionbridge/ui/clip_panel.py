"""왼쪽 '모션 목록' — 리타겟 중인 애니메이션(클립)을 한눈에 보고, 눌러서 바로 오가며 작업하고, 다른 모션에서 했던 세팅을 가져왔다 뺄 수 있다.

설정·본별 보정·레이어·IK 구간은 모션마다 따로 저장되므로 하나를 고치다가 다른 모션으로 넘어가도 작업이 그대로 남는다 (core/pipeline.py 의 클립별 설정)."""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget

from ..core.loader import FILE_FILTER
from .widgets import CollapsibleBox

TITLE = "모션 목록 — 리타겟 중인 애니메이션"


class ClipPanel(QWidget):
    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self._busy = False
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        head = QLabel("🎞 " + TITLE)
        head.setStyleSheet("font-weight:700")
        head.setWordWrap(True)
        v.addWidget(head)
        self.lst = QListWidget()
        self.lst.setToolTip("눌러서 그 모션으로 넘어갑니다. 설정·본 보정·레이어·IK 구간은 모션마다 따로 저장돼서, 다른 모션을 보고 돌아와도 하던 작업이 그대로예요.\n"
                            "✎ = 손댄 모션 (본 보정·레이어·IK 구간·프레임 편집이 있음)")
        self.lst.currentRowChanged.connect(self._row)
        v.addWidget(self.lst, 1)
        r0 = QHBoxLayout()
        self.b_add = QPushButton("＋ 모션 추가…")
        self.b_add.setToolTip("모션 파일(PSA·FBX·GLB·BVH)을 골라 목록에 더합니다. 같은 캐릭터 뼈대의 것만 들어갑니다.\n추출 탭에서 애니메이션을 추출하면 자동으로 여기에 더해져요.")
        self.b_add.clicked.connect(lambda _c=False: self.add_files())
        self.b_rm = QPushButton("－ 선택한 모션 빼기")
        self.b_rm.setToolTip("지금 고른 모션을 목록에서 뺍니다 (그 모션의 설정·보정·레이어·IK 구간도 함께 사라져요. 파일은 지워지지 않습니다)")
        self.b_rm.clicked.connect(lambda _c=False: self.remove_current())
        r0.addWidget(self.b_add, 1)
        r0.addWidget(self.b_rm, 1)
        v.addLayout(r0)
        self.count = QLabel("")
        self.count.setStyleSheet("color:#8aa")
        v.addWidget(self.count)

        self.box = CollapsibleBox("⬇ 다른 모션의 세팅 가져오기")
        bl = QVBoxLayout(self.box)
        note = QLabel("앞서 리타겟한 다른 모션에서 했던 세팅을 지금 모션으로 가져옵니다. 마음에 안 들면 [빼기]로 가져오기 전으로 돌아가요.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#8aa")
        bl.addWidget(note)
        bl.addWidget(QLabel("어느 모션에서"))
        self.src = QComboBox()
        bl.addWidget(self.src)
        self.checks = {}
        from ..core.pipeline import Project
        tips = {"settings": "스케일, 발 IK, 골반 낮추기, 다듬기(떨림 줄이기·반전), 무릎 보정 등 리타겟 조절 탭의 값들",
                "offsets": "🦴 본 편집으로 고친 본별 회전·위치·크기 보정",
                "layers": "보정 레이어(키)를 통째로 복사합니다. 프레임 번호 그대로라 길이가 다른 모션이면 어긋날 수 있어요",
                "ik": "'IK 구간 직접 지정'으로 정한 켜기/끄기 구간. 프레임 번호 그대로 복사됩니다"}
        for key, text in Project.IMPORT_PARTS:
            cb = QCheckBox(text)
            cb.setChecked(key in ("settings", "offsets"))
            cb.setToolTip(tips[key])
            bl.addWidget(cb)
            self.checks[key] = cb
        r = QHBoxLayout()
        self.b_in = QPushButton("⬇ 가져오기")
        self.b_in.setToolTip("고른 항목을 지금 모션에 덮어씁니다 (여러 번 가져와도 되고, 빼기는 마지막 것부터 하나씩 되돌립니다)")
        self.b_in.clicked.connect(lambda _c=False: self.do_import())
        self.b_out = QPushButton("↩ 빼기")
        self.b_out.setToolTip("마지막으로 가져온 것을 빼고 가져오기 전 상태로 되돌립니다")
        self.b_out.clicked.connect(lambda _c=False: self.do_undo())
        r.addWidget(self.b_in, 1)
        r.addWidget(self.b_out, 1)
        bl.addLayout(r)
        self.hist = QLabel("")
        self.hist.setWordWrap(True)
        self.hist.setStyleSheet("color:#ffdc50")
        bl.addWidget(self.hist)
        v.addWidget(self.box)
        self.refresh()

    # ---- 표시
    def refresh(self) -> None:
        pr = self.app.pr
        self._busy = True
        try:
            anims = pr.src.animations if pr.src is not None else []
            self.lst.clear()
            for i, a in enumerate(anims):
                mark = "✎ " if pr.clip_touched(i) else "   "
                it = QListWidgetItem("%s%d. %s  (%d프레임)" % (mark, i + 1, a.name, a.n_frames))
                it.setToolTip("%s — %d프레임, %.0ffps" % (a.name, a.n_frames, a.fps))
                self.lst.addItem(it)
            if anims:
                self.lst.setCurrentRow(min(max(pr.clip, 0), len(anims) - 1))
            self.count.setText("모션 %d개" % len(anims) if anims else "소스 모션을 열면 여기에 목록이 나옵니다")
            cur = self.src.currentData()
            self.src.clear()
            for i, a in enumerate(anims):
                if i != pr.clip:
                    self.src.addItem("%d. %s%s" % (i + 1, a.name, "" if pr.settings_of(i) is not None else "  (아직 손대지 않음)"), i)
            j = self.src.findData(cur) if cur is not None else -1
            if j >= 0:
                self.src.setCurrentIndex(j)
            can = len(anims) > 1
            self.b_rm.setEnabled(can)
            self.b_in.setEnabled(can and self.src.count() > 0)
            h = pr.import_history() if anims else []
            self.b_out.setEnabled(bool(h))
            self.hist.setText("") if not h else self.hist.setText("가져온 기록 (마지막이 위):\n" + "\n".join("· " + x for x in reversed(h)))
        finally:
            self._busy = False

    def select(self, i: int) -> None:
        """다른 곳(콤보박스·되돌리기)에서 클립이 바뀌었을 때 목록 줄만 맞춘다."""
        self._busy = True
        try:
            if 0 <= i < self.lst.count():
                self.lst.setCurrentRow(i)
        finally:
            self._busy = False

    def _row(self, row: int) -> None:
        if self._busy or row < 0 or self.app.pr.src is None or row == self.app.pr.clip:
            return
        self.app.clip_combo.setCurrentIndex(row)       # 기존 클립 전환 경로(_clip_changed)를 그대로 쓴다

    # ---- 가져오기 / 빼기
    def do_import(self) -> None:
        pr = self.app.pr
        ci = self.src.currentData()
        parts = [k for k, cb in self.checks.items() if cb.isChecked()]
        if ci is None or pr.src is None:
            self.app.statusBar().showMessage("가져올 다른 모션을 고르세요")
            return
        try:
            note = pr.import_clip_state(int(ci), parts)
        except ValueError as e:
            self.app.statusBar().showMessage(str(e))
            return
        self._applied(note)

    def do_undo(self) -> None:
        note = self.app.pr.undo_import()
        if note is None:
            self.app.statusBar().showMessage("뺄 것이 없습니다")
            return
        self._applied("뺐습니다 — " + note)

    def _applied(self, msg: str) -> None:
        a = self.app
        a._syncing = True
        a._sync_settings_ui()
        a._syncing = False
        a.recompute_timer.stop()
        a.recompute()
        if getattr(a, "layer_panel", None) is not None:
            a.layer_panel.refresh()
        a.history.commit(a._edit_state())
        self.refresh()
        a.statusBar().showMessage(msg)

    # ---- 목록 편집: 모션 더하기 / 빼기
    def add_files(self) -> None:
        a = self.app
        paths, _ = QFileDialog.getOpenFileNames(a, "모션 추가", a._open_start_dir("anim"), FILE_FILTER)
        if paths:
            self.add_paths(list(paths), select_first=True)

    def add_paths(self, paths, select_first: bool = False) -> int:
        """파일들을 목록에 더한다 (추출 직후에도 쓴다). 돌려주는 값: 새로 더해진 모션 수."""
        a = self.app
        try:
            added, skipped = a.pr.add_motions(list(paths))
        except Exception as e:  # noqa: BLE001
            a.statusBar().showMessage("모션 추가 실패: %s" % e)
            return 0
        if a.pr.src is not None and added and added[0] == 0:
            a._after_load("source")                     # 목록이 비어 있었으면 첫 모션이 소스가 됨
        else:
            a._refill_clip_combo()
        if select_first and added and a.pr.src is not None and len(a.pr.src.animations) > 1:
            a.clip_combo.setCurrentIndex(added[0])
        self.refresh()
        msg = "모션 %d개를 목록에 더했습니다" % len(added) if added else "더해진 모션이 없습니다"
        if skipped:
            msg += " · 건너뜀 %d개: %s" % (len(skipped), "; ".join(skipped[:2]))
        a.statusBar().showMessage(msg)
        return len(added)

    def remove_current(self) -> None:
        a = self.app
        pr = a.pr
        if pr.src is None or len(pr.src.animations) <= 1:
            a.statusBar().showMessage("마지막 모션은 목록에서 뺄 수 없습니다 (소스 빼기를 쓰세요)")
            return
        ci = self.lst.currentRow()
        if ci < 0:
            return
        nm = pr.src.animations[ci].name
        if pr.clip_touched(ci):
            r = QMessageBox.question(a, "모션 빼기", "'%s' 에는 손댄 작업(본 보정·레이어·IK 구간·프레임 편집)이 있습니다.\n목록에서 빼면 그 작업도 사라집니다. 뺄까요?" % nm)
            if r != QMessageBox.StandardButton.Yes:
                return
        try:
            pr.remove_clip(ci)
        except ValueError as e:
            a.statusBar().showMessage(str(e))
            return
        a._refill_clip_combo()
        a._syncing = True
        a._sync_settings_ui()
        a._syncing = False
        a.recompute(reframe=True)
        a.history.reset(a._edit_state())
        self.refresh()
        a.statusBar().showMessage("목록에서 뺐습니다: " + nm)

