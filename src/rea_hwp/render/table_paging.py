"""표를 쪽 조각으로 나누는 한글 2022 행 배치 루프(HwpApp.dll 역분석)를 옮긴 순수 함수.

함수 이름은 HwpApp.dll 주소: FUN_101e9fc0(행 루프), FUN_101f0620(행 생성), FUN_101ed0b0(칸 판정), FUN_101ed510(행 경계).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

#: 쪽 나눔 방식(표 attr bit 0~1): 0 나누지 않음, 1 행 단위(셀을 자르지 않음), 2 셀 글을 줄 단위로 자름.
MODE_NONE = 0
MODE_ROWS = 1
MODE_LINES = 2
#: 남은 높이를 무제한으로 두는 값(제목 행, 억지 배치).
UNLIMITED = -1.0
#: 글이 다 놓인 칸에서 남는 빈 높이가 이보다 작으면 다음 쪽으로 넘기지 않는다(1mm + 1000HU, 상수 초기화 코드에서 확인).
CARRY_THRESHOLD = 1283
#: 행 높이의 최소 증가와 쪽 바닥 판정에서 빼는 값(HU). 표 attr bit 3이면 1이다(뜻은 확인하지 못함).
MIN_UNIT = 100
#: 한 표의 쪽 조각 수 상한(무한 반복 방지).
MAX_FRAGMENTS = 2000
#: 높이 제한이 없을 때 쓰는 가용 높이.
BIG = 1e15

# 칸 표지(HWPCELLLO [3]).
CARRIED = 1
CONTINUES = 2
NO_LINE = 4
TEXT_DONE = 8


@dataclass
class PagingLine:
    #: 셀 글 영역 안의 연속 세로 위치(저장 쪽 조각의 되감김을 이어 붙인 좌표).
    top: float
    height: float
    #: 조각 첫 줄일 때 줄 위에 함께 두는 간격(문단 위 간격, 셀 첫 줄은 저장 vertical_pos).
    lead: float = 0.0
    #: 이 줄과 함께 놓이는 떠 있는 개체의 아래 끝(줄 위 기준). 줄 높이보다 크면 판정과 내용 높이에 이 값을 쓴다.
    reach: float = 0.0

    @property
    def bottom(self) -> float:
        return self.top + max(self.height, self.reach)


@dataclass
class PagingBlock:
    """셀 글 안의 자리차지 표. 셀이 쪽 조각으로 나뉘면 이 표도 같은 규칙으로 나뉘어 이어진다."""

    #: 표 위 끝의 셀 글 연속 좌표(바깥 위 여백 아래).
    top: float
    table: PagingTable
    margin_top: float
    margin_bottom: float
    #: 그리는 쪽에서 표를 찾는 열쇠(이 모듈은 쓰지 않는다).
    key: object = None
    _whole: TableFragment | None = None

    @property
    def whole(self) -> TableFragment:
        """나누지 않고 놓은 조각."""
        if self._whole is None:
            self._whole = paginate_table(self.table, BIG, BIG, True).fragments[0]
        return self._whole

    @property
    def height(self) -> float:
        return self.whole.bottom

    @property
    def end(self) -> float:
        """뒤 셀 글이 이어지는 연속 좌표."""
        return self.top + self.height + self.margin_bottom


@dataclass
class PagingCell:
    row: int
    col: int
    row_span: int
    col_span: int
    stored_height: float
    margin_top: float
    margin_bottom: float
    is_header: bool
    #: 셀 글 항목(줄과 자리차지 표)을 연속 좌표 순서로.
    lines: list[PagingLine | PagingBlock]

    @property
    def end(self) -> int:
        return self.row + self.row_span


@dataclass
class PagingTable:
    rows: int
    cells: list[PagingCell]
    mode: int
    repeat_header: bool
    spacing: float = 0.0
    unit: int = MIN_UNIT
    _analysis: tuple[list[tuple[int, int]], int] | None = None

    def analysis(self) -> tuple[list[tuple[int, int]], int]:
        """(행 묶음, 반복할 제목 행 수)."""
        if self._analysis is None:
            bundles = row_bundles(self)
            header_rows = header_row_count(self, bundles) if self.repeat_header and self.mode != MODE_NONE else 0
            self._analysis = (bundles, header_rows)
        return self._analysis


@dataclass
class CellBox:
    """쪽 조각 안의 셀 칸 하나(y는 조각 맨 위 기준)."""

    cell: int
    y: float
    height: float
    #: 이 조각에서 다루는 셀 글 항목 범위 [first_line, end_line). 끝 항목이 나뉘는 중인 표면 end_line은 그 항목이다.
    first_line: int
    end_line: int
    #: 위·아래 여백 + 이 조각에 놓인 글 높이.
    content: float
    flags: int = 0
    #: 놓은 항목마다 (항목 번호, 셀 글 영역 안 위 y, 표 항목이면 그 조각).
    placements: list[tuple[int, float, TableFragment | None]] = field(default_factory=list)
    #: 조각을 시작할 때와 끝낼 때 나뉘는 중인 셀 안 표의 진행 상태.
    resume: PagerState | None = None
    block: PagerState | None = None

    @property
    def carried(self) -> bool:
        return bool(self.flags & CARRIED)

    @property
    def continues(self) -> bool:
        return bool(self.flags & CONTINUES)


@dataclass
class TableFragment:
    #: 조각에 놓인 표 행 번호와 행마다 위 y, 마지막 값은 조각 아래(셀 간격 포함).
    rows: list[int]
    row_tops: list[float]
    boxes: list[CellBox]
    start_y: float
    continues: bool

    @property
    def bottom(self) -> float:
        return self.row_tops[-1]


@dataclass
class TablePaging:
    fragments: list[TableFragment]
    #: 첫 조각이 한 행도 놓지 못해(-3) 표 전체를 다음 쪽으로 옮겼는가.
    moved_whole: bool
    header_rows: int
    #: 이어지는 조각 위에 다시 그리는 제목 영역 높이(0이면 반복하지 않음).
    header_height: float = 0.0


@dataclass
class _Row:
    row: int
    top: float
    boxes: list[CellBox] = field(default_factory=list)


@dataclass
class _CellState:
    next_line: int = 0
    used: float = 0.0
    done: bool = False
    block: PagerState | None = None


@dataclass
class PagerState:
    """표 하나의 쪽 나눔 진행 상태. advance는 이것을 고치지 않고 새 상태를 돌려준다."""

    states: list[_CellState]
    start_row: int = 0
    carried: list[int] = field(default_factory=list)
    header_height: float = 0.0
    number: int = 0

    @classmethod
    def start(cls, table: PagingTable) -> PagerState:
        return cls([_CellState() for _ in table.cells])


class _Move(Exception):
    """조각 첫 행에 놓을 수 없음(-3)."""


def min_height(table: PagingTable, cell: PagingCell) -> float:
    """FUN_101aefd0: max(최소 단위, 위 여백 + 아래 여백)."""
    return max(table.unit, cell.margin_top + cell.margin_bottom)


def row_bundles(table: PagingTable) -> list[tuple[int, int]]:
    """행마다 (행 묶음 시작, 행 묶음 끝). 묶음은 여러 행 셀로 이어진 연속 행이다(FUN_101ec500)."""
    ends = [r + 1 for r in range(table.rows)]
    for cell in table.cells:
        if cell.row < table.rows:
            ends[cell.row] = max(ends[cell.row], min(cell.end, table.rows))
    bundles = [(r, r + 1) for r in range(table.rows)]
    r = 0
    while r < table.rows:
        end = ends[r]
        k = r
        while k < end:
            end = max(end, ends[k])
            k += 1
        for k in range(r, end):
            bundles[k] = (r, end)
        r = end
    return bundles


def header_row_count(table: PagingTable, bundles: list[tuple[int, int]] | None = None) -> int:
    """0행부터 이어지는 묶음 가운데 묶음 시작 행에서 시작하는 셀 하나라도 제목 셀인 묶음까지의 행 수."""
    bundles = bundles if bundles is not None else row_bundles(table)
    count = 0
    r = 0
    while r < table.rows:
        start, end = bundles[r]
        if not any(cell.is_header and cell.row == start for cell in table.cells):
            break
        count = end
        r = end
    return count


def split_cell_text(cell: PagingCell, start: int, remaining: float, carried: bool) -> tuple[int, int, float]:
    """split_cell_items의 (결과, 끝 항목, 놓은 글 높이)."""
    result, end, height, _, _ = split_cell_items(cell, start, None, remaining, carried)
    return result, end, height


def split_cell_items(
    cell: PagingCell, start: int, resume: PagerState | None, remaining: float, carried: bool
) -> tuple[int, int, float, list[tuple[int, float, TableFragment | None]], PagerState | None]:
    """셀 글을 남은 높이에 맞춰 자른다(FUN_101f3b10). (결과 1·2·3, 끝 항목, 글 높이, 놓은 항목, 나뉘는 중인 표 상태).

    줄은 (조각 안 줄 위 + 줄 높이) <= 남은 글 높이면 들어간다(줄 간격 제외, 셀은 이하 비교).
    이어진 칸은 첫 항목이 들어가지 않아도 놓는다(조각 첫 줄 억지 배치, 앞 조각에서 이어진 칸만).
    자리차지 표가 다 들어가지 않으면 같은 규칙으로 한 조각만 놓고 셀은 다음 조각으로 이어진다.
    """
    items = cell.lines
    room = BIG if remaining == UNLIMITED else max(0.0, remaining - cell.margin_top - cell.margin_bottom)
    placements: list[tuple[int, float, TableFragment | None]] = []
    height = 0.0
    index = start
    origin = 0.0
    if resume is not None:
        block = items[index]
        assert isinstance(block, PagingBlock)
        fragment, progress = advance(block.table, resume, room - block.margin_top - block.margin_bottom, True)
        assert fragment is not None
        placements.append((index, block.margin_top, fragment))
        bottom = block.margin_top + fragment.bottom
        if fragment.continues:
            return 2, index, bottom, placements, progress
        height = bottom + block.margin_bottom
        origin = block.end - height
        index += 1
    elif index < len(items):
        first = items[index]
        origin = first.top - (first.lead if isinstance(first, PagingLine) else first.margin_top)
        # 조각 첫 줄 바로 위에 같은 문단의 자리차지 표가 붙으면 문단 위 간격 없이 조각 맨 위에서 시작한다(실측:
        # task1718 41쪽 셀 문단 위 간격 500 뒤의 1×1 표, 정답은 5pt 위. 전체 평가 +1,123, 하락 0).
        if (
            isinstance(first, PagingLine)
            and index + 1 < len(items)
            and isinstance(items[index + 1], PagingBlock)
            and abs(items[index + 1].top - items[index + 1].margin_top - first.top) < 1
        ):
            origin = first.top
    while index < len(items):
        item = items[index]
        forced = carried and not placements
        if isinstance(item, PagingLine):
            bottom = item.bottom - origin
            if bottom > room and not forced:
                break
            placements.append((index, item.top - origin, None))
            height = max(height, bottom)
            index += 1
            continue
        top = item.top - origin
        if top + item.height + item.margin_bottom <= room:
            placements.append((index, top, item.whole))
            height = max(height, top + item.height + item.margin_bottom)
            index += 1
            continue
        fragment, progress = advance(item.table, PagerState.start(item.table), room - top - item.margin_bottom, forced)
        if fragment is None:
            break
        placements.append((index, top, fragment))
        if fragment.continues:
            return 2, index, max(height, top + fragment.bottom), placements, progress
        height = max(height, top + fragment.bottom + item.margin_bottom)
        origin = item.end - (top + fragment.bottom + item.margin_bottom)
        index += 1
    if not placements:
        return 3, start, 0.0, [], resume
    return (1 if index == len(items) else 2), index, height, placements, None


def paginate_table(
    table: PagingTable,
    first_available: float,
    next_available: float,
    force_first: bool = False,
    reductions: list[float] | None = None,
) -> TablePaging:
    """표를 쪽 조각으로 나눈다. available은 조각 가용 높이(바깥 여백·캡션을 뺀 값)다.

    force_first면 첫 조각에서도 -3(표 전체 다음 쪽)을 내지 않고 억지로 놓는다(이미 쪽 맨 위인 표).
    reductions[k]는 표가 걸친 k번째 쪽(표를 옮겼으면 옮기기 전 쪽이 0)의 가용 높이에서 더 뺄 값(각주 자리)이다.
    """
    _, header_rows = table.analysis()
    pager = PagerState.start(table)
    fragments: list[TableFragment] = []
    moved_whole = False
    while len(fragments) < MAX_FRAGMENTS:
        number = len(fragments)
        available = first_available if number == 0 and not moved_whole else next_available
        page = number + moved_whole
        if reductions and page < len(reductions):
            available -= reductions[page]
        force = force_first if number == 0 and not moved_whole else True
        fragment, following = advance(table, pager, available, force)
        if fragment is None:
            if number != 0 or moved_whole:
                raise RuntimeError("table fragment could not be placed even when forced")
            moved_whole = True
            continue
        if number == 0 and not force and fragment.continues and _header_only(fragment, header_rows):
            # 역분석 행 루프는 제목 행만 남긴 첫 조각을 허용하지만 정답은 표 전체를 옮긴다(task2287 19쪽 <표 2>).
            moved_whole = True
            continue
        fragments.append(fragment)
        pager = following
        if not fragment.continues:
            break
    return TablePaging(fragments, moved_whole, header_rows, pager.header_height)


def advance(
    table: PagingTable, pager: PagerState, available: float, force: bool
) -> tuple[TableFragment | None, PagerState]:
    """다음 쪽 조각 하나를 놓는다. 첫 조각을 놓을 수 없으면(-3) (None, pager)."""
    bundles, header_rows = table.analysis()
    start_y = table.spacing
    if pager.number > 0 and pager.header_height:
        start_y += table.spacing + pager.header_height
    if table.mode == MODE_NONE:
        # 나누지 않는 표는 통째로 놓고, 들어가지 않으면 억지 배치가 아닌 한 다음 조각으로 미룬다.
        fragment = _place_fragment(table, bundles, header_rows, pager.states, 0, [], start_y, BIG, True)
        if fragment.bottom > available and not force:
            return None, pager
    else:
        try:
            fragment = _place_fragment(
                table, bundles, header_rows, pager.states, pager.start_row, pager.carried, start_y, available, force
            )
        except _Move:
            return None, pager
    following = PagerState(_committed(pager.states, fragment), number=pager.number + 1)
    following.header_height = pager.header_height
    if pager.number == 0 and fragment.continues and header_rows:
        following.header_height = _header_height(table, fragment, header_rows)
    if fragment.continues:
        following.start_row, following.carried = _next_start(table, fragment)
    return fragment, following


def _header_only(fragment: TableFragment, header_rows: int) -> bool:
    return bool(header_rows) and all(row < header_rows for row in fragment.rows)


def _header_height(table: PagingTable, fragment: TableFragment, header_rows: int) -> float:
    """첫 조각의 마지막 제목 행 0열 칸(가려졌으면 덮는 셀) 아래 − 셀 간격."""
    row = header_rows - 1
    for box in fragment.boxes:
        cell = table.cells[box.cell]
        if cell.row <= row < cell.end and cell.col <= 0 < cell.col + cell.col_span:
            return box.y + box.height - table.spacing
    return 0.0


def _committed(states: list[_CellState], fragment: TableFragment) -> list[_CellState]:
    following = [dataclasses.replace(state) for state in states]
    for box in fragment.boxes:
        state = following[box.cell]
        state.next_line = box.end_line
        state.used += box.height
        state.done = state.done or bool(box.flags & TEXT_DONE)
        state.block = box.block
    return following


def _next_start(table: PagingTable, fragment: TableFragment) -> tuple[int, list[int]]:
    """FUN_101f0620 첫 행: 앞 조각 마지막 행에서 이어지는 칸과 다음 시작 행."""
    last = fragment.rows[-1]
    reaching = [box for box in fragment.boxes if table.cells[box.cell].row <= last < table.cells[box.cell].end]
    continuing = [box.cell for box in reaching if box.continues]
    ends = [table.cells[box.cell].end for box in reaching if not box.continues]
    starts = [table.cells[cell].row for cell in continuing]
    if continuing and (not ends or max(ends) <= max(starts)):
        start = max(max(starts), last)
    else:
        start = max(max(ends, default=last + 1), last + 1)
    return start, continuing


def _place_fragment(
    table: PagingTable,
    bundles: list[tuple[int, int]],
    header_rows: int,
    states: list[_CellState],
    start_row: int,
    carried: list[int],
    start_y: float,
    available: float,
    force: bool,
) -> TableFragment:
    rows: list[_Row] = []
    y = start_y
    row = start_row
    continuing_end: int | None = None
    by_row: dict[int, list[int]] = {}
    for index, cell in enumerate(table.cells):
        by_row.setdefault(cell.row, []).append(index)
    while True:
        current = _Row(row, y)
        fresh = by_row.get(row, [])
        if not rows:
            current.boxes += [_carried_box(table, states, index, y) for index in carried]
            fresh = [index for index in fresh if index not in carried]
        current.boxes += [_new_box(table, states, index, y) for index in fresh]
        rows.append(current)
        if header_rows and row < header_rows:
            remaining = UNLIMITED
        else:
            remaining = max(0.0, available - table.unit - table.spacing - y)
        verdict, continuing_end = _judge_row(table, bundles, rows, remaining, force, continuing_end)
        if verdict == "close":
            bottom = _close_row(table, rows, rows[-1].top, True, continuing_end)
            break
        y = _close_row(table, rows, current.top, False, continuing_end)
        next_row = row + 1
        if continuing_end is not None and continuing_end <= next_row or next_row >= table.rows:
            bottom = _close_row(table, rows, rows[-1].top, True, continuing_end)
            break
        row = next_row
    boxes = [box for item in rows for box in item.boxes]
    continues = any(box.continues for box in boxes) or rows[-1].row + 1 < table.rows
    return TableFragment([item.row for item in rows], [*(item.top for item in rows), bottom], boxes, start_y, continues)


def _new_box(table: PagingTable, states: list[_CellState], index: int, y: float) -> CellBox:
    cell = table.cells[index]
    height = max(cell.stored_height, min_height(table, cell))
    start = states[index].next_line
    return CellBox(index, y, height, start, start, 0.0, resume=states[index].block)


def _carried_box(table: PagingTable, states: list[_CellState], index: int, y: float) -> CellBox:
    """앞 조각에서 잘려 이어진 칸: h = max(최소, max(저장, 최소) − 앞 조각들에서 쓴 높이)."""
    cell = table.cells[index]
    state = states[index]
    floor = min_height(table, cell)
    height = max(floor, max(cell.stored_height, floor) - state.used)
    flags = CARRIED | (TEXT_DONE if state.done else 0)
    start = state.next_line
    return CellBox(index, y, height, start, start, 0.0, flags, resume=state.block)


def _judge_row(
    table: PagingTable,
    bundles: list[tuple[int, int]],
    rows: list[_Row],
    remaining: float,
    force: bool,
    continuing_end: int | None,
) -> tuple[str, int | None]:
    """FUN_101ed0b0: 칸마다 글을 자르고 높이를 정한다. ("ok"|"close", 이어지는 칸의 가장 이른 끝 행).

    행을 지우거나 되돌리면 rows를 줄이고 "close"를 돌려준다. 첫 행이라 놓을 수 없으면 _Move.
    """
    current = rows[-1]
    first_row = rows[0].row
    bundle_start = bundles[current.row][0] if current.row < len(bundles) else current.row
    if table.mode == MODE_ROWS and remaining != UNLIMITED and force and bundle_start <= first_row:
        remaining = UNLIMITED
    rows_limited = table.mode == MODE_ROWS and remaining != UNLIMITED
    lines_limited = table.mode == MODE_LINES and remaining != UNLIMITED
    verdict = "close" if current.row + 1 >= table.rows else "ok"
    for box in current.boxes:
        cell = table.cells[box.cell]
        if rows_limited and remaining < box.height or box.flags & TEXT_DONE:
            box.flags |= NO_LINE
        else:
            result, box.end_line, text, box.placements, box.block = split_cell_items(
                cell, box.first_line, box.resume, remaining if lines_limited else UNLIMITED, box.carried
            )
            box.flags |= {1: TEXT_DONE, 2: CONTINUES, 3: CONTINUES | NO_LINE}[result]
            box.content = cell.margin_top + cell.margin_bottom + text
        if box.flags & NO_LINE:
            box.content = min_height(table, cell)
            box.end_line = box.first_line
            box.placements = []
            box.block = box.resume
        if rows_limited and (box.flags & NO_LINE or remaining < box.content):
            if bundle_start > first_row:
                del rows[next(k for k, item in enumerate(rows) if item.row >= bundle_start) :]
                return "close", bundle_start
            raise _Move
        if lines_limited and (box.flags & (CARRIED | NO_LINE)) == NO_LINE:
            if current.row > first_row:
                rows.pop()
                return "close", continuing_end
            if not force:
                raise _Move
        if box.content < box.height:
            if lines_limited and remaining < box.height:
                cut = max(remaining, box.content)
                if not box.flags & TEXT_DONE or box.height - cut >= CARRY_THRESHOLD:
                    box.flags |= CONTINUES
                else:
                    box.flags &= ~CONTINUES
                box.height = cut
        else:
            box.height = box.content
        if box.flags & CONTINUES:
            end = cell.end
            continuing_end = end if continuing_end is None else min(continuing_end, end)
            verdict = "ok"
    return verdict, continuing_end


def _close_row(table: PagingTable, rows: list[_Row], top: float, last: bool, continuing_end: int | None) -> float:
    """FUN_101ed510: 행 아래 경계를 정하고 그 행에서 끝나는 칸(마지막 행이면 이어지는 칸도)을 경계까지 늘린다."""
    last_row = rows[-1].row
    bottom = top + table.unit
    if last and table.mode == MODE_LINES:
        threshold = last_row + 2 if continuing_end is None else min(continuing_end, last_row + 2)
        for item in rows:
            for box in item.boxes:
                if not box.continues and table.cells[box.cell].end >= threshold:
                    box.flags |= CONTINUES
    closing = [
        box
        for item in rows
        for box in item.boxes
        if last and box.continues or table.cells[box.cell].end == last_row + 1
    ]
    for box in closing:
        bottom = max(bottom, box.y + box.height + table.spacing)
    for box in closing:
        box.height = bottom - box.y - table.spacing
    return bottom
