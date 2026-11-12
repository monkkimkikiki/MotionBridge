"""손가락 자동 매핑 (위치 기준): 이름에서 손가락을 못 찾는 리그(Finger11_l 처럼 번호만 있거나 이름이 제각각인 것)에서
'손 아래 뼈 사슬의 바인드 포즈 위치'만 보고 엄지~새끼 × 1~3마디를 정한다 (Qt 없음).

순서: ① 손 아래에서 손가락 사슬(갈래 하나 = 사슬 하나)을 찾고 ② 손끝 방향에 수직인 가로 축으로 늘어놓은 뒤
③ 양 끝 중 '손목에 더 가깝고 짧게 뻗은' 쪽을 엄지로 보고 ④ 다섯 개가 안 되면 가로 위치가 가장 가까운 손가락 이름에 짝지어 준다.
이름으로 이미 정해진 슬롯은 건드리지 않는다 (빈 슬롯만 채움)."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from .model import Skeleton

FINGERS = ["Thumb", "Index", "Middle", "Ring", "Pinky"]
_CANON = np.array([0.0, 0.25, 0.5, 0.75, 1.0])      # 가로 축에서 엄지(0) -> 새끼(1) 의 대략적 위치
_SKIP_WORDS = ("weapon", "prop", "socket", "ik", "twist", "helper", "roll", "armor", "wrist", "inner", "outer", "attach", "cup", "palm")


def _children(skel: Skeleton) -> List[List[int]]:
    ch: List[List[int]] = [[] for _ in range(skel.n)]
    for j in range(skel.n):
        p = int(skel.parents[j])
        if p >= 0:
            ch[p].append(j)
    return ch


def _longest_path(children, start: int) -> List[int]:
    best: List[int] = []

    def go(n: int, path: List[int]) -> None:
        nonlocal best
        path = path + [n]
        if not children[n]:
            if len(path) > len(best):
                best = path
            return
        for c in children[n]:
            go(c, path)

    go(start, [])
    return best


def finger_chains(skel: Skeleton, hand: int) -> List[List[int]]:
    """손 아래의 손가락 사슬 목록 (손에 가까운 뼈 -> 끝). 소품·무기처럼 한 마디짜리 갈래는 뺀다."""
    children = _children(skel)
    out: List[List[int]] = []

    def walk(n: int) -> None:
        path = [n]
        while len(children[path[-1]]) == 1:
            path.append(children[path[-1]][0])
        end = path[-1]
        kids = children[end]
        if not kids:
            out.append(path)
        elif len(path) >= 2:                              # 끝이 갈라지는 손가락 (겹친 보조 뼈): 가장 긴 가닥만 쓴다
            out.append(path + _longest_path(children, kids[0])[1:] if len(kids) == 1 else path[:-1] + _longest_path(children, end))
        else:                                             # 손바닥 뼈(Cup 등): 갈래마다 손가락 하나
            for k in kids:
                walk(k)

    for c in children[hand]:
        walk(c)
    return [p for p in out if len(p) >= 2 and not any(w in skel.names[p[0]].lower() for w in _SKIP_WORDS if w not in ("cup", "palm", "wrist"))
            and "weapon" not in skel.names[p[0]].lower() and "prop" not in skel.names[p[0]].lower()]


def _geometry(skel: Skeleton, hand: int, chains: List[List[int]]):
    _wr, wp = skel.bind_world()
    base = wp[hand]
    tips = np.array([wp[c[-1]] - base for c in chains])
    roots = np.array([wp[c[0]] - base for c in chains])
    d = tips.mean(axis=0)
    if np.linalg.norm(d) < 1e-9:
        d = roots.mean(axis=0)
    d = d / max(np.linalg.norm(d), 1e-12)                       # 손끝 방향
    rel = tips - np.outer(tips @ d, d)
    if len(chains) >= 2 and np.linalg.norm(rel) > 1e-9:
        u, s, vt = np.linalg.svd(rel - rel.mean(axis=0), full_matrices=False)
        lat = vt[0]
    else:
        lat = np.cross(d, np.array([0.0, 1.0, 0.0]))
    lat = lat / max(np.linalg.norm(lat), 1e-12)
    return d, lat, roots, tips


def order_chains(skel: Skeleton, hand: int, chains: List[List[int]]) -> Optional[Tuple[List[int], np.ndarray]]:
    """사슬을 엄지 쪽부터 새끼 쪽으로 늘어놓은 순서(인덱스)와 각각의 가로 위치(엄지 0 ~ 새끼 1). 2개 미만이면 None.
    ① 엄지 = 손목에 가장 가까운 곳에서 시작하고 손끝 쪽으로 가장 덜 뻗는 사슬  ② 나머지 사슬은 가로 축(그들끼리의 주성분)으로 엄지 쪽부터 줄 세운다."""
    n = len(chains)
    if n < 2:
        return None
    d, _lat, _roots, tips = _geometry(skel, hand, chains)
    _wr, wp = skel.bind_world()
    prox = np.array([wp[c[max(len(c) - 3, 0)]] - wp[hand] for c in chains])     # 첫 마디(손바닥 뼈가 있어도 같은 자리를 비교하도록 '뒤에서 세 번째' 뼈)
    fr, ft = prox @ d, tips @ d
    scale = max(float(np.mean(ft)), 1e-12)
    seglen = np.array([sum(float(np.linalg.norm(wp[c[k + 1]] - wp[c[k]])) for k in range(max(len(c) - 4, 0), len(c) - 1)) for c in chains])
    dirs = []
    for i, c in enumerate(chains):
        v = wp[c[-1]] - wp[c[max(len(c) - 3, 0)]]
        dirs.append(v / max(float(np.linalg.norm(v)), 1e-12))
    dev = np.array([1.0 - float(np.dot(v, d)) for v in dirs])                  # 손끝 방향에서 얼마나 벌어졌나 (엄지가 가장 크다)

    def thumb_score(i: int) -> float:
        # 엄지 = 다른 손가락보다 (1) 사슬이 짧고 (2) 손목에 가까운 곳에서 시작하고 (3) 손끝 쪽으로 덜 뻗고 (4) 벌어져 있다.
        # 가중치는 실제 게임 리그 7종(14손)과 합성 손 120개에서 엄지를 가장 잘 찾는 값으로 골랐다 (README '손가락 자동 매핑' 참고).
        o = [k for k in range(n) if k != i]
        short = (float(np.median(seglen[o])) - seglen[i]) / max(float(np.median(seglen[o])), 1e-12)
        near = float(np.mean(fr[o]) - fr[i]) / scale
        reach = float(np.mean(ft[o]) - ft[i]) / scale
        splay = (dev[i] - float(np.mean(dev[o]))) / max(float(np.std(dev)), 0.05)
        return 3.0 * short + 1.0 * near + 1.0 * reach + 0.5 * splay

    th = int(np.argmax([thumb_score(i) for i in range(n)]))
    rest = [i for i in range(n) if i != th]
    mid = np.array([np.mean([wp[j] - wp[hand] for j in (c[:-1] if len(c) >= 4 else c)], axis=0) for c in chains])     # 끝 뼈는 서로 겹치는 리그가 있어 뺀다
    pts = mid[rest] - np.outer(mid[rest] @ d, d)
    if len(rest) >= 2 and np.linalg.norm(pts - pts.mean(axis=0)) > 1e-9:
        _u, _s, vt = np.linalg.svd(pts - pts.mean(axis=0), full_matrices=False)
        lat = vt[0]
    else:
        lat = (mid[th] - mid[rest[0]]) - ((mid[th] - mid[rest[0]]) @ d) * d
    lat = lat / max(float(np.linalg.norm(lat)), 1e-12)
    if float((mid[th] - mid[rest].mean(axis=0)) @ lat) > 0:          # 엄지 쪽이 음수가 되도록
        lat = -lat
    u = mid[rest] @ lat
    idx = [rest[k] for k in np.argsort(u)]
    us = np.sort(u)
    rng = max(float(us[-1] - us[0]), 1e-12)
    t = [0.0] + [0.25 + 0.75 * float((x - us[0]) / rng) for x in us]
    return [th] + idx, np.array(t)


def _assign(t: np.ndarray) -> Optional[List[int]]:
    """엄지->새끼 순서로 늘어선 n개(가로 위치 t, 0~1)를 5개 손가락 이름 중 순서를 지키며 가장 가까운 것에 짝짓는다 (DP)."""
    n = len(t)
    if n > 5:
        return None
    INF = 1e18
    cost = np.full((n + 1, 6), INF)
    take = np.zeros((n + 1, 6), dtype=int)
    cost[0, :] = 0.0
    for i in range(1, n + 1):
        for k in range(i, 6):                                    # i 번째 사슬이 k-1 번 손가락 (k = 1..5)
            for kk in range(i - 1, k):
                c = cost[i - 1, kk] + abs(t[i - 1] - _CANON[k - 1])
                if c < cost[i, k]:
                    cost[i, k], take[i, k] = c, kk
    k = int(np.argmin(cost[n, 1:])) + 1
    res = []
    for i in range(n, 0, -1):
        res.append(k - 1)
        k = take[i, k]
    return res[::-1]


def _leading_metacarpal(skel: Skeleton, hand: int, chains: List[List[int]]) -> bool:
    """사슬 맨 앞 뼈가 '손바닥 뼈(metacarpal)'인가 — 이 손의 모든 사슬에 대해 한 번에 정한다.
    ① 엄지가 다른 손가락보다 한 마디 짧으면(엄지는 손바닥 뼈가 없는 리그가 많다) 나머지에는 손바닥 뼈가 붙어 있다.
    ② 길이가 모두 같으면: 사슬 시작점이 손목에 아주 가깝고(손목~시작점이 첫 마디보다 짧음) 첫 마디가 길면 손바닥 뼈, 아니면 끝에 붙은 꼬리 뼈."""
    lens = [len(c) for c in chains]
    if max(lens) <= 3:
        return False
    if min(lens) < max(lens) and sum(1 for n in lens if n == max(lens)) >= len(lens) - 1:
        return True
    _wr, wp = skel.bind_world()
    r = []
    for c in chains:
        seg0 = float(np.linalg.norm(wp[c[1]] - wp[c[0]]))
        r.append(float(np.linalg.norm(wp[c[0]] - wp[hand])) / max(seg0, 1e-9))
    return float(np.median(r)) < 0.8


def pick_three(chain: List[int], meta: bool, lmax: int) -> List[int]:
    """사슬에서 1~3마디로 쓸 뼈 3개. meta=True 인 손에서는 가장 긴 사슬(손바닥 뼈가 붙은 쪽)의 앞 1개를 버리고, 남는 뒤쪽(꼬리 뼈)은 버린다.
    (엄지처럼 손바닥 뼈가 없어 한 마디 짧은 사슬은 앞을 버리지 않는다.) 3마디 이하는 그대로."""
    if len(chain) <= 3:
        return list(chain)
    start = 1 if (meta and len(chain) >= lmax) else 0
    return chain[start:start + 3]


def map_hand(skel: Skeleton, hand: int, min_chains: int = 2) -> Dict[str, int]:
    """한 손의 손가락 슬롯 (Thumb1..Pinky3 -> 관절). 이름 접두(L_/R_)는 붙이지 않는다. 못 찾으면 빈 딕셔너리.
    min_chains: 마디가 셋 이상인 사슬이 이만큼은 있어야 손가락으로 본다 (발톱·촉수 한두 개를 손가락으로 착각하지 않도록)."""
    chains = finger_chains(skel, hand)
    long_enough = sum(1 for c in chains if len(c) >= 3)
    simple_hand = len(chains) >= 3 and all(len(c) >= 2 for c in chains)   # 2마디 손가락 리그(바이페드 3손가락 등): 평행 사슬 3개 이상이면 손이다
    if (long_enough < min_chains and not simple_hand) or not 2 <= len(chains) <= 5:
        return {}                                        # 마디 셋 이상 사슬이 이만큼 있거나, 2마디라도 평행 사슬 3개는 있어야 손가락 (발톱·촉수 한두 개는 제외)
    od = order_chains(skel, hand, chains)
    if od is None:
        return {}
    order, t = od
    names = _assign(t)
    if names is None:
        return {}
    meta = _leading_metacarpal(skel, hand, chains)
    lens = [len(c) for c in chains]
    out: Dict[str, int] = {}
    for rank, ci in enumerate(order):
        bones = pick_three(chains[ci], meta, max(lens))
        for m, j in enumerate(bones[:3], 1):
            out["%s%d" % (FINGERS[names[rank]], m)] = int(j)
    return out


def fill_fingers(skel: Skeleton, slots: Dict[str, int], min_chains: int = 3) -> Dict[str, int]:
    """slots 에 이미 있는 것은 그대로 두고, 한쪽 손에 손가락 슬롯이 하나도 없을 때만 위치 기준으로 채운다. 새로 넣은 슬롯 목록을 돌려준다."""
    added: Dict[str, int] = {}
    for side in ("L", "R"):
        hand = slots.get(side + "_Hand")
        if hand is None or any(k.startswith(side + "_") and any(f in k for f in FINGERS) for k in slots):
            continue
        for k, j in map_hand(skel, int(hand), min_chains).items():
            key = "%s_%s" % (side, k)
            if key not in slots:
                added[key] = j
    slots.update(added)
    return added
