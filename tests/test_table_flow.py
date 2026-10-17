from types import SimpleNamespace

from rea_hwp.render.table_flow import CellLine, assign_fragments
from rea_hwp.render.tables import cell_margins, stored_pieces


def _paragraph(*positions: tuple[int, int]) -> list:
    return [SimpleNamespace(vertical_pos=top, line_height=height) for top, height in positions]


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
