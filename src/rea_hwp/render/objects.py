"""표·그림·글상자 같은 개체의 크기와 위치를 정하고 그린다."""

from __future__ import annotations

import base64
from dataclasses import dataclass

from .. import tags
from ..document import Node
from ..models.controls import (
    AutoNumberCtrl,
    CaptionListHeader,
    ColumnDefCtrl,
    ObjectCtrlHeader,
    TextBoxListHeader,
    TextWrap,
)
from ..models.shapes import ShapeComponent, ShapePicture
from .common import (
    ALIGN_CENTER,
    ALIGN_END,
    REL_COLUMN_OR_PARA,
    REL_PAGE,
    REL_PAPER,
    REL_PARA,
    TAC_ASCENT,
    Area,
    PageState,
    ParagraphView,
    TextChar,
    column_layout,
)
from .forms import draw_form
from .scene import Image
from .table_flow import is_block_table

#: 연결 글상자를 찾을 때 넘친 줄 폭과 빈 글상자 안쪽 폭의 허용 차이(HU). 검증 안 됨: BookReview는 0~2HU 차이.
LINK_WIDTH_TOLERANCE = 100
#: 캡션 방향(명세 표72).
CAPTION_LEFT, CAPTION_RIGHT, CAPTION_TOP, CAPTION_BOTTOM = range(4)


@dataclass
class Caption:
    header: CaptionListHeader
    views: list[ParagraphView]
    text_width: float
    #: 캡션 문단 높이.
    content: float
    #: 개체 상자에 더하는 길이(위·아래는 높이 + 간격, 왼·오른은 캡션 폭 + 간격).
    extent: float
    #: 왼·오른 캡션이면 True.
    beside: bool
    #: 개체 바깥 왼 여백.
    margin_left: int

    @property
    def direction(self) -> int:
        return self.header.direction


class ObjectsMixin:
    def object_size(self, model: ObjectCtrlHeader, state: PageState, area: Area) -> tuple[float, float]:
        geometry = state.geometry
        width, height = float(model.width), float(model.height)
        reference_width = {0: geometry.paper_width, 1: geometry.body_width, 2: area.width, 3: area.width}
        if model.width_rel_to in reference_width:
            width = reference_width[model.width_rel_to] * model.width / 10000
        if model.height_rel_to == 0:
            height = geometry.paper_height * model.height / 10000
        elif model.height_rel_to == 1:
            height = geometry.body_height * model.height / 10000
        return width, height

    def place_object(
        self, state: PageState, view: ParagraphView, ctrl: Node, x: float, baseline: float, area: Area
    ) -> None:
        model: ObjectCtrlHeader = ctrl.model
        width, height = self.object_size(model, state, area)
        margins = model.outer_margins or [0, 0, 0, 0]
        caption = self.object_caption(ctrl, width, margins[0])
        # 캡션은 개체 상자에 들어간다: 위·아래 캡션은 높이를, 왼·오른 캡션은 폭을 늘린다(실측: pyhwp table-caption).
        box_width = width + (caption.extent if caption and caption.beside else 0)
        box_height = height + (caption.extent if caption and not caption.beside else 0)
        if model.treat_as_char:
            # 바깥 여백을 포함한 개체 상자의 85%를 기준선 위에 둔다(rhwp TAC_OBJECT_ASCENT_RATIO, 저장 줄과 일치).
            left = x + margins[0]
            top = baseline - TAC_ASCENT * (box_height + margins[2] + margins[3]) + margins[2]
        else:
            left, top = self.floating_position(model, state, view, area, box_width, box_height)
            if is_block_table(ctrl) and model.vert_offset < 0 and model.vert_rel_to == REL_COLUMN_OR_PARA:
                # 문단 기준 자리차지 표는 문단 위로 올라가지 않는다(실측: 셀 안 issue4889 1쪽 55행 표 오프셋 −975,
                # 위 선 289.12; 본문 kr_156369118 5쪽 s0 p63 표 오프셋 −622, 정답 기준선 695.2pt).
                top -= model.vert_offset
            if self.in_body and model.text_wrap == TextWrap.TOP_AND_BOTTOM:
                state, top = self.stacked_top(state, left, box_width, top, box_height + margins[2] + margins[3])
            if ctrl.record.payload[:4] != tags.TABLE_CTRL.to_bytes(4, "little"):
                # 바깥 여백 포함 상자를 기준에 맞춰 놓고 개체는 왼·위 여백만큼 안쪽에 그린다(rhwp picture_footnote.rs).
                # 표는 table_flow.draw_table이 같은 보정을 한다.
                left += margins[0]
                top += margins[2]
        if caption and caption.direction == CAPTION_LEFT:
            left += caption.extent
        elif caption and caption.direction == CAPTION_TOP:
            top += caption.extent
        first, last, bottom = state, state, top + height
        if _out_of_scope(ctrl):
            state.page.excluded.append((left, top, width, height))
        elif ctrl.record.payload[:4] == tags.TABLE_CTRL.to_bytes(4, "little"):
            placed = self.draw_table(state, ctrl, left, top)
            if placed is not None and placed.last_state is not state and self.in_body and _is_top_and_bottom(model):
                self.continue_stacked_box(state, placed.last_state, placed.bottom + margins[3])
            if placed is not None:
                first, left, top, last, bottom = (
                    placed.first_state,
                    placed.left,
                    placed.top,
                    placed.last_state,
                    placed.bottom,
                )
        else:
            component = ctrl.find(tags.SHAPE_COMPONENT)
            if component is not None:
                self.draw_component(state, component, left, top, width, height)
            form = ctrl.find(tags.FORM_OBJECT)
            if form is not None:
                draw_form(self, state, form, left, top, width, height)
        if caption:
            owner = last if caption.direction == CAPTION_BOTTOM else first
            self.place_caption(owner, caption, left, top, bottom, width)

    def object_caption(self, ctrl: Node, width: float, margin_left: int) -> Caption | None:
        children = ctrl.children
        found = next(
            (i for i, c in enumerate(children) if c.tag == tags.LIST_HEADER and isinstance(c.model, CaptionListHeader)),
            None,
        )
        if found is None:
            return None
        header: CaptionListHeader = children[found].model
        paragraphs = []
        for child in children[found + 1 :]:
            if child.tag != tags.PARA_HEADER:
                break
            paragraphs.append(child)
        if not paragraphs:
            return None
        beside = header.direction in (CAPTION_LEFT, CAPTION_RIGHT)
        text_width = header.caption_width if beside else header.max_width or width
        views = self.paragraph_views(paragraphs, text_width)
        content = max((view.content_height for view in views), default=0)
        extent = (header.caption_width if beside else content) + header.caption_gap
        return Caption(header, views, text_width, content, extent, beside, margin_left)

    def place_caption(
        self, state: PageState, caption: Caption, left: float, top: float, bottom: float, width: float
    ) -> None:
        """그려진 개체 상자(left, top, bottom, width) 옆에 캡션 문단을 놓는다."""
        header = caption.header
        # 여백까지 넓힌 캡션은 바깥 왼 여백에서 시작한다.
        x = left - (caption.margin_left if header.include_margin else 0)
        if header.direction == CAPTION_TOP:
            y = top - caption.extent
        elif header.direction == CAPTION_BOTTOM:
            y = bottom + header.caption_gap
        else:
            x = left - caption.extent if header.direction == CAPTION_LEFT else left + width + header.caption_gap
            # 왼·오른 캡션은 문단 목록 세로 정렬로 개체 높이 안에 맞춘다(실측: table-caption 표 4·6).
            slack = max(0.0, bottom - top - caption.content)
            y = top + {1: slack / 2, 2: slack}.get(header.vert_align, 0.0)
        area = Area(x, y, caption.text_width)
        saved = self.in_body, self.current_note_number
        self.in_body = False
        try:
            for view in caption.views:
                numbers = [c.model.number for c in view.controls.values() if isinstance(c.model, AutoNumberCtrl)]
                if numbers:
                    # 캡션 번호(표·그림 번호)는 컨트롤에 저장된 번호를 쓴다.
                    self.current_note_number = numbers[0]
                for index in range(len(view.segments)):
                    self.place_line(state, view, index, area)
        finally:
            self.in_body, self.current_note_number = saved

    def floating_position(
        self, model: ObjectCtrlHeader, state: PageState, view: ParagraphView, area: Area, width: float, height: float
    ) -> tuple[float, float]:
        geometry = state.geometry
        column_x, column_width = geometry.columns[state.column]
        horizontal = {
            REL_PAPER: (0.0, geometry.paper_width),
            REL_PAGE: (geometry.body_left, geometry.body_width),
            # 셀 안에서는 단 기준이 셀 글 영역이다(실측: issue4889 셀 안 55행 표 왼쪽 선 63.69pt).
            REL_COLUMN_OR_PARA: (geometry.body_left + column_x, column_width) if self.in_body else (area.x, area.width),
            # 문단 기준은 문단 왼·오른 여백 안쪽이다(hwpctl_API 67쪽 코드 예시 표: 왼 여백 4500, PDF x +45pt).
            REL_PARA: (
                area.x + view.para_shape.left_margin // 2,
                area.width - (view.para_shape.left_margin + view.para_shape.right_margin) // 2,
            ),
        }
        ref_x, ref_w = horizontal.get(model.horz_rel_to, horizontal[REL_COLUMN_OR_PARA])
        para_top = area.y + (view.segments[0].vertical_pos if view.segments else 0)
        if self.in_body and model.vert_rel_to == REL_COLUMN_OR_PARA and _pushes_first_line(view):
            para_top = self.flow_position(state, para_top)
        elif (
            self.in_body
            and model.vert_rel_to == REL_COLUMN_OR_PARA
            and model.text_wrap == TextWrap.TOP_AND_BOTTOM
            and abs(para_top - self.flow_position(state, para_top) - _pushed_extent(model, height)) < 1
        ):
            # 개체 하나뿐이어도 저장된 첫 줄이 앞 줄 끝에서 꼭 개체 상자(오프셋·높이·위아래 바깥 여백)만큼 아래면
            # 그 개체에 밀린 줄이다(issue1921 p94: 앞 문단 끝 2080 + 28 + 45271 + 282 = 첫 줄 47661,
            # 정답 표는 앞 문단 바로 아래). 차이가 그와 다르면 앞 개체에 밀린 줄이라 저장 줄을 기준으로 한다
            # (kr_156489726 s1 p60: 앞 표 아래 줄 47073, 정답 표는 그 줄 + 오프셋 1614).
            para_top = self.flow_position(state, para_top)
        elif self.in_body and model.vert_rel_to == REL_COLUMN_OR_PARA:
            # 문단 기준 위치는 문단 위 간격 앞이다(편람 47쪽 s2 p214: 첫 줄 1900 = 앞 줄 끝 1200 + 위 간격 1400/2,
            # 정답 표 위 선 132.11pt = 1200 자리).
            para_top -= view.para_shape.space_before // 2
        vertical = {
            REL_PAPER: (0.0, geometry.paper_height),
            REL_PAGE: (geometry.body_top, geometry.body_height),
            REL_COLUMN_OR_PARA: (para_top, geometry.body_height),
        }
        ref_y, ref_h = vertical.get(model.vert_rel_to, vertical[REL_COLUMN_OR_PARA])
        return (
            _align(ref_x, ref_w, width, model.horz_align, model.horz_offset),
            _align(ref_y, ref_h, height, model.vert_align, model.vert_offset),
        )

    def flow_position(self, state: PageState, para_top: float) -> float:
        """첫 줄이 개체 아래로 밀린 문단의 원래 위치: 같은 쪽·단에서 앞 줄이 끝난 곳."""
        if self.flow_cursor is None:
            # 문서 첫 문단은 앞 줄이 없어 쪽(단 영역) 맨 위에서 시작한다(pic-in-table-with-toggle 표 위 선 102.01pt).
            return min(para_top, state.geometry.body_top + state.zone_top)
        cursor_state, column, bottom = self.flow_cursor
        if cursor_state is not state:
            # 쪽 첫 문단이면 쪽(단 영역) 맨 위에서 시작한다(issue6946 s2 p0: 구역 첫 문단의 표 두 개 뒤 줄 65959).
            return min(para_top, state.geometry.body_top + state.zone_top)
        return bottom if column == state.column and bottom < para_top else para_top

    def stacked_top(
        self, state: PageState, left: float, width: float, top: float, height: float
    ) -> tuple[PageState, float]:
        """자리차지 개체끼리는 겹치지 않는다. 앞서 놓인 상자와 겹치면 그 아래로 민다. 돌려주는 값은 (쪽, 위 끝).

        실측: pyhwp table-caption 표 2·7, 중간진도보고서 44쪽 표 20(저장 세로 오프셋 −17997이어도 표 19 아래).
        앞 상자가 쪽을 넘긴 표면 그 표가 끝난 쪽의 표 아래로 민다(issue5699 s0 p115: 표 둘째(오프셋 784)가 10쪽에서
        11쪽으로 넘어간 표 첫째 아래, 정답 11쪽 「□ 비용:」 128.5pt).
        """
        registry = self.__dict__.setdefault("_stacked_boxes", {})
        boxes = registry.setdefault((id(state.page), state.column), [])
        moved = True
        while moved:
            moved = False
            for box_left, box_right, box_top, box_bottom, continued in boxes:
                if left < box_right and box_left < left + width and top < box_bottom and box_top < top + height:
                    moved = True
                    if continued is None:
                        top = box_bottom
                        continue
                    state, top = continued
                    geometry = state.geometry
                    if top + height > geometry.body_top + geometry.body_height:
                        # 표 나머지 아래에 다 들어가지 않으면 그 다음 쪽 맨 위에서 시작한다(pr-1674 s0 p27 표2 696pt:
                        # 3쪽 표1 나머지(335pt) 아래에 들어가지 않아 정답 4쪽 위 선 70.84pt).
                        following = self.states.index(state) + 1
                        if following < len(self.states):
                            state = self.states[following]
                        else:
                            state = self.new_page(state.base_geometry)
                        top = state.geometry.body_top
                    boxes = registry.setdefault((id(state.page), state.column), [])
                    break
        boxes.append((left, left + width, top, top + height, None))
        return state, top

    def continue_stacked_box(self, first: PageState, last: PageState, bottom: float) -> None:
        """방금 쌓은 상자의 표가 쪽을 넘겼다. 첫 쪽에서는 쪽 끝까지, 끝난 쪽에서는 쪽 위부터 bottom까지 차지한다."""
        registry = self.__dict__["_stacked_boxes"]
        boxes = registry[id(first.page), first.column]
        box_left, box_right, box_top, _, _ = boxes[-1]
        boxes[-1] = (box_left, box_right, box_top, float("inf"), (last, bottom))
        registry.setdefault((id(last.page), last.column), []).append((box_left, box_right, float("-inf"), bottom, None))

    def draw_component(
        self, state: PageState, component: Node, left: float, top: float, width: float, height: float
    ) -> None:
        picture = component.find(tags.SHAPE_COMPONENT_PICTURE)
        if picture is not None and isinstance(picture.model, ShapePicture):
            self.draw_picture(state, picture.model, left, top, width, height)
        text_list = component.find(tags.LIST_HEADER)
        if text_list is not None and isinstance(text_list.model, TextBoxListHeader):
            header = text_list.model
            margins = header.margins
            paragraphs = [child for child in component.children if child.tag == tags.PARA_HEADER]
            inner_width = width - margins[0] - margins[1]
            views = self.paragraph_views(paragraphs, inner_width)
            area = Area(left + margins[0], top + margins[2], inner_width)
            self.place_linked_text(state, views, area, (height - margins[2] - margins[3], header.vert_align))
        for child in component.children:
            if child.tag == tags.SHAPE_COMPONENT and isinstance(child.model, ShapeComponent):
                sub = child.model
                box = _rendered_box(sub)
                if box is None:
                    box = (sub.offset_x, sub.offset_y, sub.current_width, sub.current_height)
                self.draw_component(state, child, left + box[0], top + box[1], box[2], box[3])

    def place_linked_text(
        self, state: PageState, views: list[ParagraphView], area: Area, alignment: tuple[float, int]
    ) -> None:
        """글상자 문단을 놓는다. 되돌아간 뒤 줄은 연결된 다음 글상자로 넘어간다. alignment는 (안쪽 높이, 세로 정렬).

        연결 대상은 같은 쪽의 빈 글상자 가운데 안쪽 폭이 그 줄 폭과 같은 것이다(basic__BookReview 2쪽: 넘친 줄 폭
        13832·19132가 빈 글상자 폭과 같고, PDF에서 그 상자에 이어진다). 빈 글상자를 나중에 그리면 그때 놓는다.
        """
        empty, pending = self.__dict__.setdefault("_linked_boxes", {}).setdefault(id(state.page), ([], []))
        has_ink = any(isinstance(item, TextChar) and item.char.strip() for view in views for item in view.chars)
        if not has_ink and not any(view.controls for view in views):
            empty.append(area)
            for item in [item for item in pending if abs(item[2] - area.width) <= LINK_WIDTH_TOLERANCE]:
                pending.remove(item)
                self._place_lines(state, item[0], area)
            return
        # 글상자 안 다단은 되감길 때마다 다음 단으로 간다(multicolumns-layout 4쪽). 마지막 단 뒤는 연결 글상자로.
        column_def = next(
            (c.model for view in views for c in view.node.children if isinstance(c.model, ColumnDefCtrl)), None
        )
        columns = column_layout(column_def, round(area.width))
        placed: list[tuple[ParagraphView, int, Area]] = []
        rest: list[tuple[ParagraphView, int]] = []
        previous, column = None, 0
        for view in views:
            for index, segment in enumerate(view.segments):
                if not rest and previous is not None and segment.vertical_pos < previous:
                    if column + 1 < len(columns):
                        column += 1
                    else:
                        rest.append((view, index))
                        previous = segment.vertical_pos
                        continue
                if rest:
                    rest.append((view, index))
                else:
                    x, width = columns[column]
                    placed.append((view, index, Area(area.x + x, area.y, width)))
                previous = segment.vertical_pos
        if not rest:
            # 넘치지 않은 글상자 문단은 세로 정렬대로 놓는다(kr_156489577 2쪽 「목차」: 46pt 상자 가운데 24pt 글자,
            # 기준선 108.12).
            inner_height, vert_align = alignment
            content = max(
                (view.segments[index].vertical_pos + view.segments[index].line_height for view, index, _ in placed),
                default=0,
            )
            slack = max(0.0, inner_height - content)
            shift = {1: slack / 2, 2: slack}.get(vert_align, 0.0)
            placed = [(view, index, Area(box.x, box.y + shift, box.width)) for view, index, box in placed]
        for view, index, box in placed:
            self.place_line(state, view, index, box)
        if not rest:
            return
        # 줄 폭은 문단 왼·오른 여백(2배 저장)을 뺀 값이다.
        first_view, first_index = rest[0]
        shape = first_view.para_shape
        width = first_view.segments[first_index].segment_width + (shape.left_margin + shape.right_margin) // 2
        target = next((box for box in empty if abs(box.width - width) <= LINK_WIDTH_TOLERANCE), None)
        if target is None:
            pending.append((rest, (state, area), width))
        else:
            self._place_lines(state, rest, target)

    def flush_linked_text(self) -> None:
        """연결할 빈 글상자를 끝내 찾지 못한 넘친 줄은 원래 글상자 안에 놓는다(글상자 안 다단 등)."""
        for _, pending in self.__dict__.pop("_linked_boxes", {}).values():
            for rest, (state, area), _ in pending:
                self._place_lines(state, rest, area)

    def _place_lines(self, state: PageState, lines: list[tuple[ParagraphView, int]], area: Area) -> None:
        for view, index in lines:
            self.place_line(state, view, index, area)

    def draw_picture(
        self, state: PageState, picture: ShapePicture, left: float, top: float, width: float, height: float
    ) -> None:
        href = self.image_href(picture.bin_data_id)
        if href is not None:
            state.page.items.append(Image(left, top, width, height, href))

    def image_href(self, bin_data_id: int) -> str | None:
        if bin_data_id in self.image_cache:
            return self.image_cache[bin_data_id]
        href = None
        if 1 <= bin_data_id <= len(self.styles.bin_data):
            entry = self.styles.bin_data[bin_data_id - 1]
            name = entry.storage_name
            if name and name in self.document.container.raw_streams:
                compressed = {0: None, 1: True, 2: False}.get(entry.compression)
                data = self.document.container.bin_data(name, compressed)
                href = f"data:{_mime(data, name)};base64,{base64.b64encode(data).decode('ascii')}"
        self.image_cache[bin_data_id] = href
        return href


#: 1단계 범위 밖 개체: 수식, 그리고 OLE·차트·글맵시를 담은 그리기 개체.
_OUT_OF_SCOPE_TAGS = frozenset({tags.SHAPE_COMPONENT_OLE, tags.SHAPE_COMPONENT_TEXTART, tags.CHART_DATA, tags.EQEDIT})


def _rendered_box(component: ShapeComponent) -> tuple[float, float, float, float] | None:
    """묶음 안 도형이 그려지는 상자(묶음 기준 x, y, 폭, 높이). 이동 행렬 · (축척·회전 행렬 쌍)… 을 원래 크기에 적용한다.

    실측: kr_156489577 2쪽 목차 묶음. 자식 오프셋·현재 크기만 쓰면 +93pt, −320pt 어긋난다.
    """
    if component.translation is None or not component.scale_rotation:
        return None
    matrix = component.translation
    for scale, rotation in component.scale_rotation:
        matrix = _compose(_compose(matrix, scale), rotation)
    corners = [_apply(matrix, x, y) for x in (0, component.original_width) for y in (0, component.original_height)]
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def _compose(first: tuple, second: tuple) -> tuple:
    """[a, b, tx, c, d, ty] 아핀 행렬 곱 first·second(second를 먼저 적용)."""
    a1, b1, x1, c1, d1, y1 = first
    a2, b2, x2, c2, d2, y2 = second
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        a1 * x2 + b1 * y2 + x1,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        c1 * x2 + d1 * y2 + y1,
    )


def _apply(matrix: tuple, x: float, y: float) -> tuple[float, float]:
    a, b, tx, c, d, ty = matrix
    return a * x + b * y + tx, c * x + d * y + ty


def _pushes_first_line(view: ParagraphView) -> bool:
    """첫 줄 맨 앞이 아닌 자리에 자리차지 개체가 있으면 한글은 첫 줄을 개체 아래로 내리고, 개체는 문단이 시작할
    자리를 기준으로 놓는다(실측: hwpx-h-01 문단 21은 공백 뒤 표, 첫 줄 69055 = 앞 문단 끝 63364 + 표 상자 5691;
    pyhwp table-caption 문단 1처럼 맨 앞 표 하나뿐이면 첫 줄은 표 위치에 그대로 있다).
    """
    if not view.segments:
        return False
    first_line_end = view.segments[1].text_start if len(view.segments) > 1 else 1 << 31
    blocks = [
        view.positions[index]
        for index, ctrl in view.controls.items()
        if view.positions[index] < first_line_end
        and isinstance(ctrl.model, ObjectCtrlHeader)
        and not ctrl.model.treat_as_char
        and ctrl.model.text_wrap == TextWrap.TOP_AND_BOTTOM
    ]
    # 맨 앞 개체 하나뿐이면 첫 줄은 개체 위치에 그대로 있다. 둘 이상이면 차례로 쌓은 아래로 밀린다
    # (float-stack-defer p1: 표 두 개 뒤 줄 58203).
    return any(position > 0 for position in blocks) or len(blocks) > 1


def _pushed_extent(model: ObjectCtrlHeader, height: float) -> float:
    """문단 첫 줄을 아래로 미는 개체 상자 길이: 세로 오프셋 + 높이 + 위·아래 바깥 여백."""
    margins = model.outer_margins or [0, 0, 0, 0]
    return model.vert_offset + height + margins[2] + margins[3]


def _is_top_and_bottom(model) -> bool:
    """글자처럼 취급하지 않는 자리차지 개체."""
    return (
        isinstance(model, ObjectCtrlHeader) and not model.treat_as_char and model.text_wrap == TextWrap.TOP_AND_BOTTOM
    )


def _out_of_scope(ctrl: Node) -> bool:
    """수식이거나, OLE·차트·글맵시를 담은 그리기 개체면 True. 표는 셀 안 개체를 셀을 그릴 때 따로 판정한다."""
    kind = ctrl.record.payload[:4]
    if kind == tags.EQUATION_CTRL.to_bytes(4, "little"):
        return True
    if kind == tags.TABLE_CTRL.to_bytes(4, "little"):
        return False
    pending = list(ctrl.children)
    while pending:
        node = pending.pop()
        if node.tag in _OUT_OF_SCOPE_TAGS:
            return True
        pending.extend(node.children)
    return False


def _align(reference: float, span: float, size: float, align: int, offset: int) -> float:
    if align == ALIGN_CENTER:
        return reference + (span - size) / 2 + offset
    if align in (ALIGN_END, 4):
        return reference + span - size - offset
    return reference + offset


def _mime(data: bytes, name: str) -> str:
    signatures = {
        b"\x89PNG": "image/png",
        b"\xff\xd8": "image/jpeg",
        b"GIF8": "image/gif",
        b"BM": "image/bmp",
    }
    for signature, mime in signatures.items():
        if data.startswith(signature):
            return mime
    extension = name.rsplit(".", 1)[-1].lower()
    return {"wmf": "image/wmf", "emf": "image/emf", "svg": "image/svg+xml"}.get(extension, "application/octet-stream")
