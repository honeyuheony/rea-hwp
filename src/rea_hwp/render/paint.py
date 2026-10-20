"""한글 535 PDF 저장의 쪽 그리기 차례와 그리기 판정을 장면 항목 차례에 옮긴다.

근거: HwpApp FUN_10188740(쪽 층 차례), FUN_1018c200·FUN_101b08b0(떠 있는 개체 층·z_order), FUN_1030cbb0(판정).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .scene import Page

#: 글 목록 열쇠: 바탕쪽과 머리말·꼬리말. 쪽 차례는 바탕쪽 → 글 뒤로 개체 → 머리말·꼬리말·쪽 번호 → 본문(FUN_10188740).
MASTER_LIST = "master"
BAND_LIST = "band"
#: 쪽 테두리·배경. 바탕쪽보다 먼저(맨 아래에) 그린다.
PAGE_LIST = "page"


@dataclass(eq=False)
class PaintUnit:
    """page.items·text_lines에 연달아 넣은 그리기 단위(줄, 개체) 하나. 끝 위치는 단위를 닫을 때 정한다."""

    page: Page
    item_start: int
    line_start: int
    visible: bool
    parent: PaintUnit | None
    #: 떠 있는 개체의 층(글 뒤로 −1, 글 앞으로 +1, 그 밖 0). 줄과 글자처럼 취급한 개체는 None.
    layer: int | None
    z_order: int
    #: 이 단위를 놓은 글 목록(쪽 본문 None, 셀·글상자·바탕쪽·머리말). 떠 있는 개체는 그 목록의 글 다음에 그린다.
    list_key: object
    is_line: bool
    sequence: int
    item_end: int = -1
    line_end: int = -1
    children: list[PaintUnit] = field(default_factory=list)


def arrange_pages(units: list[PaintUnit]) -> None:
    """판정에 걸린 단위를 지우고, 떠 있는 개체를 자기 글 목록의 글 뒤로 (층, z_order, 놓은 차례) 순서로 옮긴다."""
    by_page: dict[int, list[PaintUnit]] = {}
    for unit in units:
        by_page.setdefault(id(unit.page), []).append(unit)
        if unit.parent is not None:
            unit.parent.children.append(unit)
    for page_units in by_page.values():
        page = page_units[0].page
        roots = [unit for unit in page_units if unit.parent is None]
        page.items[:] = _arrange(roots, page.items, False)
        page.text_lines[:] = _arrange(roots, page.text_lines, True)


def _span(unit: PaintUnit, lines: bool) -> tuple[int, int]:
    return (unit.line_start, unit.line_end) if lines else (unit.item_start, unit.item_end)


def _pieces(children: list[PaintUnit], start: int, end: int, source: list, lines: bool):
    """[start, end) 구간을 (안 묶인 항목 목록 | 자식 단위) 차례로 나눈다."""
    position = start
    for child in sorted(children, key=lambda unit: _span(unit, lines)[0]):
        child_start, child_end = _span(child, lines)
        if child_start > position:
            yield source[position:child_start]
        yield child
        position = max(position, child_end)
    if position < end:
        yield source[position:end]


def _arrange(roots: list[PaintUnit], source: list, lines: bool) -> list:
    groups = _groups(roots, 0, len(source), source, lines)
    backdrop = [group for group in groups if group.key == PAGE_LIST]
    master = [group for group in groups if group.key == MASTER_LIST]
    bands = [group for group in groups if group.key == BAND_LIST]
    body = [group for group in groups if group.key not in (PAGE_LIST, MASTER_LIST, BAND_LIST)]
    floats = sorted(entry for group in body for entry in group.floats)
    arranged = [item for group in backdrop for item in group.compose()]
    arranged += [item for group in master for item in group.compose()]
    arranged += [item for layer, *_, content in floats if layer < 0 for item in content]
    arranged += [item for group in bands for item in group.compose()]
    arranged += [item for group in body for item in group.body]
    arranged += [item for layer, *_, content in floats if layer >= 0 for item in content]
    return arranged


@dataclass
class _ListGroup:
    """글 목록 하나의 글과, 그 목록에 매인 떠 있는 개체 (층, z_order, 차례, 항목)."""

    key: object
    body: list = field(default_factory=list)
    floats: list = field(default_factory=list)

    def compose(self) -> list:
        ordered = sorted(self.floats)
        behind = [item for layer, *_, content in ordered if layer < 0 for item in content]
        front = [item for layer, *_, content in ordered if layer >= 0 for item in content]
        return behind + self.body + front


def _groups(children: list[PaintUnit], start: int, end: int, source: list, lines: bool) -> list[_ListGroup]:
    groups: list[_ListGroup] = []
    for piece in _pieces(children, start, end, source, lines):
        if isinstance(piece, PaintUnit):
            body, floats = _emit(piece, source, lines)
            after_backdrop = bool(groups) and groups[-1].key == PAGE_LIST and piece.list_key != PAGE_LIST
            if piece.layer is None and (not groups or groups[-1].key != piece.list_key):
                groups.append(_ListGroup(piece.list_key))
            elif not groups or after_backdrop:
                groups.append(_ListGroup(piece.list_key))
            groups[-1].body += body
            groups[-1].floats += floats
        else:
            # 쪽 테두리·배경 묶음 뒤 항목·떠 있는 개체는 그 묶음에 넣지 않는다(그 묶음은 맨 아래에 따로 그린다).
            if not groups or groups[-1].key == PAGE_LIST:
                groups.append(_ListGroup(None))
            groups[-1].body += piece
    return groups


def _emit(unit: PaintUnit, source: list, lines: bool) -> tuple[list, list]:
    """(그 자리에 그릴 항목, 위 글 목록으로 넘길 떠 있는 개체)."""
    start, end = _span(unit, lines)
    if unit.layer is not None:
        if not unit.visible:
            return [], []
        content = [item for group in _groups(unit.children, start, end, source, lines) for item in group.compose()]
        return [], [(unit.layer, unit.z_order, unit.sequence, content)]
    if not unit.is_line:
        # 글자처럼 취급한 개체는 줄 안에서 그리고, 그 안(셀·글상자) 목록의 떠 있는 개체는 그 안에서 그린다.
        if not unit.visible:
            return [], []
        return [item for group in _groups(unit.children, start, end, source, lines) for item in group.compose()], []
    drawn: list = []
    floats: list = []
    for piece in _pieces(unit.children, start, end, source, lines):
        if isinstance(piece, PaintUnit):
            body, passed = _emit(piece, source, lines)
            drawn += body
            floats += passed
        else:
            drawn += piece
    # 줄이 판정에 걸려도 그 줄에 매인 떠 있는 개체는 층으로 따로 그린다(FUN_101891a0).
    return (drawn if unit.visible else []), floats
