"""스켈레톤 기반 묶기: 파일 안 데이터(스켈레톤 연결, 프레임, 트랙)로 '재생 가능한 애니메이션' 만 고른다."""
import os
import stat
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from motionbridge.core import extractor, skelgraph as S  # noqa: E402

fails = []


def check(c, m):
    if not c:
        fails.append(m)
        print("FAIL", m)


SK_SW = "/Game/Characters/Swordman/Meshes/SKEL_Swordman.SKEL_Swordman"
SK_BOSS = "/Game/Characters/Boss/Meshes/SKEL_Boss.SKEL_Boss"
R = S.Rec
RECS = [
    R("Proj/Content/Characters/Swordman/Meshes/SK_Swordman.uasset", "mesh", "SK_Swordman", SK_SW, bones=70),
    R("Proj/Content/Characters/Swordman/Meshes/SKEL_Swordman.uasset", "skeleton", "SKEL_Swordman", bones=70),
    R("Proj/Content/Characters/Swordman/Anim/A_Slash.uasset", "anim", "A_Slash", SK_SW, frames=40, length=1.3, tracks=70),
    R("Proj/Content/Characters/Swordman/Anim/A_Idle.uasset", "anim", "A_Idle", SK_SW, frames=60, length=2.0, compressed=True),
    R("Proj/Content/Characters/Swordman/Anim/A_Empty.uasset", "anim", "A_Empty", SK_SW, frames=0, length=0.0, tracks=0),
    R("Proj/Content/Characters/Swordman/Anim/A_NoSkel.uasset", "anim", "A_NoSkel", "", frames=10, length=0.3, tracks=70),
    R("Proj/Content/Characters/Swordman/Anim/A_NoKeys.uasset", "anim", "A_NoKeys", SK_SW, frames=12, length=0.4, tracks=0),
    R("Proj/Content/Characters/Swordman/Anim/AM_Combo.uasset", "montage", "AM_Combo", SK_SW, sequences=["/Game/Characters/Swordman/Anim/A_Slash.A_Slash"]),
    R("Proj/Content/Characters/Swordman/Anim/AM_Broken.uasset", "montage", "AM_Broken", SK_SW, sequences=[]),
    # 폴더는 Swordman 안인데 다른 스켈레톤을 쓰는 애니: 폴더만 보면 섞이지만 데이터로는 보스 것
    R("Proj/Content/Characters/Swordman/Anim/A_BossRoar.uasset", "anim", "A_BossRoar", SK_BOSS, frames=30, length=1.0, tracks=90),
    R("Proj/Content/Characters/Boss/Meshes/SK_Boss.uasset", "mesh", "SK_Boss", SK_BOSS, bones=90),
    # 폴더는 공용(Common)인데 스워드맨 스켈레톤을 쓰는 애니: 이름 검색으로는 못 찾지만 데이터로는 스워드맨 것
    R("Proj/Content/Common/Anim/A_Emote_Bow.uasset", "anim", "A_Emote_Bow", SK_SW, frames=90, length=3.0, tracks=70),
]

# 경로 정규화
check(S.norm_key("/Game/Characters/Swordman/Meshes/SKEL_Swordman.SKEL_Swordman") == "characters/swordman/meshes/skel_swordman", "정규화(오브젝트 경로)")
check(S.norm_key("Proj/Content/Characters/Swordman/Meshes/SKEL_Swordman.uasset") == "characters/swordman/meshes/skel_swordman", "정규화(파일 경로)")
check(S.norm_key("") == "", "빈 경로")

# 재생 가능 판정
byname = {r.name: r for r in RECS}
check(byname["A_Slash"].playable() and byname["A_Idle"].playable(), "트랙/압축 데이터가 있으면 재생 가능")
check(not byname["A_Empty"].playable() and "프레임 없음" in byname["A_Empty"].problems(), "프레임 0 은 제외")
check("스켈레톤 연결 확인 안 됨" in byname["A_NoSkel"].problems(), "스켈레톤 모르면 제외")
check("키(트랙) 데이터 없음" in byname["A_NoKeys"].problems(), "키 없으면 제외")
check(byname["AM_Combo"].playable() and not byname["AM_Broken"].playable(), "몽타주는 시퀀스를 참조해야")

groups = S.build_groups(RECS)
names = {g.name: g for g in groups}
check(set(names) == {"Swordman", "Boss"}, "캐릭터 2명: %s" % list(names))
sw, boss = names["Swordman"], names["Boss"]
check([m.name for m in sw.meshes] == ["SK_Swordman"] and sw.skeleton_assets, "메쉬 + 스켈레톤 자산")
an = sorted(a.name for a in sw.anims)
check(an == ["AM_Combo", "A_Emote_Bow", "A_Idle", "A_Slash"], "스워드맨 애니는 스켈레톤으로 연결된 재생 가능한 것만: %s" % an)
check("A_BossRoar" in [a.name for a in boss.anims] and "A_BossRoar" not in an, "폴더가 달라도 데이터로 보스 것")
rej = {r.name: why for r, why in sw.rejected}
check(set(rej) == {"A_Empty", "A_NoKeys", "AM_Broken"} and "A_NoSkel" not in rej, "뺀 것과 이유: %s" % rej)
check([g.name for g in S.build_groups(RECS, "swordman")] == ["Swordman"], "검색어로 거르기")
check(S.build_groups(RECS, "swordman,boss") == [], "쉼표 = 모두 포함")
check("캐릭터 2명" in S.summarize(groups), "요약")
al, mesh = S.anims_for_mesh(RECS, "/Game/Characters/Swordman/Meshes/SK_Swordman.SK_Swordman")
check(mesh is not None and len(al) == 4, "메쉬 -> 연결된 재생 가능 애니: %d" % len(al))
check(S.anims_for_mesh(RECS, "Nope")[0] == [], "모르는 메쉬")
check(not S.worth_inspecting("Proj/Content/Characters/X/Textures/T_Body_D.uasset") and S.worth_inspecting("Proj/Content/Characters/X/Anim/A_Run.uasset"), "텍스처는 미리 거름")

# 이름 후보 -> 데이터로 걸러 다시 정하기
from motionbridge.core.assetidx import AnimCandidate as AC  # noqa: E402
name_cands = [AC("Proj/Content/Characters/Swordman/Anim/A_Slash.uasset", 0.9, "이름 일치"),
              AC("Proj/Content/Characters/Swordman/Anim/A_BossRoar.uasset", 0.8, "폴더 일치"),   # 검사했더니 보스 스켈레톤 -> 빠져야
              AC("Proj/Content/Characters/Swordman/Anim/A_Empty.uasset", 0.7, "이름 일치"),    # 재생 불가 -> 빠져야
              AC("Proj/Content/Characters/Swordman/Anim/A_Unknown.uasset", 0.6, "이름 일치")]  # 검사 전 -> 미확인으로 뒤에
out, st, dropped = S.refine_by_skeleton(RECS, "/Game/Characters/Swordman/Meshes/SK_Swordman", name_cands, lambda p, sc, why: AC(p, sc, why))
names_out = [os.path.basename(c.path) for c in out]
check(st == "linked" and dropped == 2, "걸러낸 개수/상태: %s %s" % (st, dropped))
check(names_out[0] == "A_Slash.uasset" and "A_Emote_Bow.uasset" in names_out and "A_BossRoar.uasset" not in names_out and "A_Empty.uasset" not in names_out, "연결 확인된 것 앞, 틀린 것 제외: %s" % names_out)
check(names_out[-1] == "A_Unknown.uasset" and "미확인" in out[-1].why, "검사 안 한 것은 미확인으로 뒤에")
check(S.refine_by_skeleton([], "x", name_cands, lambda *a: None)[1] == "no_index" and S.refine_by_skeleton(RECS, "nope", name_cands, lambda *a: None)[1] == "mesh_unknown", "기록 없음/모르는 메쉬")

# 캐시
with tempfile.TemporaryDirectory() as d:
    idx = S.SkelIndex(os.path.join(d, "i.json"))
    for r in RECS:
        idx.add(r)
    idx.seen.add("Proj/Content/Foo/Bar.uasset")
    idx.add(R("Proj/Content/Bad.uasset", "unreadable", reason="x"))
    idx.save()
    j = S.SkelIndex.load(os.path.join(d, "i.json"))
    check(len(j.all()) == len(RECS) and j.done("Proj/Content/Foo/Bar.uasset") and j.done("Proj/Content/Bad.uasset"), "캐시 저장/불러오기")
    check(j.pending(["Proj/Content/Foo/Bar.uasset", "new"]) == ["new"], "검사할 것만 남김")

    # 가짜 도우미로 inspect 흐름 (셔뱅 가짜 실행파일이라 Windows 에서는 건너뜀)
    fb = os.path.join(d, "mbextract")
    if os.name == "nt":
        print("test_skelgraph: inspect 흐름은 Windows 에서 건너뜀")
        print("모두 통과" if not fails else "실패 %d개" % len(fails))
        sys.exit(1 if fails else 0)
    open(fb, "w").write('''#!%s
import sys, json
a = sys.argv[1:]
def emit(**k): print(json.dumps(k), flush=True)
assert a[0] == "inspect"
paths = open(a[a.index("--paths-file") + 1], encoding="utf-8").read().split("\\n")
for p in paths:
    if p.endswith("A_Slash.uasset"):
        emit(type="node", path=p, kind="anim", name="A_Slash", skeleton="%s", frames=40, length=1.3, tracks=70, compressed=True)
    elif p.endswith("Bad.uasset"):
        emit(type="node", path=p, kind="unreadable", reason="Boom")
emit(type="done", count=1)
''' % (sys.executable, SK_SW))
    os.chmod(fb, os.stat(fb).st_mode | stat.S_IEXEC)
    ev = []
    recs, seen = extractor.inspect_assets(fb, d, "GAME_UE5_4", None, None, ["A/A_Slash.uasset", "A/Bad.uasset", "A/Mat.uasset"], ev.append, chunk=2)
    check(len(recs) == 2 and recs[0].kind == "anim" and recs[0].playable() and recs[1].kind == "unreadable", "inspect 결과 해석")
    check(seen == {"A/A_Slash.uasset", "A/Bad.uasset", "A/Mat.uasset"} and ev and ev[-1]["done"] == 3, "검사한 경로/진행")

# 도우미가 키(트랙) 수를 못 읽은 경우(-1)는 '없음'이 아니라 '모름' -> 프레임/길이가 있으면 재생 가능으로 본다. 진짜 0 은 불가
_a = S.Rec("A/X.uasset", "anim", skeleton="/Game/S/SK", frames=30, length=1.0, tracks=-1)
_b = S.Rec("A/Y.uasset", "anim", skeleton="/Game/S/SK", frames=30, length=1.0, tracks=0)
check(_a.playable() and not _b.playable(), "트랙 수 모름(-1)은 통과, 0 은 재생 불가")

# 예전 도우미가 스켈레톤 연결을 못 읽고 남긴 기록(연결이 빈 것)은 한 번만 다시 읽는다 (사용자 PC 의 실제 상황: Swordman 애니가 메쉬 0/애니 0 으로 보임)
with tempfile.TemporaryDirectory() as _d:
    _ix = S.SkelIndex(os.path.join(_d, "x.json"))
    _old = S.Rec("P/Content/Bosses/Swordman/Animation/A_Slash.uasset", "anim", name="A_Slash", skeleton="", frames=100, length=3.0, tracks=98, compressed=True)
    _ok = S.Rec("P/Content/Bosses/Other/A_Walk.uasset", "anim", name="A_Walk", skeleton="/Game/Bosses/Other/SK_O.SK_O", frames=50, length=1.5, tracks=40)
    _ix.add(_old)
    _ix.add(_ok)
    _ix.seen.update([_old.path, _ok.path])
    check(_ix.pending([_old.path, _ok.path, "new"]) == [_old.path, "new"], "연결이 빈 옛 기록은 다시 읽을 대상: %s" % _ix.pending([_old.path, _ok.path, "new"]))
    _ix.begin([_old.path])
    _ix.add(S.Rec(_old.path, "anim", name="A_Slash", skeleton="/Game/Bosses/Swordman/Art/SK_Swordman_Skeleton.SK_Swordman_Skeleton", frames=100, length=3.0, tracks=98, compressed=True))
    check(len(_ix.recs[_old.path]) == 1 and _ix.recs[_old.path][0].skel_key, "다시 읽으면 옛 기록을 바꿈(중복 없음)")
    _ix.save()
    _j = S.SkelIndex.load(_ix.path)
    check(_j.pending([_old.path]) == [], "다시 읽은 뒤에는 대기 목록에서 빠짐")
    # 다시 읽어도 연결이 안 읽히는 파일은 매번 다시 읽지 않는다
    _bad = S.Rec("P/Content/X/A_Bad.uasset", "anim", skeleton="", frames=10, length=1.0, tracks=5)
    _j.add(_bad)
    _j.seen.add(_bad.path)
    _j.begin([_bad.path])
    _j.add(S.Rec(_bad.path, "anim", skeleton="", frames=10, length=1.0, tracks=5))
    check(_j.pending([_bad.path]) == [], "한 번 다시 읽은 연결 없는 파일은 반복해 읽지 않음")

print("모두 통과" if not fails else "실패 %d개" % len(fails))
sys.exit(1 if fails else 0)
