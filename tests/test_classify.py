"""보스/일반 몬스터/플레이어블/NPC 분류: 경로 표시·애니 신호·도감(CardDesc) 읽기."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from motionbridge.core import classify

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("FAIL", msg)


C = "G/Content/Characters/"
MESHES = [
    C + "Enemy/AA_Boss_01/Mesh/SK_AA_Boss_01.uasset",
    C + "Enemy/AA_Mob_01/Mesh/SK_AA_Mob_01.uasset",
    C + "Player/Hero/Mesh/SK_Player_Hero.uasset",
    C + "Enemy/NPC/AA_Villager/Mesh/SK_AA_Villager.uasset",
]
ANIMS = (["G/Content/Anim/AA_Boss_01/AS_AA_Boss_01_Atk_%03d.uasset" % i for i in range(130)]
         + ["G/Content/Anim/AA_Boss_01/AS_AA_Boss_01_Die_Seq.uasset"]
         + ["G/Content/Anim/AA_Mob_01/AS_AA_Mob_01_Atk_%02d.uasset" % i for i in range(20)])

m = classify.classify(MESHES, ANIMS)
check(m.get("AA_Boss_01") == "boss", "애니 많고 전용 연출 -> 보스: %s" % m.get("AA_Boss_01"))
check(m.get("AA_Mob_01") == "mob", "애니 적음 -> 일반 몬스터: %s" % m.get("AA_Mob_01"))
check(m.get("Hero") == "player", "player 경로 -> 플레이어블: %s" % m.get("Hero"))
check(m.get("AA_Villager") == "npc", "NPC 폴더 -> NPC: %s" % m.get("AA_Villager"))
check(classify.folder_of(MESHES[0]) == "AA_Boss_01", "Mesh 하위 폴더 건너뛰고 캐릭터 폴더")

# 도감(오공 CardDesc.<장><분류><번호>.UnitName): 01 잡요괴 / 02 두목·03 요왕 -> 보스 / 04 인물 -> NPC
ENTRIES = [
    ["en", "StringKVMapDesc", "CardDesc.101011.UnitName", "Wolf Soldier"],
    ["en", "StringKVMapDesc", "CardDesc.203003.UnitName", "Mad Tiger"],
    ["en", "StringKVMapDesc", "CardDesc.102001.UnitName", "Rat Captain"],
    ["ko", "StringKVMapDesc", "CardDesc.104001.UnitName", "마을 사람"],
    ["en", "StringKVMapDesc", "ItemDesc.8062.Name", "Mad Tiger"],      # 도감 카드가 아닌 항목은 무시
]
j = classify.journal_types(ENTRIES)
check(j.get(classify.nname("Wolf Soldier")) == "mob", "잡요괴 -> mob")
check(j.get(classify.nname("Mad Tiger")) == "boss", "요왕 -> boss")
check(j.get(classify.nname("Rat Captain")) == "boss", "두목 -> boss")
check(j.get(classify.nname("마을 사람")) == "npc", "인물 -> npc")
check(len(j) == 4, "카드 항목만 집계: %d" % len(j))
check(classify.journal_types([]) == {}, "빈 입력")

if fails:
    print("\n실패 %d개" % len(fails))
    sys.exit(1)
print("test_classify ALL OK")
