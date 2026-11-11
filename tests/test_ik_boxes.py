"""IK 직접 지정 UX: 부위를 고르면 본이 자동 선택(+본 편집 켜짐), 그래프의 박스를 Ctrl+끌기로 옮기고/늘이고, Delete 로 지우고, 시작 표시선이 보인다."""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qt_stub  # noqa: E402

qt_stub.install()
import test_pipeline as TP  # noqa: E402
from qt_stub import QFileDialog  # noqa: E402
from motionbridge.ui import app as A  # noqa: E402


def main():
    with tempfile.TemporaryDirectory() as d:
        os.environ["HOME"] = d
        from motionbridge.core import bridge
        bridge.CONFIG_PATH = os.path.join(d, "cfg.json")
        src, _s2, tgt, _a, _b = TP.make_inputs(d)
        mw = A.MainWindow()
        QFileDialog.open_names_ret = ([src], "")
        mw.open_source()
        QFileDialog.open_ret = (tgt, "")
        mw.open_target()
        mw.recompute()
        ip = mw.ik_panel
        ip.refresh()
        assert not mw.cb_bone_edit.isChecked()
        labs = [ip.chain.itemData(i) for i in range(ip.chain.count())]
        lab = labs[0]
        ch = ip._chain_by_label(lab)
        # 1) 부위를 고르면 그 끝 본이 골라지고 본 편집이 켜진다
        ip.chain.setCurrentIndex(1)
        ip.chain.setCurrentIndex(0)
        assert mw.cb_bone_edit.isChecked(), "본 편집 자동 켜짐"
        assert mw.viewport.selected_joint == int(ch.foot), (mw.viewport.selected_joint, ch.foot)
        # 2) 다른 본을 직접 골라도 되고, 그 본이 다른 체인이면 대상이 따라간다 / 체인 밖 본이면 대상은 그대로
        other = next(c for c in ip.app.pr.chains() if c.label != lab)
        mw._select_bone(int(other.foot))
        assert mw.viewport.selected_joint == int(other.foot) and ip._label() == other.label
        mw._select_bone(int(ch.foot))
        assert ip._label() == lab
        # 3) 구간 두 번 찍기 -> 박스, 시작만 찍으면 표시선
        mw.viewport.frame = 5
        ip.mark(True)
        cv = mw.curve_editor.canvas
        cv._size = (400, 200)
        mw.curve_editor.refresh()
        assert cv.ik_pending is not None and cv.ik_pending[0] == 5, cv.ik_pending
        gm = cv.geom()
        # 표시선을 Ctrl+끌기로 옮김
        cv.press_at(float(gm.x_of(5)), 50.0, ctrl=True)
        cv.drag_to(float(gm.x_of(8)), 50.0)
        cv.release()
        assert ip.pending_frame(lab) == 8, ip.pending_frame(lab)
        mw.viewport.frame = 14
        ip.mark(True)
        assert ip.boxes(lab) == [[8, 14, True]], ip.boxes(lab)
        mw.curve_editor.refresh()
        assert cv.ik_boxes == [[8, 14, True]] and cv.ik_pending is None
        ya, yb = cv._box_rect_y()
        ym = (ya + yb) / 2.0
        # 4) 그냥 클릭은 선택만 (Ctrl 없이 끌어도 안 움직임) / Ctrl+가운데 끌기 = 통째 이동
        cv.press_at(float(gm.x_of(11)), ym)
        assert cv.ik_sel == 0
        cv.drag_to(float(gm.x_of(15)), ym)
        cv.release()
        assert ip.boxes(lab) == [[8, 14, True]], "Ctrl 없이는 이동하지 않음"
        cv.press_at(float(gm.x_of(11)), ym, ctrl=True)
        cv.drag_to(float(gm.x_of(14)), ym)
        cv.release()
        assert ip.boxes(lab) == [[11, 17, True]], ip.boxes(lab)
        # 5) 가장자리 Ctrl+끌기 = 늘이기/줄이기
        mw.curve_editor.refresh()
        cv.press_at(float(gm.x_of(17.5)), ym, ctrl=True)
        cv.drag_to(float(gm.x_of(21)), ym)
        cv.release()
        assert ip.boxes(lab) == [[11, 21, True]], ip.boxes(lab)
        mw.curve_editor.refresh()
        cv.press_at(float(gm.x_of(10.5)), ym, ctrl=True)
        cv.drag_to(float(gm.x_of(13)), ym)
        cv.release()
        assert ip.boxes(lab) == [[13, 21, True]], ip.boxes(lab)
        # 6) Delete 로 지우기
        mw.curve_editor.refresh()
        cv.press_at(float(gm.x_of(17)), ym)
        assert cv.ik_sel == 0

        class _Ev:
            def __init__(self, k):
                self._k = k

            def key(self):
                return self._k

        from PySide6.QtCore import Qt
        cv.keyPressEvent(_Ev(Qt.Key.Key_Delete))
        assert ip.boxes(lab) == [], ip.boxes(lab)
        print("test_ik_boxes ALL OK")


main()
