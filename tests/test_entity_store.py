"""엔티티 저장소 시험 (Qt 불필요): 카탈로그 연결 · 다국어/별명/음차/코드명 찾기 · 병합 · 저장."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from motionbridge.core import charcatalog as cc
from motionbridge.core.identify import entity_store as es

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("FAIL", msg)


# ---- 정규화: 중국어/일본어가 사라지지 않아야 한다 (charcatalog.norm_text 와 다른 점)
check(es.norm("黑风大王") == "黑风大王".lower(), "중국어 보존")
check(es.norm("The Black Wind King") == es.norm("black-wind king"), "관사/부호 무시")
check(es.norm("ヤマタノオロチ") != "", "가나 보존")
check(es.words("King of the Black Wind") == es.words("Black Wind King"), "낱말 집합: 어순/of 무시")

# ---- 카탈로그에서 엔티티 만들기 (test_charcatalog 와 같은 합성 에셋)
C = "MortalShell2/Content/Sparta/Characters/"
cat = cc.Catalog.build(
    [C + "Bosses/Swordman/Art/Mesh/SK_Swordman.uasset",
     C + "Bosses/LadyOfTheWoods/Art/Mesh/SK_LadyOfTheWood.uasset",
     C + "Enemies/TarredVestige/Art/Mesh/SK_TarredVestige.uasset"],
    [C + "Bosses/Swordman/Animation/A_Attack.uasset"],
    [["en", "ST_Core_Bosses", "Boss_Swordman", "The Nameless Captive"],
     ["ko", "ST_Core_Bosses", "Boss_Swordman", "이름 없는 포로"]])

d = tempfile.mkdtemp()
st = es.EntityStore("모탈셸2", path=os.path.join(d, "ent.json"))
n = st.from_catalog(cat)
check(n == 3 and len(st.entities) == 3, "카탈로그 3명 -> 엔티티 3개: %d/%d" % (n, len(st.entities)))
sw = st.entities.get("Sparta/Swordman")
check(sw is not None and sw.kind == "boss" and "Swordman" in sw.codenames, "코드명/종류 연결")
check(sw.official.get("en") == ["The Nameless Captive"] and sw.official.get("ko") == ["이름 없는 포로"], "locres 정식 이름")
check("Characters/Bosses/Swordman" in sw.folder, "에셋 폴더 연결")

# ---- 찾기: 정식 이름(언어 무관) / 코드명 / 접두 / 퍼지(오타)
hit = st.find("이름 없는 포로")
check(hit and hit[0].entity is sw and hit[0].score >= 1.0 and "정식 이름(ko)" in hit[0].why, "한국어 정식 이름: %s" % (hit and hit[0].why))
check(st.find("the nameless captive")[0].entity is sw, "영어 + 관사 무시")
check(st.find("SK_Swordman") == [] or st.find("Swordman")[0].entity is sw, "코드명")
check(st.find("TarredVestige")[0].entity.eid == "Sparta/TarredVestige", "코드명 정확 일치")
h = st.find("이름 없는 포")  # 잘린 글자 -> 접두
check(h and h[0].entity is sw and h[0].score >= 0.88, "접두: %s" % (h and h[0].why))
h = st.find("Tarred Vestlge")  # 오타 -> 퍼지
check(h and h[0].entity.eid == "Sparta/TarredVestige" and 0.8 <= h[0].score <= 0.9, "오타 퍼지: %s" % (h and h[0].why))

# ---- 다국어/음차/별명 더하기 + 검수자 학습
st.add_name("Sparta/Swordman", "黑风大王", lang="zh", group="official", evidence="바이두 위키", url="https://x", tier="official_wiki")
st.add_name("Sparta/Swordman", "Heifeng Dawang", group="romanized")
st.learn("Sparta/Swordman", "포로 아저씨", lang="ko")
check(st.find("黑风大王")[0].entity is sw, "중국어 정식 이름으로 찾기")
check(st.find("heifeng dawang")[0].entity is sw and "음차" in st.find("heifeng dawang")[0].why, "음차로 찾기")
h = st.find("포로 아저씨")
check(h and h[0].entity is sw and sw.verified and "별명" in h[0].why, "검수자 별명 학습: %s" % (h and h[0].why))
check(any(s.get("tier") == "official_wiki" for s in sw.sources), "출처 기록")
check(st.add_name("Sparta/Swordman", "흑풍대왕", lang="ko", group="official"), "새 이름은 True")
check(not st.add_name("Sparta/Swordman", "흑풍 대왕", lang="ko", group="official"), "정규화 중복은 False")

# ---- 어순 무시 (낱말 집합)
st.add_name("Sparta/LadyOfTheWoods", "Lady of the Woods", lang="en", group="official")
h = st.find("the woods lady")
check(h and h[0].entity.eid == "Sparta/LadyOfTheWoods" and h[0].score >= 0.93, "어순 달라도 찾기: %s" % (h and h[0].why))

# ---- 교차 검증(resolve) 과 역질문 판단
hits = st.resolve(["포로 아저씨", "Heifeng Dawang", "nameless captive"])
check(hits and hits[0].entity is sw and hits[0].score == 1.0 and "+" in hits[0].why, "단서 3개 합의 가산")
two = st.resolve(["Swordman", "TarredVestige"])
check(st.need_clarify(two), "후보 2개 점수 비슷 -> 역질문 필요")
check(not st.need_clarify(hits) or len(hits) < 2 or hits[0].score - hits[1].score >= 0.15, "확실하면 역질문 불필요")

# ---- 모르는 칸 (역질문 재료)
tv = st.entities["Sparta/TarredVestige"]
u = tv.unknowns()
check("한국어 정식 이름" in u and "커뮤니티 별명" in u and "출처(위키/공략)" in u, "모르는 칸: %s" % u)
check("한국어 정식 이름" not in sw.unknowns(), "아는 칸은 안 나옴")

# ---- 병합: 웹에서 온 중복 엔티티를 하나로
st.ensure("web/black-wind-king")
st.add_name("web/black-wind-king", "Black Wind King", lang="en", group="official")
st.entities["web/black-wind-king"].context["chapter"] = "1"
kept = st.merge("Sparta/Swordman", "web/black-wind-king")
check("web/black-wind-king" not in st.entities and kept is sw, "병합: 하나만 남음")
check(st.find("black wind king")[0].entity is sw and sw.context.get("chapter") == "1" and "web/black-wind-king" in sw.merged_from, "병합: 이름/맥락 승계")

# ---- 저장/불러오기 (원자적, 깨진 파일 무시)
check(st.save() and os.path.isfile(st.path), "저장")
st2 = es.EntityStore.load("모탈셸2", path=st.path)
check(len(st2.entities) == len(st.entities), "불러오기: 수 같음")
sw2 = st2.entities["Sparta/Swordman"]
check(sw2.verified and sw2.official.get("zh") == ["黑风大王"] and st2.find("포로 아저씨")[0].entity is sw2, "불러온 뒤에도 똑같이 찾음")
open(st.path, "w", encoding="utf-8").write("{깨진 json")
check(len(es.EntityStore.load("모탈셸2", path=st.path).entities) == 0, "깨진 파일은 빈 저장소")

if fails:
    print("\n실패 %d개" % len(fails))
    sys.exit(1)
print("test_entity_store ALL OK")
