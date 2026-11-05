"""AnimRef <-> 게임 애니메이션 추출 툴 '실시간 연결'.

어도비 앱들처럼 서로를 켜고 바로 주고받는다. 내용(JSON 형식)은 handoff.py / 연동규격_추출툴.md 와 같고, 전달 방식만 실시간이다.

  · 각 프로그램은 자기 PC 안(127.0.0.1)에서만 열리는 작은 HTTP 창구를 연다 (바깥 네트워크에서는 접근 불가).
  · 포트와 비밀 토큰은 공용 폴더의 '발견 파일'(animref.json / animtool.json)에 적는다. 요청마다 X-AnimRef-Token 헤더로 확인한다.
  · 상대가 꺼져 있으면 설정된 실행 명령으로 켜고, 응답할 때까지 기다린 뒤 보낸다.
  · AnimRef 가 여는 창구:  GET /v1/ping   POST /v1/result {result_path, result?}   POST /v1/focus {clip_id}
  · 추출 툴이 열어야 하는 창구:  GET /v1/ping   POST /v1/open {query}"""
import csv
import io
import json
import os
import re
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import handoff, noop
from .settings import VERSION

TOKEN_HEADER = "X-AnimRef-Token"
APP_FILE = "animref.json"
TOOL_FILE = "animtool.json"
MAX_BODY = 4 * 1024 * 1024

# MotionBridge 안에 탭으로 들어 있을 때: 추출 툴이 같은 프로그램이므로 창구(HTTP) 없이 이 함수로 바로 넘긴다.
# 설정되면 fn(query_path)를 부르고(어느 스레드에서든 안전해야 함), 실패하면 RuntimeError(한국어)를 낸다.
LOCAL_TOOL = None


def shared_dir(s=None) -> Path:
    d = (s or {}).get("link_dir") or ""
    if d:
        return Path(d)
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "AnimRefLink"


def tool_file(s) -> Path:
    f = (s or {}).get("tool_link_file") or ""
    return Path(f) if f else shared_dir(s) / TOOL_FILE


# ---------------------------------------------------------------- 서버 (AnimRef 쪽 창구)
class LinkServer:
    def __init__(self, s, on_result=None, on_focus=None):
        self.s = s
        self.on_result = on_result or (lambda meta, ga: None)
        self.on_focus = on_focus or (lambda clip_id: None)
        self.token = secrets.token_urlsafe(24)
        self.httpd = None
        self.thread = None
        self.port = None
        self.file = shared_dir(s) / APP_FILE

    def start(self):
        owner = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _auth(self):
                host = (self.headers.get("Host") or "").split(":")[0]
                if host not in ("127.0.0.1", "localhost"):          # 웹페이지를 통한 접근(DNS 리바인딩) 차단
                    self._send(403, {"ok": False, "error": "로컬 주소로만 접근할 수 있어요."})
                    return False
                if self.headers.get(TOKEN_HEADER) != owner.token:
                    self._send(401, {"ok": False, "error": "토큰이 맞지 않아요."})
                    return False
                return True

            def do_GET(self):
                if self.path == "/v1/ping":
                    if self._auth():
                        self._send(200, {"ok": True, "app": "AnimRef", "version": VERSION})
                else:
                    self._send(404, {"ok": False, "error": "없는 주소예요."})

            def do_POST(self):
                if not self._auth():
                    return
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > MAX_BODY:
                        return self._send(413, {"ok": False, "error": "요청이 너무 커요."})
                    body = json.loads(self.rfile.read(n).decode("utf-8-sig") or "{}")
                    if not isinstance(body, dict):
                        raise ValueError("JSON 객체여야 해요.")
                except Exception as e:  # noqa: BLE001
                    return self._send(400, {"ok": False, "error": f"요청을 읽지 못했어요: {e}"})
                try:
                    if self.path == "/v1/result":
                        rp = body.get("result_path")
                        if not rp:
                            raise ValueError("result_path 가 필요해요.")
                        meta, ga = handoff.apply_result_folder(Path(rp).parent, body.get("result"))
                        owner.on_result(meta, ga)
                        return self._send(200, {"ok": True, "clip_id": Path(meta["_json"]).stem, "name": handoff.describe(ga)})
                    if self.path == "/v1/focus":
                        owner.on_focus(str(body.get("clip_id") or ""))
                        return self._send(200, {"ok": True})
                    return self._send(404, {"ok": False, "error": "없는 주소예요."})
                except ValueError as e:
                    return self._send(422, {"ok": False, "error": str(e)})
                except Exception as e:  # noqa: BLE001
                    return self._send(500, {"ok": False, "error": f"{type(e).__name__}: {e}"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"app": "AnimRef", "version": VERSION, "port": self.port, "token": self.token,
                                   "pid": os.getpid(), "started": time.strftime("%Y-%m-%dT%H:%M:%S")}), encoding="utf-8")
        tmp.replace(self.file)
        return self.port

    def stop(self):
        try:
            if self.httpd is not None:
                self.httpd.shutdown()
                self.httpd.server_close()
        finally:
            self.httpd = None
            try:
                d = json.loads(self.file.read_text(encoding="utf-8"))
                if d.get("pid") == os.getpid():          # 내가 쓴 파일만 지운다
                    self.file.unlink()
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------- 클라이언트 (추출 툴로 보내기)
def discover(path):
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if int(d["port"]) > 0 and d.get("token"):
            return d
    except Exception:  # noqa: BLE001
        pass
    return None


def call(info, method, route, body=None, timeout=3.0):
    """상대 창구 호출. 반환: 응답 dict. 실패하면 OSError / RuntimeError."""
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{int(info['port'])}{route}", data=data, method=method,
                                 headers={"Content-Type": "application/json; charset=utf-8", TOKEN_HEADER: info["token"]})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error", "")
        except Exception:  # noqa: BLE001
            msg = ""
        raise RuntimeError(msg or f"상대 프로그램이 오류를 냈어요 (HTTP {e.code})")


def tool_alive(s):
    info = discover(tool_file(s))
    if not info:
        return None
    try:
        call(info, "GET", "/v1/ping", timeout=1.5)
        return info
    except (OSError, RuntimeError):
        return None


def process_hint(s):
    """툴이 실행 중인지 찾을 때 쓸 이름. 설정(tool_process)이 있으면 그것, 없으면 실행 명령의 .exe 이름."""
    h = (s.get("tool_process") or "").strip()
    if h:
        return h
    tpl = (s.get("tool_cmd") or "").strip()
    if not tpl:
        return ""
    first = tpl[1:tpl.find('"', 1)] if tpl.startswith('"') and tpl.find('"', 1) > 0 else tpl.split()[0]
    name = re.split(r"[\\/]", first)[-1]
    return name if name.lower().endswith(".exe") else ""


def _list_processes():
    """(프로그램 이름, 창 제목) 목록. Windows 의 tasklist 를 쓴다."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    r = subprocess.run(["tasklist", "/v", "/fo", "csv", "/nh"], capture_output=True, timeout=10, creationflags=flags)
    raw = r.stdout
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("mbcs" if os.name == "nt" else "latin-1", errors="replace")
    out = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) >= 2:
            out.append((row[0], row[-1] if len(row) >= 9 else ""))
    return out


def process_running(s, lister=None):
    """실행 명령(또는 설정)으로 알아낸 이름의 프로그램이 떠 있는지. 판단할 수 없으면 None, 있으면 True/없으면 False."""
    hint = process_hint(s).lower()
    if not hint:
        return None
    try:
        for name, title in (lister or _list_processes)():
            if hint in name.lower() or hint in title.lower():
                return True
        return False
    except Exception:  # noqa: BLE001  tasklist 가 없거나 막힌 환경
        return None


def tool_status(s, lister=None):
    """("live", info) 실시간 연결됨 / ("running", None) 실행 중이지만 연결 창구는 없음 / ("off", None) 꺼짐 / ("unknown", None) 알 수 없음"""
    info = tool_alive(s)
    if info:
        return "live", info
    run = process_running(s, lister)
    if run:
        return "running", None
    return ("unknown" if run is None else "off"), None


def open_in_tool(s, query_path, wait=20.0, log=noop, launcher=None, sleep=time.sleep):
    """추출 툴에 query.json 을 실시간으로 보낸다. 꺼져 있으면 켠다.
    반환: "live"(켜져 있던 툴에 보냄) / "launched"(명령으로 켜면서 경로를 넘김) / "launched+live"(켠 뒤 보냄) / "manual"(실행 명령이 없음)
    실패하면 RuntimeError(한국어)."""
    if LOCAL_TOOL is not None:
        LOCAL_TOOL(str(query_path))
        return "live"
    info = tool_alive(s)
    if info:
        try:
            call(info, "POST", "/v1/open", {"query": str(query_path)}, timeout=10)
            return "live"
        except OSError as e:
            log(f"[추출 툴 연결] 보내지 못했어요: {e}")
    tpl = (s.get("tool_cmd") or "").strip()
    if not tpl:
        return "manual"
    if "{query}" not in tpl and process_running(s) is True:
        log("[추출 툴 연결] 추출 툴은 켜져 있지만 실시간 연결 창구가 없어요. 폴더만 열어요.")
        return "manual"                                # 이미 켜진 툴을 또 실행하지 않는다
    (launcher or handoff.launch)(s, query_path)
    log("[추출 툴 연결] 추출 툴을 실행했어요. 켜질 때까지 기다려요...")
    if "{query}" in tpl:
        return "launched"                              # 실행 명령이 경로를 직접 넘겨 줌
    t0 = time.time()
    while time.time() - t0 < wait:
        info = tool_alive(s)
        if info:
            call(info, "POST", "/v1/open", {"query": str(query_path)}, timeout=10)
            return "launched+live"
        sleep(0.5)
    raise RuntimeError(f"추출 툴을 실행했지만 {int(wait)}초 안에 연결되지 않았어요. 툴이 연결 파일({tool_file(s)})을 만드는지 확인해 주세요.")
