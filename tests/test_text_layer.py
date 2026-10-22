"""선택·복사 층: 줄 글의 글자별 위치가 그린 글자와 맞는지."""

from __future__ import annotations

import pytest
from corpus_paths import CORPUS_ROOT, USER_CORPUS_ROOT

from rea_hwp.document import HwpDocument
from rea_hwp.render.layout import layout_document

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
