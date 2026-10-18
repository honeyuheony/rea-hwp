"""배포용·암호 HWP 문서를 복호화한 본문으로 일반 사본을 만든다(정답 PDF용).

한글 2022는 배포용 문서를 PDF로 저장하지 못하고, 인쇄 제한이 걸린 문서는 인쇄도 하지 못한다.
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

from rea_hwp import crypto, tags
from rea_hwp.container import HwpContainer, inflate
from rea_hwp.record import parse_records

SECTOR = 512
MINI_SECTOR = 64
MINI_CUTOFF = 4096
FREE, END, FAT_SECTOR = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
NO_STREAM = 0xFFFFFFFF
DIFAT_IN_HEADER = 109
DOCUMENT_PROPERTIES_SIZE = 26


def plain_streams(source: Path, password: str | None = None) -> dict[str, bytes]:
    """배포용·암호 문서를 풀어 쓴 스트림. ViewText를 BodyText로 옮기고 배포용·암호 표시와 배포 레코드를 지운다."""
    container = HwpContainer.open(source, password)
    header = container.header
    if not (header.distributed or header.password_protected):
        raise ValueError(f"{source} is neither a distribution nor a password-protected document")
    streams: dict[str, bytes] = {}
    for name, raw in container.raw_streams.items():
        if name.startswith("Scripts/"):
            continue  # 스크립트도 암호화돼 있고 배치와 무관해 뺀다.
        if header.password_protected and (name == "DocInfo" or name.startswith(("BodyText/", "BinData/"))):
            raw = _deflate_prefix(crypto.decrypt_stream(raw, password or "", header.encrypt_version))
        if name.startswith("ViewText/"):
            streams["BodyText/" + name.split("/", 1)[1]] = crypto.decrypt_distribute_stream(raw)
        elif name == "FileHeader":
            flags = struct.unpack_from("<I", raw, 36)[0] & ~0x06
            streams[name] = raw[:36] + struct.pack("<I", flags) + raw[40:]
        elif name == "DocInfo":
            plain = inflate(raw) if header.compressed else raw
            records = parse_records(plain)
            records.records = [record for record in records.records if record.tag != tags.DISTRIBUTE_DOC_DATA]
            for record in records.records:
                if record.tag == tags.DOCUMENT_PROPERTIES:
                    # 암호 문서는 문서 속성 26바이트 앞에 암호 확인 자료 256바이트를 둔다(같은 문서의 일반 판과 비교).
                    record.payload = record.payload[-DOCUMENT_PROPERTIES_SIZE:]
            data = records.to_bytes()
            if header.compressed:
                packer = zlib.compressobj(9, zlib.DEFLATED, -15)
                data = packer.compress(data) + packer.flush()
            streams[name] = data
        else:
            streams[name] = raw
    return streams


def _deflate_prefix(data: bytes) -> bytes:
    """복호화한 스트림 뒤에 붙은 블록 채움 바이트를 떼고 deflate 데이터만 남긴다. 압축되지 않은 스트림은 그대로 둔다."""
    inflater = zlib.decompressobj(-15)
    try:
        inflater.decompress(data)
    except zlib.error:  # 압축하지 않고 저장한 항목(그림 원본 등)이다.
        return data
    return data[: len(data) - len(inflater.unused_data)] if inflater.eof else data


def _name_key(name: str) -> tuple[int, str]:
    # CFB 형제 항목 순서: 이름 길이가 먼저, 같으면 대문자로 비교한다.
    return len(name), name.upper()


def write_cfb(streams: dict[str, bytes], target: Path) -> None:
    """경로("저장소/스트림")별 바이트를 CFB 버전 3 파일로 쓴다. 형제 트리는 모두 검은 노드인 균형 이진 트리다."""
    entries: list[dict] = [{"name": "Root Entry", "type": 5, "children": {}, "data": b""}]
    for path, data in streams.items():
        parent = entries[0]
        parts = path.split("/")
        for part in parts[:-1]:
            if part not in parent["children"]:
                entries.append({"name": part, "type": 1, "children": {}, "data": b""})
                parent["children"][part] = len(entries) - 1
            parent = entries[parent["children"][part]]
        entries.append({"name": parts[-1], "type": 2, "children": {}, "data": data})
        parent["children"][parts[-1]] = len(entries) - 1
    for entry in entries:
        entry.update(left=NO_STREAM, right=NO_STREAM, child=NO_STREAM)

    def build(indices: list[int]) -> int:
        if not indices:
            return NO_STREAM
        middle = len(indices) // 2
        node = indices[middle]
        entries[node]["left"] = build(indices[:middle])
        entries[node]["right"] = build(indices[middle + 1 :])
        return node

    for entry in entries:
        if entry["children"]:
            ordered = sorted(entry["children"].values(), key=lambda index: _name_key(entries[index]["name"]))
            entry["child"] = build(ordered)

    fat: list[int] = []
    sectors: list[bytes] = []

    def allocate(data: bytes) -> int:
        count = (len(data) + SECTOR - 1) // SECTOR
        if count == 0:
            return END
        first = len(sectors)
        for k in range(count):
            sectors.append(data[k * SECTOR : (k + 1) * SECTOR].ljust(SECTOR, b"\0"))
            fat.append(first + k + 1 if k < count - 1 else END)
        return first

    mini_stream = bytearray()
    mini_fat: list[int] = []
    for entry in entries:
        data = entry["data"]
        entry["size"] = len(data)
        if entry["type"] != 2:
            entry["start"] = END
        elif len(data) < MINI_CUTOFF:
            count = (len(data) + MINI_SECTOR - 1) // MINI_SECTOR
            first = len(mini_fat)
            entry["start"] = first if count else END
            for k in range(count):
                mini_fat.append(first + k + 1 if k < count - 1 else END)
            mini_stream += data.ljust(count * MINI_SECTOR, b"\0")
        else:
            entry["start"] = allocate(data)
    entries[0]["start"] = allocate(bytes(mini_stream))
    entries[0]["size"] = len(mini_stream)
    mini_fat_start = allocate(b"".join(struct.pack("<I", value) for value in mini_fat))
    mini_fat_sectors = (len(mini_fat) * 4 + SECTOR - 1) // SECTOR

    directory = bytearray()
    for entry in entries:
        name = entry["name"].encode("utf-16-le") + b"\0\0"
        record = name.ljust(64, b"\0") + struct.pack("<HBB", len(name), entry["type"], 1)
        record += struct.pack("<III", entry["left"], entry["right"], entry["child"])
        record += bytes(16 + 4 + 16)  # CLSID, 상태 비트, 만든·고친 시각
        record += struct.pack("<IIi", entry["start"], entry["size"], 0)
        directory += record
    unused = bytes(64 + 4) + struct.pack("<III", NO_STREAM, NO_STREAM, NO_STREAM) + bytes(36 + 12)
    while len(directory) % SECTOR:
        directory += unused
    directory_start = allocate(bytes(directory))

    # FAT 섹터 수는 FAT 자신이 차지하는 섹터까지 셈해 정한다.
    fat_sectors = 1
    while (len(sectors) + fat_sectors) > fat_sectors * (SECTOR // 4):
        fat_sectors += 1
    if fat_sectors > DIFAT_IN_HEADER:
        raise ValueError("document too large for a header-only DIFAT")
    fat_start = len(sectors)
    fat.extend([FAT_SECTOR] * fat_sectors)
    fat.extend([FREE] * (fat_sectors * (SECTOR // 4) - len(fat)))
    table = b"".join(struct.pack("<I", value) for value in fat)
    for k in range(fat_sectors):
        sectors.append(table[k * SECTOR : (k + 1) * SECTOR])

    difat = [fat_start + k for k in range(fat_sectors)] + [FREE] * (DIFAT_IN_HEADER - fat_sectors)
    header = bytes.fromhex("D0CF11E0A1B11AE1") + bytes(16)
    header += struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) + bytes(6)
    header += struct.pack("<IIIIIIIII", 0, fat_sectors, directory_start, 0, MINI_CUTOFF,
                          mini_fat_start if mini_fat else END, mini_fat_sectors, END, 0)  # fmt: skip
    header += b"".join(struct.pack("<I", value) for value in difat)
    target.write_bytes(header + b"".join(sectors))


def main() -> int:
    source, target = Path(sys.argv[1]), Path(sys.argv[2])
    write_cfb(plain_streams(source, sys.argv[3] if len(sys.argv) > 3 else None), target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
