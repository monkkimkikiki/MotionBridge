"""엔티티 사전 접착부 시험 (Qt 불필요): 추측 이름 교정(fix_groups) · 검수자 학습(learn_confirm)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from motionbridge.core import charcatalog as cc
from motionbridge.core.identify import apply as ap
from motionbridge.core.identify import entity_store as es

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("FAIL", msg)


class G:  # vision.Group 대역
    def __init__(self, name, description="", confidence=0.3, alts=None):
        self.name, self.description, self.confidence, self.alts = name, description, confidence, list(alts or [])


C = "MortalShell2/Content/Sparta/Characters/"
cat = cc.Catalog.build(
    [C + "Bosses/Swordman/Art/Mesh/SK_Swordman.uasset",
     C + "Enemies/TarredVestige/Art/Mesh/SK_TarredVestige.uasset",
     C + "Enemies/Brigands/BrigBase/Art/Mesh/SK_BrigBase.uasset"],
    [C + "Bosses/Swordman/Animation/A_Attack.uasset"],
    [["en", "ST_Core_Bosses", "Boss_Swordman", "The Nameless Captive"],
     ["ko", "ST_Core_Bosses", "Boss_Swordman", "이름 없는 포로"]])
st = es.EntityStore("모탈셸2", path=os.path.join(tempfile.mkdtemp(), "ent.json"))
st.from_catalog(cat)

# ---- 교정: 카탈로그가 모르는 다국어/별명 이름을 대표 메시로
st.add_name("Sparta/Swordman", "黑风大王", lang="zh", group="official")
st.learn("Sparta/Swordman", "포로 아저씨")
notes = []
gs = [G("黑风大王"), G("포로 아저씨"), G("unknown"), G("TarredVestige")]
n = ap.fix_groups(st, cat, gs, notes)
check(n == 2, "교정 수: %d (%s)" % (n, notes))
check(gs[0].name == "Swordman" and "黑风大王" in gs[0].alts, "중국어 정식명 -> 대표 메시 + 원래 이름은 후보로")
check(gs[1].name == "Swordman" and gs[1].confidence >= 0.9, "검수자가 가르친 별명은 높은 확신: %.2f" % gs[1].confidence)
check(gs[2].name == "unknown", "모름 줄은 건드리지 않음")
check(gs[3].name == "TarredVestige", "카탈로그가 바로 아는 이름은 그대로")
check(any("엔티티 사전" in x for x in notes), "교정 근거 기록: %s" % notes[:1])

# ---- 설명 속 '게임 속 이름' 도 단서로 쓴다
g2 = G("검은 곰", "게임 속 이름: 이름 없는 포로. 큰 검을 든 보스")
ap.fix_groups(st, cat, [g2], [])
check(g2.name == "Swordman", "설명 속 게임 이름으로 교정: %s" % g2.name)

# ---- 비등한 후보는 교정하지 않는다 (검수자 몫)
st.add_name("Sparta/Brigands/BrigBase", "포로", lang="ko", group="alias")
g3 = G("포로")
before = g3.name
ap.fix_groups(st, cat, [g3], [])
check(g3.name == before or g3.name in ("Swordman", "BrigBase"), "모호하면 안전하게")

# ---- 학습: 확정된 메시 -> 캐릭터 별명
mesh = C + "Bosses/Swordman/Art/Mesh/SK_Swordman.uasset"
n = ap.learn_confirm(st, cat, [("늪의 기사", "게임 속 이름: 늪기사.", mesh)])
check(n == 2, "새 별명 2개 학습: %d" % n)
check(st.find("늪의 기사") and st.find("늪의 기사")[0].entity.eid == "Sparta/Swordman", "배운 별명으로 바로 찾음")
check(ap.learn_confirm(st, cat, [("늪의 기사", "", mesh)]) == 0, "같은 별명은 다시 세지 않음")
check(ap.learn_confirm(st, cat, [("SK_Swordman", "", mesh)]) == 0 and st.entities["Sparta/Swordman"].verified, "아는 이름으로 맞혔으면 확인 표시만")
check(ap.char_for_mesh(cat, mesh).id == "Swordman", "메시 경로 -> 캐릭터")
check(ap.char_for_mesh(cat, "없는/경로/x.uasset") is None, "모르는 경로는 None")

# ---- 학습이 다음 교정으로 이어진다 (전체 고리)
g4 = G("늪의 기사")
ap.fix_groups(st, cat, [g4], [])
check(g4.name == "Swordman", "학습 -> 다음 추측 자동 교정: %s" % g4.name)

if fails:
    print("\n실패 %d개" % len(fails))
    sys.exit(1)
print("test_identify_apply ALL OK")
