from rea_hwp.models.docinfo import ParaShape
from rea_hwp.models.paragraph import LineSeg
from rea_hwp.render import reflow
from rea_hwp.render.common import INDENT_APPLIED, TextChar


def para_shape(**values) -> ParaShape:
    base = {
        "attr1": 0,
        "left_margin": 0,
        "right_margin": 0,
        "indent": 0,
        "space_before": 0,
        "space_after": 0,
        "legacy_line_spacing": 160,
        "attr3": 0,
        "new_line_spacing": 160,
    }
    base.update(values)
    return ParaShape(values=base)


def chars_of(text: str) -> list[TextChar]:
    return [TextChar(index, char, 0) for index, char in enumerate(text)]


def boxes_of(text: str, width: int = 1000, space: int = 500) -> list[reflow.ItemBox]:
    return [reflow.ItemBox(space if char == " " else 0 if char == "\r" else width, 1000) for char in text]


def test_percent_line_gap_snaps_to_four_units():
    assert reflow.line_gap(para_shape(), 1500) == 900
    assert reflow.line_gap(para_shape(new_line_spacing=150), 1500) == 752


def test_fixed_and_minimum_line_gap_never_negative():
    # 퍼센트가 아닌 값은 2배로 저장된다(고정 4000 → 줄 피치 2000).
    assert reflow.line_gap(para_shape(attr3=1, new_line_spacing=4000), 1500) == 500
    assert reflow.line_gap(para_shape(attr3=3, new_line_spacing=1000), 1500) == 0
    assert reflow.line_gap(para_shape(attr3=2, new_line_spacing=600), 1500) == 300


def test_word_mode_moves_whole_word_to_next_line():
    text = "가나다 라마바사\r"
    starts = reflow.break_lines(chars_of(text), boxes_of(text), para_shape(), (5000, 5000))
    assert starts == [0, 4]


def test_char_mode_fills_the_line():
    text = "가나다 라마바사\r"
    by_char = para_shape(attr1=1 << 7)
    starts = reflow.break_lines(chars_of(text), boxes_of(text), by_char, (5000, 5000))
    assert starts == [0, 5]


def test_line_start_forbidden_char_pulls_previous_char_down():
    text = "가나다라마.\r"
    by_char = para_shape(attr1=1 << 7)
    starts = reflow.break_lines(chars_of(text), boxes_of(text), by_char, (5000, 5000))
    assert starts == [0, 4]


def test_newline_char_forces_break():
    text = "가나\n다라\r"
    starts = reflow.break_lines(chars_of(text), boxes_of(text), para_shape(), (9000, 9000))
    assert starts == [0, 3]


def test_compose_marks_hanging_indent_lines():
    text = "가나 다라 마바\r"
    segments = reflow.compose(
        chars_of(text), list(range(len(text))), boxes_of(text), para_shape(indent=-2000), 6000, 1000
    )
    assert [s.text_start for s in segments] == [0, 6]
    assert [bool(s.flags & INDENT_APPLIED) for s in segments] == [False, True]
    assert segments[1].vertical_pos == 1600
    assert segments[0].baseline_gap == 850


def test_paginate_moves_overflowing_line_to_page_top():
    lines = [LineSeg(0, 0, 1000, 1000, 850, 600, 0, 5000, 0), LineSeg(1, 1600, 1000, 1000, 850, 600, 0, 5000, 0)]
    placed = reflow.paginate([LineSeg(**{**vars(s), "vertical_pos": s.vertical_pos + 9000}) for s in lines], 11000, 300)
    assert [s.vertical_pos for s in placed] == [9000, 0]
