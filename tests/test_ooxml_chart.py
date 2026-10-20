from __future__ import annotations

import math
from pathlib import Path

import pytest
from corpus_paths import CORPUS_ROOT

from rea_hwp.document import HwpDocument
from rea_hwp.render.layout import layout_document

CORPUS = CORPUS_ROOT / "oss" / "rhwp"
SLANTED_DOC = CORPUS / "issue5447__3차원묶은세로막대형-행추가.hwp"
UPRIGHT_DOC = CORPUS / "chart__세로막대형__3차원묶은세로막대형.hwp"
REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(not SLANTED_DOC.with_suffix(".pdf").exists(), reason="corpus not available")
def test_ooxml_chart_slanted_category_labels_follow_hancom_pdf_positions(monkeypatch):
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    from bench.accuracy import pdf_pages

    page = layout_document(HwpDocument.open(SLANTED_DOC))[0]
    reference = pdf_pages(str(SLANTED_DOC.with_suffix(".pdf")))[0]

    glyphs = [(g.char, g.x / 100, g.y / 100) for g in page.glyphs]

    assert len(reference) == 16
    assert len(glyphs) == 16
    unmatched = list(reference)
    for char, x, y in glyphs:
        nearest = min((r for r in unmatched if r.char == char), key=lambda r: math.dist((x, y), (r.x, r.y)))
        assert math.dist((x, y), (nearest.x, nearest.y)) <= 1.0, (char, x, y, nearest)
        unmatched.remove(nearest)
    assert not page.excluded


@pytest.mark.skipif(not UPRIGHT_DOC.exists(), reason="corpus not available")
def test_ooxml_chart_upright_category_labels_print_no_glyphs():
    page = layout_document(HwpDocument.open(UPRIGHT_DOC))[0]

    assert page.glyphs == []
    assert not page.excluded
