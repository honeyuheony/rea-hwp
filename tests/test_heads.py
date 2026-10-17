from rea_hwp.models.docinfo import CharShape
from rea_hwp.render.heads import HeadSpec, NumberingCounter, head_geometry


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
