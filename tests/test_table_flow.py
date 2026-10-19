from pathlib import Path
from types import SimpleNamespace

import pytest

from rea_hwp import tags
from rea_hwp.document import HwpDocument
from rea_hwp.render.layout import DocumentLayout
from rea_hwp.render.table_flow import (
    CellLine,
    TableFlowMixin,
    assign_fragments,
    inline_stays_whole,
    paging_eligible,
)
from rea_hwp.render.tables import cell_margins, stored_pieces


def _paragraph(*positions: tuple[int, int]) -> list:
    return [SimpleNamespace(vertical_pos=top, line_height=height, text_height=height) for top, height in positions]


def _line(top: float, origin: float, carry: int = 0, cell_top: float = 0.0) -> CellLine:
    return CellLine(top, top + 100, None, 0, 0.0, 100.0, origin, carry, cell_top, 141.0)


def test_rewound_vertical_pos_starts_new_piece():
    paragraphs = [_paragraph((0, 1200), (1920, 1200)), _paragraph((0, 1200)), _paragraph((1920, 1000))]
    pieces, heights = stored_pieces(paragraphs)
    assert pieces == [[0, 0], [1], [1]]
    assert heights == [3120, 2920]


def test_cell_margins_fall_back_to_table_without_inner_margin_bit():
    table = SimpleNamespace(margins=[56, 56, 141, 141])
    own = SimpleNamespace(header=SimpleNamespace(attr=1 << 16, margins=[510, 510, 141, 141]))
    unset = SimpleNamespace(header=SimpleNamespace(attr=0x20, margins=[65535] * 4))
    assert cell_margins(table, own) == [510, 510, 141, 141]
    assert cell_margins(table, unset) == [56, 56, 141, 141]


def test_lines_go_to_fragment_holding_their_top():
    fragments = [(0.0, 1000.0), (1000.0, 2500.0)]
    assigned = assign_fragments([_line(200, 150), _line(1200, 1100)], fragments)
    assert [offset for _, offset in assigned[0]] == [150]
    assert [offset for _, offset in assigned[1]] == [100]


def test_carried_piece_starts_at_top_of_later_fragment():
    fragments = [(0.0, 0.0), (0.0, 1000.0), (1000.0, 2000.0), (2000.0, 3000.0)]
    assigned = assign_fragments([_line(500, 400, carry=2, cell_top=300)], fragments)
    assert list(assigned) == [3]
    assert assigned[3][0][1] == 141.0


def test_fragment_continuing_inside_cell_starts_below_cell_spacing():
    grid = SimpleNamespace(row_y=[0.0, 2000.0, 9000.0], spacing=255.0, table=SimpleNamespace(repeat_header=False))
    state = SimpleNamespace(geometry=SimpleNamespace(body_top=5000.0))
    inside = TableFlowMixin.fragment_origins(None, state, grid, None, 7000.0, 0.0, [(0.0, 4000.0), (4000.0, 9000.0)])
    at_row = TableFlowMixin.fragment_origins(None, state, grid, None, 7000.0, 0.0, [(0.0, 2000.0), (2000.0, 9000.0)])
    assert inside == [7000.0, 5255.0]
    assert at_row == [7000.0, 5000.0]


def test_paging_engine_takes_spaced_and_unsplit_tables():
    # 셀 간격 표, 나누지 않는 표(쪽 나눔 방식 0), 글자처럼 취급한 표도 엔진으로 나눈다. 크기 보호 표는 아직 제외한다.
    def eligible(spacing=0, page_break=1, tac=False, protect=False):
        grid = SimpleNamespace(spacing=spacing, table=SimpleNamespace(page_break=page_break), cells=[])
        model = SimpleNamespace(treat_as_char=tac, protect_size=protect)
        return paging_eligible(grid, {}, model, lambda ctrl: True)

    assert eligible(spacing=283)
    assert eligible(page_break=0)
    assert eligible(tac=True)
    assert not eligible(protect=True)


def test_inline_table_is_split_only_when_it_cannot_stay_whole():
    def whole(height, first=10000.0, top=False, unsplit=False):
        return inline_stays_whole(height, first, 60000.0, top, 5000.0, unsplit)

    assert whole(9999.0)
    assert not whole(12000.0)
    assert whole(12000.0, top=True)
    assert not whole(20000.0, top=True)
    assert whole(70000.0, unsplit=True)
    assert not whole(30000.0, unsplit=True)


NESTED_SQUARE_DOC = (
    Path.home() / "tools" / "hwp-corpus" / "oss" / "rhwp" / "issue5787__nested_square_table_horz_offset.hwp"
)


@pytest.mark.skipif(not NESTED_SQUARE_DOC.exists(), reason="corpus not available")
def test_paging_engine_takes_table_with_nested_square_table():
    # 셀 안 어울림 표는 기준 줄과 함께 통째로 그리고 셀 내용 높이에 넣으므로 바깥 표도 엔진으로 나눈다.
    document = HwpDocument.open(NESTED_SQUARE_DOC)
    flow = DocumentLayout(document)
    tables = [
        node
        for node in document.sections[0].walk()
        if node.tag == tags.CTRL_HEADER and node.model.ctrl_id == tags.TABLE_CTRL
    ]
    outer = [table for table in tables if any(_contains(table, other) for other in tables if other is not table)]
    assert outer
    grid, views = flow.table_layout_grid(outer[0])
    assert paging_eligible(grid, views, outer[0].model, flow.nested_eligible)


def _contains(node, target) -> bool:
    return any(child is target or _contains(child, target) for child in node.children)
