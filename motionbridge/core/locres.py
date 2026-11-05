"""언리얼 .locres(게임 안 글자 사전) 읽기. 'The Nameless Captive' 같은 화면 속 이름 -> 내부 키(이름표)를 알아내는 데 쓴다.

형식(엔진 FTextLocalizationResource): 매직 GUID, 버전(1 Compact / 2 Optimized_CRC32 / 3 Optimized_CityHash64_UTF16),
문자열 배열 위치, [항목 수], 네임스페이스들 -> 키들 -> (소스 해시, 문자열 번호), 문자열 배열(+참조 수).
"""
from __future__ import annotations

import struct
from typing import List, Tuple

MAGIC = struct.pack("<IIII", 0x7574140E, 0xFC034A67, 0x9D90154A, 0x1B7F37C3)


class LocresError(Exception):
    pass


class _R:
    def __init__(self, b: bytes) -> None:
        self.b, self.p = b, 0

    def take(self, n: int) -> bytes:
        if n < 0 or self.p + n > len(self.b):
            raise LocresError("파일이 중간에 끝났습니다")
        v = self.b[self.p:self.p + n]
        self.p += n
        return v

    def u8(self) -> int:
        return self.take(1)[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def i64(self) -> int:
        return struct.unpack("<q", self.take(8))[0]

    def fstring(self) -> str:
        n = self.i32()
        if n == 0:
            return ""
        if n < 0:  # UTF-16, 길이는 끝의 널 문자 포함
            raw = self.take(-n * 2)
            return raw[:-2].decode("utf-16-le", "replace")
        raw = self.take(n)
        return raw[:-1].decode("utf-8", "replace")


def parse_locres(data: bytes) -> List[Tuple[str, str, str]]:
    """[(네임스페이스, 키, 글자)] 를 돌려준다."""
    r = _R(data)
    if r.take(16) != MAGIC:
        raise LocresError("지원하지 않는 .locres 형식입니다 (아주 오래된 형식)")
    version = r.u8()
    if version < 1 or version > 3:
        raise LocresError("알 수 없는 .locres 버전: %d" % version)
    strings: List[str] = []
    arr_off = r.i64()
    resume = r.p
    if arr_off >= 0:
        r.p = arr_off
        for _ in range(r.i32()):
            strings.append(r.fstring())
            if version >= 2:
                r.i32()  # 참조 수
        r.p = resume
    if version >= 2:
        r.i32()  # 전체 항목 수
    out: List[Tuple[str, str, str]] = []
    for _ in range(r.u32()):
        if version >= 2:
            r.u32()  # 네임스페이스 해시
        ns = r.fstring()
        for _ in range(r.u32()):
            if version >= 2:
                r.u32()  # 키 해시
            key = r.fstring()
            r.u32()  # 소스 문자열 해시
            idx = r.i32()
            if 0 <= idx < len(strings):
                out.append((ns, key, strings[idx]))
    return out


def read_locres(path: str) -> List[Tuple[str, str, str]]:
    with open(path, "rb") as f:
        return parse_locres(f.read())
