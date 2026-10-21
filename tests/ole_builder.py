"""시험용 합성 HWP 파일: 스트림 묶음을 OLE 복합 파일(CFB 판 3, 512바이트 섹터)로 쓰고 레코드를 짓는다.

olefile은 새 파일을 만들지 못한다. 4096바이트 미만 스트림은 CFB 규칙대로 미니 스트림에 넣는다.
"""

from __future__ import annotations

import struct
import zlib

SECTOR = 512
MINI_SECTOR = 64
MINI_CUTOFF = 4096
END_OF_CHAIN = 0xFFFFFFFE
FAT_SECTOR = 0xFFFFFFFD
FREE = 0xFFFFFFFF
NO_STREAM = 0xFFFFFFFF


def record(tag: int, level: int, payload: bytes) -> bytes:
    if len(payload) >= 0xFFF:
        return struct.pack("<II", tag | level << 10 | 0xFFF << 20, len(payload)) + payload
    return struct.pack("<I", tag | level << 10 | len(payload) << 20) + payload


def deflate(data: bytes) -> bytes:
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    return compressor.compress(data) + compressor.flush()


def file_header(flags: int = 0x01, version: tuple[int, int, int, int] = (5, 1, 0, 0)) -> bytes:
    """flags bit0 압축, bit1 열기 암호, bit2 배포용, bit4 DRM."""
    signature = b"HWP Document File".ljust(32, b"\x00")
    word = version[0] << 24 | version[1] << 16 | version[2] << 8 | version[3]
    return signature + struct.pack("<II", word, flags) + bytes(256 - 40)


def _chain(fat: list[int], start: int, count: int) -> None:
    for index in range(start, start + count):
        fat[index] = index + 1 if index + 1 < start + count else END_OF_CHAIN


def build_ole(streams: dict[str, bytes]) -> bytes:
    """streams의 키는 "BodyText/Section0"처럼 저장소 이름을 '/'로 이은 경로다."""
    # 디렉터리 항목: [이름, 종류(1 저장소, 2 스트림, 5 루트), 자식 목록, 데이터]
    entries: list[list] = [["Root Entry", 5, [], b""]]
    storages = {"": 0}
    for path, data in streams.items():
        parts = path.split("/")
        parent = ""
        for depth, part in enumerate(parts):
            key = "/".join(parts[: depth + 1])
            if depth == len(parts) - 1:
                entries.append([part, 2, [], data])
                entries[storages[parent]][2].append(len(entries) - 1)
            elif key not in storages:
                entries.append([part, 1, [], b""])
                storages[key] = len(entries) - 1
                entries[storages[parent]][2].append(len(entries) - 1)
            parent = key

    mini_stream = bytearray()
    mini_fat: list[int] = []
    big: list[tuple[int, bytes]] = []
    starts: dict[int, int] = {}
    for sid, (_, kind, _, data) in enumerate(entries):
        if kind != 2 or not data:
            continue
        if len(data) < MINI_CUTOFF:
            first = len(mini_stream) // MINI_SECTOR
            count = -(-len(data) // MINI_SECTOR)
            mini_stream += data.ljust(count * MINI_SECTOR, b"\x00")
            mini_fat += [first + i + 1 if i + 1 < count else END_OF_CHAIN for i in range(count)]
            starts[sid] = first
        else:
            big.append((sid, data))

    def sectors(size: int) -> int:
        return -(-size // SECTOR)

    dir_sectors = sectors(len(entries) * 128)
    mini_fat_sectors = sectors(len(mini_fat) * 4)
    mini_stream_sectors = sectors(len(mini_stream))
    data_sectors = sum(sectors(len(data)) for _, data in big)
    body = dir_sectors + mini_fat_sectors + mini_stream_sectors + data_sectors
    fat_sectors = 1
    while fat_sectors * (SECTOR // 4) < body + fat_sectors:
        fat_sectors += 1
    assert fat_sectors <= 109, "DIFAT 확장은 만들지 않는다"

    fat = [FREE] * (fat_sectors * (SECTOR // 4))
    for index in range(fat_sectors):
        fat[index] = FAT_SECTOR
    cursor = fat_sectors
    dir_start = cursor
    _chain(fat, cursor, dir_sectors)
    cursor += dir_sectors
    mini_fat_start = cursor if mini_fat_sectors else END_OF_CHAIN
    _chain(fat, cursor, mini_fat_sectors)
    cursor += mini_fat_sectors
    mini_stream_start = cursor if mini_stream_sectors else END_OF_CHAIN
    _chain(fat, cursor, mini_stream_sectors)
    cursor += mini_stream_sectors
    for sid, data in big:
        starts[sid] = cursor
        _chain(fat, cursor, sectors(len(data)))
        cursor += sectors(len(data))

    def sort_key(sid: int) -> tuple[int, str]:
        return len(entries[sid][0]), entries[sid][0].upper()

    left_right: dict[int, int] = {}
    child_of: dict[int, int] = {}
    for sid, (_, _, children, _) in enumerate(entries):
        ordered = sorted(children, key=sort_key)
        if ordered:
            child_of[sid] = ordered[0]
        # 형제는 오른쪽 형제 고리로만 잇는다(CFB 이름 차례대로 커지는 한쪽 나무).
        for current, following in zip(ordered, ordered[1:], strict=False):
            left_right[current] = following

    directory = bytearray()
    for sid, (name, kind, _, data) in enumerate(entries):
        encoded = (name + "\x00").encode("utf-16-le")
        if kind == 5:
            start, size = mini_stream_start, len(mini_stream)
        elif kind == 2 and data:
            start, size = starts[sid], len(data)
        else:
            start, size = END_OF_CHAIN if kind == 2 else 0, 0
        directory += encoded.ljust(64, b"\x00") + struct.pack("<HBB", len(encoded), kind, 1)
        directory += struct.pack("<III", NO_STREAM, left_right.get(sid, NO_STREAM), child_of.get(sid, NO_STREAM))
        directory += bytes(16 + 4 + 16) + struct.pack("<III", start, size, 0)
    directory = directory.ljust(dir_sectors * SECTOR, b"\x00")

    header = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(16)
    header += struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) + bytes(6)
    header += struct.pack(
        "<IIIIIIIII", 0, fat_sectors, dir_start, 0, MINI_CUTOFF, mini_fat_start, mini_fat_sectors, END_OF_CHAIN, 0
    )
    difat = list(range(fat_sectors)) + [FREE] * (109 - fat_sectors)
    header += struct.pack("<109I", *difat)

    parts = [header, struct.pack(f"<{len(fat)}I", *fat), directory]
    parts.append(struct.pack(f"<{len(mini_fat)}I", *mini_fat).ljust(mini_fat_sectors * SECTOR, b"\xff"))
    parts.append(bytes(mini_stream).ljust(mini_stream_sectors * SECTOR, b"\x00"))
    for _, data in big:
        parts.append(data.ljust(sectors(len(data)) * SECTOR, b"\x00"))
    return b"".join(parts)
