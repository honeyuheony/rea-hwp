"""HWP 5.0 레코드 스트림 읽기와 바이트 동일 직렬화."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

#: 크기 필드가 이 값이면 헤더 뒤 u32가 실제 크기다(명세 5.0 rev1.3 4.1절).
EXTENDED_SIZE_MARK = 0xFFF


@dataclass
class Record:
    tag: int
    level: int
    payload: bytes
    #: 크기가 0xFFF 미만이어도 확장 크기 표기로 저장된 경우를 되살리기 위해 보관한다.
    extended_size: bool = False
    children: list[Record] = field(default_factory=list)

    def header_bytes(self) -> bytes:
        size = len(self.payload)
        if self.extended_size or size >= EXTENDED_SIZE_MARK:
            word = self.tag | (self.level << 10) | (EXTENDED_SIZE_MARK << 20)
            return struct.pack("<II", word, size)
        return struct.pack("<I", self.tag | (self.level << 10) | (size << 20))


@dataclass
class RecordStream:
    records: list[Record]
    #: 마지막 레코드 뒤에 남은, 레코드 헤더로 읽을 수 없는 바이트.
    trailing: bytes = b""

    def to_bytes(self) -> bytes:
        parts = []
        for record in self.records:
            parts.append(record.header_bytes())
            parts.append(record.payload)
        parts.append(self.trailing)
        return b"".join(parts)


def parse_records(stream: bytes) -> RecordStream:
    records: list[Record] = []
    offset = 0
    total = len(stream)
    while offset + 4 <= total:
        (word,) = struct.unpack_from("<I", stream, offset)
        tag = word & 0x3FF
        level = (word >> 10) & 0x3FF
        size = word >> 20
        header_len = 4
        extended = size == EXTENDED_SIZE_MARK
        if extended:
            if offset + 8 > total:
                break
            (size,) = struct.unpack_from("<I", stream, offset + 4)
            header_len = 8
        if offset + header_len + size > total:
            break
        start = offset + header_len
        records.append(Record(tag, level, stream[start : start + size], extended and size < EXTENDED_SIZE_MARK))
        offset = start + size
    return RecordStream(records, stream[offset:])


def build_tree(records: list[Record]) -> list[Record]:
    """level 값으로 부모-자식 관계를 만든다. 원래 순서는 깊이 우선 순회로 복원된다."""
    roots: list[Record] = []
    stack: list[Record] = []
    for record in records:
        record.children = []
        while stack and stack[-1].level >= record.level:
            stack.pop()
        (stack[-1].children if stack else roots).append(record)
        stack.append(record)
    return roots


def flatten(roots: list[Record]) -> list[Record]:
    ordered: list[Record] = []
    pending = list(reversed(roots))
    while pending:
        record = pending.pop()
        ordered.append(record)
        pending.extend(reversed(record.children))
    return ordered
