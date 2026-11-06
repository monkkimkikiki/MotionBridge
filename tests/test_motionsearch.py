"""동작·패턴 설명 -> 애니메이션 -> 캐릭터 거꾸로 찾기."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import assetidx, motionsearch as M  # noqa: E402

ANIMS = [
    "/Game/Sparta/Characters/Bosses/Swordman/Animation/Attacks/AS_Swordman_SpinAttack_01.AS_a",
    "/Game/Sparta/Characters/Bosses/Swordman/Animation/Attacks/AS_Swordman_Slash_Heavy.AS_b",
    "/Game/Sparta/Characters/Bosses/Swordman/Animation/AS_Swordman_Idle.AS_c",
    "/Game/Sparta/Characters/Enemies/Dwarf/Animation/AS_Dwarf_GroundSlam.AS_d",
    "/Game/Sparta/Characters/Enemies/Dwarf/Animation/AS_Dwarf_Rotate_Idle.AS_e",
    "/Game/Sparta/Characters/Enemies/Hexapod/Animation/AS_Hexapod_Spin_7Hit.AS_f",
    "/Game/Sparta/Characters/Enemies/Hexapod/Animation/AS_Hexapod_Charge.AS_g",
]
MESHES = [
    "/Game/Sparta/Characters/Bosses/Swordman/Art/Mesh/SK_Swordman.SK_Swordman",
    "/Game/Sparta/Characters/Bosses/Swordman/Art/Mesh/SK_Swordman_LArm.SK_Swordman_LArm",
    "/Game/Sparta/Characters/Enemies/Dwarf/Mesh/SK_Dwarf.SK_Dwarf",
]


def base(p):
    return os.path.basename(p).split(".")[0]


def test_parse():
    q = M.parse_motion("7연속 스핀 공격")
    names = [c.name for c in q.concepts]
    assert q.count == 7 and "spin" in names and "combo" in names, names
    assert M.parse_motion("땅 내려찍기").concepts[0].name == "slam"
    q = M.parse_motion("whirlwind then fire breath")
    assert [c.name for c in q.concepts][0] == "spin" and any(c.name == "throw" for c in q.concepts)
    assert M.parse_motion("zzz").concepts[0].name == "zzz", "모르는 영어 낱말은 그대로 검색"
    assert not M.parse_motion("???").concepts
    assert M.parse_motion("공격", ["Spin", "whirl"]).concepts, "AI 낱말 반영"


def test_search():
    idx = assetidx.AssetIndex(MESHES, ANIMS)
    hits, q = M.search_anims(idx, "스핀 공격")
    got = [base(h.path) for h in hits]
    assert got[0] == "AS_Swordman_SpinAttack_01", got
    assert "AS_Swordman_Idle" not in got and "AS_Dwarf_GroundSlam" not in got
    # 캐릭터 제한
    hits, _ = M.search_anims(idx, "스핀", restrict=["swordman"])
    assert [h.monster for h in hits] == ["Swordman"]
    # 타수: 7연속이면 7Hit 가 위로
    hits, _ = M.search_anims(idx, "7연속 스핀")
    assert base(hits[0].path) == "AS_Hexapod_Spin_7Hit", [base(h.path) for h in hits]
    assert M.search_anims(idx, "")[0] == []


def test_monsters_and_meshes():
    idx = assetidx.AssetIndex(MESHES, ANIMS)
    cands, mons, hits, q = M.find_meshes_by_motion(idx, "스핀 공격")
    assert mons[0].name == "Swordman", [(m.name, m.score) for m in mons]
    assert cands[0].path == MESHES[0] and cands[0].score > cands[1].score, "본체 메시가 부품보다 위"
    assert "SpinAttack" in mons[0].why
    # AI 영어 낱말이 한국어 신호 없이도 같은 결과
    cands2, mons2, _h, _q = M.find_meshes_by_motion(idx, "빙글빙글", ["spin"])
    assert mons2[0].name in ("Swordman", "Hexapod")
    # 동작이 없는 캐릭터(Dwarf: 슬램만)는 slam 으로
    _c, mons3, _h, _q = M.find_meshes_by_motion(idx, "내려찍기")
    assert mons3[0].name == "Dwarf"


if __name__ == "__main__":
    test_parse()
    test_search()
    test_monsters_and_meshes()
    print("motionsearch 통과")
