"""동작 묶음 목록: 몽타주 사본·재생 불가 파일 제외, 시작/반복/끝·번호 묶기, 폴더 분류, 이름으로 정확한 짝."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qt_stub  # noqa: E402

qt_stub.install()
from motionbridge.core import assetidx, movecatalog as M  # noqa: E402
from motionbridge.ui import vidfind_tab as V  # noqa: E402

B = "MortalShell2/Content/Sparta/Characters/Bosses/Swordman/Animation/"
NAMES = ["Attacks/A_Swordman_Attacks_Spins", "Attacks/A_Swordman_Attacks_Spins_Montage",
         "Attacks/A_Swordman_Attacks_Overhead_01", "Attacks/A_Swordman_Attacks_Overhead_01_Montage", "Attacks/A_Swordman_Attacks_Overhead_02",
         "Attacks/Plow/A_Swordman_Attacks_Plow_End", "Attacks/Plow/A_Swordman_Attacks_Plow_Loop", "Attacks/Plow/A_Swordman_Attacks_Plow_Start",
         "Attacks/A_Swordman_Attacks_Plow_End_Uppercut_Montage",
         "Attacks/StompKick/A_Swordman_Attacks_StompKick", "Attacks/StompKick/A_Swordman_Attacks_StompKick_Connect",
         "Attacks/StompKick/A_Swordman_Attacks_StompKick_Connect_Player", "Attacks/StompKick/A_Swordman_Attacks_StompKick_Connect_Player_Montage",
         "Death/A_Swordman_Death", "HitReactions/A_Swordman_HitReactions_Stun", "HitReactions/A_Swordman_HitReaction_Stun_02",
         "Locomotion/A_Swordman_Locomotion_Walk_Fwd", "Locomotion/A_Swordman_Locomotion_Walk_BackLeft", "Locomotion/BS_Swordman",
         "Riposte/A_Swordman_Riposte_01", "Riposte/A_Swordman_Riposte_01_Player",
         "Riposte/A_Swordman_Riposte_PhaseTransition_Enemy", "Riposte/A_Swordman_Riposte_PhaseTransition_Player",
         "BT_PairedAnimReaction_Swordman"]
ANIMS = [B + n + ".uasset" for n in NAMES]


def P(name):
    return [a for a in ANIMS if a.endswith("/" + name + ".uasset")][0]


def main():
    mc = M.build(ANIMS, ["Swordman"])
    assert set(mc.non_clips) == {P("BS_Swordman"), P("BT_PairedAnimReaction_Swordman")}, mc.non_clips
    assert set(mc.duplicates) == {P("A_Swordman_Attacks_Spins_Montage"), P("A_Swordman_Attacks_Overhead_01_Montage"), P("A_Swordman_Attacks_StompKick_Connect_Player_Montage")}, mc.duplicates
    assert P("A_Swordman_Attacks_Plow_End_Uppercut_Montage") in mc.clips and mc.clips[P("A_Swordman_Attacks_Plow_End_Uppercut_Montage")].montage, "원본이 없는 몽타주는 남긴다"
    moves = {(m.category, m.name): [os.path.basename(c.path)[:-7] for c in m.clips] for m in mc.moves}
    assert moves[("attack", "Plow")][:3] == ["A_Swordman_Attacks_Plow_Start", "A_Swordman_Attacks_Plow_Loop", "A_Swordman_Attacks_Plow_End"], moves[("attack", "Plow")]
    assert moves[("attack", "Plow")][-1].endswith("Uppercut_Montage"), "몽타주는 그 동작의 맨 뒤"
    assert moves[("attack", "Overhead")] == ["A_Swordman_Attacks_Overhead_01", "A_Swordman_Attacks_Overhead_02"]
    assert moves[("hitreaction", "Stun")] == ["A_Swordman_HitReactions_Stun", "A_Swordman_HitReaction_Stun_02"], "HitReaction/HitReactions 표기가 섞여도 한 동작"
    assert moves[("locomotion", "Walk")] == ["A_Swordman_Locomotion_Walk_BackLeft", "A_Swordman_Locomotion_Walk_Fwd"], "방향은 같은 동작의 변형"
    cats = [m.category for m in mc.moves]
    assert cats.index("attack") < cats.index("riposte") < cats.index("hitreaction") < cats.index("death") < cats.index("locomotion"), cats
    assert mc.clips[P("A_Swordman_Attacks_Plow_Start")].label() == "공격 · Plow (시작)" and mc.clips[P("A_Swordman_Death")].label() == "사망"
    # 짝
    assert mc.partners[P("A_Swordman_Riposte_01")] == [P("A_Swordman_Riposte_01_Player")] and mc.partners[P("A_Swordman_Riposte_01_Player")] == [P("A_Swordman_Riposte_01")]
    assert mc.partners[P("A_Swordman_Riposte_PhaseTransition_Enemy")] == [P("A_Swordman_Riposte_PhaseTransition_Player")]
    assert P("A_Swordman_Attacks_StompKick") not in mc.partners and P("A_Swordman_Attacks_Spins") not in mc.partners
    other = B.replace("Swordman", "Hexapod") + "Riposte/A_Hexapod_Riposte_01_Player.uasset"
    assert M.exact_partners(P("A_Swordman_Attacks_StompKick_Connect"), ANIMS + [other]) == [P("A_Swordman_Attacks_StompKick_Connect_Player")], "몽타주 사본·다른 캐릭터는 짝이 아님"
    assert M.exact_partners(P("A_Swordman_Riposte_01"), ANIMS + [other]) == [P("A_Swordman_Riposte_01_Player")]
    assert M.exact_partners(P("A_Swordman_Death"), ANIMS) == []
    # 폴더가 없는 이름 규칙: 첫 낱말이 분류
    flat = M.build(["G/Mon/AS_Golem_Attack_Slam_01.uasset", "G/Mon/AS_Golem_Attack_Slam_02.uasset", "G/Mon/AS_Golem_Idle.uasset"], ["Golem"])
    assert [(m.category, m.name, len(m.clips)) for m in flat.moves] == [("attack", "Slam", 2), ("idle", "Idle", 1)], [(m.category, m.name) for m in flat.moves]
    # 화면 목록 순서: 캐릭터 폴더 것은 동작 순서로, 사본·재생 불가는 빠지고, 폴더 밖 후보는 뒤에
    stray = "X/Other/A_Other_Attack.uasset"
    cands = [assetidx.AnimCandidate(a, 0.5, "") for a in reversed(ANIMS)] + [assetidx.AnimCandidate(stray, 0.9, "이름 비슷")]
    out, n_hidden = V.VidFindTab._group_by_moves(cands, mc, None)
    paths = [c.path for c in out]
    assert n_hidden == 5 and not (set(paths) & mc.hidden()) and paths[-1] == stray and paths[:-1] == mc.order(), paths[:4]
    assert "동작 〔반격·처형 (01)〕 · 짝: A_Swordman_Riposte_01_Player" == out[paths.index(P("A_Swordman_Riposte_01"))].why
    out2, _ = V.VidFindTab._group_by_moves(cands, mc, "death")
    assert out2[0].path == P("A_Swordman_Death"), "분류를 고르면 그 분류 동작이 먼저"
    print("test_movecatalog ALL OK")


main()
