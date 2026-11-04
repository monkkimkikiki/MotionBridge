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
        QFileDialog.open_ret = (src, "")
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
        assert mw.pr.result is not None and len(mw.pr.chains()) == 1, "발 하나가 빠지면 IK 체인도 하나"
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
        assert mw.pr.result is not None and not mw.warn_label.text().startswith("⚠ 리타깃 실패"), mw.warn_label.text()
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
        mw.do_export()
        fs = sorted(os.listdir(out))
        assert len([f for f in fs if f.endswith(".bvh")]) == 4 and len([f for f in fs if f.endswith(".glb")]) == 2, fs  # 클립 2개 × (blender,max)
        assert "완료" in mw.export_log.toPlainText()
        assert mw.pr.clip == 0 and mw.pr.result is not None and mw.tabs.isEnabled()
        # 본 구조 제외
        mw.cb_skeleton.setChecked(False)
        out2 = os.path.join(d, "e2")
        mw.out_edit.setText(out2)
        mw.rb_clip_all.setChecked(False)
        mw.rb_clip_cur.setChecked(True)
        mw.do_export()
        assert [f for f in os.listdir(out2)] and all(f.endswith("_curves.json") for f in os.listdir(out2))
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
        # 소스만 열린 상태에서 리타깃 내보내기 -> 경고
        mw2 = A.MainWindow()
        QFileDialog.open_ret = (src, "")
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
    # 존재하지 않는 파일
    QFileDialog.open_ret = ("/nonexistent/x.glb", "")
    QMessageBox.calls.clear()
    mw.open_source()
    assert QMessageBox.calls and mw._worker is None
    print("worker failure OK")




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
        assert mw.tabs is not None and mw.ex_helper_label.text() == fb
        # 검사 전에는 추출 불가
        QMessageBox.calls.clear()
        mw._search_anims()
        assert QMessageBox.calls and "먼저 검사" in QMessageBox.calls[-1][2]
        # 암호화되지 않은 게임: 키 입력칸 비활성, 바로 검색/추출
        QFileDialog.dir_ret = game_ok
        mw._pick_game()
        assert mw.scan.found and not mw.scan.encrypted and not mw.ex_aes.isEnabled()
        assert "바로 추출" in mw.ex_summary.text()
        mw.ex_filter.setText("(?i)AS_")
        mw._search_anims()
        assert mw.ex_list.count() == 2 and mw.ex_count.text().startswith("2개")
        QMessageBox.calls.clear()
        mw._extract_selected()  # 선택 없음
        assert QMessageBox.calls and "고르세요" in QMessageBox.calls[-1][2]
        mw.ex_out.setText(os.path.join(d, "ex"))
        mw.ex_list.selectItems([0, 1])
        mw._extract_selected()
        assert mw.ex_files.count() == 2 and sorted(os.listdir(os.path.join(d, "ex"))) == ["AS_Run.psa", "AS_Walk.psa"]
        # 추출 결과를 소스 모션으로
        mw.ex_files.selectAll()
        mw._open_extracted()
        assert mw.pr.src is not None and mw.pr.src.path.endswith(".psa") and mw.clip_combo.count() == 1
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
        # 도우미가 없으면 안내
        os.environ.pop("MOTIONBRIDGE_HELPER")
        os.environ["PATH"] = ""
        mw.cfg["helper"] = None
        QMessageBox.calls.clear()
        mw._search_anims()
        assert QMessageBox.calls and "mbextract" in QMessageBox.calls[-1][2]
    print("extract tab OK")


if __name__ == "__main__":
    test_full_flow()
    test_worker_failure()
    test_extract_tab()
    print("test_ui_logic ALL OK")
