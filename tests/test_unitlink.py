"""유닛 데이터 연결(unitlink): CardDesc ID <-> 표시 이름 <-> 코드명 — 추측 없는 정답표."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from motionbridge.core import bridge

_tmp = tempfile.mkdtemp()
bridge.CONFIG_DIR = os.path.join(_tmp, "cfg")   # 사전 저장이 진짜 홈을 건드리지 않게

from motionbridge.core.identify import entity_store, unitlink

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("FAIL", msg)


ENTRIES = [
    ["en", "StringKVMapDesc", "CardDesc.103006.UnitName", "Elder Jinchi"],
    ["ko", "StringKVMapDesc", "CardDesc.103006.UnitName", "금지장로"],
    ["en", "StringKVMapDesc", "CardDesc.101011.UnitName", "Wolf Soldier"],
    ["en", "StringKVMapDesc", "ItemDesc.103006.Name", "다른 표는 무시"],
]
card = unitlink.card_names(ENTRIES)
check(card.get(103006, {}).get("ko") == "금지장로" and 101011 in card and len(card) == 2, "카드 표: %s" % card)

check(unitlink.codename_of("b1/Content/00Main/Design/Units/GYCY/Unit_GYCY_YanJianXi_01.uasset") == "GYCY_YanJianXi_01", "코드명 추출")

UNIT_JSON = '[{"Type":"BP","Properties":{"UnitID":103006,"Hp":5000,"Other":[12,101011000]}}]'   # 101011000 은 8자리 초과 아님? 9자리 -> 매칭 안 됨
links = unitlink.build_links({"b1/.../Units/GYCY/Unit_GYCY_YanJianXi_01.uasset": UNIT_JSON}, ENTRIES)
check(len(links) == 1 and links[0]["codename"] == "GYCY_YanJianXi_01" and links[0]["ids"] == [103006]
      and links[0]["names"]["ko"] == "금지장로", "연결: %s" % links)
check(unitlink.build_links({"u.uasset": '{"UnitID": 999999}'}, ENTRIES) == [], "모르는 ID 는 안 이음")

st = entity_store.EntityStore("테스트게임")
n = unitlink.apply_to_store(st, links)
check(n == 1, "사전에 1개 등록")
hits = st.find("금지장로", limit=3)
check(hits and hits[0].score >= 0.95 and "GYCY_YanJianXi_01" in hits[0].entity.codenames,
      "한국어 인게임 이름 -> 코드명 즉시 일치: %s" % [(h.score, h.entity.codenames) for h in hits[:1]])
hits_en = st.find("Elder Jinchi", limit=3)
check(hits_en and "GYCY_YanJianXi_01" in hits_en[0].entity.codenames, "영어 이름도 즉시 일치")
check(unitlink.apply_to_store(st, links) == 0, "두 번 넣어도 중복 등록 없음")

# ---- 도감 글 검색 (search_lore): 특징 낱말 -> 몬스터 이름 (AI 없이)
CARDS_E = ENTRIES + [
    ["en", "StringKVMapDesc", "CardDesc.103006.StoryBrief", "A burnt old monk with a khakkhara staff, consumed by greed."],
    ["ko", "StringKVMapDesc", "CardDesc.103006.StoryBrief", "불에 탄 노승이 석장을 짚고 보물을 탐했다."],
    ["en", "StringKVMapDesc", "CardDesc.101011.StoryBrief", "A grey wolf soldier patrolling the hills with a spear."],
    ["ko", "StringKVMapDesc", "CardDesc.101011.UnitPoetry", "잿빛 늑대가 창을 들고 산을 지킨다."],
]
cards = unitlink.journal_cards(CARDS_E)
check(len(cards) == 2 and "석장" in cards[103006]["texts"]["ko"], "카드 글 수집: %s" % sorted(cards))
r1 = unitlink.search_lore("불탄 노승 석장", cards)
check(r1 and r1[0]["name"] == "금지장로", "한국어 특징 -> 금지장로: %s" % [(h["name"], h["score"]) for h in r1[:2]])
r2 = unitlink.search_lore("grey wolf with spear", cards)
check(r2 and r2[0]["name"] == "Wolf Soldier", "영어 설명 -> Wolf Soldier: %s" % [(h["name"], h["score"]) for h in r2[:2]])
check(unitlink.search_lore("", cards) == [] and unitlink.search_lore("용암 거인", cards) == [], "빈 질의/무관 질의는 빈 결과")
r3 = unitlink.search_lore("늑대가 창을", cards)   # 조사 떼기: 늑대가->늑대, 창을->창
check(r3 and r3[0]["name"] == "Wolf Soldier", "조사 붙은 한국어도 매칭: %s" % [(h["name"],) for h in r3[:1]])

if fails:
    print("\n실패 %d개" % len(fails))
    sys.exit(1)
print("test_unitlink ALL OK")
