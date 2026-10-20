from __future__ import annotations

import struct

import pytest
from corpus_paths import CORPUS_ROOT

from rea_hwp.models import docinfo
from rea_hwp.models.base import RecordModel

CORPUS_FILE = CORPUS_ROOT / "oss" / "rhwp" / "2010-01-06.hwp"


def wstr(text: str) -> bytes:
    return struct.pack("<H", len(text)) + text.encode("utf-16-le")


def roundtrip(model_class: type[RecordModel], payload: bytes) -> RecordModel:
    model = model_class.decode(payload)
    assert model.encode() == payload
    return model


def test_document_properties():
    payload = struct.pack("<7H3I", 2, 1, 1, 1, 1, 1, 1, 0, 5, 7)
    model = roundtrip(docinfo.DocumentProperties, payload)
    assert model.section_count == 2
    assert model.caret_para_id == 5
    assert model.caret_char_pos == 7
    assert model.tail == b""


def test_document_properties_after_password_check_data():
    check = bytes(range(256))
    payload = check + struct.pack("<7H3I", 1, 3, 1, 1, 1, 1, 1, 0, 444, 11)
    model = roundtrip(docinfo.DocumentProperties, payload)
    assert model.password_check == check
    assert model.page_start == 3
    assert model.caret_para_id == 444


def test_id_mappings_count_follows_payload_length():
    counts = list(range(1, 19))
    model = roundtrip(docinfo.IdMappings, struct.pack("<18i", *counts))
    assert model.bin_data_count == 1
    assert model.face_name_counts == [2, 3, 4, 5, 6, 7, 8]
    assert model.char_shape_count == 10
    assert model.style_count == 15
    assert model.memo_shape_count == 16
    short = roundtrip(docinfo.IdMappings, struct.pack("<15i", *counts[:15]))
    assert short.memo_shape_count == 0


def test_bin_data_embedding():
    payload = struct.pack("<HH", 0x0011, 0x1A) + wstr("png")
    model = roundtrip(docinfo.BinData, payload)
    assert model.kind == docinfo.BIN_EMBEDDING
    assert model.compression == 1
    assert model.bin_id == 0x1A
    assert model.storage_name == "BinData/BIN001A.png"


def test_bin_data_link():
    payload = struct.pack("<H", 0x0100) + wstr("C:\\a.png") + wstr("a.png")
    model = roundtrip(docinfo.BinData, payload)
    assert model.kind == docinfo.BIN_LINK
    assert model.status == 1
    assert model.abs_path == "C:\\a.png"
    assert model.rel_path == "a.png"
    assert model.storage_name is None


def test_face_name_optional_parts():
    payload = bytes([0xE1]) + wstr("함초롬바탕") + bytes([1]) + wstr("바탕") + bytes(range(10)) + wstr("Batang")
    model = roundtrip(docinfo.FaceName, payload)
    assert model.name == "함초롬바탕"
    assert model.font_type == 1
    assert model.alt_type == 1
    assert model.alt_name == "바탕"
    assert model.panose == bytes(range(10))
    assert model.default_name == "Batang"


def test_face_name_without_optional_parts():
    model = roundtrip(docinfo.FaceName, bytes([0x01]) + wstr("굴림"))
    assert model.name == "굴림"
    assert model.alt_name is None
    assert model.default_name is None


def _border_head() -> bytes:
    lines = [(1, 0x11), (2, 0x22), (3, 0x33), (0, 0)]
    sides = b"".join(struct.pack("<BBI", line_type, 1, color) for line_type, color in lines)
    return struct.pack("<H", 0x2001) + sides + struct.pack("<BBI", 0, 0, 0)


def test_border_fill_solid():
    solid = struct.pack("<IIIi", docinfo.FILL_SOLID, 0xFFFFFF, 0x000000, -1)
    payload = _border_head() + solid + struct.pack("<I", 0) + b"\x00"
    model = roundtrip(docinfo.BorderFill, payload)
    assert model.effect_3d
    assert model.has_center_line
    assert model.left == docinfo.BorderLine(1, 1, 0x11)
    assert model.top.color == 0x33
    assert model.bottom.line_type == 0
    assert model.background_color == 0xFFFFFF
    assert model.solid.pattern_type == -1
    assert model.fill_extra == b""
    assert model.fill_alphas == [0]
    assert model.tail == b""


def test_border_fill_none_has_extra_size():
    model = roundtrip(docinfo.BorderFill, _border_head() + struct.pack("<II", 0, 0))
    assert model.fill_type == 0
    assert model.background_color is None
    assert model.fill_extra == b""


def test_border_fill_gradient_and_image():
    gradient = struct.pack("<BiiiiI", 2, 90, 50, 40, 10, 3) + struct.pack("<3i3I", 0, 50, 100, 1, 2, 3)
    image = struct.pack("<BbbBH", 5, -10, 20, 1, 7)
    fill_type = docinfo.FILL_GRADIENT | docinfo.FILL_IMAGE
    extra_and_alphas = struct.pack("<I", 1) + b"\x32" + b"\x00\x00"
    payload = _border_head() + struct.pack("<I", fill_type) + gradient + image + extra_and_alphas
    model = roundtrip(docinfo.BorderFill, payload)
    assert model.gradient.kind == 2
    assert model.gradient.angle == 90
    assert model.gradient.colors == [1, 2, 3]
    assert model.gradient.positions == [0, 50, 100]
    assert model.image == docinfo.ImageFill(5, -10, 20, 1, 7)
    assert model.fill_extra == b"\x32"
    assert model.fill_alphas == [0, 0]


def _char_shape(attr: int, extra: bytes = b"") -> bytes:
    return (
        struct.pack("<7H", *range(7))
        + bytes([100] * 7)
        + struct.pack("<7b", *[-5] * 7)
        + bytes([90] * 7)
        + struct.pack("<7b", *[0] * 7)
        + struct.pack("<iIbbIIII", 1000, attr, 10, -10, 0x0000FF, 0x00FF00, 0xFFFFFF, 0xB2B2B2)
        + extra
    )


def test_char_shape_fields_and_attr_bits():
    attr = 0b11 | (1 << 2) | (2 << 8) | (1 << 11) | (1 << 13) | (1 << 15) | (1 << 18) | (2 << 26) | (1 << 30)
    model = roundtrip(docinfo.CharShape, _char_shape(attr, struct.pack("<HI", 3, 0xFF0000)))
    assert model.face_ids == list(range(7))
    assert model.spacings == [-5] * 7
    assert model.rel_sizes == [90] * 7
    assert model.base_size == 1000
    assert model.text_color == 0x0000FF
    assert model.shadow_gap_y == -10
    assert model.border_fill_id == 3
    assert model.strike_color == 0xFF0000
    assert model.italic and model.bold
    assert model.underline_type == 1
    assert model.outline_type == 2
    assert model.shadow_type == 1
    assert model.emboss and not model.engrave
    assert model.superscript and not model.subscript
    assert model.strike
    assert model.kerning


def test_char_shape_old_layout_and_placeholder_strike():
    model = roundtrip(docinfo.CharShape, _char_shape((1 << 18) | (15 << 26)))
    assert model.border_fill_id is None
    assert model.strike_color is None
    assert not model.strike


def test_tab_def():
    payload = struct.pack("<II", 1, 2) + struct.pack("<IBBH", 4000, 1, 3, 0) + struct.pack("<IBBH", 8000, 2, 0, 0)
    model = roundtrip(docinfo.TabDef, payload)
    assert model.auto_tab_left and not model.auto_tab_right
    assert model.tabs == [docinfo.Tab(4000, 1, 3, 0), docinfo.Tab(8000, 2, 0, 0)]


def _numbering_levels(count: int) -> bytes:
    heads = [struct.pack("<Ihhi", 0x0C | (index << 5), 0, 50, -1) for index in range(count)]
    return b"".join(head + wstr(f"^{index + 1}.") for index, head in enumerate(heads))


def test_numbering_full_layout():
    payload = (
        _numbering_levels(7)
        + struct.pack("<H", 1)
        + struct.pack("<7I", *[1] * 7)
        + _numbering_levels(3)
        + struct.pack("<3I", 1, 1, 1)
    )
    model = roundtrip(docinfo.Numbering, payload)
    assert [level.format for level in model.levels] == [f"^{n}." for n in range(1, 8)]
    assert model.levels[2].number_format == 2
    assert model.levels[0].auto_indent
    assert model.levels[0].char_shape_id == -1
    assert model.start_number == 1
    assert model.level_start_numbers == [1] * 7
    assert len(model.extended_levels) == 3
    assert len(model.all_levels) == 10
    assert model.extended_start_numbers == [1, 1, 1]


def test_numbering_old_layout():
    model = roundtrip(docinfo.Numbering, _numbering_levels(7) + struct.pack("<H", 3))
    assert model.start_number == 3
    assert model.level_start_numbers is None
    assert model.extended_levels is None


def test_bullet_short_and_long():
    head = struct.pack("<IhhiH", 8, 0, 50, -1, 0xF06C)
    short = roundtrip(docinfo.Bullet, head)
    assert short.bullet_char == "\uf06c"
    assert short.image_bullet_id is None
    long = roundtrip(docinfo.Bullet, head + struct.pack("<ibbBHH", 1, 0, 0, 0, 2, 0x20))
    assert long.image_bullet_id == 1
    assert long.image_id == 2
    assert long.check_char == 0x20


def _para_shape(attr1: int, extra: bytes) -> bytes:
    return struct.pack("<I6i3H4h", attr1, 1000, 2000, -500, 300, 400, 160, 1, 2, 3, 10, 20, 30, 40) + extra


def test_para_shape_new_layout():
    attr1 = 1 | (3 << 2) | (2 << 5) | (1 << 7) | (1 << 17) | (2 << 23) | (6 << 25)
    model = roundtrip(docinfo.ParaShape, _para_shape(attr1, struct.pack("<IIII", 0x10, 0, 180, 8)))
    assert model.left_margin == 1000
    assert model.indent == -500
    assert model.space_after == 400
    assert model.border_spacing == [10, 20, 30, 40]
    assert model.alignment == 3
    assert model.line_spacing_type == 0
    assert model.line_spacing == 180
    assert model.break_latin_word == 2
    assert model.break_non_latin_word == 1
    assert model.keep_with_next
    assert model.head_type == 2
    assert model.level == 8


def test_para_shape_old_layout():
    model = roundtrip(docinfo.ParaShape, _para_shape(1 | (1 << 2), struct.pack("<I", 0)))
    assert model.attr3 is None
    assert model.line_spacing_type == 1
    assert model.line_spacing == 160
    assert model.alignment == 1
    assert model.level == 0


def test_style():
    payload = wstr("바탕글") + wstr("Normal") + struct.pack("<BBhHHH", 0, 0, 1042, 3, 4, 0)
    model = roundtrip(docinfo.Style, payload)
    assert model.local_name == "바탕글"
    assert model.name == "Normal"
    assert model.kind == 0
    assert model.lang_id == 1042
    assert model.para_shape_id == 3
    assert model.char_shape_id == 4
    assert model.tail == b""


def test_doc_data_nested_parameter_set():
    inner = struct.pack("<HhH", 0x0207, 2, 0) + struct.pack("<HHI", 0x400A, 6, 0) + struct.pack("<HHI", 0x401F, 7, 100)
    payload = struct.pack("<HhH", 0x021C, 1, 0) + struct.pack("<HH", 0x0207, docinfo.PIT_SET) + inner
    model = roundtrip(docinfo.DocData, payload)
    assert model.params.set_id == 0x021C
    nested = model.params.items[0].value
    assert nested.set_id == 0x0207
    assert [(item.item_id, item.value) for item in nested.items] == [(0x400A, 0), (0x401F, 100)]


def test_doc_data_unknown_kind_goes_to_tail():
    payload = struct.pack("<HhH", 1, 1, 0) + struct.pack("<HHI", 1, 0x1234, 0)
    model = roundtrip(docinfo.DocData, payload)
    assert model.params is None
    assert model.tail == payload


def test_compatibility_records():
    assert roundtrip(docinfo.CompatibleDocument, struct.pack("<I", 1)).target_program == 1
    layout = roundtrip(docinfo.LayoutCompatibility, struct.pack("<5I", 1, 2, 3, 4, 5))
    assert layout.field_format == 5
    assert roundtrip(docinfo.DistributeDocData, bytes(256)).data == bytes(256)


def test_memo_shape():
    payload = struct.pack("<IBBIIII", 15591, 1, 1, 0, 0x99FFCC, 0x99FFFF, 0)
    model = roundtrip(docinfo.MemoShape, payload)
    assert model.width == 15591
    assert model.fill_color == 0x99FFCC


def test_forbidden_char():
    start, end = "!%),.", "$([{"
    payload = struct.pack("<4I", 0, 0, len(start), len(end)) + (start + end).encode("utf-16-le")
    model = roundtrip(docinfo.ForbiddenChar, payload)
    assert model.not_at_line_start == start
    assert model.not_at_line_end == end


def test_track_change_records():
    content = roundtrip(docinfo.TrackChange, struct.pack("<I8H", 16, 2018, 1, 3, 19, 56, 1, 0, 0) + bytes(6))
    assert content.time[0] == 2018
    assert len(content.tail) == 6
    author_payload = struct.pack("<I", 6) + "master".encode("utf-16-le") + struct.pack("<II", 1, 8)
    author = roundtrip(docinfo.TrackChangeAuthor, author_payload)
    assert author.name == "master"
    assert roundtrip(docinfo.TrackChangeInfo, struct.pack("<I", 0x38) + bytes(1028)).unknown_0 == 0x38


def test_truncated_payload_keeps_bytes_in_tail():
    model = roundtrip(docinfo.TabDef, struct.pack("<II", 0, 5) + bytes(8))
    assert model.tabs is None
    assert len(model.tail) == 8


@pytest.mark.skipif(not CORPUS_FILE.exists(), reason="코퍼스 파일이 없다")
def test_corpus_char_shape_and_face_names():
    from rea_hwp.document import HwpDocument

    document = HwpDocument.open(CORPUS_FILE)
    models = [node.model for node in document.doc_info.walk()]
    char_shapes = [model for model in models if isinstance(model, docinfo.CharShape)]
    face_names = [model for model in models if isinstance(model, docinfo.FaceName)]
    assert char_shapes and face_names
    # 1pt~100pt(1/100pt 단위).
    assert all(100 <= shape.base_size <= 10000 for shape in char_shapes)
    assert all(shape.name and shape.name.isprintable() for shape in face_names)
    id_mappings = next(model for model in models if isinstance(model, docinfo.IdMappings))
    assert sum(id_mappings.face_name_counts) == len(face_names)
    assert len(char_shapes) == id_mappings.char_shape_count
