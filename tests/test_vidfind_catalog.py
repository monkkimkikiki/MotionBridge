"""'영상으로 찾기' 추측 흐름 시험 (카탈로그 경로): 이름 목록을 자르지 않고 전부 알리는지, 화면 글자로 바로 찾는지, AI 가 줄 통째로 답해도 정리되는지."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qt_stub  # noqa: E402

qt_stub.install()
import cv2  # noqa: E402
import numpy as np  # noqa: E402

from motionbridge.core import assetidx, bridge, frames, gamenames, vision  # noqa: E402
from motionbridge.ui import app as A  # noqa: E402
from motionbridge.ui import vidfind_tab as V  # noqa: E402
from qt_stub import QFileDialog  # noqa: E402

C = "MortalShell2/Content/Sparta/Characters/"
# 알파벳 앞쪽(Mob000~)이 120종을 채우고, 보스/미니보스는 알파벳 뒤쪽 (예전 방식이면 AI 에게 아예 안 보이는 구성)
MESHES = [C + "Enemies/Mob%03d/Art/Mesh/SK_Mob%03d.uasset" % (i, i) for i in range(140)]
MESHES += [C + "Bosses/Swordman/Art/Mesh/SK_Swordman.uasset", C + "Bosses/Swordman/Art/Mesh/SK_Swordman_Skeleton.uasset",
           C + "Enemies/TarredVestige/Art/Mesh/SK_TarredVestige.uasset", C + "Bosses/Hexapod/Art/Mesh/SK_Hexapod.uasset"]
ANIMS = [C + "Bosses/Swordman/Animation/A_Swordman_Attacks_Spins.uasset", C + "Enemies/TarredVestige/Animation/A_TarredVestige_Attacks_Swipe.uasset"]
NAMES = [["en", "ST_Core_Bosses", "Boss_Swordman", "The Nameless Captive"], ["ko", "ST_Core_Bosses", "Boss_Swordman", "이름 없는 포로"],
         ["en", "ST_Core_MiniBosses", "TarredVestige_MiniBoss", "Tainted Vestige"], ["ko", "ST_Core_MiniBosses", "TarredVestige_MiniBoss", "오염된 자취"]]

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
        V.NAMES_CACHE = os.path.join(d, "names.json")
        V.FRAME_DIR = os.path.join(d, "frames")
        mw = A.MainWindow()
        vt = mw.vidfind
        vt.memory = assetidx.MatchMemory(os.path.join(d, "matches.json"))
        mw._extract_args = lambda: ("helper", "G:/Sparta", "GAME_UE5_4", None, None)
        gamenames.NameBook(NAMES).save(V.NAMES_CACHE, vt.cfg.get("game_dir", ""))
        V.extractor.list_assets = lambda *a, **k: MESHES + ANIMS

        prompts = {"read": [], "sheet": []}

        def fake_post(url, payload, headers, timeout):
            if url.endswith("/api/tags"):
                return {"models": [{"name": "qwen2.5vl:7b"}]}
            text = payload["messages"][0]["content"]
            if "Transcribe every piece of on-screen text" in text:
                prompts["read"].append(text)
                return {"message": {"content": json.dumps({"texts": ["오염된 자취", "타버린 조각상"]})}}   # 두 번째는 아이템 이름
            prompts["sheet"].append(text)
            # AI 가 목록 줄을 통째로 따라 쓰고, 같은 캐릭터를 영어 이름으로도 부르는 경우
            chars = [{"name": "Swordman = The Nameless Captive / 이름 없는 포로 [boss]", "confidence": 0.8, "description": "huge sword", "alternatives": ["Hexapod [boss]"]},
                     {"name": "Tainted Vestige", "confidence": 0.6, "description": "burning tree creature"}]
            return {"message": {"content": json.dumps({"characters": chars})}}

        V.vision.make_provider = lambda kind, cfg, post=None: vision.OllamaProvider(cfg.get("vf_ollama_model") or "qwen2.5vl:7b", post=fake_post)
        V.frames.find_ffmpeg = lambda *a: None

        def fake_sample(ff, srcs, out, per, cap, prog=None):
            os.makedirs(out, exist_ok=True)
            res = []
            for i in range(4):   # 시트를 만들 수 있게 진짜 그림 파일로
                pth = os.path.join(out, "f%d.jpg" % i)
                cv2.imwrite(pth, np.full((90, 160, 3), 60 + i * 30, np.uint8))
                res.append(frames.Frame(pth, srcs[0], i))
            return res

        V.frames.sample_sources = fake_sample
        QFileDialog.open_names_ret = (["/v/a.mp4"], "")
        vt.add_files()
        vt.ck_fast.setChecked(True)
        vt.start_guess()

        check(len(prompts["sheet"]) == 1, "시트 한 장 -> AI 호출 1번: %d" % len(prompts["sheet"]))
        sp = prompts["sheet"][0] if prompts["sheet"] else ""
        n_lines = sum(1 for ln in sp.splitlines() if " [" in ln and ln.rstrip().endswith("]") and ln[:3] in ("Mob", "Swo", "Tar", "Hex"))
        check(n_lines == 144 - 1 + 0 or n_lines >= 143, "캐릭터 143명 전부가 목록에 들어감(예전엔 120종만): %d" % n_lines)
        check("Mob139 [enemy]" in sp and "Hexapod [boss]" in sp, "알파벳 뒤쪽/앞쪽 모두 전달")
        check("TarredVestige = Tainted Vestige / 오염된 자취 [miniboss]" in sp, "게임 속 이름이 줄에 붙어 감")
        check("Swordman_Skeleton" not in sp, "메시 부품(_Skeleton)은 목록에서 빠짐")
        check(0 < len(prompts["read"]) <= 3, "화면 글자 읽기는 장면 3장 이하: %d" % len(prompts["read"]))
        names = sorted(r.group.name for r in vt.rows)
        check(names == ["Swordman", "TarredVestige"], "줄 통째/영어 이름 답이 대표 메시 이름으로 정리되고 글자로 찾은 것과 합쳐짐: %s" % names)
        rows = {r.group.name: r for r in vt.rows}
        check(rows["TarredVestige"].group.confidence >= 0.9, "글자로 확인한 캐릭터는 확신 높음: %.2f" % rows["TarredVestige"].group.confidence)
        check("화면 글자" in rows["TarredVestige"].group.description, "근거 표시: " + rows["TarredVestige"].group.description)
        check(rows["TarredVestige"].chosen() == [m for m in MESHES if m.endswith("SK_TarredVestige.uasset")][0], "메시 자동 선택: %s" % rows["TarredVestige"].chosen())
        check(rows["Swordman"].chosen() == [m for m in MESHES if m.endswith("SK_Swordman.uasset")][0], "Swordman 은 몸통 메시(부품 아님): %s" % rows["Swordman"].chosen())
        check("143명" in vt.status.text() or "143" in vt.status.text(), "상태에 캐릭터 수 안내: %s" % vt.status.text()[:200])

        # 글자 읽기를 끄면(설정) 추가 호출이 없다
        prompts["read"].clear()
        vt.cfg["vf_read_text"] = False
        vt.start_guess()
        check(prompts["read"] == [], "vf_read_text=False 면 글자 읽기 안 함")

        # ('게임 속 이름으로 찾기' 칸은 '아는 정보' + AI 확장 검색으로 흡수되어 UI 에서 빠졌다)

    if fails:
        print("\n실패 %d개" % len(fails))
        sys.exit(1)
    print("test_vidfind_catalog ALL OK")


main()
