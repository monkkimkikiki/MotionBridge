"""화면 글자로 캐릭터 찾기 + AI 답 정리 시험 (가짜 AI 사용, 네트워크/Qt 불필요)."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from motionbridge.core import charcatalog as cc
from motionbridge.core import screenname, vision

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("FAIL", msg)


C = "MortalShell2/Content/Sparta/Characters/"
cat = cc.Catalog.build(
    [C + "Bosses/Swordman/Art/Mesh/SK_Swordman.uasset", C + "Enemies/TarredVestige/Art/Mesh/SK_TarredVestige.uasset",
     C + "Enemies/Spider/Art/Mesh/SK_Spider.uasset", C + "Bosses/Hexapod/Art/Mesh/SK_Hexapod.uasset"],
    [], [["en", "ST_Core_Bosses", "Boss_Swordman", "The Nameless Captive"], ["ko", "ST_Core_Bosses", "Boss_Swordman", "이름 없는 포로"],
         ["en", "ST_Core_MiniBosses", "TarredVestige_MiniBoss", "Tainted Vestige"], ["ko", "ST_Core_MiniBosses", "TarredVestige_MiniBoss", "오염된 자취"]])


class FakeProv(vision.Provider):
    """frame 파일 이름별로 정해 둔 답을 돌려주는 가짜 AI."""
    key = "ollama"

    def __init__(self, replies):
        super().__init__()
        self.replies, self.calls, self.prompts = replies, [], []

    def _send(self, prompt, image_path):
        self.calls.append(image_path)
        self.prompts.append(prompt)
        r = self.replies.get(os.path.basename(image_path or ""), "{}")
        if isinstance(r, Exception):
            raise r
        return r


# ---- 글자 읽기 답 해석
check(vision.parse_texts('{"texts":["오염된 자취","  ","A","The Nameless Captive"]}') == ["오염된 자취", "The Nameless Captive"], "빈/한 글자 항목은 버림")
check(vision.parse_texts('```json\n{"texts":["x1"]}\n```') == ["x1"], "코드 블록으로 와도 읽음")
check(vision.parse_texts('{"texts":[{"text":"오염된 자취"}]}') == ["오염된 자취"], "객체 목록도 읽음")
check(vision.parse_texts('{"texts":[]}') == [], "빈 목록")
try:
    vision.parse_texts("죄송합니다")
    check(False, "해석 못 하면 VisionError")
except vision.VisionError:
    pass
check("health-bar" in vision.build_read_prompt() and "Ignore item names" in vision.build_read_prompt(), "읽기 질문은 아이템/메뉴 글자를 빼라고 함")

# ---- 읽은 글자 -> 캐릭터
prov = FakeProv({
    "f1.jpg": json.dumps({"texts": ["오염된 자취", "타버린 조각상", "확인"]}),
    "f2.jpg": json.dumps({"texts": ["Tainted Vestige"]}),                 # 같은 캐릭터가 또 나와도 한 번만
    "f3.jpg": json.dumps({"texts": ["이름 없는 포로"]}),
    "f4.jpg": vision.VisionError("연결 끊김"),
})
errs = []
dets = screenname.read_names(prov, ["f1.jpg", "f2.jpg", "f3.jpg", "f4.jpg"], cat, max_frames=4, on_error=errs.append)
names = [d.name for d in dets]
check(names == ["TarredVestige", "Swordman"], "글자로 찾은 캐릭터(중복·아이템 글자 제외): %s" % names)
check(all(d.confidence >= 0.9 and "화면 글자" in d.description for d in dets), "확신 높음 + 근거 설명")
check(dets[0].frame == "f1.jpg", "근거가 된 장면 기록")
check(len(errs) == 1 and "연결 끊김" in str(errs[0]), "읽기에 실패한 장면은 건너뛰고 알림")
# 비슷하지만 확실치 않은 글자는 쓰지 않는다
weak = FakeProv({"w.jpg": json.dumps({"texts": ["Sir Vest"]})})
check(screenname.read_names(weak, ["w.jpg"], cat) == [], "애매한 글자는 확인으로 치지 않음")
# 장면이 많으면 고르게 max_frames 장만
many = FakeProv({})
screenname.read_names(many, ["f%02d.jpg" % i for i in range(9)], cat, max_frames=3)
check(many.calls == ["f00.jpg", "f03.jpg", "f06.jpg"], "장면이 많으면 고르게 3장만: %s" % many.calls)

# ---- AI 답 정리
dd = [vision.Detection("Swordman = The Nameless Captive / 이름 없는 포로 [boss]", 0.8, "big sword", alts=["Hexapod [boss]", "Swordman", "SK_Spider", "ghost"]),
      vision.Detection("오염된 자취", 0.7, "tree"), vision.Detection("unknown", 0.2, "dark shape"), vision.Detection("Zorg", 0.4, "x")]
screenname.canonicalize(dd, cat)
check(dd[0].name == "Swordman", "줄 통째로 온 답을 ID 로: " + dd[0].name)
check(dd[0].alts == ["Hexapod", "Spider", "ghost"], "후보도 정리(자기 자신 제거·모르는 건 그대로): %s" % dd[0].alts)
check(dd[1].name == "TarredVestige", "한국어 이름 -> 메시 이름: " + dd[1].name)
check(dd[2].name == "unknown" and dd[3].name == "Zorg", "모르는 이름은 건드리지 않음")
agg = vision.aggregate(dets + [vision.Detection("TarredVestige", 0.6, "burning tree thing", frame="f9.jpg")])
check(len([g for g in agg if g.name == "TarredVestige"]) == 1 and [g for g in agg if g.name == "TarredVestige"][0].seen == 2, "글자로 찾은 것과 AI 가 본 것이 한 줄로 합쳐짐")

# ---- 프롬프트: 카탈로그 줄 모드
lines = cat.vocab_lines()
p = vision.build_prompt(lines, "Mortal Shell 2", sheet=True, alts=True, ids=True)
check("TarredVestige = Tainted Vestige / 오염된 자취 [miniboss]" in p and "the part before" in p, "ids 프롬프트: 줄 목록 + ID 로 답하라는 안내")
check(all(l in p for l in lines), "목록 전부가 프롬프트에 들어감")
check("Known character names" in vision.build_prompt(["A", "B"], "G") and ", ".join(["A", "B"]) in vision.build_prompt(["A", "B"], "G"), "예전 방식 프롬프트는 그대로")
# analyze_sheet(ids=True) 는 목록을 자르지 않는다 (vocab_limit 이 넉넉하므로)
big = ["Mob%03d [enemy]" % i for i in range(200)]
sheet_prov = FakeProv({"s.jpg": '{"characters":[]}'})
sheet_prov.analyze_sheet("s.jpg", "f.jpg", big, "G", (), 0, ids=True)
check("Mob199 [enemy]" in sheet_prov.prompts[0], "ids 모드는 200줄 모두 전달")

if fails:
    print("\n실패 %d개" % len(fails))
    sys.exit(1)
print("test_screenname ALL OK")
