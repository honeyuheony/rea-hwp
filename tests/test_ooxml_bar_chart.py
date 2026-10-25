from __future__ import annotations

import base64
import re

import pytest
from corpus_paths import CORPUS_ROOT

from rea_hwp.document import HwpDocument
from rea_hwp.render.layout import layout_document
from rea_hwp.render.ooxml_chart import value_axis_scale
from rea_hwp.render.scene import Image

CORPUS = CORPUS_ROOT / "oss" / "rhwp"
SERIES_REMOVED_DOC = CORPUS / "issue5447__묶은세로막대형-계열삭제.hwp"
CUSTOM_STYLE_DOC = CORPUS / "2022년 국립국어원 업무계획.hwp"

# 한글 정답 PDF의 값 축(축 길이 147.7px, 라벨 높이 23.67px): 최댓값 -> (최대, 주단위, 칸 수)
HANCOM_VALUE_AXES = [(5.0, (6.0, 2.0, 3)), (6.0, (8.0, 2.0, 4)), (45.0, (50.0, 10.0, 5)), (12.3, (15.0, 5.0, 3))]


def chart_svgs(page) -> list[str]:
    prefix = "data:image/svg+xml;base64,"
    return [
        base64.b64decode(item.href[len(prefix) :]).decode("utf-8")
        for item in page.items
        if isinstance(item, Image) and item.href.startswith(prefix)
    ]


def svg_text(svg: str) -> str:
    return "".join(re.findall(r"<text [^>]*>([^<]*)</text>", svg))


@pytest.mark.parametrize(("maximum", "expected"), HANCOM_VALUE_AXES)
def test_value_axis_scale_matches_hancom_pdf_axes(maximum, expected):
    lo, hi, major, count = value_axis_scale(0.0, maximum, 147.7, 23.67)

    assert lo == 0.0
    assert (hi, major, count) == expected


@pytest.mark.skipif(not SERIES_REMOVED_DOC.exists(), reason="corpus not available")
def test_clustered_column_chart_draws_current_series_not_stale_preview():
    page = layout_document(HwpDocument.open(SERIES_REMOVED_DOC))[0]

    [svg] = chart_svgs(page)
    text = svg_text(svg)
    assert "계열1" in text and "계열3" in text
    assert "계열2" not in text
    assert len(page.excluded) == 1


@pytest.mark.skipif(not SERIES_REMOVED_DOC.exists(), reason="corpus not available")
def test_chart_text_stays_inside_chart_picture_like_hancom_pdf():
    page = layout_document(HwpDocument.open(SERIES_REMOVED_DOC))[0]

    assert page.glyphs == []


@pytest.mark.skipif(not CUSTOM_STYLE_DOC.exists(), reason="corpus not available")
def test_chart_with_custom_series_style_keeps_preview_picture():
    pages = layout_document(HwpDocument.open(CUSTOM_STYLE_DOC))

    [svg] = chart_svgs(pages[23])
    assert "<image" in svg
    assert "<text" not in svg
