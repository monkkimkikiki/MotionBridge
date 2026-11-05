"""locres 읽기 + 게임 글자 사전(gamenames) 테스트 — 합성 .locres 로 1/2/3 버전을 모두 확인."""
import os
import struct
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import assetidx, gamenames, locres  # noqa: E402


def fs(s):
    if all(ord(c) < 128 for c in s):
        b = s.encode("ascii") + b"\0"
        return struct.pack("<i", len(b)) + b
    b = (s + "\0").encode("utf-16-le")
    return struct.pack("<i", -(len(b) // 2)) + b


def write_locres(version, ns_map):
    strings, body = [], b""
    for ns, ents in ns_map.items():
        nb = (struct.pack("<I", 0) if version >= 2 else b"") + fs(ns) + struct.pack("<I", len(ents))
        for key, text in ents:
            if text not in strings:
                strings.append(text)
            nb += (struct.pack("<I", 0) if version >= 2 else b"") + fs(key) + struct.pack("<Ii", 0, strings.index(text))
        body += nb
    head = locres.MAGIC + bytes([version])
    total = len(head) + 8 + (4 if version >= 2 else 0) + 4 + len(body)
    arr = struct.pack("<i", len(strings)) + b"".join(fs(t) + (struct.pack("<i", 1) if version >= 2 else b"") for t in strings)
    out = head + struct.pack("<q", total) + (struct.pack("<i", sum(len(e) for e in ns_map.values())) if version >= 2 else b"")
    out += struct.pack("<I", len(ns_map)) + body + arr
    return out


DATA = {
    "Enemies": [("Enemy_NamelessCaptive_Name", "The Nameless Captive"), ("Enemy_Dwarf_Name", "Dwarf Warrior"),
                ("Enemy_NamelessCaptive_Desc", "A prisoner who lost his name and mind in the dungeon below the old keep."),
                ("0A1B2C3D4E5F60718293A4B5C6D7E8F9", "Nameless Captive")],
    "UI": [("Menu_Play", "Play"), ("Boss_StoneGolem_Title", "Stone Golem"), ("한글키", "이름 없는 포로")],
}


def test_parse():
    for v in (1, 2, 3):
        rows = locres.parse_locres(write_locres(v, DATA))
        assert ("Enemies", "Enemy_NamelessCaptive_Name", "The Nameless Captive") in rows, (v, rows[:2])
        assert ("UI", "한글키", "이름 없는 포로") in rows and len(rows) == 7, (v, len(rows))
    for bad in (b"", b"x" * 40, locres.MAGIC + bytes([9]) + b"\0" * 20):
        try:
            locres.parse_locres(bad)
        except locres.LocresError:
            continue
        raise AssertionError(bad)
    try:
        locres.parse_locres(write_locres(3, DATA)[:60])
    except locres.LocresError:
        pass
    else:
        raise AssertionError("잘린 파일")
    print("locres parse OK")


MESHES = ["Sparta/Content/Characters/Enemies/Prisoner/Meshes/SK_Prisoner",
          "Sparta/Content/Characters/Enemies/NamelessCaptive/Meshes/SK_NamelessCaptive_Boss",
          "Sparta/Content/Characters/Monsters/Dwarf/SK_Dwarf_Warrior",
          "Sparta/Content/Characters/Monsters/StoneGolem/SK_StoneGolem",
          "Sparta/Content/Characters/Monsters/Convict/SK_Convict_A"]


def test_book():
    with tempfile.TemporaryDirectory() as d:
        f1, f2 = os.path.join(d, "Game_en_Game.locres"), os.path.join(d, "Game_ko_Game.locres")
        open(f1, "wb").write(write_locres(3, DATA))
        open(f2, "wb").write(write_locres(2, {"X": [("Enemy_Foo_Name", "푸")]}))
        open(os.path.join(d, "Game_fr_Game.locres"), "wb").write(write_locres(2, {"X": [("Enemy_Bar_Name", "Barre")]}))
        open(os.path.join(d, "broken.locres"), "wb").write(b"zz")
        book = gamenames.build_book([os.path.join(d, n) for n in sorted(os.listdir(d))])
        langs = {e[0] for e in book.entries}
        assert langs == {"en", "ko"}, langs  # 프랑스어는 버리고 깨진 파일은 건너뜀
        hits = book.lookup("the nameless captive!")
        assert hits[0].key in ("Enemy_NamelessCaptive_Name", "0A1B2C3D4E5F60718293A4B5C6D7E8F9") and hits[0].score >= 0.99, hits[:2]
        assert not any(h.key == "Enemy_NamelessCaptive_Desc" for h in hits), "긴 설명문은 이름이 아님"
        idx = assetidx.AssetIndex(MESHES, [])
        c = book.meshes_for("The Nameless Captive", idx)
        assert c and c[0].path == MESHES[1] and "NamelessCaptive" in c[0].why or "nameless" in c[0].why.lower(), c
        # 해시/GUID 키만 맞는 이름은 단서가 못 된다
        assert gamenames.key_words("Enemies", "0A1B2C3D4E5F60718293A4B5C6D7E8F9") == []
        # 한글 이름도 찾는다 (한국어 사전)
        assert book.lookup("이름 없는 포로")[0].text == "이름 없는 포로"
        # 저장/불러오기
        p = os.path.join(d, "names.json")
        book.save(p, "g1")
        b2 = gamenames.NameBook.load(p, "g1")
        assert b2 and len(b2) == len(book) and gamenames.NameBook.load(p, "other") is None
        assert gamenames.NameBook([]).lookup("x") == []
    print("namebook OK")


class FakeAI:
    key = "ollama"

    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def vocab_limit(self):
        return 250

    def ask_text(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_find_meshes():
    from motionbridge.core import vision
    idx = assetidx.AssetIndex(MESHES, [])
    # 글자 사전 없이도 비슷한 말로 찾는다 (리소스 이름 = Prisoner / Convict)
    idx2 = assetidx.AssetIndex([m for m in MESHES if "NamelessCaptive" not in m], [])
    c, notes = gamenames.find_meshes("The Nameless Captive", idx2)
    assert {x.name for x in c[:2]} == {"Prisoner", "Convict_A"}, c
    assert any("글자 사전" in n for n in notes), notes
    # 사전 + 이름 일치가 서로 확인해 주면 점수가 올라간다
    book = gamenames.NameBook([("en", "Enemies", "Enemy_NamelessCaptive_Name", "The Nameless Captive")])
    c, notes = gamenames.find_meshes("The Nameless Captive", idx, book)
    assert c[0].path == MESHES[1] and c[0].score >= 0.99 and "게임 글자" in c[0].why, c[0]
    # 키가 사전엔 있지만 리소스와 안 이어지는 경우(해시) 이유를 알려 준다
    book2 = gamenames.NameBook([("en", "E", "0A1B2C3D4E5F60718293A4B5C6D7E8F9", "Warden Of Ash")])
    c, notes = gamenames.find_meshes("Warden Of Ash", idx, book2)
    assert any("해시" in n for n in notes), notes
    # AI: 목록 밖의 이름은 버리고, 목록 안의 이름만 후보가 된다
    ai = FakeAI('{"matches":[{"name":"Convict_A","confidence":0.8,"reason":"captive=convict"},{"name":"MadeUpName","confidence":0.99}]}')
    c, notes = gamenames.find_meshes("Warden Of Ash", idx, None, ai)
    assert [x.name for x in c] == ["Convict_A"] and "AI 추천" in c[0].why, (c, notes)
    assert "Warden Of Ash" in ai.prompts[0] and "Convict_A" in ai.prompts[0] and "MadeUpName" not in ai.prompts[0]
    # AI 가 실패해도 나머지 결과는 유지
    c, notes = gamenames.find_meshes("The Nameless Captive", idx, None, FakeAI(vision.VisionError("연결 안 됨")))
    assert c and any("연결 안 됨" in n for n in notes)
    assert gamenames.find_meshes("", idx)[0] == []
    try:
        vision.suggest_from_vocab(FakeAI("완전 엉뚱한 글"), "x", ["a"])
    except vision.VisionError:
        pass
    else:
        raise AssertionError("해석 실패가 숨겨짐")
    print("find_meshes OK")


if __name__ == "__main__":
    test_parse()
    test_book()
    test_find_meshes()
    print("test_locres ALL OK")
