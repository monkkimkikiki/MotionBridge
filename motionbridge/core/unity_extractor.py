"""Unity game asset adapter powered by UnityPy.

Reads local Unity asset files and AssetBundles. Does not bypass DRM,
anti-cheat, online checks, or proprietary key acquisition mechanisms.
"""
from __future__ import annotations
import json, os, re
from pathlib import Path
from typing import Callable, Iterable, Optional

UNITY_MARKERS = ("UnityPlayer.dll", "GameAssembly.dll", "globalgamemanagers",
                 "globalgamemanagers.assets", "boot.config")

class UnityExtractError(RuntimeError):
    pass

def detect_unity(root: str) -> bool:
    if not os.path.isdir(root):
        return False
    try:
        for current, dirs, files in os.walk(root):
            depth = os.path.relpath(current, root).count(os.sep)
            if depth >= 4:
                dirs[:] = []
            if any(marker in set(files) for marker in UNITY_MARKERS):
                return True
            if any(name.endswith("_Data") for name in dirs):
                return True
    except OSError:
        return False
    return False

def _unitypy():
    try:
        import UnityPy
        return UnityPy
    except ImportError as exc:
        raise UnityExtractError("Unity 추출 모듈이 없습니다. pip install UnityPy 후 다시 실행하세요.") from exc

def _objects(env) -> Iterable:
    objects = env.objects
    return objects.values() if isinstance(objects, dict) else objects

def _safe_name(value: str, fallback: str) -> str:
    value = re.sub(r'[<>:"/\\\\|?*\\x00-\\x1f]', "_", str(value or "")).strip(" .")
    return value[:120] or fallback

def _name(obj) -> str:
    try:
        return str(obj.peek_name() or "")
    except Exception:
        return ""

def _load(root: str, bundle_key: Optional[str] = None):
    UnityPy = _unitypy()
    if bundle_key:
        setter = getattr(UnityPy, "set_assetbundle_decrypt_key", None)
        if not callable(setter):
            raise UnityExtractError("설치된 UnityPy가 사용자 제공 AssetBundle 키 입력을 지원하지 않습니다.")
        setter(bundle_key)
    return UnityPy.load(root)

def scan(root: str, on_event: Optional[Callable[[dict], None]] = None,
         bundle_key: Optional[str] = None) -> list[dict]:
    if not os.path.isdir(root):
        raise UnityExtractError("게임 폴더가 없습니다: " + root)
    env = _load(root)
    result = []
    for obj in _objects(env):
        try:
            item = {"path_id": int(obj.path_id), "type": obj.type.name,
                    "name": _name(obj),
                    "source": str(getattr(getattr(obj, "assets_file", None), "name", ""))}
            result.append(item)
            if on_event:
                on_event({"type": "asset", **item})
        except Exception as exc:
            if on_event:
                on_event({"type": "skip", "reason": str(exc)})
    if on_event:
        on_event({"type": "done", "count": len(result)})
    return result

def export(root: str, out_dir: str, path_ids: Optional[set[int]] = None,
           on_event: Optional[Callable[[dict], None]] = None,
           bundle_key: Optional[str] = None) -> list[str]:
    """Export common Unity objects. AnimationClip remains raw + metadata."""
    if not os.path.isdir(root):
        raise UnityExtractError("게임 폴더가 없습니다: " + root)
    os.makedirs(out_dir, exist_ok=True)
    env = _load(root, bundle_key)
    made: list[str] = []
    for obj in _objects(env):
        try:
            pid = int(obj.path_id)
            if path_ids is not None and pid not in path_ids:
                continue
            kind = obj.type.name
            name = _safe_name(_name(obj), f"{kind}_{pid}")
            target = os.path.join(out_dir, f"{name}_{pid}")
            files: list[str] = []
            if kind == "Mesh":
                data = obj.parse_as_object()
                path = target + ".obj"
                with open(path, "w", encoding="utf-8", newline="") as stream:
                    stream.write(data.export())
                files.append(path)
            elif kind in ("MeshRenderer", "SkinnedMeshRenderer"):
                data = obj.read()
                os.makedirs(target, exist_ok=True)
                data.export(target)
                files.extend(str(p) for p in Path(target).rglob("*") if p.is_file())
            elif kind == "Texture2D":
                data = obj.read()
                path = target + ".png"
                data.image.save(path)
                files.append(path)
            elif kind == "AudioClip":
                data = obj.read()
                for sample_name, payload in data.samples.items():
                    path = os.path.join(out_dir, _safe_name(sample_name, f"{name}.wav"))
                    with open(path, "wb") as stream:
                        stream.write(payload)
                    files.append(path)
            elif kind == "Font":
                data = obj.read()
                payload = data.m_FontData
                if payload:
                    ext = ".otf" if payload[:4] == b"OTTO" else ".ttf"
                    path = target + ext
                    with open(path, "wb") as stream:
                        stream.write(payload)
                    files.append(path)
            elif kind == "TextAsset":
                data = obj.read()
                payload = getattr(data, "m_Script", b"")
                if isinstance(payload, str):
                    payload = payload.encode("utf-8")
                path = target + ".txt"
                with open(path, "wb") as stream:
                    stream.write(payload)
                files.append(path)
            elif kind == "AnimationClip":
                raw_getter = getattr(obj, "get_raw_data", None)
                if callable(raw_getter):
                    path = target + ".animationclip.bytes"
                    with open(path, "wb") as stream:
                        stream.write(raw_getter())
                    files.append(path)
                metadata = {"name": name, "path_id": pid, "type": kind,
                            "note": "Unity AnimationClip typetree/raw payload; FBX/PSA conversion is not provided by this adapter."}
                try:
                    metadata["typetree"] = obj.parse_as_dict()
                except Exception as parse_error:
                    metadata["typetree_error"] = str(parse_error)
                path = target + ".json"
                with open(path, "w", encoding="utf-8") as stream:
                    json.dump(metadata, stream, ensure_ascii=False, indent=2,
                              default=lambda value: value.hex() if isinstance(value, (bytes, bytearray)) else repr(value))
                files.append(path)
            else:
                continue
            made.extend(files)
            if on_event:
                on_event({"type": "exported", "path_id": pid, "name": name,
                          "kind": kind, "files": files})
        except Exception as exc:
            if on_event:
                on_event({"type": "skip", "path_id": getattr(obj, "path_id", None),
                          "reason": f"{type(exc).__name__}: {exc}"})
    if on_event:
        on_event({"type": "done", "count": len(made)})
    return made
