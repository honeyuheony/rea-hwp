import base64
from pathlib import Path

import pytest

from rea_hwp.document import HwpDocument
from rea_hwp.render.layout import layout_document
from rea_hwp.render.scene import Image

RHWP = Path.home() / "tools" / "hwp-corpus" / "oss" / "rhwp"
VERTICAL_DOC = RHWP / "hwpx__hancom-hwp__tbox-v-flow-01.hwp"
MANUAL_DOC = RHWP / "2025 행정업무운영 편람(최종).hwp"


def _svg_images(page) -> list[str]:
    prefix = "data:image/svg+xml;base64,"
    return [
        base64.b64decode(item.href[len(prefix) :]).decode()
        for item in page.items
        if isinstance(item, Image) and item.href.startswith(prefix)
    ]


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
