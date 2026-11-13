"""추출 탭: 게임 폴더를 검사하면 전용 UE 프리셋 추천 · 3단계 판정 · 예전에 쓴 AES 키/매핑 자동 채우기."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qt_stub  # noqa: E402

qt_stub.install()
import test_gamepresets as TG  # noqa: E402
from motionbridge.core import bridge, extractor, gamescan  # noqa: E402
from motionbridge.ui import app as A  # noqa: E402


def main():
    with tempfile.TemporaryDirectory() as d:
        os.environ["HOME"] = d
        bridge.CONFIG_PATH = os.path.join(d, "cfg.json")
        bridge.CONFIG_DIR = os.path.join(d, "cfgdir")
        extractor.list_games = lambda helper: ["GAME_UE5_0", "GAME_UE5_4", "GAME_BlackMythWukong", "GAME_Valorant"]
        root = TG.make_wukong(os.path.join(d, "lib"))
        mw = A.MainWindow()
        mw.ex_game.setText(root)
        mw._after_scan(gamescan.scan_game(root))
        assert mw.ex_ue.currentData() == "GAME_BlackMythWukong", mw.ex_ue.currentData()
        assert mw.ex_ue.findData("GAME_Valorant") >= 0, "도우미가 알려 준 전체 목록으로 맞춤"
        t = mw.ex_verdict.text()
        assert "🟡" in t and "AES" in t and "usmap" in t and "2358720" in t, t
        assert mw._verdict_state == "yellow"
        # 키·매핑을 넣고 검색이 성공하면 기억 → 다른 게임을 봤다가 돌아와도 자동으로 채워짐
        key = "0x" + "CD" * 32
        um = os.path.join(d, "Wukong_Release.usmap")
        open(um, "wb").write(b"u")
        mw.ex_aes.setText(key)
        mw.ex_usmap.setText(um)
        mw.ex_links.setText("https://example.com/keys 패치 1.0.8 기준")
        mw._after_search(["b1/Content/A.uasset"])
        other = os.path.join(d, "lib", "steamapps", "common", "Unity")
        os.makedirs(other)
        open(os.path.join(other, "UnityPlayer.dll"), "wb").write(b"")
        mw.ex_game.setText(other)
        called = []
        mw.unity_tab.scan_game = lambda: called.append(1)   # UnityPy 설치 없이: 자동으로 이어지는지만 본다
        mw._after_scan(gamescan.scan_game(other))
        # 유니티 게임이면 빨강 판정 대신 같은 탭 안에서 유니티 추출로 이어진다 (별도 탭 없음)
        assert "유니티" in mw.ex_verdict.text() and "🟢" in mw.ex_verdict.text(), mw.ex_verdict.text()
        assert called == [1], "검사가 유니티로 판별하면 Unity 에셋 검사가 자동으로 이어진다"
        assert mw.ex_aes.text() == "", "다른 게임으로 바꾸면 이전 게임의 키가 남지 않음"
        mw.ex_game.setText(root)
        mw._after_scan(gamescan.scan_game(root))
        assert mw.ex_aes.text() == key and mw.ex_usmap.text() == um and mw.ex_ue.currentData() == "GAME_BlackMythWukong"
        assert "example.com" in mw.ex_links.text() and "패치" in mw.ex_links.text(), mw.ex_links.text()
        assert "🟢" in mw.ex_verdict.text() and "예전 설정" in mw.ex_verdict.text(), mw.ex_verdict.text()
        # 원인 코드가 붙은 오류는 '할 일'이 같이 나온다
        msg = extractor.explain(extractor.ExtractError("AES 키가 이 게임과 맞지 않습니다", "ERR_INVALID_AES"))
        assert "전용 항목" in msg and "ERR_INVALID_AES" in msg
    print("test_ui_gamepreset ALL OK")


if __name__ == "__main__":
    main()
