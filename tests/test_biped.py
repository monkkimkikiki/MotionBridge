"""3ds Max Biped 내보내기: 데이터 계산(좌표/델타/방향)과 파일 형식 시험. MaxScript 자체는 3ds Max 가 있어야 실행할 수 있어 여기서는 실행하지 않는다."""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge.core import biped, quat as Q  # noqa: E402
from motionbridge.core.export import PRESETS, preset_frame  # noqa: E402
from motionbridge.core.model import fk  # noqa: E402
from motionbridge.core.pipeline import ExportOptions, Project  # noqa: E402
import test_pipeline as TP  # noqa: E402


def _project(d):
    src, src2, tgt, _, _ = TP.make_inputs(d)
    pr = Project()
    pr.load_source(src)
    pr.load_target(tgt)
    return pr


def test_parts():
    pr = _project(tempfile.mkdtemp())
    parts, info = biped.build_parts(pr.tgt_slots)
    keys = [(p["slot"], p["arr"], p["link"]) for p in parts]
    assert keys[0] == ("Hips", "pelvis", 1)
    d = {p["slot"]: p for p in parts}
    assert d["L_UpperArm"]["arr"] == "larm" and d["L_UpperArm"]["link"] == 2 and d["R_Hand"]["link"] == 4
    assert d["L_UpLeg"]["arr"] == "lleg" and d["L_Foot"]["link"] == 3
    assert info["spine_links"] >= 1
    # 부모가 항상 앞: 방향 기준 자식은 뒤에 온다
    for i, p in enumerate(parts):
        assert p["aim"] == -1 or p["aim"] > i, p
    try:
        biped.build_parts({"Spine": 1})
    except ValueError as e:
        assert "Hips" in str(e)
    else:
        raise AssertionError
    print("biped parts OK")


def test_compute_and_files():
    with tempfile.TemporaryDirectory() as d:
        pr = _project(d)
        res = pr.run()
        skel, anim, F = pr.tgt.skeleton, res.anim, res.frame_tgt[0]
        slots = pr.tgt_slots
        C = Q.mul(preset_frame(PRESETS["max"]), F)
        data = biped.compute(skel, anim, slots, F, 100.0)
        parts, T = data["parts"], anim.n_frames
        assert data["deltas"].shape == (T, len(parts), 4) and data["com"].shape == (T, 3)
        # 델타를 바인드 월드 회전에 곱하면 3ds Max 프레임의 실제 월드 회전과 같아야 한다
        wr, wp = fk(skel, anim.rot, anim.pos)
        bwr, bwp = skel.bind_world()
        for i in (0, len(parts) // 2, len(parts) - 1):
            j = slots[parts[i]["slot"]]
            t = T // 2
            ax, ang = data["deltas"][t, i, :3], np.radians(data["deltas"][t, i, 3])
            dq = Q.from_axis_angle(ax, ang)
            got = Q.mul(dq, Q.mul(C, bwr[j]))
            want = Q.mul(C, wr[t, j])
            assert abs(abs(np.sum(got * want)) - 1.0) < 1e-6, (parts[i]["slot"], got, want)
        # 바인드 포즈 그대로면 델타 0, 이동 0
        zero = type(anim)(anim.name, anim.fps, np.tile(skel.rest_rot, (2, 1, 1)), np.tile(skel.offsets, (2, 1, 1)))
        z = biped.compute(skel, zero, slots, F, 100.0)
        assert np.abs(z["deltas"][..., 3]).max() < 1e-3 and np.abs(z["com"]).max() < 1e-9
        # 방향(aim): 3ds Max 프레임에서 정규화된 벡터. 왼쪽 위팔 -> 아래팔 은 월드에서 팔 방향과 일치
        i = [p["slot"] for p in parts].index("L_UpperArm")
        k = parts[i]["aim"]
        v = Q.rotate(C, bwp[slots[parts[k]["slot"]]] - bwp[slots["L_UpperArm"]])
        assert np.allclose(data["dirs"][i], v / np.linalg.norm(v), atol=1e-6)
        # 키 높이: 센티미터 모델 -> 미터 (합성 모델은 약 1.15 배 키)
        assert 1.2 < data["height_m"] < 2.4 and 0.6 < data["hip_height_m"] < 1.4, (data["height_m"], data["hip_height_m"])
        # 3ds Max 프레임: 캐릭터는 Z-up. 머리가 골반보다 높아야 한다.
        head = Q.rotate(C, bwp[slots["Head"]])[2]
        hips = Q.rotate(C, bwp[slots["Hips"]])[2]
        assert head > hips

        out = os.path.join(d, "o")
        files, warns = pr.export(ExportOptions(out_dir=out, presets=["max"], formats=["bip"], what="retargeted"))
        names = sorted(os.path.basename(f) for f in files)
        assert len(names) == 2 and names[0].endswith("_biped.mbbip") and names[1].endswith("_biped.ms"), names
        ms = open([f for f in files if f.endswith(".ms")][0], encoding="utf-8").read()
        dat = [f for f in files if f.endswith(".mbbip")][0]
        assert os.path.basename(dat) in ms and "__DATA__" not in ms
        assert ms.count("(") == ms.count(")"), "괄호 짝이 맞아야 함"
        assert "biped.createNew" in ms and "biped.setTransform" in ms and "biped.saveBipFile" in ms
        lines = open(dat, encoding="utf-8").read().split("\n")
        assert lines[0] == biped.FORMAT
        hdr = {l.split()[0]: l.split()[1:] for l in lines[1:] if l and l.split()[0] != "P" and l != "DATA"}
        assert int(hdr["frames"][0]) == T and int(hdr["parts"][0]) == len(parts)
        ip = [i for i, l in enumerate(lines) if l == "DATA"][0]
        plines = [l for l in lines[:ip] if l.startswith("P ")]
        assert len(plines) == len(parts) and all(len(l.split()) == 8 for l in plines)
        frames = [l for l in lines[ip + 1:] if l]
        assert len(frames) == T and all(len(l.split()) == 3 + 4 * len(parts) for l in frames)
        assert open([f for f in files if f.endswith(".ms")][0], "rb").read().count(b"\r\n") > 50, "윈도우 줄바꿈"
        # 소스 모션 그대로도 가능
        files2, _ = pr.export(ExportOptions(out_dir=out, presets=["max"], formats=["bip"], what="source"))
        assert any(f.endswith(".ms") for f in files2)
        # 매핑이 부족하면 경고만 하고 계속
        pr.tgt_slots.pop("L_UpperArm")
        _, w = pr.export(ExportOptions(out_dir=out, presets=["max"], formats=["bip"], what="retargeted"))
        assert any("L_UpperArm" in x for x in w), w
        pr.tgt_slots.pop("Hips")
        _, w = pr.export(ExportOptions(out_dir=out, presets=["max"], formats=["bip"], what="retargeted"))
        assert any("Hips" in x for x in w), w
    print("biped files OK")


if __name__ == "__main__":
    test_parts()
    test_compute_and_files()
    print("test_biped ALL OK")
