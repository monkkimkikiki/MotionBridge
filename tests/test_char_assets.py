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

        # 검색어 없이 '자동으로 찾기': 지운 상태에서도 메쉬가 있는 캐릭터가 알아서 나온다. 입력하면 바로 걸러진다
        pn.clear_index()
        pn.query.setText("")
        pn.index_all()
        names = [g.name for g in pn.groups]
        check("Swordman" in names and any(g.meshes for g in pn.groups), "자동 찾기: 검색어 없이 캐릭터 발견: %s" % names)
        n_all = pn.tree.topLevelItemCount()
        pn.query.setText("zzzz-없는-이름")
        check(pn.tree.topLevelItemCount() == 0, "입력하면 목록이 바로 걸러짐(없는 이름 -> 0)")
        pn.query.setText("")
        check(pn.tree.topLevelItemCount() == n_all, "비우면 전체로 복귀")

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

        # 플레이어블 캐릭터: 아이콘 + 맨 위 + 직접 표시/해제
        pn.clear_index()
        SKP = "/Game/Characters/Player/SKEL_Hero.SKEL_Hero"
        SKM = "/Game/Characters/Monster/SKEL_Ogre.SKEL_Ogre"
        pidx = pn._index()
        for r in [S.Rec("Proj/Content/Characters/Monster/SK_Ogre.uasset", "mesh", "SK_Ogre", SKM, bones=60),
                  S.Rec("Proj/Content/Characters/Monster/A_Roar.uasset", "anim", "A_Roar", SKM, frames=30, length=1.0, tracks=60),
                  S.Rec("Proj/Content/Characters/Monster/A_Walk.uasset", "anim", "A_Walk", SKM, frames=30, length=1.0, tracks=60),
                  S.Rec("Proj/Content/Characters/Player/SK_Hero.uasset", "mesh", "SK_Hero", SKP, bones=70)]:
            pidx.add(r)
        pn.query.setText("")
        pn._show(pidx, "", None)
        t0, t1 = pn.tree.topLevelItem(0).text(0), pn.tree.topLevelItem(1).text(0)
        check(t0.startswith(C.PLAYER_GUESS) and "Hero" in t0 and not t1.startswith(("🎮")), "이름으로 추정한 플레이어블은 '추정' 아이콘 + 맨 위(애니가 적어도): %s / %s" % (t0, t1))
        check("추정" in pn.tree.topLevelItem(0).tip, "툴팁에 이유")
        # 몬스터를 직접 플레이어블로 표시
        pn.tree.setCurrentItem(pn.tree.topLevelItem(1))
        pn.toggle_player()
        tt = [pn.tree.topLevelItem(i).text(0) for i in range(pn.tree.topLevelItemCount())]
        check(tt[0].startswith(C.PLAYER_MARK) and "Ogre" in tt[0] and tt[1].startswith(C.PLAYER_GUESS), "직접 표시한 것이 추정보다 위: %s" % tt)
        check(S.norm_key(SKM) in pidx.marks and pidx.marks[S.norm_key(SKM)] is True, "표시를 색인에 저장")
        # 하위 항목(메쉬)을 골라도 해제된다
        sub = pn.tree.topLevelItem(0).child(0).child(0)
        pn.tree.setCurrentItem(sub)
        pn.toggle_player()
        tt = [pn.tree.topLevelItem(i).text(0) for i in range(pn.tree.topLevelItemCount())]
        check(sum(1 for x in tt if x.startswith("🎮")) == 1 and "Hero" in tt[0], "하위 항목을 골라도 그 캐릭터가 해제: %s" % tt)
        pn.tree.clear()
        pn.toggle_player()
        check("먼저 고르세요" in pn.status.text(), "선택 없으면 안내")
        # 게임 데이터로 플레이어 확인: 기본 게임모드 -> 기본 폰 -> 메시 -> 스켈레톤. 이름이 전혀 달라도 찾는다
        pidx.marks.clear()
        pidx.add(S.Rec("Proj/Content/Characters/Monster/SK_Blob.uasset", "mesh", "SK_Blob", "/Game/Characters/Monster/SKEL_Blob.SKEL_Blob", bones=40))
        pidx.add(S.Rec("Proj/Content/Chars/Zed/SK_Zed.uasset", "mesh", "SK_Zed", "/Game/Chars/Zed/SKEL_Zed.SKEL_Zed", bones=70))
        C.extractor.find_players = lambda helper, game, ue, aes, usmap, on_event=None, filter_regex=None: {
            "configs": [{"file": "Config/DefaultEngine.ini", "key": "GlobalDefaultGameMode", "value": "/Game/Core/BP_GM.BP_GM_C"}],
            "pawns": [{"path": "Proj/Content/Core/BP_GM.uasset", "name": "BP_GM_C", "parent": "", "meshes": [], "default_pawn": "/Game/Core/BP_Hero.BP_Hero_C"},
                      {"path": "Proj/Content/Core/BP_Hero.uasset", "name": "BP_Hero_C", "parent": "", "meshes": ["/Game/Chars/Zed/SK_Zed.SK_Zed"], "camera": True},
                      {"path": "Proj/Content/Monsters/BP_Blob.uasset", "name": "BP_Blob_C", "parent": "", "meshes": ["/Game/Characters/Monster/SK_Blob.SK_Blob"]}]}
        pn.find_players()
        tt = [pn.tree.topLevelItem(i).text(0) for i in range(pn.tree.topLevelItemCount())]
        zed = [x for x in tt if "Zed" in x]
        check(zed and zed[0].startswith(C.PLAYER_MARK) and tt.index(zed[0]) == 0, "게임 데이터로 확인한 플레이어가 맨 위(이름과 무관): %s" % tt)
        check(not any(("Blob" in x) and x.startswith("🎮") for x in tt), "몬스터 폰은 표시 안 함")
        check(pidx.proofs and "기본 플레이어 폰" in list(pidx.proofs.values())[0], "이유 기록: %s" % pidx.proofs)
        # 검사 기록을 지워도 직접 정한 표시는 남고, 데이터 확인 결과는 같이 지워진다
        pidx.marks[S.norm_key(SKM)] = True
        pn.clear_index()
        check(S.norm_key(SKM) in pn._index().marks and not pn._index().proofs, "검사 기록 지워도 직접 표시 유지, 확인 결과는 지움")

        # 기록 지우기
        pn.clear_index()
        check(not pn._index().all() and pn.tree.topLevelItemCount() == 0, "검사 기록 지우기")

    print("모두 통과" if not fails else "실패 %d개" % len(fails))
    sys.exit(1 if fails else 0)


main()
