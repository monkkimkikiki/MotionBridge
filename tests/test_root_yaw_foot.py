"""루트 Yaw 를 돌려도 발 IK 가 몸을 따라간다: 골반 기준 발 위치 = (돌리기 전 위치) 를 같은 각도로 돌린 값. 접지 미끄러짐도 늘지 않는다."""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_pipeline as TP  # noqa: E402
from motionbridge.core import quat as Q  # noqa: E402
from motionbridge.core.model import fk  # noqa: E402
from motionbridge.core.pipeline import Project  # noqa: E402


def main():
    with tempfile.TemporaryDirectory() as d:
        src, _s2, tgt, _a, _b = TP.make_inputs(d)
        pr = Project()
        pr.load_source(src)
        pr.load_target(tgt)
        sk = pr.tgt.skeleton
        hips = pr.tgt_slots["Hips"]
        foot = pr.tgt_slots["L_Foot"]
        assert pr.settings.foot_ik

        def world():
            res = pr.run()
            _wr, wp = fk(sk, res.anim.rot, res.anim.pos)
            return Q.rotate(res.frame_tgt[0], wp)

        P0 = world()
        for yaw in (40.0, 90.0, -70.0):
            pr.settings.root_yaw_deg = yaw
            P1 = world()
            R = Q.from_axis_angle(np.array([0, 1.0, 0]), np.radians(yaw))
            leg = float(np.linalg.norm(P0[0, foot] - P0[0, hips]))
            err = max(float(np.linalg.norm(Q.rotate(R, P0[f, foot] - P0[f, hips]) - (P1[f, foot] - P1[f, hips]))) for f in range(P0.shape[0]))
            assert err < 0.01 * leg, "Yaw %g°: 골반 기준 발 위치가 몸과 같이 돌지 않음 (오차 %.3f / 다리 %.1f)" % (yaw, err, leg)
        print("test_root_yaw_foot ALL OK")


main()
