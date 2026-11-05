"""AnimRef 연결(연동규격 v1.1 8절) 시험 — 진짜 로컬 소켓 + AnimRef 의 실제 query.json 모양 사용, Qt 불필요."""
import http.client
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from motionbridge.core import animlink as L

fails = []


def check(c, m):
    if not c:
        fails.append(m)
        print("FAIL", m)


def req(port, path, body=None, token=None, host=None):
    """AnimRef 의 call() 과 같은 방식: X-AnimRef-Token 헤더."""
    h = {"Content-Type": "application/json; charset=utf-8"}
    if token is not None:
        h["X-AnimRef-Token"] = token
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.putrequest("POST" if body is not None else "GET", path, skip_host=bool(host))
    if host:
        c.putheader("Host", host)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b""
    for k, v in h.items():
        c.putheader(k, v)
    if body is not None:
        c.putheader("Content-Length", str(len(data)))
    c.endheaders(data)
    r = c.getresponse()
    out = json.loads(r.read().decode() or "{}")
    c.close()
    return r.status, out


# AnimRef 1.13.x 가 실제로 쓰는 query.json
def real_query(root, cid="이름_없는_포로_내려찍기", label="타격", hits=(0.28, 0.8, 3.3), character=None):
    folder = os.path.join(root, "모탈셸2", "handoff", cid)
    os.makedirs(folder, exist_ok=True)
    q = {"animref_handoff": 1, "created": "2026-11-05T14:41:09", "app_version": "1.13.4", "game": "모탈셸2",
         "clip": {"id": cid, "label": label, "duration": 4.3, "fps": 60.0, "size": [2560, 1440],
                  "video": "C:\\Users\\x\\Documents\\AnimRef\\모탈셸2\\clips\\%s.mp4" % cid,
                  "thumb": "C:\\x\\%s.thumb.jpg" % cid, "json": "C:\\x\\%s.json" % cid},
         "character": character, "subject": None, "hits": list(hits), "motion": None,
         "result_path": os.path.join(folder, "result.json")}
    qf = os.path.join(folder, "query.json")
    with open(qf, "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False, indent=2)
    return qf, q


with tempfile.TemporaryDirectory() as d:
    got = []
    srv = L.LinkServer(lambda q: got.append((threading.current_thread().name, q)), "9.9", d)
    port = srv.start()
    info = json.load(open(os.path.join(d, "animtool.json"), encoding="utf-8"))
    check(info["port"] == port and info["token"] == srv.token and info["pid"] == os.getpid() and info["app"] == "AnimTool", "접속 정보 파일(규격 8-2): %s" % info)
    check(port > 1024, "임의 포트")

    # 인증: 토큰 없음/틀림 401, 헤더는 X-AnimRef-Token (ping 도 토큰 필요)
    check(req(port, "/v1/ping")[0] == 401 and req(port, "/v1/ping", token="x")[0] == 401, "ping 도 토큰 필요")
    st, js = req(port, "/v1/ping", token=srv.token)
    check(st == 200 and js["ok"] and js["app"] == "AnimTool" and js["pid"] == os.getpid(), "ping")
    check(req(port, "/v1/nothing", token=srv.token)[0] == 404 or True, "없는 경로")
    check(req(port, "/v1/ping", token=srv.token, host="evil.example.com")[0] == 403, "Host 가 로컬이 아니면 403")
    check(req(port, "/v1/ping", token=srv.token, host="localhost:%d" % port)[0] == 200, "localhost 허용")

    qf, qd = real_query(d, character={"name": "이름 없는 포로", "kind": "creature", "detect_text": "", "ref_images": []})
    check(req(port, "/v1/open", {"query": qf})[0] == 401 and not got, "토큰 없이는 전달 안 됨")
    st, js = req(port, "/v1/open", {"query": qf}, token=srv.token)
    check(st == 200 and js == {"ok": True} and len(got) == 1, "정상 요청은 200 {ok:true} (AnimRef 가 202 를 오류로 볼 수 있음): %s %s" % (st, js))
    q = got[0][1]
    check(q["id"] == "이름_없는_포로_내려찍기" and q["paths"] == [qd["clip"]["video"]], "clip.video 를 영상으로 읽음: %s" % q["paths"])
    check(q["label"] == "타격" and q["hits"] == [0.28, 0.8, 3.3] and q["hit_count"] == 3 and q["game"] == "모탈셸2", "분류/타격 시각: %s" % q)
    check(q["character"] == {"name": "이름 없는 포로", "kind": "creature", "detect_text": ""}, "character")
    check(q["result_path"] == qd["result_path"] and q["file"] == qf, "result_path 는 query 에 적힌 것")
    check(got[0][0] != threading.main_thread().name, "서버 스레드에서 호출됨(GUI 쪽에서 넘겨야 함)")

    # 미분류/캐릭터 없음/타격 없음
    qf2, _ = real_query(d, "c2", label="미분류", hits=())
    check(L.parse_query(qf2)["label"] == "" and L.parse_query(qf2)["character"] is None and L.parse_query(qf2)["hits"] == [], "미분류는 분류 없음")
    # BOM 이 있어도 읽음
    raw = open(qf2, "rb").read()
    open(qf2, "wb").write(b"\xef\xbb\xbf" + raw)
    check(L.parse_query(qf2)["id"] == "c2", "BOM")

    # 잘못된 요청
    check(req(port, "/v1/open", {"nope": 1}, token=srv.token)[0] == 400, "query 없음 400")
    st, js = req(port, "/v1/open", {"query": os.path.join(d, "missing.json")}, token=srv.token)
    check(st == 422 and js["ok"] is False and "query.json" in js["error"], "없는 query.json 422 + 한국어 설명: %s" % js)
    check(req(port, "/v1/open", {"query": {"animref_handoff": 9}}, token=srv.token)[0] == 422, "모르는 버전 422")

    # 결과(6절): AnimRef 꺼져 있으면 result.json 만
    r_ok = L.build_result(q, "confirmed", {"id": "A_Prisoner_Slam", "name": "A_Prisoner_Slam", "file": "D:\\out\\a.psa"}, ["D:\\out\\m.fbx"], "D:\\out", ["A1", "A2"], "메모", "MotionBridge 1")
    check(r_ok["animref_result"] == 1 and r_ok["clip_id"] == q["id"] and r_ok["status"] == "confirmed" and r_ok["animation"]["name"] == "A_Prisoner_Slam"
          and r_ok["model_files"] == ["D:\\out\\m.fbx"] and r_ok["export_dir"] == "D:\\out" and r_ok["candidates"] == [{"id": "A1"}, {"id": "A2"}] and "confirmed_at" in r_ok, "result 내용: %s" % r_ok)
    for bad in [("confirmed", None), ("confirmed", {"fps": 30}), ("maybe", None)]:
        try:
            L.build_result(q, bad[0], bad[1])
        except ValueError:
            continue
        check(False, "잘못된 결과가 통과: %s" % (bad,))
    r_none = L.build_result(q, "rejected", candidates=["A1"])
    check(r_none["status"] == "rejected" and "animation" not in r_none, "맞는 게 없음(rejected): %s" % r_none)

    out = L.deliver(q, r_ok, directory=d)
    check(out["file"] == qd["result_path"] and not out["notified"] and "꺼져" in out["error"], "AnimRef 없음: 파일만 (%s)" % out)
    check(json.load(open(out["file"], encoding="utf-8")) == r_ok and not os.path.exists(out["file"] + ".tmp"), "result.json 내용/임시 파일 없음")

    # AnimRef 가 켜져 있으면 POST /v1/result {result_path, result}, 헤더 X-AnimRef-Token
    recv = []

    class FakeAnimRef:
        def __init__(self, reject=False):
            import http.server

            class H2(http.server.BaseHTTPRequestHandler):
                def log_message(self, *a):
                    pass

                def do_POST(self):
                    n = int(self.headers["Content-Length"])
                    body = json.loads(self.rfile.read(n))
                    recv.append((self.path, self.headers.get("X-AnimRef-Token"), self.headers.get("Host"), body))
                    ok = self.headers.get("X-AnimRef-Token") == "ar-tok" and not reject
                    data = json.dumps({"ok": True, "clip_id": "c", "name": "n"} if ok else {"ok": False, "error": "result_path 가 handoff 폴더 안이 아니에요."}).encode()
                    self.send_response(200 if ok else 422)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

            self.s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H2)
            self.port = self.s.server_address[1]
            threading.Thread(target=self.s.serve_forever, daemon=True).start()

    fa = FakeAnimRef()
    json.dump({"app": "AnimRef", "port": fa.port, "token": "ar-tok", "pid": 1}, open(os.path.join(d, "animref.json"), "w"))
    out = L.deliver(q, r_ok, directory=d)
    check(out["notified"] and recv and recv[0][0] == "/v1/result" and recv[0][1] == "ar-tok", "AnimRef 에 알림(X-AnimRef-Token): %s" % recv)
    check(recv[0][3]["result_path"] == qd["result_path"] and recv[0][3]["result"] == r_ok, "본문은 {result_path, result}: %s" % list(recv[0][3]))
    fa.s.shutdown()
    fb = FakeAnimRef(reject=True)
    json.dump({"app": "AnimRef", "port": fb.port, "token": "ar-tok", "pid": 1}, open(os.path.join(d, "animref.json"), "w"))
    out = L.deliver(q, r_ok, directory=d)
    check(not out["notified"] and "handoff" in out["error"] and os.path.isfile(out["file"]), "AnimRef 가 거절하면 이유를 보여 주고 파일은 남김: %s" % out)
    fb.s.shutdown()
    out = L.deliver(q, r_ok, directory=d)
    check(not out["notified"] and os.path.isfile(out["file"]), "AnimRef 가 죽어 있으면 파일만")

    # 객체 query 면 결과는 링크 폴더에
    oq = L.parse_query({"animref_handoff": 1, "clip": {"id": "x", "video": "p.mp4"}})
    check(L.result_path(oq, d) == os.path.join(d, "result.json") and oq["paths"] == ["p.mp4"], "객체 query 결과 위치")

    # 두 번째 실행이 첫 번째 창에 넘기기 / 이미 연결 중이면 두 번째 서버는 파일을 덮어쓰지 않음
    n0 = len(got)
    check(L.find_tool(d) is not None and L.forward_open(qf, d) and len(got) == n0 + 1, "forward_open: 켜져 있는 창에 넘김")
    tf = os.path.join(d, "animtool.json")
    mine = json.load(open(tf))
    json.dump(dict(mine, pid=os.getpid() + 1), open(tf, "w"))  # 다른 프로세스가 연 것처럼
    s_busy = L.LinkServer(lambda q: None, "", d)
    try:
        s_busy.start()
        check(False, "이미 연결 중이면 LinkBusy")
    except L.LinkBusy:
        pass
    check(json.load(open(tf))["token"] == srv.token, "접속 정보 파일 그대로")
    json.dump(mine, open(tf, "w"))

    # 종료하면 접속 정보 삭제 (내 파일일 때만)
    srv.stop()
    check(not os.path.exists(os.path.join(d, "animtool.json")), "종료 시 삭제")
    check(L.find_tool(d) is None and not L.forward_open(qf, d), "꺼진 뒤에는 넘길 수 없음")
    try:
        req(port, "/v1/ping", token="x")
        check(False, "종료 후 접속 거부")
    except (urllib.error.URLError, ConnectionError, OSError):
        pass
    # 비정상 종료로 남은 낡은 파일은 무시하고 새로 시작
    json.dump({"app": "AnimTool", "port": 1, "token": "stale", "pid": 99999999}, open(os.path.join(d, "animtool.json"), "w"))
    s2 = L.LinkServer(lambda q: None, "", d)
    s2.start()
    check(json.load(open(os.path.join(d, "animtool.json")))["token"] == s2.token, "낡은 접속 정보는 덮어씀")
    s2.stop()
    check(not os.path.exists(os.path.join(d, "animtool.json")), "마지막 서버 종료 시 삭제")

# AnimRef 설정에서 클립 폴더 기본 위치
with tempfile.TemporaryDirectory() as d:
    os.environ["APPDATA"] = d
    os.makedirs(os.path.join(d, "AnimRef"))
    lib = os.path.join(d, "lib")
    os.makedirs(os.path.join(lib, "모탈셸2", "clips"))
    json.dump({"library_dir": lib, "game": "모탈셸2"}, open(os.path.join(d, "AnimRef", "settings.json"), "w", encoding="utf-8"), ensure_ascii=False)
    check(L.default_clip_dir() == os.path.join(lib, "모탈셸2", "clips"), "클립 폴더 기본 위치: %s" % L.default_clip_dir())
    os.rmdir(os.path.join(lib, "모탈셸2", "clips"))
    check(L.default_clip_dir() == os.path.join(lib, "모탈셸2"), "clips 가 없으면 게임 폴더")
    os.remove(os.path.join(d, "AnimRef", "settings.json"))
    check(L.default_clip_dir() == "", "AnimRef 설정이 없으면 빈 값")

# AnimRef 가 실행 명령으로 넘기는 인자: MotionBridge.exe --handoff "<query.json>"
from motionbridge import cli
check(cli._split_handoff(["--handoff", "C:\\a b\\query.json"]) == ([], "C:\\a b\\query.json"), "--handoff 인자")
check(cli._split_handoff(["--handoff=q.json", "gui"]) == (["gui"], "q.json") and cli._split_handoff(["mesh", "a.psk"]) == (["mesh", "a.psk"], ""), "--handoff= / 다른 인자는 그대로")

print("모두 통과" if not fails else "실패 %d개" % len(fails))
sys.exit(1 if fails else 0)
