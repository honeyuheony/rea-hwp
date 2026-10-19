"""그리기 개체 레코드: SHAPE_COMPONENT와 도형별 레코드(선·사각형·타원·호·다각형·곡선·OLE·그림 등)."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import ClassVar

from .. import tags
from ..binio import Reader, Writer
from .base import FieldSpec, RecordModel
from .controls import bits
from .registry import register

PICTURE_SHAPE = tags.ctrl_id("$pic")
OLE_SHAPE = tags.ctrl_id("$ole")
CONTAINER_SHAPE = tags.ctrl_id("$con")
#: 선 정보·채우기 없이 행렬에서 끝나는 개체.
_NO_LINE_INFO = frozenset({PICTURE_SHAPE, OLE_SHAPE})

_MATRIX = struct.Struct("<6d")

Matrix = tuple[float, float, float, float, float, float]
Point = tuple[int, int]


def _read_matrix(reader: Reader) -> Matrix:
    return _MATRIX.unpack(reader.bytes(_MATRIX.size))


def _read_points(reader: Reader, count: int) -> list[Point]:
    """(x, y)가 번갈아 놓인 점 배열. 명세는 x 배열 뒤 y 배열이라 적었지만 실물은 번갈아 놓인다."""
    raw = reader.bytes(8 * count)
    return [struct.unpack_from("<ii", raw, 8 * index) for index in range(count)]


def _write_points(writer: Writer, points: list[Point]) -> None:
    for x, y in points:
        writer.write("i32", x)
        writer.write("i32", y)


_COMPONENT_FIELDS: list[FieldSpec] = [
    ("ctrl_id", "u32"),
    ("offset_x", "i32"),
    ("offset_y", "i32"),
    ("group_level", "u16"),
    ("local_version", "u16"),
    ("original_width", "u32"),
    ("original_height", "u32"),
    #: 부호 있는 값이다: 뒤집어 저장한 글상자가 높이 −1841을 담는다(온새미로 본교재 쪽 표지 글상자, 개체 높이 1842).
    ("current_width", "i32"),
    ("current_height", "i32"),
    ("flags", "u32"),
    ("rotation", "i16"),
    ("rotation_center_x", "i32"),
    ("rotation_center_y", "i32"),
]
_LINE_INFO_FIELDS: list[FieldSpec] = [
    ("line_color", "u32"),
    ("line_width", "i32"),
    ("line_attr", "u32"),
    ("outline_style", "u8"),
]


@dataclass
class ShapeComponent(RecordModel):
    """묶음 안 자식 도형의 개체 요소 속성. 선 정보 뒤의 채우기·그림자는 tail로 남긴다."""

    #: gso CTRL_HEADER 바로 아래면 앞에 컨트롤 ID가 한 번 더 있다.
    HAS_GSO_ID: ClassVar[bool] = False

    translation: Matrix | None = None
    #: (축척 행렬, 회전 행렬) 쌍. 행렬은 [a, b, tx, c, d, ty] 순서의 아핀 변환이다.
    scale_rotation: list[tuple[Matrix, Matrix]] | None = None
    child_ctrl_ids: list[int] | None = None

    def _head_spec(self) -> list[FieldSpec]:
        return ([("gso_ctrl_id", "u32")] if self.HAS_GSO_ID else []) + _COMPONENT_FIELDS

    def decode_fields(self, reader: Reader) -> None:
        if not self.read_spec(reader, self._head_spec()):
            return
        start = reader.offset
        try:
            count = reader.read("u16")
            translation = _read_matrix(reader)
            pairs = [(_read_matrix(reader), _read_matrix(reader)) for _ in range(count)]
        except ValueError:
            # 행렬이 잘린 레코드는 행렬부터 tail로 남긴다.
            reader.offset = start
            return
        self.translation, self.scale_rotation = translation, pairs
        if self.ctrl_id == CONTAINER_SHAPE:
            self._decode_children(reader)
        elif self.ctrl_id not in _NO_LINE_INFO:
            self.read_spec(reader, _LINE_INFO_FIELDS)

    def _decode_children(self, reader: Reader) -> None:
        start = reader.offset
        try:
            count = reader.read("u16")
            self.child_ctrl_ids = [reader.read("u32") for _ in range(count)]
        except ValueError:
            reader.offset = start

    def encode_fields(self, writer: Writer) -> None:
        if not self.write_spec(writer, self._head_spec()) or self.translation is None:
            return
        writer.write("u16", len(self.scale_rotation))
        writer.bytes(_MATRIX.pack(*self.translation))
        for scale, rotation in self.scale_rotation:
            writer.bytes(_MATRIX.pack(*scale))
            writer.bytes(_MATRIX.pack(*rotation))
        if self.child_ctrl_ids is not None:
            writer.write("u16", len(self.child_ctrl_ids))
            for child in self.child_ctrl_ids:
                writer.write("u32", child)
        elif self.ctrl_id not in _NO_LINE_INFO:
            self.write_spec(writer, _LINE_INFO_FIELDS)

    @property
    def shape_name(self) -> str:
        return tags.ctrl_name(self.ctrl_id)

    @property
    def horz_flip(self) -> bool:
        return bool(bits(self.flags, 0))

    @property
    def vert_flip(self) -> bool:
        return bool(bits(self.flags, 1))


@register(
    tags.SHAPE_COMPONENT,
    when=lambda context, payload: bool(context.ancestors) and context.ancestors[-1].tag == tags.CTRL_HEADER,
)
@dataclass
class GsoShapeComponent(ShapeComponent):
    """gso 컨트롤 바로 아래의 개체 요소 속성."""

    HAS_GSO_ID: ClassVar[bool] = True


register(tags.SHAPE_COMPONENT)(ShapeComponent)


@register(tags.SHAPE_COMPONENT_LINE)
@dataclass
class ShapeLine(RecordModel):
    """직선. 연결선이면 속성 뒤에 연결 대상·제어점이 온다(tail)."""

    FIELDS: ClassVar[list[FieldSpec]] = [
        ("start_x", "i32"),
        ("start_y", "i32"),
        ("end_x", "i32"),
        ("end_y", "i32"),
        # 명세 표92는 UINT16(전체 18B)이지만 실물은 20B다
        ("attr", "u32"),
    ]


@register(tags.SHAPE_COMPONENT_RECTANGLE)
@dataclass
class ShapeRectangle(RecordModel):
    FIELDS: ClassVar[list[FieldSpec]] = [
        ("round_ratio", "u8"),
        #: x1, y1, x2, y2, … 순서(명세는 x 배열 뒤 y 배열이라 적었지만 실물은 번갈아 놓인다).
        ("corners", "i32*8"),
    ]

    @property
    def corner_points(self) -> list[Point]:
        return list(zip(self.corners[0::2], self.corners[1::2], strict=True))


@register(tags.SHAPE_COMPONENT_ELLIPSE)
@dataclass
class ShapeEllipse(RecordModel):
    FIELDS: ClassVar[list[FieldSpec]] = [
        ("attr", "u32"),
        ("center_x", "i32"),
        ("center_y", "i32"),
        ("axis1_x", "i32"),
        ("axis1_y", "i32"),
        ("axis2_x", "i32"),
        ("axis2_y", "i32"),
        ("start_x", "i32"),
        ("start_y", "i32"),
        ("end_x", "i32"),
        ("end_y", "i32"),
        ("start2_x", "i32"),
        ("start2_y", "i32"),
        ("end2_x", "i32"),
        ("end2_y", "i32"),
    ]

    @property
    def is_arc(self) -> bool:
        return bool(bits(self.attr, 1))

    @property
    def arc_type(self) -> int:
        return bits(self.attr, 2, 8)


@register(tags.SHAPE_COMPONENT_ARC)
@dataclass
class ShapeArc(RecordModel):
    FIELDS: ClassVar[list[FieldSpec]] = [
        # 명세 표101은 속성 UINT32(전체 28B)지만 실물은 호 종류 BYTE 하나다(25B)
        ("arc_type", "u8"),
        ("center_x", "i32"),
        ("center_y", "i32"),
        ("axis1_x", "i32"),
        ("axis1_y", "i32"),
        ("axis2_x", "i32"),
        ("axis2_y", "i32"),
    ]


@register(tags.SHAPE_COMPONENT_POLYGON)
@dataclass
class ShapePolygon(RecordModel):
    """다각형. 명세는 점 개수를 INT16이라 적었지만 실물은 INT32이고, 점 뒤에 4바이트가 남는다(tail)."""

    points: list[Point] | None = None

    def decode_fields(self, reader: Reader) -> None:
        start = reader.offset
        try:
            count = reader.read("i32")
            self.points = _read_points(reader, count)
        except ValueError:
            reader.offset = start

    def encode_fields(self, writer: Writer) -> None:
        if self.points is not None:
            writer.write("i32", len(self.points))
            _write_points(writer, self.points)


@register(tags.SHAPE_COMPONENT_CURVE)
@dataclass
class ShapeCurve(RecordModel):
    """곡선. 점 뒤에 구간 종류(0 직선, 1 곡선) n-1개가 오고 4바이트가 남는다(tail)."""

    points: list[Point] | None = None
    segment_types: bytes | None = None

    def decode_fields(self, reader: Reader) -> None:
        start = reader.offset
        try:
            count = reader.read("i32")
            points = _read_points(reader, count)
            segment_types = reader.bytes(max(count - 1, 0))
        except ValueError:
            reader.offset = start
            return
        self.points, self.segment_types = points, segment_types

    def encode_fields(self, writer: Writer) -> None:
        if self.points is not None:
            writer.write("i32", len(self.points))
            _write_points(writer, self.points)
            writer.bytes(self.segment_types)


@register(tags.SHAPE_COMPONENT_OLE)
@dataclass
class ShapeOle(RecordModel):
    FIELDS: ClassVar[list[FieldSpec]] = [
        ("attr", "u32"),
        ("extent_x", "i32"),
        ("extent_y", "i32"),
        ("bin_data_id", "u16"),
        ("border_color", "u32"),
        ("border_width", "i32"),
        ("border_attr", "u32"),
    ]


@register(tags.SHAPE_COMPONENT_PICTURE)
@dataclass
class ShapePicture(RecordModel):
    """그림 개체(명세 표107). 효과 정보 뒤의 효과별 값과 원본 크기는 tail로 남긴다."""

    FIELDS: ClassVar[list[FieldSpec]] = [
        ("border_color", "u32"),
        ("border_width", "i32"),
        ("border_attr", "u32"),
        #: x1, y1, x2, y2, … 순서로 번갈아 놓인다.
        ("corners", "i32*8"),
        ("crop_left", "i32"),
        ("crop_top", "i32"),
        ("crop_right", "i32"),
        ("crop_bottom", "i32"),
        #: 왼, 오른, 위, 아래.
        ("inner_margins", "i16*4"),
        ("brightness", "i8"),
        ("contrast", "i8"),
        ("effect", "u8"),
        ("bin_data_id", "u16"),
        ("border_alpha", "u8"),
        ("instance_id", "u32"),
        ("effect_flags", "u32"),
    ]

    @property
    def corner_points(self) -> list[Point]:
        return list(zip(self.corners[0::2], self.corners[1::2], strict=True))


@register(tags.SHAPE_COMPONENT_CONTAINER)
@dataclass
class ShapeContainer(RecordModel):
    """묶음 개체. 명세 4.3.9.7 형식이고 코퍼스에는 없다."""

    child_ctrl_ids: list[int] | None = None

    def decode_fields(self, reader: Reader) -> None:
        start = reader.offset
        try:
            count = reader.read("u16")
            self.child_ctrl_ids = [reader.read("u32") for _ in range(count)]
        except ValueError:
            reader.offset = start

    def encode_fields(self, writer: Writer) -> None:
        if self.child_ctrl_ids is not None:
            writer.write("u16", len(self.child_ctrl_ids))
            for child in self.child_ctrl_ids:
                writer.write("u32", child)


@register(tags.FORM_OBJECT)
@dataclass
class FormObject(RecordModel):
    """양식 개체. 속성 문자열은 'CommonSet:set:…' 꼴의 직렬화된 파라미터다."""

    FIELDS: ClassVar[list[FieldSpec]] = [
        # 검증 안 됨: 실물에서 'tbc+'(체크박스) 같은 종류 ID가 두 번 연달아 온다
        ("form_type", "u32"),
        ("form_type_repeat", "u32"),
        # 검증 안 됨: 실물에서 뒤 문자열 길이와 같은 값이다
        ("properties_length", "u32"),
        ("properties", "wstr"),
    ]

    @property
    def form_type_name(self) -> str:
        return tags.ctrl_name(self.form_type)


@register(tags.MEMO_LIST)
@dataclass
class MemoList(RecordModel):
    # 검증 안 됨: 실물 4B 레코드의 u32, 1부터 늘어나는 메모 번호로 보인다
    FIELDS: ClassVar[list[FieldSpec]] = [("memo_index", "u32")]


@register(tags.VIDEO_DATA)
@dataclass
class VideoData(RecordModel):
    # 검증 안 됨: 명세 4.3.9.8 동영상 타입 INT32, 코퍼스에 없다
    FIELDS: ClassVar[list[FieldSpec]] = [("video_type", "i32")]


@register(tags.SHAPE_COMPONENT_TEXTART)
@dataclass
class ShapeTextArt(RecordModel):
    """글맵시. 명세에 구조가 없고 코퍼스에도 없어 전부 tail이다."""


@register(tags.CHART_DATA)
@dataclass
class ChartData(RecordModel):
    """차트 데이터. 명세에 구조가 없고 코퍼스에도 없어 전부 tail이다."""


@register(tags.SHAPE_COMPONENT_UNKNOWN)
@dataclass
class ShapeUnknown(RecordModel):
    """명세에 구조가 없고 코퍼스에도 없어 전부 tail이다."""
