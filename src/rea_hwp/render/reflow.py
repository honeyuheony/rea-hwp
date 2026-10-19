"""저장 줄 배치(LINE_SEG)가 없는 문단을 한글처럼 줄로 나눠 합성 LineSeg를 만든다. 좌표는 HWPUNIT.

줄 나눔 규칙 출처: kordoc(MIT) hwpx/text-metrics.ts simulateWrap, 줄 높이·간격: rhwp composer/line_breaking.rs.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass

from .. import tags
from ..models.controls import ObjectCtrlHeader, TextWrap, VertRelTo
from ..models.docinfo import CharShape, ParaShape
from ..models.paragraph import LINE_BREAK, ControlChar, LineSeg
from .common import INDENT_APPLIED, ParagraphView, TextChar
from .equation.engine import muldiv

#: 줄 머리에 올 수 없는 글자(kordoc HANCOM_FORBID_START, 한글 기본 금칙).
FORBID_START = frozenset(
    "!%),.:;>?]^`}~°·〉》」』】〕〗〙〛・！％），．：；＞？］＾｀｝～･ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ"
    "、。〃々ー゛゜ゝゞヽヾ’”․‥…‰‱′″‴‵‶‷℃℉"
)
#: 줄 끝에 올 수 없는 글자(kordoc HANCOM_FORBID_END).
FORBID_END = frozenset(
    "(<[\\{§‘“〈《「『【〔〖〘〚＄（＜［＼｛￠￡￥￦$¢£¤¥৲৳฿" + "".join(chr(0x20A0 + i) for i in range(13))
)
#: 기준선은 줄 높이의 0.85(rhwp baseline_distance_hwp, 저장 LINE_SEG 1500→1275 실측).
BASELINE_RATIO = 0.85
#: 글자와 수식 아닌 개체의 기준선 위 몫(%). HwpApp FUN_101d7770: 기준선 함수(+0x140)가 −1이면 85.
ASCENT_PERCENT = 85
#: 폭 비교의 반올림 여유(kordoc simulateWrap EPS).
FIT_EPSILON = 0.5
#: LINE_SEG 태그 bit17/18: 줄의 첫/마지막 세그먼트. 저장 줄은 대개 둘 다 켜져 있다.
SINGLE_SEGMENT = (1 << 17) | (1 << 18)
#: 줄의 첫 세그먼트 표지(LINE_SEG flags 0x20000).
LINE_FIRST_SEGMENT = 1 << 17
#: 빈 세그먼트(0x10000)인 줄 첫 세그먼트. 한글은 구간이 하나면 그 줄을 다시 나눈다(FUN_101dbcf0).
EMPTY_FIRST_SEGMENT = (1 << 16) | (1 << 17)
#: 줄 간격 종류(명세 표43): 0 글자에 따라(%), 1 고정 값, 2 여백만 지정, 3 최소.
PERCENT, FIXED, SPACE_ONLY, MINIMUM = range(4)
#: 한글 줄 나눔 단위가 어절이 아닌 글자일 때 한 단어로 묶는 라틴 글자(kordoc splitWrapUnits).
_LATIN_RANGES = ((0x21, 0x7E), (0xA1, 0x24F), (0x2018, 0x201F))


@dataclass
class ItemBox:
    """줄 나눔에 쓰는 글자·컨트롤 하나의 폭과 높이."""

    width: float
    #: 글자 크기(컨트롤은 그 자리 글자 모양의 크기). 줄 간격은 이 값으로 정한다.
    height: int
    #: 글자처럼 취급한 개체의 바깥 여백 포함 높이. 줄 높이만 늘리고 줄 간격은 바꾸지 않는다(저장 LINE_SEG 실측).
    object_height: int = 0
    #: 자간 몫. 한글은 넘침 판정에 줄 마지막 글자의 자간을 넣지 않는다(kordoc lastSp).
    spacing: float = 0.0
    #: 기준선 위 몫. 주지 않으면 줄 높이에 넣는 높이의 85%다(HwpApp FUN_101d7770, HncIntMulDiv 반올림).
    ascent: int | None = None

    def __post_init__(self) -> None:
        if self.ascent is None:
            self.ascent = muldiv(self.object_height or self.height, ASCENT_PERCENT, 100)


def measure_items(view: ParagraphView, item_width, char_shapes: list[CharShape], slot_of, inline_box) -> list[ItemBox]:
    """item_width(순번)는 layout의 글자·컨트롤 전진폭 함수, slot_of(글자, 글자 모양)는 폭을 잰 언어 슬롯 함수,
    inline_box(CTRL_HEADER)는 글자처럼 취급한 개체의 (바깥 여백 포함 높이, 기준선 위 몫)이다."""
    boxes = []
    for index, item in enumerate(view.chars):
        width = item_width(index)
        shape = char_shapes[view.shape_ids[index]]
        spacing, object_height, ascent = 0.0, 0, None
        if isinstance(item, TextChar):
            percent = shape.spacings[slot_of(item.char, shape)]
            spacing = width * percent / (100 + percent) if percent > -100 else 0.0
        else:
            ctrl = view.controls.get(index)
            if ctrl is not None and isinstance(ctrl.model, ObjectCtrlHeader) and ctrl.model.treat_as_char:
                object_height, ascent = inline_box(ctrl)
                if not object_height:
                    # 높이 0인 개체는 그 자리 글자 크기를 쓴다(FUN_101d7770).
                    ascent = None
        boxes.append(ItemBox(width, shape.base_size, object_height, spacing, ascent))
    return boxes


def line_gap(para_shape: ParaShape, line_height: int) -> int:
    """줄 아래에 더하는 간격(LINE_SEG line_spacing). rhwp compute_line_spacing_hwp."""
    kind, value = para_shape.line_spacing_type, para_shape.line_spacing
    if kind == PERCENT:
        # 퍼센트 간격은 4HU 배수로 반올림한다(rhwp: 165%×1400 → 912).
        raw = line_height * (value - 100) / 100
        return int(math.copysign(math.floor(abs(raw) / 4 + 0.5) * 4, raw))
    # 퍼센트가 아닌 줄 간격 값은 2배로 저장된다(저장 LINE_SEG 실측: issue1949 고정 3200 → 줄 피치 1600,
    # kps-ai 고정 2600 → 1300; rhwp style_resolver.rs 비-Percent 값 / 2).
    value //= 2
    if kind == FIXED:
        # 고정 간격이 글자보다 작으면 줄이 겹치도록 음수 간격을 둔다(실측: task2070 21쪽 「서론」 18pt·고정 16.6pt,
        # 다음 문단이 1.40pt 위).
        return value - line_height
    if kind == SPACE_ONLY:
        return value
    return max(0, value - line_height)


def _is_space(item) -> bool:
    return isinstance(item, TextChar) and item.char == " "


def _is_line_break(item) -> bool:
    return isinstance(item, ControlChar) and item.code == LINE_BREAK or isinstance(item, TextChar) and item.char == "\n"


def _is_latin(char: str) -> bool:
    code = ord(char)
    return any(start <= code <= end for start, end in _LATIN_RANGES)


def _units(chars: list, para_shape: ParaShape) -> list[tuple[int, int]]:
    """줄을 끊을 수 있는 단위의 (시작, 끝) 순번 목록. 공백 묶음·강제 줄 나눔은 따로 한 단위다."""
    by_char = para_shape.break_non_latin_word == 1
    latin_by_char = para_shape.break_latin_word == 2
    units: list[tuple[int, int]] = []
    start = 0
    count = len(chars)
    index = 0

    def kind(item) -> str:
        if _is_space(item):
            return "space"
        if _is_line_break(item):
            return "break"
        if isinstance(item, ControlChar):
            return "control"
        if not item.char.strip() and item.char not in ("　",):
            return "end"
        if _is_latin(item.char):
            return "latin"
        return "other"

    while index < count:
        start = index
        current = kind(chars[index])
        index += 1
        if current == "space":
            while index < count and kind(chars[index]) == "space":
                index += 1
        elif current in ("break", "end"):
            pass
        else:
            # 폭 없는 컨트롤은 뒤 글자와 한 단위로 묶는다(한글은 그 컨트롤을 다음 줄 머리에 둔다).
            while current == "control" and index < count and kind(chars[index]) == "control":
                index += 1
            if current == "control" and index < count and kind(chars[index]) in ("latin", "other"):
                current = kind(chars[index])
                index += 1
            if not by_char:
                while index < count and kind(chars[index]) in ("latin", "other", "control"):
                    if latin_by_char and kind(chars[index]) == "latin" and kind(chars[index - 1]) == "latin":
                        break
                    index += 1
            elif current == "latin" and not latin_by_char:
                while index < count and kind(chars[index]) == "latin":
                    index += 1
        units.append((start, index))
    return units


def break_lines(chars: list, boxes: list[ItemBox], para_shape: ParaShape, widths: tuple[float, float]) -> list[int]:
    """줄마다 첫 항목 순번을 돌려준다. widths는 (첫 줄 폭, 나머지 줄 폭)."""
    condense = min(_bits(para_shape.attr1, 9, 7), 75)
    starts = [0]
    line_width = 0.0
    space_width = 0.0
    real_seen = False
    line_ended = False
    placed = 0

    def available() -> float:
        return widths[0] if len(starts) == 1 else widths[1]

    def cap() -> float:
        return available() + (math.trunc(space_width * condense / 100) if condense else 0) + FIT_EPSILON

    def span(first: int, last: int) -> float:
        return sum(boxes[i].width for i in range(first, last))

    def empty() -> bool:
        return line_width == 0 and placed == 0

    def new_line(at: int, lead: float) -> None:
        nonlocal line_width, space_width, real_seen, line_ended, placed
        starts.append(at)
        line_width = lead
        space_width = 0.0
        real_seen = False
        line_ended = False
        placed = 0

    def break_before(position: int, width: float, splitting: bool = False) -> None:
        nonlocal real_seen, placed
        cut = position
        head = chars[position]
        line_start = starts[-1]
        if (
            not splitting
            and isinstance(head, TextChar)
            and head.char in FORBID_START
            and cut - 1 > line_start
            and not _is_space(chars[cut - 1])
        ):
            cut -= 1
        while cut - 1 >= line_start and isinstance(chars[cut - 1], TextChar) and chars[cut - 1].char in FORBID_END:
            cut -= 1
        if cut <= line_start:
            cut = position
        new_line(cut, span(cut, position) + width)
        real_seen = width > 0 or any(not _is_space(chars[i]) for i in range(cut, position))
        placed = 1 if position > cut else 0

    for first, last in _units(chars, para_shape):
        item = chars[first]
        if _is_space(item):
            count = last - first
            k = 0
            while k < count:
                if not line_ended and line_width >= available():
                    line_ended = True
                if line_ended and k >= 2 and first + k > starts[-1]:
                    break
                if not line_ended:
                    width = boxes[first + k].width
                    line_width += width
                    if real_seen:
                        space_width += width - boxes[first + k].spacing
                k += 1
            if k < count:
                new_line(first + k, span(first + k, last))
            continue
        if _is_line_break(item):
            if line_ended:
                new_line(first, 0.0)
            new_line(last, 0.0)
            continue
        width = span(first, last)
        if width == 0 and not isinstance(item, ControlChar):
            continue
        if line_ended:
            new_line(first, 0.0)
        if line_width + width - boxes[last - 1].spacing <= cap():
            line_width += width
            real_seen = True
            placed += 1
            continue
        if empty() or width > widths[1] + FIT_EPSILON:
            if para_shape.break_non_latin_word == 0 and not empty():
                break_before(first, 0.0)
            for index in range(first, last):
                box = boxes[index]
                if line_width + box.width - box.spacing > cap() and not empty():
                    break_before(index, 0.0, splitting=True)
                line_width += box.width
                real_seen = True
                placed += 1
            continue
        break_before(first, width)
        placed += 1
    # 문단 끝 강제 줄 나눔 뒤 빈 줄은 한글도 만들지 않는다(문단 나눔 글자가 그 줄을 끝낸다).
    while len(starts) > 1 and starts[-1] >= len(chars):
        starts.pop()
    return starts


def compose(
    chars: list,
    positions: list[int],
    boxes: list[ItemBox],
    para_shape: ParaShape,
    column_width: int,
    empty_height: int,
    start: int = 0,
    head: tuple[float, float] = (0.0, 0.0),
) -> list[LineSeg]:
    """세로 위치가 start에서 시작하는 합성 LineSeg 목록. head는 문단 머리가 첫 줄·나머지 줄에서 차지하는 폭."""
    # 문단 모양 여백·들여쓰기는 2배로 저장된다(layout.place_line과 같은 규칙).
    left = para_shape.left_margin // 2
    right = para_shape.right_margin // 2
    indent = para_shape.indent // 2
    segment_width = column_width - left - right
    first_width = segment_width - max(indent, 0) - head[0]
    rest_width = segment_width - max(-indent, 0) - head[1]
    starts = break_lines(chars, boxes, para_shape, (first_width, rest_width)) if chars else [0]
    segments: list[LineSeg] = []
    vertical = start
    for number, first in enumerate(starts):
        end = starts[number + 1] if number + 1 < len(starts) else len(chars)
        segment = _line_seg(chars, positions, boxes, para_shape, empty_height, number, first, end, vertical)
        segments.append(dataclasses.replace(segment, column_start=left, segment_width=segment_width))
        vertical += segment.line_height + segment.line_spacing
    return segments


def compose_around(
    chars: list,
    positions: list[int],
    boxes: list[ItemBox],
    para_shape: ParaShape,
    base: tuple[int, int],
    floats: list[tuple[float, float, float, float, int]],
    empty_height: int,
    start: int,
    head: tuple[float, float] = (0.0, 0.0),
    first_item: int = 0,
    first_number: int = 0,
    active_from: list[int] | None = None,
) -> list[LineSeg] | None:
    """compose와 같되 줄마다 그 줄 높이에서 어울림 개체를 피한 가로 구간(avoid_floats)으로 나눈다.

    한글은 줄을 나눌 때마다 지금 세로 위치의 구간을 다시 구한다(HwpApp FUN_101df690 → FUN_101dbfd0). 줄 높이는 줄
    첫 글자의 크기로 어림한다. 한 줄이 여러 구간으로 갈리면 아직 재현하지 못해 None. first_item·first_number는
    다시 나누기 시작하는 항목 순번과 그 줄 번호다(앞 줄은 저장 줄을 쓴다). active_from은 개체마다 그 조판 부호의
    항목 순번이다. 한글은 줄을 짜다 부호를 만나야 개체를 놓고 부호가 든 줄부터 피한다(개체 걸린 줄 일치 76→94%).
    """
    indent = para_shape.indent // 2
    segments: list[LineSeg] = []
    vertical, first, number = start, first_item, first_number
    reach = first_item
    while first < len(chars) or not segments:
        height = (boxes[first].height if first < len(boxes) else 0) or empty_height
        placed = [f for i, f in enumerate(floats) if active_from is None or active_from[i] < max(reach, first + 1)]
        ranges = avoid_floats(base, placed, vertical, height)
        if ranges is None:
            # 쓸 구간이 없으면 겹친 개체 아래로 내려간다.
            below = [
                top + h for _, top, _, h, _ in placed if top <= vertical < top + h or vertical < top < vertical + height
            ]
            if not below or min(below) <= vertical:
                return None
            vertical = math.ceil(min(below))
            continue
        if len(ranges) > 1:
            return None
        column_start, width = ranges[0]
        if number == 0:
            available = width - max(indent, 0) - head[0]
        else:
            available = width - max(-indent, 0) - head[1]
        starts = break_lines(chars[first:], boxes[first:], para_shape, (available, available)) if chars else [0]
        end = first + starts[1] if len(starts) > 1 else len(chars)
        if active_from is not None and any(first < item < end and item >= reach for item in active_from):
            # 이 줄에서 개체 부호를 만났다. 개체를 놓은 구간으로 같은 줄을 다시 나눈다.
            reach = end
            continue
        segment = _line_seg(chars, positions, boxes, para_shape, empty_height, number, first, end, vertical)
        segments.append(dataclasses.replace(segment, column_start=column_start, segment_width=width))
        vertical += segment.line_height + segment.line_spacing
        first, number = end, number + 1
    return segments


def compose_in_ranges(
    chars: list,
    positions: list[int],
    boxes: list[ItemBox],
    para_shape: ParaShape,
    ranges: list[tuple[int, int]],
    empty_height: int,
    head: tuple[float, float] = (0.0, 0.0),
    first_item: int = 0,
    first_number: int = 0,
) -> list[LineSeg]:
    """줄 번호마다 ranges의 가로 구간(단 안 시작, 폭)으로 나눈 합성 LineSeg. 구간보다 줄이 많으면 마지막 구간을 쓴다.

    first_item·first_number는 나누기 시작하는 항목 순번과 그 줄 번호다(ranges도 그 줄 번호로 찾는다).
    """
    indent = para_shape.indent // 2
    segments: list[LineSeg] = []
    vertical, first, number = 0, first_item, first_number
    while first < len(chars) or not segments:
        column_start, width = ranges[min(number, len(ranges) - 1)]
        if number == 0:
            available = width - max(indent, 0) - head[0]
        else:
            available = width - max(-indent, 0) - head[1]
        starts = break_lines(chars[first:], boxes[first:], para_shape, (available, available)) if chars else [0]
        end = first + starts[1] if len(starts) > 1 else len(chars)
        segment = _line_seg(chars, positions, boxes, para_shape, empty_height, number, first, end, vertical)
        segments.append(dataclasses.replace(segment, column_start=column_start, segment_width=width))
        vertical += segment.line_height + segment.line_spacing
        first, number = end, number + 1
    return segments


def _line_seg(
    chars: list,
    positions: list[int],
    boxes: list[ItemBox],
    para_shape: ParaShape,
    empty_height: int,
    number: int,
    first: int,
    end: int,
    vertical: int,
) -> LineSeg:
    """항목 first..end를 담는 number번째 줄의 합성 LineSeg(가로 구간은 호출한 쪽이 채운다).

    HwpApp FUN_101e3460: 줄 높이는 항목 높이(글자처럼 취급한 개체는 개체 높이)의 최댓값, 기준선은 항목 기준선 위
    몫의 최댓값이다. 줄 간격은 글자 크기(개체는 그 자리 글자 크기) 최댓값으로 정한다.
    """
    indent = para_shape.indent // 2
    items = range(first, end)
    line_height = max((boxes[i].object_height or boxes[i].height for i in items), default=0) or empty_height
    baseline_gap = max((boxes[i].ascent for i in items), default=0) or muldiv(line_height, ASCENT_PERCENT, 100)
    font_height = max((boxes[i].height for i in items), default=0) or empty_height
    flags = SINGLE_SEGMENT
    if (number == 0 and indent > 0) or (number > 0 and indent < 0):
        flags |= INDENT_APPLIED
    text_start = positions[first] if first < len(positions) else (positions[-1] + 1 if positions else 0)
    if para_shape.line_spacing_type == FIXED and not any(
        isinstance(chars[i], TextChar) and chars[i].char not in "\r\n" for i in items
    ):
        # 글자 없이 개체만 든 줄은 개체 높이를 빼 고정 간격을 맞춘다(저장 줄 실측: tac-img-02 그림 줄 th = 20500,
        # 고정 1800 간격 −18700. task2070 30쪽 수식만 든 줄도 정답 피치가 고정 18pt). 글자가 있는 줄까지 줄 높이로
        # 빼면 task2070이 −1,523자라 글자 크기를 쓴다.
        font_height = line_height
    gap = line_gap(para_shape, font_height)
    return LineSeg(text_start, vertical, line_height, line_height, baseline_gap, gap, 0, 0, flags)


#: 한글 2022가 줄 가로 구간을 잴 때 단 폭을 내리는 격자(HwpUnit).
RANGE_GRID = 4
#: 줄 가로 구간의 최소 폭(HwpApp FUN_101dbfd0, 호환 설정 0x10000이면 1mm지만 코퍼스에는 없다).
MIN_RANGE_WIDTH = 1440


def horizontal_range(
    para_shape: ParaShape, column_width: int, snap: bool = True, char_unit: int | None = None
) -> tuple[int, int] | None:
    """한글 2022가 떠 있는 개체를 피하기 전에 줄에 주는 가로 구간 (단 안 시작, 폭). 모르면 None.

    HwpApp FUN_101dbfd0: 왼쪽 = 왼쪽 여백, 오른쪽 = 단 폭 − 오른쪽 여백(여백은 2배로 저장된다). 저장 LINE_SEG가 이
    구간과 1HU라도 다르면 한글은 그 줄을 다시 나눈다(FUN_101dbcf0). 단 폭은 4HU로 내린 값이다: 1단 코퍼스에서 여백을
    뺀 뒤 내린 값과 다를 때 1,379줄 모두 먼저 내린 값과 같았다(kr_156369057 48190 → 48188 − 515 = 47673). 폭이 서로
    다른 다단(snap=False)은 내리지 않는다(KTX 33355). 여백을 뺀 폭이 최소 폭보다 좁으면 여백을 버리고 단 왼쪽부터
    최소 폭을 준다(0x101dc145~0x101dc182, 좁은 셀 저장 줄 (0, 1440)).

    낮은 비트가 선 여백은 글자 단위(백분율)라 HncIntMulDiv(여백>>1, char_unit, 100)이다. char_unit은 FUN_101b74f0
    (둘째 인자 1)의 값으로 document_char_unit이 구한다. 모르면(None) 그런 문단은 None을 돌려준다.
    """
    margins = []
    for margin in (para_shape.left_margin, para_shape.right_margin):
        if margin & 1:
            if char_unit is None:
                return None
            margins.append(muldiv(margin >> 1, char_unit, 100))
        else:
            margins.append(margin >> 1)
    if snap:
        column_width -= column_width % RANGE_GRID
    left = margins[0]
    right = column_width - margins[1]
    if right - left < MIN_RANGE_WIDTH:
        return 0, MIN_RANGE_WIDTH
    return left, right - left


def document_char_unit(char_shapes: list[CharShape], style_char_shape: int | None) -> int:
    """글자 단위 여백의 한 글자(HwpApp FUN_101b74f0, 둘째 인자 1). 문서 상태의 글자 모양 기준 크기의 절반이다
    (환경 옵션 비트 4가 서면 절반 하지 않음, 기본값은 꺼짐). 그 글자 모양은 문단과 상관없이 문서에서 하나라 첫 스타일
    (바탕글)의 글자 모양으로 본다: 코퍼스 홀수 여백 문단 209개의 저장 줄 시작이 모두 이 값으로 맞았다(글자 모양 0번은
    153개). 문서가 없으면 500.
    """
    if style_char_shape is None or style_char_shape >= len(char_shapes):
        return 500
    return char_shapes[style_char_shape].base_size // 2


def avoid_floats(
    base: tuple[int, int], floats: list[tuple[float, float, float, float, int]], top: float, height: float
) -> list[tuple[int, int]] | None:
    """가로 구간 base(시작, 폭)에서 어울림 개체를 피한 구간 목록(시작, 폭). 줄이 개체 아래로 내려가야 하면 None.

    floats는 단 기준 바깥 상자 (왼쪽, 위, 폭, 높이, 글 흐름)이고 top·height는 줄의 위 끝과 줄 높이다.
    HwpApp FUN_101dbfd0·FUN_101bb480(겹침 판정)·FUN_1027ab80(흐름별 구간 빼기) 재현.
    """
    start = base[0]
    end = base[0] + base[1]
    ranges = [[start, end]]
    for left, obj_top, width, obj_height, flow in floats:
        obj_left, obj_right = round(left), round(left + width)
        if not (obj_left <= start < obj_right or start < obj_left < end):
            continue
        if not (obj_top <= top < obj_top + obj_height or top < obj_top < top + height):
            continue
        if not width or not obj_height:
            continue
        ranges = _subtract_object(ranges, flow, obj_left, obj_right)
    if len(ranges) > 1:
        ranges = [r for r in ranges if r[1] - r[0] >= MIN_RANGE_WIDTH]
    if not ranges or all(r[1] - r[0] < MIN_RANGE_WIDTH for r in ranges):
        return None
    return [(r0, r1 - r0) for r0, r1 in ranges]


def _subtract_object(ranges: list[list[int]], flow: int, left: int, right: int) -> list[list[int]]:
    """구간들에서 개체 가로 자리 [left, right)를 글 흐름(0 양쪽, 1 왼쪽, 2 오른쪽, 3 큰 쪽)에 따라 뺀다."""
    out = [list(r) for r in ranges]
    for i, (r0, r1) in enumerate(out):
        inside = r0 < left and r1 > right
        if r1 <= left:
            if flow == 2:
                out[i][1] = r0
            continue
        if inside:
            if flow == 0:
                return [*out[:i], [r0, left], [right, r1], *out[i + 1 :]]
            if flow == 1:
                return [*out[:i], [r0, left]]
            if flow == 2:
                out[i][0] = right
                return out
            # 큰 쪽: 왼쪽이 좁거나 꼭 최소 폭(1440)이면 오른쪽을 남긴다.
            if left - r0 < r1 - right or left - r0 == MIN_RANGE_WIDTH:
                out[i][0] = right
            else:
                out[i][1] = left
            return out
        if r1 <= right:
            covers_right_end = r0 < left
            if flow == 1:
                return [*out[:i], [r0, left]] if covers_right_end else out[:i]
            out[i][1] = left if covers_right_end and flow != 2 else r0
            continue
        if flow == 1:
            return out[:i]
        if r0 < right:
            out[i][0] = right
        return out
    return out


def paragraph_gap(previous: ParaShape | None, current: ParaShape) -> int:
    """앞 문단 끝에서 이 문단 첫 줄까지 띄우는 간격. 문단 위·아래 간격도 2배로 저장된다(저장 줄 2196/2223 일치)."""
    return ((previous.space_after if previous else 0) + current.space_before) // 2


def paginate(segments: list[LineSeg], body_height, page_top: int) -> list[LineSeg]:
    """줄 아래가 본문 바닥을 넘으면 그 줄부터 다음 쪽(단) 위로 올린다. 문단 첫 줄은 page_top에서 시작한다.

    body_height는 높이이거나, (줄 번호, 그 쪽 첫 줄 번호)를 받아 그 줄이 쓸 수 있는 높이를 돌려주는 함수다.
    """
    placed: list[LineSeg] = []
    shift = 0
    page_first = 0
    for number, segment in enumerate(segments):
        top = segment.vertical_pos + shift
        restart = page_top if number == 0 else 0
        room = body_height(number, page_first) if callable(body_height) else body_height
        # 저장 LINE_SEG 실측: 줄 아래(줄 간격 제외)가 본문 높이를 넘은 줄만 다음 쪽으로 간다.
        if top + segment.line_height > room and top > restart:
            page_first = number
            shift += restart - top
            top = restart
        placed.append(dataclasses.replace(segment, vertical_pos=top))
    return placed


def flow_end(view: ParagraphView, objects: bool = True) -> int:
    """다음 문단이 이어질 세로 위치(문단 아래 간격 제외). objects면 위아래 배치 개체가 줄보다 길 때 개체 아래다."""
    last = view.segments[-1]
    end = last.vertical_pos + last.line_height + last.line_spacing
    top = view.segments[0].vertical_pos
    for ctrl in view.controls.values() if objects else ():
        model = ctrl.model
        if (
            isinstance(model, ObjectCtrlHeader)
            and not model.treat_as_char
            and model.text_wrap == TextWrap.TOP_AND_BOTTOM
            and model.vert_rel_to == VertRelTo.PARA
        ):
            # 저장 LINE_SEG 실측(86712): 다음 문단 vpos = 개체 문단 vpos + 세로 오프셋 + 높이 + 위·아래 바깥 여백.
            margins = model.outer_margins or [0, 0, 0, 0]
            end = max(end, top + model.vert_offset + model.height + margins[2] + margins[3])
    return end


def paragraph_font_height(view: ParagraphView, char_shapes: list[CharShape]) -> int:
    """글자가 없는 줄의 높이로 쓰는 문단 첫 글자 모양의 크기."""
    shape_node = view.node.find(tags.PARA_CHAR_SHAPE)
    runs = shape_node.model.runs if shape_node is not None and shape_node.model.__dict__.get("runs") else [(0, 0)]
    shape_id = view.shape_ids[-1] if view.shape_ids else runs[0][1]
    return char_shapes[shape_id].base_size


def _bits(value: int, start: int, count: int) -> int:
    return (value >> start) & ((1 << count) - 1)
