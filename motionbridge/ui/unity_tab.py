"""Unity asset extraction tab for the MotionBridge desktop UI."""
from __future__ import annotations

import os
import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from ..core import unity_extractor


class UnityExtractTab(QWidget):
    """Scan a local Unity install and export selected UnityPy-supported assets."""

    def __init__(self, host) -> None:
        super().__init__()
        self.host = host
        self.records: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)

        intro = QLabel(
            "Unity 게임 폴더의 *_Data / AssetBundle을 검사합니다. "
            "메시·텍스처·오디오·폰트·TextAsset·일부 직렬화 데이터와 "
            "AnimationClip 원본/메타데이터를 추출합니다."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        row = QHBoxLayout()
        self.game_path = QLineEdit()
        self.game_path.setPlaceholderText(r"Unity 게임 설치 폴더 (예: D:\Games\MyUnityGame)")
        self.game_path.setText(str(self.host.cfg.get("unity_game_dir", "")))
        row.addWidget(self.game_path, 1)
        browse = QPushButton("폴더 선택…")
        browse.clicked.connect(self._pick_game)
        row.addWidget(browse)
        root.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("AssetBundle 키"))
        self.bundle_key = QLineEdit()
        self.bundle_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.bundle_key.setPlaceholderText("필요한 경우에만 사용자 제공 키 입력")
        self.bundle_key.setText("")  # 키는 설정 파일에 저장하지 않는다
        self.bundle_key.setToolTip(
            "UnityPy가 지원하는 AssetBundle 복호화 키를 입력합니다. "
            "게임 키를 자동으로 찾거나 보호/온라인 검사를 우회하지 않습니다."
        )
        row.addWidget(self.bundle_key, 1)
        root.addLayout(row)

        row = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("이름/종류 필터 (예: AnimationClip, Player, Run)")
        self.filter.setClearButtonEnabled(True)
        row.addWidget(self.filter, 1)
        self.scan_button = QPushButton("Unity 검사")
        self.scan_button.clicked.connect(self.scan_game)
        row.addWidget(self.scan_button)
        root.addLayout(row)

        self.summary = QLabel("게임 폴더를 선택한 뒤 [Unity 검사]를 누르세요.")
        self.summary.setWordWrap(True)
        root.addWidget(self.summary)

        self.asset_list = QListWidget()
        self.asset_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.asset_list.setMinimumHeight(180)
        root.addWidget(self.asset_list, 1)

        row = QHBoxLayout()
        select_all = QPushButton("전체 선택")
        select_all.clicked.connect(self.asset_list.selectAll)
        row.addWidget(select_all)
        clear = QPushButton("선택 해제")
        clear.clicked.connect(self.asset_list.clearSelection)
        row.addWidget(clear)
        row.addStretch(1)
        self.export_button = QPushButton("선택 에셋 추출")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_selected)
        row.addWidget(self.export_button)
        root.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("출력 폴더"))
        self.out_path = QLineEdit(str(Path.home() / "MotionBridge_extracted" / "Unity"))
        row.addWidget(self.out_path, 1)
        out_browse = QPushButton("찾아보기…")
        out_browse.clicked.connect(self._pick_output)
        row.addWidget(out_browse)
        root.addLayout(row)

        self.log = QLabel(
            "참고: Unity AnimationClip은 현재 원본 payload + typetree JSON으로 추출합니다. "
            "Unity AnimationClip을 FBX/PSA로 변환하는 단계는 아직 연결되지 않았습니다."
        )
        self.log.setWordWrap(True)
        root.addWidget(self.log)

        self.filter.textChanged.connect(self._apply_filter)

    def _pick_game(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Unity 게임 폴더 선택", self.game_path.text())
        if path:
            self.game_path.setText(path)

    def _pick_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "출력 폴더 선택", self.out_path.text())
        if path:
            self.out_path.setText(path)

    def scan_game(self) -> None:
        game = self.game_path.text().strip()
        if not game or not os.path.isdir(game):
            QMessageBox.warning(self, "Unity 검사", "Unity 게임 설치 폴더를 먼저 선택하세요.")
            return
        key = self.bundle_key.text().strip() or None
        self.host.cfg["unity_game_dir"] = game
        self.host.cfg["unity_out"] = self.out_path.text().strip()
        from ..core import bridge
        bridge.save_config(self.host.cfg)
        self.scan_button.setEnabled(False)
        self.export_button.setEnabled(False)
        self.summary.setText("Unity 에셋 검사 중… (게임 폴더 크기에 따라 시간이 걸릴 수 있습니다)")
        self.host.run_async(
            lambda worker: unity_extractor.scan(game, bundle_key=key),
            self._scan_done,
            "Unity 에셋 검사 중",
        )

    def _scan_done(self, records) -> None:
        self.scan_button.setEnabled(True)
        self.records = list(records or [])
        self._apply_filter()
        self.summary.setText("검사 완료: 에셋 %d개 · 목록에서 필요한 항목을 골라 추출하세요." % len(self.records))
        self.export_button.setEnabled(bool(self.records))

    def _apply_filter(self, *_args) -> None:
        pattern = self.filter.text().strip()
        try:
            rx = re.compile(pattern, re.I) if pattern else None
        except re.error as exc:
            self.summary.setText("필터 정규식 오류: " + str(exc))
            return
        self.asset_list.clear()
        for record in self.records:
            label = "%-10s  %-22s  %s" % (
                record.get("path_id", ""), record.get("type", ""), record.get("name", "")
            )
            if rx and not rx.search(label):
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(record.get("asset_key") or (record.get("source", "") + "::" + str(record["path_id"]))))
            item.setToolTip("원본: " + str(record.get("source", "")))
            self.asset_list.addItem(item)
        if self.records:
            self.summary.setText("전체 %d개 중 %d개 표시" % (len(self.records), self.asset_list.count()))

    def export_selected(self) -> None:
        selected = self.asset_list.selectedItems()
        if not selected:
            QMessageBox.information(self, "Unity 추출", "목록에서 추출할 에셋을 선택하세요.")
            return
        game = self.game_path.text().strip()
        out = self.out_path.text().strip()
        if not game or not out:
            QMessageBox.warning(self, "Unity 추출", "게임 폴더와 출력 폴더를 확인하세요.")
            return
        asset_keys = {str(item.data(Qt.ItemDataRole.UserRole)) for item in selected}
        key = self.bundle_key.text().strip() or None
        os.makedirs(out, exist_ok=True)
        self.export_button.setEnabled(False)
        self.summary.setText("선택한 Unity 에셋 %d개 추출 중…" % len(asset_keys))
        self.host.run_async(
            lambda worker: unity_extractor.export(game, out, asset_keys=asset_keys, bundle_key=key),
            self._export_done,
            "Unity 에셋 추출 중",
        )

    def _export_done(self, files) -> None:
        self.export_button.setEnabled(True)
        made = list(files or [])
        self.summary.setText("추출 완료: 파일 %d개 → %s" % (len(made), self.out_path.text().strip()))
        if made:
            self.log.setText("추출된 파일:\n" + "\n".join(made[:12]) +
                             ("\n… 외 %d개" % (len(made) - 12) if len(made) > 12 else ""))
            self.host.statusBar().showMessage("Unity 추출 완료: 파일 %d개" % len(made))
        else:
            self.log.setText("지원되는 형식의 파일이 없습니다. 에셋 종류와 선택 항목을 확인하세요.")
