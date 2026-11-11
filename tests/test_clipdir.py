"""AnimRef 클립 폴더 위치: 게임 이름의 띄어쓰기가 _ 로 바뀐 폴더를 찾는다."""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import animlink


def test_clip_dir():
    with tempfile.TemporaryDirectory() as d:
        lib = os.path.join(d, "AnimRef")
        os.makedirs(os.path.join(lib, "Mortal_Shell_2", "clips"))
        os.makedirs(os.path.join(lib, "Other_Game", "clips"))
        os.utime(os.path.join(lib, "Other_Game", "clips"), (time.time() + 100, time.time() + 100))
        os.environ["APPDATA"] = d
        os.makedirs(os.path.join(d, "AnimRef"), exist_ok=True)
        def setg(g):
            json.dump({"library_dir": lib, "game": g}, open(os.path.join(d, "AnimRef", "settings.json"), "w", encoding="utf-8"))
        setg("Mortal Shell 2")
        assert animlink.default_clip_dir() == os.path.join(lib, "Mortal_Shell_2", "clips"), animlink.default_clip_dir()
        setg("없는 게임")                      # 설정과 맞는 폴더가 없으면 가장 최근 클립 폴더
        assert animlink.default_clip_dir() == os.path.join(lib, "Other_Game", "clips")
        setg("")
        assert animlink.default_clip_dir().endswith("clips")
        assert animlink._safe_name("Mortal Shell 2") == "Mortal_Shell_2"
        assert animlink._safe_name("한글 게임!") == "한글_게임"
    print("clipdir OK")


if __name__ == "__main__":
    test_clip_dir()
