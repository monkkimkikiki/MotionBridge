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

        def fake_em(helper, root, ue, aes, usmap, paths, out, fmts, variants, preset, blender, tex, keep_raw, ev, log):
            calls.append(("mesh", list(paths), out, fmts, variants, preset))
            return [os.path.join(out, "Models", "SK_Dwarf_static.fbx")]

        def fake_ea(helper, root, ue, aes, usmap, paths, out, on_event=None, kind="anim", **k):
            calls.append(("anim", list(paths), out, kind))
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
            check(calls[1][0] == "anim" and len(calls[1][1]) == 1 and calls[1][2].endswith("Animations"), "선택한 애니메이션: %s" % (calls[1],))
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

    print("모두 통과" if not fails else "실패 %d개" % len(fails))
    sys.exit(1 if fails else 0)


main()
