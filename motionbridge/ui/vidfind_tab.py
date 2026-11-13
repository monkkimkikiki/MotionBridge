"""'영상으로 찾기' 탭: 영상 속 캐릭터를 추측 -> 검수자가 고침 -> 메시/애니메이션 후보 -> 선택 추출.

흐름
  1) 영상/이미지(또는 AnimRef 클립 폴더)를 넣는다.
  2) 인식 방식(Ollama / Claude)을 고르고 [캐릭터 추측]. 게임 에셋 이름 목록 안에서 고르게 시킨다.
  3) 검수 표: 장면 이미지와 추측 이름이 보이고, 틀리면 후보 목록/검색으로 메시를 바꾼다. 고친 결과는 기억한다.
  4) [검수 확정]: 메시 목록과, 그 캐릭터의 애니메이션 후보(이름 일치 + 분류 + 타수 점수)가 나온다.
  5) 체크한 메시/애니메이션을 기존 추출 흐름(Models/, Animations/)으로 뽑는다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from typing import Dict, List, Optional

from PySide6.QtCore import QMimeData, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QInputDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QListWidget, QRadioButton,
    QHeaderView, QListWidgetItem, QMessageBox, QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import __version__
from ..core import classify
from ..core import nameglossary
from ..core import animlink, animref, assetidx, bridge, charcatalog, extractor, framepick, frames, gamenames, layout, loader, motionmatch, motionsearch, movecatalog, ollama_setup as setup_mod, pairing, panel, screenname, skelgraph, textmatch, vision
from ..core.identify import apply as identify_apply
from ..core.identify import entity_store as entstore
from ..core.identify import namefind as identify_namefind
from ..core.identify import unitlink as identify_unitlink
from ..core.identify import verify as identify_verify
from .viewer import ASSET_MIME
from .uikit import HelpIcon, Resizable, with_help
from .widgets import CollapsibleBox, EditPanel, fit_widths, wrappable

INDEX_CACHE = os.path.join(bridge.CONFIG_DIR, "assets.json")
MEMORY_FILE = os.path.join(bridge.CONFIG_DIR, "matches.json")
NAMES_CACHE = os.path.join(bridge.CONFIG_DIR, "names.json")
LOCRES_DIR = os.path.join(bridge.CONFIG_DIR, "locres")
FRAME_DIR = os.path.join(bridge.CONFIG_DIR, "vf_frames")
MESH_RX = re.compile(r"(?i)/sk[a-z]*_[^/]*$")
ANIM_RX = re.compile(r"(?i)anim")
LIST_PATTERN = r"(?i)(/sk[a-z]*_|/sm[a-z]{0,2}_|anim)"
STATIC_RX = re.compile(r"(?i)/sm[a-z]{0,2}_[^/]*$")
class _Vocab(list):
    """AI 에게 줄 이름 목록. ids=True 면 각 줄이 '<ID> = <게임 속 이름> [종류]' (캐릭터 카탈로그)."""
    ids = False


MONSTER_DIRS = re.compile(r"(?i)/(monsters?|enemies|enemy|bosses|boss|creatures?|npcs?)/")
LABEL_CHOICES = ["타격", "피격", "스킬", "연출", "회피", "사망", "걷기", "달리기", "대기"]
PICK_FILTER = "영상/이미지 (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.png *.jpg *.jpeg *.webp);;모든 파일 (*)"


IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")

FIND_TIPS = (
    "잘 찾는 법 — 무엇을 주면 쉽게 찾나:\n"
    "① 이름(가장 빠름·정확): 게임 화면/도감의 '인게임 표기 그대로'를 아는 정보에 적으세요 (한국어든 영어든 그대로).\n"
    "   게임 데이터 정답표·글자 사전에서 바로 이어집니다. 별명/줄임말/오타는 AI 확장으로 넘어가 느리고 빗나갈 수 있어요.\n"
    "② 이미지(이름을 모를 때): 몬스터 '전신'이 또렷한 장면에서, 몬스터만 꽉 차게 박스를 지정하세요.\n"
    "   · 이펙트/타격 섬광/모션 블러가 심한 순간, 몸이 반쯤 가린 장면, 너무 어두운 장면은 피하기\n"
    "   · 특징이 보이게: 꼬리·무기·방패·갑옷이 박스 안에 들어가게 (이런 게 변형 구분의 결정타)\n"
    "   · 같은 몬스터를 다른 각도로 2~3장 담으면([＋ 이 몬스터도]) 생김새 대조가 더 정확해집니다\n"
    "③ 설명: 종족(늑대/쥐/호랑이…) + 들고 있는 것(방패/횃불/활…) + 옷 색을 적으면 외형 검색이 변형까지 좁혀 줍니다.\n"
    "④ 종류 체크: 잡몹을 찾을 땐 '일반 몬스터'만 체크 — 보스가 후보를 차지하는 걸 막고 속도도 빨라집니다.")


class _PickLabel(QLabel):
    """크롭 대화상자의 그림: 마우스로 끌어 박스를 지정한다."""

    def __init__(self, owner) -> None:
        super().__init__()
        self.owner = owner
        self._p0 = None
        from PySide6.QtWidgets import QRubberBand
        self.band = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self.rect_sel = None     # QRect (표시 좌표)

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        self._p0 = ev.position().toPoint()
        self.band.setGeometry(QRect(self._p0, QSize(1, 1)))
        self.band.show()

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if self._p0 is not None:
            self.band.setGeometry(QRect(self._p0, ev.position().toPoint()).normalized())

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        if self._p0 is None:
            return
        r = QRect(self._p0, ev.position().toPoint()).normalized()
        self._p0 = None
        if r.width() >= 10 and r.height() >= 10:
            self.rect_sel = r
            self.owner._picked()
        else:
            self.band.hide()
            self.rect_sel = None
            self.owner._picked()


class _GrabDialog(QDialog):
    """영상에서 바로 찾기: 타임바로 장면을 고르고, 그 화면 위에서 몬스터를 마우스로 박스 지정하면 그 영역만 잘라 낸다.
    장면을 여러 장 저장해 두는 방식이 아니라 '장면 고르기 → 영역 지정 → 찾기'가 한 흐름이다."""

    def __init__(self, parent, ff: str, video: str, out_dir: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("영상에서 몬스터 지정 — " + os.path.basename(video))
        self.ff, self.video, self.out_dir = ff, video, out_dir
        self.crops: List[str] = []
        self.dur = frames.video_duration(ff, video) or 60.0
        self._full = ""            # 지금 장면의 원본 해상도 프레임 파일
        self._scale = 1.0
        v = QVBoxLayout(self)
        tip = QLabel("① 타임바를 움직여 몬스터 '전신'이 또렷한 장면에 놓고 (이펙트·가림·블러가 적은 순간) → ② 몬스터만 꽉 차게, 꼬리·무기 같은 특징까지 박스에 담아 지정 → ③ [완료]로 1) 목록에 추가.\n"
                     "찾기는 그 다음에 — '아는 정보'에 보스 이름을 적고 [영역 지정한 이미지로 찾기]를 누르세요. 같은 몬스터를 다른 각도로 2~3장 담으면 더 정확합니다.")
        tip.setWordWrap(True)
        v.addWidget(tip)
        self.view = _PickLabel(self)
        self.view.setMinimumSize(720, 405)
        self.view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.view.setStyleSheet("QLabel{background:#14161c;border:1px solid #3a4560}")
        v.addWidget(self.view, 0, Qt.AlignmentFlag.AlignHCenter)
        from PySide6.QtWidgets import QSlider
        h = QHBoxLayout()
        h.addWidget(QLabel("타임바"))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 999)
        self.slider.valueChanged.connect(self._time_label)
        self.slider.sliderReleased.connect(self._preview)
        h.addWidget(self.slider, 1)
        self.time_lb = QLabel("0:00.0")
        h.addWidget(self.time_lb)
        v.addLayout(h)
        h = QHBoxLayout()
        self.btn_more = QPushButton("✂ 이 영역 추가")
        self.btn_more.setEnabled(False)
        self.btn_more.setToolTip("지정한 박스를 잘라 담아 둡니다 — 다른 장면/다른 몬스터를 계속 지정할 수 있어요.")
        self.btn_more.clicked.connect(self._keep)
        h.addWidget(self.btn_more)
        self.btn_go = QPushButton("완료 — 1) 목록에 추가")
        self.btn_go.setEnabled(False)
        self.btn_go.setToolTip("담아 둔 영역(박스가 그려져 있으면 그것까지)을 잘라 1) 영상/이미지 목록에 넣습니다.\n"
                              "그 다음 '아는 정보'에 보스 이름 등을 적고 [영역 지정한 이미지로 찾기]를 누르면 찾기 시작 — 바로 찾지 않아요.")
        self.btn_go.clicked.connect(self._go)
        h.addWidget(self.btn_go)
        self.note = QLabel("")
        h.addWidget(self.note, 1)
        b = QPushButton("닫기")
        b.clicked.connect(self.reject)
        h.addWidget(b)
        v.addLayout(h)
        self._preview()

    def _t(self) -> float:
        return min(self.slider.value() / 999.0, 0.999) * self.dur

    def _time_label(self, *_a) -> None:
        t = self._t()
        self.time_lb.setText("%d:%04.1f / %d:%04.1f" % (t // 60, t % 60, self.dur // 60, self.dur % 60))

    def _frame_to(self, out: str) -> bool:
        try:
            r = frames._run([self.ff, "-ss", "%.3f" % self._t(), "-i", self.video, "-frames:v", "1", "-y", out], timeout=30.0)
            return r.returncode == 0 and os.path.isfile(out)
        except Exception:  # noqa: BLE001
            return False

    def _preview(self) -> None:
        os.makedirs(self.out_dir, exist_ok=True)
        self.view.band.hide()
        self.view.rect_sel = None
        full = os.path.join(self.out_dir, "_scene.png")
        if self._frame_to(full):
            pm = QPixmap(full)
            if not pm.isNull():
                self._full = full
                shown = pm.scaled(960, 540, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                self._scale = shown.width() / max(pm.width(), 1)
                self.view.setPixmap(shown)
                self.view.setFixedSize(shown.size())
                self.adjustSize()
                self._picked()
                return
        self._full = ""
        self.view.setText("이 지점의 장면을 읽지 못했어요 — 타임바를 조금 움직여 보세요")
        self._picked()

    def _picked(self) -> None:
        ok = self.view.rect_sel is not None and bool(self._full)
        self.btn_go.setEnabled(ok or bool(self.crops))
        self.btn_more.setEnabled(ok)

    def _cut(self) -> Optional[str]:
        """지정한 박스를 원본 해상도 장면에서 잘라 저장한다."""
        r = self.view.rect_sel
        if r is None or not self._full:
            return None
        pm = QPixmap(self._full)
        s = max(self._scale, 1e-9)
        orig = QRect(int(r.x() / s), int(r.y() / s), int(r.width() / s), int(r.height() / s)).intersected(pm.rect())
        import time as _time
        t = self._t()
        out = os.path.join(self.out_dir, "crop_%s_%02d-%04.1f_%d.png" % (_time.strftime("%H%M%S"), t // 60, t % 60, len(self.crops)))
        try:
            if pm.copy(orig).save(out, "PNG"):
                return out
        except Exception:  # noqa: BLE001
            pass
        return None

    def _keep(self) -> None:
        p = self._cut()
        if p:
            self.crops.append(p)
            self.view.band.hide()
            self.view.rect_sel = None
            self._picked()
            self.note.setText("담아 둔 영역 %d개 — 계속 지정하거나 [완료]로 목록에 추가" % len(self.crops))

    def _go(self) -> None:
        p = self._cut()
        if p:
            self.crops.append(p)
        if self.crops:
            self.accept()


class _CropDialog(QDialog):
    """스크린샷에서 '찾을 몬스터'를 마우스로 박스 지정 — 그 영역만 AI 에게 보여 줘서 빠르고, 어떤 몬스터인지 헷갈리지 않는다."""

    def __init__(self, parent, img_path: str, save_path: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("찾을 몬스터 영역 지정 — " + os.path.basename(img_path))
        self.out_path: Optional[str] = None
        self._save = save_path
        self.pm = QPixmap(img_path)
        v = QVBoxLayout(self)
        tip = QLabel("찾고 싶은 몬스터를 마우스로 끌어 박스로 지정하세요. 몬스터만 꽉 차게 잡을수록 정확합니다.")
        tip.setWordWrap(True)
        v.addWidget(tip)
        self.view = _PickLabel(self)
        if not self.pm.isNull():
            shown = self.pm.scaled(960, 600, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self._scale = shown.width() / max(self.pm.width(), 1)
            self.view.setPixmap(shown)
            self.view.setFixedSize(shown.size())
        else:
            self._scale = 1.0
            self.view.setText("(이미지를 읽지 못했습니다)")
        v.addWidget(self.view, 0, Qt.AlignmentFlag.AlignHCenter)
        h = QHBoxLayout()
        self.btn_ok = QPushButton("✂ 이 영역으로 찾기")
        self.btn_ok.setEnabled(False)
        self.btn_ok.clicked.connect(self._accept_crop)
        h.addWidget(self.btn_ok)
        b = QPushButton("영역 없이 전체 이미지로")
        b.clicked.connect(self.accept)      # out_path 없음 = 원본 전체
        h.addWidget(b)
        b = QPushButton("이 이미지는 건너뛰기")
        b.clicked.connect(self.reject)
        h.addWidget(b)
        v.addLayout(h)

    def _picked(self) -> None:
        self.btn_ok.setEnabled(self.view.rect_sel is not None)

    def _accept_crop(self) -> None:
        r = self.view.rect_sel
        if r is None or self.pm.isNull():
            self.accept()
            return
        s = max(self._scale, 1e-9)
        orig = QRect(int(r.x() / s), int(r.y() / s), int(r.width() / s), int(r.height() / s)).intersected(self.pm.rect())
        try:
            os.makedirs(os.path.dirname(self._save), exist_ok=True)
            if self.pm.copy(orig).save(self._save, "PNG"):
                self.out_path = self._save
        except Exception:  # noqa: BLE001 - 크롭 실패 시 원본 전체로
            self.out_path = None
        self.accept()


class _LoreDialog(QDialog):
    """게임 도감 브라우저: 몬스터 이름·분류·일화를 훑어보고, 하나 골라 [메시 찾기]로 바로 넘어간다."""

    _KIND = {1: "잡요괴", 2: "두목", 3: "요왕", 4: "인물"}

    def __init__(self, parent, cards: Dict[int, dict]) -> None:
        super().__init__(parent)
        self.setWindowTitle("게임 도감 — 몬스터를 고르면 바로 찾습니다")
        self.resize(760, 520)
        self.picked: Optional[str] = None
        self._rows = []
        for i, c in cards.items():
            nm = c["names"].get("ko") or c["names"].get("en") or ""
            if not nm:
                continue
            kind = self._KIND.get((int(i) // 1000) % 100, "")
            txt = (c["texts"].get("ko") or c["texts"].get("en") or "").strip()
            self._rows.append((kind, nm, txt))
        self._rows.sort(key=lambda r: ({"요왕": 0, "두목": 1, "잡요괴": 2, "인물": 3}.get(r[0], 4), r[1]))
        v = QVBoxLayout(self)
        self.q = QLineEdit()
        self.q.setPlaceholderText("도감에서 검색 — 이름이나 일화 속 낱말 (예: 늑대, 방패, 흰 옷)")
        self.q.textChanged.connect(self._refill)
        v.addWidget(self.q)
        h = QHBoxLayout()
        self.lst = QListWidget()
        self.lst.currentRowChanged.connect(self._show)
        self.lst.itemDoubleClicked.connect(lambda *_a: self._go())
        h.addWidget(self.lst, 1)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        h.addWidget(self.detail, 1)
        v.addLayout(h, 1)
        b = QHBoxLayout()
        self.note = QLabel("몬스터를 고르고 [메시 찾기] — 게임 데이터 정답표·사전으로 바로 이어집니다 (더블클릭도 같음)")
        self.note.setWordWrap(True)
        b.addWidget(self.note, 1)
        go = QPushButton("🔍 이 몬스터의 메시 찾기")
        go.clicked.connect(self._go)
        b.addWidget(go)
        c2 = QPushButton("닫기")
        c2.clicked.connect(self.reject)
        b.addWidget(c2)
        v.addLayout(b)
        self._shown = []
        self._refill()

    def _refill(self, *_a) -> None:
        q = (self.q.text() or "").strip().lower()
        self.lst.clear()
        self._shown = []
        for kind, nm, txt in self._rows:
            if q and q not in nm.lower() and q not in txt.lower():
                continue
            self._shown.append((kind, nm, txt))
            self.lst.addItem("%s%s" % (("[%s] " % kind) if kind else "", nm))
        if self._shown:
            self.lst.setCurrentRow(0)
        else:
            self.detail.setPlainText("")

    def _show(self, row: int) -> None:
        if 0 <= row < len(self._shown):
            kind, nm, txt = self._shown[row]
            self.detail.setPlainText("%s%s\n\n%s" % (nm, (" — %s" % kind) if kind else "", txt or "(일화 없음)"))

    def _go(self) -> None:
        row = self.lst.currentRow()
        if 0 <= row < len(self._shown):
            self.picked = self._shown[row][1]
            self.accept()


class _Row:
    """검수 표의 한 줄."""

    def __init__(self, group: vision.Group, combo, search, exclude, base: List[assetidx.MeshCandidate], sure: bool = True) -> None:
        self.group, self.combo, self.search, self.exclude, self.base, self.sure = group, combo, search, exclude, base, sure

    def chosen(self) -> Optional[str]:
        if self.exclude.isChecked():
            return None
        return self.combo.currentData() or None


def _thumb(frame: str, box) -> Optional[object]:
    """장면 이미지(캐릭터 위치가 있으면 그 부분)를 작은 그림으로. 읽지 못하면 None."""
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


MAX_PANEL_NAMES = 150  # AI 회의에 알려 주는 애니메이션 이름 수 (많으면 작은 모델이 헷갈린다)
MAX_MOTION_RANK = 40  # 동작 비교로 한 번에 임시로 뽑아 비교하는 최대 개수 (뽑는 데 시간이 든다)


class _AssetList(QListWidget):
    """고른 메시/애니메이션을 뷰포트로 끌어다 놓을 수 있는 목록 (항목의 UserRole = 게임 안 경로). 놓으면 영상찾기가 직접 열고 탭은 옮기지 않는다."""

    def __init__(self, kind: str) -> None:
        super().__init__()
        self.drag_kind = kind                       # "mesh" | "anim"
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)

    def mimeData(self, items):  # noqa: N802
        md = QMimeData()
        paths: List[str] = []
        for it in items:
            if it.isHidden():
                continue
            p = it.data(Qt.ItemDataRole.UserRole)
            if p and p not in paths:
                paths.append(p)
        md.setData(ASSET_MIME, json.dumps({"kind": self.drag_kind, "paths": paths, "origin": "vidfind"}).encode("utf-8"))
        return md


class _DeleteList(QListWidget):
    """Delete 키를 누르면 on_delete 를 부르는 목록 (원본 목록에서 고른 것을 빼는 데 쓴다)."""
    on_delete = None

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_Delete and self.on_delete is not None:
            self.on_delete()
            ev.accept()
            return
        super().keyPressEvent(ev)


class VidFindTab(QWidget):
    def __init__(self, win) -> None:
        super().__init__()
        self.win = win
        self.cfg = win.cfg
        self.index: Optional[assetidx.AssetIndex] = None
        self.memory = assetidx.MatchMemory(MEMORY_FILE)
        self.rows: List[_Row] = []
        self.confirmed: List[str] = []
        self.char_mode = False  # True 면 영상 추측과 상관없이 플레이어블 캐릭터 메시/애니메이션 전체 목록을 보여 주는 중
        self.clip_labels: Dict[str, str] = {}
        self.link_query: Optional[dict] = None  # AnimRef 가 보낸 요청 (답을 보내기 전까지)
        self.last_files: List[str] = []         # 방금 추출한 파일 (AnimRef 결과에 넣는다)
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

        intro = QLabel("영상(또는 장면 이미지)에 나온 캐릭터를 AI가 추측하고, 검수자가 고친 뒤 그 캐릭터의 메시와 애니메이션을 골라 추출합니다.\n"
                       "먼저 '0. 게임에서 추출' 탭에서 게임 폴더를 검사해 두세요.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.link_banner = QLabel("")
        self.link_banner.setWordWrap(True)
        self.link_banner.setStyleSheet("color:#5ab0ff")
        lay.addWidget(self.link_banner)

        # 1) 입력
        g = CollapsibleBox("1) 영상 / 이미지 / AnimRef 클립")
        v = QVBoxLayout(g)
        self.src_list = _DeleteList()
        self.src_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.src_list.setMinimumHeight(45)
        self.src_list.setToolTip("고르지 않으면 목록 전체를 씁니다. 원본이 많으면 고르게 솎아서 씁니다. 고르고 Delete 키로 뺄 수 있습니다.")
        self.src_list.on_delete = self.remove_sources
        v.addWidget(Resizable(self.src_list, "vf_src_list", 45))
        h = QHBoxLayout()
        b = QPushButton("영상/이미지 추가…")
        b.clicked.connect(self.add_files)
        h.addWidget(b)
        b = QPushButton("🎞 영상에서 몬스터 영역 따기…")
        b.setToolTip("목록에서 고른 영상(안 골랐으면 첫 영상)을 열어:\n"
                     "① 타임바로 몬스터가 잘 보이는 장면을 고르고 → ② 그 화면에서 몬스터를 마우스로 박스 지정 → ③ [완료]로 1) 목록에 이미지로 추가.\n"
                     "그 뒤 '아는 정보'에 보스 이름 등을 적고 [영역 지정한 이미지로 찾기]를 누르면, 그 영역만 AI 에게 보여 줘 찾습니다.")
        b.clicked.connect(self.grab_from_video)
        h.addWidget(b)
        b = QPushButton("AnimRef 클립 폴더…")
        b.setToolTip("AnimRef 가 자른 클립 폴더. 분류(타격/피격/스킬…)를 함께 읽습니다.")
        b.clicked.connect(self.add_animref)
        h.addWidget(b)
        b = QPushButton("AnimRef query.json 열기…")
        b.setToolTip("AnimRef 가 '추출 툴로 보내기' 때 만든 query.json 을 직접 고릅니다 (실시간 연결이 안 될 때). 메뉴 파일 > AnimRef에서 가져오기 와 같습니다.")
        b.clicked.connect(lambda *_a: self.open_query_file())
        h.addWidget(b)
        b = QPushButton("선택한 원본 지우기")
        b.setToolTip("목록에서 고른 영상/클립만 뺍니다 (파일은 지워지지 않습니다). 고른 게 없으면 아무것도 안 합니다. Delete 키도 같습니다.")
        b.clicked.connect(self.remove_sources)
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
        g = CollapsibleBox("2) 인식 방식 (검수자가 고릅니다)")
        v = QVBoxLayout(g)
        h = QHBoxLayout()
        self.engine = QComboBox()
        self.engine.addItem(vision.OllamaProvider.label, "ollama")
        self.engine.addItem(vision.GptOssProvider.label, "gptoss")
        self.engine.addItem(vision.ClaudeProvider.label, "claude")
        self.engine.addItem(vision.OpenAICompatProvider.label, "openai")
        self.engine.setCurrentIndex(max(self.engine.findData(self.cfg.get("vf_engine", "ollama")), 0))
        self.engine.currentIndexChanged.connect(self._engine_changed)
        self.engine.activated.connect(self._engine_picked)   # 사용자가 직접 골랐을 때만 (설치 안내)
        h.addWidget(self.engine, 1)
        h.addWidget(QLabel("모델"))
        self.model = QLineEdit()
        self.model.setToolTip("Ollama 예: %s\nClaude 예: %s\n다른 AI: 이미지를 볼 수 있는 모델 이름 (gpt-4o, gemini-2.5-flash …)" % (", ".join(vision.OLLAMA_MODELS), ", ".join(vision.CLAUDE_MODELS)))
        h.addWidget(self.model, 1)
        b = QPushButton("연결 확인")
        b.clicked.connect(self.check_engine)
        h.addWidget(b)
        self.btn_pull = QPushButton("모델 받기")
        self.btn_pull.setToolTip("Ollama 에 위 모델을 내려받습니다 (약 6GB, 한 번만). Ollama 가 켜져 있어야 합니다.")
        self.btn_pull.clicked.connect(self.pull_model)
        h.addWidget(self.btn_pull)
        self.btn_setup = QPushButton("자동 설치·준비")
        self.btn_setup.setToolTip("Ollama 가 없으면 설치하고, 꺼져 있으면 실행하고, 모델이 없으면 받습니다 (필요한 것만 순서대로). 처음엔 수 GB 를 받으니 시간이 걸립니다.")
        self.btn_setup.clicked.connect(self.setup_ollama)
        h.addWidget(self.btn_setup)
        v.addLayout(h)
        self.oa_row = QWidget()  # 다른 AI(OpenAI 호환) 전용: 서비스 고르기 + 서버 주소
        oh = QHBoxLayout(self.oa_row)
        oh.setContentsMargins(0, 0, 0, 0)
        oh.addWidget(QLabel("서비스"))
        self.oa_preset = QComboBox()
        for nm, _u, _m, _e in vision.OPENAI_PRESETS:
            self.oa_preset.addItem(nm, nm)
        self.oa_preset.setCurrentIndex(max(self.oa_preset.findData(self.cfg.get("vf_oa_preset", vision.OPENAI_PRESETS[0][0])), 0))
        self.oa_preset.currentIndexChanged.connect(self._preset_changed)
        oh.addWidget(self.oa_preset)
        oh.addWidget(QLabel("서버 주소"))
        self.oa_url = QLineEdit(self.cfg.get("vf_oa_url", ""))
        self.oa_url.setPlaceholderText("비워 두면 서비스의 기본 주소")
        oh.addWidget(self.oa_url, 1)
        self.btn_models = QPushButton("모델 목록")
        self.btn_models.setToolTip("서버에서 쓸 수 있는 모델 이름을 불러와 고릅니다 (이미지를 볼 수 있어 보이는 것이 위쪽). 무료 모델은 자주 바뀌므로 여기서 확인하세요.")
        self.btn_models.clicked.connect(lambda *_a: self.load_models())
        oh.addWidget(self.btn_models)
        v.addWidget(self.oa_row)
        self.oa_info = QLabel("")
        self.oa_info.setWordWrap(True)
        self.oa_info.setOpenExternalLinks(True)
        self.oa_info.setStyleSheet("color:#8a93a6")
        v.addWidget(self.oa_info)
        self._keys = {"claude": self.cfg.get("vf_claude_key", ""), "openai": self.cfg.get("vf_openai_key", "")}
        self.key_row = QWidget()
        kh = QHBoxLayout(self.key_row)
        kh.setContentsMargins(0, 0, 0, 0)
        self.key_label = QLabel("Claude API 키")
        kh.addWidget(self.key_label)
        self.key = QLineEdit(self._keys["claude"])
        self.key.textChanged.connect(self._remember_key)
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
        self.hint.setPlaceholderText("비워 두면 0번 탭에서 찾은 게임 이름을 자동으로 씁니다")
        h.addWidget(self.hint, 1)
        h.addWidget(HelpIcon("게임 이름을 알면 AI 가 그 게임의 몬스터를 떠올리기 쉬워 정확도가 올라갑니다.\n"
                             "비워 두면 [0. 게임에서 추출] 탭에서 게임 폴더를 검사할 때 자동으로 잡힌 이름(폴더 이름 기준)을 그대로 씁니다.\n"
                             "직접 적은 이름이 있으면 그것이 우선입니다."))
        h.addWidget(QLabel("이름 필터(정규식)"))
        self.filter = QLineEdit(self.cfg.get("vf_filter", ""))
        self.filter.setPlaceholderText("예: (?i)(monster|boss) — 비우면 자동")
        self.filter.setToolTip("AI에게 알려줄 후보 이름을 경로로 줄입니다. 무기/소품이 섞이면 정확도가 떨어집니다.")
        h.addWidget(self.filter, 1)
        h.addWidget(HelpIcon("게임 파일의 '경로 글자'를 거르는 규칙입니다. 비우면 자동입니다.\n\n"
                             "· 쉼표(,)로 나누면 모두 들어간 것만 남깁니다 (예: boss,skeleton)\n"
                             "· 정규식도 됩니다 — 예: (?i)(monster|boss) 는 대소문자 무시하고 monster 또는 boss 가 든 경로\n"
                             "· 한글로 적어도 오류는 없지만, 게임 파일 경로는 대부분 영어라 한글 글자는 경로에 한글이 있을 때만 맞습니다.\n"
                             "  한글 이름으로 캐릭터를 찾고 싶으면 '아는 정보' 칸에 보스 이름을 적으세요 (글자 사전 + AI 확장 검색으로 내부 이름을 찾아 줍니다)."))
        v.addLayout(h)
        kl = QHBoxLayout()
        kl.addWidget(QLabel("아는 정보 (선택) — 이 영상에 나오는 캐릭터를 한 줄에 하나씩: 게임 속 이름 — 메모"), 1)
        self.btn_lore = QPushButton("📖 게임 도감 열기")
        self.btn_lore.setToolTip("이 게임의 도감(몬스터 이름·분류·일화)을 목록으로 보여 줍니다.\n몬스터를 고르고 [메시 찾기]를 누르면 그 이름으로 바로 검색합니다 — 이름을 몰라도 도감을 훑어서 찾으세요.")
        self.btn_lore.clicked.connect(self._open_lore)
        self.btn_lore.setVisible(False)      # 도감이 있는 게임에서만 보인다 (_update_known_tip 이 켠다)
        kl.addWidget(self.btn_lore)
        kl.addWidget(HelpIcon("한 줄에 캐릭터 하나씩, 'Enter' 로 줄을 나눠 적으세요. 형식: 이름 — 메모 (메모는 없어도 됩니다)\n\n"
                              "메모에 적으면 도움이 되는 정보:\n"
                              "· 찾으려는 몬스터 이름\n"
                              "· 외형적 특징 (색, 크기, 갑옷, 뿔 …)\n"
                              "· 인간형인지 / 4족 보행인지 / 크리처(괴물)인지\n"
                              "· 쓰는 무기가 무엇인지 (검, 도끼, 창, 맨손 …)\n"
                              "· 공격 방식이 무엇인지 (내려찍기, 돌진, 회전 …)"))
        v.addLayout(kl)
        self.known = QPlainTextEdit()
        self.known.setPlainText(self.cfg.get("vf_known", ""))
        self.known.setPlaceholderText("한 줄에 하나씩 적으세요.  이름 — 외형 / 인간형·4족·크리처 / 무기 / 공격 방식\n예)\n금지장로 — 보스, 불탄 노승, 석장\nThe Nameless Captive — 보스, 쇠사슬에 묶인 인간형, 맨손, 내려찍기\n(비워도 됩니다)\n\n💡 가장 빠른 방법: 게임 화면·도감에 나오는 '인게임 표기 그대로' 적으세요 (한국어든 영어든 그대로).\n   게임 데이터(유닛 ↔ 이름 정답표)와 글자 사전에서 바로 이어져 AI 확장 없이 즉시 맞습니다.\n   별명·줄임말·오타는 AI 확장 검색(원어·음차·지역·모티프 코드명)으로 넘어가서 시간이 더 걸립니다.")
        self.known.setMinimumHeight(50)
        v.addWidget(Resizable(self.known, "vf_known_h", 50))
        self.known_tip = QLabel("")        # 게임별 맞춤 팁: 이 게임의 도감/정답표를 보고 '어떻게 적으면 바로 찾는지' 예시로 알려 준다
        self.known_tip.setWordWrap(True)
        self.known_tip.setStyleSheet("color:#8fb0d0")
        self.known_tip.setVisible(False)
        v.addWidget(self.known_tip)
        self._update_known_tip()
        self._warm_lore_bg()       # 글자 사전이 이미 있으면 도감을 뒤에서 조용히 읽어 [📖 도감] 버튼·팁을 켠다
        self.engine_note = QLabel("")
        self.engine_note.setWordWrap(True)
        v.addWidget(self.engine_note)
        lay.addWidget(g)
        self._engine_changed()

        # 3) 추측
        g = CollapsibleBox("3) 캐릭터 추측")
        v = QVBoxLayout(g)
        h = QHBoxLayout()
        self.min_conf = QSpinBox()
        self.min_conf.setRange(0, 100)
        self.min_conf.setSuffix("%")
        self.min_conf.setValue(int(self.cfg.get("vf_min_conf", 60)))
        self.min_conf.setToolTip("AI 확신이 이 값보다 낮은 줄에는 검수 표에 '⚠ 확신 낮음' 표시를 붙여 한 번 더 보게 합니다 (메시는 가장 유력한 후보가 그래도 골라져 있습니다).")
        r = QHBoxLayout()
        self.rb_mode_monster = QRadioButton("📹 영상으로 찾기 — 영상 전체를 훑어 자동 추측 (가장 느림)")
        self.rb_mode_img = QRadioButton("🖼 영역 지정한 이미지로 찾기 — 몬스터만 집어서 (추천)")
        self.rb_mode_char = QRadioButton("⌨ 이름·설명으로 찾기 — 영상 없이 글로")
        self.rb_mode_monster.setToolTip("영상 전체를 분석해 몬스터를 추측합니다 — 장면을 수십 장 뽑아 AI 에게 여러 번 물어서 가장 오래 걸립니다.\n"
                                        "찾는 몬스터가 정해져 있으면 '영역 지정한 이미지로 찾기'를 쓰세요. 결과는 어느 모드든 4) 검수 표로 이어집니다.")
        self.rb_mode_img.setToolTip("게임 영상을 일시정지하고 찍은 스크린샷을 1)에 넣고, 찾을 몬스터를 마우스로 박스 지정합니다.\n"
                                    "그 영역만 AI 에게 보여 주므로 어떤 몬스터인지 헷갈리지 않고, 후보가 여럿이면 실제 렌더로 생김새까지 대조합니다.\n"
                                    "이 모드는 이미지 파일만 쓰고 1) 목록의 영상은 건드리지 않습니다.")
        self.rb_mode_char.setToolTip("영상 없이 이름으로 찾습니다: 파일 이름 검색 + [🌐 AI 확장 검색](금지장로 같은 한국어 보스 이름·동작 설명도 원어/음차/지역 코드로 확장).\n"
                                     "찾은 캐릭터의 메시가 후보 목록에 바로 올라가고, 해당 애니메이션 클립도 같이 나옵니다.")
        mode0 = str(self.cfg.get("vf_mode") or ("char" if self.cfg.get("vf_mode_char") else "video"))
        self.rb_mode_char.setChecked(mode0 == "char")
        self.rb_mode_img.setChecked(mode0 == "image")
        self.rb_mode_monster.setChecked(mode0 not in ("char", "image"))
        r.addWidget(self.rb_mode_monster)
        r.addWidget(self.rb_mode_img)
        r.addWidget(self.rb_mode_char)
        r.addWidget(HelpIcon(FIND_TIPS))   # ❓ 잘 찾는 법: 어떤 정보/이미지를 주면 쉽게 찾는지
        r.addStretch(1)
        v.addLayout(r)
        # 찾을 종류: 에셋 신호(애니 수·전용 연출·경로 표시)로 자동 분류해, 고른 종류 안에서만 후보를 찾는다
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("찾을 종류"))
        self.cls_checks = {}
        saved_cls = self.cfg.get("vf_cls") if isinstance(self.cfg.get("vf_cls"), dict) else {}
        for key_, lab_ in (("boss", "보스"), ("mob", "일반 몬스터"), ("player", "플레이어블"), ("npc", "NPC")):
            cb_ = QCheckBox(lab_)
            cb_.setChecked(bool(saved_cls.get(key_, True)))
            cb_.toggled.connect(self._cls_changed)
            r2.addWidget(cb_)
            self.cls_checks[key_] = cb_
        r2.addWidget(HelpIcon("체크한 종류 안에서만 후보를 찾습니다. 분류 근거는 '게임이 데이터로 적어 둔 것'만 씁니다:\n"
                              "· 보스/일반 몬스터 = 게임 도감의 공식 분류(요왕·두목 = 보스, 잡요괴 = 일반) — [추출기 업데이트] 후 이름 연결이 되면 정확해집니다\n"
                              "· 플레이어블 = 경로/이름에 player·hero 표시 · NPC = NPC 폴더/이름\n"
                              "⚠ 분류를 아직 모르는 캐릭터는 어떤 체크든 '걸러지지 않습니다' — 추정으로 잘못 숨기는 일을 막기 위해서예요.\n"
                              "(예전의 '애니 개수로 보스 추정'은 잡몹이 보스로 숨는 오분류가 있어 뺐습니다.)"))
        r2.addStretch(1)
        v.addLayout(r2)
        eng_line = QLabel("어느 모드든 같은 검색 엔진을 차례로 씁니다: ① 정답표(게임 데이터의 이름↔코드명) → ② 도감 일화 글 검색 → ③ 글자 사전·뜻 풀이 → ④ AI 확장(원어·음차·지역 코드) → ⑤ 외형(종족·들고 있는 것)."
                          "  생김새 렌더 대조(⑥)는 🖼 이미지 모드에서만 돕니다. 자세한 건 옆 ? 와 [▸ 자세히] 로그.")
        eng_line.setWordWrap(True)
        eng_line.setStyleSheet("color:#8a93a6")
        v.addWidget(eng_line)
        self.char_panel = QWidget()
        cp = QVBoxLayout(self.char_panel)
        cp.setContentsMargins(0, 0, 0, 0)
        ch = QHBoxLayout()
        self.char_q = QLineEdit()
        self.char_q.setPlaceholderText("캐릭터 메쉬 이름 검색 (쉼표/띄어쓰기 = 모두 포함, 예: knight,armor). 비우고 검색하면 전체")
        self.char_q.returnPressed.connect(self.show_char_meshes)
        ch.addWidget(self.char_q, 1)
        self.char_rig = QComboBox()
        for lab, key in (("리깅 전체", "all"), ("리깅됨 (SK_ 스켈레탈)", "rigged"), ("리깅 안 됨 (SM_ 스태틱)", "static")):
            self.char_rig.addItem(lab, key)
        self.char_rig.currentIndexChanged.connect(lambda *_a: self.show_char_meshes())
        ch.addWidget(self.char_rig)
        self.ck_char_player = QCheckBox("플레이어블만")   # 화면에는 안 보임 — '찾을 종류'의 플레이어블 체크와 겹쳐서 숨김 (내부/테스트 호환용)
        self.ck_char_player.setVisible(False)
        self.ck_char_player.toggled.connect(self._char_player_toggled)
        b = QPushButton("검색")
        b.clicked.connect(lambda *_a: self.show_char_meshes())
        ch.addWidget(b)
        b = QPushButton("🌐 AI 확장 검색")
        b.setToolTip("이름이든 동작 설명이든, 리소스 이름과 전혀 달라도 캐릭터를 찾는 통합 검색입니다.\n"
                     "· 이름(예: 금지장로): AI 가 원어 정식명·병음 음차·등장 지역 이니셜(旧观音禅院→GYCY)·원전 모티프 코드명으로 확장하고,\n"
                     "  폴더들을 지역 코드와 보스 신호(애니 수·전용 연출·무기 메시)로 점수 매깁니다.\n"
                     "· 동작(예: 7연속 스핀 공격): 애니메이션 이름 낱말로 캐릭터를 거꾸로 찾습니다.\n"
                     "두 방법이 같은 캐릭터를 지목하면 가산점. 글 전용 AI(GPT-OSS 등)로도 동작합니다.")
        b.clicked.connect(self.start_namefind)
        ch.addWidget(b)
        cp.addLayout(ch)
        self.char_note = QLabel("")
        self.char_note.setWordWrap(True)
        cp.addWidget(self.char_note)
        v.addWidget(self.char_panel)
        self.char_panel.setVisible(mode0 == "char")
        self.rb_mode_char.toggled.connect(self._mode_changed)
        self.rb_mode_img.toggled.connect(self._mode_changed)
        self.ck_fast = QCheckBox("빠르게 (후보 5개)")
        self.ck_fast.setChecked(bool(self.cfg.get("vf_fast", True)))
        self.ck_fast.setToolTip("켜면 시간을 크게 줄입니다: ① 영상마다 장면 시트 한 장으로 AI 에게 한 번만 묻고(장면마다 묻지 않음), ② AI 에게 알리는 이름 목록을 %d개로 줄이고, "
                                "③ 하나로 못 박지 않고 AI 가 꼽은 후보를 합쳐 검수 칸에 후보 5개를 보여 줍니다. 끄면 장면마다 정밀하게 묻습니다(느림)." % vision.FAST_VOCAB)
        self.ck_fast.toggled.connect(lambda v: (self.cfg.__setitem__("vf_fast", bool(v)), bridge.save_config(self.cfg)))
        self.btn_guess = QPushButton("캐릭터 추측 시작")
        self.btn_guess.setMinimumHeight(32)
        if self.rb_mode_char.isChecked():
            self.btn_guess.setText("캐릭터 메쉬 목록 열기")
        elif self.rb_mode_img.isChecked():
            self.btn_guess.setText("영역 지정한 이미지로 찾기")
        self.btn_guess.clicked.connect(lambda *_a: self.start_guess(False))
        h.addWidget(self.ck_fast)
        h.addWidget(self.btn_guess, 1)
        self.btn_guess_panel = QPushButton("🧑‍⚖ AI 회의로 추측")
        self.btn_guess_panel.setMinimumHeight(32)
        self.btn_guess_panel.setToolTip("영상마다 장면 시트를 만들어, 이미지를 보는 AI(Qwen-VL·Claude·Gemini …)가 직접 보고 캐릭터 후보와 생김새를 말하면,\n"
                                        "글 전용 AI(GPT-OSS)가 그 묘사를 읽고 같은 이름 목록에서 의견을 냅니다. 서로의 이유를 보며 고치고, 합의된 캐릭터가 아래 검수 표에 들어갑니다.\n"
                                        "목록에 없는 캐릭터(이름 없는 보스 등)는 '(모름)' 줄에 생김새만 적어 둡니다. 토론 기록도 창으로 볼 수 있습니다.")
        self.btn_guess_panel.clicked.connect(lambda *_a: self.start_guess_panel())
        h.addWidget(self.btn_guess_panel, 1)
        h.addWidget(QLabel("낮은 확신 경고 기준"))
        h.addWidget(self.min_conf)
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
        g = CollapsibleBox("4) 검수 — 틀린 것은 고쳐 주세요")
        v = QVBoxLayout(g)
        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["장면", "AI 추측", "메시 (후보 / 검색)", "제외"])
        self.table.setIconSize(QSize(168, 96))
        self.table.setMinimumHeight(180)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for c, wd in ((0, 176), (1, 150), (3, 60)):
            self.table.setColumnWidth(c, wd)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setStretchLastSection(False)
        v.addWidget(Resizable(self.table, "vf_table", 180))
        # (예전의 '게임 속 이름'·'특징·패턴' 칸은 전부 흡수됐다: 글자 사전은 추측 때 자동 생성,
        #  이름·동작 설명으로 찾기는 '아는 정보' 칸과 '이름으로 찾기' 모드의 AI 확장 검색이 대신한다)
        h = QHBoxLayout()
        b = QPushButton("캐릭터 줄 직접 추가")
        b.setToolTip("AI가 놓친 캐릭터를 직접 넣습니다. 줄의 검색 칸에서 메시를 찾으세요.")
        b.clicked.connect(self.add_blank_row)
        h.addWidget(b)
        b = QPushButton("선택한 줄 삭제")
        b.setToolTip("표에서 줄을 하나 고른 뒤 누르면 그 줄을 지웁니다. 직접 추가했거나 잘못 인식된 줄을 치울 때 쓰세요.")
        b.clicked.connect(lambda *_a: self.delete_selected_row())
        h.addWidget(b)
        b = QPushButton("모두 지우기")
        b.clicked.connect(lambda *_a: self.clear_rows())
        h.addWidget(b)
        h.addStretch(1)
        self.btn_confirm = QPushButton("검수 확정 → 애니메이션 후보 보기")
        self.btn_confirm.setMinimumHeight(30)
        self.btn_confirm.clicked.connect(self.confirm)
        h.addWidget(self.btn_confirm)
        v.addLayout(h)
        self.choice_summary = QLabel("")
        self.choice_summary.setStyleSheet("color:#5ab0ff")
        v.addWidget(self.choice_summary)
        lay.addWidget(g)

        # 5) 후보 / 추출
        g = CollapsibleBox("5) 후보에서 골라 추출")
        v = QVBoxLayout(g)
        v.addWidget(QLabel("메시 (확정한 캐릭터 / 캐릭터 메쉬 검색 결과)"))
        self.mesh_list = _AssetList("mesh")
        self.mesh_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.mesh_list.setMinimumHeight(40)
        self.mesh_list.itemSelectionChanged.connect(self._mesh_sel_changed)
        self.mesh_list.itemDoubleClicked.connect(self._mesh_double_clicked)
        self.mesh_list.setToolTip("더블클릭 또는 오른쪽 뷰포트로 끌어다 놓기: 이 메시를 바로 뷰포트(타깃)로 엽니다 — 리깅(스켈레톤·스킨)이 든 GLB 로 뽑아서 바로 불러와요. 탭은 그대로 있습니다.")
        v.addWidget(Resizable(self.mesh_list, "vf_mesh_list", 40))
        v.addWidget(QLabel("↑ 더블클릭하거나 뷰포트로 끌어다 놓으면 그 메시가 바로 뷰포트에 열려요 (추출 없이 확인)"))
        h = QHBoxLayout()
        h.addWidget(QLabel("애니메이션 분류"))
        self.label = QComboBox()
        self.label.addItem("상관없음", "")
        for x in LABEL_CHOICES:
            self.label.addItem(x, x)
        self.label.currentIndexChanged.connect(self._refill_anims)
        h.addWidget(self.label)
        h.addWidget(HelpIcon("애니메이션 이름에서 알아낸 종류(공격·피격·사망 …)로 목록을 좁힙니다. '상관없음'이면 모두 보여 줍니다."))
        h.addWidget(QLabel("타수"))
        self.hits = QSpinBox()
        self.hits.setRange(0, 9)
        self.hits.setSpecialValueText("상관없음")
        self.hits.valueChanged.connect(self._refill_anims)
        h.addWidget(self.hits)
        h.addWidget(HelpIcon("연속 공격의 타수(몇 번 때리는지). 이름에서 알아낸 값이 있는 것만 맞춰 보여 줍니다. 상관없음 = 거르지 않음."))
        self.react_only = QCheckBox("🥊 피격·그로기·처형 계열만 보기")
        self.react_only.setToolTip("이름에 hit/react/stagger/groggy/execute/finisher/victim 같은 낱말이 든 애니메이션만 남깁니다 (상대 캐릭터의 '맞는 모션'을 찾을 때).")
        self.react_only.toggled.connect(lambda *_a: self._apply_anim_filter())
        h.addWidget(self.react_only)
        h.addStretch(1)
        v.addLayout(h)
        h = QHBoxLayout()
        h.addWidget(QLabel("설명으로 찾기"))
        self.anim_q = QLineEdit()
        self.anim_q.setPlaceholderText("예: 스핀 공격 / 내려찍기 / 점프 — 쉼표로 나누면 모두 맞는 것만 (예: 점프,내려찍기). 비우고 누르면 기본 목록으로")
        self.anim_q.setToolTip("동작 설명을 애니메이션 이름 낱말(spin, slam…)로 바꿔 검색합니다. 메시를 고르면 그 캐릭터 안에서, 안 고르면 전체에서 찾습니다.")
        self.anim_q.returnPressed.connect(self.search_anims_by_text)
        h.addWidget(self.anim_q, 1)
        self.anim_all = QCheckBox("모든 캐릭터에서")
        self.anim_all.setToolTip("끄면 위에서 고른(확정한) 캐릭터의 애니메이션 안에서만 찾습니다.")
        h.addWidget(self.anim_all)
        b = QPushButton("검색")
        b.clicked.connect(lambda *_a: self.search_anims_by_text())
        h.addWidget(b)
        v.addLayout(h)
        self.btn_panel = QPushButton("🧑‍⚖ AI 회의로 찾기… (여러 AI 가 영상과 목록을 보며 토론)")
        self.btn_panel.setToolTip("고른 영상 장면을 한 장의 시트로 붙여, 이미지를 보는 AI(Qwen-VL·Claude·Gemini …)가 직접 보고, 글 전용 AI(GPT-OSS)는 그 묘사를 읽고,\n"
                                  "모두 지금 목록(위쪽 %d개)에서 맞는 애니메이션을 고르고 서로의 이유를 보며 고칩니다. 합의 순위로 목록을 다시 줄 세웁니다." % MAX_PANEL_NAMES)
        self.btn_panel.clicked.connect(lambda *_a: self.open_panel())
        v.addWidget(self.btn_panel)
        h = QHBoxLayout()
        h.addWidget(QLabel("찾는 종류"))
        self.ck_k_monster = QCheckBox("몬스터·보스·NPC")
        self.ck_k_player = QCheckBox("플레이어블 캐릭터")
        for ck, key in ((self.ck_k_monster, "vf_kind_monster"), (self.ck_k_player, "vf_kind_player")):
            ck.setChecked(bool(self.cfg.get(key, True)))
            ck.toggled.connect(self._kinds_changed)
            h.addWidget(ck)
        self.ck_k_monster.setToolTip("경로에 player/pc/hero 같은 낱말이 없는 애니메이션. 끄면 몬스터 것은 검색·목록에서 빠집니다.")
        self.ck_k_player.setToolTip("경로에 player/pc/hero/fallen 같은 낱말이 든 공용 애니메이션. 이것만 켜면 플레이어 것만 훑어서 더 빠릅니다.")
        h.addStretch(1)
        v.addLayout(h)
        h = QHBoxLayout()
        h.addWidget(QLabel("목록에서 거르기"))
        self.anim_filter = QLineEdit()
        self.anim_filter.setPlaceholderText("🔍 지금 목록 안에서 글자로 바로 거르기 (쉼표·띄어쓰기 = 모두 포함, 예: character,attack)")
        self.anim_filter.setClearButtonEnabled(True)
        self.anim_filter.textChanged.connect(lambda *_a: self._apply_anim_filter())
        h.addWidget(self.anim_filter, 1)
        h.addWidget(HelpIcon("지금 목록 안에서 이름 글자로 바로 좁힙니다 (다시 검색하지 않음). 쉼표나 띄어쓰기로 나누면 모두 들어간 것만 남습니다."))
        self.anim_count = QLabel("")
        self.anim_count.setStyleSheet("color:#8a93a6")
        h.addWidget(self.anim_count)
        v.addLayout(h)
        self.anim_list = _AssetList("anim")
        self.anim_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.anim_list.setMinimumHeight(50)
        v.addWidget(Resizable(self.anim_list, "vf_anim_list", 50))
        self.anim_note = QLabel("")
        self.anim_note.setWordWrap(True)
        v.addWidget(self.anim_note)
        self.btn_verify = QPushButton("스켈레톤으로 확인 (정확도↑)")
        self.btn_verify.setToolTip("고른 메쉬와 후보 애니메이션 파일을 실제로 열어, 같은 스켈레톤에 연결되고 재생 가능한 것만 앞에 두고 아닌 것은 뺍니다.")
        self.btn_verify.clicked.connect(lambda *_a: self.verify_links())
        v.addWidget(self.btn_verify)
        h = QHBoxLayout()
        b = QPushButton("전체 선택")
        b.clicked.connect(lambda *_a: self._select_visible_anims())
        h.addWidget(b)
        b = QPushButton("선택 해제")
        b.clicked.connect(lambda *_a: self.anim_list.clearSelection())
        h.addWidget(b)
        self.btn_mrank = QPushButton("🎬 영상 동작과 비슷한 순 정렬")
        self.btn_mrank.setToolTip("영상에서 뽑은 3D 동작 파일(AnimRef 의 AI 모캡 .bvh 등)을 고르면, 지금 목록 위쪽 %d개를 임시로 뽑아서 팔다리 움직임이 얼마나 비슷한지로\n"
                                  "다시 줄 세웁니다. 이름이 비슷해도 동작이 다른 것, 이름이 달라도 동작이 같은 것을 가려냅니다. 좌우 반전(거울) 동작도 같이 봅니다." % MAX_MOTION_RANK)
        self.btn_mrank.clicked.connect(lambda *_a: self.rank_by_motion())
        h.addWidget(self.btn_mrank)
        h.addStretch(1)
        v.addLayout(h)
        ph = QHBoxLayout()
        self.btn_pair = QPushButton("🔗 고른 모션의 짝 찾기 (상대 쪽 모션 목록으로)")
        self.btn_pair.setToolTip("목록에서 모션 하나(예: 보스가 처형당하는 모션)를 고르고 누르면, 반대편 캐릭터(보스 ↔ 플레이어)의 모션 중\n"
                                 "길이가 비슷한(기본 ±30프레임) 것과 이름이 겹치는 것을 점수순으로 목록에 올립니다. 서로 맞물리는 연출(처형·패리 피니셔·잡기)에서 통합니다.\n"
                                 "필요하면 먼저 파일 안을 읽어 길이를 알아냅니다(처음엔 시간이 걸림).")
        self.btn_pair.clicked.connect(lambda *_a: self.find_pair())
        ph.addWidget(self.btn_pair, 1)
        ph.addWidget(QLabel("길이 오차(프레임)"))
        self.pair_tol = QSpinBox()
        self.pair_tol.setRange(1, 300)
        self.pair_tol.setValue(int(self.cfg.get("vf_pair_tol", 30)))
        ph.addWidget(self.pair_tol)
        ph.addWidget(HelpIcon("짝 찾기에서 '길이가 비슷하다'고 볼 프레임 차이 한계입니다. 기본 ±30프레임. 서로 맞물리는 연출은 길이가 거의 같아서, 작게 할수록 엄격합니다."))
        v.addLayout(ph)
        self.btn_view = QPushButton("선택한 애니메이션을 뷰포트로 열기 (리타겟 작업)")
        self.btn_view.setMinimumHeight(34)
        self.btn_view.setToolTip("고른 애니메이션(여러 개 가능)을 PSA 로 뽑아 소스 모션으로 엽니다. 뷰포트로 끌어다 놓아도 같고, 탭은 옮기지 않습니다. 클립 목록에서 바꿔 가며 볼 수 있습니다.\n"
                                 "같은 스켈레톤(같은 캐릭터)의 애니메이션만 함께 열립니다.")
        self.btn_view.clicked.connect(lambda *_a: self.open_in_viewport())
        v.addWidget(self.btn_view)
        self.anim_list.itemDoubleClicked.connect(self._anim_double_clicked)
        self.edit_panel = EditPanel("추출할 애니메이션 가공 (선택) — 늘리기 · fps · 구간 · 루프 · 키 압축", self.cfg, "vf_edit")
        self.edit_panel.setToolTip("켜면 추출한 PSA 옆에 '<이름>_edit.psa' 를 하나 더 만듭니다(원본은 그대로). 아무것도 안 고르면 가공하지 않습니다.")
        v.addWidget(self.edit_panel)
        h = QHBoxLayout()
        self.btn_extract_mesh = QPushButton("메시만 추출")
        self.btn_extract_mesh.setMinimumHeight(34)
        self.btn_extract_mesh.setToolTip("위에서 고른 메시만 뽑습니다 (애니메이션은 뽑지 않음).")
        self.btn_extract_mesh.clicked.connect(lambda *_a: self.extract("mesh"))
        h.addWidget(self.btn_extract_mesh)
        self.btn_extract_anim = QPushButton("애니메이션만 추출")
        self.btn_extract_anim.setMinimumHeight(34)
        self.btn_extract_anim.setToolTip("고른 애니메이션만 뽑습니다 (메시는 뽑지 않음). 가공 옵션을 켜면 _edit.psa 도 만듭니다.")
        self.btn_extract_anim.clicked.connect(lambda *_a: self.extract("anim"))
        h.addWidget(self.btn_extract_anim)
        self.btn_extract = QPushButton("메시 + 애니메이션 추출")
        self.btn_extract.setMinimumHeight(34)
        self.btn_extract.setToolTip("0번 탭의 형식/리깅/대상 프로그램/출력 폴더 설정을 그대로 씁니다. 파일로만 저장합니다.")
        self.btn_extract.clicked.connect(lambda *_a: self.extract("both"))
        h.addWidget(self.btn_extract)
        v.addLayout(h)
        lay.addWidget(g)
        lay.addStretch(1)
        fit_widths(inner)

    # ================================================================== 입력
    def _add_source(self, path: str, label: str = "", title: str = "") -> None:
        tag = "[%s] " % label if label else ""
        it = QListWidgetItem(tag + animlink.display_name(path, title))   # 'video.mp4' 같은 뜻 없는 파일 이름 대신 클립 이름
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
        start = animlink.default_clip_dir() or self.cfg.get("animref_dir", "")  # AnimRef 설정의 라이브러리/게임/clips
        d = QFileDialog.getExistingDirectory(self, "AnimRef 클립 폴더", start)
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

    def remove_sources(self) -> int:
        """목록에서 고른 원본만 뺀다. 빠진 개수를 돌려준다."""
        rows = sorted({self.src_list.row(it) for it in self.src_list.selectedItems()}, reverse=True)
        for r in rows:
            it = self.src_list.takeItem(r)
            if it is not None:
                self.clip_labels.pop(it.data(Qt.ItemDataRole.UserRole), None)
        self._update_src_note(prefix=("원본 %d개를 뺐습니다" % len(rows)) if rows else "")
        return len(rows)

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

    MODEL_KEY = {"claude": "vf_claude_model", "openai": "vf_oa_model", "ollama": "vf_ollama_model", "gptoss": "vf_gptoss_model"}

    def _preset(self):
        nm = self.oa_preset.currentData()
        return next((p for p in vision.OPENAI_PRESETS if p[0] == nm), vision.OPENAI_PRESETS[0])

    def _remember_key(self, *_a) -> None:
        kind = self._provider_kind()
        if kind in self._keys and not getattr(self, "_switching", False):
            self._keys[kind] = self.key.text()

    def _preset_changed(self, *_a) -> None:
        """서비스를 바꾸면 모델을 그 서비스의 기본 모델로 (직접 고친 모델이 다른 서비스 기본값이 아닐 때만 유지)."""
        if self._provider_kind() != "openai":
            return
        defaults = {p[2] for p in vision.OPENAI_PRESETS}
        if self.model.text().strip() in defaults | {""}:
            self.model.setText(self._preset()[2])
        self.oa_url.setPlaceholderText(self._preset()[1])
        self._engine_note_update()

    def _preset_info_text(self) -> str:
        nm = self._preset()[0]
        note, url = vision.PRESET_INFO.get(nm, ("", ""))
        return (note + ((' · <a href="%s">API 키 받는 곳</a>' % url) if url else "")) if note else ""

    def load_models(self) -> None:
        """서버의 모델 목록을 불러와 고르게 한다 (OpenAI 호환 서비스 전용)."""
        prov = self.provider()
        if prov.key != "openai":
            self.engine_note.setText("모델 목록은 '다른 AI' 방식에서 쓸 수 있습니다. Ollama 는 'ollama list' 로 확인하세요.")
            return
        self.win.run_async(lambda w: prov.list_models(), self._after_models, "모델 목록 불러오는 중")

    def _after_models(self, names) -> None:
        if not names:
            self.engine_note.setText("✖ 서버가 모델 목록을 주지 않았습니다. 서비스 사이트에서 모델 이름을 확인해 직접 입력하세요.")
            return
        ordered, likely = vision.sort_models(names)
        shown = [("👁 " if i < likely else "") + n for i, n in enumerate(ordered)]
        cur = self.model.text().strip()
        pick, ok = QInputDialog.getItem(self, "모델 고르기", "이미지를 볼 수 있어 보이는 모델(👁)이 위에 있습니다 — 이름으로만 추정이라 틀릴 수 있어요.\n전체 %d개" % len(ordered),
                                        shown, max(ordered.index(cur), 0) if cur in ordered else 0, False)
        if ok and pick:
            self.model.setText(pick.replace("👁 ", "", 1))
            self.engine_note.setText("모델을 '%s' 로 정했습니다. [연결 확인]으로 확인하세요." % self.model.text())

    def _engine_note_update(self) -> None:
        kind = self._provider_kind()
        if kind == "claude":
            note = "영상 이미지가 인터넷으로 Anthropic 에 전송되고 호출한 만큼 요금이 듭니다."
        elif kind == "openai":
            nm, url, _m, env = self._preset()
            local = "localhost" in (self.oa_url.text() or url)
            note = ("내 서버(%s)로 보냅니다 — 이미지를 볼 수 있는 모델이어야 해요." % (self.oa_url.text().strip() or url)) if local else \
                   "영상 이미지가 인터넷으로 %s 에 전송되고 호출한 만큼 요금이 듭니다. 이미지를 볼 수 있는 모델을 고르세요. (키는 환경변수 %s 로도 가능)" % (nm, env or "-")
        elif kind == "gptoss":
            note = ("OpenAI 의 열린 모델 GPT-OSS-20B 를 내 PC(Ollama)에서 돌립니다 — 무료, 인터넷으로 아무것도 보내지 않아요. 글 전용이라 '게임 속 이름/동작 설명으로 찾기'와 "
                    "AI 낱말 변환에 쓰이고, 영상 장면 인식에는 쓸 수 없습니다. 메모리 16GB 이상 권장, 모델 약 %dGB. [자동 설치·준비]로 한 번에 준비하세요." % vision.GPTOSS_SIZE_GB)
        else:
            note = "내 PC에서만 처리됩니다. Ollama 설치 후 'ollama pull %s' 로 모델을 받으세요. 그래픽카드가 약하면 느리고 덜 정확합니다." % vision.OLLAMA_MODELS[0]
        self.engine_note.setText(note)
        if getattr(self, "oa_info", None) is not None:
            self.oa_info.setText(self._preset_info_text() if kind == "openai" else "")
            self.oa_info.setVisible(kind == "openai")

    def _engine_changed(self, *_a) -> None:
        kind = self._provider_kind()
        self._switching = True
        try:
            if kind == "openai":
                self.model.setText(self.cfg.get("vf_oa_model") or self._preset()[2])
                self.oa_url.setPlaceholderText(self._preset()[1])
            elif kind == "claude":
                self.model.setText(self.cfg.get("vf_claude_model") or vision.CLAUDE_MODELS[0])
            elif kind == "gptoss":
                self.model.setText(self.cfg.get("vf_gptoss_model") or vision.GPTOSS_MODEL)
            else:
                self.model.setText(self.cfg.get("vf_ollama_model") or vision.OLLAMA_MODELS[0])
            if kind in self._keys:
                self.key.setText(self._keys[kind])
                self.key_label.setText("Claude API 키" if kind == "claude" else "API 키")
                self.key.setPlaceholderText("sk-ant-… (비워 두면 환경변수 ANTHROPIC_API_KEY 사용)" if kind == "claude"
                                            else "서비스에서 받은 키 (내 서버는 비워도 됨)")
        finally:
            self._switching = False
        self.key_row.setVisible(kind in ("claude", "openai"))
        self.oa_row.setVisible(kind == "openai")
        self.btn_pull.setVisible(kind in ("ollama", "gptoss"))
        self.btn_setup.setVisible(kind in ("ollama", "gptoss"))
        self._engine_note_update()

    def _save_settings(self) -> None:
        kind = self._provider_kind()
        self._remember_key()
        self.cfg["vf_engine"] = kind
        self.cfg[self.MODEL_KEY.get(kind, "vf_ollama_model")] = self.model.text().strip()
        self.cfg["vf_oa_preset"] = self.oa_preset.currentData() or vision.OPENAI_PRESETS[0][0]
        self.cfg["vf_oa_url"] = self.oa_url.text().strip()
        self.cfg["vf_save_key"] = bool(self.save_key.isChecked())
        for k, cfgkey in (("claude", "vf_claude_key"), ("openai", "vf_openai_key")):
            if self.save_key.isChecked() and self._keys.get(k, "").strip():
                self.cfg[cfgkey] = self._keys[k].strip()
            else:
                self.cfg.pop(cfgkey, None)
        self.cfg["vf_per"] = int(self.per.value())
        self.cfg["vf_hint"] = self.hint.text().strip()
        self.cfg["vf_known"] = self.known.toPlainText()
        self.cfg["vf_filter"] = self.filter.text().strip()
        self.cfg["vf_min_conf"] = int(self.min_conf.value())
        bridge.save_config(self.cfg)

    def provider(self) -> vision.Provider:
        self._save_settings()
        kind = self._provider_kind()
        cfg = dict(self.cfg)
        cfg["vf_claude_key"] = self._keys.get("claude", "").strip()  # 저장 여부와 상관없이 지금 입력한 키를 쓴다
        cfg["vf_openai_key"] = self._keys.get("openai", "").strip()
        cfg[self.MODEL_KEY.get(kind, "vf_ollama_model")] = self.model.text().strip()
        return vision.make_provider(kind, cfg)

    def check_engine(self) -> None:
        prov = self.provider()
        self.win.run_async(lambda w: prov.check(), self._after_check, "연결 확인 중")

    def _after_check(self, res) -> None:
        ok, msg, _names = res
        self.engine_note.setText(("✔ " if ok else "✖ ") + msg)

    def _engine_picked(self, *_a) -> None:
        """GPT-OSS/Ollama 를 직접 고르면, 아직 준비 안 됐을 때 바로 설치할지 묻는다."""
        if self._provider_kind() not in ("ollama", "gptoss"):
            return
        prov = self.provider()

        def job(w):
            return (prov.check()[0], bool(setup_mod.find_ollama()))

        self.win.run_async(job, self._after_pick_check, "AI 준비 상태 확인 중")

    def _after_pick_check(self, res) -> None:
        ready, _installed = res
        if ready:
            self.engine_note.setText("✔ 이미 준비되어 있습니다.")
            return
        r = QMessageBox.question(self, "AI 설치", "이 AI 를 쓰려면 Ollama 와 모델이 필요하고 아직 준비되지 않았습니다.\n"
                                 "지금 자동으로 설치할까요? (필요한 것만 받습니다 — 모델은 수 GB, 한 번만)",
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if r == QMessageBox.StandardButton.Yes:
            self.setup_ollama()

    def setup_ollama(self) -> None:
        prov = self.provider()
        if not isinstance(prov, vision.OllamaProvider):
            return

        def job(w):
            return setup_mod.ensure_ready(prov, lambda pct, st: w.progress.emit(pct, 100, st[:60]))

        self.win.run_async(job, lambda m: self.engine_note.setText("✔ " + m), "AI 설치·준비 중 (%s)" % prov.model)

    def pull_model(self) -> None:
        prov = self.provider()
        if not isinstance(prov, vision.OllamaProvider):
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
        self._ensure_names(a, w)   # 글자 사전(.locres)이 없으면 자동으로 만든다 (캐릭터 이름 연결에 필요)
        if not force:
            idx = self.index or assetidx.load_index(INDEX_CACHE, game)
            if idx:
                try:
                    self._lore_cards()     # 캐시된 목록으로 시작해도 도감·분류는 워밍해 둔다 (여긴 작업 스레드)
                    self._cls_map(idx)
                except Exception:  # noqa: BLE001
                    pass
                return idx
        w.progress.emit(0, 1, "게임 파일에서 이름 목록 읽는 중 (처음 한 번은 오래 걸립니다)")
        paths = extractor.list_assets(helper, game, ue, aes, usmap, LIST_PATTERN, 200000)
        meshes = [p for p in paths if MESH_RX.search(p)]
        # 리깅 없는 캐릭터 모델링(SM_*): 게임 전체 소품까지 담으면 너무 커서 캐릭터 폴더/플레이어 이름이 든 것만
        statics = [p for p in paths if STATIC_RX.search(p) and (set(assetidx._dir_parts(p)) & layout.MARKERS or assetidx.is_player_path(p))]
        ms = set(meshes) | set(statics)
        anims = [p for p in paths if p not in ms and ANIM_RX.search(p) and not STATIC_RX.search(p)]
        if not meshes:
            raise RuntimeError("메시(SK_*)를 하나도 찾지 못했습니다. 0번 탭에서 UE 버전/mappings 를 확인하세요.")
        idx = assetidx.AssetIndex(meshes, anims, statics)
        assetidx.save_index(INDEX_CACHE, idx, game)
        try:
            w.progress.emit(0, 1, "캐릭터 종류 분류·도감 읽는 중 (목록 만들 때 한 번만)")
            self._lore_cards()      # 도감(27MB 사전)을 작업 스레드에서 미리 읽어 둔다 — UI 가 얼지 않게
            self._cls_map(idx)      # 분류를 지금 계산해 파일로 저장 — 이후 찾기에서는 바로 읽는다
        except Exception:  # noqa: BLE001 - 분류는 보조
            pass
        return idx

    def _catalog(self, idx: assetidx.AssetIndex) -> "charcatalog.Catalog":
        """에셋 목록(+ 있으면 글자 사전)으로 만든 캐릭터 카탈로그. 같은 목록이면 다시 만들지 않는다."""
        try:
            mt = os.path.getmtime(NAMES_CACHE) if os.path.isfile(NAMES_CACHE) else 0.0
        except OSError:
            mt = 0.0
        key = (id(idx), mt)
        c = self.__dict__.get("_charcat_memo")
        if c and c[0] == key:
            return c[1]
        book = self.name_book()
        cat = charcatalog.Catalog.build([m.path for m in idx.meshes], idx.anims, book.entries if book else ())
        self._charcat_memo = (key, cat)
        return cat

    def _entities(self, idx: assetidx.AssetIndex, hint: str = "") -> "entstore.EntityStore":
        """게임별 엔티티 사전: 카탈로그로 채우고(코드명·locres 이름), 검수자 학습을 계속 쌓는다."""
        game = (hint or (self.win.ex_gname.text().strip() if hasattr(self.win, "ex_gname") else "") or "game")
        memo = self.__dict__.get("_entstore_memo")
        if memo is not None and memo[0] == (id(idx), game):
            return memo[1]
        st = entstore.EntityStore.load(game)
        try:
            st.from_catalog(self._catalog(idx))
            self._ensure_unitlinks(st, game)   # 게임 유닛 데이터(이름 ↔ 코드명 정답표)를 게임마다 한 번 읽어 넣는다
            st.save()
        except Exception:  # noqa: BLE001 - 사전은 보조 기능: 채우기 실패가 추측을 막지 않게
            pass
        self._entstore_memo = ((id(idx), game), st)
        return st

    def _lore_cards(self, build: bool = True):
        """게임 도감(일화·시구) 카드 — names.json 이 바뀌지 않는 한 한 번만 만든다.
        build=False (UI 스레드용): 이미 읽어 둔 것만 쓴다 — 27MB 파싱으로 화면이 얼지 않게."""
        try:
            mt = os.path.getmtime(NAMES_CACHE) if os.path.isfile(NAMES_CACHE) else 0.0
        except OSError:
            mt = 0.0
        memo = self.__dict__.get("_lore_memo")
        if isinstance(memo, tuple) and memo[0] == mt:
            return memo[1]
        if not build:
            return {}
        cards = {}
        try:
            from ..core.identify import unitlink
            with open(NAMES_CACHE, "r", encoding="utf-8") as f:
                cards = unitlink.journal_cards((json.load(f) or {}).get("entries") or [])
        except Exception:  # noqa: BLE001 - 도감이 없는 게임
            cards = {}
        self._lore_memo = (mt, cards)
        return cards

    def _warm_lore_bg(self) -> None:
        """도감(27MB 사전)을 UI 를 막지 않고 뒤에서 읽는다 — 끝나면 팁과 [📖 도감] 버튼이 켜진다."""
        if self.__dict__.get("_lore_warming") or not os.path.isfile(NAMES_CACHE) or self._lore_cards(build=False):
            return
        self._lore_warming = True
        import threading

        def run() -> None:
            try:
                self._lore_cards()
            except Exception:  # noqa: BLE001 - 워밍 실패는 조용히
                pass
            finally:
                self._lore_warming = False

        threading.Thread(target=run, daemon=True).start()
        self._poll_lore()

    def _poll_lore(self) -> None:
        if self._lore_cards(build=False) or not self.__dict__.get("_lore_warming"):
            self._update_known_tip()
            return
        QTimer.singleShot(700, self._poll_lore)

    def _update_known_tip(self) -> None:
        """게임별 맞춤 팁: 이 게임의 도감(일화)·정답표를 확인해서, 실제 예시를 들어 '어떻게 적으면 바로 찾는지'를 보여 준다."""
        try:
            cards = self._lore_cards(build=False)   # UI 스레드: 작업(job)이 읽어 둔 것만 — 아직이면 팁은 다음 갱신 때
            game = ((self.hint.text().strip() if hasattr(self, "hint") else "")
                    or (self.win.ex_gname.text().strip() if hasattr(self.win, "ex_gname") else "") or "game")
            linked = 0
            try:
                marker = os.path.join(bridge.CONFIG_DIR, "entities", entstore._safe(game) + "_unitlink.json")
                with open(marker, "r", encoding="utf-8") as f:
                    linked = int((json.load(f) or {}).get("linked") or 0)
            except (OSError, ValueError, TypeError):
                pass
            lines = []
            if cards:        # ① 도감이 있는 게임: 실제 일화 예시로 안내
                lines.append("💡 이 게임('%s') 팁: 도감 %d종 발견 — 게임이 몬스터 특징을 '글'로 적어 둡니다. 적은 낱말이 이 글과 겹치면 AI 없이 즉시 찾습니다." % (game, len(cards)))
                ex = self._lore_example(cards)
                if ex:
                    lines.append("   예) 이 게임의 도감은 이런 식입니다 — %s" % ex)
                    lines.append("   → 저런 글에 나올 법한 낱말(종족·옷차림·무기·장소)이나 '인게임 이름 그대로'를 적으세요.")
            else:            # 도감이 없는 게임: 있는 내부 데이터 순서로 폴백해서 맞춤 안내
                book = None
                try:
                    book = self.name_book()
                except Exception:  # noqa: BLE001
                    book = None
                if book is not None and len(book):     # ② 글자 사전(locres)은 있는 게임
                    ex = ""
                    for e in book.entries:
                        if str(e[0]).startswith("ko") and e[3] and 2 <= len(str(e[3])) <= 12:
                            ex = str(e[3])
                            break
                    if not ex:
                        for e in book.entries:
                            if str(e[0]) == "en" and e[3] and 3 <= len(str(e[3])) <= 16:
                                ex = str(e[3])
                                break
                    lines.append("💡 이 게임('%s') 팁: 도감 글은 없지만 게임 글자 사전 %d개를 읽어 뒀습니다 — 화면/자막의 '인게임 표기 그대로' 적는 게 가장 정확합니다." % (game, len(book)))
                    if ex:
                        lines.append("   예) 이 게임의 표기는 '%s' 같은 식 — 보이는 그대로 적으세요. 별명·줄임말은 AI 확장으로 넘어가 느립니다." % ex)
                else:
                    gl = None
                    try:
                        gl = self.glossary(game)
                    except Exception:  # noqa: BLE001
                        gl = None
                    if gl is not None and gl.entries:   # ③ 뜻 풀이 사전만 있는 게임 (리소스 이름이 병음/암호)
                        k0 = next(iter(sorted(gl.entries)))
                        e0 = gl.entries.get(k0) or {}
                        mean = " / ".join(x for x in (e0.get("ko"), e0.get("en")) if x)
                        lines.append("💡 이 게임('%s') 팁: 도감·이름 사전이 없고 리소스 이름이 암호 같은 게임입니다 — 뜻 풀이 %d개로 연결하니 '종족 + 특징(무기·옷·색)'을 적으세요." % (game, len(gl.entries)))
                        if mean:
                            lines.append("   예) 이 게임의 리소스 '%s' = %s → 이런 '뜻'에 해당하는 낱말로 적으면 걸립니다." % (k0, mean))
                    else:                               # ④ 아무 사전도 없는 게임: 파일/폴더 이름이 전부
                        idx0 = self.index or self._load_index_cached()
                        folder_ex = ""
                        if idx0:
                            from ..core import classify as _cls
                            for mm in idx0.meshes:
                                f0 = _cls.folder_of(mm.path)
                                if re.fullmatch(r"[A-Za-z][A-Za-z_]{3,24}", f0 or ""):
                                    folder_ex = f0
                                    break
                        if idx0:
                            lines.append("💡 이 게임('%s') 팁: 도감·사전 데이터가 없어 '파일 이름 검색'이 기본입니다 — 영어 이름/종족 낱말(wolf, golem …)로 적는 게 잘 걸립니다." % game)
                            if folder_ex:
                                lines.append("   예) 이 게임의 캐릭터 폴더는 '%s' 같은 식 — 이런 영어 낱말로 적으세요." % folder_ex)
            if linked:
                lines.append("   정답표: 게임 데이터에서 이름 ↔ 코드명 %d쌍을 읽어 뒀습니다 — 인게임 이름이면 즉시 확정됩니다." % linked)
            self.known_tip.setText("\n".join(lines))
            self.known_tip.setVisible(bool(lines))
            if hasattr(self, "btn_lore"):
                self.btn_lore.setVisible(bool(cards))
                if cards:
                    self.btn_lore.setText("📖 게임 도감 (%d종)" % len(cards))
        except Exception:  # noqa: BLE001 - 팁은 보조
            self.known_tip.setVisible(False)

    def _open_lore(self) -> None:
        """도감 브라우저: 몬스터를 고르면 그 이름으로 '이름·설명으로 찾기'를 바로 돌린다. (큰 사전 파싱은 뒤에서)"""
        cards = self._lore_cards(build=False)
        if cards:
            self._show_lore(cards)
            return
        self.win.run_async(lambda w: self._lore_cards(), self._show_lore, "게임 도감 읽는 중 (처음 한 번)")

    def _show_lore(self, cards) -> None:
        if not cards:
            QMessageBox.information(self, "게임 도감", "이 게임에서 도감 데이터를 찾지 못했습니다. (글자 사전을 만든 뒤에 생깁니다 — 찾기를 한 번 돌리면 자동)")
            return
        dlg = _LoreDialog(self, cards)
        if dlg.exec() and dlg.picked:
            self.rb_mode_char.setChecked(True)     # 이름·설명으로 찾기 모드로
            self.char_q.setText(dlg.picked)
            self.start_namefind()

    @staticmethod
    def _lore_example(cards) -> str:
        """도감 카드 하나를 예시로: '이 게임은 몬스터를 이런 식으로 설명한다'."""
        best = ""
        for _i, c in sorted(cards.items()):
            nm = c["names"].get("ko") or c["names"].get("en") or ""
            tx = (c["texts"].get("ko") or c["texts"].get("en") or "").strip().replace("\n", " ")
            if nm and len(tx) >= 20:
                best = "'%s': \"%s…\"" % (nm, tx[:56])
                if c["names"].get("ko"):
                    break
        return best

    def _ensure_unitlinks(self, st: "entstore.EntityStore", game: str) -> None:
        """게임이 데이터로 적어 둔 '유닛 ↔ 표시 이름' 연결(CardDesc ID)을 읽어 엔티티 사전에 넣는다 — 게임마다 한 번.

        이게 있으면 '아는 정보'에 인게임 이름(금지장로/Elder Jinchi …)을 그대로 적었을 때
        AI 확장 없이 완전 일치로 즉시 코드명이 나온다. 옛 추출기(json 명령 없음)·테이블 없는 게임은 조용히 건너뛴다."""
        from ..core.identify import unitlink
        marker = os.path.join(bridge.CONFIG_DIR, "entities", entstore._safe(game) + "_unitlink.json")
        stamp = []
        for p in (NAMES_CACHE,):
            try:
                stamp.append(round(os.path.getmtime(p), 2) if os.path.isfile(p) else 0)
            except OSError:
                stamp.append(0)
        try:
            with open(marker, "r", encoding="utf-8") as f:
                if (json.load(f) or {}).get("stamp") == stamp:
                    return
        except (OSError, ValueError):
            pass
        a = self.__dict__.get("_last_args")      # 워커 스레드에서 대화상자(_extract_args)를 띄우지 않게, 시작할 때 담아 둔 값
        if a is None or not os.path.isfile(NAMES_CACHE):
            return
        helper, gdir, ue, aes, usmap = a
        units = [p for p in extractor.list_assets(helper, gdir, ue, aes, usmap, r"(?i)/units?/[^/]*\.uasset", 4000)
                 if os.path.basename(p).lower().startswith("unit") and "preview" not in os.path.basename(p).lower()]
        n_link = 0
        if units:
            import tempfile as _tf
            with _tf.TemporaryDirectory(prefix="mb_unit_") as td:
                files = extractor.export_json(helper, gdir, ue, aes, usmap, units[:2000], td)
                texts = {}
                by_stem = {os.path.splitext(os.path.basename(f))[0].lower(): f for f in files}
                for p in units:
                    f = by_stem.get(re.sub(r"[^\w-]+", "_", os.path.splitext(os.path.basename(p))[0]).lower())
                    if not f:
                        continue
                    try:
                        with open(f, "r", encoding="utf-8", errors="replace") as fh:
                            texts[p] = fh.read(2_000_000)
                    except OSError:
                        continue
                with open(NAMES_CACHE, "r", encoding="utf-8") as fh:
                    entries = (json.load(fh) or {}).get("entries") or []
                n_link = unitlink.apply_to_store(st, unitlink.build_links(texts, entries))
        try:
            os.makedirs(os.path.dirname(marker), exist_ok=True)
            with open(marker, "w", encoding="utf-8") as f:
                json.dump({"stamp": stamp, "linked": n_link}, f)
        except OSError:
            pass

    def _owned_anims(self, idx: assetidx.AssetIndex, mesh_path: str):
        """캐릭터 카탈로그에서 이 메시가 어느 캐릭터인지 알면, 그 캐릭터 폴더의 애니메이션 경로들 (모르면 None -> 예전처럼 이름 유사도만)."""
        try:
            cat = self._catalog(idx)
            if len(cat.all()) < charcatalog.MIN_CHARS:
                return None
            ch = cat.of_path(mesh_path)
        except Exception:  # noqa: BLE001 - 카탈로그가 안 만들어져도 찾기는 계속
            return None
        return list(ch.anims) if ch is not None and ch.anims else None

    def _moves(self, idx: assetidx.AssetIndex, mesh_path: str):
        """이 메시의 캐릭터 폴더 애니메이션을 '동작' 단위로 묶은 목록 (캐릭터를 모르면 None)."""
        try:
            cat = self._catalog(idx)
            if len(cat.all()) < charcatalog.MIN_CHARS:
                return None
            ch = cat.of_path(mesh_path)
            if ch is None or not ch.anims:
                return None
            return movecatalog.build(ch.anims, [ch.leaf])
        except Exception:  # noqa: BLE001 - 묶기에 실패해도 예전 목록은 그대로 보여 준다
            return None

    @staticmethod
    def _group_by_moves(cands, mc, label_cat=None):
        """후보를 동작 묶음 순서로 다시 세운다: 몽타주 사본·재생 불가 파일은 빼고, 같은 동작의 시작/반복/끝·번호를 붙여 놓고, 짝(플레이어 쪽)을 표시한다.
        분류(label_cat)를 골랐으면 그 분류로 읽히는 동작을 먼저. 캐릭터 폴더 밖의 후보는 그 뒤에 원래 순서로."""
        hidden = mc.hidden()
        by = {c.path: c for c in cands}
        order = [p for p in mc.order() if p in by]
        if label_cat:
            hit = [p for p in order if assetidx.category_scores(p).get(label_cat, 0.0) > 0]
            order = hit + [p for p in order if p not in set(hit)]
        out = []
        for p in order:
            c, clip = by[p], mc.clips[p]
            why = "동작 〔%s〕" % clip.label()
            pr = mc.partners.get(p)
            if pr:
                why += " · 짝: " + os.path.basename(pr[0]).split(".")[0]
            out.append(assetidx.AnimCandidate(p, c.score, why))
        inside = set(order) | hidden
        out += [c for c in cands if c.path not in inside]
        return out, len([c for c in cands if c.path in hidden])

    def _vocab(self, idx: assetidx.AssetIndex, limit: int, pat: str, style: str = "raw"):
        """AI에게 알려줄 이름 목록과 안내 문구.
        style: 'lines' = 캐릭터 카탈로그 줄('<ID> = <게임 속 이름> [종류]', 결과의 ids 가 True) / 'names' = 카탈로그의 대표 메시 이름 / 'raw' = 예전 방식(메시 이름 알파벳순).
        카탈로그는 메시 413종을 캐릭터 100여 명으로 묶어 자르지 않고 전부 알린다 (예전엔 120종만 알려서 뒤쪽 캐릭터는 AI 가 아예 볼 수 없었다)."""
        notes: List[str] = []
        if style in ("lines", "names"):
            try:
                cat = self._catalog(idx)
                chars = cat.all()
            except Exception as e:  # noqa: BLE001 - 카탈로그가 안 만들어져도 예전 방식으로 계속
                chars, cat = [], None
                notes.append("캐릭터 카탈로그를 만들지 못해 예전 방식으로 진행합니다: %s" % e)
            if cat is not None and len(chars) >= charcatalog.MIN_CHARS:
                if pat:
                    try:
                        chars = [c for c in chars if assetidx.filter_paths(c.meshes, pat)]
                    except re.error as e:
                        notes.append("이름 필터가 올바르지 않아 무시했습니다: %s" % e)
                if len(chars) > limit:
                    notes.append("캐릭터 %d명 중 %d명만 AI에게 알렸습니다 (보스 > 미니보스 > 일반 > NPC 순). '이름 필터'로 줄이면 정확해집니다." % (len(chars), limit))
                    chars = chars[:limit]
                named = sum(1 for c in chars if c.names)
                notes.append("캐릭터 %d명을 전부 AI에게 알렸습니다 (게임 속 이름이 있는 캐릭터 %d명)." % (len(chars), named) if len(chars) <= limit else "")
                out = _Vocab(cat.vocab_lines(chars=chars) if style == "lines" else cat.vocab_names(chars=chars))
                out.ids = style == "lines"
                return out, [n for n in notes if n]
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
                notes.append("메시가 많아 캐릭터 폴더(monster/enemy/boss)만 후보로 썼습니다 (%d종)." % len(names))
        if len(names) > limit:
            notes.append("이름 %d종 중 %d종만 AI에게 알렸습니다. '이름 필터'로 줄이면 정확해집니다." % (len(names), limit))
        return names, notes

    def _mode_changed(self, *_a) -> None:
        on = bool(self.rb_mode_char.isChecked())
        img = bool(self.rb_mode_img.isChecked())
        self.cfg["vf_mode_char"] = on
        self.cfg["vf_mode"] = "char" if on else ("image" if img else "video")
        self.char_panel.setVisible(on)
        self.btn_guess.setText("캐릭터 메쉬 목록 열기" if on else ("영역 지정한 이미지로 찾기" if img else "캐릭터 추측 시작"))
        self.btn_guess_panel.setVisible(not on and not img)
        if on:
            if self._load_index_cached():
                self.show_char_meshes()
            else:
                self.status.setText("캐릭터 메쉬 찾기: 에셋 이름 목록이 아직 없어요. '캐릭터 메쉬 목록 열기'를 누르면 게임 파일에서 읽어 옵니다.")
        elif self.char_mode:
            self.char_mode = False
            self.confirmed = []
            self.mesh_list.clear()
            self.anim_list.clear()
            for ck in (self.ck_k_monster, self.ck_k_player):  # 종류 체크도 다시 둘 다
                ck.blockSignals(True)
                ck.setChecked(True)
                ck.blockSignals(False)
            self.status.setText("몬스터 찾기로 돌아왔습니다.")

    def _char_player_toggled(self, on: bool) -> None:
        for ck, v in ((self.ck_k_monster, not on), (self.ck_k_player, True)):  # 애니메이션 종류도 같이
            ck.blockSignals(True)
            ck.setChecked(v)
            ck.blockSignals(False)
        self.show_char_meshes()

    def start_chars(self, force_index: bool = False) -> None:
        """AI 추측 없이 캐릭터 메쉬 검색 목록을 연다 (에셋 이름 목록이 없으면 먼저 읽는다)."""
        if not force_index and self._load_index_cached():
            self.show_char_meshes()
            return
        a = self.win._extract_args()
        if a is None:
            return

        def job(w):
            return self._ensure_index(a, w, force_index)

        def done(idx):
            self.index = idx
            self._update_known_tip()
            self.show_char_meshes()

        self.win.run_async(job, done, "에셋 이름 목록 읽는 중")

    def start_namefind(self) -> None:
        """AI 확장 검색: 유저가 아는 이름(금지장로)을 원어·음차·지역 코드·모티프 코드명으로 확장해 후보 폴더를 추린다."""
        clue = self.char_q.text().strip()
        if not clue:
            QMessageBox.information(self, "AI 확장 검색", "찾을 캐릭터 이름을 검색 칸에 적어 주세요 (예: 금지장로).")
            return
        a = self.win._extract_args()
        if a is None:
            return
        self._last_args = a
        prov = self.provider()
        hint = self.hint.text().strip() or (self.win.ex_gname.text().strip() if hasattr(self.win, "ex_gname") else "")

        def job(w):
            idx = self._ensure_index(a, w, False)
            w.progress.emit(0, 3, "에셋 목록 준비됨 (메시 %d · 애니 %d) — 엔티티 사전 여는 중" % (len(idx.meshes), len(idx.anims)))
            store = self._entities(idx, hint)
            # ⓪ 정답표/사전 '완전 일치'면 AI 를 아예 부르지 않는다 — 게임 데이터가 이미 이름 ↔ 코드명을 알고 있으므로 즉시.
            #    (도감에서 고른 이름, 인게임 표기 그대로 적은 이름이 여기로 끝난다. 유닛 정답표는 [추출기 업데이트] 후 생김.)
            direct = []
            for hh in store.find(clue, limit=3):
                if hh.score < 0.93:
                    continue
                for code in hh.entity.codenames:
                    cl = "/" + (code or "").lower() + "/"
                    ms = [m.path for m in idx.meshes if cl in m.path.lower()]
                    if not ms:
                        continue
                    na = sum(1 for a2 in idx.anims if cl in (a2 or "").lower() or (code or "").lower() in (a2 or "").lower().rsplit("/", 1)[-1])
                    if not any(d["folder"].lower() == code.lower() for d in direct):
                        direct.append({"folder": code, "score": round(min(hh.score, 1.0), 3),
                                       "why": "게임 데이터 일치 — '%s'" % clue, "meshes": ms, "anims": na})
            if direct:
                w.progress.emit(2, 3, "게임 데이터(정답표/사전)에서 즉시 일치 — AI 확장 생략")
                return {"candidates": direct, "expansion": {}, "idx": idx, "clue": clue,
                        "note": "게임 데이터 정답표로 즉시 일치 (AI 확장 생략 — 0.1초 미만)"}
            w.progress.emit(1, 3, "AI 에게 '%s' 확장 요청 중 (원어 정식명·병음 음차·지역 이니셜·모티프 코드명)" % clue)
            res = identify_namefind.find(prov.ask_text, clue, hint, [m.path for m in idx.meshes], idx.anims, store)
            exp_ = res.get("expansion") or {}
            got_ = ", ".join((exp_.get("romanized") or []) + (exp_.get("codenames") or []) + (exp_.get("initials") or []))[:120]
            w.progress.emit(2, 3, ("확장 결과: %s → 폴더 %d개 점수 매김, 동작 이름으로 교차 확인 중" % (got_ or "(없음)", len(res.get("candidates") or []))))
            try:
                # 도감 일화 검색: 적은 글('흰 옷 늑대' 같은 특징 문구)이 게임 도감의 설명과 겹치면 그 몬스터를 후보에 더한다
                cards_n = self._lore_cards()
                for h_ in (identify_unitlink.search_lore(clue, cards_n) if cards_n else [])[:3]:
                    if h_["score"] < 0.5:
                        continue
                    for hh_ in store.find(h_["name"], limit=1):
                        if hh_.score < 0.9:
                            continue
                        for code_ in hh_.entity.codenames:
                            hit_ = next((c for c in res["candidates"] if c["folder"].lower() == code_.lower()), None)
                            if hit_ is not None:
                                hit_["score"] = round(min(hit_["score"] + 0.2, 1.0), 3)
                                hit_["why"] += " + 도감 일화 '%s'" % h_["name"]
                            else:
                                res["candidates"].append({"folder": code_, "score": 0.9,
                                                          "why": "도감 일화 — '%s' (%s)" % (h_["name"], ",".join(h_["words"])[:40]),
                                                          "meshes": [], "anims": 0})
                    res.setdefault("note", "")
                res["candidates"].sort(key=lambda d: -d["score"])
            except Exception:  # noqa: BLE001 - 도감 검색은 보조
                pass
            try:
                # 같은 입구에서 '동작 설명'(7연속 스핀 공격 …)으로도 찾는다 — 두 엔진이 같은 캐릭터를 지목하면 가산
                _c, mons, _h, mq = motionsearch.find_meshes_by_motion(idx, clue)
                for m_ in mons:
                    hit = next((c for c in res["candidates"] if c["folder"].lower() == m_.name.lower()), None)
                    if hit is not None:
                        hit["score"] = round(min(hit["score"] + 0.15, 1.0), 3)
                        hit["why"] += " + 동작 이름(%s)" % m_.why
                    else:
                        res["candidates"].append({"folder": m_.name, "score": round(min(m_.score * 0.6, 0.9), 3),
                                                  "why": "동작 이름 — " + m_.why, "meshes": [], "anims": len(m_.hits)})
                if mons and mq.describe():
                    res["note"] = ((res.get("note") or "") + " · 동작으로 알아들음: %s" % mq.describe()).strip(" ·")
                res["candidates"].sort(key=lambda d: -d["score"])
                res["candidates"] = res["candidates"][:10]
            except Exception:  # noqa: BLE001 - 동작 검색은 보조
                pass
            res["idx"], res["clue"] = idx, clue
            return res

        self.win.run_async(job, self._after_namefind, "AI 확장 검색 중", cancellable=True)

    def _after_namefind(self, res) -> None:
        self.index = res.get("idx") or self.index
        self._update_known_tip()
        cands = res.get("candidates") or []
        exp = res.get("expansion") or {}
        if not cands:
            self.char_note.setText("'%s' 후보를 못 찾았습니다. %s\n확장 결과: %s" % (
                res.get("clue"), res.get("note") or "", ", ".join(exp.get("romanized", []) + exp.get("codenames", []))[:200]))
            return
        top = cands[0]
        lines = ["🌐 '%s' 후보 %d개 — 가장 유력: %s (%.0f%%, %s)" % (res.get("clue"), len(cands), top["folder"], top["score"] * 100, top["why"])]
        for c in cands[1:5]:
            lines.append("· %s (%.0f%%, %s, 애니 %d)" % (c["folder"], c["score"] * 100, c["why"], c["anims"]))
        if res.get("note"):
            lines.append("AI: " + res["note"])
        # 리스트업 즉시 후보 자동 등록: 찾은 '모든' 후보 캐릭터의 리깅 메시(뷰포트에서 열 수 있는 것)를
        # 후보 목록에 바로 올리고 전부 선택한다 — 애니메이션 후보도 같이 나온다.
        idx = self.index
        self.char_mode = True
        self.mesh_list.clear()
        seen, n_items = set(), 0

        def _fill(apply_cls: bool) -> None:
            nonlocal n_items
            for c in cands:
                paths = c.get("meshes") or ([m.path for m in idx.meshes if ("/" + c["folder"].lower() + "/") in m.path.lower()] if idx else [])
                for p in paths:
                    m = idx.mesh(p) if idx else None
                    if p in seen or m is None or not m.rigged or (apply_cls and not self._class_ok(p)):   # 인덱스에 있는 리깅 메시만 + 찾을 종류 체크
                        continue
                    seen.add(p)
                    it = QListWidgetItem("[%d%% %s] %s    (%s)" % (round(c["score"] * 100), c["folder"], m.name, p))
                    it.setData(Qt.ItemDataRole.UserRole, p)
                    it.setToolTip("%s — %s\n%s\n더블클릭하면 뷰포트(타깃)로 바로 열립니다" % (c["folder"], c["why"], p))
                    self.mesh_list.addItem(it)
                    n_items += 1

        _fill(True)
        if n_items == 0 and self._cls_want() is not None:   # 분류가 거칠어 전부 걸러진 경우: 0개로 끝내지 말고 전부 보여 준다
            _fill(False)
            if n_items:
                lines.append("⚠ '찾을 종류' 체크와 맞는 후보가 없어 종류 구분 없이 전부 올렸습니다 — 이 게임의 분류가 아직 거칠 수 있어요 ([추출기 업데이트] 후 도감 연결로 정확해집니다).")
        self.confirmed = sorted(seen)
        self.mesh_list.selectAll()
        lines.append("후보 %d종의 메시 %d개를 아래 후보 목록에 올리고 선택해 두었습니다 — 더블클릭하면 그 메시가 뷰포트로 바로 열립니다. 애니메이션 후보도 같이 나옵니다." % (len(cands), n_items))
        self.char_q.setText(res.get("clue") or top["folder"])
        self.char_note.setText("\n".join(lines))
        self._refill_anims()

    def grab_from_video(self) -> None:
        """영상에서 바로 찾기: 타임바로 장면을 고르고 그 화면에서 몬스터를 박스 지정 → 그 영역만 AI 에게 보여 준다."""
        sel = [p for p in self.source_paths(True) if frames.kind_of(p) == "video"]
        vids = sel or [p for p in self.source_paths(False) if frames.kind_of(p) == "video"]
        if not vids:
            QMessageBox.information(self, "영상에서 몬스터 지정", "1) 목록에 영상을 먼저 추가하세요.")
            return
        ff = frames.find_ffmpeg(self.cfg.get("ffmpeg"))
        if not ff:
            QMessageBox.warning(self, "영상에서 몬스터 지정", "ffmpeg 를 찾지 못해 영상에서 장면을 뽑을 수 없습니다.\n환경 설정에서 ffmpeg 경로를 지정해 주세요.")
            return
        dlg = _GrabDialog(self, ff, vids[0], os.path.join(bridge.CONFIG_DIR, "vf_crops"))
        dlg.exec()
        if not dlg.crops:
            return
        # 바로 찾지 않고 1) 목록에 이미지로 추가만 한다 — '아는 정보'를 적을 틈을 주기 위해
        have = set(self.source_paths(False))
        self.src_list.clearSelection()
        for p in dlg.crops:
            if p not in have:
                self._add_source(p, "장면")
            for i in range(self.src_list.count()):          # 새로 넣은(또는 이미 있던) 크롭을 선택해 둔다
                if self.src_list.item(i).data(Qt.ItemDataRole.UserRole) == p:
                    self.src_list.item(i).setSelected(True)
        self.rb_mode_img.setChecked(True)     # 모드도 '이미지로 찾기'로
        self._update_src_note()
        self.status.setText("지정한 영역 %d장을 1) 목록에 추가했습니다.\n"
                            "'아는 정보'에 보스 이름 등을 적으면 정확도가 올라갑니다 — 준비되면 [영역 지정한 이미지로 찾기]를 누르세요." % len(dlg.crops))

    def start_guess(self, force_index: bool = False, pre_frames: Optional[List[str]] = None) -> None:
        if self.rb_mode_char.isChecked():  # 캐릭터 메쉬 찾기: AI 추측 없이 이름 검색
            self.start_chars(force_index)
            return
        srcs = self.source_paths()
        if not srcs and pre_frames is None:
            QMessageBox.warning(self, "캐릭터 추측", "영상/이미지를 먼저 추가하세요.")
            return
        if pre_frames is None and self.rb_mode_img.isChecked():   # 이미지로 찾기: 이미지 파일만 쓰고(영상은 건드리지 않음), 유저가 몬스터 영역을 박스로 지정
            imgs = [s for s in srcs if os.path.splitext(s)[1].lower() in IMAGE_EXTS]
            if not imgs:
                QMessageBox.information(self, "영역 지정한 이미지로 찾기",
                                        "1) 목록에 이미지(스크린샷)가 없습니다 — 이 모드는 이미지 파일만 쓰고 영상은 건드리지 않습니다.\n"
                                        "영상에서 찾으려면 1) 의 [🎞 영상에서 몬스터 지정해서 찾기…]를 누르세요 (장면 고르기 + 영역 지정).")
                return
            import time as _time
            crop_dir = os.path.normpath(os.path.join(bridge.CONFIG_DIR, "vf_crops"))
            pre_frames = []
            for ci, p in enumerate(imgs[:4]):
                if os.path.normpath(os.path.dirname(p)) == crop_dir:
                    pre_frames.append(p)                     # 영상에서 이미 영역 지정으로 만든 크롭 — 다시 지정하지 않고 그대로
                    continue
                dlg = _CropDialog(self, p, os.path.join(crop_dir, "crop_%s_%02d.png" % (_time.strftime("%H%M%S"), ci)))
                if dlg.exec():
                    pre_frames.append(dlg.out_path or p)     # 영역 지정 → 크롭, '전체 이미지로' → 원본
            if not pre_frames:
                return
        a = self.win._extract_args()
        if a is None:
            return
        self._last_args = a      # 워커 스레드(유닛 데이터 읽기 등)가 대화상자 없이 쓰게 담아 둔다
        prov = self.provider()
        if not prov.supports_images:
            QMessageBox.warning(self, "인식 방식", "%s 는 글 전용이라 영상 장면 인식에는 쓸 수 없습니다.\n장면 인식은 Ollama(비전 모델)·Claude·다른 AI 로 바꿔 주세요.\n(이름·설명으로 찾기 / AI 낱말 변환에는 그대로 쓸 수 있어요.)" % prov.label)
            return
        ok, msg, _ = prov.check() if prov.key in ("claude", "openai") else (True, "", [])
        if not ok:
            QMessageBox.warning(self, "인식 방식", msg)
            return
        per, hint, pat = int(self.per.value()), self.hint.text().strip(), self.filter.text().strip()
        hint = hint or (self.win.ex_gname.text().strip() if hasattr(self.win, "ex_gname") else "")  # 비우면 0번 탭의 게임 이름
        known = gamenames.parse_known(self.known.toPlainText())
        book = self.name_book() if known else None
        ff = frames.find_ffmpeg(self.cfg.get("ffmpeg"))
        w_cap = 24
        fast = bool(self.ck_fast.isChecked())
        want_cls = self._cls_want()      # 찾을 종류: 비싼 단계(확장·생김새 비교)도 이 종류 안에서만 돌게 job 에 넘긴다

        class _Stopped(Exception):
            pass

        def stop_check(w):
            if w.cancel.is_set():
                raise _Stopped()

        def job(w):
            def _pmap(items, fn, label):
                """항목별 AI 질의를 클라우드 AI 면 동시에 보낸다 — 질문·답은 순차와 똑같고 기다리는 시간만 줄어든다 (정확도 동일)."""
                if not items:
                    return []
                workers = min(4, len(items)) if prov.key in ("claude", "openai") else 1
                out = [None] * len(items)
                if workers <= 1:
                    for i_, it_ in enumerate(items):
                        stop_check(w)
                        w.progress.emit(i_, len(items), label)
                        try:
                            out[i_] = fn(it_)
                        except Exception as e_:  # noqa: BLE001
                            out[i_] = e_
                    return out
                from concurrent.futures import ThreadPoolExecutor
                stop_check(w)
                w.progress.emit(0, len(items), "%s — %d건을 동시에 보냄" % (label, len(items)))
                with ThreadPoolExecutor(max_workers=workers) as pool_:
                    futs_ = [pool_.submit(fn, it_) for it_ in items]
                    for i_, fu_ in enumerate(futs_):
                        w.progress.emit(i_, len(items), label)
                        try:
                            out[i_] = fu_.result()
                        except Exception as e_:  # noqa: BLE001
                            out[i_] = e_
                return out

            if isinstance(prov, vision.OllamaProvider):  # 모델이 없으면 긴 작업(목록 읽기, 장면 뽑기) 전에 바로 알린다
                ok2, msg2, _n = prov.check()
                if not ok2:
                    raise RuntimeError(msg2)
            idx = self._ensure_index(a, w, force_index)
            stop_check(w)
            cls_map_j = self._cls_map(idx) if want_cls else {}

            def cls_ok_j(p_):
                """찾을 종류 체크: 비싼 단계(확장 검색·생김새 비교)도 체크한 종류만 — 단, 분류를 모르는 캐릭터는 거르지 않는다."""
                return classify.passes(cls_map_j.get(classify.folder_of(p_)), want_cls)

            if pre_frames is not None:        # 이미지로 찾기: 유저가 지정한 영역(크롭)을 그대로 장면으로 쓴다 — 장면 뽑기 생략
                from types import SimpleNamespace
                os.makedirs(FRAME_DIR, exist_ok=True)      # 빠르게 모드의 장면 시트 저장용
                fr = [SimpleNamespace(path=p, source=p) for p in pre_frames]
                w.progress.emit(0, 1, "지정한 이미지 %d장으로 바로 인식합니다 (장면 뽑기 생략)" % len(fr))
            else:
                try:
                    shutil.rmtree(FRAME_DIR, ignore_errors=True)
                except OSError:
                    pass

                def sampling(i, n, name):
                    stop_check(w)
                    w.progress.emit(i, n, "장면 뽑는 중 " + name)

                fr = framepick.sample_sources_best(ff, srcs, FRAME_DIR, max(per, 6) if fast else per, w_cap, sampling)   # 검은/정지(메뉴) 화면 제외, 서로 다른 장면 위주
            stop_check(w)
            vocab, notes = self._vocab(idx, prov.vocab_limit(), pat, style="lines")   # 카탈로그면 자르지 않고 전부 (잘리면 보스 > 미니보스 > 일반 > NPC 순으로 남김)
            ids = bool(getattr(vocab, "ids", False))
            cat = self._catalog(idx) if ids else None
            read_text = bool(self.cfg.get("vf_read_text", True)) and cat is not None
            gl = None
            if not ids and bool(self.cfg.get("vf_gloss", True)):
                # 리소스 이름이 영어 낱말이 아닌 게임(오공 = 병음)은 이름만으로 캐릭터를 이을 수 없다 -> 게임마다 한 번 AI 에게 이름 뜻을 풀어 받아 저장해 쓴다
                try:
                    gl = self.glossary(hint)
                    all_names = self._gloss_names(idx)
                    if gl.entries or nameglossary.looks_opaque(all_names):
                        todo = gl.missing(all_names)
                        if todo:
                            n_new, gerrs = nameglossary.generate(prov.ask_text, all_names, hint, gl,
                                                                 on_progress=lambda i, n: (stop_check(w), w.progress.emit(i, max(n, 1), "리소스 이름 뜻 풀이 중 (게임마다 처음 한 번) %d/%d" % (i, n))))
                            notes.append("리소스 이름 뜻 풀이: %d개 새로 풀었습니다 (전체 %d개 기억 중)%s" % (n_new, len(gl.entries), (" · 오류: " + gerrs[0]) if gerrs else ""))
                        # 같은 캐릭터의 여러 벌(_01, _02a…)은 하나만 남겨 더 많은 캐릭터를 알리고, 뜻이 풀린 이름을 앞에 둔다
                        reps_ = {}
                        for v in (all_names if not pat else list(vocab)):
                            reps_.setdefault(nameglossary.base_key(v), v)
                        ordered = sorted(reps_.values(), key=lambda v: (0 if gl.meaning(v) else 1, (gl.get(v) or {}).get("kind") not in ("boss", "enemy", "npc", "player")))
                        vocab = _Vocab([gl.annotate(v) for v in ordered[:prov.vocab_limit()]])
                        notes.append("이름 목록에 뜻을 붙여 AI 에게 알렸습니다 (%d종, 예: %s)" % (len(vocab), vocab[0] if vocab else ""))
                    else:
                        gl = None
                except _Stopped:
                    raise
                except Exception as e:  # noqa: BLE001 - 풀이는 보조 기능
                    notes.append("리소스 이름 뜻 풀이를 건너뜁니다: %s" % str(e)[:160])
                    gl = None
            if not ids and fast and len(vocab) > vision.FAST_VOCAB:
                vocab = vocab[:vision.FAST_VOCAB]
            if known:  # 알려 준 이름을 리소스 이름으로 바꿔 두고, 이름 목록에 꼭 들어가게 한다
                gamenames.resolve_known(known, idx, book)
                # 표시 이름으로 못 이은 보스 이름(금지장로 등): AI 확장 검색(원어·음차·지역·모티프 코드명)으로 잇고,
                # 유력 후보 보스들을 전부 검수 후보에 넣어 검수자가 고르게 한다 (여러 개면 동시에 질의)
                ks_all = [k for k in known if k.top is None]
                store_k = self._entities(idx, hint) if ks_all else None
                mp_k = [m.path for m in idx.meshes if cls_ok_j(m.path)]
                ks_ = []
                for k in ks_all:   # ⓪ 정답표/사전 '완전 일치'면 AI 를 부르지 않는다 — 내부 데이터가 이미 아는 이름은 즉시
                    hitd = []
                    for hh in (store_k.find(k.name, limit=2) if store_k is not None else []):
                        if hh.score < 0.93:
                            continue
                        for code in hh.entity.codenames:
                            cl = "/" + (code or "").lower() + "/"
                            ms2 = [p for p in mp_k if cl in p.lower()]
                            if ms2:
                                p2 = min(ms2, key=lambda x: ("base" not in x.lower(), len(x)))
                                if p2 not in {c.path for c in hitd}:
                                    hitd.append(assetidx.MeshCandidate(p2, assetidx.mesh_name(p2), min(hh.score, 0.98), "게임 데이터 일치 — '%s'" % k.name))
                    if hitd:
                        for ci2, c2 in enumerate(hitd[:3]):
                            k.cands.insert(ci2, c2)
                        notes.append("아는 정보 '%s' → 게임 데이터(정답표/사전) 즉시 일치 — AI 확장 생략" % k.name)
                    else:
                        ks_.append(k)
                for k, nf_ in zip(ks_, _pmap(ks_, lambda k_: identify_namefind.find(prov.ask_text, k_.name, hint, mp_k, idx.anims, store_k),
                                             "아는 정보 AI 확장 검색 (원어·음차·지역 코드명)")):
                    if isinstance(nf_, Exception):
                        notes.append("아는 정보 '%s' 확장 검색 실패: %s" % (k.name, str(nf_)[:80]))
                        continue
                    added_ = []
                    for ci_, cand_ in enumerate((nf_.get("candidates") or [])[:4]):
                        if cand_["score"] < 0.35 or not cand_["meshes"]:
                            continue
                        p_ = min(cand_["meshes"], key=lambda x: ("base" not in x.lower(), len(x)))
                        k.cands.insert(len(added_), assetidx.MeshCandidate(p_, assetidx.mesh_name(p_), min(cand_["score"], 0.95), "AI 확장 — " + cand_["why"]))
                        added_.append("%s(%d%%)" % (cand_["folder"], round(cand_["score"] * 100)))
                    if added_:
                        notes.append("아는 정보 '%s' → 후보 %s (AI 확장 — 검수 칸에서 고르세요)" % (k.name, ", ".join(added_)))
                for k in known:
                    if k.top and not any(v == k.top.name or v.startswith(k.top.name + " ") for v in vocab):
                        vocab = [k.top.name] + list(vocab)
                    notes.append("아는 정보: %s → %s" % (k.name, (k.top.name + " (%d%%)" % round(k.top.score * 100)) if k.top else "리소스를 못 찾음"))
            klines = [k.prompt_line() for k in known]
            dets: List[vision.Detection] = []
            errors: List[str] = []

            def finish(dets):
                """인식 결과 마무리: 뜻이 붙은 이름 정리 -> 묶기 -> 못 찾은 줄은 뜻 풀이로 다시 찾기."""
                if gl is not None:
                    for d_ in dets:      # '이름 (뜻)' 을 통째로 답한 경우 이름만 남긴다
                        d_.name = nameglossary.strip_annotation(d_.name)
                        d_.alts = [nameglossary.strip_annotation(x) for x in d_.alts]
                groups_ = gamenames.merge_known(vision.aggregate(dets), known)
                if gl is not None and gl.entries:
                    # 이름을 못 댔거나(모름) 이름과 맞는 메시가 없는 줄: 외형 설명·게임 속 이름으로, 뜻이 붙은 전체 목록에서 AI 가 다시 고른다
                    all_names = self._gloss_names(idx)
                    rows_g = []
                    for g_ in groups_:
                        if len(rows_g) >= 8:
                            break
                        unknown_ = (g_.name or "").strip().lower() in assetidx.UNKNOWN_WORDS
                        if not unknown_ and idx.rank_meshes(g_.name, "", 1, None):
                            continue
                        m_ = re.search(r"게임 속 이름: ([^.]+)", g_.description or "")
                        ingame = (m_.group(1).strip() if m_ else "") or ("" if unknown_ else g_.name)
                        desc_ = re.sub(r"게임 속 이름: [^.]+\.?", "", g_.description or "").strip()
                        rows_g.append((g_, unknown_, ingame, desc_))
                    res_g = _pmap(rows_g, lambda t: nameglossary.pick(prov.ask_text, t[3], t[2], all_names, gl, hint),
                                  "뜻 풀이로 캐릭터 다시 찾는 중")
                    for (g_, unknown_, ingame, desc_), picks in zip(rows_g, res_g):
                        if isinstance(picks, Exception):
                            errors.append("뜻 풀이로 찾기: %s" % str(picks)[:160])
                            continue
                        if not picks:
                            picks = [(n, s, why) for n, s, why in gl.search(ingame or desc_, all_names, 5)]
                        if picks:
                            if ingame and unknown_ is False and g_.name == ingame:
                                g_.description = ("게임 속 이름: %s. %s" % (ingame, desc_)).strip()
                            g_.name = picks[0][0]
                            g_.confidence = max(g_.confidence, min(picks[0][1], 0.6))      # 뜻 풀이로 고른 것은 낮은 확신으로 (⚠ 표시) — 검수자가 확인
                            g_.alts = [p[0] for p in picks[1:]] + [a_ for a_ in g_.alts if a_ not in {p[0] for p in picks}]
                            notes.append("뜻 풀이로 찾음: %s → %s%s" % (ingame or desc_[:40], picks[0][0], (" (%s)" % picks[0][2]) if picks[0][2] else ""))
                try:
                    # 엔티티 사전: 별명·다국어 정식명·음차·오타로도 캐릭터를 잇는다 (검수자가 가르친 것 포함)
                    n_fix = identify_apply.fix_groups(self._entities(idx, hint), self._catalog(idx), groups_, notes)
                    if n_fix:
                        notes.append("엔티티 사전으로 %d개 이름을 교정했습니다" % n_fix)
                except Exception as e:  # noqa: BLE001 - 사전 교정은 보조 기능
                    errors.append("엔티티 사전: %s" % str(e)[:120])
                # 영상에서 추측한 이름도 '아는 정보'처럼 AI 확장 검색(원어 정식명·음차·지역 머리글자 코드명)으로 잇는다
                # — 이름 글자로 메시를 못 찾은 줄만, 최대 4줄. 찾은 후보는 전부 검수 칸에 들어간다.
                try:
                    rows_x = []
                    for g_ in groups_:
                        if len(rows_x) >= 4:
                            break
                        nm_ = (g_.name or "").strip()
                        unknown_ = not nm_ or nm_.lower() in assetidx.UNKNOWN_WORDS
                        if not unknown_ and idx.rank_meshes(nm_, "", 1, None):
                            continue
                        if unknown_ and not (g_.description or "").strip():
                            continue
                        rows_x.append((g_, nm_, unknown_))
                    mesh_paths_ = [m.path for m in idx.meshes if cls_ok_j(m.path)]
                    store_ = self._entities(idx, hint) if rows_x else None
                    lore_cards = self._lore_cards() if rows_x else {}

                    def _expand(t):
                        from ..core.identify import unitlink as _ul
                        g_, nm_, unknown_ = t
                        ex_, why_ = [], ""
                        desc_full = " ".join(x for x in (nm_, g_.description or "") if x).strip()
                        lore_names = []
                        # ⓪ 게임 도감(일화·시구) 글 검색 — AI 없이 수 ms. 게임이 직접 적어 둔 특징 글이라 가장 믿을 만하다.
                        #    예: "white robed wolf" -> 도감 '늑대 검객' -> (정답표가 있으면) 코드명 -> 메시 바로.
                        if lore_cards and desc_full:
                            try:
                                hits_l = _ul.search_lore(desc_full, lore_cards)
                                lore_names = [h["name"] for h in hits_l if h["score"] >= 0.5][:3]
                                if lore_names and store_ is not None:
                                    for nm2 in lore_names:
                                        for hh in store_.find(nm2, limit=1):
                                            if hh.score < 0.9:
                                                continue
                                            for code in hh.entity.codenames:
                                                cl = "/" + (code or "").lower() + "/"
                                                ms2 = [mp for mp in mesh_paths_ if cl in mp.lower()]
                                                if ms2:
                                                    p2 = min(ms2, key=lambda x: ("base" not in x.lower(), len(x)))
                                                    if not any(c.path == p2 for c in ex_):
                                                        ex_.append(assetidx.MeshCandidate(p2, assetidx.mesh_name(p2), 0.9, "도감 일화 — '%s'" % nm2))
                                    if ex_:
                                        why_ = "도감 일화(정답표)"
                            except Exception:  # noqa: BLE001 - 도감 검색은 보조
                                lore_names = []
                        if not unknown_:   # 이름이 있으면: 원어·음차·지역 코드명 확장 (금지장로 방식)
                            nf_ = identify_namefind.find(prov.ask_text, nm_, hint, mesh_paths_, idx.anims, store_)
                            for cand_ in (nf_.get("candidates") or [])[:4]:
                                if cand_["score"] < 0.35 or not cand_["meshes"]:
                                    continue
                                p_ = min(cand_["meshes"], key=lambda x: ("base" not in x.lower(), len(x)))
                                ex_.append(assetidx.MeshCandidate(p_, assetidx.mesh_name(p_), min(cand_["score"], 0.95), "AI 확장 — " + cand_["why"]))
                            why_ = "AI 확장"
                        if not ex_:        # 이름이 없거나(잡몹) 이름 확장이 비면: 외형 설명으로 종족 변형 + 부속 파일 특징 (방패 든 늑대 방식)
                            desc_x = " ".join(x for x in (nm_ if not unknown_ else "", g_.description or "", " ".join(lore_names)) if x).strip()
                            if desc_x:
                                mob_ = identify_namefind.find_mob(prov.ask_text, desc_x, hint, mesh_paths_, idx.anims)
                                for cand_ in (mob_.get("candidates") or [])[:8]:   # 같은 종족 변형이 많으면 생김새 토너먼트가 가린다
                                    if cand_["score"] < 0.35 or not cand_["meshes"]:
                                        continue
                                    p_ = min(cand_["meshes"], key=lambda x: ("base" not in x.lower(), len(x)))
                                    ex_.append(assetidx.MeshCandidate(p_, assetidx.mesh_name(p_), min(cand_["score"], 0.9), "외형 — " + cand_["why"]))
                                why_ = "외형(종족·들고 있는 것)"
                        return ex_, why_

                    for (g_, nm_, _u), r_ in zip(rows_x, _pmap(rows_x, _expand, "AI 확장 검색 (원어·음차·지역 코드명 / 외형)")):
                        if isinstance(r_, Exception):
                            errors.append("AI 확장 검색: %s" % str(r_)[:120])
                            continue
                        ex_, why_ = r_
                        if ex_:
                            try:
                                g_.ex_cands = ex_
                            except Exception:  # noqa: BLE001
                                pass
                            notes.append("'%s' → %s 후보 %d개 (검수 칸에서 고르세요)" % (nm_ or (g_.description or "")[:30], why_, len(ex_)))
                except _Stopped:
                    raise
                except Exception as e:  # noqa: BLE001 - 확장은 보조
                    errors.append("AI 확장 검색: %s" % str(e)[:120])
                # 이미지(영역 지정) 모드: 후보 메시를 실제로 렌더해 '생김새'로 가린다 — Lang_03 을 찾은 방식 그대로.
                # 후보 -> GLB 추출 -> Blender 썸네일(게임마다 캐시) -> 지정한 이미지와 한 시트 -> VLM 이 같은 캐릭터를 지목.
                # 추출/Blender/VLM 어느 하나라도 안 되면 조용히 건너뛴다 (보조 기능).
                if pre_frames is not None:
                    try:
                        helper_a, game_a, ue_a, aes_a, usmap_a = a
                        gname_v = hint or "game"
                        rows_v = []
                        for g_ in groups_:
                            if len(rows_v) >= 3 or not getattr(g_, "frame", "") or not os.path.isfile(g_.frame):
                                continue
                            cands_v = [c.path for c in idx.rank_meshes(g_.name, g_.description, 6, self.memory)]
                            for c_ in (getattr(g_, "ex_cands", None) or []):
                                if c_.path not in cands_v:
                                    cands_v.append(c_.path)
                            ok_v = []
                            for p_ in cands_v:
                                mi_ = idx.mesh(p_)
                                if mi_ is not None and mi_.rigged and cls_ok_j(p_):   # 찾을 종류 체크 안의 후보만 렌더
                                    ok_v.append(p_)
                            ok_v = ok_v[:identify_verify.MAX_CANDS * 2]   # 변형이 많으면 토너먼트(4개씩 조별 -> 결승)로 가린다
                            if len(ok_v) >= 2:
                                rows_v.append((g_, ok_v))
                        # 썸네일이 아직 없는 후보만 모아 '한 번에' 추출 — 추출기가 호출마다 pak 전체를 다시 마운트해서
                        # 한 개씩 뽑으면 후보 수 × 수십 초가 걸리던 것을 마운트 1번으로 줄인다.
                        glb_map = {}
                        need_ = list(dict.fromkeys(p_ for _g, ov in rows_v for p_ in ov
                                                   if not os.path.isfile(identify_verify.thumb_path(gname_v, p_))))
                        if need_:
                            stop_check(w)
                            w.progress.emit(0, 1, "생김새 비교: 후보 메시 %d개를 한 번에 추출 중 (게임마다 처음 한 번)" % len(need_))
                            out_dir = identify_verify.cache_dir(gname_v, "refglb")
                            os.makedirs(out_dir, exist_ok=True)
                            files_ = extractor.export_assets(helper_a, game_a, ue_a, aes_a, usmap_a, need_, out_dir,
                                                             on_event=lambda _e: None, kind="mesh", mesh_format="glb", textures=True) or []
                            by_stem = {}
                            for f_ in files_:
                                if f_.lower().endswith(".glb"):
                                    by_stem.setdefault(os.path.splitext(os.path.basename(f_))[0].lower(), f_)
                            if len(by_stem) < len(need_):     # 반환 목록에 없으면 캐시 폴더에서 찾는다
                                for root_, _dd, nn_ in os.walk(out_dir):
                                    for n_ in nn_:
                                        if n_.lower().endswith(".glb"):
                                            by_stem.setdefault(os.path.splitext(n_)[0].lower(), os.path.join(root_, n_))
                            for p_ in need_:
                                glb_map[p_] = by_stem.get(os.path.splitext(os.path.basename(p_))[0].lower())
                        for vi_, (g_, ok_v) in enumerate(rows_v):
                            stop_check(w)
                            w.progress.emit(vi_, len(rows_v), "생김새 비교: 후보 렌더(한 번에)·AI 대조 중")
                            thumbs_ = identify_verify.ensure_thumbs(
                                gname_v, ok_v, lambda p_: glb_map.get(p_),
                                progress=lambda i_, nn2, nm_: w.progress.emit(i_, nn2, "후보 렌더 %s" % nm_))
                            r_v = identify_verify.verify_rounds(prov.ask, gname_v,
                                                                g_.frame, getattr(g_, "box", None), ok_v, thumbs_,
                                                                FRAME_DIR, panel.make_sheet)
                            if r_v and r_v.get("path") and float(r_v.get("confidence") or 0) >= 0.45:
                                try:
                                    g_.ver_pick = r_v["path"]
                                    g_.ver_why = str(r_v.get("why") or "")
                                except Exception:  # noqa: BLE001
                                    pass
                                notes.append("생김새 비교로 지목: %s (%.0f%%%s)" % (
                                    assetidx.mesh_name(r_v["path"]), float(r_v.get("confidence") or 0) * 100,
                                    (", " + r_v["why"]) if r_v.get("why") else ""))
                    except _Stopped:
                        raise
                    except Exception as e:  # noqa: BLE001 - 생김새 비교는 보조
                        errors.append("생김새 비교: %s" % str(e)[:120])
                return groups_

            if fast:   # 영상마다 장면 시트 한 장 -> AI 호출 1번 (클라우드 AI 는 영상 여러 개를 동시에)
                by_src: Dict[str, List[str]] = {}
                for f in fr:
                    by_src.setdefault(f.source, []).append(f.path)
                notes.append("빠르게 모드: 영상 %d개 × AI 호출 1번 (이름 목록 %d개)" % (len(by_src), len(vocab)))
                jobs = list(by_src.items())

                def one(si_src):
                    si, (src, paths) = si_src
                    stop_check(w)
                    sheet = panel.make_sheet(paths, os.path.join(FRAME_DIR, "guess_sheet_%02d.jpg" % si), cols=4, cell_w=384, max_frames=9)
                    found: List[vision.Detection] = []
                    if read_text:   # 화면에 이름이 적혀 있으면(보스 체력바 등) 추측하지 않고 글자로 바로 찾는다
                        found = screenname.read_names(prov, paths, cat, 3, on_error=lambda e, _s=src: errors.append("%s(글자 읽기): %s" % (os.path.basename(_s), e)))
                    return found + prov.analyze_sheet(sheet, paths[0], vocab, hint, klines, 0 if ids else vision.FAST_VOCAB, ids=ids)

                workers = 3 if prov.key in ("claude", "openai") else 1
                done_n = 0
                if workers == 1:
                    for it in enumerate(jobs):
                        w.progress.emit(done_n, len(jobs), "캐릭터 인식 중 " + os.path.basename(it[1][0]))
                        try:
                            dets += one(it)
                        except vision.VisionError as e:
                            errors.append("%s: %s" % (os.path.basename(it[1][0]), e))
                            if len(errors) >= 3 and not dets:
                                break
                        done_n += 1
                else:
                    from concurrent.futures import ThreadPoolExecutor
                    with ThreadPoolExecutor(max_workers=workers) as ex:
                        futs = [(it, ex.submit(one, it)) for it in enumerate(jobs)]
                        for it, fu in futs:
                            w.progress.emit(done_n, len(jobs), "캐릭터 인식 중 " + os.path.basename(it[1][0]))
                            try:
                                dets += fu.result()
                            except vision.VisionError as e:
                                errors.append("%s: %s" % (os.path.basename(it[1][0]), e))
                            done_n += 1
                if cat is not None:
                    screenname.canonicalize(dets, cat)   # AI 가 낸 이름(ID/줄 통째/영·한 이름) -> 대표 메시 이름
                return {"idx": idx, "groups": finish(dets), "errors": errors, "notes": notes, "frames": len(fr), "vocab": len(vocab)}
            if read_text:
                dets += screenname.read_names(prov, [f.path for f in fr], cat, 3, on_error=lambda e: errors.append("글자 읽기: %s" % e))
            for i, f in enumerate(fr):
                stop_check(w)  # 취소하면 다음 장면부터 안 한다
                w.progress.emit(i, len(fr), "캐릭터 인식 중 " + os.path.basename(f.path))
                try:
                    dets += prov.analyze(f.path, vocab, hint, klines, ids)
                except vision.VisionError as e:
                    errors.append("%s: %s" % (os.path.basename(f.path), e))
                    if len(errors) >= 3 and not dets:
                        break  # 계속 실패하면 멈춘다 (키/연결 문제)
            if cat is not None:
                screenname.canonicalize(dets, cat)
            groups_ = finish(dets)
            return {"idx": idx, "groups": groups_, "errors": errors, "notes": notes, "frames": len(fr), "vocab": len(vocab)}

        self.win.run_async(job, self._after_guess, "캐릭터 추측 중", cancellable=True,
                           on_cancel=lambda: self.status.setText("캐릭터 추측을 취소했습니다. 인식 방식을 바꾸고 다시 시작할 수 있어요."))

    def _after_guess(self, res) -> None:
        self.index = res["idx"]
        self._update_known_tip()   # 이제 도감/정답표가 준비됐을 수 있으니 게임 맞춤 팁 갱신
        groups: List[vision.Group] = res["groups"]
        self.fill_table(groups)
        lines = ["장면 %d장 분석, 캐릭터 %d종 추측 (AI에게 알린 이름 %d종, 에셋 메시 %d / 애니메이션 %d)" % (
            res["frames"], len(groups), res["vocab"], len(self.index.meshes), len(self.index.anims))]
        lines += res["notes"]
        if res["errors"]:
            lines.append("인식 오류 %d건: %s" % (len(res["errors"]), res["errors"][0][:200]))
        self.status.setText("\n".join(lines))
        if not groups:
            QMessageBox.warning(self, "캐릭터 추측", "캐릭터를 찾지 못했습니다.\n\n" + ("\n".join(res["errors"][:3]) or "다른 장면이나 인식 방식을 시도하세요. '캐릭터 줄 직접 추가'로 직접 넣을 수도 있습니다."))

    # ================================================================== 찾을 종류 (보스/일반 몬스터/플레이어블/NPC)
    def _cls_changed(self, *_a) -> None:
        self.cfg["vf_cls"] = {k: bool(cb.isChecked()) for k, cb in self.cls_checks.items()}
        bridge.save_config(self.cfg)
        if self.char_mode:
            self.show_char_meshes()

    def _cls_stamp(self) -> list:
        """분류 입력(에셋 목록·글자 사전·뜻 풀이)의 수정 시각 — 하나라도 바뀌면 분류를 다시 계산."""
        out = []
        for p in (INDEX_CACHE, NAMES_CACHE, getattr(self.__dict__.get("_gloss"), "path", "") or ""):
            try:
                out.append(round(os.path.getmtime(p), 2) if p and os.path.isfile(p) else 0)
            except OSError:
                out.append(0)
        return out

    def _cls_map(self, idx, build: bool = True):
        """'캐릭터 폴더 -> 종류' 표. 인덱스를 묶을 때 한 번 계산해 파일로 저장하고, 다음부터는 바로 읽는다.
        근거 서열(아래가 위를 덮어씀): 추정(애니 수·전용 연출) < 뜻 풀이 사전(AI) < 게임 도감(공식 분류). 플레이어블 표시는 유지.

        build=False (UI 스레드용): 이미 계산된 것(메모/캐시 파일)만 쓰고, 없으면 빈 표 — 화면이 얼지 않게.
        무거운 계산(도감 27MB 파싱·카탈로그)은 작업 스레드(build=True: 인덱스 묶기/추측 job)에서만 한다."""
        if idx is None or not idx:
            return {}
        memo = self.__dict__.get("_cls_memo")
        if isinstance(memo, tuple) and memo[0] is idx:
            return memo[1]
        cache_p = os.path.join(bridge.CONFIG_DIR, "cls_cache.json")
        stamp = self._cls_stamp()
        try:
            with open(cache_p, "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and d.get("ver") == 2 and d.get("stamp") == stamp and isinstance(d.get("map"), dict):
                self._cls_memo = (idx, d["map"])
                return d["map"]
        except (OSError, ValueError):
            pass
        if not build:
            return {}
        from ..core import classify
        m = classify.classify([mm.path for mm in idx.meshes], idx.anims)
        try:
            # ① 뜻 풀이 사전이 리소스 이름마다 기억해 둔 종류(boss/enemy/npc/player) — AI 추정이라 도감보다 약하다
            gl = self.glossary()
            if gl.entries:
                to_cls = {"boss": "boss", "enemy": "mob", "npc": "npc", "player": "player"}
                for mm in idx.meshes:
                    k = to_cls.get(((gl.get(mm.name) or {}).get("kind")) or "")
                    if k:
                        f_ = classify.folder_of(mm.path)
                        if m.get(f_) != "player":
                            m[f_] = k
        except Exception:  # noqa: BLE001 - 사전이 없으면 추정치로
            pass
        try:
            # ② 게임 도감의 '공식 분류'(잡요괴/두목/요왕/인물) — 가장 확실하므로 마지막에 덮어쓴다
            with open(NAMES_CACHE, "r", encoding="utf-8") as f:
                journal = classify.journal_types((json.load(f) or {}).get("entries") or [])
            if journal:
                cat = self._catalog(idx)
                for ch in cat.chars.values():
                    k = next((journal[classify.nname(n)] for n in (list(ch.en) + list(ch.ko)) if classify.nname(n) in journal), None)
                    if not k:
                        continue
                    for p in ch.meshes:
                        f_ = classify.folder_of(p)
                        if m.get(f_) != "player":   # 플레이어블 표시는 그대로 둔다
                            m[f_] = k
        except Exception:  # noqa: BLE001 - 도감이 없는 게임은 추정치로
            pass
        try:
            with open(cache_p, "w", encoding="utf-8") as f:
                json.dump({"ver": 2, "stamp": stamp, "map": m}, f, ensure_ascii=False)
        except OSError:
            pass
        self._cls_memo = (idx, m)
        return m

    def _cls_want(self):
        """체크된 종류들. 전부/하나도 체크 안 됨 = 필터 없음(None)."""
        want = {k for k, cb in self.cls_checks.items() if cb.isChecked()}
        return None if (not want or len(want) >= len(self.cls_checks)) else want

    def _class_ok(self, path: str) -> bool:
        want = self._cls_want()
        if want is None or not path:
            return True
        idx = self.index or self._load_index_cached()
        from ..core import classify
        m = self._cls_map(idx, build=False)      # UI 스레드: 계산된 표가 없으면 거르지 않는다 (화면이 얼지 않게)
        return classify.passes(m.get(classify.folder_of(path)) if m else None, want)

    def _cls_tag(self, path: str) -> str:
        """후보 글머리에 붙일 [보스]/[일반 몬스터] 같은 표시."""
        idx = self.index
        if idx is None or not path:
            return ""
        from ..core import classify
        k = classify.LABEL.get(self._cls_map(idx, build=False).get(classify.folder_of(path)) or "")
        return ("[%s] " % k) if k else ""

    # ================================================================== 검수 표
    def _merge_cands(self, idx, base, alts, n: int = 5):
        """랭킹 상위 + AI 가 꼽은 다른 후보(각각 가장 비슷한 메시)를 섞어 n개 (중복 없이)."""
        out = list(base[:max(1, n - min(len(alts), n - 1) if alts else n)])
        have = {c.path for c in out}
        for al in alts:
            for c in idx.rank_meshes(al, "", 1, None):
                if c.path not in have and len(out) < n:
                    out.append(c)
                    have.add(c.path)
        for c in base:
            if len(out) >= n:
                break
            if c.path not in have:
                out.append(c)
                have.add(c.path)
        return out

    def _cand_text(self, c: assetidx.MeshCandidate) -> str:
        return "%s%s   [%d%%] %s" % (self._cls_tag(c.path), c.name, round(min(c.score, 1.0) * 100), c.why)

    def _fill_combo(self, combo, cands: List[assetidx.MeshCandidate], pick_first: bool = True) -> None:
        """후보를 채운다. pick_first=False 면 맨 위에 '직접 고르세요'를 두고 그것이 선택된 채로 시작한다."""
        combo.clear()
        for c in cands:      # 확신이 낮아도 가장 유력한 후보를 골라 둔다 (틀리면 검수자가 바꿈). 낮은 확신은 표에 ⚠ 로만 알린다
            combo.addItem(self._cand_text(c), c.path)
        if not cands:
            combo.addItem("(후보를 못 찾았어요 — 아래 검색 칸, 또는 ❓ 잘 찾는 법 참고)", None)
            combo.setToolTip(FIND_TIPS)

    def fill_table(self, groups: List[vision.Group]) -> None:
        # 검수자가 메시를 바꾸면 group.name 이 메시 이름으로 덮이므로, 학습용으로 원래 추측을 남겨 둔다
        self._orig_guess = {id(g): (g.name, g.description) for g in groups}
        self.rows = []
        self.table.setRowCount(0)
        self.table.setRowCount(len(groups))
        for r, g in enumerate(groups):
            self._make_row(r, g)

    def _make_row(self, r: int, g: vision.Group) -> None:
        idx = self.index or assetidx.AssetIndex()
        base = idx.rank_meshes(g.name, g.description, 8, self.memory)
        if g.alts or self.ck_fast.isChecked():   # 후보군: AI 가 꼽은 다른 이름들도 합쳐 5개
            base = self._merge_cands(idx, base, g.alts, 5)
        ex_ = list(getattr(g, "ex_cands", None) or [])
        if ex_:                                  # AI 확장 검색으로 찾은 후보는 '전부' 앞에 넣는다
            have_ = {c.path for c in ex_}
            base = ex_ + [c for c in base if c.path not in have_]
        if not base:      # 이름 글자로는 못 찾음 -> 뜻 풀이 사전(한국어/영어/한자 뜻)으로 후보를 낸다
            base = self._gloss_cands(" ".join(x for x in (g.name, g.description) if x))
        base = [c for c in base if self._class_ok(c.path)]   # 찾을 종류 체크에 맞는 것만
        vp = getattr(g, "ver_pick", "") or ""
        if vp:   # 생김새 비교(렌더 대조)가 지목한 메시는 맨 앞에
            hit = next((c for c in base if c.path == vp), None)
            if hit is not None:
                base.remove(hit)
            base.insert(0, assetidx.MeshCandidate(vp, assetidx.mesh_name(vp), 0.97,
                                                  "생김새 일치 — " + ((getattr(g, "ver_why", "") or "렌더 대조")[:80])))
        remembered = bool(base) and base[0].why.startswith("이전에 확정")
        sure = remembered or bool(vp) or (bool(g.name) and g.confidence >= self.min_conf.value() / 100.0)
        it = QTableWidgetItem("")
        pm = _thumb(g.frame, g.box)
        if pm is not None:
            it.setIcon(QIcon(pm))
        self.table.setItem(r, 0, it)
        name = g.name or "(모름)"
        warn = "⚠ 확신 낮음 — 직접 확인\n" if g.name and not sure else ""
        txt = "%s%s\n확신 %d%% · %d장면\n%s" % (warn, name, round(g.confidence * 100), g.seen, g.description[:80])
        self.table.setItem(r, 1, QTableWidgetItem(txt))
        box = QWidget()
        bl = QVBoxLayout(box)
        bl.setContentsMargins(2, 2, 2, 2)
        combo = QComboBox()
        self._fill_combo(combo, base, pick_first=sure)
        search = QLineEdit()
        search.setPlaceholderText("다른 메시 찾기: 이름 일부를 입력")
        bl.addWidget(combo)
        bl.addWidget(search)
        pick = QHBoxLayout()
        sel_lb = QLabel("")
        sel_lb.setWordWrap(True)
        pick.addWidget(sel_lb, 1)
        btn_see = QPushButton("👁 뷰포트에서 보기")
        btn_see.setToolTip("지금 고른 메시를 뷰포트(타깃)로 열어 모양을 직접 확인합니다 (추출 없이 확인).")
        pick.addWidget(btn_see)
        bl.addLayout(pick)
        self.table.setCellWidget(r, 2, box)
        ex = QCheckBox("제외")
        self.table.setCellWidget(r, 3, ex)
        row = _Row(g, combo, search, ex, base, sure)
        row.sel_label = sel_lb
        search.textChanged.connect(lambda text, rw=row: self._on_search(rw, text))
        combo.currentIndexChanged.connect(lambda *_a, rw=row: self._row_choice_changed(rw))
        ex.toggled.connect(lambda *_a, rw=row: self._row_choice_changed(rw))
        btn_see.clicked.connect(lambda *_a, rw=row: self._see_row(rw))
        self.rows.append(row)
        self.table.setRowHeight(r, 124)
        self._refresh_choice(row)

    def _refresh_choice(self, row: _Row) -> None:
        """줄에 '지금 무엇을 골랐는지'를 크게 보여 준다 (✔ 선택됨 / 제외 / 아직 못 골랐음) + 맨 아래 요약."""
        lb = getattr(row, "sel_label", None)
        if lb is not None:
            p = row.chosen()
            if row.exclude.isChecked():
                lb.setText("🚫 제외됨 (추출하지 않음)")
                lb.setStyleSheet("color:#8a93a6")
            elif p:
                m = self.index.mesh(p) if self.index else None
                lb.setText("✔ 선택됨: " + (m.name if m else assetidx.mesh_name(p)))
                lb.setStyleSheet("color:#5ab0ff;font-weight:700")
            else:
                lb.setText("❓ 아직 못 골랐음 — 후보에서 고르거나 검색하세요")
                lb.setStyleSheet("color:#e0a050;font-weight:700")
        self._update_choice_summary()

    def _row_choice_changed(self, row: _Row) -> None:
        self._refresh_choice(row)

    def _update_choice_summary(self) -> None:
        lb = self.__dict__.get("choice_summary")
        if lb is None:
            return
        rows = self.__dict__.get("rows") or []
        n_ok = sum(1 for r in rows if r.chosen())
        n_ex = sum(1 for r in rows if r.exclude.isChecked())
        n_no = len(rows) - n_ok - n_ex
        lb.setText("확정하면: 메시 %d개 선택됨 · 제외 %d · 못 고른 줄 %d%s" % (n_ok, n_ex, n_no, "  (못 고른 줄은 빠집니다)" if n_no else ""))

    def _see_row(self, row: _Row) -> None:
        p = row.combo.currentData()
        if p:
            self.open_mesh_in_viewport(p)

    def glossary(self, hint: str = "") -> "nameglossary.Glossary":
        """이 게임의 리소스 이름 풀이 사전 (게임 이름별로 저장)."""
        game = (hint or self.hint.text().strip() or (self.win.ex_gname.text().strip() if hasattr(self.win, "ex_gname") else "")
                or layout.detect_game_name(self.cfg.get("game_dir", "")) or "game")
        cur = self.__dict__.get("_gloss")
        if cur is None or cur.game != game:
            cur = nameglossary.Glossary.load(game)
            self._gloss = cur
        return cur

    @staticmethod
    def _gloss_names(idx) -> List[str]:
        """풀이할 이름들: 뼈대가 있는(캐릭터일 가능성이 큰) 메시 이름을 앞에, 나머지를 뒤에 (개수 제한에 걸릴 때 캐릭터가 먼저 풀리게)."""
        rig, rest, seen = [], [], set()
        for m in idx.meshes:
            k = m.name.lower()
            if k in seen:
                continue
            seen.add(k)
            (rig if assetidx.is_rigged_path(m.path) else rest).append(m.name)
        return sorted(rig, key=str.lower) + sorted(rest, key=str.lower)

    def _gloss_cands(self, text: str, limit: int = 5) -> List[assetidx.MeshCandidate]:
        """뜻 풀이 사전으로 찾은 메시 후보 ('게임 속 이름: …' 이 들어 있으면 그 이름을 우선)."""
        idx = self.index
        if idx is None or not (text or "").strip():
            return []
        try:
            gl = self.glossary()
            if not gl.entries:
                return []
            m = re.search(r"게임 속 이름: ([^.]+)", text)
            queries = ([m.group(1).strip()] if m else []) + [text]
            names = self._gloss_names(idx)
            out: List[assetidx.MeshCandidate] = []
            have = set()
            for q in queries:
                for n, s, why in gl.search(q, names, limit):
                    for c in idx.rank_meshes(n, "", 1, None):
                        if c.path not in have:
                            have.add(c.path)
                            out.append(assetidx.MeshCandidate(c.path, c.name, s, why))
                if out:
                    break
            return out[:limit]
        except Exception:  # noqa: BLE001 - 보조 기능
            return []

    def _on_search(self, row: _Row, text: str) -> None:
        text = (text or "").strip()
        if not text:
            self._fill_combo(row.combo, row.base, pick_first=row.sure)
            return
        idx = self.index
        found = idx.search(text) if idx else []
        if idx and len(found) < 3:      # 한국어·영어 뜻으로도 찾는다 (예: '늑대' -> GYCY_Lang)
            have = {c.path for c in found}
            found = found + [c for c in self._gloss_cands(text) if c.path not in have]
        self._fill_combo(row.combo, found)

    # ================================================================== 게임 속 이름으로 찾기
    def _load_index_cached(self) -> Optional[assetidx.AssetIndex]:
        if self.index is None:
            idx = assetidx.load_index(INDEX_CACHE, self.cfg.get("game_dir", ""))
            if idx is not None:
                self.index = idx
        return self.index

    def name_book(self) -> Optional[gamenames.NameBook]:
        return gamenames.NameBook.load(NAMES_CACHE, self.cfg.get("game_dir", ""))

    def _ensure_names(self, a, w) -> None:
        """글자 사전(.locres → names.json)이 없거나 다른 게임 것이면 자동으로 만든다 (예전의 '글자 사전 만들기' 버튼을 흡수).

        실패해도 조용히 계속한다 — 사전이 없으면 카탈로그는 이름 연결 없이 묶기만 한다."""
        try:
            if self.name_book() is not None:
                return
            w.progress.emit(0, 1, "게임 글자 사전(.locres) 만드는 중 (게임마다 처음 한 번)")
            shutil.rmtree(LOCRES_DIR, ignore_errors=True)
            files = extractor.export_locres(*a, LOCRES_DIR)
            book = gamenames.build_book(files)
            if len(book):
                book.save(NAMES_CACHE, self.cfg.get("game_dir", "") or a[1])
                self._charcat_memo = None   # 새 사전으로 카탈로그를 다시 만들게
        except Exception:  # noqa: BLE001 - 사전은 보조 입력
            pass

    def _motion_index(self):
        return self.index or self._load_index_cached()

    def _motion_extra(self, text: str, use_ai: bool):
        """(AI 영어 낱말, 안내). AI 가 없거나 실패해도 내장 낱말표로 계속한다."""
        if not use_ai:
            return [], ""
        try:
            return vision.motion_keywords(self.provider(), text), ""
        except Exception as e:  # 네트워크/모델 오류는 검색을 막지 않는다
            return [], "AI 낱말 변환 실패(내장 낱말표만 사용): %s" % str(e)[:120]

    def search_anims_by_text(self) -> None:
        text = self.anim_q.text().strip()
        if not text:
            self._refill_anims()
            return
        idx = self._motion_index()
        if idx is None:
            QMessageBox.warning(self, "설명으로 찾기", "에셋 이름 목록이 아직 없습니다. 한 번 '캐릭터 추측 시작'을 실행하세요.")
            return
        restrict = None
        chosen = self._chosen_rigged() if self.char_mode else self._selected_mesh_paths()
        if not self.anim_all.isChecked() and chosen:
            restrict = [layout.character_of(p) for p in chosen]
        use_ai = True   # 동작 설명 -> 영어 낱말 변환 (실패해도 내장 낱말표로 계속)
        kinds = self._kinds()

        def job(w):
            w.progress.emit(0, 1, "애니메이션 찾는 중")
            extra, warn = self._motion_extra(text, use_ai)
            hits, q = motionsearch.search_anims(idx, text, extra, restrict, kinds=kinds)
            return hits, q, extra, warn

        def done(out):
            hits, q, extra, warn = out
            self.anim_list.clear()
            for h in hits:
                it = QListWidgetItem("%3d%%  %s · %s    %s" % (round(min(max(h.score, 0.0), 1.0) * 100), h.monster, os.path.basename(h.path).split(".")[0], h.why))
                it.setData(Qt.ItemDataRole.UserRole, h.path)
                it.setToolTip(h.path)
                self.anim_list.addItem(it)
            where = "고른 캐릭터 안" if restrict else "전체"
            msg = "'%s' → %s%s. %s에서 애니메이션 %d개." % (text, q.describe(), (" + AI: " + ", ".join(extra)) if extra else "", where, len(hits))
            if not hits and restrict:
                msg += " '모든 캐릭터에서'를 켜 보세요."
            if kinds is not None:
                msg += " (종류: %s만)" % ("플레이어블" if kinds == {assetidx.KIND_PLAYER} else "몬스터·NPC")
            self.anim_note.setText((msg + " " + warn).strip())
            self._apply_anim_filter()

        self.win.run_async(job, done, "애니메이션 찾는 중", cancellable=True, on_cancel=lambda: self.status.setText("애니메이션 검색을 취소했습니다."))

    def delete_selected_row(self) -> None:
        cur = self.table.currentRow()
        if not (0 <= cur < len(self.rows)):
            QMessageBox.warning(self, "줄 삭제", "지울 줄을 표에서 먼저 클릭하세요.")
            return
        self.table.removeRow(cur)
        self.rows.pop(cur)
        if self.rows:  # 다음 줄을 이어서 지울 수 있게 선택을 옮긴다
            self.table.selectRow(min(cur, len(self.rows) - 1))
        self.status.setText("줄 삭제: 남은 캐릭터 %d종" % len(self.rows))

    def clear_rows(self) -> None:
        if not self.rows:
            return
        if QMessageBox.question(self, "모두 지우기", "검수 표의 %d줄을 모두 지울까요? (직접 고친 내용도 사라집니다)" % len(self.rows),
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self.rows = []
        self.table.setRowCount(0)
        self.status.setText("검수 표를 비웠습니다.")

    def add_blank_row(self) -> None:
        if self.index is None:
            idx = assetidx.load_index(INDEX_CACHE, self.cfg.get("game_dir", ""))
            if idx is None:
                QMessageBox.warning(self, "캐릭터 줄 추가", "에셋 이름 목록이 아직 없습니다. 한 번 '캐릭터 추측 시작'을 실행하세요.")
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

    def _mesh_sel_changed(self, *_a) -> None:
        self._refill_anims()

    def _chosen_rigged(self) -> List[str]:
        """캐릭터 메쉬 찾기에서 고른 메시 중 리깅된 것 (애니메이션은 리깅된 메시에만 있다)."""
        idx = self.index
        out = []
        for it in self.mesh_list.selectedItems():
            pth = it.data(Qt.ItemDataRole.UserRole)
            m = idx.mesh(pth) if idx else None
            if m is None or m.rigged:
                out.append(pth)
        return out

    def show_char_meshes(self, *_a) -> bool:
        """캐릭터 메쉬 찾기: 캐릭터 모델링 전체를 이름으로 검색해 (리깅됨 먼저, 리깅 안 됨 다음) 나열한다."""
        idx = self.index or self._load_index_cached()
        if idx is None or not idx:
            QMessageBox.warning(self, "캐릭터 메쉬 찾기", "에셋 이름 목록이 아직 없습니다. '캐릭터 메쉬 목록 열기'로 게임 파일에서 한 번 읽어 오세요.")
            return False
        meshes = idx.character_meshes(self.char_q.text(), self.char_rig.currentData() or "all", bool(self.ck_char_player.isChecked()))
        if self._cls_want() is not None:
            meshes = [m for m in meshes if self._class_ok(m.path)]
        self.char_mode = True
        self.confirmed = [m.path for m in meshes]
        self.mesh_list.clear()
        shown = meshes[: self.CHAR_LIST_MAX]
        for m in shown:
            it = QListWidgetItem("[%s] %s    (%s)" % ("리깅됨" if m.rigged else "리깅 없음", m.name, m.path))
            it.setData(Qt.ItemDataRole.UserRole, m.path)
            it.setToolTip(m.path)
            self.mesh_list.addItem(it)
        n_r = sum(1 for m in meshes if m.rigged)
        note = "캐릭터 메쉬 %d개 — 리깅됨 %d · 리깅 없음 %d" % (len(meshes), n_r, len(meshes) - n_r)
        if len(meshes) > len(shown):
            note += " (앞 %d개만 표시 — 검색어로 좁히세요)" % len(shown)
        if not idx.statics_known:
            note += ". 이 이름 목록에는 리깅 없는 메시가 없어요: '에셋 목록 새로 읽기'를 누르면 포함됩니다"
        note += ". 메시를 고르면 그 캐릭터에 해당하는 애니메이션만 아래에 나옵니다 (고르지 않으면 전체)."
        self.char_note.setText(note)
        self.status.setText("캐릭터 메쉬 찾기: " + note.split(" — ")[0] + "개")
        self._refill_anims()
        return True

    CHAR_LIST_MAX = 3000

    def confirm(self) -> None:
        self.char_mode = False
        meshes = self.chosen_meshes()
        if not meshes:
            QMessageBox.warning(self, "검수 확정", "메시가 정해진 캐릭터가 없습니다. 후보에서 고르거나 검색 칸에서 찾으세요.")
            return
        for r in self.rows:  # 검수자가 확정한 짝을 기억 -> 다음에 같은 추측이 나오면 맨 앞 후보
            p = r.chosen()
            if p:
                self.memory.remember(r.group.name, r.group.description, p)
        self.memory.save()
        try:
            # 엔티티 사전에도 학습: AI 가 냈던 이름/화면 글자 -> 확정된 메시의 캐릭터 별명
            memo = self.__dict__.get("_entstore_memo")
            og = self.__dict__.get("_orig_guess", {})
            if memo is not None and self.index is not None:
                picks = []
                for r in self.rows:
                    p = r.chosen()
                    if p:
                        o, d = og.get(id(r.group), (r.group.name, r.group.description))
                        picks.append((o, d, p))
                if identify_apply.learn_confirm(memo[1], self._catalog(self.index), picks) or picks:
                    memo[1].save()
        except Exception:  # noqa: BLE001 - 학습 실패가 확정을 막지 않게
            pass
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

    # ------------------------------------------------------------------ 종류 / 글자 거르기
    def _kinds(self) -> Optional[set]:
        """체크된 종류. 둘 다 켜져 있거나 둘 다 꺼져 있으면 None(전체)."""
        m, p = self.ck_k_monster.isChecked(), self.ck_k_player.isChecked()
        if m == p:
            return None
        return {assetidx.KIND_MONSTER} if m else {assetidx.KIND_PLAYER}

    def _kinds_changed(self, *_a) -> None:
        self.cfg["vf_kind_monster"] = bool(self.ck_k_monster.isChecked())
        self.cfg["vf_kind_player"] = bool(self.ck_k_player.isChecked())
        self._refill_anims()

    def _apply_anim_filter(self, *_a) -> None:
        terms = textmatch.terms(self.anim_filter.text())  # 쉼표·공백 모두 '모두 포함'
        n = self.anim_list.count()
        shown = 0
        for i in range(n):
            it = self.anim_list.item(i)
            hay = (it.text() + " " + str(it.data(Qt.ItemDataRole.UserRole) or "")).lower()
            ok = all(t in hay for t in terms) and (not self.react_only.isChecked() or pairing.is_reaction(os.path.basename(str(it.data(Qt.ItemDataRole.UserRole) or it.text()))))
            it.setHidden(not ok)
            if not ok and it.isSelected():
                it.setSelected(False)  # 가려진 것은 선택에서도 뺀다 (모르는 사이에 추출되지 않게)
            shown += 1 if ok else 0
        self.anim_count.setText(("%d / %d개 표시" % (shown, n)) if (terms or self.react_only.isChecked()) else ("%d개" % n if n else ""))

    def _select_visible_anims(self) -> None:
        for i in range(self.anim_list.count()):
            it = self.anim_list.item(i)
            if not it.isHidden():
                it.setSelected(True)

    def _picked_anims(self) -> List[str]:
        return [it.data(Qt.ItemDataRole.UserRole) for it in self.anim_list.selectedItems() if not it.isHidden()]

    PLAYER_LIST_MAX = 30000

    def _fill_all_anims(self) -> None:
        """캐릭터 메쉬 찾기에서 메시를 고르지 않았을 때: 종류 체크에 맞는 애니메이션을 이름순으로 전부 (점수·개수 제한 없음)."""
        idx = self.index
        paths = idx.anims_of_kinds(self._kinds() or {assetidx.KIND_PLAYER, assetidx.KIND_MONSTER}) if idx else []
        paths = sorted(paths, key=lambda a: (os.path.basename(a).lower(), a.lower()))
        for a in paths[: self.PLAYER_LIST_MAX]:
            it = QListWidgetItem("%s    %s" % (os.path.basename(a).split(".")[0], os.path.dirname(a.replace("\\", "/"))))
            it.setData(Qt.ItemDataRole.UserRole, a)
            it.setToolTip(a)
            self.anim_list.addItem(it)
        msg = "애니메이션 전체 %d개 (이름순)." % len(paths)
        if len(paths) > self.PLAYER_LIST_MAX:
            msg += " 너무 많아 앞 %d개만 보입니다. 거르기로 좁히세요." % self.PLAYER_LIST_MAX
        self.anim_note.setText(msg + " 위에서 메시를 고르면 그 캐릭터에 해당하는 클립만 남습니다. 목록 거르기(쉼표=모두 포함)·설명으로 찾기도 쓸 수 있어요.")
        self._apply_anim_filter()

    def _refill_anims(self, *_a) -> None:
        self.anim_list.clear()
        idx = self.index
        if self.char_mode and idx is not None and not self._chosen_rigged():
            if self.mesh_list.selectedItems():
                self.anim_note.setText("리깅 없는 메시(SM_)에는 애니메이션이 없습니다. 리깅된 메시를 고르거나 선택을 풀어 전체 목록을 보세요.")
            else:
                self._fill_all_anims()
            return
        if idx is None or not self.confirmed:
            return
        label = self.label.currentData() or None
        hits = int(self.hits.value()) or None
        total = 0
        empty: List[str] = []
        players: List[str] = []
        kinds = self._kinds()
        force = True if kinds == {assetidx.KIND_PLAYER} else None     # 메시 경로에 player/pc/hero 가 있으면 아래에서 자동 판단
        hidden_kind = 0
        n_drop = 0
        skel_state: Dict[str, int] = {}
        move_notes: List[str] = []
        for p in (self._chosen_rigged() if self.char_mode else self._selected_mesh_paths()):
            m = idx.mesh(p)
            nm = m.name if m else assetidx.mesh_name(p)
            if force or assetidx.is_player_path(p):
                players.append(nm)
            cands = idx.rank_anims(p, label, hits, 60, player=force, owned=None if (force or assetidx.is_player_path(p)) else self._owned_anims(idx, p))
            cands, st, dropped = self._refine(p, cands)
            n_drop += dropped
            skel_state[st] = skel_state.get(st, 0) + 1
            mc = None if (force or assetidx.is_player_path(p)) else self._moves(idx, p)
            if mc is not None and cands:
                cands, _n_hidden = self._group_by_moves(cands, mc, assetidx.LABEL_CATEGORY.get((label or "").strip()) if label else None)
                move_notes.append("%s: %s" % (nm, mc.summary()))
            if not cands:
                empty.append(nm)
            for c in cands:
                if kinds is not None and idx.kind(c.path) not in kinds:
                    hidden_kind += 1
                    continue
                it = QListWidgetItem("%3d%%  %s · %s    %s" % (round(min(max(c.score, 0.0), 1.0) * 100), nm, os.path.basename(c.path), c.why))
                it.setData(Qt.ItemDataRole.UserRole, c.path)
                it.setToolTip(c.path)
                self.anim_list.addItem(it)
                total += 1
        msg = "애니메이션 후보 %d개 (점수 높은 순)." % total
        if move_notes:
            msg = "애니메이션 후보 %d개 — 같은 캐릭터 폴더의 것을 동작별로 묶어 앞에 두었습니다 (%s). 짝이 있는 모션은 '짝:' 으로 표시됩니다." % (total, "; ".join(move_notes[:3]))
        if players:
            msg += " 플레이어블 캐릭터(%s)는 몸이 아니라 player/pc/hero 이름이 든 공용 애니메이션을 보여줍니다." % ", ".join(players[:3])
        if empty:
            msg += " 이름이 맞는 애니메이션을 못 찾은 캐릭터: %s — 0번 탭에서 직접 검색해 보세요." % ", ".join(empty)
        if hidden_kind:
            msg += " 종류 체크로 %d개를 뺐습니다." % hidden_kind
        if skel_state.get("linked"):
            msg += " ✔ 스켈레톤 연결 확인된 재생 가능 애니메이션을 앞에 두었고" + (", 다른 스켈레톤이거나 재생 못 하는 %d개는 뺐습니다." % n_drop if n_drop else ".")
        elif skel_state.get("mesh_unknown") or skel_state.get("no_index"):
            msg += " 아직 파일 안을 읽어 확인하지 않아 이름 점수만 보입니다 — [스켈레톤으로 확인]을 누르면 실제 연결로 걸러냅니다."
        self.anim_note.setText(msg)
        self._apply_anim_filter()
        self._auto_motion_rank(msg)

    def _auto_motion_rank(self, base_msg: str) -> None:
        """메시의 '생김새 검증'처럼, 애니 후보도 영상의 실제 움직임으로 자동 정렬한다.

        고른 영상에 AI 모캡 결과(.bvh)가 있으면 후보가 채워지는 즉시 동작 비교(DTW)로 줄 세우고,
        없으면 만드는 방법을 안내만 한다. 같은 확정/같은 모캡 파일로는 한 번만 돈다."""
        if self.char_mode or not self.confirmed or self.anim_list.count() == 0:
            return
        if not bool(self.cfg.get("vf_motion_auto", True)):
            return
        mf = ""
        for sp in self.source_paths(True):
            mf = animref.motion_file(sp)
            if mf:
                break
        key = (tuple(sorted(self.confirmed)), mf)
        if not mf:
            if self.__dict__.get("_motion_hint_key") != key:
                self._motion_hint_key = key
                self.anim_note.setText(base_msg + "\n💡 영상의 AI 모캡(.bvh)이 있으면 실제 움직임이 비슷한 순으로 자동 정렬됩니다 — "
                                                  "AnimRef 의 AI 모캡으로 만들거나, [🎬 영상 동작과 비슷한 순 정렬]에서 파일을 직접 고르세요.")
            return
        if self.__dict__.get("_motion_ranked_key") == key or getattr(self.win, "_worker", None) is not None:
            return
        self._motion_ranked_key = key
        self.rank_by_motion(mf)

    def _skel_index(self):
        try:
            return self.win.char_panel._index()
        except Exception:  # noqa: BLE001
            return None

    def _refine(self, mesh_path: str, cands):
        sidx = self._skel_index()
        if sidx is None:
            return cands, "no_index", 0
        return skelgraph.refine_by_skeleton(sidx.all(), mesh_path, cands, lambda p, sc, why: assetidx.AnimCandidate(p, sc, why))

    def verify_links(self) -> None:
        """고른 메쉬와 그 후보 애니메이션의 파일 안을 읽어, 실제로 같은 스켈레톤에 연결되고 재생 가능한지 확인한다."""
        a = self.win._extract_args()
        idx = self.index
        if a is None or idx is None:
            return
        meshes = self._chosen_rigged() if self.char_mode else self._selected_mesh_paths()
        if not meshes:
            QMessageBox.warning(self, "스켈레톤 확인", "메쉬를 먼저 고르세요.")
            return
        sidx = self._skel_index()
        if sidx is None:
            return
        label = self.label.currentData() or None
        hits = int(self.hits.value()) or None
        todo = set(meshes)
        for p in meshes:
            todo.update(c.path for c in idx.rank_anims(p, label, hits, 60, player=None, owned=self._owned_anims(idx, p)))
        todo = [p for p in sorted(todo) if skelgraph.worth_inspecting(p)]

        def job(w):
            self.win.char_panel._run_inspect(w, a, sidx, sidx.pending(todo))
            return len(todo)

        self.win.run_async(job, lambda n: self._refill_anims(), "스켈레톤 연결 확인 중 (파일 안을 읽는 중)", cancellable=True)

    def _char_map(self, anims: List[str]) -> Dict[str, str]:
        """고른 메시가 둘 이상일 때, 애니메이션마다 이름이 가장 어울리는 캐릭터 폴더를 정한다 (못 정하면 경로로)."""
        meshes = [it.data(Qt.ItemDataRole.UserRole) for it in self.mesh_list.selectedItems()]
        idx = self.index
        if len(meshes) < 2 or idx is None:
            return {}
        ents = [m for m in (idx.mesh(p) for p in meshes) if m is not None]
        out: Dict[str, str] = {}
        for a in anims:
            best = max(((idx.char_score(m, a)[0], m) for m in ents), key=lambda t: t[0], default=(0.0, None))
            if best[1] is not None and best[0] > 0.2:
                out[a] = layout.character_of(best[1].path)
        return out

    def _single_char(self) -> Optional[str]:
        """고른 메시가 한 캐릭터뿐이면 애니메이션도 그 캐릭터 폴더에 모은다 (애니 경로가 달라도)."""
        names = {layout.character_of(it.data(Qt.ItemDataRole.UserRole)) for it in self.mesh_list.selectedItems()}
        return names.pop() if len(names) == 1 else None

    # ================================================================== 짝 찾기 (상대 모션 -> 맞물리는 모션)
    def find_pair(self) -> None:
        sel = [it for it in self.anim_list.selectedItems() if not it.isHidden()]
        if len(sel) != 1:
            QMessageBox.warning(self, "짝 찾기", "기준이 될 모션을 목록에서 하나만 고르세요 (예: 보스가 처형당하는 모션).")
            return
        idx = self.index
        sidx = self._skel_index()
        a = self.win._extract_args()
        if idx is None or sidx is None or a is None:
            QMessageBox.warning(self, "짝 찾기", "에셋 이름 목록과 게임 설정이 필요합니다. 0번 탭에서 게임을 설정하고 '캐릭터 추측'이나 '에셋 목록 읽기'를 먼저 하세요.")
            return
        base = sel[0].data(Qt.ItemDataRole.UserRole)
        want = assetidx.KIND_MONSTER if assetidx.path_kind(base) == assetidx.KIND_PLAYER else assetidx.KIND_PLAYER   # 반대편 캐릭터
        pool = sorted(idx.anims_of_kinds({want}))[: self.PLAYER_LIST_MAX]
        # 1) 이름이 정확히 짝인 것 (X <-> X_Player / X_Enemy): 길이를 재지 않아도 확실하다. 보스 폴더 안에 플레이어 쪽 모션이 같이 들어 있는 게임이 많다
        exact = movecatalog.exact_partners(base, idx.anims)

        def add_exact():
            for p in exact:
                it = QListWidgetItem("100%%  %s    이름이 정확히 짝 (%s 와 역할 낱말만 다름)" % (os.path.basename(p).split(".")[0], os.path.basename(base).split(".")[0]))
                it.setData(Qt.ItemDataRole.UserRole, p)
                it.setToolTip(p)
                self.anim_list.addItem(it)

        if exact:
            self.anim_list.clear()
            add_exact()
            self.anim_note.setText("기준 '%s' 의 짝을 이름으로 바로 찾았습니다: %d개 (맨 위). 길이가 비슷한 다른 후보도 이어서 찾는 중…" % (os.path.basename(base).split(".")[0], len(exact)))
            pool = [p for p in pool if p not in set(exact)]
        if not pool and exact:
            return
        if not pool:
            QMessageBox.warning(self, "짝 찾기", "반대편 캐릭터의 애니메이션 이름이 목록에 없습니다.")
            return
        tol = int(self.pair_tol.value())
        self.cfg["vf_pair_tol"] = tol
        bridge.save_config(self.cfg)

        def lens(paths):
            out = {}
            for r in sidx.all():
                if r.path in paths and r.kind in ("anim", "montage") and r.length > 0 and r.frames > 0:
                    out[r.path] = (r.frames, r.length)
            return out

        def job(w):
            need = [p for p in [base] + pool if skelgraph.worth_inspecting(p)]
            todo = sidx.pending(need)
            if todo:
                w.progress.emit(0, len(todo), "길이를 알아내려고 파일 안을 읽는 중 (%d개, 처음 한 번만)" % len(todo))
                self.win.char_panel._run_inspect(w, a, sidx, todo)
            known = lens(set([base] + pool))
            if base not in known:
                raise RuntimeError("기준 모션의 길이를 읽지 못했습니다 (몽타주이거나 읽을 수 없는 파일일 수 있어요).")
            fr, ln = known[base]
            cands = [(p, *known.get(p, (-1, -1.0))) for p in pool]
            return pairing.pair_rank(base, fr, ln, cands, tol), (fr, ln), len(known) - 1

        def done(out):
            hits, (fr, ln), n_known = out
            self.anim_list.clear()
            add_exact()
            for h in hits:
                it = QListWidgetItem("%3d%%  %s    %s" % (round(h.score * 100), os.path.basename(h.path).split(".")[0], h.why))
                it.setData(Qt.ItemDataRole.UserRole, h.path)
                it.setToolTip(h.path)
                self.anim_list.addItem(it)
            self.anim_note.setText((("이름이 정확히 짝인 모션 %d개를 맨 위에 두었습니다. " % len(exact)) if exact else "") +
                                   "기준 '%s' (%d프레임, %.2f초) → %s 쪽 모션 %d개 중 길이 ±%d프레임 안 %d개 (길이를 읽은 것 %d개). "
                                   "이제 [AI 회의로 찾기]로 영상과 맞는 것을 고르세요." % (os.path.basename(base).split(".")[0], fr, ln, "플레이어" if want == assetidx.KIND_PLAYER else "몬스터·보스",
                                                                           len(pool), tol, len(hits), n_known))
            self._apply_anim_filter()

        self.win.run_async(job, done, "짝 찾는 중", cancellable=True, on_cancel=lambda: self.anim_note.setText("짝 찾기를 취소했습니다 (목록은 그대로)."))

    # ================================================================== AI 회의
    def _panel_members(self, picked: Dict[str, bool], second_model: str):
        """고른 참가자마다 Provider 를 만든다 (모델/키는 위 '인식 방식' 설정을 따른다)."""
        self._save_settings()
        cfg = dict(self.cfg)
        cfg["vf_claude_key"] = self._keys.get("claude", "").strip()
        cfg["vf_openai_key"] = self._keys.get("openai", "").strip()
        out = []
        if picked.get("ollama"):
            out.append(panel.Member("Ollama 비전 (%s)" % (cfg.get("vf_ollama_model") or vision.OLLAMA_MODELS[0]), vision.make_provider("ollama", cfg)))
        if picked.get("ollama2") and second_model.strip():
            c2 = dict(cfg)
            c2["vf_ollama_model"] = second_model.strip()
            out.append(panel.Member("Ollama 비전 (%s)" % second_model.strip(), vision.make_provider("ollama", c2)))
        if picked.get("gptoss"):
            out.append(panel.Member("GPT-OSS", vision.make_provider("gptoss", cfg)))
        if picked.get("claude"):
            out.append(panel.Member("Claude", vision.make_provider("claude", cfg)))
        if picked.get("openai"):
            out.append(panel.Member("다른 AI (%s)" % (cfg.get("vf_oa_model") or self._preset()[2]), vision.make_provider("openai", cfg)))
        return out

    def _ask_panel_members(self, title: str, info: str):
        """회의 참가자·라운드 고르는 창. 취소/잘못 고르면 None, 아니면 (members, rounds)."""
        d = QDialog(self)
        d.setWindowTitle(title)
        lay = QVBoxLayout(d)
        intro = QLabel("회의에 참가할 AI 를 고르세요. 이미지를 보는 AI 가 한 명 이상 필요합니다. 모델과 키는 위 '인식 방식' 설정을 따릅니다.\n" + info)
        intro.setWordWrap(True)
        lay.addWidget(intro)
        opts = [("ollama", "Ollama 비전 모델 (설정의 모델, 예: qwen2.5vl:7b) — 이미지 봄", True),
                ("ollama2", "Ollama 두 번째 비전 모델 — 이미지 봄", False),
                ("gptoss", "GPT-OSS-20B — 글 전용 (다른 AI 의 묘사를 읽고 추론)", True),
                ("claude", "Claude (API 키) — 이미지 봄", False),
                ("openai", "다른 AI (Gemini·GPT·Groq … 설정의 서비스) — 이미지 모델이어야 함", False)]
        boxes = {}
        for k, t, on in opts:
            cb = QCheckBox(t)
            cb.setChecked(bool(self.cfg.get("vf_panel_" + k, on)))
            boxes[k] = cb
            lay.addWidget(cb)
        h = QHBoxLayout()
        h.addWidget(QLabel("두 번째 비전 모델"))
        second = QLineEdit(self.cfg.get("vf_panel_model2", "gemma3:12b"))
        h.addWidget(second, 1)
        h.addWidget(QLabel("라운드"))
        rounds = QSpinBox()
        rounds.setRange(1, 4)
        rounds.setValue(int(self.cfg.get("vf_panel_rounds", 2)))
        h.addWidget(rounds)
        lay.addLayout(h)
        bh = QHBoxLayout()
        ok, cancel = QPushButton("회의 시작"), QPushButton("취소")
        ok.clicked.connect(d.accept)
        cancel.clicked.connect(d.reject)
        bh.addStretch(1)
        bh.addWidget(ok)
        bh.addWidget(cancel)
        lay.addLayout(bh)
        if not d.exec():
            return None
        picked = {k: bool(cb.isChecked()) for k, cb in boxes.items()}
        for k, v_ in picked.items():
            self.cfg["vf_panel_" + k] = v_
        self.cfg["vf_panel_model2"] = second.text().strip()
        self.cfg["vf_panel_rounds"] = int(rounds.value())
        bridge.save_config(self.cfg)
        members = self._panel_members(picked, second.text())
        if not members:
            QMessageBox.warning(self, "AI 회의", "참가자를 한 명 이상 고르세요.")
            return None
        if not any(m.sees for m in members):
            QMessageBox.warning(self, "AI 회의", "이미지를 볼 수 있는 AI 를 한 명 이상 고르세요 (GPT-OSS 는 그림을 못 봅니다).")
            return None
        for m in members:
            ok_, msg, _n = m.provider.check() if m.provider.key in ("claude", "openai", "ollama", "gptoss") else (True, "", [])
            if not ok_:
                QMessageBox.warning(self, "AI 회의 — %s" % m.name, msg)
                return None
        return members, int(rounds.value())

    @staticmethod
    def _transcript_text(verdict) -> str:
        lines = []
        for t in verdict.transcript:
            lines.append("[라운드 %d] %s\n%s\n" % (t.round, t.speaker, t.text))
        lines.append("— 합의 순위 —")
        for i, (nm_, sc, who) in enumerate(verdict.ranking[:10], 1):
            lines.append("%d. %s  (점수 %d%%, 지지: %s)" % (i, nm_, round(sc * 100), ", ".join(who)))
        if verdict.errors:
            lines.append("\n⚠ " + " / ".join(verdict.errors))
        return "\n".join(lines)

    def _show_text_dialog(self, title: str, text: str) -> None:
        d = QDialog(self)
        d.setWindowTitle(title)
        lay = QVBoxLayout(d)
        box = QPlainTextEdit()
        box.setReadOnly(True)
        box.setPlainText(text)
        box.setMinimumSize(720, 480)
        lay.addWidget(box)
        b = QPushButton("닫기")
        b.clicked.connect(d.accept)
        lay.addWidget(b)
        d.exec()

    # ---- 캐릭터 추측을 AI 회의로
    def start_guess_panel(self) -> None:
        """영상(원본)마다 장면 시트를 만들어 비전 AI 와 글 전용 AI(GPT-OSS)가 토론해 캐릭터를 정한다. 결과는 같은 검수 표에 들어간다."""
        if self.rb_mode_char.isChecked():
            QMessageBox.warning(self, "AI 회의", "'캐릭터 메쉬 찾기' 모드는 AI 없이 이름으로 찾습니다. '몬스터 찾기' 모드로 바꿔 주세요.")
            return
        srcs = self.source_paths()
        if not srcs:
            QMessageBox.warning(self, "AI 회의", "영상/이미지를 먼저 추가하세요.")
            return
        a = self.win._extract_args()
        if a is None:
            return
        got = self._ask_panel_members("AI 회의로 캐릭터 추측", "영상마다 장면 시트를 한 장 만들어 회의합니다. 영상이 많으면 AI 호출이 그만큼 늘어납니다.")
        if got is None:
            return
        members, n_rounds = got
        per, hint, pat = int(self.per.value()), self.hint.text().strip(), self.filter.text().strip()
        hint = hint or (self.win.ex_gname.text().strip() if hasattr(self.win, "ex_gname") else "")
        known = gamenames.parse_known(self.known.toPlainText())
        book = self.name_book() if known else None
        ff = frames.find_ffmpeg(self.cfg.get("ffmpeg"))
        limit = min(m.provider.vocab_limit() for m in members)
        if self.ck_fast.isChecked():
            limit = min(limit, vision.FAST_VOCAB)

        def job(w):
            idx = self._ensure_index(a, w, False)
            shutil.rmtree(FRAME_DIR, ignore_errors=True)

            def sampling(i, n, name):
                if w.cancel.is_set():
                    raise panel.PanelStopped()
                w.progress.emit(i, n, "장면 뽑는 중 " + name)

            fr = framepick.sample_sources_best(ff, srcs, FRAME_DIR, max(per, 6), 24, sampling)
            vocab, notes = self._vocab(idx, limit, pat, style="names")
            if known:
                gamenames.resolve_known(known, idx, book)
                for k in known:
                    if k.top and k.top.name not in vocab:
                        vocab = [k.top.name] + list(vocab)
                    notes.append("아는 정보: %s → %s" % (k.name, (k.top.name + " (%d%%)" % round(k.top.score * 100)) if k.top else "리소스를 못 찾음"))
            by_src: Dict[str, List[str]] = {}
            for f in fr:
                by_src.setdefault(f.source, []).append(f.path)
            results = panel.run_char_panel(members, by_src, vocab, hint, [k.prompt_line() for k in known], n_rounds, FRAME_DIR,
                                           on_progress=lambda m: w.progress.emit(0, 1, m), stop=lambda: w.cancel.is_set())
            dets = [d for r in results for d in r.detections]
            text = "\n\n".join("===== %s =====\n%s" % (os.path.basename(r.source), self._transcript_text(r.verdict)) for r in results)
            errs = [e for r in results for e in r.verdict.errors]
            return {"idx": idx, "groups": gamenames.merge_known(vision.aggregate(dets), known), "errors": errs, "notes": notes + ["AI 회의로 추측 (참가: %s, %d라운드)" % (", ".join(m.name for m in members), n_rounds)],
                    "frames": len(fr), "vocab": len(vocab), "panel_text": text}

        def done(res):
            self._after_guess(res)
            self.last_panel_text = res.get("panel_text", "")
            if self.last_panel_text:
                self._show_text_dialog("AI 회의 결과 — 캐릭터 추측 (표는 검수 칸에 채워졌습니다)", self.last_panel_text)

        self.win.run_async(job, done, "AI 회의로 캐릭터 추측 중 (AI 가 느리면 몇 분 걸립니다)", cancellable=True,
                           on_cancel=lambda: self.status.setText("AI 회의를 취소했습니다."))

    def open_panel(self) -> None:
        names = []
        for i in range(self.anim_list.count()):
            it = self.anim_list.item(i)
            if not it.isHidden():
                names.append(os.path.splitext(os.path.basename(str(it.data(Qt.ItemDataRole.UserRole))))[0])
        names = names[:MAX_PANEL_NAMES]
        if not names:
            QMessageBox.warning(self, "AI 회의", "회의에 올릴 애니메이션 목록이 비어 있습니다. 먼저 캐릭터를 고르거나 검색해서 후보를 만드세요.")
            return
        if not self.source_paths(True):
            QMessageBox.warning(self, "AI 회의", "영상/이미지를 먼저 추가하세요.")
            return
        got = self._ask_panel_members("AI 회의로 찾기", "회의에 올리는 애니메이션: 지금 목록 위쪽 %d개." % len(names))
        if got is None:
            return
        members, rounds = got
        srcs = self.source_paths(True)
        hint = self.hint.text().strip() or (self.win.ex_gname.text().strip() if hasattr(self.win, "ex_gname") else "")
        ff = frames.find_ffmpeg(self.cfg.get("ffmpeg"))
        n_rounds = int(rounds)

        def job(w):
            shutil.rmtree(FRAME_DIR, ignore_errors=True)
            w.progress.emit(0, 1, "장면 시트 만드는 중")
            fr = frames.sample_sources(ff, srcs, FRAME_DIR, 12, 12)
            sheet = panel.make_sheet([f.path for f in fr], os.path.join(FRAME_DIR, "panel_sheet.jpg"))
            return panel.run_panel(members, sheet, names, hint, n_rounds, lambda m: w.progress.emit(0, 1, m)), sheet

        self.win.run_async(job, self._after_panel, "AI 회의 중 (AI 가 느리면 몇 분 걸립니다)", cancellable=True,
                           on_cancel=lambda: self.anim_note.setText("AI 회의를 취소했습니다 (목록은 그대로)."))

    def _after_panel(self, out) -> None:
        verdict, sheet = out
        text = self._transcript_text(verdict)
        rank = {nm_: (sc, who) for nm_, sc, who in verdict.ranking}
        keep = []
        for i in range(self.anim_list.count()):
            it = self.anim_list.item(i)
            pth = it.data(Qt.ItemDataRole.UserRole)
            base = os.path.splitext(os.path.basename(str(pth)))[0]
            keep.append((rank.get(base), it.text(), pth, str(it.toolTip() or "")))
        keep.sort(key=lambda t: -(t[0][0] if t[0] else -1.0))
        self.anim_list.clear()
        for r, txt, pth, tip in keep:
            it = QListWidgetItem(("🧑‍⚖ %3d%%  " % round(r[0] * 100) if r else "") + txt)
            it.setData(Qt.ItemDataRole.UserRole, pth)
            it.setToolTip(tip + (("\nAI 회의 지지: " + ", ".join(r[1])) if r else ""))
            self.anim_list.addItem(it)
        self.last_panel_text = text
        self.anim_note.setText("AI 회의 완료 — 합의 1위: %s. 목록을 합의 순으로 다시 줄 세웠습니다. 눈으로 확인하고 고르세요." % (verdict.ranking[0][0] if verdict.ranking else "없음"))
        self._show_text_dialog("AI 회의 결과", text)

    # ================================================================== 동작으로 비교해 정렬
    def rank_by_motion(self, query_path: Optional[str] = None) -> None:
        """영상에서 뽑은 3D 동작(BVH 등)과 목록의 애니메이션을 실제 움직임으로 비교해 비슷한 순으로 다시 줄 세운다."""
        rows = [i for i in range(self.anim_list.count()) if not self.anim_list.item(i).isHidden()][:MAX_MOTION_RANK]
        if not rows:
            QMessageBox.warning(self, "동작으로 정렬", "정렬할 애니메이션 목록이 비어 있습니다. 먼저 후보를 만들거나 검색하세요.")
            return
        win = self.win
        auto_note = ""
        if query_path is None:       # 1번 박스에서 고른(없으면 전체) 영상 중 AI 모캡 .bvh 가 이미 있는 것을 자동으로 쓴다
            for sp in self.source_paths(True):
                mf = animref.motion_file(sp)
                if mf:
                    query_path, auto_note = mf, os.path.basename(sp)
                    break
        if query_path is None:
            start = self.cfg.get("vf_motion_dir", "") or ""
            query_path, _f = QFileDialog.getOpenFileName(self, "영상에서 뽑은 3D 동작 파일 (AnimRef AI 모캡 .bvh 등)", start,
                                                         "모션 (*.bvh *.glb *.gltf *.fbx *.psa);;모든 파일 (*)")
        if not query_path:
            return
        self.cfg["vf_motion_dir"] = os.path.dirname(query_path)
        a = win._extract_args()
        if a is None:
            return
        paths = [self.anim_list.item(i).data(Qt.ItemDataRole.UserRole) for i in rows]
        tmp = os.path.join(tempfile.gettempdir(), "motionbridge_motionrank")
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp, exist_ok=True)
        lay = layout.Layout(tmp, "cmp", force_char=self._single_char())
        lay.chars.update(self._char_map(paths))
        total = self.anim_list.count()
        skips: List[str] = []

        def job(w):
            w.progress.emit(0, 1, "영상 동작 파일 읽는 중")
            q = loader.load_model(query_path)
            if not q.animations:
                raise RuntimeError("이 파일에는 동작(애니메이션)이 없습니다: " + os.path.basename(query_path))

            def ev(e):
                if e.get("type") == "skip":
                    skips.append(os.path.basename(e.get("path", "")))
                if e.get("type") in ("exported", "skip"):
                    w.progress.emit(0, len(paths), "후보 뽑는 중 " + os.path.basename(e.get("path", "")))

            files = extractor.export_assets(*a, paths, tmp, ev, kind="anim", lay=lay)
            psa = [f for f in files if f.lower().endswith(".psa")]
            cands = []
            for i, f in enumerate(psa):
                if w.cancel.is_set():
                    return {}, len(psa)
                w.progress.emit(i, len(psa), "동작 비교 중 " + os.path.basename(f))
                try:
                    m = loader.load_model(f)
                except Exception as e:  # noqa: BLE001 - 읽지 못한 것은 건너뛴다
                    skips.append("%s (%s)" % (os.path.basename(f), str(e)[:40]))
                    continue
                if m.animations:
                    cands.append((os.path.splitext(os.path.basename(f))[0].lower(), m.skeleton, m.animations[0]))
            ranked = motionmatch.rank((q.skeleton, q.animations[0]), cands)
            if not ranked and cands:
                raise RuntimeError("영상 동작 파일의 본 이름을 사람 몸으로 알아보지 못했습니다 (골반·허벅지·팔 본이 필요). AnimRef AI 모캡의 .bvh 를 쓰세요.")
            return {k: sc for k, sc in ranked}, len(psa)

        def done(out):
            res, n = out
            if not res:
                self.anim_note.setText("동작 비교를 하지 못했습니다." + ((" 건너뜀: " + ", ".join(skips[:3])) if skips else ""))
                return
            keep = []
            for i in range(self.anim_list.count()):
                it = self.anim_list.item(i)
                pth = it.data(Qt.ItemDataRole.UserRole)
                sc = res.get(os.path.basename(str(pth)).split(".")[0].lower())
                keep.append((sc, it.text(), pth, str(pth)))  # 항목 툴팁은 경로였다
            keep.sort(key=lambda t: -(t[0].score if t[0] else -1.0))
            self.anim_list.clear()
            for sc, text, pth, tip in keep:
                head = ("동작 %3d%%%s  " % (round(sc.score * 100), " ↔" if sc.mirrored else "")) if sc else "동작 비교 안 됨  "
                it = QListWidgetItem(head + text)
                it.setData(Qt.ItemDataRole.UserRole, pth)
                it.setToolTip(tip + ("\n좌우 반전(거울)했을 때 더 비슷합니다 (평균 %.1f°)" % sc.deg if sc and sc.mirrored else ("\n평균 각도 차 %.1f°" % sc.deg if sc else "")))
                self.anim_list.addItem(it)
            top = max(res.values(), key=lambda s: s.score)
            more = (" (위쪽 %d개만 비교, 나머지는 뒤에)" % len(paths)) if total > len(paths) else ""
            self.anim_note.setText(("[%s 의 모캡 파일을 자동으로 썼습니다] " % auto_note if auto_note else "") + "영상 동작과 비슷한 순으로 정렬했습니다 — %d개 비교, 가장 비슷한 점수 %d%%%s. 점수는 팔다리·몸통 뼈 방향이 얼마나 같은지(시간 맞춤, 좌우 반전 포함)이고, ↔ 는 거울 반전입니다.%s"
                                   % (len(res), round(top.score * 100), more, (" 건너뜀 %d" % len(skips)) if skips else ""))
            self._apply_anim_filter()

        win.run_async(job, done, "동작 비교 중", cancellable=True, on_cancel=lambda: self.anim_note.setText("동작 비교를 취소했습니다 (목록은 그대로)."))

    # ================================================================== 뷰포트로 열기
    def _anim_double_clicked(self, item, *_a) -> None:
        self.open_in_viewport([item.data(Qt.ItemDataRole.UserRole)])

    def open_in_viewport(self, anims: Optional[List[str]] = None) -> None:
        if anims is None:
            anims = self._picked_anims()
        if not anims:
            QMessageBox.warning(self, "뷰포트로 열기", "열 애니메이션을 목록에서 고르세요 (Ctrl/Shift 로 여러 개, 더블클릭은 하나).")
            return
        a = self.win._extract_args()
        if a is None:
            return
        win = self.win
        out = win.ex_out.text().strip()
        if not out:
            return
        lay = win.make_layout(self._single_char())
        lay.chars.update(self._char_map(anims))
        bridge.save_config(self.cfg)
        win._ex_skips = []

        def job(w):
            def ev(e):
                if e.get("type") == "skip":
                    win._ex_skips.append("%s — %s" % (os.path.basename(e.get("path", "")), e.get("reason", "")))
                if e.get("type") in ("exported", "skip"):
                    w.progress.emit(0, len(anims), os.path.basename(e.get("path", "")))
            files = extractor.export_assets(*a, anims, out, ev, kind="anim", lay=lay)
            psa = [f for f in files if f.lower().endswith(".psa")]
            if not psa:
                raise RuntimeError("애니메이션을 뽑지 못했습니다. " + " / ".join(win._ex_skips[:3]))
            w.progress.emit(0, 1, "모션 읽는 중")
            added, skipped = win.pr.add_motions(psa)      # 지금 목록에 이어 붙인다 (소스를 갈아엎지 않음)
            return files, added, skipped

        def done(res):
            files, added, skipped = res
            n = len(added)
            self.last_files = list(files)
            win.ex_files.set_paths(files)
            win._added_done([f for f in files if f.lower().endswith(".psa")], (added, skipped), play=True)  # 목록에 더하고 바로 재생 (탭은 옮기지 않는다 — 이 탭에 머물며 더 고를 수 있게)
            msg = "소스 모션 %d개를 뷰포트로 열었습니다 — 왼쪽 '모션 목록'에 올라갔습니다" % n
            if skipped:
                msg += " (건너뜀 %d: %s)" % (len(skipped), skipped[0][:80])
                if added:
                    QMessageBox.information(self, "뷰포트로 열기", "일부는 열지 못했습니다:\n" + "\n".join(skipped)[:700])
            win.statusBar().showMessage(msg)

        win.run_async(job, done, "애니메이션 뽑는 중")

    def open_dropped(self, kind: str, paths: List[str]) -> None:
        """이 탭의 목록에서 뷰포트로 끌어다 놓았을 때: 더블클릭과 같은 방식으로 열고, 탭은 그대로 둔다 (파일·매핑 탭으로 이동하지 않음)."""
        if not paths:
            return
        if kind == "mesh":
            if len(paths) > 1:
                self.win.statusBar().showMessage("메시는 한 번에 하나만 열 수 있어 첫 번째(%s)를 열었습니다" % assetidx.mesh_name(paths[0]))
            self.open_mesh_in_viewport(paths[0])
        else:
            self.open_in_viewport(list(paths))

    def _mesh_double_clicked(self, item, *_a) -> None:
        self.open_mesh_in_viewport(item.data(Qt.ItemDataRole.UserRole))

    def open_mesh_in_viewport(self, mesh: str) -> None:
        """고른 메시를 리깅(스켈레톤+스킨)이 든 GLB 로 뽑아 바로 뷰포트의 타깃으로 연다 (Blender 필요 없음)."""
        a = self.win._extract_args()
        if a is None:
            return
        win = self.win
        out = win.ex_out.text().strip()
        if not out:
            return
        lay = win.make_layout(layout.character_of(mesh))
        tex = bool(win.ex_tex.isChecked())
        bridge.save_config(self.cfg)
        win._ex_skips = []
        name = assetidx.mesh_name(mesh)

        def job(w):
            def ev(e):
                if e.get("type") == "skip":
                    win._ex_skips.append("%s — %s" % (os.path.basename(e.get("path", "")), e.get("reason", "")))
                if e.get("type") in ("exported", "skip"):
                    w.progress.emit(0, 1, os.path.basename(e.get("path", "")))
            files = extractor.export_assets(*a, [mesh], out, ev, kind="mesh", mesh_format="glb", textures=tex, lay=lay)
            glb = next((f for f in files if f.lower().endswith(".glb")), None)
            if not glb:
                raise RuntimeError("메시를 뽑지 못했습니다. " + " / ".join(win._ex_skips[:3]))
            w.progress.emit(0, 1, "메시 읽는 중")
            m = win.pr.load_target(glb)
            return files, glb, m.skeleton.n

        def done(res):
            files, glb, nbones = res
            self.last_files = list(files)
            win.ex_files.set_paths(files)
            win._after_load("target")   # 탭은 옮기지 않는다
            win.statusBar().showMessage("메시를 뷰포트로 열었습니다: %s (본 %d개)" % (name, nbones) if nbones else
                                        "메시를 열었지만 스켈레톤(리깅)이 없어요: %s — 다른 후보를 써 보세요" % name)

        win.run_async(job, done, "메시 여는 중 (%s)" % name)

    # ================================================================== 추출
    def extract(self, what: str = "both") -> None:
        """what: 'mesh' 메시만 / 'anim' 애니메이션만 / 'both' 둘 다."""
        what = what if what in ("mesh", "anim") else "both"
        meshes = [it.data(Qt.ItemDataRole.UserRole) for it in self.mesh_list.selectedItems()] if what != "anim" else []
        anims = self._picked_anims() if what != "mesh" else []
        if not meshes and not anims:
            QMessageBox.warning(self, "추출", {"mesh": "추출할 메시를 목록에서 고르세요.", "anim": "추출할 애니메이션을 목록에서 고르세요."}.get(what, "추출할 메시나 애니메이션을 목록에서 고르세요."))
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
        lay = win.make_layout(self._single_char())
        lay.chars.update(self._char_map(anims))
        edit = self.edit_panel.options()
        self.edit_panel.save()
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
                files += extractor.export_meshes(*a, meshes, out, fmts, variants, preset, win.pr.blender, tex, keep_raw, ev, win._ex_skips.append, lay=lay)
            if anims:
                got = extractor.export_assets(*a, anims, out, ev, kind="anim", lay=lay)
                files += got
                if edit.active():  # 늘리기/압축 등: 추출한 PSA 옆에 가공본을 만든다
                    infos: List[str] = []
                    made, warns = extractor.edit_psa_files(got, edit, on_info=lambda f, i: infos.append("%s — %s" % (os.path.basename(f), i.summary())))
                    files += made
                    win._ex_skips += ["가공(%s): %s" % (edit.describe(), x) for x in infos[:3]] + warns
            return files

        win.run_async(job, self._after_extract, "추출 중")

    def _after_extract(self, files) -> None:
        win = self.win
        self.last_files = list(files or [])
        win.ex_files.set_paths(files)
        if not files:
            why = [x for x in getattr(win, "_ex_skips", []) if x][:6]
            QMessageBox.warning(self, "추출", "추출된 파일이 없습니다.\n\n" + ("\n".join(why) if why else "도우미가 이유를 알려주지 않았습니다."))
            return
        win.statusBar().showMessage("추출 완료: %d개 — '0. 게임에서 추출' 탭 4) 에서 폴더를 열 수 있습니다" % len(files))
        win.tabs.setCurrentIndex(0)

    # ================================================================== AnimRef 연결
    def open_query_file(self, path: str = "") -> None:
        """AnimRef 가 만든 query.json 을 골라 같은 화면을 채운다 (실시간 연결과 같은 동작)."""
        if not path:
            lib = animlink.animref_settings().get("library_dir") or ""
            start = os.path.join(lib, "handoff") if lib else self.cfg.get("last_dir", "")
            path, _ = QFileDialog.getOpenFileName(self, "AnimRef query.json 선택", self.cfg.get("animref_query_dir") or start, "query.json (query.json *.json)")
        if not path:
            return
        try:
            q = animlink.parse_query(path)
        except animlink.LinkError as e:
            QMessageBox.warning(self, "AnimRef 가져오기", str(e))
            return
        self.cfg["animref_query_dir"] = os.path.dirname(os.path.dirname(path))
        bridge.save_config(self.cfg)
        self.load_query(q)
        self.win.tabs.setCurrentIndex(self.win.tabs.indexOf(self))

    def load_query(self, q: dict) -> None:
        """AnimRef 가 보낸 요청으로 화면을 채운다 (UI 스레드에서 불린다)."""
        self.link_query = q
        # 보낼 때마다 목록에 쌓인다 (이미 있는 파일은 건너뜀). 비우려면 '선택한 원본 지우기'/'목록 비우기'.
        had = self.src_list.count() > 0
        have = set(self.source_paths(False))
        for p in q.get("paths", []):
            if p not in have:
                self._add_source(p, q.get("label", ""), q.get("title", ""))
                have.add(p)
        if q.get("label") in LABEL_CHOICES:
            self.label.setCurrentIndex(max(self.label.findData(q["label"]), 0))
        hits = q.get("hits") or []
        if hits:  # 타격 시각 개수 = 타수 (AnimRef 가 소리·번쩍임으로 찾은 값이라 틀릴 수 있음)
            self.hits.setValue(min(len(hits), 9))
        ch = q.get("character") or {}
        if ch.get("name"):  # AnimRef 에서 유저가 정한 캐릭터 -> '아는 정보'에 넣어 이름으로 리소스를 먼저 찾는다
            have = [k.name.lower() for k in gamenames.parse_known(self.known.toPlainText())]
            if ch["name"].lower() not in have:
                cur = self.known.toPlainText().strip()
                self.known.setPlainText((cur + "\n" if cur else "") + ch["name"] + (" — %s" % ch["kind"] if ch.get("kind") else ""))
        if not had:      # 이미 쌓아 둔 원본이 있으면 지금까지의 추측·검수 결과는 그대로 둔다 (새 클립은 '캐릭터 추측 시작'을 다시 누르면 더해진다)
            self.rows, self.confirmed = [], []
            self.char_mode = False
            self.table.setRowCount(0)
            self.mesh_list.clear()
            self.anim_list.clear()
        facts = []
        if q.get("label"):
            facts.append("분류 %s" % q["label"])
        if hits:
            facts.append("타격 시각 %s초 (AnimRef 추정, 틀릴 수 있음)" % ", ".join("%g" % t for t in hits))
        if ch.get("name"):
            facts.append("캐릭터 %s" % ch["name"])
        self._update_src_note(prefix=" · ".join(facts))
        n = len(q.get("paths", []))
        missing = [p for p in q.get("paths", []) if not os.path.isfile(p)]
        nm = animlink.display_name((q.get("paths") or [""])[0], q.get("title", "")) if n else ""
        self.link_banner.setText("AnimRef 요청%s — %s 를 받았어요%s."
                                 % (" #%s" % q["id"] if q.get("id") else "", ("'%s'" % nm) if n == 1 else "영상/클립 %d개" % n, " · " + " · ".join(facts) if facts else ""))
        if missing:
            self.link_banner.setText(self.link_banner.text() + "\n⚠ 영상 파일이 없습니다: " + wrappable(missing[0]))
        if n and not missing:  # 받기만 한다 — 캐릭터 추측은 자동으로 시작하지 않는다 (인식 방식을 고른 뒤 직접 시작)
            sc = self.win.scan
            ready = sc is not None and sc.found and sc.root == self.win.ex_game.text().strip()
            self.link_banner.setText(self.link_banner.text() + ("\n(인식 방식을 확인하고 '캐릭터 추측 시작'을 눌러 주세요.)" if ready else
                                     "\n(먼저 '0. 게임에서 추출' 탭에서 게임 폴더를 검사한 뒤 '캐릭터 추측 시작'을 누르세요.)"))
