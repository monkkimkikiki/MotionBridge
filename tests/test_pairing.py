import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import pairing as P  # noqa: E402


def check(c, m):
    if not c:
        raise AssertionError(m)
    print("  ok:", m)


check(P.is_reaction("X/Boss/A_Captive_HitReact_Heavy") and P.is_reaction("AM_Boss_Execute_Victim_01") and not P.is_reaction("A_Captive_Idle_Loop"), "반응 계열 판별")
check(P.words("AS_PC_ExecutePlunge_01") == ["as", "pc", "execute", "plunge", "01"], "낱말 쪼개기: %s" % P.words("AS_PC_ExecutePlunge_01"))
s, c = P.name_similarity("A_Boss_Execute_Victim_01", "AS_PC_Execute_Attacker_01")
check(s == 1.0 and c == ["01", "execute"] or set(c) == {"01", "execute"}, "역할 낱말은 빼고 비교: %s %s" % (s, c))

# 기준: 60fps 에서 330프레임(5.5초). 허용 ±30프레임 = ±0.5초
cands = [
    ("P/AS_PC_Execute_Attacker_01", 330, 5.5),       # 길이 같고 이름 겹침 -> 1위
    ("P/AS_PC_Execute_Attacker_02", 300, 5.0),       # 정확히 -30프레임: 허용 끝
    ("P/AS_PC_Light_01", 336, 5.6),                  # 이름은 다르지만 길이 비슷
    ("P/AS_PC_Dodge", 60, 1.0),                      # 너무 짧음 -> 제외
    ("P/AS_PC_Heavy_Long", 600, 10.0),               # 너무 김 -> 제외
    ("P/AS_PC_Unknown", -1, -1.0),                   # 길이 모름 -> 낮은 점수로 남김
    ("P/AS_PC_Execute_Attacker_03", 30 * 6, 6.0),    # 30fps 6초: 초 단위 비교 (+0.5초 = 허용 끝)
]
hits = P.pair_rank("B/A_Boss_Execute_Victim_01", 330, 5.5, cands, tol_frames=30)
names = [os.path.basename(h.path) for h in hits]
check(names[0] == "AS_PC_Execute_Attacker_01" and hits[0].score > 0.9, "1위: %s" % names)
check("AS_PC_Dodge" not in names and "AS_PC_Heavy_Long" not in names, "허용 오차 밖은 제외")
check("AS_PC_Execute_Attacker_02" in names and "AS_PC_Execute_Attacker_03" in names, "허용 끝(±30프레임)은 포함: %s" % names)
check(names[-1] == "AS_PC_Unknown" and hits[-1].d_frames is None and hits[-1].score < 0.2, "길이 모르는 후보는 맨 뒤")
tight = P.pair_rank("B/A_Boss_Execute_Victim_01", 330, 5.5, cands, tol_frames=2)
check([os.path.basename(h.path) for h in tight][:1] == ["AS_PC_Execute_Attacker_01"] and len(tight) == 2, "오차 2프레임이면 거의 다 빠짐: %s" % [os.path.basename(h.path) for h in tight])
try:
    P.pair_rank("x", -1, -1.0, [])
    check(False, "기준 길이 모름")
except ValueError:
    check(True, "기준 길이를 모르면 알림")
print("test_pairing ALL OK")
