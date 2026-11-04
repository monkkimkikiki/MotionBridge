import base64
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import gamescan as G  # noqa: E402


def make_utoc(path, encrypted=False, guid=b"\0" * 16, ver=8):
    h = bytearray(144)
    h[:16] = G.UTOC_MAGIC
    h[16] = ver
    h[64:80] = guid
    h[80] = 0x01 | 0x08 | (0x02 if encrypted else 0)
    open(path, "wb").write(bytes(h))


def make_pak(path, encrypted=False, guid=b"\0" * 16, ver=11, body=5000):
    footer = guid + bytes([1 if encrypted else 0]) + G.PAK_MAGIC + struct.pack("<I", ver) + struct.pack("<QQ", 100, 50) + b"\0" * 20 + b"\0" + b"\0" * 160
    open(path, "wb").write(b"x" * body + footer)


def build(d, encrypted):
    paks = os.path.join(d, "Game", "Content", "Paks")
    os.makedirs(paks)
    os.makedirs(os.path.join(d, "Game", "Binaries", "Win64"))
    open(os.path.join(d, "Game", "Binaries", "Win64", "Game-Win64-Shipping.exe"), "wb").write(b"MZ")
    g = bytes(range(16)) if encrypted else b"\0" * 16
    make_utoc(os.path.join(paks, "pakchunk0-Windows.utoc"), encrypted, g)
    open(os.path.join(paks, "pakchunk0-Windows.ucas"), "wb").write(b"u" * 3000)
    make_pak(os.path.join(paks, "pakchunk0-Windows.pak"), encrypted, g, body=10)  # 작은 짝 pak -> 제외
    make_pak(os.path.join(paks, "pakchunk4-Windows.pak"), encrypted, g)  # 데이터 있는 pak
    open(os.path.join(paks, "unrelated.txt"), "w").write("x")


def test_scan():
    with tempfile.TemporaryDirectory() as d:
        build(d, False)
        s = G.scan_game(d)
        assert s.found and not s.encrypted and s.is_unreal5 and s.exe.endswith("Shipping.exe")
        assert [c.name for c in s.containers] == ["pakchunk0-Windows.utoc", "pakchunk4-Windows.pak"], [c.name for c in s.containers]
        assert "바로 추출" in s.summary() and "usmap" in s.summary()
    with tempfile.TemporaryDirectory() as d:
        build(d, True)
        s = G.scan_game(d)
        assert s.encrypted and s.key_guids == {bytes(range(16)).hex()}
        assert "AES 키가 필요" in s.summary()
    with tempfile.TemporaryDirectory() as d:
        s = G.scan_game(d)
        assert not s.found and "찾지 못했습니다" in s.summary()
        assert not G.scan_game(os.path.join(d, "없음")).found
    print("scan OK")


def test_key():
    h = "0123456789abcdef" * 4
    assert G.normalize_aes_key(h) == "0x" + h.upper()
    assert G.normalize_aes_key("0x" + h.upper()) == "0x" + h.upper()
    assert G.normalize_aes_key('  "0X%s" ' % h) == "0x" + h.upper()
    b = base64.b64encode(bytes(range(32))).decode()
    assert G.normalize_aes_key(b) == "0x" + bytes(range(32)).hex().upper()
    for bad in ["", "0x1234", "g" * 64, h + "0", "hello"]:
        try:
            G.normalize_aes_key(bad)
        except ValueError:
            continue
        raise AssertionError("받아들이면 안 됨: " + bad)
    print("aes key OK")


if __name__ == "__main__":
    test_scan()
    test_key()
    print("test_gamescan ALL OK")
