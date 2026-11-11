"""Engine detection and pluggable game-asset extractor registry."""
from __future__ import annotations
import importlib.metadata
import os

class AdapterError(RuntimeError):
    pass

def detect_engine(root: str) -> str:
    if not os.path.isdir(root):
        raise AdapterError("게임 폴더가 없습니다: " + root)
    from . import unity_extractor
    if unity_extractor.detect_unity(root):
        return "unity"
    try:
        from .gamescan import scan_game
        if scan_game(root).found:
            return "unreal"
    except Exception:
        pass
    return "unknown"

def adapters() -> dict:
    found = {}
    try:
        eps = importlib.metadata.entry_points()
        selected = eps.select(group="motionbridge.extractors") if hasattr(eps, "select") else eps.get("motionbridge.extractors", [])
        for ep in selected:
            try:
                factory = ep.load()
                found[ep.name] = factory() if callable(factory) else factory
            except Exception:
                continue
    except Exception:
        pass
    return found

def adapter_for(engine: str, root: str):
    engine = (engine or "auto").lower()
    if engine == "auto":
        engine = detect_engine(root)
    if engine == "unity":
        from . import unity_extractor
        return engine, unity_extractor
    plugin = adapters().get(engine)
    if plugin is not None:
        return engine, plugin
    if engine == "unreal":
        return engine, None
    if engine == "unknown":
        raise AdapterError("엔진을 자동 판별하지 못했습니다. 지원 어댑터를 설치하거나 motionbridge adapters 로 확인하세요.")
    raise AdapterError(f"'{engine}' 추출 어댑터를 찾지 못했습니다.")

def list_adapters() -> list[dict]:
    result = [
        {"name": "unity", "status": "built-in", "description": "UnityPy 기반 Unity 에셋 검사/추출"},
        {"name": "unreal", "status": "built-in-existing", "description": "기존 CUE4Parse mbextract 경로"},
    ]
    for name, adapter in adapters().items():
        result.append({"name": name, "status": "plugin",
                       "description": getattr(adapter, "description", "외부 엔진 어댑터")})
    return result
