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
    # Black Myth: Wukong uses a game-specific UE5.0 version profile in CUE4Parse.
    "GAME_BlackMythWukong",
    "GAME_UE5_6", "GAME_UE5_5", "GAME_UE5_4", "GAME_UE5_3", "GAME_UE5_2", "GAME_UE5_1", "GAME_UE5_0",
    "GAME_UE4_27", "GAME_UE4_26", "GAME_UE4_25",
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
