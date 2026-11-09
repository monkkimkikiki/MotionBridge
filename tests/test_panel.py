"""AI 회의: 가짜 AI 로 역할(보는 쪽/글 전용), 목록 검증, 라운드 수정, 합의 순위를 시험한다."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import panel, vision  # noqa: E402


def check(c, m):
    if not c:
        raise AssertionError(m)
    print("  ok:", m)


NAMES = ["AS_PC_Jump_Attack_01", "AS_PC_Execute_Plunge_01", "AS_PC_Light_01", "AS_PC_Dodge_F", "AM_PC_Parry_Finisher"]


class Fake(vision.Provider):
    def __init__(self, key, sees, script):
        super().__init__()
        self.key, self.supports_images, self.script, self.calls = key, sees, list(script), []

    def _send(self, prompt, image_path):
        self.calls.append((prompt, image_path))
        return self.script.pop(0)


def ans(desc, *picks):
    return json.dumps({"description": desc, "picks": [{"name": n, "confidence": c, "reason": r} for n, c, r in picks]})


eyes = Fake("ollama", True, [
    ans("The player jumps and plunges onto a kneeling boss.", ("AS_PC_Execute_Plunge_01", 0.7, "plunge onto boss"), ("not_in_list_xyz", 0.9, "hallucinated")),
    ans("Same.", ("AS_PC_Execute_Plunge_01", 0.8, "still plunge")),
])
brain = Fake("gptoss", False, [
    ans("", ("AS_PC_Jump_Attack_01", 0.6, "jump"), ("AS_PC_Execute_Plunge_01", 0.5, "execution + jump")),
    ans("", ("AS_PC_Execute_Plunge_01", 0.9, "agree with eyes: boss kneeling = execution")),
])
with tempfile.TemporaryDirectory() as d:
    import numpy as np
    import cv2
    fr = []
    for i in range(5):
        p = os.path.join(d, "f%d.jpg" % i)
        cv2.imwrite(p, np.full((90, 160, 3), 40 * i, np.uint8))
        fr.append(p)
    sheet = panel.make_sheet(fr, os.path.join(d, "sheet.jpg"), cols=3, cell_w=80)
    im = cv2.imread(sheet)
    check(im is not None and im.shape[1] == 3 * 80 and im.shape[0] == 2 * 45, "시트 만들기 %s" % (im.shape,))

    v = panel.run_panel([panel.Member("Qwen", eyes), panel.Member("GPT-OSS", brain)], sheet, NAMES, hint="이름 없는 포로", rounds=2)
check(v.ranking[0][0] == "AS_PC_Execute_Plunge_01" and set(v.ranking[0][2]) == {"Qwen", "GPT-OSS"}, "합의 1위와 지지자: %s" % (v.ranking,))
check(all(n in NAMES for n, _s, _w in v.ranking), "목록에 없는 이름은 버림")
check(eyes.calls[0][1] == sheet and brain.calls[0][1] is None, "보는 쪽은 시트를, 글 전용은 그림 없이")
check("kneeling boss" in brain.calls[0][0], "글 전용은 비전 AI 의 장면 묘사를 읽음")
check("Other panelists' latest choices" in brain.calls[1][0] and "plunge onto boss" in brain.calls[1][0], "2라운드는 서로의 선택·이유를 봄")
check([t.speaker for t in v.transcript] == ["Qwen", "GPT-OSS", "Qwen", "GPT-OSS"] and v.transcript[0].round == 1, "대화 기록 순서")
check(not v.errors, "오류 없음")

# 실패한 참가자는 건너뛰고 계속
bad = Fake("openai", True, ["이건 JSON 이 아님"])
ok = Fake("ollama", True, [ans("x", ("AS_PC_Light_01", 0.6, "r"))])
v = panel.run_panel([panel.Member("A", bad), panel.Member("B", ok)], "s.jpg", NAMES, rounds=1)
check(v.ranking[0][0] == "AS_PC_Light_01" and len(v.errors) == 1, "한 명 실패해도 진행: %s" % v.errors)

# 그림 보는 AI 가 없으면 거부 / 글 전용에 이미지 시도 안 함
try:
    panel.run_panel([panel.Member("G", Fake("gptoss", False, []))], "s.jpg", NAMES)
    check(False, "비전 없음 거부")
except vision.VisionError as e:
    check("이미지를 볼 수 있는" in str(e), "비전 AI 필수")
# 이름 검증: 경로·확장자·대소문자 허용
n = panel._Names(["Game/AS_PC_Jump_Attack_01"])
check(n.find("as_pc_jump_attack_01.psa") == "Game/AS_PC_Jump_Attack_01" and n.find("AS_PC_Jump") is None, "이름 정규화/모호한 부분일치 거부")
print("test_panel ALL OK")
