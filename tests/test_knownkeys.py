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

if fails:
    print("\n실패 %d개" % len(fails))
    sys.exit(1)
print("test_knownkeys ALL OK")
