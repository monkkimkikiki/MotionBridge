"""밝은 테마 / 어두운 테마.

팔레트(기본 색)는 QPalette 로 바꾸고, 글씨마다 직접 지정해 둔 색(setStyleSheet 의 #8aa 같은 것)은
어두운 바탕용이라 밝은 바탕에서 안 보이므로 짝이 되는 색으로 바꿔 끼운다 (왕복해도 원래대로 돌아옴).
3D 뷰포트·그래프 캔버스처럼 직접 그리는 화면은 두 테마 모두 어두운 바탕을 그대로 쓴다.
"""
from __future__ import annotations

import re

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# 어두운 테마 색 → 밝은 테마 색 (소문자, # 포함)
DARK_TO_LIGHT = {
    "#8aa": "#46647a",
    "#8a93a6": "#586278",
    "#ffdc50": "#8a5e00",
    "#e0a050": "#a85a00",
    "#5ab0ff": "#0f5fb0",
    "#242a36": "#e9eef8",
    "#3a4560": "#b3c0dc",
    "#3a4050": "#b0b8c8",
    "#4a5570": "#8d9abb",
    "#222836": "#eef1f7",
    "#aab3c8": "#3a4560",
    "#2b2f36": "#e6e9ef",
    "#aeb4c0": "#454c5a",
    "#444a55": "#b7bdca",
    "#3b4252": "#ffffff",
    "#343a45": "#f1f3f8",
}
LIGHT_TO_DARK = {v: k for k, v in DARK_TO_LIGHT.items()}
assert len(LIGHT_TO_DARK) == len(DARK_TO_LIGHT), "밝은 색이 겹치면 되돌릴 수 없다"

_TOKEN = re.compile(r"#[0-9a-fA-F]{3,6}(?![0-9a-fA-F])")
# 탭 줄은 글씨(흰색)까지 같이 바뀌어야 해서 통째로 바꾼다 (chrometabs.STYLE 참고)
WHOLE: dict = {}

_mode = "dark"


def mode() -> str:
    return _mode


def convert_css(css: str, to: str) -> str:
    """스타일시트 안의 색을 to('light'|'dark') 테마용으로 바꾼다. 해당 없는 색은 그대로."""
    if not css:
        return css
    if css in WHOLE.get(to, {}):
        return WHOLE[to][css]
    table = DARK_TO_LIGHT if to == "light" else LIGHT_TO_DARK

    def sub(m):
        return table.get(m.group(0).lower(), m.group(0))

    return _TOKEN.sub(sub, css)


def register_whole(dark_css: str, light_css: str) -> None:
    WHOLE.setdefault("light", {})[dark_css] = light_css
    WHOLE.setdefault("dark", {})[light_css] = dark_css


def dark_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(38, 41, 48))
    p.setColor(QPalette.ColorRole.WindowText, QColor(225, 228, 235))
    p.setColor(QPalette.ColorRole.Base, QColor(28, 31, 38))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(44, 48, 56))
    p.setColor(QPalette.ColorRole.Text, QColor(225, 228, 235))
    p.setColor(QPalette.ColorRole.Button, QColor(52, 56, 66))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(225, 228, 235))
    p.setColor(QPalette.ColorRole.Highlight, QColor(60, 130, 200))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(52, 56, 66))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(225, 228, 235))
    return p


def light_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, QColor(244, 246, 250))
    p.setColor(QPalette.ColorRole.WindowText, QColor(28, 32, 42))
    p.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(238, 241, 247))
    p.setColor(QPalette.ColorRole.Text, QColor(28, 32, 42))
    p.setColor(QPalette.ColorRole.Button, QColor(232, 236, 244))
    p.setColor(QPalette.ColorRole.ButtonText, QColor(28, 32, 42))
    p.setColor(QPalette.ColorRole.Highlight, QColor(40, 110, 190))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 225))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(28, 32, 42))
    p.setColor(QPalette.ColorRole.PlaceholderText, QColor(120, 128, 145))
    return p


def _remap(w, to: str) -> None:
    try:
        css = w.styleSheet()
    except Exception:  # noqa: BLE001
        return
    if not css:
        return
    new = convert_css(css, to)
    if new != css:
        w.setStyleSheet(new)


class _Follower(QObject):
    """테마가 밝음일 때, 나중에 만들어져 처음 보이는 위젯의 색도 바꿔 준다."""

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802
        try:
            if _mode == "light" and ev.type() == QEvent.Type.Show and hasattr(obj, "styleSheet"):
                _remap(obj, "light")
        except Exception:  # noqa: BLE001
            pass
        return False


_follower = None


def apply_theme(app, new_mode: str) -> str:
    """app 전체를 'dark' / 'light' 로 바꾼다. 적용된 모드를 돌려준다."""
    global _mode, _follower
    new_mode = "light" if new_mode == "light" else "dark"
    try:
        app.setStyle("Fusion")
        app.setPalette(light_palette() if new_mode == "light" else dark_palette())
    except Exception:  # noqa: BLE001
        pass
    _mode = new_mode
    try:
        if _follower is None:
            _follower = _Follower(app)
            app.installEventFilter(_follower)
        for w in list(app.allWidgets()):
            _remap(w, new_mode)
    except Exception:  # noqa: BLE001  (시험용 가짜 Qt)
        pass
    return new_mode
