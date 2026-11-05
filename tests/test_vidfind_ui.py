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
        check("Dwarf" in prompts[0] and "StoneGolem" in prompts[0], "AI에게 게임 이름 목록을 알림")
        check(len(prompts) == 2, "장면마다 호출: %d" % len(prompts))
        r0, r1 = vt.rows
        check(r0.group.name == "Dwarf" and r0.chosen() == MESHES[0], "Dwarf 는 자동으로 맞는 메시: %s" % r0.chosen())
        check(r1.chosen() is None and r1.combo.currentData() is None, "모르는 몬스터는 후보 없음")
        check("몬스터 2종" in vt.status.text(), "상태: %s" % vt.status.text())

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
        check(not any("Hexapod" in vt.anim_list.item(i).data(A.Qt.ItemDataRole.UserRole) for i in range(vt.anim_list.count())), "다른 몬스터 제외")
        vt.label.setCurrentIndex(vt.label.findData("타격"))
        top = vt.anim_list.item(0).data(A.Qt.ItemDataRole.UserRole)
        check("Attack" in top or "Slam" in top, "타격 분류 최상위: %s" % top)
        vt.hits.setValue(3)
        check(vt.anim_list.item(0).data(A.Qt.ItemDataRole.UserRole).endswith("3Hit_Combo_1"), "3타 우선")

        # 기억: 다시 추측하면 모르던 몬스터가 StoneGolem 으로 바로 제안된다
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
        # ---------------- 특징·패턴으로 몬스터 찾기: 이름이 다 틀려도 '스핀 공격' 애니메이션이 있는 몬스터를 거꾸로 찾는다
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
        # 애니메이션 설명 검색: 고른 몬스터 안 / 모든 몬스터
        vt.confirmed = [sword]
        vt.mesh_list.clear()
        vt.mesh_list.addItem(V.QListWidgetItem("Swordman"))
        vt.mesh_list.item(0).setData(A.Qt.ItemDataRole.UserRole, sword)
        vt.mesh_list.selectAll()
        vt.anim_q.setText("스핀")
        vt.anim_all.setChecked(False)
        vt.search_anims_by_text()
        got = [vt.anim_list.item(i).data(A.Qt.ItemDataRole.UserRole) for i in range(vt.anim_list.count())]
        check(got == [spin], "고른 몬스터 안에서 스핀만: %s" % got)
        vt.anim_q.setText("slam")
        vt.search_anims_by_text()
        check(vt.anim_list.count() == 0 and "모든 몬스터" in vt.anim_note.text(), "없으면 전체 검색 안내")
        vt.anim_all.setChecked(True)
        vt.search_anims_by_text()
        check(vt.anim_list.count() == 1 and "StoneGolem" in vt.anim_list.item(0).text(), "전체에서 찾음: %s" % vt.anim_list.item(0).text() if vt.anim_list.count() else "없음")
        vt.anim_q.setText("")
        vt.search_anims_by_text()  # 비우면 기본 목록(분류/타수 기준)으로 돌아간다 — 오류 없이 지나가야 함
        # ---------------- 아는 정보: 영상에 나오는 몬스터를 미리 적으면 이름이 달라도 리소스를 먼저 찾아 AI 에게 알려 준다
        os.remove(V.NAMES_CACHE) if os.path.exists(V.NAMES_CACHE) else None
        vt.known.setPlainText("The Nameless Captive — 보스, 쇠사슬에 묶인 인간형\nDwarf Warrior")
        script["i"] = 0
        prompts.clear()
        vt.start_guess()
        check(prompts and "The Nameless Captive" in prompts[0] and "internal name probably: Prisoner" in prompts[0] and "쇠사슬" in prompts[0], "아는 정보가 AI 질문에 들어감")
        by = {r.group.name: r for r in vt.rows}
        check("Prisoner" in by and by["Prisoner"].chosen() == pris, "AI 가 '모름'이라 한 줄이 아는 정보와 합쳐져 Prisoner 로: %s" % list(by))
        check(by["Dwarf"].chosen() == MESHES[0] and "게임 속 이름: Dwarf Warrior" in by["Dwarf"].group.description, "AI 가 알아본 몬스터는 확신이 올라감")
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

        # ---------------- 몬스터 둘을 함께 뽑으면 애니메이션이 각자 몬스터 폴더로
        vt.index = assetidx.AssetIndex(MESHES, ANIMS)
        vt.mesh_list.clear()
        for m in (MESHES[0], MESHES[2]):
            it = V.QListWidgetItem(m)
            it.setData(A.Qt.ItemDataRole.UserRole, m)
            vt.mesh_list.addItem(it)
        vt.mesh_list.selectAll()
        cm = vt._char_map([a for a in ANIMS if "Dwarf_Idle" in a or "StoneGolem" in a])
        check(set(cm.values()) == {"Dwarf", "StoneGolem"}, "애니메이션마다 어울리는 몬스터 폴더: %s" % cm)

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


        # ---------------- 확신이 낮으면 자동으로 고르지 않음 (이름 없는 보스를 엉뚱한 몬스터로 확정하던 문제)
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
        vt.open_in_viewport()
        check(exported and exported[-1] == picks, "고른 애니메이션만 뽑음: %s" % exported)
        check(mw.pr.src is not None and len(mw.pr.src.animations) == 2 and mw.clip_combo.count() == 2, "소스 모션으로 열림: %d" % (len(mw.pr.src.animations) if mw.pr.src else -1))
        check(mw.ex_files.count() == 2, "추출 목록에도 올라감")
        # 더블클릭 = 그 하나만
        vt._anim_double_clicked(vt.anim_list.item(2))
        check(len(mw.pr.src.animations) == 1 and exported[-1] == [vt.anim_list.item(2).data(A.Qt.ItemDataRole.UserRole)], "더블클릭은 하나만 열기")

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
        check(vt.link_box._visible and "포로_내려찍기" in vt.link_banner.text() and vt.label.currentData() == "타격" and vt.hits.value() == 3, "배너/분류/타수")
        check("0.28" in vt.link_banner.text() and "3.3" in vt.link_banner.text() and "분류 타격" in vt.src_note.text(), "타격 시각과 분류를 같이 표시: %s" % vt.src_note.text())
        check("The Nameless Captive" in vt.known.toPlainText(), "AnimRef 캐릭터가 '아는 정보'에 들어감: %r" % vt.known.toPlainText())
        check("영상 파일이 없습니다" in vt.link_banner.text(), "영상 파일이 없으면 경고")
        vt.known.setPlainText("")
        # 아무것도 안 고르고 보내기 -> 안내, 요청은 유지
        QMessageBox.calls.clear()
        vt.send_result(False)
        check(QMessageBox.calls and vt.link_query is not None, "선택 없이 보내기 안내")
        # 후보 만들고 고른 뒤 보내기
        vt.rows = []
        vt.index = assetidx.AssetIndex(MESHES, ANIMS)
        vt.confirmed = [MESHES[0]]
        vt.mesh_list.clear()
        vt.mesh_list.addItem(V.QListWidgetItem("Dwarf"))
        vt.mesh_list.item(0).setData(A.Qt.ItemDataRole.UserRole, MESHES[0])
        vt.mesh_list.selectAll()
        vt._refill_anims()
        vt.anim_list.selectItems([0, 1])
        pick = vt.anim_list.item(0).data(A.Qt.ItemDataRole.UserRole)
        stem = os.path.basename(pick).split(".")[0]
        vt.last_files = ["/out/G/Dwarf/Animations/%s.psa" % stem, "/out/G/Dwarf/Models/SK_Dwarf_rigged.fbx"]
        vt.send_result(False)
        res = json.load(open(rf, encoding="utf-8"))
        check(res["animref_result"] == 1 and res["status"] == "confirmed" and res["clip_id"] == "포로_내려찍기", "result.json 6절 형식: %s" % res)
        check(res["animation"]["id"] == pick and res["animation"]["name"] == stem and res["animation"]["file"].endswith(stem + ".psa"), "고른 애니메이션: %s" % res["animation"])
        check(res["model_files"] == ["/out/G/Dwarf/Models/SK_Dwarf_rigged.fbx"] and res["export_dir"] == "/out/G/Dwarf" and "함께 고른" in res["note"], "모델 파일/폴더/메모: %s" % res)
        check(vt.link_query is None and not vt.link_box._visible and "알리지 못했습니다" in vt.link_banner.text() and "결과 가져오기" in vt.link_banner.text(), "AnimRef 꺼짐 안내: %s" % vt.link_banner.text())
        # '맞는 게 없음' -> rejected
        write_query("포로_내려찍기", "타격", [])
        send_open()
        vt.send_result(True)
        res = json.load(open(rf, encoding="utf-8"))
        check(res["status"] == "rejected" and res["clip_id"] == "포로_내려찍기" and "animation" not in res, "맞는 게 없음 결과: %s" % res)
        mw.stop_link()
        check(mw.link is None and not os.path.exists(os.path.join(ldir, "animtool.json")), "종료 시 접속 정보 삭제")

        # 메뉴 [파일 > AnimRef에서 가져오기]: query.json 을 직접 골라도 같은 동작
        write_query("직접", "스킬", [1.0, 2.0])
        QFileDialog.open_ret = (qf, "")
        mw.import_animref()
        check(vt.link_query and vt.link_query["id"] == "직접" and vt.label.currentData() == "스킬" and vt.hits.value() == 2, "query.json 직접 열기")
        check(any(v[3] == "AnimRef에서 가져오기…" for v in mw._actions.values()), "파일 메뉴에 항목")
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
        check(mw.tabs.count() == 7 and mw.tabs.tabText(5) == "영상찾기" and mw.tabs.indexOf(vt) == 5, "탭 7개")
        mw.tabs.setCurrentIndex(5)
        check(mw.tabs.currentIndex() == 5 and mw.tabs.current_page() is vt, "탭 이동/표시")
        mw.tabs._bar_changed(2)  # 탭 줄을 눌렀을 때
        check(mw.tabs.currentIndex() == 2, "탭 클릭으로 이동")

        # 분리 / 합치기: 분리해도 번호로 이동하면 그 창이 앞으로 나오고, 닫으면 제자리로 돌아온다
        hub = mw.tabs.hub
        win = hub.detach(vt, None)
        check(win is not None and hub.is_detached(vt) and vt not in mw.tabs.pages and len(win.view.pages) == 1, "탭 분리")
        mw.tabs.setCurrentIndex(5)
        check(hub.view_of(vt) is win.view and win.view.current_page() is vt, "분리된 탭도 번호로 접근")
        hub.dock(vt)
        check(not hub.is_detached(vt) and mw.tabs.pages.index(vt) == 5 and win not in hub.floats, "합치기: 원래 순서 자리로")
        w2 = hub.detach(mw.tabs.widget(1), None)
        hub.redock_window(w2)
        check(mw.tabs.pages.index(mw.tabs.widget(1)) == 1 and not hub.floats, "창 닫으면 메인으로 복귀")
        a, b = mw.tabs.pages[0], mw.tabs.pages[1]
        hub.move(a, mw.tabs, len(mw.tabs.pages))
        check(mw.tabs.pages[-1] is a and mw.tabs.pages[0] is b and mw.tabs.indexOf(a) == 0, "순서 바꿔도 번호는 그대로")

    print("모두 통과" if not fails else "실패 %d개" % len(fails))
    sys.exit(1 if fails else 0)


main()
