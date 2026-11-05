"""AnimRef 연결: 내 PC 안에서만 열리는 작은 서버 + 결과 돌려주기. Qt 에 의존하지 않는다.

  1) 시작할 때 127.0.0.1 임의 포트로 서버를 열고 `%APPDATA%\\AnimRefLink\\animtool.json` 에 port/token/pid 를 쓴다(종료 때 지움).
       GET  /v1/ping            -> 살아 있는지 확인 (토큰 불필요)
       POST /v1/open {query}    -> 후보 목록 화면을 띄우라는 요청 (토큰 필요)
  2) 요청을 받으면 on_open(query) 를 부른다 (GUI 쪽에서 UI 스레드로 넘긴다).
  3) 유저가 확정하거나 '맞는 게 없음'을 누르면 result.json 을 쓰고 AnimRef 의 POST /v1/result 로 알린다.
     AnimRef 가 꺼져 있으면 result.json 만 남긴다.

※ 연동규격 문서(8절)를 받기 전의 가정은 맨 위 상수(ANIMREF_FILE, TOKEN_HEADERS)와 parse_query / build_result 두 함수에 모아 두었다.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional

LINK_DIR_NAME = "AnimRefLink"
TOOL_FILE = "animtool.json"      # 이 프로그램이 쓰는 접속 정보 (port/token/pid)
ANIMREF_FILE = "animref.json"    # [가정] AnimRef 가 쓰는 접속 정보. 규격서 확인 필요
RESULT_FILE = "result.json"
TOKEN_HEADERS = ("X-Token", "X-AnimRef-Token")  # [가정] 'Authorization: Bearer …' 도 받는다
MAX_BODY = 1_000_000


class LinkError(Exception):
    pass


def link_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, LINK_DIR_NAME)


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
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)  # 읽는 쪽이 쓰다 만 파일을 보지 않게


# ---------------------------------------------------------------------- 서버
class LinkServer:
    def __init__(self, on_open: Callable[[dict], None], version: str = "", directory: Optional[str] = None) -> None:
        self.on_open = on_open
        self.version = version
        self.dir = directory or link_dir()
        self.token = secrets.token_hex(16)
        self.port = 0
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def tool_file(self) -> str:
        return os.path.join(self.dir, TOOL_FILE)

    def start(self) -> int:
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

            def _authorized(self) -> bool:
                got = ""
                auth = self.headers.get("Authorization", "")
                if auth.lower().startswith("bearer "):
                    got = auth[7:].strip()
                for h in TOKEN_HEADERS:
                    got = got or (self.headers.get(h) or "").strip()
                return bool(got) and hmac.compare_digest(got, srv.token)

            def do_GET(self):  # noqa: N802
                if self.path.split("?")[0] == "/v1/ping":
                    self._send(200, {"ok": True, "app": "MotionBridge", "version": srv.version, "pid": os.getpid()})
                else:
                    self._send(404, {"ok": False, "error": "not found"})

            def do_POST(self):  # noqa: N802
                if self.path.split("?")[0] != "/v1/open":
                    self._send(404, {"ok": False, "error": "not found"})
                    return
                if not self._authorized():
                    self._send(401, {"ok": False, "error": "bad token"})
                    return
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    if n <= 0 or n > MAX_BODY:
                        raise ValueError("body size")
                    data = json.loads(self.rfile.read(n).decode("utf-8"))
                    if not isinstance(data, dict) or "query" not in data:
                        raise ValueError("query missing")
                except (ValueError, UnicodeDecodeError) as e:
                    self._send(400, {"ok": False, "error": str(e)})
                    return
                try:
                    q = parse_query(data["query"])
                except LinkError as e:
                    self._send(422, {"ok": False, "error": str(e)})
                    return
                srv.on_open(q)  # UI 스레드로 넘기는 일은 호출한 쪽 몫 (여기는 서버 스레드)
                self._send(202, {"ok": True})

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="animlink", daemon=True)
        self._thread.start()
        _write_json(self.tool_file, {"port": self.port, "token": self.token, "pid": os.getpid()})
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


# ---------------------------------------------------------------------- 요청/결과 형식 [규격서 확인 필요]
def parse_query(q) -> dict:
    """/v1/open 의 query 를 읽는다. 문자열이면 query.json 경로, 객체면 그 자체.

    돌려주는 dict: {"file": query.json 경로 또는 "", "id": 요청 번호, "paths": [영상/클립…], "label": 분류, "hits": 타수, "raw": 원본}
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
    paths: List[str] = []
    for k in ("clips", "videos", "paths", "files", "clip", "video", "path", "file"):
        v = obj.get(k)
        for x in (v if isinstance(v, list) else [v]):
            if isinstance(x, dict):
                x = x.get("path") or x.get("file")
            if isinstance(x, str) and x and x not in paths:
                paths.append(x)
    hits = obj.get("hits")
    return {"file": file, "id": obj.get("id") or obj.get("request_id") or "", "paths": paths,
            "label": str(obj.get("label") or obj.get("category") or ""),
            "hits": int(hits) if isinstance(hits, (int, float)) and hits else None, "raw": obj}


def build_result(query: dict, meshes: List[str], anims: List[str], none: bool = False) -> dict:
    return {"id": query.get("id", ""), "status": "none" if none else "confirmed",
            "meshes": [] if none else list(meshes), "animations": [] if none else list(anims)}


def result_path(query: dict, directory: Optional[str] = None) -> str:
    """result.json 은 query.json 옆에 둔다 (query 가 객체로 왔으면 링크 폴더)."""
    base = os.path.dirname(query["file"]) if query.get("file") else (directory or link_dir())
    return os.path.join(base, RESULT_FILE)


# ---------------------------------------------------------------------- AnimRef 로 결과 알리기
def find_animref(directory: Optional[str] = None) -> Optional[dict]:
    d = _read_json(os.path.join(directory or link_dir(), ANIMREF_FILE))
    if isinstance(d, dict) and d.get("port"):
        return d
    return None


def post_result(info: dict, result: dict, timeout: float = 5.0) -> bool:
    req = urllib.request.Request("http://127.0.0.1:%d/v1/result" % int(info["port"]),
                                 data=json.dumps(result, ensure_ascii=False).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "X-Token": str(info.get("token") or ""),
                                          "Authorization": "Bearer " + str(info.get("token") or "")})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def deliver(query: dict, meshes: List[str], anims: List[str], none: bool = False, directory: Optional[str] = None) -> Dict[str, object]:
    """result.json 을 쓰고 AnimRef 에 알린다. {"file": 경로, "notified": 알림 성공 여부}"""
    res = build_result(query, meshes, anims, none)
    path = result_path(query, directory)
    _write_json(path, res)
    info = find_animref(directory)
    return {"file": path, "notified": bool(info and post_result(info, res))}
