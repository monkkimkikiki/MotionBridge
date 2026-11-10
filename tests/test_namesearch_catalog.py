"""보스 이름으로 찾기: 같은 캐릭터 폴더의 메시·애니메이션이 정확히, 빠짐없이, 맨 앞에 나오는지 (이름이 비슷한 다른 캐릭터에 밀리지 않게)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from motionbridge.core import assetidx, charcatalog, gamenames  # noqa: E402

C = "MortalShell2/Content/Sparta/Characters/"
MESHES = [C + "Enemies/Mob%03d/Art/Mesh/SK_Mob%03d.uasset" % (i, i) for i in range(12)]
OWN_MESH = [C + "Enemies/TarredVestige/Art/Mesh/SK_TarredVestige.uasset", C + "Enemies/TarredVestige/Art/Mesh/SK_TarredVestige_Hand.uasset"]
DECOY_MESH = [C + "Enemies/TarredBrigand/Art/Mesh/SK_TarredBrigand.uasset", C + "Enemies/TarredVestigeLite/Art/Mesh/SK_TarredVestigeLite.uasset"]
OWN_ANIMS = [C + "Enemies/TarredVestige/Animation/A_TarredVestige_%s.uasset" % n for n in
             ("Attacks_Overhead", "Attacks_Swipe", "Idle", "Death", "Locomotion_Walk_Fwd", "Zzz_Weird_Name_01")]
DECOY_ANIMS = [C + "Enemies/TarredBrigand/Animation/A_TarredBrigand_Attacks_Overhead.uasset",
               C + "Enemies/TarredVestigeLite/Animation/A_TarredVestigeLite_Attacks_Swipe.uasset"]
NAMES = [["en", "ST_Core_MiniBosses", "TarredVestige_MiniBoss", "Tainted Vestige"], ["ko", "ST_Core_MiniBosses", "TarredVestige_MiniBoss", "오염된 자취"]]

fails = []


def check(c, m):
    if not c:
        fails.append(m)
        print("FAIL", m)


def main():
    meshes = MESHES + OWN_MESH + DECOY_MESH
    anims = OWN_ANIMS + DECOY_ANIMS
    idx = assetidx.AssetIndex(meshes, anims)
    book = gamenames.NameBook(NAMES)
    cat = charcatalog.Catalog.build(meshes, anims, book.entries)
    check(len(cat.all()) >= charcatalog.MIN_CHARS, "카탈로그가 캐릭터 %d명 (>= MIN_CHARS)" % len(cat.all()))

    hit = cat.find("오염된 자취", 1)
    check(hit and hit[0].score >= 0.85, "한국어 이름이 정확히 맞음: %s" % [(h.char.id, h.score) for h in hit])
    ch = hit[0].char
    check(ch.body_mesh() == OWN_MESH[0], "몸통 메시가 대표: %s" % ch.body_mesh())
    check(set(ch.meshes) == set(OWN_MESH), "메시는 이 캐릭터 폴더 것만 (비슷한 이름 캐릭터 제외): %s" % sorted(ch.meshes))
    check(set(ch.anims) == set(OWN_ANIMS), "애니는 이 캐릭터 폴더 것만: %d개" % len(ch.anims))

    own = cat.of_path(OWN_MESH[0]).anims
    for label in (None, "공격"):
        for limit in (60, 3):     # 라벨(분류) 점수를 쓸 때도, 한도가 작아도 같은 폴더 것이 전부 앞에 와야 함
            res = idx.rank_anims(OWN_MESH[0], label, None, limit, owned=own)
            paths = [c.path for c in res]
            tag = "label=%s limit=%d" % (label, limit)
            check(set(OWN_ANIMS) <= set(paths), "같은 폴더 애니 %d개 전부 포함(%s): %d" % (len(OWN_ANIMS), tag, len(set(OWN_ANIMS) & set(paths))))
            check(all(p in OWN_ANIMS for p in paths[:len(OWN_ANIMS)]), "같은 폴더 애니가 맨 앞(%s): %s" % (tag, [os.path.basename(p) for p in paths[:len(OWN_ANIMS) + 2]]))
            check(res[0].why.startswith("같은 캐릭터 폴더"), "근거에 '같은 캐릭터 폴더'(%s): %s" % (tag, res[0].why))

    # owned 를 안 주면 예전 동작 그대로 (이름이 전혀 안 닮은 애니는 빠질 수 있음)
    old = [c.path for c in idx.rank_anims(OWN_MESH[0], None, None, 60)]
    check(OWN_ANIMS[0] in old, "owned 없이도 예전 방식은 그대로 동작")

    if fails:
        print("\n실패 %d개" % len(fails))
        sys.exit(1)
    print("test_namesearch_catalog ALL OK")


main()
