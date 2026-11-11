"""손가락 위치 기준 자동 매핑: 이름을 지우고(bone_N), 자식 순서를 섞고, 손을 아무 방향으로 돌리고, 좌우를 뒤집고, 손바닥 뼈/꼬리 뼈가 붙은 변형을 만들어도
엄지~새끼 × 1~3마디가 맞게 나와야 한다. 이름이 있으면 이름 쪽이 이기고(건드리지 않음), 발톱/촉수만 있는 손은 손가락으로 보지 않는다."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from motionbridge.core import fingermap, mapping  # noqa: E402
from motionbridge.core.model import Skeleton  # noqa: E402

FING = ["Thumb", "Index", "Middle", "Ring", "Pinky"]


def rot_random(rng):
    q = rng.normal(size=4)
    q /= np.linalg.norm(q)
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def build_hand(rng, meta=False, tail=False, mirror=False, shuffle=True, rename=True, jitter=0.05, rotate=True, thumb_meta=False):
    """손(원점) 아래에 손가락 5개. 월드 위치로 만들고 부모 기준 offset 으로 바꾼다. 정답: 이름(Thumb1..) -> 관절 번호."""
    pos = [np.zeros(3)]
    parents = [-1]
    truth = {}
    sgn = -1.0 if mirror else 1.0

    def add(p, parent):
        pos.append(np.asarray(p, float) * (1 + rng.normal(scale=jitter)))
        parents.append(parent)
        return len(pos) - 1

    specs = []     # (이름, 시작 위치, 방향, 마디 길이들)
    lat = {"Index": -2.4, "Middle": -0.8, "Ring": 0.8, "Pinky": 2.4}
    for f, z in lat.items():
        specs.append((f, np.array([2.0 if meta else 7.0, 0.0, sgn * z]), np.array([1.0, -0.15, 0.0]), [4.2, 2.8, 2.2] if f != "Pinky" else [3.2, 2.0, 1.8]))
    specs.append(("Thumb", np.array([2.5, -0.6, sgn * -4.0]), np.array([0.6, -0.4, -0.7 * sgn]), [3.6, 2.6, 2.0]))
    order = list(range(len(specs)))
    if shuffle:
        rng.shuffle(order)
    for si in order:
        f, start, dirv, segs = specs[si]
        dirv = dirv / np.linalg.norm(dirv)
        parent = 0
        cur = start.copy()
        use_meta = meta and (f != "Thumb" or thumb_meta)
        if use_meta:
            parent = add(start * 0.0 + np.array([0.0, 0.0, start[2] * 0.5]), 0)
            cur = start
        bones = []
        for k, ln in enumerate(segs):
            j = add(cur, parent)
            bones.append(j)
            parent = j
            cur = cur + dirv * ln
        if tail:
            parent = add(cur, parent)
        for m, j in enumerate(bones, 1):
            truth["%s%d" % (f, m)] = j
    P = np.array(pos)
    if rotate:
        P = P @ rot_random(rng).T
    offs = np.zeros_like(P)
    for j, pa in enumerate(parents):
        offs[j] = P[j] - (P[pa] if pa >= 0 else 0)
    names = ["bone_%d" % i for i in range(len(P))] if rename else ["j%d" % i for i in range(len(P))]
    return Skeleton(names, np.array(parents), offs), truth


def main():
    rng = np.random.default_rng(7)
    tot = ok = 0
    for trial in range(120):
        meta = bool(trial % 2)
        tail = bool((trial // 2) % 2)
        mirror = bool((trial // 4) % 2)
        thumb_meta = meta and trial % 7 == 0
        sk, truth = build_hand(rng, meta=meta, tail=tail, mirror=mirror, thumb_meta=thumb_meta)
        got = fingermap.map_hand(sk, 0)
        good = sum(1 for k, v in truth.items() if got.get(k) == v)
        tot += len(truth)
        ok += good
        assert good == len(truth), ("trial", trial, dict(meta=meta, tail=tail, mirror=mirror, thumb_meta=thumb_meta), sorted(k for k, v in truth.items() if got.get(k) != v))
    assert ok == tot
    # 이름으로 이미 손가락이 정해진 손은 건드리지 않는다
    sk, truth = build_hand(rng, rename=False)
    slots = {"L_Hand": 0, "L_Index1": 99}
    assert fingermap.fill_fingers(sk, slots) == {} and slots["L_Index1"] == 99
    # 빈 손은 채운다 (auto_map 이 호출하는 경로)
    slots = {"L_Hand": 0}
    added = fingermap.fill_fingers(sk, slots)
    assert len(added) == 15 and all(k.startswith("L_") for k in added), len(added)
    # 발톱/촉수 2개뿐인 손은 손가락으로 보지 않는다
    sk2, _t = build_hand(rng, rename=True)
    claws = Skeleton(["hand", "c1a", "c1b", "c2a", "c2b"], np.array([-1, 0, 1, 0, 3]), np.array([[0, 0, 0], [1, 0, 1], [1, 0, 0], [1, 0, -1], [1, 0, 0]], float))
    assert fingermap.map_hand(claws, 0) == {}
    # 발가락 이름에 손가락 낱말이 들어 있어도(MiddleToe1) 손가락 슬롯으로 잡지 않는다
    assert mapping.classify_name("MiddleToe1_l") is None and mapping.classify_name("middle_01_l")[1] == "Finger:Middle"
    print("test_fingermap ALL OK (%d/%d)" % (ok, tot))


main()
