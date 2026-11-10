"""캐릭터 카탈로그 시험 (Qt 불필요): 캐릭터 묶기 · 게임 속 표시 이름 연결 · 화면 글자/AI 답 찾기."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from motionbridge.core import charcatalog as cc

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("FAIL", msg)


C = "MortalShell2/Content/Sparta/Characters/"
MESHES = [
    # 보스: 몸통 + 부품/변형 (모두 한 캐릭터로 묶여야 한다)
    C + "Bosses/Swordman/Art/Mesh/SK_Swordman.uasset",
    C + "Bosses/Swordman/Art/Mesh/SK_Swordman_Skeleton.uasset",
    C + "Bosses/Swordman/Art/Mesh/SK_Swordman_ArmL_Detached.uasset",
    C + "Bosses/Swordman/Art/Mesh/SK_Swordman_Physics.uasset",
    C + "Bosses/LadyOfTheWoods/Art/Mesh/SK_LadyOfTheWood.uasset",
    C + "Bosses/HeadBoss/Art/Mesh/SK_MouthBoss.uasset",
    # 일반/미니보스
    C + "Enemies/TarredVestige/Art/Mesh/SK_TarredVestige.uasset",
    C + "Enemies/TarredVestige/Art/Mesh/SK_TarredVestige_Skeleton.uasset",
    C + "Enemies/Offspring_Placeholder/Art/Mesh/SK_Spider.uasset",
    C + "Enemies/HutchbackCarrier/Art/Mesh/SK_HutchbackCarrier.uasset",
    C + "Enemies/MS1_HeavyCultist/Art/Mesh/SK_HeavyCultist.uasset",
    # 가족 폴더: Brigands 아래 서로 다른 캐릭터
    C + "Enemies/Brigands/BrigBase/Art/Mesh/SK_BrigBase.uasset",
    C + "Enemies/Brigands/BrigElite/Art/Mesh/SK_BrigElite.uasset",
    # 플레이어블(셸) / NPC
    C + "Shells/KnightLady/Art/SK_Shell_KnightLady_V04.uasset",
    C + "NPCs/Egon/Art/SK_Egon.uasset",
    # 캐릭터가 아닌 것 (무시)
    "MortalShell2/Content/Sparta/Weapons/Player/Proxima/Art/SK/SK_Proxima_MG.uasset",
    "MortalShell2/Content/Sparta/Environments/Props/SK_Chest.uasset",
    # 옛 게임(MS1) 쪽 캐릭터
    "MortalShell2/Content/MS1/Characters/Enemies/Bosses/Tarsus/Art/Mesh/SK_MS1_Tarsus.uasset",
]
ANIMS = [
    C + "Bosses/Swordman/Animation/Attacks/A_Swordman_Attacks_Spins.uasset",
    C + "Bosses/Swordman/Animation/Attacks/A_Swordman_Attacks_Overhead_01.uasset",
    C + "Bosses/LadyOfTheWoods/Animation/A_Lady_Idle.uasset",
    C + "Enemies/TarredVestige/Animation/Attacks/A_TarredVestige_Attacks_Swipe.uasset",
    C + "Enemies/Brigands/BrigBase/Animation/Actions/A_BrigBase_Actions_OnFire_01.uasset",
    C + "Enemies/Brigands/BrigElite/Animation/Attacks/A_BrigElite_Slash.uasset",
]
# locres 항목: [언어, 표, 키, 글자]
NAMES = [
    ["en", "ST_Core_Bosses", "Boss_Swordman", "The Nameless Captive"], ["ko", "ST_Core_Bosses", "Boss_Swordman", "이름 없는 포로"],
    ["en", "ST_Core_Bosses", "Boss_LadyOffTheWoods", "Magdalena, the Lady of the Woods"], ["ko", "ST_Core_Bosses", "Boss_LadyOffTheWoods", "숲의 여인 마그달레나"],   # 키 오타
    ["en", "ST_Core_Bosses", "Boss_Head", "The Lost Child"], ["ko", "ST_Core_Bosses", "Boss_Head", "잃어버린 아이"],                                            # 별칭
    ["en", "ST_Core_MiniBosses", "TarredVestige_MiniBoss", "Tainted Vestige"], ["ko", "ST_Core_MiniBosses", "TarredVestige_MiniBoss", "오염된 자취"],
    ["en", "ST_Core_MiniBosses", "Hutchback_MiniBoss", "Vrannic, the Grand Illusionist"], ["ko", "ST_Core_MiniBosses", "Hutchback_MiniBoss", "위대한 환영술사 브라닉"],  # 줄임
    ["en", "ST_Core_MiniBosses", "HeavyCultist_MiniBoss", "Prophet of Profane Infinities"],                                                                    # 옛 접두(MS1_)
    ["en", "ST_Core_MiniBosses", "BrigandElite_MiniBoss", "The Warden"],                                                                                         # 가족 폴더 + 줄임말
    ["en", "ST_Core_MiniBosses", "GrishaCaged_MiniBoss", "Caged Grisha"],                                                                                        # 폴더 없음 -> 이어 붙이지 않는다
    ["en", "ST_Core_Shells", "KnightLady", "Proxima"], ["ko", "ST_Core_Shells", "KnightLady", "프록시마"],
    ["en", "ST_Core_Shells", "KnightLadyTagline", "Purpose gives the soul its shape."],                                                                          # 문구 -> 제외
    ["en", "ST_Core_NpcNames", "Egon", "Egon"], ["ko", "ST_Core_NpcNames", "Egon", "에곤"],
    ["en", "ST_Core_Items", "BurntEffigy", "Burnt Effigy"], ["ko", "ST_Core_Items", "BurntEffigy", "타버린 조각상"],                                            # 아이템 표 -> 무시
    ["fr", "ST_Core_Bosses", "Boss_Swordman", "Le Captif"],                                                                                                      # 영/한 외 언어 무시
]

cat = cc.Catalog.build(MESHES, ANIMS, NAMES)
ids = {c.id for c in cat.all(True)}

# ---- 묶기
check({"Swordman", "LadyOfTheWoods", "HeadBoss", "TarredVestige", "HutchbackCarrier", "KnightLady", "Egon", "Tarsus"} <= ids, "캐릭터 폴더 묶기: %s" % sorted(ids))
check({"Brigands/BrigBase", "Brigands/BrigElite"} <= ids and "Brigands" not in ids, "가족 폴더(Brigands) 아래 캐릭터가 따로 잡힘")
check(not any("Weapons" in c.folder or "Props" in c.folder for c in cat.chars.values()), "무기/소품은 캐릭터가 아님")
sw = cat.by_name("Swordman")
check(len(sw.meshes) == 4 and len(sw.anims) == 2, "Swordman 메시 4(몸+부품) / 애니 2: %d/%d" % (len(sw.meshes), len(sw.anims)))
check(sw.body_name() == "Swordman", "대표 몸통은 부품/Skeleton 이 아니라 Swordman: " + sw.body_name())
check(cat.by_name("MouthBoss").id == "HeadBoss", "폴더 이름과 몸통 메시 이름이 달라도 둘 다로 찾는다")
check(len(cat.all()) == len(cat.all(True)) - 1 and all(not c.legacy for c in cat.all()), "옛 게임(MS1) 캐릭터는 기본 목록에서 빠진다")
# 애니메이션은 '이름 비슷한 것'이 아니라 폴더 소속
be = cat.chars[("Sparta", "Brigands/BrigElite")]
check([os.path.basename(a) for a in be.anims] == ["A_BrigElite_Slash.uasset"], "가족 폴더: 캐릭터별 애니메이션이 섞이지 않음")
check(cat.of_path(ANIMS[3]).id == "TarredVestige" and cat.of_path(MESHES[11]).id == "Brigands/BrigBase", "경로 -> 캐릭터")
check(cat.of_path("MortalShell2/Content/Sparta/Weapons/x.uasset") is None, "캐릭터 경로가 아니면 None")

# ---- 표시 이름 연결
check(sw.en == ["The Nameless Captive"] and sw.ko == ["이름 없는 포로"], "Boss_Swordman -> Swordman: %s %s" % (sw.en, sw.ko))
check(sw.kind == "boss", "kind boss")
check(cat.by_name("LadyOfTheWoods").ko == ["숲의 여인 마그달레나"], "키 오타(LadyOffTheWoods)도 연결")
check(cat.by_name("HeadBoss").en == ["The Lost Child"], "별칭(Boss_Head -> HeadBoss)")
tv = cat.by_name("TarredVestige")
check(tv.kind == "miniboss" and tv.ko == ["오염된 자취"], "미니보스 이름: %s %s" % (tv.kind, tv.ko))
check(cat.by_name("HutchbackCarrier").en == ["Vrannic, the Grand Illusionist"], "줄임(Hutchback_MiniBoss -> HutchbackCarrier)")
check(cat.chars[("Sparta", "MS1_HeavyCultist")].en == ["Prophet of Profane Infinities"], "옛 접두 MS1_ 는 이름 비교에서 무시")
check(cat.chars[("Sparta", "Brigands/BrigElite")].en == ["The Warden"], "BrigandElite_MiniBoss -> BrigElite")
check(not any("Caged" in n for c in cat.chars.values() for n in c.en), "폴더가 없는 이름은 억지로 잇지 않는다")
check(cat.by_name("KnightLady").en == ["Proxima"] and cat.by_name("KnightLady").kind == "shell", "셸 이름")
check(not any("soul" in n.lower() for c in cat.chars.values() for n in c.en), "문구 항목(Tagline)은 이름이 아님")
check(not any("Effigy" in n for c in cat.chars.values() for n in c.en), "아이템 표는 무시")
check(not any("Captif" in n for c in cat.chars.values() for n in c.names), "영/한 외 언어는 무시")

# ---- 화면 글자로 찾기
def top(q):
    f = cat.find(q, 1)
    return (f[0].char.id, f[0].score) if f else (None, 0.0)


check(top("오염된 자취") == ("TarredVestige", 1.0), "한국어 이름 정확히")
check(top("오염된자취")[0] == "TarredVestige" and top("오염된자취")[1] == 1.0, "띄어쓰기 달라도")
check(top("THE NAMELESS CAPTIVE") == ("Swordman", 1.0), "영어 대소문자/관사 무시")
check(top("오엮된 자취")[0] == "TarredVestige" and 0.7 <= top("오엮된 자취")[1] < 1.0, "글자 인식 오타 한 글자는 점수만 낮게: %s" % (top("오엮된 자취"),))
check(top("▶ 이름 없는 포로 ◀")[0] == "Swordman", "앞뒤 기호가 붙어도 (이름이 글자 안에 들어 있음)")
check(top("타버린 조각상")[0] is None, "아이템 이름은 캐릭터로 오인하지 않음")
check(top("확인")[0] is None and top("a")[0] is None, "짧거나 무관한 글자는 못 찾음")

# ---- AI 답 정리
for ans in ("Swordman", "swordman", "Swordman = The Nameless Captive / 이름 없는 포로 [boss]", "The Nameless Captive", "이름 없는 포로", "SK_Swordman"):
    c = cat.resolve(ans)
    check(c is not None and c.id == "Swordman", "resolve(%r) -> %s" % (ans, c.id if c else None))
check(cat.resolve("MouthBoss [boss]").id == "HeadBoss", "몸통 메시 이름으로도")
check(cat.resolve("unknown") is None and cat.resolve("") is None and cat.resolve("captive") is None, "모르는 말은 None (억지로 잇지 않음)")

# ---- 목록: 자르지 않는다
lines = cat.vocab_lines()
check(len(lines) == len(cat.all()), "목록은 캐릭터 전부")
check("TarredVestige = Tainted Vestige / 오염된 자취 [miniboss]" in lines, "목록 줄 모양: " + str(lines[:3]))
check(lines.index([l for l in lines if l.startswith("Swordman")][0]) < lines.index([l for l in lines if l.startswith("Egon")][0]), "보스가 NPC 보다 앞")
big = cc.Catalog.build(["%sEnemies/Mob%03d/Art/SK_Mob%03d.uasset" % (C, i, i) for i in range(300)])
check(len(big.vocab_lines()) == 300, "캐릭터가 300명이어도 목록을 자르지 않음")

# ---- 실제 게임과 같은 이름 규칙(대소문자만 다른 폴더, 중첩 구조)에서도 깨지지 않음
odd = cc.Catalog.build([C + "NPCs/Shellkeeper/Art/SK_ShellKeeper.uasset", C + "NPCs/ShellKeeper/Art/SK_ShellKeeper.uasset", C + "NPCs/Blacksmith/Blacksmith/Art/SK_B.uasset",
                        C + "NPCs/Blacksmith/Blacksmith_Old/Art/SK_B2.uasset"])
check(len(odd.all()) >= 3, "비슷한 이름/중첩 폴더에서도 목록이 만들어진다: %s" % sorted(c.id for c in odd.all()))

# ---- 루트 이름이 Sparta 가 아닌 다른 게임: 전부 '옛 에셋'으로 숨겨져 0명이 되면 안 된다
other = cc.Catalog.build(["Game/Content/Characters/Enemies/Goblin%d/Mesh/SK_Goblin%d.uasset" % (i, i) for i in range(10)])
check(len(other.all()) == 10, "Sparta 루트가 없는 게임도 목록에 나온다: %d" % len(other.all()))
mix = cc.Catalog.build(["G/Sparta/Characters/Enemies/A/Art/SK_A.uasset", "G/MS1/Characters/Enemies/B/Art/SK_B.uasset"])
check([c.id for c in mix.all()] == ["A"], "Sparta 와 MS1 이 같이 있으면 MS1 은 옛 에셋으로 뒤로 밀림")

if fails:
    print("\n실패 %d개" % len(fails))
    sys.exit(1)
print("test_charcatalog ALL OK")
