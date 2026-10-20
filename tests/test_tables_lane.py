from types import SimpleNamespace

import pytest
from corpus_paths import CORPUS_ROOT

from rea_hwp.document import HwpDocument
from rea_hwp.models.paragraph import LineSeg
from rea_hwp.render.layout import layout_document
from rea_hwp.render.svg import page_glyph_table
from rea_hwp.render.table_flow import CellLine, TableFlowMixin, _restored_baseline, assign_fragments
from rea_hwp.render.tables import (
    LINE_FIRST_SEGMENT,
    Cell,
    TableGrid,
    _solve_boundaries,
    cell_height,
    cell_margins,
    stored_pieces,
)

CORPUS = CORPUS_ROOT / "oss"
FULL_LINE = LINE_FIRST_SEGMENT | 1 << 18


def _segment(top: int, height: int = 1000, baseline: int = 850, spacing: int = 600, flags: int = FULL_LINE) -> LineSeg:
    return LineSeg(0, top, height, height, baseline, spacing, 0, 10000, flags)


def _glyph_line(page, text: str) -> tuple[float, float]:
    glyphs = page_glyph_table(page)
    start = "".join(char for char, _, _ in glyphs).index(text)
    return glyphs[start][1], glyphs[start][2]


def test_same_span_constraints_take_longest_length():
    # 한 행에 높이 2854와 4082인 셀이 있으면 행 높이는 4082다.
    assert _solve_boundaries(2, [(0, 1, 2854), (0, 1, 4082), (1, 1, 1000)]) == [0.0, 4082.0, 5082.0]


def test_repeated_zero_vertical_pos_in_one_paragraph_is_a_page_piece():
    _, heights = stored_pieces([[_segment(0), _segment(0)]])
    assert heights == [1000, 1000]


def test_repeated_zero_vertical_pos_is_not_a_piece_in_columns_or_across_paragraphs():
    _, columns = stored_pieces([[_segment(0), _segment(0)]], columns=True)
    _, paragraphs = stored_pieces([[_segment(0)], [_segment(0)], [_segment(652)]])
    assert columns == [1000]
    assert paragraphs == [1652]


def test_rewind_in_multi_column_cell_moves_to_next_column_until_columns_run_out():
    lines = [[_segment(0), _segment(1600), _segment(3200)], [_segment(0), _segment(1600)], [_segment(0)]]
    pieces, heights = stored_pieces(lines, columns=True, column_count=2)
    assert pieces == [[0, 0, 0], [0, 0], [1]]
    assert heights == [4200, 1000]


def test_multi_column_table_cell_stays_on_its_page_in_second_column():
    path = CORPUS / "pyhwp" / "tests__hwp5_tests__fixtures__multicolumns-layout.hwp"
    pages = layout_document(HwpDocument.open(path))
    cell_glyphs = [(x, y) for char, x, y in page_glyph_table(pages[3]) if char == "표"]
    assert len(pages) == 4
    assert any(abs(x - 428) < 1 for x, _ in cell_glyphs)


def test_half_line_baseline_is_restored_to_ascent():
    restored = _restored_baseline(_segment(0, height=1200, baseline=600, spacing=0))
    kept = _restored_baseline(_segment(0, height=1200, baseline=1020, spacing=0))
    assert restored.baseline_gap == 1020
    assert kept.baseline_gap == 1020


def _cell(row: int, col: int, height: int, lines: list[list]) -> Cell:
    header = SimpleNamespace(row=row, col=col, row_span=1, col_span=1, height=height, attr=0, margins=[0] * 4)
    return Cell(header, [], lines)


def test_split_row_ignores_stored_height_of_its_cells():
    table = SimpleNamespace(margins=[0, 0, 283, 283])
    plain = _cell(9, 1, 3166, [[_segment(0)]])
    other_row = _cell(10, 1, 3166, [[_segment(0)]])
    offsets = {(9, 8): [0.0, 1566.0]}
    assert cell_height(table, plain, offsets) == 1566
    assert cell_height(table, other_row, offsets) == 3166


def _fragments(page_break: int, top: float, rows: list[float]) -> list[tuple[float, float]]:
    geometry = SimpleNamespace(body_top=0, body_height=10000)
    state = SimpleNamespace(geometry=geometry)
    table = SimpleNamespace(page_break=page_break, repeat_header=False)
    grid = TableGrid(table, [], [0.0], rows, {})
    return TableFlowMixin.table_fragments(None, state, grid, top, (0, 0), [])


def test_unsplittable_table_moves_whole_or_overflows_a_fresh_page():
    assert _fragments(0, 5000, [0.0, 3000.0, 6000.0]) == [(0.0, 0.0), (0.0, 6000.0)]
    assert _fragments(0, 100, [0.0, 6000.0, 10050.0]) == [(0.0, 10050.0)]


def test_split_leaves_reserve_above_page_bottom():
    # 셀 안에서도 나누는 표는 바닥에서 ROW_RESERVE(80) 위까지 들어가는 행을 이 쪽에 둔다.
    assert _fragments(2, 0, [0.0, 4950.0, 9900.0, 12000.0]) == [(0.0, 9900.0), (9900.0, 12000.0)]
    assert _fragments(2, 0, [0.0, 4950.0, 9950.0, 12000.0]) == [(0.0, 4950.0), (4950.0, 12000.0)]
    assert _fragments(2, 0, [0.0, 4950.0, 9850.0]) == [(0.0, 9850.0)]


def test_captions_sit_beside_their_tables():
    # 정답 PDF(한글 2022) 기준선: 위 캡션 136.08, 아래 캡션 245.40, 왼쪽 캡션 313.56(x 113.40), 오른쪽 캡션 x 331.68.
    page = layout_document(HwpDocument.open(CORPUS / "pyhwp/tests__hwp5_tests__fixtures__table-caption.hwp"))[0]
    _, top_y = _glyph_line(page, "표1위캡션")
    _, bottom_y = _glyph_line(page, "표2아래캡션")
    left_x, left_y = _glyph_line(page, "표3왼쪽")
    right_x, _ = _glyph_line(page, "표4오른쪽")
    assert abs(top_y - 136.08) < 0.5
    assert abs(bottom_y - 245.40) < 0.5
    assert abs(left_x - 113.40) < 0.5 and abs(left_y - 313.56) < 0.5
    assert abs(right_x - 331.68) < 0.5


def test_object_after_text_in_first_line_anchors_at_paragraph_start():
    # 문단 21의 표는 공백 뒤에 있어 첫 줄이 표 아래로 밀렸다. 정답 PDF 1쪽 표 위 선은 707.95pt다.
    page = layout_document(HwpDocument.open(CORPUS / "rhwp/hwpx__hancom-hwp__hwpx-h-01.hwp"))[0]
    tops = sorted(item.y1 / 100 for item in page.items if type(item).__name__ == "Line" and item.y1 == item.y2)
    assert any(abs(top - 707.95) < 1.0 for top in tops)


def test_top_and_bottom_tables_do_not_overlap():
    # 표 7은 같은 문단의 표 8과 앞 문단의 표 6 아래에 쌓인다. 정답 PDF 캡션 기준선은 611.52pt다.
    page = layout_document(HwpDocument.open(CORPUS / "pyhwp/tests__hwp5_tests__fixtures__table-caption.hwp"))[0]
    _, caption_y = _glyph_line(page, "표7여백까지확대")
    assert abs(caption_y - 611.52) < 0.5


def test_later_piece_is_centered_in_visible_part_of_cell():
    # 셀(0~9000)의 둘째 조각(높이 2000)이 쪽 조각 4000~9000에 보이는 부분 안 가운데에 놓인다.
    line = CellLine(0, 100, None, 0, 0.0, 100.0, 0.0, 1, 0.0, 141.0, 9000.0, 141.0, 1, 2000.0)
    assigned = assign_fragments([line], [(0.0, 4000.0), (4000.0, 9000.0)])
    assert assigned[1][0][1] == 141.0 + (5000 - 282 - 2000) / 2


def test_negative_cell_margin_counts_as_zero():
    table = SimpleNamespace(margins=[510, 510, 141, 141])
    header = SimpleNamespace(attr=1 << 16, margins=[0, 283, 65535, 65535])
    assert cell_margins(table, SimpleNamespace(header=header)) == [0, 283, 0, 0]


def test_page_crossing_nested_table_splits_with_its_host_table():
    # 바깥 1×1 표 안의 1×1 표 안 55행 표가 한글 2022 PDF에서 3쪽에 걸친다.
    pages = layout_document(HwpDocument.open(CORPUS / "rhwp/issue4889__18098267_nested_fragment_origin.hwp"))
    assert len(pages) == 3
    # 정답 PDF 3쪽 「3. 국제입찰 대상 공사」 기준선은 576.72pt다(쪽을 넘은 중첩 표 뒤 문단).
    assert abs(_glyph_line(pages[2], "3.국제입찰대상공사")[1] - 576.72) < 1.0
    assert all(page_glyph_table(page) for page in pages)


def test_trailing_ideographic_space_is_left_out_of_right_alignment():
    page = layout_document(HwpDocument.open(CORPUS.parent / "public" / "kr_156369107_189358975.hwp"))[0]
    glyphs = page_glyph_table(page)
    chars = "".join(char for char, _, _ in glyphs)
    plain = glyphs[chars.index("101.27")][1]
    padded = glyphs[chars.index("101.73")][1]
    # 정답 1쪽(비트맵 칸): 폭 3128인 이웃 칸의 「101.27」과 전각 공백으로 끝나는 「101.73」이 같은 오른쪽 끝에 맞는다.
    assert padded - plain == pytest.approx(31.28, abs=0.2)
