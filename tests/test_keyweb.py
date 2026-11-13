"""웹 키 검색(keyweb): 검색 결과 파싱·공개 사이트 필터·'aes 근처' 키 추출 (네트워크 없이 가짜 get 으로)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from motionbridge.core import keyweb

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("FAIL", msg)


K1 = "A" * 64
K2 = "B" * 64
SEARCH = ('<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.gildor.org%2Fsmf%2Findex.php%3Ftopic%3D7450.0&amp;rut=x">1</a>'
          '<a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fevil.example.com%2Fkeys&amp;rut=x">2</a>'
          '<a href="https://github.com/someone/GameAESKey">3</a>')
PAGE_GILDOR = "<html><b>AES key:</b> 0x%s </html>" % K1
PAGE_GITHUB = ("<html>sha256 checksum %s is not it</html>" % ("C" * 64)   # 'aes' 낱말이 근처(±240자)에 없음 -> 걸러짐
               + "x" * 300
               + "<p>the aes key is <code>%s</code></p>" % K2)


def fake_get(url, *a, **k):
    if "duckduckgo" in url:
        return SEARCH
    if "gildor" in url:
        return PAGE_GILDOR
    if "github" in url:
        return PAGE_GITHUB
    raise RuntimeError("열면 안 되는 주소: " + url)


urls = keyweb.search_urls("x", get=fake_get)
check(urls == ["https://www.gildor.org/smf/index.php?topic=7450.0", "https://github.com/someone/GameAESKey"],
      "공개 사이트만 추림 (evil 제외): %s" % urls)

cands, srcs = keyweb.find_keys("Some Game", get=fake_get)
check("0x" + K1 in cands and "0x" + K2 in cands, "두 페이지의 키 수집: %s" % cands)
check(("0x" + "C" * 64) not in cands, "aes 낱말 근처가 아닌 64자리(해시)는 제외")
check(any("gildor" in s for s in srcs), "출처 기록: %s" % srcs)

bad = keyweb.find_keys("x", get=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("net down")))
check(bad == ([], []), "네트워크 실패는 조용히 빈 결과")

if fails:
    print("\n실패 %d개" % len(fails))
    sys.exit(1)
print("test_keyweb ALL OK")
