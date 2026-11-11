"""왼쪽 '모션 목록' — 리타겟 중인 애니메이션(클립)을 한눈에 보고, 눌러서 바로 오가며 작업하고, 다른 모션에서 했던 세팅을 가져왔다 뺄 수 있다.

설정·본별 보정·레이어·IK 구간은 모션마다 따로 저장되므로 하나를 고치다가 다른 모션으로 넘어가도 작업이 그대로 남는다 (core/pipeline.py 의 클립별 설정)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget

from .widgets import CollapsibleBox

TITLE = "모션 목록 — 뷰포트에 연 애니메이션"


class ClipPanel(QWidget):
    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self._busy = False
        self._checked: set = set()        # 체크한 모션 (애니메이션 객체의 id — 번호가 밀려도 따라간다)
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        head = QLabel("🎞 " + TITLE)
        head.setStyleSheet("font-weight:700")
        head.setWordWrap(True)
        v.addWidget(head)
        self.lst = QListWidget()
        self.lst.setToolTip("뷰포트에 연 모션이 자동으로 여기에 올라옵니다. 눌러서 그 모션으로 넘어갑니다. 앞의 체크박스를 켠 모션은 내보내기 때 한꺼번에 내보내집니다.\n"
                            "눌러서 그 모션으로 넘어갑니다. 설정·본 보정·레이어·IK 구간은 모션마다 따로 저장돼서, 다른 모션을 보고 돌아와도 하던 작업이 그대로예요.\n"
                            "✎ = 손댄 모션 (본 보정·레이어·IK 구간·프레임 편집이 있음)")
        self.lst.currentRowChanged.connect(self._row)
        self.lst.itemChanged.connect(self._item_changed)
        v.addWidget(self.lst, 1)
        r1 = QHBoxLayout()
        self.b_all = QPushButton("☑ 전체 체크")
        self.b_all.setToolTip("목록의 모든 모션을 체크합니다 (내보내기 때 전부 내보내집니다)")
        self.b_all.clicked.connect(lambda _c=False: self.set_all_checked(True))
        self.b_none = QPushButton("☐ 전체 해제")
        self.b_none.setToolTip("체크를 모두 끕니다 (체크가 하나도 없으면 내보내기는 지금 보는 모션만 내보냅니다)")
        self.b_none.clicked.connect(lambda _c=False: self.set_all_checked(False))
        r1.addWidget(self.b_all, 1)
        r1.addWidget(self.b_none, 1)
        v.addLayout(r1)
        r0 = QHBoxLayout()
        self.b_rm = QPushButton("－ 선택한 모션 지우기")
        self.b_rm.setToolTip("지금 고른 모션을 목록에서 지웁니다 (그 모션의 설정·보정·레이어·IK 구간도 함께 사라져요. 파일은 지워지지 않습니다)")
        self.b_rm.clicked.connect(lambda _c=False: self.remove_current())
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
                it.setToolTip("%s — %d프레임, %.0ffps\n체크하면 내보내기 때 함께 내보냅니다" % (a.name, a.n_frames, a.fps))
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Checked if id(a) in self._checked else Qt.CheckState.Unchecked)
                self.lst.addItem(it)
            if anims:
                self.lst.setCurrentRow(min(max(pr.clip, 0), len(anims) - 1))
            alive = {id(a) for a in anims}
            self._checked &= alive                       # 지워졌거나 새로 연 소스의 옛 체크는 버린다
            self._set_count(len(anims))
            cur = self.src.currentData()
            entries = [("%d. %s%s" % (i + 1, a.name, "" if pr.settings_of(i) is not None else "  (아직 손대지 않음)"), i)
                       for i, a in enumerate(anims) if i != pr.clip]
            have = [(self.src.itemText(k), self.src.itemData(k)) for k in range(self.src.count())]
            if have != entries:
                # 내용이 같으면 건드리지 않는다: 사용자가 상자를 눌러 목록을 펼친 사이에 재계산이 끝나 clear() 하면 펼친 목록이 비어 버렸다
                self.src.clear()
                for text, i in entries:
                    self.src.addItem(text, i)
                j = self.src.findData(cur) if cur is not None else -1
                if j >= 0:
                    self.src.setCurrentIndex(j)
            self.b_rm.setEnabled(bool(anims))
            self.b_all.setEnabled(bool(anims))
            self.b_none.setEnabled(bool(self._checked))
            self.b_in.setEnabled(len(anims) > 1 and self.src.count() > 0)
            h = pr.import_history() if anims else []
            self.b_out.setEnabled(bool(h))
            self.hist.setText("") if not h else self.hist.setText("가져온 기록 (마지막이 위):\n" + "\n".join("· " + x for x in reversed(h)))
        finally:
            self._busy = False
        self._notify()

    # ---- 체크 (내보내기 대상)
    def _set_count(self, n: int) -> None:
        k = len(self._checked)
        self.count.setText(("모션 %d개 · 체크 %d개" % (n, k)) if n else "소스 모션을 열면 여기에 자동으로 올라옵니다")

    def _notify(self) -> None:
        f = getattr(self.app, "_update_clip_export_hint", None)
        if f is not None:
            f()

    def _item_changed(self, it=None) -> None:
        if self._busy:
            return
        anims = self.app.pr.src.animations if self.app.pr.src is not None else []
        on = set()
        for i in range(min(self.lst.count(), len(anims))):
            if self.lst.item(i).checkState() == Qt.CheckState.Checked:
                on.add(id(anims[i]))
        self._checked = on
        self._set_count(len(anims))
        self.b_none.setEnabled(bool(on))
        self._notify()

    def checked_clips(self) -> list:
        """체크한 모션의 클립 번호들 (목록 순서)."""
        anims = self.app.pr.src.animations if self.app.pr.src is not None else []
        return [i for i, a in enumerate(anims) if id(a) in self._checked]

    def set_all_checked(self, on: bool) -> None:
        anims = self.app.pr.src.animations if self.app.pr.src is not None else []
        self._checked = {id(a) for a in anims} if on else set()
        self.refresh()

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

    # ---- 목록 편집: 뷰포트로 연 모션은 자동으로 더해진다 / 지우기
    def add_paths(self, paths, select_first: bool = False) -> int:
        """파일들을 목록에 더한다 (뷰포트로 열 때·추출 직후에 쓴다). 돌려주는 값: 새로 더해진 모션 수."""
        a = self.app
        try:
            added, skipped = a.pr.add_motions(list(paths))
        except Exception as e:  # noqa: BLE001
            a.statusBar().showMessage("모션 추가 실패: %s" % e)
            return 0
        self.added_ui(added, skipped, select_first)
        return len(added)

    def added_ui(self, added, skipped, select_first: bool = False, select_existing=None) -> None:
        """add_motions 가 끝난 뒤 화면을 맞춘다 (일하는 스레드에서 add_motions 만 하고 이쪽은 화면 스레드에서 부른다).
        select_existing: 새로 더해진 것이 없고 이미 목록에 있던 모션으로 가야 할 때 그 번호."""
        a = self.app
        if a.pr.src is not None and added and added[0] == 0:
            a._after_load("source")                     # 목록이 비어 있었으면 첫 모션이 소스가 됨
        else:
            a._refill_clip_combo()
        if select_first and added and a.pr.src is not None and len(a.pr.src.animations) > 1:
            a.clip_combo.setCurrentIndex(added[0])
        elif not added and select_existing is not None and a.pr.src is not None:
            a.clip_combo.setCurrentIndex(int(select_existing))
        self.refresh()
        if added:
            msg = "모션 %d개를 목록에 더했습니다" % len(added)
        elif select_existing is not None:
            msg = "이미 모션 목록에 있는 모션이라 그 모션으로 이동했습니다"
        else:
            msg = "더해진 모션이 없습니다"
        if skipped and not (select_existing is not None and not added):
            msg += " · 건너뜀 %d개: %s" % (len(skipped), "; ".join(skipped[:2]))
        a._last_status = msg
        a.statusBar().showMessage(msg)

    def remove_current(self) -> None:
        a = self.app
        pr = a.pr
        if pr.src is None:
            return
        ci = self.lst.currentRow()
        if ci < 0:
            return
        nm = pr.src.animations[ci].name
        if pr.clip_touched(ci):
            r = QMessageBox.question(a, "모션 지우기", "'%s' 에는 손댄 작업(본 보정·레이어·IK 구간·프레임 편집)이 있습니다.\n목록에서 지우면 그 작업도 사라집니다. 지울까요?" % nm)
            if r != QMessageBox.StandardButton.Yes:
                return
        if len(pr.src.animations) <= 1:                  # 마지막 하나를 지우면 소스 모션을 빼는 것과 같다
            a.unload_source()
            self.refresh()
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
        a.statusBar().showMessage("목록에서 지웠습니다: " + nm)

