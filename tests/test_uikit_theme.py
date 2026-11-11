"""공통 UI 부품(? 아이콘, 크기 조절, 테마 색 바꿔 끼우기)."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
if os.environ.get("MB_REAL_QT") != "1":
    import qt_stub
    qt_stub.install()

from motionbridge.ui import chrometabs, theme, uikit


def test_convert_roundtrip():
    css = "color:#8aa;font-weight:700"
    light = theme.convert_css(css, "light")
    assert "#8aa" not in light and light != css
    assert theme.convert_css(light, "dark") == css, "왕복하면 원래대로"
    assert theme.convert_css(light, "light") == light, "두 번 적용해도 같음 (멱등)"
    # 매핑에 없는 색(초록 버튼 등)은 그대로
    keep = "QPushButton{background:#2e8b57;color:white}"
    assert theme.convert_css(keep, "light") == keep
    # 대문자도 처리, 색 뒤에 숫자·글자가 더 붙은 긴 색은 건드리지 않음
    assert theme.convert_css("color:#8AA", "light") == "color:" + theme.DARK_TO_LIGHT["#8aa"]
    assert theme.convert_css("color:#8aa1b2c3", "light") == "color:#8aa1b2c3"
    assert theme.convert_css("", "light") == ""
    # 탭 줄은 통째로 바뀜
    assert theme.convert_css(chrometabs.STYLE, "light") == chrometabs.STYLE_LIGHT
    assert theme.convert_css(chrometabs.STYLE_LIGHT, "dark") == chrometabs.STYLE
    assert len(set(theme.DARK_TO_LIGHT.values())) == len(theme.DARK_TO_LIGHT)
    print("theme convert OK")


def test_clamp():
    assert uikit.clamp_height(100, 50, 60) == 150
    assert uikit.clamp_height(100, -90, 60) == 60
    assert uikit.clamp_height(100, 99999, 60, 500) == 500
    print("clamp OK")


def test_resizable_persist():
    store = {}
    uikit.Resizable.bind(store)

    class Inner:
        h = None

        def setMinimumHeight(self, v):
            self.mn = v

        def setMaximumHeight(self, v):
            self.mx = v

        def height(self):
            return 120

    from PySide6.QtWidgets import QWidget
    w = uikit.Resizable.__new__(uikit.Resizable)
    w.inner, w.key, w.min_h = Inner(), "k", 70
    w.set_height(200)
    assert store["k"] == 200 and w.inner.mn == 200 and w.inner.mx == 200
    w.set_height(10)
    assert store["k"] == 70, "최소 높이 아래로는 안 줄어듦"
    w.reset()
    assert "k" not in store and w.inner.mx == uikit.BIG
    uikit.Resizable.bind(None)
    print("resizable OK")


def test_help_icon():
    h = uikit.HelpIcon("설명")
    assert h.help_text() == "설명"
    print("help OK")


if __name__ == "__main__":
    test_convert_roundtrip()
    test_clamp()
    test_resizable_persist()
    test_help_icon()
    print("test_uikit_theme ALL OK")


def test_flip_animref_style():
    """AnimRef 화면의 어두운 스타일이 밝은 테마에서 통째로 밝아지고(검은 바탕이 남지 않음), 되돌리면 글자 하나 안 틀리고 원래대로."""
    import colorsys, re
    from motionbridge.animref.ui import theme as ar
    light = theme.convert_css(ar.STYLE, "light")
    def lum(t):
        h = t[1:]
        h = "".join(c * 2 for c in h) if len(h) == 3 else h
        return colorsys.rgb_to_hls(*(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)))[1]
    bgs = re.findall(r"background(?:-color)?:\s*(#[0-9a-fA-F]{3,6})", light)
    dark_bg = [b for b in bgs if lum(b) < 0.35 and colorsys.rgb_to_hls(*(int(b[1:][i:i+2],16)/255 for i in (0,2,4)))[2] < 0.42]
    assert bgs and not dark_bg, dark_bg
    m = re.search(r"QWidget \{ color: (#[0-9a-fA-F]{6})", light)
    assert m and lum(m.group(1)) < 0.35, "기본 글자색은 어두워야 함: %s" % (m and m.group(1))
    assert "#5b7cfa" in light, "강조색은 그대로"
    assert theme.convert_css(light, "dark") == ar.STYLE
    assert theme.convert_css(light, "light") == light
    print("animref style flip OK")


if __name__ == "__main__":
    test_flip_animref_style()
