"""표를 본문 흐름에 놓고, 쪽 바닥을 넘으면 쪽 단위로 나눈다."""

from __future__ import annotations

import dataclasses
import itertools
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass

from .. import tags
from ..document import Node
from ..errors import CorruptFileError
from ..models.controls import CaptionListHeader, ObjectCtrlHeader, TextWrap, VertRelTo
from ..models.paragraph import LineSeg
from . import reflow
from .common import (
    ALIGN_CENTER,
    ALIGN_END,
    ALIGN_START,
    MAX_TABLE_PAGES,
    REL_COLUMN_OR_PARA,
    SPLIT_CELLS,
    TAC_ASCENT,
    Area,
    PageState,
    ParagraphView,
    SectionGeometry,
    column_layout,
)
from .scene import Image, Line
from .table_paging import (
    MIN_UNIT,
    MODE_LINES,
    MODE_NONE,
    MODE_ROWS,
    CellBox,
    PagingBlock,
    PagingCell,
    PagingLine,
    PagingTable,
    TableFragment,
    paginate_table,
)
from .tables import (
    LINE_FIRST_SEGMENT,
    Cell,
    TableGrid,
    cell_column_def,
    cell_columns,
    cell_margins,
    cell_pieces,
    draw_cell_boxes,
    draw_table_background,
    draw_table_frame,
    header_row_count,
    solve_rows,
    stored_height,
    stored_lines,
    stored_pieces,
    table_grid,
)

#: 중첩 표 격자 높이가 저장 높이의 이 배수를 넘으면 쪽을 넘는 표로 보고 격자 높이를 셀 높이에 쓴다(경험값).
NESTED_SPLIT_RATIO = 1.5
#: 위·아래쪽 캡션 방향(명세 표72, objects.py와 같은 값).
CAPTION_TOP = 2
CAPTION_BOTTOM = 3
#: 쪽 맨 위로 보는 여유(HU): 표 위 끝이 본문 위 + 바깥 위 여백에서 이만큼 안이면 이미 쪽 맨 위다(검증 안 됨: 1pt).
PAGE_TOP_SLACK = 100
BIG_ROOM = 1e12
#: 표 쪽 나눔(명세 표75 bit 0~1): 0 나누지 않음.
NO_SPLIT = 0
#: 표를 쪽에서 자를 때 바깥 아래 여백 위로 더 남기는 길이(HU). 남은 표가 통째로 들어가면 쓰지 않는다.
#: 실측: task2287 25~34쪽 이어지는 표 조각 아래 끝이 본문 바닥 − 바깥 여백보다 1.18~1.20pt 위,
#: issue2063 40쪽은 그 위치에서 0.01pt 넘는 행을 다음 쪽으로 넘김, exam_eng 2쪽 통째 표는 바닥에 딱 맞게 들어감.
#: 이 값이 무엇에서 나오는지는 확인하지 못했다.
SPLIT_RESERVE = 120
#: 셀 안에서도 나누는 표에서 행을 통째로 이 쪽에 둘 때 남기는 길이(HU). 실측 경계: 편람 352쪽 바닥 − 109 행은 이 쪽,
#: issue5699 11쪽 바닥 − 38 행은 다음 쪽. 80은 전체 평가로 고른 값이다(30·50·60·90·110보다 좋음, 검증 안 됨).
ROW_RESERVE = 80
#: 쪽을 넘는 여러 행 셀을 늘린 뒤 쪽 조각을 다시 계산하는 최대 횟수(경험값, 검증 안 됨).
CARRIED_FIT_ROUNDS = 4
#: 쪽을 넘는 행의 마지막 조각을 쪽 바닥까지 늘릴 때 남기는 여유(HWPUNIT).
LAST_PIECE_BOTTOM_SLACK = 60


@dataclass
class HostFragments:
    """쪽을 넘는 바깥 표의 쪽 조각. 셀 안 자리차지 표는 이 조각을 따라 함께 나뉜다."""

    #: 바깥 표 세로 좌표의 쪽 조각 구간.
    fragments: list[tuple[float, float]]
    #: 쪽 조각마다 쪽 상태와 구간 시작이 놓인 쪽 y.
    states: list[PageState]
    origins: list[float]
    #: 지금 그리는 쪽 조각 번호.
    number: int = 0
    #: 쪽 조각마다 보이는 아래 끝의 쪽 y(없으면 조각 끝).
    bottoms: list[float] | None = None


@dataclass
class TablePlacement:
    """그려진 표의 자리. 캡션을 붙일 때 쓴다."""

    first_state: PageState
    left: float
    top: float
    last_state: PageState
    bottom: float


@dataclass
class CellLine:
    """셀 줄 하나(세로 좌표는 표 맨 위 기준)."""

    top: float
    bottom: float
    view: ParagraphView
    index: int
    inner_x: float
    inner_width: float
    #: 셀 문단 목록의 vertical_pos 0 위치.
    origin: float
    #: 여러 행 셀의 저장 쪽 조각 순번. 0이 아니면 셀이 시작한 쪽 조각에서 이만큼 뒤 쪽 조각 맨 위에 놓는다.
    carry: int = 0
    cell_top: float = 0.0
    margin_top: float = 0.0
    cell_bottom: float = 0.0
    margin_bottom: float = 0.0
    #: 셀 세로 정렬(0 위, 1 가운데, 2 아래)과 이 줄이 속한 쪽 조각의 내용 높이. 쪽에 보이는 셀 부분 안 정렬에 쓴다.
    vert_align: int = 0
    piece_height: float = 0.0
    #: 여러 행 셀이나 쪽을 넘는 중첩 표 뒤 문단의 줄이면 True. 줄 위치가 아니라 쪽 조각 순번(carry)이나
    #: anchor로 쪽 조각을 고르고 그 쪽에 보이는 셀 부분 안에서 놓는다.
    carried: bool = False
    #: 쪽 조각을 이 세로 좌표가 든 조각으로 고르고 그 조각 맨 위(셀 위 여백 아래)를 문단 원점으로 삼는다.
    anchor: float | None = None
    #: 보이는 셀 부분의 남는 자리가 이보다 작을 때만 세로 정렬한다(쪽을 넘는 한 행 셀의 첫 조각).
    room_limit: float = float("inf")
    #: LINE_SEG 없이 합성한 줄이면 True.
    synthetic: bool = False
    #: 셀 오른쪽 끝(표 왼쪽 기준). 0이면 안쪽 오른쪽 끝을 쓴다.
    cell_right: float = 0.0


def clip_images_x(items: list, start: int, clip_left: float, clip_right: float) -> None:
    """items[start:]의 그림 가운데 [clip_left, clip_right] 밖으로 나간 부분을 잘라 낸다."""
    for index in range(start, len(items)):
        image = items[index]
        if not isinstance(image, Image) or image.width <= 0:
            continue
        shown_left, shown_right = max(image.x, clip_left), min(image.x + image.width, clip_right)
        if shown_left <= image.x and shown_right >= image.x + image.width:
            continue
        if shown_right <= shown_left:
            continue
        crop_left, crop_top, crop_right, crop_bottom = image.crop or (0.0, 0.0, 1.0, 1.0)
        span = crop_right - crop_left
        new_left = crop_left + span * (shown_left - image.x) / image.width
        new_right = crop_left + span * (shown_right - image.x) / image.width
        items[index] = dataclasses.replace(
            image, x=shown_left, width=shown_right - shown_left, crop=(new_left, crop_top, new_right, crop_bottom)
        )


def assign_fragments(
    cell_lines: list[CellLine], fragments: list[tuple[float, float]], visible_ends: list[float] | None = None
) -> dict[int, list]:
    """쪽 조각 번호마다 (셀 줄, 쪽 조각 원점에서 셀 문단 원점까지 거리). 줄 위 끝이 든 쪽 조각에 놓는다.

    visible_ends는 쪽 조각마다 보이는 표의 아래 끝이다. 표가 이어지는 쪽에서는 자른 곳보다 아래인 쪽 바닥까지 보인다.
    """
    real = [number for number, (start, end) in enumerate(fragments) if end > start or number == len(fragments) - 1]
    starts = [fragments[number][0] for number in real]
    assigned: dict[int, list] = {}
    for line in cell_lines:
        if not (line.carried or line.carry):
            number = real[max(0, bisect_right(starts, line.top) - 1)]
            # LINE_SEG 없이 합성한 셀 줄은 이어지는 쪽 조각에서 셀 위 여백 아래에 놓는다(실측: 80168 14쪽 이어진 셀
            # 첫 줄 −1.53pt). 저장 줄은 저장 위치에 이미 들어 있다(넣으면 issue1949 −81k).
            shift = line.margin_top if line.synthetic and line.cell_top < fragments[number][0] else 0.0
            assigned.setdefault(number, []).append((line, line.origin - fragments[number][0] + shift))
            continue
        anchor = line.cell_top if line.anchor is None else line.anchor
        position = max(0, bisect_right(starts, anchor) - 1) + line.carry
        number = real[min(position, len(real) - 1)]
        start = fragments[number][0]
        end = visible_ends[number] if visible_ends else fragments[number][1]
        top = max(line.cell_top, start)
        # 쪽 조각은 그 쪽에 보이는 셀 부분 안에서 세로 정렬한다(실측: task2287 26·27쪽 가운데 정렬 −2.98·−4.92pt,
        # table_scattered_header_rowbreak 2쪽 「강관말뚝」).
        room = min(line.cell_bottom, end) - top - line.margin_top - line.margin_bottom - line.piece_height
        shift = {1: room / 2, 2: room}.get(line.vert_align, 0.0) if 0 < room < line.room_limit else 0.0
        assigned.setdefault(number, []).append((line, top - start + line.margin_top + shift))
    return assigned


def fit_carried_cells(
    grid: TableGrid, fragments: list[tuple[float, float]], page_ends: list[float], single: bool = False
) -> list[float] | None:
    """쪽을 넘는 여러 행 셀(single이면 저장 쪽 조각이 있는 한 행 셀)이 쪽마다 그려진 높이를 맞추도록 다시 푼 행 경계.

    늘릴 셀이 없으면 None.

    page_ends는 쪽 조각마다 쪽 바닥의 표 세로 좌표다. 두 조건 중 큰 쪽을 셀이 걸친 행 높이 합의 하한으로 준다.
    - 마지막 저장 조각이 그 쪽에 남은 셀 부분에 들어간다(실측: task2287 25~29쪽 행 2~3 셀 조각 5개, 29쪽 남은 부분
      = 마지막 조각 15300 + 셀 위·아래 여백, 정답 155.68pt, 행 저장 높이로는 108.88pt).
    - 저장 셀 높이는 쪽마다 그려진 부분의 합이다(실측: task2287 29~32쪽 행 10~26 셀 저장 110835, 11행을 29쪽 바닥에서
      자르고 나뉜 행 16·24를 쪽 바닥까지 그린 합으로 행 25~26 = 11680, 정답 116.70pt).
    """
    if grid.spacing:
        return None
    real = [number for number, (start, end) in enumerate(fragments) if end > start or number == len(fragments) - 1]
    starts = [fragments[number][0] for number in real]
    required: dict[int, float] = {}
    grows = False
    for cell in grid.cells:
        if (cell.header.row_span == 1) != single or single and len(cell_pieces(cell)[1]) < 2:
            continue
        _, top, _, span = grid.cell_box(cell)
        bottom = top + span
        first = max(0, bisect_right(starts, top) - 1)
        if first == len(real) - 1 or bottom <= starts[first + 1]:
            continue
        drawn = 0.0
        for position in range(first, len(real)):
            number = real[position]
            visible_end = fragments[number][1] if number == len(fragments) - 1 else page_ends[number]
            drawn += max(0.0, min(bottom, visible_end) - max(top, fragments[number][0]))
        need = stored_height(cell) - drawn
        _, heights = cell_pieces(cell)
        margins = cell_margins(grid.table, cell)
        if len(heights) == 1:
            need = max(need, max(heights[0], cell.floor) + margins[2] + margins[3] - span)
        position = first + len(heights) - 1
        # 우리 쪽 나눔에서 셀이 마지막 조각의 쪽까지 이어지지 않으면 저장 쪽 나눔과 달라졌으니 조각으로 늘리지 않는다.
        if len(heights) > 1 and position < len(real) and bottom > starts[position]:
            need = max(need, max(top, starts[position]) + margins[2] + heights[-1] + margins[3] - bottom)
        required[id(cell)] = span + need
        grows = grows or need > 0.5
    if not grows:
        return None
    return solve_rows(grid.table, grid.cells, len(grid.row_y) - 1, grid.piece_starts, required)


def _restored_baseline(segment: LineSeg) -> LineSeg:
    """줄 높이의 절반으로 저장된 셀 줄 기준선 간격을 글자 높이의 0.85로 되돌린다.

    실측: 해외직접투자 보도자료(hwpx-h-01 4쪽) 숫자 셀은 기준선 간격 600, 줄 높이 1200, 줄 간격 0으로
    저장되었지만 한글 2022 PDF 기준선은 셀 줄 위 끝에서 1020(0.85 × 1200) 아래다.
    """
    if segment.text_height and segment.baseline_gap * 2 == segment.line_height:
        return dataclasses.replace(segment, baseline_gap=round(segment.text_height * TAC_ASCENT))
    return segment


def is_block_table(ctrl: Node) -> bool:
    """셀 문단 기준 위쪽에 놓이는 자리차지 표(바깥 표와 함께 쪽을 넘는다)."""
    model = ctrl.model
    return (
        ctrl.record.payload[:4] == tags.TABLE_CTRL.to_bytes(4, "little")
        and isinstance(model, ObjectCtrlHeader)
        and not model.treat_as_char
        and model.text_wrap == TextWrap.TOP_AND_BOTTOM
        and model.vert_rel_to == REL_COLUMN_OR_PARA
        and model.vert_align == ALIGN_START
    )


def _margin_shift(align: int, start_margin: int, end_margin: int) -> float:
    if align == ALIGN_CENTER:
        return 0.0
    if align in (ALIGN_END, 4):
        return -end_margin
    return start_margin


#: 엔진이 다루는 표 쪽 나눔 방식.
PAGING_MODES = (MODE_NONE, MODE_ROWS, MODE_LINES)


def paging_eligible(
    grid: TableGrid,
    views: dict[int, list[ParagraphView]],
    model: ObjectCtrlHeader,
    nested_ok: Callable[[Node], bool],
) -> bool:
    """table_paging으로 나눌 표인가. 다단·세로쓰기 셀·크기 보호 표는 제외한다.

    셀 간격 표와 나누지 않는 표(쪽 나눔 방식 0)도 엔진으로 나눈다(전체 평가 편람 +302, 떨어진 문서 없음).
    글자처럼 취급한 본문 표도 엔진으로 나눈다. 줄 안 글자라 들어가면 나누지 않는 처리는 draw_paged_table이 한다.

    셀 안 자리차지 표는 nested_ok(그 표도 엔진으로 나눌 수 있는가)를 만족해야 한다. 셀 안 비자리차지 표는
    문단 기준(_is_reaching_table)이어야 한다.
    """
    if model.protect_size or grid.table.page_break not in PAGING_MODES:
        return False
    table_ctrl = tags.TABLE_CTRL.to_bytes(4, "little")
    for cell in grid.cells:
        if cell.header.text_direction or cell_column_def(cell) is not None:
            return False
        # 글자처럼 취급한 셀 안 표는 그 줄 높이에 들어 있어 줄 하나로, 자리차지 표는 셀 글 항목으로 다룬다.
        for view in views[id(cell)]:
            for ctrl in view.controls.values():
                if ctrl.record.payload[:4] != table_ctrl or ctrl.model.treat_as_char:
                    continue
                if is_block_table(ctrl) and not nested_ok(ctrl):
                    return False
                if not is_block_table(ctrl) and not _is_reaching_table(ctrl):
                    return False
    return True


def inline_table_rooms(
    height: float, first_available: float, next_available: float, at_page_top: bool
) -> tuple[float, float]:
    """글자처럼 취급한 표의 (첫 조각, 다음 조각) 가용 높이. 표는 줄의 일부라 나누지 않는다.

    남은 높이에 들어가거나 이미 쪽 맨 위면 그 자리에 통째로 그린다(한글 2022 실험: law_18453497 첫 표 마지막 행을
    15000HU 늘려 용지 밖까지 넘쳐도 한 쪽). 새 쪽에도 안 들어가면 그 자리에서 넘쳐 그린다(kr_156489570 53쪽 나누지
    않는 표 183635HU, 나눔 설정 표는 검증 안 됨). 나머지는 통째로 다음 쪽 맨 위로 옮긴다(software.hwp 「SW 내부 설계」).
    """
    if height <= first_available or at_page_top or height > next_available:
        return BIG_ROOM, next_available
    return 0.0, BIG_ROOM


@dataclass
class NestedInput:
    """셀 안 자리차지 표 하나의 엔진 입력과 그리기 자료."""

    ctrl: Node
    table: PagingTable
    grid: TableGrid
    sources: dict[int, list[tuple[ParagraphView, int] | None]]


@dataclass(frozen=True)
class StackedFloat:
    """셀 글 항목으로 쌓은 떠 있는 개체(같은 문단 앞 개체 아래에 놓인다)."""

    view: ParagraphView
    ctrl: Node


CellSource = tuple[ParagraphView, int] | StackedFloat | None


def cell_items(
    views: list[ParagraphView], nested: Callable[[Node], NestedInput]
) -> tuple[list[PagingLine | PagingBlock], list[CellSource]]:
    """셀 글을 줄과 자리차지 표 항목으로, 저장 쪽 조각의 되감김 없이 이어 붙인 세로 위치로 다시 센다.

    앞 줄보다 위이거나 같은 위치의 새 줄(첫 세그먼트)이면 되감김이다. 되감긴 줄은 앞 줄 위 + 줄 높이 + 줄 간격
    (문단이 바뀌면 문단 간격 더함)에 놓는다. 자리차지 표 뒤 줄은 표 아래(바깥 아래 여백 포함)보다 위로 오지 않는다.
    """
    items: list[PagingLine | PagingBlock] = []
    sources: list[CellSource] = []
    offset = 0.0
    previous: tuple[float, LineSeg, int, ParagraphView] | None = None
    last_end = 0.0
    flow_end = 0.0
    floor = 0.0
    para_top = 0.0
    after: list[tuple[Node, NestedInput]] = []
    #: 이 셀에 놓인 겹침 불가 개체 상자(가로 시작, 가로 끝, 위, 아래). 뒤 문단 개체가 피한다.
    float_boxes: list[tuple[float, float, float, float]] = []
    chained = False
    for number, view in enumerate(views):
        for index, segment in enumerate(view.segments):
            if previous is not None:
                top, last, paragraph, last_view = previous
                stored = last.vertical_pos
                if segment.vertical_pos < stored or (
                    segment.vertical_pos == stored and segment.flags & LINE_FIRST_SEGMENT
                ):
                    restart = top + last.line_height + last.line_spacing
                    if paragraph != number:
                        restart += reflow.paragraph_gap(last_view.para_shape, view.para_shape)
                    offset = restart - segment.vertical_pos
                    for item, source in zip(items, sources, strict=True):
                        # 저장 쪽 조각 경계에 걸친 개체는 다음 조각 맨 위(vertical_pos 0)에 놓인다(실측:
                        # rowbreak-problem 17쪽 글상자 74.9pt = 조각 위, 뒤 줄은 저장 위치 그대로. 개체 자리면 +9.67pt).
                        if isinstance(source, StackedFloat) and item.top < offset < item.top + item.height:
                            item.top = offset
            top = segment.vertical_pos + offset
            if index == 0:
                after = []
                for ctrl in _block_tables(view):
                    model = ctrl.model
                    margins = model.outer_margins or [0, 0, 0, 0]
                    # 표는 문단 위 + 양수 오프셋 + 바깥 위 여백에 놓되 기준 문단 줄을 끊지 않고 그 아래로 간다(실측:
                    # rowbreak-problem 7쪽 표 8 행 2: 다음 문단 20301 = 둘째 줄 아래 6380 + 283 + 13331 + 283 + 24).
                    # 첫 줄이 앞 문단 끝에서 표 차지 높이만큼 밀려 저장됐으면 표는 줄 위에 있다(inner-table-01 31301).
                    extent = max(0, model.vert_offset) + margins[2] + model.height + margins[3]
                    extent += _vertical_caption_extent(ctrl)
                    pushed = segment.vertical_pos - last_end >= extent - 1
                    data = nested(ctrl)
                    if pushed:
                        base = flow_end if previous is not None else top
                        block = PagingBlock(
                            base + max(0, model.vert_offset) + margins[2], data.table, margins[2], margins[3], data
                        )
                        items.append(block)
                        sources.append(None)
                        floor = max(floor, block.end)
                    else:
                        after.append((ctrl, data))
            if top < floor:
                offset += floor - top
                top = floor
            if not items:
                lead = top
            elif index == 0:
                lead = min(top, reflow.paragraph_gap(None, view.para_shape))
            else:
                lead = 0.0
            stacked = _stacked_floats(view) if index == 0 else []
            if index == 0:
                below = [] if stacked else _floats_below_lines(view)
            reach = (
                _floating_reach(view, segment.vertical_pos, stacked[1:] + below) - segment.vertical_pos
                if index == 0
                else 0.0
            )
            # 행 높이는 글자 높이로 측정한다(tables.stored_pieces와 같다, issue1510).
            items.append(PagingLine(top, segment.text_height, lead, reach))
            sources.append((view, index))
            stack_bottom = top + reach
            if stacked:
                first_model = stacked[0].model
                float_boxes.append(
                    (
                        first_model.horz_offset,
                        first_model.horz_offset + first_model.width,
                        top + max(0, first_model.vert_offset),
                        stack_bottom,
                    )
                )
            for ctrl in stacked[1:]:
                # 겹칠 수 없는 개체는 앞 개체 아래에 쌓고, 셀은 개체 사이에서 쪽을 나눈다. 음수 세로 오프셋은 쓰지
                # 않는다(실측: issue2004 셀 문단 하나의 그림 5장, 정답 4~8쪽마다 한 장씩 조각 위 + 셀 위 여백).
                model = ctrl.model
                margins = model.outer_margins or [0, 0, 0, 0]
                extent = margins[2] + model.height + margins[3]
                items.append(PagingLine(stack_bottom, extent))
                sources.append(StackedFloat(view, ctrl))
                float_boxes.append(
                    (model.horz_offset, model.horz_offset + model.width, stack_bottom, stack_bottom + extent)
                )
                stack_bottom += extent
                if model.text_wrap == TextWrap.TOP_AND_BOTTOM:
                    # 어울림 개체 옆에는 글이 흐르고 한글은 그 결과를 저장 줄에 남긴다(issue1921 13쪽 셀: 그림 둘 옆
                    # 빈 줄 1432·2864…). 자리차지 개체만 뒤 줄을 밀어낸다.
                    floor = max(floor, stack_bottom)
            if index == 0:
                # 문단 기준 개체 위치는 문단 위 간격 앞이다(본문 objects.floating_position과 같다). kr_156489673 2쪽
                # 셀 표: 줄 27392 = 앞 줄 끝 25392 + 위 간격 2000, 정답 표 위 = 25392 + 오프셋 3918 + 바깥 위 여백.
                previous_end = flow_end if previous is not None else 0.0
                para_top = max(top - view.para_shape.space_before // 2, previous_end)
                for ctrl in _no_overlap_floats(view):
                    if any(ctrl is other for other in stacked + below):
                        continue
                    model = ctrl.model
                    margins = model.outer_margins or [0, 0, 0, 0]
                    extent = margins[2] + model.height + _vertical_caption_extent(ctrl) + margins[3]
                    left, right = model.horz_offset, model.horz_offset + model.width
                    natural = start = para_top + model.vert_offset
                    moved = True
                    while moved:
                        moved = False
                        for box_left, box_right, box_top, box_bottom in float_boxes:
                            if (
                                left < box_right
                                and box_left < right
                                and box_top < start + extent
                                and start < box_bottom
                            ):
                                start, moved = box_bottom, True
                    if start != natural or chained:
                        # 앞 겹침 불가 개체에 밀린 개체는 그 아래로 가고 뒤 개체도 이어 쌓여, 셀이 개체 앞에서 쪽을
                        # 나눌 수 있다(실측: issue1921 11·12쪽 셀 그림 넷, 정답 위 끝 = 앞 그림 아래 515.9·693.1pt,
                        # 넷째는 원래 자리가 쪽 아래라 12쪽 조각 위). 밀리지 않은 나란한 개체는 줄과 함께 둔다
                        # (issue1949).
                        chained = True
                        items.append(PagingLine(start, extent))
                        sources.append(StackedFloat(view, ctrl))
                        if model.text_wrap == TextWrap.TOP_AND_BOTTOM:
                            floor = max(floor, start + extent)
                    float_boxes.append((left, right, start, start + extent))
            if index == len(view.segments) - 1:
                for ctrl in below:
                    # 기준 문단 줄보다 아래에서 시작하는 개체는 따로 쌓아 셀이 개체 앞에서 쪽을 나눌 수 있게 한다
                    # (실측: rowbreak-problem 16쪽 글상자, 정답은 기준 줄 둘을 16쪽에 두고 글상자만 17쪽 조각 위).
                    model = ctrl.model
                    margins = model.outer_margins or [0, 0, 0, 0]
                    extent = margins[2] + model.height + _vertical_caption_extent(ctrl) + margins[3]
                    items.append(PagingLine(para_top + model.vert_offset, extent))
                    sources.append(StackedFloat(view, ctrl))
                    if model.text_wrap == TextWrap.TOP_AND_BOTTOM:
                        # 어울림 개체 옆에는 글이 흐르므로 뒤 줄을 개체 아래로 밀지 않는다(같은 글상자 옆 빈 문단들).
                        floor = max(floor, para_top + model.vert_offset + extent)
                for ctrl, data in after:
                    model = ctrl.model
                    margins = model.outer_margins or [0, 0, 0, 0]
                    block_top = para_top + max(0, model.vert_offset)
                    if index > 0:
                        # 줄이 여럿인 기준 문단은 줄을 끊지 않는다(한 줄 문단은 표가 그 줄 자리에서 시작한다).
                        block_top = max(block_top, top + segment.line_height)
                    # 위 캡션은 표 위에 놓여 표를 그만큼 내린다(issue5875 7쪽 셀 <표 2>: 다음 문단 31437 = 기준 줄
                    # 21711 + 바깥 여백 140 + 캡션 1767 + 표 7679 + 바깥 여백 140, 정답 캡션 기준선이 기준 줄 아래).
                    block_top += margins[2] + _vertical_caption_extent(ctrl, (CAPTION_TOP,))
                    block = PagingBlock(block_top, data.table, margins[2], margins[3], data)
                    items.append(block)
                    sources.append(None)
                    floor = max(floor, block.end)
            previous = (top, segment, number, view)
            last_end = segment.vertical_pos + segment.line_height + segment.line_spacing
            flow_end = top + segment.line_height + segment.line_spacing
    return items, sources


def _block_tables(view: ParagraphView) -> list[Node]:
    return [ctrl for _, ctrl in sorted(view.controls.items()) if is_block_table(ctrl)]


def _stacked_floats(view: ParagraphView) -> list[Node]:
    """문단의 겹침 불가(attr bit 14 꺼짐) 어울림·자리차지 개체가 앞 개체와 가로로 겹치며 이어지면 그 개체들.

    가로로 나란한 개체(issue1949 그림 둘)와 글 앞·뒤 개체(편람)는 쌓지 않는다. 아니면 빈 목록.
    """
    found = [
        ctrl
        for _, ctrl in sorted(view.controls.items())
        if _is_flow_float(ctrl)
        and not ctrl.model.attr >> 14 & 1
        and ctrl.model.text_wrap in (TextWrap.SQUARE, TextWrap.TOP_AND_BOTTOM)
    ]
    for previous, current in itertools.pairwise(found):
        a, b = previous.model, current.model
        if a.horz_offset + a.width <= b.horz_offset or b.horz_offset + b.width <= a.horz_offset:
            return []
    return found if len(found) > 1 else []


def _no_overlap_floats(view: ParagraphView) -> list[Node]:
    """문단의 겹침 불가(attr bit 14 꺼짐) 어울림·자리차지 떠 있는 개체들."""
    return [
        ctrl
        for _, ctrl in sorted(view.controls.items())
        if _is_flow_float(ctrl)
        and not ctrl.model.attr >> 14 & 1
        and ctrl.model.text_wrap in (TextWrap.SQUARE, TextWrap.TOP_AND_BOTTOM)
    ]


def _floats_below_lines(view: ParagraphView) -> list[Node]:
    """문단의 떠 있는 개체가 하나뿐이고 그 위끝이 문단 줄들의 아래보다 아래면 그 개체(아니면 빈 목록).

    개체가 여럿이면 가로로 나란할 수 있어 쌓지 않는다(issue1949 그림 둘을 쌓으면 −71k).
    """
    floats = [ctrl for _, ctrl in sorted(view.controls.items()) if _is_flow_float(ctrl)]
    if len(floats) != 1:
        return []
    first, last = view.segments[0], view.segments[-1]
    lines_bottom = last.vertical_pos + last.line_height - first.vertical_pos
    return [
        ctrl
        for ctrl in floats
        if ctrl.model.vert_rel_to == VertRelTo.PARA
        and ctrl.model.text_wrap in (TextWrap.SQUARE, TextWrap.TOP_AND_BOTTOM)
        and ctrl.model.vert_offset >= lines_bottom
    ]


def _wraps_square_object(view: ParagraphView) -> bool:
    return any(
        isinstance(ctrl.model, ObjectCtrlHeader)
        and not ctrl.model.treat_as_char
        and ctrl.model.text_wrap == TextWrap.SQUARE
        for ctrl in view.controls.values()
    )


def _is_flow_float(ctrl: Node) -> bool:
    model = ctrl.model
    return (
        isinstance(model, ObjectCtrlHeader)
        and ctrl.record.payload[:4] != tags.TABLE_CTRL.to_bytes(4, "little")
        and not model.treat_as_char
        and model.vert_rel_to == REL_COLUMN_OR_PARA
        and bool(model.attr >> 13 & 1)
    )


def _is_reaching_table(ctrl: Node) -> bool:
    """셀 안 문단 기준 비자리차지 표(어울림 등). 기준 줄과 함께 통째로 그리고 셀 내용 높이는 그 아래까지 닿는다.

    엔진에 넣을 때 전체 평가 +220(kr_156489673 +144, issue2020 +50), 떨어진 문서 없음.
    """
    model = ctrl.model
    return (
        ctrl.record.payload[:4] == tags.TABLE_CTRL.to_bytes(4, "little")
        and isinstance(model, ObjectCtrlHeader)
        and not model.treat_as_char
        and not is_block_table(ctrl)
        and model.vert_rel_to == REL_COLUMN_OR_PARA
        and bool(model.attr >> 13 & 1)
    )


def _floating_reach(view: ParagraphView, para_top: float, skipped: list[Node] | None = None) -> float:
    """문단에 단 떠 있는 개체(문단 기준, 본문 영역 제한 bit 13, 자리차지 표 제외)의 가장 아래 끝. 없으면 para_top.

    역분석 FUN_101728e0: 셀 내용 높이는 글 아래와 이런 개체 아래 중 큰 값이다(실측: issue1921 10쪽 표 15 행 0 그림
    19055가 쪽에 들어가지 않아 정답은 표 전체를 11쪽으로). 개체는 문단 첫 줄과 함께 놓는다. 한글은 기준 줄보다
    아래에서 시작하는 개체를 개체만 다음 쪽으로 넘기기도 한다(rowbreak-problem 16쪽 글상자, 아직 다루지 않음).
    """
    bottom = para_top
    skipped_ids = {id(ctrl) for ctrl in skipped or []}
    for ctrl in view.controls.values():
        model = ctrl.model
        if (_is_flow_float(ctrl) or _is_reaching_table(ctrl)) and id(ctrl) not in skipped_ids:
            # 개체는 바깥 위 여백만큼 안쪽에 그려지고 아래 여백까지 자리를 차지한다(실측: issue1949 14쪽 그림 여백 852).
            # 위·아래 캡션도 자리를 차지한다(실측: 정책연구 22쪽 셀 가운데 정렬 그림 + 아래 캡션 3줄, 정답 위 틈 4pt).
            margins = model.outer_margins or [0, 0, 0, 0]
            extent = margins[2] + model.height + _vertical_caption_extent(ctrl) + margins[3]
            bottom = max(bottom, para_top + vertical_offset(model) + extent)
    return bottom


def vertical_offset(model: ObjectCtrlHeader) -> int:
    """개체 위치에 더하는 세로 오프셋.

    본문 영역으로 제한한(attr 0x2000) 문단 기준 개체는 문단 위로 올라가지 않는다(FUN_101c58e0: 음수 세로 오프셋을
    0으로). 실측: 셀 안 issue4889 1쪽 55행 표 오프셋 −975, 위 선 289.12; 본문 kr_156369118 5쪽 s0 p63 표 오프셋 −622,
    정답 기준선 695.2pt; 어울림 표 정책연구용역 오프셋 −21453; 셀 안 위아래 배치 그림 중간진도 24쪽 그림 23 오프셋
    −52790, 정답은 셀 맨 위. 제한하지 않으면 그대로 올라간다(issue1510 표 B 오프셋 −2000, 정답 위 선 82.03pt = 문단 위
    99.2 − 20 + 바깥 여백 2.83). 표가 아닌 글 앞·뒤 개체는 막지 않는다(FUN_102d51c0은 위아래 배치일 때만 이 값을
    더하는 경로가 있다. exam_science 2쪽 셀 안 글 앞 도형 −3685는 정답이 문단 위). 남은 조건(FUN_1001dd70 설정 +0x10
    bit 0x20)은 뜻을 확인하지 못했다.
    """
    if (
        (model.ctrl_id == tags.TABLE_CTRL or model.text_wrap in (TextWrap.SQUARE, TextWrap.TOP_AND_BOTTOM))
        and model.vert_rel_to == VertRelTo.PARA
        and model.flow_with_text
        and model.vert_offset < 0
    ):
        return 0
    return model.vert_offset


def _vertical_caption_extent(ctrl: Node, directions: tuple[int, ...] = (CAPTION_TOP, CAPTION_BOTTOM)) -> float:
    """위·아래 캡션이 개체 높이에 더하는 길이(저장 줄 기준 글 높이 + 캡션 간격). 캡션이 없거나 옆이면 0."""
    children = ctrl.children
    found = next(
        (i for i, c in enumerate(children) if c.tag == tags.LIST_HEADER and isinstance(c.model, CaptionListHeader)),
        None,
    )
    if found is None or children[found].model.direction not in directions:
        return 0.0
    content = 0.0
    for child in children[found + 1 :]:
        if child.tag != tags.PARA_HEADER:
            break
        lines = child.find(tags.PARA_LINE_SEG)
        for segment in lines.model.segments if lines is not None else []:
            content = max(content, segment.vertical_pos + segment.line_height)
    return content + children[found].model.caption_gap if content else 0.0


def paging_table(
    grid: TableGrid,
    views: dict[int, list[ParagraphView]],
    nested: Callable[[Node], NestedInput],
    stacked: set[int] | None = None,
) -> tuple[PagingTable, dict[int, list[tuple[ParagraphView, int] | None]]]:
    """table_paging 입력과 셀(id)마다 엔진 항목 번호에 대응하는 (문단 보기, 줄 번호). 표 항목은 None."""
    sources: dict[int, list[tuple[ParagraphView, int] | None]] = {}
    cells = []
    for cell in grid.cells:
        items, sources[id(cell)] = cell_items(views[id(cell)], nested)
        if stacked is not None:
            stacked.update(id(source.ctrl) for source in sources[id(cell)] if isinstance(source, StackedFloat))
        margins = cell_margins(grid.table, cell)
        header = cell.header
        cells.append(
            PagingCell(
                header.row,
                header.col,
                header.row_span,
                header.col_span,
                stored_height(cell),
                margins[2],
                margins[3],
                header.is_header,
                items,
            )
        )
    # 표 attr bit 3이면 최소 단위가 100 대신 1이다(역분석 FUN_101e9fc0, 뜻은 확인하지 못함).
    unit = 1 if grid.table.attr >> 3 & 1 else MIN_UNIT
    table = PagingTable(
        len(grid.row_y) - 1, cells, grid.table.page_break, grid.table.repeat_header, spacing=grid.spacing, unit=unit
    )
    return table, sources


class TableFlowMixin:
    def table_layout_grid(
        self, ctrl: Node, recompose: bool = False
    ) -> tuple[TableGrid | None, dict[int, list[ParagraphView]]]:
        """표 격자와 셀마다 문단 보기. 셀 문단에 놓인 자리차지 중첩 표는 셀 높이와 쪽 나눔 후보에 넣는다.

        recompose면 저장 줄이 셀 저장 높이에 다 들어가지 않는 셀(쪽에서 나뉘지 않은)과 저장 쪽 나눔이 낡은 셀
        (stale_cell_split) 가운데 셀 폭으로 다시 나눈 줄이 더 적은 셀을 다시 나눈 줄로 놓는다.
        """
        views: dict[int, list[ParagraphView]] = {}
        recomposed = []

        def cell_views(table, cell) -> list[list]:
            # LINE_SEG가 없는 셀 문단은 셀 폭으로 합성 줄을 만들어 행 높이와 줄 배치에 함께 쓴다.
            margins = cell_margins(table, cell)
            # 셀 글 폭은 목록 머리 text_width다(issue7333 셀 폭 0, text_width 13531 − 여백 1020 → 저장 줄 12508).
            width = (cell.header.text_width or cell.header.width) - margins[0] - margins[1]
            views[id(cell)] = [
                dataclasses.replace(view, segments=[_restored_baseline(segment) for segment in view.segments])
                for view in self.paragraph_views(cell.paragraphs, width)
            ]

            lines = [view.segments for view in views[id(cell)]]
            stored = stored_pieces(lines)[1]
            # 한 줄짜리 조각 뒤 문단도 다음 쪽에서 0으로 저장된다. 셀 저장 높이가 조각마다 그린 상자 합과 같을 때만
            # 그렇게 본다(실측: task2287 173쪽 행 8 두 문단 모두 0, 셀 2564 = 2 × (1000 + 282), 정답 둘째 문단 174쪽).
            restarted = stored_pieces(lines, paragraph_restarts=True)[1]
            if (
                len(restarted) > len(stored)
                and abs(sum(restarted) + len(restarted) * (margins[2] + margins[3]) - stored_height(cell)) <= 1
            ):
                cell.paragraph_restarts = True
                stored = restarted
            overfull = len(stored) == 1 and stored[0] + margins[2] + margins[3] > stored_height(cell)
            # 쪽 조각이 낡은 셀(실측: task2287 123쪽 행 35 조각 [4000, 1000], 정답은 끝 줄을 이어 한 쪽에 둠).
            # 다시 나누기는 개체를 피하지 않는다. 어울림 개체가 좁힌 저장 줄은 버리지 않는다(user-parsing-test 3쪽:
            # 그림 옆 줄 폭 29051, 셀 저장 높이 282라 넘친 셀로 보면 그림 밑까지 한 줄로 이어짐).
            if (
                recompose
                and any(lines)
                and not any(_wraps_square_object(view) for view in views[id(cell)])
                and (overfull or len(stored) > 1 and self.stale_cell_split(views[id(cell)], width, stored))
            ):
                fresh = self.recomposed_views(views[id(cell)], width)
                if sum(len(v.segments) for v in fresh) < sum(map(len, lines)):
                    views[id(cell)] = fresh
                    lines = [view.segments for view in fresh]
                    recomposed.append(cell)
            # 저장 쪽 조각이 있는 셀은 문단 위치가 조각마다 다시 세어져 중첩 표 자리를 셀 좌표로 알 수 없다.
            if len(stored_pieces(lines)[1]) == 1:
                previous_end = 0.0
                for index, view in enumerate(views[id(cell)]):
                    for nested in view.controls.values():
                        if is_block_table(nested):
                            self.add_nested_table(cell, index, view, nested, previous_end)
                    if view.segments:
                        last = view.segments[-1]
                        previous_end = last.vertical_pos + last.line_height + last.line_spacing
            return lines

        grid = table_grid(ctrl, cell_views)
        if grid is not None:
            grid.recomposed = bool(recomposed)
        return grid, views

    def stale_cell_split(self, views: list[ParagraphView], width: float, stored: list[int]) -> bool:
        """저장 쪽 조각이 있는 셀을 셀 폭으로 다시 나누면 첫 조각 높이 안에 들어가는가.

        폭을 좁혀 보는 여유는 두지 않는다(머리 기호 폭을 줄 나눔에 넣은 뒤 3% 여유보다 +5,202자, task2287 123쪽).
        """
        fresh = stored_pieces([view.segments for view in self.recomposed_views(views, width)])[1]
        return len(fresh) == 1 and fresh[0] <= stored[0]

    def add_nested_table(
        self, cell: Cell, index: int, view: ParagraphView, ctrl: Node, previous_end: float = 0.0
    ) -> None:
        """셀 문단에 놓인 자리차지 표의 자리와 아래 끝을 셀에 적는다(문단 기준 위쪽 정렬만 다룬다).

        previous_end는 앞 문단 마지막 줄 끝(줄 간격 포함)이다.
        """
        grid, _ = self.table_layout_grid(ctrl)
        if grid is None:
            return
        model = ctrl.model
        margins = model.outer_margins or [0, 0, 0, 0]
        para_top = view.segments[0].vertical_pos if view.segments else 0
        # 첫 줄이 앞 문단 끝에서 표 차지 높이만큼 밀려 저장됐으면 표는 줄 위, 앞 문단 끝에 있다(cell_items와 같은 판정.
        # 실측: issue2020 1쪽 행 6 셀 첫 줄 2550 = 141 + 2268 + 141, 셀 2650. 줄 기준이면 행이 24.5pt 늘어난다).
        if (
            view.segments
            and para_top - previous_end >= max(0, model.vert_offset) + margins[2] + model.height + margins[3] - 1
        ):
            para_top = previous_end
        top = para_top + max(0, model.vert_offset) + margins[2]
        # 저장 높이는 쪽을 넘지 않는 중첩 표에서는 맞고(issue6111 20쪽), 쪽을 넘으면 첫 쪽 몫만 적힌다
        # (issue2007 저장 654.8pt, 실제 13쪽). 격자가 저장 높이보다 훨씬 크면 쪽을 넘는 표로 보고 쪽 나눔 후보에 넣는다.
        # 셀에 저장 쪽 조각이 있는 중첩 표도 쪽을 넘는다(실측: 86712 27쪽 1×1 표, 격자/저장 1.46, 조각 [67500, 31623]).
        crosses_pages = grid.height > NESTED_SPLIT_RATIO * model.height or bool(grid.forced_cuts)
        if crosses_pages:
            cell.nested.append((top, grid, margins[3], margins[2]))
            cell.resume = (index + 1, top + grid.height + margins[3])
            self.__dict__.setdefault("page_crossing_tables", set()).add(id(ctrl))
        cell.floor = max(cell.floor, top + (grid.height if crosses_pages else model.height) + margins[3])

    def draw_table(self, state: PageState, ctrl: Node, left: float, top: float) -> TablePlacement | None:
        """표를 그리고 실제로 놓인 자리(첫 조각 쪽과 위 끝, 마지막 조각 쪽과 아래 끝)를 돌려준다."""
        grid, views = self.table_layout_grid(ctrl)
        if grid is None:
            return None
        model = ctrl.model
        if not model.treat_as_char and model.outer_margins:
            # 글자처럼 취급하지 않는 표는 정렬 기준 쪽 바깥 여백만큼 안으로 들인다(실측: issue2063 왼쪽 정렬 -1.4pt).
            left += _margin_shift(model.horz_align, model.outer_margins[0], model.outer_margins[1])
            top += _margin_shift(model.vert_align, model.outer_margins[2], model.outer_margins[3])
            if self.in_body and model.vert_rel_to == REL_COLUMN_OR_PARA and model.flow_with_text:
                # 본문 영역으로 제한한(attr 0x2000) 문단 기준 본문 표는 본문 위(+ 바깥 위 여백) 위로 올라가지 않는다
                # (실측: 편람 4구역 p64 표 오프셋 −600, 쪽 맨 위 문단이라 정답 위 선 111.95 = 본문 위 110.56 + 1.4).
                # 제한하지 않으면 본문 위로 나간다(issue1510 표 B 위 선 82.03pt, 본문 위 99.2pt).
                top = max(top, state.geometry.body_top + model.outer_margins[2])
        plans = self.__dict__.get("block_plans", {})
        if id(ctrl) in plans:
            return self.draw_planned_table(state, ctrl, left, plans.pop(id(ctrl)))
        host: HostFragments | None = self.__dict__.get("host_fragments")
        if host is not None and not self.in_body and id(ctrl) in self.__dict__.get("page_crossing_tables", ()):
            placed = self.draw_nested_table(host, grid, views, ctrl, left, top)
            if placed is not None:
                return placed
        elif host is not None and not self.in_body and is_block_table(ctrl):
            placed = self.draw_nested_across(host, grid, views, ctrl, left, top)
            if placed is not None:
                return placed
        # 이어지는 쪽에서도 바깥 위 여백만큼 내려 시작한다(실측: issue2063 2쪽 dy -1.43pt).
        # 쪽 바닥에서는 바깥 아래 여백을 남긴다(실측: issue2063 1쪽 끝 행 아래 555.14pt가 바닥 − 1.41을 넘어 다음 쪽).
        outer = (0, 0)
        if not model.treat_as_char and model.outer_margins:
            outer = (model.outer_margins[2], model.outer_margins[3])
        continued_top = outer[0]
        if self.options.table_engine and self.in_body and paging_eligible(grid, views, model, self.nested_eligible):
            # 저장 높이를 넘는 낡은 셀 줄은 셀 폭으로 다시 나눈 줄이 더 적으면 그것을 쓴다(실측: task2287 81쪽 표 47
            # 행 2~3 열 7 저장 11줄 14282 > 셀 12982, 정답 행 3은 저장 높이).
            if self.options.engine_recompose:
                fresh, fresh_views = self.table_layout_grid(ctrl, recompose=True)
                if fresh is not None:
                    grid, views = fresh, fresh_views
            return self.draw_paged_table(state, ctrl, grid, views, left, top, outer)
        cell_lines = self.table_cell_lines(grid, views)
        split = self.in_body
        self.in_body = False
        try:
            own_top = (model.outer_margins or [0, 0, 0, 0])[2]
            fragments = [(0.0, grid.height)]
            # 셀을 늘리면 가운데 정렬 줄이 움직여 자를 자리가 바뀌므로 몇 번 다시 맞춘다(마지막 회는 쪽 조각만 센다).
            # 다음 쪽으로 이어지는 쪽 조각은 쪽 바닥(바깥 여백과 SPLIT_RESERVE 위)까지 보인다
            # (실측: table_scattered_header_rowbreak 2쪽 마지막 행이 811.15pt까지 늘어나고 「강관말뚝」이 그 안 가운데).
            # 셀 간격 표는 셀 간격만큼 더 위에서 끊는다(실측: 편람 152쪽 조각 아래 선 661.1pt, 셀 간격 2.55pt).
            page_bottom = state.geometry.body_top + state.geometry.body_height - outer[1] - SPLIT_RESERVE - grid.spacing

            def fit_rounds(grid, views, cell_lines):
                fragments = [(0.0, grid.height)]
                for round_number in range(CARRIED_FIT_ROUNDS if split else 0):
                    fragments = self.table_fragments(
                        state,
                        grid,
                        top,
                        outer,
                        cell_lines,
                        own_top,
                        model.treat_as_char,
                        self.bottom_caption_extent(ctrl),
                    )
                    origins = self.fragment_origins(state, grid, ctrl, top, continued_top, fragments)
                    page_ends = [
                        start + page_bottom - origin for (start, _), origin in zip(fragments, origins, strict=True)
                    ]
                    fitted = fit_carried_cells(grid, fragments, page_ends)
                    if fitted is None or fitted == grid.row_y or round_number == CARRIED_FIT_ROUNDS - 1:
                        break
                    grid.row_y = fitted
                    cell_lines = self.table_cell_lines(grid, views)
                return fragments, cell_lines

            fragments, cell_lines = fit_rounds(grid, views, cell_lines)
            # 셀 저장 높이보다 긴 저장 줄은 다른 버전 한글이 다른 글꼴 폭으로 나눈 낡은 줄일 수 있어 셀 폭으로
            # 다시 나눈 줄을 시험하고, 표 쪽 조각 수가 같을 때만 쓴다(실측: task2287 247쪽 행 2 셀 저장 높이 1482,
            # 저장 세 줄, 정답 두 줄 29.87pt).
            if split:
                fresh, fresh_views = self.table_layout_grid(ctrl, recompose=True)
                if fresh is not None and fresh.recomposed:
                    fresh_lines = self.table_cell_lines(fresh, fresh_views)
                    refit, fresh_lines = fit_rounds(fresh, fresh_views, fresh_lines)
                    if len(refit) == len(fragments):
                        grid, views, cell_lines, fragments = fresh, fresh_views, fresh_lines, refit
            if split:
                # 쪽을 넘는 한 행 셀도 저장 높이가 쪽마다 그린 상자 합이라 마지막 조각 상자를 그만큼 늘린다(실측:
                # hwpx_sample2 21쪽 행 3 저장 52986, 20쪽 상자 104.64pt, 정답 21쪽 상자 424.57pt > 내용 420.26pt).
                # 늘려서 표가 쪽을 더 쓰면 하지 않는다(issue6712: 5.2pt 늘리면 3쪽, 정답 2쪽 — 첫 쪽 상자 끝 추정 오차).
                origins = self.fragment_origins(state, grid, ctrl, top, continued_top, fragments)
                page_ends = [
                    start + page_bottom - origin for (start, _), origin in zip(fragments, origins, strict=True)
                ]
                fitted = fit_carried_cells(grid, fragments, page_ends, single=True)
                if fitted is not None and fitted != grid.row_y:
                    saved_rows, saved_lines = grid.row_y, cell_lines
                    grid.row_y = fitted
                    cell_lines = self.table_cell_lines(grid, views)
                    refit = self.table_fragments(
                        state,
                        grid,
                        top,
                        outer,
                        cell_lines,
                        own_top,
                        model.treat_as_char,
                        self.bottom_caption_extent(ctrl),
                    )
                    if len(refit) == len(fragments):
                        fragments = refit
                    else:
                        grid.row_y, cell_lines = saved_rows, saved_lines
                        # 다 늘리면 쪽을 넘으면 마지막 쪽 바닥까지만 늘린다(실측: issue6712 2쪽 표 끝 797.5pt =
                        # 쪽 바닥, 가운데 정렬 글 7.1pt 아래). 바닥 여유는 우리 쪽 바닥 추정 오차다(1쪽 표 끝도
                        # 정답보다 0.6pt 아래).
                        allowed = page_ends[-1] - fragments[-1][1] - LAST_PIECE_BOTTOM_SLACK
                        total = fitted[-1] - saved_rows[-1]
                        if allowed > 0.5 and total > allowed:
                            factor = allowed / total
                            grid.row_y = [r + (f - r) * factor for f, r in zip(fitted, saved_rows, strict=True)]
                            cell_lines = self.table_cell_lines(grid, views)
                            refit = self.table_fragments(
                                state,
                                grid,
                                top,
                                outer,
                                cell_lines,
                                own_top,
                                model.treat_as_char,
                                self.bottom_caption_extent(ctrl),
                            )
                            if len(refit) == len(fragments):
                                fragments = refit
                            else:
                                grid.row_y, cell_lines = saved_rows, saved_lines
            # 첫 행에 제목 셀이 하나라도 있으면 한글은 그 행을 반복한다(실측: task2287 212쪽·synam·issue5875 첫 행 한
            # 칸만 제목 셀). 반복해서 표가 쪽을 더 쓰면 저장 배치가 다른 표라 모든 셀이 제목일 때만 반복한다
            # (task2287 151쪽부터 낡은 셀 줄로 행이 커져 156쪽부터 쪽이 밀림).
            if split and grid.table.repeat_header:
                plain = header_row_count(grid)
                grid.header_any = True
                if header_row_count(grid) > plain:
                    refit = self.table_fragments(
                        state,
                        grid,
                        top,
                        outer,
                        cell_lines,
                        own_top,
                        model.treat_as_char,
                        self.bottom_caption_extent(ctrl),
                    )
                    if len(refit) == len(fragments):
                        fragments = refit
                    else:
                        grid.header_any = False
                else:
                    grid.header_any = False
            header_rows = header_row_count(grid) if grid.table.repeat_header else 0
            header_height = grid.row_y[header_rows] if header_rows else 0.0
            moved_whole = fragments[0] == (0.0, 0.0)
            origins = self.fragment_origins(state, grid, ctrl, top, continued_top, fragments)
            visible_ends = [
                end if number == len(fragments) - 1 else max(end, start + page_bottom - origins[number])
                for number, (start, end) in enumerate(fragments)
            ]
            assigned = assign_fragments(cell_lines, fragments, visible_ends)
            placement = TablePlacement(state, left, top, state, top + grid.height)
            # 쪽을 먼저 모두 만들어 둔다. 셀 안 자리차지 표가 뒤 쪽 조각에 이어 그릴 수 있어야 한다.
            # 다단이면 다음 조각은 다음 단에서, 마지막 단이면 다음 쪽 첫 단에서 잇는다
            # (실측: 편람 370·371쪽 2단 [별표 4] 표, 정답은 371쪽 왼쪽 단에서 이어짐).
            states, columns = [state], [state.column]
            for _ in fragments[1:]:
                if columns[-1] + 1 < len(states[-1].geometry.columns):
                    states.append(states[-1])
                    columns.append(columns[-1] + 1)
                    continue
                states.append(self.new_page(self.section_geometry_of(states[-1])))
                states[-1].continued_by_table = True
                columns.append(0)
            # 맞쪽 편집이면 이어지는 쪽의 본문 왼쪽이 달라 표도 그만큼 옮긴다(실측: 편람 48·50쪽 dx −12.4pt = 1247HU).
            lefts = [
                left
                + page.geometry.body_left
                - states[0].geometry.body_left
                + page.geometry.columns[column][0]
                - states[0].geometry.columns[columns[0]][0]
                for page, column in zip(states, columns, strict=True)
            ]
            if moved_whole:
                placement.first_state, placement.top, placement.left = states[1], origins[1], lefts[1]
            for number, state in enumerate(states):
                if header_height and number > 0 and not (moved_whole and number == 1):
                    header_top = origins[number] - header_height
                    if fragments[number][0] not in grid.row_y:
                        header_top -= grid.spacing
                    frame = draw_table_frame(
                        grid, lefts[number], header_top, state.page, self.styles, (0.0, header_height)
                    )
                    for line in cell_lines:
                        if line.carry == 0 and line.top < header_height:
                            self.place_cell_line(state, line, lefts[number], header_top + line.origin)
                    state.page.items.extend(frame)
            host = HostFragments(
                fragments,
                states,
                origins,
                bottoms=[origins[k] + visible_ends[k] - fragments[k][0] for k in range(len(fragments))],
            )
            saved_host = self.__dict__.get("host_fragments")
            self.host_fragments = host if len(fragments) > 1 else None
            try:
                for number, (start, end) in enumerate(fragments):
                    if number == 0 and moved_whole:
                        continue
                    state, origin = states[number], origins[number]
                    host.number = number
                    bottom = end if number == len(fragments) - 1 else start + page_bottom - origin
                    frame = self.draw_fragment_frame(grid, lefts[number], origin, state, (start, end), bottom)
                    for line, offset in assigned.get(number, []):
                        self.place_cell_line(state, line, lefts[number], origin + offset)
                    state.page.items.extend(frame)
                    if split:
                        self.table_bottom = origin + (end - start)
                        self.table_bottom_margin = (model.outer_margins or [0, 0, 0, 0])[3]
                    placement.last_state, placement.bottom = state, origin + (end - start)
            finally:
                self.host_fragments = saved_host
            self.current_state = state
            if split:
                state.column = columns[-1]
            if split and moved_whole and len(fragments) == 2 and not model.treat_as_char:
                # 통째로 다음 쪽에 옮긴 떠 있는 표 뒤 본문은 이 쪽에서 잇는다(실측: task1725 s0 p4342 표 1(나누지
                # 않음)은 201쪽 맨 위, 뒤 문단 64060~72940은 200쪽 바닥까지, 정답 200쪽 707.4pt 「*미연방의 항공규칙」).
                states[1].continued_by_table = False
                self.__dict__.setdefault("float_pages", set()).add(id(states[1]))
                self.current_state = states[0]
        finally:
            self.in_body = split
        return placement

    def draw_paged_table(
        self,
        state: PageState,
        ctrl: Node,
        grid: TableGrid,
        views: dict[int, list[ParagraphView]],
        left: float,
        top: float,
        outer: tuple[int, int],
    ) -> TablePlacement:
        """table_paging이 정한 쪽 조각마다 칸 상자와 셀 줄을 그린다."""
        model = ctrl.model
        geometry = state.geometry
        tail = self.bottom_caption_extent(ctrl)
        own_top = (model.outer_margins or [0, 0, 0, 0])[2]
        at_page_top = top - geometry.body_top <= max(outer[0], own_top) + PAGE_TOP_SLACK
        table, sources = paging_table(grid, views, self.nested_input, self.__dict__.setdefault("stacked_floats", set()))
        first_available = geometry.body_top + geometry.body_height - top - outer[1] - tail
        next_available = geometry.body_height - outer[0] - outer[1] - tail
        if model.treat_as_char:
            first_available, next_available = inline_table_rooms(
                grid.height, first_available, next_available, at_page_top
            )
        # 표를 놓기 전에 그 쪽에 모인 각주 자리는 뺀다. 셀 줄의 각주까지 조각마다 빼면 긴 각주가 많은 쪽에서 나빠진다
        # (중간진도보고서 175쪽~, 한글은 각주를 쪽 사이에서 나눈다).
        paging = paginate_table(
            table,
            first_available,
            next_available,
            at_page_top,
            [self.footnote_reserve(state)],
        )
        count = len(paging.fragments) + paging.moved_whole
        states, columns = [state], [state.column]
        for _ in range(count - 1):
            if columns[-1] + 1 < len(states[-1].geometry.columns):
                states.append(states[-1])
                columns.append(columns[-1] + 1)
                continue
            states.append(self.new_page(self.section_geometry_of(states[-1])))
            states[-1].continued_by_table = True
            columns.append(0)
        lefts = [
            left
            + page.geometry.body_left
            - states[0].geometry.body_left
            + page.geometry.columns[column][0]
            - states[0].geometry.columns[columns[0]][0]
            for page, column in zip(states, columns, strict=True)
        ]
        origins = [top] + [page.geometry.body_top + outer[0] for page in states[1:]]
        if paging.moved_whole:
            origins[1] += self.top_caption_extent(ctrl)
        skip = int(paging.moved_whole)
        positions = [
            (states[number + skip], lefts[number + skip], origins[number + skip])
            for number in range(len(paging.fragments))
        ]
        self.in_body = False
        try:
            self.draw_fragments(grid, table, sources, paging.fragments, positions)
        finally:
            self.in_body = True
        last_state, last_origin = states[-1], origins[-1]
        bottom = last_origin + paging.fragments[-1].bottom
        self.table_bottom = bottom
        self.table_bottom_margin = (model.outer_margins or [0, 0, 0, 0])[3]
        #: 쪽을 넘긴 마지막 조각에 셀 글이 하나도 없는가(셀 높이만 넘친 빈 나머지).
        self.table_remainder_empty = len(paging.fragments) > 1 and not any(
            box.placements for box in paging.fragments[-1].boxes
        )
        placement = TablePlacement(states[skip], lefts[skip], origins[skip], last_state, bottom)
        self.current_state = last_state
        last_state.column = columns[-1]
        if paging.moved_whole and len(paging.fragments) == 1 and not model.treat_as_char:
            # 통째로 다음 쪽에 옮긴 떠 있는 표 뒤 본문은 이 쪽에서 잇는다(기존 경로와 같은 처리, task1725 s0 p4342).
            states[1].continued_by_table = False
            self.__dict__.setdefault("float_pages", set()).add(id(states[1]))
            self.current_state = states[0]
        return placement

    def nested_input(self, ctrl: Node) -> NestedInput:
        grid, views = self.table_layout_grid(ctrl, recompose=self.options.engine_recompose)
        if grid is None:
            raise CorruptFileError("nested table has no TABLE record; the file is damaged")
        table, sources = paging_table(grid, views, self.nested_input)
        return NestedInput(ctrl, table, grid, sources)

    def nested_eligible(self, ctrl: Node) -> bool:
        grid, views = self.table_layout_grid(ctrl, recompose=self.options.engine_recompose)
        return grid is not None and paging_eligible(grid, views, ctrl.model, self.nested_eligible)

    def draw_fragments(
        self,
        grid: TableGrid,
        table: PagingTable,
        sources: dict[int, list[tuple[ParagraphView, int] | None]],
        fragments: list[TableFragment],
        positions: list[tuple[PageState, float, float]],
    ) -> None:
        """쪽 조각마다 (쪽, 표 왼쪽, 조각 위)에 칸 상자와 셀 줄을 그린다. 셀 안 표는 기준 줄을 놓을 때 그린다."""
        _, header_rows = table.analysis()
        header_boxes = []
        if header_rows and len(fragments) > 1 and fragments[1].start_y > table.spacing:
            header_boxes = [box for box in fragments[0].boxes if table.cells[box.cell].row < header_rows]
        # 셀 안 표 조각의 자리를 먼저 모두 적는다. 기준 줄은 표가 이어지는 뒤 조각보다 먼저 그려질 수 있다.
        plans = self.__dict__.setdefault("block_plans", {})
        for fragment, (page, _, origin) in zip(fragments, positions, strict=True):
            for box in fragment.boxes:
                cell = grid.cells[box.cell]
                for item, local, piece in box.placements:
                    if piece is not None:
                        block = table.cells[box.cell].lines[item]
                        top = origin + box.y + cell_margins(grid.table, cell)[2] + self.box_shift(cell, box) + local
                        plans.setdefault(id(block.key.ctrl), []).append((page, top, piece, block.key))
        outermost = self.__dict__.get("note_floor") is None
        try:
            for number, (fragment, (page, x, origin)) in enumerate(zip(fragments, positions, strict=True)):
                boxes = (header_boxes if number else []) + fragment.boxes
                frames = []
                for box in boxes:
                    cell = grid.cells[box.cell]
                    cx, _, width, _ = grid.cell_box(cell)
                    frames.append((cell, x + cx, origin + box.y, width, box.height))
                if frames:
                    top = min(frame[2] for frame in frames)
                    bottom = max(frame[2] + frame[4] for frame in frames)
                    draw_table_background(grid.table, (x, top, grid.width, bottom - top), page.page, self.styles)
                frame = draw_cell_boxes(frames, page.page, self.styles)
                if outermost:
                    # 셀 안 각주는 그 쪽 표 조각 아래에 자리가 있어야 그 쪽에 놓인다(정책연구 78쪽 각주 107~110).
                    self.note_floor = origin + fragment.bottom
                for box in boxes:
                    self.place_box_lines(page, grid, grid.cells[box.cell], box, sources, x, origin)
                page.page.items.extend(frame)
        finally:
            if outermost:
                self.note_floor = None

    def draw_planned_table(
        self, state: PageState, ctrl: Node, left: float, plan: list[tuple[PageState, float, TableFragment, NestedInput]]
    ) -> TablePlacement:
        """바깥 표 엔진이 정한 쪽 조각 자리대로 셀 안 자리차지 표를 그린다(기준 줄을 놓는 쪽의 left 기준)."""
        data = plan[0][3]
        positions = [(page, left + page.geometry.body_left - state.geometry.body_left, top) for page, top, _, _ in plan]
        self.draw_fragments(data.grid, data.table, data.sources, [piece for _, _, piece, _ in plan], positions)
        first, last = plan[0], plan[-1]
        return TablePlacement(first[0], positions[0][1], first[1], last[0], last[1] + last[2].bottom)

    @staticmethod
    def box_shift(cell: Cell, box: CellBox) -> float:
        """세로 정렬: 칸 높이와 이 조각 내용 높이의 차이."""
        room = box.height - box.content
        return {1: room / 2, 2: room}.get(cell.header.vert_align, 0.0) if room > 0 else 0.0

    def place_box_lines(
        self,
        state: PageState,
        grid: TableGrid,
        cell: Cell,
        box: CellBox,
        sources: dict[int, list[tuple[ParagraphView, int] | None]],
        left: float,
        origin: float,
    ) -> None:
        """칸 상자 안에 이 조각의 셀 줄을 놓는다."""
        margins = cell_margins(grid.table, cell)
        cx, _, width, _ = grid.cell_box(cell)
        shift = self.box_shift(cell, box)
        for item, local, piece in box.placements:
            source = sources[id(cell)][item]
            if piece is not None or source is None:
                continue
            if isinstance(source, StackedFloat):
                self.place_stacked_float(
                    state,
                    source,
                    left + cx + margins[0],
                    origin + box.y + margins[2] + shift + local,
                    width - margins[0] - margins[1],
                )
                continue
            view, index = source
            segment = view.segments[index]
            line_top = origin + box.y + margins[2] + shift + local
            list_top = line_top - segment.vertical_pos
            cell_line = CellLine(
                line_top,
                line_top + segment.line_height,
                view,
                index,
                cx + margins[0],
                width - margins[0] - margins[1],
                list_top,
                cell_right=cx + width,
            )
            self.place_cell_line(state, cell_line, left, list_top)

    def fragment_origins(
        self, state: PageState, grid: TableGrid, ctrl: Node, top: float, continued_top: float, fragments: list
    ) -> list[float]:
        """쪽 조각마다 그 조각 시작이 놓이는 쪽 y. 이어지는 쪽은 반복 제목 행 아래에서 시작한다."""
        header_rows = header_row_count(grid) if grid.table.repeat_header else 0
        header_height = grid.row_y[header_rows] if header_rows else 0.0
        moved_whole = fragments[0] == (0.0, 0.0)
        origins = [top] + [
            state.geometry.body_top + continued_top + (0.0 if moved_whole and number == 1 else header_height)
            for number in range(1, len(fragments))
        ]
        # 셀 중간에서 이어지는 조각은 제목 행 아래 셀 간격을 두고 잘린 셀을 다시 연다(실측: 편람 153쪽 135.7·138.2pt).
        for number in range(1, len(fragments)):
            if fragments[number][0] not in grid.row_y:
                origins[number] += grid.spacing
        if moved_whole:
            # 통째로 다음 쪽에 가는 표는 위쪽 캡션도 함께 가므로 캡션 자리만큼 내려 시작한다
            # (실측: task2287 19쪽 <표 2> 캡션 기준선 68.88, 표 위 선 78.87).
            origins[1] += self.top_caption_extent(ctrl)
        return origins

    def draw_fragment_frame(
        self, grid: TableGrid, left: float, origin: float, state: PageState, clip: tuple[float, float], visible: float
    ) -> list[Line]:
        """쪽 조각 칸 배경을 칠하고 테두리 선을 돌려준다(draw_cell_boxes).
        다음 쪽으로 이어지는 셀은 자른 곳이 아니라 보이는 아래 끝까지 그린다.

        실측: issue4514 10쪽과 task2287 26~28쪽 조각 아래 선이 자른 곳이 아니라 쪽마다 같은 자리(781.91·536.01pt)다.
        셀 안에서도 나누는 표는 자른 곳에서 시작하는 행도 빈 조각으로 쪽 바닥까지 그린다(실측: issue4514 9쪽
        page_break 2, 다음 쪽으로 넘긴 행이 747.26~781.91pt에 빈 칸). 셀 단위로 나누는 표는 그리지 않는다
        (실측: issue2063 40쪽 page_break 1, 537.45pt 아래 선 없음).
        """
        start, end = clip
        if visible <= end:
            # 쪽 바닥을 넘는 조각(내용만 들어가 이 쪽에 둔 행)은 쪽 바닥에서 잘라 그린다
            # (실측: rowbreak-problem-pages 3쪽 행 8 아래 선 768.72pt, 행 끝은 771.67pt).
            return draw_table_frame(grid, left, origin, state.page, self.styles, (start, visible))
        starting = grid.table.page_break != SPLIT_CELLS
        continued = []
        for cell in grid.cells:
            _, top, _, height = grid.cell_box(cell)
            if (top < end or starting and top == end) and end < top + height:
                continued.append(cell)
        kept = [cell for cell in grid.cells if not any(cell is other for other in continued)]
        draw_table_background(
            grid.table, (left, origin, grid.width, max(end, visible) - start), state.page, self.styles
        )
        lines = draw_table_frame(
            dataclasses.replace(grid, cells=kept), left, origin, state.page, self.styles, clip, background=False
        )
        # 이어지는 셀은 제 높이가 짧아도 쪽 바닥까지 늘여 그린다(issue4514 9쪽 빈 칸에서 넓힌 규칙, 검증 안 됨).
        stretched = [y if y <= end else max(y, visible) for y in grid.row_y]
        return lines + draw_table_frame(
            dataclasses.replace(grid, cells=continued, row_y=stretched),
            left,
            origin,
            state.page,
            self.styles,
            (start, visible),
            background=False,
        )

    def draw_nested_across(
        self,
        host: HostFragments,
        grid: TableGrid,
        views: dict[int, list[ParagraphView]],
        ctrl: Node,
        left: float,
        top: float,
    ) -> TablePlacement | None:
        """저장 쪽 조각이 있는 셀 안 자리차지 표가 그 쪽 바닥을 넘으면 바깥 표의 다음 쪽 조각 맨 위에서 잇는다.

        이런 셀은 문단 위치가 조각마다 다시 세어져 표를 행 높이에 넣지 못하므로(add_nested_table) 그릴 때 나눈다.
        실측: task1718 38쪽 셀 안 표 145600HU가 38쪽 바닥에서 나뉘어 39쪽 62.21pt부터 이어짐.
        """
        number = host.number
        if host.bottoms is None or number + 1 >= len(host.fragments) or top + grid.height <= host.bottoms[number]:
            return None
        state = host.states[number]
        margins = ctrl.model.outer_margins or [0, 0, 0, 0]
        line = self.__dict__.get("cell_line")
        inset = (line.margin_top if line is not None else 0) + margins[2]
        # 이 쪽은 바깥 표 조각의 보이는 아래 끝까지 쓰고, 다음 쪽부터는 바깥 표 조각 맨 위(+ 여백)에서 잇는다.
        geometry = dataclasses.replace(
            state.geometry, body_height=host.bottoms[number] + margins[3] + SPLIT_RESERVE - state.geometry.body_top
        )
        cell_lines = self.table_cell_lines(grid, views)
        fragments = self.table_fragments(
            dataclasses.replace(state, geometry=geometry), grid, top, (margins[2], margins[3]), cell_lines
        )
        fragments = fragments[: len(host.fragments) - number]
        if len(fragments) < 2 or fragments[0] == (0.0, 0.0):
            return None
        assigned = assign_fragments(cell_lines, fragments)
        first_left = state.geometry.body_left
        ys = [top] + [host.origins[number + k] + inset for k in range(1, len(fragments))]
        pages = host.states[number : number + len(fragments)]
        # 이 표 셀 안의 자리차지 표도 같은 쪽 조각을 따라 나뉜다.
        inner = HostFragments(fragments, pages, ys, bottoms=host.bottoms[number : number + len(fragments)])
        saved = self.__dict__.get("host_fragments")
        self.host_fragments = inner
        try:
            for k, (low, high) in enumerate(fragments):
                inner.number = k
                page = pages[k]
                page_left = left + page.geometry.body_left - first_left
                frame = draw_table_frame(grid, page_left, ys[k], page.page, self.styles, (low, high))
                for cell_line, offset in assigned.get(k, []):
                    self.place_cell_line(page, cell_line, page_left, ys[k] + offset)
                page.page.items.extend(frame)
        finally:
            self.host_fragments = saved
        last = len(fragments) - 1
        return TablePlacement(
            state, left, top, host.states[number + last], ys[last] + fragments[last][1] - fragments[last][0]
        )

    def bottom_caption_extent(self, ctrl: Node) -> float:
        """아래쪽 캡션이 표 아래에 차지하는 길이(캡션 높이 + 간격). 없으면 0."""
        model = ctrl.model
        caption = self.object_caption(ctrl, model.width, (model.outer_margins or [0])[0])
        return caption.extent if caption is not None and caption.direction == CAPTION_BOTTOM else 0.0

    def top_caption_extent(self, ctrl: Node) -> float:
        """위쪽 캡션이 표 위에 차지하는 길이(캡션 높이 + 간격). 없으면 0."""
        model = ctrl.model
        caption = self.object_caption(ctrl, model.width, (model.outer_margins or [0])[0])
        return caption.extent if caption is not None and caption.direction == CAPTION_TOP else 0.0

    def draw_nested_table(
        self,
        host: HostFragments,
        grid: TableGrid,
        views: dict[int, list[ParagraphView]],
        ctrl: Node,
        left: float,
        top: float,
    ) -> TablePlacement | None:
        """바깥 표 쪽 조각을 따라 셀 안 자리차지 표를 나눠 그린다. 한 조각에 다 들어가면 None."""
        start = host.fragments[host.number][0]
        table_top = top - host.origins[host.number] + start
        # 이어지는 쪽에서는 셀 위 여백과 이 표 바깥 위 여백 아래에서 다시 시작한다
        # (실측: issue4889 2쪽 55행 표 위 선 63.65pt = 본문 위 + 겹친 위 여백 다섯 개).
        line = self.__dict__.get("cell_line")
        inset = (line.margin_top if line is not None else 0) + (ctrl.model.outer_margins or [0, 0, 0, 0])[2]
        pieces = []
        for number in range(host.number, len(host.fragments)):
            low, high = host.fragments[number]
            if number == len(host.fragments) - 1:
                high = table_top + grid.height
            low, high = max(low, table_top), min(high, table_top + grid.height)
            if high > low:
                y = host.origins[number] + low - host.fragments[number][0]
                if number > host.number:
                    y += inset
                pieces.append((number, low - table_top, high - table_top, y))
        if len(pieces) < 2:
            return None
        cell_lines = self.table_cell_lines(grid, views)
        assigned = assign_fragments(cell_lines, [(low, high) for _, low, high, _ in pieces])
        # 이 표 셀 안의 자리차지 표도 같은 쪽 조각을 따라 나뉜다(issue4889: 1×1 표 안 1×1 표 안 55행 표).
        inner = HostFragments(
            [(low, high) for _, low, high, _ in pieces],
            [host.states[number] for number, _, _, _ in pieces],
            [y for _, _, _, y in pieces],
        )
        saved = self.__dict__.get("host_fragments")
        self.host_fragments = inner
        try:
            first_left = host.states[pieces[0][0]].geometry.body_left
            for index, (number, low, high, y) in enumerate(pieces):
                state = host.states[number]
                inner.number = index
                page_left = left + state.geometry.body_left - first_left
                frame = draw_table_frame(grid, page_left, y, state.page, self.styles, (low, high))
                for line, offset in assigned.get(index, []):
                    self.place_cell_line(state, line, page_left, y + offset)
                state.page.items.extend(frame)
        finally:
            self.host_fragments = saved
        first, last = pieces[0], pieces[-1]
        return TablePlacement(host.states[first[0]], left, first[3], host.states[last[0]], last[3] + last[2] - last[1])

    def table_cell_lines(self, grid: TableGrid, views: dict[int, list[ParagraphView]]) -> list[CellLine]:
        placed = []
        split_rows = {row for row, _ in grid.piece_starts}
        for cell in grid.cells:
            cx, cy, width, height = grid.cell_box(cell)
            margins = cell_margins(grid.table, cell)
            inner_height = height - margins[2] - margins[3]
            cell_views = views[id(cell)]
            pieces, heights = cell_pieces(cell)
            inner_width = width - margins[0] - margins[1]
            column_boxes = column_layout(cell_column_def(cell), round(inner_width))
            line_columns = cell_columns(cell)
            # 한 행 셀의 저장 쪽 조각은 셀 안 위치(piece_starts)로, 여러 행 셀의 조각은 쪽 조각 순번으로 놓는다.
            starts = grid.piece_starts.get((cell.header.row, cell.header.col))
            multi_piece = starts is None and len(heights) > 1
            # 여러 행 셀은 쪽마다 보이는 부분 안에서 조각을 정렬하므로 조각이 하나여도 쪽 조각 순번으로 놓는다.
            # 쪽을 넘는 한 행 셀이 있는 행의 다른 셀도 그 쪽에 보이는 부분 안에서 정렬한다(실측: issue4514 10쪽 가운데
            # 정렬 「요구사항 상세설명」은 행 전체가 아니라 10쪽에 보이는 칸의 가운데).
            carried = multi_piece or starts is None and (cell.header.row_span > 1 or cell.header.row in split_rows)
            # 쪽을 넘는 한 행 셀은 저장 셀 높이가 첫 조각 상자보다 크면 첫 조각을 그 쪽에 보이는 부분 안에서 정렬한다
            # (실측: issue4514 10쪽 저장 41303 > 29582, 가운데 정렬로 5.31pt 내려감. issue1921 24쪽 5957 < 24982,
            # issue1949 282는 위 정렬). 검증 안 됨: 세 문서에서 고른 경계다. 둘째 조각부터는 위 정렬이다
            # (실측: kr_156489709 4~6쪽 이어지는 조각을 가운데 정렬하면 +1.62pt). 남는 자리가 다음 조각 첫 줄
            # (줄 높이 + 줄 간격)보다 크면 글 줄이 아닌 것이 넘어간 것이라 위 정렬이다
            # (실측: pic-in-head-02 3쪽 남는 자리 255pt, 다음 조각 첫 줄 12pt).
            room_limit = float("inf")
            if starts is not None and len(starts) > 1 and cell.header.vert_align and stored_height(cell) > starts[1]:
                carried = True
                room_limit = next(
                    (
                        seg.line_height + seg.line_spacing
                        for nums, view in zip(pieces, cell_views, strict=True)
                        for seg, n in zip(view.segments, nums, strict=False)
                        if n == 1
                    ),
                    0,
                )
            content = max(heights[0] if starts is None else starts[len(heights) - 1] + heights[-1], cell.floor)
            # 세로 정렬할 내용 높이에는 떠 있는 개체 아래 끝도 든다(글 뒤 그림 포함. 실측: issue2020 1쪽 「사진」 셀
            # 저장 줄 끝 9384, 그림 끝 9983, 정답 위치는 9984 기준 가운데. 전체 평가 kps-ai +804).
            reach = 0.0
            if starts is None and not multi_piece:
                reach = max(
                    [0.0]
                    + [_floating_reach(view, view.segments[0].vertical_pos) for view in cell_views if view.segments]
                )
                content = max(content, reach)
            shift = 0.0
            if cell.header.vert_align == 1 and not multi_piece:
                shift = max(0.0, (inner_height - content) / 2)
            elif cell.header.vert_align == 2 and not multi_piece:
                shift = max(0.0, inner_height - content)
            for paragraph, (view, numbers, columns) in enumerate(zip(cell_views, pieces, line_columns, strict=True)):
                # 쪽을 넘는 중첩 표 뒤 문단은 그 표가 끝난 쪽에 놓이고, 위치는 그 쪽 조각 맨 위에서 센다
                # (실측: issue4889 3쪽 「3. 국제입찰 대상 공사」 vertical_pos 50540 = 3쪽 셀 맨 위에서 505.4pt).
                resume = cell.resume is not None and paragraph >= cell.resume[0]
                for index, segment in enumerate(view.segments):
                    piece = numbers[index]
                    lift = starts[min(piece, len(starts) - 1)] if starts else 0.0
                    # 쪽을 넘는 한 행 셀은 마지막 조각만 남은 칸 안에서 세로 정렬한다(실측: issue1921 20~24쪽 가운데
                    # 정렬 셀, 같은 행 두 행 셀의 마지막 조각이 380 길어 24쪽만 1.9pt 내려감).
                    origin = cy + margins[2] + (shift if not starts or piece == len(starts) - 1 else 0.0) + lift
                    line_top = origin + segment.vertical_pos
                    column_x, column_width = column_boxes[columns[index]] if index < len(columns) else (0, inner_width)
                    placed.append(
                        CellLine(
                            line_top,
                            line_top + segment.line_height,
                            view,
                            index,
                            cx + margins[0] + column_x,
                            column_width,
                            origin,
                            piece if carried else 0,
                            cy,
                            margins[2],
                            cy + height,
                            margins[3],
                            # 가운데 조각은 위 정렬이고(kr_156489709 4~6쪽) 마지막 조각은 셀 정렬을 따른다
                            # (issue6712 2쪽).
                            0
                            if resume or starts is not None and 0 < piece < len(starts) - 1
                            else cell.header.vert_align,
                            max(heights[piece], reach),
                            carried or resume,
                            cy + margins[2] + cell.resume[1] if resume else None,
                            # 다음 조각 첫 줄로 정렬을 막는 제한은 첫 조각에만 쓴다
                            # (issue6712 마지막 조각 남는 자리 13.5pt).
                            room_limit if piece == 0 else float("inf"),
                            not stored_lines(view.node),
                            cx + width,
                        )
                    )
        return placed

    def place_stacked_float(
        self, state: PageState, source: StackedFloat, left: float, top: float, width: float
    ) -> None:
        """쌓은 개체를 그 항목 자리(바깥 위 여백 위)에 그린다. 세로 오프셋은 쓰지 않는다."""
        model = source.ctrl.model
        anchor = dataclasses.replace(source.view.segments[0], vertical_pos=0)
        view = dataclasses.replace(source.view, segments=[anchor, *source.view.segments[1:]])
        stacked = self.__dict__.setdefault("stacked_floats", set())
        stacked.discard(id(source.ctrl))
        try:
            self.place_object(state, view, source.ctrl, left, top, Area(left, top - vertical_offset(model), width))
        finally:
            stacked.add(id(source.ctrl))

    def place_cell_line(self, state: PageState, line: CellLine, left: float, list_top: float) -> None:
        saved = self.__dict__.get("cell_line")
        self.cell_line = line
        try:
            # 셀 글 목록마다 떠 있는 개체를 그 셀 글 다음에 그린다(FUN_1018cd80 → FUN_10189050).
            start = len(state.page.items)
            with self.painting_list(("cell", line.inner_x, line.origin)):
                self.place_line(state, line.view, line.index, Area(left + line.inner_x, list_top, line.inner_width))
            # 한글은 셀 내용을 셀 영역으로 잘라 그린다(kr_156489775 10쪽 PDF 클립 [363.6, 309.0, 534.2, 548.8]: 왼쪽은
            # 칸 왼쪽 + 안 여백, 오른쪽은 칸 끝). 칸 밖으로 나간 그림이 표 테두리를 덮지 않게 그림만 가로로 자른다.
            right = left + (line.cell_right or line.inner_x + line.inner_width)
            clip_images_x(state.page.items, start, left + line.inner_x, right)
        finally:
            self.cell_line = saved

    def table_fragments(
        self,
        state: PageState,
        grid,
        top: float,
        outer: tuple[int, int],
        cell_lines,
        own_top: int = 0,
        inline: bool = False,
        tail: float = 0.0,
    ) -> list[tuple[float, float]]:
        """쪽 바닥을 넘는 표를 나눌 표 세로 좌표 구간들. 행 경계에서 자르되, 행이 한 쪽보다 길면 줄 경계에서 자른다."""
        geometry = state.geometry
        body_bottom = geometry.body_top + geometry.body_height
        header_rows = header_row_count(grid) if grid.table.repeat_header else 0
        header_height = grid.row_y[header_rows] if header_rows else 0.0
        fragments: list[tuple[float, float]] = []
        start = 0.0
        available = body_bottom - top - outer[1]
        at_page_top = top - geometry.body_top <= max(outer[0], own_top) + PAGE_TOP_SLACK
        if inline and at_page_top and grid.height - available <= geometry.paper_height - body_bottom:
            # 쪽 맨 위 글자처럼 취급한 표가 본문보다 조금 길면 나누지 않고 아래 여백으로 넘쳐 그린다
            # (실측: KTX 2쪽 표 704pt, 본문 700pt, 정답 가로선 775.55pt > 본문 바닥 771.03pt). 넘침 한도는 검증 안 됨.
            return [(0.0, grid.height)]
        if tail and not at_page_top and grid.height <= available < grid.height + tail:
            # 표는 들어가도 아래 캡션이 들어가지 않으면 캡션과 함께 다음 쪽으로 간다
            # (실측: pyhwp table-caption 표 8, 남은 42.8pt에 표 12.8pt + 아래 캡션 34.5pt, 정답은 2쪽 127.42pt).
            return [(0.0, 0.0), (0.0, grid.height)]
        if grid.table.page_break == NO_SPLIT:
            # 나누지 않는 표는 넘치면 통째로 다음 쪽으로 가고, 새 쪽에도 다 들어가지 않으면 그 자리에서 넘쳐 그린다
            # (실측: tac-img-02 2쪽 표 722pt는 72.28~793.65pt, 본문 바닥 785.19pt).
            # 이미 쪽 맨 위(위 여백 바로 아래)에 놓인 표는 옮겨도 같은 자리라 그대로 넘쳐 그린다
            # (실측: 편람 145쪽 글자처럼 취급 표 55481HU, 본문 55559HU, 바깥 여백 140 때문에 0.6pt 넘침).
            fresh_page = geometry.body_height - outer[0] - outer[1]
            if grid.height <= available or grid.height > fresh_page or at_page_top:
                return [(0.0, grid.height)]
            return [(0.0, 0.0), (0.0, grid.height)]
        forced = grid.forced_cuts
        nested_rows = grid.nested_rows
        nested_lines = grid.nested_lines
        # 셀 첫 줄 위는 행 경계와 같아 빼고, 둘째 줄부터 그 줄 위를 자를 자리로 본다.
        nested_points = nested_rows + [(top, below, above) for top, _, below, above, first in nested_lines if not first]
        # 저장 쪽 나눔은 우리 표 위치가 조금 어긋나도 따른다(쪽 높이의 1/4까지 넘침 허용, 실측 근거 없는 여유값).
        slack = geometry.body_height / 4
        first_body_row = grid.row_y[header_rows + 1] if header_rows + 1 < len(grid.row_y) else grid.height
        fits_first = bool(forced) and forced[0] <= available + slack
        # 첫 본문 행이 새 쪽에도 다 들어가지 않으면 옮겨도 소용없으므로 이 쪽에서부터 행 안에서 자른다.
        first_row_fits_page = (
            first_body_row - header_height <= geometry.body_height - outer[0] - outer[1] - header_height
        )
        # 셀 안에서도 나누는 표는 첫 본문 행의 줄이 하나라도 이 쪽에 들어가면 그 행을 이 쪽에서부터 나눈다
        # (실측: 80168 13쪽 한 행 표 461pt를 남은 260pt에 523.59~773.03으로 나눔). 줄은 줄 간격까지 들어가야 한다
        # (실측: hwpctl_API 35쪽 코드 상자는 남은 18.19pt에 첫 줄 10pt + 간격 6pt가 못 들어가 36쪽으로 통째로 감).
        first_lines_fit = any(
            line.carry == 0
            and header_height <= line.top
            and line.bottom + line.view.segments[line.index].line_spacing <= available - SPLIT_RESERVE
            for line in cell_lines
        )
        if (
            not fits_first
            and grid.height > available
            and first_body_row > available - SPLIT_RESERVE
            and first_row_fits_page
            and (grid.table.page_break == SPLIT_CELLS or not first_lines_fit)
        ):
            # 제목 행 말고는 한 행도 온전히 들어가지 않으면 표 전체를 다음 쪽 맨 위에서 시작한다.
            fragments.append((0.0, 0.0))
            available = geometry.body_height - outer[0] - outer[1]
        page_room = geometry.body_height - outer[0] - outer[1] - header_height
        # 셀 단위로 나누는 표는 여러 행 셀을 가르는 행 경계에서 자르지 않는다
        # (실측: aift 12쪽 행 5~6에 걸친 셀, 정답은 행 5 위에서 잘라 두 행을 13쪽으로).
        spanned = set()
        if grid.table.page_break == SPLIT_CELLS:
            for cell in grid.cells:
                first, last = cell.header.row, cell.header.row + cell.header.row_span
                spanned.update(grid.row_y[first + 1 : min(last, len(grid.row_y) - 1)])
        while len(fragments) < MAX_TABLE_PAGES:
            pending = [b for b in forced if b > start]
            if grid.spacing:
                # 셀 간격 표는 저장 쪽 조각 경계가 든 행이 이 쪽에 통째로 들어가면 그 경계를 따르지 않는다. 한글 2022는
                # 다시 조판해 앞 행 높이가 달라지면 옛 저장 경계와 다르게 나눈다(실측: 편람 154쪽 행 5 저장 조각
                # [5500, 17500], 정답은 행 5~6 전부를 154쪽에. 전체 평가 +5,340, 하락 0).
                fit_limit = start + available - SPLIT_RESERVE
                pending = [b for b in pending if min((r for r in grid.row_y if r > b), default=grid.height) > fit_limit]
            if grid.height - start <= available and not pending:
                break
            limit = start + available - SPLIT_RESERVE
            visible_end = None
            if pending and pending[0] <= limit + slack:
                cut = pending[0]
                # 셀 간격 표에서 행 안 저장 경계로 자르면 다음 쪽은 이 쪽에 실제로 그린 끝(쪽 바닥)에서 잇는다. 이어진
                # 조각 높이 = 저장 높이 − 앞 쪽에 보인 높이(실측: 편람 153쪽 행 3, 정답 81.4pt, 경계에서 이으면 91.4pt).
                if grid.spacing:
                    row_end = min((b for b in grid.row_y if b > cut), default=grid.height)
                    # 그 쪽에서 실제로 그린 끝(호출부 page_bottom − 조각 원점과 같은 식)
                    seen = start + available - SPLIT_RESERVE - grid.spacing
                    if fragments and start not in grid.row_y:
                        seen -= grid.spacing
                    if cut < seen < row_end:
                        visible_end = seen
            else:
                # 셀 안에서도 나누는 표는 행 경계에 ROW_RESERVE만 남긴다(실측: 편람 352쪽 행 끝이 SPLIT_RESERVE 안
                # 0.11pt, 정답은 이 쪽 483.57pt). 셀 단위로 나누는 표는 SPLIT_RESERVE를 남긴다(issue2063 40쪽).
                row_limit = limit if grid.table.page_break == SPLIT_CELLS else start + available - ROW_RESERVE
                boundaries = [b for b in grid.row_y if start < b <= row_limit and b not in spanned]
                # 이어지는 쪽에서는 중첩 표가 겹친 위 여백 아래에서 다시 시작하므로 그만큼 덜 들어간다.
                boundaries += [
                    b for b, below, above in nested_rows if start < b and b + below + above * bool(start) <= limit
                ]
                cut = max(boundaries) if boundaries else None
                carried_row = False
                if cut is not None and grid.table.page_break != SPLIT_CELLS:
                    # 셀 안에서도 나누는 표는 다음 행이 높이로는 넘쳐도 셀 내용이 다 들어가면 그 행을 이 쪽에 두고 쪽
                    # 바닥에서 잘라 그린다(실측: rowbreak-problem-pages 3쪽 행 8, 저장 높이 67.08pt를 704.59~768.72에).
                    # 셀 내용은 세로 정렬 전 높이로 따진다(실측: kr_156489577 15쪽 가운데 정렬 행 18).
                    after = min((b for b in grid.row_y if b > cut), default=grid.height)
                    # 이 행에서 끝나는 셀은 앞 행에서 내려온 여러 행 셀까지 내용이 들어가야 한다(실측: task2287 25쪽
                    # 행 2~3은 줄이지 않음). 이 행 아래로 이어지는 여러 행 셀은 다음 쪽에서 이어지므로 따지지 않는다.
                    row_lines = [
                        line for line in cell_lines if line.cell_top < after and cut < line.cell_bottom <= after
                    ]
                    # 여러 행 셀 내용이 이 행 안에서 다음 쪽 조각으로 저장됐으면 한글도 이 행을 쪽 바닥에서 나눴다(실측:
                    # task2287 92쪽 행 2~3 셀 조각 40000·1000, 정답은 행 3을 534.57pt에서 잘라 93쪽 나머지 51.69pt).
                    carried_row = any(line.carry for line in row_lines)
                    if (
                        limit < after
                        and any(cut <= line.cell_top for line in row_lines)
                        and all(
                            line.carry == 0
                            and line.anchor is None
                            and line.cell_top + line.margin_top + line.piece_height + line.margin_bottom <= limit
                            for line in row_lines
                        )
                    ):
                        if after >= grid.row_y[-1]:
                            # 마지막 행이면 표가 이 쪽에서 끝나고 쪽 바닥에서 잘린다
                            # (실측: task2097 1741000 2쪽 마지막 행 저장 112pt를 702.43~754.69에).
                            fragments.append((start, start + available))
                            return fragments
                        cut = after
                following = [b for b in grid.row_y if cut is not None and b > cut]
                # 다음 행이 한 쪽보다 길면 이 쪽에서부터 행 안에서 자른다(실측: task2287 25쪽 여러 행 셀).
                oversized = carried_row or bool(following) and min(following) - cut > page_room
                if cut is None or oversized or grid.table.page_break == SPLIT_CELLS and cut - start < available / 2:
                    # 줄은 줄 간격까지 들어가야 이 쪽에 남는다(실측: 80168 29쪽 「등의 명세」 줄 아래 781.7pt <
                    # 한계 782.6pt, 줄 간격 4.4pt를 더하면 넘쳐 정답은 30쪽).
                    crossing = [
                        line.top
                        for line in cell_lines
                        if line.carry == 0
                        and line.top < limit < line.bottom + line.view.segments[line.index].line_spacing
                    ]
                    inside = min(crossing) if crossing else limit
                    # 셀 안 중첩 표가 걸치면 그 표의 행 경계나 줄 사이에서 자른다. 중첩 표는 쪽 조각을 저장하지 않는다
                    # (실측: issue4889 1쪽 끝 행은 2쪽으로, issue2007 2쪽 조문 대비표 4행은 줄 사이에서 나뉨).
                    nested_cut = max(
                        (
                            b
                            for b, below, above in nested_points
                            if start < b <= inside
                            and b + below + above * bool(start) <= limit
                            and not any(top < b < bottom for top, bottom, _, _, _ in nested_lines)
                        ),
                        default=None,
                    )
                    cut = nested_cut if nested_cut is not None else inside if inside > start else limit
            fragments.append((start, cut))
            start = cut if visible_end is None else visible_end
            available = page_room
        fragments.append((start, grid.height))
        return fragments

    def section_geometry_of(self, state: PageState) -> SectionGeometry:
        """맞쪽 보정 전 구역 기하(새 쪽을 만들 때 쓴다)."""
        return state.base_geometry
