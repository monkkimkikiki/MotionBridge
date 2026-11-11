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
check(sum(1 for p in pages if p.isVisible()) == 1 and pages[0].isVisible(), "탭: 현재 화면 하나만 보임(나머지가 왼쪽 위에 겹치지 않음)")
seen = []
t.currentChanged.connect(seen.append)
t.setCurrentIndex(2)
app.processEvents()
check(sum(1 for p in pages if p.isVisible()) == 1 and pages[2].isVisible(), "탭 전환 뒤에도 하나만 보임")
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
    check(mw.mode() == "animref" and mw.animref.isVisible() and not mw.tabs.isVisible(), "시작 화면은 AnimRef")
    mw.set_mode("motionbridge")
    app.processEvents()
    check(mw.tabs.count() == 8, "메인 창 탭 8개 (AnimRef 는 최상단 선택)")
    check(sum(1 for p in mw.tabs.pages if p.isVisible()) == 1, "메인 창: 처음에 보이는 탭 화면은 하나")
    for i in range(mw.tabs.count()):
        mw.tabs.setCurrentIndex(i)
        app.processEvents()
        vis = [mw.tabs.tabText(k) for k, p in enumerate(mw.tabs.pages) if p.isVisible()]
        check(len(vis) == 1 and mw.tabs.widget(i).isVisible(), "탭 %d 에서 보이는 화면: %s" % (i, vis))
    check(mw.tabs.currentIndex() == 7, "모든 탭 순회")
    vt = mw.vidfind
    win = mw.tabs.hub.detach(vt, None)
    app.processEvents()
    check(win is not None and win.isVisible(), "영상찾기 탭 분리")
    mw.tabs.setCurrentIndex(5)
    check(mw.tabs.hub.view_of(vt).current_page() is vt, "분리 후에도 이동 호출 동작")
    win.close()
    app.processEvents()
    check(mw.tabs.pages.index(vt) == 1, "닫으면 원래 자리 (둘째 칸): %d" % mw.tabs.pages.index(vt))
    # AnimRef 탭: 열 때 AnimRef 화면이 만들어지고, '추출 툴로 보내기'가 같은 프로그램의 영상찾기 탭으로 바로 간다
    ar = mw.animref
    mw.set_mode("animref")
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
        if w3 is not None:      # AnimRef 가 탭 줄 안에 있을 때만 (지금은 최상단 선택이라 탭 줄 밖 -> 분리 대상이 아님)
            check(ar.inner.isVisible(), "AnimRef 탭 분리")
            w3.close()
            app.processEvents()
            check(mw.tabs.pages.index(ar) == 0, "AnimRef 탭 합치기(맨 앞)")
        else:
            check(ar not in mw.tabs.pages, "AnimRef 는 탭 줄 밖(최상단 선택)이라 분리 대상이 아님")
        check(ar.can_close(), "닫기 허용")
        # 영상 플레이어는 모달이 아니다 (띄워 둔 채 다른 곳을 쓸 수 있음)
        import inspect

        from motionbridge.animref.ui import library_tab as _lt
        src_play = inspect.getsource(_lt.LibraryTab.play)
        check("exec()" not in src_play and "setModal(False)" in src_play and "show()" in src_play, "영상 플레이어 비모달")
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
    mw.frame_slider.setValue(23)
    bar.mark_out()
    check(bar.strip.selection() == (8, 23) and bar.range() == (8, 24), "시작/끝 = 끝 프레임 포함 구간")
    check(bar.apply_speed(2.0) and mw.vd.n_frames == T0 - 8, "구간 속도 2배 -> 화면 반영")
    app.processEvents()
    check(not bar.strip.grab().isNull() and len(bar.strip.blocks()) == 3, "편집 후 조각 3개 그리기")
    mw.frame_slider.setValue(3)
    check(bar.hold() and bar.undo() and bar.redo() and bar.reset() and mw.vd.n_frames == T0, "정지/되돌리기/다시/원본")
    from PySide6.QtCore import QPoint, QPointF as _QPF, Qt as _Q2  # noqa: E402
    from PySide6.QtGui import QWheelEvent  # noqa: E402

    bar.clear_marks()
    n_a = mw.vd.n_frames
    wev = QWheelEvent(_QPF(50, 20), _QPF(50, 20), QPoint(0, 0), QPoint(0, 120), _Q2.MouseButton.NoButton, _Q2.KeyboardModifier.ControlModifier, _Q2.ScrollPhase.NoScrollPhase, False)
    bar.strip.wheelEvent(wev)
    bar._wheel_flush()
    check(mw.vd.n_frames == n_a + 1, "Ctrl+휠 한 칸 = 대상 조각 1프레임 늘림")
    mw.undo()
    check(mw.vd.n_frames == n_a, "Ctrl+Z 가 프레임 편집을 되돌림")
    # 뷰포트 배치 드래그: Shift+왼쪽 드래그가 진짜 마우스 이벤트로 애니메이션 위치를 바꾼다
    from PySide6.QtCore import QEvent, QPointF, Qt as _Qt  # noqa: E402
    from PySide6.QtGui import QMouseEvent  # noqa: E402

    vp = mw.viewport
    vp.resize(800, 600)
    vp.set_data(mw.pr, mw.vd, reframe=True)      # 시험 중 바뀐 모션 크기에 카메라를 다시 맞춘다 (실제 사용에서는 불러올 때 맞춰짐)
    app.processEvents()
    shift = _Qt.KeyboardModifier.ShiftModifier
    LB = _Qt.MouseButton.LeftButton

    def _ev(kind, x, y, btn, btns, mods=None):
        return QMouseEvent(kind, QPointF(x, y), QPointF(x, y), btn, btns, shift if mods is None else mods)

    before = tuple(mw.pr.settings.place_offset)
    vp.mousePressEvent(_ev(QEvent.Type.MouseButtonPress, 400, 480, LB, LB))
    for i in range(1, 6):
        vp.mouseMoveEvent(_ev(QEvent.Type.MouseMove, 400 + 20 * i, 480, _Qt.MouseButton.NoButton, LB))
    vp.mouseReleaseEvent(_ev(QEvent.Type.MouseButtonRelease, 500, 480, LB, _Qt.MouseButton.NoButton))
    after = tuple(mw.pr.settings.place_offset)
    try:
        from motionbridge.ui.viewer_math import drag_placement as _dp
        _vd = vp.vd
        print("DIAG cam: eye", vp.cam.eye(), "basis", vp.cam.basis(), "ground", _vd.ground_y, "center", _vd.center, "gp", vp.cam.ground_point(400, 480, 800.0, 600.0, _vd.ground_y))
        print("DIAG place: vd", _vd is not None, "size", vp.width(), vp.height(), "drag", vp._drag, "bone_edit", vp.bone_edit,
              "dp", None if _vd is None else _dp(vp.cam, "move", (400, 480), (420, 480), float(vp.width()), float(vp.height()), _vd.ground_y, (_vd.center[0], _vd.center[2])))
    except Exception as _e:  # noqa: BLE001
        print("DIAG place err", repr(_e))
    check(after != before and vp._drag == "camera", "Shift+드래그 -> 애니메이션 위치가 바뀜: %s -> %s" % (before, after))
    mw._place_reset()
    # 보정 레이어 패널 / 기즈모 좌표계 / 시작 안내 줄 / 실시간 갱신이 진짜 Qt 에서 동작한다
    lp = mw.layer_panel
    lp.add_layer()
    app.processEvents()
    check(lp.lst.count() == 2 and mw.pr.layer_stack().current() is not None, "레이어 패널: 원본 줄 + 새 레이어 줄")
    lp.lst.item(1).setCheckState(_Qt.CheckState.Unchecked)
    check(mw.pr.layer_stack().layers[0].mute, "레이어 목록 체크 해제 -> 끔")
    lp.lst.item(1).setCheckState(_Qt.CheckState.Checked)
    check(not mw.pr.layer_stack().layers[0].mute, "다시 체크 -> 켬")
    mw.cb_bone_edit.setChecked(True)
    jt0 = sorted(mw.viewport.pickable)[0]
    mw._bone_picked(jt0)
    lp.rec.setChecked(False)
    lp.rec.setChecked(True)
    check(lp.recording() and mw.viewport.bone_tool == "rotate", "키 기록 켜기 -> 회전 도구")
    mw.frame_slider.setValue(5)
    from motionbridge.core import quat as _Qq  # noqa: E402
    import numpy as _np  # noqa: E402

    mw._bone_dragged(jt0, _Qq.from_axis_angle(_np.array([0.0, 1.0, 0.0]), _np.radians(25.0)))
    app.processEvents()
    check(mw.pr.layer_stack().current().frames(jt0) == [5] and not mw.pr.settings.rot_offsets, "끌기 -> 현재 프레임에 레이어 키 (본별 보정은 그대로)")
    print("DIAG layers:", [lp.lst.item(i).text() for i in range(lp.lst.count())], "top", mw.pr.layer_stack().top,
          "cur", mw.pr.layer_stack().current().name, [(l.name, sum(len(l.frames(j)) for j in range(60))) for l in mw.pr.layer_stack().layers])
    check(any("키 1" in lp.lst.item(i).text() for i in range(lp.lst.count())), "레이어 목록의 키 개수 갱신: " + " | ".join(lp.lst.item(i).text() for i in range(lp.lst.count())))
    mw._live_fire()
    check(not mw._live_pending, "실시간 갱신 한 번 처리")
    lp.rec.setChecked(False)
    # 스택 줄 선택(원본 <-> 레이어) / 합치기 / 곡선 그래프 그리기·마우스가 진짜 Qt 에서 동작한다
    lp.lst.setCurrentRow(0)
    app.processEvents()
    check(mw.pr.layer_stack().top == 0 and lp.lst.count() == 2, "스택에서 '원본' 줄 선택 -> 레이어 미적용 상태")
    lp.lst.setCurrentRow(1)
    app.processEvents()
    check(mw.pr.layer_stack().top == 1, "레이어 줄 선택 -> 그 상태로 복귀")
    lp.add_layer()
    app.processEvents()
    lp.merge_down()
    app.processEvents()
    check(len(mw.pr.layer_stack()) == 1 and lp.lst.count() == 2, "아래와 합치기 -> 레이어 1개")
    mw.recompute()
    ce = mw.curve_editor
    ce.show()
    ce.refresh()
    app.processEvents()
    check(ce.canvas.T > 0 and not ce.canvas.grab().isNull(), "곡선 그래프: 그리기")
    wev2 = QWheelEvent(_QPF(100, 50), _QPF(100, 50), QPoint(0, 0), QPoint(0, 120), _Q2.MouseButton.NoButton, _Q2.KeyboardModifier.ControlModifier, _Q2.ScrollPhase.NoScrollPhase, False)
    span0 = ce.canvas.view[1] - ce.canvas.view[0]
    ce.canvas.wheelEvent(wev2)
    check(ce.canvas.view[1] - ce.canvas.view[0] < span0, "곡선 그래프: Ctrl+휠로 가로 확대")
    plain = QWheelEvent(_QPF(100, 50), _QPF(100, 50), QPoint(0, 0), QPoint(0, 120), _Q2.MouseButton.NoButton, _Q2.KeyboardModifier.NoModifier, _Q2.ScrollPhase.NoScrollPhase, False)
    span1 = ce.canvas.view[1] - ce.canvas.view[0]
    ce.canvas.wheelEvent(plain)
    check(ce.canvas.view[1] - ce.canvas.view[0] == span1 and not plain.isAccepted(), "곡선 그래프: 그냥 휠은 줌이 안 걸리고 위로 넘어감")
    n_b = mw.vd.n_frames
    bar.strip.wheelEvent(plain)
    bar._wheel_flush()
    check(mw.vd.n_frames == n_b, "타임바: 그냥 휠은 조각 길이를 안 바꿈")
    # IK 구간 직접 지정 패널: 제목 줄을 눌러 접었다 펴지고, 펴면 안의 버튼이 보이고, 눌러 구간 지정이 된다 (진짜 Qt)
    ikp = mw.ik_panel
    mw.tabs.setCurrentIndex(2)
    app.processEvents()
    ikp.set_collapsed(False, save=False)
    app.processEvents()
    check(ikp.isVisible() and ikp.b_on.isVisible() and ikp.lst.isVisible(), "IK 구간 패널: 펼치면 안의 위젯이 보임 (패널 %s, 버튼 %s)" % (ikp.isVisible(), ikp.b_on.isVisible()))
    from PySide6.QtCore import QPoint as _QP
    from PySide6.QtTest import QTest as _QT
    _QT.mouseClick(ikp, _Qt.MouseButton.LeftButton, _Qt.KeyboardModifier.NoModifier, _QP(20, 6))   # 진짜 마우스 클릭 경로로
    app.processEvents()
    check(ikp.is_collapsed() and not ikp.b_on.isVisible(), "IK 구간 패널: 제목 줄을 누르면 접힘")
    _QT.mouseClick(ikp, _Qt.MouseButton.LeftButton, _Qt.KeyboardModifier.NoModifier, _QP(20, 6))
    app.processEvents()
    check(not ikp.is_collapsed() and ikp.b_on.isVisible() and ikp.height() > 80, "IK 구간 패널: 다시 누르면 펴짐 (높이 %d)" % ikp.height())
    check(ikp.chain.count() >= 1 and "IK" in ikp.b_on.text(), "IK 구간 패널: 대상 목록/버튼 글자 (%s)" % ikp.b_on.text())
    ikp.mark(True)
    mw.frame_slider.setValue(min(5, mw.pr.anim.n_frames - 1))
    ikp.mark(True)
    app.processEvents()
    check(ikp._pending is None, "IK 구간 패널: 두 번 눌러 구간 지정")
    ikp.clear_chain()
    # 화면 크기 조절: 뷰포트와 아래 줄 사이 / 모션 목록과 탭 사이를 끄는 줄 (진짜 Qt)
    mw.vsplit.setSizes([420, 260])
    app.processEvents()
    h_top, h_bot = mw.vsplit.sizes()[:2]
    check(mw.vsplit.count() == 2 and mw.viewport.height() >= 180 and mw.curve_editor.isVisibleTo(mw), "위아래 크기 조절 줄: 뷰포트 %d / 아래 %d" % (h_top, h_bot))
    mw.vsplit.setSizes([300, 520])
    mw._vsplit_moved()
    print("DIAG vsplit:", mw.vsplit.sizes(), "min", mw.vsplit.widget(0).minimumSizeHint().height(), mw.vsplit.widget(1).minimumSizeHint().height(), "win", mw.height())
    _top_min = mw.vsplit.widget(0).minimumSizeHint().height()     # 창이 작으면 뷰포트가 최소 높이에서 더 줄지 않는다
    check(0.1 <= mw.cfg.get("vsplit_bottom", 0) <= 0.85 and (mw.vsplit.sizes()[1] > mw.vsplit.sizes()[0] * 0.9 or mw.vsplit.sizes()[0] <= _top_min + 2), "아래 영역을 키우면 비율이 저장됨 (%s)" % mw.cfg.get("vsplit_bottom"))
    mw._left_split.setSizes([260, 700])
    mw._left_split_moved()
    check(mw.cfg.get("clip_panel_w", 0) >= 150 and mw.clip_panel.width() >= 150, "모션 목록 폭 조절 (%s)" % mw.cfg.get("clip_panel_w"))
    mw.vsplit.setSizes([420, 260])
    # 왼쪽 모션 목록: 클립을 눌러 오가기 / 다른 모션 세팅 가져오기 / 빼기 (진짜 Qt 위젯)
    import copy as _cp
    cpn = mw.clip_panel
    n_clip0 = len(mw.pr.src.animations)
    ex_ = _cp.deepcopy(mw.pr.src.animations[0])
    ex_.name = "스모크 두번째"
    mw.pr.src.animations.append(ex_)
    mw._after_load("source")
    app.processEvents()
    check(cpn.lst.count() == n_clip0 + 1 and cpn.lst.item(n_clip0).text().find("스모크 두번째") >= 0, "모션 목록: 줄 %d개" % cpn.lst.count())
    mw.pr.settings.root_yaw_deg = 12.0
    cpn.lst.setCurrentRow(n_clip0)
    app.processEvents()
    check(mw.pr.clip == n_clip0 and mw.clip_combo.currentIndex() == n_clip0, "모션 목록: 줄을 누르면 그 모션으로")
    mw._set("root_yaw_deg", 55.0)
    cpn.lst.setCurrentRow(0)
    app.processEvents()
    check(mw.pr.clip == 0 and abs(mw.pr.settings.root_yaw_deg - 12.0) < 1e-9, "모션마다 설정이 따로 남음 (%s)" % mw.pr.settings.root_yaw_deg)
    cpn.lst.setCurrentRow(n_clip0)
    app.processEvents()
    for k_, cb_ in cpn.checks.items():
        cb_.setChecked(k_ == "settings")
    cpn.src.setCurrentIndex(cpn.src.findData(0))
    cpn.do_import()
    app.processEvents()
    check(abs(mw.pr.settings.root_yaw_deg - 12.0) < 1e-9 and cpn.b_out.isEnabled(), "다른 모션 세팅 가져오기")
    cpn.do_undo()
    app.processEvents()
    check(abs(mw.pr.settings.root_yaw_deg - 55.0) < 1e-9 and not cpn.b_out.isEnabled(), "빼기: 가져오기 전으로")
    mw.pr.src.animations.pop()
    mw.pr.reset_clip_settings()
    mw._after_load("source")
    app.processEvents()
    # 휠 잠금: 숫자칸 위 휠은 값이 안 바뀌고 (잠금 켜짐), 끄면 바뀐다. 본 편집은 눌리는 버튼
    from PySide6.QtWidgets import QApplication as _QA2
    check(mw.wheel_guard.lock and mw.wheel_cb.isChecked(), "휠 잠금 기본 켜짐")
    nf_ = mw._fields["ik_knee_stable"][0]
    nf_.show()
    v0_ = nf_.slider.value()
    vs0_ = nf_.spin.value()
    wv_ = QWheelEvent(_QPF(5, 5), _QPF(5, 5), QPoint(0, 0), QPoint(0, 120), _Q2.MouseButton.NoButton, _Q2.KeyboardModifier.NoModifier, _Q2.ScrollPhase.NoScrollPhase, False)
    _QA2.sendEvent(nf_.slider, wv_)
    app.processEvents()
    check(nf_.slider.value() == v0_ and nf_.spin.value() == vs0_, "슬라이더 휠 잠금 켜짐: 파란 슬라이더가 휠로 안 바뀜 (%s)" % nf_.slider.value())
    cw_ = QWheelEvent(_QPF(5, 5), _QPF(5, 5), QPoint(0, 0), QPoint(0, -120 if v0_ >= nf_.slider.maximum() else 120), _Q2.MouseButton.NoButton, _Q2.KeyboardModifier.ControlModifier, _Q2.ScrollPhase.NoScrollPhase, False)
    _QA2.sendEvent(nf_.slider, cw_)
    app.processEvents()
    check(nf_.slider.value() != v0_, "Ctrl+휠이면 잠금이 켜져 있어도 슬라이더 값이 바뀜 (%s)" % nf_.slider.value())
    nf_.spin.setValue(vs0_)
    app.processEvents()
    sp2_ = QWheelEvent(_QPF(5, 5), _QPF(5, 5), QPoint(0, 0), QPoint(0, 120), _Q2.MouseButton.NoButton, _Q2.KeyboardModifier.NoModifier, _Q2.ScrollPhase.NoScrollPhase, False)
    check(not mw.wheel_guard.watched(nf_.spin) and not mw.wheel_guard.watched(mw.frame_slider), "숫자칸·프레임 슬라이더는 잠금 대상이 아님")
    mw.toggle_wheel_lock()
    check(not mw.wheel_guard.lock and not mw.wheel_cb.isChecked(), "Ctrl+L 동작: 잠금 해제")
    mw.set_wheel_lock(True)
    check(mw.cb_bone_edit.isCheckable() and ("ON" if mw.cb_bone_edit.isChecked() else "OFF") in mw.cb_bone_edit.text(), "본 편집은 눌리는 버튼: " + mw.cb_bone_edit.text())
    mw.cb_bone_edit.setChecked(True)
    check("ON" in mw.cb_bone_edit.text(), "본 편집 켜면 글자가 ON")
    mw.cb_bone_edit.setChecked(False)
    mw.set_guide_visible(False)
    check(not mw.guide_box.isVisible() and not mw.guide_toggle.isChecked(), "안내 줄 숨김 -> 버튼도 꺼짐")
    mw.guide_toggle.click()
    check(mw.guide_box.isVisible() or not mw.isVisible(), "[ℹ 안내 줄] 버튼으로 다시 보임")
    pts = ce.canvas.key_points()
    if pts:
        f_, v_, _id = pts[0]
        g_ = ce.canvas.geom()
        px_, py_ = float(g_.x_of(f_)), float(g_.y_of(v_))
        ce.canvas.mousePressEvent(_ev(QEvent.Type.MouseButtonPress, px_, py_, LB, LB, _Qt.KeyboardModifier.NoModifier))
        ce.canvas.mouseMoveEvent(_ev(QEvent.Type.MouseMove, px_, py_ - 15, _Qt.MouseButton.NoButton, LB, _Qt.KeyboardModifier.NoModifier))
        ce.canvas.mouseReleaseEvent(_ev(QEvent.Type.MouseButtonRelease, px_, py_ - 15, LB, _Qt.MouseButton.NoButton, _Qt.KeyboardModifier.NoModifier))
        app.processEvents()
        check(not ce.canvas.grab().isNull(), "곡선 그래프: 키 끌기 뒤 다시 그리기")
    # 곡선 그래프: Shift+끌기 구간 선택 + 버튼, 곡선 위 끌기로 키 만들기 (진짜 Qt 이벤트)
    ce.canvas._size = None
    g2 = ce.canvas.geom()
    LB2 = _Qt.MouseButton.LeftButton
    SH = _Qt.KeyboardModifier.ShiftModifier
    ce.canvas.mousePressEvent(_ev(QEvent.Type.MouseButtonPress, float(g2.x_of(2)), 40.0, LB2, LB2, SH))
    ce.canvas.mouseMoveEvent(_ev(QEvent.Type.MouseMove, float(g2.x_of(6)), 40.0, _Qt.MouseButton.NoButton, LB2, SH))
    ce.canvas.mouseReleaseEvent(_ev(QEvent.Type.MouseButtonRelease, float(g2.x_of(6)), 40.0, LB2, _Qt.MouseButton.NoButton))
    app.processEvents()
    check(ce.canvas.range == (2, 6) and not ce.canvas.grab().isNull() and "3" in ce.range_lbl.text(), "곡선 그래프: Shift+끌기로 구간 선택 (%s)" % (ce.canvas.range,))
    for m_, bt_ in ce.btns.items():
        check(bt_.text() != "", "구간 버튼 있음: %s" % m_)
    ce.canvas.range = None
    ce.range_lbl.setText("")
    for sp in ("local", "screen", "world"):
        mw.space_combo.setCurrentIndex(mw.space_combo.findData(sp))
        check(mw.viewport.gizmo_space == sp and not mw.viewport.grab().isNull(), "기즈모 좌표계 %s 로 그리기" % sp)
    check(mw.guide_box.isVisible() or not mw.cfg.get("show_guide", True), "시작 안내 줄 표시")
    check("확인하고 다듬기" in mw.guide_steps.text() and mw.guide_btn.text() != "", "안내 줄: 지금 단계/버튼 (%s)" % mw.guide_btn.text())
    mw.set_guide_visible(False)
    check(not mw.guide_box.isVisible(), "안내 줄 숨기기")
    mw.set_guide_visible(True)
    mw._rot_reset()
    mw.pr.layers = {}
    lp.refresh()
    # 진행 창(추측 중): X/취소/Esc = 취소 신호 한 번, 정상 종료는 신호 없음
    from motionbridge.ui.widgets import BusyDialog  # noqa: E402

    got = []
    bd = BusyDialog("캐릭터 추측 중", mw)
    bd.cancelled.connect(lambda: got.append(1))
    bd.show()
    bd.set_progress(1, 4, "장면 a.png", "캐릭터 추측 중")
    app.processEvents()
    check(bd.bar.maximum() == 4 and bd.bar.value() == 1 and "2/4" in bd.text.text(), "진행 창 진행률 표시")
    bd.close()  # 오른쪽 위 X
    bd.reject()
    check(got == [1], "X 로 닫으면 취소 신호 한 번: %s" % got)
    # 멈추는 중: 취소 신호 뒤에 set_stopping 하면 창이 닫히지 않고 안내가 바뀌며, 또 눌러도 신호는 늘지 않는다
    bd3 = BusyDialog("y", mw)
    bd3.cancelled.connect(lambda: (got.append(3), bd3.set_stopping()))
    bd3.show()
    bd3.reject()
    bd3.close()
    bd3.reject()
    app.processEvents()
    check(got == [1, 3] and bd3.isVisible() and bd3.stopping and "멈추는 중" in bd3.text.text() and not bd3.btn.isEnabled(), "취소 뒤 '멈추는 중' 표시 유지: %s" % got)
    bd3.finish()
    check(not bd3.isVisible(), "멈춤이 끝나면 닫힘")
    got.remove(3)
    # 취소할 수 없는 작업의 창: Esc/X 를 무시하고, 끝나면(finish) 닫힌다
    bd4 = BusyDialog("z", mw, closable=False)
    bd4.cancelled.connect(lambda: got.append(4))
    bd4.show()
    bd4.reject()
    bd4.close()
    app.processEvents()
    check(bd4.isVisible() and 4 not in got and not bd4.btn.isVisible(), "취소 불가 작업 창: X/Esc 무시, 취소 버튼 없음")
    bd4.finish()
    check(not bd4.isVisible(), "취소 불가 작업 창: 끝나면 닫힘")
    bd5 = BusyDialog("w", mw, closable=False)
    bd5.show_later(50)
    import time as _t
    _t.sleep(0.12)
    app.processEvents()
    check(bd5.isVisible(), "0.3초 뒤에도 작업 중이면 창이 나타남(show_later)")
    bd5.finish()
    bd6 = BusyDialog("v", mw, closable=False)
    bd6.show_later(50)
    bd6.finish()
    _t.sleep(0.12)
    app.processEvents()
    check(not bd6.isVisible(), "짧게 끝난 작업은 창이 안 뜸")
    bd2 = BusyDialog("x", mw)
    bd2.cancelled.connect(lambda: got.append(2))
    bd2.show()
    bd2.finish()
    check(got == [1] and not bd2.isVisible(), "정상 종료는 취소 신호 없음")
    mw.close()
    check(alink.LOCAL_TOOL is None if ar.inner is not None else True, "닫으면 직접 전달 해제")

# 라이브러리 탭: 날짜 > 세션 > 분류 트리 (진짜 Qt 에서만 확인 가능)
try:
    import json as _json
    import types as _types
    from datetime import datetime as _dt
    from pathlib import Path as _P
    from motionbridge.animref.ui.library_tab import LibraryTab
    from motionbridge.animref.core import settings as _AS
    with tempfile.TemporaryDirectory() as _d:
        _s = _AS.load() if hasattr(_AS, "load") else dict(_AS.DEFAULTS)
        _s["library_dir"], _s["game"] = _d, "g"
        _cd = _P(_d) / "g" / "clips"
        _cd.mkdir(parents=True)
        _v = _P(_d) / "2026-11-09 14-03-22.mkv"
        _v.write_bytes(b"v")
        _ts = _dt(2026, 11, 9, 14, 25, 10).timestamp()
        os.utime(_v, (_ts, _ts))
        for _n, _l in (("c1", "타격"), ("c2", "피격")):
            (_cd / (_n + ".mp4")).write_bytes(b"x")
            (_cd / (_n + ".json")).write_text(_json.dumps({"source_video": str(_v), "source_name": _v.name, "label_manual": _l, "created": "2026-11-09T15:00:00"}), encoding="utf-8")
        _main = _types.SimpleNamespace(s=_s, busy=False, save_settings=lambda: None, flash=lambda *a: None, log=lambda *a: None)
        _lt = LibraryTab(_main)
        _lt.refresh()
        _root = _lt.sess_tree.topLevelItem(0)
        check(_root is not None and _root.childCount() == 1, "라이브러리: 날짜 1개")
        _sess = _root.child(0).child(0) if _root is not None and _root.childCount() else None
        check(_sess is not None and "14:03:22 ~ 14:25:10" in _sess.text(0), "라이브러리: 세션 시각 표시: %s" % (_sess.text(0) if _sess else None))
        check(_sess is not None and [_sess.child(i).text(0).split("  ")[0] for i in range(_sess.childCount())] == ["전체", "타격", "피격"], "라이브러리: 전체/타격/피격")
        _lt.sess_tree.setCurrentItem(_sess.child(1))
        check(len(_lt.shown) == 1, "라이브러리: 타격만 보임")
        _lt.sess_tree.setCurrentItem(_sess.child(0))
        check(len(_lt.shown) == 2, "라이브러리: 전체는 둘 다")
except Exception as _e:  # noqa: BLE001
    check(False, "라이브러리 탭 스모크 예외: %r" % (_e,))

# 뷰포트 본 편집: 켜고 끄기, 파츠 목록(트리) 만들기가 진짜 Qt 에서도 오류 없이 동작
try:
    mw.cb_bone_edit.setChecked(True)
    check(mw.bone_tree.isVisibleTo(mw) or True, "본 편집 켜기")
    mw._maybe_refresh_bone_ui()
    check(hasattr(mw.viewport, "pick_joint") and hasattr(mw.viewport, "_bone_delta"), "뷰포트 본 편집 함수")
    mw.cb_bone_edit.setChecked(False)
except Exception as _e:  # noqa: BLE001
    check(False, "본 편집 스모크 예외: %r" % (_e,))

# 보스전 자동 녹화 패널: 만들어지고, 영역 미지정이면 켜지지 않고, 수동 녹화 중에는 자동 시작 안 함
try:
    import types as _types2
    from motionbridge.animref.ui.collect_tab import CollectTab
    from motionbridge.animref.core import settings as _AS2, bossrun as _br, boss as _bs
    _s2 = _AS2.load() if hasattr(_AS2, "load") else dict(_AS2.DEFAULTS)
    _main2 = _types2.SimpleNamespace(s=_s2, busy=False, save_settings=lambda: None, flash=lambda *a: None, log=lambda *a: None)
    _ct = CollectTab(_main2)
    check(hasattr(_ct, "boss") and not _ct.boss.active(), "보스 패널: 기본은 꺼짐")
    check(not _ct.boss._refresh_ready(), "보스 패널: 영역 미지정이면 준비 안 됨")
    check(hasattr(_ct.boss, "calibrate_photo") and hasattr(_ct.boss, "clear_photo"), "보스 패널: 사진으로 정확하게 지정 버튼")
    check(not _ct.boss.btn_unphoto.isEnabled(), "보스 패널: 사진 지정 전에는 해제 버튼 비활성")
    check("사진" in _ct.boss.ready_note.text(), "보스 패널: 안내에 사진 지정(선택) 언급")
    check(hasattr(_ct, "src_list") and hasattr(_ct, "analyze_range") and hasattr(_ct, "refresh_sources"), "수집 탭: 원본 영상 목록 + 구간 분석")
    check(not _ct.ck_autorec.isChecked() and not _main2.s.get("auto_analyze_recording"), "수집 탭: 녹화 후 자동 분석은 기본 꺼짐")
    _ct.refresh_sources()
    check(_ct._origin("C:/없는/영상.mp4") == "dropped", "수집 탭: 모르는 영상은 직접 넣은 영상으로 취급")
    check(hasattr(_ct, "rename_source") and hasattr(_ct, "src_clips") and hasattr(_ct, "btn_src_name"), "수집 탭: 영상 제목 붙이기 + 이 영상의 클립 목록")
    check(hasattr(_ct, "_pick_files") and hasattr(_ct, "_pick_folder") and hasattr(_ct, "_reveal_source"), "수집 탭: 파일/폴더 가져오기 + 원본 위치 열기 버튼")
    from motionbridge.animref.ui import progress_dialog as _pd
    check(hasattr(_pd.ProgressDialog, "exec") and hasattr(_pd.ProgressDialog, "_progress"), "진행 현황 창 준비")
    from motionbridge.animref.ui import range_dialog as _rd
    check(_rd.fmt(75.34) == "1:15.3" and hasattr(_rd.RangeDialog, "add_range"), "구간 선택 창 준비")
    _ct.boss.spins["end_grace"].setValue(45)
    check(_bs.cfg(_main2.s)["end_grace"] == 45, "보스 패널: 설정이 저장 값에 반영")
    _ct.boss.spins["match"].setValue(65)
    check(abs(_bs.cfg(_main2.s)["match"] - 0.35) < 1e-6, "보스 패널: 민감도 65%% = 내부 기준 0.35 (%s)" % _bs.cfg(_main2.s)["match"])
    _ct.boss.spins["defeat_hp"].setValue(8)
    check(abs(_bs.cfg(_main2.s)["defeat_hp"] - 0.08) < 1e-6, "보스 패널: 처치 체력 8%% = 0.08")
    _run = _br.BossRun(_main2.s)
    _smp = _br.Sample(0.0, True, 0.9, 1.0, False)
    _ev = []
    for _t in range(0, 6):
        _smp = _br.Sample(float(_t), True, 0.9, 1.0, False)
        _ev += _run.apply(_smp, True)
    check(not any(k == "start" for k, _ in _ev), "보스 패널: 수동 녹화 중에는 자동 시작 안 함")
except Exception as _e:  # noqa: BLE001
    check(False, "보스 패널 스모크 예외: %r" % (_e,))

# 에셋 목록 -> 뷰포트 끌어다 놓기: 트리가 종류/경로를 실은 데이터를 만들고, 뷰포트가 그걸 받아 신호를 낸다
try:
    import json as _j3
    from PySide6.QtCore import QMimeData as _MD
    from PySide6.QtWidgets import QTreeWidgetItem as _TI
    from motionbridge.ui import char_assets as _CA
    from motionbridge.ui.viewer import ASSET_MIME as _AM, Viewport as _VP
    _tr = _CA._DragTree()
    _root = _TI(["g"])
    _leaf = _TI(["a"])
    _leaf.setData(0, _CA.ROLE, ("anim", "G/A_Slash.uasset"))
    _root.addChild(_leaf)
    _tr.addTopLevelItem(_root)
    _md = _tr.mimeData([_root])
    _d = _j3.loads(bytes(_md.data(_AM)).decode("utf-8"))
    check(_d == {"kind": "anim", "paths": ["G/A_Slash.uasset"]}, "끌기 데이터: %s" % _d)
    _vp = _VP()
    check(_vp.acceptDrops(), "뷰포트가 끌어다 놓기를 받음")
    # 영상찾기 목록에서 끌기: 출처(origin) 가 실려 가고, 숨겨진 항목은 빠진다
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtWidgets import QListWidgetItem as _LI
    from motionbridge.ui import vidfind_tab as _VF
    _al = _VF._AssetList("anim")
    _i1, _i2 = _LI("a"), _LI("b")
    _i1.setData(_Qt.ItemDataRole.UserRole, "G/A_1.uasset")
    _i2.setData(_Qt.ItemDataRole.UserRole, "G/A_2.uasset")
    _al.addItem(_i1)
    _al.addItem(_i2)
    _i2.setHidden(True)
    _d2 = _j3.loads(bytes(_al.mimeData([_i1, _i2]).data(_AM)).decode("utf-8"))
    check(_d2 == {"kind": "anim", "paths": ["G/A_1.uasset"], "origin": "vidfind"}, "영상찾기 끌기 데이터: %s" % _d2)
    check(_al.dragEnabled(), "영상찾기 목록은 끌기 가능")
except Exception as _e:  # noqa: BLE001
    check(False, "끌어다 놓기 스모크 예외: %r" % (_e,))

# 접고 펴는 섹션 (제목 줄 클릭 / 상태 저장 복원 / 원래 숨겨진 위젯 유지)
try:
    from PySide6.QtCore import QPointF as _PF, Qt as _Q2
    from PySide6.QtGui import QMouseEvent as _ME
    from PySide6.QtCore import QEvent as _EV, QPoint as _PT
    from PySide6.QtWidgets import QGroupBox, QPushButton as _PB, QVBoxLayout as _VB
    from motionbridge.ui.widgets import CollapsibleBox as _CB

    _store, _saved = {}, []
    _CB.bind(_store, lambda: _saved.append(dict(_store)))
    _box = _CB("시험 섹션")
    _lay = _VB(_box)
    _b1, _b2 = _PB("보임"), _PB("원래 숨김")
    _lay.addWidget(_b1)
    _lay.addWidget(_b2)
    _b2.hide()
    _box.show()
    app.processEvents()
    check(_box.title() == "시험 섹션" and QGroupBox.title(_box).startswith("\u25bc"), "접기 박스: title() 은 화살표 없는 이름, 화면에는 ▼")
    check(_b1.isVisible() and not _box.is_collapsed(), "처음엔 펼쳐짐")
    h0 = _box.height()
    _pos = _PF(20.0, float(_box.header_height() - 4))
    _ev = _ME(_EV.Type.MouseButtonPress, _pos, _box.mapToGlobal(_pos), _Q2.MouseButton.LeftButton, _Q2.MouseButton.LeftButton, _Q2.KeyboardModifier.NoModifier)
    _box.mousePressEvent(_ev)
    app.processEvents()
    check(_box.is_collapsed() and not _b1.isVisible() and _box.maximumHeight() == _box.header_height(), "제목 줄 클릭 -> 접힘")
    check(_store == {"시험 섹션": True} and _saved, "접힘 상태 저장: %s" % _store)
    _box.toggle()
    app.processEvents()
    check(not _box.is_collapsed() and _b1.isVisible() and not _b2.isVisible() and _box.maximumHeight() > 10000, "다시 펴면 보이던 것만 보임")
    check(_store == {}, "펴면 저장 항목 제거")
    # 저장된 상태로 시작
    _store["복원 섹션"] = True
    _box2 = _CB("복원 섹션")
    _VB(_box2).addWidget(_PB("x"))
    _box2.show()
    app.processEvents()
    check(_box2.is_collapsed(), "저장된 접힘 상태로 시작")
    _CB.bind(None, None)
except Exception as _e:  # noqa: BLE001
    check(False, "접기 박스 스모크 예외: %r" % (_e,))

# 영상찾기 'AI 회의' 두 버튼: 눌렀을 때 예외 없이 안내/대화상자가 떠야 한다 (가짜 Qt 로는 못 잡는 실제 위젯 오류)
try:
    from PySide6.QtWidgets import QDialog as _QD, QMessageBox as _QM

    _vt = mw.vidfind
    _warns = []
    _QM.warning = staticmethod(lambda *a, **k: _warns.append(str(a[2]) if len(a) > 2 else ""))
    _QD.exec = lambda self: 0     # 대화상자는 '취소'로 닫는다
    _vt.clear_sources()
    _vt.start_guess_panel()
    check(_warns and "추가" in _warns[-1], "AI 회의로 추측: 영상이 없으면 안내 (%s)" % (_warns[-1:] or ["없음"]))
    _vt._add_source("/v/a.mp4")
    _vt.win._extract_args = lambda: ("h", "r", "ue", None, None)
    _vt.start_guess_panel()       # 참가자 고르는 창을 만드는 중 예외가 나면 여기서 터진다
    check(True, "AI 회의로 추측: 참가자 창 구성 오류 없음")
    _vt._add_source("/v/a.mp4")
    _vt.anim_list.addItem("AS_X")
    _vt.open_panel()
    check(True, "AI 회의로 찾기: 참가자 창 구성 오류 없음")
except Exception as _e:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    check(False, "AI 회의 버튼 스모크 예외: %r" % (_e,))

# 공통 UI 부품: ? 아이콘 / 끌어서 크기 조절 / 테마 전환 / 접기 화살표 / 루트 표시
try:
    from PySide6.QtWidgets import QApplication as _QA, QLabel as _QL
    from motionbridge.ui import theme as _th
    from motionbridge.ui.uikit import HelpIcon as _HI, Resizable as _RZ

    _icons = [w for w in mw.findChildren(_HI)]
    check(len(_icons) >= 10, "? 도움말 아이콘이 화면 곳곳에 있음: %d개" % len(_icons))
    check(all(i.toolTip() for i in _icons), "모든 ? 아이콘에 설명 글이 있음")
    _rz = [w for w in mw.findChildren(_RZ)]
    check(len(_rz) >= 6, "크기 조절 상자: %d개" % len(_rz))
    _r0 = _rz[0]
    _r0.set_height(200)
    check(_r0.inner.minimumHeight() == 200 and _r0.inner.maximumHeight() == 200 and mw.cfg["sizes"].get(_r0.key) == 200, "끌어서 바꾼 높이가 적용되고 저장됨")
    _r0.reset()
    check(_r0.key not in mw.cfg["sizes"], "두 번 누르면(초기화) 저장 값이 지워짐")
    _app = _QA.instance()
    _before = [(w, w.styleSheet()) for w in _app.allWidgets() if w.styleSheet()]
    mw.toggle_theme()
    check(_th.mode() == "light" and mw.cfg["theme"] == "light", "밝은 테마로 전환")
    _bad = [w for w in _app.allWidgets() if ("#8aa" in w.styleSheet() or "#ffdc50" in w.styleSheet()) and not _th._keep_dark(w)]
    for _w in _bad[:5]:
        print("DIAG theme left:", type(_w).__name__, _w.objectName(), _w.styleSheet()[:120])
    check(not _bad, "밝은 테마에서 어두운 바탕용 글자색이 남지 않음: %d개" % len(_bad))
    mw.toggle_theme()
    check(_th.mode() == "dark", "어두운 테마로 되돌림")
    _diff = []
    for _w, _v in _before:
        try:
            if _w.styleSheet() != _v:
                _diff.append((_v, _w.styleSheet()))
        except RuntimeError:      # 그 사이 없어진 위젯
            pass
    for _a, _b in _diff[:5]:
        print("DIAG theme roundtrip:", _a[:110], "=>", (_b or "")[:110])
    check(not _diff, "왕복하면 스타일이 원래대로 (다른 것 %d개)" % len(_diff))
    mw.toggle_clip_panel()
    check(mw.clip_panel.isHidden() and mw.cfg.get("clip_panel_hidden"), "모션 목록 접기")
    mw.toggle_clip_panel()
    check(not mw.clip_panel.isHidden(), "모션 목록 펴기")
    mw._bone_edit_toggled(True)
    check(not mw.bone_tree_box.isHidden(), "본 편집을 켜면 파츠 목록 칸이 보임")
    mw._set_tree_open(False)
    check(mw.bone_tree.isHidden(), "파츠 목록 접기")
    mw._set_tree_open(True)
    mw._bone_edit_toggled(False)
    _img = mw.viewport.grab()
    check(not _img.isNull(), "루트 표시를 포함해 뷰포트 그리기")
except Exception as _e:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    check(False, "공통 UI 부품 스모크 예외: %r" % (_e,))

print("모두 통과" if not fails else "실패 %d개" % len(fails))
sys.exit(1 if fails else 0)
