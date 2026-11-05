import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core.pathtree import build_tree  # noqa: E402


def names(ns):
    return [(n.name, n.count) for n in ns]


def test_tree():
    ps = [
        "MS2/Content/Chars/Boss/Anim/Atk/A_Hit_01.uasset",
        "MS2/Content/Chars/Boss/Anim/Atk/A_Hit_02.uasset",
        "MS2/Content/Chars/Boss/Anim/Walk/A_Walk.uasset",
        "MS2/Content/Chars/Boss/Anim/A_Idle.uasset",
        "MS2/Content/Chars/Player/A_Roll.uasset",
        "MS2/Content/Chars/Player/A_Roll.uasset",  # 중복은 한 번
    ]
    t = build_tree(ps, strip_ext=True)
    # 공통 앞부분은 한 줄로 합쳐지고, 총 개수가 표시된다
    assert names(t) == [("MS2/Content/Chars", 5)], names(t)
    top = t[0]
    assert names(top.children) == [("Boss/Anim", 4), ("Player", 1)]
    anim = top.children[0]
    # 폴더가 먼저, 파일이 나중. 각각 이름순
    assert [c.name for c in anim.children] == ["Atk", "Walk", "A_Idle"]
    assert [c.name for c in anim.children[0].children] == ["A_Hit_01", "A_Hit_02"]
    assert anim.children[2].is_leaf and anim.children[2].path.endswith("A_Idle.uasset")
    # 폴더에서 아래 모든 파일
    assert top.leaves() == [c for c in top.leaves()] and len(top.leaves()) == 5
    assert sorted(anim.children[0].leaves()) == sorted(p for p in ps[:2])
    # 확장자 유지 / 윈도우 경로 / 빈 입력
    t2 = build_tree([r"C:\a\b\x.psa", r"C:\a\b\y.psa", r"C:\a\c\z.psa"])
    assert names(t2) == [("C:/a", 3)] and [c.name for c in t2[0].children] == ["b", "c"] and t2[0].children[0].children[0].name == "x.psa"
    assert build_tree([]) == []
    # 폴더 하나에 파일 하나뿐이면 폴더를 합치지 않고 파일은 그대로 (폴더만 합침)
    t3 = build_tree(["a/b/c.txt"])
    assert names(t3) == [("a/b", 1)] and t3[0].children[0].is_leaf
    # 같은 이름의 폴더와 파일이 섞여도 폴더 구조가 깨지지 않는다
    t4 = build_tree(["x/y", "x/y/z"])
    assert t4[0].count == 2
    print("pathtree OK")


if __name__ == "__main__":
    test_tree()
    print("test_pathtree ALL OK")
