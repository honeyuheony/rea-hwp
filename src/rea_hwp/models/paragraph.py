"""문단 레코드: PARA_HEADER, PARA_TEXT, PARA_CHAR_SHAPE, PARA_LINE_SEG, PARA_RANGE_TAG."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .. import tags
from ..binio import Reader, Writer
from .base import RecordModel
from .registry import register

#: 1 WCHAR만 차지하는 제어 문자(명세 표6). 나머지 0~31은 8 WCHAR(코드 + 12바이트 + 코드)다.
CHAR_CONTROLS = frozenset({0, 10, 13, 24, 25, 26, 27, 28, 29, 30, 31})
INLINE_CONTROLS = frozenset({4, 5, 6, 7, 8, 9, 19, 20})
TAB = 9
LINE_BREAK = 10
PARA_BREAK = 13


@register(tags.PARA_HEADER)
@dataclass
class ParaHeader(RecordModel):
    FIELDS = [
        ("raw_char_count", "u32"),
        ("control_mask", "u32"),
        ("para_shape_id", "u16"),
        ("style_id", "u8"),
        ("break_type", "u8"),
        ("char_shape_count", "u16"),
        ("range_tag_count", "u16"),
        ("line_seg_count", "u16"),
        ("instance_id", "u32"),
        ("track_change_merge", "u16"),
    ]

    @property
    def char_count(self) -> int:
        return self.raw_char_count & 0x7FFFFFFF


@dataclass
class ControlChar:
    code: int
    #: 코드 뒤 14바이트(확장 컨트롤이면 앞 4바이트가 컨트롤 ID, 끝 2바이트는 코드 반복).
    data: bytes

    @property
    def ctrl_id(self) -> int:
        return struct.unpack_from("<I", self.data, 0)[0]

    @property
    def is_extended(self) -> bool:
        return self.code not in INLINE_CONTROLS

    @property
    def tab_width(self) -> int:
        return struct.unpack_from("<I", self.data, 0)[0]


@register(tags.PARA_TEXT)
@dataclass
class ParaText(RecordModel):
    #: 글자 문자열과 ControlChar가 섞인 목록. 문자열 안의 글자 하나가 WCHAR 하나다.
    items: list[str | ControlChar] = field(default_factory=list)

    def decode_fields(self, reader: Reader) -> None:
        payload = reader.payload
        units = len(payload) // 2
        buffer: list[int] = []
        index = 0
        while index < units:
            (unit,) = struct.unpack_from("<H", payload, index * 2)
            if unit >= 32 or unit in CHAR_CONTROLS:
                buffer.append(unit)
                index += 1
                continue
            if index + 8 > units:
                break
            if buffer:
                self.items.append(_units_to_text(buffer))
                buffer = []
            start = index * 2 + 2
            self.items.append(ControlChar(unit, payload[start : start + 14]))
            index += 8
        if buffer:
            self.items.append(_units_to_text(buffer))
        reader.offset = index * 2

    def encode_fields(self, writer: Writer) -> None:
        for item in self.items:
            if isinstance(item, ControlChar):
                writer.write("u16", item.code)
                writer.bytes(item.data)
            else:
                writer.bytes(item.encode("utf-16-le", errors="surrogatepass"))

    def positioned(self) -> list[tuple[int, str | ControlChar]]:
        """(WCHAR 위치, 항목) 목록. 글자 모양 구간·LINE_SEG의 위치와 같은 좌표다."""
        position = 0
        placed: list[tuple[int, str | ControlChar]] = []
        for item in self.items:
            if isinstance(item, ControlChar):
                placed.append((position, item))
                position += 8
            else:
                for char in item:
                    placed.append((position, char))
                    position += 1
        return placed


def _units_to_text(units: list[int]) -> str:
    return struct.pack(f"<{len(units)}H", *units).decode("utf-16-le", errors="surrogatepass")


@register(tags.PARA_CHAR_SHAPE)
@dataclass
class ParaCharShape(RecordModel):
    #: (시작 WCHAR 위치, CHAR_SHAPE ID) 목록.
    runs: list[tuple[int, int]] = field(default_factory=list)

    def decode_fields(self, reader: Reader) -> None:
        while reader.remaining >= 8:
            self.runs.append((reader.read("u32"), reader.read("u32")))

    def encode_fields(self, writer: Writer) -> None:
        for position, shape_id in self.runs:
            writer.write("u32", position)
            writer.write("u32", shape_id)


@dataclass
class LineSeg:
    text_start: int
    vertical_pos: int
    line_height: int
    text_height: int
    baseline_gap: int
    line_spacing: int
    column_start: int
    segment_width: int
    #: bit0 쪽의 첫 줄, bit1 단의 첫 줄, bit16 빈 세그먼트, bit17/18 줄의 첫/마지막 세그먼트, bit20 들여쓰기 적용.
    flags: int


_LINE_SEG = struct.Struct("<IiiiiiiiI")


@register(tags.PARA_LINE_SEG)
@dataclass
class ParaLineSeg(RecordModel):
    segments: list[LineSeg] = field(default_factory=list)

    def decode_fields(self, reader: Reader) -> None:
        while reader.remaining >= _LINE_SEG.size:
            self.segments.append(LineSeg(*_LINE_SEG.unpack(reader.bytes(_LINE_SEG.size))))

    def encode_fields(self, writer: Writer) -> None:
        for seg in self.segments:
            writer.bytes(
                _LINE_SEG.pack(
                    seg.text_start,
                    seg.vertical_pos,
                    seg.line_height,
                    seg.text_height,
                    seg.baseline_gap,
                    seg.line_spacing,
                    seg.column_start,
                    seg.segment_width,
                    seg.flags,
                )
            )


@register(tags.PARA_RANGE_TAG)
@dataclass
class ParaRangeTag(RecordModel):
    #: (시작, 끝, 태그) 목록. 태그 상위 8비트는 종류, 하위 24비트는 데이터다.
    ranges: list[tuple[int, int, int]] = field(default_factory=list)

    def decode_fields(self, reader: Reader) -> None:
        while reader.remaining >= 12:
            self.ranges.append((reader.read("u32"), reader.read("u32"), reader.read("u32")))

    def encode_fields(self, writer: Writer) -> None:
        for start, end, tag in self.ranges:
            writer.write("u32", start)
            writer.write("u32", end)
            writer.write("u32", tag)
