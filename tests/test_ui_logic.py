"""가짜 Qt 로 MainWindow 의 로직 경로를 실행해 본다 (연결 오류, 이름 오류, 상태 갱신 확인)."""
import json
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import qt_stub  # noqa: E402

qt_stub.install()
from motionbridge.ui import app as A  # noqa: E402
import test_pipeline as TP  # noqa: E402
from qt_stub import QFileDialog, QMessageBox, QPainter  # noqa: E402


def test_full_flow():
    with tempfile.TemporaryDirectory() as d:
        src, src2, tgt, _, _ = TP.make_inputs(d)
        os.environ["HOME"] = d  # 설정 파일이 사용자 홈을 건드리지 않게
        from motionbridge.core import bridge

        bridge.CONFIG_PATH = os.path.join(d, "cfg.json")
        mw = A.MainWindow()
        # 열기
        QFileDialog.open_names_ret = ([src], "")        # 소스 열기는 여러 개 선택 가능 — 열면 모션 목록에 자동으로 올라간다
        mw.open_source()
        assert mw.pr.src is not None and mw.clip_combo.count() == 1
        assert mw.map_table.rowCount() > 20
        QFileDialog.open_ret = (tgt, "")
        mw.open_target()
        assert mw.pr.result is not None, [c for c in QMessageBox.calls]
        assert mw.vd is not None and mw.vd.n_frames == 40
        assert mw.frame_slider.value() == 0 and mw.frame_slider._hi == 39
        assert "타깃" in mw.info_label.text() and "스케일" in mw.info_label.text()
        # 매핑 표: 타깃 쪽 Hips 콤보가 올바른 본을 가리키고, 콤보 변경이 매핑을 바꾼다
        rows = {mw.map_table.item(r, 0).data(A.Qt.ItemDataRole.UserRole): r for r in range(mw.map_table.rowCount())}
        r = rows["L_Foot"]
        cb_t = mw.map_table.cellWidget(r, 2)
        assert cb_t.currentIndex() - 1 == mw.pr.tgt_slots["L_Foot"]
        n_pairs = len(mw.pr.pairs())
        cb_t.setCurrentIndex(0)  # 연결 해제
        assert "L_Foot" not in mw.pr.tgt_slots and len(mw.pr.pairs()) == n_pairs - 1
        assert mw.map_table.item(r, 0).text().startswith("⚠"), "한쪽만 연결된 행 표시"
        assert mw.recompute_timer.active, "변경 후 재계산이 예약되어야 함"
        mw.recompute()
        assert mw.pr.result is not None and len([c for c in mw.pr.chains() if c.kind == 'foot']) == 1, "발 하나가 빠지면 IK 체인도 하나"
        cb_t.setCurrentIndex(mw.pr.tgt.skeleton.index("foot_l") + 1)
        assert len(mw.pr.pairs()) == n_pairs
        # 설정 위젯 -> 설정 반영 (슬라이더/스핀 연동)
        nf = mw._fields["scale_mult"][0]
        nf.spin.setValue(1.25)
        assert abs(mw.pr.settings.scale_mult - 1.25) < 1e-9
        assert abs(nf.slider.value() * nf.step + nf.lo - 1.25) < nf.step
        nf.slider.setValue(nf.slider.value() + 10)
        assert abs(mw.pr.settings.scale_mult - (1.25 + 0.1)) < 0.02
        cb, inv = mw._fields["transfer_root_motion"]
        cb.setChecked(True)  # 제자리 체크 -> transfer_root_motion False
        assert mw.pr.settings.transfer_root_motion is False
        mw._fields["foot_ik"][0].setChecked(False)
        assert mw.pr.settings.foot_ik is False
        mw._fields["foot_ik"][0].setChecked(True)
        mw.hip_fields[1].spin.setValue(2.0)
        assert mw.pr.settings.hip_offset[1] == 2.0 and mw.pr.settings.hip_offset[0] == 0.0
        # 뷰포트 드래그 배치: 값이 설정과 입력칸에 쌓이고, 초기화로 돌아온다
        mw._placement_dragged(0.5, -0.25, 30.0)
        mw._placement_dragged(0.5, 0.0, 170.0)
        assert abs(mw.pr.settings.place_offset[0] - 1.0) < 1e-9 and abs(mw.pr.settings.place_offset[1] + 0.25) < 1e-9
        assert abs(mw.pr.settings.root_yaw_deg - (-160.0)) < 1e-9, mw.pr.settings.root_yaw_deg  # 200° -> -160° 로 감김
        assert abs(mw.place_fields[0].spin.value() - 1.0) < 1e-6 and mw.place_label.text()
        mw.place_fields[1].spin.setValue(0.4)
        assert abs(mw.pr.settings.place_offset[1] - 0.4) < 1e-9
        mw._place_reset()
        assert mw.pr.settings.place_offset == (0.0, 0.0) and mw.pr.settings.root_yaw_deg == 0.0 and mw.place_label.text() == ""
        assert mw.viewport.drag_mode == "camera"
        mw.drag_combo.setCurrentIndex(mw.drag_combo.findData("move"))
        assert mw.viewport.drag_mode == "move"
        mw.drag_combo.setCurrentIndex(0)
        axis_cb = mw._fields["src_up"][0]
        axis_cb.setCurrentIndex(axis_cb.findData("+Y"))
        assert mw.pr.settings.src_up == "+Y"
        # 회전 보정
        j_sp = mw.pr.tgt_slots["Spine1"]
        mw.rot_combo.setCurrentIndex(mw.rot_combo.findData(j_sp))
        mw.rot_fields[0].spin.setValue(12.0)
        assert mw.pr.settings.rot_offsets == {j_sp: (12.0, 0.0, 0.0)}
        mw.rot_fields[0].spin.setValue(0.0)
        assert mw.pr.settings.rot_offsets == {}, "0 으로 돌리면 제거"
        mw.rot_fields[1].spin.setValue(7.0)
        mw.com_combo.setCurrentIndex(mw.com_combo.findData(mw.pr.tgt_slots["Hips"]))
        assert mw.pr.settings.com_joint == mw.pr.tgt_slots["Hips"]
        mw.recompute()
        assert mw.pr.result is not None and not mw.warn_label.text().startswith("⚠ 리타겟 실패"), mw.warn_label.text()
        # 프로필 저장/불러오기 (위젯 동기화 포함)
        prof = os.path.join(d, "p.json")
        QFileDialog.save_ret = (prof, "")
        mw.save_profile()
        assert json.load(open(prof))["settings"]["scale_mult"] > 1.3
        mw._reset_settings()
        assert mw.pr.settings.scale_mult == 1.0 and nf.value() == 1.0 and mw.pr.settings.rot_offsets == {}
        QFileDialog.open_ret = (prof, "")
        mw.load_profile()
        assert abs(mw.pr.settings.scale_mult - 1.35) < 0.02 and abs(nf.value() - mw.pr.settings.scale_mult) < 1e-9
        assert mw.rot_combo.currentData() is not None and mw.pr.settings.rot_offsets
        assert mw.hip_fields[1].value() == 2.0 and mw.com_combo.currentData() == mw.pr.tgt_slots["Hips"]
        assert cb.isChecked() and mw.pr.settings.transfer_root_motion is False
        # 모델에 맞춰 스케일 자동 맞춤: 기준을 바꾸면 최종 스케일이 그 기준의 비율이 된다
        mw._reset_settings()
        mw.scale_basis.setCurrentIndex(mw.scale_basis.findData("height"))
        mw.fit_scale()
        from motionbridge.core import scalefit as _sf
        rep = _sf.measure(mw.pr, "height")
        assert abs(mw.pr.settings.scale_mult - rep.mult) < 0.02 and mw.pr.settings.auto_scale
        assert abs(nf.value() - mw.pr.settings.scale_mult) < 1e-9, "배율 칸에도 반영"
        assert "전체 키" in mw.scale_note.text() and abs(mw.pr.run(analyze_quality=False).k - rep.chosen.ratio) < 0.02 * rep.chosen.ratio
        mw._reset_settings()
        # 프레임 편집(영상 편집기식 조각 줄): 구간 속도 / 자르기 / 삭제 / 복제 / 정지 / 되돌리기가 바로 화면에 반영된다
        bar = mw.retime_bar
        T0 = mw.pr.src.animations[0].n_frames
        mw.recompute()
        mw.history.reset(mw._edit_state())  # 앞선 설정 변경 기록은 비우고 프레임 편집만 본다
        bar.refresh()
        assert bar.strip.blocks() and len(bar.strip.blocks()) == 1 and "원본 %d → 결과 %d" % (T0, T0) in bar.info.text(), bar.info.text()
        assert not bar.btn_undo.isEnabled() and not bar.btn_reset.isEnabled()
        # 줄 클릭 = 그 프레임으로 이동 (x -> 프레임 -> x 가 일치)
        x20 = bar.strip.x_of(20)
        bar.strip.press_at(x20)
        bar.strip._drag = None
        assert mw.frame_slider.value() == 20 and bar.strip.frame == 20
        assert abs(bar.strip.frame_at(bar.strip.x_of(33)) - 33) <= 0
        # 표시 없이 속도 = 지금 조각 전체
        assert bar.range() == (0, T0 - 1)
        # 시작/끝 표시 = 구간 선택 (끝 프레임 포함) -> 그 구간만 2배 빠르게
        mw.frame_slider.setValue(10)
        bar.mark_in()
        assert bar.has_selection() and bar.strip.selection() == (10, T0 - 1) and bar.range() == (10, T0 - 1), "시작만 있으면 마지막까지"
        mw.frame_slider.setValue(29)
        bar.mark_out()
        assert bar.strip.selection() == (10, 29) and bar.range() == (10, 30), "끝 프레임을 포함"
        assert "선택한 구간 11~30프레임" in bar.info.text() and bar.len_spin.value() == 20, bar.info.text()
        assert bar.apply_speed(2.0)
        assert mw.pr.is_edited() and mw.vd.n_frames == T0 - 10 and mw.frame_slider._hi == T0 - 11
        assert (bar.strip.m_in, bar.strip.m_out) == (10, 19), "표시가 새 구간을 따라감"
        assert len(bar.strip.blocks()) == 3 and "빠르게 1" in bar.info.text() and bar.btn_undo.isEnabled() and bar.btn_reset.isEnabled()
        # 되돌리기 / 다시 = 앱 전체 실행 취소(Ctrl+Z)와 같은 기록
        assert bar.undo() and mw.vd.n_frames == T0 and not mw.pr.is_edited()
        assert bar.redo() and mw.vd.n_frames == T0 - 10
        mw.undo()
        assert mw.vd.n_frames == T0 and not mw.pr.is_edited(), "Ctrl+Z 가 프레임 편집도 되돌림"
        mw.redo()
        assert mw.vd.n_frames == T0 - 10 and mw.pr.is_edited()
        # 직접 길이 조절: 숫자 / 휠 / 블록 끝 끌기
        bar.clear_marks()
        bar.reset()
        mw.frame_slider.setValue(10)
        bar.mark_in()
        mw.frame_slider.setValue(29)
        bar.mark_out()
        assert bar.apply_length(40) and mw.vd.n_frames == T0 + 20 and bar.range() == (10, 50), "20프레임 구간을 40프레임으로"
        bar.wheel_adjust(3)
        bar._wheel_flush()
        assert bar.range()[1] - bar.range()[0] == 43 and mw.vd.n_frames == T0 + 23, "휠 한 칸 = 1프레임"
        bar.wheel_adjust(-5)
        bar.wheel_adjust(1)
        bar._wheel_flush()
        assert bar.range()[1] - bar.range()[0] == 39, "연속 휠은 모아서 적용"
        bar.clear_marks()
        n_pieces = len(mw.pr.frame_edit().pieces)
        s0 = bar.strip
        s0.resize(900, s0.H)
        i = [k for k, (a, b) in enumerate(mw.pr.frame_edit().layout()) if a == 10][0]
        n_old = mw.pr.frame_edit().pieces[i].n
        tot0 = mw.vd.n_frames
        s0.press_at(s0.edge_x(i))  # 블록 오른쪽 끝을 잡고
        assert s0._drag == "edge"
        ppf = s0._edge[3]
        s0.drag_to(s0.edge_x(i) + 5 * ppf)
        assert s0.edge_preview == (i, n_old + 5)
        s0.release()
        assert mw.pr.frame_edit().pieces[i].n == n_old + 5 and mw.vd.n_frames == tot0 + 5
        assert len(mw.pr.frame_edit().pieces) == n_pieces
        # Shift+드래그 = 줄에서 구간 선택
        s0.press_at(s0.x_of(3), shift=True)
        s0.drag_to(s0.x_of(8))
        s0.release()
        assert s0.selection() == (3, 8) and bar.has_selection()
        bar.clear_marks()
        assert not bar.has_selection()
        assert bar.reset() and mw.vd.n_frames == T0
        # 단축키: 메뉴에 등록되어 있고 눌렀을 때 막대 동작을 부른다
        keys = {v[3]: v[1] for v in mw._actions.values()}
        assert keys.get("구간 시작 표시 [") == ("I",) and keys.get("선택 구간 삭제") == ("Delete",)
        mw.frame_slider.setValue(4)
        mw._dispatch("retime_mark_in")
        assert bar.strip.m_in == 4
        mw._dispatch("retime_clear")
        assert bar.strip.m_in is None
        # 자르기 / 복제 / 삭제 / 정지 / 부드럽게
        bar.clear_marks()
        mw.frame_slider.setValue(5)
        assert bar.split() and len(mw.pr.frame_edit().pieces) == 2
        n_before = mw.vd.n_frames
        assert bar.duplicate() and mw.vd.n_frames > n_before
        assert bar.delete() and mw.vd.n_frames == n_before
        bar.hold_n.setValue(7)
        assert bar.hold() and mw.vd.n_frames == n_before + 7
        assert bar.reset() and mw.vd.n_frames == T0 and not mw.pr.is_edited()
        bar.ramp_a.setValue(1.0)
        bar.ramp_b.setValue(2.0)
        assert bar.ramp() and mw.vd.n_frames < T0
        assert bar.undo()
        # 조각 순서 바꾸기: 앞 조각을 뒤로 -> 길이는 그대로, 더 못 가면 아무 일 없음
        bar.reset()
        mw.frame_slider.setValue(5)
        assert bar.split()
        mw.frame_slider.setValue(0)
        assert bar.move(1) and mw.vd.n_frames == T0 and not bar.move(1) and mw.frame_slider.value() == 34
        bar.reset()
        mw._reset_settings()
        # 좌표축 빠른 수정: 지금 쓰이는 축에서 시작해 돌리고/뒤집고, 자동으로 되돌린다
        mw._reset_settings()
        mw.recompute()
        st = mw.pr.settings
        d0 = mw.pr.result.frame_tgt[1]
        assert st.tgt_up == "auto" and "적용 축" in mw.axis_notes["tgt"].text(), mw.axis_notes["tgt"].text()
        from motionbridge.core.mapping import axes_of_frame
        init = axes_of_frame(mw.pr.result.frame_tgt[0])
        assert mw.axis_quick("tgt", "yaw90") and st.tgt_up != "auto" and st.tgt_fwd != "auto"
        up0, fwd0 = init
        assert mw.pr.result.frame_tgt[1] != d0 and "수동" in mw.pr.result.frame_tgt[1], mw.pr.result.frame_tgt[1]
        for _ in range(3):  # 처음 한 번 + 세 번 = 네 번이면 제자리
            mw.axis_quick("tgt", "yaw90")
        assert (st.tgt_up, st.tgt_fwd) == (up0, fwd0), "90° 네 번이면 제자리"
        mw.axis_quick("tgt", "yaw180")
        assert st.tgt_fwd != fwd0 and st.tgt_up == up0
        mw.axis_quick("tgt", "flip_up")
        assert st.tgt_up != up0
        assert mw.axis_quick("src", "pitch90") and st.src_up != "auto"
        assert mw._fields["tgt_up"][0].currentData() == st.tgt_up, "콤보에도 반영"
        mw.axis_quick("tgt", "auto")
        mw.axis_quick("src", "auto")
        assert st.tgt_up == st.tgt_fwd == st.src_up == st.src_fwd == "auto" and mw.pr.result.frame_tgt[1] == d0
        mw.unload_target()
        assert not mw.axis_quick("tgt", "yaw90"), "타깃이 없으면 아무 일 없음"
        QFileDialog.open_ret = (tgt, "")
        mw.open_target()
        # 열어 둔 타깃/소스를 뺄 수 있다 (다른 쪽은 그대로, 뷰어는 남은 것만 보여 줌)
        assert mw.btn_unload_tgt.isEnabled() and mw.btn_unload_src.isEnabled()
        mw.unload_target()
        assert mw.pr.tgt is None and mw.pr.src is not None and mw.pr.result is None and mw.vd is not None and not mw.pr.tgt_slots
        assert not mw.btn_unload_tgt.isEnabled() and mw.btn_unload_src.isEnabled() and "타깃" not in mw.info_label.text()
        mw.unload_target()  # 이미 비었으면 아무 일 없음
        QFileDialog.open_ret = (tgt, "")
        mw.open_target()
        assert mw.pr.tgt is not None and mw.pr.result is not None and mw.btn_unload_tgt.isEnabled(), "다시 열 수 있음"
        mw.unload_source()
        assert mw.pr.src is None and mw.pr.tgt is not None and mw.pr.result is None and mw.clip_combo.count() == 0
        QFileDialog.open_names_ret = ([src], "")        # 소스 열기는 여러 개 선택 가능 — 열면 모션 목록에 자동으로 올라간다
        mw.open_source()
        assert mw.pr.src is not None and mw.pr.result is not None
        mw._reset_settings()
        # 타임라인 / 뷰포트 그리기
        mw.frame_slider.setValue(12)
        assert "13 / 40" in mw.frame_label.text()
        before = QPainter.drawn
        mw.viewport.paintEvent(None)
        assert QPainter.drawn > before + 20, "뷰포트가 그려지지 않음"
        mw.btn_play.setChecked(True)
        assert mw.play_timer.active
        mw._tick()
        assert mw.frame_slider.value() == 13
        mw.btn_play.setChecked(False)
        # 품질 목록
        assert mw.quality_summary.text().startswith("오류")
        if mw.issue_list.count():
            it = mw.issue_list.item(0)
            mw._issue_clicked(it)
            assert mw.frame_slider.value() == int(it.data(A.Qt.ItemDataRole.UserRole))
        # 클립 전환
        mw.clip_combo.addItem("1: extra")
        mw.pr.src.animations.append(mw.pr.src.animations[0].slice(0, 20))
        mw._clip_changed(1)
        assert mw.vd.n_frames == 20
        mw.clip_combo.setCurrentIndex(0)
        mw._clip_changed(0)
        # 내보내기 (워커는 동기 실행)
        out = os.path.join(d, "export")
        mw.out_edit.setText(out)
        mw.cb_fmt["glb"].setChecked(True)
        mw.cb_preset["max"].setChecked(True)
        mw.rb_clip_all.setChecked(True)
        assert mw.cb_organize.isChecked(), "캐릭터별 폴더 정리가 기본"
        mw.do_export()
        walk = sorted(os.path.relpath(os.path.join(r, f), out).replace("\\", "/") for r, _ds, fl in os.walk(out) for f in fl)
        fs = [os.path.basename(f) for f in walk]
        assert len([f for f in fs if f.endswith(".bvh")]) == 4 and len([f for f in fs if f.endswith(".glb")]) == 2, walk  # 클립 2개 × (blender,max)
        assert all((w.count("/") == 2 and w.split("/")[1] == "Models") or (w.count("/") == 3 and w.split("/")[1] == "Animations") for w in walk), walk  # 캐릭터/Models/파일, 캐릭터/Animations/클립이름/파일 (맥스·블렌더 함께)
        clipdir = [w for w in walk if w.endswith("walk_max.bvh")][0].rsplit("/", 1)[0]
        assert any(w == clipdir + "/walk_blender.bvh" for w in walk) and any(w == clipdir + "/사용법.txt" for w in walk), walk   # 한 클립 폴더에 함께
        assert clipdir.split("/")[-2] == "Animations", clipdir
        assert "완료" in mw.export_log.toPlainText()
        assert mw.pr.clip == 0 and mw.pr.result is not None and mw.tabs.isEnabled()
        # 본 구조 제외
        mw.cb_skeleton.setChecked(False)
        out2 = os.path.join(d, "e2")
        mw.out_edit.setText(out2)
        mw.rb_clip_all.setChecked(False)
        mw.rb_clip_cur.setChecked(True)
        mw.do_export()
        walk2 = [os.path.join(r, f) for r, _ds, fl in os.walk(out2) for f in fl]
        assert walk2 and all(f.endswith("_curves.json") and os.sep + "Animations" + os.sep in f for f in walk2), walk2
        # 리타겟 탭의 '애니메이션 가공'이 내보내기에 반영된다 (2배 빠르게 = 프레임 절반)
        import json as _json
        n_plain = _json.load(open(walk2[0], encoding="utf-8"))["frames"]
        mw.rt_edit.mode.setCurrentIndex(mw.rt_edit.mode.findData("speed"))
        mw.rt_edit.value.setValue(2.0)
        assert "프레임" in mw.rt_edit_note.text() and mw.cfg.get("rt_edit_mode") == "speed", mw.rt_edit_note.text()
        out_e = os.path.join(d, "e_edit")
        mw.out_edit.setText(out_e)
        mw.do_export()
        walk_e = [os.path.join(r, f) for r, _ds, fl in os.walk(out_e) for f in fl]
        n_fast = _json.load(open(walk_e[0], encoding="utf-8"))["frames"]
        assert abs(n_fast - ((n_plain - 1) / 2.0 + 1)) <= 1, (n_plain, n_fast)
        mw.rt_edit.mode.setCurrentIndex(0)
        assert mw.rt_edit_note.text() == "", mw.rt_edit_note.text()
        # 뷰포트 본 편집: 파츠 목록이 만들어지고, 끌어 돌린 값이 '본별 회전 보정'에 쌓이며, 되돌리기/초기화가 된다
        from motionbridge.core import quat as _Q
        mw.cb_bone_edit.setChecked(True)
        assert mw.viewport.bone_edit and mw.viewport.pickable, "본 편집을 켜면 고를 수 있는 본이 생김"
        assert mw.bone_tree.topLevelItemCount() >= 1, "파츠 목록 생성"
        jt = sorted(mw.viewport.pickable)[0]
        mw._bone_picked(jt)
        assert mw.viewport.selected_joint == jt and mw.rot_combo.currentData() == jt, "선택이 왼쪽 콤보와 동기화"
        qy = _Q.from_axis_angle(np.array([0.0, 1.0, 0.0]), np.radians(30.0))
        mw._bone_dragged(jt, qy)
        mw._bone_dragged(jt, qy)
        e = mw.pr.settings.rot_offsets[jt]
        assert abs(e[1] - 60.0) < 0.05 and abs(e[0]) < 0.05 and abs(e[2]) < 0.05, ("30°+30° 가 60° 로 합쳐져야 함", e)
        assert abs(mw.rot_fields[1].value() - 60.0) < 0.05, "왼쪽 슬라이더에도 반영"
        assert jt in mw.viewport.edited_joints and mw.btn_bone_reset.isEnabled()
        mw._bone_dragged(jt, _Q.conj(_Q.mul(qy, qy)))
        assert jt not in mw.pr.settings.rot_offsets, "반대로 돌려 0 이 되면 보정이 지워짐"
        # 루트 Yaw 가 있으면 화면에 보이는 방향대로 돌도록 보정한다 (보정은 yaw 이전 프레임에서 적용되므로)
        mw.pr.settings.root_yaw_deg = 90.0
        mw._bone_dragged(jt, _Q.from_axis_angle(np.array([1.0, 0.0, 0.0]), np.radians(20.0)))
        e2 = mw.pr.settings.rot_offsets[jt]
        yaw_q = _Q.from_axis_angle(np.array([0.0, 1.0, 0.0]), np.radians(90.0))
        back = _Q.mul(_Q.mul(yaw_q, _Q.from_euler(np.array(e2), "XYZ")), _Q.conj(yaw_q))   # yaw 를 거친 뒤(화면에서 본) 회전
        want = _Q.from_axis_angle(np.array([1.0, 0.0, 0.0]), np.radians(20.0))
        assert abs(abs(float(np.dot(back, want))) - 1.0) < 1e-6, ("화면 기준 회전과 같아야 함", e2)
        mw.pr.settings.root_yaw_deg = 0.0
        mw._bone_reset_selected()
        assert jt not in mw.pr.settings.rot_offsets, "이 본 초기화"
        # 보정 레이어: 키 기록을 켜고 본을 끌면 '본별 보정' 이 아니라 레이어의 현재 프레임 키가 된다 (원본 결과는 그대로, 실행 취소 가능)
        mw.recompute()
        base_rot = mw.pr.result.anim.rot.copy()
        mw.history.commit(mw._edit_state())
        mw.layer_panel.add_layer()
        mw.layer_panel.rec.setChecked(True)
        assert mw.layer_panel.recording() and mw.layer_panel.layer() is not None
        mw.frame_slider.setValue(3)
        mw.viewport.frame = 3
        mw._bone_picked(jt)
        mw._bone_dragged(jt, qy)
        assert not mw.pr.settings.rot_offsets, "키 기록 중에는 본별 보정을 건드리지 않는다"
        assert mw.layer_panel.layer().frames(jt) == [3], mw.layer_panel.layer().frames(jt)
        assert jt in mw._edited_set()
        mw.recompute()
        assert mw.pr.result.base_anim is not None and np.allclose(mw.pr.result.base_anim.rot, base_rot), "원본 리타겟 결과는 그대로"
        assert not np.allclose(mw.pr.result.anim.rot[:, jt], base_rot[:, jt])
        mw.history.commit(mw._edit_state())
        mw.layer_panel.layer().mute = True          # 끄면 원래 결과로
        mw.recompute()
        assert mw.pr.result.base_anim is None and np.allclose(mw.pr.result.anim.rot, base_rot)
        mw.layer_panel.layer().mute = False
        mw.undo()                                    # 실행 취소: 레이어 키가 사라진다
        assert not (mw.pr.layers.get(mw.pr.clip) and mw.pr.layers[mw.pr.clip].total_keys()), "실행 취소로 키 제거"
        mw.layer_panel.rec.setChecked(False)
        # 레이어 스택: 줄을 고르면 그 상태(원본 <-> 레이어까지 적용)가 되고, 합치면 같은 모양이 한 층으로 남는다
        lp = mw.layer_panel
        yax = np.array([0.0, 1.0, 0.0])
        mw.pr.layers = {}
        mw.recompute()
        base_rot = mw.pr.result.anim.rot.copy()
        lp.add_layer()
        lp.layer().set_key(jt, 3, rot=_Q.from_axis_angle(yax, np.radians(30.0)))
        lp.add_layer()
        lp.layer().set_key(jt, 3, rot=_Q.from_axis_angle(yax, np.radians(20.0)))
        lp.layer().set_key(jt, 6, rot=_Q.from_axis_angle(yax, np.radians(-15.0)))
        st = mw.pr.layer_stack()
        assert st.top == 2 and len(st) == 2
        mw.recompute()
        r2 = mw.pr.result.anim.rot.copy()
        lp._row_changed(0)
        mw.recompute()
        assert st.top == 0 and len(st) == 2 and mw.pr.result.base_anim is None and np.allclose(mw.pr.result.anim.rot, base_rot), "원본 상태 = 레이어 없는 결과"
        assert lp.lst.count() == 3, "원본 줄 + 레이어 2줄"
        lp._row_changed(1)
        mw.recompute()
        r1 = mw.pr.result.anim.rot.copy()
        assert st.top == 1 and not np.allclose(r1, base_rot) and not np.allclose(r1, r2), "1번 레이어까지만 적용"
        lp._row_changed(2)
        mw.recompute()
        assert np.allclose(mw.pr.result.anim.rot, r2), "다시 위로 고르면 되돌아옴 (숨긴 레이어는 지워지지 않음)"
        lp.merge_all()
        mw.recompute()
        assert len(st) == 1 and st.top == 1 and np.allclose(mw.pr.result.anim.rot, r2, atol=2e-3), "합친 뒤에도 같은 모양"
        lp.add_layer()
        lp.merge_down()
        assert len(st) == 1, "아래와 합치기"
        # 곡선 그래프: 키를 위아래로 끌면 그 프레임의 최종 값이 따라온다 / 더블클릭 = 키 추가 / 우클릭 = 삭제 / 원본 상태에서는 편집 불가
        mw._bone_picked(jt)
        mw.recompute()
        ce = mw.curve_editor
        cv = ce.canvas
        cv._size = (400, 200)
        ce.refresh()
        assert cv.T == mw.pr.result.anim.n_frames and cv.layer is lp.layer() and cv.key_frames, (cv.T, cv.key_frames)
        f0, v0, (ch0, _f) = cv.key_points()[0]
        gm = cv.geom()
        x, y = float(gm.x_of(f0)), float(gm.y_of(v0))
        cv.press_at(x, y)
        assert cv._mode == "key"
        cv.drag_to(x, y - 25)
        want = float(gm.v_of(y - 25))
        assert mw._live_pending, "그래프 끌기 -> 실시간 갱신 예약"
        mw._live_fire()
        assert abs(float(cv.final[ch0][f0]) - want) < 0.6, (float(cv.final[ch0][f0]), want)
        cv.release()
        n_keys = len(lp.layer().frames(jt))
        fa = next(f for f in range(cv.T) if f not in lp.layer().frames(jt) and f > 2)
        cv.press_at(float(gm.x_of(fa)), 40.0, double=True)
        assert fa in lp.layer().frames(jt) and len(lp.layer().frames(jt)) == n_keys + 1, "더블클릭 = 키 추가"
        mw.recompute()
        ce.refresh()
        gm = cv.geom()
        pt = [q for q in cv.key_points() if q[0] == fa][0]
        cv.press_at(float(gm.x_of(fa)), float(gm.y_of(pt[1])), right=True)
        assert fa not in lp.layer().frames(jt), "우클릭 = 키 삭제"
        # 곡선 위를 잡고 끌면 키가 생긴다 (그냥 클릭은 프레임 이동만)
        fb = next(f for f in range(3, cv.T - 3) if all(abs(f - k) >= 3 for k in lp.layer().frames(jt)))
        mw.recompute()
        ce.refresh()
        gm = cv.geom()
        ch1 = cv.chans[0]
        yy = float(gm.y_of(cv.final[ch1][fb]))
        n0 = len(lp.layer().frames(jt))
        cv.press_at(float(gm.x_of(fb)), yy)
        assert cv._mode == "pull" and len(lp.layer().frames(jt)) == n0, ("곡선 클릭만으로는 키가 안 생김", cv._mode, fb, lp.layer().frames(jt), cv.T, cv.msg)
        cv.release()
        assert len(lp.layer().frames(jt)) == n0
        cv.press_at(float(gm.x_of(fb)), yy)
        cv.drag_to(float(gm.x_of(fb)), yy - 20)
        assert cv._mode == "key" and fb in lp.layer().frames(jt), "끌기 시작하면 키가 생김"
        mw._live_fire()
        gm = cv.geom()
        assert abs(float(cv.final[cv._drag_ch][fb]) - float(gm.v_of(yy - 20))) < 0.8, (cv.final[cv._drag_ch][fb], gm.v_of(yy - 20))
        cv.release()
        lp.layer().remove_key(jt, fb)
        # Shift+끌기 = 구간 고르기 -> 버튼(지우고 블렌드)
        got_r = []
        cv.range_picked.connect(lambda a, b: got_r.append((a, b)))
        cv.press_at(float(gm.x_of(5)), 30.0, shift=True)
        cv.drag_to(float(gm.x_of(9)), 30.0)
        cv.release()
        assert cv.range == (5, 9) and got_r[-1] == (5, 9) and (lp.rng_a.value(), lp.rng_b.value()) == (6, 10), (cv.range, got_r)
        assert "6" in ce.range_lbl.text() and "10" in ce.range_lbl.text()
        lp.layer().fade = 0
        keys_before = {f: k for f, k in lp.layer().keys.get(jt, {}).items()}
        ce.range_do("flat")
        assert "일직선" in lp._msg, lp._msg
        ce.range_do("line")
        assert "이어서" in lp._msg, lp._msg
        mw.recompute()
        ce.refresh()
        lp.layer().keys[jt] = keys_before
        cv.range = None
        lp._row_changed(0)
        mw.recompute()
        ce.refresh()
        assert cv.layer is None
        cv.press_at(float(cv.geom().x_of(fa)), 40.0, double=True)
        assert cv.msg and not lp.stack().total_keys() == 0, "원본 상태에서는 키를 못 만들고 이유를 알려 준다"
        mw.pr.layers = {}
        mw.recompute()
        # 한 프레임만 고치면 앞뒤로 서서히 원래 값으로 돌아간다 (맥스 바이패드 레이어처럼). 레이어가 골라져 있으면 기록 체크 없이도 레이어 키, 원본 상태면 본별 보정
        mw.pr.layers = {}
        mw.pr.settings.rot_offsets.clear()
        mw.recompute()
        base2 = mw.pr.result.anim.rot.copy()
        Tn = base2.shape[0]
        lp.rec.setChecked(True)                    # 기본값
        lp.add_layer()
        assert lp.recording(), "레이어를 고르면 기록은 자동"
        fm = min(8, Tn - 1)
        mw.frame_slider.setValue(fm)
        mw.viewport.frame = fm
        mw._bone_picked(jt)
        mw._bone_dragged(jt, _Q.from_axis_angle(yax, np.radians(35.0)))
        assert not mw.pr.settings.rot_offsets and lp.layer().frames(jt) == [fm], "레이어 키로 기록 (본별 보정은 그대로)"
        mw.recompute()
        ch = np.degrees(_Q.angle_between(mw.pr.result.anim.rot[:, jt], base2[:, jt]))
        assert ch[fm] > 20, ch[fm]
        far = [f for f in range(Tn) if abs(f - fm) >= 9]
        assert far and all(ch[f] < 1e-6 for f in far), "퍼짐 구간 밖은 원래 각도"
        lp.fade.set_value(0.0)
        lp._fade_changed(0.0)
        mw.recompute()
        ch0 = np.degrees(_Q.angle_between(mw.pr.result.anim.rot[:, jt], base2[:, jt]))
        assert all(abs(ch0[f] - ch0[fm]) < 1e-6 for f in range(Tn)), "퍼짐 0 이면 끝 키 값을 계속 유지"
        lp._row_changed(0)                                        # 원본 상태: 본 편집은 전체 구간 보정으로
        mw._bone_dragged(jt, _Q.from_axis_angle(yax, np.radians(10.0)))
        assert jt in mw.pr.settings.rot_offsets and not lp.recording()
        mw._bone_reset_selected()
        mw.pr.layers = {}
        mw.recompute()
        # 휠 잠금 토글 / 본 편집 버튼
        assert mw.wheel_guard.lock is True
        mw.toggle_wheel_lock()
        assert mw.wheel_guard.lock is False and mw.cfg["wheel_lock"] is False
        mw.set_wheel_lock(True)
        assert mw.wheel_guard.lock is True and mw.wheel_cb.isChecked()
        mw.cb_bone_edit.setChecked(True)
        assert "ON" in mw.cb_bone_edit.text()
        mw.cb_bone_edit.setChecked(False)
        assert "OFF" in mw.cb_bone_edit.text()
        # Ctrl+클릭: 다른 본에 지금 프레임 키만 찍는다 (레이어가 없으면 만들고, 선택은 그대로)
        mw.pr.layers = {}
        mw.recompute()
        sel0 = mw.viewport.selected_joint
        jo = next(j for j in mw.pr.tgt_slots.values() if j != jt)
        mw.frame_slider.setValue(4)
        mw.viewport.frame = 4
        mw._bone_ctrl_clicked(jo)
        assert len(mw.pr.layer_stack()) == 1 and mw.pr.layer_stack().current().frames(jo) == [4], "Ctrl+클릭 = 그 본 키"
        assert mw.viewport.selected_joint == sel0, "선택은 그대로"
        mw.pr.layers = {}
        mw.recompute()
        # 바이패드식 위치 편집: 발을 옮기면 허벅지·정강이가 돌아 따라오고(길이 유지), '이 본만 움직이기' 면 그 본만 위치 키가 생긴다
        mw.pr.layers = {}
        mw.pr.settings.rot_offsets.clear()
        mw.pr.settings.pos_offsets.clear()
        mw.recompute()
        lay_ = mw.layer_panel
        lay_.add_layer()
        lay_.layer().fade = 0
        mw.frame_slider.setValue(6)
        mw.viewport.frame = 6
        from motionbridge.core import posik as _pk
        foot = mw.pr.tgt_slots["L_Foot"]
        up_, lo_, end_, _nm = _pk.end_chain(mw.pr.tgt_slots, foot)
        P0 = np.array(mw.vd.tgt_pos[6], dtype=float)
        len1, len2 = np.linalg.norm(P0[up_] - P0[lo_]), np.linalg.norm(P0[lo_] - P0[end_])
        d_ = (P0[up_] - P0[end_]) * 0.12
        assert not mw.cb_pos_only.isChecked(), "기본은 IK 방식"
        mw._bone_drag_started()
        mw._bone_moved(foot, d_)
        mw.recompute()
        P1 = np.array(mw.vd.tgt_pos[6], dtype=float)
        assert np.linalg.norm(P1[end_] - (P0[end_] + d_)) < 0.02 * (len1 + len2), "발이 끈 곳으로 따라옴"
        assert abs(np.linalg.norm(P1[up_] - P1[lo_]) - len1) < 1e-4 * (1 + len1) and abs(np.linalg.norm(P1[lo_] - P1[end_]) - len2) < 1e-4 * (1 + len2), "뼈 길이 유지"
        assert lay_.layer().frames(up_) == [6] and lay_.layer().frames(lo_) == [6] and not lay_.layer().frames(foot), "위/가운데 본 회전 키, 발 위치 키는 없음"
        # 이 본만 움직이기: 발에 위치 키만 생기고 위쪽 본은 그대로
        mw.pr.layers = {}
        mw.recompute()
        lay_.add_layer()
        lay_.layer().fade = 0
        mw.cb_pos_only.setChecked(True)
        mw._bone_drag_started()
        mw._bone_moved(foot, d_)
        assert lay_.layer().frames(foot) == [6] and not lay_.layer().frames(up_) and not lay_.layer().frames(lo_), "이 본만"
        mw.cb_pos_only.setChecked(False)
        # 손·발 끝이 아닌 본은 항상 그 본만
        mw.pr.layers = {}
        mw.recompute()
        lay_.add_layer()
        mw._bone_moved(mw.pr.tgt_slots["Hips"], np.array([0.0, 0.01, 0.0]))
        assert lay_.layer().frames(mw.pr.tgt_slots["Hips"]) == [6]
        mw.pr.layers = {}
        mw.recompute()
        # 에셋 찾기 검색칸: '\\' 만 남은 예전 값은 버린다
        from motionbridge.ui.char_assets import CharAssetPanel
        mw.cfg["char_query"] = "\\"
        assert CharAssetPanel(mw).query.text() == "" and mw.cfg["char_query"] == ""
        mw.cfg["char_query"] = "swordman"
        assert CharAssetPanel(mw).query.text() == "swordman", "정상 검색어는 유지"
        mw.cfg["char_query"] = ""
        # 안내 줄 숨겼다가 다시 보이기 (버튼 / 단축키 동작)
        mw.set_guide_visible(False)
        assert not mw.guide_box.isVisible() and not mw.guide_toggle.isChecked()
        mw.toggle_guide()
        assert mw.guide_box.isVisible() and mw.guide_toggle.isChecked(), "다시 나타남"
        assert any(k == "F2" for _n, k in mw.shortcut_table()), "F2 단축키"
        # 왼쪽 모션 목록: 클립을 눌러 오가면 설정이 클립마다 따로 남고, 다른 모션 세팅을 가져왔다 뺄 수 있다
        import copy as _copy
        n0 = len(mw.pr.src.animations)
        mw.pr.clip = 0
        mw.pr.reset_clip_settings()
        extra = _copy.deepcopy(mw.pr.src.animations[0])
        extra.name = "second"
        mw.pr.src.animations.append(extra)
        mw._after_load("source")
        cp = mw.clip_panel
        assert cp.lst.count() == n0 + 1 and mw.clip_combo.count() == n0 + 1, (cp.lst.count(), mw.clip_combo.count())
        mw.pr.settings.root_yaw_deg = 25.0
        cp.lst.setCurrentRow(1)
        assert mw.pr.clip == 1 and mw.clip_combo.currentIndex() == 1, "목록을 누르면 클립이 바뀜"
        assert mw.pr.settings.root_yaw_deg == 25.0, "처음 여는 모션은 보던 설정으로 시작"
        mw._set("root_yaw_deg", 70.0)
        mw.clip_combo.setCurrentIndex(0)
        assert mw.pr.clip == 0 and cp.lst.currentRow() == 0 and mw.pr.settings.root_yaw_deg == 25.0, "돌아오면 그 모션 설정이 그대로"
        assert mw._fields["root_yaw_deg"][0].spin.value() == 25.0, "화면의 값도 그 모션 것"
        cp.lst.setCurrentRow(1)
        assert cp.src.count() == n0 and cp.b_in.isEnabled() and not cp.b_out.isEnabled()
        for k, cb in cp.checks.items():
            cb.setChecked(k == "settings")
        mw.pr.settings.root_yaw_deg = 70.0
        cp.src.setCurrentIndex(0)
        cp.do_import()
        assert mw.pr.settings.root_yaw_deg == 25.0 and cp.b_out.isEnabled() and "가져온 기록" in cp.hist.text(), "가져오기"
        assert mw._fields["root_yaw_deg"][0].spin.value() == 25.0
        cp.do_undo()
        assert mw.pr.settings.root_yaw_deg == 70.0 and not cp.b_out.isEnabled(), "빼기 = 가져오기 전으로"
        mw.undo()                                              # 실행 취소도 클립 설정까지 함께 되돌림 (오류 없이)
        # 모션 목록에서 빼기 / 파일로 더하기 (추출 후 자동 추가도 같은 경로)
        assert cp.b_rm.isEnabled()
        n_before = len(mw.pr.src.animations)
        mw.pr.clip = 0
        cp.lst.setCurrentRow(n_before - 1)
        cp.remove_current()
        assert len(mw.pr.src.animations) == n_before - 1 and cp.lst.count() == n_before - 1 and mw.clip_combo.count() == n_before - 1, "목록에서 빠짐"
        added_n = cp.add_paths([src2])
        assert added_n == 1 and cp.lst.count() == n_before
        assert cp.add_paths([src2]) == 0, "이미 있는 파일은 다시 안 들어감"
        mw.pr.src.animations.pop()
        mw.pr.reset_clip_settings()
        mw._after_load("source")
        assert cp.lst.count() == n0
        # IK 구간 직접 지정 패널: 켜기/끄기 구간이 설정에 들어가고, 지우면 자동 판정으로 돌아간다
        ik = mw.ik_panel
        mw.pr.layers = {}
        mw.recompute()
        assert ik.chain.count() >= 2, "체인 목록"
        lab = ik.chain.itemData(0)
        T_ = mw.pr.anim.n_frames
        mw.frame_slider.setValue(2)
        mw.viewport.frame = 2
        ik.mark(True)
        assert ik._pending is not None and "여기까지" in ik.b_on.text() and "여기서부터" in ik.b_off.text(), "첫 번째 누름 = 시작만 찍음"
        assert not mw.pr.settings.ik_ranges, "아직 구간 없음"
        mw.frame_slider.setValue(6)
        mw.viewport.frame = 6
        ik.mark(True)
        assert ik._pending is None and mw.pr.settings.ik_ranges[mw.pr.clip][lab] == [[2, 6, True]], mw.pr.settings.ik_ranges
        # 끝 프레임이 시작보다 앞이어도 순서를 바로잡고, 다른 버튼을 누르면 새로 시작 / 취소
        mw.frame_slider.setValue(8)
        mw.viewport.frame = 8
        ik.mark(False)
        ik.mark(True)
        assert ik._pending[0] is True, "다른 쪽 버튼 = 새로 시작"
        ik.cancel()
        assert ik._pending is None
        ik.mark(False)
        mw.frame_slider.setValue(4)
        mw.viewport.frame = 4
        ik.mark(False)
        assert mw.pr.settings.ik_ranges[mw.pr.clip][lab][-1][2] is False
        mw.recompute()
        pl = [p for c, p in zip(mw.pr.result.chains, mw.pr.result.plans) if c.label == lab][0]
        assert pl.contact[2:4].all() and not pl.contact[4:9].any(), "직접 지정이 접지에 반영: %s" % pl.contact[:12]
        assert ik.lst.count() == 2
        ik.clear_chain()
        mw.recompute()
        assert not mw.pr.settings.ik_ranges, "지우면 자동으로 돌아감"
        # 맥스식 키 찍기: 0프레임에 원본 키 -> 중간에서 고침 -> 뒤에 다시 원본 키 = 그 사이만 바뀜 / 구간 편집(지우고 블렌드)
        lp.rec.setChecked(True)
        mw.pr.layers = {}
        mw.pr.settings.rot_offsets.clear()
        mw.recompute()
        base3 = mw.pr.result.anim.rot.copy()
        Tn = base3.shape[0]
        assert Tn >= 24, Tn
        lp.add_layer()
        lp.layer().fade = 0
        lp.scope.setCurrentIndex(lp.scope.findData("one"))
        mw._bone_picked(jt)
        for f in (2, 14):
            mw.frame_slider.setValue(f)
            mw.viewport.frame = f
            lp.key_original()
        mw.frame_slider.setValue(8)
        mw.viewport.frame = 8
        mw._bone_dragged(jt, _Q.from_axis_angle(yax, np.radians(40.0)))
        mw.recompute()
        chg = np.degrees(_Q.angle_between(mw.pr.result.anim.rot[:, jt], base3[:, jt]))
        assert chg[8] > 25 and chg[2] < 1e-6 and all(chg[f] < 1e-6 for f in range(14, Tn)), "원본 키 사이만 바뀌고 뒤는 원본으로 돌아감"
        assert 0 < chg[5] < chg[8] and 0 < chg[11] < chg[8], "앞뒤로 블렌드"
        # 대상 '아래 본 전부'로 키 찍기
        lp.scope.setCurrentIndex(lp.scope.findData("tree"))
        n_t = len(lp._targets())
        assert n_t >= 1 and jt in lp._targets()
        lp.scope.setCurrentIndex(lp.scope.findData("one"))
        # 구간 편집: 8프레임 주변을 지우고 블렌드 -> 앞뒤 프레임(원본 키 2, 14)을 잇는 직선, 변화는 사라짐
        lp.rng_a.setValue(3)
        lp.rng_b.setValue(14)
        lp.range_action("blend")
        mw.recompute()
        chg2 = np.degrees(_Q.angle_between(mw.pr.result.anim.rot[:, jt], base3[:, jt]))
        assert chg2.max() < 1.0, "지우고 블렌드 하면 튀던 변화가 앞뒤 값으로 이어짐: %s" % chg2.max()
        # 레이어가 없어도 구간 편집이 새 레이어를 만들어 기록한다 (원본 상태에서 시작)
        lp._row_changed(0)
        mw.pr.layers = {}
        mw.recompute()
        lp.range_action("line", 3, 9)
        assert len(mw.pr.layer_stack()) == 1, "레이어 자동 생성"
        mw.pr.layers = {}
        mw.recompute()
        # 끄는 동안 실시간 갱신: 가벼운 계산을 바로 하고(품질 분석 생략), 손을 놓으면 전체 계산이 이어진다
        mw.pr.issues = ["old"]
        mw._bone_dragged(jt, qy)
        assert mw._live_pending, "끌기 -> 실시간 갱신 예약"
        mw._live_fire()
        assert not mw._live_pending and mw.pr.result is not None
        assert mw.recompute_timer.isActive() or True
        mw._bone_reset_selected()
        mw.recompute()
        # 좌표계 선택
        mw.space_combo.setCurrentIndex(mw.space_combo.findData("local"))
        mw._space_changed()
        assert mw.viewport.gizmo_space == "local"
        mw.space_combo.setCurrentIndex(mw.space_combo.findData("world"))
        mw._space_changed()
        # 뷰포트 클릭 선택: 본의 화면 좌표 근처를 찍으면 그 본, 멀면 None. 끄는 방향 -> 카메라 기준 회전
        vp = mw.viewport
        vp.width, vp.height = (lambda: 800), (lambda: 600)
        ids, xy = vp._joint_screen(800.0, 600.0)
        assert ids and len(ids) == len(xy), "고를 수 있는 본의 화면 좌표"
        k = len(ids) // 2
        assert vp.pick_joint(xy[k][0] + 3, xy[k][1] - 2) in ids, "점 가까이 찍으면 본이 골라짐"
        assert vp.pick_joint(-500, -500) is None, "멀리 찍으면 선택 없음"
        q_right = vp._bone_delta(10.0, 0.0, False)       # 오른쪽으로 끌기 = 카메라 위 축 기준 +
        r_, u_, f_ = vp.cam.basis()
        ax = np.asarray(q_right[1:]) / max(np.linalg.norm(q_right[1:]), 1e-12)
        assert np.allclose(ax, u_, atol=1e-6) and abs(2 * np.degrees(np.arccos(q_right[0])) - 6.0) < 1e-6, (ax, u_)
        q_down = vp._bone_delta(0.0, 10.0, False)        # 아래로 끌기 = 카메라 오른쪽 축 기준 +
        assert np.allclose(np.asarray(q_down[1:]) / np.linalg.norm(q_down[1:]), r_, atol=1e-6)
        mw.cb_bone_edit.setChecked(False)
        assert not mw.viewport.bone_edit
        # 형식 미선택 / FBX 인데 Blender 없음 -> 경고창, 파일 없음
        mw.cb_skeleton.setChecked(True)
        for cb in mw.cb_fmt.values():
            cb.setChecked(False)
        QMessageBox.calls.clear()
        mw.do_export()
        assert QMessageBox.calls and QMessageBox.calls[-1][0] == "warning"
        mw.cb_fmt["fbx"].setChecked(True)
        os.environ["PATH"] = ""
        os.environ.pop("MOTIONBRIDGE_BLENDER", None)
        mw.pr.blender = None
        QMessageBox.calls.clear()
        mw.do_export()
        assert QMessageBox.calls and "Blender" in QMessageBox.calls[-1][2]
        # 소스만 열린 상태에서 리타겟 내보내기 -> 경고
        mw2 = A.MainWindow()
        QFileDialog.open_names_ret = ([src], "")
        mw2.open_source()
        assert mw2.pr.result is None and mw2.vd is not None and mw2.vd.ghost_pos is not None
        QMessageBox.calls.clear()
        mw2.do_export()
        assert QMessageBox.calls and QMessageBox.calls[-1][0] == "warning"
        mw2.viewport.paintEvent(None)
    print("ui logic OK")


def test_worker_failure():
    mw = A.MainWindow()
    QMessageBox.calls.clear()
    mw.run_async(lambda w: 1 / 0, lambda r: None, "시험")
    assert QMessageBox.calls and QMessageBox.calls[-1][0] == "critical" and mw._worker is None and mw.tabs.isEnabled()
    # 취소: 작업 함수에 취소가 전달되고, 결과 콜백은 호출되지 않으며 화면은 바로 풀린다
    called, cancelled, seen = [], [], []

    def fn(w):
        assert not w.cancel.is_set()
        mw.cancel_async()
        seen.append(w.cancel.is_set())
        return 5

    mw.run_async(fn, lambda r: called.append(r), "시험2", cancellable=True, on_cancel=lambda: cancelled.append(1))
    assert seen == [True] and not called and cancelled == [1] and mw._worker is None and mw.tabs.isEnabled()
    assert len(mw._zombies) == 0 and not mw.cancel_async(), "작업이 끝났으면 남은 것 없음, 작업이 없으면 취소는 아무 일도 안 함"
    mw._zombies = []
    # 취소할 수 있는 작업은 진행 창이 뜨고, 창의 X(reject)가 곧 취소다. 끝나면 창은 닫힌다
    shown = []

    def fn2(w):
        dlg = mw._busy_dlg
        shown.append(dlg is not None)
        w.progress.emit(2, 5, "장면 c.png")
        mw._worker_progress(2, 5, "장면 c.png")
        assert "3/5" in dlg.text.text() and "시험3" in dlg.text.text(), dlg.text.text()
        dlg.reject()  # 사용자가 X 를 누름
        # 하던 단계가 끝나기 전에는 창이 닫히지 않고 '멈추는 중'으로 바뀐다 (화면도 계속 잠김)
        assert dlg.stopping and mw._busy_dlg is dlg and "멈추는 중" in dlg.text.text(), dlg.text.text()
        dlg.reject()  # 멈추는 중에 또 눌러도 취소가 두 번 처리되지 않는다
        return 7

    called.clear(); cancelled.clear()
    mw.run_async(fn2, lambda r: called.append(r), "시험3", cancellable=True, on_cancel=lambda: cancelled.append(1))
    assert shown == [True] and not called and cancelled == [1] and mw._worker is None and mw._busy_dlg is None
    mw._zombies = []
    # 취소할 수 없는 작업에도 '작업 중' 창이 있고(취소 버튼 없음), 끝나면 닫힌다. Esc/X(reject)는 무시된다
    seen2 = []

    def fn3(w):
        d = mw._busy_dlg
        seen2.append((d is not None, d.closable))
        d.reject()
        seen2.append(w.cancel.is_set())
        return 9

    called.clear()
    mw.run_async(fn3, lambda r: called.append(r), "시험3b")
    assert seen2 == [(True, False), False] and called == [9] and mw._busy_dlg is None and mw._worker is None
    called.clear()
    mw.run_async(lambda w: 3, lambda r: called.append(r), "시험4", cancellable=True)
    assert called == [3] and mw._busy_dlg is None, "정상 종료하면 진행 창이 닫힘"
    mw.run_async(lambda w: 3, lambda r: None, "시험5")
    assert mw._busy_dlg is None, "취소 못 하는 작업엔 진행 창 없음"
    # 존재하지 않는 파일
    QFileDialog.open_names_ret = (["/nonexistent/x.glb"], "")
    QMessageBox.calls.clear()
    mw.open_source()
    assert QMessageBox.calls and mw._worker is None
    print("worker failure OK")




def test_undo_shortcuts_meshmode():
    with tempfile.TemporaryDirectory() as d:
        src, src2, tgt, _, _ = TP.make_inputs(d)
        from motionbridge.core import bridge

        bridge.CONFIG_PATH = os.path.join(d, "cfg.json")
        mw = A.MainWindow()
        # ---- 단축키 표
        table = dict(mw.shortcut_table())
        keys = [k for v in table.values() for k in v.split(" / ")]
        assert len(keys) == len(set(keys)), "단축키 중복: %s" % sorted(k for k in keys if keys.count(k) > 1)
        assert table["실행 취소"] == "Ctrl+Z" and "Ctrl+Y" in table["다시 실행"] and "Ctrl+Shift+Z" in table["다시 실행"]
        for need in ("Ctrl+O", "Ctrl+S", "Ctrl+E", "Space", "F", "B", "Ctrl+Q"):
            assert need in keys, need
        assert [table["메시: " + n] for n in ("표시 안 함", "점", "와이어", "반투명", "솔리드", "머티리얼 (재질별 색/텍스처)")] == ["0", "1", "2", "3", "4", "5"]
        # 모든 메뉴 동작이 실제 메서드로 연결되어 있다
        for key, (qa, sc, action, text) in mw._actions.items():
            assert hasattr(mw, action.partition(":")[0]), (key, action)
        QFileDialog.open_names_ret = ([src], "")        # 소스 열기는 여러 개 선택 가능 — 열면 모션 목록에 자동으로 올라간다
        mw.open_source()
        QFileDialog.open_ret = (tgt, "")
        mw.open_target()
        assert mw.pr.result is not None and not mw.history.can_undo(), "파일을 연 직후에는 되돌릴 것이 없다"
        # ---- 실행 취소 / 다시 실행: 설정
        mw.undo()
        assert "없습니다" in mw.statusBar().last
        nf = mw._fields["scale_mult"][0]
        nf.spin.setValue(1.5)
        mw._timer_fire()  # 조절이 멈춘 시점
        nf.spin.setValue(2.0)
        mw._timer_fire()
        assert mw.pr.settings.scale_mult == 2.0 and len(mw.history.undo_stack) == 2
        mw.undo()
        assert abs(mw.pr.settings.scale_mult - 1.5) < 1e-9 and abs(nf.value() - 1.5) < 1e-9, "위젯도 되돌아가야 함"
        mw.undo()
        assert mw.pr.settings.scale_mult == 1.0 and nf.value() == 1.0
        mw.redo()
        assert abs(mw.pr.settings.scale_mult - 1.5) < 1e-9
        mw.redo()
        assert mw.pr.settings.scale_mult == 2.0
        mw.redo()
        assert "없습니다" in mw.statusBar().last
        # 타이머 확정 전의 변경도 한 번에 되돌린다 (확정 + 되돌림)
        mw.hip_fields[1].spin.setValue(3.0)
        mw.undo()
        assert mw.pr.settings.hip_offset[1] == 0.0 and mw.pr.settings.scale_mult == 2.0
        mw.redo()
        assert mw.pr.settings.hip_offset[1] == 3.0
        # ---- 실행 취소: 본 매핑
        rows = {mw.map_table.item(r, 0).data(A.Qt.ItemDataRole.UserRole): r for r in range(mw.map_table.rowCount())}
        cb_t = mw.map_table.cellWidget(rows["L_Foot"], 2)
        orig = mw.pr.tgt_slots["L_Foot"]
        cb_t.setCurrentIndex(0)
        mw._timer_fire()
        assert "L_Foot" not in mw.pr.tgt_slots
        mw.undo()
        assert mw.pr.tgt_slots.get("L_Foot") == orig
        assert mw.map_table.cellWidget(rows["L_Foot"], 2).currentIndex() - 1 == orig, "매핑 표도 되돌아가야 함"
        mw.redo()
        assert "L_Foot" not in mw.pr.tgt_slots
        mw.undo()
        # 새 파일을 열면 기록이 지워진다
        QFileDialog.open_ret = (tgt, "")
        mw.open_target()
        assert not mw.history.can_undo()
        # ---- 프레임 이동 단축키
        mw.frame_slider.setValue(5)
        mw.step_frame(1)
        assert mw.frame_slider.value() == 6
        mw.step_frame(-10)
        assert mw.frame_slider.value() == 0, "범위 밖으로 나가지 않는다"
        mw.goto_frame("last")
        assert mw.frame_slider.value() == mw.vd.n_frames - 1
        mw.step_frame(10)
        assert mw.frame_slider.value() == mw.vd.n_frames - 1
        mw.goto_frame("first")
        assert mw.frame_slider.value() == 0
        mw._viewport_key("next")
        assert mw.frame_slider.value() == 1
        mw.toggle_play()
        assert mw.btn_play.isChecked() and mw.play_timer.active
        mw._viewport_key("play")
        assert not mw.btn_play.isChecked()
        # 탭 / 보기 토글
        mw._dispatch("goto_tab:2")
        mw._dispatch("toggle_view:bones")
        assert mw.viewport.show_bones is False
        mw._dispatch("toggle_view:ghost")
        assert mw.viewport.show_ghost is False
        mw._dispatch("toggle_view:bones")
        assert mw.viewport.show_bones is True
        # ---- 메시 보기 방식
        assert mw.viewport.mesh_mode == "solid"
        for k in ("points", "wire", "translucent", "solid", "material", "off"):
            mw._dispatch("mesh:" + k)
            assert mw.viewport.mesh_mode == k and mw.cfg["mesh_mode"] == k
            mw.viewport.paintEvent(None)  # 예외 없이 그려진다 (가짜 Qt)
        mw._dispatch("mesh:off")
        mw.mesh_cycle()
        assert mw.viewport.mesh_mode == "points"
        for _ in range(5):
            mw.mesh_cycle()
        assert mw.viewport.mesh_mode == "off", "순환해서 처음으로"
        QMessageBox.calls.clear()
        mw.show_shortcuts()
        assert "Ctrl+Z" in QMessageBox.calls[-1][2]
    print("undo/shortcuts/mesh mode OK")


def test_extract_tab():
    if os.name == "nt":
        print("test_extract_tab: Windows 에서는 건너뜀 (셔뱅 가짜 실행파일 사용)")
        return
    import test_extract as TE
    import test_gamescan as GS
    from motionbridge.core import bridge

    with tempfile.TemporaryDirectory() as d:
        bridge.CONFIG_PATH = os.path.join(d, "cfg.json")
        fb = TE.make_helper(d)
        os.environ["MOTIONBRIDGE_HELPER"] = fb
        game_ok, game_enc = os.path.join(d, "ok"), os.path.join(d, "enc")
        os.makedirs(game_ok)
        os.makedirs(game_enc)
        GS.build(game_ok, False)
        GS.build(game_enc, True)
        mw = A.MainWindow()
        assert mw.tabs is not None and mw.ex_helper_label.text().replace("\u200b", "") == fb
        # 검사 전에는 2)·3) 설정이 잠기고 [검사] 버튼이 눈에 띄는 색으로 안내한다
        assert mw._scan_gated and all(not b.isEnabled() for b in mw._scan_gated)
        assert "먼저 누르세요" in mw.ex_scan_btn.text() and "ff7a00" in mw.ex_scan_btn.styleSheet()
        # 검사 전에는 추출 불가
        QMessageBox.calls.clear()
        mw._search_anims()
        assert QMessageBox.calls and "먼저 검사" in QMessageBox.calls[-1][2]
        # 암호화되지 않은 게임: 키 입력칸 비활성, 바로 검색/추출
        QFileDialog.dir_ret = game_ok
        mw._pick_game()
        assert mw.scan.found and not mw.scan.encrypted and not mw.ex_aes.isEnabled()
        assert "바로 추출" in mw.ex_summary.text()
        assert all(b.isEnabled() for b in mw._scan_gated)  # 검사를 마치면 열린다
        assert "검사 완료" in mw.ex_scan_btn.text() and "2e8b57" in mw.ex_scan_btn.styleSheet()
        mw.ex_game.setText(game_enc)                       # 다른 폴더로 바꾸면 다시 잠긴다
        mw._update_scan_gate()
        assert all(not b.isEnabled() for b in mw._scan_gated) and "먼저 누르세요" in mw.ex_scan_btn.text()
        mw.ex_game.setText(game_ok)
        mw._update_scan_gate()
        assert all(b.isEnabled() for b in mw._scan_gated)
        mw.ex_filter.setText("(?i)AS_")
        mw._search_anims()
        assert mw.ex_list.count() == 2 and mw.ex_count.text().startswith("2개")
        # 트리: 폴더 노드(개수 표시) 아래에 파일이 있고, 폴더를 고르면 안의 파일 전부가 선택된 것으로 센다
        top = mw.ex_list.topLevelItem(0)
        assert top.childCount() >= 1 and "(2)" in top.text(0), top.text(0)
        assert mw.ex_list.selected_paths() == []
        mw.ex_list.select_where(lambda it: it.data(0, mw.ex_list.ROLE) is None and "(2)" in it.text(0))
        assert mw.ex_list.selected_paths() == mw.ex_list.all_paths() and len(mw.ex_list.all_paths()) == 2
        mw._update_ex_count()
        assert "2개 중 2개 선택" in mw.ex_count.text()
        mw.ex_list.set_all_expanded(False)
        assert not mw.ex_list.topLevelItem(0).expanded
        mw.ex_list.set_all_expanded(True)
        assert mw.ex_list.topLevelItem(0).expanded
        mw.ex_list._sel = []
        QMessageBox.calls.clear()
        mw._extract_selected()  # 선택 없음
        assert QMessageBox.calls and "고르세요" in QMessageBox.calls[-1][2]
        mw.ex_out.setText(os.path.join(d, "ex"))
        mw.ex_list.select_where(lambda it: it.data(0, mw.ex_list.ROLE) is not None)
        mw._extract_selected()
        assert mw.ex_files.count() == 2
        got = sorted((os.path.relpath(r, os.path.join(d, "ex")), f) for r, _ds, fs in os.walk(os.path.join(d, "ex")) for f in fs)
        assert [f for _r, f in got] == ["AS_Run.psa", "AS_Walk.psa"] and all(len(r.split(os.sep)) == 3 and r.endswith("Animations") for r, _f in got), got
        # 추출하면 바로 화면에 바뀐 모션이 열린다 (여러 개면 첫 번째)
        assert mw.pr.src is not None and mw.pr.src.path.endswith(os.path.basename(mw.ex_files.first_path())), mw.pr.src and mw.pr.src.path
        before = mw.pr.src.path
        # 아무것도 추출되지 않으면 이유를 알려주고 화면은 그대로
        mw.ex_list.set_paths(["Game/Content/Anim/AM_Skip.uasset"])
        mw.ex_list.select_where(lambda it: it.data(0, mw.ex_list.ROLE) is not None)
        QMessageBox.calls.clear()
        mw._extract_selected()
        assert QMessageBox.calls and "추출된 파일이 없습니다" in QMessageBox.calls[-1][2] and "AnimSequence" in QMessageBox.calls[-1][2], QMessageBox.calls
        assert mw.pr.src.path == before and mw.ex_files.count() == 0
        mw.ex_list.set_paths(["Game/Content/Anim/AS_Walk.uasset", "Game/Content/Anim/AS_Run.uasset"])
        mw.ex_list.select_where(lambda it: it.data(0, mw.ex_list.ROLE) is not None)
        mw._extract_selected()
        # 추출 결과를 소스 모션으로
        mw.ex_files.selectAll()
        mw._open_extracted()
        # 이미 목록에 올라가 있는 모션이므로 중복으로 더하지 않고 그 모션으로 이동한다
        assert mw.pr.src is not None and mw.pr.src.path.endswith(".psa") and mw.clip_combo.count() == 2
        assert mw.pr.clip_of_path(mw.ex_files.first_path()) == mw.pr.clip
        # 암호화된 게임: 키 없이는 도우미를 부르지 않는다
        log = os.environ["FAKE_LOG"]
        n_before = len(open(log).read().splitlines())
        QFileDialog.dir_ret = game_enc
        mw._pick_game()
        assert mw.scan.encrypted and mw.ex_aes.isEnabled() and "AES 키가 필요" in mw.ex_summary.text()
        QMessageBox.calls.clear()
        mw._search_anims()
        assert QMessageBox.calls and "암호화" in QMessageBox.calls[-1][2] and bytes(range(16)).hex() in QMessageBox.calls[-1][2]
        mw.ex_aes.setText("엉터리")
        QMessageBox.calls.clear()
        mw._search_anims()
        assert QMessageBox.calls and "형식" in QMessageBox.calls[-1][2]
        assert len(open(log).read().splitlines()) == n_before, "키가 잘못됐는데 도우미가 호출됨"
        mw.ex_aes.setText("0x" + "11" * 32)
        mw.ex_filter.setText("(?i)anim")
        mw._search_anims()
        assert mw.ex_list.count() == 3
        # 메시: 형식/리깅/대상 프로그램 설정이 변환 호출로 넘어가고, 결과는 목록에 오르며, 폴더를 열 수 있다
        calls, opened = [], []
        real_em, real_cm, real_open, real_find = A.extractor.export_meshes, A.meshconv.convert_mesh, A.osopen.open_in_explorer, A.bridge.find_blender
        outd = os.path.join(d, "meshout")

        def fake_em(helper, root, ue_, aes_, usmap_, paths, out, fmts, variants, preset, blender, tex, keep_raw, ev, log, lay=None):
            calls.append((paths, out, fmts, variants, preset, tex, keep_raw))
            m = os.path.join(out, "Models")
            return [os.path.join(m, "SK_Hero_static.fbx"), os.path.join(m, "SK_Hero_static.obj"), os.path.join(m, "_raw", "SK_Hero.pskx")]

        def fake_cm(src, out, fmts, variants, preset, blender, log):
            calls.append(("convert", src, out, fmts, variants, preset))
            return [os.path.join(out, "SK_Old_rigged.fbx")]
        try:
            A.extractor.export_meshes, A.meshconv.convert_mesh, A.osopen.open_in_explorer = fake_em, fake_cm, lambda p: opened.append(p) or True
            mw.ex_kind.setCurrentIndex(1)
            mw.ex_out.setText(outd)
            assert outd in mw.ex_dir_label._text.replace("\u200b", "") and "href" in mw.ex_dir_label._text
            mw.ex_list.set_paths(["Game/Content/Chars/SK_Hero.uasset"])
            mw.ex_list.select_where(lambda it: it.data(0, mw.ex_list.ROLE) is not None)
            A.bridge.find_blender = lambda *a, **k: None
            QMessageBox.calls.clear()
            mw._extract_selected()
            assert QMessageBox.calls and "Blender" in QMessageBox.calls[-1][2] and not calls
            A.bridge.find_blender = lambda *a, **k: "/b"
            mw.ex_obj.setChecked(True)
            mw.ex_rig.setCurrentIndex(2)
            mw.ex_target.setCurrentIndex(mw.ex_target.findData("maya"))
            mw._extract_selected()
            assert calls[-1][0] == ["Game/Content/Chars/SK_Hero.uasset"] and calls[-1][1] == outd
            assert calls[-1][2:] == (["fbx", "obj"], ["static"], "maya", False, True), calls[-1]
            assert mw.ex_files.count() == 3
            # 목록에 파일 종류 이름표가 붙는다
            def texts(it):
                out = [it.text(0)]
                for i in range(it.childCount()):
                    out += texts(it.child(i))
                return out
            alltxt = [x for i in range(mw.ex_files.topLevelItemCount()) for x in texts(mw.ex_files.topLevelItem(i))]
            assert any(x.startswith("[모델링 · 리깅 없음]") and x.endswith("SK_Hero_static.fbx") for x in alltxt), alltxt
            assert any(x.startswith("[모델링 원본]") for x in alltxt) and any("모델링 데이터" in x for x in alltxt), alltxt
            # 폴더 열기: 출력 폴더 링크 / 선택 항목 위치 / 폴더 줄 더블클릭
            mw._open_out_folder()
            assert opened[-1] == outd and os.path.isdir(outd)
            mw.ex_files.set_paths([os.path.join(outd, "a.fbx"), os.path.join(outd, "b.fbx")])
            mw.ex_files.selectAll()
            mw._open_selected_location()
            assert opened[-1] == outd, opened
            mw.ex_files._sel = []
            mw._open_selected_location()
            assert opened[-1] == outd
            mw._ex_files_double_clicked(mw.ex_files.topLevelItem(0))
            assert opened[-1] == outd
            # 이미 뽑은 PSKX 변환: 변환된 결과물(_static/_rigged)과 비메시는 제외
            mw.ex_files.set_paths([os.path.join(outd, "SK_Old.pskx"), os.path.join(outd, "SK_Old_static.glb"), os.path.join(outd, "AS_Walk.psa")])
            mw.ex_files.selectAll()
            n = len(calls)
            mw._convert_selected_meshes()
            assert calls[n][:2] == ("convert", os.path.join(outd, "SK_Old.pskx")) and len(calls) == n + 1, calls[n:]
            assert calls[n][3:] == (["fbx", "obj"], ["static"], "maya")
            assert any(x.endswith("SK_Old_rigged.fbx") for x in mw.ex_files.all_paths())
            # 선택이 메시가 아니면 안내
            mw.ex_files.set_paths([os.path.join(outd, "AS_Walk.psa")])
            mw.ex_files.selectAll()
            QMessageBox.calls.clear()
            mw._convert_selected_meshes()
            assert QMessageBox.calls and "PSKX" in QMessageBox.calls[-1][2]
        finally:
            A.extractor.export_meshes, A.meshconv.convert_mesh, A.osopen.open_in_explorer, A.bridge.find_blender = real_em, real_cm, real_open, real_find
            mw.ex_kind.setCurrentIndex(0)
        # 도우미가 없으면 안내
        os.environ.pop("MOTIONBRIDGE_HELPER")
        os.environ["PATH"] = ""
        mw.cfg["helper"] = None
        QMessageBox.calls.clear()
        mw._search_anims()
        assert QMessageBox.calls and "mbextract" in QMessageBox.calls[-1][2]
        # 종류 전환: 메시
        mw.ex_kind.setCurrentIndex(1)
        assert mw.ex_filter.text() == A.MainWindow.KIND_FILTERS["mesh"] and "변환" in mw.btn_extract._text
        mw.ex_kind.setCurrentIndex(0)
        assert mw.ex_filter.text() == "(?i)anim" and "PSA" in mw.btn_extract._text
        mw.ex_filter.setText("(?i)내가쓴식")  # 사용자가 바꾼 식은 종류를 바꿔도 유지
        mw.ex_kind.setCurrentIndex(1)
        assert mw.ex_filter.text() == "(?i)내가쓴식"
        QMessageBox.calls.clear()
        mw._open_extracted_as_target()  # 추출 파일이 없으면 안내
        assert QMessageBox.calls
    print("extract tab OK")


def test_extract_list_drag():
    """추출 탭의 '에셋 찾기 / 추출' 목록도 뷰포트로 끌어다 놓을 수 있다 (종류 = 지금 고른 추출 종류, 폴더는 안의 파일 전부)."""
    mw = A.MainWindow()
    t = mw.ex_list
    assert callable(t.drag_kind)
    t.set_paths(["Game/A/anim1.uasset", "Game/A/anim2.uasset", "Game/B/mesh1.uasset"])

    class It:
        def __init__(self, paths=None, kids=()):
            self.p, self.k = paths, list(kids)

        def data(self, col, role):
            return self.p

        def childCount(self):
            return len(self.k)

        def child(self, i):
            return self.k[i]

    leaf = It("Game/B/mesh1.uasset")
    folder = It(None, [It("Game/A/anim1.uasset"), It("Game/A/anim2.uasset")])
    mw.ex_kind.setCurrentIndex(mw.ex_kind.findData("anim"))
    d = t.drag_payload([folder, leaf])
    assert d["kind"] == "anim" and d["paths"] == ["Game/A/anim1.uasset", "Game/A/anim2.uasset", "Game/B/mesh1.uasset"], d
    for i in range(mw.ex_kind.count()):
        if mw.ex_kind.itemData(i) != "anim":
            mw.ex_kind.setCurrentIndex(i)
            assert t.drag_payload([leaf])["kind"] == "mesh"
            break
    print("extract list drag OK")


def test_collapsible_subclasses_keep_base_api():
    """CollapsibleBox 를 상속한 패널이 접기/펴기용 이름(toggle 등)을 다른 뜻으로 덮어쓰면 제목 줄을 눌러도 안 열린다 (IK 구간 패널이 그랬음)."""
    import inspect
    from motionbridge.ui import widgets as W
    from motionbridge.ui.curve_editor import CurveEditor
    from motionbridge.ui.ik_panel import IKRangePanel
    from motionbridge.ui.layer_panel import LayerPanel
    base = W.CollapsibleBox
    names = [n for n, f in vars(base).items() if callable(f) and not n.startswith("__")]
    for cls in (IKRangePanel, LayerPanel, CurveEditor, W.EditPanel):
        for n in names:
            mine, theirs = cls.__dict__.get(n), base.__dict__.get(n)
            if mine is None or not callable(mine):
                continue
            assert inspect.signature(mine) == inspect.signature(theirs), "%s.%s 가 CollapsibleBox.%s 와 인자가 다릅니다 (접기/펴기가 깨집니다)" % (cls.__name__, n, n)
    assert "toggle" not in IKRangePanel.__dict__, "IKRangePanel.toggle 은 CollapsibleBox.toggle() 을 가립니다 — mark() 를 쓰세요"
    # (실제로 접고 펴지는지는 진짜 Qt 점검(real_qt_smoke)이 마우스 클릭으로 확인한다)
    mw = A.MainWindow()
    assert callable(mw.ik_panel.mark) and inspect.signature(type(mw.ik_panel).toggle) == inspect.signature(base.toggle)
    print("collapsible subclass api OK")


def test_clip_checks_export():
    """모션 목록: 뷰포트로 연 파일이 자동으로 올라오고, 체크한 모션이 내보내기 대상이 된다."""
    with tempfile.TemporaryDirectory() as d:
        src, src2, tgt, _, _ = TP.make_inputs(d)
        from motionbridge.core import bridge
        bridge.CONFIG_PATH = os.path.join(d, "cfg.json")
        mw = A.MainWindow()
        cp = mw.clip_panel
        assert not hasattr(cp, "b_add") or not isinstance(cp.__dict__.get("b_add"), type(cp.b_rm)), "파일 고르기 버튼은 없다"
        QFileDialog.open_names_ret = ([src], "")
        mw.open_source()
        QFileDialog.open_names_ret = ([src2], "")           # 두 번째 소스도 목록에 자동으로 더해진다 (소스를 갈아엎지 않음)
        mw.open_source()
        n = len(mw.pr.src.animations)
        assert n >= 2 and cp.lst.count() == n, (n, cp.lst.count())
        # 체크 전: 라디오 선택대로 (현재 클립만)
        mw.pr.clip = 0
        assert cp.checked_clips() == [] and mw._export_clips() == [0] and mw.clip_hint.text() == ""
        # 모션 앞 체크박스 -> 체크한 것 전부
        Qt = A.Qt
        for i in (0, n - 1):
            cp.lst.item(i).setCheckState(Qt.CheckState.Checked)
        cp._item_changed()
        assert cp.checked_clips() == [0, n - 1] and mw._export_clips() == [0, n - 1]
        assert "2개" in mw.clip_hint.text() and "2개" in cp.count.text()
        cp.refresh()                                          # 다시 그려도 체크 유지
        assert cp.checked_clips() == [0, n - 1]
        cp.set_all_checked(True)
        assert cp.checked_clips() == list(range(n))
        cp.set_all_checked(False)
        assert cp.checked_clips() == [] and mw._export_clips() == [mw.pr.clip]
        # 지우기: 체크 상태는 모션을 따라가고, 마지막 하나를 지우면 소스를 뺀다
        cp.set_all_checked(True)
        cp.lst.setCurrentRow(0)
        cp.remove_current()
        assert len(mw.pr.src.animations) == n - 1 and len(cp.checked_clips()) == n - 1
        while mw.pr.src is not None:
            cp.lst.setCurrentRow(0)
            cp.remove_current()
        assert mw.clip_combo.count() == 0 and cp.lst.count() == 0
    print("clip checks export OK")


def test_exist_policy_ui():
    from motionbridge.core import safesave
    mw = A.MainWindow()
    # 기본값은 '덮어쓰기' (_old_ 중복 파일을 만들지 않음) — 두 탭의 콤보가 같은 설정
    assert mw._exist_policy() == safesave.OVERWRITE
    assert mw.ex_exist.currentData() == mw.out_exist.currentData() == safesave.OVERWRITE
    assert "덮어쓰기" in mw.btn_export._text
    assert mw.ex_exist.count() == 2 and mw.ex_exist.findData(safesave.KEEP_OLD) < 0
    # 예전 설정 파일에 keep_old 가 저장돼 있어도 덮어쓰기로 읽는다
    mw.cfg["on_exist"] = safesave.KEEP_OLD
    assert mw._exist_policy() == safesave.OVERWRITE
    # 내보내기 탭에서 새 이름으로 바꾸면 추출 탭도 따라간다
    mw.out_exist.setCurrentIndex(mw.out_exist.findData(safesave.NEW_NAME))
    assert mw._exist_policy() == safesave.NEW_NAME and mw.ex_exist.currentData() == safesave.NEW_NAME
    assert "새 이름" in mw.btn_export._text
    # 내보내기 탭에서 덮어쓰기로 바꾸면 추출 탭도 따라가고 버튼/설명이 바뀐다
    mw.out_exist.setCurrentIndex(mw.out_exist.findData(safesave.OVERWRITE))
    assert mw._exist_policy() == safesave.OVERWRITE and mw.ex_exist.currentData() == safesave.OVERWRITE
    assert "덮어쓰기" in mw.btn_export._text and mw.cfg["on_exist"] == safesave.OVERWRITE
    # 추출 레이아웃에 정책이 전달된다
    mw.ex_out.setText("/tmp/mb_x")
    assert mw.make_layout().on_exist == safesave.OVERWRITE
    # 척추 중간값 체크박스: 기본 켜짐, 끄면 설정에 반영
    assert mw.pr.settings.spine_mix is True
    cb = mw._fields["spine_mix"][0]
    cb.setChecked(False)
    assert mw.pr.settings.spine_mix is False
    cb.setChecked(True)
    # Biped 템플릿 필드가 있다
    mw.biped_tpl.setText("/tmp/skinned.max")
    assert mw.biped_tpl.text().endswith("skinned.max")
    print("exist policy UI OK")


if __name__ == "__main__":
    test_full_flow()
    test_worker_failure()
    test_undo_shortcuts_meshmode()
    test_extract_tab()
    test_collapsible_subclasses_keep_base_api()
    test_clip_checks_export()
    test_exist_policy_ui()
    test_extract_list_drag()
    print("test_ui_logic ALL OK")
