"""동일 모션 모으기: 종류(attack/locomotion…)/동작 폴더로 복사, 이름 겹침·중복·몽타주 처리, 원본은 그대로."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from motionbridge.core import motionlib  # noqa: E402


def _mk(root, rel, data=b"x"):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return p


def main():
    with tempfile.TemporaryDirectory() as d:
        g = os.path.join(d, "out", "Game")
        a1 = _mk(g, "Swordman/Animations/Attacks/A_Swordman_Attacks_Plow_Start.psa", b"1")
        a2 = _mk(g, "Swordman/Animations/Attacks/A_Swordman_Attacks_Plow_End.psa", b"2")
        a3 = _mk(g, "Swordman/Animations/Attacks/A_Swordman_Attacks_Spins.psa", b"3")
        a3m = _mk(g, "Swordman/Animations/Attacks/A_Swordman_Attacks_Spins_Montage.psa", b"3")
        bs = _mk(g, "Swordman/Animations/Locomotion/BS_Swordman.psa", b"4")
        r1 = _mk(g, "Swordman/Animations/Locomotion/A_Swordman_Locomotion_Run_Fwd.psa", b"5")
        r2 = _mk(g, "Boar/Animations/Locomotion/A_Boar_Locomotion_Run_Fwd.psa", b"6")
        junk = _mk(g, "Boar/Models/Boar.glb", b"7")
        lib = os.path.join(d, "out", "Library")
        rep = motionlib.collect(motionlib.scan(os.path.join(d, "out"), lib) + [junk], lib)
        got = sorted(os.path.relpath(t, lib).replace("\\", "/") for _s, t in rep.copied)
        assert got == ["attack/Plow/A_Swordman_Attacks_Plow_End.psa", "attack/Plow/A_Swordman_Attacks_Plow_Start.psa",
                       "attack/Spins/A_Swordman_Attacks_Spins.psa", "locomotion/Run/A_Boar_Locomotion_Run_Fwd.psa",
                       "locomotion/Run/A_Swordman_Locomotion_Run_Fwd.psa"], got
        why = dict(rep.skipped)
        assert "몽타주" in why[a3m] and "클립이 아님" in why[bs] and junk in why, why
        assert all(os.path.exists(p) for p in (a1, a2, a3, a3m, bs, r1, r2, junk)), "원본은 그대로"
        idx = json.load(open(os.path.join(lib, motionlib.INDEX_NAME), encoding="utf-8"))
        assert idx["종류별_개수"] == {"attack": 3, "locomotion": 2}, idx["종류별_개수"]
        # 다시 돌리면 같은 내용은 건너뛴다 (라이브러리 안 파일은 다시 모으지 않음)
        rep2 = motionlib.collect(motionlib.scan(os.path.join(d, "out")), lib)
        assert not rep2.copied and len(rep2.same) == 5, (len(rep2.copied), len(rep2.same))
        # 같은 이름·다른 내용 -> 캐릭터 이름을 붙여 둘 다 남긴다
        other = _mk(os.path.join(d, "out", "Game2"), "Boar/Animations/Locomotion/A_Boar_Locomotion_Run_Fwd.psa", b"DIFFERENT")
        rep3 = motionlib.collect([other], lib)
        assert len(rep3.renamed) == 1 and os.path.basename(rep3.renamed[0][1]) == "Boar__A_Boar_Locomotion_Run_Fwd.psa", rep3.renamed
        assert open(os.path.join(lib, "locomotion", "Run", "A_Boar_Locomotion_Run_Fwd.psa"), "rb").read() == b"6"
        print("test_motionlib ALL OK")


main()
