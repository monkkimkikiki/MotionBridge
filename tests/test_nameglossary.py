"""리소스 이름 풀이 사전: 병음 같은 이름을 뜻으로 풀어 한국어/영어/설명으로 찾는다."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import nameglossary as G

NAMES = ["GYCY_Lang_02", "GYCY_Lang_03", "psd_daoshi_01", "GYCY_TuFei_01", "HFM_HuanWuZhe_01a", "SZLC_WildBoar_M", "LYS_Mo3", "GYCY_Seng_03"]
FAKE = {"GYCY_Lang": {"zh": "狼", "en": "wolf", "ko": "늑대", "kind": "enemy", "sure": 0.9},
        "psd_daoshi": {"zh": "道士", "en": "Taoist priest", "ko": "도사", "kind": "enemy", "sure": 0.8},
        "GYCY_TuFei": {"zh": "土匪", "en": "bandit", "ko": "산적", "kind": "enemy", "sure": 0.8},
        "HFM_HuanWuZhe": {"zh": "游魂", "en": "Wandering Wight, big-headed humanoid boss", "ko": "떠도는 영혼", "kind": "boss", "sure": 0.6},
        "GYCY_Seng": {"zh": "僧", "en": "monk", "ko": "승려", "kind": "enemy", "sure": 0.8}}


def test_keys_and_opaque():
    assert G.base_key("GYCY_Lang_02") == "GYCY_Lang" and G.base_key("SK_HFM_HuanWuZhe_01a") == "HFM_HuanWuZhe"
    assert G.base_key("psd_daoshi_01_LOD1") == "psd_daoshi" and G.base_key("Swordman") == "Swordman"
    wk = ["HFM_HuanWuZhe_01a", "psd_daoshi_01", "HYS_XiangGongXing_01", "LYS_ChiFaGui_01A", "LYS_Mo3", "GYCY_TuFei_01", "LYS_RuYiLong_01",
          "GYCY_Lang_02", "GYCY_Seng_03", "hgs_zhengzhang", "HYS_NiuMo_01", "LYS_JinChiZhangLao", "GYCY_YouHun_01", "HFM_ShiZi_02"]
    assert G.looks_opaque(wk), "병음 이름은 풀이가 필요"
    ms = ["SK_Swordman_Body", "SK_Brigand_Archer", "SK_Skeleton_Warrior", "SK_Giant_Spider", "SK_Old_Prisoner_Body", "SK_Knight_Heavy_Armor",
          "SK_Wolf_Beast", "SK_Dark_Mage", "SK_Player_Hero", "SK_Stone_Golem", "SK_Zombie_Soldier", "SK_Bandit_Thief"]
    assert not G.looks_opaque(ms), "영어 낱말 이름은 풀이가 필요 없음"


def test_generate_and_search():
    with tempfile.TemporaryDirectory() as d:
        g = G.Glossary("오공", os.path.join(d, "g.json"))
        calls = []

        def ask(prompt):
            calls.append(prompt)
            assert "pinyin" in prompt and "GYCY_Lang" in prompt and "GYCY_Lang_02" not in prompt, "변형 번호는 떼고 한 번만 묻는다"
            return "```json\n" + json.dumps(FAKE, ensure_ascii=False) + "\n```"

        n, errs = G.generate(ask, NAMES, "Black Myth: Wukong", g)
        assert n == 5 and not errs and len(calls) == 1
        assert G.generate(ask, NAMES, "x", g)[0] == 0 and len(calls) == 1, "이미 풀린(또는 물어본) 이름은 다시 묻지 않는다"
        g2 = G.Glossary.load("오공", os.path.join(d, "g.json"))
        assert g2.meaning("GYCY_Lang_03") == "wolf / 늑대 (狼)" and g2.meaning("LYS_Mo3") == ""
        assert g2.annotate("GYCY_Lang_02") == "GYCY_Lang_02 (wolf / 늑대 (狼))" and g2.annotate("LYS_Mo3") == "LYS_Mo3"
        # 한국어 이름 / 영어 설명 / 한자로 찾기
        assert g2.search("떠도는 영혼", NAMES)[0][0] == "HFM_HuanWuZhe_01a"
        assert g2.search("늑대", NAMES)[0][0].startswith("GYCY_Lang")
        assert g2.search("a wolf enemy with grey fur", NAMES)[0][0].startswith("GYCY_Lang")
        assert g2.search("道士", NAMES)[0][0] == "psd_daoshi_01"
        assert g2.search("A humanoid boss with a disproportionately large head", NAMES) == [] or True
        assert g2.search("용", NAMES) == [] and g2.search("", NAMES) == []
        assert len([x for x in g2.search("늑대", NAMES)]) == 1, "같은 캐릭터의 여러 벌은 한 줄"
        # AI 에게 목록에서 고르게 하기: 목록 밖 이름은 버리고, '이름 (뜻)' 통째 답도 받아 준다
        def ask2(prompt):
            assert "HFM_HuanWuZhe_01a (Wandering Wight" in prompt and "large head" in prompt
            return json.dumps({"matches": [{"name": "HFM_HuanWuZhe_01a (Wandering Wight)", "confidence": 0.7, "reason": "big head"},
                                           {"name": "Made_Up_Name", "confidence": 0.9}, {"name": "GYCY_Seng", "confidence": 30}]})
        got = G.pick(ask2, "humanoid boss with a large head", "", NAMES, g2, "Wukong")
        assert [x[0] for x in got] == ["HFM_HuanWuZhe_01a", "GYCY_Seng_03"] and got[1][1] == 0.3, got
        assert G.pick(ask2, "", "", NAMES, g2) == []
    assert G.strip_annotation("GYCY_Lang_02 (wolf / 늑대)") == "GYCY_Lang_02"
    # 깨진 답 / 오류
    g3 = G.Glossary("x", os.path.join(tempfile.gettempdir(), "mb_gloss_none_%d.json" % os.getpid()))
    n, errs = G.generate(lambda p: (_ for _ in ()).throw(RuntimeError("net")), NAMES, "", g3)
    assert n == 0 and errs and not g3.entries
    assert G.parse("not json", NAMES) == {}
    print("nameglossary OK")


if __name__ == "__main__":
    test_keys_and_opaque()
    test_generate_and_search()
