"""같은 스켈레톤(사람 모양이 아닌 몬스터 등) 알아보기와 '본 이름대로 그대로 적용'."""
import os
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from motionbridge.core import quat as Q  # noqa: E402
from motionbridge.core.mapping import auto_map, frame_from_axes  # noqa: E402
from motionbridge.core.model import Animation, Skeleton, fk  # noqa: E402
from motionbridge.core.pipeline import Project  # noqa: E402
from motionbridge.core.retarget import RetargetSettings, retarget  # noqa: E402
from motionbridge.core.rigmatch import explain_rig, match_rig  # noqa: E402

fails = []


def check(c, m):
    if not c:
        fails.append(m)
        print("FAIL", m)


rng = np.random.default_rng(7)
PAR = [-1, 0, 1, 2, 1, 4, 5, 1, 7, 8, 0, 10, 11, 0, 13, 14, 14, 16]       # 나무 모양: 가지가 많은 몬스터 (사람 본 이름 없음)
N = len(PAR)


def monster(names_fmt="Limb_%d"):
    offs = rng.normal(size=(N, 3)) * 30.0                  # 언리얼 센티미터 단위
    rest = Q.normalize(Q.from_axis_angle(rng.normal(size=(N, 3)), rng.uniform(-0.6, 0.6, size=N)))
    offs[0] = [0.0, 0.0, 90.0]
    return Skeleton([names_fmt % i for i in range(N)], PAR, offs, rest, "monster")


def convert(sk, M, s, names_fmt, local_axes_rotated):
    """같은 리그를 다른 좌표축(M)/단위(s)로 옮긴 타깃. local_axes_rotated: 본 로컬 축도 M 으로 돌아간 변환(glTF 내보내기식)인지."""
    wr, wp = sk.bind_world()
    Wt = Q.mul(M, Q.mul(wr, Q.conj(M))) if local_axes_rotated else Q.mul(M, wr)
    Pt = s * Q.rotate(M, wp)
    rot = np.empty_like(Wt)
    off = np.empty_like(Pt)
    for j in range(sk.n):
        p = sk.parents[j]
        rot[j] = Wt[j] if p < 0 else Q.mul(Q.conj(Wt[p]), Wt[j])
        off[j] = Pt[j] if p < 0 else Q.rotate(Q.conj(Wt[p]), Pt[j] - Pt[p])
    return Skeleton([names_fmt % i for i in range(N)], sk.parents.copy(), off, rot, "monster_glb")


def anim_for(sk, T=24):
    rot = np.repeat(sk.rest_rot[None], T, axis=0)
    for t in range(T):
        rot[t] = Q.mul(Q.from_axis_angle(rng.normal(size=(N, 3)), rng.uniform(-0.5, 0.5, size=N)), rot[t])
    pos = np.repeat(sk.offsets[None], T, axis=0)
    pos[:, 0] += np.stack([np.linspace(0, 120, T), np.linspace(0, -40, T), 15 * np.sin(np.linspace(0, 6, T))], axis=1)   # 루트 이동
    return Animation("attack", 30.0, rot, pos)


M = Q.mul(Q.from_axis_angle(np.array([1.0, 0, 0]), -np.pi / 2), Q.from_axis_angle(np.array([0, 0, 1.0]), np.pi / 2))   # Z-up/X-앞 -> Y-up/Z-앞
S = 0.01
src = monster()
check(auto_map(src) is not None and "L_UpLeg" not in auto_map(src), "시험용 몬스터는 사람 본 매핑이 안 됨: %s" % list(auto_map(src))[:6])

for rotated in (False, True):
    tgt = convert(src, M, S, "limb.%d", rotated)             # 이름 표기도 다르게 (Limb_3 / limb.3)
    mt = match_rig(src, tgt)
    check(mt is not None and len(mt.pairs) == N, "같은 리그 알아봄(로컬 축 %s)" % rotated)
    if mt is None:
        continue
    check(abs(mt.scale - S) < 1e-6 and mt.rel_rms < 1e-6, "스케일/오차: %s %s" % (mt.scale, mt.rel_rms))
    check(np.allclose(Q.rotate(mt.q, np.array([1.0, 2, 3])), Q.rotate(M, np.array([1.0, 2, 3])), atol=1e-6), "좌표축 회전을 맞게 구함")
    an = anim_for(src)
    st = RetargetSettings(start_at_origin=False, foot_ik=False)
    res = retarget(src, an, tgt, [], st, [], {}, {}, rig=mt)
    wr_t, wp_t = fk(tgt, res.anim.rot, res.anim.pos)
    _, wp_s = fk(src, an.rot, an.pos)
    want = S * Q.rotate(M, wp_s)
    err = np.abs(wp_t - want).max()
    check(err < 1e-6, "모든 본 위치가 원본 모션과 같다(로컬 축 %s): 최대 오차 %.2e" % (rotated, err))
    check(res.k > 0 and not res.ik_applied and len(res.pairs) == N, "IK 없이 이름 쌍으로 적용")
    check(any("같은 스켈레톤" in w for w in res.warnings), "화면에 같은 스켈레톤임을 알림")

# 사람 매핑으로 그냥 돌리면 어떻게 되는지 (참고): 매핑된 본이 거의 없어서 못 돌린다
try:
    retarget(src, anim_for(src), convert(src, M, S, "Limb_%d", False), [], RetargetSettings(), [], auto_map(src), {})
    check(False, "매핑 없이 일반 리타깃은 오류여야 함")
except ValueError:
    pass

# 다른 리그는 같은 리그로 보지 않는다
other = monster()                                           # 이름은 같지만 구조/모양이 다름
check(match_rig(src, other) is None, "이름만 같고 모양이 다르면 아님")
_m, _note = explain_rig(src, other)
check(_m is None and "모양이 맞지 않" in _note, "이유를 알려 줌: %s" % _note)
parents2 = list(PAR)
parents2[5] = 1
sk2 = Skeleton(src.names, parents2, src.offsets.copy(), src.rest_rot.copy(), "x")
check(match_rig(src, sk2) is None, "계층이 다르면 아님")
sk3 = Skeleton(["Other_%d" % i for i in range(N)], PAR, src.offsets.copy(), src.rest_rot.copy(), "x")
check(match_rig(src, sk3) is None, "이름이 안 맞으면 아님")
sk4 = Skeleton(src.names[:6], PAR[:6], src.offsets[:6].copy(), src.rest_rot[:6].copy(), "x")
check(match_rig(src, sk4) is None, "본 수가 많이 다르면 아님 (일부만 같음)")

# 프로젝트: 사람 모양이 아니면 자동으로 쓰고, 설정에서 끌 수 있다. 쌍이 없어도 준비됨
pr = Project()
tgt = convert(src, M, S, "limb.%d", True)
pr.src = SimpleNamespace(skeleton=src, animations=[anim_for(src)], path="a.psa", notes=[])
pr.tgt = SimpleNamespace(skeleton=tgt, animations=[], path="m.glb", notes=[], meshes=[])
pr.src_slots, pr.tgt_slots = auto_map(src), auto_map(tgt)
check(pr.rig() is not None and len(pr.pairs()) == N and pr.chains() == [] and pr.ready(), "프로젝트가 같은 리그를 자동으로 씀")
res = pr.run(0, analyze_quality=False)
check(res is not None and len(res.pairs) == N, "프로젝트 실행")
pr.settings.same_rig = False
check(pr.rig() is None and pr.pairs() == [], "설정에서 끌 수 있음")
pr.tgt = SimpleNamespace(skeleton=other, animations=[], path="m.glb", notes=[], meshes=[])
pr.settings.same_rig = True
check(pr.rig() is None and "모양이 맞지 않" in pr.rig_note, "프로젝트가 못 알아본 이유를 들고 있음")
pr.tgt = SimpleNamespace(skeleton=tgt, animations=[], path="m.glb", notes=[], meshes=[])
check(pr.rig() is not None and pr.rig_note == "", "다시 맞으면 이유 지움")

# 사람 모양이면(양쪽 골반·다리·팔이 잡힘) 기존 사람 리타깃을 그대로 쓴다
pr.settings.same_rig = True
hum = {k: 1 for k in ("Hips", "L_UpLeg", "R_UpLeg", "L_Foot", "R_Foot", "L_UpperArm", "R_UpperArm")}
pr.src_slots, pr.tgt_slots = dict(hum), dict(hum)
check(pr.rig() is None, "사람 모양은 같은 리그 모드를 쓰지 않음")

print("모두 통과" if not fails else "실패 %d개" % len(fails))
sys.exit(1 if fails else 0)
