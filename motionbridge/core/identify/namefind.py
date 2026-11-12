"""이름 단서로 보스/캐릭터 리소스 찾기 — 쿼리 확장 (Qt 불필요).

'금지장로'처럼 유저가 아는 이름(한국어/영어)과 리소스 이름(병음·모티프 코드명)이 전혀 다를 때:
  1) LLM 이 단서를 확장한다: 원어 정식명(중국어 등), 병음 음차, 등장 지역(병음 + 이니셜),
     원전(서유기 등) 모티프에서 나올 법한 내부 코드명 후보까지.
  2) 에셋 인덱스의 캐릭터 폴더들을 점수 매긴다: 이름 유사 + 지역 코드 일치
     (예: 旧观音禅院 -> GuanYinChanYuan -> 'GYCY' 이니셜) + 보스 신호(애니 수·전용 연출·무기 메시).
  3) 후보를 근거와 함께 돌려준다. 확정은 생김새 검증/검수자 몫, 고른 것은 entity_store 가 기억한다.

실례: '금지장로' -> 지역 GYCY + 보스 신호 -> GYCY_YanJianXi(眼看喜, 서유기 16회 '보물을 보고 기뻐한' 탐욕 모티프).
"""
from __future__ import annotations

import difflib
import os
import re
from typing import Callable, Dict, List, Optional, Sequence

from ..vision import _extract_json
from .entity_store import EntityStore, norm

_STRUCT_DIRS = {"mesh", "meshes", "model", "models", "art", "skeletalmesh", "sk", "texture", "textures"}
_TAIL = re.compile(r"(?i)(_\d{1,3}[a-z]?|_base|_body)+$")
_AREA = re.compile(r"^([A-Za-z]{2,5})_")


def build_prompt(clue: str, game_hint: str = "") -> str:
    game = (" in the game '%s'" % game_hint) if game_hint else ""
    return (
        "A user is looking for the game assets of a character called \"%s\"%s. "
        "Game resources often use the developer's native language romanization (e.g. pinyin) or "
        "motif-based codenames from the source literature, NOT the localized display name.\n"
        "Expand the clue. Answer ONLY JSON:\n"
        '{"official": {"zh": "", "en": "", "ko": "", "ja": ""},\n'
        ' "romanized": ["pinyin or romaji of the official name, with and without spaces"],\n'
        ' "location": {"name": "where it appears", "romanized": "", "initials": ["initial letters of each romanized syllable, e.g. GuanYinChanYuan -> GYCY"]},\n'
        ' "keywords": ["short related words that could appear in file names (monster type, weapon, lore items)"],\n'
        ' "codenames": ["guesses for internal codenames: motif words from the source novel/lore, abbreviations"],\n'
        ' "why": "one short sentence"}' % (clue, game)
    )


def parse_expand(text: str) -> Dict:
    """LLM 답 -> 확장 사전 (깨진 답은 빈 값으로)."""
    obj = _extract_json(text or "")
    if not isinstance(obj, dict):
        obj = {}
    out = {"official": {}, "romanized": [], "initials": [], "keywords": [], "codenames": [], "why": str(obj.get("why") or "")[:160]}
    off = obj.get("official")
    if isinstance(off, dict):
        out["official"] = {str(k): str(v) for k, v in off.items() if v}
    loc = obj.get("location") if isinstance(obj.get("location"), dict) else {}
    for key, src in (("romanized", obj.get("romanized")), ("initials", loc.get("initials")),
                     ("keywords", obj.get("keywords")), ("codenames", obj.get("codenames"))):
        if isinstance(src, (list, tuple)):
            out[key] = [str(x).strip() for x in src if str(x).strip()][:12]
    if loc.get("romanized"):
        out["romanized"].append(str(loc["romanized"]))
    return out


def folder_stats(mesh_paths: Sequence[str], anim_paths: Sequence[str] = ()) -> Dict[str, dict]:
    """메시/애니 경로 -> 캐릭터 폴더별 통계: 메시들, 애니 수, 보스 신호(전용 연출·무기)."""
    stats: Dict[str, dict] = {}
    for p in mesh_paths:
        parts = [x for x in (p or "").replace("\\", "/").split("/") if x]
        if len(parts) < 2:
            continue
        folder = parts[-2]
        if folder.lower() in _STRUCT_DIRS and len(parts) >= 3:
            folder = parts[-3]
        s = stats.setdefault(folder, {"folder": folder, "meshes": [], "anims": 0, "cine": False, "weapons": 0})
        s["meshes"].append(p)
        if "weapon" in parts[-1].lower():
            s["weapons"] += 1
    lows = sorted(((k.lower(), k) for k in stats), key=lambda x: -len(x[0]))   # 긴 이름 먼저 (부분 겹침 방지)
    for a in anim_paths:
        la = (a or "").replace("\\", "/").lower()
        base = la.rsplit("/", 1)[-1]
        for lk, k in lows:
            if ("/" + lk + "/") in la or lk in base:
                s = stats[k]
                s["anims"] += 1
                if "die_seq" in base or "jiezhan" in base or "_seq" in base:
                    s["cine"] = True
                break
    return stats


def _core_name(folder: str) -> str:
    """'GYCY_YanJianXi_01' -> 'yanjianxi' (지역 접두사·번호 꼬리 제거)."""
    f = _TAIL.sub("", folder or "")
    m = _AREA.match(f)
    if m and len(f) > len(m.group(0)):
        f = f[len(m.group(0)):]
    return norm(f)


def score_folders(exp: Dict, stats: Dict[str, dict], clue: str = "", store: Optional[EntityStore] = None, limit: int = 10) -> List[dict]:
    """확장 결과로 폴더들을 점수 매긴다 -> [{folder, score, why, meshes, anims}] (근거 포함, 점수순)."""
    names = [clue] + list(exp.get("romanized", [])) + list(exp.get("codenames", [])) + list(exp.get("official", {}).values())
    names = [norm(n) for n in names if n and norm(n)]
    keywords = [norm(k) for k in exp.get("keywords", []) if norm(k)]
    initials = {norm(i) for i in exp.get("initials", []) if i}
    store_codes = set()
    if store is not None and clue:
        for h in store.find(clue, limit=3):
            if h.score >= 0.86:
                store_codes |= {norm(c) for c in h.entity.codenames}
    out = []
    for folder, s in stats.items():
        core, whole = _core_name(folder), norm(folder)
        m = _AREA.match(folder)
        area = m.group(1) if m else ""
        sc, why = 0.0, []
        if store_codes and (core in store_codes or whole in store_codes):
            sc += 0.6
            why.append("기억된 캐릭터")
        best = 0.0
        for n in names:
            if not n:
                continue
            if n == core or n == whole:
                best = max(best, 0.55)
            elif len(n) >= 4 and (n in whole or core in n):
                best = max(best, 0.45)
            else:
                r = difflib.SequenceMatcher(None, n, core).ratio()
                if r >= 0.72:
                    best = max(best, 0.3 * r + 0.1)
        if best:
            sc += best
            why.append("이름 유사")
        if area and norm(area) in initials:
            sc += 0.35
            why.append("지역 코드 %s" % area)
        if any(k and (k in whole) for k in keywords):
            sc += 0.1
            why.append("관련 낱말")
        if s["anims"] >= 80:
            sc += 0.1
            why.append("애니 %d개(보스급)" % s["anims"])
        elif s["anims"] >= 30:
            sc += 0.05
        if s["cine"]:
            sc += 0.08
            why.append("전용 연출")
        if s["weapons"]:
            sc += 0.04
        if sc <= 0.05:
            continue
        out.append({"folder": folder, "score": round(min(sc, 1.0), 3), "why": " + ".join(why),
                    "meshes": list(s["meshes"]), "anims": s["anims"]})
    out.sort(key=lambda d: (-d["score"], d["folder"].lower()))
    return out[:limit]


def find(ask_text: Optional[Callable[[str], str]], clue: str, game_hint: str,
         mesh_paths: Sequence[str], anim_paths: Sequence[str] = (),
         store: Optional[EntityStore] = None) -> Dict:
    """단서 하나로 후보 폴더들을 찾는다 -> {"candidates": [...], "expansion": {...}, "note": str}.

    LLM(ask_text) 이 없거나 실패하면 확장 없이 이름 유사·보스 신호만으로 추린다 (조용한 폴백)."""
    exp, note = {"official": {}, "romanized": [], "initials": [], "keywords": [], "codenames": [], "why": ""}, ""
    if ask_text is not None:
        try:
            exp = parse_expand(ask_text(build_prompt(clue, game_hint)))
            note = exp.get("why") or ""
        except Exception as e:  # noqa: BLE001 - 확장 실패해도 검색은 계속
            note = "AI 확장 실패(%s) — 이름 유사로만 찾습니다" % str(e)[:80]
    stats = folder_stats(mesh_paths, anim_paths)
    return {"candidates": score_folders(exp, stats, clue=clue, store=store), "expansion": exp, "note": note}
