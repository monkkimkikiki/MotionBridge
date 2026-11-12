"""추출기만 따로 업데이트 + 게임 설정 내보내기/불러오기."""
import io
import json
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import bridge, gamepresets, helperupdate as H


def make_zip(names):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n in names:
            z.writestr(n, "x")
    return b.getvalue()


def test_update():
    rel = {"name": "mbextract 20261112-abc", "assets": [{"name": H.ZIP_NAME, "browser_download_url": "u://zip"},
                                                        {"name": H.INFO_NAME, "browser_download_url": "u://info"}]}
    files = {H.API_URL: json.dumps(rel).encode(), "u://info": json.dumps({"version": "20261112-abc", "games": 120}).encode(),
             "u://zip": make_zip([H.EXE_NAME, "oo2core_9_win64.dll"])}
    fetch = lambda u: files[u]
    info = H.latest_info(fetch)
    assert info["version"] == "20261112-abc" and info["zip_url"] == "u://zip"
    with tempfile.TemporaryDirectory() as d:
        r = H.install(info, fetch, verify=lambda exe: ["GAME_UE5_4", "GAME_BlackMythWukong"], root=d)
        assert os.path.isfile(r["path"]) and r["path"].endswith(os.path.join("20261112-abc", H.EXE_NAME))
        assert H.installed_info(r["path"])["version"] == "20261112-abc"
        assert not any(n.endswith(".part") for n in os.listdir(d)), "임시 폴더가 남지 않음"
        # 받은 추출기가 동작하지 않으면 설치하지 않는다
        info2 = dict(info, version="bad")
        for bad_verify in (lambda exe: [], lambda exe: (_ for _ in ()).throw(OSError("x"))):
            try:
                H.install(info2, fetch, verify=bad_verify, root=d)
                assert False
            except H.UpdateError:
                pass
        assert sorted(os.listdir(d)) == ["20261112-abc"], os.listdir(d)
        # exe 가 없는 압축 / 깨진 압축 / 폴더 밖으로 나가는 경로
        for bad in (make_zip(["readme.txt"]), b"not a zip", make_zip(["../evil.exe", H.EXE_NAME])):
            files["u://zip"] = bad
            try:
                H.install(info2, fetch, verify=lambda exe: ["GAME_UE5_4"], root=d)
                assert False
            except H.UpdateError:
                pass
        assert sorted(os.listdir(d)) == ["20261112-abc"] and not os.path.exists(os.path.join(os.path.dirname(d), "evil.exe"))
    # 릴리스가 아직 없을 때 / 네트워크 오류
    for f in (lambda u: json.dumps({"assets": []}).encode(), lambda u: (_ for _ in ()).throw(OSError("offline"))):
        try:
            H.latest_info(f)
            assert False
        except H.UpdateError:
            pass
    assert H.new_games(["GAME_UE5_4"], ["GAME_UE5_4", "GAME_NewGame", "x"]) == ["GAME_NewGame"]
    print("helper update OK")


def test_preset_io():
    old = bridge.CONFIG_DIR
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        try:
            class SC:
                appid, exe, project = "2358720", "b1.exe", "b1"

                def __init__(self, root):
                    self.root = root
            bridge.CONFIG_DIR = a
            gamepresets.remember(SC(os.path.join(a, "Wukong")), name="오공", ue="GAME_BlackMythWukong", aes="0x" + "A" * 64, usmap=r"C:\my\w.usmap", status="ok")
            out = os.path.join(a, "share.json")
            assert gamepresets.export_file(out) == 1
            d = json.load(open(out, encoding="utf-8"))
            assert all("usmap" not in e and e["aes"] for e in d["games"].values()), "내 컴퓨터 경로는 내보내지 않음"
            gamepresets.export_file(os.path.join(a, "nokey.json"), include_keys=False)
            assert all("aes" not in e for e in json.load(open(os.path.join(a, "nokey.json"), encoding="utf-8"))["games"].values())
            bridge.CONFIG_DIR = b                      # 다른 사람 컴퓨터
            assert gamepresets.import_file(out) == 1
            e = gamepresets.lookup(SC(os.path.join(b, "어디든", "Wukong")))
            assert e and e["ue"] == "GAME_BlackMythWukong" and e["aes"].startswith("0xAAAA"), e
            assert gamepresets.import_file(out) == 0, "같은 파일을 또 불러오면 바뀌는 것 없음"
            try:
                gamepresets.import_file(os.path.join(a, "없는파일.json"))
                assert False
            except ValueError:
                pass
        finally:
            bridge.CONFIG_DIR = old
    print("preset io OK")


if __name__ == "__main__":
    test_update()
    test_preset_io()
