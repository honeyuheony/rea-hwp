"""브라우저가 그리지 못하는 비트맵(TIFF, PCX)을 PNG로 바꾼다.

범위: TIFF 첫 IFD의 비압축·LZW(예측자 2), 1·8비트 회색과 RGB(A), strip 배치. PCX 1비트·8비트 팔레트·24비트.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Iterable

_TIFF_SIGNATURES = (b"II*\x00", b"MM\x00*")
_PCX_MANUFACTURER = 10

# TIFF 태그 번호(TIFF 6.0)
_WIDTH, _HEIGHT, _BITS, _COMPRESSION, _PHOTOMETRIC = 256, 257, 258, 259, 262
_STRIP_OFFSETS, _SAMPLES, _ROWS_PER_STRIP, _STRIP_COUNTS, _PLANAR, _PREDICTOR = 273, 277, 278, 279, 284, 317
_TYPE_SIZES = {1: 1, 3: 2, 4: 4}
_TYPE_FORMATS = {1: "B", 3: "H", 4: "I"}

# PNG 색 형식: 회색, RGB, 팔레트, 회색+알파, RGBA
_PNG_GRAY, _PNG_RGB, _PNG_PALETTE, _PNG_GRAY_ALPHA, _PNG_RGBA = 0, 2, 3, 4, 6
_INVERT = bytes(255 - value for value in range(256))


def png_from_raster(data: bytes) -> bytes | None:
    """TIFF·PCX면 같은 그림의 PNG, 다른 형식이거나 옮기지 않은 배치면 None."""
    if data.startswith(_TIFF_SIGNATURES):
        return _tiff_to_png(data)
    if len(data) > 128 and data[0] == _PCX_MANUFACTURER and data[2] == 1:
        return _pcx_to_png(data)
    return None


def image_mime(data: bytes, name: str) -> str:
    """머리 바이트로 래스터 형식을, 그 밖에는 저장 이름의 확장자로 mime을 정한다."""
    signatures = {
        b"\x89PNG": "image/png",
        b"\xff\xd8": "image/jpeg",
        b"GIF8": "image/gif",
        b"BM": "image/bmp",
    }
    for signature, mime in signatures.items():
        if data.startswith(signature):
            return mime
    extension = name.rsplit(".", 1)[-1].lower()
    return {"wmf": "image/wmf", "emf": "image/emf", "svg": "image/svg+xml"}.get(extension, "application/octet-stream")


def pixel_size(data: bytes) -> tuple[int, int] | None:
    """PNG·JPEG·GIF·BMP 머리에 적힌 (가로, 세로) 픽셀 수. 다른 형식이면 None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        return struct.unpack_from(">II", data, 16)
    if data.startswith(b"GIF") and len(data) >= 10:
        return struct.unpack_from("<HH", data, 6)
    if data.startswith(b"BM") and len(data) >= 26:
        width, height = struct.unpack_from("<ii", data, 18)
        return width, abs(height)
    if data.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 9 <= len(data) and data[offset] == 0xFF:
            marker = data[offset + 1]
            (length,) = struct.unpack_from(">H", data, offset + 2)
            # SOF0~SOF15 가운데 DHT(C4)·JPG(C8)·DAC(CC)는 프레임 머리가 아니다.
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack_from(">HH", data, offset + 5)
                return width, height
            offset += 2 + length
    return None


def _tiff_to_png(data: bytes) -> bytes | None:
    order = "<" if data[:2] == b"II" else ">"
    (ifd,) = struct.unpack_from(order + "I", data, 4)
    (count,) = struct.unpack_from(order + "H", data, ifd)
    fields: dict[int, list[int]] = {}
    for index in range(count):
        tag, kind, number, value_offset = struct.unpack_from(order + "HHII", data, ifd + 2 + 12 * index)
        if kind not in _TYPE_SIZES:
            continue
        start = ifd + 2 + 12 * index + 8
        if _TYPE_SIZES[kind] * number > 4:
            start = value_offset
        fields[tag] = list(struct.unpack_from(f"{order}{number}{_TYPE_FORMATS[kind]}", data, start))

    width, height = fields[_WIDTH][0], fields[_HEIGHT][0]
    samples = fields.get(_SAMPLES, [1])[0]
    bits = fields.get(_BITS, [1])[0]
    compression = fields.get(_COMPRESSION, [1])[0]
    photometric = fields.get(_PHOTOMETRIC, [0])[0]
    color_types = {(0, 1): _PNG_GRAY, (1, 1): _PNG_GRAY, (0, 2): _PNG_GRAY_ALPHA, (1, 2): _PNG_GRAY_ALPHA}
    color_types |= {(2, 3): _PNG_RGB, (2, 4): _PNG_RGBA}
    if (
        compression not in (1, 5)
        or fields.get(_PLANAR, [1])[0] != 1
        or (photometric, samples) not in color_types
        or (bits, samples) not in ((1, 1), (8, samples))
    ):
        return None

    strips = []
    for offset, length in zip(fields[_STRIP_OFFSETS], fields[_STRIP_COUNTS], strict=True):
        strip = data[offset : offset + length]
        strips.append(_lzw_decode(strip) if compression == 5 else strip)
    row_bytes = (width * samples * bits + 7) // 8
    pixels = b"".join(strips)
    if len(pixels) < row_bytes * height:
        raise ValueError(f"TIFF strips hold {len(pixels)} bytes, expected {row_bytes * height}")
    rows = [bytearray(pixels[row * row_bytes : (row + 1) * row_bytes]) for row in range(height)]
    for row in rows:
        if bits == 8 and fields.get(_PREDICTOR, [1])[0] == 2:
            # 수평 차분: 같은 표본끼리 왼쪽 값을 더해 되돌린다(256으로 돈다).
            for index in range(samples, row_bytes):
                row[index] = (row[index] + row[index - samples]) & 0xFF
        if photometric == 0:
            # WhiteIsZero: PNG 회색은 0이 검정이라 뒤집는다(알파 표본은 그대로).
            step = 1 if bits == 1 else samples
            row[::step] = row[::step].translate(_INVERT)
    return _png(width, height, bits, color_types[photometric, samples], rows)


def _lzw_decode(code_bytes: bytes) -> bytes:
    """TIFF LZW: 앞자리 비트부터 읽고, 표가 다음 폭의 한 칸 앞(511, 1023, 2047)에 이르면 코드 폭을 늘린다."""
    clear, end = 256, 257
    table = [bytes([value]) for value in range(256)] + [b"", b""]
    output = bytearray()
    width, buffer, buffered = 9, 0, 0
    previous: bytes | None = None
    for byte in code_bytes:
        buffer = (buffer << 8) | byte
        buffered += 8
        while buffered >= width:
            buffered -= width
            code = buffer >> buffered
            buffer &= (1 << buffered) - 1
            if code == end:
                return bytes(output)
            if code == clear:
                del table[258:]
                width, previous = 9, None
                continue
            if previous is None:
                entry = table[code]
            else:
                entry = table[code] if code < len(table) else previous + previous[:1]
                table.append(previous + entry[:1])
            output += entry
            previous = entry
            if len(table) == (1 << width) - 1 and width < 12:
                width += 1
    return bytes(output)


def _pcx_to_png(data: bytes) -> bytes | None:
    bits, left, top, right, bottom = struct.unpack_from("<B4H", data, 3)
    planes = data[65]
    (line_bytes,) = struct.unpack_from("<H", data, 66)
    width, height = right - left + 1, bottom - top + 1
    total = line_bytes * planes * height
    decoded = bytearray()
    position = 128
    while len(decoded) < total and position < len(data):
        byte = data[position]
        position += 1
        if byte >= 0xC0:
            decoded += bytes([data[position]]) * (byte & 0x3F)
            position += 1
        else:
            decoded.append(byte)
    if len(decoded) < total:
        return None

    def plane(row: int, index: int) -> bytes:
        start = (row * planes + index) * line_bytes
        return bytes(decoded[start : start + line_bytes])

    if bits == 1 and planes == 1:
        # 두 색은 머리의 16색 팔레트 앞 두 칸(48바이트 EGA 팔레트, 16번째 바이트부터).
        palette = data[16:22]
        rows = [plane(row, 0)[: (width + 7) // 8] for row in range(height)]
        return _png(width, height, 1, _PNG_PALETTE, rows, palette)
    if bits == 8 and planes == 1 and len(data) >= 769 and data[-769] == 12:
        return _png(width, height, 8, _PNG_PALETTE, [plane(row, 0)[:width] for row in range(height)], data[-768:])
    if bits == 8 and planes == 3:
        rgb_rows = []
        for row in range(height):
            rgb = bytearray(width * 3)
            for index in range(3):
                rgb[index::3] = plane(row, index)[:width]
            rgb_rows.append(bytes(rgb))
        return _png(width, height, 8, _PNG_RGB, rgb_rows)
    return None


def _png(
    width: int, height: int, bits: int, color_type: int, rows: Iterable[bytes | bytearray], palette: bytes | None = None
) -> bytes:
    """행마다 필터 0을 붙여 deflate로 묶은 PNG."""
    filtered = b"".join(b"\x00" + row for row in rows)

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    header = struct.pack(">IIBBBBB", width, height, bits, color_type, 0, 0, 0)
    parts = [b"\x89PNG\r\n\x1a\n", chunk(b"IHDR", header)]
    if palette is not None:
        parts.append(chunk(b"PLTE", palette))
    parts += [chunk(b"IDAT", zlib.compress(filtered, 6)), chunk(b"IEND", b"")]
    return b"".join(parts)
