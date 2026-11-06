"""쉼표(,) = 모두 포함 검색."""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import assetidx, motionsearch, textmatch as T  # noqa: E402


def test_helpers():
    assert T.parts(" character , attack ,,") == ["character", "attack"]
    assert T.terms("Character,Attack heavy") == ["character", "attack", "heavy"]
    assert T.contains_all("X/Player/A_PC_HeavyAttack", "player,attack") and not T.contains_all("X/Player/A_PC_Roll", "player,attack")
    assert T.contains_all("anything", "")
    # 정규식 안의 쉼표는 나누지 않는다
    assert T.split_top_level("a{2,3},[x,y],(p|q,r),z") == ["a{2,3}", "[x,y]", "(p|q,r)", "z"]
    assert T.split_top_level(r"a\,b,c") == [r"a\,b", "c"]
    assert T.and_regex("walk") == "walk" and T.and_regex("(?i)(walk|run)") == "(?i)(walk|run)"
    rx = re.compile(T.and_regex("character,attack"))
    assert rx.search("Game/Characters/Hero/Animations/A_Attack_01") and not rx.search("Game/Characters/Hero/Idle")
    assert rx.search("GAME/CHARACTERS/X/ATTACK"), "쉼표 검색은 대소문자 무시"
    rx = re.compile(T.and_regex("(?i)Walk,Hero"))
    assert rx.search("a/hero/WALK") and not rx.search("a/hero/run")
    rx = re.compile(T.and_regex("a{2,3},b"))  # 반복 횟수의 쉼표는 정규식 그대로
    assert rx.search("xaab") and not rx.search("xab")


def test_assets():
    paths = ["G/Characters/Hero/Animations/A_Attack_01", "G/Characters/Hero/Animations/A_Roll", "G/Monsters/Dwarf/Animations/A_Attack_1"]
    assert assetidx.filter_paths(paths, "hero,attack") == [paths[0]]
    assert assetidx.filter_paths(paths, "(?i)attack") == [paths[0], paths[2]]
    idx = assetidx.AssetIndex(["G/Characters/Hero/Meshes/SK_Hero.SK_Hero", "G/Monsters/Dwarf/Meshes/SK_Dwarf.SK_Dwarf"], [])
    assert [c.name for c in idx.search("monsters,dwarf")] == ["Dwarf"]
    assert idx.search("hero,dwarf") == [] or all("hero" in c.path.lower() and "dwarf" in c.path.lower() for c in idx.search("hero,dwarf"))


def test_motion_and():
    meshes = ["G/Monsters/Swordman/Meshes/SK_Swordman.SK_Swordman"]
    anims = ["G/Monsters/Swordman/Animations/AS_Swordman_SpinAttack_01", "G/Monsters/Swordman/Animations/AS_Swordman_Jump",
             "G/Monsters/Swordman/Animations/AS_Swordman_JumpSlam", "G/Monsters/Swordman/Animations/AS_Swordman_Idle"]
    idx = assetidx.AssetIndex(meshes, anims)
    one, _ = motionsearch.search_anims(idx, "점프")
    both, _ = motionsearch.search_anims(idx, "점프,내려찍기")
    names = [os.path.basename(h.path) for h in both]
    assert names == ["AS_Swordman_JumpSlam"], names
    assert len(one) >= len(both) and {os.path.basename(h.path) for h in one} >= set(names)
    none, _ = motionsearch.search_anims(idx, "스핀,점프")
    assert none == []


if __name__ == "__main__":
    test_helpers()
    test_assets()
    test_motion_and()
    print("test_textmatch ALL OK")
