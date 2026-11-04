"""추출 흐름 테스트: 게임 검사 -> AES 게이트 -> (가짜) 도우미 호출 -> PSA 를 소스 모션으로."""
import io
import json
import os
import stat
import sys
import tempfile
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge import cli  # noqa: E402
from motionbridge.core import extractor  # noqa: E402
from motionbridge.core.io_psa import write_psa  # noqa: E402
from motionbridge.core.pipeline import Project  # noqa: E402
import test_gamescan as GS  # noqa: E402
import synth  # noqa: E402

FAKE = '''#!{py}
import sys, os, json
sys.path.insert(0, {root!r}); sys.path.insert(0, {tests!r})
a = sys.argv[1:]
def opt(n):
    return a[a.index(n) + 1] if n in a else None
open(os.environ["FAKE_LOG"], "a").write(json.dumps(a) + "\\n")
def emit(**k): print(json.dumps(k), flush=True)
if opt("--aes") == "0xBAD":
    emit(type="error", message="AES 키가 맞지 않습니다"); sys.exit(1)
emit(type="log", message="마운트된 파일 3개")
if a[0] == "list":
    for p in ["Game/Content/Anim/AS_Walk.uasset", "Game/Content/Anim/AS_Run.uasset", "Game/Content/Anim/AM_Skip.uasset"]:
        if __import__("re").search(opt("--filter"), p):
            emit(type="asset", path=p)
    emit(type="done", count=2)
elif a[0] == "export":
    from motionbridge.core.io_psa import write_psa
    import synth
    sk, meta = synth.build_skeleton("ue", "z_up_x", 100.0, "A", "bone_x")
    out = opt("--out"); n = 0
    for p in open(opt("--paths-file"), encoding="utf-8").read().split("\\n"):
        if "Skip" in p:
            emit(type="skip", path=p, reason="AnimSequence 가 아님"); continue
        an, _ = synth.walk_animation(sk, meta, n_frames=24, name=os.path.basename(p)[:-7])
        f = os.path.join(out, os.path.basename(p)[:-7] + ".psa")
        write_psa(f, sk, [an], conj=(False, True))
        emit(type="exported", path=p, file=f); n += 1
    emit(type="done", count=n)
'''


def run_cli(argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = cli.main(argv)
    return rc, buf.getvalue()


def make_helper(d):
    fb = os.path.join(d, "mbextract")
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    open(fb, "w").write(FAKE.format(py=sys.executable, root=root, tests=os.path.dirname(os.path.abspath(__file__))))
    os.chmod(fb, os.stat(fb).st_mode | stat.S_IEXEC)
    os.environ["FAKE_LOG"] = os.path.join(d, "log.txt")
    return fb


def test_flow_unencrypted():
    if os.name == "nt":
        print("test_flow_unencrypted: Windows 에서는 건너뜀 (셔뱅 가짜 실행파일 사용)")
        return
    with tempfile.TemporaryDirectory() as d:
        GS.build(os.path.join(d, "game"), False) if os.makedirs(os.path.join(d, "game")) is None else None
        game = os.path.join(d, "game")
        fb = make_helper(d)
        rc, txt = run_cli(["extract", "scan", "--game", game])
        assert rc == 0 and "바로 추출" in txt, txt
        rc, txt = run_cli(["extract", "list", "--game", game, "--helper", fb, "--filter", "(?i)AS_"])
        assert rc == 0 and "AS_Walk" in txt and "AM_Skip" not in txt and "usmap" in txt, txt
        out = os.path.join(d, "out")
        rc, txt = run_cli(["extract", "export", "--game", game, "--helper", fb, "--out", out, "--filter", "(?i)anim", "--usmap", __file__])
        assert rc == 0 and "건너뜀" in txt and "PSA 2개" in txt, txt
        psas = sorted(f for f in os.listdir(out) if f.endswith(".psa"))
        assert psas == ["AS_Run.psa", "AS_Walk.psa"], psas
        calls = [json.loads(x) for x in open(os.path.join(d, "log.txt")).read().splitlines()]
        assert "--aes" not in calls[0], "암호화 안 된 게임에 키가 넘어가면 안 됨"
        # 추출한 PSA 를 소스 모션으로
        pr = Project()
        m = pr.load_source(os.path.join(out, "AS_Walk.psa"))
        assert m.animations[0].n_frames == 24 and len(pr.src_slots) > 15
    print("extract flow (unencrypted) OK")


def test_flow_encrypted():
    if os.name == "nt":
        print("test_flow_encrypted: Windows 에서는 건너뜀 (셔뱅 가짜 실행파일 사용)")
        return
    with tempfile.TemporaryDirectory() as d:
        game = os.path.join(d, "game")
        os.makedirs(game)
        GS.build(game, True)
        fb = make_helper(d)
        rc, txt = run_cli(["extract", "list", "--game", game, "--helper", fb])
        assert rc == 2 and "AES 키" in txt and bytes(range(16)).hex() in txt, txt
        assert not os.path.exists(os.path.join(d, "log.txt")), "키 없이 도우미를 호출하면 안 됨"
        rc, txt = run_cli(["extract", "list", "--game", game, "--helper", fb, "--aes", "not-a-key"])
        assert rc == 1 and "형식" in txt, txt
        rc, txt = run_cli(["extract", "list", "--game", game, "--helper", fb, "--aes", "0x" + "ab" * 32])
        assert rc == 0 and "AS_Walk" in txt, txt
        calls = [json.loads(x) for x in open(os.path.join(d, "log.txt")).read().splitlines()]
        assert calls[-1][calls[-1].index("--aes") + 1] == "0x" + "AB" * 32
        # 도우미가 보고한 오류는 그대로 전달
        try:
            extractor.list_assets(fb, game, "GAME_UE5_4", "0xBAD" if False else None, None)
        except extractor.ExtractError:
            raise AssertionError("키 없이도 가짜 도우미는 성공해야 함")
        os.environ["FAKE_BAD"] = "1"
        try:
            extractor.run_helper(fb, ["list", "--aes", "0xBAD", "--filter", "x"])
        except extractor.ExtractError as e:
            assert "AES 키가 맞지 않습니다" in str(e)
        else:
            raise AssertionError("오류 이벤트가 예외가 되어야 함")
    print("extract flow (encrypted) OK")


def test_no_helper():
    if os.name == "nt":
        print("test_no_helper: Windows 에서는 건너뜀 (셔뱅 가짜 실행파일 사용)")
        return
    with tempfile.TemporaryDirectory() as d:
        game = os.path.join(d, "g")
        os.makedirs(game)
        GS.build(game, False)
        os.environ["PATH"] = ""
        os.environ.pop("MOTIONBRIDGE_HELPER", None)
        from motionbridge.core import bridge
        old = bridge.CONFIG_PATH
        bridge.CONFIG_PATH = os.path.join(d, "none.json")
        try:
            rc, txt = run_cli(["extract", "list", "--game", game])
        finally:
            bridge.CONFIG_PATH = old
        if extractor.find_helper() is None:
            assert rc == 3 and "mbextract" in txt, txt
    print("no helper OK")


if __name__ == "__main__":
    test_flow_unencrypted()
    test_flow_encrypted()
    test_no_helper()
    print("test_extract ALL OK")
