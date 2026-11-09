"""서로 맞물리는 연출(처형·패리 피니셔·잡기 등)의 짝 찾기: 보스의 피격/처형당함 모션 -> 그와 짝인 플레이어 공격 모션.

두 모션이 한 장면으로 만들어졌으면 보통 길이가 거의 같고(앞뒤 여유 프레임은 어긋날 수 있어 기본 ±30프레임), 이름에 같은 번호/낱말이 든다.
 - 길이: 초 단위로 비교한다(프레임 속도가 달라도 됨). 허용 오차를 넘으면 후보에서 뺀다. 길이를 모르는 후보는 낮은 점수로 남긴다.
 - 이름: Victim/Attacker/Player/Boss 같은 '역할' 낱말을 빼고 남은 낱말이 겹칠수록 가산.
Qt 에 의존하지 않는다.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

# 피격·그로기·처형당함 계열 (상대 쪽 모션을 가려낼 때)
REACTION_WORDS = ("hit", "react", "hurt", "damage", "stagger", "stun", "groggy", "knock", "down", "fall", "kneel", "break", "guardbreak",
                  "exec", "finish", "fatal", "victim", "riposte", "parry", "grab", "death", "dead", "die", "faint", "weak")
# 짝 연출을 알리는 낱말 (공유하면 가산)
PAIR_WORDS = ("exec", "finish", "fatal", "riposte", "parry", "grab", "backstab", "kill", "plunge", "throw", "drain", "sync")
# 이름에서 빼는 '역할/주체' 낱말
ROLE_WORDS = {"victim", "attacker", "player", "pc", "boss", "enemy", "monster", "npc", "char", "character", "hero", "sk", "as", "am", "a", "anim", "animation",
              "mon", "target", "receiver", "giver", "user", "me", "you", "loop", "start", "end", "montage", "seq", "sequence"}

_SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])")


def words(name: str) -> List[str]:
    base = os.path.splitext(os.path.basename(str(name).replace("\\", "/")))[0]
    return [w.lower() for w in _SPLIT.split(base) if w]


def is_reaction(text: str) -> bool:
    ws = words(text)
    return any(any(w.startswith(r) for r in REACTION_WORDS) for w in ws)


def _core(name: str) -> set:
    return {w for w in words(name) if w not in ROLE_WORDS and len(w) > 1}


def name_similarity(a: str, b: str) -> Tuple[float, List[str]]:
    """역할 낱말을 뺀 낱말 집합의 겹침(0~1)과 겹친 낱말."""
    ca, cb = _core(a), _core(b)
    if not ca or not cb:
        return 0.0, []
    common = ca & cb
    return len(common) / len(ca | cb), sorted(common)


@dataclass
class PairHit:
    path: str
    score: float
    why: str
    d_frames: Optional[float]     # 기준 모션과의 길이 차 (기준 프레임 속도로 환산한 프레임 수, 모르면 None)
    length: float = -1.0


def pair_rank(src_path: str, src_frames: int, src_length: float, cands: Iterable[Tuple[str, int, float]],
              tol_frames: float = 30.0) -> List[PairHit]:
    """cands: (경로, 프레임 수, 길이(초)) — 모르면 -1. 점수 높은 순."""
    if src_length <= 0 or src_frames <= 0:
        raise ValueError("기준 모션의 길이를 모릅니다 (파일 안을 먼저 읽어야 합니다).")
    fps = src_frames / src_length
    tol_s = max(tol_frames, 1.0) / fps
    out: List[PairHit] = []
    src_words = set(words(src_path))
    for path, fr, ln in cands:
        sim, common = name_similarity(src_path, path)
        shared_pair = [w for w in common if any(w.startswith(p) for p in PAIR_WORDS)]
        bonus = 0.1 if shared_pair else 0.0
        if ln is None or ln <= 0:
            score = min(1.0, 0.1 + 0.4 * sim + bonus)
            out.append(PairHit(path, score, "길이를 모름" + ((" · 이름 겹침: " + ",".join(common)) if common else ""), None, -1.0))
            continue
        d = abs(ln - src_length)
        if d > tol_s:
            continue
        ls = 1.0 - 0.5 * d / tol_s                       # 오차 0 -> 1.0, 허용 끝 -> 0.5
        score = min(1.0, 0.6 * ls + 0.4 * sim + bonus)
        dfr = (ln - src_length) * fps
        why = "길이 %.2fs (%+.0f프레임)" % (ln, dfr) + ((" · 이름 겹침: " + ",".join(common)) if common else "")
        out.append(PairHit(path, round(score, 4), why, dfr, ln))
    out.sort(key=lambda h: (-h.score, abs(h.d_frames) if h.d_frames is not None else 1e9, h.path))
    return out
