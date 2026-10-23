"""선택·복사 층: 줄 글의 글자별 위치가 그린 글자와 맞는지."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest
from corpus_paths import CORPUS_ROOT, USER_CORPUS_ROOT

from rea_hwp import tags
from rea_hwp.document import HwpDocument
from rea_hwp.models.controls import EqEdit
from rea_hwp.render.common import TAC_ASCENT
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


@pytest.mark.parametrize("path", DOCUMENTS, ids=lambda path: path.name)
def test_text_line_top_bottom_hold_baseline(path):
    # 띠는 글자 상자와 합쳐 그리므로, layout 줄 몫이 기준선을 놓치는 회귀는 이 시험으로만 드러난다.
    lines = [line for page in layout_document(HwpDocument.open(path)) for line in page.text_lines]
    placed = [line for line in lines if line.top is not None]
    assert placed
    for line in placed:
        assert line.top <= line.baseline <= line.bottom, line.text


#: 여러 줄 문단 안에 글자 높이가 줄 높이보다 큰 줄(1128·1156)이 있는 문서.
TALL_TEXT_DOCUMENT = CORPUS_ROOT / "oss/rhwp/21_언어_기출_편집가능본.hwp"


@pytest.mark.parametrize("path", [DOCUMENTS[0], DOCUMENTS[2], TALL_TEXT_DOCUMENT], ids=lambda path: path.name)
def test_text_line_bottom_meets_next_line_top_in_paragraph(path):
    pairs = 0
    for page in layout_document(HwpDocument.open(path)):
        for above, below in zip(page.text_lines, page.text_lines[1:], strict=False):
            same = above.paragraph is not None and above.paragraph == below.paragraph
            if same and above.top is not None and below.top is not None and below.top > above.top:
                assert below.top == above.bottom, (page.number, above.text, below.text)
                pairs += 1
    assert pairs


@pytest.mark.parametrize("path", [DOCUMENTS[2], DOCUMENTS[4]], ids=lambda path: path.name)
def test_paragraph_last_line_bottom_leaves_out_line_spacing(path):
    document = HwpDocument.open(path)
    pending = [node for stream in document.sections for node in stream.nodes]
    one_line = set()
    while pending:
        node = pending.pop()
        segments = node.find(tags.PARA_LINE_SEG) if node.tag == tags.PARA_HEADER else None
        if segments is not None and len(segments.model.segments) == 1:
            one_line.add(document.record_id(node.record))
        pending.extend(node.children)
    checked = 0
    for page in layout_document(document):
        for line in page.text_lines:
            # 기준선이 글자 크기 × 0.85에 있는 보통 글 줄(수식·개체로 높아진 줄 제외)은 줄 높이 = 글자 크기다.
            plain = line.top is not None and line.baseline - line.top == round(line.size * TAC_ASCENT)
            if plain and line.paragraph in one_line:
                assert line.bottom - line.top == line.size, line.text
                checked += 1
    assert checked


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


def test_glyph_line_band_leaves_edges_to_script_with_line_end():
    glyphs = [Glyph("가", 1000, 2000, 1000, "바탕"), Glyph("나", 2000, 2000, 1000, "바탕")]
    line = TextLine("가나", 1000, 2000, 2000, 1000, None, [1000, 2000], glyphs)
    element = ET.fromstring(text_layer([line], 10000, 10000)).find("text")
    assert (element.get("data-end"), element.get("data-x")) == ("30", None)


def test_band_spans_layout_line_top_to_bottom():
    line = TextLine("가", 1000, 1850, 1000, 1000, None, [1000], [Glyph("가", 1000, 1850, 1000, "바탕")], 1000, 3400)
    element = ET.fromstring(text_layer([line], 10000, 10000)).find("text")
    assert element.get("data-band") == "10 24"


def test_band_falls_back_to_glyph_box_without_layout_line():
    line = TextLine("가", 1000, 3000, 1000, 2000, None, [1000], [Glyph("가", 1000, 3000, 2000, "바탕")])
    element = ET.fromstring(text_layer([line], 10000, 10000)).find("text")
    assert element.get("data-band") == "13 20"


def test_fit_line_band_starts_follow_char_x_and_leave_out_astral_low_unit():
    line = TextLine("\U0001d465가", 1000, 2000, 3000, 1000, None, [1000, 2500], [None, None])
    element = ET.fromstring(text_layer([line], 10000, 10000)).find("text")
    assert (element.get("class"), element.get("data-x"), element.get("data-end")) == ("ln fit", "10 - 25", "40")


def test_fit_line_band_starts_spread_line_width_without_char_x():
    element = ET.fromstring(text_layer([TextLine("가나다", 1000, 2000, 3000, 1000)], 10000, 10000)).find("text")
    assert (element.get("data-x"), element.get("data-end")) == ("10 20 30", "40")


def test_zero_width_fit_line_keeps_browser_selection_without_band():
    element = ET.fromstring(text_layer([TextLine("가", 1000, 2000, 0, 1000)], 10000, 10000)).find("text")
    assert (element.get("data-band"), element.get("data-x")) == (None, None)


def test_carriage_return_goes_to_html_as_char_reference():
    layer = text_layer([TextLine("가\r나", 1000, 2000, 3000, 1000)], 10000, 10000)
    assert "\r" not in layer and "가&#13;나" in layer
    assert ET.fromstring(layer).find("text").get("data-x") == "10 20 30"


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


#: 띠 세로 값 시험 문서: 고정·음수 줄 간격(cyber 10쪽 「(」), 수식 든 마지막 줄(edu 181쪽), 글자 오프셋(exam_kor).
BAND_DOCUMENTS = [
    *DOCUMENTS,
    CORPUS_ROOT / "_clones/rhwp/samples/issue6795/1341000-201100013-cyber-university-application.hwp",
    CORPUS_ROOT / "oss/rhwp/task2287__1342000_edu_curriculum_map.hwp",
    CORPUS_ROOT / "oss/rhwp/exam_kor.hwp",
]


def _band(element: ET.Element) -> tuple[float, float]:
    top, height = (float(value) * HU_PER_PT for value in element.get("data-band").split())
    return top, top + height


def _drawn_box(line: TextLine) -> tuple[float, float]:
    drawn = [(glyph.y, glyph.size) for glyph in line.glyphs if glyph is not None] or [(line.baseline, line.size)]
    return min(y - size * TAC_ASCENT for y, size in drawn), max(y + size * (1 - TAC_ASCENT) for y, size in drawn)


@pytest.fixture(scope="module", params=BAND_DOCUMENTS, ids=lambda path: path.name)
def band_pages(request):
    return [(page, _layer_lines(page)) for page in layout_document(HwpDocument.open(request.param))]


def test_band_covers_drawn_glyphs_and_layout_line(band_pages):
    for page, elements in band_pages:
        for line, element in zip(page.text_lines, elements, strict=True):
            if element.get("data-band") is None:
                assert line.width <= 0, (page.number, line.text)
                continue
            top, bottom = _band(element)
            glyph_top, glyph_bottom = _drawn_box(line)
            assert top <= glyph_top + 1 and bottom >= glyph_bottom - 1, (page.number, line.text)


def test_band_of_paragraph_neighbors_do_not_overlap_unless_glyphs_do(band_pages):
    for page, elements in band_pages:
        lines = list(zip(page.text_lines, elements, strict=True))
        for (above, upper), (below, lower) in zip(lines, lines[1:], strict=False):
            same = above.paragraph is not None and above.paragraph == below.paragraph
            if not same or upper.get("data-band") is None or lower.get("data-band") is None:
                continue
            if _band(upper)[1] > _band(lower)[0] + 1 and _band(lower)[1] > _band(upper)[0]:
                assert _drawn_box(above)[1] > _drawn_box(below)[0], (page.number, above.text, below.text)


def test_band_edge_data_matches_text_line(band_pages):
    for page, elements in band_pages:
        for line, element in zip(page.text_lines, elements, strict=True):
            if element.get("data-band") is None:
                continue
            end = (line.x + line.width) / HU_PER_PT
            if "fit" in element.get("class").split():
                starts = element.get("data-x").split()
                assert len(starts) == _utf16_len("".join(element.itertext())), (page.number, line.text)
            assert float(element.get("data-end")) == pytest.approx(end, abs=0.001), (page.number, line.text)


def test_band_widens_to_glyph_raised_above_line_top():
    glyph = Glyph("가", 1000, 1735, 1000, "바탕")
    line = TextLine("가", 1000, 1850, 1000, 1000, None, [1000], [glyph], 1000, 2000)
    assert ET.fromstring(text_layer([line], 10000, 10000)).find("text").get("data-band") == "8.85 11.15"


def test_band_boundary_moves_up_to_raised_glyph_of_next_line_in_paragraph():
    above = TextLine("가", 1000, 1850, 1000, 1000, "0:1", [1000], [Glyph("가", 1000, 1850, 1000, "바탕")], 1000, 2600)
    raised = Glyph("나", 1000, 3335, 1000, "바탕")
    below = TextLine("나", 1000, 3450, 1000, 1000, "0:1", [1000], [raised], 2600, 3600)
    bands = [element.get("data-band") for element in ET.fromstring(text_layer([above, below], 10000, 10000))]
    assert bands == ["10 14.85", "24.85 11.15"]


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
