"""웹에서 공개된 AES 키 후보 모으기 — AI 없이도 동작한다.

흐름: 검색(DuckDuckGo HTML) → 믿을 만한 공개 사이트(모딩 포럼·깃허브 등) 상위 몇 페이지를 열어 →
키처럼 생긴 것(64자리 16진수)을 뽑는다. 후보는 반드시 extractor.pick_working_key 로
'실제로 게임이 열리는지' 검증한 뒤에만 쓰므로, 엉뚱한 해시가 섞여 들어와도 해가 없다.

가져온 페이지는 글자로만 다룬다(실행·해석하지 않음). 키 후보 수는 상한을 둬서 검증 시간이 길어지지 않게 한다.
"""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
from typing import Callable, List, Optional, Tuple

from . import gamescan

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MotionBridge-keyfind"}
# 키가 실제로 공개돼 있는 공개(로그인 불필요) 사이트들만 연다
_HOSTS = ("gildor.org", "github.com", "raw.githubusercontent.com", "gist.github.com",
          "nexusmods.com", "forums.nexusmods.com", "tcrf.net", "reddit.com", "steamcommunity.com")
_AES_NEAR = re.compile(r"(?i)aes|encrypt")   # 'key' 단독은 너무 흔해서(체크섬 설명 등) 제외


def _get(url: str, timeout: float = 12.0, cap: int = 1_500_000) -> str:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - 공개 웹 페이지 읽기 전용
        return r.read(cap).decode("utf-8", "replace")


def search_urls(query: str, limit: int = 6, get: Callable[[str], str] = _get) -> List[str]:
    """검색 결과에서 공개 사이트 주소만 추린다."""
    html = get("https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote_plus(query))
    out: List[str] = []
    for u in re.findall(r'href="([^"]+)"', html):
        m = re.search(r"uddg=([^&\"]+)", u)          # 덕덕고는 실제 주소를 uddg= 뒤에 숨긴다
        real = urllib.parse.unquote(m.group(1)) if m else (u if u.startswith("http") else "")
        if not real.startswith("http"):
            continue
        host = urllib.parse.urlparse(real).netloc.lower()
        if any(host == h or host.endswith("." + h) for h in _HOSTS) and real not in out:
            out.append(real)
        if len(out) >= limit:
            break
    return out


def _keys_in_page(text: str) -> List[str]:
    """페이지 글에서 키 후보: 'aes/key' 낱말 근처의 64자리 16진수만 (깃허브의 sha256 해시 홍수를 걸러낸다)."""
    out: List[str] = []
    for m in re.finditer(r"(?:0x)?[0-9A-Fa-f]{64}", text):
        lo, hi = max(m.start() - 240, 0), min(m.end() + 240, len(text))
        if m.group(0).lower().startswith("0x") or _AES_NEAR.search(text[lo:hi]):
            for k in gamescan.extract_key_candidates(m.group(0)):
                if k not in out:
                    out.append(k)
    return out


def find_keys(game: str, on_progress: Optional[Callable[[str], None]] = None,
              max_pages: int = 5, max_keys: int = 12,
              get: Callable[[str], str] = _get) -> Tuple[List[str], List[str]]:
    """게임 이름으로 웹에서 키 후보를 모은다 -> (후보들, 출처 주소들). 실패해도 조용히 빈 목록."""

    def say(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    cands: List[str] = []
    sources: List[str] = []
    for q in ("%s pak AES key" % game, "%s AES key FModel" % game):
        try:
            say("웹 검색: %s" % q)
            urls = search_urls(q, get=get)
        except Exception:  # noqa: BLE001 - 검색 실패는 다음 검색어로
            continue
        for u in urls[:max_pages]:
            if len(cands) >= max_keys:
                break
            try:
                say("페이지 읽는 중: %s" % urllib.parse.urlparse(u).netloc)
                for k in _keys_in_page(_get_text(get(u))):
                    if k not in cands and len(cands) < max_keys:
                        cands.append(k)
                        if u not in sources:
                            sources.append(u)
            except Exception:  # noqa: BLE001 - 페이지 하나 실패는 건너뜀
                continue
        if cands:
            break
    return cands, sources


def _get_text(html: str) -> str:
    """태그를 대충 걷어낸 글 (키는 글자라 정밀한 파싱이 필요 없다)."""
    return re.sub(r"<[^>]+>", " ", html or "")
