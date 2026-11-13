"""AI 확장 검색 시험 (Qt 불필요): 단서 확장 해석 · 폴더 통계 · 지역코드+보스신호 랭킹 (금지장로 실사례 재현)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from motionbridge.core.identify import namefind as nf

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("FAIL", msg)


# ---- 오공식 합성 에셋: 지역코드_이름 폴더, Meshes 하위, 애니는 별도 트리
C = "b1/Content/00MainHZ/Characters/Enemy/"
A = "b1/Content/00Main/Animation/"
MESHES = (
    [C + "GYCY/GYCY_YanJianXi_01/Meshes/SK_GYCY_YanJIanXi_01_base.uasset",
     C + "GYCY/GYCY_YanJianXi_01/Meshes/SK_GYCY_YanJianXi_weapon_l.uasset",
     C + "GYCY/GYCY_HFDW_01a/Mesh/SK_GYCY_HFDW_01a.uasset",
     C + "GYCY/GYCY_Seng_01/Mesh/SK_GYCY_Seng_01.uasset",
     C + "HFM/HFM_GanShi_01A/Mesh/SK_HFM_GanShi_01A.uasset"])
ANIMS = ([A + "GYCY/GYCY_YanJianXi_01/AS_GYCY_YanJianXi_Atk_%02d.uasset" % i for i in range(90)]
         + [A + "GYCY/GYCY_YanJianXi_01/AS_GYCY_YanJianXi_01_Die_Seq.uasset"]
         + [A + "GYCY/GYCY_Seng_01/AS_GYCY_Seng_01_Atk_%02d.uasset" % i for i in range(10)]
         + [A + "HFM/HFM_GanShi_01A/AS_HFM_GanShi_01A_Atk_%02d.uasset" % i for i in range(20)])

# ---- 폴더 통계
st = nf.folder_stats(MESHES, ANIMS)
check("GYCY_YanJianXi_01" in st and st["GYCY_YanJianXi_01"]["anims"] == 91, "애니 수 집계: %s" % st.get("GYCY_YanJianXi_01", {}).get("anims"))
check(st["GYCY_YanJianXi_01"]["cine"] and st["GYCY_YanJianXi_01"]["weapons"] == 1, "전용 연출·무기 신호")
check(st["GYCY_Seng_01"]["anims"] == 10 and not st["GYCY_Seng_01"]["cine"], "일반 몹은 신호 없음")

# ---- 확장 답 해석 (깨진 답 안전)
exp = nf.parse_expand('{"official":{"zh":"金池长老","en":"Elder Jinchi"},"romanized":["JinChi ZhangLao"],'
                      '"location":{"name":"旧观音禅院","romanized":"GuanYinChanYuan","initials":["GYCY"]},'
                      '"keywords":["seng","zhong"],"codenames":["YanJianXi"],"why":"욕심 모티프"}')
check(exp["initials"] == ["GYCY"] and "YanJianXi" in exp["codenames"] and "GuanYinChanYuan" in exp["romanized"], "확장 해석: %s" % exp)
check(nf.parse_expand("json 아님")["romanized"] == [], "깨진 답은 빈 확장")

# ---- 랭킹: 금지장로 -> 모티프 코드명 + 지역 + 보스 신호로 1위
res = nf.score_folders(exp, st, clue="금지장로")
check(res and res[0]["folder"] == "GYCY_YanJianXi_01", "1위: %s" % (res and res[0]["folder"]))
check(res[0]["score"] >= 0.9 and "지역 코드 GYCY" in res[0]["why"] and "이름 유사" in res[0]["why"], "근거: %s (%.2f)" % (res[0]["why"], res[0]["score"]))
seng = next((r for r in res if r["folder"] == "GYCY_Seng_01"), None)
check(seng is None or seng["score"] < res[0]["score"] - 0.2, "일반 몹과 점수 차이가 크다")

# ---- 코드명 추측이 없어도 지역+보스 신호로 상위에 온다
exp2 = dict(exp)
exp2["codenames"] = []
res2 = nf.score_folders(exp2, st, clue="금지장로")
check(res2 and res2[0]["folder"] == "GYCY_YanJianXi_01", "코드명 없이도 1위: %s" % (res2 and res2[0]["folder"]))

# ---- find(): LLM 실패 폴백 — 이름 유사만으로라도 돌아간다
r = nf.find(lambda p: (_ for _ in ()).throw(RuntimeError("연결 안 됨")), "YanJianXi", "오공", MESHES, ANIMS)
check("실패" in r["note"] and r["candidates"] and r["candidates"][0]["folder"] == "GYCY_YanJianXi_01", "LLM 없이 폴백: %s" % r["note"])
r2 = nf.find(None, "ganshi", "", MESHES, ANIMS)
check(r2["candidates"] and r2["candidates"][0]["folder"] == "HFM_GanShi_01A", "ask 없이 직접 유사 검색")

# ---- 잡몹(이름 없음): 외형 -> 종족 변형 + 부속 파일 특징 (방패 든 늑대 실사례 재현: GYCY_Lang_07)
MOBS = ([C + "GYCY/GYCY_Lang_%02d/Meshes/SK_GYCY_Lang_%02d.uasset" % (i, i) for i in range(1, 9)]
        + [C + "GYCY/GYCY_Lang_07/Meshes/SK_GYCY_Lang_07_weapon_l.uasset",
           C + "GYCY/GYCY_Lang_03/Meshes/SK_GYCY_Lang_03_huoba.uasset",
           "b1/Content/00Main/VFX/Characters/HYS/HYS_TeChuShi_01/Mesh/SK_GYCY_Lang_07_Shield_VFX.uasset"]
        + MESHES)
mexp = nf.parse_mob('{"species":["lang","wolf"],"attributes":[["dun","dunpai","shield","pai"]],"why":"회색 늑대 + 사자 방패"}')
check(mexp["species"] == ["lang", "wolf"] and mexp["attributes"] == [["dun", "dunpai", "shield", "pai"]], "잡몹 확장 해석: %s" % mexp)
check(nf.parse_mob("깨진 답")["species"] == [], "깨진 잡몹 답은 빈 확장")
mr = nf.score_mob(mexp, nf.folder_stats(MOBS, ANIMS), MOBS)
check(mr and mr[0]["folder"] == "GYCY_Lang_07", "잡몹 1위 = 방패 변형: %s" % (mr and mr[0]["folder"]))
check("들고" in mr[0]["why"] and all(r["folder"].startswith("GYCY_Lang") for r in mr), "변형만 모임 + 특징 근거: %s" % mr[0]["why"])
check(mr[0]["score"] > mr[1]["score"], "특징 맞는 변형이 점수 우위")
mfail = nf.find_mob(lambda p: (_ for _ in ()).throw(RuntimeError("안 됨")), "늑대", "오공", MOBS, ANIMS)
check("실패" in mfail["note"] and mfail["candidates"] == [], "LLM 실패 시 조용한 빈 결과: %s" % mfail["note"])

# ---- 종족 혼동 묶음: AI 가 '여우(fox)'라고 봐도 늑대(Lang) 변형이 나온다 (어두운 장면 오인식 실사례)
fox = nf.score_mob({"species": ["fox"], "attributes": [], "why": ""}, nf.folder_stats(MOBS, ANIMS), MOBS)
check(any(c["folder"] == "GYCY_Lang_03" for c in fox), "fox -> Lang 변형 확장: %s" % [c["folder"] for c in fox[:4]])
check(nf._expand_species(["tiger"]) != ["tiger"] and "mao" in nf._expand_species(["tiger"]), "고양이과 묶음 확장")
check(nf._expand_species(["golem"]) == ["golem"], "모르는 종족은 그대로")
tok = nf.score_mob({"species": ["lang"], "attributes": [], "why": ""},
                   nf.folder_stats(MOBS + [C + "HFM/BoLangGu/Mesh/SK_BoLangGu.uasset"], ANIMS), MOBS)
bolang = next((c for c in tok if c["folder"] == "BoLangGu"), None)
lang3 = next(c for c in tok if c["folder"] == "GYCY_Lang_03")
check(bolang is None or bolang["score"] < lang3["score"], "토큰 일치(Lang_03)가 이름 일부(BoLangGu)보다 위")

if fails:
    print("\n실패 %d개" % len(fails))
    sys.exit(1)
print("test_namefind ALL OK")
