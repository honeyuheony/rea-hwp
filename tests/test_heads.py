import pytest
from corpus_paths import CORPUS_ROOT

from rea_hwp.document import HwpDocument
from rea_hwp.models.docinfo import CharShape
from rea_hwp.render.heads import HeadSpec, NumberingCounter, head_geometry
from rea_hwp.render.layout import layout_document

SPEC_DOC = (
    CORPUS_ROOT
    / "oss"
    / "hwp-rs"
    / "crates__hwp__tests__integration__hancom__files__한글문서파일형식_5.0_revision1.3.hwp"
)


def _shape(size: int) -> CharShape:
    shape = CharShape(values={})
    shape.values["base_size"] = size
    return shape


def test_numbering_counter_resets_deeper_levels_and_restores_other_ids():
    counter = NumberingCounter()
    assert counter.advance(1, 0)[:2] == [1, 0]
    assert counter.advance(1, 1)[:2] == [1, 1]
    assert counter.advance(1, 1)[:2] == [1, 2]
    assert counter.advance(1, 0)[:2] == [2, 0]
    assert counter.advance(2, 0)[:1] == [1]
    assert counter.advance(1, 0)[:1] == [3]


def test_bullet_area_is_glyph_plus_distance_percent_and_auto_indent_shifts_every_line():
    # basic__sungeo 3쪽: 14pt 글머리표, 본문과의 거리 20% → 본문이 16.8pt 뒤에서 시작.
    spec = HeadSpec("", 0, attr=0x08, width_adjust=0, distance=20, bullet=True)
    geometry = head_geometry(spec, _shape(1400), 1400.0, indent=0)
    assert geometry is not None
    assert (geometry.first, geometry.rest, geometry.marker_dx) == (1680, 1680, -1680)


def test_head_without_auto_indent_shifts_only_first_line():
    spec = HeadSpec("1.", -1, attr=0x10, width_adjust=0, distance=500, bullet=False)
    geometry = head_geometry(spec, _shape(1000), 800.0, indent=0)
    assert geometry is not None
    assert (geometry.first, geometry.rest) == (1300, 0)


@pytest.mark.skipif(not SPEC_DOC.exists(), reason="corpus not available")
def test_outline_head_in_text_box_uses_its_own_section_numbering():
    page = layout_document(HwpDocument.open(SPEC_DOC))[7]
    # 한글 2022 PDF 8쪽: 표지 글상자 제목은 4구역 개요 번호(1수준 형식이 빈 문자열)를 써서 번호 없이 「I. 」로 시작한다.
    title = next(line.text for line in page.text_lines if "5.0 파일" in line.text)
    assert title.startswith("I.")
    assert title.rstrip().endswith("구조")
