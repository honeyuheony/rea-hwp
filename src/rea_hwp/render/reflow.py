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
#: 폭 비교의 반올림 여유(kordoc simulateWrap EPS).
FIT_EPSILON = 0.5
#: LINE_SEG 태그 bit17/18: 줄의 첫/마지막 세그먼트. 저장 줄은 대개 둘 다 켜져 있다.
SINGLE_SEGMENT = (1 << 17) | (1 << 18)
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


def measure_items(view: ParagraphView, item_width, char_shapes: list[CharShape], slot_of) -> list[ItemBox]:
    """item_width(순번)는 layout의 글자·컨트롤 전진폭 함수, slot_of(글자, 글자 모양)는 폭을 잰 언어 슬롯 함수다."""
    boxes = []
    for index, item in enumerate(view.chars):
        width = item_width(index)
        shape = char_shapes[view.shape_ids[index]]
        spacing, object_height = 0.0, 0
        if isinstance(item, TextChar):
            percent = shape.spacings[slot_of(item.char, shape)]
            spacing = width * percent / (100 + percent) if percent > -100 else 0.0
        else:
            ctrl = view.controls.get(index)
            if ctrl is not None and isinstance(ctrl.model, ObjectCtrlHeader) and ctrl.model.treat_as_char:
                margins = ctrl.model.outer_margins or [0, 0, 0, 0]
                object_height = ctrl.model.height + margins[2] + margins[3]
        boxes.append(ItemBox(width, shape.base_size, object_height, spacing))
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
        return max(0, value - line_height)
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
) -> list[LineSeg]:
    """세로 위치가 start에서 시작하는 합성 LineSeg 목록. 쪽 나누기는 paginate가 한다."""
    # 문단 모양 여백·들여쓰기는 2배로 저장된다(layout.place_line과 같은 규칙).
    left = para_shape.left_margin // 2
    right = para_shape.right_margin // 2
    indent = para_shape.indent // 2
    segment_width = column_width - left - right
    first_width = segment_width - max(indent, 0)
    rest_width = segment_width - max(-indent, 0)
    starts = break_lines(chars, boxes, para_shape, (first_width, rest_width)) if chars else [0]
    segments: list[LineSeg] = []
    vertical = start
    for number, first in enumerate(starts):
        end = starts[number + 1] if number + 1 < len(starts) else len(chars)
        text_height = max((boxes[i].height for i in range(first, end)), default=0) or empty_height
        line_height = max([text_height] + [boxes[i].object_height for i in range(first, end)])
        flags = SINGLE_SEGMENT
        if (number == 0 and indent > 0) or (number > 0 and indent < 0):
            flags |= INDENT_APPLIED
        text_start = positions[first] if first < len(positions) else (positions[-1] + 1 if positions else 0)
        gap = line_gap(para_shape, text_height)
        segments.append(
            LineSeg(
                text_start,
                vertical,
                line_height,
                text_height,
                round(line_height * BASELINE_RATIO),
                gap,
                left,
                segment_width,
                flags,
            )
        )
        vertical += line_height + gap
    return segments


def paragraph_gap(previous: ParaShape | None, current: ParaShape) -> int:
    """앞 문단 끝에서 이 문단 첫 줄까지 띄우는 간격. 문단 위·아래 간격도 2배로 저장된다(저장 줄 2196/2223 일치)."""
    return ((previous.space_after if previous else 0) + current.space_before) // 2


def paginate(segments: list[LineSeg], body_height: int, page_top: int) -> list[LineSeg]:
    """줄 아래가 본문 바닥을 넘으면 그 줄부터 다음 쪽(단) 위로 올린다. 문단 첫 줄은 page_top에서 시작한다."""
    placed: list[LineSeg] = []
    shift = 0
    for number, segment in enumerate(segments):
        top = segment.vertical_pos + shift
        restart = page_top if number == 0 else 0
        # 저장 LINE_SEG 실측: 줄 아래(줄 간격 제외)가 본문 높이를 넘은 줄만 다음 쪽으로 간다.
        if top + segment.line_height > body_height and top > restart:
            shift += restart - top
            top = restart
        placed.append(dataclasses.replace(segment, vertical_pos=top))
    return placed


def flow_end(view: ParagraphView) -> int:
    """다음 문단이 이어질 세로 위치(문단 아래 간격 제외). 위아래 배치 개체가 줄보다 길면 개체 아래다."""
    last = view.segments[-1]
    end = last.vertical_pos + last.line_height + last.line_spacing
    top = view.segments[0].vertical_pos
    for ctrl in view.controls.values():
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
