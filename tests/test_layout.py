"""추출 폴더 정리 테스트: 출력 폴더 / 게임 이름 / 캐릭터 / Animations|Models."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge.core import extractor, layout, meshconv  # noqa: E402
import test_extract as TE  # noqa: E402


def test_character_of():
    c = layout.character_of
    assert c("MortalShell2/Content/Sparta/Characters/Bosses/Swordman/Art/Mesh/SK_Swordman.uasset") == "Swordman", "Art 폴더는 캐릭터 이름이 아님"
    assert c("/Game/Sparta/Characters/Bosses/Swordman/Animation/Attacks/AS_Swordman_SpinAttack_01") == "Swordman"
    assert c("Sparta/Content/Characters/Enemies/NamelessCaptive/Meshes/SK_NamelessCaptive.uasset") == "NamelessCaptive"
    assert c("Game/Content/Art/Monsters/Humanoid/Prisoner/Animations/Attack/A_Prisoner_Attack01") == "Prisoner"
    assert c("Game/Content/Anims/AM_Dwarf_Heavy_02") == "Dwarf"
    assert c("Game/Content/SK_Hero.uasset") == "Hero"
    assert c("") == layout.MISC
    assert layout.safe_name('a<b>:c/d') == "a_b__c_d" and layout.safe_name("  ") == layout.MISC
    taken = set()
    assert layout.unique_name("d", "x.psa", taken) == "x.psa" and layout.unique_name("d", "X.psa", taken) == "X_2.psa"
    print("character_of OK")


def test_game_name():
    with tempfile.TemporaryDirectory() as d:
        game = os.path.join(d, "steamapps", "common", "Sparta")
        os.makedirs(game)
        assert layout.detect_game_name(game) == "Sparta"
        open(os.path.join(d, "steamapps", "appmanifest_1.acf"), "w").write('"AppState"\n{\n\t"name"\t\t"Mortal Shell 2"\n\t"installdir"\t\t"Sparta"\n}')
        assert layout.detect_game_name(game) == "Mortal Shell 2"
        assert layout.detect_game_name(os.path.join(d, "other")) == "other"
    print("game_name OK")


def test_export_with_layout():
    if os.name == "nt":
        print("test_export_with_layout: Windows 에서는 건너뜀")
        return
    with tempfile.TemporaryDirectory() as d:
        fb = TE.make_helper(d)
        out = os.path.join(d, "o")
        lay = layout.Layout(out, "Mortal Shell 2")
        paths = ["Sparta/Content/Characters/Enemies/Prisoner/Animations/AS_Walk.uasset",
                 "Sparta/Content/Characters/Enemies/Prisoner/Animations/AS_Run.uasset",
                 "Sparta/Content/Characters/Enemies/Dwarf/Animations/AS_Idle.uasset"]
        files = extractor.export_assets(fb, d, "GAME_UE5_6", None, None, paths, out, kind="anim", lay=lay)
        rel = sorted(os.path.relpath(f, out) for f in files)
        assert rel == sorted([os.path.join("Mortal Shell 2", "Prisoner", "Animations", "AS_Walk.psa"),
                              os.path.join("Mortal Shell 2", "Prisoner", "Animations", "AS_Run.psa"),
                              os.path.join("Mortal Shell 2", "Dwarf", "Animations", "AS_Idle.psa")]), rel
        assert all(os.path.isfile(f) for f in files)
        assert [x for x in os.listdir(out) if x != "Mortal Shell 2"] == [], os.listdir(out)  # 임시 폴더 없음
        # 캐릭터를 직접 정하면 애니메이션 경로가 달라도 한 폴더로
        lay2 = layout.Layout(out, "G", force_char="Captive")
        files = extractor.export_assets(fb, d, "GAME_UE5_6", None, None, paths[:1] + paths[2:], out, kind="anim", lay=lay2)
        assert {os.path.dirname(f) for f in files} == {os.path.join(out, "G", "Captive", "Animations")}
        # 메시: 변환본과 원본이 캐릭터의 Models 한 폴더에
        real = meshconv.convert_mesh

        def fake_convert(src, o, formats, variants, preset, blender=None, on_log=None):
            os.makedirs(o, exist_ok=True)
            r = os.path.join(o, os.path.basename(src).split(".")[0] + "_rigged.fbx")
            open(r, "wb").write(b"x")
            return [r]
        meshconv.convert_mesh = fake_convert
        try:
            mp = ["Sparta/Content/Characters/Enemies/Prisoner/Meshes/SK_Prisoner.uasset"]
            files = extractor.export_meshes(fb, d, "GAME_UE5_6", None, None, mp, out, ["fbx"], ["rigged"], "max", lay=lay)
            md = os.path.join(out, "Mortal Shell 2", "Prisoner", "Models")
            assert sorted(os.listdir(md)) == ["SK_Prisoner.psk", "SK_Prisoner_rigged.fbx"], os.listdir(md)
            assert all(os.path.dirname(f) == md for f in files)
            files = extractor.export_meshes(fb, d, "GAME_UE5_6", None, None, mp, out, ["fbx"], ["rigged"], "max", keep_raw=False, lay=lay)
            assert [os.path.basename(f) for f in files] == ["SK_Prisoner_rigged.fbx"]
            files = extractor.export_meshes(fb, d, "GAME_UE5_6", None, None, mp, out, [], [], "max", lay=lay)
            assert [os.path.dirname(f) for f in files] == [md]
        finally:
            meshconv.convert_mesh = real
        assert not any(n.startswith(".mb_stage") for n in os.listdir(out))
    print("export with layout OK")


def test_program_dirs_and_fbm():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        a = layout.program_dir(os.path.join(d, "C", "Animations"), "max")
        assert a.endswith(os.path.join("Animations", "3dsMax")) and layout.program_dir("x", "blender").endswith("Blender") and layout.program_dir("x", "maya").endswith("Maya")
        assert layout.program_dir("x", "max", organize=False) == "x"
        os.makedirs(a)
        layout.write_guide(a, "max")
        g = os.path.join(a, layout.GUIDE_NAME)
        assert os.path.exists(g) and ".ms" in open(g, encoding="utf-8").read()
        open(g, "w", encoding="utf-8").write("내가 고친 것")
        layout.write_guide(a, "max")    # 이미 있으면 덮어쓰지 않는다
        assert open(g, encoding="utf-8").read() == "내가 고친 것"
        # 빈 .fbm 만 지운다 (텍스처가 든 것은 유지)
        e, f = os.path.join(a, "x.fbm"), os.path.join(a, "y.fbm")
        os.makedirs(e)
        os.makedirs(f)
        open(os.path.join(f, "t.png"), "wb").write(b"1")
        gone = layout.remove_empty_fbm(d)
        assert gone == [e] and not os.path.exists(e) and os.path.exists(f), gone
    print("program dirs / fbm OK")


if __name__ == "__main__":
    test_character_of()
    test_game_name()
    test_export_with_layout()
    test_program_dirs_and_fbm()
    print("test_layout ALL OK")
