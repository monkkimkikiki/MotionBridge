"""재질 → 기본 색 텍스처 연결 (.mat / 이름 추정)."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import mattex


def touch(p, text=""):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def test_mat_file_and_guess():
    with tempfile.TemporaryDirectory() as d:
        mesh = os.path.join(d, "Game", "Boss", "Models", "SK_Boss.pskx")
        touch(mesh)
        # ① .mat 파일이 가리키는 텍스처 (이름이 재질과 전혀 달라도 연결)
        touch(os.path.join(d, "Game", "Boss", "Materials", "MI_Boss_Body.mat"), "Diffuse=T_Strange_01\nNormal=T_Strange_01_N\n")
        touch(os.path.join(d, "Game", "Boss", "Textures", "T_Strange_01.png"))
        touch(os.path.join(d, "Game", "Boss", "Textures", "T_Strange_01_N.png"))
        # ③ 이름 추정: 노멀/마스크는 고르지 않고 _D 를 고른다
        touch(os.path.join(d, "Game", "Boss", "Textures", "T_Boss_Head_N.png"))
        touch(os.path.join(d, "Game", "Boss", "Textures", "T_Boss_Head_ORM.png"))
        touch(os.path.join(d, "Game", "Boss", "Textures", "T_Boss_Head_D.tga"))
        # 전혀 상관없는 그림
        touch(os.path.join(d, "Game", "Boss", "Textures", "T_Rock_D.png"))
        got = mattex.find_textures(mesh, ["MI_Boss_Body", "MI_Boss_Head", "MI_Boss_Cape"])
        assert got["MI_Boss_Body"]["how"] == "mat" and got["MI_Boss_Body"]["path"].endswith("T_Strange_01.png"), got
        assert got["MI_Boss_Head"]["how"] == "name" and got["MI_Boss_Head"]["path"].endswith("T_Boss_Head_D.tga"), got
        assert "MI_Boss_Cape" not in got, "닮은 텍스처가 없으면 억지로 잇지 않는다"
        lst = mattex.texture_list(mesh, ["MI_Boss_Body", "MI_Boss_Cape"])
        assert lst[0].endswith("T_Strange_01.png") and lst[1] == ""
        assert mattex.find_textures(mesh, []) == {}
    assert mattex._tex_stem("/Game/X/T_Body_D.T_Body_D") == "t_body_d"
    assert mattex.core_name("MI_Boss_Body_Inst") == "boss_body"
    print("mattex OK")


if __name__ == "__main__":
    test_mat_file_and_guess()
