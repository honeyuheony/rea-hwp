from types import SimpleNamespace

from corpus_paths import CORPUS_ROOT

from rea_hwp.document import HwpDocument
from rea_hwp.models.paragraph import LineSeg
from rea_hwp.render.layout import layout_document
from rea_hwp.render.options import RenderOptions
from rea_hwp.render.svg import page_glyph_table
from rea_hwp.render.table_flow import cell_items
from rea_hwp.render.table_paging import (
    MODE_LINES,
    MODE_ROWS,
    PagingBlock,
    PagingCell,
    PagingLine,
    PagingTable,
    header_row_count,
    paginate_table,
    split_cell_text,
)
from rea_hwp.render.tables import LINE_FIRST_SEGMENT

CORPUS = CORPUS_ROOT / "oss"
BIG = 10**7


def _cell(row, col, stored, lines=(), row_span=1, header=False, margins=(100, 100)):
    return PagingCell(row, col, row_span, 1, stored, margins[0], margins[1], header, list(lines))


def _lines(count, height, pitch=None):
    return [PagingLine(index * (pitch or height), height) for index in range(count)]


def _glyph_y(page, text: str) -> float | None:
    glyphs = page_glyph_table(page)
    start = "".join(char for char, _, _ in glyphs).find(text)
    return glyphs[start][2] if start >= 0 else None


def test_multi_row_cell_shortfall_goes_to_its_last_row():
    table = PagingTable(
        2,
        [
            _cell(0, 0, 1000, _lines(1, 500)),
            _cell(0, 1, 3000, _lines(1, 500), row_span=2),
            _cell(1, 0, 1000, _lines(1, 500)),
        ],
        MODE_LINES,
        False,
    )
    paging = paginate_table(table, BIG, BIG)
    assert paging.fragments[0].row_tops == [0, 1000, 3000]


def test_line_fits_when_its_bottom_equals_remaining_text_height():
    cell = _cell(0, 0, 0, _lines(3, 400))
    # 남은 1000 − 여백 200 = 800: 두 줄째 아래 800은 들어가고 세 줄째는 넘는다.
    assert split_cell_text(cell, 0, 1000, carried=False) == (2, 2, 800)


def test_carried_cell_starts_with_stored_height_minus_height_used_before():
    table = PagingTable(1, [_cell(0, 0, 5000, _lines(10, 400))], MODE_LINES, False)
    # 첫 조각 남은 높이 = 2100 − 100 = 2000, 글 자리 1800에 네 줄.
    paging = paginate_table(table, 2100, BIG)
    first, second = (fragment.boxes[0] for fragment in paging.fragments)
    assert (first.first_line, first.end_line, first.height) == (0, 4, 2000)
    assert (second.first_line, second.end_line, second.height) == (4, 10, 3000)


def test_first_row_without_room_for_one_line_moves_whole_table():
    table = PagingTable(2, [_cell(0, 0, 1200, _lines(1, 1000)), _cell(1, 0, 1200, _lines(1, 1000))], MODE_LINES, False)
    paging = paginate_table(table, 1100, BIG)
    assert paging.moved_whole
    assert [fragment.rows for fragment in paging.fragments] == [[0, 1]]


def test_row_without_room_for_one_line_starts_next_fragment():
    table = PagingTable(2, [_cell(0, 0, 1200, _lines(1, 1000)), _cell(1, 0, 1200, _lines(1, 1000))], MODE_LINES, False)
    # 행 1 남은 높이 = 2000 − 100 − 1200 = 700, 글 자리 500에 1000 줄이 들어가지 않는다.
    paging = paginate_table(table, 2000, BIG)
    assert [fragment.rows for fragment in paging.fragments] == [[0], [1]]


def test_finished_cell_leftover_below_threshold_is_not_carried():
    # 저장 5000, 글 1200: 남은 4000이면 남는 빈 높이 1000 < 1283이라 이 쪽에서 끝낸다.
    table = PagingTable(1, [_cell(0, 0, 5000, _lines(1, 1000))], MODE_LINES, False)
    assert len(paginate_table(table, 4100, BIG).fragments) == 1


def test_finished_cell_leftover_over_threshold_is_carried_as_empty_box():
    table = PagingTable(1, [_cell(0, 0, 5000, _lines(1, 1000))], MODE_LINES, False)
    paging = paginate_table(table, 3100, BIG)
    assert [fragment.boxes[0].height for fragment in paging.fragments] == [3000, 2000]


def test_header_rows_follow_bundles_from_row_zero_with_any_header_cell():
    table = PagingTable(
        3,
        [
            _cell(0, 0, 1000, header=True),
            _cell(0, 1, 1000),
            _cell(1, 0, 1000),
            _cell(2, 0, 1000, header=True),
        ],
        MODE_LINES,
        True,
    )
    assert header_row_count(table) == 1


def test_header_cell_spanning_two_rows_makes_two_header_rows():
    table = PagingTable(3, [_cell(0, 0, 2000, header=True, row_span=2), _cell(2, 0, 1000)], MODE_LINES, True)
    assert header_row_count(table) == 2


def test_header_cell_starting_inside_row_bundle_does_not_make_header_rows():
    # 행 0~1은 두 행 셀로 한 묶음이고 제목 셀은 행 1에서 시작한다: 묶음 시작 행만 보므로 제목 행 수는 0이다.
    table = PagingTable(
        2, [_cell(0, 0, 2000, row_span=2), _cell(0, 1, 1000), _cell(1, 1, 1000, header=True)], MODE_LINES, True
    )
    assert header_row_count(table) == 0


def test_continued_fragment_starts_below_repeated_header():
    table = PagingTable(
        4,
        [_cell(0, 0, 1000, _lines(1, 400), header=True)] + [_cell(row, 0, 1000, _lines(1, 400)) for row in (1, 2, 3)],
        MODE_LINES,
        True,
    )
    paging = paginate_table(table, 2600, BIG)
    assert (paging.header_height, paging.fragments[1].start_y, paging.fragments[1].rows) == (1000, 1000, [2, 3])


def test_header_only_first_fragment_moves_whole_table():
    table = PagingTable(
        2,
        [_cell(0, 0, 1000, _lines(1, 400), header=True), _cell(1, 0, 1000, _lines(1, 1000))],
        MODE_LINES,
        True,
    )
    assert paginate_table(table, 1500, BIG).moved_whole


def test_row_mode_moves_whole_row_bundle_to_next_fragment():
    table = PagingTable(
        3,
        [
            _cell(0, 0, 1000, _lines(1, 400)),
            _cell(1, 0, 2000, _lines(1, 400), row_span=2),
            _cell(1, 1, 1000, _lines(1, 400)),
            _cell(2, 1, 1000, _lines(1, 400)),
        ],
        MODE_ROWS,
        False,
    )
    # 행 1에서 시작하는 묶음(행 1~2) 셀 2000이 남은 높이 2500 − 100 − 1000 = 1400에 들어가지 않는다.
    paging = paginate_table(table, 2500, BIG)
    assert [fragment.rows for fragment in paging.fragments] == [[0], [1, 2]]


def test_line_with_floating_object_below_does_not_fit_when_object_overflows():
    # 줄 높이 1000이지만 함께 놓이는 떠 있는 개체 아래가 5000: 글 자리 3000에 들어가지 않는다.
    cell = _cell(0, 0, 0, [PagingLine(0, 1000, reach=5000)])
    assert split_cell_text(cell, 0, 3200, carried=False) == (3, 0, 0)


def test_nested_table_in_cell_continues_into_next_outer_fragment():
    inner = PagingTable(3, [_cell(row, 0, 1000, _lines(1, 400)) for row in range(3)], MODE_LINES, False)
    outer = PagingTable(1, [_cell(0, 0, 0, [PagingLine(0, 1000), PagingBlock(1000, inner, 0, 0)])], MODE_LINES, False)
    # 첫 조각 글 자리 2300: 줄 1000 뒤 표에 1300, 표 행 0만 들어가고(행 1 남은 200) 나머지 행은 다음 조각.
    paging = paginate_table(outer, 2600, BIG)
    first, second = (fragment.boxes[0] for fragment in paging.fragments)
    assert [(item, local, piece.rows if piece else None) for item, local, piece in first.placements] == [
        (0, 0, None),
        (1, 1000, [0]),
    ]
    assert [(item, local, piece.rows) for item, local, piece in second.placements] == [(1, 0, [1, 2])]


def _view(segments, space_before=0):
    return SimpleNamespace(
        segments=segments, para_shape=SimpleNamespace(space_before=space_before, space_after=0), controls={}
    )


def test_rewound_stored_line_continues_below_previous_line():
    first = LineSeg(0, 0, 1000, 1000, 850, 300, 0, 10000, LINE_FIRST_SEGMENT)
    second = LineSeg(0, 0, 1000, 1000, 850, 300, 0, 10000, LINE_FIRST_SEGMENT)
    lines, _ = cell_items([_view([first]), _view([second], space_before=400)], nested=None)
    # 앞 줄 위 0 + 줄 높이 1000 + 줄 간격 300 + 문단 위 간격 400 / 2.
    assert [line.top for line in lines] == [0, 1500]


def _corpus_pages(name: str, engine: bool):
    return layout_document(HwpDocument.open(CORPUS / name), RenderOptions(table_engine=engine))


def test_split_multi_row_cell_leaves_row_50_at_stored_height():
    page = _corpus_pages("rhwp/task2287__1342000_edu_curriculum_map.hwp", True)[170]
    # 한글 2022 PDF 171쪽: 행 50 「[9국01-12]」 165.60pt, 행 51 「[9국01-11]」 185.88pt.
    assert abs((_glyph_y(page, "[9국01-12]") or 0) - 165.60) <= 1
    assert abs((_glyph_y(page, "[9국01-11]") or 0) - 185.88) <= 1


def test_table_whose_first_row_has_no_room_for_a_line_moves_to_next_page():
    pages = _corpus_pages("rhwp/hwpspec.hwp", True)
    # 한글 2022 PDF 43쪽: 표 66 첫 행 「자료형」 93.72pt(42쪽에는 표가 없다).
    assert abs((_glyph_y(pages[42], "자료형") or 0) - 93.72) <= 1


def test_cell_with_floating_pictures_that_do_not_fit_moves_whole_table():
    page = _corpus_pages("rhwp/issue1921__59043_regulatory_analysis.hwp", True)[10]
    # 한글 2022 PDF 11쪽: 표 15 행 1 캡션 「유튜브 담배제품 후기·추천」 262.32pt(행 0 그림이 10쪽에 들어가지 않음).
    assert abs((_glyph_y(page, "유튜브담배제품후기") or 0) - 262.32) <= 1


def test_nested_table_splits_along_outer_cell_fragments():
    page = _corpus_pages("rhwp/basic__issue2007_nested_cell_pagination_42065.hwp", True)[11]
    # 한글 2022 PDF 12쪽: 셀 안 표 이어지는 조각의 「제272조의2(선거범죄의조사등)」 149.40pt.
    assert abs((_glyph_y(page, "제272조의2(선거범죄의조사등)") or 0) - 149.40) <= 1


def test_table_engine_option_reaches_layout_and_environment_is_ignored(monkeypatch):
    document = HwpDocument.open(CORPUS / "rhwp/basic__issue2007_nested_cell_pagination_42065.hwp")
    monkeypatch.setenv("REA_HWP_TABLE_ENGINE", "0")
    by_default = layout_document(document)
    engine_on = layout_document(document, RenderOptions(table_engine=True))
    engine_off = layout_document(document, RenderOptions(table_engine=False))
    # 이 문서는 셀 안 표를 나누는 두 경로의 쪽 수가 다르다(엔진 17쪽, 경험 규칙 15쪽).
    assert len(by_default) == len(engine_on) != len(engine_off)
