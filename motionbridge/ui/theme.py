"""밝은 테마 / 어두운 테마.

팔레트(기본 색)는 QPalette 로 바꾸고, 글씨마다 직접 지정해 둔 색(setStyleSheet 의 #8aa 같은 것)은
어두운 바탕용이라 밝은 바탕에서 안 보이므로 짝이 되는 색으로 바꿔 끼운다 (왕복해도 원래대로 돌아옴).
3D 뷰포트만 두 테마 모두 어두운 바탕을 그대로 쓰고, 곡선 그래프·프레임 막대는 tc() 로 테마를 따른다.
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


_REV: dict = {}      # 밝게 바꾼 스타일 → 원래(어두운) 스타일. 되돌릴 때 정확히 원래대로


def _flip(tok: str) -> str:
    """표에 없는 색: 무채색에 가까우면 밝기를 뒤집고(어두운 바탕 ↔ 밝은 바탕), 강조색(채도 높음)은 그대로 두되 너무 밝으면 조금 어둡게."""
    import colorsys
    h = tok[1:]
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return tok
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    hh, ll, ss = colorsys.rgb_to_hls(r, g, b)
    if ss < 0.42 or ll < 0.18 or ll > 0.93:
        ll = 1.0 - ll
        ll = 0.04 + ll * 0.94 if ll > 0.5 else ll * 0.9          # 순백/순흑을 살짝 누그러뜨림
    elif ll > 0.68:
        ll = 0.42                                                 # 밝은 강조색 글자는 밝은 바탕에서 안 보이므로 어둡게
    else:
        return tok
    r, g, b = colorsys.hls_to_rgb(hh, ll, ss)
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


def convert_css(css: str, to: str) -> str:
    """스타일시트 안의 색을 to('light'|'dark') 테마용으로 바꾼다.

    밝게: 짝 색 표에 있으면 그 색, 없으면 밝기를 뒤집는다(AnimRef 화면처럼 표에 없는 어두운 색도 따라오게).
    어둡게: 밝게 바꿀 때 기억해 둔 원래 글자로 정확히 되돌린다."""
    if not css:
        return css
    if css in WHOLE.get(to, {}):
        return WHOLE[to][css]
    if to == "dark":
        # 밝게 바꾼 적이 있는 글자만 되돌린다. 기억에 없는 것은 원래부터 어두운 테마용이므로 손대지 않는다
        # (#3a4560 처럼 '밝은 쪽 짝 색'이면서 '어두운 쪽 원래 색'이기도 한 값이 있어, 표로 거꾸로 바꾸면 멀쩡한 색이 망가진다)
        return _REV.get(css, css)
    if css in _REV:            # 이미 밝게 바꾼 것 (두 번 적용 방지)
        return css
    lights = set(LIGHT_TO_DARK)

    def sub(m):
        t = m.group(0).lower()
        if t in DARK_TO_LIGHT:
            return DARK_TO_LIGHT[t]
        if t in lights:
            return m.group(0)
        return _flip(t)

    new = _TOKEN.sub(sub, css)
    if new != css:
        _REV[new] = css
    return new


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


def _keep_dark(w) -> bool:
    """뷰포트 위 도구 줄처럼 '항상 어둡게' 표시한 곳 안의 위젯인가."""
    try:
        for _ in range(8):
            if w is None:
                return False
            if w.property("mbKeepDark"):
                return True
            w = w.parentWidget()
    except Exception:  # noqa: BLE001
        pass
    return False


def _remap(w, to: str) -> None:
    try:
        css = w.styleSheet()
    except Exception:  # noqa: BLE001
        return
    if to == "light" and css and _keep_dark(w):
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
            # 처음 보일 때 + 프로그램이 글자색을 다시 지정했을 때(StyleChange: 판정 줄·선택 표시처럼 상태에 따라 색을 바꾸는 곳)
            if _mode == "light" and ev.type() in (QEvent.Type.Show, QEvent.Type.StyleChange) and hasattr(obj, "styleSheet"):
                _remap(obj, "light")
        except Exception:  # noqa: BLE001
            pass
        return False


_follower = None
_OWN_PALETTE: dict = {}     # 위젯 → 어두운 테마일 때의 자기 팔레트


def track_palette(widget, dark_pal) -> None:
    """자기 팔레트를 따로 입힌 화면을 등록해 두면 테마를 바꿀 때 같이 바뀐다."""
    _OWN_PALETTE[widget] = dark_pal
    if _mode == "light":
        try:
            widget.setPalette(light_palette())
            _remap(widget, "light")
        except Exception:  # noqa: BLE001
            pass


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
            try:
                w.update()          # 직접 그리는 화면(그래프·프레임 막대)도 새 색으로 다시 그린다
            except Exception:  # noqa: BLE001
                pass
        for w in list(_OWN_PALETTE):       # 자기 팔레트를 따로 가진 화면(AnimRef)
            try:
                w.setPalette(light_palette() if new_mode == "light" else _OWN_PALETTE[w])
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001  (시험용 가짜 Qt)
        pass
    return new_mode


def paint_rgb(r: int, g: int, b: int):
    """직접 그리는 화면(곡선 그래프·프레임 막대)의 색: 밝은 테마면 어두운 바탕용 색을 밝은 바탕용 짝 색으로 바꾼 (r, g, b)."""
    if _mode != "light":
        return int(r), int(g), int(b)
    tok = _flip("#%02x%02x%02x" % (int(r) & 255, int(g) & 255, int(b) & 255))
    return int(tok[1:3], 16), int(tok[3:5], 16), int(tok[5:7], 16)


def tc(*args):
    """QColor(...) 대신 쓰는 테마 색. 인자는 QColor 와 같다 (r,g,b[,a]) 또는 ('#rrggbb'). 3D 뷰포트에서는 쓰지 않는다(항상 어둡게)."""
    try:
        if _mode == "light":
            if len(args) >= 3 and all(isinstance(a, (int, float)) for a in args[:3]):
                r, g, b = paint_rgb(args[0], args[1], args[2])
                return QColor(r, g, b, *args[3:])
            if len(args) == 1 and isinstance(args[0], str) and args[0].startswith("#") and len(args[0]) in (4, 7):
                return QColor(_flip(args[0]))
    except Exception:  # noqa: BLE001
        pass
    return QColor(*args)
