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
        QFileDialog.open_ret = (src, "")
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
        assert mw.ex_files.count() == 2 and sorted(os.listdir(os.path.join(d, "ex", "Animations"))) == ["AS_Run.psa", "AS_Walk.psa"]
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
        # 메시: 형식/리깅/대상 프로그램 설정이 변환 호출로 넘어가고, 결과는 목록에 오르며, 폴더를 열 수 있다
        calls, opened = [], []
        real_em, real_cm, real_open, real_find = A.extractor.export_meshes, A.meshconv.convert_mesh, A.osopen.open_in_explorer, A.bridge.find_blender
        outd = os.path.join(d, "meshout")

        def fake_em(helper, root, ue_, aes_, usmap_, paths, out, fmts, variants, preset, blender, tex, keep_raw, ev, log):
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


if __name__ == "__main__":
    test_full_flow()
    test_worker_failure()
    test_undo_shortcuts_meshmode()
    test_extract_tab()
    print("test_ui_logic ALL OK")
