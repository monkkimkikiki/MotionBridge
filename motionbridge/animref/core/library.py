"""클립 라이브러리: 클립마다 <이름>.mp4 / .json / .thumb.jpg / .sheet.jpg / .emb.npy 로 저장된다.
json 하나가 클립의 모든 정보(자동 분류, 사용자 분류, 묶음)를 담는다."""
import json
import re
import shutil
from pathlib import Path

UNLABELED = "미분류"
OTHER_GROUP = "기타"


def safe_name(s) -> str:
    return re.sub(r"[^\w\-]+", "_", str(s)).strip("_") or "x"  # 한글/일본어/중국어 등 유니코드 문자 허용


def library_root(s) -> Path:
    return Path(s["library_dir"])


def game_dir(s, game=None) -> Path:
    d = library_root(s) / safe_name(game or s["game"])
    (d / "clips").mkdir(parents=True, exist_ok=True)
    return d


def list_games(s):
    root = library_root(s)
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "clips").exists())


def base_of(json_path) -> Path:
    p = Path(json_path)
    return p.parent / p.stem


def file_of(meta, kind) -> Path:
    b = base_of(meta["_json"])
    return {
        "video": b.with_name(b.name + ".mp4"),
        "thumb": b.with_name(b.name + ".thumb.jpg"),
        "sheet": b.with_name(b.name + ".sheet.jpg"),
        "emb": b.with_name(b.name + ".emb.npy"),
    }[kind]


def load_clip(json_path):
    try:
        m = json.loads(Path(json_path).read_text(encoding="utf-8"))
    except Exception:
        return None
    m["_json"] = str(json_path)
    return m


def load_clips(gdir):
    out = []
    for p in sorted((Path(gdir) / "clips").glob("*.json")):
        m = load_clip(p)
        if m and file_of(m, "video").exists():
            out.append(m)
    out.sort(key=lambda m: m.get("created", ""))
    return out


def save_clip(meta):
    data = {k: v for k, v in meta.items() if not k.startswith("_")}
    p = Path(meta["_json"])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def final_label(meta) -> str:
    return meta.get("label_manual") or meta.get("ai_label") or UNLABELED


def set_manual_label(meta, label):
    meta["label_manual"] = label or None  # 사용자가 고른 분류는 AI와 같아도 저장 (자동 삭제 보호)
    save_clip(meta)


def delete_clip(meta):
    """영상을 지우지 못하면(다른 프로그램이 열고 있음) 정보 파일은 남겨 둔다."""
    try:
        file_of(meta, "video").unlink(missing_ok=True)
    except OSError:
        return False
    for kind in ("thumb", "sheet", "emb"):
        try:
            file_of(meta, kind).unlink()
        except OSError:
            pass
    try:
        Path(meta["_json"]).unlink()
    except OSError:
        pass
    return True


def _walk_replace(o, pairs, exact):
    """JSON 안의 문자열에서 옛 경로/이름을 새 것으로 바꾼다 (pairs: 앞부분 치환, exact: 통째로 같을 때 치환)."""
    if isinstance(o, str):
        if o in exact:
            return exact[o]
        for a, b in pairs:
            if o.startswith(a):
                return b + o[len(a):]
        return o
    if isinstance(o, list):
        return [_walk_replace(v, pairs, exact) for v in o]
    if isinstance(o, dict):
        return {k: _walk_replace(v, pairs, exact) for k, v in o.items()}
    return o


def rename_clip(s, meta, new_name):
    """클립 이름(파일 이름)을 바꾼다. 영상·정보·썸네일 외에 뽑아 둔 모션 폴더, 추출 툴 연동 폴더도 같이 옮기고
    클립 정보 안의 경로를 고친다. 반환: (새 meta, 알림 목록). 바꿀 수 없으면 ValueError(한국어). 중간에 실패하면 되돌린다."""
    old = Path(meta["_json"]).stem
    new = safe_name(str(new_name).strip())
    if not str(new_name).strip():
        raise ValueError("이름을 입력해 주세요.")
    if len(new) > 80:
        raise ValueError("이름이 너무 길어요. (80자 이하)")
    if new == old:            # 파일 이름은 그대로여도, 붙인 이름은 화면에 바로 보이게 기억한다
        if str(meta.get("custom_name") or "") != str(new_name).strip():
            meta = dict(meta)
            meta["custom_name"] = str(new_name).strip()
            save_clip(meta)
        return meta, []
    if len(new) > 80:
        raise ValueError("이름이 너무 길어요. (80자 이하)")
    cdir = Path(meta["_json"]).parent
    gdir = cdir.parent
    same_ci = new.lower() == old.lower()                     # 대소문자만 바꾸는 경우 (Windows 는 같은 파일로 봄)
    if not same_ci:
        for suffix in (".json", ".mp4", ".thumb.jpg", ".sheet.jpg", ".emb.npy"):
            if (cdir / (new + suffix)).exists():
                raise ValueError(f"'{new}' 이름의 클립이 이미 있어요. 다른 이름을 써 주세요.")
    moves = []                                                # (from, to)
    for suffix in (".mp4", ".thumb.jpg", ".sheet.jpg", ".emb.npy"):
        src = cdir / (old + suffix)
        if src.exists():
            moves.append((src, cdir / (new + suffix)))
    folders = []                                              # 모션/연동 폴더
    for sub in ("motion", "handoff"):
        src = gdir / sub / old
        if src.exists():
            dst = gdir / sub / new
            if dst.exists() and not same_ci:
                raise ValueError(f"'{sub}' 폴더에 '{new}' 이름의 옛 작업 폴더가 남아 있어요: {dst}\n다른 이름을 쓰거나 그 폴더를 정리해 주세요.")
            folders.append((src, dst))
    done = []
    notes = []

    def undo():
        for a, b in reversed(done):
            try:
                b.replace(a)
            except OSError:
                pass
    try:
        for a, b in moves + folders:
            try:
                a.replace(b)
            except OSError as e:
                raise ValueError(f"'{a.name}' 파일을 옮기지 못했어요. 다른 프로그램(재생기 등)이 열고 있는지 확인해 주세요. ({e})")
            done.append((a, b))
        old_json = Path(meta["_json"])
        new_json = cdir / (new + ".json")
        old_json.replace(new_json)
        done.append((old_json, new_json))
    except Exception:
        undo()
        raise
    # --- 여기부터는 이미 옮겨졌으므로 실패해도 되돌리지 않고 알림만 남긴다
    old_base, new_base = str(cdir / old), str(cdir / new)
    old_m, new_m = gdir / "motion" / old, gdir / "motion" / new
    old_h, new_h = gdir / "handoff" / old, gdir / "handoff" / new
    meta = dict(meta)
    meta["_json"] = str(new_json)
    meta["custom_name"] = str(new_name).strip()      # 목록에 보이는 이름 (예전에는 파일 이름만 바뀌고 목록 글자는 '영상 제목+시간' 그대로라 안 바뀐 것처럼 보였다)
    rec = meta.get("motion")
    if isinstance(rec, dict) and (new_m.exists()) and Path(rec.get("dir", "")) == old_m:
        try:
            for f in list(new_m.iterdir()):                   # <옛이름>.bvh 같은 파일 이름 앞부분도 새 이름으로
                if f.is_file() and f.name.startswith(old):
                    f.replace(new_m / (new + f.name[len(old):]))
            rec = dict(rec)
            rec["dir"] = str(new_m)
            for k in ("bvh", "json", "txt"):
                v = rec.get(k)
                if v and Path(v).parent == old_m:
                    n = Path(v).name
                    rec[k] = str(new_m / ((new + n[len(old):]) if n.startswith(old) else n))
            if rec.get("name") == old:
                rec["name"] = new
            meta["motion"] = rec
        except OSError as e:
            notes.append(f"모션 파일 이름을 일부 바꾸지 못했어요: {e}")
    pairs = [(str(old_h), str(new_h)), (str(old_m), str(new_m)), (old_base + ".", new_base + ".")]
    exact = {}
    if isinstance(meta.get("handoff"), dict):
        meta["handoff"] = _walk_replace(meta["handoff"], pairs, exact)
    qp = new_h / "query.json"
    if new_h.exists():
        for f in ("query.json", "result.json"):
            fp = new_h / f
            if not fp.exists():
                continue
            try:
                d = json.loads(fp.read_text(encoding="utf-8-sig"))
                d = _walk_replace(d, pairs, exact)
                if isinstance(d.get("clip"), dict) and d["clip"].get("id") == old:
                    d["clip"]["id"] = new
                if d.get("clip_id") == old:
                    d["clip_id"] = new
                tmp = fp.with_suffix(".tmp")
                tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
                tmp.replace(fp)
            except Exception as e:  # noqa: BLE001
                notes.append(f"연동 파일 {f} 안의 이름을 고치지 못했어요: {e}")
    if isinstance(meta.get("game_anim"), dict):
        meta["game_anim"] = _walk_replace(meta["game_anim"], pairs, exact)
    save_clip(meta)
    return meta, notes


def export(clips, dest, log=print, sess=None):
    """분류/묶음별 폴더로 복사한다. sess(Sessions)를 주면 '날짜 / 세션 / 분류' 폴더로 나눈다 (라이브러리 화면과 같은 구조, 바꾼 이름 그대로)."""
    dest = Path(dest)
    n = 0
    tree = {}
    if sess is not None:
        from . import sessions as _s
        for day in _s.build_tree(list(clips), sess, [], final_label):
            for sx in day["sessions"]:
                for m in sx["clips"]:
                    tree[m["_json"]] = (day["date_name"], sx["name"])
    for m in clips:
        src = file_of(m, "video")
        if m["_json"] in tree:
            dn, sn = tree[m["_json"]]
            d = dest / safe_name(dn) / safe_name(sn) / safe_name(final_label(m))
        else:
            d = dest / safe_name(final_label(m)) / safe_name(m.get("group") or OTHER_GROUP)
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, d / src.name)
        sheet = file_of(m, "sheet")
        if sheet.exists():
            shutil.copy2(sheet, d / sheet.name)
        n += 1
    log(f"{n}개 클립을 내보냈습니다: {dest}")
    return n
