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
    os.environ["APPDATA"] = os.path.join(d, "appdata")
    bridge.CONFIG_PATH = os.path.join(d, "cfg.json")
    from motionbridge.ui import app as A  # noqa: E402

    mw = A.MainWindow()
    mw.show()
    app.processEvents()
    check(mw.tabs.count() == 8, "메인 창 탭 8개")
    for i in range(mw.tabs.count()):
        mw.tabs.setCurrentIndex(i)
        app.processEvents()
    check(mw.tabs.currentIndex() == 7, "모든 탭 순회")
    vt = mw.vidfind
    win = mw.tabs.hub.detach(vt, None)
    app.processEvents()
    check(win is not None and win.isVisible(), "영상찾기 탭 분리")
    mw.tabs.setCurrentIndex(5)
    check(mw.tabs.hub.view_of(vt).current_page() is vt, "분리 후에도 이동 호출 동작")
    win.close()
    app.processEvents()
    check(mw.tabs.pages.index(vt) == 5, "닫으면 원래 자리")
    # AnimRef 탭: 열 때 AnimRef 화면이 만들어지고, '추출 툴로 보내기'가 같은 프로그램의 영상찾기 탭으로 바로 간다
    ar = mw.animref
    mw.tabs.setCurrentIndex(7)
    app.processEvents()
    check(ar.inner is not None, "AnimRef 화면 생성: %s" % (ar.error or "ok"))
    if ar.inner is not None:
        from motionbridge.animref.core import link as alink

        check(ar.inner.embedded and ar.inner.tabs.count() == 3 and ar.inner.link_server is None, "내장 모드(자체 연결 창구 없음)")
        check(alink.LOCAL_TOOL is not None, "추출 툴 직접 전달 연결")
        qd = os.path.join(d, "handoff", "c1")
        os.makedirs(qd)
        qf = os.path.join(qd, "query.json")
        import json

        json.dump({"animref_handoff": 1, "clip": {"id": "c1", "label": "타격", "video": os.path.join(d, "v.mp4")}, "hits": [0.5],
                   "result_path": os.path.join(qd, "result.json")}, open(qf, "w", encoding="utf-8"))
        alink.LOCAL_TOOL(qf)
        app.processEvents()
        check(mw.vidfind.link_query and mw.vidfind.link_query["id"] == "c1", "AnimRef -> 영상찾기 직접 전달")
        w3 = mw.tabs.hub.detach(ar, None)  # 분리해도 동작
        app.processEvents()
        check(w3 is not None and ar.inner.isVisible(), "AnimRef 탭 분리")
        w3.close()
        app.processEvents()
        check(mw.tabs.pages.index(ar) == 7, "AnimRef 탭 합치기")
        check(ar.can_close(), "닫기 허용")
        tb = ar.inner.library.topbar  # 좁은 화면: 글자 대신 아이콘만 (글이 잘려 안 보이는 것 방지)
        tb.set_compact(False)
        full = [b.text() for b, *_r in tb._tracked]
        tb.set_compact(True)
        check(all(len(b.text()) <= 2 for b, *_r in tb._tracked) and all("\n" in b.toolTip() or b.toolTip() for b, *_r in tb._tracked), "좁으면 아이콘만")
        tb.set_compact(False)
        check([b.text() for b, *_r in tb._tracked] == full and any("게임 모션 찾기" in t for t in full), "넓으면 글자 복귀")
        check(tb.needed() > 0, "필요 폭 계산")
    # 프레임 편집 줄: 실제 그리기/마우스/편집이 진짜 Qt 에서 돈다
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import test_pipeline as TP  # noqa: E402

    src, _s2, tgt, _a, _b = TP.make_inputs(d)
    mw.pr.load_source(src)
    mw.pr.load_target(tgt)
    mw.pr.automap()
    mw.recompute()
    app.processEvents()
    bar = mw.retime_bar
    T0 = mw.pr.src.animations[0].n_frames
    bar.strip.resize(700, bar.strip.H)
    img = bar.strip.grab()
    check(not img.isNull() and bar.strip.blocks() and mw.vd.n_frames == T0, "조각 줄 그리기")
    bar.strip.press_at(bar.strip.x_of(12))
    bar.strip._drag = None
    check(mw.frame_slider.value() == 12 and bar.strip.frame == 12, "줄 클릭 -> 프레임 이동")
    mw.frame_slider.setValue(8)
    bar.mark_in()
    mw.frame_slider.setValue(24)
    bar.mark_out()
    check(bar.apply_speed(2.0) and mw.vd.n_frames == T0 - 8, "구간 속도 2배 -> 화면 반영")
    app.processEvents()
    check(not bar.strip.grab().isNull() and len(bar.strip.blocks()) == 3, "편집 후 조각 3개 그리기")
    mw.frame_slider.setValue(3)
    check(bar.hold() and bar.undo() and bar.redo() and bar.reset() and mw.vd.n_frames == T0, "정지/되돌리기/다시/원본")
    # 진행 창(추측 중): X/취소/Esc = 취소 신호 한 번, 정상 종료는 신호 없음
    from motionbridge.ui.widgets import BusyDialog  # noqa: E402

    got = []
    bd = BusyDialog("몬스터 추측 중", mw)
    bd.cancelled.connect(lambda: got.append(1))
    bd.show()
    bd.set_progress(1, 4, "장면 a.png", "몬스터 추측 중")
    app.processEvents()
    check(bd.bar.maximum() == 4 and bd.bar.value() == 1 and "2/4" in bd.text.text(), "진행 창 진행률 표시")
    bd.close()  # 오른쪽 위 X
    bd.reject()
    check(got == [1], "X 로 닫으면 취소 신호 한 번: %s" % got)
    bd2 = BusyDialog("x", mw)
    bd2.cancelled.connect(lambda: got.append(2))
    bd2.show()
    bd2.finish()
    check(got == [1] and not bd2.isVisible(), "정상 종료는 취소 신호 없음")
    mw.close()
    check(alink.LOCAL_TOOL is None if ar.inner is not None else True, "닫으면 직접 전달 해제")

print("모두 통과" if not fails else "실패 %d개" % len(fails))
sys.exit(1 if fails else 0)
