"""AI 호출 재시도: 일시 오류(503 과부하·429 한도)는 기다렸다 다시, 그 밖의 오류는 바로 올린다. Gemini 는 가벼운 모델로 대체."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from motionbridge.core import vision

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("FAIL", msg)


slept = []
vision.RETRY_DELAYS = (0.0, 0.0, 0.0)

# 503 두 번 뒤 성공 -> 결과를 돌려준다
calls = []


def flaky(url, payload, headers, timeout):
    calls.append(payload.get("model"))
    if len(calls) <= 2:
        raise vision.VisionError('HTTP 503 {"error":{"message":"This model is currently experiencing high demand"}}')
    return {"choices": [{"message": {"content": "ok"}}]}


p = vision.OpenAICompatProvider("k", "gpt-4o", "https://api.example.com/v1", post=flaky)
check(p.ask_text("hi") == "ok" and len(calls) == 3, "503 두 번 뒤 재시도 성공: %s" % calls)

# 401 은 재시도하지 않고 바로 키 오류
calls.clear()


def denied(url, payload, headers, timeout):
    calls.append(1)
    raise vision.VisionError("HTTP 401 bad key")


p = vision.OpenAICompatProvider("k", "gpt-4o", "https://api.example.com/v1", post=denied)
try:
    p.ask_text("hi")
    check(False, "401 은 오류가 나야 함")
except vision.VisionError as e:
    check("거부" in str(e) and len(calls) == 1, "401 은 재시도 없이 키 오류: %s / %d회" % (e, len(calls)))

# Gemini: 기본 모델이 계속 503 이면 가벼운 모델로 대체해 성공
calls.clear()


def busy_main(url, payload, headers, timeout):
    calls.append(payload.get("model"))
    if payload.get("model") == "gemini-flash-latest":
        raise vision.VisionError("HTTP 503 overloaded")
    return {"choices": [{"message": {"content": "lite ok"}}]}


p = vision.OpenAICompatProvider("k", "gemini-flash-latest", "https://generativelanguage.googleapis.com/v1beta/openai", post=busy_main)
check(p.ask_text("hi") == "lite ok" and calls[-1] == "gemini-flash-lite-latest", "Gemini 과부하 -> lite 대체: %s" % calls)

# 끝까지 503 이면 알아듣기 쉬운 안내
def always_busy(url, payload, headers, timeout):
    raise vision.VisionError("HTTP 503 overloaded")


p = vision.OpenAICompatProvider("k", "gpt-4o", "https://api.example.com/v1", post=always_busy)
try:
    p.ask_text("hi")
    check(False, "계속 503 이면 오류")
except vision.VisionError as e:
    check("붐빕니다" in str(e), "계속 503 -> 붐빔 안내: %s" % e)

if fails:
    print("\n실패 %d개" % len(fails))
    sys.exit(1)
print("test_vision_retry ALL OK")
