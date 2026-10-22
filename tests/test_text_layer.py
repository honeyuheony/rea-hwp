"""선택·복사 층: 줄 글의 글자별 위치가 그린 글자와 맞는지."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from corpus_paths import CORPUS_ROOT, USER_CORPUS_ROOT

from rea_hwp.document import HwpDocument
from rea_hwp.render.html import text_layer
from rea_hwp.render.layout import layout_document
from rea_hwp.render.scene import Glyph, TextLine
from rea_hwp.render.svg import HU_PER_PT, copy_text

DOCUMENTS = [
    CORPUS_ROOT / "oss/pyhwp/tests__hwp5_tests__fixtures__multicolumns.hwp",
    CORPUS_ROOT / "oss/rhwp/basic__shortcut.hwp",
    CORPUS_ROOT / "oss/hwp-rs/crates__hwp__tests__integration__hancom__files__한글문서파일형식_수식_revision1.3.hwp",
    CORPUS_ROOT / "oss/hwp-rs/crates__hwp__tests__integration__project__files__dutmal.hwp",
    CORPUS_ROOT / "oss/hwp-rs/crates__hwp__tests__integration__hancom__files__한글문서파일형식_5.0_revision1.3.hwp",
    USER_CORPUS_ROOT / "user-report-title.hwp",
]


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda path: path.name)
def test_text_line_char_x_matches_drawn_glyphs(path):
    pages = layout_document(HwpDocument.open(path))
    drawn_chars = 0
    for page in pages:
        drawn = {id(item) for item in page.items}
        for line in page.text_lines:
            assert len(line.char_x) == len(line.glyphs) == len(line.text), (page.number, line.text)
            for char, x, glyph in zip(line.text, line.char_x, line.glyphs, strict=True):
                if glyph is None:
                    continue
                drawn_chars += 1
                assert id(glyph) in drawn, (page.number, line.text, char)
                assert char in glyph.char, (page.number, line.text, char)
                assert x == glyph.x, (page.number, line.text, char)
    assert drawn_chars


def test_text_line_char_x_keeps_spaces_in_order():
    pages = layout_document(HwpDocument.open(DOCUMENTS[1]))
    lines = [line for page in pages for line in page.text_lines if " " in line.text.strip()]
    assert lines
    for line in lines:
        assert line.char_x == sorted(line.char_x), line.text
        assert all(glyph is None for char, glyph in zip(line.text, line.glyphs, strict=True) if not char.strip())


def _layer_lines(page) -> list[ET.Element]:
    root = ET.fromstring(text_layer(page.text_lines, page.width, page.height))
    return [element for element in root.iter() if "ln" in (element.get("class") or "").split()]


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda path: path.name)
def test_text_layer_line_copies_same_text_as_text_line(path):
    for page in layout_document(HwpDocument.open(path)):
        lines = _layer_lines(page)
        assert ["".join(element.itertext()) for element in lines] == [copy_text(line.text) for line in page.text_lines]


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda path: path.name)
def test_text_layer_places_each_char_at_drawn_glyph_x(path):
    checked = 0
    for page in layout_document(HwpDocument.open(path)):
        for line, element in zip(page.text_lines, _layer_lines(page), strict=True):
            plain = all(copy_text(char) == char for char in line.text) and all(
                glyph is None or glyph.char == char and glyph.scale_x == 1.0 and not glyph.rotation
                for char, glyph in zip(line.text, line.glyphs, strict=True)
            )
            if "fit" in element.get("class") or not plain:
                continue
            placed = [float(x) for span in element.iter("tspan") for x in span.get("x").split()]
            assert placed == pytest.approx([x / HU_PER_PT for x in line.char_x], abs=0.001), line.text
            checked += 1
    assert checked


def test_text_layer_covers_equation_script_line_by_line_width():
    page = layout_document(HwpDocument.open(DOCUMENTS[2]))[8]
    element = next(
        element for element in _layer_lines(page) if "".join(element.itertext()).startswith("결과 예시:rm 2H_2")
    )
    assert element.get("class") == "ln fit"
    assert element.get("lengthAdjust") == "spacingAndGlyphs"


def test_text_layer_lets_joined_jamo_flow_after_first_char():
    glyph = Glyph("\uac00\u11a8", 1000, 2000, 1000, "바탕")
    line = TextLine(
        "\uac00\u11a8 나", 1000, 2000, 2000, 1000, None, [1000, 1000, 2000, 2500], [glyph, glyph, None, None]
    )
    line.glyphs[3] = Glyph("나", 2500, 2000, 1000, "바탕")
    element = ET.fromstring(text_layer([line], 10000, 10000)).find("text")
    spans = element.findall("tspan")
    assert (spans[0].get("x"), spans[0].text) == ("10", "\uac00\u11a8")
    assert [span.get("x") for span in spans[1:]] == ["20", "25"]


def test_text_layer_divides_char_x_by_condensed_scale():
    glyphs = [Glyph("가", 1000, 2000, 1000, "바탕", scale_x=0.8), Glyph("나", 1800, 2000, 1000, "바탕", scale_x=0.8)]
    line = TextLine("가나", 1000, 2000, 1600, 1000, None, [1000, 1800], glyphs)
    element = ET.fromstring(text_layer([line], 10000, 10000)).find("text")
    assert element.get("transform") == "translate(10 0) scale(0.8000 1)"
    assert element.find("tspan").get("x") == "0 10"
