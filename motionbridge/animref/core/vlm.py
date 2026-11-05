"""영상 이해 AI (내 PC의 Ollama + Qwen3-VL 등)로 클립을 판정한다.

클립에서 프레임 여러 장을 시간 순서대로 번호를 붙여 한 장의 그리드로 만들고,
'움직임/효과음/레터박스/컷 전환' 단서와 함께 보내서 분류 하나를 JSON으로 받는다.
Ollama 공식 REST API만 사용하므로 추가 파이썬 패키지가 필요 없다.
"""
import base64
import functools
import hashlib
import json
import urllib.error
import urllib.request

from . import Cancelled, media, noop

OLLAMA_DOWNLOAD = "https://ollama.com/download"

# (최소 VRAM GB, 모델, 설명)
RECOMMENDED = [
    (10.0, "qwen3-vl:8b", "정확도 높음 · 약 6GB 다운로드 · 그래픽카드 메모리 12GB 권장"),
    (6.0, "qwen3-vl:4b", "균형 · 약 3.3GB 다운로드 · 그래픽카드 메모리 8GB 권장"),
    (0.0, "qwen3-vl:2b", "가벼움 · 약 2GB 다운로드 · 저사양/CPU용 (정확도 낮음)"),
]

# HTTP 프록시 설정을 무시하고 localhost에 직접 연결
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


PROMPT_VER = 1


class OllamaError(RuntimeError):
    pass


class OllamaUnavailable(OllamaError):
    """Ollama에 연결할 수 없거나 모델이 없음 (클립 문제가 아님)."""


@functools.lru_cache(maxsize=1)
def gpu_vram_gb():
    try:
        import torch

        if torch.cuda.is_available():
            return torch.cuda.get_device_properties(0).total_memory / 1024 ** 3, torch.cuda.get_device_name(0)
    except Exception:
        pass
    return 0.0, None


def recommend_model():
    vram, _ = gpu_vram_gb()
    for need, model, desc in RECOMMENDED:
        if vram >= need:
            return model, desc
    return RECOMMENDED[-1][1], RECOMMENDED[-1][2]


def model_of(s):
    return (s.get("vlm_model") or "").strip() or recommend_model()[0]


def _req(s, path, payload=None, timeout=10):
    url = s.get("ollama_url", "http://127.0.0.1:11434").rstrip("/") + path
    url = url.replace("//localhost:", "//127.0.0.1:")  # Windows에서 IPv6 시도 지연 방지
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"},
                                 method="GET" if payload is None else "POST")
    return _OPENER.open(req, timeout=timeout)


def status(s):
    """(실행 중?, 설치된 모델 이름 목록, 오류 메시지)"""
    try:
        with _req(s, "/api/tags", timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
        return True, [m.get("name", "") for m in data.get("models", [])], ""
    except Exception as e:
        return False, [], str(e)


def has_model(names, model):
    model = model.strip().lower()
    want = model if ":" in model else model + ":latest"
    return any(n.lower() in (want, model) for n in names)


def pull(s, model=None, log=noop, progress=noop, cancel=None):
    """모델을 내려받는다 (진행률 표시)."""
    model = model or model_of(s)
    log(f"영상 이해 AI 모델 내려받는 중: {model}")
    try:
        r = _req(s, "/api/pull", {"model": model, "name": model, "stream": True}, timeout=3600)
    except urllib.error.HTTPError as e:
        raise OllamaError(f"모델을 내려받지 못했어요: {_http_error_text(e)}") from e
    except urllib.error.URLError as e:
        raise OllamaUnavailable("Ollama에 연결할 수 없어요. Ollama를 설치하고 실행했는지 확인하세요.") from e
    last = ""
    with r:
        for line in r:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            try:
                ev = json.loads(line.decode("utf-8"))
            except Exception:
                continue
            if ev.get("error"):
                raise OllamaError(f"모델을 내려받지 못했어요: {ev['error']}"
                                  + ("\n(Ollama를 최신 버전으로 업데이트해 보세요)" if "version" in str(ev["error"]).lower() else ""))
            total, done = ev.get("total"), ev.get("completed")
            last = ev.get("status", last)
            if total and done:
                progress(done / total, f"모델 내려받는 중 {model}  {done / 1e9:.2f} / {total / 1e9:.2f} GB")
            else:
                progress(-1, f"모델 준비 중: {last}")
    if last != "success":
        raise OllamaError("모델 내려받기가 중간에 끊겼어요. 다시 시도해 주세요.")
    progress(1.0, f"모델 준비 완료: {model}")
    log(f"모델 준비 완료: {model}")
    return model


def frame_grid(video, n=8, tile_w=448, cols=4):
    """클립에서 n장을 뽑아 번호를 붙인 그리드 JPEG(bytes)."""
    import cv2
    import numpy as np

    frames = media.sample_frames(video, n, tile_w)
    if not frames:
        return None
    tiles = []
    for i, f in enumerate(frames):
        f = cv2.cvtColor(f, cv2.COLOR_RGB2BGR).copy()
        cv2.rectangle(f, (0, 0), (44, 34), (0, 0, 0), -1)
        cv2.putText(f, str(i + 1), (8, 27), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2, cv2.LINE_AA)
        tiles.append(f)
    th = max(t.shape[0] for t in tiles)
    tiles = [np.pad(t, ((0, th - t.shape[0]), (0, 0), (0, 0))) for t in tiles]
    while len(tiles) % cols:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)]
    grid = np.vstack([np.vstack([r, np.full((4, r.shape[1], 3), 40, np.uint8)]) for r in rows])
    ok, buf = cv2.imencode(".jpg", grid, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return buf.tobytes() if ok else None


def labels_hash(s):
    raw = json.dumps([[l["name"], l.get("prompts", [])] for l in s["labels"]], ensure_ascii=False)
    extra = f"|{model_of(s).lower()}|{s.get('vlm_frames', 8)}|v{PROMPT_VER}"
    return hashlib.md5((raw + extra).encode("utf-8")).hexdigest()[:10]


def build_prompt(s, meta, n_frames):
    h = meta.get("hints") or {}
    cats = "\n".join(f'- "{l["name"]}": ' + ("; ".join(l.get("prompts", [])) or l["name"]) for l in s["labels"])
    clues = []
    if h:
        if h.get("has_audio"):
            clues.append(f"impact-like sound effects detected: {h.get('impact_sounds', 0)}")
        clues.append(f"sudden screen flashes: {h.get('flashes', 0)}")
        clues.append(f"letterbox (black bars) ratio: {h.get('letterbox', 0)}")
        clues.append(f"camera cuts: {h.get('cuts', 0)}")
    return (
        "You are labeling short video-game clips for an animator's motion reference library.\n"
        f"The image is a grid of {n_frames} frames from a {float(meta.get('duration', 0)):.1f}-second clip, "
        "in time order (numbered, left to right, then top to bottom). Look at how the characters move "
        "across the frames: who attacks, whether the attack connects, who reacts.\n\n"
        f"Choose exactly ONE category that best describes the main event of the clip:\n{cats}\n\n"
        + ("Signal-analysis clues (may be noisy): " + "; ".join(clues) + "\n\n" if clues else "")
        + "Rules: if an attack visibly connects, prefer the hit category over the plain attack category. "
        "If nothing combat-related happens, choose the walking, running or idle category when one fits, otherwise the other category. "
        "Give confidence between 0 and 1. Write `reason` as one short sentence in Korean."
    )


def judge(s, meta, video_path, log=noop):
    """클립 하나를 판정 -> (label, confidence, reason)"""
    names = [l["name"] for l in s["labels"]]
    n = int(s.get("vlm_frames", 8))
    img = frame_grid(video_path, n)
    if img is None:
        raise OllamaError("클립에서 프레임을 읽지 못했어요.")
    schema = {
        "type": "object",
        "properties": {
            "label": {"type": "string", "enum": names},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["label", "confidence", "reason"],
    }
    payload = {
        "model": model_of(s),
        "stream": False,
        "format": schema,
        "think": False,
        "options": {"temperature": 0},
        "messages": [{"role": "user", "content": build_prompt(s, meta, n),
                      "images": [base64.b64encode(img).decode("ascii")]}],
    }
    try:
        body = _post_chat(s, payload)
    except OllamaError as e:
        if "think" in str(e).lower():  # 'think' 옵션을 모르는 모델/버전
            payload.pop("think", None)
            body = _post_chat(s, payload)
        else:
            raise
    content = body.get("message", {}).get("content", "")
    try:
        out = json.loads(content)
    except Exception:
        raise OllamaError(f"AI 응답을 이해하지 못했어요: {content[:120]}")
    label = out.get("label")
    if label not in names:
        label = next((x for x in names if x in str(label)), None)
    if label is None:
        raise OllamaError(f"AI가 목록에 없는 분류를 골랐어요: {out.get('label')}")
    try:
        conf = max(0.0, min(1.0, float(out.get("confidence", 0.5))))
    except Exception:
        conf = 0.5
    return label, conf, str(out.get("reason", ""))[:200]


def choose_among(s, prompt, image_jpeg, n):
    """이미지 한 장과 질문을 보내 '0(없음)~n 중 하나'를 고르게 한다. 반환: (번호, 확신 0~1, 이유)"""
    schema = {
        "type": "object",
        "properties": {
            "choice": {"type": "integer", "minimum": 0, "maximum": int(n)},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["choice", "confidence", "reason"],
    }
    payload = {
        "model": model_of(s),
        "stream": False,
        "format": schema,
        "think": False,
        "options": {"temperature": 0},
        "messages": [{"role": "user", "content": prompt,
                      "images": [base64.b64encode(image_jpeg).decode("ascii")]}],
    }
    try:
        body = _post_chat(s, payload)
    except OllamaError as e:
        if "think" in str(e).lower():
            payload.pop("think", None)
            body = _post_chat(s, payload)
        else:
            raise
    content = body.get("message", {}).get("content", "")
    try:
        out = json.loads(content)
        choice = int(out.get("choice", 0))
    except Exception:
        raise OllamaError(f"AI 응답을 이해하지 못했어요: {content[:120]}")
    try:
        conf = max(0.0, min(1.0, float(out.get("confidence", 0.5))))
    except Exception:
        conf = 0.5
    return max(0, min(int(n), choice)), conf, str(out.get("reason", ""))[:200]


def _http_error_text(e):
    msg = e.read().decode("utf-8", "replace")
    try:
        msg = json.loads(msg).get("error", msg)
    except Exception:
        pass
    return msg


def _post_chat(s, payload):
    try:
        with _req(s, "/api/chat", payload, timeout=300) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        msg = _http_error_text(e)
        if "not found" in msg.lower():
            raise OllamaUnavailable(f"모델 '{payload['model']}'이(가) 없어요. [설정]에서 [모델 내려받기]를 눌러 주세요.")
        raise OllamaError(msg)
    except urllib.error.URLError as e:
        raise OllamaUnavailable("Ollama에 연결할 수 없어요. Ollama가 실행 중인지 확인하세요.") from e
