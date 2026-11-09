"""AI 모캡(GVHMR/WHAM) 도우미: SMPL 결과 -> 모션 변환, WSL 경로, 설치/실행 스크립트(문법), 상태 해석."""
import os
import subprocess
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import mocapai as M, quat as Q, smplmotion as S  # noqa: E402
from motionbridge.core.io_bvh import read_bvh  # noqa: E402
from motionbridge.core.model import fk  # noqa: E402


def _fake_params(F=12, seed=3):
    rng = np.random.default_rng(seed)
    # 대략 사람 모양의 기본 자세 관절 위치(m): 골반 높이 0.95, 다리/척추/팔
    J = np.zeros((22, 3))
    J[0] = [0, 0.95, 0]
    for i, off in {1: [0.09, -0.08, 0], 2: [-0.09, -0.08, 0], 3: [0, 0.12, 0], 4: [0, -0.4, 0], 5: [0, -0.4, 0], 6: [0, 0.13, 0], 7: [0, -0.4, 0],
                   8: [0, -0.4, 0], 9: [0, 0.05, 0], 10: [0, -0.05, 0.12], 11: [0, -0.05, 0.12], 12: [0, 0.2, 0], 13: [0.05, 0.1, 0],
                   14: [-0.05, 0.1, 0], 15: [0, 0.1, 0.02], 16: [0.1, 0, 0], 17: [-0.1, 0, 0], 18: [0.26, 0, 0], 19: [-0.26, 0, 0],
                   20: [0.25, 0, 0], 21: [-0.25, 0, 0]}.items():
        J[i] = J[M_PARENTS[i]] + np.array(off)
    root = rng.normal(0, 0.4, (F, 3))
    body = rng.normal(0, 0.3, (F, 21, 3))
    tr = np.cumsum(rng.normal(0, 0.02, (F, 3)), axis=0)
    return J, root, body, tr


M_PARENTS = S.SMPL22_PARENTS


def test_smpl_to_motion_and_bvh():
    J, root, body, tr = _fake_params()
    m = S.build(J, root, body, tr, fps=30)
    sk, an = m.skeleton, m.animations[0]
    assert sk.names[0] == "Pelvis" and sk.n == 22 and an.n_frames == 12 and an.fps == 30
    # 독립 계산: SMPL 순기구학 (월드 회전 = 부모 월드 회전 * 로컬 회전, 관절 위치 = 부모 위치 + 부모 월드 회전 * (J[i]-J[부모]))
    wr, wp = fk(sk, an.rot, an.pos)
    for f in (0, 5, 11):
        R = [None] * 22
        P = [None] * 22
        for i in range(22):
            aa = root[f] if i == 0 else body[f, i - 1]
            q = S.axis_angle_to_quat(aa)
            if i == 0:
                R[0], P[0] = q, (J[0] + tr[f]) * S.CM
            else:
                p = M_PARENTS[i]
                R[i] = Q.mul(R[p], q)
                P[i] = P[p] + Q.rotate(R[p], (J[i] - J[p]) * S.CM)
        assert np.allclose(np.array(P), wp[f], atol=1e-6), f
    # BVH 로 쓰고 다시 읽어도 같은 월드 자세
    with tempfile.TemporaryDirectory() as d:
        npz = os.path.join(d, "x.npz")
        np.savez(npz, joints=J, root_orient=root, body_pose=body, transl=tr, fps=30.0)
        bvh, n = S.npz_to_bvh(npz, os.path.join(d, "o", "x.bvh"))
        assert n == 12 and os.path.isfile(bvh)
        back = read_bvh(bvh)
        b_sk, b_an = back.skeleton, back.animations[0]
        assert b_sk.n >= 22 and b_an.n_frames == 12
        idx = [b_sk.index(nm) for nm in S.SMPL22_NAMES]
        bwr, bwp = fk(b_sk, b_an.rot, b_an.pos)
        # 읽은 BVH 의 월드 위치가 원래와 같다 (루트 위치 단위/축은 그대로)
        assert np.allclose(bwp[:, idx], wp, atol=1e-3), float(np.abs(bwp[:, idx] - wp).max())
        # 덤프가 불완전하면 이유를 말한다
        np.savez(os.path.join(d, "bad.npz"), joints=J)
        try:
            S.load_npz(os.path.join(d, "bad.npz"))
            raise AssertionError("실패해야 함")
        except ValueError as e:
            assert "root_orient" in str(e)
    print("smpl -> bvh OK")


def test_paths_and_status():
    assert M.to_wsl_path(r"C:\Users\나\Videos\a b.mp4") == "/mnt/c/Users/나/Videos/a b.mp4"
    assert M.to_wsl_path("d:/x/y.mp4") == "/mnt/d/x/y.mp4" and M.to_wsl_path("/home/a/b") == "/home/a/b"
    assert M.sh_quote("it's") == "'it'\\''s'"
    for e in M.ENGINES.values():
        st = M.parse_probe("wsl=1\ngpu=NVIDIA GeForce RTX 3080, 551.23\ncurl=1\nbzip2=1\nmamba=1\nrepo=1\nenv=1\n" + "\n".join("need:%d=1" % i for i in range(len(e.need_files))))
        assert st.wsl and "RTX 3080" in st.gpu and st.installed() and st.ready(e) and st.models_ok(e)
        st = M.parse_probe("wsl=1\ngpu=\nrepo=1\nenv=1\nneed:0=0\nneed:1=1")
        assert not st.gpu and st.installed() and not st.ready(e) and not st.models_ok(e)
    assert not M.parse_probe("").wsl
    print("paths/status OK")


def _bash_ok(script: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False, encoding="utf-8", newline="\n") as f:
        f.write(script)
    try:
        r = subprocess.run(["bash", "-n", f.name], capture_output=True, text=True)
        return r.stderr
    finally:
        os.remove(f.name)


def test_install_chumpy():
    for e in mocapai.ENGINES.values():
        sc = mocapai.install_script(e)
        check = sc.index("--no-build-isolation chumpy") < sc.index("pip install -r requirements.txt") and "numpy==1.23.5" in sc and ("envs/%s" % e.key) in sc
        assert check, e.key
    print("chumpy OK")


def test_scripts():
    g, w = M.GVHMR, M.WHAM
    scripts = {
        "gvhmr install": M.install_script(g), "wham install": M.install_script(w),
        "gvhmr probe": M.probe_script(g), "wham probe": M.probe_script(w),
        "gvhmr place": M.place_models_script(g, r"C:\Users\x\Downloads\smpl"), "wham place": M.place_models_script(w, "/mnt/d/smpl"),
        "gvhmr run": M.run_script_text(g, r"C:\v\my clip's.mp4", r"C:\out\a.npz", True), "gvhmr run moving": M.run_script_text(g, "/a/b.mp4", "/o/a.npz", False),
        "wham run": M.run_script_text(w, r"C:\v\x.mp4", r"C:\out\a.npz", True, fps=24),
    }
    for k, s in scripts.items():
        err = _bash_ok(s)
        assert not err, "%s: %s" % (k, err)
    gi, wi = scripts["gvhmr install"], scripts["wham install"]
    assert "zju3dv/GVHMR" in gi and "python=3.10" in gi and "gdown --folder" in gi and "gvhmr_siga24_release.ckpt" in gi
    assert "yohanshin/WHAM" in wi and "python=3.9" in wi and "cudatoolkit=11.3" in wi and "ViTPose" in wi
    gr = scripts["gvhmr run"]
    assert "tools/demo/demo.py" in gr and " -s" in gr and "/mnt/c/v/my clip" in gr and "hmr4d_results.pt" in gr and "mb_smpl_dump.py" in gr
    assert " -s" not in scripts["gvhmr run moving"].split("demo.py")[1].split("\n")[0]
    wr = scripts["wham run"]
    assert "--estimate_local_only" in wr and "FPS=24" in wr and "wham_output.pkl" in wr
    assert "dataset/body_models" in scripts["wham place"] and "SMPLX_NEUTRAL.npz" in scripts["gvhmr place"] and "SMPLX_NEUTRAL.npz" not in scripts["wham place"]
    # 덤프 스크립트는 문법이 맞아야 한다 (torch/smplx 없이는 실행하지 않는다)
    compile(M.DUMP_PY, "dump", "exec")
    print("scripts OK")


def test_run_script_and_place_models():
    if os.name == "nt":
        return
    lines = []
    rc = M.run_script('echo hello\necho "두번째"\nexit 3\n', lines.append)
    assert rc == 3 and lines == ["hello", "두번째"], (rc, lines)
    import threading
    ev = threading.Event()
    ev.set()
    assert M.run_script("sleep 30\n", None, cancel=ev) == -1, "취소하면 바로 멈춤"
    # SMPL 파일 놓기: 받은 폴더(하위 폴더/zip 포함)에서 찾아 엔진이 찾는 자리에 복사
    with tempfile.TemporaryDirectory() as d:
        home = os.path.join(d, "home")
        src = os.path.join(d, "dl")
        os.makedirs(os.path.join(src, "smpl", "models"))
        os.makedirs(home)
        open(os.path.join(src, "smpl", "models", "basicModel_neutral_lbs_10_207_0_v1.0.0.pkl"), "w").write("N")
        open(os.path.join(src, "SMPLX_NEUTRAL.npz"), "w").write("X")
        env_home = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            out = []
            rc = M.run_script(M.place_models_script(M.GVHMR, src), out.append)
        finally:
            os.environ["HOME"] = env_home or ""
        base = os.path.join(home, "motionbridge_ai", "gvhmr", "inputs", "checkpoints", "body_models")
        assert rc == 0 and open(os.path.join(base, "smpl", "SMPL_NEUTRAL.pkl")).read() == "N" and open(os.path.join(base, "smplx", "SMPLX_NEUTRAL.npz")).read() == "X", out
        os.environ["HOME"] = home
        try:
            rc = M.run_script(M.place_models_script(M.GVHMR, os.path.join(d, "empty")), [].append)
        finally:
            os.environ["HOME"] = env_home or ""
        assert rc == 4, "파일이 없으면 오류 코드"
    print("run_script/place OK")


if __name__ == "__main__":
    test_smpl_to_motion_and_bvh()
    test_paths_and_status()
    test_scripts()
    test_install_chumpy()
    test_run_script_and_place_models()
    print("test_mocapai ALL OK")
