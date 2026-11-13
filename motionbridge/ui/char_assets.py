"""에셋 찾기 (캐릭터·스켈레톤 보기): 캐릭터 > 메쉬 / 애니메이션(재생 가능한 것만).

기존 '폴더 트리' 검색은 그대로 두고, 이 패널이 따로 한다. 이름이나 폴더가 아니라 파일 안 데이터(스켈레톤 연결, 프레임, 키 수)로 묶는다 (core/skelgraph).
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import List, Optional, Set

import json

from PySide6.QtCore import QMimeData, Qt
from .uikit import Resizable
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from ..core import bridge, extractor, skelgraph, textmatch
from ..core.skelgraph import Group, Rec
from .viewer import ASSET_MIME

ROLE = Qt.ItemDataRole.UserRole
# 플레이어블 캐릭터 표시는 인식이 정확해질 때까지 화면에서 꺼 둔다 (코드는 그대로 두고 이 스위치만 켜면 된다)
PLAYER_FEATURE = False
PLAYER_MARK = "🎮 [플레이어블]  "      # 플레이어블 캐릭터(직접 표시 / 게임 데이터로 확인) 이름 앞에 붙는 표시 (목록 맨 위에 모아 보여 준다)
PLAYER_GUESS = "🎮? [플레이어블 추정]  "  # 이름·폴더만 보고 추정한 것 (게임 데이터로 확인하지 못함)
MAX_FIRST = 4000        # 한 번의 검색에서 파일 안을 열어 볼 최대 개수 (넘으면 알리고 앞부분만)
ANIM_ALL = r"(?i)(anim|/A[SM]?_[^/]*$)"   # 애니메이션 후보 (폴더 이름에 anim 이 없어도 A_/AS_/AM_ 이름은 포함)
RIG_ALL = r"(?i)(anim|/A[SM]?_[^/]*$|/SKM?_[^/]*$|skeleton|skeletal|/SKEL_|character|/char[a-z]*/|mesh)"   # 자동 찾기: 애니메이션 + 메쉬/스켈레톤 후보


def _anim_text(r: Rec) -> str:
    bits = []
    if r.kind == "montage":
        bits.append("몽타주 → 시퀀스 %d개" % len(r.sequences))
    else:
        if r.frames > 0:
            bits.append("%d프레임" % r.frames)
        if r.length > 0:
            bits.append("%.2f초" % r.length)
        if r.tracks > 0:
            bits.append("%d트랙" % r.tracks)
        elif r.compressed:
            bits.append("압축 데이터")
    return "%s  · %s" % (r.name or os.path.basename(r.path), " · ".join(bits)) if bits else (r.name or os.path.basename(r.path))


class _DragTree(QTreeWidget):
    """고른 메쉬/애니메이션을 뷰포트로 끌어다 놓을 수 있는 트리 (종류와 게임 안 경로를 실어 보낸다)."""

    def mimeData(self, items):  # noqa: N802
        md = QMimeData()
        kind, paths = "", []
        for it in items:
            stack = [it]
            while stack:
                x = stack.pop(0)
                d = x.data(0, ROLE)
                if d and d[0] != "group" and (not kind or d[0] == kind) and d[1] not in paths:
                    kind = kind or d[0]
                    paths.append(d[1])
                for i in range(x.childCount()):
                    stack.append(x.child(i))
        md.setData(ASSET_MIME, json.dumps({"kind": kind, "paths": paths}).encode("utf-8"))
        return md


class CharAssetPanel(QWidget):
    def __init__(self, win) -> None:
        super().__init__()
        self.win = win
        self.index: Optional[skelgraph.SkelIndex] = None
        self._game = ""
        self.groups: List[Group] = []
        self._build()

    # ------------------------------------------------------------------ 화면
    def _build(self) -> None:
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(QLabel("<b>캐릭터·스켈레톤으로 찾기</b> — 파일 안을 실제로 읽어서, 같은 스켈레톤을 쓰는 메쉬와 '재생 가능한' 애니메이션만 캐릭터별로 묶습니다 "
                           "(폴더 위치·이름은 보지 않습니다).<br>"
                           "<span style='color:#7ff0a0'>👇 먼저 아래 초록 버튼을 한 번 눌러야 목록이 생깁니다 — 처음 한 번만 오래 걸립니다.</span>"))
        self.btn_auto = QPushButton("🔍  자동으로 캐릭터 찾기 (게임 전체) — 먼저 이걸 누르세요")
        self.btn_auto.setMinimumHeight(40)
        self.btn_auto.setStyleSheet(
            "QPushButton{font-size:13px;font-weight:700;border:2px solid #2f7d4a;border-radius:8px;background:#245c3a;color:#dff5e6}"
            "QPushButton:hover{background:#2f7d4a;color:#ffffff}")
        self.btn_auto.setToolTip("검색어 없이, 게임의 메쉬·스켈레톤·애니메이션 파일 안을 읽어서 캐릭터별로 자동으로 묶습니다. 처음 한 번만 오래 걸리고, 이어서 하거나 취소해도 읽은 만큼은 남습니다.")
        self.btn_auto.clicked.connect(lambda *_a: self.index_all())
        v.addWidget(self.btn_auto)
        h = QHBoxLayout()
        saved_q = str(self.win.cfg.get("char_query", "") or "")
        if not saved_q.strip().strip("\\/").strip():          # '\' 나 '/' 만 남은 예전 검색어는 버린다 (칸이 계속 '\' 로 채워져 있던 문제)
            saved_q = ""
            self.win.cfg["char_query"] = ""
        self.query = QLineEdit(saved_q)
        self.query.setClearButtonEnabled(True)                 # 칸 안의 ✕ 로 한 번에 지움
        self.query.setPlaceholderText("찾은 캐릭터 중에서 고르기 (예: swordman — 쉼표로 나누면 모두 포함). 비우면 전체")
        self.query.textChanged.connect(self._filter)       # 입력하는 대로 이미 찾은 목록을 바로 걸러 보여 준다
        self.query.returnPressed.connect(self.search)
        h.addWidget(self.query, 1)
        self.btn_search = QPushButton("이 이름으로 더 찾기")
        self.btn_search.setToolTip("목록에 없으면: 이 이름에 맞는 에셋의 파일 안을 추가로 읽어 목록에 더합니다 (한 번 읽은 파일은 기억합니다)")
        self.btn_search.clicked.connect(lambda *_a: self.search())
        h.addWidget(self.btn_search)
        v.addLayout(h)

        self.tree = _DragTree()
        self.tree.setDragEnabled(True)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumHeight(110)
        self.tree.setToolTip("메쉬/애니메이션을 고른 뒤 오른쪽 뷰포트로 끌어다 놓으면 바로 열립니다")
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        v.addWidget(Resizable(self.tree, "ex_char_tree_h", 110))
        self.status = QLabel("[자동으로 캐릭터 찾기]를 누르면 게임에서 캐릭터를 알아서 찾아 정리합니다. 게임 폴더와 도우미는 위 1)·2)에서 설정합니다.")
        self._shown_once = False
        self.status.setWordWrap(True)
        v.addWidget(self.status)

        h = QHBoxLayout()
        self.btn_prev = QPushButton("선택한 애니메이션 미리보기")
        self.btn_prev.setToolTip("애니메이션을 추출해서 바로 뷰포트에 엽니다 (더블클릭, 또는 뷰포트로 끌어다 놓기도 같음)")
        self.btn_prev.clicked.connect(lambda *_a: self.preview())
        h.addWidget(self.btn_prev)
        self.btn_mesh = QPushButton("선택한 메쉬를 타깃으로 열기")
        self.btn_mesh.setToolTip("메쉬를 GLB 로 뽑아 타깃 모델 자리에 엽니다")
        self.btn_mesh.clicked.connect(lambda *_a: self.open_mesh())
        h.addWidget(self.btn_mesh)
        self.btn_send = QPushButton("선택 항목을 추출 목록으로")
        self.btn_send.setToolTip("고른 애니메이션/메쉬를 위 폴더 트리 목록에 올려 평소처럼 추출합니다")
        self.btn_send.clicked.connect(lambda *_a: self.send_to_list())
        h.addWidget(self.btn_send)
        v.addLayout(h)
        h = QHBoxLayout()           # 플레이어 버튼 · 모으기 · 검사 기록 지우기를 한 줄에
        self.btn_find_player = self.btn_player = None
        if PLAYER_FEATURE:
            self._build_player_buttons(h)
        self.btn_lib = QPushButton("📚 같은 종류 모션 모으기")
        self.btn_lib.setToolTip("고른 애니메이션(폴더를 고르면 안의 전부)을 추출해서 '출력 폴더/Library/종류/동작/' 으로 모읍니다.\n"
                                "attack 은 attack 끼리, run 은 run 끼리 — 여러 캐릭터를 골라 두면 같은 종류가 한 폴더에 쌓여 모션 라이브러리가 됩니다.")
        self.btn_lib.clicked.connect(lambda *_a: self.collect_library())
        h.addWidget(self.btn_lib)
        self.btn_clear = QPushButton("검사 기록 지우기")
        self.btn_clear.setToolTip("'자동으로 캐릭터 찾기'로 읽어 둔 기록을 지웁니다 (게임 파일은 그대로, 다시 찾으면 처음부터 읽습니다).")
        self.btn_clear.clicked.connect(lambda *_a: self.clear_index())
        h.addWidget(self.btn_clear)
        h.addStretch(1)
        v.addLayout(h)

    def _build_player_buttons(self, h) -> None:
        self.btn_find_player = QPushButton("🎮  플레이어 자동 확인")
        self.btn_find_player.setToolTip("게임 데이터에서 플레이어가 조종하는 캐릭터를 직접 찾습니다: 게임 설정의 기본 게임모드 → 기본 폰 블루프린트가 쓰는 메시 → 그 스켈레톤. "
                                        "이름이 아니라 데이터로 확인하며, 확인된 캐릭터는 🎮 로 표시되어 맨 위로 올라갑니다.")
        self.btn_find_player.clicked.connect(lambda *_a: self.find_players())
        h.addWidget(self.btn_find_player)
        self.btn_player = QPushButton("🎮  플레이어블 표시/해제")
        self.btn_player.setToolTip("고른 캐릭터를 '플레이어블 캐릭터' 로 직접 표시하거나(맨 위로 올라갑니다) 해제합니다. 이름으로 잘못 짐작한 것을 바로잡을 때 쓰세요. 한 번 정하면 기억합니다.")
        self.btn_player.clicked.connect(lambda *_a: self.toggle_player())
        h.addWidget(self.btn_player)

    def showEvent(self, e) -> None:  # noqa: N802
        super().showEvent(e)
        if not self._shown_once:         # 전에 찾아 둔 기록이 있으면 처음 열 때 바로 보여 준다
            self._shown_once = True
            try:
                if self.win.ex_game.text().strip() and self._index().recs:
                    self._show(self._index(), self.query.text().strip(), None, extra=" (전에 찾아 둔 기록)")
            except Exception:  # noqa: BLE001
                pass

    def _filter(self, *_a) -> None:
        """입력하는 대로 이미 찾은 목록을 걸러 보여 준다 (파일을 새로 읽지 않는다)."""
        if not self.win.ex_game.text().strip():
            return
        idx = self._index()
        if idx.recs:
            self._show(idx, self.query.text().strip(), None)

    # ------------------------------------------------------------------ 색인
    def _index(self) -> skelgraph.SkelIndex:
        game = self.win.ex_game.text().strip()
        if self.index is None or game != self._game:
            self._game = game
            key = hashlib.sha1(game.lower().encode("utf-8")).hexdigest()[:10]
            self.index = skelgraph.SkelIndex.load(os.path.join(bridge.CONFIG_DIR, "skelidx_%s.json" % key))
        return self.index

    def clear_index(self) -> None:
        idx = self._index()
        idx.recs.clear()
        idx.unreadable.clear()
        idx.seen.clear()
        idx.rechecked.clear()
        idx.proofs.clear()
        idx.save()
        self.groups = []
        self.tree.clear()
        self.status.setText("검사 기록을 지웠습니다.")

    # ------------------------------------------------------------------ 검색
    def search(self) -> None:
        a = self.win._extract_args()
        if a is None:
            return
        q = self.query.text().strip()
        if not q.strip("\\/ "):
            q = ""
        self.win.cfg["char_query"] = q
        idx = self._index()
        pattern = textmatch.and_regex(q) if q else ANIM_ALL
        if not pattern.startswith("(?i)"):
            pattern = "(?i)" + pattern  # 대소문자 구분 없이
        if not q:  # 비우면 새로 검사하지 않고 이미 찾은 것 전체를 보여 준다
            self._show(idx, q, set())
            return

        def job(w):
            paths = extractor.list_assets(*a, pattern, 20000, None)
            cand = [p for p in paths if skelgraph.worth_inspecting(p)]
            todo = idx.pending(cand)[:MAX_FIRST]
            skipped = len(idx.pending(cand)) - len(todo)
            self._run_inspect(w, a, idx, todo)
            return set(cand), len(cand), len(todo), skipped

        def done(res):
            touch, n_cand, n_new, skipped = res
            self._show(idx, q, touch, extra=" (이번에 새로 읽은 파일 %d개%s)" % (n_new, ", 너무 많아 %d개는 건너뜀 — 검색어를 좁혀 주세요" % skipped if skipped else ""))

        self.win.run_async(job, done, "파일 안을 읽는 중 (스켈레톤 연결 확인)", cancellable=True)

    def index_all(self) -> None:
        a = self.win._extract_args()
        if a is None:
            return
        if QMessageBox.question(self, "자동으로 캐릭터 찾기", "게임의 메쉬·스켈레톤·애니메이션 파일을 열어 어느 스켈레톤을 쓰는지 기록하고, 캐릭터별로 자동으로 묶습니다.\n수천 개면 수십 분 걸릴 수 있고, 중간에 취소해도 읽은 만큼은 저장됩니다.\n\n진행할까요?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        idx = self._index()

        def job(w):
            paths = [p for p in extractor.list_assets(*a, RIG_ALL, 200000, None) if skelgraph.worth_inspecting(p)]
            todo = idx.pending(paths)
            self._run_inspect(w, a, idx, todo)
            return len(todo)

        def done(n):
            self._show(idx, self.query.text().strip(), None, extra=" (새로 읽은 파일 %d개)" % n)
            if PLAYER_FEATURE:
                self.find_players(quiet=True)     # 캐릭터 목록이 만들어졌으면 이어서 게임 데이터로 플레이어도 확인한다

        self.win.run_async(job, done, "캐릭터 자동 찾는 중 (파일 안 읽기)", cancellable=True)

    def _run_inspect(self, w, a, idx: skelgraph.SkelIndex, todo: List[str]) -> None:
        if not todo:
            return
        total = len(todo)

        def prog(e):
            if e.get("type") == "progress":
                w.progress.emit(int(e.get("done", 0)), total, "읽는 중 %d / %d" % (e.get("done", 0), total))

        # 조금씩 읽고 그때마다 저장해서, 취소하거나 멈춰도 읽은 만큼은 남는다
        step = 200
        for i in range(0, total, step):
            if w.cancel.is_set():
                break
            recs, seen = extractor.inspect_assets(*a, todo[i:i + step], None, chunk=step, cancel=w.cancel)
            idx.begin(todo[i:i + step])          # 옛 기록이 있던 파일은 새로 읽은 것으로 바꾼다
            for r in recs:
                idx.add(r)
            idx.seen.update(seen)
            idx.save()
            prog({"type": "progress", "done": min(i + step, total)})

    # ------------------------------------------------------------------ 보이기
    def _show(self, idx: skelgraph.SkelIndex, q: str, touch: Optional[Set[str]], extra: str = "") -> None:
        groups = skelgraph.build_groups(idx.all(), q, touch, idx.marks, idx.proofs, players=PLAYER_FEATURE)
        self.groups = groups
        self.tree.clear()
        for g in groups:
            self.tree.addTopLevelItem(self._group_item(g))
        n_un = len(idx.unreadable)
        nolink = [r for r in idx.all() if r.kind in ("mesh", "anim", "montage") and not r.skel_key]
        n_nolink = len(nolink)
        msg = skelgraph.summarize(groups) + extra
        if not groups:
            msg = "맞는 캐릭터를 찾지 못했습니다" + extra + ". 검색어를 바꾸거나, 도우미가 파일을 읽지 못했는지(UE 버전·usmap) 확인하세요."
        if n_nolink:
            msg += " · 스켈레톤 연결을 읽지 못한 메쉬/애니 %d개(캐릭터에 못 묶음 — 도우미가 연결 정보를 못 읽은 경우%s)" % (n_nolink, (": " + next((r.reason for r in nolink if r.reason), "")) if any(r.reason for r in nolink) else "")
        if n_un:
            msg += " · 열지 못한 파일 %d개" % n_un
        self.status.setText(msg)

    def _group_item(self, g: Group) -> QTreeWidgetItem:
        top = QTreeWidgetItem(["%s%s   (메쉬 %d · 애니메이션 %d)" % ((PLAYER_GUESS if g.player_src == "name" else PLAYER_MARK) if g.player else "", g.name, len(g.meshes), len(g.anims))])
        top.setData(0, ROLE, ("group", g.skeleton))
        tip = "스켈레톤: " + (g.skeleton_path or g.skeleton)
        if g.player:
            tip = ("🎮 플레이어블 캐릭터 — %s\n" if g.player_src != "name" else "🎮? 이름·폴더만 보고 추정 — %s (게임 데이터로 확인하지 못함)\n") % g.player_why \
                + "(아니면 [🎮 플레이어블 표시/해제] 로 바로잡을 수 있습니다)\n" + tip
        elif g.player_why:
            tip += "\n플레이어블 아님 — " + g.player_why
        top.setToolTip(0, tip)
        top.setExpanded(True)
        m = QTreeWidgetItem(["메쉬 (%d)" % len(g.meshes) + ("" if g.meshes else " — 이 검사 기록에 메쉬 없음")])
        for r in g.meshes:
            it = QTreeWidgetItem(["%s  · 리깅됨%s" % (r.name or os.path.basename(r.path), (" · %d본" % r.bones) if r.bones else "")])
            it.setData(0, ROLE, ("mesh", r.path))
            it.setToolTip(0, r.path)
            m.addChild(it)
        for r in g.skeleton_assets:
            it = QTreeWidgetItem(["%s  · 스켈레톤%s" % (r.name or os.path.basename(r.path), (" · %d본" % r.bones) if r.bones else "")])
            it.setToolTip(0, r.path)
            m.addChild(it)
        top.addChild(m)
        m.setExpanded(True)
        a = QTreeWidgetItem(["애니메이션 · 재생 가능 (%d)" % len(g.anims)])
        for r in g.anims:
            it = QTreeWidgetItem([_anim_text(r)])
            it.setData(0, ROLE, ("anim", r.path))
            it.setToolTip(0, r.path + "\n스켈레톤 연결 확인됨")
            a.addChild(it)
        top.addChild(a)
        a.setExpanded(True)
        if g.rejected:
            rj = QTreeWidgetItem(["재생 불가라 뺀 항목 (%d)" % len(g.rejected)])
            for r, why in g.rejected:
                it = QTreeWidgetItem(["%s — %s" % (r.name or os.path.basename(r.path), why)])
                it.setToolTip(0, r.path)
                rj.addChild(it)
            top.addChild(rj)
        return top

    def find_players(self, quiet: bool = False) -> None:
        """게임 데이터(기본 게임모드 → 기본 폰 → 폰이 쓰는 메시 → 스켈레톤)로 플레이어블 캐릭터를 확인한다."""
        a = self.win._extract_args()
        if a is None:
            return
        idx = self._index()
        if not idx.recs:
            if not quiet:
                self.status.setText("먼저 [자동으로 캐릭터 찾기] 로 캐릭터 목록을 만든 뒤 눌러 주세요 (폰이 쓰는 메시를 캐릭터에 연결하려면 검사 기록이 필요합니다).")
            return

        def job(w):
            try:
                info = extractor.find_players(*a)
            except Exception:  # noqa: BLE001 - 자동으로 이어서 돌릴 때는 실패해도 캐릭터 목록은 그대로 둔다
                if not quiet:
                    raise
                return None, None
            return info, skelgraph.player_proofs(info.get("pawns", []), info.get("configs", []), idx.all())

        def done(res):
            info, proofs = res
            if info is None:
                return
            idx.proofs = dict(proofs)
            idx.save()
            n_pawn, n_cfg = len(info.get("pawns", [])), len(info.get("configs", []))
            note = " (플레이어 확인: 폰 %d개·설정 %d개를 읽음 → %s)" % (n_pawn, n_cfg, ("캐릭터 %d명을 플레이어블로 확인" % len(proofs)) if proofs else
                                                              "데이터로는 플레이어 폰을 찾지 못함 — 이름 추정 또는 [🎮 플레이어블 표시/해제] 로 직접 정해 주세요")
            self._show(idx, self.query.text().strip(), None, extra=note)

        self.win.run_async(job, done, "플레이어 캐릭터 찾는 중 (게임 데이터 읽기)", cancellable=True)

    def toggle_player(self) -> None:
        """고른 캐릭터를 플레이어블로 표시/해제 (자동 추정을 사람이 바로잡는다). 여러 개를 골랐으면 첫 캐릭터 기준으로 한꺼번에 바꾼다."""
        sel = list(self.tree.selectedItems())
        keys: List[str] = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            stack = [top]
            hit = False
            while stack and not hit:
                x = stack.pop()
                hit = any(x is s for s in sel)
                for j in range(x.childCount()):
                    stack.append(x.child(j))
            d = top.data(0, ROLE)
            if hit and d and d[0] == "group" and d[1] not in keys:
                keys.append(d[1])
        if not keys:
            self.status.setText("플레이어블로 표시할 캐릭터를 목록에서 먼저 고르세요.")
            return
        idx = self._index()
        cur = {g.skeleton: g.player for g in self.groups}
        new = not cur.get(keys[0], False)
        for k in keys:
            idx.marks[k] = new
        idx.save()
        self._show(idx, self.query.text().strip(), None, extra=" (플레이어블 %s %d명)" % ("표시" if new else "해제", len(keys)))

    # ------------------------------------------------------------------ 선택 읽기
    def _leaf_paths(self, kind: str) -> List[str]:
        out: List[str] = []
        for it in self.tree.selectedItems():
            stack = [it]
            while stack:
                x = stack.pop()
                d = x.data(0, ROLE)
                if d and d[0] == kind and d[1] not in out:
                    out.append(d[1])
                for i in range(x.childCount()):
                    stack.append(x.child(i))
        return out

    def _double_clicked(self, it, col=0) -> None:
        d = it.data(0, ROLE)
        if not d:
            return
        if d[0] == "anim":
            self.win.extract_for_char([d[1]], "anim")
        elif d[0] == "mesh":
            self.win.extract_for_char([d[1]], "mesh")

    def preview(self) -> None:
        p = self._leaf_paths("anim")
        if not p:
            QMessageBox.warning(self, "미리보기", "애니메이션을 고르세요.")
            return
        self.win.extract_for_char(p[:1], "anim")

    def open_mesh(self) -> None:
        p = self._leaf_paths("mesh")
        if not p:
            QMessageBox.warning(self, "메쉬", "메쉬를 고르세요.")
            return
        self.win.extract_for_char(p[:1], "mesh")

    def collect_library(self) -> None:
        p = self._leaf_paths("anim")
        if not p:
            QMessageBox.warning(self, "모션 모으기", "애니메이션(또는 애니메이션이 든 폴더)을 고르세요. 여러 캐릭터를 함께 골라도 됩니다.")
            return
        self.win.extract_to_library(p)

    def send_to_list(self) -> None:
        anims, meshes = self._leaf_paths("anim"), self._leaf_paths("mesh")
        if not anims and not meshes:
            QMessageBox.warning(self, "추출 목록", "애니메이션이나 메쉬를 고르세요.")
            return
        if anims and meshes:
            QMessageBox.information(self, "추출 목록", "애니메이션과 메쉬는 따로 추출합니다. 애니메이션부터 목록에 올립니다 (메쉬는 다시 골라 올려 주세요).")
        self.win.set_extract_list(anims or meshes, "anim" if anims else "mesh")
