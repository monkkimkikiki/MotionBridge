"""공용 위젯/유틸."""
import os
import subprocess
import sys
import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QThread, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..core import Cancelled


def card(title=None, desc=None):
    f = QFrame()
    f.setObjectName("card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(20, 18, 20, 18)
    lay.setSpacing(10)
    if title:
        t = QLabel(title)
        t.setObjectName("cardTitle")
        lay.addWidget(t)
    if desc:
        lay.addWidget(muted(desc))
    return f, lay


def muted(text):
    l = QLabel(text)
    l.setObjectName("muted")
    l.setWordWrap(True)
    return l


def restyle(w, name):
    w.setObjectName(name)
    w.style().unpolish(w)
    w.style().polish(w)


def open_path(path):
    """경로를 기본 프로그램/탐색기로 연다. 지워졌으면 가장 가까운 상위 폴더를 연다.
    (없는 경로를 그대로 넘기면 Windows 가 '위치를 사용할 수 없습니다' 오류 창을 띄운다)"""
    s = str(path or "").strip()
    if not s:
        return False
    p = Path(s)
    while not p.exists():
        if p.parent == p:
            return False
        p = p.parent
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
    return True


def reveal(path):
    p = Path(path)
    if sys.platform == "win32" and p.exists():
        subprocess.Popen(["explorer", "/select,", str(p)])
    else:
        open_path(p.parent if p.is_file() else p)


def friendly_error(e) -> str:
    t = f"{type(e).__name__}: {e}"
    low = t.lower()
    if "torchvision::" in low or ("torchvision" in low and "does not exist" in low):
        return ("AI 라이브러리 설치가 꼬였어요 (torch와 torchvision 버전이 서로 맞지 않음).\n\n"
                "프로그램을 닫고 AnimRef 폴더의 fix_torch.bat 를 더블클릭해서 복구한 뒤 다시 실행해 주세요.\n"
                "원본 영상은 지워지지 않았어요.")
    if any(k in low for k in ("urlopen", "getaddrinfo", "connectionerror", "name or service",
                              "max retries", "ssl", "http error", "download")):
        return "AI 모델을 내려받지 못했어요. 인터넷 연결을 확인한 뒤 다시 시도해 주세요."
    if "cuda out of memory" in low or "outofmemory" in low:
        return "그래픽카드 메모리가 부족해요. [설정]에서 분석 속도를 '빠르게'로 바꾸고 게임을 끈 상태로 다시 시도해 주세요."
    if "no space left" in low:
        return "저장 공간이 부족해요. 저장 폴더의 디스크 공간을 확인해 주세요."
    return t


class Job(QThread):
    """오래 걸리는 작업을 백그라운드에서 실행한다. fn(*args, log=, progress=, cancel=)"""
    log = Signal(str)
    progress = Signal(float, str)
    succeeded = Signal(object)
    failed = Signal(str, bool)  # 메시지, 취소 여부

    def __init__(self, title, fn, args=(), kwargs=None):
        super().__init__()
        self.title, self.fn, self.args, self.kwargs = title, fn, args, dict(kwargs or {})
        self.cancel = threading.Event()

    def run(self):
        try:
            r = self.fn(*self.args, **self.kwargs, log=self.log.emit, progress=self.progress.emit,
                        cancel=self.cancel)
            self.succeeded.emit(r)
        except Cancelled:
            self.failed.emit("작업을 취소했어요.", True)
        except Exception as e:
            self.log.emit(traceback.format_exc())
            self.failed.emit(friendly_error(e), False)


class SpinKeyFilter(QObject):
    """숫자 입력칸이 키보드 포커스를 잡고 있으면 Qt가 Space·Ctrl+Z 같은 키를 '글자 입력/편집'으로 먼저 가져가서
    창 단축키가 안 먹는다. 입력칸은 클릭했을 때만 포커스를 받게 하고, 포커스가 있어도 Space·되돌리기·복구는
    창 단축키가 처리하게 하며, Enter는 값 확정만 한다(그 뒤 포커스를 놓는다)."""

    def __init__(self, dialog, spins):
        super().__init__(dialog)
        self.dialog = dialog
        for sp in spins:
            sp.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            sp.lineEdit().installEventFilter(self)

    def drop_focus(self):
        w = QApplication.focusWidget()
        if w is not None and w is not self.dialog and self.dialog.isAncestorOf(w):
            w.clearFocus()

    def eventFilter(self, obj, e):
        t = e.type()
        if t == QEvent.Type.ShortcutOverride:
            k, ctrl = e.key(), bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
            if k == Qt.Key.Key_Space or (ctrl and k in (Qt.Key.Key_Z, Qt.Key.Key_Y)):
                e.ignore()
                return True
            if k in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                e.accept()
                return False
        elif t == QEvent.Type.KeyPress and e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            QTimer.singleShot(0, self.drop_focus)
        return False


class Hotkey(QObject):
    """게임 중에도 동작하는 전역 단축키 (pynput)."""
    pressed = Signal()

    def __init__(self, key="f9"):
        super().__init__()
        self.key = key.lower()
        self.listener = None
        self.ok = False
        self._down = False
        try:
            from pynput import keyboard

            def match(k):
                name = getattr(k, "name", None) or (getattr(k, "char", None) or "")
                return str(name).lower() == self.key

            def on_press(k):
                if match(k) and not self._down:  # 키를 누르고 있어도 한 번만
                    self._down = True
                    self.pressed.emit()

            def on_release(k):
                if match(k):
                    self._down = False

            self.listener = keyboard.Listener(on_press=on_press, on_release=on_release)
            self.listener.daemon = True
            self.listener.start()
            self.ok = True
        except Exception:
            self.ok = False

    def set_key(self, key):
        self.key = key.lower()

    def stop(self):
        if self.listener:
            try:
                self.listener.stop()
            except Exception:
                pass


def env_info():
    try:
        import torch

        if torch.cuda.is_available():
            return f"GPU: {torch.cuda.get_device_name(0)}"
        return "CPU 모드 (NVIDIA GPU가 없으면 분석이 느릴 수 있어요)"
    except Exception:
        return ""


def is_windows():
    return os.name == "nt"


def split_icon(text: str):
    """'🤖 AI 분류 실행' -> ('🤖', 'AI 분류 실행'). 맨 앞 글자가 아이콘이 아니면 ('', 전체)."""
    head, _, rest = text.partition(" ")
    if rest and not any(ch.isalnum() for ch in head):
        return head, rest
    return "", text


def needed_width(texts, advance, pad=30, gap=8) -> int:
    """버튼들(글자 + 여백)을 한 줄에 다 펼치는 데 필요한 폭. advance(text) = 글자 폭(px)."""
    return int(sum(advance(t) + pad + gap for t in texts))


class AdaptiveRow(QWidget):
    """버튼 줄: 자리가 모자라면 글자를 숨기고 아이콘만 보인다 (글자가 잘려서 못 읽는 것보다 낫다).
    전체 글은 툴팁에 있다. track() 으로 등록한 버튼만 바뀌고, 나머지(검색칸/슬라이더)는 그대로다."""

    def __init__(self, reserve=260, parent=None):
        super().__init__(parent)
        self.h = QHBoxLayout(self)
        self.h.setContentsMargins(0, 0, 0, 0)
        self.reserve = reserve      # 검색칸·슬라이더 등 줄어들지 않는 부분이 쓰는 폭
        self._tracked = []          # [(버튼, 아이콘, 글, 툴팁)]
        self.compact = False

    def track(self, *buttons):
        for b in buttons:
            full = b.text()
            icon, _ = split_icon(full)
            if not icon:
                continue
            tip = b.toolTip()
            self._tracked.append((b, icon, full, (full + ("\n" + tip if tip else ""))))
        return self

    def needed(self) -> int:
        fm = self.fontMetrics()
        return needed_width([t[2] for t in self._tracked], lambda x: fm.horizontalAdvance(x)) + self.reserve

    def set_compact(self, on: bool) -> None:
        if on == self.compact and getattr(self, "_applied", False):
            return
        self.compact, self._applied = on, True
        for b, icon, full, tip in self._tracked:
            b.setText(icon if on else full)
            b.setToolTip(tip)
            b.setMinimumWidth(40 if on else 0)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        try:
            self.set_compact(self.width() < self.needed())
        except Exception:  # noqa: BLE001 (그리기 중 예외로 창이 죽지 않게)
            pass
