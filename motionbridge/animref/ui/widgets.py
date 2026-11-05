"""공용 위젯/유틸."""
import os
import subprocess
import sys
import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QThread, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout

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
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


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
