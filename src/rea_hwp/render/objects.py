"""표·그림·글상자 같은 개체의 크기와 위치를 정하고 그린다."""

from __future__ import annotations

import base64
import dataclasses
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
from ..models.paragraph import LineSeg
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
from .scene import Glyph, Image
from .table_flow import _margin_shift, is_block_table
from .wmf import WmfDrawing, convert_wmf

#: 연결 글상자를 찾을 때 넘친 줄 폭과 빈 글상자 안쪽 폭의 허용 차이(HU). 검증 안 됨: BookReview는 0~2HU 차이.
LINK_WIDTH_TOLERANCE = 100
#: 문단 머리 break_type의 구역(0x01)·쪽(0x04)·단(0x08) 나누기 비트.
BREAK_SECTION_PAGE_COLUMN = 0x01 | 0x04 | 0x08
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
        if id(ctrl) in self.__dict__.get("stacked_floats", ()):
            # 셀 엔진이 쌓아 따로 그리는 개체(table_flow.place_stacked_float).
            return
        width, height = self.object_size(model, state, area)
        component = ctrl.find(tags.SHAPE_COMPONENT)
        if component is not None and isinstance(component.model, ShapeComponent):
            # 글상자는 개체 머리 높이가 낡아도 도형 요소 현재 높이로 그린다(실측: 3-10월_교육_통합_2022 2쪽 보기 상자
            # 머리 1249, 현재 19904 = 기준 줄 높이 19905).
            text_list = component.find(tags.LIST_HEADER)
            if text_list is not None and isinstance(text_list.model, TextBoxListHeader):
                height = max(height, float(component.model.current_height))
        margins = model.outer_margins or [0, 0, 0, 0]
        anchor_state = state
        caption = self.object_caption(ctrl, width, margins[0])
        # 캡션은 개체 상자에 들어간다: 위·아래 캡션은 높이를, 왼·오른 캡션은 폭을 늘린다(실측: pyhwp table-caption).
        box_width = width + (caption.extent if caption and caption.beside else 0)
        box_height = height + (caption.extent if caption and not caption.beside else 0)
        if model.treat_as_char:
            # 바깥 여백을 포함한 개체 상자의 85%를 기준선 위에 둔다(rhwp TAC_OBJECT_ASCENT_RATIO, 저장 줄과 일치).
            left = x + margins[0]
            top = baseline - TAC_ASCENT * (box_height + margins[2] + margins[3]) + margins[2]
        else:
            full_height = None
            if self.in_body and is_block_table(ctrl) and model.text_wrap == TextWrap.TOP_AND_BOTTOM:
                grid, _ = self.table_layout_grid(ctrl)
                full_height = grid.height if grid is not None else None
            left, top = self.floating_position(model, state, view, area, box_width, box_height, full_height)
            if self.in_body and model.text_wrap == TextWrap.SQUARE:
                state.floats.append((state.column, *outer_box(model, left, top, box_width, box_height)))
            if is_block_table(ctrl) and model.vert_offset < 0 and model.vert_rel_to == REL_COLUMN_OR_PARA:
                # 문단 기준 자리차지 표는 문단 위로 올라가지 않는다(실측: 셀 안 issue4889 1쪽 55행 표 오프셋 −975,
                # 위 선 289.12; 본문 kr_156369118 5쪽 s0 p63 표 오프셋 −622, 정답 기준선 695.2pt).
                top -= model.vert_offset
            if self.in_body and model.text_wrap == TextWrap.TOP_AND_BOTTOM:
                state, top = self.stacked_top(state, left, box_width, top, box_height + margins[2] + margins[3])
            if ctrl.record.payload[:4] != tags.TABLE_CTRL.to_bytes(4, "little"):
                # 바깥 여백까지 든 상자가 정렬 기준에 맞고 개체는 그 안에 그린다. 오른쪽·아래 정렬이면
                # 오른·아래 여백만큼 안쪽이다(오른쪽 정렬 그림을 피한 저장 줄이 왼 여백 566HU만큼 더 좁았다).
                # 표는 draw_table이 같은 보정을 한다.
                left += _margin_shift(model.horz_align, margins[0], margins[1])
                top += _margin_shift(model.vert_align, margins[2], margins[3])
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
            if placed is not None and anchor_state is not state and placed.last_state is placed.first_state:
                # 앞 개체에 밀려 다음 쪽에 통째로 놓인 표 뒤 본문은 기준 쪽에서 잇는다(실측: hwpspec s2 p271 표 67이
                # 43쪽, 다음 문단 p272 21656은 같은 43쪽).
                self.current_state = anchor_state
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
        self,
        model: ObjectCtrlHeader,
        state: PageState,
        view: ParagraphView,
        area: Area,
        width: float,
        height: float,
        full_height: float | None = None,
    ) -> tuple[float, float]:
        """full_height는 쪽을 넘을 수 있는 표의 격자 높이다(저장 높이는 첫 쪽 몫만 적히기도 한다)."""
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
        elif (
            self.in_body
            and model.vert_rel_to == REL_COLUMN_OR_PARA
            and model.text_wrap == TextWrap.TOP_AND_BOTTOM
            and full_height is not None
            and view.segments
            and self.flow_position(state, para_top) + view.segments[0].line_height + view.segments[0].line_spacing
            < para_top
            and self.flow_position(state, para_top) + _pushed_extent(model, full_height)
            > geometry.body_top + geometry.body_height
        ):
            # 앞 줄 끝에서 시작한 표가 이 쪽을 넘기면 첫 줄은 다음 쪽 표 나머지 아래로 저장된다. 표는 앞 줄 끝에 놓는다
            # (실측: 정책연구용역 s0 p1822 앞 줄 끝 26000, 첫 줄 37848 = 175쪽 표 나머지 아래, 정답 표 위 선 324.84pt).
            # 앞 줄 끝에서 한 줄 간격 안에 저장된 줄은 이 쪽 줄이다(편람 149쪽 표: 차이 1000, 정답은 저장 줄 기준).
            para_top = self.flow_position(state, para_top)
        elif (
            self.in_body
            and model.vert_rel_to == REL_COLUMN_OR_PARA
            and (
                not self.starts_page_by_rewind(state, view)
                or bool(view.segments)
                and view.segments[0].vertical_pos == view.para_shape.space_before // 2 > 0
            )
            and not (self.lines_after_spill == 0 and self.table_bottom is not None)
        ):
            # 문단 기준 위치는 문단 위 간격 앞이다(편람 47쪽 s2 p214: 첫 줄 1900 = 앞 줄 끝 1200 + 위 간격 1400/2,
            # 정답 표 위 선 132.11pt = 1200 자리). 쪽 나눔(rowbreak s1 p2 500)도 위 간격을 넣어 저장하지만, 넘쳐서
            # 새 쪽 첫 문단이 되면 위 간격 없이 저장된다(hwpspec s2 p181 31쪽 14091).
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

    def starts_page_by_rewind(self, state: PageState, view: ParagraphView) -> bool:
        """쪽·단 나누기 없이 넘쳐서 이 쪽·단의 첫 문단이 됐는가."""
        continues = (
            self.flow_cursor is not None and self.flow_cursor[0] is state and self.flow_cursor[1] == state.column
        )
        return not continues and not view.node.model.break_type & BREAK_SECTION_PAGE_COLUMN

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
                            # 표를 통째로 옮겨 만든 쪽이라 뒤 문단의 되감기가 이 쪽을 잇는다(실측: basicsReport s0 p101
                            # 표 둘째가 5쪽, 정답은 p112 50528부터 같은 5쪽).
                            self.__dict__.setdefault("float_pages", set()).add(id(state))
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

    def textbox_grown_line(self, view: ParagraphView, index: int) -> LineSeg | None:
        """줄의 글자처럼 취급한 글상자 글을 다시 나누면 더 길어질 때 그만큼 늘린 줄. 아니면 None.

        한글 2022는 저장 줄보다 넓어진 글상자 글을 다시 나누고 상자를 늘린다(실측: pr-1674 s0 p159 「붙임2-1」
        저장 한 줄 6660HU, 우리 폭 6788HU > 안쪽 6662HU, 정답은 두 줄로 상자가 51.6pt가 되어 뒤 표가 다음 쪽으로 감).
        """
        first, last = view.line_range(index)
        growth = 0.0
        for order in range(first, last):
            ctrl = view.controls.get(order)
            if ctrl is not None and isinstance(ctrl.model, ObjectCtrlHeader) and ctrl.model.treat_as_char:
                growth = max(growth, self.textbox_growth(ctrl))
        if growth <= 0:
            return None
        segment = view.segments[index]
        return dataclasses.replace(segment, line_height=segment.line_height + round(growth))

    def textbox_growth(self, ctrl: Node) -> float:
        component = ctrl.find(tags.SHAPE_COMPONENT)
        text_list = component.find(tags.LIST_HEADER) if component is not None else None
        if text_list is None or not isinstance(text_list.model, TextBoxListHeader):
            return 0.0
        margins = text_list.model.margins
        paragraphs = [child for child in component.children if child.tag == tags.PARA_HEADER]
        inner_width = ctrl.model.width - margins[0] - margins[1]
        stored = self.paragraph_views(paragraphs, inner_width)
        if not stored or not all(view.segments for view in stored):
            return 0.0
        fresh = self.recomposed_views(stored, inner_width)
        if sum(len(view.segments) for view in fresh) <= sum(len(view.segments) for view in stored):
            return 0.0
        return max(0.0, fresh[-1].content_height + margins[2] + margins[3] - ctrl.model.height)

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
            views = self.paragraph_views(paragraphs, inner_width, textbox=True)
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
        drawing = self.metafile(picture.bin_data_id)
        if drawing is not None:
            self.draw_metafile(state, drawing, left, top, width, height)
            return
        href = self.image_href(picture.bin_data_id)
        if href is not None:
            state.page.items.append(Image(left, top, width, height, href))

    def draw_metafile(
        self, state: PageState, drawing: WmfDrawing, left: float, top: float, width: float, height: float
    ) -> None:
        """WMF 그림은 도형을 SVG 그림으로, 글자는 그림 상자에 맞춰 늘린 글자로 그린다(한글 PDF도 글자로 찍는다)."""
        svg = base64.b64encode(drawing.svg.encode("utf-8")).decode("ascii")
        state.page.items.append(Image(left, top, width, height, f"data:image/svg+xml;base64,{svg}"))
        scale_x = width / drawing.width if drawing.width else 1.0
        scale_y = height / drawing.height if drawing.height else 1.0
        for text in drawing.texts:
            x = left + text.x * scale_x
            for index, char in enumerate(text.text):
                advance = text.advances[index] * scale_x if text.advances and index < len(text.advances) else 0.0
                if char.strip():
                    state.page.items.append(
                        Glyph(
                            char,
                            x,
                            top + text.y * scale_y,
                            text.size * scale_y,
                            text.face,
                            text.color,
                            text.bold,
                            text.italic,
                            advance=advance,
                        )
                    )
                x += advance

    def metafile(self, bin_data_id: int) -> WmfDrawing | None:
        entry = self.styles.bin_data[bin_data_id - 1] if 1 <= bin_data_id <= len(self.styles.bin_data) else None
        if entry is None or (entry.extension or "").lower() != "wmf":
            return None
        cache = self.__dict__.setdefault("_metafiles", {})
        if bin_data_id not in cache:
            data = self.bin_bytes(bin_data_id)
            cache[bin_data_id] = convert_wmf(data) if data is not None else None
        return cache[bin_data_id]

    def bin_bytes(self, bin_data_id: int) -> bytes | None:
        if 1 <= bin_data_id <= len(self.styles.bin_data):
            entry = self.styles.bin_data[bin_data_id - 1]
            name = entry.storage_name
            if name and name in self.document.container.raw_streams:
                compressed = {0: None, 1: True, 2: False}.get(entry.compression)
                return self.document.container.bin_data(name, compressed)
        return None

    def image_href(self, bin_data_id: int) -> str | None:
        if bin_data_id in self.image_cache:
            return self.image_cache[bin_data_id]
        href = None
        data = self.bin_bytes(bin_data_id)
        if data is not None:
            name = self.styles.bin_data[bin_data_id - 1].storage_name
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
    """수식이거나, OLE·차트·글맵시를 담은 그리기 개체면 True. 표·글상자 문단 안 개체는 그 글을 놓을 때 따로 판정한다."""
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
        # 글상자 문단 속 수식은 글을 놓을 때 하나씩 제외하므로 글상자 전체를 범위 밖으로 보지 않는다.
        if node.tag == tags.PARA_HEADER:
            continue
        pending.extend(node.children)
    return False


def outer_box(
    model: ObjectCtrlHeader, left: float, top: float, width: float, height: float
) -> tuple[float, float, float, float, int]:
    """floating_position 자리(left, top)에 놓인 개체의 바깥 여백까지 든 상자와 글 흐름.

    바깥 상자가 정렬 기준에 맞는다(표 그리기 _margin_shift와 같은 규칙, 오른쪽 정렬 그림의 피한 구간 566HU로 확인).
    """
    margins = model.outer_margins or [0, 0, 0, 0]
    return (
        left + _margin_shift(model.horz_align, margins[0], margins[1]) - margins[0],
        top + _margin_shift(model.vert_align, margins[2], margins[3]) - margins[2],
        width + margins[0] + margins[1],
        height + margins[2] + margins[3],
        int(model.text_flow),
    )


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
