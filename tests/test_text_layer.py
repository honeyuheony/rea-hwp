"""선택·복사 층: 줄 글의 글자별 위치가 그린 글자와 맞는지."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest
from corpus_paths import CORPUS_ROOT, USER_CORPUS_ROOT

from rea_hwp import tags
from rea_hwp.document import HwpDocument
from rea_hwp.models.controls import EqEdit
from rea_hwp.render.equation.unicode import LEGACY_EQUATION_FAMILY
from rea_hwp.render.html import text_layer, viewer_html
from rea_hwp.render.layout import layout_document
from rea_hwp.render.scene import Glyph, Page, TextLine
from rea_hwp.render.svg import HU_PER_PT, copy_text, page_to_svg

SVG_NS = "http://www.w3.org/2000/svg"

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


def test_text_layer_falls_back_to_line_fit_when_glyphs_are_shorter_than_text():
    line = TextLine("가나", 1000, 2000, 2000, 1000, None, [1000, 2000], [Glyph("가", 1000, 2000, 1000, "바탕")])
    element = ET.fromstring(text_layer([line], 10000, 10000)).find("text")
    assert (element.get("class"), "".join(element.itertext())) == ("ln fit", "가나")


def test_text_layer_keeps_glyph_positions_when_dropped_control_char_has_no_glyph():
    glyphs = [Glyph("가", 1000, 2000, 1000, "바탕"), None, Glyph("나", 2000, 2000, 1000, "바탕")]
    line = TextLine("가\x18나", 1000, 2000, 2000, 1000, None, [1000, 2000, 2000], glyphs)
    element = ET.fromstring(text_layer([line], 10000, 10000)).find("text")
    assert element.get("class") == "ln"
    assert [(span.get("x"), span.text) for span in element.findall("tspan")] == [("10 20", "가나")]


@pytest.mark.parametrize(
    ("font", "char"), [("바탕\x01", "가"), (LEGACY_EQUATION_FAMILY, "x"), ("바탕", "\U000f02b1")], ids=repr
)
def test_text_layer_uses_same_font_family_as_drawn_glyph(font, char):
    glyph = Glyph(char, 1000, 2000, 1000, font, bold=True)
    line = TextLine(char, 1000, 2000, 1000, 1000, None, [1000], [glyph])
    span = ET.fromstring(text_layer([line], 10000, 10000)).find("text/tspan")
    drawn = ET.fromstring(page_to_svg(Page(10000, 10000, 1, items=[glyph]))).find(f"{{{SVG_NS}}}text")
    assert (span.get("font-family"), span.get("font-weight")) == (drawn.get("font-family"), drawn.get("font-weight"))


def test_viewer_puts_every_text_layer_in_page_from_start():
    layer = text_layer([TextLine("가", 1000, 2000, 1000, 1000)], 59528, 84188)
    html = viewer_html("t", [("p1.svg", 595.28, 841.88), ("p2.svg", 841.88, 595.28)], [layer, layer])
    figures = html.split('<figure class="page"')[1:]
    assert all(layer in figure for figure in figures)
    assert "<template" not in html
    ratios = [re.search(r"aspect-ratio: ([\d.]+) / ([\d.]+)", figure.split(">")[0]).groups() for figure in figures]
    assert [(float(width), float(height)) for width, height in ratios] == [(595.28, 841.88), (841.88, 595.28)]


def test_text_layer_falls_back_to_line_fit_for_rotated_glyph():
    glyphs = [Glyph("가", 1000, 2000, 1000, "바탕", rotation=-90.0), Glyph("나", 1000, 3000, 1000, "바탕")]
    line = TextLine("가나", 1000, 2000, 2000, 1000, None, [1000, 1000], glyphs)
    element = ET.fromstring(text_layer([line], 10000, 10000)).find("text")
    assert (element.get("class"), "".join(element.itertext())) == ("ln fit", "가나")


def test_band_edges_put_joined_jamo_end_at_next_char_x():
    glyph = Glyph("\uac00\u11a8", 1000, 2000, 1000, "바탕")
    line = TextLine(
        "\uac00\u11a8 나", 1000, 2000, 2000, 1000, None, [1000, 1000, 2000, 2500], [glyph, glyph, None, None]
    )
    line.glyphs[3] = Glyph("나", 2500, 2000, 1000, "바탕")
    element = ET.fromstring(text_layer([line], 10000, 10000)).find("text")
    assert element.get("data-edges") == "10 20 20 25 30"


def test_band_edges_count_utf16_units_of_astral_char():
    glyphs = [Glyph("\U0001d465", 1000, 2000, 1000, "바탕"), Glyph("가", 2000, 2000, 1000, "바탕")]
    line = TextLine("\U0001d465가", 1000, 2000, 2000, 1000, None, [1000, 2000], glyphs)
    element = ET.fromstring(text_layer([line], 10000, 10000)).find("text")
    assert element.get("data-edges") == "10 20 20 30"


def test_band_spans_text_height_with_baseline_at_ascent():
    line = TextLine("가", 1000, 3000, 1000, 2000, None, [1000], [Glyph("가", 1000, 3000, 2000, "바탕")])
    element = ET.fromstring(text_layer([line], 10000, 10000)).find("text")
    assert element.get("data-band") == "13 20"


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda path: path.name)
def test_band_edges_cover_dom_text_from_line_start_to_line_end(path):
    for page in layout_document(HwpDocument.open(path)):
        for line, element in zip(page.text_lines, _layer_lines(page), strict=True):
            edges = [float(edge) for edge in element.get("data-edges").split()]
            assert len(edges) == _utf16_len("".join(element.itertext())) + 1, (page.number, line.text)
            if "fit" in element.get("class").split():
                first = line.x
            else:
                first = next(x for char, x in zip(line.text, line.char_x, strict=True) if copy_text(char))
            assert edges[0] == pytest.approx(first / HU_PER_PT, abs=0.001), (page.number, line.text)
            assert edges[-1] == pytest.approx((line.x + line.width) / HU_PER_PT, abs=0.001), (page.number, line.text)


def _equation_scripts(document: HwpDocument) -> list[str]:
    scripts = []
    pending = [node for stream in document.sections for node in stream.nodes]
    while pending:
        node = pending.pop()
        if node.tag == tags.EQEDIT and isinstance(node.model, EqEdit) and node.model.script:
            scripts.append(re.sub(r"[\r\n\t]+", " ", node.model.script).strip())
        pending.extend(node.children)
    return [script for script in scripts if script]


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda path: path.name)
def test_only_equation_script_lines_fall_back_to_line_fit(path):
    document = HwpDocument.open(path)
    scripts = _equation_scripts(document)
    for page in layout_document(document):
        for line, element in zip(page.text_lines, _layer_lines(page), strict=True):
            if "fit" in element.get("class").split():
                assert any(script in line.text for script in scripts), (page.number, line.text)
