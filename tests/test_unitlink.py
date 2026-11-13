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

if fails:
    print("\n실패 %d개" % len(fails))
    sys.exit(1)
print("test_unitlink ALL OK")
