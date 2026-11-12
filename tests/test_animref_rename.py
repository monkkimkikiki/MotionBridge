"""AnimRef 클립 이름 바꾸기: 바꾼 이름이 목록에 바로 보인다 (예전에는 파일 이름만 바뀌고 목록 글자는 그대로였다)."""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.animref.core import library, titles


def test_rename_shows():
    with tempfile.TemporaryDirectory() as d:
        cdir = Path(d) / "Game" / "clips"
        cdir.mkdir(parents=True)
        (cdir / "clip_0001.mp4").write_bytes(b"x")
        (cdir / "clip_0001.json").write_text(json.dumps({"video": "clip_0001.mp4", "source_name": "2026-11-12 10-00-00.mkv",
                                                         "start_in_source": 5.0, "duration": 3.0, "created": "1"}), encoding="utf-8")
        m = library.load_clips(cdir.parent)[0]
        before = titles.clip_title(m)
        assert "00:00:05" in before and library.final_label(m) == library.UNLABELED
        new_meta, notes = library.rename_clip({}, m, "보스 내려찍기 1")
        assert titles.clip_title(new_meta) == "보스 내려찍기 1", titles.clip_title(new_meta)
        m2 = library.load_clips(cdir.parent)
        assert len(m2) == 1 and titles.clip_title(m2[0]) == "보스 내려찍기 1", "다시 읽어도(목록 새로고침) 바꾼 이름이 보임"
        assert Path(m2[0]["_json"]).stem == library.safe_name("보스 내려찍기 1")
        # 파일 이름으로는 같은 이름이어도(특수문자만 다름) 보이는 이름은 바뀐다
        m3, _ = library.rename_clip({}, m2[0], "보스 내려찍기!1")
        assert titles.clip_title(library.load_clips(cdir.parent)[0]) == "보스 내려찍기!1"
    print("animref rename OK")


if __name__ == "__main__":
    test_rename_shows()
