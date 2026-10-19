from __future__ import annotations

import struct
from pathlib import Path

import pytest

from rea_hwp.document import HwpDocument
from rea_hwp.render.layout import layout_document
from rea_hwp.render.ole import content_metafile
from rea_hwp.render.styles import DocStyles
from rea_hwp.render.wmf import convert_wmf


def record(function: int, params: bytes) -> bytes:
    return struct.pack("<IH", 3 + len(params) // 2, function) + params


def metafile(*records: bytes) -> bytes:
    body = b"".join(records) + record(0x0000, b"")
    header = struct.pack("<HHHIHIH", 1, 9, 0x0300, (18 + len(body)) // 2, 0, 0, 0)
    return header + body


def font(height: int, face: bytes) -> bytes:
    params = struct.pack("<hhhhhBBBBBBBB", height, 0, 0, 0, 400, 0, 0, 0, 129, 0, 0, 0, 0) + face + b"\0"
    return record(0x02FB, params + b"\0" * (len(params) & 1))


def text_out(x: int, y: int, raw: bytes) -> bytes:
    padded = raw + b"\0" * (len(raw) & 1)
    return record(0x0521, struct.pack("<H", len(raw)) + padded + struct.pack("<hh", y, x))


def test_convert_wmf_rejects_non_metafile_bytes():
    assert convert_wmf(b"not a metafile at all") is None


def test_baseline_aligned_text_keeps_reference_point_and_decodes_cp949():
    data = metafile(
        record(0x020B, struct.pack("<hh", 0, 0)),
        record(0x020C, struct.pack("<hh", 1000, 2000)),
        font(-100, "바탕".encode("cp949")),
        record(0x012D, struct.pack("<H", 0)),
        record(0x012E, struct.pack("<H", 0x0018)),
        text_out(300, 400, "가A".encode("cp949")),
    )

    drawing = convert_wmf(data)

    assert drawing is not None
    assert (drawing.width, drawing.height) == (2000, 1000)
    [text] = drawing.texts
    assert text.text == "가A"
    assert text.face == "바탕"
    assert (text.x, text.y, text.size) == (300, 400, 100)


def test_top_aligned_text_moves_baseline_below_reference_point():
    data = metafile(
        record(0x020C, struct.pack("<hh", 1000, 2000)),
        font(-100, b"Arial"),
        record(0x012D, struct.pack("<H", 0)),
        text_out(300, 400, b"x"),
    )

    drawing = convert_wmf(data)

    assert drawing is not None
    assert drawing.texts[0].y > 400


CHART_DOC = (
    Path.home()
    / "tools"
    / "hwp-corpus"
    / "oss"
    / "rhwp"
    / ("task2070__1130000-201900011_D0150004-1-002_2017년기준 시장구조조사.hwp")
)


@pytest.mark.skipif(not CHART_DOC.exists(), reason="corpus not available")
def test_chart_metafile_axis_labels_rise_upward():
    document = HwpDocument.open(CHART_DOC)
    entry = DocStyles.from_document(document).bin_data[0]
    data = document.container.bin_data(entry.storage_name, {0: None, 1: True, 2: False}.get(entry.compression))

    drawing = convert_wmf(data)

    assert drawing is not None
    labels = {text.text: text.y for text in drawing.texts if text.text in ("40.0", "50.0", "60.0")}
    assert labels["40.0"] > labels["50.0"] > labels["60.0"]


def rotated_font(height: int, escapement: int) -> bytes:
    params = struct.pack("<hhhhhBBBBBBBB", height, 0, escapement, escapement, 400, 0, 0, 0, 0, 0, 0, 0, 0) + b"Arial\0"
    return record(0x02FB, params + b"\0" * (len(params) & 1))


def test_escapement_turns_text_line_and_moves_top_alignment_sideways():
    data = metafile(
        record(0x020C, struct.pack("<hh", 1000, 2000)),
        rotated_font(-100, 900),
        record(0x012D, struct.pack("<H", 0)),
        text_out(300, 400, b"x"),
    )

    drawing = convert_wmf(data)

    assert drawing is not None
    [text] = drawing.texts
    assert text.angle == 90
    # 90도 글줄에서 위 정렬 기준점은 글자 높이만큼 오른쪽(글줄의 아래쪽)으로 옮겨진 기준선 위에 있다.
    assert text.y == pytest.approx(400)
    assert text.x > 300


OLE_DOC = Path.home() / "tools" / "hwp-corpus" / "oss" / "rhwp" / "issue2559__1341000_research_report_footnotes.hwp"


@pytest.mark.skipif(not OLE_DOC.exists(), reason="corpus not available")
def test_ole_content_presentation_yields_chart_metafile_with_slanted_labels():
    document = HwpDocument.open(OLE_DOC)

    drawing = convert_wmf(content_metafile(document.container.bin_data("BinData/BIN0001.OLE", True)))

    assert drawing is not None
    angles = {text.text: text.angle for text in drawing.texts}
    assert angles["독일"] == 45
    assert angles["최소 보수"] == 0


@pytest.mark.skipif(not OLE_DOC.exists(), reason="corpus not available")
def test_ole_slanted_label_glyphs_follow_hancom_pdf_positions():
    pages = layout_document(HwpDocument.open(OLE_DOC))

    glyphs = [(g.char, g.x / 100, g.y / 100) for g in pages[89].glyphs]

    # 한글 2022 PDF 90쪽의 「슬로」(45도 축 이름표) 원점.
    for char, x, y in (("슬", 129.06, 323.20), ("로", 132.73, 319.38)):
        assert any(c == char and abs(gx - x) <= 1 and abs(gy - y) <= 1 for c, gx, gy in glyphs)


CHART_ONLY_DOC = Path.home() / "tools" / "hwp-corpus" / "oss" / "hwplib" / "sample_hwp__basic__차트.hwp"


@pytest.mark.skipif(not CHART_ONLY_DOC.exists(), reason="corpus not available")
def test_vtchart_labels_follow_hancom_pdf_positions():
    pages = layout_document(HwpDocument.open(CHART_ONLY_DOC))

    glyphs = [(g.char, g.x / 100, g.y / 100) for g in pages[0].glyphs]

    # 한글 2022 PDF: 세로축 「100」 첫 글자, 가로축 「행1」, 범례 「열1」 첫 글자의 원점.
    for char, x, y in (("1", 96.12, 119.4), ("행", 127.32, 288.36), ("열", 321.72, 150.36)):
        assert any(c == char and abs(gx - x) <= 1 and abs(gy - y) <= 1 for c, gx, gy in glyphs)
