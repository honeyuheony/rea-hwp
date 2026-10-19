"""각주(쪽 아래)와 미주(본문 끝)를 배치한다."""

from __future__ import annotations

from .. import tags
from ..document import Node
from ..models.controls import FootnoteShape, ObjectCtrlHeader, TextWrap, VertRelTo
from ..models.docinfo import BorderLine
from .borders import border_lines
from .common import Area, PageState, ParagraphView, _list_paragraphs

FOOTNOTE_ID = tags.FOOTNOTE_CTRL.to_bytes(4, "little")


class NotesMixin:
    def flow_endnotes(self, shape) -> None:
        """미주를 본문 끝 위치에서 이어 쌓는다. 단·쪽 바닥을 넘는 줄은 다음 단·쪽 맨 위로 넘긴다."""
        if not self.endnotes or self.cursor is None:
            self.endnotes = []
            return
        state, column, y = self.cursor
        geometry = state.geometry
        if state.footnotes:
            # 각주가 있는 쪽에서는 미주를 잇지 않고 새 쪽 맨 위에서 구분선 없이 시작한다
            # (pyhwp footnote-endnote·hwplib 각주미주 2쪽 PDF). 검증 안 됨: 근거는 이 두 문서뿐이다.
            state = self.new_page(geometry)
            column, y = 0, geometry.body_top
        elif shape is not None:
            y += shape.divider_margin_top
            column_x, column_width = geometry.columns[column]
            length = divider_length(shape.divider_length, column_width)
            if shape.divider_line_type:
                state.page.items.extend(
                    border_lines(
                        BorderLine(shape.divider_line_type, shape.divider_line_width, shape.divider_line_color),
                        state.geometry.body_left + column_x,
                        y,
                        state.geometry.body_left + column_x + length,
                        y,
                    )
                )
            y += shape.divider_margin_bottom
        spacing = shape.note_spacing if shape is not None else 0
        bottom_limit = geometry.body_top + geometry.body_height
        for note in self.endnotes:
            self.current_note_number = note.model.number
            top = y
            previous = None
            floor: float | None = None
            for view in self.paragraph_views(_list_paragraphs(note), geometry.columns[column][1]):
                extent = pushing_extent(view)
                for index, segment in enumerate(view.segments):
                    # 한글 2022는 저장된 단 나눔(세로 위치 되감기)을 따르지 않고 줄을 이어 쌓은 뒤
                    # 단 바닥을 넘는 줄만 넘긴다(3-11월_실전_통합_2022 11쪽, 3-09월_교육_통합_2022 18쪽).
                    if previous is not None:
                        if segment.vertical_pos > previous.vertical_pos:
                            top += segment.vertical_pos - previous.vertical_pos
                        else:
                            # 한글은 다음 줄을 글자 높이로 내린다(재저장본 줄 높이 ≠ 글자 높이인 줄 447개 모두).
                            # 실측: 3-11월_실전_통합_2022 11쪽 「ⅳ) 일 때」 높이 1662·글자 900, 다음 줄이 11쪽 끝.
                            top += previous.text_height + previous.line_spacing
                    if floor is not None:
                        top = max(top, floor)
                        floor = None
                    # 넘침 판단은 줄 높이가 아니라 글자 높이로 한다.
                    # 뒤 개체 때문에 줄 높이만 큰 줄은 남는다(3-09월_교육_통합_2022 22쪽).
                    need = max(segment.text_height, extent) if index == 0 else segment.text_height
                    if top + need > bottom_limit and top > geometry.body_top:
                        if column + 1 < len(geometry.columns):
                            column += 1
                        else:
                            state = self.new_page(geometry)
                            column = 0
                        top = geometry.body_top
                    previous = segment
                    column_x, column_width = geometry.columns[column]
                    state.column = column
                    area = Area(state.geometry.body_left + column_x, top - segment.vertical_pos, column_width)
                    self.place_line(state, view, index, area)
                    if index == 0 and extent:
                        floor = top + extent
                    # 다음 미주는 마지막 줄 간격을 빼고 미주 사이 간격만큼 띄운다(한글 2022 PDF 실측).
                    y = top + segment.line_height
            y += spacing
        self.endnotes = []

    def draw_footnotes(self, state: PageState) -> None:
        """쪽 각주를 본문 바닥에 붙여 쌓고, 그 위에 구분선을 긋는다."""
        geometry = state.geometry
        shape = self.footnote_shape_of(state.footnotes[0].ctrl)
        pieces = [self.footnote_pieces(note.ctrl, geometry.body_width)[note.piece] for note in state.footnotes]
        heights = [piece_height(lines) for lines in pieces]
        spacing = shape.note_spacing if shape is not None else 0
        total = sum(heights) + spacing * max(0, len(heights) - 1)
        y = geometry.body_top + geometry.body_height - total
        if shape is not None and shape.divider_line_type:
            line_y = y - shape.divider_margin_bottom
            length = divider_length(shape.divider_length, geometry.body_width)
            line = BorderLine(shape.divider_line_type, shape.divider_line_width, shape.divider_line_color)
            state.page.items.extend(border_lines(line, geometry.body_left, line_y, geometry.body_left + length, line_y))
        for note, lines, height in zip(state.footnotes, pieces, heights, strict=True):
            self.current_note_number = note.ctrl.model.number
            area = Area(geometry.body_left, y, geometry.body_width)
            for view, index in lines:
                self.place_line(state, view, index, area)
            y += height + spacing

    def footnote_pieces(self, ctrl: Node, width: float) -> list[list[tuple[ParagraphView, int]]]:
        """각주 줄을 쪽 조각으로 나눈다. 저장 줄 위치가 앞 줄보다 크지 않은 줄에서 새 조각이 시작한다.

        주석 안 문단들의 저장 세로 위치는 주석 첫 줄부터 이어서 센다(정책연구 66쪽 각주 77: 0, 1172 | 0, 1172).
        """
        pieces: list[list[tuple[ParagraphView, int]]] = [[]]
        previous: int | None = None
        for view in self.paragraph_views(_list_paragraphs(ctrl), width):
            for index, segment in enumerate(view.segments):
                if previous is not None and segment.vertical_pos <= previous:
                    pieces.append([])
                pieces[-1].append((view, index))
                previous = segment.vertical_pos
        return pieces

    def footnote_split_pending(self, state: PageState) -> bool:
        """쪽 마지막 각주가 다음 쪽으로 이어지는 조각인가. 그러면 그 쪽의 각주 자리는 찼다."""
        if not state.footnotes:
            return False
        last = state.footnotes[-1]
        return last.piece + 1 < len(self.footnote_pieces(last.ctrl, state.geometry.body_width))

    def footnote_shape_of(self, ctrl: Node) -> FootnoteShape | None:
        """각주 컨트롤이 속한 구역의 각주 모양(구역 정의 아래 첫 FOOTNOTE_SHAPE)."""
        if not hasattr(self, "_footnote_shapes"):
            self._footnote_shapes: dict[int, FootnoteShape | None] = {}
            for section in self.document.sections:
                shape = next((n.model for n in section.walk() if n.tag == tags.FOOTNOTE_SHAPE), None)
                for node in section.walk():
                    if node.tag == tags.CTRL_HEADER and node.record.payload[:4] == FOOTNOTE_ID:
                        self._footnote_shapes[id(node)] = shape
        return self._footnote_shapes.get(id(ctrl))


def divider_length(raw: int, width: float) -> float:
    """구분선 길이. 저장 값은 하위 2바이트(HWPUNIT16)이고 음수는 정해진 길이다.

    -1 5cm, -2 2cm, -3 단 폭의 1/3, -4 단 폭(rhwp layout.rs note_separator_length_px).
    """
    value = (raw & 0xFFFF) - ((raw & 0x8000) << 1)
    lengths = {0: 0, -1: 14173, -2: 5669, -3: width / 3, -4: width}
    return min(width, max(0, lengths.get(value, value if value > 0 else width / 3)))


def pushing_extent(view: ParagraphView) -> int:
    """문단 위끝 기준으로, 뒤 줄을 아래로 밀어내는 위아래 배치 개체의 아래끝(없으면 0).

    한글 2022는 저장된 줄 위치가 개체를 빠뜨렸어도 다음 줄을 개체 아래에 놓는다
    (3-11월_실전_통합_2024-구분선위20미주사이0구분선아래20 미주 12: 다음 줄 = 문단 위 + 세로 오프셋 + 높이).
    """
    extent = 0
    for ctrl in view.controls.values():
        model = ctrl.model
        if (
            isinstance(model, ObjectCtrlHeader)
            and not model.treat_as_char
            and model.text_wrap == TextWrap.TOP_AND_BOTTOM
            and model.vert_rel_to == VertRelTo.PARA
        ):
            margins = model.outer_margins or [0, 0, 0, 0]
            extent = max(extent, model.vert_offset + model.height + margins[3])
    return extent


def piece_height(lines: list[tuple[ParagraphView, int]]) -> int:
    """각주 조각이 차지하는 높이(조각 첫 줄 위치 0 기준, 마지막 줄 간격 제외)."""
    return max((view.segments[i].vertical_pos + view.segments[i].line_height for view, i in lines), default=0)
