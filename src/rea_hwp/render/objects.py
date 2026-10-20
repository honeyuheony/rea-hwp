"""표·그림·글상자 같은 개체의 크기와 위치를 정하고 그린다."""

from __future__ import annotations

import base64
import dataclasses
import functools
import math
import os
import struct
from dataclasses import dataclass
from pathlib import Path

from .. import tags
from ..document import Node
from ..models.controls import (
    AutoNumberCtrl,
    CaptionListHeader,
    ColumnDefCtrl,
    EqEdit,
    ObjectCtrlHeader,
    TextBoxListHeader,
    TextWrap,
)
from ..models.docinfo import FILL_GRADIENT, FILL_SOLID
from ..models.paragraph import LineSeg
from ..models.shapes import (
    ShapeComponent,
    ShapeEllipse,
    ShapeLine,
    ShapeOle,
    ShapePicture,
    ShapePolygon,
    ShapeRectangle,
)
from .chart import ChartDrawing, draw_vtchart, vtchart_contents
from .common import (
    ALIGN_CENTER,
    ALIGN_END,
    LINE_PAINT_GROW,
    OBJECT_PAINT_GROW,
    REL_COLUMN_OR_PARA,
    REL_PAGE,
    REL_PAPER,
    REL_PARA,
    Area,
    PageState,
    ParagraphView,
    TextChar,
    column_layout,
)
from .equation import LegacyEngineState, LegacyEquationError, measure_legacy
from .equation import measure as measure_equation
from .equation.engine import muldiv
from .equation.glyphs import EQUATION_FONT, glyph_table
from .forms import draw_form
from .ole import content_metafile, has_ooxml_chart
from .ooxml_chart import draw_ooxml_chart, ooxml_chart_contents
from .raster import png_from_raster
from .reflow import ASCENT_PERCENT, RANGE_GRID
from .scene import Glyph, Image, Line, Rect
from .table_flow import _margin_shift, is_block_table, vertical_offset
from .textart import FontFiles, TextArtDrawing, TextArtFontError, draw_textart
from .wmf import WmfDrawing, convert_wmf

#: 연결 글상자를 찾을 때 넘친 줄 폭과 빈 글상자 안쪽 폭의 허용 차이(HU). 검증 안 됨: BookReview는 0~2HU 차이.
LINK_WIDTH_TOLERANCE = 100
#: 문단 머리 break_type의 구역(0x01)·쪽(0x04)·단(0x08) 나누기 비트.
BREAK_SECTION_PAGE_COLUMN = 0x01 | 0x04 | 0x08
#: 떠 있는 개체의 그리기 층(FUN_1018c200): 글 뒤로 −1, 글 앞으로 +1, 어울림·자리차지 0.
PAINT_LAYERS = {TextWrap.BEHIND_TEXT: -1, TextWrap.IN_FRONT_OF_TEXT: 1}
#: 단 정의 종류 2(평행 다단). 평행 다단에서는 개체를 다음 단으로 옮기지 않는다(FUN_102dbf80).
PARALLEL_COLUMNS = 2
#: 캡션 방향(명세 표72).
CAPTION_LEFT, CAPTION_RIGHT, CAPTION_TOP, CAPTION_BOTTOM = range(4)
#: 한글 2022 수식 엔진(HwpPlugin)의 버전 문자열. 다른 버전은 HwpApp 옛 엔진(FUN_106578c0)이 계산한다.
NEW_EQUATION_ENGINE = "Equation Version 60"


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
    def equation_box(self, ctrl: Node) -> tuple[int, int, int] | None:
        """한글 2022가 문서를 열 때 다시 계산하는 수식 (폭, 높이, 저장 기준선). 수식이 아니거나 계산하지 못하면 None."""
        key = id(ctrl)
        if key not in self.equation_boxes and _LEGACY_DONE not in self.equation_boxes and _is_legacy_equation(ctrl):
            self.measure_legacy_equations()
        if key not in self.equation_boxes:
            self.equation_boxes[key] = _recomputed_equation(ctrl)
        return self.equation_boxes[key]

    def measure_legacy_equations(self) -> None:
        """옛 엔진 수식을 문서 순서대로 한꺼번에 계산한다. 글꼴 플래그가 앞 수식에서 이어진다(LegacyEngineState)."""
        state = LegacyEngineState()
        stack = [node for section in reversed(self.document.sections) for node in reversed(section.nodes)]
        while stack:
            node = stack.pop()
            stack.extend(reversed(node.children))
            if _is_legacy_equation(node):
                self.equation_boxes[id(node)] = _recomputed_equation(node, state)
        self.equation_boxes[_LEGACY_DONE] = None

    def resized_equation(self, ctrl: Node) -> bool:
        box = self.equation_box(ctrl)
        return box is not None and box != (ctrl.model.width, ctrl.model.height, ctrl.find(tags.EQEDIT).model.baseline)

    def resized_size(self, ctrl: Node) -> tuple[int, int] | None:
        """한글이 문서를 열 때 바꾸는 개체 (폭, 높이). 바뀌지 않으면 None.

        수식은 다시 계산한 크기다. 셀에 크기가 바뀐 수식이 든 표는 한글이 셀을 다시 짜 높이를 행 높이 합으로 다시
        쓴다(재저장본: 그런 표 21개 모두 높이가 바뀌었고 20개가 우리 격자 높이와 같다).
        """
        key = ("size", id(ctrl))
        if key not in self.equation_boxes:
            model = ctrl.model
            box = self.equation_box(ctrl)
            size = box[:2] if box is not None else None
            if box is None and ctrl.record.payload[:4] == tags.TABLE_CTRL.to_bytes(4, "little"):
                if any(self.resized_equation(node) for node in _descendants(ctrl) if node.tag == tags.CTRL_HEADER):
                    grid, _ = self.table_layout_grid(ctrl)
                    size = (model.width, round(grid.height)) if grid is not None else None
            self.equation_boxes[key] = size if size is not None and size != (model.width, model.height) else None
        return self.equation_boxes[key]

    def resized_object(self, ctrl: Node) -> bool:
        """한글이 문서를 열 때 크기나 기준선을 바꿔 그 문단 줄을 다시 짜게 하는 개체인가."""
        return self.resized_equation(ctrl) or self.resized_size(ctrl) is not None

    def object_model(self, ctrl: Node) -> ObjectCtrlHeader:
        """개체 머리. 크기가 바뀌는 개체는 바뀐 폭·높이를 넣은 사본이다(문서 모델은 고치지 않는다)."""
        model = ctrl.model
        size = self.resized_size(ctrl) if isinstance(model, ObjectCtrlHeader) else None
        if size is None:
            return model
        key = ("model", id(ctrl))
        if key not in self.equation_boxes:
            self.equation_boxes[key] = type(model)(
                values={**model.values, "width": size[0], "height": size[1]}, tail=model.tail
            )
        return self.equation_boxes[key]

    def inline_box(self, ctrl: Node) -> tuple[int, int]:
        """글자처럼 취급한 개체의 (바깥 여백 포함 높이, 기준선 위 몫).

        HwpApp FUN_101d7770: 개체 높이 × 기준선 함수(+0x140) 값 / 100(HncIntMulDiv). 수식은 EQEDIT 기준선을 돌려주고
        (FUN_1026ee50), 다른 개체는 −1이라 85다. 바깥 여백을 높이에 넣는 것은 85% 규칙을 따랐다(수식으로는 검증 안 됨).
        """
        model = self.object_model(ctrl)
        margins = model.outer_margins or [0, 0, 0, 0]
        height = model.height + margins[2] + margins[3]
        return height, muldiv(height, self.ascent_percent(ctrl), 100)

    def ascent_percent(self, ctrl: Node) -> int:
        box = self.equation_box(ctrl)
        if box is not None:
            return box[2]
        equation = ctrl.find(tags.EQEDIT) if _is_equation(ctrl) else None
        if equation is not None and isinstance(equation.model, EqEdit):
            return equation.model.baseline
        return ASCENT_PERCENT

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
        model = self.object_model(ctrl)
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
            # 바깥 여백을 포함한 개체 상자의 기준선 위 몫(수식은 기준선 백분율, 나머지 85%)을 기준선 위에 둔다
            # (inline_box와 같은 규칙, rhwp TAC_OBJECT_ASCENT_RATIO, 저장 줄과 일치).
            left = x + margins[0]
            top = baseline - muldiv(round(box_height) + margins[2] + margins[3], self.ascent_percent(ctrl), 100)
            top += margins[2]
        else:
            full_height = None
            if self.in_body and is_block_table(ctrl) and model.text_wrap == TextWrap.TOP_AND_BOTTOM:
                grid, _ = self.table_layout_grid(ctrl)
                full_height = grid.height if grid is not None else None
            left, top = self.floating_position(model, state, view, area, box_width, box_height, full_height)
            if self.overflows_to_next_column(model, ctrl, state, top, box_height + margins[2] + margins[3]):
                # FUN_102d51c0 0x102d5474: 단 아래를 넘는 문단 기준 개체는 대기 목록에 들어갔다가 다음 단을 시작할 때
                # 단 맨 위에 놓인다(FUN_102d6280). 다시 놓을 때 세로 오프셋을 더하는 조건(obj+0x68)은 확인하지 못해
                # 더하지 않는다
                state = dataclasses.replace(state, column=state.column + 1)
                left, _ = self.floating_position(model, state, view, area, box_width, box_height, full_height)
                top = state.geometry.body_top + state.zone_top
            if (self.in_body or self.body_anchor) and model.text_wrap == TextWrap.SQUARE:
                state.floats.append((state.column, *outer_box(model, left, top, box_width, box_height)))
            if self.in_body and model.text_wrap == TextWrap.TOP_AND_BOTTOM and model.flow_with_text:
                line_top = area.y + view.segments[0].vertical_pos if view.segments else None
                state, top = self.stacked_top(
                    state, left, box_width, top, box_height + margins[2] + margins[3], line_top
                )
            elif (self.in_body or self.body_anchor) and model.text_wrap == TextWrap.TOP_AND_BOTTOM:
                # FUN_102d5900은 본문 영역으로 제한한(attr 0x2000) 문단 기준 개체만 앞 개체 아래로 민다(issue1510 표 C는
                # 표 B와 겹친다). 종이·쪽 기준이나 제한 없는 개체도 쪽 개체 목록에 들어가 뒤 개체가 피한다.
                self.register_stacked_box(state, left, box_width, top, box_height + margins[2] + margins[3])
            if ctrl.record.payload[:4] != tags.TABLE_CTRL.to_bytes(4, "little"):
                # 바깥 여백까지 든 상자가 정렬 기준에 맞고 개체는 그 안에 그린다. 오른쪽·아래 정렬이면
                # 오른·아래 여백만큼 안쪽이다(오른쪽 정렬 그림을 피한 저장 줄이 왼 여백 566HU만큼 더 좁았다).
                # 표는 draw_table이 같은 보정을 한다.
                left += _margin_shift(model.horz_align, margins[0], margins[1])
                top += _margin_shift(model.vert_align, margins[2], margins[3])
        # 떠 있는 개체는 본문 글과 따로 층으로(FUN_1018c200), 글자처럼 취급한 개체는 줄 안에서(FUN_1019b7d0) 그린다.
        # 표는 draw_table이 자리를 다시 정하므로(다음 쪽으로 밀림 등) 개체 상자 대신 셀마다 판정한다(FUN_1018cd80).
        is_table = ctrl.record.payload[:4] == tags.TABLE_CTRL.to_bytes(4, "little")
        box = None if is_table else (left, top, box_width, box_height)
        layer = None if model.treat_as_char else PAINT_LAYERS.get(model.text_wrap, 0)
        with self.painting(state.page, box, OBJECT_PAINT_GROW, layer=layer, z_order=model.z_order):
            self.draw_object(state, ctrl, model, caption, anchor_state, left, top, width, height)

    def draw_object(
        self,
        state: PageState,
        ctrl: Node,
        model: ObjectCtrlHeader,
        caption: Caption | None,
        anchor_state: PageState,
        left: float,
        top: float,
        width: float,
        height: float,
    ) -> None:
        margins = model.outer_margins or [0, 0, 0, 0]
        if caption and caption.direction == CAPTION_LEFT:
            left += caption.extent
        elif caption and caption.direction == CAPTION_TOP:
            top += caption.extent
        first, last, bottom = state, state, top + height
        if self.out_of_scope(ctrl, (width, height)):
            state.page.excluded.append((left, top, width, height))
            component = ctrl.find(tags.SHAPE_COMPONENT)
            if component is not None:
                # 옮기지 못한 개체도 그릴 수 있는 만큼(새 차트의 표현 그림 등)은 그리고 채점에서만 뺀다.
                self.draw_component(state, component, left, top, width, height)
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
                # 글자처럼 취급해 늘어나는 글상자(textbox_grown_line)는 늘어난 줄도 찍는다(pr-1674 16쪽 「2-1」).
                growth = self.textbox_growth(ctrl) if model.treat_as_char else 0.0
                self.draw_component(state, component, left, top, width, height, text_growth=growth)
            form = ctrl.find(tags.FORM_OBJECT)
            if form is not None:
                draw_form(self, state, form, left, top, width, height)
        if caption:
            owner = last if caption.direction == CAPTION_BOTTOM else first
            self.place_caption(owner, caption, left, top, bottom, width)

    def overflows_to_next_column(
        self, model: ObjectCtrlHeader, ctrl: Node, state: PageState, top: float, outer_height: float
    ) -> bool:
        """FUN_102d51c0: 문단 기준·본문 영역 제한 개체가 단 아래를 넘고 옮겨 갈 다음 단이 있는가(표는 쪽 나눔 엔진)."""
        geometry = state.geometry
        column_def = geometry.column_def
        return (
            self.in_body
            and not is_block_table(ctrl)
            and model.vert_rel_to == REL_COLUMN_OR_PARA
            and model.flow_with_text
            and state.column + 1 < len(geometry.columns)
            and not (column_def is not None and column_def.kind == PARALLEL_COLUMNS)
            and top + outer_height > geometry.body_top + geometry.body_height
        )

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
        if len({w for _, w in geometry.columns}) == 1:
            # 한글의 단 좌표는 폭이 같은 단이면 4HU로 내린 폭이다(줄 구간 reflow.horizontal_range와 같다).
            # 단 기준 오른쪽 맞춤 개체도 그 끝에 맞는다(143E433F 2단 22961: 저장 줄이 개체 왼쪽 13325 =
            # 22960 − 바깥 폭 9635까지).
            column_width -= column_width % RANGE_GRID
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
        vert_offset = vertical_offset(model)
        return (
            _align(ref_x, ref_w, width, model.horz_align, model.horz_offset),
            _align(ref_y, ref_h, height, model.vert_align, vert_offset),
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
            return min(para_top, state.geometry.body_top + max(state.zone_top, 0))
        cursor_state, column, bottom = self.flow_cursor
        if cursor_state is not state:
            # 쪽 첫 문단이면 쪽(단 영역) 맨 위에서 시작한다(issue6946 s2 p0: 구역 첫 문단의 표 두 개 뒤 줄 65959).
            # 넘쳐서 만든 쪽의 zone_top은 −첫 줄 세로 위치라 음수다(labnote s0 p12 76450, 정답 표 위 선 31pt).
            return min(para_top, state.geometry.body_top + max(state.zone_top, 0))
        if column != state.column or bottom >= para_top:
            return para_top
        # 앞 문단 위아래 배치 개체에 걸린 자리는 그 아래에서 시작한다(kr_156489673 s0 p1: 앞 줄 끝 9638이 p0 표
        # 7126~28256 안, 정답 표는 저장 줄 28256 + 오프셋 2866). 자기 문단 개체는 이 자리를 기준으로 놓이므로 빼고 본다.
        earlier = self.__dict__.get("earlier_boxes", set())
        registry = self.__dict__.get("_stacked_boxes", {})
        boxes = [box for (page, _), listed in registry.items() if page == id(state.page) for box in listed]
        column_x, column_width = state.geometry.columns[state.column]
        left = state.geometry.body_left + column_x
        moved = True
        while moved:
            moved = False
            for box in boxes:
                box_left, box_right, box_top, box_bottom, continued = box
                # 앞 쪽에서 넘어온 표 나머지(위 끝 −inf)는 표 쪽 나눔이 이어 붙인 자리라 피하지 않는다(hwpspec 42쪽).
                if (
                    id(box) in earlier
                    and continued is None
                    and box_top != float("-inf")
                    and box_left < left + column_width
                    and left < box_right
                    and box_top <= bottom < box_bottom
                ):
                    bottom, moved = box_bottom, True
        return min(bottom, para_top)

    def stacked_top(
        self, state: PageState, left: float, width: float, top: float, height: float, line_top: float | None = None
    ) -> tuple[PageState, float]:
        """자리차지 개체끼리는 겹치지 않는다. 앞서 놓인 상자와 겹치면 그 아래로 민다. 돌려주는 값은 (쪽, 위 끝).

        실측: pyhwp table-caption 표 2·7, 중간진도보고서 44쪽 표 20(저장 세로 오프셋 −17997이어도 표 19 아래).
        앞 상자가 쪽을 넘긴 표면 그 표가 끝난 쪽의 표 아래로 민다(issue5699 s0 p115: 표 둘째(오프셋 784)가 10쪽에서
        11쪽으로 넘어간 표 첫째 아래, 정답 11쪽 「□ 비용:」 128.5pt).
        """
        registry = self.__dict__.setdefault("_stacked_boxes", {})
        boxes = registry.setdefault((id(state.page), state.column), [])
        anchor, pushed = state, False
        earlier = self.__dict__.get("earlier_boxes", set())
        moved = True
        while moved:
            moved = False
            # 겹침은 쪽 개체 목록 전체에서 찾는다(FUN_102d5900 0x102d5a5e): 두 단에 걸친 띠 아래로 다른 단 개체도 밀린다
            # (143E433F 오른쪽 단 차트가 왼쪽 단 문단 0의 종이 기준 띠 아래 283HU)
            page_boxes = [box for (page, _), listed in registry.items() if page == id(state.page) for box in listed]
            for box in page_boxes:
                box_left, box_right, box_top, box_bottom, continued = box
                if left < box_right and box_left < left + width and top < box_bottom and box_top < top + height:
                    moved = True
                    if continued is None:
                        # 같은 문단 앞 개체에 밀렸는가(earlier_boxes는 이 문단을 놓기 전부터 있던 상자).
                        top, pushed = box_bottom, id(box) not in earlier
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
        geometry = state.geometry
        if (
            pushed
            and state is anchor
            and line_top is not None
            and top <= line_top < top + height
            and top + height > geometry.body_top + geometry.body_height
        ):
            # 같은 문단 앞 개체에 밀려 쌓인 개체가 이 쪽에 다 들어가지 않는데 기준 문단 저장 줄이 그 자리에 있으면,
            # 한글은 개체를 여기서 시작하지 않고 다음 쪽 맨 위에 둔 것이다(실측: float-stack-defer 문단 1 표 둘째, 정답
            # 2쪽 90.86pt부터 통째로). 앞 문단 개체에 밀린 표는 그 자리에서 나뉜다(hwpspec 26·29·33쪽).
            following = self.states.index(state) + 1
            if following < len(self.states):
                state = self.states[following]
            else:
                state = self.new_page(state.base_geometry)
                self.__dict__.setdefault("float_pages", set()).add(id(state))
            top = state.geometry.body_top
            boxes = registry.setdefault((id(state.page), state.column), [])
        boxes.append((left, left + width, top, top + height, None))
        return state, top

    def register_stacked_box(self, state: PageState, left: float, width: float, top: float, height: float) -> None:
        registry = self.__dict__.setdefault("_stacked_boxes", {})
        registry.setdefault((id(state.page), state.column), []).append((left, left + width, top, top + height, None))

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
        self,
        state: PageState,
        component: Node,
        left: float,
        top: float,
        width: float,
        height: float,
        origin: tuple[float, float] | None = None,
        text_growth: float = 0.0,
    ) -> None:
        """origin은 맨 바깥 묶음의 왼쪽 위다. 자식 렌더링 행렬과 오프셋은 바로 위 묶음이 아니라 그 묶음 기준이다."""
        if isinstance(component.model, ShapeComponent):
            svg = _shape_svg(component, width, height)
            if svg is not None:
                href = f"data:image/svg+xml;base64,{base64.b64encode(svg.encode('utf-8')).decode('ascii')}"
                pad = _shape_pad(component.model)
                state.page.items.append(Image(left - pad, top - pad, width + 2 * pad, height + 2 * pad, href))
        picture = component.find(tags.SHAPE_COMPONENT_PICTURE)
        if picture is not None and isinstance(picture.model, ShapePicture):
            self.draw_picture(state, picture.model, left, top, width, height)
        textart = component.find(tags.SHAPE_COMPONENT_TEXTART)
        if textart is not None:
            drawing = self.textart_drawing(textart, width, height)
            if drawing is not None:
                svg = _textart_svg(drawing, component.model)
                href = f"data:image/svg+xml;base64,{base64.b64encode(svg.encode('utf-8')).decode('ascii')}"
                state.page.items.append(Image(left, top, width, height, href))
        ole = component.find(tags.SHAPE_COMPONENT_OLE)
        if ole is not None and isinstance(ole.model, ShapeOle):
            chart = self.chart_drawing(ole.model.bin_data_id, left, top, width, height)
            drawing = self.ole_drawing(ole.model.bin_data_id) if chart is None else None
            if chart is not None:
                self.draw_chart(state, chart)
            elif drawing is not None:
                self.draw_metafile(state, drawing, left, top, width, height)
                # 새 차트는 도형을 비트맵으로 찍고 기울어진 범주 이름만 글자로 찍는다. 도형은 표현 그림으로 대신한다.
                labels = self.ooxml_chart_labels(ole.model.bin_data_id, left, top, width, height)
                if labels is not None:
                    self.draw_chart(state, labels)
        text_list = component.find(tags.LIST_HEADER)
        if text_list is not None and isinstance(text_list.model, TextBoxListHeader):
            header = text_list.model
            margins = header.margins
            paragraphs = [child for child in component.children if child.tag == tags.PARA_HEADER]
            inner_width = width - margins[0] - margins[1]
            views = self.paragraph_views(paragraphs, inner_width, textbox=True)
            area = Area(left + margins[0], top + margins[2], inner_width)
            inner_height = height - margins[2] - margins[3]
            with self.painting_list(("text box", id(component))):
                if header.text_direction:
                    self.place_vertical_text(state, views, area, inner_height, header)
                else:
                    self.place_linked_text(
                        state, views, area, (inner_height, header.vert_align), inner_height + margins[3] + text_growth
                    )
        # 중첩 묶음 자식도 맨 바깥 묶음 기준이다(편람 9쪽: 세 단계 묶음 이동 행렬이 모두 25159, 정답 x 268pt).
        origin = origin or (left, top)
        for child in component.children:
            if child.tag == tags.SHAPE_COMPONENT and isinstance(child.model, ShapeComponent):
                sub = child.model
                box = _rendered_box(sub)
                if box is None:
                    box = (sub.offset_x, sub.offset_y, sub.current_width, sub.current_height)
                self.draw_component(state, child, origin[0] + box[0], origin[1] + box[1], box[2], box[3], origin)

    def place_vertical_text(
        self, state: PageState, views: list[ParagraphView], area: Area, inner_height: float, header: TextBoxListHeader
    ) -> None:
        """세로쓰기 글상자: 줄은 오른쪽부터 왼쪽으로 늘어선 열이고 vertical_pos는 오른쪽 끝에서 잰 거리다.

        세로 정렬은 열 묶음의 가로 위치다(tbox-v-flow-01 PDF: 가운데 정렬, 첫 열 오른쪽 = 안쪽 오른쪽 − 남는 폭/2).
        """
        content = max((s.vertical_pos + s.line_height for view in views for s in view.segments), default=0)
        slack = max(0.0, area.width - content)
        # 검증 안 됨: 위(0)는 오른쪽, 아래(2)는 왼쪽 끝으로 본다(실측은 가운데뿐).
        right = area.x + area.width - {1: slack / 2, 2: slack}.get(header.vert_align, 0.0)
        for view in views:
            for index, segment in enumerate(view.segments):
                column_right = right - segment.vertical_pos
                box = (column_right - segment.line_height, area.y, segment.line_height, inner_height)
                with self.painting(state.page, box, LINE_PAINT_GROW, is_line=True):
                    self.draw_vertical_line(state, view, index, column_right, area.y, header.text_direction == 1)

    def place_linked_text(
        self,
        state: PageState,
        views: list[ParagraphView],
        area: Area,
        alignment: tuple[float, int],
        visible_height: float,
    ) -> None:
        """글상자 문단을 놓는다. 되돌아간 뒤 줄은 연결된 다음 글상자로 넘어간다. alignment는 (안쪽 높이, 세로 정렬).

        위 끝이 visible_height(상자 아래 끝) 밑인 저장 줄은 찍지 않는다. 정답 PDF 실측: group-drawing-02 「id」
        1600 ≥ 1000 + 284, 편람 161쪽 「기재란」 1600 ≥ 1404 + 142는 없고, 8쪽 「의미함」 4086 < 3565 + 850은 있다.
        대체 글꼴로 다시 나눈 줄은 한글 배치가 아니라 빼지 않는다(annual_report 5쪽 「다.」는 정답에서 앞 줄 끝).

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
            # 세로 정렬은 상자 밖 줄까지 든 내용 높이로 정한다(편람 161쪽 「민원인」: 뺀 줄을 정렬에서 빼면 2pt 내려감).
            if view.segments[index].vertical_pos < visible_height or not _is_stored_line(view, index):
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
        with self.painting_list(("text box", id(area))):
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
            # 기울인 글줄(글꼴 escapement)은 그림 좌표에서 글줄 방향으로 나아간 뒤 상자 비율로 늘린다.
            cos, sin = math.cos(math.radians(text.angle)), math.sin(math.radians(text.angle))
            along = 0.0
            for index, char in enumerate(text.text):
                advance = text.advances[index] if text.advances and index < len(text.advances) else 0.0
                if char.strip():
                    state.page.items.append(
                        Glyph(
                            char,
                            left + (text.x + along * cos) * scale_x,
                            top + (text.y - along * sin) * scale_y,
                            text.size * scale_y,
                            text.face,
                            text.color,
                            text.bold,
                            text.italic,
                            advance=advance * scale_x,
                            rotation=text.angle,
                        )
                    )
                along += advance

    def metafile(self, bin_data_id: int) -> WmfDrawing | None:
        entry = self.styles.bin_data[bin_data_id - 1] if 1 <= bin_data_id <= len(self.styles.bin_data) else None
        if entry is None or (entry.extension or "").lower() != "wmf":
            return None
        cache = self.__dict__.setdefault("_metafiles", {})
        if bin_data_id not in cache:
            data = self.bin_bytes(bin_data_id)
            cache[bin_data_id] = convert_wmf(data) if data is not None else None
        return cache[bin_data_id]

    def ole_drawing(self, bin_data_id: int) -> WmfDrawing | None:
        """OLE 개체의 내용 표현 메타파일. 개체 상자에 늘려 그린다(정답 PDF 글자 위치와 일치)."""
        cache = self.__dict__.setdefault("_ole_drawings", {})
        if bin_data_id not in cache:
            data = self.bin_bytes(bin_data_id)
            metafile = content_metafile(data) if data is not None else None
            drawing = convert_wmf(metafile) if metafile is not None else None
            cache[bin_data_id] = drawing
        return cache[bin_data_id]

    def chart_drawing(
        self, bin_data_id: int, left: float, top: float, width: float, height: float
    ) -> ChartDrawing | None:
        """VtChart OLE를 개체 상자에 그린 차트. 한글은 이 차트를 표현 스트림이 아니라 플러그인이 직접 그린다

        (HwpApp FUN_10221580 → HncChartBase FUN_100ef7a0). 옮기지 않은 차트 종류면 None.
        """
        contents = self.__dict__.setdefault("_vtchart_contents", {})
        if bin_data_id not in contents:
            data = self.bin_bytes(bin_data_id)
            contents[bin_data_id] = vtchart_contents(data) if data is not None else None
        if contents[bin_data_id] is None:
            return None
        return draw_vtchart(contents[bin_data_id], left / 100, top / 100, width / 100, height / 100)

    def ooxml_chart_labels(
        self, bin_data_id: int, left: float, top: float, width: float, height: float
    ) -> ChartDrawing | None:
        """새 차트(OOXMLChartContents)가 PDF 글자로 찍는 기울어진 범주 이름. 새 차트가 아니거나 범위 밖이면 None."""
        contents = self.__dict__.setdefault("_ooxml_chart_contents", {})
        if bin_data_id not in contents:
            data = self.bin_bytes(bin_data_id)
            contents[bin_data_id] = ooxml_chart_contents(data) if data is not None else None
        if contents[bin_data_id] is None:
            return None
        return draw_ooxml_chart(contents[bin_data_id], left / 100, top / 100, width / 100, height / 100)

    def draw_chart(self, state: PageState, chart: ChartDrawing) -> None:
        for shape in chart.shapes:
            color = "#{:02x}{:02x}{:02x}".format(*shape.color)
            (x1, y1), (x2, y2) = shape.points
            if shape.kind == "line":
                # 검증 안 됨: 차트 모듈이 선 굵기를 주지 않아 장치 한 점(600DPI = 12HU)으로 그린다.
                state.page.items.append(Line(x1 * 100, y1 * 100, x2 * 100, y2 * 100, 12, color))
            else:
                state.page.items.append(Rect(x1 * 100, y1 * 100, (x2 - x1) * 100, (y2 - y1) * 100, fill=color))
        for glyph in chart.glyphs:
            state.page.items.append(
                Glyph(glyph.char, glyph.x * 100, glyph.y * 100, glyph.size * 100, glyph.font, rotation=glyph.angle)
            )

    def textart_drawing(self, node: Node, width: float, height: float) -> TextArtDrawing | None:
        """글맵시 윤곽. 글꼴 파일이 없거나 아직 옮기지 못한 모양(원·여러 줄·HFT)이면 None이고 범위 밖으로 남긴다."""
        cache = self.__dict__.setdefault("_textart_drawings", {})
        key = (id(node), round(width), round(height))
        if key not in cache:
            try:
                cache[key] = draw_textart(node.record.payload, width, height, _textart_fonts().find)
            except (TextArtFontError, NotImplementedError, ImportError):
                # 그리지 못하는 글맵시는 그 영역을 채점에서 빼는 기존 동작(범위 밖)으로 둔다.
                cache[key] = None
        return cache[key]

    def out_of_scope(self, ctrl: Node, box_size: tuple[float, float] | None = None) -> bool:
        """수식이거나 그리지 못하는 OLE·차트·글맵시를 담은 개체. 글상자 문단 안 개체는 그 글을 놓을 때 따로 판정한다."""
        kind = ctrl.record.payload[:4]
        if kind == tags.EQUATION_CTRL.to_bytes(4, "little"):
            return True
        if kind == tags.TABLE_CTRL.to_bytes(4, "little"):
            return False
        pending = list(ctrl.children)
        while pending:
            node = pending.pop()
            if node.tag == tags.SHAPE_COMPONENT_OLE:
                if not isinstance(node.model, ShapeOle):
                    return True
                component = _owner_component(ctrl, node)
                size = (component.model.current_width, component.model.current_height) if component else (0, 0)
                if self.chart_drawing(node.model.bin_data_id, 0, 0, *size) is not None:
                    continue
                # 새 차트는 한글이 OOXML로 다시 그려(HncDrawingEngine) 표현 그림과 다를 수 있어, 글자로 찍히는
                # 범주 이름을 옮긴 차트만 범위 안에 둔다. 표현 그림에 글자가 있으면 PDF에 없는 글자가 생겨 범위 밖이다.
                data = self.bin_bytes(node.model.bin_data_id)
                drawing = self.ole_drawing(node.model.bin_data_id)
                if drawing is None or data is None:
                    return True
                # 범주 이름 위치는 상자 크기에 따라 달라, 그릴 상자(box_size, HU)를 아는 맨 위 도형만 판정한다.
                if has_ooxml_chart(data) and (
                    drawing.texts
                    or box_size is None
                    or component is not ctrl.find(tags.SHAPE_COMPONENT)
                    or self.ooxml_chart_labels(node.model.bin_data_id, 0, 0, *box_size) is None
                ):
                    return True
            elif node.tag == tags.SHAPE_COMPONENT_TEXTART:
                component = _owner_component(ctrl, node)
                size = (component.model.current_width, component.model.current_height) if component else (0, 0)
                if self.textart_drawing(node, *size) is None:
                    return True
            elif node.tag in _OUT_OF_SCOPE_TAGS:
                return True
            # 글상자 문단 속 수식은 글을 놓을 때 하나씩 제외하므로 글상자 전체를 범위 밖으로 보지 않는다.
            if node.tag == tags.PARA_HEADER:
                continue
            pending.extend(node.children)
        return False

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
            png = png_from_raster(data)
            if png is not None:
                data, name = png, "converted.png"
            href = f"data:{_mime(data, name)};base64,{base64.b64encode(data).decode('ascii')}"
        self.image_cache[bin_data_id] = href
        return href


#: 1단계 범위 밖 개체: 수식, 차트 데이터를 담은 그리기 개체(OLE·글맵시는 그리지 못할 때만).
_OUT_OF_SCOPE_TAGS = frozenset({tags.CHART_DATA, tags.EQEDIT})


def _rendered_box(component: ShapeComponent) -> tuple[float, float, float, float] | None:
    """묶음 안 도형이 그려지는 상자(맨 바깥 묶음 기준 x, y, 폭, 높이). 원래 크기에 단계마다 축척 → 회전, 끝으로 이동.

    실측: kr_156489577 2쪽 목차 묶음. 자식 오프셋·현재 크기만 쓰면 +93pt, −320pt 어긋난다. 좌우 뒤집힌 글상자(회전 행렬
    −1)는 축척을 먼저 적용해야 정답 위치다(편람 4쪽 「차례」 상자 왼쪽 9640HU = 96.4pt, 회전 먼저면 −51310HU).
    """
    if component.translation is None or not component.scale_rotation:
        return None
    matrix = component.translation
    for scale, rotation in component.scale_rotation:
        matrix = _compose(_compose(matrix, rotation), scale)
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


def _is_stored_line(view: ParagraphView, index: int) -> bool:
    """줄이 LINE_SEG 레코드에 저장된 그대로인가(다시 나누거나 옮긴 줄이 아닌가)."""
    stored = view.node.find(tags.PARA_LINE_SEG)
    return stored is not None and any(segment is view.segments[index] for segment in stored.model.segments)


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


def _descendants(node: Node):
    for child in node.children:
        yield child
        yield from _descendants(child)


def _is_equation(ctrl: Node) -> bool:
    return ctrl.record.payload[:4] == tags.EQUATION_CTRL.to_bytes(4, "little")


#: equation_boxes에서 옛 엔진 수식을 모두 계산했다는 표지.
_LEGACY_DONE = "legacy-equations-measured"


def _is_legacy_equation(ctrl: Node) -> bool:
    equation = ctrl.find(tags.EQEDIT) if _is_equation(ctrl) else None
    return equation is not None and isinstance(equation.model, EqEdit) and equation.model.version != NEW_EQUATION_ENGINE


def _recomputed_equation(ctrl: Node, legacy_state: LegacyEngineState | None = None) -> tuple[int, int, int] | None:
    """HwpApp FUN_10539be0은 'eqed'마다 FUN_1026e540을 조건 없이 불러 높이·기준선을 덮어쓰고, 폭은 EQEDIT attr bit 0이
    꺼져 있을 때만 덮어쓴다(켜져 있으면 저장 폭 안에 가운데 맞춘다)."""
    equation = ctrl.find(tags.EQEDIT) if _is_equation(ctrl) else None
    if equation is None or not isinstance(equation.model, EqEdit):
        return None
    model = equation.model
    try:
        if model.version != NEW_EQUATION_ENGINE:
            width, height, baseline = measure_legacy(model.script or "", model.font_size, legacy_state)
        else:
            # 빈 글꼴 이름은 덮어쓰지 않아 개체 기본 글꼴이 남는다(FUN_10549670). 기본 글꼴 HYhwpEQ는 검증 안 됨.
            font = model.font_name or EQUATION_FONT
            if font not in glyph_table().fonts:
                return None
            width, height, baseline = measure_equation(model.script or "", model.font_size, font)
    except LegacyEquationError:
        # 옛 엔진 해석이 실패하면 FUN_1026e540이 저장 크기를 덮어쓰지 않는다(0x1026e7ef jne).
        return None
    except (KeyError, NotImplementedError):
        # 글리프 표에 없는 글자·크기와 아직 옮기지 않은 수식 객체는 저장 크기를 쓴다.
        return None
    if model.attr & 1:
        width = ctrl.model.width
    return width, height, baseline


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


#: 글맵시 글꼴을 찾을 폴더. REA_HWP_FONT_DIRS(경로 구분자로 나눔)가 없으면 Windows 글꼴과 한컴 공용 TTF 가운데 있는 것.
_DEFAULT_FONT_DIRS = (
    Path("C:/Windows/Fonts"),
    Path("/mnt/c/Windows/Fonts"),
    Path("/mnt/c/Program Files (x86)/HNC/Office 2022/HOffice120/Shared/TTF"),
)


@functools.cache
def _textart_fonts() -> FontFiles:
    configured = os.environ.get("REA_HWP_FONT_DIRS")
    directories = [Path(p) for p in configured.split(os.pathsep) if p] if configured else _DEFAULT_FONT_DIRS
    return FontFiles([d for d in directories if d.is_dir()])


def _owner_component(ctrl: Node, target: Node) -> Node | None:
    """target 레코드를 자식으로 둔 도형 요소."""
    pending = [ctrl]
    while pending:
        node = pending.pop()
        if any(child is target for child in node.children):
            return node if node.tag == tags.SHAPE_COMPONENT else None
        pending.extend(node.children)
    return None


def _colorref(value: int) -> str:
    return f"#{value & 0xFF:02x}{value >> 8 & 0xFF:02x}{value >> 16 & 0xFF:02x}"


def _textart_fill(tail: bytes) -> tuple[str, str]:
    """도형 요소 tail의 채우기 정보(명세 표28: 종류 u32, 단색 12바이트, 그러데이션)를 SVG 채우기와 정의로 바꾼다."""
    if len(tail) < 4:
        return "none", ""
    (kind,) = struct.unpack_from("<I", tail)
    offset = 4
    fill = "none"
    if kind & FILL_SOLID and len(tail) >= offset + 12:
        fill = _colorref(struct.unpack_from("<I", tail, offset)[0])
        offset += 12
    if kind & FILL_GRADIENT and len(tail) >= offset + 21:
        # 머리는 유형 1바이트 + 4바이트 필드들(docinfo._read_gradient와 같다). 색이 셋 이상이면 위치가 먼저 온다.
        _, angle, _, _, _, count = struct.unpack_from("<BiiiiI", tail, offset)
        offset += 21
        positions = list(struct.unpack_from(f"<{count}i", tail, offset)) if count > 2 else []
        offset += 4 * count if count > 2 else 0
        colors = struct.unpack_from(f"<{count}I", tail, offset) if len(tail) >= offset + 4 * count else ()
        if colors:
            # 검증 안 됨: 유형(줄무늬·원형 등)과 관계없이 각도를 준 선형 그러데이션으로 그린다(글자 위치·채점과 무관).
            stops = "".join(
                f'<stop offset="{(positions[i] / 100 if positions else i / max(len(colors) - 1, 1)):g}" '
                f'stop-color="{_colorref(color)}"/>'
                for i, color in enumerate(colors)
            )
            rotate = f"rotate({angle} 0.5 0.5)"
            definition = f'<defs><linearGradient id="g" gradientTransform="{rotate}">{stops}</linearGradient></defs>'
            return "url(#g)", definition
    return fill, ""


#: 선 종류(선 속성 하위 6비트, 0 없음·1 실선) → 점선 무늬(선 굵기 배수).
#: 검증 안 됨: 명세 선 종류 이름(파선·점선·일점쇄선·이점쇄선·긴 파선)만 보고 고른 무늬다(채점과 무관).
_DASH_PATTERNS = {2: (4, 2), 3: (1, 1), 4: (4, 2, 1, 2), 5: (4, 2, 1, 2, 1, 2), 6: (8, 2)}
#: 타원 사분원을 3차 베지어로 그릴 때 제어점 거리 비율.
_ELLIPSE_KAPPA = 0.5522847498
#: 화살표 단위 길이의 최소값(HU). HncBaseDraw FUN_100435b0: 단위 = max(0x11b, 선 굵기).
_ARROW_MIN_UNIT = 0x11B


def _shape_svg(component: Node, width: float, height: float) -> str | None:
    """사각형·타원·직선·다각형 도형의 선과 채우기를 개체 상자(폭×높이, HU) 크기 SVG로. 그릴 것이 없으면 None.

    도형 좌표는 원래 크기 기준이라 묶음 행렬(_rendered_box와 같은 순서)로 옮긴 뒤 그 외곽을 받은 상자에 맞춘다.
    호·곡선과 화살표·그림자는 옮기지 않았다.
    """
    model = component.model
    geometry = next(
        (
            child.model
            for child in component.children
            if isinstance(child.model, (ShapeRectangle, ShapeEllipse, ShapeLine, ShapePolygon))
        ),
        None,
    )
    # 뒤집혀 저장된 상자(현재 크기 음수, 온새미로 표지 글상자)는 옮기지 않았다.
    if geometry is None or width < 0 or height < 0 or (isinstance(geometry, ShapeEllipse) and geometry.is_arc):
        return None
    line_width = getattr(model, "line_width", 0) or 0
    line_kind = (getattr(model, "line_attr", 0) or 0) & 0x3F
    fill, definition = ("none", "") if isinstance(geometry, ShapeLine) else _textart_fill(model.tail)
    if (line_kind == 0 or line_width <= 0) and fill == "none":
        return None

    subpaths = _shape_subpaths(geometry)
    if not subpaths:
        return None
    matrix = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    if model.translation is not None and model.scale_rotation:
        matrix = model.translation
        for scale, rotation in model.scale_rotation:
            matrix = _compose(_compose(matrix, rotation), scale)
    corners = [_apply(matrix, x, y) for x in (0, model.original_width) for y in (0, model.original_height)]
    left, top = min(c[0] for c in corners), min(c[1] for c in corners)
    span_x, span_y = max(c[0] for c in corners) - left, max(c[1] for c in corners) - top
    scale_x = width / span_x if span_x else 1.0
    scale_y = height / span_y if span_y else 1.0

    def place(x: float, y: float) -> tuple[float, float]:
        px, py = _apply(matrix, x, y)
        return (px - left) * scale_x, (py - top) * scale_y

    def text(point: tuple[float, float]) -> str:
        return f"{point[0]:.1f} {point[1]:.1f}"

    color = _colorref(model.line_color)
    has_arrows = bool(line_kind and line_width > 0 and not isinstance(geometry, ShapeEllipse))
    commands, arrows = [], ""
    for start, segments, closed in subpaths:
        first = place(*start)
        placed = [tuple(place(*p) for p in segment) for segment in segments]
        if has_arrows and not closed:
            before_end = placed[-1][-2] if len(placed[-1]) == 3 else (placed[-2][-1] if len(placed) > 1 else first)
            for tip, before, shape_bit, size_bit in (
                (first, placed[0][0], 10, 22),
                (placed[-1][-1], before_end, 16, 26),
            ):
                svg, inset = _arrow_svg(
                    tip,
                    before,
                    (model.line_attr >> shape_bit) & 0x3F,
                    (model.line_attr >> size_bit) & 0xF,
                    line_width,
                    color,
                )
                arrows += svg
                if shape_bit == 10 and len(placed[0]) == 1:
                    first = _toward(first, before, inset)
                elif shape_bit == 16 and len(placed[-1]) == 1:
                    placed[-1] = (_toward(placed[-1][0], before, inset),)
        commands.append("M" + text(first))
        for segment in placed:
            commands.append(("C" if len(segment) == 3 else "L") + " ".join(text(p) for p in segment))
        if closed:
            commands.append("Z")
    stroke = ' stroke="none"'
    if line_kind and line_width > 0:
        stroke = f' stroke="{color}" stroke-width="{line_width}"'
        if line_kind in _DASH_PATTERNS:
            stroke += f' stroke-dasharray="{",".join(str(n * line_width) for n in _DASH_PATTERNS[line_kind])}"'
    pad = _shape_pad(model)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{-pad} {-pad} {width + 2 * pad:g} {height + 2 * pad:g}" '
        f'preserveAspectRatio="none">{definition}<path d="{"".join(commands)}" fill="{fill}"{stroke}/>{arrows}</svg>'
    )


def _shape_pad(model: object) -> float:
    """도형 SVG가 개체 상자 밖으로 그리는 폭: 선 굵기, 화살표가 있으면 가장 긴 화살표 길이까지."""
    line_width = max(getattr(model, "line_width", 0) or 0, 0)
    attr = getattr(model, "line_attr", 0) or 0
    reach = 0.0
    for shape_bit, size_bit in ((10, 22), (16, 26)):
        if (attr >> shape_bit) & 0x3F:
            length, width = _arrow_size((attr >> size_bit) & 0xF, line_width)
            reach = max(reach, length, width)
    return line_width + reach


def _arrow_size(size: int, line_width: float) -> tuple[float, float]:
    """화살표 크기 코드(명세 표 82, 0~8)의 (길이, 폭) HU. HncBaseDraw FUN_10042b90의 단위 배수다."""
    unit = max(_ARROW_MIN_UNIT, line_width)
    return (size // 3 * 2 + 2) * unit, 2 * (size % 3 + 1) * unit


def _arrow_svg(
    tip: tuple[float, float], before: tuple[float, float], shape: int, size: int, line_width: float, color: str
) -> tuple[str, float]:
    """선 끝 tip에 붙는 화살표와 선을 줄일 길이. 모양은 엔진(FUN_10042b90) case와 같은 값만 그린다.

    정답 PDF로 확인한 것: 1 채운 삼각형(group-drawing-02), 2 열린 V자(편람 116쪽), 3 오목 깊이가 길이 1/3인 채운
    화살(편람 161쪽, 상수 3.0). 1·3은 선이 화살 길이 절반만큼 짧다(밑변 들임 상수 0.5, KTX 굵은 선 11.26pt).
    명세 4~8(마름모·원·사각)은 말뭉치에 없어 엔진 대응을 확인하지 못해 그리지 않는다.
    """
    dx, dy = tip[0] - before[0], tip[1] - before[1]
    span = (dx * dx + dy * dy) ** 0.5
    if shape not in (1, 2, 3) or span == 0:
        return "", 0.0
    ux, uy = dx / span, dy / span
    length, width = _arrow_size(size, line_width)

    def point(back: float, side: float) -> str:
        return f"{tip[0] - ux * back - uy * side:.1f} {tip[1] - uy * back + ux * side:.1f}"

    wing = (point(length, width / 2), point(length, -width / 2))
    if shape == 2:
        svg = f'<path d="M{wing[0]}L{point(0, 0)}L{wing[1]}" fill="none" stroke="{color}" stroke-width="{line_width}"/>'
        return svg, 0.0
    notch = f"L{point(length * 2 / 3, 0)}" if shape == 3 else ""
    return f'<path d="M{point(0, 0)}L{wing[0]}{notch}L{wing[1]}Z" fill="{color}"/>', min(length / 2, span)


def _toward(point: tuple[float, float], target: tuple[float, float], distance: float) -> tuple[float, float]:
    dx, dy = target[0] - point[0], target[1] - point[1]
    span = (dx * dx + dy * dy) ** 0.5
    if span == 0:
        return point
    return point[0] + dx * distance / span, point[1] + dy * distance / span


Subpath = tuple[tuple[float, float], list[tuple[tuple[float, float], ...]], bool]


def _shape_subpaths(geometry: object) -> list[Subpath]:
    """도형 레코드의 윤곽(원래 좌표). 구간은 끝점 하나(직선)이거나 제어점 둘과 끝점(3차 베지어)이다."""
    if isinstance(geometry, ShapeLine):
        return [((geometry.start_x, geometry.start_y), [((geometry.end_x, geometry.end_y),)], False)]
    if isinstance(geometry, ShapePolygon):
        points = geometry.points or []
        if len(points) < 2:
            return []
        return [(points[0], [(p,) for p in points[1:]], points[0] == points[-1])]
    if isinstance(geometry, ShapeEllipse):
        cx, cy = geometry.center_x, geometry.center_y
        ax, ay = geometry.axis1_x - cx, geometry.axis1_y - cy
        bx, by = geometry.axis2_x - cx, geometry.axis2_y - cy
        k = _ELLIPSE_KAPPA
        # 0°, 90°, 180°, 270° 점을 차례로 잇는 사분원 네 개.
        quarter = [(ax, ay), (bx, by), (-ax, -ay), (-bx, -by)]
        segments = []
        for index in range(4):
            (ux, uy), (vx, vy) = quarter[index], quarter[(index + 1) % 4]
            segments.append(
                ((cx + ux + k * vx, cy + uy + k * vy), (cx + vx + k * ux, cy + vy + k * uy), (cx + vx, cy + vy))
            )
        return [((cx + ax, cy + ay), segments, True)]
    xs, ys = [p[0] for p in geometry.corner_points], [p[1] for p in geometry.corner_points]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    # 검증 안 됨: 곡률(%)은 짧은 변에 대한 모서리 반지름 비율로 본다(50%면 양 끝이 반원).
    radius = min(x1 - x0, y1 - y0) * geometry.round_ratio / 100
    if radius <= 0:
        return [(geometry.corner_points[0], [(p,) for p in geometry.corner_points[1:]], True)]
    k = radius * (1 - _ELLIPSE_KAPPA)
    segments = [
        ((x1 - radius, y0),),
        ((x1 - k, y0), (x1, y0 + k), (x1, y0 + radius)),
        ((x1, y1 - radius),),
        ((x1, y1 - k), (x1 - k, y1), (x1 - radius, y1)),
        ((x0 + radius, y1),),
        ((x0 + k, y1), (x0, y1 - k), (x0, y1 - radius)),
        ((x0, y0 + radius),),
        ((x0, y0 + k), (x0 + k, y0), (x0 + radius, y0)),
    ]
    return [((x0 + radius, y0), segments, True)]


def _textart_svg(drawing: TextArtDrawing, component: ShapeComponent) -> str:
    """그림자를 먼저, 글자 윤곽을 도형 요소의 채우기와 선으로 그린다(FUN_100fc390 → FUN_100fbbd0)."""
    fill, definition = _textart_fill(component.tail)
    line_width = getattr(component, "line_width", 0) or 0
    # 선 속성 하위 6비트는 선 종류이고 0이면 선이 없다.
    has_line = line_width > 0 and getattr(component, "line_attr", 0) & 0x3F
    stroke = f' stroke="{_colorref(component.line_color)}" stroke-width="{line_width}"' if has_line else ""
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {drawing.width:g} {drawing.height:g}" '
        f'preserveAspectRatio="none">{definition}'
    ]
    if drawing.shadow_path:
        parts.append(f'<path d="{drawing.shadow_path}" fill="{drawing.shadow_color}"/>')
    parts.append(f'<path d="{drawing.path}" fill="{fill}" fill-rule="nonzero"{stroke}/>')
    parts.append("</svg>")
    return "".join(parts)
