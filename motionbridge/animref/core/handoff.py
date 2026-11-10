"""AnimRef -> (별도로 만드는) 게임 애니메이션 추출 툴 연동.

역할 분담
  AnimRef  : 영상에서 캐릭터를 알아보고(등록 캐릭터), 대략적인 모션 정보(길이·타격 시각·거친 모션)를 만들어 넘긴다. 영상을 보여준다.
  추출 툴  : 그 정보로 비슷한 게임 애니메이션 후보를 나열 -> 유저가 검수/선택 -> 선택한 애니메이션 + 모델링 데이터를 추출 -> 적용
  AnimRef 는 선택 결과(result.json)를 읽어 클립에 '연결' 표시만 한다.

주고받는 법은 파일(JSON)이다. 규격: 연동규격_추출툴.md"""
import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import media, noop
from .library import file_of, final_label, game_dir, save_clip
from .settings import VERSION

QUERY_NAME = "query.json"
RESULT_NAME = "result.json"
FORMAT_VERSION = 1
CMD_HELP = '예: "C:\\tools\\animtool.exe" --handoff {query}     ({query}=query.json 경로, {folder}=폴더 경로 로 바뀌어요)'


def handoff_root(s) -> Path:
    return game_dir(s) / "handoff"


def folder_of(s, meta) -> Path:
    return handoff_root(s) / Path(meta["_json"]).stem


def _character(s, meta):
    from . import characters as C

    name = (meta.get("subject") or {}).get("character") or s.get("mocap_character") or ""
    if not name:
        return None
    c = C.load_character(s, name)
    if c is None:
        return {"name": name, "kind": "", "detect_text": "", "ref_images": []}
    return {"name": c["name"], "kind": c.get("kind", ""), "detect_text": c.get("detect_text", ""),
            "ref_images": [str(p) for p in C.ref_images(c)]}


def _clip_title(meta) -> str:
    """사람이 알아볼 클립 이름 ('<영상 제목> 00:00:00~00:00:10'). 못 만들면 빈 문자열."""
    try:
        from .titles import clip_title
        return clip_title(meta)
    except Exception:
        return ""


def build_query(meta, s, markers=None, copy_video=False, log=noop):
    """클립 하나의 연동 파일 묶음을 만든다. 반환: query.json 경로(str)."""
    folder = folder_of(s, meta)
    folder.mkdir(parents=True, exist_ok=True)
    video = file_of(meta, "video")
    if not video.exists():
        raise RuntimeError(f"영상 파일이 없어요: {video}")
    info = media.probe(str(video))
    vpath = video
    if copy_video:
        vpath = folder / f"video{video.suffix or '.mp4'}"
        shutil.copy2(video, vpath)
    subj = meta.get("subject") or {}
    mo = meta.get("motion") or {}
    mpath = mo.get("json")
    if markers is None:
        markers = mo.get("markers")
    if markers is None:
        from .mocap import hit_markers
        markers = hit_markers(meta, s, log)
    q = {
        "animref_handoff": FORMAT_VERSION,
        "created": datetime.now().isoformat(timespec="seconds"),
        "app_version": VERSION,
        "game": s.get("game", ""),
        "clip": {"id": Path(meta["_json"]).stem, "title": _clip_title(meta), "label": final_label(meta), "duration": round(float(info.duration), 3),
                 "fps": round(float(info.fps), 3), "size": [info.w, info.h], "video": str(vpath),
                 "thumb": str(file_of(meta, "thumb")), "json": str(meta["_json"])},
        "character": _character(s, meta),
        "subject": ({"boxes": subj["boxes"], "video_size": subj.get("video_size"), "how": subj.get("how", ""),
                     "box_format": "[t_sec, x1, y1, x2, y2] in video pixels"} if subj.get("boxes") else None),
        "hits": [round(float(t), 3) for t in (markers or [])],
        "motion": ({"format": "animref-motion", "version": 1, "path": mpath, "engine": mo.get("engine", "wham"),
                    "quality": "rough", "frames": mo.get("frames"), "fps": mo.get("fps")}
                   if mpath and Path(mpath).exists() else None),
        "result_path": str(folder / RESULT_NAME),
    }
    qp = folder / QUERY_NAME
    tmp = qp.with_suffix(".tmp")
    tmp.write_text(json.dumps(q, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(qp)
    (folder / RESULT_NAME).unlink(missing_ok=True)            # 이전 결과와 섞이지 않게
    meta.pop("game_anim", None)
    meta["handoff"] = {"query": str(qp), "sent": q["created"]}
    save_clip(meta)
    return str(qp)


def _quote(p):
    return f'"{p}"'


def launch(s, query_path):
    """설정된 명령으로 추출 툴을 실행한다. 명령이 없으면 None. 반환: 실행한 명령 문자열."""
    tpl = (s.get("tool_cmd") or "").strip()
    if not tpl:
        return None
    cmd = tpl.replace("{query}", _quote(query_path)).replace("{folder}", _quote(str(Path(query_path).parent)))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    subprocess.Popen(cmd, shell=True, creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, cwd=str(Path(query_path).parent))
    return cmd


def read_result(meta, s, folder=None):
    """추출 툴이 쓴 result.json 을 읽어 클립에 연결 정보를 저장한다. 반환: game_anim dict, 아직 없으면 None.
    잘못된 파일이면 ValueError(한국어 설명). folder 를 주면 그 폴더의 result.json 을 읽는다(실시간 연결용)."""
    rp = (Path(folder) if folder else folder_of(s, meta)) / RESULT_NAME
    if not rp.exists():
        return None
    try:
        d = json.loads(rp.read_text(encoding="utf-8-sig"))
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"result.json 을 읽지 못했어요: {e}")
    if not isinstance(d, dict) or d.get("animref_result") != FORMAT_VERSION:
        raise ValueError("result.json 형식이 달라요 (animref_result: 1 이어야 해요).")
    status = d.get("status", "confirmed")
    if status not in ("confirmed", "rejected", "none"):
        raise ValueError(f"result.json 의 status 값이 이상해요: {status}")
    anim = d.get("animation") or {}
    if status == "confirmed" and not (anim.get("id") or anim.get("name") or anim.get("file")):
        raise ValueError("result.json 에 선택한 애니메이션 정보(animation.id/name/file)가 없어요.")
    ga = {"status": status, "animation": anim, "model_files": list(d.get("model_files") or []),
          "export_dir": d.get("export_dir", ""), "note": d.get("note", ""), "tool": d.get("tool", ""),
          "confirmed_at": d.get("confirmed_at") or datetime.now().isoformat(timespec="seconds"),
          "candidates": len(d.get("candidates") or [])}
    meta["game_anim"] = ga
    save_clip(meta)
    return ga


def apply_result_folder(folder, result=None):
    """폴더(query.json 이 있는 곳)의 결과를 읽어 클립에 저장한다. result(dict)를 주면 먼저 result.json 으로 쓴다.
    반환: (meta, game_anim). 실시간 연결(link.py)이 쓴다."""
    folder = Path(folder)
    if folder.parent.name != "handoff":
        raise ValueError("연동 폴더(…\\handoff\\<클립>)가 아니에요.")
    qp = folder / QUERY_NAME
    if not qp.exists():
        raise ValueError(f"query.json 이 없는 폴더예요: {folder}")
    q = json.loads(qp.read_text(encoding="utf-8-sig"))
    from .library import load_clip
    meta = load_clip(q["clip"]["json"])
    if meta is None:
        raise ValueError(f"클립을 찾지 못했어요: {q['clip'].get('id')}")
    meta["_json"] = q["clip"]["json"]
    if result is not None:
        tmp = folder / (RESULT_NAME + ".tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(folder / RESULT_NAME)
    ga = read_result(meta, {}, folder=folder)
    if ga is None:
        raise ValueError("result.json 이 아직 없어요.")
    return meta, ga


def describe(ga):
    if not ga:
        return ""
    if ga.get("status") == "rejected":
        return "후보 중 맞는 게 없었어요"
    if ga.get("status") == "none":
        return "후보를 찾지 못했어요"
    a = ga.get("animation") or {}
    return a.get("name") or a.get("id") or Path(a.get("file", "")).stem or "연결됨"
