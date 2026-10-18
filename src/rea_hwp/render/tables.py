"""표 컨트롤의 열·행 경계를 정하고 셀 배경·테두리를 그린다."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .. import tags
from ..document import Node
from ..models.controls import CellListHeader, ColumnDefCtrl, Table
from .borders import border_lines
from .scene import Page, Rect
from .styles import DocStyles, colorref

#: LINE_SEG 태그 bit17: 줄의 첫 세그먼트.
LINE_FIRST_SEGMENT = 1 << 17


@dataclass
class Cell:
    header: CellListHeader
    #: 셀 문단(LIST_HEADER 뒤에 같은 수준으로 이어지는 PARA_HEADER).
    paragraphs: list[Node]
    #: 문단마다 줄 배치(저장 LINE_SEG, 없으면 table_grid에 넘긴 함수가 만든 합성 줄).
    lines: list[list] = field(default_factory=list)
    #: 셀 문단에 놓인 쪽을 넘는 자리차지 표(셀 문단 목록 원점에서 표 위 끝까지 거리, 격자, 표 바깥 아래·위 여백).
    nested: list[tuple[float, TableGrid, int, int]] = field(default_factory=list)
    #: 중첩 표가 셀 문단 목록 원점에서 차지하는 아래 끝(바깥 아래 여백 포함). 셀 내용 높이의 하한이다.
    floor: float = 0.0
    #: 쪽을 넘는 중첩 표 다음 문단 번호와 그 표 아래 끝(셀 문단 목록 원점 기준).
    resume: tuple[int, float] | None = None
    #: 문단이 바뀌어도 vertical_pos 0 다음 0을 새 쪽 조각으로 센다(stored_pieces paragraph_restarts).
    paragraph_restarts: bool = False


@dataclass
class TableGrid:
    table: Table
    cells: list[Cell]
    col_x: list[float]
    row_y: list[float]
    #: 쪽을 넘는 한 행 셀((행, 열))마다 저장 쪽 조각의 셀 안 시작 위치(piece_offsets).
    piece_starts: dict[tuple[int, int], list[float]]
    #: 셀 간격. 행·열 경계(row_y·col_x)는 간격을 앞에 포함하고, 셀은 경계에서 간격만큼 뒤에서 시작한다.
    spacing: float = 0.0
    #: 쪽을 넘는 여러 행 셀((시작 행, 열))의 저장 쪽 조각 시작 위치(셀 맨 위 기준, piece_offsets와 같은 셈).
    #: 자를 자리 후보로만 쓴다. 셀 높이·행 경계·셀 안 줄 위치에는 쓰지 않는다.
    span_piece_starts: dict[tuple[int, int], list[float]] = field(default_factory=dict)
    #: 셀 하나라도 제목 셀이면 제목 행으로 본다(기본은 행의 모든 셀이 제목 셀일 때).
    header_any: bool = False
    #: 낡은 저장 줄을 셀 폭으로 다시 나눈 셀이 있는 격자인가.
    recomposed: bool = False

    @property
    def width(self) -> float:
        return self.col_x[-1] + self.spacing

    @property
    def height(self) -> float:
        return self.row_y[-1] + self.spacing

    @property
    def forced_cuts(self) -> list[float]:
        """한글이 저장한 셀 안 쪽 나눔 위치(표 세로 좌표). 셀 안 중첩 표의 저장 쪽 나눔도 넣는다."""
        cuts = {
            self.row_y[row] + self.spacing + start
            for (row, _), starts in self.piece_starts.items()
            for start in starts[1:]
        }
        cuts.update(top + cut for top, grid, _, _ in self.nested_tables() for cut in grid.forced_cuts)
        return sorted(cut for cut in cuts if cut < self.height)

    @property
    def nested_rows(self) -> list[tuple[float, float, float]]:
        """셀 안 중첩 표의 (행 경계, 아래로 남는 여백 합, 이어지는 쪽에서 위에 다시 붙는 여백 합)."""
        rows = []
        for top, grid, below, above in self.nested_tables():
            rows += [(top + y, below, above) for y in grid.row_y[1:]]
            rows += [
                (top + y, below + inner_below, above + inner_above) for y, inner_below, inner_above in grid.nested_rows
            ]
        return sorted(rows)

    @property
    def nested_lines(self) -> list[tuple[float, float, float, float, bool]]:
        """셀 안 중첩 표의 셀 줄마다 (위, 아래, 아래 여백 합, 이어지는 쪽 위 여백 합, 셀 첫 줄). 위쪽 정렬로 본다."""
        lines = []
        for top, grid, below, above in self.nested_tables():
            for cell in grid.cells:
                margins = cell_margins(grid.table, cell)
                origin = top + grid.row_y[cell.header.row] + margins[2]
                segments = [segment for paragraph in cell.lines for segment in paragraph]
                for index, segment in enumerate(segments):
                    line_top = origin + segment.vertical_pos
                    lines.append(
                        (line_top, line_top + segment.line_height, below + margins[3], above + margins[2], index == 0)
                    )
            lines += [
                (top + line_top, top + line_bottom, below + inner_below, above + inner_above, first)
                for line_top, line_bottom, inner_below, inner_above, first in grid.nested_lines
            ]
        return lines

    def nested_tables(self) -> list[tuple[float, TableGrid, float, float]]:
        """셀 안 중첩 표마다 (이 표 세로 좌표의 위 끝, 격자, 셀·표 바깥 아래 여백 합, 셀·표 바깥 위 여백 합)."""
        found = []
        for cell in self.cells:
            if cell.nested:
                margins = cell_margins(self.table, cell)
                base = self.row_y[cell.header.row] + self.spacing + margins[2]
                found += [
                    (base + offset, grid, margins[3] + below, margins[2] + above)
                    for offset, grid, below, above in cell.nested
                ]
        return found

    def cell_box(self, cell: Cell) -> tuple[float, float, float, float]:
        header = cell.header
        x = self.col_x[header.col] + self.spacing
        y = self.row_y[header.row] + self.spacing
        right = self.col_x[min(header.col + header.col_span, len(self.col_x) - 1)]
        bottom = self.row_y[min(header.row + header.row_span, len(self.row_y) - 1)]
        return x, y, right - x, bottom - y


def collect_cells(ctrl: Node) -> tuple[Table | None, list[Cell]]:
    table: Table | None = None
    cells: list[Cell] = []
    for child in ctrl.children:
        if child.tag == tags.TABLE and isinstance(child.model, Table):
            table = child.model
        elif child.tag == tags.LIST_HEADER and isinstance(child.model, CellListHeader):
            cells.append(Cell(child.model, []))
        elif child.tag == tags.PARA_HEADER and cells:
            cells[-1].paragraphs.append(child)
    return table, cells


def _solve_boundaries(count: int, spans: list[tuple[int, int, int]]) -> list[float]:
    """(시작, 칸 수, 길이) 제약으로 경계 좌표를 정한다. 먼저 한 칸짜리, 다음 여러 칸 제약을 풀고 남은 빈칸은 나눈다."""
    bound: list[float | None] = [None] * (count + 1)
    bound[0] = 0.0
    changed = True
    # 같은 칸 범위의 제약이 여럿이면 가장 긴 것이 그 범위를 정한다(실측: task2287 113쪽 5행 높이 40.76pt, 셀 4082).
    longest: dict[tuple[int, int], int] = {}
    for start, span, length in spans:
        longest[start, span] = max(length, longest.get((start, span), length))
    ordered = sorted(((start, span, length) for (start, span), length in longest.items()), key=lambda item: item[1])
    while changed:
        changed = False
        for start, span, length in ordered:
            end = min(start + span, count)
            if bound[start] is not None and bound[end] is None:
                bound[end] = bound[start] + length
                changed = True
            elif bound[end] is not None and bound[start] is None:
                bound[start] = bound[end] - length
                changed = True
    known = [i for i, value in enumerate(bound) if value is not None]
    for index in range(count + 1):
        if bound[index] is None:
            left = max(i for i in known if i < index)
            right = min((i for i in known if i > index), default=None)
            if right is None:
                bound[index] = bound[left] + 0.0
            else:
                bound[index] = bound[left] + (bound[right] - bound[left]) * (index - left) / (right - left)
    return [float(value) for value in bound]


def stored_lines(paragraph: Node) -> list:
    found = paragraph.find(tags.PARA_LINE_SEG)
    return (getattr(found.model, "segments", None) or []) if found else []


def stored_pieces(
    lines: list[list], columns: bool = False, paragraph_restarts: bool = False, column_count: int = 1
) -> tuple[list[list[int]], list[int]]:
    """줄마다 쪽 조각 번호와 조각별 내용 높이. 셀이 쪽을 넘으면 한글은 다음 쪽 줄의 vertical_pos를 다시 센다.

    앞 조각이 0에 놓인 한 줄뿐이면 다음 쪽 줄도 0으로 저장되므로, 같은 문단 안에서 0이 이어지는 줄도 새 조각으로
    본다(실측: table_scattered_header_rowbreak 3쪽 끝 셀 「바다모래인 / 경우」). 다단 셀(columns)의 0은 다음 단이다.
    """
    pieces, _ = _pieces_and_columns(lines, columns, paragraph_restarts, column_count)
    heights = [0] * (max((number for numbers in pieces for number in numbers), default=0) + 1)
    for segments, numbers in zip(lines, pieces, strict=True):
        for segment, number in zip(segments, numbers, strict=True):
            heights[number] = max(heights[number], segment.vertical_pos + segment.line_height)
    return pieces, heights


def _pieces_and_columns(
    lines: list[list], columns: bool, paragraph_restarts: bool, column_count: int
) -> tuple[list[list[int]], list[list[int]]]:
    """줄마다 (쪽 조각 번호, 단 번호). 다단 셀은 되감길 때마다 다음 단으로 가고 단이 다 차면 새 쪽 조각이다
    (실측: multicolumns-layout 4쪽 2단 셀, 4800 다음 0이 정답에서 같은 쪽 오른쪽 단)."""
    pieces: list[list[int]] = []
    column_numbers: list[list[int]] = []
    piece, column = 0, 0
    previous: int | None = None
    for segments in lines:
        numbers, columns_of_paragraph = [], []
        for index, segment in enumerate(segments):
            if previous is not None and segment.vertical_pos < previous and column + 1 < column_count:
                column += 1
            elif previous is not None and (
                segment.vertical_pos < previous
                or segment.vertical_pos == previous == 0
                and (index > 0 or paragraph_restarts)
                and not columns
                and segment.flags & LINE_FIRST_SEGMENT
            ):
                piece, column = piece + 1, 0
            numbers.append(piece)
            columns_of_paragraph.append(column)
            previous = segment.vertical_pos
        pieces.append(numbers)
        column_numbers.append(columns_of_paragraph)
    return pieces, column_numbers


def cell_pieces(cell: Cell) -> tuple[list[list[int]], list[int]]:
    """셀 줄의 쪽 조각(stored_pieces)."""
    column_def = cell_column_def(cell)
    return stored_pieces(cell.lines, column_def is not None, cell.paragraph_restarts, _column_count(column_def))


def cell_columns(cell: Cell) -> list[list[int]]:
    """셀 줄마다 놓일 단 번호(cell_pieces와 같은 되감기 규칙)."""
    column_def = cell_column_def(cell)
    return _pieces_and_columns(cell.lines, column_def is not None, cell.paragraph_restarts, _column_count(column_def))[
        1
    ]


def _column_count(column_def: ColumnDefCtrl | None) -> int:
    return column_def.count if column_def is not None else 1


def cell_column_def(cell: Cell) -> ColumnDefCtrl | None:
    return next(
        (
            child.model
            for paragraph in cell.paragraphs
            for child in paragraph.children
            if child.tag == tags.CTRL_HEADER and isinstance(child.model, ColumnDefCtrl) and child.model.count > 1
        ),
        None,
    )


#: 셀 높이 값이 이 이상이면 u32로 저장된 음수라 쓰지 않는다(rhwp table_layout.rs와 같은 기준).
INVALID_LENGTH = 0x80000000


def stored_height(cell: Cell) -> int:
    return cell.header.height if cell.header.height < INVALID_LENGTH else 0


def cell_margins(table: Table, cell: Cell) -> list[int]:
    """셀 안쪽 여백(왼, 오른, 위, 아래). 안 여백 지정 비트가 없으면 셀 값은 쓰지 않고 표 기본 여백을 쓴다."""
    # rhwp parser/control.rs: LIST_HEADER 6~7바이트 bit 0(우리 attr bit 16)이 셀 고유 여백 사용 여부다.
    if not cell.header.attr >> 16 & 1:
        return table.margins
    # 음수(i16 −1 = 65535)로 저장된 쪽은 0으로 본다(실측: issue1921 8쪽 표 행 높이 26.47pt, 그대로 쓰면 1321pt).
    return [0 if value >= 0x8000 else value for value in cell.header.margins]


def _cumulative(columns: list[list[float]]) -> list[float]:
    starts = [0.0]
    for k in range(max(len(c) for c in columns) - 1):
        starts.append(starts[-1] + max(c[k] if k < len(c) else 0 for c in columns))
    return starts


def piece_offsets(table: Table, cells: list[Cell]) -> dict[tuple[int, int], list[float]]:
    """쪽을 넘는 한 행 셀마다 저장 쪽 조각이 시작하는 셀 안 세로 위치(조각 k는 앞 조각 내용과 셀 위·아래 여백 뒤)."""
    by_row: dict[int, list[list[float]]] = {}
    split_cells = []
    for cell in cells:
        _, heights = cell_pieces(cell)
        if len(heights) > 1 and cell.header.row_span == 1:
            margins = cell_margins(table, cell)
            by_row.setdefault(cell.header.row, []).append([h + margins[2] + margins[3] for h in heights])
            split_cells.append(cell)
    # 한 행 셀끼리는 같은 쪽에서 나뉘므로 행마다 조각 위치를 맞춘다.
    row_starts = {row: _cumulative(columns) for row, columns in by_row.items()}
    return {(cell.header.row, cell.header.col): row_starts[cell.header.row] for cell in split_cells}


def span_piece_offsets(table: Table, cells: list[Cell]) -> dict[tuple[int, int], list[float]]:
    """쪽을 넘는 여러 행 셀마다 저장 쪽 조각이 시작하는 셀 안 세로 위치(task2287 29쪽: 행 2~3 셀 조각 5개)."""
    found = {}
    for cell in cells:
        _, heights = cell_pieces(cell)
        if len(heights) > 1 and cell.header.row_span > 1:
            margins = cell_margins(table, cell)
            found[cell.header.row, cell.header.col] = _cumulative([[h + margins[2] + margins[3] for h in heights]])
    return found


def cell_height(
    table: Table, cell: Cell, offsets: dict[tuple[int, int], list[float]], use_stored: bool = True
) -> float:
    """저장된 셀 높이는 최소 높이다. 내용이 더 높으면 행이 늘어난다(쪽을 넘는 셀은 조각을 이어 붙인 높이)."""
    margins = cell_margins(table, cell)
    _, heights = cell_pieces(cell)
    starts = offsets.get((cell.header.row, cell.header.col))
    # 쪽을 넘는 행의 저장 높이는 쪽마다 그린 상자 높이를 더한 값이라 쓰지 않는다
    # (실측: table_scattered_header_rowbreak 4쪽 첫 행은 남은 줄 높이 15.70pt, 저장 높이로 늘리면 16.0pt).
    split_row = cell.header.row_span == 1 and any(row == cell.header.row for row, _ in offsets)
    # 쪽을 넘는 여러 행 셀도 같다(task2287 행 2~3 셀: 저장 45725, 쪽 조각 내용 40000·1000. 버리면 이 문서 +6046자).
    split_span = cell.header.row_span > 1 and len(heights) > 1
    # 셀 간격이 있는 표는 쪽을 넘은 행도 저장 높이가 행 높이다(편람 s4 p47 표: 행 3·4·5 = 저장 13419·51996·25763,
    # lane-L 실측). 셀 간격이 갈림 기준인지는 이 표 하나로만 확인했다.
    ignored = (split_row and not getattr(table, "cell_spacing", 0)) or split_span or not use_stored
    minimum = 0 if ignored else stored_height(cell)
    if starts is None:
        # 여러 행 셀의 뒤 조각은 이어지는 쪽 조각 맨 위에 놓으므로 첫 조각만 높이에 반영한다.
        return max(minimum, max(heights[0], cell.floor) + margins[2] + margins[3])
    # 행 조각 시작은 행에서 가장 많이 나뉜 셀 기준이라, 조각이 적은 셀은 자기 마지막 조각의 시작을 쓴다
    # (실측: 편람 314~322쪽 행 12 셀 조각 2개·6개, 행 전체 끝을 쓰면 173260, 정답 나머지 한 줄로 154996).
    return max(minimum, max(starts[len(heights) - 1] + heights[-1], cell.floor) + margins[2] + margins[3])


def table_grid(ctrl: Node, cell_lines: Callable[[Table, Cell], list[list]] | None = None) -> TableGrid | None:
    """cell_lines는 셀 문단마다 줄 배치를 돌려준다(없으면 저장 LINE_SEG만 쓴다)."""
    table, cells = collect_cells(ctrl)
    if table is None or not cells:
        return None
    for cell in cells:
        cell.lines = cell_lines(table, cell) if cell_lines else [stored_lines(node) for node in cell.paragraphs]
    cols = max(table.cols, max(c.header.col + c.header.col_span for c in cells))
    rows = max(table.rows, max(c.header.row + c.header.row_span for c in cells))
    # 여러 칸 셀의 크기는 그 사이 셀 간격(칸 수 − 1개)을 포함하므로, 간격을 넣기 전 경계 제약에서는 뺀다
    # (편람 35쪽 셀 간격 표: 빼지 않으면 두 행 셀 행이 54.31pt, 정답 49.2pt).
    spacing = float(table.cell_spacing or 0)
    col_x = _solve_boundaries(
        cols, [(c.header.col, c.header.col_span, c.header.width - (c.header.col_span - 1) * spacing) for c in cells]
    )
    offsets = piece_offsets(table, cells)

    def span_height(cell: Cell, length: float) -> float:
        return length - (cell.header.row_span - 1) * spacing

    def multi_row_height(cell: Cell) -> float:
        _, heights = cell_pieces(cell)
        return cell_height(table, cell, offsets) if len(heights) > 1 else stored_height(cell)

    # 한 행짜리 셀은 내용 높이로 행을 늘리고, 여러 행 셀은 저장 높이로 경계를 맞춘다.
    row_y = _solve_boundaries(
        rows,
        [
            (
                c.header.row,
                c.header.row_span,
                span_height(c, multi_row_height(c)) if c.header.row_span > 1 else cell_height(table, c, offsets),
            )
            for c in cells
        ],
    )
    if any(later < earlier for earlier, later in zip(row_y, row_y[1:], strict=False)):
        # 저장 셀 높이가 서로 맞지 않아 경계가 거꾸로 풀리면 한 행 셀로 행을 정하고 여러 행 셀은 아래 부족분으로 늘린다
        # (task2287 s0 표 47행: 행 22~26 여러 행 셀 높이 합이 299 어긋나 행 23 경계가 행 22보다 위로 풀림).
        row_y = solve_rows(table, cells, rows, offsets)
    # 끝 행이 위인 셀부터 늘린다. 셀 둘이 겹친 행에서 함께 모자라면 한 번만 늘어난다
    # (table_scattered_header_rowbreak 41쪽: 행 39~70·55~69 셀이 9.23pt씩 모자라고 정답은 24.93pt 행 하나).
    for cell in sorted(cells, key=lambda c: (c.header.row + c.header.row_span, c.header.row_span)):
        if cell.header.row_span > 1:
            end = min(cell.header.row + cell.header.row_span, rows)
            shortfall = span_height(cell, cell_height(table, cell, offsets)) - (row_y[end] - row_y[cell.header.row])
            if shortfall > 0:
                row_y = row_y[:end] + [value + shortfall for value in row_y[end:]]
    row_y = _fit_stored_height(ctrl, table, cells, row_y, offsets)
    # 셀 간격은 표 가장자리와 셀 사이, 셀과 셀 사이에 모두 들어간다(편람 표 개체 높이 = 행 높이 합 + (행 수 + 1) × 255).
    if spacing:
        col_x = [x + index * spacing for index, x in enumerate(col_x)]
        row_y = [y + index * spacing for index, y in enumerate(row_y)]
    return TableGrid(table, cells, col_x, row_y, offsets, spacing, span_piece_offsets(table, cells))


def solve_rows(
    table: Table, cells: list[Cell], rows: int, offsets: dict[tuple[int, int], list[float]], required=None
) -> list[float]:
    """한 행 셀로 행 높이를 정하고, 여러 행 셀은 끝 행이 위인 것부터 모자란 만큼 끝 행을 늘린 행 경계(셀 간격 제외).

    required는 셀(id)마다 걸친 행 높이 합의 하한을 준다(여러 행 셀은 저장 높이 대신, 한 행 셀은 내용 높이와 함께).
    """
    heights = [0.0] * rows
    for cell in cells:
        if cell.header.row_span == 1:
            need = (required or {}).get(id(cell), 0.0)
            heights[cell.header.row] = max(heights[cell.header.row], cell_height(table, cell, offsets), need)
    spacing = float(table.cell_spacing or 0)
    # 저장 높이가 내용보다 작은 한 행 셀이 있는 행은 다른 버전 한글이 다르게 나눈 낡은 배치다. 그 행을 지나는 여러 행
    # 셀의 저장 높이도 낡아 내용으로만 맞춘다(실측: task2287 146쪽 표 행 8~17 셀 저장 47708, 행 16 셀 저장 400·1082,
    # 정답 행 17 = 내용 40.3pt).
    stale_rows = {
        cell.header.row
        for cell in cells
        if cell.header.row_span == 1 and 0 < stored_height(cell) < cell_height(table, cell, offsets) - 1
    }
    for cell in sorted(cells, key=lambda c: (c.header.row + c.header.row_span, c.header.row_span)):
        if cell.header.row_span > 1:
            end = min(cell.header.row + cell.header.row_span, rows)
            need = (required or {}).get(id(cell))
            stale = bool(stale_rows.intersection(range(cell.header.row, end)))
            if need is None:
                need = cell_height(table, cell, offsets, use_stored=not stale) - (cell.header.row_span - 1) * spacing
            heights[end - 1] += max(0.0, need - sum(heights[cell.header.row : end]))
    row_y = [0.0]
    for height in heights:
        row_y.append(row_y[-1] + height)
    return row_y


def _fit_stored_height(
    ctrl: Node, table: Table, cells: list[Cell], row_y: list[float], offsets: dict[tuple[int, int], list[float]]
) -> list[float]:
    """셀 저장 높이 합이 표 저장 높이보다 크면 마지막 행을 내용이 허락하는 데까지 줄여 표 높이를 저장값에 맞춘다.

    kr_156489597 정답 11쪽: 마지막 행 4860 = 표 26866 − 앞 행 합(셀 저장 5094, 내용 4702). pic-in-head-01 9쪽: 표 높이
    689.5pt = 저장 69017(셀 저장 69812). 본문에 바로 놓인 표(레코드 수준 1)이고 셀 간격이 없으며 쪽을 넘는 셀이 없을
    때만 쓴다(셀 안 표 issue4889, 쪽 나뉜 셀 issue1949는 다르게 저장된다 — lane-L 시험).
    """
    stored = getattr(ctrl.model, "height", 0) or 0
    # 저장 높이가 마지막 행 위보다 작으면 쪽을 넘는 표의 첫 쪽 조각 높이다(issue6132 s0 p91: 행 합 87513, 저장 58119).
    overshoots_last_row = len(row_y) >= 2 and row_y[-2] < stored < row_y[-1]
    if table.cell_spacing or ctrl.record.level != 1 or offsets or not overshoots_last_row:
        return row_y
    last = len(row_y) - 2
    need = 0.0
    for cell in cells:
        if cell.header.row + cell.header.row_span - 1 >= last:
            margins = cell_margins(table, cell)
            _, heights = cell_pieces(cell)
            content = max(heights[0], cell.floor) + margins[2] + margins[3]
            if cell.header.row_span > 1:
                # 여러 행 셀은 저장 높이를 지킨다(issue7336 s0 p57: 행 1~2 셀 64752 안에 중첩 표).
                content = max(content, stored_height(cell))
            need = max(need, content - (row_y[last] - row_y[cell.header.row]))
    height = max(need, stored - row_y[last])
    if height >= row_y[-1] - row_y[last]:
        return row_y
    return [*row_y[:-1], row_y[last] + height]


def header_row_count(grid: TableGrid) -> int:
    """표 맨 위부터 이어지는 제목 행 수. 중간에 흩어진 제목 셀은 반복하지 않는다(rhwp table.rs)."""
    rows = 0
    while True:
        cells = [c for c in grid.cells if c.header.row == rows]
        marked = any if grid.header_any else all
        if not cells or not marked(c.header.is_header for c in cells):
            return rows
        rows += 1


def draw_table_frame(
    grid: TableGrid,
    x: float,
    y: float,
    page: Page,
    styles: DocStyles,
    clip: tuple[float, float] | None = None,
) -> None:
    """셀 배경을 먼저 모두 칠한 뒤 테두리를 긋는다. clip은 표 세로 좌표 [시작, 끝) 구간, y는 그 시작이 놓일 위치다."""
    start, end = clip if clip is not None else (0.0, grid.height)
    boxes = []
    for cell in grid.cells:
        cx, cy, width, height = grid.cell_box(cell)
        top_s, bottom_s = max(cy, start), min(cy + height, end)
        if bottom_s > top_s:
            boxes.append((cell, x + cx, y + top_s - start, width, bottom_s - top_s))
    draw_cell_boxes(boxes, page, styles)


def draw_cell_boxes(boxes: list[tuple[Cell, float, float, float, float]], page: Page, styles: DocStyles) -> None:
    """(셀, 왼쪽, 위, 폭, 높이) 상자마다 배경을 먼저 모두 칠한 뒤 테두리를 긋는다."""
    lines = []
    for cell, left, top, width, height in boxes:
        fill = styles.border_fill(cell.header.border_fill_id)
        if fill is None:
            continue
        right, bottom = left + width, top + height
        color = colorref(fill.background_color)
        if color:
            page.items.append(Rect(left, top, width, bottom - top, fill=color))
        lines += border_lines(fill.left, left, top, left, bottom)
        lines += border_lines(fill.right, right, top, right, bottom)
        lines += border_lines(fill.top, left, top, right, top)
        lines += border_lines(fill.bottom, left, bottom, right, bottom)
    page.items.extend(lines)
