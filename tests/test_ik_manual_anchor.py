"""IK 구간을 직접 켰을 때 발이 '구간 평균(발이 있던 적 없는 자리)'이 아니라 켠 지점에 고정되는지."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
from motionbridge.core.model import fk  # noqa: E402
from motionbridge.core.retarget import RetargetSettings, retarget, default_chains  # noqa: E402
from motionbridge.core.mapping import auto_map, pairs_from_slots  # noqa: E402
from motionbridge.core import quat as Q  # noqa: E402
from test_retarget import COMBOS, IK_TGT, synth  # noqa: E402


def foot_path(manual):
    sc = COMBOS[0][0]
    ssk, smeta = synth.build_skeleton(**sc)
    tsk, tmeta = synth.build_skeleton(**IK_TGT)
    an, _ = synth.walk_animation(ssk, smeta, n_frames=60)
    sm, tm = auto_map(ssk), auto_map(tsk)
    res = retarget(ssk, an, tsk, pairs_from_slots(sm, tm), RetargetSettings(foot_ik=True, hand_ik=False), default_chains(sm, tm), sm, tm, manual_ik=manual)
    ch = [c for c in res.chains if c.kind == "foot"][0]
    Ft = res.frame_tgt[0]
    _wr, wp = fk(tsk, res.anim.rot, res.anim.pos)
    return Q.rotate(Ft, wp)[:, ch.foot], ch.label, res


def main():
    base, label, _ = foot_path(None)
    # 걷는 중(발이 앞으로 움직이는 구간)을 직접 켠다: 끝 프레임 가까이에서 발이 켠 첫 프레임 자리에 머물러야 한다
    a, b = 19, 29           # 자동 접지(0~17, 31~)가 아닌, 발이 공중에서 움직이는 구간
    free_travel = np.linalg.norm(base[b] - base[a])
    assert free_travel > 1e-3, "시험 전제: 이 구간에서 발이 움직임 %.4f" % free_travel
    fixed, _l, res = foot_path({label: [[a, b, True]]})
    pl = [p for p in res.plans if p.label == label][0]
    full = np.flatnonzero((pl.weight > 0.999) & (np.arange(len(pl.weight)) >= a) & (np.arange(len(pl.weight)) <= b))
    assert len(full) >= 2, full
    d_first = np.linalg.norm(pl.target[full] - base[a], axis=1).max()
    d_mean = np.linalg.norm(pl.target[full] - base[a:b + 1].mean(axis=0), axis=1).min()
    assert d_first < 1e-6, "IK 목표 = 켠 첫 프레임에 발이 있던 자리: %.6f" % d_first
    assert d_mean > 1.0, "구간 평균(발이 있던 적 없는 중간 지점)이 아님: %.3f" % d_mean
    ok = [f for f in full if not pl.fail[f]]            # 몸이 멀어져 다리가 안 닿는 프레임은 뺀다
    spread = np.linalg.norm(fixed[ok] - base[a], axis=1).max()
    assert ok and spread < 1e-3, "닿는 프레임에서는 발이 실제로 그 자리에: %.5f" % spread
    # 원래부터 접지인 구간을 켜면 예전처럼 평균(떨림 제거) — 결과가 크게 안 변함
    print("test_ik_manual_anchor ALL OK (움직임 %.3f, 고정 흔들림 %.5f)" % (free_travel, spread))


main()
