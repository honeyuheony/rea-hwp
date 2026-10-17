"""BodyText 컨트롤 레코드: CTRL_HEADER, LIST_HEADER, TABLE, CTRL_DATA, EQEDIT와
구역 정의 자식(PAGE_DEF, FOOTNOTE_SHAPE, PAGE_BORDER_FILL)."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, ClassVar

from .. import tags
from ..binio import Reader, Writer
from .base import FieldSpec, RecordModel
from .registry import DecodeContext, register

FORM_CTRL = tags.ctrl_id("form")
#: 개체 공통 속성(명세 표69)으로 시작하는 컨트롤.
OBJECT_CTRLS = frozenset({tags.TABLE_CTRL, tags.GSO_CTRL, tags.EQUATION_CTRL, FORM_CTRL})


def bits(value: int, start: int, width: int = 1) -> int:
    return (value >> start) & ((1 << width) - 1)


def payload_ctrl_id(payload: bytes) -> int:
    return struct.unpack_from("<I", payload, 0)[0] if len(payload) >= 4 else 0


def parent_ctrl_id(context: DecodeContext) -> int | None:
    """부모 레코드가 CTRL_HEADER면 그 컨트롤 ID, 아니면 None."""
    if not context.ancestors:
        return None
    parent = context.ancestors[-1]
    if parent.tag != tags.CTRL_HEADER:
        return None
    return payload_ctrl_id(parent.payload)


def parent_tag(context: DecodeContext) -> int | None:
    return context.ancestors[-1].tag if context.ancestors else None


def _when_ctrl(*ctrl_ids: int):
    wanted = frozenset(ctrl_ids)
    return lambda context, payload: payload_ctrl_id(payload) in wanted


# ---------------------------------------------------------------------------
# CTRL_HEADER
# ---------------------------------------------------------------------------


@dataclass
class CtrlHeader(RecordModel):
    """컨트롤 ID만 해석한다. 종류별 모델이 없는 컨트롤(bokm, tcmt 등)이 이것으로 남는다."""

    FIELDS: ClassVar[list[FieldSpec]] = [("ctrl_id", "u32")]

    @property
    def ctrl_name(self) -> str:
        return tags.ctrl_name(self.ctrl_id)


class VertRelTo(IntEnum):
    PAPER = 0
    PAGE = 1
    PARA = 2


class HorzRelTo(IntEnum):
    PAPER = 0
    PAGE = 1
    COLUMN = 2
    PARA = 3


class ObjectAlign(IntEnum):
    """기준이 paper·page면 TOP/CENTER/BOTTOM, para면 LEFT(0)/RIGHT(2)로 읽는다."""

    TOP = 0
    CENTER = 1
    BOTTOM = 2
    INSIDE = 3
    OUTSIDE = 4


class TextWrap(IntEnum):
    # 명세 표70은 Square·Tight·Through·TopAndBottom… 순서지만 실물은 아래 순서다(hwplib·HWPX 대조).
    SQUARE = 0
    TOP_AND_BOTTOM = 1
    BEHIND_TEXT = 2
    IN_FRONT_OF_TEXT = 3


class TextFlow(IntEnum):
    BOTH_SIDES = 0
    LEFT_ONLY = 1
    RIGHT_ONLY = 2
    LARGEST_ONLY = 3


def _enum(kind: type[IntEnum], value: int) -> IntEnum | int:
    try:
        return kind(value)
    except ValueError:
        return value


OBJECT_COMMON_FIELDS: list[FieldSpec] = [
    ("ctrl_id", "u32"),
    ("attr", "u32"),
    ("vert_offset", "i32"),
    ("horz_offset", "i32"),
    ("width", "u32"),
    ("height", "u32"),
    ("z_order", "i32"),
    #: 왼, 오른, 위, 아래.
    ("outer_margins", "i16*4"),
    ("instance_id", "u32"),
    ("prevent_page_break", "i32"),
    ("description", "wstr"),
]


@register(tags.CTRL_HEADER, when=_when_ctrl(*OBJECT_CTRLS))
@dataclass
class ObjectCtrlHeader(CtrlHeader):
    """표·그리기 개체·수식·양식 개체의 개체 공통 속성(명세 표69). 설명문 뒤 남는 0~2바이트는 tail."""

    FIELDS: ClassVar[list[FieldSpec]] = OBJECT_COMMON_FIELDS

    @property
    def treat_as_char(self) -> bool:
        return bool(bits(self.attr, 0))

    @property
    def affect_line_spacing(self) -> bool:
        return bool(bits(self.attr, 2))

    @property
    def vert_rel_to(self) -> VertRelTo | int:
        return _enum(VertRelTo, bits(self.attr, 3, 2))

    @property
    def vert_align(self) -> ObjectAlign | int:
        return _enum(ObjectAlign, bits(self.attr, 5, 3))

    @property
    def horz_rel_to(self) -> HorzRelTo | int:
        return _enum(HorzRelTo, bits(self.attr, 8, 2))

    @property
    def horz_align(self) -> ObjectAlign | int:
        return _enum(ObjectAlign, bits(self.attr, 10, 3))

    @property
    def flow_with_text(self) -> bool:
        """세로 기준이 para일 때 본문 영역 안으로 제한하는지."""
        return bool(bits(self.attr, 13))

    @property
    def allow_overlap(self) -> bool:
        return bool(bits(self.attr, 14))

    @property
    def width_rel_to(self) -> int:
        """0 paper, 1 page, 2 column, 3 para, 4 absolute."""
        return bits(self.attr, 15, 3)

    @property
    def height_rel_to(self) -> int:
        """0 paper, 1 page, 2 absolute."""
        return bits(self.attr, 18, 2)

    @property
    def protect_size(self) -> bool:
        return bool(bits(self.attr, 20))

    @property
    def text_wrap(self) -> TextWrap | int:
        return _enum(TextWrap, bits(self.attr, 21, 3))

    @property
    def text_flow(self) -> TextFlow | int:
        return _enum(TextFlow, bits(self.attr, 24, 2))

    @property
    def numbering_kind(self) -> int:
        """0 없음, 1 그림, 2 표, 3 수식."""
        return bits(self.attr, 26, 3)


@register(tags.CTRL_HEADER, when=_when_ctrl(tags.SECTION_DEF_CTRL))
@dataclass
class SectionDefCtrl(CtrlHeader):
    FIELDS: ClassVar[list[FieldSpec]] = [
        ("ctrl_id", "u32"),
        ("attr", "u32"),
        ("column_gap", "i16"),
        ("vert_grid", "u16"),
        ("horz_grid", "u16"),
        ("default_tab", "u32"),
        ("numbering_para_shape_id", "u16"),
        ("page_start", "u16"),
        ("figure_start", "u16"),
        ("table_start", "u16"),
        ("equation_start", "u16"),
        # 검증 안 됨: pyhwp section_def.py unknown1·unknown2(u32 둘), 실물은 대개 0이고 작은 정수가 섞인다
        ("reserved", "bytes:8"),
        # 검증 안 됨: 명세 4.3.10.1 대표 Language(5.0.1.5 이상)
        ("language", "u16"),
    ]

    @property
    def hide_first_header(self) -> bool:
        return bool(bits(self.attr, 0))

    @property
    def hide_first_footer(self) -> bool:
        return bool(bits(self.attr, 1))

    @property
    def hide_first_master_page(self) -> bool:
        return bool(bits(self.attr, 2))

    @property
    def hide_first_border(self) -> bool:
        return bool(bits(self.attr, 3))

    @property
    def hide_first_fill(self) -> bool:
        return bool(bits(self.attr, 4))

    @property
    def hide_first_page_number(self) -> bool:
        return bool(bits(self.attr, 5))

    @property
    def border_first_page_only(self) -> bool:
        return bool(bits(self.attr, 8))

    @property
    def fill_first_page_only(self) -> bool:
        return bool(bits(self.attr, 9))

    @property
    def text_direction(self) -> int:
        """0 가로, 1 세로."""
        return bits(self.attr, 16, 3)

    @property
    def hide_empty_line(self) -> bool:
        return bool(bits(self.attr, 19))


@register(tags.CTRL_HEADER, when=_when_ctrl(tags.COLUMN_DEF_CTRL))
@dataclass
class ColumnDefCtrl(CtrlHeader):
    """단 정의. 너비가 다르면 [너비, 간격, …, 너비] 2n-1개가 오고, 값은 합이 32768인 비율이다."""

    def decode_fields(self, reader: Reader) -> None:
        if not self.read_spec(reader, [("ctrl_id", "u32"), ("attr", "u16"), ("gap", "i16")]):
            self._clear_rest()
            return
        spec: list[FieldSpec] = [("width_gaps", f"u16*{self._array_len()}")]
        spec += [("attr2", "u16"), ("line_type", "u8"), ("line_width", "u8"), ("line_color", "u32")]
        self.read_spec(reader, spec)

    def _clear_rest(self) -> None:
        for name in ("width_gaps", "attr2", "line_type", "line_width", "line_color"):
            self.values.setdefault(name, None)

    def _array_len(self) -> int:
        return 0 if self.same_width or self.count == 0 else 2 * self.count - 1

    def encode_fields(self, writer: Writer) -> None:
        spec: list[FieldSpec] = [("ctrl_id", "u32"), ("attr", "u16"), ("gap", "i16")]
        if self.values.get("width_gaps") is not None:
            spec.append(("width_gaps", f"u16*{len(self.width_gaps)}"))
        spec += [("attr2", "u16"), ("line_type", "u8"), ("line_width", "u8"), ("line_color", "u32")]
        self.write_spec(writer, spec)

    @property
    def kind(self) -> int:
        """0 일반, 1 배분, 2 평행."""
        return bits(self.attr, 0, 2)

    @property
    def count(self) -> int:
        return bits(self.attr, 2, 8)

    @property
    def direction(self) -> int:
        """0 왼쪽부터, 1 오른쪽부터, 2 맞쪽."""
        return bits(self.attr, 10, 2)

    @property
    def same_width(self) -> bool:
        return bool(bits(self.attr, 12))

    @property
    def widths(self) -> list[int]:
        """단마다의 너비 비율(합 32768 기준). 같은 너비면 빈 목록."""
        return list(self.width_gaps[0::2]) if self.width_gaps else []

    @property
    def gaps(self) -> list[int]:
        """단 사이 간격 비율. 같은 너비면 빈 목록이고 gap을 쓴다."""
        return list(self.width_gaps[1::2]) if self.width_gaps else []


@register(tags.CTRL_HEADER, when=_when_ctrl(tags.HEADER_CTRL, tags.FOOTER_CTRL))
@dataclass
class HeaderFooterCtrl(CtrlHeader):
    FIELDS: ClassVar[list[FieldSpec]] = [
        ("ctrl_id", "u32"),
        ("attr", "u32"),
        # 검증 안 됨: 명세에 없는 u32, 실물에서 0~67 사이의 작은 정수다(일련번호로 보인다)
        ("unknown", "u32"),
    ]

    @property
    def apply_page(self) -> int:
        """0 양쪽, 1 짝수 쪽, 2 홀수 쪽."""
        return bits(self.attr, 0, 2)


@register(tags.CTRL_HEADER, when=_when_ctrl(tags.FOOTNOTE_CTRL, tags.ENDNOTE_CTRL))
@dataclass
class NoteCtrl(CtrlHeader):
    # 명세는 8바이트 예약이라 적었지만 실물은 번호 u32 뒤에 장식 문자·번호 모양이 온다(hwplib·rhwp 대조).
    FIELDS: ClassVar[list[FieldSpec]] = [
        ("ctrl_id", "u32"),
        ("number", "u32"),
        ("prefix_char", "u16"),
        ("suffix_char", "u16"),
        ("number_shape", "u32"),
        ("instance_id", "u32"),
    ]


class AutoNumberKind(IntEnum):
    PAGE = 0
    FOOTNOTE = 1
    ENDNOTE = 2
    FIGURE = 3
    TABLE = 4
    EQUATION = 5


class _NumberingAttr:
    attr: int

    @property
    def kind(self) -> AutoNumberKind | int:
        return _enum(AutoNumberKind, bits(self.attr, 0, 4))

    @property
    def number_shape(self) -> int:
        return bits(self.attr, 4, 8)

    @property
    def superscript(self) -> bool:
        return bool(bits(self.attr, 12))


@register(tags.CTRL_HEADER, when=_when_ctrl(tags.AUTO_NUMBER_CTRL))
@dataclass
class AutoNumberCtrl(_NumberingAttr, CtrlHeader):
    FIELDS: ClassVar[list[FieldSpec]] = [
        ("ctrl_id", "u32"),
        ("attr", "u32"),
        ("number", "u16"),
        ("user_symbol", "u16"),
        ("prefix_char", "u16"),
        ("suffix_char", "u16"),
    ]


@register(tags.CTRL_HEADER, when=_when_ctrl(tags.NEW_NUMBER_CTRL))
@dataclass
class NewNumberCtrl(_NumberingAttr, CtrlHeader):
    FIELDS: ClassVar[list[FieldSpec]] = [("ctrl_id", "u32"), ("attr", "u32"), ("number", "u16")]


@register(tags.CTRL_HEADER, when=_when_ctrl(tags.PAGE_HIDE_CTRL))
@dataclass
class PageHideCtrl(CtrlHeader):
    """attr bit0 머리말, 1 꼬리말, 2 바탕쪽, 3 테두리, 4 배경, 5 쪽 번호를 감춘다."""

    FIELDS: ClassVar[list[FieldSpec]] = [("ctrl_id", "u32"), ("attr", "u32")]


@register(tags.CTRL_HEADER, when=_when_ctrl(tags.PAGE_ODD_EVEN_CTRL))
@dataclass
class PageOddEvenCtrl(CtrlHeader):
    FIELDS: ClassVar[list[FieldSpec]] = [("ctrl_id", "u32"), ("attr", "u32")]

    @property
    def apply_page(self) -> int:
        """0 양쪽, 1 짝수 쪽, 2 홀수 쪽."""
        return bits(self.attr, 0, 2)


@register(tags.CTRL_HEADER, when=_when_ctrl(tags.PAGE_NUMBER_POS_CTRL))
@dataclass
class PageNumberPosCtrl(CtrlHeader):
    FIELDS: ClassVar[list[FieldSpec]] = [
        ("ctrl_id", "u32"),
        ("attr", "u32"),
        ("user_symbol", "u16"),
        ("prefix_char", "u16"),
        ("suffix_char", "u16"),
        ("dash_char", "u16"),
    ]

    @property
    def number_shape(self) -> int:
        return bits(self.attr, 0, 8)

    @property
    def position(self) -> int:
        """0 없음, 1~3 위 왼·가운데·오른, 4~6 아래 왼·가운데·오른, 7·8 바깥 위·아래, 9·10 안쪽 위·아래."""
        return bits(self.attr, 8, 4)


@register(tags.CTRL_HEADER, when=_when_ctrl(tags.INDEX_MARK_CTRL))
@dataclass
class IndexMarkCtrl(CtrlHeader):
    # 둘째 키워드가 비면 실물에 4바이트가 더 붙는다(tail).
    FIELDS: ClassVar[list[FieldSpec]] = [("ctrl_id", "u32"), ("keyword1", "wstr"), ("keyword2", "wstr")]


@register(tags.CTRL_HEADER, when=_when_ctrl(tags.OVERLAP_CTRL))
@dataclass
class OverlapCtrl(CtrlHeader):
    """글자 겹침. 실물에는 겹칠 글자만 있고 뒤가 없는 레코드도 있다."""

    def decode_fields(self, reader: Reader) -> None:
        head: list[FieldSpec] = [
            ("ctrl_id", "u32"),
            ("text", "wstr"),
            ("border_type", "u8"),
            ("inner_char_size", "i8"),
            ("expansion", "u8"),
            ("char_shape_count", "u8"),
        ]
        if self.read_spec(reader, head):
            self.read_spec(reader, [("char_shape_ids", f"u32*{self.char_shape_count}")])
        else:
            self.values["char_shape_ids"] = None

    def encode_fields(self, writer: Writer) -> None:
        spec: list[FieldSpec] = [
            ("ctrl_id", "u32"),
            ("text", "wstr"),
            ("border_type", "u8"),
            ("inner_char_size", "i8"),
            ("expansion", "u8"),
            ("char_shape_count", "u8"),
        ]
        if self.values.get("char_shape_ids") is not None:
            spec.append(("char_shape_ids", f"u32*{len(self.char_shape_ids)}"))
        self.write_spec(writer, spec)


@register(tags.CTRL_HEADER, when=_when_ctrl(tags.DUTMAL_CTRL))
@dataclass
class DutmalCtrl(CtrlHeader):
    FIELDS: ClassVar[list[FieldSpec]] = [
        ("ctrl_id", "u32"),
        ("main_text", "wstr"),
        ("sub_text", "wstr"),
        ("position", "u32"),
        ("size_ratio", "u32"),
        ("option", "u32"),
        ("style_id", "u32"),
        ("align", "u32"),
    ]


def _is_field(context: DecodeContext, payload: bytes) -> bool:
    return bits(payload_ctrl_id(payload), 24, 8) == ord("%")


@register(tags.CTRL_HEADER, when=_is_field)
@dataclass
class FieldCtrl(CtrlHeader):
    """'%'로 시작하는 필드 시작 컨트롤(누름틀·하이퍼링크·책갈피 필드 등)."""

    FIELDS: ClassVar[list[FieldSpec]] = [
        ("ctrl_id", "u32"),
        ("attr", "u32"),
        ("extra_attr", "u8"),
        ("command", "wstr"),
        ("field_id", "u32"),
        # 명세에 없음: 실물에 4바이트 더 있다. rhwp는 메모 번호로 읽는다.
        ("memo_index", "u32"),
    ]

    @property
    def editable_in_read_only(self) -> bool:
        return bool(bits(self.attr, 0))


# ---------------------------------------------------------------------------
# LIST_HEADER
# ---------------------------------------------------------------------------

#: 명세 표65는 문단 수 INT16 + 속성 UINT32(6B)지만 실물은 그 사이에 2바이트가 더 있다.
LIST_HEADER_FIELDS: list[FieldSpec] = [("para_count", "i16"), ("unknown", "u16"), ("attr", "u32")]


@dataclass
class ListHeader(RecordModel):
    """문단 리스트 헤더. 각주·미주·숨은 설명·메모처럼 따로 해석할 필드가 없는 곳에 쓴다."""

    FIELDS: ClassVar[list[FieldSpec]] = LIST_HEADER_FIELDS

    @property
    def text_direction(self) -> int:
        """0 가로, 1 세로."""
        return bits(self.attr, 0, 3)

    @property
    def line_wrap(self) -> int:
        """0 일반 줄바꿈, 1 자간을 조정해 한 줄 유지, 2 내용에 따라 폭이 늘어남."""
        return bits(self.attr, 3, 2)

    @property
    def vert_align(self) -> int:
        """0 위, 1 가운데, 2 아래."""
        return bits(self.attr, 5, 2)


def _is_cell(context: DecodeContext, payload: bytes) -> bool:
    return parent_ctrl_id(context) == tags.TABLE_CTRL and tags.TABLE in context.preceding_sibling_tags


@register(tags.LIST_HEADER, when=_is_cell)
@dataclass
class CellListHeader(ListHeader):
    """표 셀. text_width 뒤의 셀 필드 이름(파라미터 셋)은 tail로 남긴다."""

    FIELDS: ClassVar[list[FieldSpec]] = LIST_HEADER_FIELDS + [
        ("col", "u16"),
        ("row", "u16"),
        ("col_span", "u16"),
        ("row_span", "u16"),
        ("width", "u32"),
        ("height", "u32"),
        #: 왼, 오른, 위, 아래.
        ("margins", "u16*4"),
        ("border_fill_id", "u16"),
        # 명세에 없음: 실물 셀 99.8%에서 width와 같다(hwplib textWidth).
        ("text_width", "u32"),
    ]

    @property
    def is_header(self) -> bool:
        # 검증 안 됨: kordoc hwp5-scene.ts가 attr bit18을 제목 셀로 읽는다
        return bool(bits(self.attr, 18))


def _is_caption(context: DecodeContext, payload: bytes) -> bool:
    if parent_ctrl_id(context) not in OBJECT_CTRLS:
        return False
    return not {tags.TABLE, tags.SHAPE_COMPONENT, tags.EQEDIT} & set(context.preceding_sibling_tags)


@register(tags.LIST_HEADER, when=_is_caption)
@dataclass
class CaptionListHeader(ListHeader):
    """표·개체의 캡션(명세 표72). 실물은 최대 폭 뒤에 0바이트 8개가 더 붙기도 한다(tail)."""

    FIELDS: ClassVar[list[FieldSpec]] = LIST_HEADER_FIELDS + [
        ("caption_attr", "u32"),
        ("caption_width", "u32"),
        ("caption_gap", "i16"),
        ("max_width", "u32"),
    ]

    @property
    def direction(self) -> int:
        """0 왼쪽, 1 오른쪽, 2 위, 3 아래."""
        return bits(self.caption_attr, 0, 2)

    @property
    def include_margin(self) -> bool:
        return bool(bits(self.caption_attr, 2))


@register(tags.LIST_HEADER, when=lambda context, payload: parent_tag(context) == tags.SHAPE_COMPONENT)
@dataclass
class TextBoxListHeader(ListHeader):
    """글상자. 최대 폭 뒤에 예약 12바이트와 필드 이름 파라미터 셋이 오기도 한다(tail)."""

    FIELDS: ClassVar[list[FieldSpec]] = LIST_HEADER_FIELDS + [
        #: 왼, 오른, 위, 아래.
        ("margins", "i16*4"),
        ("max_width", "u32"),
    ]


_HEADER_FOOTER_LIST_FIELDS: list[FieldSpec] = LIST_HEADER_FIELDS + [
    ("text_width", "u32"),
    ("text_height", "u32"),
    ("text_ref", "u8"),
    ("number_ref", "u8"),
]


@register(
    tags.LIST_HEADER,
    when=lambda context, payload: parent_ctrl_id(context) in (tags.HEADER_CTRL, tags.FOOTER_CTRL),
)
@dataclass
class HeaderFooterListHeader(ListHeader):
    FIELDS: ClassVar[list[FieldSpec]] = _HEADER_FOOTER_LIST_FIELDS


@register(tags.LIST_HEADER, when=lambda context, payload: parent_ctrl_id(context) == tags.SECTION_DEF_CTRL)
@dataclass
class MasterPageListHeader(ListHeader):
    """구역 정의 아래 바탕쪽 리스트(명세 4.3.10.1 바탕쪽 정보)."""

    FIELDS: ClassVar[list[FieldSpec]] = _HEADER_FOOTER_LIST_FIELDS


register(tags.LIST_HEADER)(ListHeader)


# ---------------------------------------------------------------------------
# 구역 정의 자식: PAGE_DEF, FOOTNOTE_SHAPE, PAGE_BORDER_FILL
# ---------------------------------------------------------------------------


@register(tags.PAGE_DEF)
@dataclass
class PageDef(RecordModel):
    FIELDS: ClassVar[list[FieldSpec]] = [
        ("paper_width", "u32"),
        ("paper_height", "u32"),
        ("margin_left", "u32"),
        ("margin_right", "u32"),
        ("margin_top", "u32"),
        ("margin_bottom", "u32"),
        ("margin_header", "u32"),
        ("margin_footer", "u32"),
        ("margin_gutter", "u32"),
        ("attr", "u32"),
    ]

    @property
    def landscape(self) -> bool:
        return bool(bits(self.attr, 0))

    @property
    def gutter_type(self) -> int:
        """0 한쪽 편집, 1 맞쪽 편집, 2 위로 넘기기."""
        return bits(self.attr, 1, 2)


@register(tags.FOOTNOTE_SHAPE)
@dataclass
class FootnoteShape(RecordModel):
    """각주/미주 모양. 구역 정의 아래 첫째가 각주, 둘째가 미주다."""

    FIELDS: ClassVar[list[FieldSpec]] = [
        ("attr", "u32"),
        ("user_symbol", "u16"),
        ("prefix_char", "u16"),
        ("suffix_char", "u16"),
        ("start_number", "u16"),
        # 명세는 HWPUNIT16이지만 실물은 4바이트다(-1 같은 음수가 흔하다). 검증 안 됨: 하위 2바이트가 길이로 보인다
        ("divider_length", "i32"),
        ("divider_margin_top", "u16"),
        ("divider_margin_bottom", "u16"),
        ("note_spacing", "u16"),
        ("divider_line_type", "u8"),
        ("divider_line_width", "u8"),
        ("divider_line_color", "u32"),
    ]

    @property
    def number_shape(self) -> int:
        return bits(self.attr, 0, 8)

    @property
    def placement(self) -> int:
        """각주: 0 단마다 따로, 1 통단, 2 오른쪽 단. 미주: 0 문서 끝, 1 구역 끝."""
        return bits(self.attr, 8, 2)

    @property
    def numbering(self) -> int:
        """0 앞 구역에 이어서, 1 현재 구역부터 새로, 2 쪽마다 새로(각주만)."""
        return bits(self.attr, 10, 2)

    @property
    def superscript(self) -> bool:
        return bool(bits(self.attr, 12))

    @property
    def below_text(self) -> bool:
        return bool(bits(self.attr, 13))


@register(tags.PAGE_BORDER_FILL)
@dataclass
class PageBorderFill(RecordModel):
    """쪽 테두리/배경. 구역 정의 아래 양쪽·짝수·홀수 순으로 셋이 온다."""

    FIELDS: ClassVar[list[FieldSpec]] = [
        ("attr", "u32"),
        #: 왼, 오른, 위, 아래.
        ("margins", "i16*4"),
        ("border_fill_id", "u16"),
    ]

    @property
    def relative_to_paper(self) -> bool:
        """False면 본문 기준."""
        return bool(bits(self.attr, 0))

    @property
    def include_header(self) -> bool:
        return bool(bits(self.attr, 1))

    @property
    def include_footer(self) -> bool:
        return bool(bits(self.attr, 2))

    @property
    def fill_area(self) -> int:
        """0 종이, 1 쪽, 2 테두리."""
        return bits(self.attr, 3, 2)


# ---------------------------------------------------------------------------
# TABLE
# ---------------------------------------------------------------------------


@dataclass
class CellZone:
    start_col: int
    start_row: int
    end_col: int
    end_row: int
    border_fill_id: int


@register(tags.TABLE)
@dataclass
class Table(RecordModel):
    """표 개체 속성. 영역 속성 뒤에 2바이트가 남는 실물이 있다(tail)."""

    zones: list[CellZone] | None = None

    _HEAD: ClassVar[list[FieldSpec]] = [
        ("attr", "u32"),
        ("rows", "u16"),
        ("cols", "u16"),
        ("cell_spacing", "u16"),
        #: 왼, 오른, 위, 아래.
        ("margins", "u16*4"),
    ]

    def decode_fields(self, reader: Reader) -> None:
        rest_names = ("row_cell_counts", "border_fill_id", "zone_count")
        if not self.read_spec(reader, self._HEAD):
            self.values.update(dict.fromkeys(rest_names))
            return
        rest = [("row_cell_counts", f"u16*{self.rows}"), ("border_fill_id", "u16"), ("zone_count", "u16")]
        if not self.read_spec(reader, rest):
            return
        zones = []
        for _ in range(self.zone_count):
            if reader.remaining < 10:
                break
            zones.append(CellZone(*(reader.read("u16") for _ in range(5))))
        if len(zones) < self.zone_count:
            # 영역이 잘렸으면 개수부터 tail로 돌린다.
            reader.offset -= 2 + 10 * len(zones)
            self.values["zone_count"] = None
            return
        self.zones = zones

    def encode_fields(self, writer: Writer) -> None:
        spec = [
            *self._HEAD,
            ("row_cell_counts", f"u16*{len(self.values.get('row_cell_counts') or [])}"),
            ("border_fill_id", "u16"),
            ("zone_count", "u16"),
        ]
        if not self.write_spec(writer, spec):
            return
        for zone in self.zones or []:
            for value in (zone.start_col, zone.start_row, zone.end_col, zone.end_row, zone.border_fill_id):
                writer.write("u16", value)

    @property
    def page_break(self) -> int:
        """0 나누지 않음, 1 셀 단위로 나눔, 2 나누되 셀은 나누지 않음."""
        return bits(self.attr, 0, 2)

    @property
    def repeat_header(self) -> bool:
        return bool(bits(self.attr, 2))


# ---------------------------------------------------------------------------
# CTRL_DATA (파라미터 셋)
# ---------------------------------------------------------------------------

PIT_NULL = 0
PIT_BSTR = 1
PIT_SET = 0x8000
PIT_ARRAY = 0x8001
PIT_BINDATA = 0x8002
#: PIT_I1~PIT_UI(2~9). 명세 표52의 자료형 열대로 모두 4바이트다(2·5·6·9는 실물 확인).
_PIT_INTEGERS = {2: "i32", 3: "i32", 4: "i32", 5: "i32", 6: "u32", 7: "u32", 8: "u32", 9: "u32"}


@dataclass
class ParameterItem:
    item_id: int
    kind: int
    value: Any


@dataclass
class ParameterSet:
    set_id: int
    items: list[ParameterItem] = field(default_factory=list)

    def find(self, item_id: int) -> ParameterItem | None:
        return next((item for item in self.items if item.item_id == item_id), None)


def read_parameter_set(reader: Reader, depth: int = 0) -> ParameterSet:
    """명세 표50. 아이템 개수는 명세의 INT16이 아니라 실물대로 4바이트로 읽는다."""
    if depth > 16:
        raise ValueError("parameter set nested too deep")
    set_id = reader.read("u16")
    count = reader.read("i32")
    params = ParameterSet(set_id)
    for _ in range(count):
        item_id = reader.read("u16")
        kind = reader.read("u16")
        params.items.append(ParameterItem(item_id, kind, _read_parameter_value(reader, kind, depth)))
    return params


def _read_parameter_value(reader: Reader, kind: int, depth: int) -> Any:
    if kind == PIT_BSTR:
        return reader.wstr()
    if kind == PIT_NULL:
        # 검증 안 됨: 명세 표52가 PIT_NULL 자료형을 UINT로 적었다
        return reader.read("u32")
    if kind in _PIT_INTEGERS:
        return reader.read(_PIT_INTEGERS[kind])
    if kind == PIT_SET:
        return read_parameter_set(reader, depth + 1)
    if kind == PIT_ARRAY:
        # 명세는 INT16 개수 + 파라미터 셋 배열이지만 실물은 4바이트 개수 + (종류, 값) 배열이다.
        elements = []
        for _ in range(reader.read("i32")):
            element_kind = reader.read("u16")
            elements.append((element_kind, _read_parameter_value(reader, element_kind, depth + 1)))
        return elements
    if kind == PIT_BINDATA:
        return reader.read("u16")
    raise ValueError(f"unknown parameter item kind {kind:#x}")


def write_parameter_set(writer: Writer, params: ParameterSet) -> None:
    writer.write("u16", params.set_id)
    writer.write("i32", len(params.items))
    for item in params.items:
        writer.write("u16", item.item_id)
        writer.write("u16", item.kind)
        _write_parameter_value(writer, item.kind, item.value)


def _write_parameter_value(writer: Writer, kind: int, value: Any) -> None:
    if kind == PIT_BSTR:
        writer.wstr(value)
    elif kind == PIT_NULL:
        writer.write("u32", value)
    elif kind in _PIT_INTEGERS:
        writer.write(_PIT_INTEGERS[kind], value)
    elif kind == PIT_SET:
        write_parameter_set(writer, value)
    elif kind == PIT_ARRAY:
        writer.write("i32", len(value))
        for element_kind, element in value:
            writer.write("u16", element_kind)
            _write_parameter_value(writer, element_kind, element)
    elif kind == PIT_BINDATA:
        writer.write("u16", value)


#: 누름틀·책갈피 이름을 담는 파라미터 셋 ID와 이름 아이템 ID(실물 확인).
NAME_PARAMETER_SET = 0x021B
NAME_PARAMETER_ITEM = 0x4000


@register(tags.CTRL_DATA)
@dataclass
class CtrlData(RecordModel):
    """컨트롤 임의 데이터(명세 표66). 파라미터 셋으로 읽지 못하면 전부 tail로 남긴다."""

    params: ParameterSet | None = None

    def decode_fields(self, reader: Reader) -> None:
        start = reader.offset
        try:
            self.params = read_parameter_set(reader)
        except ValueError:
            reader.offset = start
            self.params = None

    def encode_fields(self, writer: Writer) -> None:
        if self.params is not None:
            write_parameter_set(writer, self.params)

    @property
    def name(self) -> str | None:
        """책갈피·누름틀 이름."""
        if self.params is None or self.params.set_id != NAME_PARAMETER_SET:
            return None
        item = self.params.find(NAME_PARAMETER_ITEM)
        return item.value if item is not None and item.kind == PIT_BSTR else None


# ---------------------------------------------------------------------------
# EQEDIT
# ---------------------------------------------------------------------------


@register(tags.EQEDIT)
@dataclass
class EqEdit(RecordModel):
    FIELDS: ClassVar[list[FieldSpec]] = [
        ("attr", "u32"),
        ("script", "wstr"),
        ("font_size", "u32"),
        ("color", "u32"),
        ("baseline", "i16"),
        # 명세 표105에 없음: 실물은 기준선 뒤에 2바이트가 더 있고 그 뒤에 버전·글꼴 문자열이 온다
        ("unknown", "u16"),
        ("version", "wstr"),
        ("font_name", "wstr"),
    ]

    @property
    def line_mode(self) -> bool:
        """True면 줄 단위, False면 글자 단위."""
        return bool(bits(self.attr, 0))


register(tags.CTRL_HEADER)(CtrlHeader)
