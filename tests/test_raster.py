from __future__ import annotations

import struct
import zlib

from rea_hwp.render.raster import _lzw_decode, pixel_size, png_from_raster


def png_rows(png: bytes) -> tuple[tuple[int, int, int, int], list[bytes]]:
    position, idat, header = 8, b"", None
    while position < len(png):
        (length,) = struct.unpack_from(">I", png, position)
        kind, body = png[position + 4 : position + 8], png[position + 8 : position + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBB", body[:10])
        elif kind == b"IDAT":
            idat += body
        position += 12 + length
    assert header is not None
    raw = zlib.decompress(idat)
    width, height, bits, color_type = header
    samples = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    stride = 1 + (width * samples * bits + 7) // 8
    rows = [raw[row * stride : (row + 1) * stride] for row in range(height)]
    assert all(row[0] == 0 for row in rows)
    return header, [row[1:] for row in rows]


def tiff(
    width: int, height: int, pixels: bytes, *, photometric: int, samples: int = 1, bits: int = 8, **extra: int
) -> bytes:
    entries = {256: width, 257: height, 258: bits, 259: extra.get("compression", 1), 262: photometric}
    entries |= {273: 0, 277: samples, 278: height, 279: len(pixels), 317: extra.get("predictor", 1)}
    ifd_size = 2 + 12 * len(entries) + 4
    entries[273] = 8 + ifd_size
    body = struct.pack("<H", len(entries))
    for tag, value in sorted(entries.items()):
        body += struct.pack("<HHII", tag, 4, 1, value)
    return b"II*\x00" + struct.pack("<I", 8) + body + b"\x00" * 4 + pixels


def lzw_encode(plain: bytes) -> bytes:
    table = {bytes([value]): value for value in range(256)}
    codes, current, next_code, width = [(256, 9)], b"", 258, 9
    for byte in plain:
        candidate = current + bytes([byte])
        if candidate in table:
            current = candidate
            continue
        codes.append((table[current], width))
        table[candidate] = next_code
        next_code += 1
        if next_code == 1 << width:
            width += 1
        current = bytes([byte])
    codes += [(table[current], width), (257, width)]
    value = count = 0
    for code, size in codes:
        value, count = (value << size) | code, count + size
    value <<= -count % 8
    return value.to_bytes((count + 7) // 8, "big")


def test_lzw_decode_round_trips_through_width_changes() -> None:
    plain = bytes(range(256)) * 12 + b"abcabcabcabc" * 50

    assert _lzw_decode(lzw_encode(plain)) == plain


def test_tiff_gray_with_horizontal_predictor() -> None:
    differences = bytes([10, 5, 5, 200, 0, 100])
    png = png_from_raster(tiff(3, 2, differences, photometric=1, predictor=2))

    header, rows = png_rows(png)
    assert header == (3, 2, 8, 0)
    assert rows == [bytes([10, 15, 20]), bytes([200, 200, 44])]


def test_tiff_lzw_white_is_zero_bilevel_is_inverted() -> None:
    packed = bytes([0b10100000, 0b01000000])
    png = png_from_raster(tiff(3, 2, lzw_encode(packed), photometric=0, bits=1, compression=5))

    header, rows = png_rows(png)
    assert header == (3, 2, 1, 0)
    assert rows == [bytes([0b01011111]), bytes([0b10111111])]


def test_pcx_run_length_bilevel_keeps_header_palette() -> None:
    header = bytearray(128)
    header[0:4] = bytes([10, 5, 1, 1])
    struct.pack_into("<4H", header, 4, 0, 0, 9, 1)
    header[16:22] = bytes.fromhex("000000ffffff")
    header[65] = 1
    struct.pack_into("<H", header, 66, 2)
    runs = bytes([0xC2, 0xF0, 0x0F, 0xC1, 0xFF])

    png = png_from_raster(bytes(header) + runs)

    (width, height, bits, color_type), rows = png_rows(png)
    assert (width, height, bits, color_type) == (10, 2, 1, 3)
    assert rows == [bytes([0xF0, 0xF0]), bytes([0x0F, 0xFF])]
    assert bytes.fromhex("000000ffffff") in png


def test_other_formats_are_left_alone() -> None:
    assert png_from_raster(b"\x89PNG\r\n\x1a\n") is None
    assert png_from_raster(tiff(1, 1, b"\x00", photometric=3)) is None


def test_pixel_size_reads_png_gif_bmp_and_jpeg_headers():
    png = b"\x89PNG\r\n\x1a\n" + struct.pack(">I4sII", 13, b"IHDR", 1229, 513)
    gif = b"GIF89a" + struct.pack("<HH", 529, 749)
    bmp = b"BM" + b"\0" * 16 + struct.pack("<ii", 87, -93)
    jpeg = (
        b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 4) + b"JF" + b"\xff\xc0" + struct.pack(">HBHH", 11, 8, 1441, 1195)
    )
    assert pixel_size(png) == (1229, 513)
    assert pixel_size(gif) == (529, 749)
    assert pixel_size(bmp) == (87, 93)
    assert pixel_size(jpeg) == (1195, 1441)
    assert pixel_size(b"II*\x00") is None
