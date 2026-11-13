"""내장 공개 AES 키 목록: 게임 이름/폴더/프로젝트 아무거나로 매칭된다."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from motionbridge.core import knownkeys
from motionbridge.core import gamepresets

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("FAIL", msg)


wukong = "0xA896068444F496956900542A215367688B49B19C2537FCD2743D8585BA1EB128"
check(knownkeys.keys_for("Black Myth Wukong") == [wukong], "표시 이름으로 매칭")
check(knownkeys.keys_for("", "BlackMythWukong", "") == [wukong], "설치 폴더 이름으로 매칭")
check(knownkeys.keys_for("검은 신화: 오공") == [wukong], "한국어 이름으로 매칭")
check(knownkeys.keys_for("b1wukong") == [wukong], "프로젝트+이름 조합")
check(knownkeys.keys_for("Elden Ring") == [], "모르는 게임은 빈 목록")
check(knownkeys.keys_for("", "", "") == [], "빈 입력은 빈 목록")
check(gamepresets.known_keys_for("wukong") == [wukong], "gamepresets 경유 노출")
check(all(k.startswith("0x") and len(k) == 66 for info in knownkeys.KNOWN.values() for k in info["keys"]),
      "모든 내장 키는 0x+64자리 형식")

# ---- 로컬 파일 + 팀 공유 폴더에서 추가로 읽는다 (코드 수정 없이 키 배포)
import json
import tempfile

from motionbridge.core import bridge

with tempfile.TemporaryDirectory() as d:
    old_dir = bridge.CONFIG_DIR
    bridge.CONFIG_DIR = os.path.join(d, "cfg")
    os.makedirs(bridge.CONFIG_DIR, exist_ok=True)
    k_local = "0x" + "1" * 64
    k_share = "0x" + "2" * 64
    with open(os.path.join(bridge.CONFIG_DIR, knownkeys.FILE_NAME), "w", encoding="utf-8") as f:
        json.dump({"My Game": {"match": ["mygame"], "keys": [k_local]}}, f)
    share = os.path.join(d, "share")
    os.makedirs(share, exist_ok=True)
    with open(os.path.join(share, knownkeys.FILE_NAME), "w", encoding="utf-8") as f:
        json.dump({"Team Game": {"match": ["teamgame"], "keys": [k_share]}}, f)
    check(knownkeys.keys_for("MyGame") == [k_local], "로컬 known_keys.json 매칭")
    check(knownkeys.keys_for("TeamGame") == [], "공유 폴더는 지정해야 읽음")
    check(gamepresets.known_keys_for("TeamGame", share=share) == [k_share], "팀 공유 폴더 known_keys.json 매칭")
    check(knownkeys.keys_for("wukong") == [wukong], "시드와 파일이 같이 동작")
    with open(os.path.join(bridge.CONFIG_DIR, knownkeys.FILE_NAME), "w", encoding="utf-8") as f:
        f.write("깨진 json")
    check(knownkeys.keys_for("MyGame") == [], "깨진 파일은 조용히 무시")
    bridge.CONFIG_DIR = old_dir

if fails:
    print("\n실패 %d개" % len(fails))
    sys.exit(1)
print("test_knownkeys ALL OK")
