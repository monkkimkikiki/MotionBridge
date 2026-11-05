"""AnimRef 연결 서버/결과 시험 (진짜 로컬 소켓 사용, Qt 불필요)."""
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


def req(port, path, body=None, headers=None):
    r = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), data=json.dumps(body).encode() if body is not None else None,
                               headers=headers or {}, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(r, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


with tempfile.TemporaryDirectory() as d:
    got = []
    srv = L.LinkServer(lambda q: got.append((threading.current_thread().name, q)), "9.9", d)
    port = srv.start()
    info = json.load(open(os.path.join(d, "animtool.json")))
    check(info["port"] == port and info["token"] == srv.token and info["pid"] == os.getpid(), "접속 정보 파일: %s" % info)
    check(port > 1024, "임의 포트")

    st, js = req(port, "/v1/ping")
    check(st == 200 and js["ok"] and js["app"] == "MotionBridge" and js["pid"] == os.getpid(), "ping")
    check(req(port, "/v1/nothing")[0] == 404, "없는 경로 404")

    qf = os.path.join(d, "req1", "query.json")
    os.makedirs(os.path.dirname(qf))
    json.dump({"id": "r1", "clips": [{"path": "C:/c/a.mp4"}, "C:/c/b.mp4"], "label": "타격", "hits": 3}, open(qf, "w"))
    # 토큰 없음/틀림 -> 401, 요청은 전달되지 않음
    check(req(port, "/v1/open", {"query": qf})[0] == 401, "토큰 없음 401")
    check(req(port, "/v1/open", {"query": qf}, {"X-Token": "wrong"})[0] == 401, "틀린 토큰 401")
    check(not got, "인증 실패는 전달 안 됨")
    # 정상 (두 가지 헤더 방식)
    st, js = req(port, "/v1/open", {"query": qf}, {"X-Token": srv.token})
    check(st == 202 and js["ok"] and len(got) == 1, "정상 요청 전달: %s" % st)
    st, _ = req(port, "/v1/open", {"query": qf}, {"Authorization": "Bearer " + srv.token})
    check(st == 202 and len(got) == 2, "Bearer 방식")
    q = got[0][1]
    check(q["id"] == "r1" and q["paths"] == ["C:/c/a.mp4", "C:/c/b.mp4"] and q["label"] == "타격" and q["hits"] == 3 and q["file"] == qf, "query 해석: %s" % q)
    check(got[0][0] != threading.main_thread().name, "서버 스레드에서 호출됨(GUI 쪽에서 넘겨야 함)")
    # 잘못된 요청
    H = {"X-Token": srv.token}
    check(req(port, "/v1/open", {"nope": 1}, H)[0] == 400, "query 없음 400")
    check(req(port, "/v1/open", {"query": os.path.join(d, "missing.json")}, H)[0] == 422, "없는 query.json 422")
    check(req(port, "/v1/open", {"query": {"id": "x", "path": "p.mp4"}}, H)[0] == 202, "객체 query 허용")
    check(len(got) == 3, "객체 query 전달")

    # 결과: AnimRef 꺼져 있으면 result.json 만
    out = L.deliver(q, ["M1"], ["A1", "A2"], directory=d)
    res = json.load(open(out["file"]))
    check(out["file"] == os.path.join(d, "req1", "result.json") and not out["notified"], "AnimRef 없음: 파일만 (%s)" % out)
    check(res == {"id": "r1", "status": "confirmed", "meshes": ["M1"], "animations": ["A1", "A2"]}, "결과 내용: %s" % res)
    out = L.deliver(q, ["M1"], ["A1"], none=True, directory=d)
    res = json.load(open(out["file"]))
    check(res["status"] == "none" and res["meshes"] == [] and res["animations"] == [], "맞는 게 없음: %s" % res)

    # AnimRef 가 켜져 있으면 POST /v1/result 로 알림
    recv = []

    class FakeAnimRef:
        def __init__(self):
            import http.server

            outer = self

            class H2(http.server.BaseHTTPRequestHandler):
                def log_message(self, *a):
                    pass

                def do_POST(self):
                    n = int(self.headers["Content-Length"])
                    recv.append((self.path, self.headers.get("X-Token"), json.loads(self.rfile.read(n))))
                    self.send_response(200)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"{}")

            self.s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H2)
            self.port = self.s.server_address[1]
            threading.Thread(target=self.s.serve_forever, daemon=True).start()

    fa = FakeAnimRef()
    json.dump({"port": fa.port, "token": "ar-tok", "pid": 1}, open(os.path.join(d, "animref.json"), "w"))
    out = L.deliver(q, ["M1"], ["A1"], directory=d)
    check(out["notified"] and recv and recv[0][0] == "/v1/result" and recv[0][1] == "ar-tok" and recv[0][2]["status"] == "confirmed", "AnimRef 에 알림: %s" % recv)
    fa.s.shutdown()
    out = L.deliver(q, ["M1"], ["A1"], directory=d)
    check(not out["notified"] and os.path.isfile(out["file"]), "AnimRef 가 죽어 있으면 파일만")

    # 객체 query 면 결과는 링크 폴더에
    oq = L.parse_query({"id": "x", "path": "p.mp4"})
    check(L.result_path(oq, d) == os.path.join(d, "result.json"), "객체 query 결과 위치")

    # 종료하면 접속 정보 삭제 (내 파일일 때만)
    srv.stop()
    check(not os.path.exists(os.path.join(d, "animtool.json")), "종료 시 삭제")
    try:
        req(port, "/v1/ping")
        check(False, "종료 후 접속 거부")
    except (urllib.error.URLError, ConnectionError, OSError):
        pass
    s2 = L.LinkServer(lambda q: None, "", d)
    s2.start()
    s3 = L.LinkServer(lambda q: None, "", d)
    s3.start()  # 나중에 켠 쪽이 파일을 차지
    s2.stop()
    check(os.path.exists(os.path.join(d, "animtool.json")), "남의 접속 정보 파일은 지우지 않음")
    s3.stop()
    check(not os.path.exists(os.path.join(d, "animtool.json")), "마지막 서버 종료 시 삭제")

print("모두 통과" if not fails else "실패 %d개" % len(fails))
sys.exit(1 if fails else 0)
