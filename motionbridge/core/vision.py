"""장면 속 몬스터를 알아보는 이미지 인식 연결부: Ollama(내 PC) 또는 Claude(API).

  * 두 방식 모두 같은 질문을 보내고 같은 JSON 형식으로 받는다.
  * 게임에 실제로 있는 캐릭터 이름 목록(vocab)을 함께 보내, 목록 안에서 고르게 한다.
  * 네트워크 호출은 post/get 함수를 바꿔 끼울 수 있어 시험할 때 가짜로 대체한다.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .assetidx import DESC_STOP, UNKNOWN_WORDS, norm_key, tokens

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
OLLAMA_MODELS = ["qwen2.5vl:7b", "llama3.2-vision", "gemma3:12b", "minicpm-v"]
CLAUDE_MODELS = ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"]
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
VOCAB_LIMIT = {"ollama": 250, "claude": 800}  # 한 번에 알려줄 이름 수 (작은 모델은 긴 목록에 약하다)


class VisionError(Exception):
    pass


@dataclass
class Detection:
    name: str
    confidence: float = 0.0
    description: str = ""
    box: Optional[Tuple[float, float, float, float]] = None  # 장면 안 위치 (0~1 비율: 왼, 위, 오른, 아래)
    frame: str = ""


@dataclass
class Group:
    """여러 장면에서 같은 몬스터로 묶은 결과 (검수 표의 한 줄)."""
    name: str
    description: str
    seen: int
    confidence: float
    frame: str
    box: Optional[Tuple[float, float, float, float]]
    frames: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------- 질문과 답 해석
def build_prompt(vocab: Sequence[str], game_hint: str = "", known: Sequence[str] = ()) -> str:
    names = ", ".join(vocab)
    game = (" from the video game %s" % game_hint) if game_hint else " from a video game"
    extra = ""
    if known:
        extra = ("The user says these characters appear in the video (their in-game display name, the likely internal name, and a note):\n%s\n"
                 "Use this as a strong hint. If a visible character matches one of them, answer with its internal name from the list.\n\n"
                 % "\n".join("- " + k for k in known))
    return (
        "This is a screenshot%s. Identify every distinct monster, enemy, boss or NPC character visible, and also the player-controlled "
        "(playable) character when its body is visible (ignore first-person hands/weapon, UI, and scenery). Several can be on screen at once. "
        "For the playable character use a name from the list that contains words like player, hero or protagonist if one exists, otherwise answer \"player\".\n"
        "%sKnown character names in this game's files:\n[%s]\n\n"
        "For each visible character answer with:\n"
        '  "name": the matching name from the list above exactly as written. Do NOT force a match: if the character is not clearly one of them '
        '(for example an unnamed or unique boss/NPC, or a variant you cannot tell apart), answer "unknown",\n'
        '  "confidence": a number from 0 to 1 (use below 0.5 when you are unsure),\n'
        '  "description": a short English description of its look (colors, body shape, weapon),\n'
        '  "box": [left, top, right, bottom] as fractions of the image (0 to 1), or null if unsure.\n'
        'Reply with ONLY JSON in this form: {"characters":[{"name":"...","confidence":0.0,"description":"...","box":null}]}. '
        'If no character is visible reply {"characters":[]}.' % (game, extra, names)
    )


def _extract_json(text: str):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I)
    try:
        return json.loads(t)
    except ValueError:
        pass
    for open_, close in (("{", "}"), ("[", "]")):
        a, b = t.find(open_), t.rfind(close)
        if a != -1 and b > a:
            try:
                return json.loads(t[a:b + 1])
            except ValueError:
                continue
    return None


def _norm_box(v) -> Optional[Tuple[float, float, float, float]]:
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(x) for x in v)
    except (TypeError, ValueError):
        return None
    m = max(x0, y0, x1, y1)
    if m > 1.5:
        if m > 1000:
            return None
        x0, y0, x1, y1 = x0 / 1000.0, y0 / 1000.0, x1 / 1000.0, y1 / 1000.0  # 0~1000 눈금으로 답한 경우
    x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
    y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
    if x1 - x0 < 0.02 or y1 - y0 < 0.02:
        return None
    return (x0, y0, x1, y1)


def parse_response(text: str, frame: str = "") -> List[Detection]:
    """모델 답(JSON 비슷한 글)을 Detection 목록으로. 형식이 조금 틀려도 읽을 수 있는 만큼 읽는다."""
    obj = _extract_json(text)
    if isinstance(obj, dict):
        obj = obj.get("characters") or obj.get("monsters") or obj.get("results") or []
    if not isinstance(obj, list):
        raise VisionError("인식 결과를 해석하지 못했습니다: " + (text or "")[:200])
    out: List[Detection] = []
    for it in obj:
        if isinstance(it, str):
            it = {"name": it}
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or it.get("character") or "").strip()[:80]
        desc = str(it.get("description") or "").strip()[:240]
        if not name and not desc:
            continue
        try:
            conf = float(it.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        if conf > 1.0:
            conf = conf / 100.0 if conf <= 100 else 1.0
        out.append(Detection(name or "unknown", max(0.0, min(1.0, conf)), desc, _norm_box(it.get("box")), frame))
    return out


# ---------------------------------------------------------------------- 네트워크 (바꿔 끼울 수 있음)
def _http(url: str, payload: Optional[dict], headers: Dict[str, str], timeout: float) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:400]
        except Exception:  # noqa: BLE001
            pass
        raise VisionError("HTTP %d %s" % (e.code, body))
    except urllib.error.URLError as e:
        raise VisionError("연결할 수 없습니다: %s" % getattr(e, "reason", e))
    except (TimeoutError, OSError) as e:
        raise VisionError("응답이 없습니다: %s" % e)


def _http_stream(url: str, payload: dict, timeout: float):
    """줄 단위 JSON 을 흘려주는 응답을 하나씩 돌려준다 (Ollama 모델 내려받기 진행 상황)."""
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    yield json.loads(raw.decode("utf-8"))
                except ValueError:
                    continue
    except urllib.error.HTTPError as e:
        raise VisionError("HTTP %d" % e.code)
    except urllib.error.URLError as e:
        raise VisionError("연결할 수 없습니다: %s" % getattr(e, "reason", e))
    except (TimeoutError, OSError) as e:
        raise VisionError("응답이 없습니다: %s" % e)


def _b64(path: str) -> Tuple[str, str]:
    ext = os.path.splitext(path)[1].lower()
    mt = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}.get(ext, "image/jpeg")
    with open(path, "rb") as f:
        return mt, base64.b64encode(f.read()).decode("ascii")


class Provider:
    key = ""
    label = ""

    def __init__(self, post: Callable = _http) -> None:
        self.post = post

    def vocab_limit(self) -> int:
        return VOCAB_LIMIT[self.key]

    def ask(self, image_path: str, prompt: str) -> str:
        return self._send(prompt, image_path)

    def ask_text(self, prompt: str) -> str:
        """그림 없이 글로만 묻는다 (게임 속 이름 -> 비슷한 리소스 이름 고르기 등)."""
        return self._send(prompt, None)

    def _send(self, prompt: str, image_path: Optional[str]) -> str:  # pragma: no cover - 하위 클래스가 구현
        raise NotImplementedError

    def analyze(self, image_path: str, vocab: Sequence[str], game_hint: str = "", known: Sequence[str] = ()) -> List[Detection]:
        text = self.ask(image_path, build_prompt(list(vocab)[: self.vocab_limit()], game_hint, known))
        return parse_response(text, image_path)


class OllamaProvider(Provider):
    key = "ollama"
    label = "Ollama (내 PC, 무료)"

    def __init__(self, model: str = OLLAMA_MODELS[0], host: str = DEFAULT_OLLAMA_HOST, post: Callable = _http, timeout: float = 300.0,
                 stream: Callable = _http_stream) -> None:
        super().__init__(post)
        self.model, self.host, self.timeout, self.stream = model, host.rstrip("/"), timeout, stream

    def pull(self, on_progress: Optional[Callable[[int, str], None]] = None) -> None:
        """모델을 내려받는다 (Ollama 가 실행 중이어야 함). on_progress(퍼센트, 상태 글)."""
        pct, ok = 0, False
        for ev in self.stream(self.host + "/api/pull", {"name": self.model, "stream": True}, 3600.0):
            if ev.get("error"):
                raise VisionError("모델을 받지 못했습니다: %s" % ev["error"])
            total, done = ev.get("total"), ev.get("completed")
            if total and done is not None:
                pct = max(0, min(100, int(done * 100 / total)))
            status = str(ev.get("status") or "")
            ok = ok or status == "success"
            if on_progress:
                on_progress(100 if status == "success" else pct, status)
        if not ok:
            raise VisionError("모델 내려받기가 끝나지 않았습니다. 인터넷 연결을 확인하고 다시 시도하세요.")

    def check(self) -> Tuple[bool, str, List[str]]:
        """(사용 가능, 안내, 설치된 모델 이름들)"""
        try:
            d = self.post(self.host + "/api/tags", None, {}, 10.0)
        except VisionError as e:
            return False, "Ollama 에 연결하지 못했습니다. Ollama 가 실행 중인지 확인하세요. (%s)" % e, []
        names = [m.get("name", "") for m in d.get("models", []) if isinstance(m, dict)]
        base = self.model.split(":")[0]
        if not any(n == self.model or n.split(":")[0] == base for n in names):
            have = ("\n이미 설치된 모델: %s (모델 칸에 이 중 하나를 적어도 됩니다)" % ", ".join(names)) if names else ""
            return False, ("모델 '%s' 이(가) 아직 내려받아지지 않았습니다 (Ollama 는 켜져 있습니다).\n"
                           "아래 [모델 받기] 버튼을 누르세요 (약 6GB, 한 번만 받으면 됩니다). 직접 받으려면 명령 창에서 'ollama pull %s'.%s") % (self.model, self.model, have), names
        return True, "Ollama 준비 완료 (%s)" % self.model, names

    def _send(self, prompt: str, image_path: Optional[str]) -> str:
        msg0 = {"role": "user", "content": prompt}
        if image_path:
            msg0["images"] = [_b64(image_path)[1]]
        payload = {"model": self.model, "stream": False, "format": "json", "options": {"temperature": 0}, "messages": [msg0]}
        try:
            d = self.post(self.host + "/api/chat", payload, {"Content-Type": "application/json"}, self.timeout)
        except VisionError as e:
            if "404" in str(e):
                raise VisionError("Ollama 에 모델 '%s' 이(가) 없습니다: ollama pull %s" % (self.model, self.model))
            raise
        msg = d.get("message") or {}
        return str(msg.get("content") or "")


class ClaudeProvider(Provider):
    key = "claude"
    label = "Claude (API 키 필요, 더 정확)"

    def __init__(self, api_key: str, model: str = CLAUDE_MODELS[0], post: Callable = _http, timeout: float = 120.0) -> None:
        super().__init__(post)
        self.api_key, self.model, self.timeout = (api_key or "").strip(), model, timeout

    def check(self) -> Tuple[bool, str, List[str]]:
        if not self.api_key:
            return False, "Claude API 키를 입력하세요 (환경변수 ANTHROPIC_API_KEY 도 사용 가능).", []
        if not self.api_key.startswith("sk-"):
            return False, "API 키 형식이 이상합니다 (보통 sk- 로 시작).", []
        return True, "Claude 사용 준비 (%s) — 호출한 만큼 요금이 듭니다." % self.model, []

    def _send(self, prompt: str, image_path: Optional[str]) -> str:
        if not self.api_key:
            raise VisionError("Claude API 키가 없습니다.")
        content: List[dict] = []
        if image_path:
            mt, b64 = _b64(image_path)
            content.append({"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}})
        content.append({"type": "text", "text": prompt})
        payload = {"model": self.model, "max_tokens": 1024, "messages": [{"role": "user", "content": content}]}
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        try:
            d = self.post(ANTHROPIC_URL, payload, headers, self.timeout)
        except VisionError as e:
            s = str(e)
            if "HTTP 401" in s:
                raise VisionError("Claude API 키가 거부됐습니다. 키를 확인하세요.")
            if "HTTP 404" in s:
                raise VisionError("Claude 모델 '%s' 을(를) 쓸 수 없습니다. 다른 모델을 고르세요." % self.model)
            if "HTTP 429" in s:
                raise VisionError("Claude 호출 한도에 걸렸습니다. 잠시 뒤 다시 시도하세요.")
            raise
        parts = d.get("content") or []
        return "".join(p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text")


def make_provider(kind: str, cfg: dict, post: Callable = _http) -> Provider:
    if kind == "claude":
        key = (cfg.get("vf_claude_key") or os.environ.get("ANTHROPIC_API_KEY") or "").strip()
        return ClaudeProvider(key, cfg.get("vf_claude_model") or CLAUDE_MODELS[0], post)
    return OllamaProvider(cfg.get("vf_ollama_model") or OLLAMA_MODELS[0], cfg.get("vf_ollama_host") or DEFAULT_OLLAMA_HOST, post)


# ---------------------------------------------------------------------- 게임 속 이름 -> 리소스 이름 (글만)
def build_name_prompt(in_game_name: str, vocab: Sequence[str], description: str = "") -> str:
    return ("A video game shows a character whose in-game display name is: \"%s\"%s\n"
            "The game files name the same character differently (internal asset names). Below is the closed list of internal "
            "asset names. Pick up to 5 names from the list that are most likely this character, best first. Internal names often use "
            "synonyms or different wording (e.g. 'captive' may be 'prisoner' or 'convict'; 'nameless' may be 'unnamed' or 'faceless'). "
            "Use ONLY names exactly as written in the list. If none is plausible, answer with an empty list.\n"
            "Answer with JSON only: {\"matches\": [{\"name\": \"<name from list>\", \"confidence\": 0.0-1.0, \"reason\": \"<short>\"}]}\n"
            "List: %s") % (in_game_name, (" (" + description + ")") if description else "", ", ".join(vocab))


def suggest_from_vocab(provider: "Provider", in_game_name: str, vocab: Sequence[str], description: str = "") -> List[Tuple[str, float, str]]:
    """AI 에게 이름 목록 안에서 고르게 한다. 목록에 없는 이름은 버린다 (지어내기 방지). [(이름, 확신, 이유)]"""
    names = list(vocab)[: provider.vocab_limit()]
    if not names or not (in_game_name or "").strip():
        return []
    text = provider.ask_text(build_name_prompt(in_game_name.strip(), names, description))
    obj = _extract_json(text)
    if isinstance(obj, dict):
        obj = obj.get("matches") or obj.get("results") or []
    if not isinstance(obj, list):
        raise VisionError("이름 추천 결과를 해석하지 못했습니다: " + (text or "")[:200])
    lower = {n.lower(): n for n in names}
    out: List[Tuple[str, float, str]] = []
    for it in obj:
        if isinstance(it, str):
            it = {"name": it}
        if not isinstance(it, dict):
            continue
        n = lower.get(str(it.get("name") or "").strip().lower())
        if not n or any(n == o[0] for o in out):
            continue
        try:
            c = float(it.get("confidence", 0.5))
        except (TypeError, ValueError):
            c = 0.5
        out.append((n, max(0.0, min(1.0, c / 100.0 if c > 1.0 else c)), str(it.get("reason") or "")[:120]))
    return out[:5]


# ---------------------------------------------------------------------- 여러 장면 묶기
def _desc_set(text: str) -> set:
    return {w for w in tokens(text) if w not in DESC_STOP}


def aggregate(dets: Sequence[Detection]) -> List[Group]:
    """같은 이름(또는 비슷한 설명)의 인식 결과를 한 줄로 묶는다. 자주/확실하게 보인 순."""
    groups: Dict[str, Group] = {}
    udesc: Dict[str, set] = {}  # 이름을 모르는 묶음의 설명 단어
    for d in dets:
        unknown = d.name.strip().lower() in UNKNOWN_WORDS
        if unknown:
            ds = _desc_set(d.description)
            if not ds:
                continue
            key = ""
            for k, other in udesc.items():  # 설명 단어가 절반 이상 겹치면 같은 몬스터로 본다
                if len(ds & other) / len(ds | other) >= 0.5:
                    key = k
                    break
            if not key:
                key = "d:%d" % len(udesc)
                udesc[key] = ds
        else:
            key = "n:" + norm_key(d.name)
            if key == "n:":
                continue
        g = groups.get(key)
        if g is None:
            groups[key] = Group("" if unknown else d.name, d.description, 1, d.confidence, d.frame, d.box, [d.frame])
            continue
        g.seen += 1
        if d.frame and d.frame not in g.frames:
            g.frames.append(d.frame)
        if d.confidence > g.confidence:
            g.confidence, g.frame, g.box = d.confidence, d.frame, d.box
            if d.description:
                g.description = d.description
    out = list(groups.values())
    out.sort(key=lambda g: (-g.seen, -g.confidence, g.name.lower()))
    return out


def build_motion_prompt(description: str) -> str:
    return ("A video game monster's attack or movement is described below (the text may be Korean or English). Convert it into short ENGLISH "
            "keywords that would appear in an animation asset file name (single lowercase words such as spin, whirlwind, slam, slash, swing, "
            "thrust, leap, charge, roar, combo, throw, summon, cast). Give 3 to 8 keywords, most important first. Do not invent monster names.\n"
            "Answer with JSON only: {\"keywords\": [\"spin\", \"...\"]}\n"
            "Description: %s") % description


def motion_keywords(provider: "Provider", description: str) -> List[str]:
    """동작 설명 -> 애니메이션 파일 이름에 나올 법한 영어 낱말 (AI 는 번역만 하고 에셋 이름은 만들지 않는다)."""
    if not (description or "").strip():
        return []
    text = provider.ask_text(build_motion_prompt(description.strip()))
    obj = _extract_json(text)
    if isinstance(obj, dict):
        obj = obj.get("keywords") or obj.get("words") or []
    if not isinstance(obj, list):
        raise VisionError("동작 키워드 결과를 해석하지 못했습니다: " + (text or "")[:200])
    out: List[str] = []
    for w in obj:
        w = re.sub(r"[^a-z0-9]", "", str(w).lower())
        if 2 <= len(w) <= 20 and w not in out:
            out.append(w)
    return out[:8]
