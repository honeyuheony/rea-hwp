"""라이브러리로 쓰는 쪽이 기대는 약속: XML로 읽히는 SVG, 입력 상한, 오류 자료형, 공개 API."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest
from corpus_paths import CORPUS_ROOT

from rea_hwp.document import HwpDocument
from rea_hwp.render.html import text_layer
from rea_hwp.render.layout import layout_document
from rea_hwp.render.scene import Glyph, Page, TextLine
from rea_hwp.render.svg import page_to_svg

CORPUS = CORPUS_ROOT
needs_corpus = pytest.mark.skipif(not CORPUS.exists(), reason="corpus not available")
#: 문단 글에 C0 제어 문자(U+0018·U+001E·U+001F)가 있어 14ea115의 SVG나 글 층에 그대로 나가던 문서.
CONTROL_CHAR_DOCS = (
    "oss/rhwp/[2027] 온새미로 1 본교재.hwp",
    "oss/rhwp/hwpx__hancom-hwp__[2027] 온새미로 1 본교재.hwp",
    "oss/rhwp/task1725__text_footnote_tail_overpagination.hwp",
)
C0_FORBIDDEN = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def test_svg_drops_xml_forbidden_control_chars_from_glyph_text():
    page = Page(width=10000, height=10000, number=1, items=[Glyph("\x18", 100, 200, 1000, "바탕\x01")])
    page.items.append(Glyph("가", 1100, 200, 1000, "바탕"))
    root = ET.fromstring(page_to_svg(page))
    texts = [element for element in root.iter() if element.tag.endswith("text")]
    assert [(element.text, element.get("font-family")) for element in texts] == [(None, "바탕"), ("가", "바탕")]


def test_text_layer_drops_control_chars_but_keeps_tab():
    layer = text_layer([TextLine("가\x18\t나\x0b", x=0, baseline=1000, width=100, size=1000)], 10000, 10000)
    assert ">가\t나</span>" in layer


def test_text_layer_copies_bound_and_fixed_width_spaces_as_spaces():
    layer = text_layer([TextLine("가\x1e나\x1f다", x=0, baseline=1000, width=100, size=1000)], 10000, 10000)
    assert ">가 나 다</span>" in layer


@needs_corpus
@pytest.mark.parametrize("relative", CONTROL_CHAR_DOCS)
def test_every_page_of_control_char_document_is_xml_clean(relative):
    pages = layout_document(HwpDocument.open(CORPUS / relative))
    for page in pages:
        ET.fromstring(page_to_svg(page))
        assert not C0_FORBIDDEN.search(text_layer(page.text_lines, page.width, page.height)), page.number
    assert len(pages) > 1
