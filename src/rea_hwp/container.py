"""OLE 복합 파일에서 HWP 5.0 스트림을 꺼내고 압축·암호를 푼다."""

from __future__ import annotations

import io
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

import olefile

from . import crypto

SIGNATURE = b"HWP Document File"


class HwpFormatError(ValueError):
    """HWP 5.0 문서로 열 수 없다."""


@dataclass(frozen=True)
class FileHeader:
    raw: bytes

    @property
    def version(self) -> tuple[int, int, int, int]:
        (word,) = struct.unpack_from("<I", self.raw, 32)
        return (word >> 24) & 0xFF, (word >> 16) & 0xFF, (word >> 8) & 0xFF, word & 0xFF

    @property
    def flags(self) -> int:
        return struct.unpack_from("<I", self.raw, 36)[0]

    @property
    def compressed(self) -> bool:
        return bool(self.flags & 0x01)

    @property
    def password_protected(self) -> bool:
        return bool(self.flags & 0x02)

    @property
    def distributed(self) -> bool:
        return bool(self.flags & 0x04)

    @property
    def drm(self) -> bool:
        return bool(self.flags & 0x10)

    @property
    def encrypt_version(self) -> int:
        return struct.unpack_from("<I", self.raw, 44)[0] if len(self.raw) >= 48 else 0


def inflate(raw: bytes) -> bytes:
    """HWP는 헤더 없는 deflate를 쓴다. zlib 헤더가 붙은 스트림도 받아 준다."""
    try:
        return zlib.decompress(raw, -15)
    except zlib.error as raw_error:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            raise raw_error from None


class HwpContainer:
    """OLE 스트림 원본 바이트와, 압축·암호를 푼 레코드 스트림을 함께 다룬다."""

    def __init__(self, raw_streams: dict[str, bytes], password: str | None = None) -> None:
        self.raw_streams = raw_streams
        if "FileHeader" not in raw_streams:
            raise HwpFormatError("FileHeader stream is missing; not an HWP 5.0 file")
        self.header = FileHeader(raw_streams["FileHeader"])
        if not self.header.raw.startswith(SIGNATURE):
            raise HwpFormatError("FileHeader signature is not 'HWP Document File'")
        if self.header.drm:
            raise HwpFormatError("DRM-protected HWP documents cannot be decrypted")
        if self.header.password_protected:
            if self.header.encrypt_version != crypto.SUPPORTED_ENCRYPT_VERSION:
                raise HwpFormatError(f"EncryptVersion {self.header.encrypt_version} is not supported (only 4)")
            if password is None:
                raise crypto.HwpDecryptError("document is password protected; pass password=")
        self.password = password

    @classmethod
    def open(cls, source: str | Path | bytes, password: str | None = None) -> HwpContainer:
        data = source if isinstance(source, bytes) else Path(source).read_bytes()
        if not olefile.isOleFile(io.BytesIO(data)):
            raise HwpFormatError("not an OLE compound file (HWP 5.0 uses OLE; HWPX/HWP3 are not supported)")
        with olefile.OleFileIO(io.BytesIO(data)) as ole:
            streams = {"/".join(path): ole.openstream(path).read() for path in ole.listdir(streams=True)}
        return cls(streams, password)

    def section_names(self, view_text: bool | None = None) -> list[str]:
        storage = "ViewText" if (self.header.distributed if view_text is None else view_text) else "BodyText"
        names = [name for name in self.raw_streams if name.startswith(storage + "/Section")]
        return sorted(names, key=lambda name: int(name.rsplit("Section", 1)[1]))

    def record_stream(self, name: str) -> bytes:
        """레코드 구조 스트림(DocInfo, BodyText, ViewText)을 레코드 바이트 상태로 돌려준다."""
        raw = self.raw_streams[name]
        if name.startswith("ViewText/"):
            raw = crypto.decrypt_distribute_stream(raw)
        elif self.header.password_protected:
            raw = crypto.decrypt_password_stream(raw, self.password or "")
        return inflate(raw) if self.header.compressed else raw

    def bin_data(self, name: str, compressed: bool | None = None) -> bytes:
        """BinData 항목. compressed가 None이면 문서 기본 압축 여부를 따른다."""
        raw = self.raw_streams[name]
        if self.header.password_protected:
            raw = crypto.decrypt_password_stream(raw, self.password or "")
        if self.header.compressed if compressed is None else compressed:
            return inflate(raw)
        return raw
