"""에셋 찾기 · 캐릭터/스켈레톤 보기 (가짜 Qt + 가짜 도우미)."""
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qt_stub  # noqa: E402

qt_stub.install()
from motionbridge.core import bridge, skelgraph as S  # noqa: E402
from motionbridge.ui import app as A  # noqa: E402
from motionbridge.ui import char_assets as C  # noqa: E402
from qt_stub import QMessageBox  # noqa: E402

fails = []


def check(c, m):
    if not c:
        fails.append(m)
        print("FAIL", m)


SK = "/Game/Characters/Swordman/SKEL_Swordman.SKEL_Swordman"
P = "Proj/Content/Characters/Swordman/"
ALL = [P + "SK_Swordman.uasset", P + "SKEL_Swordman.uasset", P + "A_Slash.uasset", P + "A_Bad.uasset", "Proj/Content/Common/A_Bow.uasset",
       P + "T_Body.uasset"]
DATA = {
    P + "SK_Swordman.uasset": dict(kind="mesh", name="SK_Swordman", skeleton=SK, bones=70),
    P + "SKEL_Swordman.uasset": dict(kind="skeleton", name="SKEL_Swordman", bones=70),
    P + "A_Slash.uasset": dict(kind="anim", name="A_Slash", skeleton=SK, frames=30, length=1.0, tracks=70),
    P + "A_Bad.uasset": dict(kind="anim", name="A_Bad", skeleton=SK, frames=0, length=0.0, tracks=0),
    "Proj/Content/Common/A_Bow.uasset": dict(kind="anim", name="A_Bow", skeleton=SK, frames=60, length=2.0, tracks=70),
}


def main():
    with tempfile.TemporaryDirectory() as d:
        os.environ["HOME"] = d
        bridge.CONFIG_PATH = os.path.join(d, "cfg.json")
        bridge.CONFIG_DIR = d
        mw = A.MainWindow()
        pn = mw.char_panel
        check(isinstance(pn, C.CharAssetPanel), "패널이 추출 탭에 있음")
        check(abs(mw._split_ratio - 0.47) < 1e-6, "기본 화면 비율 47:53")
        mw.ex_game.setText(os.path.join(d, "game"))
        mw._extract_args = lambda: ("helper", "G", "GAME_UE5_4", None, None)
        W = type("W", (), {"cancel": threading.Event(), "progress": type("P", (), {"emit": lambda *a: None})()})()
        mw.run_async = lambda fn, done, label, **k: done(fn(W))
        inspected = []
        C.extractor.list_assets = lambda helper, game, ue, aes, usmap, pat, limit=5000, on_event=None: [p for p in ALL if __import__("re").search(pat, p)]

        def fake_inspect(helper, game, ue, aes, usmap, paths, on_event=None, chunk=400, cancel=None):
            inspected.extend(paths)
            return [S.Rec(p, path_data["kind"], **{k: v for k, v in path_data.items() if k != "kind"}) for p in paths if p in DATA for path_data in [DATA[p]]], set(paths)

        C.extractor.inspect_assets = fake_inspect
        pn.query.setText("swordman")
        pn.search()
        check(P + "T_Body.uasset" not in inspected and len(inspected) == 4, "텍스처는 안 열고 4개만 검사: %s" % inspected)
        check(len(pn.groups) == 1 and pn.groups[0].name == "Swordman", "캐릭터 1명")
        g = pn.groups[0]
        check([a.name for a in g.anims] == ["A_Slash"] and [r.name for r, _w in g.rejected] == ["A_Bad"], "재생 가능만 애니메이션: %s" % [a.name for a in g.anims])
        check(pn.tree.topLevelItemCount() == 1 and "재생 가능" in pn.status.text() or "애니메이션 1개" in pn.status.text(), "상태: " + pn.status.text())

        # 한 번 읽은 파일은 다시 읽지 않는다
        inspected.clear()
        pn.search()
        check(inspected == [], "재검색은 새로 읽지 않음")

        # 게임 전체 색인: 공용 폴더 애니(A_Bow)도 같은 스켈레톤이라 붙는다
        QMessageBox.question_yes = True
        pn.index_all()
        check("A_Bow" in [a.name for a in pn.groups[0].anims], "게임 전체 색인으로 공용 폴더 애니도 연결: %s" % [a.name for a in pn.groups[0].anims])

        # 더블클릭/버튼은 추출으로 이어짐
        calls = []
        mw.extract_for_char = lambda paths, kind: calls.append((list(paths), kind))
        top = pn.tree.topLevelItem(0)
        anim_leaf = None
        stack = [top]
        while stack:
            x = stack.pop()
            dd = x.data(0, C.ROLE)
            if dd and dd[0] == "anim":
                anim_leaf = x
            for i in range(x.childCount()):
                stack.append(x.child(i))
        pn._double_clicked(anim_leaf)
        check(calls and calls[-1][1] == "anim" and calls[-1][0][0].endswith(("A_Slash.uasset", "A_Bow.uasset")), "더블클릭 -> 애니 추출/미리보기: %s" % calls)

        # 실제 추출 흐름: 종류 전환 + GLB 자동 체크
        del mw.extract_for_char
        got = []
        mw._extract_paths = lambda paths: got.append(list(paths))
        mw.ex_glb.setChecked(False)
        mw.extract_for_char([P + "SK_Swordman.uasset"], "mesh")
        check(mw.ex_kind.currentData() == "mesh" and mw.ex_glb.isChecked() and got == [[P + "SK_Swordman.uasset"]], "메쉬 미리보기는 GLB 켜고 추출")
        mw.set_extract_list([P + "A_Slash.uasset"], "anim")
        check(mw.ex_list.all_paths() == [P + "A_Slash.uasset"] and mw.ex_kind.currentData() == "anim", "추출 목록으로 보내기")

        # 기록 지우기
        pn.clear_index()
        check(not pn._index().all() and pn.tree.topLevelItemCount() == 0, "검사 기록 지우기")

    print("모두 통과" if not fails else "실패 %d개" % len(fails))
    sys.exit(1 if fails else 0)


main()
