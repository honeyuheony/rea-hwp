"""표를 본문 흐름에 놓고, 쪽 바닥을 넘으면 쪽 단위로 나눈다."""

from __future__ import annotations

import dataclasses
from bisect import bisect_right
from dataclasses import dataclass

from .. import tags
from ..document import Node
from ..models.controls import ObjectCtrlHeader, TextWrap
from ..models.paragraph import LineSeg
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
)
from .tables import (
    Cell,
    TableGrid,
    cell_margins,
    cell_pieces,
    draw_table_frame,
    header_row_count,
    stored_height,
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
#: 표 쪽 나눔(명세 표75 bit 0~1): 0 나누지 않음.
NO_SPLIT = 0
#: 표를 쪽에서 자를 때 바깥 아래 여백 위로 더 남기는 길이(HU). 남은 표가 통째로 들어가면 쓰지 않는다.
#: 실측: task2287 25~34쪽 이어지는 표 조각 아래 끝이 본문 바닥 − 바깥 여백보다 1.18~1.20pt 위,
#: issue2063 40쪽은 그 위치에서 0.01pt 넘는 행을 다음 쪽으로 넘김, exam_eng 2쪽 통째 표는 바닥에 딱 맞게 들어감.
#: 이 값이 무엇에서 나오는지는 확인하지 못했다.
SPLIT_RESERVE = 120


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
            assigned.setdefault(number, []).append((line, line.origin - fragments[number][0]))
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


class TableFlowMixin:
    def table_layout_grid(self, ctrl: Node) -> tuple[TableGrid | None, dict[int, list[ParagraphView]]]:
        """표 격자와 셀마다 문단 보기. 셀 문단에 놓인 자리차지 중첩 표는 셀 높이와 쪽 나눔 후보에 넣는다."""
        views: dict[int, list[ParagraphView]] = {}

        def cell_views(table, cell) -> list[list]:
            # LINE_SEG가 없는 셀 문단은 셀 폭으로 합성 줄을 만들어 행 높이와 줄 배치에 함께 쓴다.
            margins = cell_margins(table, cell)
            views[id(cell)] = [
                dataclasses.replace(view, segments=[_restored_baseline(segment) for segment in view.segments])
                for view in self.paragraph_views(cell.paragraphs, cell.header.width - margins[0] - margins[1])
            ]
            lines = [view.segments for view in views[id(cell)]]
            # 저장 쪽 조각이 있는 셀은 문단 위치가 조각마다 다시 세어져 중첩 표 자리를 셀 좌표로 알 수 없다.
            if len(stored_pieces(lines)[1]) == 1:
                for index, view in enumerate(views[id(cell)]):
                    for nested in view.controls.values():
                        if is_block_table(nested):
                            self.add_nested_table(cell, index, view, nested)
            return lines

        return table_grid(ctrl, cell_views), views

    def add_nested_table(self, cell: Cell, index: int, view: ParagraphView, ctrl: Node) -> None:
        """셀 문단에 놓인 자리차지 표의 자리와 아래 끝을 셀에 적는다(문단 기준 위쪽 정렬만 다룬다)."""
        grid, _ = self.table_layout_grid(ctrl)
        if grid is None:
            return
        model = ctrl.model
        margins = model.outer_margins or [0, 0, 0, 0]
        para_top = view.segments[0].vertical_pos if view.segments else 0
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
            if self.in_body and model.vert_rel_to == REL_COLUMN_OR_PARA:
                # 문단 기준 본문 표는 음수 세로 오프셋이어도 본문 위(+ 바깥 위 여백) 위로 올라가지 않는다
                # (실측: 편람 4구역 p64 표 오프셋 −600, 쪽 맨 위 문단이라 정답 위 선 111.95 = 본문 위 110.56 + 1.4).
                top = max(top, state.geometry.body_top + model.outer_margins[2])
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
        cell_lines = self.table_cell_lines(grid, views)
        split = self.in_body
        self.in_body = False
        try:
            own_top = (model.outer_margins or [0, 0, 0, 0])[2]
            fragments = (
                self.table_fragments(
                    state, grid, top, outer, cell_lines, own_top, model.treat_as_char, self.bottom_caption_extent(ctrl)
                )
                if split
                else [(0.0, grid.height)]
            )
            header_rows = header_row_count(grid) if grid.table.repeat_header else 0
            header_height = grid.row_y[header_rows] if header_rows else 0.0
            moved_whole = fragments[0] == (0.0, 0.0)
            # 다음 쪽으로 이어지는 쪽 조각은 쪽 바닥(바깥 여백과 SPLIT_RESERVE 위)까지 보인다
            # (실측: table_scattered_header_rowbreak 2쪽 마지막 행이 811.15pt까지 늘어나고 「강관말뚝」이 그 안 가운데).
            page_bottom = state.geometry.body_top + state.geometry.body_height - outer[1] - SPLIT_RESERVE
            origins = [top] + [
                state.geometry.body_top + continued_top + (0.0 if moved_whole and number == 1 else header_height)
                for number in range(1, len(fragments))
            ]
            if moved_whole:
                # 통째로 다음 쪽에 가는 표는 위쪽 캡션도 함께 가므로 캡션 자리만큼 내려 시작한다
                # (실측: task2287 19쪽 <표 2> 캡션 기준선 68.88, 표 위 선 78.87).
                origins[1] += self.top_caption_extent(ctrl)
            visible_ends = [
                end if number == len(fragments) - 1 else max(end, start + page_bottom - origins[number])
                for number, (start, end) in enumerate(fragments)
            ]
            assigned = assign_fragments(cell_lines, fragments, visible_ends)
            placement = TablePlacement(state, left, top, state, top + grid.height)
            # 쪽을 먼저 모두 만들어 둔다. 셀 안 자리차지 표가 뒤 쪽 조각에 이어 그릴 수 있어야 한다.
            states = [state]
            for _ in fragments[1:]:
                states.append(self.new_page(self.section_geometry_of(states[-1])))
                states[-1].continued_by_table = True
            # 맞쪽 편집이면 이어지는 쪽의 본문 왼쪽이 달라 표도 그만큼 옮긴다(실측: 편람 48·50쪽 dx −12.4pt = 1247HU).
            lefts = [left + page.geometry.body_left - states[0].geometry.body_left for page in states]
            if moved_whole:
                placement.first_state, placement.top, placement.left = states[1], origins[1], lefts[1]
            for number, state in enumerate(states):
                if header_height and number > 0 and not (moved_whole and number == 1):
                    header_top = origins[number] - header_height
                    draw_table_frame(grid, lefts[number], header_top, state.page, self.styles, (0.0, header_height))
                    for line in cell_lines:
                        if line.carry == 0 and line.top < header_height:
                            self.place_cell_line(state, line, lefts[number], header_top + line.origin)
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
                    self.draw_fragment_frame(grid, lefts[number], origin, state, (start, end), bottom)
                    for line, offset in assigned.get(number, []):
                        self.place_cell_line(state, line, lefts[number], origin + offset)
                    if split:
                        self.table_bottom = origin + (end - start)
                    placement.last_state, placement.bottom = state, origin + (end - start)
            finally:
                self.host_fragments = saved_host
            self.current_state = state
        finally:
            self.in_body = split
        return placement

    def draw_fragment_frame(
        self, grid: TableGrid, left: float, origin: float, state: PageState, clip: tuple[float, float], visible: float
    ) -> None:
        """쪽 조각 테두리. 다음 쪽으로 이어지는 셀은 자른 곳이 아니라 보이는 아래 끝까지 그린다.

        실측: issue4514 10쪽과 task2287 26~28쪽 조각 아래 선이 자른 곳이 아니라 쪽마다 같은 자리(781.91·536.01pt)다.
        셀 안에서도 나누는 표는 자른 곳에서 시작하는 행도 빈 조각으로 쪽 바닥까지 그린다(실측: issue4514 9쪽
        page_break 2, 다음 쪽으로 넘긴 행이 747.26~781.91pt에 빈 칸). 셀 단위로 나누는 표는 그리지 않는다
        (실측: issue2063 40쪽 page_break 1, 537.45pt 아래 선 없음).
        """
        start, end = clip
        if visible <= end:
            # 쪽 바닥을 넘는 조각(내용만 들어가 이 쪽에 둔 행)은 쪽 바닥에서 잘라 그린다
            # (실측: rowbreak-problem-pages 3쪽 행 8 아래 선 768.72pt, 행 끝은 771.67pt).
            draw_table_frame(grid, left, origin, state.page, self.styles, (start, visible))
            return
        starting = grid.table.page_break != SPLIT_CELLS
        continued = []
        for cell in grid.cells:
            _, top, _, height = grid.cell_box(cell)
            if (top < end or starting and top == end) and end < top + height:
                continued.append(cell)
        kept = [cell for cell in grid.cells if not any(cell is other for other in continued)]
        draw_table_frame(dataclasses.replace(grid, cells=kept), left, origin, state.page, self.styles, clip)
        # 이어지는 셀은 제 높이가 짧아도 쪽 바닥까지 늘여 그린다(issue4514 9쪽 빈 칸에서 넓힌 규칙, 검증 안 됨).
        stretched = [y if y <= end else max(y, visible) for y in grid.row_y]
        draw_table_frame(
            dataclasses.replace(grid, cells=continued, row_y=stretched),
            left,
            origin,
            state.page,
            self.styles,
            (start, visible),
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
                draw_table_frame(grid, page_left, ys[k], page.page, self.styles, (low, high))
                for cell_line, offset in assigned.get(k, []):
                    self.place_cell_line(page, cell_line, page_left, ys[k] + offset)
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
                draw_table_frame(grid, page_left, y, state.page, self.styles, (low, high))
                for line, offset in assigned.get(index, []):
                    self.place_cell_line(state, line, page_left, y + offset)
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
            content = max(heights[0] if starts is None else starts[-1] + heights[-1], cell.floor)
            shift = 0.0
            if cell.header.vert_align == 1 and not multi_piece:
                shift = max(0.0, (inner_height - content) / 2)
            elif cell.header.vert_align == 2 and not multi_piece:
                shift = max(0.0, inner_height - content)
            for paragraph, (view, numbers) in enumerate(zip(cell_views, pieces, strict=True)):
                # 쪽을 넘는 중첩 표 뒤 문단은 그 표가 끝난 쪽에 놓이고, 위치는 그 쪽 조각 맨 위에서 센다
                # (실측: issue4889 3쪽 「3. 국제입찰 대상 공사」 vertical_pos 50540 = 3쪽 셀 맨 위에서 505.4pt).
                resume = cell.resume is not None and paragraph >= cell.resume[0]
                for index, segment in enumerate(view.segments):
                    piece = numbers[index]
                    lift = starts[min(piece, len(starts) - 1)] if starts else 0.0
                    origin = cy + margins[2] + shift + lift
                    line_top = origin + segment.vertical_pos
                    placed.append(
                        CellLine(
                            line_top,
                            line_top + segment.line_height,
                            view,
                            index,
                            cx + margins[0],
                            width - margins[0] - margins[1],
                            origin,
                            piece if carried else 0,
                            cy,
                            margins[2],
                            cy + height,
                            margins[3],
                            0 if resume or starts is not None and piece > 0 else cell.header.vert_align,
                            heights[piece],
                            carried or resume,
                            cy + margins[2] + cell.resume[1] if resume else None,
                            room_limit,
                        )
                    )
        return placed

    def place_cell_line(self, state: PageState, line: CellLine, left: float, list_top: float) -> None:
        saved = self.__dict__.get("cell_line")
        self.cell_line = line
        try:
            self.place_line(state, line.view, line.index, Area(left + line.inner_x, list_top, line.inner_width))
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
            if grid.height - start <= available and not pending:
                break
            limit = start + available - SPLIT_RESERVE
            if pending and pending[0] <= limit + slack:
                cut = pending[0]
            else:
                boundaries = [b for b in grid.row_y if start < b <= limit and b not in spanned]
                # 이어지는 쪽에서는 중첩 표가 겹친 위 여백 아래에서 다시 시작하므로 그만큼 덜 들어간다.
                boundaries += [
                    b for b, below, above in nested_rows if start < b and b + below + above * bool(start) <= limit
                ]
                cut = max(boundaries) if boundaries else None
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
                oversized = bool(following) and min(following) - cut > page_room
                if cut is None or oversized or grid.table.page_break == SPLIT_CELLS and cut - start < available / 2:
                    crossing = [line.top for line in cell_lines if line.carry == 0 and line.top < limit < line.bottom]
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
            start = cut
            available = page_room
        fragments.append((start, grid.height))
        return fragments

    def section_geometry_of(self, state: PageState) -> SectionGeometry:
        """맞쪽 보정 전 구역 기하(새 쪽을 만들 때 쓴다)."""
        return state.base_geometry
