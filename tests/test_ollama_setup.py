"""GPT-OSS-20B 선택지와 Ollama 자동 설치·준비 흐름 (네트워크·설치는 가짜로 바꿔 끼운다)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from motionbridge.core import ollama_setup as S  # noqa: E402
from motionbridge.core import vision  # noqa: E402


def check(c, m):
    if not c:
        raise AssertionError(m)
    print("  ok:", m)


# --- 공급자: 글 전용 + 요청 형태
calls = []


def post(url, payload, headers, timeout):
    calls.append((url, payload))
    if url.endswith("/api/tags"):
        return {"models": [{"name": "gpt-oss:20b"}]}
    if url.endswith("/api/chat"):
        return {"message": {"content": '```json\n{"characters":[{"name":"Dwarf","confidence":0.9}]}\n```', "thinking": "..."}}
    raise vision.VisionError("HTTP 404")


g = vision.make_provider("gptoss", {}, post)
check(isinstance(g, vision.GptOssProvider) and g.model == "gpt-oss:20b" and g.key == "gptoss" and not g.supports_images, "make_provider gptoss")
check(vision.make_provider("gptoss", {"vf_gptoss_model": "gpt-oss:120b"}, post).model == "gpt-oss:120b", "모델 직접 지정")
check(g.vocab_limit() == 500, "이름 목록 한도")
ok, msg, names = g.check()
check(ok and "글 전용" in msg, "준비 확인: " + msg)
txt = g.ask_text("hi")
pl = calls[-1][1]
check("Dwarf" in txt and "format" not in pl and pl["options"]["num_ctx"] >= 16384 and pl["think"] == "low" and "images" not in pl["messages"][0], "텍스트 요청 형태")
check(vision.parse_response(txt)[0].name == "Dwarf", "코드블록 JSON 도 해석")
try:
    g.ask("x.png", "p")
    check(False, "이미지 거부")
except vision.VisionError as e:
    check("글만" in str(e), "이미지 인식은 안내와 함께 거부")
try:
    g.analyze("x.png", ["a"])
    check(False, "analyze 거부")
except vision.VisionError:
    check(True, "analyze 도 거부")
check(vision.OllamaProvider("qwen2.5vl:7b", post=post).supports_images, "비전 모델은 이미지 가능")
ok, msg, _ = vision.GptOssProvider(post=lambda u, p, h, t: {"models": []}).check()
check(not ok and "14GB" in msg and "gpt-oss:20b" in msg, "없을 때 안내: " + msg)
check(vision.is_text_only("gpt-oss:120b") and not vision.is_text_only("gemma3:12b"), "글 전용 판정")

# --- ensure_ready: 필요한 것만 순서대로
log = []


class P(vision.OllamaProvider):
    def __init__(self, have_model):
        super().__init__("gpt-oss:20b", post=lambda *a: {"models": [{"name": "gpt-oss:20b"}] if have_model else []})

    def pull(self, on_progress=None):
        log.append("pull")


state = {"installed": False, "running": False}


def mk(inst, run, have):
    state.update(installed=inst, running=run)
    log.clear()
    kw = dict(
        install_fn=lambda cb: (log.append("install"), state.update(installed=True))[0] or "ollama.exe",
        find_fn=lambda: "ollama.exe" if state["installed"] else None,
        running_fn=lambda host: state["running"],
        start_fn=lambda exe: (log.append("start"), state.update(running=True)),
        sleep=lambda s: None,
    )
    return S.ensure_ready(P(have), lambda p, m: None, **kw)


msg = mk(False, False, False)
check(log == ["install", "start", "pull"] and "설치" in msg and "받기" in msg, "처음부터: %s %s" % (log, msg))
msg = mk(True, False, False)
check(log == ["start", "pull"], "설치됨·꺼짐: %s" % log)
msg = mk(True, True, False)
check(log == ["pull"], "켜짐·모델 없음: %s" % log)
msg = mk(True, True, True)
check(log == [] and "이미 모두" in msg, "모두 준비: " + msg)

# 실행했는데 응답이 없으면 오류
state.update(installed=True, running=False)
try:
    S.ensure_ready(P(True), find_fn=lambda: "o", running_fn=lambda h: False, start_fn=lambda e: None, sleep=lambda s: None, wait_s=0.0)
    check(False, "무응답 오류")
except vision.VisionError as e:
    check("응답이 없습니다" in str(e), "서버 무응답 안내")

# --- install
import types  # noqa: E402

try:
    S.install(windows=False)
    check(False, "비윈도우 안내")
except vision.VisionError as e:
    check("ollama.com" in str(e), "비윈도우는 안내만")
steps = []
exe = S.install(lambda p, m: steps.append("p"), windows=True,
                download_fn=lambda url, dest, cb: (steps.append("dl:" + url), open(dest, "wb").write(b"x")),
                run=lambda cmd, **kw: (steps.append("run:" + " ".join(cmd[1:])), types.SimpleNamespace(returncode=0))[1],
                find_fn=lambda: "C:/o/ollama.exe")
check(exe == "C:/o/ollama.exe" and steps[0] == "dl:" + S.INSTALLER_URL and steps[-1].startswith("run:/VERYSILENT"), "윈도우 설치 순서: %s" % steps)
try:
    S.install(windows=True, download_fn=lambda *a: None, run=lambda *a, **k: types.SimpleNamespace(returncode=1), find_fn=lambda: None)
    check(False, "설치 실패 오류")
except vision.VisionError as e:
    check("설치가 끝나지 않았습니다" in str(e), "설치 실패 안내")
try:
    S.install(windows=True, download_fn=lambda *a: (_ for _ in ()).throw(OSError("net")))
    check(False, "다운로드 실패")
except vision.VisionError as e:
    check("받지 못했습니다" in str(e), "다운로드 실패 안내")
print("test_ollama_setup ALL OK")
