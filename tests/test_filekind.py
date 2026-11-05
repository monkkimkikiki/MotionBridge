"""파일 종류 이름표: 처음 보는 사람도 모델링/애니메이션/리깅 유무를 알 수 있어야 한다."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import filekind  # noqa: E402


def test_describe():
    d = lambda n: filekind.describe(n)[:2]
    assert d("Animations/A_Roll.psa") == ("[애니메이션]", "anim")
    assert d("Models/SK_Dwarf_rigged.fbx") == ("[모델링 · 리깅 포함]", "model")
    assert d("Models/SK_Dwarf_static.fbx") == ("[모델링 · 리깅 없음]", "model")
    assert d("Models/SK_Dwarf_static.obj") == ("[모델링 · 리깅 없음]", "model")
    assert d("Models/SK_Dwarf_rigged.glb") == ("[모델링 · 리깅 포함]", "model")
    assert d("Models/_raw/SK_Dwarf.pskx") == ("[모델링 원본]", "model")
    assert d("out/A_Walk_max.fbx") == ("[애니메이션 FBX]", "anim")
    assert d("out/thing.fbx") == ("[3D 파일]", "other")
    assert d("x.ms")[0] == "[Max 스크립트]" and d("x.mbbip")[0] == "[Biped 데이터]" and d("x.xyz")[0] == "[기타]"
    assert all(filekind.describe(p)[2] for p in ["a.psa", "a.pskx", "a_rigged.fbx", "a.ms", "a.zip"])
    assert filekind.folder_hint("a/b/Models") == "모델링 데이터" and filekind.folder_hint("Animations")
    assert filekind.folder_hint("Content") == ""
    print("filekind OK")


if __name__ == "__main__":
    test_describe()
