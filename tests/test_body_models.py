"""BodyText 컨트롤·도형 모델: 합성 바이트 왕복과 주요 필드, 코퍼스 표 셀 주소."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from rea_hwp import models, tags
from rea_hwp.document import HwpDocument
from rea_hwp.models.controls import (
    CaptionListHeader,
    CellListHeader,
    ColumnDefCtrl,
    CtrlData,
    EqEdit,
    FieldCtrl,
    HorzRelTo,
    ListHeader,
    ObjectAlign,
    ObjectCtrlHeader,
    PageDef,
    SectionDefCtrl,
    Table,
    TextBoxListHeader,
    TextWrap,
    VertRelTo,
)
from rea_hwp.models.registry import DecodeContext, decode_record
from rea_hwp.models.shapes import GsoShapeComponent, ShapeComponent, ShapePicture, ShapePolygon
from rea_hwp.record import Record

models.load_all()

CORPUS = Path.home() / "tools" / "hwp-corpus"
TABLE_FIXTURE = CORPUS / "oss" / "pyhwp" / "tests__hwp5_tests__fixtures__table.hwp"


def wstr(text: str) -> bytes:
    return struct.pack("<H", len(text)) + text.encode("utf-16-le")


def ctrl_bytes(name: str) -> bytes:
    return struct.pack("<I", tags.ctrl_id(name))


def decode(tag: int, payload: bytes, ancestors=(), preceding=()):
    model = decode_record(Record(tag, 0, payload), DecodeContext(tuple(ancestors), 0, tuple(preceding)))
    assert model.encode() == payload
    return model


def object_common(name: str, attr: int) -> bytes:
    return (
        ctrl_bytes(name)
        + struct.pack("<IiiIIi", attr, -100, 200, 3000, 4000, 7)
        + struct.pack("<4h", 10, 20, 30, 40)
        + struct.pack("<Ii", 0xABCD, 1)
        + wstr("표입니다")
    )


def test_object_ctrl_header_fields_and_attr_bits():
    # 글자처럼, 세로 para·center, 가로 column·right, 글 뒤로, 오른쪽에만 글 배치
    attr = 1 | (2 << 3) | (1 << 5) | (2 << 8) | (2 << 10) | (2 << 21) | (2 << 24)
    payload = object_common("tbl ", attr) + b"\x00\x00"
    model = decode(tags.CTRL_HEADER, payload)
    assert isinstance(model, ObjectCtrlHeader)
    assert (model.vert_offset, model.horz_offset, model.width, model.height) == (-100, 200, 3000, 4000)
    assert model.outer_margins == [10, 20, 30, 40]
    assert model.description == "표입니다"
    assert model.tail == b"\x00\x00"
    assert model.treat_as_char is True
    assert model.vert_rel_to is VertRelTo.PARA
    assert model.vert_align is ObjectAlign.CENTER
    assert model.horz_rel_to is HorzRelTo.COLUMN
    assert model.horz_align is ObjectAlign.BOTTOM
    assert model.text_wrap is TextWrap.BEHIND_TEXT
    assert model.text_flow == 2


def test_object_ctrl_header_without_description_keeps_none():
    payload = object_common("gso ", 0)[:44]
    model = decode(tags.CTRL_HEADER, payload)
    assert isinstance(model, ObjectCtrlHeader)
    assert model.prevent_page_break == 1
    assert model.description is None


def test_section_def_without_language():
    secd = ctrl_bytes("secd") + struct.pack("<IhHHIHHHHH", 0x10003, 1134, 0, 0, 8000, 1, 5, 0, 0, 0) + bytes(8)
    model = decode(tags.CTRL_HEADER, secd)
    assert isinstance(model, SectionDefCtrl)
    assert (model.column_gap, model.default_tab, model.page_start) == (1134, 8000, 5)
    assert model.hide_first_header and model.hide_first_footer and model.text_direction == 1
    assert model.language is None


def test_column_def_same_and_different_widths():
    same = ctrl_bytes("cold") + struct.pack("<HhHBBI", (2 << 2) | (1 << 12), 2268, 0, 1, 1, 0x112233)
    model = decode(tags.CTRL_HEADER, same)
    assert isinstance(model, ColumnDefCtrl)
    assert (model.kind, model.count, model.same_width, model.gap) == (0, 2, True, 2268)
    assert model.widths == [] and model.line_color == 0x112233

    widths = [13722, 590, 18456]
    different = ctrl_bytes("cold") + struct.pack("<Hh3HHBBI", 2 << 2, 1417, *widths, 0, 0, 0, 0)
    model = decode(tags.CTRL_HEADER, different)
    assert (model.count, model.same_width) == (2, False)
    assert model.widths == [13722, 18456] and model.gaps == [590]


def test_field_ctrl():
    payload = ctrl_bytes("%hlk") + struct.pack("<IB", 1, 0) + wstr("http\\://a;1;0;0;") + struct.pack("<II", 77, 0)
    model = decode(tags.CTRL_HEADER, payload)
    assert isinstance(model, FieldCtrl)
    assert (model.command, model.field_id, model.memo_index) == ("http\\://a;1;0;0;", 77, 0)


def table_ctrl_record() -> Record:
    return Record(tags.CTRL_HEADER, 1, object_common("tbl ", 0))


def test_list_header_selection_by_parent_and_siblings():
    cell = struct.pack("<hHI", 1, 0, 1 << 5)
    cell += struct.pack("<4H2I4HHI", 2, 1, 3, 1, 5000, 900, 141, 141, 141, 141, 7, 5000)
    model = decode(tags.LIST_HEADER, cell + bytes(9), [table_ctrl_record()], [tags.TABLE])
    assert isinstance(model, CellListHeader)
    assert (model.col, model.row, model.col_span, model.row_span) == (2, 1, 3, 1)
    assert (model.width, model.height, model.border_fill_id, model.text_width) == (5000, 900, 7, 5000)
    assert model.margins == [141, 141, 141, 141] and model.vert_align == 1
    assert model.tail == bytes(9)

    caption = struct.pack("<hHI", 1, 0, 0) + struct.pack("<IIhI", 3, 8504, 850, 40000)
    model = decode(tags.LIST_HEADER, caption, [table_ctrl_record()], [])
    assert isinstance(model, CaptionListHeader)
    assert (model.direction, model.caption_width, model.caption_gap, model.max_width) == (3, 8504, 850, 40000)

    text_box = struct.pack("<hHI", 1, 0, 0) + struct.pack("<4hI", 283, 283, 141, 141, 9000)
    model = decode(tags.LIST_HEADER, text_box, [Record(tags.SHAPE_COMPONENT, 2, b"")], [])
    assert isinstance(model, TextBoxListHeader)
    assert model.margins == [283, 283, 141, 141] and model.max_width == 9000

    footnote = struct.pack("<hHI", 1, 0, 0) + bytes(8)
    model = decode(tags.LIST_HEADER, footnote, [Record(tags.CTRL_HEADER, 1, ctrl_bytes("fn  "))], [])
    assert type(model) is ListHeader and model.para_count == 1


def test_table_with_zones_and_page_def():
    zone = struct.pack("<5H", 0, 0, 1, 1, 9)
    payload = struct.pack("<IHHH4H", 0b110, 2, 3, 0, 1, 2, 3, 4) + struct.pack("<2H", 3, 2)
    payload += struct.pack("<HH", 4, 1) + zone
    model = decode(tags.TABLE, payload)
    assert isinstance(model, Table)
    assert (model.rows, model.cols, model.row_cell_counts, model.border_fill_id) == (2, 3, [3, 2], 4)
    assert model.zones[0].end_row == 1 and model.zones[0].border_fill_id == 9
    assert model.page_break == 2 and model.repeat_header is True

    page = struct.pack("<10I", 59528, 84188, 8504, 8504, 5668, 4252, 4252, 4252, 0, 0b101)
    model = decode(tags.PAGE_DEF, page)
    assert isinstance(model, PageDef)
    assert model.landscape is True and model.gutter_type == 2 and model.paper_height == 84188


def test_ctrl_data_parameter_set_with_nested_set_and_array():
    name = struct.pack("<HiHH", 0x021B, 1, 0x4000, 1) + wstr("셀이름")
    model = decode(tags.CTRL_DATA, name)
    assert isinstance(model, CtrlData) and model.name == "셀이름"

    array = struct.pack("<HHi", 0x4008, 0x8001, 2) + struct.pack("<HI", 5, 0xFF0000) + struct.pack("<HI", 5, 0xFFFFFF)
    nested = struct.pack("<Hi", 0x0266, 2) + struct.pack("<HHI", 0x4001, 9, 4) + array
    payload = struct.pack("<HiHH", 0x021B, 1, 0x0266, 0x8000) + nested
    model = decode(tags.CTRL_DATA, payload)
    inner = model.params.find(0x0266).value
    assert inner.find(0x4001).value == 4
    assert inner.find(0x4008).value == [(5, 0xFF0000), (5, 0xFFFFFF)]
    assert model.tail == b""


def test_eqedit():
    payload = struct.pack("<I", 1) + wstr("1 over 2") + struct.pack("<IIhH", 1000, 0, 72, 0)
    payload += wstr("Equation Version 60") + wstr("HYhwpEQ")
    model = decode(tags.EQEDIT, payload)
    assert isinstance(model, EqEdit)
    assert (model.script, model.font_size, model.baseline, model.font_name) == ("1 over 2", 1000, 72, "HYhwpEQ")
    assert model.line_mode is True


def matrix(*values: float) -> bytes:
    return struct.pack("<6d", *values)


def component_body(shape: str) -> bytes:
    return (
        ctrl_bytes(shape)
        + struct.pack("<iiHHIIIIIhii", 10, 20, 0, 1, 1000, 500, 2000, 1000, 1, 90, 500, 250)
        + struct.pack("<H", 1)
        + matrix(1, 0, 5, 0, 1, 6)
        + matrix(2, 0, 0, 0, 2, 0)
        + matrix(0, -1, 0, 1, 0, 0)
    )


def test_shape_component_top_level_and_nested():
    gso = Record(tags.CTRL_HEADER, 1, object_common("gso ", 0))
    payload = ctrl_bytes("gso ") + component_body("$rec") + struct.pack("<IiIB", 0xFF, 33, 0x41, 0) + bytes(30)
    model = decode(tags.SHAPE_COMPONENT, payload, [gso])
    assert isinstance(model, GsoShapeComponent)
    assert model.shape_name == "$rec"
    assert (model.offset_x, model.offset_y, model.current_width, model.current_height) == (10, 20, 2000, 1000)
    assert model.horz_flip is True and model.rotation == 90
    assert model.translation == (1, 0, 5, 0, 1, 6)
    assert model.scale_rotation == [((2, 0, 0, 0, 2, 0), (0, -1, 0, 1, 0, 0))]
    assert (model.line_color, model.line_width) == (0xFF, 33)
    assert len(model.tail) == 30

    container = Record(tags.SHAPE_COMPONENT, 2, b"")
    children = struct.pack("<HII", 2, tags.ctrl_id("$rec"), tags.ctrl_id("$pic"))
    model = decode(tags.SHAPE_COMPONENT, component_body("$con") + children, [gso, container])
    assert type(model) is ShapeComponent
    assert model.child_ctrl_ids == [tags.ctrl_id("$rec"), tags.ctrl_id("$pic")]


def test_picture_and_polygon():
    payload = struct.pack("<IiI", 0, 0, 0) + struct.pack("<8i", 0, 0, 100, 0, 100, 50, 0, 50)
    payload += struct.pack("<4i", 1, 2, 99, 48) + struct.pack("<4h", 0, 0, 0, 0)
    payload += struct.pack("<bbBHBII", -10, 20, 0, 3, 0, 0x1234, 0) + bytes(9)
    model = decode(tags.SHAPE_COMPONENT_PICTURE, payload)
    assert isinstance(model, ShapePicture)
    assert model.bin_data_id == 3
    assert (model.crop_left, model.crop_top, model.crop_right, model.crop_bottom) == (1, 2, 99, 48)
    assert model.corner_points == [(0, 0), (100, 0), (100, 50), (0, 50)]
    assert (model.brightness, model.contrast) == (-10, 20)

    polygon = struct.pack("<i6i", 3, 0, 0, 10, 0, 5, 8) + bytes(4)
    model = decode(tags.SHAPE_COMPONENT_POLYGON, polygon)
    assert isinstance(model, ShapePolygon)
    assert model.points == [(0, 0), (10, 0), (5, 8)] and model.tail == bytes(4)


@pytest.mark.skipif(not TABLE_FIXTURE.exists(), reason="코퍼스 폴더가 없다")
def test_corpus_table_cells_tile_the_grid():
    document = HwpDocument.open(TABLE_FIXTURE)
    table_ctrl = next(
        node
        for node in document.sections[0].walk()
        if node.tag == tags.CTRL_HEADER and node.model.ctrl_id == tags.TABLE_CTRL
    )
    table = table_ctrl.find(tags.TABLE).model
    cells = [node.model for node in table_ctrl.find_all(tags.LIST_HEADER)]
    assert all(isinstance(cell, CellListHeader) for cell in cells)
    assert (table.rows, table.cols) == (2, 3)
    assert len(cells) == sum(table.row_cell_counts)
    assert [(cell.row, cell.col, cell.row_span, cell.col_span) for cell in cells] == [
        (0, 0, 1, 1),
        (0, 1, 1, 1),
        (0, 2, 2, 1),
        (1, 0, 1, 2),
    ]
    covered = [
        (row, col)
        for cell in cells
        for row in range(cell.row, cell.row + cell.row_span)
        for col in range(cell.col, cell.col + cell.col_span)
    ]
    assert sorted(covered) == [(row, col) for row in range(2) for col in range(3)]
