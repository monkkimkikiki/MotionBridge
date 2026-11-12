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

if fails:
    print("\n실패 %d개" % len(fails))
    sys.exit(1)
print("test_namefind ALL OK")
