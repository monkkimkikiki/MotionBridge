"""② 라이브러리 탭: 썸네일 그리드, 분류/묶음 필터, 분류 변경, 내보내기."""
import copy
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QAbstractItemView, QTreeWidget, QTreeWidgetItem, QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel,
                               QLineEdit, QListView, QListWidget, QListWidgetItem, QMenu, QMessageBox,
                               QPushButton, QSlider, QStackedWidget, QVBoxLayout, QWidget)

from ..core import classify, library, sessions, titles
from ..core.settings import label_names

from .player import PlayerDialog
from .widgets import AdaptiveRow, muted, open_path, reveal

# 버튼과 우클릭 메뉴가 같은 글을 쓴다 (이름이 달라 헷갈리지 않게)
LBL_FIND = "🎯 게임 모션 찾기"
TIP_FIND = ("이 영상과 같은 동작을 게임 데이터에서 찾아요. 선택한 클립을 '영상찾기'(추출·리타겟 도구)로 보내면 비슷한 후보를 나열하고, "
            "맞는 걸 고르면 애니메이션을 꺼내 내 모델에 리타겟할 수 있어요. (클립 1개 선택)")
LBL_MOCAP = "🦴 AI 모캡"
TIP_MOCAP = "영상 속 사람 동작을 AI로 3D 모션(BVH)으로 뽑아 Blender / 3ds Max 의 리그에 키프레임으로 넣어요 (선택한 클립, 없으면 전체)"
ALL = "__all__"
ROLE = Qt.ItemDataRole.UserRole


class LibraryTab(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.setObjectName("page")
        self.clips, self.shown = [], []
        self.scope = None  # None = 전체, 아니면 (날짜, 세션 키, 분류) — 세션 트리에서 고른 곳
        self._sess = None
        self._icons = {}

        root = QHBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(16)

        # --- 왼쪽 필터
        left = QVBoxLayout()
        left.addWidget(QLabel("녹화 날짜 › 세션 › 분류"))
        self.sess_tree = QTreeWidget()
        self.sess_tree.setHeaderHidden(True)
        self.sess_tree.setToolTip("녹화한 날짜 아래에 녹화를 시작했다가 끝낸 한 번(세션)이 있고, 그 안에 전체·분류별 클립이 있어요.\n우클릭: 날짜/세션 이름 바꾸기, 원본 녹화 위치 열기")
        self.sess_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.sess_tree.customContextMenuRequested.connect(self._tree_menu)
        self.sess_tree.currentItemChanged.connect(lambda *_: self._scope_changed())
        left.addWidget(self.sess_tree, 4)
        left.addWidget(QLabel("분류 (모든 세션)"))
        self.label_list = QListWidget()
        self.label_list.currentItemChanged.connect(lambda *_: self._label_changed())
        left.addWidget(self.label_list, 2)
        left.addWidget(QLabel("비슷한 모션 묶음"))
        self.group_list = QListWidget()
        self.group_list.currentItemChanged.connect(lambda *_: self.apply_filter())
        left.addWidget(self.group_list, 2)
        left.addWidget(QLabel("보기"))
        self.view_combo = QComboBox()
        self.view_combo.addItem("모든 클립", ALL)
        self.view_combo.addItem("긴 동작(연속기)만", "full")
        self.view_combo.addItem("한 타씩만", "hit")
        self.view_combo.addItem("캐릭터 확대만", "crop")
        self.view_combo.currentIndexChanged.connect(lambda *_: self.apply_filter())
        left.addWidget(self.view_combo)
        lw = QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(300)
        root.addWidget(lw)

        # --- 오른쪽
        right = QVBoxLayout()
        self.topbar = AdaptiveRow(reserve=330)  # 좁아지면 버튼은 아이콘만
        top = self.topbar.h
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 클립/원본 영상 이름으로 검색 (쉼표로 나누면 모두 포함)")
        self.search.textChanged.connect(lambda *_: self.apply_filter())
        top.addWidget(self.search, 1)
        top.addWidget(muted("크기"))
        self.size = QSlider(Qt.Orientation.Horizontal)
        self.size.setRange(120, 320)
        self.size.setValue(int(main.s.get("thumb_size", 180)))
        self.size.setFixedWidth(110)
        self.size.valueChanged.connect(self._resize_icons)
        top.addWidget(self.size)
        self.btn_ai = QPushButton("🤖 AI 분류 실행")
        self.btn_ai.setToolTip("새 클립을 분석하고, 분류 항목/묶음 설정을 바꿨다면 전체에 다시 적용해요.")
        self.btn_ai.clicked.connect(self.run_classify)
        btn_export = QPushButton("📦 폴더로 내보내기")
        btn_export.setToolTip("지금 보이는 클립을 '분류/묶음' 폴더 구조로 복사해요.")
        btn_export.clicked.connect(self.export)
        btn_edit = QPushButton("✂ 직접 자르기")
        btn_edit.setToolTip("원본 영상 타임라인을 보면서 클립을 직접 만들어요")
        btn_edit.clicked.connect(lambda: self.main.open_editor())
        top.addWidget(btn_edit)
        btn_find = QPushButton(LBL_FIND)
        btn_find.setToolTip(TIP_FIND)
        btn_find.clicked.connect(lambda: self.find_game_motion(self.selected()))
        top.addWidget(btn_find)
        btn_mocap = QPushButton(LBL_MOCAP)
        btn_mocap.setToolTip(TIP_MOCAP)
        btn_mocap.clicked.connect(lambda: self.mocap(self.selected() or list(self.clips)))
        top.addWidget(btn_mocap)
        btn_open = QPushButton("📂 저장 폴더")
        btn_open.clicked.connect(lambda: open_path(library.game_dir(self.main.s)))
        btn_ref = QPushButton("⟳")
        btn_ref.setToolTip("새로고침")
        btn_ref.clicked.connect(self.refresh)
        for b in (self.btn_ai, btn_export, btn_open, btn_ref):
            top.addWidget(b)
        self.topbar.track(self.btn_ai, btn_export, btn_edit, btn_find, btn_mocap, btn_open)
        self.search.setMinimumWidth(90)
        right.addWidget(self.topbar)

        self.count_label = muted("")
        right.addWidget(self.count_label)

        self.stack = QStackedWidget()
        self.empty = QLabel("아직 클립이 없어요.\n\n[① 수집하기] 탭에서 영상을 넣거나 녹화해 보세요.")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setObjectName("muted")
        self.stack.addWidget(self.empty)

        self.model = QStandardItemModel(self)
        self.view = QListView()
        self.view.setObjectName("grid")
        self.view.setModel(self.model)
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setMovement(QListView.Movement.Static)
        self.view.setUniformItemSizes(True)
        self.view.setWordWrap(True)
        self.view.setSpacing(8)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._menu)
        self.view.doubleClicked.connect(lambda idx: self.play(idx.row()))
        self.stack.addWidget(self.view)
        right.addWidget(self.stack, 1)
        right.addWidget(muted("더블클릭: 재생(슬로모션·프레임 단위)  ·  E: 클립 수정(길이 조절·나누기)  ·  T: 속도·편집(부분 슬로모션, 자르고 붙이기, 여러 클립 이어붙이기)  ·  "
                              "G: 게임 모션 찾기  ·  M: AI 모캡  ·  우클릭: 분류 바꾸기/삭제  ·  Ctrl/Shift로 여러 개 선택"))
        root.addLayout(right, 1)
        self._resize_icons(self.size.value(), save=False)

    # ---------------------------------------------------------------- 데이터
    def refresh(self):
        gdir = library.game_dir(self.main.s)
        self.clips = library.load_clips(gdir)
        self._sess = sessions.Sessions(gdir)
        for m in self.clips:  # 클립이 어느 날짜/세션인지 (원본 녹화 기준)
            m["_session_date"] = sessions.date_of(self._sess.ensure(m).get("start", ""))
        self._sess.save()
        self._fill_tree()
        cur = self.label_list.currentItem().data(ROLE) if self.label_list.currentItem() else ALL
        counts = {}
        for m in self.clips:
            counts[library.final_label(m)] = counts.get(library.final_label(m), 0) + 1
        order = [n for n in label_names(self.main.s) if n in counts]
        order += sorted(n for n in counts if n not in order)
        self.label_list.blockSignals(True)
        self.label_list.clear()
        self._add_item(self.label_list, f"전체  ({len(self.clips)})", ALL)
        for n in order:
            self._add_item(self.label_list, f"{n}  ({counts[n]})", n)
        self._select(self.label_list, cur)
        self.label_list.blockSignals(False)
        self._label_changed()
        self.stack.setCurrentIndex(1 if self.clips else 0)

    # ---------------------------------------------------------------- 날짜 › 세션 › 분류 트리
    def _fill_tree(self):
        cur = self.scope
        order = [n for n in label_names(self.main.s)]
        tree = sessions.build_tree(self.clips, self._sess, order, library.final_label)
        self.sess_tree.blockSignals(True)
        self.sess_tree.clear()
        root = QTreeWidgetItem([f"전체 보기  ({len(self.clips)})"])
        root.setData(0, ROLE, ("scope", None))
        self.sess_tree.addTopLevelItem(root)
        pick = root
        for d in tree:
            di = QTreeWidgetItem([f"📅 {d['date_name']}  ({d['count']})"])
            di.setData(0, ROLE, ("date", d["date"], d["date_name"]))
            root.addChild(di)
            if cur and cur == (d["date"], None, None):
                pick = di
            for sx in d["sessions"]:
                si = QTreeWidgetItem(["🎬 " + sessions.session_title(sx)])
                si.setData(0, ROLE, ("session", d["date"], sx["key"], sx["name"]))
                si.setToolTip(0, "원본 녹화: " + sx["key"])
                di.addChild(si)
                if cur and cur == (d["date"], sx["key"], None):
                    pick = si
                for lab, items in [("전체", sx["clips"])] + list(sx["labels"].items()):
                    li = QTreeWidgetItem([f"{lab}  ({len(items)})"])
                    li.setData(0, ROLE, ("label", d["date"], sx["key"], None if lab == "전체" else lab))
                    si.addChild(li)
                    if cur and cur == (d["date"], sx["key"], None if lab == "전체" else lab):
                        pick = li
        root.setExpanded(True)
        for i in range(root.childCount()):
            root.child(i).setExpanded(True)  # 날짜는 펼치고, 세션 안(분류)은 필요할 때 연다
        self.sess_tree.setCurrentItem(pick)
        self.sess_tree.blockSignals(False)
        self._read_scope()

    def _read_scope(self):
        it = self.sess_tree.currentItem()
        d = it.data(0, ROLE) if it is not None else None
        if not d or d[0] == "scope":
            self.scope = None
        elif d[0] == "date":
            self.scope = (d[1], None, None)
        elif d[0] == "session":
            self.scope = (d[1], d[2], None)
        else:
            self.scope = (d[1], d[2], d[3])

    def _scope_changed(self):
        self._read_scope()
        self.apply_filter()

    def _tree_menu(self, pos):
        it = self.sess_tree.itemAt(pos)
        d = it.data(0, ROLE) if it is not None else None
        if not d or d[0] not in ("date", "session"):
            return
        menu = QMenu(self)
        if d[0] == "date":
            menu.addAction("✏ 날짜 폴더 이름 바꾸기", lambda: self._rename_node(d))
        else:
            menu.addAction("✏ 세션 이름 바꾸기", lambda: self._rename_node(d))
            menu.addAction("🕒 시작/종료 시각 고치기", lambda: self._edit_times(d))
            menu.addAction("원본 녹화 위치 열기", lambda: reveal(d[2]) if Path(d[2]).exists() else QMessageBox.information(self, "원본", "원본 영상이 없어요(옮겼거나 지웠어요)."))
        menu.exec(self.sess_tree.viewport().mapToGlobal(pos))

    def _rename_node(self, d):
        old = d[2] if d[0] == "date" else d[3]
        text, ok = QInputDialog.getText(self, "이름 바꾸기", "표시 이름 (비우면 기본 이름으로 돌아가요. 클립 파일은 그대로예요):", text=old)
        if not ok:
            return
        if d[0] == "date":
            self._sess.rename_date(d[1], text if text.strip() != d[1] else "")
        else:
            self._sess.rename_session(d[2], text)
        self._sess.save()
        self._fill_tree()
        self.apply_filter()

    def _edit_times(self, d):
        rec = self._sess.data["sessions"].get(d[2], {})
        text, ok = QInputDialog.getText(self, "시작/종료 시각", "시작 ~ 종료 (예: 2026-11-09 14:03:22 ~ 2026-11-09 14:25:10):",
                                        text="%s ~ %s" % ((rec.get("start") or "").replace("T", " "), (rec.get("end") or "").replace("T", " ")))
        if not ok or "~" not in text:
            return
        a, b = [t.strip().replace(" ", "T") for t in text.split("~", 1)]
        try:
            from datetime import datetime as _dt
            _dt.fromisoformat(a), _dt.fromisoformat(b)
        except ValueError:
            QMessageBox.warning(self, "시각", "형식이 맞지 않아요. 예: 2026-11-09 14:03:22")
            return
        self._sess.set_times(d[2], a, b)
        self._sess.save()
        self.refresh()

    def _add_item(self, lst, text, data):
        it = QListWidgetItem(text)
        it.setData(ROLE, data)
        lst.addItem(it)

    def _select(self, lst, data):
        for i in range(lst.count()):
            if lst.item(i).data(ROLE) == data:
                lst.setCurrentRow(i)
                return
        lst.setCurrentRow(0)

    def _label_changed(self):
        label = self._cur(self.label_list)
        cur_group = self._cur(self.group_list)
        groups = {}
        for m in self.clips:
            if label in (ALL, library.final_label(m)):
                g = m.get("group") or library.OTHER_GROUP
                groups[g] = groups.get(g, 0) + 1
        self.group_list.blockSignals(True)
        self.group_list.clear()
        self._add_item(self.group_list, "전체", ALL)
        for g in sorted(groups, key=lambda g: (g == library.OTHER_GROUP, -groups[g], g)):
            self._add_item(self.group_list, f"{g}  ({groups[g]})", g)
        self._select(self.group_list, cur_group)
        self.group_list.blockSignals(False)
        self.apply_filter()

    def _cur(self, lst):
        it = lst.currentItem()
        return it.data(ROLE) if it else ALL

    def apply_filter(self):
        label, group, view = self._cur(self.label_list), self._cur(self.group_list), self.view_combo.currentData()
        qs = [t.strip() for t in self.search.text().lower().split(",") if t.strip()]  # 쉼표: 모두 포함
        self.shown = [m for m in self.clips
                      if sessions.matches_scope(m, self.scope, library.final_label)
                      and label in (ALL, library.final_label(m))
                      and group in (ALL, m.get("group") or library.OTHER_GROUP)
                      and view in (ALL, m.get("view"))
                      and all(q in m.get("source_name", "").lower() or q in Path(m["_json"]).stem.lower() or q in titles.clip_title(m).lower() for q in qs)]
        self.model.clear()
        for m in self.shown:
            lab = library.final_label(m)
            if m.get("label_manual"):
                head = f"✔ {lab}"
            elif m.get("ai_conf") is not None:
                head = f"{'🧠 ' if m.get('ai_engine') == 'vlm' else ''}{lab} {m['ai_conf']:.0%}"
            else:
                head = lab
            if m.get("view") == "hit" and m.get("hit_count"):
                head = f"[{m['hit_index']}/{m['hit_count']}타] " + head
            elif m.get("manual"):
                head = "✋ " + head
            it = QStandardItem(self._icon(m), f"{head}\n{m.get('duration', 0):.1f}초 · {titles.clip_title(m)[:34]}")
            it.setToolTip(f"{titles.clip_title(m)}\n파일: {Path(m['_json']).stem}\n원본: {m.get('source_name', '')}\n원본 {m.get('t_range', ['', ''])[0]}초 부근\n묶음: {m.get('group') or '-'}"
                          + (f"\nAI: {m['ai_reason']}" if m.get("ai_reason") else ""))
            self.model.appendRow(it)
        unl = sum(1 for m in self.clips if not m.get("ai_label"))
        txt = f"{len(self.shown)}개 표시 중 (전체 {len(self.clips)}개)"
        if unl:
            txt += f"  ·  AI 분류 전 클립 {unl}개 → [🤖 AI 분류 실행]"
        self.count_label.setText(txt)

    def _icon(self, m):
        p = str(library.file_of(m, "thumb"))
        s = self.size.value()
        key = (p, s)
        if key not in self._icons:
            pm = QPixmap(p)
            if pm.isNull():
                pm = QPixmap(s, s)
                pm.fill(Qt.GlobalColor.darkGray)
            self._icons[key] = QIcon(pm.scaled(s, s, Qt.AspectRatioMode.KeepAspectRatio,
                                               Qt.TransformationMode.SmoothTransformation))
        return self._icons[key]

    def _resize_icons(self, v, save=True):
        self.view.setIconSize(QSize(v, v))
        self.view.setGridSize(QSize(v + 28, v + 58))
        if save:
            self.main.s["thumb_size"] = v
            self.main.save_settings()
            self._icons.clear()
            self.apply_filter()

    # ---------------------------------------------------------------- 동작
    def selected(self):
        return [self.shown[i.row()] for i in self.view.selectionModel().selectedIndexes() if i.row() < len(self.shown)]

    def play(self, row):
        if row < 0 or row >= len(self.shown):
            return
        # 모달이 아니다: 영상을 띄워 둔 채로 라이브러리·다른 탭을 계속 쓸 수 있다 (보면서 분류/정리). 플레이어는 하나만 두고 재사용한다.
        dlg = getattr(self, "_player", None)
        if dlg is not None and dlg.isVisible():
            dlg.clips = list(self.shown)
            dlg.load(row)
            dlg.raise_()
            dlg.activateWindow()
            return
        dlg = self._player = PlayerDialog(list(self.shown), row, self.main)
        dlg.setModal(False)
        dlg.on_change = self.refresh

        def closed(*_a):
            if getattr(self, "_player", None) is dlg:
                self._player = None
            if dlg.changed:
                self.refresh()
            dlg.deleteLater()

        dlg.finished.connect(closed)
        dlg.show()

    def _menu(self, pos):
        idx = self.view.indexAt(pos)
        if not idx.isValid():
            return
        if not self.view.selectionModel().isSelected(idx):
            self.view.setCurrentIndex(idx)
        sel = self.selected()
        menu = QMenu(self)
        menu.addAction("▶ 재생", lambda: self.play(idx.row()))
        if len(sel) == 1:
            menu.addAction("✏ 이름 바꾸기  F2", lambda: self.rename(sel[0]))
        menu.addAction("✏ 클립 수정 (길이 조절·나누기)  E", lambda: self.main.edit_clip(sel[0]))
        menu.addAction("🎞 속도·편집 (부분 슬로모션/배속, 자르고 붙이기)  T" if len(sel) == 1
                       else f"🔗 선택한 클립 {len(sel)}개 이어붙이기·편집  T",
                       lambda: self.retime(sel))
        menu.addAction(f"🔪 타수별로 나누기 (1타·2타·3타…) ({len(sel)}개)  H", lambda: self.split_hits(sel))
        menu.addAction(LBL_FIND + "  G", lambda: self.find_game_motion(sel))
        menu.addAction(LBL_MOCAP + "  M", lambda: self.mocap(sel))
        sub = menu.addMenu(f"분류 바꾸기 ({len(sel)}개)")
        for n in label_names(self.main.s) + [library.UNLABELED]:
            sub.addAction(n, lambda _=False, n=n: self.set_label(sel, n))
        menu.addAction(f"🧠 AI로 다시 판정 ({len(sel)}개)", lambda: self.rejudge(sel))
        menu.addAction("✂ 원본 타임라인에서 다시 자르기",
                       lambda: self.main.open_editor(sel[0].get("source_video"), sel[0].get("start_in_source")))
        menu.addAction("탐색기에서 보기", lambda: reveal(library.file_of(sel[0], "video")))
        menu.addSeparator()
        menu.addAction(f"🗑 삭제 ({len(sel)}개)", lambda: self.delete(sel))
        menu.exec(self.view.viewport().mapToGlobal(pos))

    def retime(self, clips):
        if self.main.retime_clips(list(clips)):
            self.refresh()

    def split_hits(self, clips):
        if self.main.split_hits_clips(list(clips)):
            self.refresh()

    def find_game_motion(self, clips):
        self.main.send_clip_to_tool(list(clips))

    def mocap(self, clips):
        if self.main.mocap_clips(list(clips)):
            self.refresh()

    def set_label(self, clips, name):
        for m in clips:
            library.set_manual_label(m, name)
        self.refresh()

    def rename(self, meta):
        if self.main.busy:
            QMessageBox.information(self, "이름 바꾸기", "다른 작업이 진행 중이에요. 끝난 뒤에 바꿔 주세요.")
            return
        old = Path(meta["_json"]).stem
        text, ok = QInputDialog.getText(self, "이름 바꾸기", "클립 이름 (파일 이름으로 쓰여요. 특수문자는 _ 로 바뀌어요):", text=old)
        if not ok or not text.strip():
            return
        try:
            new_meta, notes = library.rename_clip(self.main.s, meta, text)
        except ValueError as e:
            QMessageBox.warning(self, "이름 바꾸기", str(e))
            return
        self._icons.clear()
        self.refresh()
        new = Path(new_meta["_json"]).stem
        self.main.flash(f"이름을 바꿨어요: {old} → {new}")
        if notes:
            QMessageBox.information(self, "이름 바꾸기", "\n".join(notes))

    def delete(self, clips):
        if QMessageBox.question(self, "삭제", f"클립 {len(clips)}개를 삭제할까요? 되돌릴 수 없어요.") != QMessageBox.StandardButton.Yes:
            return
        failed = sum(1 for m in clips if not library.delete_clip(m))
        self.refresh()
        if failed:
            QMessageBox.warning(self, "삭제", f"{failed}개는 다른 프로그램이 열고 있어서 지우지 못했어요.")

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Delete and self.selected():
            self.delete(self.selected())
        elif e.key() == Qt.Key.Key_F2 and len(self.selected()) == 1:
            self.rename(self.selected()[0])
        elif e.key() == Qt.Key.Key_E and self.selected():
            self.main.edit_clip(self.selected()[0])
        elif e.key() == Qt.Key.Key_T and self.selected():
            self.retime(self.selected())
        elif e.key() == Qt.Key.Key_H and self.selected():
            self.split_hits(self.selected())
        elif e.key() == Qt.Key.Key_G and self.selected():
            self.find_game_motion(self.selected())
        elif e.key() == Qt.Key.Key_M and self.selected():
            self.mocap(self.selected())
        elif e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.view.currentIndex().isValid():
            self.play(self.view.currentIndex().row())
        else:
            super().keyPressEvent(e)

    def run_classify(self):
        if not self.clips:
            QMessageBox.information(self, "AI 분류", "분류할 클립이 없어요.")
            return
        self.main.start_job("AI 분류", classify.run, (copy.deepcopy(self.main.s),), on_done=lambda _: self.refresh())

    def rejudge(self, clips):
        force = {m["_json"] for m in clips}
        self.main.start_job("AI 다시 판정", classify.run, (copy.deepcopy(self.main.s),),
                            kwargs={"force": force}, on_done=lambda _: self.refresh())

    def export(self):
        if not self.shown:
            QMessageBox.information(self, "내보내기", "내보낼 클립이 없어요.")
            return
        d = QFileDialog.getExistingDirectory(self, "내보낼 폴더 선택")
        if not d:
            return
        n = library.export(self.shown, d, log=self.main.log, sess=self._sess)
        if QMessageBox.question(self, "내보내기 완료", f"{n}개 클립을 내보냈어요.\n폴더를 열까요?") == QMessageBox.StandardButton.Yes:
            open_path(d)

    def set_busy(self, busy):
        self.btn_ai.setEnabled(not busy)
