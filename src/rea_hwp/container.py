"""OLE 복합 파일에서 HWP 5.0 스트림을 꺼내고 압축·암호를 푼다. 푼 크기는 Limits 상한 안에서만 받는다."""

from __future__ import annotations

import io
import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

import olefile

from . import crypto, tags
from .errors import (
    CorruptFileError,
    DrmProtectedError,
    LimitExceededError,
    PasswordRequiredError,
    UnsupportedFormatError,
    WrongPasswordError,
)
from .errors import HwpFormatError as HwpFormatError  # 예전 경로 rea_hwp.container.HwpFormatError

SIGNATURE = b"HWP Document File"
_MB = 1 << 20
#: 한 번에 풀어 받는 크기. 상한을 넘는 입력은 상한 + 이 크기에서 멈춘다(다 푼 결과는 bytes로 한 번 더 복사한다).
_INFLATE_CHUNK = _MB
_SECTION_NAME = re.compile(r"(BodyText|ViewText)/Section(\d+)")


@dataclass(frozen=True)
class Limits:
    """믿을 수 없는 입력에 거는 상한. 넘으면 LimitExceededError를 낸다."""

    #: DocInfo·본문 스트림을 풀어 둔 크기의 합(바이트). kordoc MAX_TOTAL_DECOMPRESS와 같다. 말뭉치 최대 33.6MB.
    max_stream_bytes: int = 100 * _MB
    #: 한 문서의 레코드 수. 말뭉치 최대 639,058개(sample_hwp__big_file.hwp)의 1.56배.
    max_records: int = 1_000_000
    #: BinData 항목 하나를 풀어 둔 크기. 검증 안 됨: A4 300dpi 24비트 BMP(약 26MB)의 두 배 남짓, 말뭉치 최대 29.2MB.
    max_bin_data_bytes: int = 64 * _MB
    #: 한 문서의 BinData 항목을 풀어 둔 크기의 합. 검증 안 됨: 위 상한의 네 배, 말뭉치 최대 181MB.
    max_total_bin_data_bytes: int = 256 * _MB


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


def inflate(raw: bytes, limit: int | None = None) -> bytes:
    """헤더 없는 deflate(HWP 기본)와 zlib 헤더가 붙은 스트림을 푼다. limit 바이트를 넘으면 푸는 도중에 멈춘다."""
    for window_bits in (-zlib.MAX_WBITS, zlib.MAX_WBITS):
        decompressor = zlib.decompressobj(window_bits)
        plain = bytearray()
        pending = raw
        try:
            while True:
                piece = decompressor.decompress(pending, _INFLATE_CHUNK)
                plain += piece
                if limit is not None and len(plain) > limit:
                    raise LimitExceededError(f"decompressed data exceeds {limit} bytes")
                pending = decompressor.unconsumed_tail
                if decompressor.eof or (not pending and len(piece) < _INFLATE_CHUNK):
                    break
        except zlib.error:
            continue
        if decompressor.eof:
            return bytes(plain)
    raise CorruptFileError("compressed stream is damaged or truncated; the file cannot be read")


class HwpContainer:
    """OLE 스트림 원본 바이트와, 압축·암호를 푼 레코드 스트림을 함께 다룬다."""

    def __init__(
        self, raw_streams: dict[str, bytes], password: str | None = None, limits: Limits | None = None
    ) -> None:
        self.raw_streams = raw_streams
        self.limits = limits or Limits()
        #: 지금까지 푼 스트림 이름별 크기. 같은 스트림을 다시 풀어도 상한 합계에 두 번 세지 않는다.
        self._stream_sizes: dict[str, int] = {}
        self._bin_data_sizes: dict[str, int] = {}
        if "FileHeader" not in raw_streams:
            raise UnsupportedFormatError("FileHeader stream is missing; this is not an HWP 5.0 file")
        self.header = FileHeader(raw_streams["FileHeader"])
        if not self.header.raw.startswith(SIGNATURE):
            raise UnsupportedFormatError("FileHeader signature is not 'HWP Document File'; this is not an HWP 5.0 file")
        if len(self.header.raw) < 40:
            raise CorruptFileError(f"FileHeader is {len(self.header.raw)} bytes, expected 256; the file is damaged")
        if self.header.version[0] != 5:
            raise UnsupportedFormatError(f"HWP version {'.'.join(map(str, self.header.version))} is not supported")
        if self.header.drm:
            raise DrmProtectedError("document is DRM-protected; save it without DRM in Hangul and try again")
        if self.header.password_protected:
            version = self.header.encrypt_version
            if version != crypto.SUPPORTED_ENCRYPT_VERSION and version not in crypto.LEGACY_ENCRYPT_VERSIONS:
                raise UnsupportedFormatError(f"EncryptVersion {version} is not supported (only 0, 1, 2, 4)")
            if password is None:
                raise PasswordRequiredError("document is password protected; pass password=")
        self.password = password
        if "DocInfo" not in raw_streams:
            raise CorruptFileError("DocInfo stream is missing; the file is damaged")
        if not self.section_names():
            raise CorruptFileError(f"{self.body_storage} has no Section streams; the file is damaged")

    @classmethod
    def open(
        cls, source: str | Path | bytes, password: str | None = None, limits: Limits | None = None
    ) -> HwpContainer:
        data = source if isinstance(source, bytes) else Path(source).read_bytes()
        if not olefile.isOleFile(io.BytesIO(data)):
            raise UnsupportedFormatError(
                "not an OLE compound file (HWP 5.0 uses OLE; HWPX and HWP 3.0 are not supported)"
            )
        # 깨진 그릇에서 olefile은 OleFileError 말고도 IndexError·OverflowError·struct.error 등을 낸다.
        try:
            with olefile.OleFileIO(io.BytesIO(data)) as ole:
                paths = ole.listdir(streams=True)
                # 섹터가 자기 자신을 가리키면 olefile은 선언한 크기만큼 같은 섹터를 되풀이해 읽는다. 스트림은 파일 안에
                # 들어 있으므로 크기 합이 파일보다 크면 읽기 전에 거절한다.
                declared = sum(ole.get_size(path) for path in paths)
                if declared > len(data):
                    raise CorruptFileError(
                        f"OLE streams declare {declared} bytes in a {len(data)}-byte file; the file is damaged"
                    )
                streams = {"/".join(path): ole.openstream(path).read() for path in paths}
        except (CorruptFileError, MemoryError):
            raise
        except Exception as error:
            raise CorruptFileError(
                f"OLE container is damaged ({type(error).__name__}); the file cannot be read"
            ) from error
        return cls(streams, password, limits)

    @property
    def body_storage(self) -> str:
        """본문을 읽는 저장소. 배포용 비트가 켜진 문서만 ViewText다(배포용 비트 없이 ViewText를 가진 문서도 있다)."""
        return "ViewText" if self.header.distributed else "BodyText"

    def section_names(self, view_text: bool | None = None) -> list[str]:
        storage = self.body_storage if view_text is None else ("ViewText" if view_text else "BodyText")
        numbered = []
        for name in self.raw_streams:
            matched = _SECTION_NAME.fullmatch(name)
            if matched and matched.group(1) == storage:
                numbered.append((int(matched.group(2)), name))
        return [name for _, name in sorted(numbered)]

    def record_stream(self, name: str) -> bytes:
        """레코드 구조 스트림(DocInfo, BodyText, ViewText)을 레코드 바이트 상태로 돌려준다."""
        if name not in self.raw_streams:
            raise CorruptFileError(f"{name} stream is missing; the file is damaged")
        raw = self.raw_streams[name]
        budget = self.limits.max_stream_bytes - sum(size for other, size in self._stream_sizes.items() if other != name)
        if name.startswith("ViewText/"):
            raw = crypto.decrypt_distribute_stream(raw)
        elif self.header.password_protected:
            raw = crypto.decrypt_stream(raw, self.password or "", self.header.encrypt_version)
        too_large = LimitExceededError(
            f"decompressed record streams exceed {self.limits.max_stream_bytes} bytes; raise Limits.max_stream_bytes"
        )
        if self.header.compressed:
            try:
                plain = inflate(raw, budget)
            except CorruptFileError as error:
                if self.header.password_protected and name == "DocInfo":
                    raise WrongPasswordError("password is wrong; pass the document's open password") from error
                raise
            except LimitExceededError as error:
                raise too_large from error
        else:
            plain = raw
        if len(plain) > budget:
            raise too_large
        if (
            self.header.password_protected
            and name == "DocInfo"
            and not _starts_with_tag(plain, tags.DOCUMENT_PROPERTIES)
        ):
            raise WrongPasswordError("password is wrong; pass the document's open password")
        self._stream_sizes[name] = len(plain)
        return plain

    def bin_data(self, name: str, compressed: bool | None = None) -> bytes:
        """BinData 항목. compressed가 None이면 문서 기본 압축 여부를 따른다."""
        if name not in self.raw_streams:
            raise CorruptFileError(f"{name} stream is missing; the file is damaged")
        raw = self.raw_streams[name]
        budget = min(
            self.limits.max_bin_data_bytes,
            self.limits.max_total_bin_data_bytes
            - sum(size for other, size in self._bin_data_sizes.items() if other != name),
        )
        too_large = LimitExceededError(
            f"{name} exceeds the picture limit ({self.limits.max_bin_data_bytes} bytes each, "
            f"{self.limits.max_total_bin_data_bytes} bytes per document); raise the Limits values"
        )
        if self.header.password_protected:
            raw = crypto.decrypt_stream(raw, self.password or "", self.header.encrypt_version)
        # HwpApp FUN_10256b90: 암호가 있으면 항목의 압축 값과 상관없이 압축 모드(EnterCompressMode)로 연다.
        if self.header.password_protected or (self.header.compressed if compressed is None else compressed):
            try:
                plain = inflate(raw, budget)
            except LimitExceededError as error:
                raise too_large from error
        else:
            plain = raw
        if len(plain) > budget:
            raise too_large
        self._bin_data_sizes[name] = len(plain)
        return plain


def _starts_with_tag(stream: bytes, tag: int) -> bool:
    return len(stream) >= 4 and struct.unpack_from("<I", stream)[0] & 0x3FF == tag
