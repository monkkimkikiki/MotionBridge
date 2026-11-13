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
    species_l = [norm(s) for s in exp.get("species", []) if len(norm(s)) >= 4]
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
            elif len(n) >= 4 and (n in whole or (len(core) >= 4 and core in n)):   # 짧은 폴더명('An')이 아무 이름에나 걸리지 않게
                best = max(best, 0.45)
            else:
                r = difflib.SequenceMatcher(None, n, core).ratio()
                if r >= 0.72:
                    best = max(best, 0.3 * r + 0.1)
        if best:
            sc += best
            why.append("이름 유사")
        else:
            # 도감 일화의 종족 낱말(거미 -> zhizhu): 이름이 안 맞을 때만, 이름 일치보다 낮게 친다
            toks_f = {norm(t) for t in re.split(r"[_\-\s]+", folder or "") if t}
            sp_hit = next((sp for sp in species_l if sp in toks_f or core.startswith(sp)), "")
            if sp_hit:
                sc += 0.35
                why.append("일화 종족 '%s'" % sp_hit)
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
        # 이름·지역·기억·관련 낱말 중 하나라도 맞아야 후보 — 보스 신호(애니 수·연출)만으로는 후보가 되지 않는다
        # (안 그러면 아무 이름이나 넣어도 애니 많은 같은 폴더들이 늘 위에 나온다)
        if not why or all(w.startswith("애니") or w == "전용 연출" for w in why):
            continue
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


# ---------------------------------------------------------------------- 잡몹(이름 없는 몬스터): 외형 -> 종족 변형 찾기
# 실례(방패 든 늑대): 늑대 -> 狼 -> 병음 'Lang' 으로 변형 폴더(GYCY_Lang_01~08)를 모으고,
# '방패' 는 각 변형의 부속 파일 이름(weapon/Shield/huoba 횃불)에서 찾아 변형을 구분했다 -> GYCY_Lang_07.

def build_mob_prompt(desc: str, game_hint: str = "") -> str:
    game = (" in the game '%s'" % game_hint) if game_hint else ""
    return (
        "A user is looking for a regular enemy (mob)%s. They don't know its name, only how it looks: \"%s\".\n"
        "Game resources use the developer's native language romanization (e.g. pinyin for Chinese games) for the creature type, "
        "and attachment files are named after what the mob holds/wears (shield, bow, torch, lantern...).\n"
        "Answer ONLY JSON:\n"
        '{"species": ["creature-type word stems that could appear in folder names: origin-language romanization first (e.g. wolf -> lang), then english"],\n'
        ' "attributes": [["synonym stems for one held/worn item, origin romanization + english, e.g. shield -> dun, dunpai, shield, pai"], ["next item..."]],\n'
        ' "why": "one short sentence"}' % (game, (desc or "")[:400])
    )


def parse_mob(text: str) -> Dict:
    obj = _extract_json(text or "")
    if not isinstance(obj, dict):
        obj = {}
    sp = [str(x).strip() for x in (obj.get("species") or []) if str(x).strip()][:8] if isinstance(obj.get("species"), (list, tuple)) else []
    attrs: List[List[str]] = []
    if isinstance(obj.get("attributes"), (list, tuple)):
        for grp in obj["attributes"][:6]:
            if isinstance(grp, (list, tuple)):
                g = [str(x).strip() for x in grp if str(x).strip()][:8]
            else:
                g = [str(grp).strip()] if str(grp).strip() else []
            if g:
                attrs.append(g)
    return {"species": sp, "attributes": attrs, "why": str(obj.get("why") or "")[:160]}


# 서로 자주 헷갈리는 종족 묶음 — AI 가 '여우'라고 봐도 실제 폴더는 늑대(Lang)인 경우가 흔하다 (어둡고 흐린 장면).
# 한 묶음의 어간이 하나라도 나오면 묶음 전체로 넓혀 변형 폴더를 찾는다 (AI 호출 추가 없음 = 공짜 확장).
_SPECIES_GROUPS = [
    ["wolf", "fox", "dog", "jackal", "hound", "lang", "huli", "gou", "chai", "quan",
     "늑대", "여우", "이리", "승냥이", "狼", "狐"],                                               # 개과
    ["tiger", "cat", "lion", "leopard", "panther", "mao", "shi", "bao", "laohu",
     "호랑이", "고양이", "사자", "표범", "虎", "猫", "狮", "豹"],                                  # 고양이과
    ["rat", "mouse", "weasel", "ferret", "marten", "shu", "huangshulang", "diao",
     "쥐", "족제비", "담비", "鼠", "貂"],                                                         # 쥐·족제비
    ["snake", "serpent", "viper", "she", "mang", "뱀", "구렁이", "蛇", "蟒"],
    ["monkey", "ape", "gorilla", "hou", "yuan", "원숭이", "猴", "猿"],
    ["bear", "xiong", "곰", "熊"],
    ["boar", "pig", "hog", "zhu", "돼지", "멧돼지", "猪"],
    ["bird", "eagle", "hawk", "crow", "crane", "niao", "ying", "que", "he",
     "새", "독수리", "매", "까마귀", "학", "鸟", "鹰", "鹤"],
    ["frog", "toad", "wa", "chan", "개구리", "두꺼비", "蛙"],
    ["deer", "elk", "lu", "사슴", "鹿"],
    ["spider", "zhizhu", "거미", "蜘蛛"],
    ["scorpion", "xiezi", "전갈", "蝎"],
    ["bull", "ox", "cow", "niu", "소", "牛"],
    ["dragon", "long", "용", "龙"],
    ["centipede", "wugong", "지네", "蜈蚣"],
    ["toad", "chanchu", "두꺼비", "蟾"],
]


def lore_stems(texts: Sequence[str]) -> List[str]:
    """도감 글(일화·시구)에 나오는 종족 낱말 -> 그 종족 묶음의 낱말 전부 (원어 병음 포함).
    예: '둘째 언니' 일화에 '거미'가 나오면 zhizhu 를 얻어 PSD_ZhiZhuJing 폴더와 잇는다 — AI 없이."""
    low = " ".join(t or "" for t in texts).lower()
    out: List[str] = []
    for grp in _SPECIES_GROUPS:
        if any(len(w) >= 2 and w.lower() in low for w in grp if not re.fullmatch(r"[a-z]{1,3}", w)):
            out += [g for g in grp if re.fullmatch(r"[a-z]{4,}", g)]
    return list(dict.fromkeys(out))


def _expand_species(stems: Sequence[str]) -> List[str]:
    out = [s for s in stems if s]
    low = {norm(s) for s in out}
    for grp in _SPECIES_GROUPS:
        if low & {norm(g) for g in grp}:
            out += [g for g in grp if norm(g) not in low]
    return list(dict.fromkeys(out))


def score_mob(exp: Dict, stats: Dict[str, dict], mesh_paths: Sequence[str] = (), limit: int = 12) -> List[dict]:
    """종족 어간(혼동 묶음으로 확장)으로 변형 폴더를 모으고, 부속 파일 이름의 특징 낱말로 변형을 구분해 점수 매긴다."""
    species = [norm(s) for s in _expand_species(list(exp.get("species", []))) if len(norm(s)) >= 3]
    attrs = [[norm(a) for a in grp if len(norm(a)) >= 3] for grp in exp.get("attributes", [])]
    attrs = [g for g in attrs if g]
    if not species:
        return []
    base_names = [(p, (p or "").replace("\\", "/").rsplit("/", 1)[-1].lower()) for p in mesh_paths]
    out = []
    for folder, s in stats.items():
        core, whole = _core_name(folder), norm(folder)
        toks = {norm(t) for t in re.split(r"[_\-\s]+", folder or "") if t}
        sp_tok = next((sp for sp in species if sp == core or sp in toks), "")                 # 'GYCY_Lang_03' 의 토큰 'lang'
        sp_sub = sp_tok or next((sp for sp in species if sp in whole and len(sp) >= 4), "")   # 'HuangPaoLang' 속 'lang' (덜 확실)
        if not sp_sub:
            continue
        sc, why = (0.45, ["종족 '%s'" % sp_tok]) if sp_tok else (0.36, ["종족 '%s'(이름 일부)" % sp_sub])
        # 이 변형에 딸린 파일 이름들: 폴더 안 메시 + (다른 곳에 있어도) 파일 이름에 이 폴더명이 든 것 (예: VFX 의 ..._Lang_07_Shield_VFX)
        fl = norm(folder)
        hay = " ".join((p or "").replace("\\", "/").rsplit("/", 1)[-1].lower() for p in s["meshes"])
        hay += " " + " ".join(b for _p, b in base_names if fl and fl in norm(b))
        n_attr = 0
        for grp in attrs:
            word = next((a for a in grp if a in norm(hay)), "")
            if word:
                n_attr += 1
                why.append("들고/걸친 것 '%s'" % word)
        sc += min(n_attr * 0.25, 0.5)
        if attrs and not n_attr:
            sc -= 0.1          # 특징을 아는데 하나도 안 맞는 변형은 뒤로
        out.append({"folder": folder, "score": round(max(min(sc, 1.0), 0.0), 3), "why": " + ".join(why),
                    "meshes": list(s["meshes"]), "anims": s["anims"]})
    out.sort(key=lambda d: (-d["score"], d["folder"].lower()))
    return out[:limit]


def find_mob(ask_text: Optional[Callable[[str], str]], desc: str, game_hint: str,
             mesh_paths: Sequence[str], anim_paths: Sequence[str] = ()) -> Dict:
    """외형 설명만으로 잡몹 변형 후보를 찾는다 -> {"candidates": [...], "expansion": {...}, "note": str}."""
    exp, note = {"species": [], "attributes": [], "why": ""}, ""
    if ask_text is not None:
        try:
            exp = parse_mob(ask_text(build_mob_prompt(desc, game_hint)))
            note = exp.get("why") or ""
        except Exception as e:  # noqa: BLE001
            note = "AI 외형 확장 실패(%s)" % str(e)[:80]
    stats = folder_stats(mesh_paths, anim_paths)
    return {"candidates": score_mob(exp, stats, mesh_paths), "expansion": exp, "note": note}
