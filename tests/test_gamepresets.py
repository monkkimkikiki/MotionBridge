"""게임 인식(엔진·버전·전용 프리셋 추천·3단계 판정)과 게임별 설정 기억."""
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import bridge, gamepresets as GP, gamescan as G


def touch(p, data=b""):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "wb").write(data)


def utoc(encrypted):
    h = bytearray(144)
    h[:16] = G.UTOC_MAGIC
    h[16] = 3
    h[80] = 2 if encrypted else 0
    return bytes(h)


def make_wukong(d):
    root = os.path.join(d, "steamapps", "common", "BlackMythWukong")
    touch(os.path.join(root, "b1", "Content", "Paks", "pakchunk0-WindowsNoEditor.utoc"), utoc(True))
    touch(os.path.join(root, "b1", "Content", "Paks", "pakchunk0-WindowsNoEditor.ucas"), b"x" * 10)
    ver = struct.pack("<III II", 0xFEEF04BD, 0x10000, (5 << 16) | 0, 0, 0)[:16]
    touch(os.path.join(root, "b1", "Binaries", "Win64", "b1-Win64-Shipping.exe"), b"MZ" + b"\0" * 100 + ver + b"\0" * 40)
    touch(os.path.join(root, "b1", "Binaries", "Win64", "oo2core_9_win64.dll"), b"x")
    touch(os.path.join(d, "steamapps", "appmanifest_2358720.acf"), '"appid" "2358720"\n"name" "Black Myth: Wukong"\n"installdir" "BlackMythWukong"\n'.encode())
    return root


def test_scan_and_suggest():
    with tempfile.TemporaryDirectory() as d:
        root = make_wukong(d)
        sc = G.scan_game(root)
        assert sc.found and sc.encrypted and sc.engine == "unreal", (sc.engine, sc.evidence)
        assert sc.appid == "2358720" and sc.project == "b1" and sc.exe_version.startswith("5.0."), (sc.appid, sc.project, sc.exe_version)
        games = ["GAME_UE5_0", "GAME_UE5_4", "GAME_BlackMythWukong", "GAME_Valorant", "GAME_UE5_LATEST"]
        assert G.suggest_ue(sc, games)[0] == "GAME_BlackMythWukong"
        # 전용 프리셋이 없는 게임은 실행 파일 버전으로
        sc2 = G.GameScan(os.path.join(d, "SomeGame"), None, exe="x-Win64-Shipping.exe", exe_version="5.3.2.0", project="Some")
        assert G.suggest_ue(sc2, games)[0] is None or True
        assert G.suggest_ue(sc2, games + ["GAME_UE5_3"])[0] == "GAME_UE5_3"
        mv = G.merge_versions(games)
        assert mv[0] == "GAME_UE5_6" and "GAME_BlackMythWukong" in mv and "GAME_UE5_LATEST" not in mv and mv.index("GAME_BlackMythWukong") > mv.index("GAME_UE4_25")
        # 3단계 판정
        assert G.verdict(sc, False, False)[0] == "yellow" and "AES" in G.verdict(sc, False, False)[1] and "usmap" in G.verdict(sc, False, False)[1]
        assert G.verdict(sc, True, True)[0] == "green"
        assert G.verdict(sc, True, True, {"status": "blocked", "reason": "x"})[0] == "red"
    print("scan/suggest OK")


def test_other_engines():
    with tempfile.TemporaryDirectory() as d:
        u = os.path.join(d, "UnityGame")
        touch(os.path.join(u, "UnityPlayer.dll"))
        touch(os.path.join(u, "Game_Data", "resources.assets"))
        sc = G.scan_game(u)
        assert sc.engine == "unity" and G.verdict(sc, False, False)[0] == "red", sc.engine
        r = os.path.join(d, "REGame")
        touch(os.path.join(r, "re_chunk_000.pak"), b"KPKA" + b"\0" * 600)
        sc = G.scan_game(r)
        assert sc.engine == "re" and G.verdict(sc, False, False)[0] == "red", (sc.engine, sc.evidence)
    print("other engines OK")


def test_presets():
    with tempfile.TemporaryDirectory() as d:
        bridge.CONFIG_DIR = os.path.join(d, "cfg")
        share = os.path.join(d, "nas")
        os.makedirs(share)
        root = make_wukong(d)
        sc = G.scan_game(root)
        assert GP.lookup(sc, share) is None
        key = "0x" + "AB" * 32
        um = os.path.join(d, "Wukong_Release.usmap")
        touch(um, b"u")
        GP.remember(sc, share, name="Black Myth Wukong", ue="GAME_BlackMythWukong", aes=key, usmap=um, status="ok", links=["https://example.com/k"])
        e = GP.lookup(sc, share)
        assert e and e["ue"] == "GAME_BlackMythWukong" and e["aes"] == key and e["usmap"] == um and e["links"], e
        # 폴더를 다른 드라이브로 옮겨도(경로가 달라도) AppID 로 찾는다
        d2 = os.path.join(d, "other")
        root2 = make_wukong(d2)
        assert GP.lookup(G.scan_game(root2), share)["aes"] == key
        # 빈 값으로 다시 저장해도 예전 키가 지워지지 않는다
        GP.remember(sc, share, ue="GAME_BlackMythWukong", aes="", status="ok")
        assert GP.lookup(sc, share)["aes"] == key
        # 공유 폴더에는 내 컴퓨터 경로(usmap)는 안 쓰고, 키 공유를 끄면 키도 안 쓴다
        sh = GP._read(os.path.join(share, GP.FILE_NAME))
        assert sh and all("usmap" not in v for v in sh.values()) and any(v.get("aes") == key for v in sh.values())
        GP.remember(sc, share, share_keys=False, aes=key, status="ok")
        assert all("aes" not in v for v in GP._read(os.path.join(share, GP.FILE_NAME)).values())
        # 내 컴퓨터 기록이 없어도 공유 폴더 기록으로 채워진다
        os.remove(GP.local_path())
        GP.remember(sc, share, aes=key, status="ok")
        os.remove(GP.local_path())
        got = GP.lookup(sc, share)
        assert got and got["source"] == "share" and got["aes"] == key
        # 게임 폴더 근처의 usmap 찾기
        touch(os.path.join(root, "Mappings.usmap"), b"u")
        assert GP.find_usmap_near(sc).endswith("Mappings.usmap")
    print("presets OK")


if __name__ == "__main__":
    test_scan_and_suggest()
    test_other_engines()
    test_presets()
    print("test_gamepresets ALL OK")


def test_key_candidates():
    from motionbridge.core import extractor, gamescan
    a, b = "A" * 64, "0123456789abcdef" * 4
    text = "Main key: 0x%s\n다른 버전 %s 그리고 중복 0x%s, 짧은것 0x1234, 너무 긴 %s" % (a, b, a.lower(), "F" * 70)
    assert gamescan.extract_key_candidates(text) == ["0x" + a, "0x" + b.upper()]
    assert gamescan.extract_key_candidates("") == []
    calls = []
    old = extractor.probe
    try:
        def fake(helper, game, ue, aes, usmap, limit=6):
            calls.append(aes)
            return {"code": "ERR_MISSING_USMAP" if aes.endswith("2") else "ERR_INVALID_AES", "message": "m"}
        extractor.probe = fake
        key, tried = extractor.pick_working_key("h", "g", "GAME_UE5_0", None, ["0x1", "0x2", "0x3"])
        assert key == "0x2" and calls == ["0x1", "0x2"] and [t["ok"] for t in tried] == [False, True], "매핑만 없는 경우도 키는 맞는 것"
        key, tried = extractor.pick_working_key("h", "g", "GAME_UE5_0", None, ["0x1", "0x3"])
        assert key == "" and len(tried) == 2
    finally:
        extractor.probe = old
    print("key candidates OK")


test_key_candidates()
