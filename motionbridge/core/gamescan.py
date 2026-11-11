"""게임 폴더 검사: pak / IoStore(utoc) 컨테이너를 찾아서 암호화 여부와 형식을 읽는다.

읽기만 한다(파일 앞부분/뒷부분 몇십~수백 바이트). 수십 GB 짜리 ucas 는 열지 않는다.
암호화 판정 근거
  * IoStore(.utoc): 헤더의 ContainerFlags 비트 1(Encrypted)과 EncryptionKeyGuid
  * pak(.pak)    : 푸터의 bEncryptedIndex 와 EncryptionKeyGuid
"""
from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass, field
from typing import List, Optional, Set

UTOC_MAGIC = b"-==--==--==--==-"
PAK_MAGIC = struct.pack("<I", 0x5A6F12E1)
ZERO_GUID = "0" * 32

# CUE4Parse 의 EGame 이름 (FModel 의 'UE 버전' 목록과 같은 이름). 최신부터.
UE_VERSIONS = [
    "GAME_UE5_6", "GAME_UE5_5", "GAME_UE5_4", "GAME_UE5_3", "GAME_UE5_2", "GAME_UE5_1", "GAME_UE5_0",
    "GAME_UE4_27", "GAME_UE4_26", "GAME_UE4_25",
    # 커스텀 엔진 게임 전용 프리셋 (도우미에서 전체 목록을 못 받았을 때도 고를 수 있게 대표적인 것만)
    "GAME_BlackMythWukong",
]


@dataclass
class Container:
    name: str
    kind: str  # 'pak' | 'iostore'
    size: int  # 데이터 파일 크기 (iostore 는 .ucas 포함)
    encrypted: bool
    key_guid: str
    version: int  # pak 버전 / toc 버전
    note: str = ""


@dataclass
class GameScan:
    root: str
    paks_dir: Optional[str]
    containers: List[Container] = field(default_factory=list)
    exe: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    engine: str = ""            # 'unreal' | 'unity' | 're' | 'godot' | 'unknown'
    evidence: List[str] = field(default_factory=list)   # 엔진을 그렇게 본 근거
    appid: str = ""             # 스팀 AppID (알 수 있을 때)
    project: str = ""           # 언리얼 프로젝트 폴더 이름 (예: b1)
    exe_version: str = ""       # 실행 파일의 파일 버전 (언리얼은 보통 엔진 버전, 예: 5.0.0.0)

    @property
    def found(self) -> bool:
        return bool(self.containers)

    @property
    def encrypted(self) -> bool:
        return any(c.encrypted for c in self.containers)

    @property
    def key_guids(self) -> Set[str]:
        return {c.key_guid for c in self.containers if c.encrypted and c.key_guid != ZERO_GUID}

    @property
    def total_size(self) -> int:
        return sum(c.size for c in self.containers)

    @property
    def is_unreal5(self) -> bool:
        return any((c.kind == "iostore") or (c.kind == "pak" and c.version >= 9) for c in self.containers)

    def summary(self) -> str:
        if not self.found:
            return "pak / utoc 파일을 찾지 못했습니다. 게임 설치 폴더(예: ...\\common\\<게임>)를 고르세요."
        n_pak = sum(c.kind == "pak" for c in self.containers)
        n_io = sum(c.kind == "iostore" for c in self.containers)
        lines = ["컨테이너 %d개 (pak %d, IoStore %d), 총 %.1f GB" % (len(self.containers), n_pak, n_io, self.total_size / 1e9)]
        if self.encrypted:
            enc = [c.name for c in self.containers if c.encrypted]
            lines.append("암호화됨 (%d개): AES 키가 필요합니다." % len(enc))
            if self.key_guids:
                lines.append("키 GUID: " + ", ".join(sorted(self.key_guids)))
        else:
            lines.append("암호화되지 않음: AES 키 없이 바로 추출할 수 있습니다.")
        vers = sorted({(c.kind, c.version) for c in self.containers})
        lines.append("형식: " + ", ".join("%s v%d" % (k, v) for k, v in vers))
        if self.is_unreal5:
            lines.append("언리얼 5 계열로 보입니다. 보통 mappings(.usmap) 파일이 필요합니다.")
        lines += ["※ " + w for w in self.warnings]
        return "\n".join(lines)


# ----------------------------------------------------------------------------- 읽기
def read_utoc(path: str):
    """(encrypted, key_guid_hex, toc_version) 또는 None."""
    with open(path, "rb") as f:
        h = f.read(144)
    if len(h) < 81 or h[:16] != UTOC_MAGIC:
        return None
    flags = h[80]
    return bool(flags & 2), h[64:80].hex(), h[16]


def read_pak_footer(path: str):
    """(encrypted_index, key_guid_hex, pak_version) 또는 None."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(max(0, size - 400))
        tail = f.read()
    i = tail.rfind(PAK_MAGIC)
    if i < 17 or i + 8 > len(tail):
        return None
    version = struct.unpack("<I", tail[i + 4:i + 8])[0]
    return bool(tail[i - 1]), tail[i - 17:i - 1].hex(), version


def find_paks_dir(root: str, max_depth: int = 6) -> Optional[str]:
    """pak/utoc 가 들어 있는 폴더를 찾는다. 'Paks' 이름 폴더를 우선한다."""
    best, best_score = None, -1
    root = os.path.abspath(root)
    base_depth = root.count(os.sep)
    for dp, dn, fn in os.walk(root):
        if dp.count(os.sep) - base_depth >= max_depth:
            dn[:] = []
        n = sum(1 for f in fn if f.lower().endswith((".pak", ".utoc")))
        if n:
            score = n + (1000 if os.path.basename(dp).lower() == "paks" else 0)
            if score > best_score:
                best, best_score = dp, score
    return best


def find_exe(root: str) -> Optional[str]:
    for dp, dn, fn in os.walk(root):
        for f in fn:
            if f.lower().endswith("-shipping.exe"):
                return os.path.join(dp, f)
        if dp[len(root):].count(os.sep) >= 4:
            dn[:] = []
    return None


def scan_game(root: str) -> GameScan:
    if not os.path.isdir(root):
        return GameScan(root, None, warnings=["폴더가 없습니다: " + root])
    pdir = find_paks_dir(root)
    sc = GameScan(root, pdir)
    sc.exe = find_exe(root)
    try:
        sc.engine, sc.evidence = detect_engine(root, pdir)
        sc.appid = steam_appid(root)
        sc.project = project_name(root, pdir)
        sc.exe_version = exe_version(sc.exe) if sc.exe else ""
    except Exception as e:  # noqa: BLE001 - 부가 정보라 실패해도 검사는 계속
        sc.warnings.append("부가 정보(엔진/버전) 읽기 실패: %s" % e)
    if pdir is None:
        return sc
    seen_io = set()
    for fn in sorted(os.listdir(pdir)):
        low = fn.lower()
        p = os.path.join(pdir, fn)
        stem = fn[:-5] if low.endswith(".utoc") else fn[:-4]
        try:
            if low.endswith(".utoc"):
                r = read_utoc(p)
                if r is None:
                    sc.warnings.append("%s: utoc 헤더를 읽지 못함" % fn)
                    continue
                ucas = os.path.join(pdir, stem + ".ucas")
                size = os.path.getsize(ucas) if os.path.isfile(ucas) else 0
                if not size:
                    sc.warnings.append("%s: 짝이 되는 .ucas 가 없음" % fn)
                seen_io.add(stem.lower())
                sc.containers.append(Container(fn, "iostore", size, r[0], r[1], r[2]))
            elif low.endswith(".pak"):
                r = read_pak_footer(p)
                if r is None:
                    sc.warnings.append("%s: pak 푸터를 읽지 못함" % fn)
                    continue
                # IoStore 와 짝을 이루는 아주 작은 pak 은 목록에서 제외(데이터 없음)
                if stem.lower() in seen_io or os.path.isfile(os.path.join(pdir, stem + ".utoc")):
                    if os.path.getsize(p) < 4096 and not r[0]:
                        continue
                sc.containers.append(Container(fn, "pak", os.path.getsize(p), r[0], r[1], r[2]))
        except OSError as e:
            sc.warnings.append("%s: %s" % (fn, e))
    return sc


# ----------------------------------------------------------------------------- AES 키 입력
_HEX = re.compile(r"^(0x)?([0-9a-fA-F]{64})$", re.I)


class KeyError_(ValueError):
    pass


def normalize_aes_key(text: str) -> str:
    """사용자가 붙여 넣은 AES 키를 '0x' + 64자리 16진수 대문자로. 형식이 틀리면 ValueError.
    지원: 64자리 16진수(0x 유무), 44자 Base64(32바이트)."""
    t = (text or "").strip().strip('"').strip("'")
    if not t:
        raise KeyError_("AES 키가 비어 있습니다.")
    m = _HEX.match(t)
    if m:
        return "0x" + m.group(2).upper()
    import base64

    if re.fullmatch(r"[A-Za-z0-9+/]{43}=?", t):
        raw = base64.b64decode(t + ("=" if len(t) == 43 else ""))
        if len(raw) == 32:
            return "0x" + raw.hex().upper()
    raise KeyError_("AES 키 형식이 맞지 않습니다. 0x 로 시작하는 64자리 16진수(AES-256) 또는 32바이트 Base64 를 넣으세요.")


# ----------------------------------------------------------------------------- 엔진 · 버전 · 게임 식별
def _shallow_files(root: str, depth: int = 3, cap: int = 6000) -> List[str]:
    out: List[str] = []
    root = os.path.abspath(root)
    base = root.count(os.sep)
    for dp, dn, fn in os.walk(root):
        if dp.count(os.sep) - base >= depth:
            dn[:] = []
        for f in fn:
            out.append(os.path.join(dp, f))
            if len(out) >= cap:
                return out
    return out


def detect_engine(root: str, paks_dir: Optional[str] = None):
    """(엔진, 근거 목록). 파일 이름과 머리글자만 본다 (게임 파일을 바꾸지 않음)."""
    files = _shallow_files(root)
    low = [f.lower() for f in files]
    names = {os.path.basename(f) for f in low}
    ev: List[str] = []
    if paks_dir or any(f.endswith(".utoc") for f in low):
        if any(f.endswith((".utoc", ".ucas")) for f in low) or paks_dir:
            # .pak 은 다른 엔진도 쓰는 확장자라 머리글자를 본다: RE 엔진 pak 은 'KPKA' 로 시작
            for f in files:
                if f.lower().endswith(".pak"):
                    try:
                        with open(f, "rb") as fh:
                            if fh.read(4) == b"KPKA":
                                return "re", ["%s 가 RE 엔진 형식(KPKA)" % os.path.basename(f)]
                    except OSError:
                        pass
                    break
            if any(f.endswith("-shipping.exe") for f in low):
                ev.append("…-Shipping.exe 실행 파일")
            if any(f.endswith(".utoc") for f in low):
                ev.append(".utoc/.ucas (언리얼 IoStore)")
            if paks_dir:
                ev.append("Paks 폴더: " + os.path.relpath(paks_dir, root))
            return "unreal", ev
    if "unityplayer.dll" in names or any(f.endswith("globalgamemanagers") or f.endswith(".assets") for f in low) or \
            any(os.sep + "il2cpp_data" in f or f.endswith("gameassembly.dll") for f in low):
        return "unity", ["UnityPlayer.dll / *_Data 폴더 (유니티)"]
    if any(re.search(r"re_chunk_\d+\.pak", f) for f in low):
        return "re", ["re_chunk_*.pak (RE 엔진)"]
    if any(f.endswith(".pck") for f in low):
        return "godot", [".pck (고도 엔진)"]
    if any(f.endswith((".forge", ".arc", ".bdt", ".bhd", ".rpf", ".pac", ".cpk")) for f in low):
        return "unknown", ["독자 형식 묶음 파일(.forge/.arc/.bdt/.rpf/.cpk 등)"]
    return "unknown", []


def exe_version(path: str) -> str:
    """실행 파일의 파일 버전 'a.b.c.d' (VS_FIXEDFILEINFO). 못 읽으면 빈 글자. 언리얼 게임은 보통 엔진 버전이 들어 있다."""
    sig = struct.pack("<I", 0xFEEF04BD)
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            # 버전 정보는 자원 영역(파일 뒤쪽)에 있다. 큰 파일은 뒤 64MB 만 본다.
            start = max(0, size - 64 * 1024 * 1024)
            f.seek(start)
            data = f.read()
        i = data.rfind(sig)
        if i < 0 or i + 16 > len(data):
            return ""
        ms, ls = struct.unpack("<II", data[i + 8:i + 16])
        return "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except OSError:
        return ""


def steam_appid(root: str) -> str:
    root = os.path.normpath(root)
    for f in _shallow_files(root, 4, 4000):
        if os.path.basename(f).lower() == "steam_appid.txt":
            try:
                with open(f, "r", encoding="utf-8", errors="replace") as fh:
                    m = re.search(r"\d{3,}", fh.read(64))
                if m:
                    return m.group(0)
            except OSError:
                pass
    common = os.path.dirname(root)
    if os.path.basename(common).lower() == "common":
        apps = os.path.dirname(common)
        try:
            for fn in os.listdir(apps):
                if fn.startswith("appmanifest_") and fn.endswith(".acf"):
                    with open(os.path.join(apps, fn), "r", encoding="utf-8", errors="replace") as fh:
                        txt = fh.read()
                    inst = re.search(r'"installdir"\s+"([^"]*)"', txt)
                    if inst and inst.group(1).lower() == os.path.basename(root).lower():
                        return fn[len("appmanifest_"):-4]
        except OSError:
            pass
    return ""


def project_name(root: str, paks_dir: Optional[str]) -> str:
    """언리얼 프로젝트 폴더 이름: …/<프로젝트>/Content/Paks 의 <프로젝트>."""
    if not paks_dir:
        return ""
    p = os.path.normpath(paks_dir)
    parts = p.split(os.sep)
    low = [x.lower() for x in parts]
    if "content" in low:
        i = len(low) - 1 - low[::-1].index("content")
        if i >= 1:
            return parts[i - 1]
    return ""


_UE_VER = re.compile(r"^GAME_UE(\d)_(\d+)$")
# 프로젝트 폴더 이름이 게임 이름과 전혀 다른 경우 (폴더/실행파일 이름 소문자 → 프리셋 이름의 핵심 낱말)
PROJECT_ALIASES = {"b1": "blackmythwukong"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def merge_versions(dynamic: List[str]) -> List[str]:
    """화면에 보일 UE 버전 목록: 일반 버전(최신부터) + 게임 전용 프리셋(이름순). dynamic 은 도우미가 알려 준 전체 목록."""
    names = list(dict.fromkeys([n for n in (dynamic or []) if n.startswith("GAME_")] + UE_VERSIONS))
    generic = [n for n in names if _UE_VER.match(n)]
    generic.sort(key=lambda n: tuple(int(x) for x in _UE_VER.match(n).groups()), reverse=True)
    special = sorted((n for n in names if not _UE_VER.match(n) and "LATEST" not in n and not n.endswith("_Legacy")), key=str.lower)
    return generic + special


def suggest_ue(sc: "GameScan", games: List[str], game_name: str = ""):
    """(프리셋 이름, 이유) 또는 (None, ""). ① 게임 전용 프리셋이 있으면 그것 ② 실행 파일 버전으로 일반 버전."""
    games = games or UE_VERSIONS
    keys = []
    for raw in (sc.project, os.path.basename(os.path.normpath(sc.root)), game_name,
                os.path.basename(sc.exe or "").split("-")[0]):
        k = _norm(raw)
        if k:
            keys.append(PROJECT_ALIASES.get(k, k))
    special = {_norm(n[5:]): n for n in games if n.startswith("GAME_") and not _UE_VER.match(n)}
    for k in keys:
        if len(k) >= 5 and k in special:
            return special[k], "게임 전용 프리셋이 있습니다"
    for k in keys:        # 이름 일부만 겹치는 경우 (예: 폴더 'BlackMythWukong Demo')
        if len(k) >= 8:
            for sk, n in special.items():
                if len(sk) >= 8 and (sk in k or k in sk):
                    return n, "게임 전용 프리셋이 있습니다"
    m = re.match(r"^(\d)\.(\d+)\.", sc.exe_version or "")
    if m and m.group(1) in ("4", "5"):
        n = "GAME_UE%s_%s" % (m.group(1), m.group(2))
        if n in games or n in UE_VERSIONS:
            return n, "실행 파일 버전 %s" % sc.exe_version
    return None, ""


def verdict(sc: "GameScan", has_key: bool, has_usmap: bool, preset: Optional[dict] = None):
    """(상태, 제목, 설명). 상태: 'green' 바로 추출 가능 / 'yellow' 조건부(무엇이 필요한지) / 'red' 직접 추출 불가."""
    preset = preset or {}
    if preset.get("status") == "blocked":
        return "red", "추출 불가로 기록된 게임", str(preset.get("reason") or "예전에 추출이 안 되는 게임으로 기록했습니다.")
    if sc.engine == "unity":
        return "red", "유니티 게임 — 지금은 직접 추출 불가", "이 도구의 추출기는 언리얼 전용입니다. AnimRef / AI 모캡으로 영상에서 모션을 얻으세요. (유니티 추출 모듈은 이후 확장 대상)"
    if sc.engine in ("re", "godot") or (sc.engine == "unknown" and not sc.found):
        why = {"re": "RE 엔진", "godot": "고도 엔진"}.get(sc.engine, "언리얼이 아닌(또는 알 수 없는) 엔진")
        return "red", "%s — 직접 추출 불가" % why, "언리얼 컨테이너(.pak/.utoc)가 아니어서 열 수 없습니다. AnimRef / AI 모캡으로 영상에서 모션을 얻으세요."
    if not sc.found:
        return "red", "컨테이너를 찾지 못함", "게임 설치 폴더가 맞는지 확인하세요."
    need = []
    if sc.encrypted and not has_key:
        need.append("AES 키")
    if sc.is_unreal5 and any(c.kind == "iostore" for c in sc.containers) and not has_usmap:
        need.append("매핑 파일(.usmap)")
    if need:
        return "yellow", "조건부 가능 — %s 필요" % " · ".join(need), "아래 칸에 넣으면 됩니다. 한 번 성공하면 다음부터는 게임 폴더만 고르면 자동으로 채워집니다."
    return "green", "추출 가능", "필요한 것이 모두 준비됐습니다. 검색/추출을 진행하세요."
