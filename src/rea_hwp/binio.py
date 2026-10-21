"""레코드 payload를 읽고 쓰는 리틀 엔디언 바이트 도구."""

from __future__ import annotations

import struct

from .errors import CorruptFileError

_FORMATS = {
    "u8": "<B",
    "i8": "<b",
    "u16": "<H",
    "i16": "<h",
    "u32": "<I",
    "i32": "<i",
}
_SIZES = {name: struct.calcsize(fmt) for name, fmt in _FORMATS.items()}


class TruncatedPayload(CorruptFileError):
    """payload가 해석하려는 필드보다 짧다."""


class Reader:
    def __init__(self, payload: bytes, offset: int = 0) -> None:
        self.payload = payload
        self.offset = offset

    @property
    def remaining(self) -> int:
        return len(self.payload) - self.offset

    def read(self, kind: str) -> int:
        size = _SIZES[kind]
        if self.remaining < size:
            raise TruncatedPayload(f"{kind} at {self.offset} needs {size} bytes, {self.remaining} left")
        (value,) = struct.unpack_from(_FORMATS[kind], self.payload, self.offset)
        self.offset += size
        return value

    def bytes(self, length: int) -> bytes:
        if length < 0 or self.remaining < length:
            raise TruncatedPayload(f"{length} bytes at {self.offset}, {self.remaining} left")
        chunk = self.payload[self.offset : self.offset + length]
        self.offset += length
        return chunk

    def wstr(self) -> str:
        """u16 글자 수 + UTF-16LE 문자열(HWP의 WCHAR 배열 표기)."""
        length = self.read("u16")
        return self.bytes(length * 2).decode("utf-16-le", errors="surrogatepass")

    def rest(self) -> bytes:
        return self.bytes(self.remaining)


class Writer:
    def __init__(self) -> None:
        self._parts: list[bytes] = []

    def write(self, kind: str, value: int) -> None:
        self._parts.append(struct.pack(_FORMATS[kind], value))

    def bytes(self, chunk: bytes) -> None:
        self._parts.append(chunk)

    def wstr(self, text: str) -> None:
        encoded = text.encode("utf-16-le", errors="surrogatepass")
        self.write("u16", len(encoded) // 2)
        self._parts.append(encoded)

    def getvalue(self) -> bytes:
        return b"".join(self._parts)
