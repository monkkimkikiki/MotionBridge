"""진짜 PySide6(화면 없이 offscreen)로 창이 실제로 만들어지고 탭 분리/합치기가 되는지 확인.

가짜 Qt 시험(run_all)은 위젯 배치·시그널 연결 오류를 못 잡는다. CI 에서는 PySide6 가 설치되므로 여기서 한 번 더 본다.
PySide6 가 없으면 건너뛴다(종료 코드 0).
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
try:
    from PySide6.QtWidgets import QApplication, QLabel
except ImportError:
    print("PySide6 없음: 건너뜀")
    sys.exit(0)

fails = []


def check(c, m):
    if not c:
        fails.append(m)
        print("FAIL", m)
    else:
        print("ok  ", m)


app = QApplication.instance() or QApplication([])

# 1) 탭 줄 단독
from motionbridge.ui.chrometabs import ChromeTabs  # noqa: E402

t = ChromeTabs()
pages = [QLabel("p%d" % i) for i in range(4)]
for i, p in enumerate(pages):
    t.addTab(p, "탭%d" % i, "긴 이름 %d" % i)
t.show()
app.processEvents()
seen = []
t.currentChanged.connect(seen.append)
t.setCurrentIndex(2)
check(t.currentIndex() == 2 and t.bar.currentIndex() == 2 and seen == [2], "번호로 전환 + 신호")
t.bar.setCurrentIndex(1)
check(t.currentIndex() == 1 and seen[-1] == 1, "탭 줄을 눌러 전환")
w = t.hub.detach(pages[1], None)
app.processEvents()
check(w is not None and w.isVisible() and pages[1].parent() is not None and t.count() == 4 and len(t.pages) == 3, "탭 분리 -> 별도 창")
check(t.bar.count() == 3 and w.view.bar.count() == 1, "탭 줄 개수 갱신")
t.setCurrentIndex(1)
check(t.hub.view_of(pages[1]) is w.view, "분리된 화면도 번호로 접근")
w.close()
app.processEvents()
check(not t.hub.floats and t.pages.index(pages[1]) == 1 and t.bar.count() == 4, "창 닫으면 제자리로")
check(t.hub.detach(pages[0], None) is not None and t.hub.detach(pages[2], None) is not None, "여러 개 분리")
t.hub.detach(pages[3], None)
check(len(t.pages) >= 1, "메인 창은 비지 않음")

# 2) 실제 메인 창
from motionbridge.core import bridge  # noqa: E402

with tempfile.TemporaryDirectory() as d:
    os.environ["HOME"] = d
    os.environ["USERPROFILE"] = d
    bridge.CONFIG_PATH = os.path.join(d, "cfg.json")
    from motionbridge.ui import app as A  # noqa: E402

    mw = A.MainWindow()
    mw.show()
    app.processEvents()
    check(mw.tabs.count() == 7, "메인 창 탭 7개")
    for i in range(mw.tabs.count()):
        mw.tabs.setCurrentIndex(i)
        app.processEvents()
    check(mw.tabs.currentIndex() == 6, "모든 탭 순회")
    vt = mw.vidfind
    win = mw.tabs.hub.detach(vt, None)
    app.processEvents()
    check(win is not None and win.isVisible(), "영상찾기 탭 분리")
    mw.tabs.setCurrentIndex(5)
    check(mw.tabs.hub.view_of(vt).current_page() is vt, "분리 후에도 이동 호출 동작")
    win.close()
    app.processEvents()
    check(mw.tabs.pages.index(vt) == 5, "닫으면 원래 자리")
    mw.close()

print("모두 통과" if not fails else "실패 %d개" % len(fails))
sys.exit(1 if fails else 0)
