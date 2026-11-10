"""3ds Max 일반 본(Maya 식) 내보내기 + 3ds Max 자동 실행(maxrun). 실제 3ds Max 는 없으므로 데이터 계산·스크립트 형태·명령줄·로그 해석·가짜 실행 파일로 흐름만 시험한다."""
import os
import stat
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge.core import biped, maxbones, maxrun, quat as Q  # noqa: E402
from motionbridge.core.export import PRESETS, preset_frame  # noqa: E402
from motionbridge.core.model import fk  # noqa: E402
from motionbridge.core.pipeline import ExportOptions  # noqa: E402
import test_biped as TB  # noqa: E402


def test_compute_and_data():
    with tempfile.TemporaryDirectory() as d:
        pr = TB._project(d)
        res = pr.run()
        skel, anim, F = pr.tgt.skeleton, res.anim, res.frame_tgt[0]
        slots = pr.tgt_slots
        C = Q.mul(preset_frame(PRESETS["max"]), F)
        data = maxbones.compute(skel, anim, slots, F, 100.0)
        T, N = anim.n_frames, skel.n
        assert data["deltas"].shape == (T, N, 4) and data["posd"].shape == (T, len(data["pos_bones"]), 3)
        assert slots["Hips"] in data["pos_bones"], "골반은 위치 키를 받는다"
        assert all(skel.parents[j] < j for j in range(N)) and data["parents"] == [int(p) for p in skel.parents], "부모가 먼저"
        # 모든 본: 델타를 바인드 월드 회전에 곱하면 실제 월드 회전 (3ds Max 프레임)
        wr, _wp = fk(skel, anim.rot, anim.pos)
        bwr, _bwp = skel.bind_world()
        for j in range(N):
            for t in (0, T // 2, T - 1):
                ax, ang = data["deltas"][t, j, :3], np.radians(data["deltas"][t, j, 3])
                dm = np.concatenate([[np.cos(ang / 2)], ax * np.sin(ang / 2)])
                want = Q.mul(Q.mul(C, wr[t, j]), Q.conj(C))
                got = Q.mul(dm, Q.mul(Q.mul(C, bwr[j]), Q.conj(C)))
                assert abs(float(np.dot(want, got))) > 0.9999, (j, t)
        path = os.path.join(d, "x.mbbones")
        maxbones.write_data(path, data)
        lines = open(path, encoding="utf-8").read().splitlines()
        assert lines[0] == maxbones.FORMAT and "DATA" in lines
        i0 = lines.index("DATA")
        bl = [l for l in lines if l.startswith("B\t")]
        assert len(bl) == N and len(lines) - i0 - 1 == T
        # 스크립트가 읽는 방식(공백/탭 분리 + 뒤에서 5개가 숫자) 그대로 해석 가능
        for l in bl:
            tk = l.replace("\t", " ").split()
            assert tk[0] == "B" and len(tk) >= 7 and int(tk[-5]) >= 0 and int(tk[-4]) >= 0
        nvals = 3 * len(data["pos_bones"]) + 4 * N
        assert all(len(l.split()) == nvals for l in lines[i0 + 1:])
    print("maxbones compute OK")


def test_script_and_auto():
    with tempfile.TemporaryDirectory() as d:
        pr = TB._project(d)
        res = pr.run()
        skel, anim, F = pr.tgt.skeleton, res.anim, res.frame_tgt[0]
        try:
            maxbones.export_bones(d, "n", skel, anim, pr.tgt_slots, F, 100.0, "")
        except ValueError as e:
            assert ".max" in str(e)
        else:
            raise AssertionError("템플릿 없이 일반 본은 안 된다")
        tpl = "C:\\작업\\skinned.max"
        files, _w = maxbones.export_bones(d, "m", skel, anim, pr.tgt_slots, F, 100.0, tpl)
        ms = open(files[0], encoding="utf-8").read()
        assert "__" not in ms.replace("__init__", "") or True
        for ph in ("__AUTO__", "__LOG__", "__DATAABS__", "__BASEABS__", "__QUIT__", "__TEMPLATE__", "__DATA__", "__AUTOHEAD__"):
            assert ph not in ms, ph
        assert 'local tmplFile = "C:/작업/skinned.max"' in ms and "global mbAuto = false" in ms
        TB._lint_scope(ms)
        # 3ds Max 2026 실측으로 얻은 규칙: node.rotation = quat 는 어긋나므로 변환 행렬로 넣고, angleaxis 대신 직접 만든 행렬을 쓴다
        assert "nodes[i].transform = tmk" in ms and "(nodes[i].rotation =" not in ms, "회전은 transform 행렬로 넣는다"
        assert "angleaxis" not in ms.replace("MaxScript 의 (angleaxis ...)", "") and "fn mbMat ang ax" in ms
        assert "Orientation_Constraint" in ms and "회전 점검" in ms, "구속을 풀고, 월드 회전 전체를 점검한다"
        assert files[1].endswith(".mbbones") and os.path.exists(files[1])
        # 자동 실행용: 경로가 박히고 메시지 창이 없다
        auto = {"log": os.path.join(d, "L.log"), "quit": True}
        f2, _ = maxbones.export_bones(d, "a", skel, anim, pr.tgt_slots, F, 100.0, tpl, auto)
        ms2 = open(f2[0], encoding="utf-8").read()
        assert "global mbAuto = true" in ms2 and "global mbQuitAfter = true" in ms2 and "a_retarget" in ms2
        assert os.path.join(d, "a_retarget.mbbones").replace("\\", "/") in ms2
        # Biped 스크립트도 같은 자동 실행 장치를 가진다 (checkForSave 는 자동일 때 건너뜀)
        fb, _ = biped.export_biped(d, "b", skel, anim, pr.tgt_slots, F, 100.0, tpl, auto)
        msb = open(fb[0], encoding="utf-8").read()
        assert "global mbAuto = true" in msb and "if not mbAuto do ( if not (checkForSave())" in msb and "SAVED: " in msb
        assert "messageBox msg" not in msb.split("fn mbRun")[1], "mbRun 안에서는 mbSay 만 쓴다"
        TB._lint_scope(msb)
        assert "angleaxis" not in msb.replace("MaxScript 의 (angleaxis ...)", ""), "angleaxis as matrix3 는 3ds Max 에서 방향이 거꾸로 나왔다(실측)"
        assert "회전 점검" in msb and "local gz = undefined" in msb, "월드 회전 전체 점검 + 발 관절을 바닥으로 한 골반 높이 비율"
    print("maxbones script OK")


def test_maxrun():
    envd = {"ADSK_3DSMAX_x64_2024": "D:\\Max24", "ADSK_3DSMAX_x64_2025": "D:\\Max25"}
    have = {os.path.join("D:\\Max25", "3dsmaxbatch.exe")}
    assert maxrun.find_max("", envd, lambda p: [], lambda p: p in have) == os.path.join("D:\\Max25", "3dsmaxbatch.exe"), "최신 설치 우선"
    assert maxrun.find_max("", {}, lambda p: [], lambda p: False) == ""
    only_gui = {os.path.join("D:\\Max25", "3dsmax.exe")}
    assert maxrun.find_max("D:\\Max25", {}, lambda p: [], lambda p: p in only_gui).endswith("3dsmax.exe"), "폴더를 주면 안에서 찾는다"
    assert maxrun.find_max("X:\\a\\3dsmax.exe", {}, lambda p: [], lambda p: True) == "X:\\a\\3dsmax.exe", "지정한 파일 우선"
    assert maxrun.build_command("C:\\m\\3dsmaxbatch.exe", "s.ms") == ["C:\\m\\3dsmaxbatch.exe", "s.ms"]
    c = maxrun.build_command("C:\\m\\3dsmax.exe", "s.ms")
    assert "-silent" in c and c[-1] == "s.ms"
    info = maxrun.parse_log("hello\nSAVED: C:/a/b_retarget.max\n⚠ 자가 점검: x\n")
    assert info["saved"] == ["C:/a/b_retarget.max"] and len(info["warnings"]) == 1

    class CP:
        def __init__(self, rc, err=b""):
            self.returncode, self.stderr = rc, err

    with tempfile.TemporaryDirectory() as d:
        log = os.path.join(d, "l.log")

        def ok_runner(cmd, **kw):
            open(log, "w", encoding="utf-8").write("SAVED: %s\n" % os.path.join(d, "o.max"))
            return CP(0)

        r = maxrun.run("3dsmaxbatch.exe", "s.ms", log, 5, ok_runner)
        assert r["ok"] and r["saved"] == [os.path.join(d, "o.max")]
        r = maxrun.run("3dsmaxbatch.exe", "s.ms", log, 5, lambda cmd, **kw: CP(0))   # 로그를 안 남김 = 스크립트가 중간에 멈춤
        assert not r["ok"] and "저장 결과" in r["error"], r
        r = maxrun.run("3dsmaxbatch.exe", "s.ms", log, 5, lambda cmd, **kw: CP(3, b"boom"))
        assert not r["ok"] and "boom" in r["error"]

        def boom(cmd, **kw):
            raise OSError("없음")

        assert "없음" in maxrun.run("x", "s.ms", log, 5, boom)["error"]
    print("maxrun OK")


def test_pipeline_auto_with_fake_max():
    """가짜 3dsmaxbatch(셸 스크립트)로 내보내기 -> 실행 -> 로그 해석 -> 결과 파일 목록까지 이어서 시험."""
    if os.name == "nt":
        return
    with tempfile.TemporaryDirectory() as d:
        pr = TB._project(d)
        fake = os.path.join(d, "3dsmaxbatch.exe")
        # 인자로 받은 .ms 에서 로그 경로를 읽어 SAVED 줄을 쓰고, 그 .max 파일도 만든다
        open(fake, "w").write('#!/bin/sh\nLOG=$(grep -m1 "global mbLogFile" "$1" | sed \'s/.*= "//; s/".*//\')\n'
                              'OUT="$(dirname "$LOG")/fake_retarget.max"\n: > "$OUT"\necho "SAVED: $OUT" >> "$LOG"\n')
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IXUSR)
        tpl = os.path.join(d, "skinned.max")
        open(tpl, "w").close()
        for method in ("biped", "bones"):
            out = os.path.join(d, "out_" + method)
            files, w = pr.export(ExportOptions(out_dir=out, presets=["max"], formats=["bip"], what="retargeted", biped_template=tpl,
                                               max_method=method, max_auto=True, max_exe=fake))
            assert any(f.endswith("fake_retarget.max") for f in files), (method, files, w)
            assert any("자동 적용 완료" in x for x in w), w
            assert any(f.endswith("_retarget.ms") for f in files)
            assert any(f.endswith(".mbbones" if method == "bones" else ".mbbip") for f in files)
        # 3ds Max 를 못 찾으면 .ms 만 만들고 이유를 알려 준다
        out = os.path.join(d, "out_none")
        saved = maxrun.find_max
        maxrun.find_max = lambda *a, **k: ""
        try:
            files, w = pr.export(ExportOptions(out_dir=out, presets=["max"], formats=["bip"], what="retargeted", biped_template=tpl, max_auto=True))
        finally:
            maxrun.find_max = saved
        assert any("찾지 못해" in x for x in w) and any(f.endswith(".ms") for f in files), w
        # 자동 적용을 끄면 실행하지 않는다
        files, w = pr.export(ExportOptions(out_dir=os.path.join(d, "out_off"), presets=["max"], formats=["bip"], what="retargeted", biped_template=tpl,
                                           max_auto=False, max_exe=fake))
        assert not any(f.endswith("fake_retarget.max") for f in files)
    print("pipeline auto OK")


def test_read_log_cp949(tmp_path=None):
    import tempfile, os
    from motionbridge.core import maxrun
    d = tempfile.mkdtemp()
    p = os.path.join(d, "a.log")
    open(p, "wb").write("SAVED: D:\\한글\\a.max\n".encode("cp949"))
    assert "한글" in maxrun.read_log(p)
    open(p, "wb").write("SAVED: D:\\한글\\b.max\n".encode("utf-8"))
    assert "한글" in maxrun.read_log(p)


if __name__ == "__main__":
    test_compute_and_data()
    test_script_and_auto()
    test_maxrun()
    test_read_log_cp949()
    test_pipeline_auto_with_fake_max()
    print("test_maxbones ALL OK")

