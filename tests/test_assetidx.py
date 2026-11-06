"""에셋 색인/후보 고르기 시험 (Qt 불필요)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from motionbridge.core import assetidx as ai

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("FAIL", msg)


M = [
    "Sparta/Content/Characters/Monsters/Dwarf/Meshes/SK_Dwarf.SK_Dwarf",
    "Sparta/Content/Characters/Monsters/Dwarf/Meshes/SK_Dwarf_Archer.SK_Dwarf_Archer",
    "Sparta/Content/Characters/Monsters/Hexapod/Meshes/SK_Hexapod.SK_Hexapod",
    "Sparta/Content/Characters/Monsters/StoneGolem/Meshes/SK_StoneGolem_LOD0.SK_StoneGolem_LOD0",
    "Sparta/Content/Props/SK_Chest.SK_Chest",
]
A = [
    "Sparta/Content/Characters/Monsters/Dwarf/Animations/A_Dwarf_Attack_3Hit_Combo_1",
    "Sparta/Content/Characters/Monsters/Dwarf/Animations/A_Dwarf_Attack_2Hit_Combo_1",
    "Sparta/Content/Characters/Monsters/Dwarf/Animations/A_Dwarf_HitReact_Front",
    "Sparta/Content/Characters/Monsters/Dwarf/Animations/A_Dwarf_Idle",
    "Sparta/Content/Characters/Monsters/Dwarf/Animations/A_Dwarf_Skill_GroundSmash",
    "Sparta/Content/Characters/Monsters/Dwarf/Animations/A_Dwarf_Death",
    "Sparta/Content/Characters/Monsters/Hexapod/Animations/A_Hexapod_Attack_Claw",
    "Sparta/Content/Characters/Monsters/StoneGolem/Animations/AM_StoneGolem_Slam",
    "Sparta/Content/Characters/Player/Animations/A_Player_Attack_Light",
]

# 이름 분해
check(ai.mesh_name(M[0]) == "Dwarf", "SK_ 접두사 제거: %s" % ai.mesh_name(M[0]))
check(ai.mesh_name(M[3]) == "StoneGolem", "LOD 접미사 제거: %s" % ai.mesh_name(M[3]))
check(ai.tokens("StoneGolemBoss_01") == ["stone", "golem", "boss"], "CamelCase 분해: %s" % ai.tokens("StoneGolemBoss_01"))
check(ai.hit_count("A_Dwarf_Attack_3Hit_Combo_1") == 3, "타수 3")
check(ai.hit_count("A_X_Combo2") == 2, "콤보2 -> 2")
check(ai.hit_count("A_X_Idle") is None, "타수 없음")

idx = ai.AssetIndex(M, A)
check(idx.names() == ["Chest", "Dwarf", "Dwarf_Archer", "Hexapod", "StoneGolem"], "이름 목록: %s" % idx.names())

# 메시 후보: 정확히 맞는 이름이 1순위
r = idx.rank_meshes("Dwarf")
check(r and r[0].name == "Dwarf" and r[0].why == "이름 일치", "정확 일치 1순위: %s" % r[:2])
check(r[1].name == "Dwarf_Archer", "비슷한 이름이 2순위: %s" % [c.name for c in r])
# 띄어쓰기/대소문자가 달라도 맞아야 한다
r = idx.rank_meshes("stone golem")
check(r and r[0].name == "StoneGolem", "띄어쓰기 달라도 일치: %s" % r[:2])
# 오타 정도는 비슷한 이름으로
r = idx.rank_meshes("Hexapood")
check(r and r[0].name == "Hexapod", "오타 허용: %s" % r[:2])
# 모름 + 설명에 단어가 있으면 설명으로
r = idx.rank_meshes("unknown", "a large stone golem with glowing eyes")
check(r and r[0].name == "StoneGolem", "설명 단어로 찾기: %s" % r[:2])
# 전혀 관련 없으면 후보 없음
check(idx.rank_meshes("Zzzzqq") == [], "관련 없으면 비어 있음")

# 기억: 검수자가 'Skeleton Warrior' 를 Dwarf_Archer 로 고치면 다음에 1순위
with tempfile.TemporaryDirectory() as d:
    mp = os.path.join(d, "matches.json")
    mem = ai.MatchMemory(mp)
    check(idx.rank_meshes("Skeleton Warrior", memory=mem) == [] or idx.rank_meshes("Skeleton Warrior", memory=mem)[0].name != "Dwarf_Archer", "기억 전")
    mem.remember("Skeleton Warrior", "", M[1])
    mem.save()
    mem2 = ai.MatchMemory(mp)
    r = idx.rank_meshes("skeleton warrior", memory=mem2)
    check(r and r[0].path == M[1] and r[0].why == "이전에 확정한 매칭", "기억된 매칭이 1순위: %s" % r[:1])
    # 모름 + 같은 설명도 기억
    mem2.remember("unknown", "grey hunched goblin with club", M[2])
    check(mem2.best("unknown", "Grey hunched goblin, club") == M[2], "설명으로 기억")
    check(mem2.best("unknown", "") is None, "설명 없는 모름은 기억하지 않음")
    # 깨진 파일
    with open(mp, "w") as f:
        f.write("{not json")
    check(ai.MatchMemory(mp).data == {}, "깨진 파일은 빈 기억")

# 애니메이션 후보: 다른 캐릭터 것은 제외
r = idx.rank_anims(M[0])
paths = [c.path for c in r]
check(all("Dwarf" in p for p in paths), "Dwarf 애니메이션만: %s" % paths)
check(len(paths) == 6, "Dwarf 애니메이션 6개: %d" % len(paths))
check(not any("Player" in p or "Hexapod" in p for p in paths), "다른 캐릭터 제외")

# 분류 '타격' + 3타
r = idx.rank_anims(M[0], label="타격", hits=3)
check(r[0].path.endswith("A_Dwarf_Attack_3Hit_Combo_1"), "타격 3타가 1순위: %s" % [c.path.split("/")[-1] for c in r[:3]])
check("3타 일치" in r[0].why, "이유에 3타: %s" % r[0].why)
check(r[1].path.endswith("A_Dwarf_Attack_2Hit_Combo_1"), "2타가 그 다음: %s" % r[1].path)
# 분류 '피격'
r = idx.rank_anims(M[0], label="피격")
check(r[0].path.endswith("A_Dwarf_HitReact_Front"), "피격 분류: %s" % r[0].path)
# 분류 '스킬'
r = idx.rank_anims(M[0], label="스킬")
check(r[0].path.endswith("A_Dwarf_Skill_GroundSmash"), "스킬 분류: %s" % r[0].path)
# 분류 없음이면 이름순 정렬만, 점수는 동일 계열
r = idx.rank_anims(M[0])
check(len({c.score for c in r}) <= 2, "분류 없으면 점수 차이 없음")
# 다른 폴더에 있지만 이름에 몬스터가 들어간 애니메이션 (AM_ 몽타주)
r = idx.rank_anims(M[3], label="공격")
check(r and r[0].path.endswith("AM_StoneGolem_Slam"), "몽타주 이름으로 연결: %s" % r)
# 모르는 분류는 무시
r = idx.rank_anims(M[0], label="기타")
check(len(r) == 6, "모르는 분류는 필터하지 않음")

# 캐시
with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "sub", "assets.json")
    ai.save_index(p, idx, "G:/Sparta")
    b = ai.load_index(p, "G:/Sparta")
    check(b is not None and [m.path for m in b.meshes] == M and b.anims == A, "색인 저장/불러오기")
    check(ai.load_index(p, "other") is None, "다른 게임 폴더면 무시")
    check(ai.load_index(os.path.join(d, "none.json")) is None, "없는 파일")

# 검색
check([c.name for c in idx.search("golem")] == ["StoneGolem"], "부분 문자열 검색")
check(len(idx.search("")) == 5, "빈 검색은 앞부분 전체")
check(ai.filter_paths(M, r"(?i)monsters") == M[:4], "경로 필터")

# 플레이어블 캐릭터: 몸(메시)이 여러 벌이어도 공용 애니메이션(player/pc 경로)으로 찾는다
PM = ["Sparta/Content/Characters/Player/Meshes/SK_Player_Shell_Tiel.SK_Player_Shell_Tiel", "Sparta/Content/Characters/Monsters/Dwarf/Meshes/SK_Dwarf.SK_Dwarf"]
PA = ["Sparta/Content/Characters/Player/Animations/A_PC_LightAttack_01", "Sparta/Content/Characters/Player/Animations/A_PC_Roll_Fwd",
      "Sparta/Content/Characters/Monsters/Dwarf/Animations/A_Dwarf_Attack_1", "Sparta/Content/Common/Hero_Idle"]
pidx = ai.AssetIndex(PM, PA)
check(ai.is_player_path(PM[0]) and not ai.is_player_path(PM[1]), "플레이어 경로 판별")
check([m.name for m in pidx.player_meshes()] == ["Player_Shell_Tiel"], "플레이어 메시 목록")
PM2 = PM + ["Sparta/Content/Characters/Player/Meshes/SK_Armor_Heavy.SK_Armor_Heavy", "Sparta/Content/Characters/Monsters/Dwarf/Meshes/SK_DwarfBoss.SK_DwarfBoss"]
rig = ai.AssetIndex(PM2, PA).player_rig_meshes()
check([m.path for m in rig] == sorted([PM2[0], PM2[2]], key=str.lower), "같은 폴더(같은 리그) 변형까지 플레이어 후보: %s" % [m.name for m in rig])
rp = pidx.rank_anims(PM[0], None, None, 10)
check({c.path for c in rp} == {PA[0], PA[1], PA[3]} and all("플레이어" in c.why for c in rp), "플레이어는 공용(player/pc/hero) 애니메이션을 보여줌: %s" % [c.path for c in rp])
rp2 = pidx.rank_anims(PM[0], "타격", None, 10)
check(rp2 and rp2[0].path == PA[0], "플레이어도 분류(타격)로 정렬: %s" % [c.path for c in rp2])
rm = pidx.rank_anims(PM[1], None, None, 10)
check([c.path for c in rm] == [PA[2]], "몬스터는 그대로 메시 이름으로")
check({c.path for c in pidx.rank_anims(PM[1], None, None, 10, player=True)} == {PA[0], PA[1], PA[3]}, "강제로 플레이어 모드")
# 종류(몬스터/플레이어) 거르기: 점수 계산 전에 경로만으로 나눈다
check(ai.path_kind(PA[0]) == "player" and ai.path_kind(PA[2]) == "monster" and pidx.kind(PA[3]) == "player", "경로 종류 판별")
check(set(pidx.anims_of_kinds({"player"})) == {PA[0], PA[1], PA[3]} and pidx.anims_of_kinds({"monster"}) == [PA[2]], "종류별 애니메이션")
check(pidx.anims_of_kinds(None) == PA and pidx.anims_of_kinds({"player", "monster"}) == PA and pidx.anims_of_kinds(set()) == PA, "종류 없음/둘 다 = 전체")
big = ai.AssetIndex(PM + ["X/SK_M%d.SK_M%d" % (i, i) for i in range(50)], [])
check("Player_Shell_Tiel" in big.names(10) and len(big.names(10)) == 10, "이름 목록을 줄여도 플레이어 이름은 남김")
check(pidx.rank_meshes("Player")[0].name == "Player_Shell_Tiel", "AI 가 'Player' 라고 하면 플레이어 메시")

print("모두 통과" if not fails else "실패 %d개" % len(fails))
sys.exit(1 if fails else 0)
