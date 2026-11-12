"""손가락 수가 다를 때의 가중 블렌딩 시험 (Qt 불필요): 5지 소스 -> 3지/벙어리 타깃."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from motionbridge.core.mapping import finger_blend

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("FAIL", msg)


def slots(side, fingers, joints=(1, 2, 3), base=0):
    out = {}
    i = base
    for f in fingers:
        for m in joints:
            out["%s_%s%d" % (side, f, m)] = i
            i += 1
    return out


SRC5 = slots("L", ["Thumb", "Index", "Middle", "Ring", "Pinky"])
# ---- 5지 -> 3지 (엄지/검지/새끼): 빠진 중지는 검지로, 약지는 새끼로 섞인다
tgt3 = slots("L", ["Thumb", "Index", "Pinky"], base=100)
fb = finger_blend(SRC5, tgt3)
check("L_Index1" in fb and "L_Pinky1" in fb, "빠진 이웃이 있는 손가락만 섞임: %s" % sorted(fb))
check("L_Thumb1" not in fb, "엄지는 이웃이 없어 1:1 그대로")
idx = dict(fb["L_Index1"])
check("L_Index1" in idx and "L_Middle1" in idx and idx["L_Index1"] > idx["L_Middle1"], "검지 = 검지(주) + 중지(보조): %s" % idx)
check(abs(sum(idx.values()) - 1.0) < 1e-9, "비율 합 1")
pk = dict(fb["L_Pinky1"])
check("L_Ring1" in pk and "L_Pinky1" in pk, "새끼 = 새끼 + 약지: %s" % pk)
check(all(("L_Index%d" % m) in fb for m in (1, 2, 3)), "모든 마디에 적용")

# ---- 구성이 같으면 빈 dict (기존 1:1)
check(finger_blend(SRC5, slots("L", ["Thumb", "Index", "Middle", "Ring", "Pinky"], base=100)) == {}, "같은 구성은 그대로")

# ---- 소스가 2마디뿐이면 가장 가까운 마디로 폴백
src2 = slots("L", ["Thumb", "Index", "Middle", "Ring", "Pinky"], joints=(1, 2))
fb2 = finger_blend(src2, tgt3)
row3 = dict(fb2.get("L_Index3", []))
check(row3 and all(k.endswith("2") for k in row3), "타깃 3마디 <- 소스 2마디 폴백: %s" % row3)

# ---- 벙어리장갑(엄지+통손 Index 하나): 검지에 중지·약지·새끼가 다 섞이진 않아도(거리 밖) 중지는 온다
mitt = slots("R", ["Thumb", "Index"], base=200)
fbm = finger_blend(slots("R", ["Thumb", "Index", "Middle", "Ring", "Pinky"]), mitt)
im = dict(fbm.get("R_Index1", []))
check("R_Middle1" in im, "벙어리: 검지에 중지 섞임: %s" % im)

if fails:
    print("\n실패 %d개" % len(fails))
    sys.exit(1)
print("test_fingerblend ALL OK")
