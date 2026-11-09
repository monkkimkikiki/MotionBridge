"""설정 저장/불러오기. 설정은 %APPDATA%/AnimRef/settings.json 에 저장된다."""
import copy
import json
import os
import sys
from pathlib import Path

APP_NAME = "AnimRef"
VERSION = "1.13.2"

# 이전 버전 기본 분류 이름 (사용자가 안 바꿨으면 새 기본값으로 교체)
OLD_DEFAULT_NAMES = ["공격", "피격", "스킬", "사망", "회피", "이동/대기"]

# v1.5.3 이전 기본 분류 (사용자가 고치지 않았으면 새 기본값으로 교체)
PREV_LABELS_V4 = [
    {"name": "타격", "discard": False, "prompts": [
        "an attack connecting with a target: a weapon, fist or projectile hits, with impact sparks or hit flash"]},
    {"name": "피격", "discard": False, "prompts": [
        "a character being hit: flinching, staggering, knocked back or knocked down"]},
    {"name": "공격", "discard": False, "prompts": [
        "a character performing an attack swing, combo or shot that does not clearly hit anything"]},
    {"name": "스킬", "discard": False, "prompts": [
        "a character using a special skill or magic with large visual effects"]},
    {"name": "연출", "discard": False, "prompts": [
        "a cinematic or cutscene: camera cuts, close-ups, letterbox bars, hidden HUD, finisher or ultimate animation"]},
    {"name": "사망", "discard": False, "prompts": [
        "a character dying or collapsing to the ground"]},
    {"name": "회피", "discard": False, "prompts": [
        "a character dodging, rolling, dashing or jumping away"]},
    {"name": "이동/대기", "discard": True, "prompts": [
        "a character walking, running or idling, or only the camera moving, with no combat"]},
    {"name": "기타", "discard": True, "prompts": [
        "menus, loading screens, inventory UI, or anything that is not character action"]},
]


# name: 화면에 보일 이름 / prompts: AI 설명(영어 권장) / discard: 이 분류로 판정되면 자동 삭제
# 걷기·달리기·이동/대기는 기본적으로 자동 삭제지만, [① 수집하기]의 '이동 장면도 포함'을 켜면 지우지 않는다.
MOVE_NAMES = ("걷기", "달리기", "이동/대기")
DEFAULT_LABELS = [
    {"name": "타격", "discard": False, "prompts": [
        "an attack connecting with a target: a weapon, fist or projectile hits, with impact sparks or hit flash"]},
    {"name": "피격", "discard": False, "prompts": [
        "a character being hit: flinching, staggering, knocked back or knocked down"]},
    {"name": "공격", "discard": False, "prompts": [
        "a character performing an attack swing, combo or shot that does not clearly hit anything"]},
    {"name": "스킬", "discard": False, "prompts": [
        "a character using a special skill or magic with large visual effects"]},
    {"name": "연출", "discard": False, "prompts": [
        "a cinematic or cutscene: camera cuts, close-ups, letterbox bars, hidden HUD, finisher or ultimate animation"]},
    {"name": "사망", "discard": False, "prompts": [
        "a character dying or collapsing to the ground"]},
    {"name": "회피", "discard": False, "prompts": [
        "a character dodging, rolling, dashing or jumping away"]},
    {"name": "걷기", "discard": True, "prompts": [
        "a character walking at a normal pace, with no combat"]},
    {"name": "달리기", "discard": True, "prompts": [
        "a character running or sprinting, with no combat"]},
    {"name": "대기", "discard": True, "prompts": [
        "a character standing idle, or only the camera moving, with no character action"]},
    {"name": "기타", "discard": True, "prompts": [
        "menus, loading screens, inventory UI, or anything that is not character action"]},
]

# 분석 속도 프리셋: (탐지 해상도 너비, 분석 fps)
SPEED_PRESETS = {
    "fast": (640, 10),
    "normal": (960, 15),
    "precise": (1280, 20),
}
SPEED_NAMES = {"fast": "빠르게", "normal": "보통", "precise": "정밀하게"}

DEFAULTS = {
    "game": "내 게임",
    "library_dir": str(Path.home() / "Documents" / "AnimRef"),
    "sensitivity": 50,          # 0(적게, 엄격) ~ 100(많이)
    "speed": "normal",
    "targets": "person, humanoid character, monster, creature",
    "detector": "yolov8s-worldv2.pt",
    "conf": 0.25,
    "save_crop": False,
    "save_full": True,
    "combat_only": True,        # 효과음/번쩍임이 있는 구간만 후보로
    "include_move": False,      # True면 걷기·달리기 같은 이동 장면도 클립으로 (combat_only 무시, 이동 분류 자동 삭제 안 함)
    "pre_pad": 1.0,             # 동작 시작 전 여유 (예비동작)
    "post_pad": 1.2,            # 동작 끝난 후 여유 (후속동작/복귀)
    "min_s": 0.3,
    "max_s": 8.0,
    "min_clip_len": 2.0,        # 클립 최소 길이
    "split_mode": "both",       # combo = 연속 동작 / hit = 한 타씩 / both = 둘 다
    "hit_pre": 0.8,             # 한 타 클립: 타격 전 최대 여유
    "hit_post": 0.8,            # 한 타 클립: 타격 후 최대 여유
    "hit_min_len": 1.0,         # 한 타 클립 최소 길이
    "hit_min_gap": 0.3,         # 이보다 가까운 타격음은 한 타로 봄
    "crop_margin": 0.3,
    "crf": 23,
    "source_policy_recorded": "keep",    # OBS로 녹화한 원본: keep / trash / delete (기본: 그대로 두기)
    "source_policy_dropped": "keep",     # 직접 넣은 영상
    "auto_classify": True,
    "group_precision": 50,      # 0(크게 묶기) ~ 100(잘게 묶기)
    "min_group": 3,
    "hotkey": "f9",
    "obs": {"host": "localhost", "port": 4455, "password": ""},
    "boss": {},                 # 보스전 자동 녹화 설정 (core/boss.py DEFAULTS 와 합쳐서 씀)
    "labels": DEFAULT_LABELS,
    "engine": "vlm",            # vlm = 영상 이해 AI(Ollama) / clip = 기본 분류
    "vlm_model": "",            # 비어 있으면 그래픽카드에 맞춰 자동 추천
    "ollama_url": "http://127.0.0.1:11434",
    "vlm_frames": 8,
    "auto_discard": True,       # discard 표시된 분류로 판정된 클립 자동 삭제
    "discard_min_conf": 0.6,
    "obs_record_dir": "",       # OBS 녹화 폴더 (OBS에서 자동으로 알아냄)
    "blender_exe": "",          # AI 모캡: Blender 실행 파일 (비면 자동으로 찾음)
    "rig_blend": "",            # AI 모캡: 모션을 넣을 리그가 들어 있는 .blend 파일
    "mocap_in_place": False,    # AI 모캡: 앞뒤·좌우 이동 빼기
    "mocap_markers": True,      # AI 모캡: 타격 시점 마커
    "mocap_reduce": 0.0,        # AI 모캡: 키 줄이기 허용 각도(도). 0이면 매 프레임
    "mocap_video_bg": False,    # AI 모캡: 참고 영상을 Blender 카메라 배경으로
    "mocap_character": "",      # AI 모캡: 마지막으로 고른 캐릭터 이름 ("" = 자동)
    "mocap_crop_subject": True,   # AI 모캡: 대상을 따라가며 확대해서 분석 (작은 캐릭터를 WHAM이 놓치지 않게)
    "link_enabled": True,       # 추출 툴과 실시간 연결 (내 PC 안에서만 열리는 창구)
    "link_dir": "",             # 연결 정보 폴더 (비우면 %APPDATA%\\AnimRefLink)
    "tool_link_file": "",       # 추출 툴의 연결 정보 파일 (비우면 연결 폴더의 animtool.json)
    "tool_process": "",         # 추출 툴이 켜져 있는지 확인할 이름 (프로그램 이름 또는 창 제목 일부)
    "analysis_python": "",      # 자동 분석(torch)을 돌릴 파이썬. 비우면 AnimRef 설치 폴더의 .venv 를 자동으로 찾음
    "tool_cmd": "",             # 추출 툴 연동: 실행 명령 ({query}, {folder} 자리표시자)
    "tool_copy_video": False,   # 추출 툴 연동: 영상을 연동 폴더에 복사
    "tool_open_video": True,    # 추출 툴 연동: 보낸 뒤 영상 열기
    "mocap_use_vlm": True,      # AI 모캡: 대상이 애매하면 AI(VLM)에게 확인
    "last_dir": "",
    "welcome_shown": False,
    "settings_version": 5,
}


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    d = Path(base) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def models_dir() -> Path:
    d = app_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def resource_path(rel: str) -> Path:
    """exe로 묶였을 때도 assets 경로를 찾는다."""
    base = Path(__file__).resolve().parent.parent  # MotionBridge 안: 패키지(animref) 폴더 (exe 로 묶여도 같은 위치로 풀림)
    return base / rel


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def settings_path() -> Path:
    return app_dir() / "settings.json"


def load() -> dict:
    p = settings_path()
    data = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    s = _merge(DEFAULTS, data)
    _migrate(s, data)
    # 손상된 값(타입이 다르거나 비어 있음)은 기본값으로 되돌린다
    for k, dv in DEFAULTS.items():
        if dv is not None and not isinstance(s.get(k), type(dv)) and not (
                isinstance(dv, float) and isinstance(s.get(k), int)):
            s[k] = copy.deepcopy(dv)
    for k, dv in DEFAULTS["obs"].items():
        if not isinstance(s["obs"].get(k), type(dv)):
            s["obs"][k] = dv
    if s.get("split_mode") not in ("combo", "hit", "both"):
        s["split_mode"] = "both"
    for k in ("source_policy_recorded", "source_policy_dropped"):
        if s.get(k) not in ("keep", "trash", "delete"):
            s[k] = DEFAULTS[k]
    if not s.get("labels"):
        s["labels"] = copy.deepcopy(DEFAULT_LABELS)
    return s


def _migrate(s, data):
    """이전 버전 설정을 새 기본값으로 올린다."""
    try:
        ver = int(data.get("settings_version", 1) or 1) if data else 2
    except (TypeError, ValueError):
        ver = 1
    if data and ver < 2:
        s["save_full"], s["save_crop"] = True, False   # 전체 화면 클립을 기본으로
        if s.get("crf") == 18:
            s["crf"] = 23
        raw = data.get("labels") if isinstance(data.get("labels"), list) else []
        names = [l.get("name") for l in raw if isinstance(l, dict)]
        if not names or names == OLD_DEFAULT_NAMES:
            s["labels"] = copy.deepcopy(DEFAULT_LABELS)
    if data and ver < 3:
        # 클립이 짧게 끊기던 이전 기본값을 새 기본값으로
        if s.get("pre_pad") == 0.5:
            s["pre_pad"] = 1.0
        if s.get("post_pad") == 0.5:
            s["post_pad"] = 1.2
        if s.get("max_s") == 4.0:
            s["max_s"] = 8.0
    if data and ver < 4:
        # 예전 기본값(휴지통)이 그대로 저장돼 있던 경우 새 기본값(그대로 두기)으로
        if s.get("source_policy_recorded") == "trash":
            s["source_policy_recorded"] = "keep"
    if data and ver < 5:
        # 분류표를 고치지 않았다면 걷기/달리기가 들어간 새 기본 분류표로
        def _norm(ls):
            try:
                return [(l["name"], bool(l.get("discard")), list(l.get("prompts", []))) for l in ls]
            except Exception:
                return None
        if _norm(data.get("labels") or []) == _norm(PREV_LABELS_V4):
            s["labels"] = copy.deepcopy(DEFAULT_LABELS)
    s["settings_version"] = DEFAULTS["settings_version"]
    if not isinstance(s.get("labels"), list):
        s["labels"] = []
    s["labels"] = [l for l in s["labels"] if isinstance(l, dict) and l.get("name")]
    for l in s["labels"]:
        l.setdefault("discard", False)
        l.setdefault("prompts", [])


def save(s: dict):
    p = settings_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def percentile(s) -> float:
    """민감도(0~100) -> 움직임 에너지 백분위 임계값. 민감할수록 낮은 백분위."""
    sens = max(0, min(100, int(s.get("sensitivity", 50))))
    return 95.0 - 25.0 * sens / 100.0


def group_threshold(s) -> float:
    """묶음 정밀도(0~100) -> 코사인 유사도 임계값."""
    p = max(0, min(100, int(s.get("group_precision", 50))))
    return 0.30 + 0.50 * p / 100.0


def label_names(s):
    return [l["name"] for l in s.get("labels", []) if l.get("name")]


def discard_names(s):
    names = {l["name"] for l in s.get("labels", []) if l.get("name") and l.get("discard")}
    if s.get("include_move"):   # 이동 장면도 모으는 모드에서는 걷기/달리기/이동을 지우지 않는다
        names -= set(MOVE_NAMES)
    return names
