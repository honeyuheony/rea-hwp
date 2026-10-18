from __future__ import annotations

import struct
from pathlib import Path

import pytest

from rea_hwp.document import HwpDocument
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
