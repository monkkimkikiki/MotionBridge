"""영상찾기: 리소스 이름이 병음인 게임(오공)에서도 뜻 풀이로 메시 후보를 낸다 (가짜 Qt + 가짜 AI)."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qt_stub  # noqa: E402

qt_stub.install()
from motionbridge.core import assetidx, bridge, frames, nameglossary, vision  # noqa: E402
from motionbridge.ui import app as A  # noqa: E402
from motionbridge.ui import vidfind_tab as V  # noqa: E402
from qt_stub import QFileDialog  # noqa: E402

P = "b1/Content/00Main/Characters/"
CH = ["HFM_HuanWuZhe_01a", "psd_daoshi_01", "HYS_XiangGongXing_01", "LYS_ChiFaGui_01A", "LYS_Mo3", "GYCY_TuFei_01", "LYS_RuYiLong_01",
      "GYCY_Lang_02", "GYCY_Lang_03", "GYCY_Seng_03", "hgs_zhengzhang", "HYS_NiuMo_01", "LYS_JinChi_01", "GYCY_YouHun_01", "HFM_ShiZi_02", "SZLC_YeZhu_M"]
MESHES = ["%s%s/Meshes/SK_%s.uasset" % (P, c, c) for c in CH]
ANIMS = ["%s%s/Anims/A_%s_Attack01.uasset" % (P, c, c) for c in CH]
GLOSS = {"GYCY_YouHun": {"zh": "游魂", "en": "Wandering Wight, giant-headed ghost boss", "ko": "떠도는 영혼", "kind": "boss", "sure": 0.8},
         "GYCY_Lang": {"zh": "狼", "en": "wolf guai", "ko": "늑대 요괴", "kind": "enemy", "sure": 0.9},
         "psd_daoshi": {"zh": "道士", "en": "Taoist priest", "ko": "도사", "kind": "enemy", "sure": 0.8},
         "GYCY_TuFei": {"zh": "土匪", "en": "bandit", "ko": "산적", "kind": "enemy", "sure": 0.8}}
fails = []


def check(ok, m):
    if not ok:
        fails.append(m)
        print("FAIL", m)


def main():
    with tempfile.TemporaryDirectory() as d:
        os.environ["HOME"] = d
        bridge.CONFIG_PATH = os.path.join(d, "cfg.json")
        bridge.CONFIG_DIR = d
        V.INDEX_CACHE = os.path.join(d, "assets.json")
        V.FRAME_DIR = os.path.join(d, "frames")
        mw = A.MainWindow()
        vt = mw.vidfind
        vt.memory = assetidx.MatchMemory(os.path.join(d, "matches.json"))
        mw._extract_args = lambda: ("helper", "G:/Wukong", "GAME_BlackMythWukong", None, None)
        V.extractor.list_assets = lambda *a, **k: MESHES + ANIMS
        prompts = []

        def fake_post(url, payload, headers, timeout):
            if url.endswith("/api/tags"):
                return {"models": [{"name": "qwen2.5vl:7b"}]}
            p = payload["messages"][0]["content"]
            prompts.append(p)
            if "decode what creature" in p:                      # ① 이름 뜻 풀이
                return {"message": {"content": json.dumps(GLOSS, ensure_ascii=False)}}
            if "closed list of this game's internal character asset names" in p:   # ③ 뜻이 붙은 목록에서 다시 고르기
                if "떠도는 영혼" in p.split("List:")[0]:
                    return {"message": {"content": json.dumps({"matches": [{"name": "SK_GYCY_YouHun_01", "confidence": 0.8, "reason": "游魂"}]})}}
                return {"message": {"content": json.dumps({"matches": [{"name": "SK_GYCY_YouHun_01 (Wandering Wight)", "confidence": 0.7, "reason": "large head"},
                                                                        {"name": "SK_HFM_ShiZi_02", "confidence": 0.2}]})}}
            # ② 장면 인식: 이름을 못 댄다
            return {"message": {"content": json.dumps({"characters": [{"name": "unknown", "confidence": 0.0,
                                                                         "description": "A humanoid boss with a disproportionately large head"}]})}}

        V.vision.make_provider = lambda kind, cfg, post=None: vision.OllamaProvider("qwen2.5vl:7b", post=fake_post)
        V.frames.find_ffmpeg = lambda *a: None

        def fake_sample(ff, srcs, out, per, cap, prog=None):
            os.makedirs(out, exist_ok=True)
            pth = os.path.join(out, "f0.jpg")
            open(pth, "wb").write(b"\xff\xd8 fake")
            return [frames.Frame(pth, srcs[0], 0)]

        V.frames.sample_sources = fake_sample
        if hasattr(V, "framepick"):
            V.framepick.sample_sources_best = lambda ff, srcs, out, per, cap, prog=None: fake_sample(ff, srcs, out, per, cap, prog)
        V.panel.make_sheet = lambda paths, out, **k: (open(out, "wb").write(b"\xff\xd8 sheet"), out)[1]
        vt.engine.setCurrentIndex(vt.engine.findData("ollama"))
        vt.hint.setText("Black Myth Wukong")
        vt.known.setPlainText("떠도는 영혼")
        QFileDialog.open_names_ret = (["/v/wukong.mp4"], "")
        vt.add_files()
        vt.start_guess()
        kinds = ["gloss" if "decode what creature" in p else "pick" if "closed list of this game" in p
                 else "expand" if "Expand the clue" in p else "see" for p in prompts]   # expand = '아는 정보' AI 확장 검색
        check(kinds.count("gloss") == 1, "이름 뜻 풀이를 한 번 물음: %s" % kinds)
        see = next((p for p in prompts if "decode what creature" not in p and "closed list of this game" not in p and "Expand the clue" not in p), "")
        check("(Wandering Wight" in see and "pinyin" in see, "인식 AI 에게 주는 이름 목록에 뜻이 붙음")
        check("SK_GYCY_Lang_03" not in see or "SK_GYCY_Lang_02" not in see, "같은 캐릭터의 여러 벌은 하나만 알림")
        gl = nameglossary.Glossary.load("Black Myth Wukong")
        check(gl.meaning("SK_GYCY_YouHun_01").startswith("Wandering Wight"), "풀이가 게임 이름으로 저장됨: %s" % list(gl.entries)[:3])
        rows = vt.rows
        check(len(rows) >= 1, "줄이 만들어짐")
        youhun = MESHES[CH.index("GYCY_YouHun_01")]
        for r in rows:
            cands = [r.combo.itemData(i) for i in range(r.combo.count())]
            check(youhun in cands, "모름/한국어 이름 줄에도 뜻 풀이로 메시 후보가 나옴: %s -> %s" % (r.group.name, cands))
            check(r.chosen() == youhun, "가장 유력한 후보가 골라져 있음: %s" % r.chosen())
        check(kinds.count("pick") >= 1, "못 찾은 줄은 뜻이 붙은 목록에서 AI 가 다시 고름: %s" % kinds)
        check(any("游魂" in ln for ln in vt.status.text().splitlines()), "AI 가 고른 결과('SK_' 를 붙여 답해도 받아 줌): %s" % vt.status.text()[-120:])
        check(any("뜻 풀이로 찾음" in ln for ln in vt.status.text().splitlines()), "상태 줄에 뜻 풀이 결과: %s" % vt.status.text()[-200:])
        # 검색 칸에 한국어 뜻을 적어도 찾는다
        n_before = len(prompts)
        vt._on_search(rows[0], "늑대")
        found = [rows[0].combo.itemData(i) for i in range(rows[0].combo.count())]
        check(any("GYCY_Lang" in str(x) for x in found) and len(prompts) == n_before, "검색 칸에 '늑대' -> GYCY_Lang (AI 호출 없이): %s" % found)
        # 두 번째 실행: 뜻 풀이를 다시 묻지 않는다
        vt.start_guess()
        check(sum(1 for p in prompts[n_before:] if "decode what creature" in p) == 0, "이미 풀린 이름은 다시 묻지 않음")
    print("모두 통과" if not fails else "실패 %d개" % len(fails))
    sys.exit(1 if fails else 0)


main()
