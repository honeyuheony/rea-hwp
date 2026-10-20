import base64
from pathlib import Path

import pytest

from rea_hwp.document import HwpDocument
from rea_hwp.render.layout import layout_document
from rea_hwp.render.scene import Glyph, Image

RHWP = Path.home() / "tools" / "hwp-corpus" / "oss" / "rhwp"
VERTICAL_DOC = RHWP / "hwpx__hancom-hwp__tbox-v-flow-01.hwp"
MANUAL_DOC = RHWP / "2025 행정업무운영 편람(최종).hwp"
GROUP_DOC = RHWP / "group-drawing-02.hwp"


def _svg_images(page) -> list[str]:
    prefix = "data:image/svg+xml;base64,"
    return [
        base64.b64decode(item.href[len(prefix) :]).decode()
        for item in page.items
        if isinstance(item, Image) and item.href.startswith(prefix)
    ]


def _origin(page, char: str) -> tuple[float, float]:
    glyph = next(item for item in page.items if isinstance(item, Glyph) and item.char == char)
    return glyph.x / 100, glyph.y / 100


@pytest.mark.skipif(not VERTICAL_DOC.exists(), reason="corpus not available")
def test_vertical_text_box_writes_columns_right_to_left():
    page = layout_document(HwpDocument.open(VERTICAL_DOC))[0]
    # 한글 2022 PDF: 첫 열 「죽」 (258.12, 114.00), 둘째 열 「한」 (242.16, 114.00), 「,」는 「、」 (246.96, 214.08).
    assert _origin(page, "죽") == pytest.approx((258.12, 114.00), abs=0.5)
    assert _origin(page, "한") == pytest.approx((242.16, 114.00), abs=0.5)
    assert _origin(page, "、") == pytest.approx((246.96, 214.08), abs=0.5)


@pytest.mark.skipif(not VERTICAL_DOC.exists(), reason="corpus not available")
def test_rectangle_outline_is_drawn():
    page = layout_document(HwpDocument.open(VERTICAL_DOC))[0]
    # 글상자 사각형: 선 종류 1(실선), 굵기 33HU, 검정.
    assert any('stroke="#000000" stroke-width="33"' in svg for svg in _svg_images(page))


@pytest.mark.skipif(not MANUAL_DOC.exists(), reason="corpus not available")
def test_rectangle_solid_fill_is_drawn():
    page = layout_document(HwpDocument.open(MANUAL_DOC))[84]
    # 바탕쪽 「제1절」 상자: 채우기 종류 1(단색) 0x646464, 선 종류 0(없음).
    assert any('fill="#646464" stroke="none"' in svg for svg in _svg_images(page))


@pytest.mark.skipif(not GROUP_DOC.exists(), reason="corpus not available")
def test_text_box_line_below_inner_height_is_not_drawn():
    page = layout_document(HwpDocument.open(GROUP_DOC))[0]
    # 「item id」 상자 일곱 개(안쪽 높이 1000)의 둘째 줄 「id」(세로 위치 1600)는 정답 PDF에 없다. 「d」는 3개뿐이다.
    assert sum(1 for item in page.items if isinstance(item, Glyph) and item.char == "d") == 3
