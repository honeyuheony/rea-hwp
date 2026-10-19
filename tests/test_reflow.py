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


def test_compose_leaves_head_width_out_of_every_line():
    text = "가나다라마\r"
    segments = reflow.compose(
        chars_of(text), list(range(len(text))), boxes_of(text), para_shape(attr1=1 << 7), 5000, 1000, head=(1000, 1000)
    )
    assert [s.text_start for s in segments] == [0, 4]


def test_horizontal_range_snaps_column_width_before_margins():
    # 여백은 2배로 저장된다. 단 폭을 먼저 4HU로 내린다(eq-01 48190 → 48188, kr_156369057 48188 − 515 = 47673).
    assert reflow.horizontal_range(para_shape(), 48190) == (0, 48188)
    assert reflow.horizontal_range(para_shape(right_margin=1030), 48190) == (0, 47673)
    assert reflow.horizontal_range(para_shape(left_margin=-200), 48190) == (-100, 48288)
    # 폭이 서로 다른 다단은 내리지 않는다(KTX 33355).
    assert reflow.horizontal_range(para_shape(), 33355, snap=False) == (0, 33355)
    # 백분율 여백(낮은 비트)과 최소 폭보다 좁은 구간은 모른다.
    assert reflow.horizontal_range(para_shape(left_margin=21), 48190) is None
    assert reflow.horizontal_range(para_shape(left_margin=2 * 47000), 48190) is None


def test_avoid_floats_follows_text_flow_side():
    base = (0, 20000)
    box = (8000, 0, 4000, 5000)
    assert reflow.avoid_floats(base, [(*box, 0)], 1000, 1000) == [(0, 8000), (12000, 8000)]
    assert reflow.avoid_floats(base, [(*box, 1)], 1000, 1000) == [(0, 8000)]
    assert reflow.avoid_floats(base, [(*box, 2)], 1000, 1000) == [(12000, 8000)]
    # 큰 쪽: 같은 폭이면 왼쪽, 왼쪽이 좁으면 오른쪽을 남긴다.
    assert reflow.avoid_floats(base, [(*box, 3)], 1000, 1000) == [(0, 8000)]
    assert reflow.avoid_floats(base, [(6000, 0, 4000, 5000, 3)], 1000, 1000) == [(10000, 10000)]
    # 줄이 개체 아래면 그대로, 오른쪽 끝을 덮는 개체는 그 앞까지.
    assert reflow.avoid_floats(base, [(*box, 0)], 6000, 1000) == [(0, 20000)]
    assert reflow.avoid_floats(base, [(15000, 0, 9000, 5000, 0)], 0, 1000) == [(0, 15000)]
    # 남는 쪽이 최소 폭(1440)보다 좁으면 지운다. 다 좁으면 줄은 개체 아래로 내려간다(None).
    assert reflow.avoid_floats(base, [(1000, 0, 18000, 5000, 0)], 0, 1000) is None
    assert reflow.avoid_floats(base, [(19000, 0, 900, 5000, 0)], 0, 1000) == [(0, 19000)]


def test_compose_around_narrows_lines_beside_float_only():
    # 폭 1000 글자 열 개씩. 오른쪽 6000~10000에 높이 3000 개체: 개체 옆 줄은 6000, 그 아래 줄은 다시 10000.
    text = "가" * 25 + "\r"
    chars, boxes = chars_of(text), boxes_of(text)
    floats = [(6000, 0, 4000, 3000, 0)]
    segments = reflow.compose_around(chars, list(range(len(text))), boxes, para_shape(), (0, 10000), floats, 1000, 0)
    assert [s.text_start for s in segments] == [0, 6, 12, 22]
    assert [(s.column_start, s.segment_width) for s in segments] == [(0, 6000), (0, 6000), (0, 10000), (0, 10000)]
    assert [s.vertical_pos for s in segments] == [0, 1600, 3200, 4800]


def test_line_takes_tallest_item_and_highest_ascent():
    # 재저장본 3-10월_교육_통합_2022: 글자 900 사이 수식(높이 2298, 기준선 62%)이 든 줄은 줄·글자 높이 2298, 기준선
    # 1425(= 2298 × 62%), 줄 간격은 글자 크기 900의 150%로 452다(HwpApp FUN_101e3460).
    text = "가□나\r"
    boxes = [
        reflow.ItemBox(900, 900),
        reflow.ItemBox(3000, 900, 2298, ascent=1425),
        reflow.ItemBox(900, 900),
        reflow.ItemBox(0, 900),
    ]
    segment = reflow.compose(
        chars_of(text), list(range(len(text))), boxes, para_shape(new_line_spacing=150), 20000, 900
    )[0]
    assert (segment.line_height, segment.text_height, segment.baseline_gap, segment.line_spacing) == (
        2298,
        2298,
        1425,
        452,
    )


def test_text_baseline_rounds_half_up():
    # 저장 줄 실측: 글자 1050 → 893, 850 → 723(HncIntMulDiv). 짝수 반올림이면 892·722가 된다.
    for size, baseline in ((1050, 893), (850, 723)):
        boxes = [reflow.ItemBox(1000, size), reflow.ItemBox(0, size)]
        segment = reflow.compose(chars_of("가\r"), [0, 1], boxes, para_shape(), 20000, size)[0]
        assert segment.baseline_gap == baseline


def test_compose_in_ranges_starts_at_given_line_and_range():
    text = "가" * 12 + "\r"
    segments = reflow.compose_in_ranges(
        chars_of(text),
        list(range(len(text))),
        boxes_of(text),
        para_shape(),
        [(0, 5000), (500, 4000)],
        1000,
        first_item=5,
        first_number=1,
    )
    assert [s.text_start for s in segments] == [5, 9]
    assert [(s.column_start, s.segment_width) for s in segments] == [(500, 4000), (500, 4000)]
    assert [s.vertical_pos for s in segments] == [0, 1600]
