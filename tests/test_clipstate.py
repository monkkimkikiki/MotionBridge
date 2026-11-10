"""클립(모션)마다 리타겟 설정을 따로 갖고, 다른 모션의 세팅을 필요한 부분만 가져왔다가 뺄 수 있다."""
import copy
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge.core.pipeline import Project  # noqa: E402
import test_pipeline as TP  # noqa: E402


def project(d, n=3):
    src, _s2, tgt, _a, _b = TP.make_inputs(d)
    pr = Project()
    pr.load_source(src)
    pr.load_target(tgt)
    for i in range(1, n):
        a = copy.deepcopy(pr.src.animations[0])
        a.name = "clip%d" % i
        pr.src.animations.append(a)
    return pr


def test_per_clip_settings():
    with tempfile.TemporaryDirectory() as d:
        pr = project(d)
        pr.settings.root_yaw_deg = 30.0
        pr.clip = 1                                   # 처음 여는 클립은 그때 설정을 복사해서 시작 (예전과 같은 값)
        assert pr.settings.root_yaw_deg == 30.0 and pr.settings is not pr.settings_of(0)
        pr.settings.root_yaw_deg = 90.0               # 이 클립만 바뀜
        pr.clip = 0
        assert pr.settings.root_yaw_deg == 30.0
        pr.clip = 1
        assert pr.settings.root_yaw_deg == 90.0
        assert pr.settings_of(2) is None
        # 결과가 클립별 설정을 따른다
        r1 = pr.run(1, analyze_quality=False)
        r0 = pr.run(0, analyze_quality=False)
        assert not np.allclose(r1.anim.rot, r0.anim.rot), "클립마다 다른 설정이 결과에 반영"
        # IK 구간은 클립 번호별로 한곳에 모인다
        pr.settings.ik_ranges[1] = {"왼쪽 다리": [[1, 3, True]]}
        pr.clip = 0
        assert pr.settings.ik_ranges.get(1)
        pr.clip = 2
        pr.settings.ik_ranges[2] = {"왼쪽 다리": [[0, 2, False]]}
        pr.clip = 1
        assert pr.settings.ik_ranges[2] and pr.settings.ik_ranges[1]
        # 새 소스를 열면 클립별 설정도 비워진다
        pr.unload_source()
        assert pr._stash == {} and pr.clip == 0
    print("per clip settings OK")


def test_import_and_undo():
    with tempfile.TemporaryDirectory() as d:
        pr = project(d)
        hip = pr.tgt_slots["Hips"]
        # 클립 0: 설정, 본별 보정, 레이어, IK 구간을 꾸민다
        pr.settings.root_yaw_deg = 40.0
        pr.settings.rot_offsets[hip] = (0.0, 10.0, 0.0)
        lay = pr.layer_stack().add()
        lay.set_key(hip, 2, pos=np.array([0.0, 3.0, 0.0]))
        pr.settings.ik_ranges[0] = {"왼쪽 다리": [[1, 4, True]]}
        pr.clip = 1
        pr.settings.root_yaw_deg = 5.0
        pr.settings.rot_offsets.clear()
        pr.layers.pop(1, None)
        pr.settings.ik_ranges.pop(1, None)
        base1 = copy.deepcopy(pr.settings)
        # 일부만 가져오기: 기본 설정만
        note = pr.import_clip_state(0, ["settings"])
        assert "기본 설정" in note and pr.settings.root_yaw_deg == 40.0
        assert not pr.settings.rot_offsets and not pr.layers.get(1), "고르지 않은 항목은 그대로"
        # 이어서 나머지도
        pr.import_clip_state(0, ["offsets", "layers", "ik"])
        assert pr.settings.rot_offsets[hip] == (0.0, 10.0, 0.0)
        assert pr.layer_stack().total_keys() == 1 and pr.layer_stack() is not pr.layer_stack(0), "레이어는 복사본"
        assert pr.settings.ik_ranges[1] == {"왼쪽 다리": [[1, 4, True]]}
        assert len(pr.import_history()) == 2
        # 원래 클립 0 은 그대로
        assert pr.settings_of(0).root_yaw_deg == 40.0 and pr.layer_stack(0).total_keys() == 1
        # 빼기: 가져온 순서의 반대로 하나씩 되돌아간다
        n2 = pr.undo_import()
        assert "본별 보정" in n2 and not pr.settings.rot_offsets and not pr.layers.get(1) and not pr.settings.ik_ranges.get(1)
        assert pr.settings.root_yaw_deg == 40.0
        n1 = pr.undo_import()
        assert "기본 설정" in n1 and pr.settings.root_yaw_deg == 5.0
        assert pr.settings.fx_smooth == base1.fx_smooth
        assert pr.undo_import() is None
        # 결과가 실제로 가져온 설정을 따른다
        pr.import_clip_state(0, ["settings", "offsets"])
        ra = pr.run(1, analyze_quality=False).anim.rot.copy()
        rb = pr.run(0, analyze_quality=False)
        base = rb.base_anim if rb.base_anim is not None else rb.anim
        assert np.allclose(ra, base.rot, atol=1e-6), "같은 설정을 가져오면 같은 리타겟 결과"
        # 잘못된 사용
        pr.clip = 1
        for bad in ((1, ["settings"]), (0, [])):
            try:
                pr.import_clip_state(*bad)
                raise AssertionError("오류가 나야 함")
            except ValueError:
                pass
        # 타깃을 바꾸면 본 번호가 달라지므로 보정은 비워지고 되돌리기 기록도 비워진다
        pr.load_target(TP.make_inputs(d)[2])
        assert not pr.settings.rot_offsets and not pr.import_history() and not pr.settings_of(0).rot_offsets
    print("import / undo OK")


def test_touched():
    with tempfile.TemporaryDirectory() as d:
        pr = project(d)
        assert not pr.clip_touched(0) and not pr.clip_touched(1)
        pr.settings.rot_offsets[pr.tgt_slots["Hips"]] = (0, 5.0, 0)
        assert pr.clip_touched(0) and not pr.clip_touched(1)
        pr.clip = 2
        assert pr.clip_touched(0), "다른 클립을 보고 있어도 보정 표시가 남음"
    print("touched OK")


if __name__ == "__main__":
    test_per_clip_settings()
    test_import_and_undo()
    test_touched()
