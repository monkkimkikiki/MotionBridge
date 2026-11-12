"""AnimRef 연결 (연동규격 v1.1 의 8절): 내 PC 안에서만 열리는 작은 서버 + 결과 돌려주기. Qt 에 의존하지 않는다.

  1) 시작할 때 127.0.0.1 임의 포트로 서버를 열고 `%APPDATA%\\AnimRefLink\\animtool.json` 에 port/token/pid 를 쓴다(종료 때 지움).
       GET  /v1/ping            -> {"ok":true}            (토큰 필요)
       POST /v1/open {query}    -> query.json 경로를 받아 후보 화면을 띄운다 (토큰 필요)
     모든 요청은 `X-AnimRef-Token` 헤더가 맞아야 하고(아니면 401), Host 가 127.0.0.1/localhost 가 아니면 403.
  2) 요청을 받으면 on_open(query) 를 부른다 (GUI 쪽에서 UI 스레드로 넘긴다).
  3) 유저가 확정하거나 '맞는 게 없음'을 누르면 query.json 의 result_path 에 result.json(6절)을 쓰고
     AnimRef 의 POST /v1/result {result_path, result} 로 알린다. AnimRef 가 꺼져 있으면 result.json 만 남긴다.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional, Tuple

LINK_DIR_NAME = "AnimRefLink"
TOOL_FILE = "animtool.json"      # 이 프로그램이 쓰는 접속 정보 (app/port/token/pid)
ANIMREF_FILE = "animref.json"    # AnimRef 가 쓰는 접속 정보
RESULT_FILE = "result.json"
TOKEN_HEADER = "X-AnimRef-Token"
APP_NAME = "AnimTool"            # 규격서 8-2 의 animtool.json "app" 값
RESULT_VERSION = 1
LOCAL_HOSTS = ("127.0.0.1", "localhost")
UNCLASSIFIED = ("", "미분류", "unclassified")
MAX_BODY = 1_000_000


class LinkError(Exception):
    pass


class LinkBusy(OSError):
    """다른 MotionBridge 가 이미 AnimRef 와 연결돼 있다."""


def link_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, LINK_DIR_NAME)


def animref_settings_file() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "AnimRef", "settings.json")


def animref_settings() -> dict:
    d = _read_json(animref_settings_file())
    return d if isinstance(d, dict) else {}


def _safe_name(s) -> str:
    """AnimRef 가 게임 이름으로 폴더를 만들 때 쓰는 규칙 (core/library.py safe_name 과 같음): 글자·숫자·_·- 외에는 _ 로."""
    import re
    return re.sub(r"[^\w\-]+", "_", str(s)).strip("_") or "x"


def default_clip_dir() -> str:
    """AnimRef 가 클립을 모아 두는 폴더(<라이브러리>/<게임>/clips).

    AnimRef 는 게임 이름의 띄어쓰기 등을 _ 로 바꿔 폴더를 만든다 ('Mortal Shell 2' → 'Mortal_Shell_2').
    그래서 설정의 게임 이름 그대로 찾으면 폴더를 못 찾던 문제를 같은 규칙으로 고쳤다.
    설정의 게임 폴더가 없으면 라이브러리 안에서 가장 최근에 클립이 생긴 게임 폴더, 그것도 없으면 라이브러리."""
    s = animref_settings()
    lib = str(s.get("library_dir") or "")
    if not lib:
        return ""
    game = str(s.get("game") or "")
    cands = []
    if game:
        for g in (_safe_name(game), game):
            cands += [os.path.join(lib, g, "clips"), os.path.join(lib, g)]
    for cand in cands:
        if cand and os.path.isdir(cand):
            return cand
    best, best_t = "", -1.0
    try:
        for name in os.listdir(lib):
            c = os.path.join(lib, name, "clips")
            if os.path.isdir(c):
                t = os.path.getmtime(c)
                if t > best_t:
                    best, best_t = c, t
    except OSError:
        pass
    if best:
        return best
    return lib if os.path.isdir(lib) else ""


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except (OSError, SystemError):
        return False  # Windows 에서 없는 pid 는 OSError(WinError 87)


def _read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)  # 읽는 쪽이 쓰다 만 파일을 보지 않게


def _call(info: dict, method: str, route: str, body: Optional[dict] = None, timeout: float = 5.0) -> dict:
    """상대 창구 호출. 실패하면 OSError, 상대가 오류를 내면 LinkError(한국어 설명)."""
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (int(info["port"]), route), data=data, method=method,
                                 headers={"Content-Type": "application/json; charset=utf-8", TOKEN_HEADER: str(info.get("token") or "")})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error", "")
        except Exception:  # noqa: BLE001
            msg = ""
        raise LinkError(msg or "상대 프로그램이 오류를 냈습니다 (HTTP %d)" % e.code)
    except ValueError as e:
        raise LinkError("상대 프로그램의 응답을 읽지 못했습니다: %s" % e)


def find_tool(directory: Optional[str] = None) -> Optional[dict]:
    """실행 중인 MotionBridge(=이 툴)의 접속 정보. 파일이 있어도 ping 이 안 되면 None."""
    info = _read_json(os.path.join(directory or link_dir(), TOOL_FILE))
    if not (isinstance(info, dict) and info.get("port") and info.get("token")):
        return None
    try:
        _call(info, "GET", "/v1/ping", timeout=1.5)
    except (OSError, LinkError):
        return None
    return info


def forward_open(query_path: str, directory: Optional[str] = None) -> bool:
    """이미 켜져 있는 MotionBridge 에 query.json 을 넘긴다 (두 번째 실행이 첫 번째 창에 넘기는 용도)."""
    info = find_tool(directory)
    if info is None:
        return False
    try:
        _call(info, "POST", "/v1/open", {"query": os.path.abspath(query_path)}, timeout=10)
        return True
    except (OSError, LinkError):
        return False


# ---------------------------------------------------------------------- 서버
class LinkServer:
    def __init__(self, on_open: Callable[[dict], None], version: str = "", directory: Optional[str] = None) -> None:
        self.on_open = on_open
        self.version = version
        self.dir = directory or link_dir()
        self.token = secrets.token_urlsafe(24)
        self.port = 0
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def tool_file(self) -> str:
        return os.path.join(self.dir, TOOL_FILE)

    def start(self) -> int:
        other = find_tool(self.dir)
        if other is not None and other.get("pid") != os.getpid():
            raise LinkBusy("다른 MotionBridge 가 이미 AnimRef 와 연결돼 있습니다 (pid %s)" % other.get("pid"))
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # 콘솔 소음 없음
                pass

            def _send(self, code: int, obj: dict) -> None:
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _auth(self) -> bool:
                host = (self.headers.get("Host") or "").rsplit(":", 1)[0] if not (self.headers.get("Host") or "").startswith("[") else ""
                if host not in LOCAL_HOSTS:  # 웹페이지가 몰래 호출하는 것(DNS 리바인딩) 차단
                    self._send(403, {"ok": False, "error": "로컬 주소로만 접근할 수 있습니다"})
                    return False
                got = (self.headers.get(TOKEN_HEADER) or "").strip()
                if not got or not hmac.compare_digest(got.encode("utf-8"), srv.token.encode("utf-8")):
                    self._send(401, {"ok": False, "error": "토큰이 맞지 않습니다"})
                    return False
                return True

            def do_GET(self):  # noqa: N802
                if self.path.split("?")[0] != "/v1/ping":
                    self._send(404, {"ok": False, "error": "없는 주소입니다"})
                elif self._auth():
                    self._send(200, {"ok": True, "app": APP_NAME, "version": srv.version, "pid": os.getpid()})

            def _read_body(self) -> tuple:
                """응답하기 전에 본문부터 읽는다 -> (오류글, 바이트).
                본문을 안 읽고 응답 후 닫으면 Windows 가 연결을 리셋해(10054) 클라이언트가 401 같은 응답을 못 받는다."""
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    return "본문 크기가 올바르지 않습니다", b""
                if n <= 0 or n > MAX_BODY:
                    return "본문 크기가 올바르지 않습니다", b""
                out = b""
                while len(out) < n:
                    chunk = self.rfile.read(n - len(out))
                    if not chunk:
                        break
                    out += chunk
                return "", out

            def do_POST(self):  # noqa: N802
                body_err, raw = self._read_body()
                if not self._auth():
                    return
                if self.path.split("?")[0] != "/v1/open":
                    self._send(404, {"ok": False, "error": "없는 주소입니다"})
                    return
                try:
                    if body_err:
                        raise ValueError(body_err)
                    data = json.loads(raw.decode("utf-8-sig"))
                    if not isinstance(data, dict) or "query" not in data:
                        raise ValueError("query 가 필요합니다")
                except (ValueError, UnicodeDecodeError) as e:
                    self._send(400, {"ok": False, "error": str(e)})
                    return
                try:
                    q = parse_query(data["query"])
                except LinkError as e:
                    self._send(422, {"ok": False, "error": str(e)})
                    return
                srv.on_open(q)  # UI 스레드로 넘기는 일은 호출한 쪽 몫 (여기는 서버 스레드)
                self._send(200, {"ok": True})

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="animlink", daemon=True)
        self._thread.start()
        _write_json(self.tool_file, {"app": APP_NAME, "version": self.version, "port": self.port, "token": self.token,
                                     "pid": os.getpid(), "started": time.strftime("%Y-%m-%dT%H:%M:%S")})
        return self.port

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        cur = _read_json(self.tool_file)
        if isinstance(cur, dict) and cur.get("token") == self.token:  # 내가 쓴 파일일 때만 지운다
            try:
                os.remove(self.tool_file)
            except OSError:
                pass


# ---------------------------------------------------------------------- query.json (4절)
def _num_list(v) -> List[float]:
    out: List[float] = []
    for x in v if isinstance(v, list) else []:
        if isinstance(x, (int, float)) and not isinstance(x, bool):
            out.append(round(float(x), 3))
    return out


GENERIC_STEMS = {"video", "clip", "movie", "vid", "media", "output", "temp"}


def display_name(path: str, title: str = "") -> str:
    """영상 목록에 보일 이름: 제목이 있으면 제목, 파일 이름이 'video.mp4' 처럼 뜻 없는 이름이면 그 위 폴더(클립 이름), 아니면 파일 이름."""
    if title and title.strip():
        return title.strip()
    base = os.path.basename(path or "")
    stem = os.path.splitext(base)[0]
    if stem.lower() in GENERIC_STEMS:
        parent = os.path.basename(os.path.dirname(path or ""))
        if parent:
            return "%s / %s" % (parent, base)
    return base


def parse_query(q) -> dict:
    """/v1/open 의 query(= query.json 경로) 또는 query 객체를 읽는다.

    돌려주는 dict: id, game, paths(영상), label(분류, 미분류면 ""), hits(타격 시각 초 목록), hit_count, duration, fps, thumb,
    character{name,kind,detect_text}|None, result_path, file(query.json 경로), raw
    """
    file = ""
    if isinstance(q, str):
        file = q
        obj = _read_json(q)
        if not isinstance(obj, dict):
            raise LinkError("query.json 을 읽지 못했습니다: " + q)
    elif isinstance(q, dict):
        obj = q
        file = str(q.get("query_file") or "")
    else:
        raise LinkError("query 형식이 올바르지 않습니다")
    if obj.get("animref_handoff") not in (None, 1):
        raise LinkError("지원하지 않는 query 버전입니다: %s" % obj.get("animref_handoff"))
    clip = obj.get("clip") if isinstance(obj.get("clip"), dict) else {}
    video = str(clip.get("video") or obj.get("video") or "")
    ch = obj.get("character") if isinstance(obj.get("character"), dict) else None
    label = str(clip.get("label") or "").strip()
    result_path = str(obj.get("result_path") or "")
    if not result_path and file:
        result_path = os.path.join(os.path.dirname(file), RESULT_FILE)
    hits = _num_list(obj.get("hits"))
    title = str(clip.get("title") or "").strip()
    return {"file": file, "id": str(clip.get("id") or obj.get("id") or ""), "title": title, "game": str(obj.get("game") or ""),
            "paths": [video] if video else [], "label": "" if label in UNCLASSIFIED else label, "hits": hits, "hit_count": len(hits),
            "duration": clip.get("duration"), "fps": clip.get("fps"), "thumb": str(clip.get("thumb") or ""),
            "character": ({"name": str(ch.get("name") or ""), "kind": str(ch.get("kind") or ""), "detect_text": str(ch.get("detect_text") or "")}
                          if ch and ch.get("name") else None),
            "result_path": result_path, "raw": obj}


# ---------------------------------------------------------------------- result.json (6절)
def build_result(query: dict, status: str, animation: Optional[dict] = None, model_files: List[str] = (), export_dir: str = "",
                 candidates: List[str] = (), note: str = "", tool: str = "MotionBridge") -> dict:
    """status: confirmed(선택함) / rejected(후보 중 맞는 게 없음) / none(후보를 못 찾음)."""
    if status not in ("confirmed", "rejected", "none"):
        raise ValueError("status 가 올바르지 않습니다: " + status)
    res: dict = {"animref_result": RESULT_VERSION, "clip_id": query.get("id", ""), "status": status, "tool": tool,
                 "confirmed_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    if status == "confirmed":
        anim = {k: v for k, v in (animation or {}).items() if v not in (None, "")}
        if not (anim.get("id") or anim.get("name") or anim.get("file")):
            raise ValueError("확정하려면 애니메이션 정보(id/name/file)가 하나는 필요합니다")
        res["animation"] = anim
        res["model_files"] = list(model_files)
        if export_dir:
            res["export_dir"] = export_dir
    res["candidates"] = [{"id": c} for c in candidates]
    res["note"] = note
    return res


def result_path(query: dict, directory: Optional[str] = None) -> str:
    """결과를 쓸 위치: query.json 의 result_path, 없으면 query.json 옆, 그것도 없으면 연결 폴더."""
    if query.get("result_path"):
        return str(query["result_path"])
    base = os.path.dirname(query["file"]) if query.get("file") else (directory or link_dir())
    return os.path.join(base, RESULT_FILE)


# ---------------------------------------------------------------------- AnimRef 로 결과 알리기
def find_animref(directory: Optional[str] = None) -> Optional[dict]:
    d = _read_json(os.path.join(directory or link_dir(), ANIMREF_FILE))
    if isinstance(d, dict) and d.get("port") and d.get("token"):
        return d
    return None


def post_result(info: dict, path: str, result: dict, timeout: float = 5.0) -> Tuple[bool, str]:
    """POST /v1/result. (성공 여부, 실패 이유)"""
    try:
        _call(info, "POST", "/v1/result", {"result_path": path, "result": result}, timeout)
        return True, ""
    except LinkError as e:
        return False, str(e)
    except OSError as e:
        return False, "AnimRef 에 연결하지 못했습니다 (%s)" % e


def deliver(query: dict, result: dict, directory: Optional[str] = None) -> Dict[str, object]:
    """result.json 을 쓰고 AnimRef 에 알린다. {"file": 경로, "notified": 알림 성공 여부, "error": 실패 이유}"""
    path = result_path(query, directory)
    _write_json(path, result)
    info = find_animref(directory)
    if info is None:
        return {"file": path, "notified": False, "error": "AnimRef 가 꺼져 있습니다"}
    ok, why = post_result(info, path, result)
    return {"file": path, "notified": ok, "error": why}
