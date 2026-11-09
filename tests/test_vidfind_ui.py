"""'영상으로 찾기' 탭의 흐름 시험 (가짜 Qt + 가짜 인식 서버 + 가짜 추출 도우미)."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qt_stub  # noqa: E402

qt_stub.install()
from motionbridge.core import assetidx, bridge, frames, vision  # noqa: E402
from motionbridge.ui import app as A  # noqa: E402
from motionbridge.ui import vidfind_tab as V  # noqa: E402
from qt_stub import QFileDialog, QMessageBox  # noqa: E402

MESHES = [
    "Sparta/Content/Characters/Monsters/Dwarf/Meshes/SK_Dwarf",
    "Sparta/Content/Characters/Monsters/Hexapod/Meshes/SK_Hexapod",
    "Sparta/Content/Characters/Monsters/StoneGolem/Meshes/SK_StoneGolem",
    "Sparta/Content/Props/SK_Sword",
]
ANIMS = [
    "Sparta/Content/Characters/Monsters/Dwarf/Animations/A_Dwarf_Attack_3Hit_Combo_1",
    "Sparta/Content/Characters/Monsters/Dwarf/Animations/A_Dwarf_Attack_2Hit_Combo_1",
    "Sparta/Content/Characters/Monsters/Dwarf/Animations/A_Dwarf_HitReact",
    "Sparta/Content/Characters/Monsters/Dwarf/Animations/A_Dwarf_Idle",
    "Sparta/Content/Characters/Monsters/StoneGolem/Animations/A_StoneGolem_Slam",
    "Sparta/Content/Characters/Monsters/Hexapod/Animations/A_Hexapod_Attack_Claw",
]

fails = []


def check(c, m):
    if not c:
        fails.append(m)
        print("FAIL", m)


def main():
    with tempfile.TemporaryDirectory() as d:
        os.environ["HOME"] = d
        bridge.CONFIG_PATH = os.path.join(d, "cfg.json")
        V.INDEX_CACHE = os.path.join(d, "assets.json")
        V.FRAME_DIR = os.path.join(d, "frames")
        mw = A.MainWindow()
        vt = mw.vidfind
        vt.memory = assetidx.MatchMemory(os.path.join(d, "matches.json"))
        mw._extract_args = lambda: ("helper", "G:/Sparta", "GAME_UE5_4", None, None)

        listed = []

        def fake_list(helper, game, ue, aes, usmap, pattern, limit=5000, on_event=None):
            listed.append(pattern)
            return MESHES + ANIMS

        V.extractor.list_assets = fake_list
        prompts = []
        script = {"i": 0}

        def fake_post(url, payload, headers, timeout):
            if url.endswith("/api/tags"):
                return {"models": [{"name": "qwen2.5vl:7b"}]}
            prompts.append(payload["messages"][0]["content"])
            script["i"] += 1
            if script["i"] == 1:
                chars = [{"name": "Dwarf", "confidence": 0.9, "description": "armored dwarf", "box": [0.1, 0.1, 0.6, 0.9]},
                         {"name": "unknown", "confidence": 0.5, "description": "tall grey stone brute"}]
            else:
                chars = [{"name": "Dwarf", "confidence": 0.7, "description": "dwarf"}]
            return {"message": {"content": json.dumps({"characters": chars})}}

        V.vision.make_provider = lambda kind, cfg, post=None: vision.OllamaProvider(cfg.get("vf_ollama_model") or "qwen2.5vl:7b", post=fake_post)
        V.frames.find_ffmpeg = lambda *a: None
        def fake_sample(ff, srcs, out, per, cap, prog=None):
            os.makedirs(out, exist_ok=True)
            res = []
            for i in range(2):
                pth = os.path.join(out, "f%d.jpg" % i)
                open(pth, "wb").write(b"\xff\xd8 fake")
                res.append(frames.Frame(pth, srcs[0], i))
            return res

        V.frames.sample_sources = fake_sample

        # 입력 없으면 안내
        QMessageBox.calls.clear()
        vt.start_guess()
        check(QMessageBox.calls and "추가" in QMessageBox.calls[-1][2], "입력 없음 안내")

        QFileDialog.open_names_ret = (["/v/a.mp4", "/v/b.png", "/v/c.txt"], "")
        vt.add_files()
        check(vt.src_list.count() == 2 and vt.source_paths(False) == ["/v/a.mp4", "/v/b.png"], "영상/이미지만 추가: %s" % vt.source_paths(False))

        # 추측
        vt.start_guess()
        check(listed == [V.LIST_PATTERN], "에셋 목록은 한 번에 읽음")
        check(os.path.isfile(V.INDEX_CACHE), "색인 저장")
        check(len(vt.rows) == 2, "추측 2종: %d" % len(vt.rows))

        # 추측 취소: 취소를 알리면 작업이 다음 단계로 넘어가지 않고 멈춘다 (취소 버튼은 추측에만 켜짐)
        import threading
        cap = []
        _orig_run = mw.run_async
        mw.run_async = lambda fn, done, label, **k: cap.append((fn, k))
        vt.start_guess()
        mw.run_async = _orig_run
        check(len(cap) == 1 and cap[0][1].get("cancellable") is True and callable(cap[0][1].get("on_cancel")), "추측은 취소할 수 있게 등록")
        fw = type("W", (), {"cancel": threading.Event(), "progress": type("P", (), {"emit": lambda *a: None})()})()
        fw.cancel.set()
        try:
            cap[0][0](fw)
            check(False, "취소하면 작업이 멈춰야 함")
        except Exception as e:  # noqa: BLE001
            check(type(e).__name__ == "_Stopped", "취소 -> 작업 중단: %s" % type(e).__name__)
        prev_status = vt.status.text()
        cap[0][1]["on_cancel"]()
        check("취소" in vt.status.text(), "취소 안내: %s" % vt.status.text())
        vt.status.setText(prev_status)
        check("Dwarf" in prompts[0] and "StoneGolem" in prompts[0], "AI에게 게임 이름 목록을 알림")
        check(len(prompts) == 2, "장면마다 호출: %d" % len(prompts))
        r0, r1 = vt.rows
        check(r0.group.name == "Dwarf" and r0.chosen() == MESHES[0], "Dwarf 는 자동으로 맞는 메시: %s" % r0.chosen())
        check(r1.chosen() is None and r1.combo.currentData() is None, "모르는 캐릭터는 후보 없음")
        check("캐릭터 2종" in vt.status.text(), "상태: %s" % vt.status.text())

        # 검수: 검색으로 StoneGolem 지정
        r1.search.setText("golem")
        check(r1.chosen() == MESHES[2], "검색해서 고름: %s" % r1.chosen())
        r1.search.setText("")
        check(r1.chosen() is None, "검색을 지우면 원래 후보로")
        r1.search.setText("golem")

        # 확정 -> 메시/애니메이션 후보
        vt.confirm()
        check(vt.mesh_list.count() == 2 and vt.confirmed == [MESHES[0], MESHES[2]], "확정 메시: %s" % vt.confirmed)
        check(vt.anim_list.count() == 5, "애니메이션 후보 5개 (Dwarf 4 + Golem 1): %d" % vt.anim_list.count())
        check(not any("Hexapod" in vt.anim_list.item(i).data(A.Qt.ItemDataRole.UserRole) for i in range(vt.anim_list.count())), "다른 캐릭터 제외")
        vt.label.setCurrentIndex(vt.label.findData("타격"))
        top = vt.anim_list.item(0).data(A.Qt.ItemDataRole.UserRole)
        check("Attack" in top or "Slam" in top, "타격 분류 최상위: %s" % top)
        vt.hits.setValue(3)
        check(vt.anim_list.item(0).data(A.Qt.ItemDataRole.UserRole).endswith("3Hit_Combo_1"), "3타 우선")

        # 기억: 다시 추측하면 모르던 캐릭터가 StoneGolem 으로 바로 제안된다
        mem = assetidx.MatchMemory(os.path.join(d, "matches.json"))
        check(mem.best("unknown", "tall grey stone brute") == MESHES[2], "기억 파일에 저장됨")
        script["i"] = 0
        vt.start_guess()
        check(vt.rows[1].combo.currentData() == MESHES[2] and "이전에 확정" in vt.rows[1].combo.currentText(), "기억된 매칭이 첫 후보: %s" % vt.rows[1].combo.currentText())
        check(len(listed) == 1, "색인은 캐시를 씀")

        # 제외 체크
        vt.rows[0].exclude.setChecked(True)
        check(vt.chosen_meshes() == [MESHES[2]], "제외한 줄은 빠짐")
        vt.rows[0].exclude.setChecked(False)

        # 추출: 선택한 것만, 형식 설정은 0번 탭 것을 씀
        calls = []

        def fake_em(helper, root, ue, aes, usmap, paths, out, fmts, variants, preset, blender, tex, keep_raw, ev, log, lay=None):
            calls.append(("mesh", list(paths), lay.model_dest(paths[0]), fmts, variants, preset))
            return [os.path.join(out, "Models", "SK_Dwarf_static.fbx")]

        def fake_ea(helper, root, ue, aes, usmap, paths, out, on_event=None, kind="anim", **k):
            calls.append(("anim", list(paths), k["lay"].anim_dest(paths[0]), kind))
            return [os.path.join(out, "A_Dwarf_Attack_3Hit_Combo_1.psa")]

        V.extractor.export_meshes, V.extractor.export_assets = fake_em, fake_ea
        real_find = A.bridge.find_blender
        A.bridge.find_blender = lambda *a, **k: "/b"
        try:
            vt.confirm()
            vt.mesh_list.selectItems([0])
            vt.anim_list.selectItems([0])
            mw.ex_out.setText(os.path.join(d, "out"))
            mw.ex_target.setCurrentIndex(mw.ex_target.findData("maya"))
            vt.extract()
            check(len(calls) == 2, "메시+애니 둘 다 호출: %s" % calls)
            check(calls[0][0] == "mesh" and calls[0][1] == [MESHES[0]] and calls[0][5] == "maya", "선택한 메시와 대상 설정: %s" % (calls[0],))
            check(calls[1][0] == "anim" and len(calls[1][1]) == 1 and calls[1][2].endswith(os.path.join("Dwarf", "Animations")), "선택한 애니메이션: %s" % (calls[1],))
            check(calls[0][2].endswith(os.path.join("Dwarf", "Models")) and calls[0][2].startswith(os.path.join(d, "out")), "메시도 게임/캐릭터/Models: %s" % (calls[0][2],))
            check(len(os.path.relpath(calls[1][2], os.path.join(d, "out")).split(os.sep)) == 3, "출력 폴더 아래 3단계만: %s" % (calls[1][2],))
            check(mw.ex_files.count() == 2, "결과가 추출 탭 목록에 올라감: %d" % mw.ex_files.count())
            # 아무것도 안 고르면 안내
            vt.mesh_list.selectItems([])
            vt.anim_list.selectItems([])
            QMessageBox.calls.clear()
            n = len(calls)
            vt.extract()
            check(QMessageBox.calls and len(calls) == n, "선택 없음 안내")
        finally:
            A.bridge.find_blender = real_find


        # ---------------- 게임 속 이름으로 찾기 (리소스 이름이 화면 이름과 다를 때)
        import test_locres as TL
        from motionbridge.core import gamenames
        pris = "Sparta/Content/Characters/Enemies/Prisoner/Meshes/SK_Prisoner"
        vt.index = assetidx.AssetIndex(MESHES + [pris], ANIMS)
        vt.cfg["game_dir"] = "G:/game"
        V.NAMES_CACHE = os.path.join(d, "names.json")
        vt.rows = []
        vt.table.setRowCount(0)
        vt.add_blank_row()
        vt.table.selectRow(0)
        vt.ingame.setText("The Nameless Captive")
        vt.ingame_ai.setChecked(False)
        vt.find_by_ingame_name()
        check(vt.rows[0].chosen() == pris, "비슷한 말(captive~prisoner)로 찾아 선택: %s" % vt.rows[0].chosen())
        check(vt.rows[0].group.name == "The Nameless Captive", "빈 줄에 게임 속 이름이 들어감")
        check("글자 사전" in vt.ingame_note.text(), "사전이 없다는 안내: %s" % vt.ingame_note.text())
        # 글자 사전이 있으면 그쪽이 우선
        book = gamenames.NameBook([("en", "E", "Enemy_Dwarf_Name", "The Nameless Captive")])
        book.save(V.NAMES_CACHE, "G:/game")
        vt.find_by_ingame_name()
        check(vt.rows[0].chosen() == MESHES[0] and "게임 글자" in vt.rows[0].combo.currentText() + vt.rows[0].base[0].why, "글자 사전 결과가 1순위: %s" % vt.rows[0].chosen())
        # AI 를 켜면 이름 목록 안에서만 고른다
        ai = TL.FakeAI('{"matches":[{"name":"StoneGolem","confidence":0.9,"reason":"x"}]}')
        vt.provider = lambda: ai
        vt.ingame.setText("Rock Titan")
        vt.ingame_ai.setChecked(True)
        vt.find_by_ingame_name()
        check(vt.rows[0].chosen() == MESHES[2] and ai.prompts, "AI 추천이 후보로: %s" % vt.rows[0].chosen())
        QMessageBox.calls.clear()
        vt.ingame.setText("")
        vt.find_by_ingame_name()
        check(QMessageBox.calls, "이름 없이 누르면 안내")
        del vt.provider  # 시험용으로 바꾼 것을 되돌림
        # ---------------- 특징·패턴으로 캐릭터 찾기: 이름이 다 틀려도 '스핀 공격' 애니메이션이 있는 캐릭터를 거꾸로 찾는다
        spin = "Sparta/Content/Characters/Bosses/Swordman/Animation/Attacks/AS_Swordman_SpinAttack_01"
        sword = "Sparta/Content/Characters/Bosses/Swordman/Art/Mesh/SK_Swordman"
        vt.index = assetidx.AssetIndex(MESHES + [pris, sword], ANIMS + [spin, "Sparta/Content/Characters/Bosses/Swordman/Animation/AS_Swordman_Idle"])
        vt.rows = []
        vt.table.setRowCount(0)
        vt.add_blank_row()
        vt.table.selectRow(0)
        vt.motion_text.setText("7연속 스핀 공격")
        vt.motion_ai.setChecked(False)
        vt.find_by_motion()
        check(vt.rows[0].chosen() == sword, "스핀 애니메이션이 있는 Swordman 이 1순위: %s" % vt.rows[0].chosen())
        check("spin" in vt.motion_note.text() and "Swordman" in vt.motion_note.text(), "안내: %s" % vt.motion_note.text())
        kw = TL.FakeAI('{"keywords":["whirlwind","spin"]}')
        vt.provider = lambda: kw
        vt.motion_text.setText("빙글빙글 도는 공격")
        vt.motion_ai.setChecked(True)
        vt.find_by_motion()
        check(kw.prompts and vt.rows[0].chosen() == sword and "AI" in vt.motion_note.text(), "AI 영어 낱말 보조: %s" % vt.motion_note.text())
        del vt.provider
        QMessageBox.calls.clear()
        vt.motion_text.setText("")
        vt.find_by_motion()
        check(QMessageBox.calls, "빈 설명 안내")
        # 애니메이션 설명 검색: 고른 캐릭터 안 / 모든 캐릭터
        vt.confirmed = [sword]
        vt.mesh_list.clear()
        vt.mesh_list.addItem(V.QListWidgetItem("Swordman"))
        vt.mesh_list.item(0).setData(A.Qt.ItemDataRole.UserRole, sword)
        vt.mesh_list.selectAll()
        vt.anim_q.setText("스핀")
        vt.anim_all.setChecked(False)
        vt.search_anims_by_text()
        got = [vt.anim_list.item(i).data(A.Qt.ItemDataRole.UserRole) for i in range(vt.anim_list.count())]
        check(got == [spin], "고른 캐릭터 안에서 스핀만: %s" % got)
        vt.anim_q.setText("slam")
        vt.search_anims_by_text()
        check(vt.anim_list.count() == 0 and "모든 캐릭터" in vt.anim_note.text(), "없으면 전체 검색 안내")
        vt.anim_all.setChecked(True)
        vt.search_anims_by_text()
        check(vt.anim_list.count() == 1 and "StoneGolem" in vt.anim_list.item(0).text(), "전체에서 찾음: %s" % vt.anim_list.item(0).text() if vt.anim_list.count() else "없음")
        vt.anim_q.setText("")
        vt.search_anims_by_text()  # 비우면 기본 목록(분류/타수 기준)으로 돌아간다 — 오류 없이 지나가야 함
        # ---------------- 목록 안에서 글자로 거르기 + 종류(몬스터/플레이어) 체크
        vt.anim_list.clear()
        for nm, pth in (("90%  A · AS_Swordman_SpinAttack_01", "X/Monsters/Swordman/AS_Swordman_SpinAttack_01"),
                        ("80%  A · AS_Swordman_Idle", "X/Monsters/Swordman/AS_Swordman_Idle"),
                        ("70%  P · A_PC_HeavyAttack", "X/Player/Animations/A_PC_HeavyAttack")):
            it = V.QListWidgetItem(nm)
            it.setData(A.Qt.ItemDataRole.UserRole, pth)
            vt.anim_list.addItem(it)
        vt.anim_list.selectAll()
        vt.anim_filter.setText("attack")
        vis = [vt.anim_list.item(i).text() for i in range(3) if not vt.anim_list.item(i).isHidden()]
        check(len(vis) == 2 and "Idle" not in " ".join(vis) and "2 / 3" in vt.anim_count.text(), "글자로 거르기: %s %s" % (vis, vt.anim_count.text()))
        vt.anim_filter.setText("ATTACK heavy")  # 대소문자 무시, 띄어쓰기는 모두 포함
        check(vt._picked_anims() == ["X/Player/Animations/A_PC_HeavyAttack"], "가려진 것은 선택에서 빠짐: %s" % vt._picked_anims())
        vt.anim_filter.setText("player,attack")  # 쉼표도 모두 포함
        check(vt._picked_anims() == [] or vt._picked_anims() == ["X/Player/Animations/A_PC_HeavyAttack"], "쉼표 검색")
        vis = [i for i in range(3) if not vt.anim_list.item(i).isHidden()]
        check(vis == [2], "쉼표: player 와 attack 둘 다 든 것만: %s" % vis)
        vt.anim_filter.setText("zzz")
        check(vt._picked_anims() == [] and "0 / 3" in vt.anim_count.text(), "없는 글자")
        vt.anim_filter.setText("")
        check(all(not vt.anim_list.item(i).isHidden() for i in range(3)) and "3개" in vt.anim_count.text(), "비우면 모두 표시")
        vt._select_visible_anims()
        vt.anim_filter.setText("idle")
        vt._select_visible_anims()
        check(vt._picked_anims() == ["X/Monsters/Swordman/AS_Swordman_Idle"], "전체 선택은 보이는 것만")
        vt.anim_filter.setText("")
        check(vt._kinds() is None, "종류: 둘 다 켜면 전체")
        vt.ck_k_player.setChecked(False)
        check(vt._kinds() == {"monster"} and vt.cfg.get("vf_kind_player") is False, "몬스터만")
        vt.ck_k_player.setChecked(True)
        vt.ck_k_monster.setChecked(False)
        check(vt._kinds() == {"player"}, "플레이어만")
        vt.ck_k_monster.setChecked(True)
        vt.anim_filter.setText("")
        # ---------------- 캐릭터 메쉬 찾기 모드: AI 추측 없이 캐릭터 모델링 전체 검색 (리깅됨/리깅 없음), 고른 캐릭터의 애니메이션만
        saved_idx = vt.index
        P = "G/Characters/Player/Meshes/"
        vt.index = assetidx.AssetIndex([P + "SK_Player_A.SK_Player_A", P + "SK_Armor_B.SK_Armor_B", "G/Characters/Monsters/Golem/Meshes/SK_Golem.SK_Golem"],
                                       ["G/Characters/Player/Animations/A_PC_Roll", "G/Characters/Player/Animations/A_PC_Attack_01", "G/Characters/Monsters/Golem/Animations/A_Golem_Slam"],
                                       [P + "SM_Player_Helmet.SM_Player_Helmet", "G/Characters/Monsters/Golem/Meshes/SM_GolemRock.SM_GolemRock"])
        try:
            vt.rb_mode_char.setChecked(True)  # 모드를 바꾸면 바로 목록이 열린다
            check(vt.char_mode and vt.mesh_list.count() == 5 and "캐릭터 메쉬" in vt.btn_guess.text() and vt.char_panel.isVisible() is not False, "캐릭터 메쉬 전체: %d" % vt.mesh_list.count())
            check(vt.mesh_list.item(0).text().startswith("[리깅됨]") and vt.mesh_list.item(4).text().startswith("[리깅 없음]"), "리깅됨이 먼저, 리깅 없음이 뒤: %s / %s" % (vt.mesh_list.item(0).text(), vt.mesh_list.item(4).text()))
            check(vt.anim_list.count() == 3, "메시를 안 골랐으면 애니메이션 전체: %d" % vt.anim_list.count())
            vt.char_rig.setCurrentIndex(vt.char_rig.findData("static"))
            check(vt.mesh_list.count() == 2 and all(vt.mesh_list.item(i).text().startswith("[리깅 없음]") for i in range(2)), "리깅 안 된 것만")
            vt.char_rig.setCurrentIndex(vt.char_rig.findData("rigged"))
            check(vt.mesh_list.count() == 3, "리깅된 것만 3")
            vt.char_rig.setCurrentIndex(vt.char_rig.findData("all"))
            vt.char_q.setText("player, helmet")
            vt.show_char_meshes()
            check(vt.mesh_list.count() == 1 and "Helmet" in vt.mesh_list.item(0).text(), "쉼표 = 모두 포함 검색")
            vt.char_q.setText("")
            vt.ck_char_player.setChecked(True)  # 플레이어블만 (같은 폴더 변형 포함)
            check(vt.mesh_list.count() == 3 and vt._kinds() == {"player"}, "플레이어블만: %d" % vt.mesh_list.count())
            check(vt.anim_list.count() == 2, "플레이어 애니메이션만")
            vt.ck_char_player.setChecked(False)
            vt.ck_k_monster.setChecked(True)
            vt.show_char_meshes()
            # 몬스터 메시를 고르면 그 캐릭터에 해당하는 클립만
            gi = [i for i in range(vt.mesh_list.count()) if vt.mesh_list.item(i).text().startswith("[리깅됨]") and "Golem" in vt.mesh_list.item(i).text()][0]
            vt.mesh_list.selectItems([gi])
            vt._mesh_sel_changed()
            check(vt.anim_list.count() == 1 and "Slam" in vt.anim_list.item(0).text(), "고른 캐릭터의 클립만: %s" % [vt.anim_list.item(i).text() for i in range(vt.anim_list.count())])
            # 리깅 없는 메시만 고르면 애니메이션 없음 안내
            si = [i for i in range(vt.mesh_list.count()) if vt.mesh_list.item(i).text().startswith("[리깅 없음]")][0]
            vt.mesh_list.selectItems([si])
            vt._mesh_sel_changed()
            check(vt.anim_list.count() == 0 and "SM_" in vt.anim_note.text(), "리깅 없는 메시는 애니메이션 없음")
            vt.mesh_list.selectItems([])
            vt._mesh_sel_changed()
            vt.anim_filter.setText("roll")
            check(len([i for i in range(vt.anim_list.count()) if not vt.anim_list.item(i).isHidden()]) == 1, "목록 거르기")
            vt.anim_filter.setText("")
            vt.mesh_list.clear()
            vt.char_mode = False
            vt.start_guess(False)  # 영상이 없어도 AI 를 부르지 않고 캐릭터 메쉬 목록
            check(vt.char_mode and vt.mesh_list.count() == 5, "추측 버튼 = 캐릭터 메쉬 목록 (영상 없이도)")
            vt.rb_mode_monster.setChecked(True)
            vt.rb_mode_char.setChecked(False)  # (진짜 Qt 는 같은 묶음 라디오가 자동으로 해제된다)
            check(not vt.char_mode and vt.mesh_list.count() == 0 and vt.btn_guess.text() == "캐릭터 추측 시작", "몬스터 찾기로 복귀")
        finally:
            vt.index = saved_idx
        # ---------------- 영상 속 동작과 비슷한 순으로 정렬 (뼈 방향 + DTW). 뽑기/읽기는 가짜로 바꿔서 흐름만 확인한다
        import numpy as _np
        import synth as _synth
        from motionbridge.core.model import Animation as _An, Model as _Model

        def _walk(cfg, **kw):
            sk, meta = _synth.build_skeleton(**cfg)
            return sk, _synth.walk_animation(sk, meta, **kw)[0]
        QA = dict(style="mixamo", frame="y_up_z", scale=1.0, pose="T", local_axes="aligned")
        QB = dict(style="ue", frame="z_up_x", scale=100.0, pose="T", local_axes="bone_x", height=1.1, leg_scale=1.15)
        qsk, qan = _walk(QA, n_frames=60)
        models = {"query.bvh": _Model("query.bvh", qsk, [qan])}
        sk_b, an_b = _walk(QB, n_frames=60)
        sk_r, _an_r = _walk(QA, n_frames=60)
        stems = {"a_pc_rest": _Model("x", sk_r, [_An.rest(sk_r, 30)]), "a_pc_heavyattack": _Model("y", sk_b, [an_b]),
                 "as_swordman_idle": _Model("z", sk_r, [_synth.walk_animation(sk_r, _synth.build_skeleton(**QA)[1], n_frames=60, stance=0.3, speed=1.6, hip_drop=0.2)[0]])}
        real_load, real_ea3 = V.loader.load_model, V.extractor.export_assets
        V.loader.load_model = lambda path, *a, **k: models["query.bvh"] if path.endswith("query.bvh") else stems[os.path.splitext(os.path.basename(path))[0].lower()]
        V.extractor.export_assets = lambda helper, root, ue, aes, usmap, paths, out, on_event=None, kind="anim", **k: [os.path.join(out, os.path.basename(p).split(".")[0] + ".psa") for p in paths]
        try:
            vt.anim_list.clear()
            for nm, pth in (("90%  idle", "X/Monsters/Swordman/AS_Swordman_Idle"), ("80%  rest", "X/Player/Animations/A_PC_Rest"), ("70%  heavy", "X/Player/Animations/A_PC_HeavyAttack")):
                it = V.QListWidgetItem(nm)
                it.setData(A.Qt.ItemDataRole.UserRole, pth)
                it.setToolTip(pth)
                vt.anim_list.addItem(it)
            QMessageBox.calls.clear()
            vt.rank_by_motion(os.path.join(d, "query.bvh"))
            check(vt.anim_list.count() == 3, "동작 정렬 후에도 3개: %s %s" % (vt.anim_note.text()[:80], QMessageBox.calls[-1:]))
            order = [vt.anim_list.item(i).data(A.Qt.ItemDataRole.UserRole) for i in range(3)]
            texts = [vt.anim_list.item(i).text() for i in range(3)]
            check(order[0] == "X/Player/Animations/A_PC_HeavyAttack" and order[-1] == "X/Player/Animations/A_PC_Rest", "동작이 같은 것이 맨 위, 정지 자세가 맨 아래: %s" % order)
            check(texts[0].startswith("동작 ") and "%" in texts[0] and "비슷한 순으로 정렬했습니다" in vt.anim_note.text(), "점수 표시: %s | %s" % (texts[0], vt.anim_note.text()[:40]))
            check(vt.cfg.get("vf_motion_dir") == d, "마지막 동작 파일 폴더 기억")
            # 빈 목록이면 안내
            vt.anim_list.clear()
            QMessageBox.calls.clear()
            vt.rank_by_motion(os.path.join(d, "query.bvh"))
            check(QMessageBox.calls and QMessageBox.calls[-1][0] == "warning", "빈 목록 안내")
        finally:
            V.loader.load_model, V.extractor.export_assets = real_load, real_ea3
        # ---------------- 아는 정보: 영상에 나오는 캐릭터를 미리 적으면 이름이 달라도 리소스를 먼저 찾아 AI 에게 알려 준다
        os.remove(V.NAMES_CACHE) if os.path.exists(V.NAMES_CACHE) else None
        vt.known.setPlainText("The Nameless Captive — 보스, 쇠사슬에 묶인 인간형\nDwarf Warrior")
        script["i"] = 0
        prompts.clear()
        vt.start_guess()
        check(prompts and "The Nameless Captive" in prompts[0] and "internal name probably: Prisoner" in prompts[0] and "쇠사슬" in prompts[0], "아는 정보가 AI 질문에 들어감")
        by = {r.group.name: r for r in vt.rows}
        check("Prisoner" in by and by["Prisoner"].chosen() == pris, "AI 가 '모름'이라 한 줄이 아는 정보와 합쳐져 Prisoner 로: %s" % list(by))
        check(by["Dwarf"].chosen() == MESHES[0] and "게임 속 이름: Dwarf Warrior" in by["Dwarf"].group.description, "AI 가 알아본 캐릭터는 확신이 올라감")
        check("아는 정보: The Nameless Captive → Prisoner" in vt.status.text(), "상태에 해석 결과: %s" % vt.status.text())
        check(vt.cfg.get("vf_known", "").startswith("The Nameless"), "입력은 설정에 기억")
        vt.known.setPlainText("")

        # ---------------- 검수 표에서 줄 지우기
        n0 = len(vt.rows)
        check(n0 >= 2, "지울 줄이 여러 개 있음: %d" % n0)
        last_name = vt.rows[-1].group.name
        vt.table.selectRow(0)
        first = vt.rows[0]
        vt.delete_selected_row()
        check(len(vt.rows) == n0 - 1 and first not in vt.rows and vt.table.rowCount() == n0 - 1, "선택한 줄 삭제: %d" % len(vt.rows))
        check(vt.rows[-1].group.name == last_name and vt.table.cellWidget(len(vt.rows) - 1, 3) is vt.rows[-1].exclude, "남은 줄과 표 칸이 어긋나지 않음")
        QMessageBox.calls.clear()
        vt.table.selectRow(-1)
        vt.delete_selected_row()
        check(QMessageBox.calls and len(vt.rows) == n0 - 1, "줄을 안 골랐으면 안내")
        QMessageBox.question_yes = False
        vt.clear_rows()
        check(len(vt.rows) == n0 - 1, "모두 지우기를 취소하면 그대로")
        QMessageBox.question_yes = True
        vt.clear_rows()
        check(vt.rows == [] and vt.table.rowCount() == 0, "모두 지우기")
        vt.add_blank_row()
        check(len(vt.rows) == 1, "지운 뒤에도 줄 추가 가능")

        # ---------------- 캐릭터 둘을 함께 뽑으면 애니메이션이 각자 캐릭터 폴더로
        vt.index = assetidx.AssetIndex(MESHES, ANIMS)
        vt.mesh_list.clear()
        for m in (MESHES[0], MESHES[2]):
            it = V.QListWidgetItem(m)
            it.setData(A.Qt.ItemDataRole.UserRole, m)
            vt.mesh_list.addItem(it)
        vt.mesh_list.selectAll()
        cm = vt._char_map([a for a in ANIMS if "Dwarf_Idle" in a or "StoneGolem" in a])
        check(set(cm.values()) == {"Dwarf", "StoneGolem"}, "애니메이션마다 어울리는 캐릭터 폴더: %s" % cm)

        # 글자 사전 만들기: 도우미가 꺼낸 .locres 를 읽어 저장
        if os.path.exists(V.NAMES_CACHE):
            os.remove(V.NAMES_CACHE)
        V.LOCRES_DIR = os.path.join(d, "locres")
        real_el = V.extractor.export_locres

        def fake_el(helper, root, ue, aes, usmap, out, on_event=None):
            os.makedirs(out, exist_ok=True)
            f = os.path.join(out, "Game_en_Game.locres")
            open(f, "wb").write(TL.write_locres(3, TL.DATA))
            return [f]
        V.extractor.export_locres = fake_el
        try:
            vt.build_name_book()
            nb = vt.name_book()
            check(nb is not None and len(nb) == 7 and "글자 사전 완성" in vt.ingame_note.text(), "글자 사전 저장: %s" % vt.ingame_note.text())
            V.extractor.export_locres = lambda *a, **k: []
            vt.build_name_book()
            check("찾지 못했습니다" in vt.ingame_note.text(), "locres 가 없으면 안내: %s" % vt.ingame_note.text())
        finally:
            V.extractor.export_locres = real_el

        # ---------------- 모델이 없으면 긴 작업 전에 바로 안내 + [모델 받기]
        state = {"have": False, "pulled": 0}

        class P2(vision.OllamaProvider):
            def check(self):
                return (True, "ok", []) if state["have"] else (False, "모델 '%s' 이(가) 아직 내려받아지지 않았습니다. [모델 받기] 버튼" % self.model, ["llava"])

            def pull(self, on_progress=None):
                state["pulled"] += 1
                state["have"] = True
                on_progress and on_progress(50, "pulling x")

        V.vision.make_provider = lambda kind, cfg, post=None: P2(cfg.get("vf_ollama_model") or "qwen2.5vl:7b")
        vt.engine.setCurrentIndex(vt.engine.findData("ollama"))
        check(vt.btn_pull._visible, "Ollama 선택 시 모델 받기 버튼")
        listed.clear()
        QMessageBox.calls.clear()
        vt.start_guess()
        check(not listed, "모델이 없으면 에셋 목록 읽기 전에 멈춤")
        check(QMessageBox.calls and QMessageBox.calls[-1][0] == "critical" and "모델 받기" in QMessageBox.calls[-1][2] and not QMessageBox.calls[-1][2].startswith("RuntimeError"), "친절한 오류창: %s" % QMessageBox.calls[-1:])
        vt.pull_model()
        check(state["pulled"] == 1 and "받았습니다" in vt.engine_note.text(), "모델 받기 실행: %s" % vt.engine_note.text())
        vt.pull_model()
        check(state["pulled"] == 1 and "이미 받아져" in vt.engine_note.text(), "이미 있으면 다시 받지 않음")
        vt.engine.setCurrentIndex(vt.engine.findData("claude"))
        check(not vt.btn_pull._visible, "Claude 선택 시 받기 버튼 숨김")


        # ---------------- 확신이 낮으면 자동으로 고르지 않음 (이름 없는 보스를 엉뚱한 캐릭터로 확정하던 문제)
        vt.index = assetidx.AssetIndex(MESHES, ANIMS)
        vt.memory = assetidx.MatchMemory(os.path.join(d, "m2.json"))
        vt.min_conf.setValue(60)
        vt.fill_table([vision.Group("Hexapod", "six legged", 2, 0.4, "", None, []), vision.Group("Hexapod", "six legged", 2, 0.9, "", None, []),
                       vision.Group("", "grey stone brute", 1, 0.3, "", None, [])])
        lo, hi, unk = vt.rows
        check(lo.chosen() is None and "확신이 낮아" in lo.combo.currentText() and not lo.sure, "낮은 확신은 자동 선택 안 함: %s" % lo.combo.currentText())
        check(hi.chosen() == MESHES[1], "높은 확신은 자동 선택")
        check("⚠" in vt.table.item(0, 1).text() and "⚠" not in vt.table.item(1, 1).text(), "낮은 확신 표시")
        lo.combo.setCurrentIndex(1)
        check(lo.chosen() == MESHES[1], "후보에서 직접 고르면 선택됨")
        lo.search.setText("zzzz")
        lo.search.setText("")
        check(lo.chosen() is None, "검색을 지워도 낮은 확신은 자동 선택으로 돌아가지 않음")
        vt.min_conf.setValue(30)
        vt.fill_table([vision.Group("Hexapod", "", 1, 0.4, "", None, [])])
        check(vt.rows[0].chosen() == MESHES[1], "기준을 낮추면 자동 선택")
        vt.min_conf.setValue(60)

        # ---------------- 후보 애니메이션을 뷰포트(소스 모션)로 열기
        from motionbridge.core.io_psa import write_psa
        import synth
        sk_, meta_ = synth.build_skeleton("ue", "z_up_x", 100.0, "A", "bone_x")
        an1, _ = synth.walk_animation(sk_, meta_, n_frames=20, name="x")
        an2, _ = synth.walk_animation(sk_, meta_, n_frames=14, name="y", speed=1.2)
        exported = []

        def fake_ea2(helper, root, ue, aes, usmap, paths, out, on_event=None, kind="anim", **k):
            res = []
            for pth, an in zip(paths, (an1, an2, an1)):
                out = k["lay"].anim_dest(pth)
                os.makedirs(out, exist_ok=True)
                f = os.path.join(out, os.path.basename(pth) + ".psa")
                write_psa(f, sk_, [an])
                res.append(f)
            exported.append(list(paths))
            return res

        V.extractor.export_assets = fake_ea2
        vt.index = assetidx.AssetIndex(MESHES, ANIMS)
        vt.confirmed = [MESHES[0]]
        vt.mesh_list.clear()
        vt.mesh_list.addItem(V.QListWidgetItem("Dwarf"))
        vt.mesh_list.item(0).setData(A.Qt.ItemDataRole.UserRole, MESHES[0])
        vt.mesh_list.selectAll()
        vt._refill_anims()
        QMessageBox.calls.clear()
        vt.anim_list.selectItems([])
        vt.open_in_viewport()
        check(QMessageBox.calls and mw.pr.src is None, "선택 없이 열기 안내")
        vt.anim_list.selectItems([0, 1])
        picks = [vt.anim_list.item(i).data(A.Qt.ItemDataRole.UserRole) for i in (0, 1)]
        mw.ex_out.setText(os.path.join(d, "out2"))
        mw.tabs.setCurrentIndex(5)
        vt.open_in_viewport()
        check(mw.tabs.currentIndex() == 5, "뷰포트로 열어도 매핑 탭으로 넘어가지 않고 그 탭에 머묾")
        check(exported and exported[-1] == picks, "고른 애니메이션만 뽑음: %s" % exported)
        check(mw.pr.src is not None and len(mw.pr.src.animations) == 2 and mw.clip_combo.count() == 2, "소스 모션으로 열림: %d" % (len(mw.pr.src.animations) if mw.pr.src else -1))
        check(mw.ex_files.count() == 2, "추출 목록에도 올라감")
        # 더블클릭 = 그 하나만
        vt._anim_double_clicked(vt.anim_list.item(2))
        check(len(mw.pr.src.animations) == 1 and exported[-1] == [vt.anim_list.item(2).data(A.Qt.ItemDataRole.UserRole)], "더블클릭은 하나만 열기")

        # 메시 더블클릭 = 추출 단계 없이 리깅된 GLB 를 뽑아 바로 타깃으로
        import shutil
        import test_pipeline as TPL
        os.makedirs(os.path.join(d, "mk"), exist_ok=True)
        _s, _s2, tgt_glb, _a, _b = TPL.make_inputs(os.path.join(d, "mk"))
        mesh_calls = []

        def fake_ea_mesh(helper, root, ue, aes, usmap, paths, out, on_event=None, kind="anim", **k):
            mesh_calls.append((list(paths), kind, k.get("mesh_format")))
            dst = os.path.join(k["lay"].model_dest(paths[0]), "SK_Dwarf.glb")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(tgt_glb, dst)
            return [dst]

        V.extractor.export_assets = fake_ea_mesh
        mw.pr.tgt = None
        vt._mesh_double_clicked(vt.mesh_list.item(0))
        check(mesh_calls == [([MESHES[0]], "mesh", "glb")], "메시를 GLB 로 뽑음: %s" % mesh_calls)
        check(mw.pr.tgt is not None and mw.pr.tgt.skeleton.n > 0, "뷰포트 타깃으로 열림 (리깅 포함)")
        md = mw.cfg.get("last_mesh_dir", "")
        check(md.replace("\\", "/").endswith("/Models") and os.path.isdir(md) and mw._open_start_dir("mesh") == md, "타깃 열기 창은 메시를 뽑은 폴더에서 시작: %s" % md)
        ad = mw.cfg.get("last_anim_dir", "")
        check(ad.replace("\\", "/").endswith("/Animations") and mw._open_start_dir("anim") == ad, "소스 열기 창은 모션을 뽑은 폴더에서 시작: %s" % ad)
        mw.cfg["last_mesh_dir"] = "/nonexistent/zzz"
        check(mw._open_start_dir("mesh") == mw.cfg.get("last_dir", ""), "폴더가 없으면 마지막으로 연 폴더")
        mw.note_extracted([md + "/_raw/x.pskx", md + "/a.fbx"])
        check(mw.cfg["last_mesh_dir"] == md, "_raw 는 건너뜀")
        V.extractor.export_assets = fake_ea2

        # ---------------- 메시만 / 애니메이션만 추출
        calls = {"mesh": 0, "anim": 0}
        real_ea, real_em = V.extractor.export_assets, V.extractor.export_meshes
        V.extractor.export_assets = lambda *a, **k: (calls.__setitem__("anim", calls["anim"] + 1), real_ea(*a, **k))[1]
        V.extractor.export_meshes = lambda *a, **k: (calls.__setitem__("mesh", calls["mesh"] + 1), [])[1]
        vt.mesh_list.selectAll()
        vt.anim_list.selectItems([0])
        QMessageBox.calls.clear()
        vt.extract("mesh")
        vt.extract("anim")
        check(calls["mesh"] <= 1 and calls["anim"] == 1, "각각 해당 종류만 호출: %s" % calls)
        vt.anim_list.selectItems([])
        QMessageBox.calls.clear()
        vt.extract("anim")
        check(QMessageBox.calls and "애니메이션" in QMessageBox.calls[-1][2], "애니 없이 애니만 누르면 안내")
        V.extractor.export_assets, V.extractor.export_meshes = real_ea, real_em
        vt.anim_list.selectItems([0])
        # ---------------- 추출 + 가공(늘리기/압축): 원본 PSA 옆에 _edit.psa 가 생긴다
        from motionbridge.core.io_psa import read_psa_exact
        vt.mesh_list.clearSelection() if hasattr(vt.mesh_list, "clearSelection") else None
        vt.mesh_list.selectItems([]) if hasattr(vt.mesh_list, "selectItems") else None
        vt.anim_list.selectItems([0])
        vt.edit_panel.mode.setCurrentIndex(vt.edit_panel.mode.findData("speed"))
        vt.edit_panel.value.setValue(2.0)
        vt.edit_panel.compress.setChecked(True)
        mw.ex_out.setText(os.path.join(d, "out3"))
        vt.extract()
        edited = [f for f in vt.last_files if f.endswith("_edit.psa")]
        plain = [f for f in vt.last_files if f.endswith(".psa") and not f.endswith("_edit.psa")]
        check(len(edited) == 1 and len(plain) == 1 and os.path.isfile(plain[0]), "원본 PSA 는 두고 _edit.psa 를 추가: %s" % vt.last_files)
        n0 = read_psa_exact(plain[0]).animations[0].n_frames
        n1 = read_psa_exact(edited[0]).animations[0].n_frames
        check(abs(n1 - ((n0 - 1) / 2.0 + 1)) <= 1, "2배 빠르게 = 프레임 절반: %d -> %d" % (n0, n1))
        check(vt.cfg.get("vf_edit_compress") is True and vt.cfg.get("vf_edit_mode") == "speed", "가공 설정을 기억")
        vt.edit_panel.mode.setCurrentIndex(0)
        vt.edit_panel.compress.setChecked(False)
        vt.extract()
        check(not [f for f in vt.last_files if f.endswith("_edit.psa")], "가공을 끄면 _edit 파일 없음")

        # Claude: 키 없으면 호출 전에 안내
        vt.engine.setCurrentIndex(vt.engine.findData("claude"))
        check(vt.model.text() == "claude-sonnet-5-5", "Claude 기본 모델: %s" % vt.model.text())
        V.vision.make_provider = lambda kind, cfg, post=None: vision.ClaudeProvider(cfg.get("vf_claude_key", ""), cfg.get("vf_claude_model", "claude-sonnet-5-5"), post=fake_post)
        vt.key.setText("")
        QMessageBox.calls.clear()
        n = len(prompts)
        vt.start_guess()
        check(QMessageBox.calls and "API 키" in QMessageBox.calls[-1][2] and len(prompts) == n, "키 없음 안내")
        # 키는 '저장'을 체크해야만 파일에 남는다
        vt.key.setText("sk-secret")
        vt.save_key.setChecked(False)
        vt.provider()
        check("sk-secret" not in open(bridge.CONFIG_PATH, encoding="utf-8").read(), "키는 기본으로 저장하지 않음")
        vt.save_key.setChecked(True)
        vt.provider()
        check("sk-secret" in open(bridge.CONFIG_PATH, encoding="utf-8").read(), "저장 체크 시 저장")

        # 직접 줄 추가
        n = len(vt.rows)
        vt.add_blank_row()
        check(len(vt.rows) == n + 1 and vt.rows[-1].chosen() is None, "빈 줄 추가")
        vt.rows[-1].search.setText("hexa")
        check(vt.rows[-1].chosen() == MESHES[1], "빈 줄에서 검색: %s" % vt.rows[-1].chosen())

        # ---------------- AnimRef 연결: 요청 -> 화면 -> 결과 (AnimRef 1.13 의 실제 query.json 모양)
        from motionbridge.core import animlink
        ldir = os.path.join(d, "link")
        check(mw.start_link(ldir) and mw.link is not None and os.path.isfile(os.path.join(ldir, "animtool.json")), "연결 서버 시작")
        hdir = os.path.join(d, "lib", "모탈셸2", "handoff", "포로_내려찍기")
        os.makedirs(hdir)
        qf = os.path.join(hdir, "query.json")
        rf = os.path.join(hdir, "result.json")

        def write_query(cid, label, hits, char=None, video="/v/hit1.mp4"):
            json.dump({"animref_handoff": 1, "game": "모탈셸2", "clip": {"id": cid, "label": label, "duration": 3.9, "fps": 60.0, "video": video},
                       "character": char, "subject": None, "hits": hits, "motion": None, "result_path": rf}, open(qf, "w", encoding="utf-8"), ensure_ascii=False)

        def send_open():
            rq = urllib.request.Request("http://127.0.0.1:%d/v1/open" % mw.link.port, data=json.dumps({"query": qf}).encode(),
                                        headers={"X-AnimRef-Token": mw.link.token}, method="POST")
            return urllib.request.urlopen(rq, timeout=5).status

        import urllib.request
        write_query("포로_내려찍기", "타격", [0.28, 0.8, 3.3], {"name": "The Nameless Captive", "kind": "creature"})
        check(send_open() == 200, "AnimRef 의 /v1/open 은 200")
        check(vt.link_query and vt.link_query["id"] == "포로_내려찍기" and vt.source_paths(False) == ["/v/hit1.mp4"], "clip.video 가 목록에 들어감: %s" % vt.source_paths(False))
        check("포로_내려찍기" in vt.link_banner.text() and vt.label.currentData() == "타격" and vt.hits.value() == 3, "배너/분류/타수")
        check("0.28" in vt.link_banner.text() and "3.3" in vt.link_banner.text() and "분류 타격" in vt.src_note.text(), "타격 시각과 분류를 같이 표시: %s" % vt.src_note.text())
        check("The Nameless Captive" in vt.known.toPlainText(), "AnimRef 캐릭터가 '아는 정보'에 들어감: %r" % vt.known.toPlainText())
        check("영상 파일이 없습니다" in vt.link_banner.text(), "영상 파일이 없으면 경고")
        vt.known.setPlainText("")
        # AnimRef 탭 -> 같은 프로그램의 영상찾기 탭으로 바로 전달 (창구 없이)
        vt.link_query = None
        write_query("내장", "스킬", [1.0])
        mw.animref._send_to_tool(qf)
        check(vt.link_query and vt.link_query["id"] == "내장" and vt.label.currentData() == "스킬", "AnimRef 탭 -> 영상찾기 탭 직접 전달")
        mw.stop_link()
        check(mw.link is None and not os.path.exists(os.path.join(ldir, "animtool.json")), "종료 시 접속 정보 삭제")

        # 메뉴 [파일 > AnimRef에서 가져오기]: query.json 을 직접 골라도 같은 동작
        write_query("직접", "스킬", [1.0, 2.0])
        QFileDialog.open_ret = (qf, "")
        mw.import_animref()
        check(vt.link_query and vt.link_query["id"] == "직접" and vt.label.currentData() == "스킬" and vt.hits.value() == 2, "query.json 직접 열기")
        check(any(v[3] == "AnimRef에서 가져오기…" for v in mw._actions.values()), "파일 메뉴에 항목")
        # 인식 방식: 다른 AI(OpenAI 호환) 선택 -> 서비스/주소 줄과 키 줄이 보이고, 키는 방식마다 따로 기억한다
        vt.engine.setCurrentIndex(vt.engine.findData("openai"))
        check(vt.oa_row.isVisible() and vt.key_row.isVisible() and not vt.btn_pull.isVisible(), "다른 AI 입력 줄 표시")
        check(vt.model.text() == "gpt-4o", "기본 모델: %s" % vt.model.text())
        vt.oa_preset.setCurrentIndex(vt.oa_preset.findData("Google Gemini"))
        check(vt.model.text() == "gemini-flash-latest", "서비스를 바꾸면 모델도 기본값으로: %s" % vt.model.text())
        # 무료 서비스: 안내 문구(무료 한도 + 키 받는 곳 링크)가 서비스마다 바뀐다
        vt.oa_preset.setCurrentIndex(vt.oa_preset.findData("🆓 OpenRouter 무료 (자동 선택)"))
        check(vt.model.text() == "openrouter/free" and "분당 20회" in vt.oa_info.text() and "openrouter.ai/keys" in vt.oa_info.text(), "무료 서비스 안내: %s" % vt.oa_info.text()[:60])
        vt.oa_preset.setCurrentIndex(vt.oa_preset.findData("🆓 Groq"))
        check(vt.model.text() != "openrouter/free" and "console.groq.com" in vt.oa_info.text(), "서비스 바꾸면 안내도 바뀜")
        # 모델 목록: 이미지를 볼 수 있어 보이는 것이 위, 고르면 모델 칸에 들어간다
        from qt_stub import QInputDialog  # noqa: E402
        V.QInputDialog = QInputDialog
        QInputDialog.item_ret = ("👁 qwen/qwen3.8-27b", True)
        vt._after_models(["whisper-large", "llama-3.3-70b", "qwen/qwen3.8-27b"])
        shown = QInputDialog.calls[-1][2]
        check(shown[0].startswith("👁 ") and shown[-1] == "whisper-large" and vt.model.text() == "qwen/qwen3.8-27b", "모델 목록에서 고르기: %s -> %s" % (shown, vt.model.text()))
        QInputDialog.item_ret = ("", False)
        vt.model.setText("keep")
        vt._after_models(["a"])
        check(vt.model.text() == "keep", "취소하면 그대로")
        vt._after_models([])
        check("모델 목록을 주지 않았습니다" in vt.engine_note.text(), "빈 목록 안내")
        vt.oa_preset.setCurrentIndex(vt.oa_preset.findData("Google Gemini"))
        vt.key.setText("g-secret")
        vt.engine.setCurrentIndex(vt.engine.findData("claude"))
        check(vt.key.text() != "g-secret" and not vt.oa_row.isVisible(), "Claude 로 바꾸면 다른 키: %r" % vt.key.text())
        vt.key.setText("sk-ant-1")
        vt.engine.setCurrentIndex(vt.engine.findData("openai"))
        check(vt.key.text() == "g-secret", "다른 AI 키가 따로 남아 있음")
        seen_cfg = {}
        V.vision.make_provider = lambda kind, cfg, post=None: (seen_cfg.update(kind=kind, cfg=dict(cfg)), vision.OllamaProvider("x", post=fake_post))[1]
        vt.provider()
        c = seen_cfg["cfg"]
        check(seen_cfg["kind"] == "openai" and c["vf_openai_key"] == "g-secret" and c["vf_oa_model"] == "gemini-flash-latest" and c["vf_oa_preset"] == "Google Gemini"
              and c["vf_claude_key"] == "sk-ant-1", "provider() 가 입력값을 넘김: %s" % {k: v for k, v in c.items() if k.startswith("vf_oa") or k.endswith("key")})
        vt.engine.setCurrentIndex(vt.engine.findData("ollama"))
        check(vt.btn_pull.isVisible() and not vt.key_row.isVisible(), "Ollama 로 돌아오면 모델 받기")
        # 영상을 받아도 캐릭터 추측은 자동으로 시작하지 않는다 (인식 방식을 고른 뒤 직접 시작)
        called = []
        _orig_guess = vt.start_guess
        vt.start_guess = lambda *a, **k: called.append(1)
        write_query("자동금지", "타격", [0.5], video=qf)  # 파일이 실제로 있는 경로
        QFileDialog.open_ret = (qf, "")
        mw.import_animref()
        check(vt.link_query and vt.link_query["id"] == "자동금지" and not called, "받기만 하고 추측은 자동 시작 안 함")
        check("캐릭터 추측 시작" in vt.link_banner.text(), "배너가 직접 시작하라고 안내: %s" % vt.link_banner.text())
        vt.start_guess = _orig_guess
        vt.link_query = None

        # AnimRef 클립 폴더 대화상자는 AnimRef 설정의 라이브러리/게임/clips 에서 시작
        os.environ["APPDATA"] = os.path.join(d, "appdata")
        os.makedirs(os.path.join(d, "appdata", "AnimRef"))
        clips = os.path.join(d, "lib", "모탈셸2", "clips")
        os.makedirs(clips)
        json.dump({"library_dir": os.path.join(d, "lib"), "game": "모탈셸2"}, open(os.path.join(d, "appdata", "AnimRef", "settings.json"), "w", encoding="utf-8"), ensure_ascii=False)
        seen_start = []
        QFileDialog.getExistingDirectory = staticmethod(lambda parent, title, start="", *a, **k: seen_start.append(start) or "")
        vt.add_animref()
        check(seen_start == [clips], "클립 폴더 대화상자 시작 위치: %s" % seen_start)

        # 탭: 크롬식 탭 줄 (번호 = 처음 추가된 순서)
        check(mw.tabs.count() == 9 and mw.tabs.tabText(5) == "영상찾기" and mw.tabs.indexOf(vt) == 5 and mw.tabs.tabText(7) == "AnimRef", "탭 9개 (AnimRef·AI모캡 포함)")
        mw.tabs.setCurrentIndex(5)
        check(mw.tabs.currentIndex() == 5 and mw.tabs.current_page() is vt, "탭 이동/표시")
        check(mw.tabs.pages[0] is mw.animref, "탭 줄 맨 앞은 AnimRef")
        check(mw.tabs.indexOf(mw.animref) == 7, "AnimRef 번호는 그대로(7)")
        mw.tabs._bar_changed(2)  # 탭 줄을 눌러 둘째 칸(번호 1)으로
        check(mw.tabs.currentIndex() == 1, "탭 클릭으로 이동")

        # 분리 / 합치기: 분리해도 번호로 이동하면 그 창이 앞으로 나오고, 닫으면 제자리로 돌아온다
        hub = mw.tabs.hub
        win = hub.detach(vt, None)
        check(win is not None and hub.is_detached(vt) and vt not in mw.tabs.pages and len(win.view.pages) == 1, "탭 분리")
        mw.tabs.setCurrentIndex(5)
        check(hub.view_of(vt) is win.view and win.view.current_page() is vt, "분리된 탭도 번호로 접근")
        hub.dock(vt)
        check(not hub.is_detached(vt) and mw.tabs.pages.index(vt) == 6 and win not in hub.floats, "합치기: 원래 순서 자리로")
        w2 = hub.detach(mw.tabs.widget(1), None)
        hub.redock_window(w2)
        check(mw.tabs.pages.index(mw.tabs.widget(1)) == 2 and not hub.floats, "창 닫으면 메인으로 복귀")
        a, b = mw.tabs.pages[1], mw.tabs.pages[2]
        hub.move(a, mw.tabs, len(mw.tabs.pages))
        check(mw.tabs.pages[-1] is a and mw.tabs.pages[1] is b and mw.tabs.indexOf(a) == 0, "순서 바꿔도 번호는 그대로")
        w3 = hub.detach(mw.animref, None)
        hub.redock_window(w3)
        check(mw.tabs.pages[0] is mw.animref, "AnimRef 를 분리했다 합쳐도 맨 앞")

    print("모두 통과" if not fails else "실패 %d개" % len(fails))
    sys.exit(1 if fails else 0)


main()
