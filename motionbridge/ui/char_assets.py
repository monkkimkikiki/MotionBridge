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
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from ..core import bridge, extractor, skelgraph, textmatch
from ..core.skelgraph import Group, Rec
from .viewer import ASSET_MIME

ROLE = Qt.ItemDataRole.UserRole
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
                if d and (not kind or d[0] == kind) and d[1] not in paths:
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
                           "(폴더 위치·이름은 보지 않습니다)."))
        self.btn_auto = QPushButton("🔍  자동으로 캐릭터 찾기 (게임 전체)")
        self.btn_auto.setMinimumHeight(38)
        self.btn_auto.setToolTip("검색어 없이, 게임의 메쉬·스켈레톤·애니메이션 파일 안을 읽어서 캐릭터별로 자동으로 묶습니다. 처음 한 번만 오래 걸리고, 이어서 하거나 취소해도 읽은 만큼은 남습니다.")
        self.btn_auto.clicked.connect(lambda *_a: self.index_all())
        v.addWidget(self.btn_auto)
        h = QHBoxLayout()
        self.query = QLineEdit(self.win.cfg.get("char_query", ""))
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
        self.tree.setMinimumHeight(260)
        self.tree.setToolTip("메쉬/애니메이션을 고른 뒤 오른쪽 뷰포트로 끌어다 놓으면 바로 열립니다")
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        v.addWidget(self.tree)
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
        h = QHBoxLayout()
        self.btn_clear = QPushButton("검사 기록 지우기")
        self.btn_clear.clicked.connect(lambda *_a: self.clear_index())
        h.addWidget(self.btn_clear)
        h.addStretch(1)
        v.addLayout(h)

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

        self.win.run_async(job, lambda n: self._show(idx, self.query.text().strip(), None, extra=" (새로 읽은 파일 %d개)" % n), "캐릭터 자동 찾는 중 (파일 안 읽기)", cancellable=True)

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
            for r in recs:
                idx.add(r)
            idx.seen.update(seen)
            idx.save()
            prog({"type": "progress", "done": min(i + step, total)})

    # ------------------------------------------------------------------ 보이기
    def _show(self, idx: skelgraph.SkelIndex, q: str, touch: Optional[Set[str]], extra: str = "") -> None:
        groups = skelgraph.build_groups(idx.all(), q, touch)
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
        top = QTreeWidgetItem(["%s   (메쉬 %d · 애니메이션 %d)" % (g.name, len(g.meshes), len(g.anims))])
        top.setToolTip(0, "스켈레톤: " + (g.skeleton_path or g.skeleton))
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

    def send_to_list(self) -> None:
        anims, meshes = self._leaf_paths("anim"), self._leaf_paths("mesh")
        if not anims and not meshes:
            QMessageBox.warning(self, "추출 목록", "애니메이션이나 메쉬를 고르세요.")
            return
        if anims and meshes:
            QMessageBox.information(self, "추출 목록", "애니메이션과 메쉬는 따로 추출합니다. 애니메이션부터 목록에 올립니다 (메쉬는 다시 골라 올려 주세요).")
        self.win.set_extract_list(anims or meshes, "anim" if anims else "mesh")
