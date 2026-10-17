"""저장된 줄 배치(LINE_SEG)로 문단·표·개체를 쪽 위에 놓는다. 좌표는 HWPUNIT."""

from __future__ import annotations

import dataclasses
import unicodedata

from .. import tags
from ..document import HwpDocument, Node
from ..models.controls import (
    AutoNumberCtrl,
    ColumnDefCtrl,
    HeaderFooterCtrl,
    NewNumberCtrl,
    NoteCtrl,
    ObjectCtrlHeader,
    OverlapCtrl,
    PageHideCtrl,
    PageNumberPosCtrl,
    SectionDefCtrl,
    TextWrap,
    VertRelTo,
)
from ..models.docinfo import CharShape
from ..models.paragraph import PARA_BREAK, TAB, ControlChar, LineSeg
from . import heads, metrics, reflow
from .common import (
    CENTER,
    DEVICE_UNIT,
    DISTRIBUTE,
    INDENT_APPLIED,
    JUSTIFY,
    MIRRORED,
    RIGHT,
    SPLIT,
    TAC_ASCENT,
    Area,
    PageState,
    ParagraphView,
    SectionGeometry,
    SectionState,
    TextChar,
    TextMeasurer,
    _affix,
    _count_trailing_spaces,
    _is_space,
    _list_paragraphs,
    _superscript,
    column_layout,
    device_round,
    script_scale,
)
from .decor import DecorMixin
from .notes import NotesMixin
from .numbering import format_number
from .objects import ObjectsMixin
from .scene import Glyph, Page
from .styles import DocStyles, colorref
from .table_flow import TableFlowMixin

#: 글자 겹침 테두리 종류(명세 4.3.10.12): 1 원. 한글 PDF는 원을 「◯」 글자로 찍는다.
CIRCLE_BORDER = 1
CIRCLE_GLYPH = "\u25ef"
#: 한글 전용 문자 영역(보조 사용자 정의 영역 A). 글자 겹침에 쓰면 PDF에 U+F000 한 글자로 찍힌다.
PRIVATE_USE_PLANE = 0xF0000
PUA_GLYPH = "\uf000"
#: 단 종류(명세 표: 단 정의 속성 bit0-1): 1 배분 다단.
DISTRIBUTED_COLUMNS = 1
#: 탭 채움 종류(명세 표25): 3 점선.
LEADER_DOT = 3


class DocumentLayout(ObjectsMixin, TableFlowMixin, NotesMixin, DecorMixin):
    def __init__(self, document: HwpDocument) -> None:
        self.document = document
        self.styles = DocStyles.from_document(document)
        self.measurer = TextMeasurer(self.styles)
        self.pages: list[Page] = []
        self.states: list[PageState] = []
        self.page_number = 1
        self.footnote_number = 0
        self.section = SectionState()
        self.page_numbers: list[int] = []
        self.page_sections: list[SectionState] = []
        self.image_cache: dict[int, str | None] = {}
        self.endnotes: list[Node] = []
        self.note_shapes: list = []
        self.current_note_number = 0
        #: 본문 마지막 줄이 끝난 위치(쪽 상태, 단, y). 미주는 여기서 이어진다.
        self.cursor: tuple[PageState, int, float] | None = None
        #: 본문 마지막 줄이 끝난 위치에 줄 간격을 더한 곳. 다음 문단이 시작할 자리다.
        self.flow_cursor: tuple[PageState, int, float] | None = None
        #: 본문 흐름에 놓는 중이면 True(표를 쪽 단위로 나눌 수 있다). 셀·머리말·주석 안에서는 False.
        self.in_body = False
        #: 표가 쪽을 늘렸을 때 본문이 이어 쓸 마지막 쪽.
        self.current_state: PageState | None = None
        #: 쪽을 넘긴 표 문단 뒤에 놓은 줄 수.
        self.lines_after_spill = 0
        #: 마지막으로 그린 표 조각의 아래 y. table_flow가 정한다.
        self.table_bottom: float | None = None

    def run(self) -> list[Page]:
        for section in self.document.sections:
            self.section = SectionState()
            self.note_shapes = [n.model for n in section.walk() if n.tag == tags.FOOTNOTE_SHAPE]
            self.layout_section(section.nodes)
            endnote_shape = self.note_shapes[1] if len(self.note_shapes) > 1 else None
            if endnote_shape is not None and endnote_shape.placement == 1:
                self.flow_endnotes(endnote_shape)
        if self.endnotes:
            self.flow_endnotes(self.note_shapes[1] if len(self.note_shapes) > 1 else None)
        for index, state in enumerate(self.states):
            self.decorate_page(state, self.page_sections[index], self.page_numbers[index])
        self.flush_linked_text()
        return self.pages

    def new_page(self, geometry: SectionGeometry) -> PageState:
        page = Page(geometry.paper_width, geometry.paper_height, len(self.pages) + 1)
        if self.states:
            self.page_number += 1
        page_def = geometry.page_def
        base = geometry
        if page_def.gutter_type == MIRRORED and self.page_number % 2 == 0:
            # 맞쪽 편집: 짝수 쪽은 안쪽(제본) 여백이 오른쪽으로 가서 왼쪽 여백 자리에 오른쪽 여백이 온다.
            geometry = dataclasses.replace(geometry, body_left=page_def.margin_right)
        state = PageState(page, geometry, base)
        self.pages.append(page)
        self.states.append(state)
        self.page_numbers.append(self.page_number)
        self.page_sections.append(self.section)
        return state

    def layout_section(self, nodes: list[Node]) -> None:
        geometry = self.section_geometry(nodes)
        # 한 쪽 안에서 단 정의가 바뀌면 새 단 영역은 앞 영역 아래에서 구역 정의의 「단 사이 간격」만큼 띄운다
        # (한글 2022 PDF 실측: shortcut 2000HU 6곳, interview·multicolumns-widths 1134HU).
        zone_gap = _section_def(nodes).column_gap if _section_def(nodes) is not None else 0
        state: PageState | None = None
        previous_vpos: int | None = None
        #: 앞 줄(위아래 배치 개체 포함)이 차지한 아래 끝의 저장 세로 위치.
        previous_bottom = previous_line_bottom = previous_line_end = 0
        #: 본문 바닥을 넘어 이어진 빈 줄들의 첫 세로 위치. 다음 글자 줄을 밀 때 함께 민다.
        overflow_blank_vpos: int | None = None
        #: 앞 문단이 끝난 세로 위치와 문단 모양. LINE_SEG가 없는 문단은 여기에 이어 붙인다.
        flow_end, previous_shape = 0, None
        for node in nodes:
            if node.tag != tags.PARA_HEADER:
                continue
            view = ParagraphView.build(node, self.styles)
            # 구역 정의가 든 문단은 한 구역 스트림 안에 있어도 새 구역이다(issue-505-equations: 문단마다 구역 나눔).
            new_section = state is not None and _section_def([node]) is not None
            if new_section:
                geometry = self.section_geometry([node])
                zone_gap = _section_def([node]).column_gap
            column_def = _column_def(node)
            # 1단에서 단 나누기는 쪽 나누기와 같다(온새미로 1 본교재 s3 p169: 빈 줄 하나뿐인 쪽 다음 새 쪽).
            # 단 정의를 바꾸는 문단의 단 나누기는 새 단 영역만 연다(shortcut p37).
            column_break_as_page = node.model.break_type & 0x08 and len(geometry.columns) == 1 and column_def is None
            page_break = bool(node.model.break_type & 0x04 or column_break_as_page) or new_section
            if column_def is not None and state is not None and not new_section:
                geometry = dataclasses.replace(
                    geometry, columns=column_layout(column_def, geometry.body_width), column_def=column_def
                )
                if not page_break:
                    state = self.start_zone(state, geometry, view, zone_gap)
                    previous_vpos, flow_end = None, 0
            elif (
                node.model.break_type & 0x08
                and state is not None
                and state.column + 1 == len(geometry.columns) > 1
                and geometry.column_def is not None
                and geometry.column_def.kind == DISTRIBUTED_COLUMNS
                and not state.continued_by_table
            ):
                # 배분 다단의 마지막 단에서 단 나누기는 같은 단 정의로 새 단 영역을 연다(한글 2022 PDF 실측: shortcut
                # 3·6쪽). 일반 다단은 새 쪽으로 간다(3-11월_실전_통합 문항마다 단 나누기).
                state = self.start_zone(state, geometry, view, zone_gap)
                previous_vpos, flow_end = None, 0
            view_reflowed = not view.segments
            if view_reflowed:
                if state is None:
                    state = self.new_page(geometry)
                page_top = view.para_shape.space_before // 2
                forced_page = previous_vpos is not None and page_break
                start = page_top if forced_page else flow_end + reflow.paragraph_gap(previous_shape, view.para_shape)
                view = self.reflowed(view, geometry.columns[state.column][1], start)
                view = dataclasses.replace(
                    view, segments=reflow.paginate(view.segments, geometry.body_height, page_top)
                )
            spilled = False
            for index, segment in enumerate(view.segments):
                rewound = previous_vpos is not None and self.restarts_column(
                    state, geometry, (previous_vpos, previous_line_bottom, previous_bottom), segment
                )
                forced = index == 0 and previous_vpos is not None and page_break
                if (
                    state is not None
                    and index == 0
                    and not (forced or rewound or view_reflowed)
                    and segment.vertical_pos == previous_line_end
                ):
                    # 문단 위·아래 간격 없이 앞 줄 바로 다음에 저장된 문단(hwpx 변환본 온새미로 s1 p94: 311743+1500)은
                    # 한글이 인쇄할 때 간격을 넣어 다시 짠다(PDF 10pt 아래). 그만큼 뒤 줄을 내린다.
                    state.zone_top += reflow.paragraph_gap(previous_shape, view.para_shape)
                state_before = state
                if state is None:
                    state = self.new_page(geometry)
                elif rewound and not forced and index == 0 and segment.vertical_pos > 0 and _anchored_floats(view):
                    state = self.place_floats_before_page(state, geometry, view)
                    if state is None:
                        state = self.next_column(
                            state_before, geometry, segment, forced, (previous_line_bottom, flow_end)
                        )
                elif forced or rewound:
                    state = self.next_column(
                        state,
                        geometry,
                        segment,
                        forced,
                        (previous_line_bottom, flow_end),
                        first_line=index == 0,
                    )
                elif (
                    state.continued_by_table
                    and index == 0
                    and previous_line_end + reflow.paragraph_gap(previous_shape, view.para_shape)
                    < segment.vertical_pos
                    < previous_bottom
                ):
                    # 표가 넘긴 쪽에서 되감기 없이 이어지는 문단이라도 앞 줄 다음 자리(문단 간격 포함)를 건너뛰어 앞 표
                    # 아래보다 위에 저장됐으면 이미 그 쪽 기준이다(kps-ai p25: 7128에서 59320 높이 목차 표 다음 37892).
                    # 다음 되감기는 진짜 새 쪽이다. 앞 줄 바로 다음이면(task1725, hwp_table_test p21) 표가 넘기 전
                    # 쪽 기준이라 표시를 남긴다.
                    state.continued_by_table = False
                elif state.pushed_bottom is not None and segment.vertical_pos < previous_vpos:
                    # 밀려 온 줄 뒤에 앞 줄보다 위로 저장된 줄은 밀린 줄 바로 아래에 잇는다(hwp3-sample16-hwp5-2022
                    # p161: 72568 줄을 민 뒤 72308 빈 줄, 그 다음 문단 첫 줄이 PDF에서 두 줄 아래 29.5pt).
                    state.zone_top = state.pushed_bottom - segment.vertical_pos
                elif not view_reflowed and self.overflows_page(state, geometry, view, index):
                    # 바로 앞에 바닥을 넘은 빈 줄이 이어 있으면 그 줄부터 민다(온새미로 hwpx 10·44쪽: 빈 줄 하나만큼
                    # PDF 첫 줄이 18pt 아래). 빈 줄 뒤가 되감기면 밀지 않는다(kr_156369238 p268).
                    start = overflow_blank_vpos if overflow_blank_vpos is not None else segment.vertical_pos
                    state = self.new_page(geometry)
                    state.zone_top = state.zone_bottom = -start
                    state.pushed_bottom = 0.0
                top = state.zone_top + segment.vertical_pos
                below_bottom = (
                    len(geometry.columns) == 1 and top > 0 and top + segment.line_height > geometry.body_height
                )
                if below_bottom and not _line_has_ink(view, index):
                    overflow_blank_vpos = segment.vertical_pos if overflow_blank_vpos is None else overflow_blank_vpos
                else:
                    overflow_blank_vpos = None
                preplaced = self.__dict__.get("_preplaced", ())
                if (
                    index == 0
                    and not view_reflowed
                    and _floats_overlap_first_line(view, geometry.body_height)
                    and not any(id(ctrl) in preplaced for _, ctrl in _anchored_floats(view))
                ):
                    state = self.place_floats_first(state, geometry, view, segment)
                previous_vpos = segment.vertical_pos
                previous_line_bottom = previous_bottom = segment.vertical_pos + segment.line_height
                previous_line_end = previous_line_bottom + segment.line_spacing
                column_x, column_width = geometry.columns[state.column]
                area = Area(state.geometry.body_left + column_x, geometry.body_top + state.zone_top, column_width)
                line_view, grown = view, None if view_reflowed else self.head_sized_line(view, index)
                if grown is not None:
                    line_view = dataclasses.replace(
                        view, segments=[*view.segments[:index], grown, *view.segments[index + 1 :]]
                    )
                self.current_state = None
                self.in_body = True
                try:
                    self.place_line(state, line_view, index, area)
                finally:
                    self.in_body = False
                line_bottom = state.zone_top + segment.vertical_pos + segment.line_height
                if grown is not None:
                    # 늘어난 줄만큼 뒤 줄을 내린다.
                    line_bottom += grown.line_height - segment.line_height
                    state.zone_top += (
                        grown.line_height + grown.line_spacing - segment.line_height - segment.line_spacing
                    )
                self.lines_after_spill += 1
                if self.current_state is not None and self.current_state is not state:
                    self.lines_after_spill = 0
                    spilled = True
                    state = self.current_state
                    if self.table_bottom is not None:
                        line_bottom = self.table_bottom - geometry.body_top
                state.zone_bottom = max(state.zone_bottom, line_bottom)
                if state.pushed_bottom is not None:
                    state.pushed_bottom = max(state.pushed_bottom, line_bottom + segment.line_spacing)
                self.cursor = (state, state.column, geometry.body_top + line_bottom)
                self.flow_cursor = (state, state.column, geometry.body_top + line_bottom + segment.line_spacing)
            flow_end = reflow.flow_end(view)
            previous_bottom = max(previous_bottom, flow_end)
            if spilled:
                # 쪽을 넘긴 표 뒤는 표 마지막 조각 아래에서 이어진다. 그 위치를 모르면 그 쪽 맨 위에서 잇는다.
                flow_end = round(self.table_bottom - geometry.body_top) if self.table_bottom is not None else 0
            previous_shape = view.para_shape

    def place_floats_before_page(
        self, state: PageState, geometry: SectionGeometry, view: ParagraphView
    ) -> PageState | None:
        """줄은 다음 쪽으로 넘어갔지만 문단 기준 자리차지 개체는 앞 문단 바로 아래(앞 쪽)에서 시작한다.

        한글 2022 PDF pr-1674: s0 p27 줄은 3쪽 26488로 되감기지만 표1은 2쪽 p26 바로 아래 509.32pt에서 시작해 3쪽으로
        넘어가고, 표2(나누지 않음)는 4쪽에 놓이며 본문은 3쪽에서 잇는다. 개체를 앞 쪽에 놓고 그 다음 쪽을 돌려준다.
        앞 쪽 다음 쪽이 생기지 않으면 None(평소처럼 되감기).
        """
        if self.flow_cursor is None or self.flow_cursor[0] is not state:
            return None
        column_x, column_width = geometry.columns[state.column]
        area = Area(state.geometry.body_left + column_x, geometry.body_top + state.zone_top, column_width)
        placed = self.__dict__.setdefault("_preplaced", set())
        # 개체의 문단 기준 위치는 저장된 첫 줄(다음 쪽)이 아니라 앞 문단이 끝난 자리다.
        first = dataclasses.replace(view.segments[0], vertical_pos=round(self.flow_cursor[2] - area.y))
        view = dataclasses.replace(view, segments=[first, *view.segments[1:]])
        self.in_body = True
        try:
            for char_index, ctrl in _anchored_floats(view):
                self.place_control(state, view, ctrl, area.x, area.y, area.y, area, char_index)
                placed.add(id(ctrl))
        finally:
            self.in_body = False
        following = self.states.index(state) + 1
        if following >= len(self.states):
            return None
        state = self.states[following]
        state.continued_by_table = False
        return state

    def place_floats_first(
        self, state: PageState, geometry: SectionGeometry, view: ParagraphView, segment: LineSeg
    ) -> PageState:
        """첫 줄에 보이는 내용이 있는데 저장 위치가 떠 있는 표들과 겹치면, 표들을 먼저 차례로 쌓고 줄은 그 아래에 둔다.

        한글 2022 PDF issue6946 3구역 p0: 떠 있는 표 둘(67751, 67090)과 글자처럼 취급한 표(69464) 한 줄이 세로 위치 0에
        저장돼 있지만, 정답은 7쪽·8쪽에 떠 있는 표, 9쪽에 그 줄을 놓는다. 줄이 들어가지 않으면 다음 쪽 맨 위로 간다.
        """
        column_x, column_width = geometry.columns[state.column]
        area = Area(state.geometry.body_left + column_x, geometry.body_top + state.zone_top, column_width)
        start = area.y + segment.vertical_pos
        if self.flow_cursor is not None and self.flow_cursor[0] is state:
            start = max(start, self.flow_cursor[2])
        first = dataclasses.replace(segment, vertical_pos=round(start - area.y))
        anchor_view = dataclasses.replace(view, segments=[first, *view.segments[1:]])
        placed = self.__dict__.setdefault("_preplaced", set())
        bottom, last = start, state
        self.in_body = True
        try:
            for char_index, ctrl in _anchored_floats(view):
                self.current_state, self.table_bottom = None, None
                self.place_control(last, anchor_view, ctrl, area.x, area.y, area.y, area, char_index)
                placed.add(id(ctrl))
                if self.current_state is not None:
                    last = self.current_state
                if self.table_bottom is not None:
                    bottom = self.table_bottom
        finally:
            self.in_body = False
            self.current_state = None
        last.continued_by_table = False
        if bottom + segment.line_height > geometry.body_top + geometry.body_height:
            last = self.new_page(geometry)
            last.zone_top = last.zone_bottom = -segment.vertical_pos
        else:
            last.zone_top = last.zone_bottom = bottom - geometry.body_top - segment.vertical_pos
        return last

    def next_column(
        self,
        state: PageState,
        geometry: SectionGeometry,
        segment: LineSeg,
        forced: bool,
        previous_end: tuple[int, int],
        first_line: bool = False,
    ) -> PageState:
        """쪽 나눔이나 저장 세로 위치 되감기에서 다음 단(마지막 단이면 새 쪽)으로 간다.

        previous_end는 앞 줄 아래와 앞 문단이 끝난 세로 위치다.
        """
        previous_line_bottom, flow_end = previous_end
        # 표가 넘긴 쪽은 되감기만 이어 받는다. 쪽 나눔 문단은 그 다음 쪽으로 간다(kr_156489570·issue1921·kps-ai: 한글도
        # 표 나머지 쪽 다음에 쪽 나눔 문단을 둔다). 우리만 표를 넘긴 문서(pic-in-head-01 p29, KTX)는 쪽이 하나 는다.
        if state.continued_by_table and not forced:
            state.continued_by_table = False
            # 새 문단 첫 줄이 표 나머지 아래에 각주 자리를 빼고 들어가지 않으면 다음 쪽으로 간다(중간진도보고서 79쪽:
            # 표 아래 각주 107~111, 정답은 80쪽 맨 위). 들어가면 저장 위치를 그대로 쓴다(issue4514: 표 아래로 옮기면
            # 뒤 줄이 밀려 쪽이 는다). 문단 안 둘째 줄(passport p0[1])에는 적용하지 않는다.
            remainder = (self.table_bottom - geometry.body_top) if self.table_bottom is not None else 0.0
            # 표 문단과 이 문단 사이에 저장상 표와 같은 쪽 문단이 있었으면 한글은 표를 앞 쪽에 다 놓은 것이다(편람 s4
            # p60 표 뒤 p61·p62가 55556, 정답 160쪽에 표 전부). 그때는 이 쪽을 이어 쓴다.
            if first_line and not self.lines_after_spill and segment.vertical_pos < remainder:
                room = geometry.body_height - self.footnote_reserve(state)
                if remainder + segment.line_height > room:
                    return self.new_page(geometry)
        elif not forced and state.pushed_bottom is not None:
            # 밀려 온 줄 다음 쪽 첫 줄. 저장 배치가 밀린 줄 자리를 이미 비워 두었으면 그대로(issue2559 p48: 56840 다음
            # 1944, hwp3-sample16-hwp5-2010: 73116 다음 3152), 아니면 밀린 줄 아래에서 잇는다(…-2022: 72568 다음 852).
            # 구역 누적 좌표(본문 높이를 넘는 값)면 그 줄이 쪽 맨 위다(온새미로 hwpx 46쪽: 418350 줄을 민 뒤 353550).
            vpos = segment.vertical_pos
            page_relative = vpos if vpos + segment.line_height <= geometry.body_height else 0
            base = state.pushed_bottom if page_relative < state.pushed_bottom else 0.0
            state.zone_top = base - (vpos - page_relative)
            state.pushed_bottom = None
        elif not forced and state.column + 1 < len(geometry.columns):
            state.column += 1
        else:
            state = self.new_page(geometry)
            if not forced and segment.vertical_pos + segment.line_height > geometry.body_height:
                # 구역 누적 좌표: 되돌아간 줄 위치를 새 쪽 맨 위로 삼는다.
                state.zone_top = state.zone_bottom = -segment.vertical_pos
            if forced and previous_line_bottom <= segment.vertical_pos <= flow_end:
                # 쪽 나눔 문단인데 세로 위치가 앞 문단 끝에서 이어 저장된 경우(issue3587 p12: 69953+1600,
                # rnote__labnote-001 p12: 앞 문단 개체 아래). 이 줄을 새 쪽 맨 위로 삼는다. 새 쪽 기준 값
                # (footnote-01 p38: 앞 문단 끝 7680 다음 15000)은 그대로 둔다.
                state.zone_top = state.zone_bottom = -segment.vertical_pos
        return state

    def head_sized_line(self, view: ParagraphView, index: int) -> LineSeg | None:
        """문단 번호 글자가 저장 줄 글자 높이보다 크면 그 크기로 다시 잰 첫 줄. 아니면 None.

        hwpx 변환본은 번호 크기를 빼고 줄 높이를 저장했다(온새미로 hwpx s2 p150: 12pt 「01」인데 1000/800, 같은 원문
        hwp판은 1200/960이고 PDF도 그만큼 아래).
        """
        if index != 0:
            return None
        head = self.paragraph_head(view)
        segment = view.segments[0]
        if head is None or head.shape.base_size <= segment.text_height:
            return None
        grow = head.shape.base_size - segment.text_height
        line_height = segment.line_height + grow
        return dataclasses.replace(
            segment,
            line_height=line_height,
            text_height=head.shape.base_size,
            baseline_gap=round(line_height * reflow.BASELINE_RATIO),
            line_spacing=reflow.line_gap(view.para_shape, head.shape.base_size),
        )

    def footnote_reserve(self, state: PageState) -> float:
        """그 쪽에 지금까지 모인 각주가 본문 바닥에서 차지할 높이(구분선 여백 포함, notes.draw_footnotes와 같은 식)."""
        if not state.footnotes:
            return 0.0
        shape = self.footnote_shape_of(state.footnotes[0])
        heights = [
            max(
                (v.content_height for v in self.paragraph_views(_list_paragraphs(ctrl), state.geometry.body_width)),
                default=0,
            )
            for ctrl in state.footnotes
        ]
        spacing = shape.note_spacing if shape is not None else 0
        margins = (shape.divider_margin_top + shape.divider_margin_bottom) if shape is not None else 0
        return sum(heights) + spacing * (len(heights) - 1) + margins

    def overflows_page(self, state: PageState, geometry: SectionGeometry, view: ParagraphView, index: int) -> bool:
        """저장 위치로는 본문 바닥을 넘는 글자 줄인가. 한글은 인쇄할 때 이 줄을 다음 쪽 맨 위로 민다.

        hwp3-sample16-hwp5-2022 6쪽: 72568+1300 > 72844인 줄이 PDF에서는 7쪽 첫 줄이다(같은 원문 2018 판은 저장 단계에서
        이미 넘기지 않았다). 빈 줄은 밀지 않는다(kr_156369238 p268: 바닥을 넘는 빈 줄 뒤 다음 문단이 새 쪽 0에서 시작).
        """
        segment = view.segments[index]
        top = state.zone_top + segment.vertical_pos
        if len(geometry.columns) > 1 or top <= 0 or top + segment.line_height <= geometry.body_height:
            return False
        return _line_has_ink(view, index)

    def restarts_column(
        self, state: PageState | None, geometry: SectionGeometry, previous: tuple[int, int, int], segment: LineSeg
    ) -> bool:
        """저장 세로 위치가 앞 줄보다 되돌아간 이 줄이 다음 단·쪽 맨 위의 줄인가.

        previous는 앞 줄의 (세로 위치, 줄 아래, 위아래 배치 개체까지 포함한 문단 아래)다.
        """
        previous_vpos, previous_line_bottom, previous_bottom = previous
        vpos = segment.vertical_pos
        room = geometry.body_height - (max(state.zone_top, 0) if state is not None else 0)
        if vpos < previous_vpos:
            # 본문 바닥 근처 빈 줄이나 음수 줄 간격 뒤 줄은 앞 줄보다 조금 위로 저장된다(kr_156369238 p269: 73519 다음
            # 73019, 편람 p847). 새 단 맨 위 줄은 바닥을 넘을 수 없고, 바닥을 넘은 앞 줄과 겹칠 수 없다
            # (hwp3-sample16-hwp5-2022 p161: 72568 줄이 바닥을 넘은 뒤 72308).
            if vpos == 0:
                return True
            if vpos + segment.line_height > room and previous_vpos - vpos > previous_line_bottom - previous_vpos:
                # 구역 누적 좌표로 저장된 문서(hwpx 변환)는 새 쪽 첫 줄도 본문 높이보다 큰 값으로 되돌아간다
                # (온새미로 hwpx s1 p186: 498535 다음 432959). 앞 줄 높이보다 크게 되돌아가면 바닥 근처 겹침이 아니다.
                return True
            overlaps_overflow = previous_line_bottom > room and vpos > 2 * previous_vpos - previous_line_bottom
            return vpos + segment.line_height <= room and not overlaps_overflow
        if vpos != 0 or previous_vpos != 0:
            return False
        # 앞 줄과 같이 0인 줄: 다단에서는 단마다 첫 줄이 0이다(multicolumns-layout 3단). 1단에서는 앞 줄 아래에
        # 이 줄이 들어가지 않을 때만 새 쪽이다(tac-img-02·passport: 쪽 높이 개체 다음 줄). 들어가면 겹쳐 놓는다
        # (table-ipc 어울림 개체 옆 빈 줄, 음수 줄 간격).
        return len(geometry.columns) > 1 or previous_bottom + segment.line_height > room

    def start_zone(self, state: PageState, geometry: SectionGeometry, view: ParagraphView, gap: float) -> PageState:
        """단 정의가 바뀐 문단에서 새 단 영역을 앞 영역 아래에 연다. 첫 줄이 들어가지 않으면 새 쪽에서 연다."""
        top = state.zone_bottom + gap if state.zone_bottom > 0 else 0.0
        first = view.segments[0] if view.segments else None
        if first is not None and top > 0 and top + first.vertical_pos + first.line_height > geometry.body_height:
            return self.new_page(geometry)
        state.geometry = dataclasses.replace(state.geometry, columns=geometry.columns, column_def=geometry.column_def)
        state.base_geometry = dataclasses.replace(
            state.base_geometry, columns=geometry.columns, column_def=geometry.column_def
        )
        state.column = 0
        state.zone_top = state.zone_bottom = top
        return state

    def section_geometry(self, nodes: list[Node]) -> SectionGeometry:
        page_def, column_def = None, None
        for node in nodes:
            for child in node.children:
                if child.tag != tags.CTRL_HEADER:
                    continue
                found = child.find(tags.PAGE_DEF)
                if found is not None and page_def is None:
                    page_def = found.model
                if isinstance(child.model, ColumnDefCtrl) and column_def is None:
                    column_def = child.model
            if page_def is not None:
                break
        if page_def is None:
            raise ValueError("section has no PAGE_DEF; cannot determine page size")
        return SectionGeometry.from_page_def(page_def, column_def)

    def place_paragraphs(self, state: PageState, paragraphs: list[Node], area: Area) -> None:
        for view in self.paragraph_views(paragraphs, area.width):
            for index in range(len(view.segments)):
                self.place_line(state, view, index, area)

    def paragraph_views(self, paragraphs: list[Node], width: float) -> list[ParagraphView]:
        """문단 목록(셀·글상자·머리말·주석)의 보기. LINE_SEG가 없는 문단은 앞 문단 끝에 이어 줄을 만든다."""
        views: list[ParagraphView] = []
        for node in paragraphs:
            view = ParagraphView.build(node, self.styles)
            if not view.segments:
                previous = views[-1] if views else None
                start = reflow.flow_end(previous) if previous else 0
                start += reflow.paragraph_gap(previous.para_shape if previous else None, view.para_shape)
                view = self.reflowed(view, width, start)
            views.append(view)
        return views

    def reflowed(self, view: ParagraphView, width: float, start: int) -> ParagraphView:
        """LINE_SEG가 없는 문단에 폭 width로 나눈 합성 줄을 붙인다."""
        boxes = reflow.measure_items(
            view, lambda index: self.item_width(view, index), self.styles.char_shapes, self.measurer.slot
        )
        empty_height = reflow.paragraph_font_height(view, self.styles.char_shapes)
        segments = reflow.compose(view.chars, view.positions, boxes, view.para_shape, int(width), empty_height, start)
        return dataclasses.replace(view, segments=segments)

    def place_line(self, state: PageState, view: ParagraphView, index: int, area: Area) -> None:
        segment = view.segments[index]
        first, last = view.line_range(index)
        items = list(range(first, last))
        forced_break = False
        while items and isinstance(view.chars[items[-1]], TextChar) and view.chars[items[-1]].char in ("\r", "\n"):
            forced_break = forced_break or view.chars[items[-1]].char == "\n"
            items.pop()
        widths = [self.item_width(view, i) for i in items]
        x_start = area.x + segment.column_start
        available = segment.segment_width
        if segment.flags & INDENT_APPLIED:
            # 여백·들여쓰기는 2배로 저장된다. 들여쓰기는 첫 줄, 내어쓰기는 나머지 줄에 이 비트가 켜진다.
            indent = abs(view.para_shape.indent) // 2
            x_start += indent
            available -= indent
        head = self.paragraph_head(view)
        if head is not None:
            shift = head.first if index == 0 else head.rest
            x_start += shift
            available -= shift
        chars = [view.chars[i] for i in items]
        natural = sum(widths)
        trailing_count = _count_trailing_spaces(chars)
        trailing = sum(widths[len(widths) - trailing_count :]) if trailing_count else 0
        is_last_line = index == len(view.segments) - 1
        alignment = view.para_shape.alignment
        slack = available - (natural - trailing)
        interior_end = len(chars) - trailing_count
        kinds = [
            (metrics.SPACE if _is_space(c) else metrics.GLYPH)
            if isinstance(c, TextChar)
            else (metrics.TAB if c.code == TAB else metrics.FIXED)
            for c in chars[:interior_end]
        ]
        stretch = alignment in (JUSTIFY, SPLIT) and not is_last_line and not forced_break
        distribute = alignment == DISTRIBUTE
        extras = metrics.justify_offsets(widths[:interior_end], kinds, slack, stretch, distribute)
        extras += [0.0] * trailing_count
        centered = alignment == CENTER or (distribute and interior_end <= 1)
        offset = max(0.0, slack) / 2 if centered else (max(0.0, slack) if alignment == RIGHT else 0.0)
        x = x_start + offset
        line_top = area.y + segment.vertical_pos
        # 기준선 간격이 0으로 저장된 줄(미주 첫 줄 등)은 글자 높이의 0.85를 쓴다(rhwp paragraph_layout.rs와 같은 보정).
        # 글자 높이의 정확히 절반으로 저장된 기준선(다른 프로그램이 만든 LINE_SEG)도 한글은 0.85로 다시 잡는다
        # (hwp-multi-001 1쪽 「총투자」 셀: 저장 500/1000, 정답 PDF 853; kr_156489577 14쪽 셀 550/1100 → 정답 약 935).
        stored = segment.baseline_gap
        half = stored > 0 and stored * 2 == segment.text_height
        baseline_gap = stored if stored > 0 and not half else round(segment.text_height * TAC_ASCENT)
        baseline = line_top + baseline_gap
        if head is not None and index == 0:
            self.place_text(state, head.text, head.shape, x_start + head.marker_dx, baseline)
        for order, (char_index, width) in enumerate(zip(items, widths, strict=True)):
            item = view.chars[char_index]
            if isinstance(item, TextChar):
                if item.char.strip():
                    shape = self.styles.char_shapes[item.shape_id]
                    slot = self.measurer.slot(item.char, shape)
                    self.append_glyph(state, item.char, shape, slot, device_round(x), baseline)
            elif char_index in view.controls:
                self.place_control(state, view, view.controls[char_index], x, line_top, baseline, area, char_index)
            elif item.code == TAB and tab_fill(item) == LEADER_DOT:
                shape = self.styles.char_shapes[view.shape_ids[char_index]]
                self.place_leader(state, shape, x, width + extras[order], baseline)
            x += width + extras[order]

    def place_leader(self, state: PageState, shape: CharShape, x: float, width: float, baseline: float) -> None:
        """점선 채움 탭을 「·」 글자로 채운다. 글자 크기의 1/4 간격으로 셀 수 있는 만큼에서 둘을 빼고,
        탭 시작 한 칸 뒤부터 탭 끝 두 칸 앞까지 고르게 놓는다(편람 4쪽 12pt·kps-ai 2쪽 13pt PDF 실측)."""
        pitch = round(shape.base_size * shape.rel_sizes[metrics.HANGUL] / 100) // 4
        count = int(width // pitch) - 2 if pitch > 0 else 0
        if count < 1:
            return
        step = (width - 3 * pitch) / (count - 1) if count > 1 else 0.0
        for k in range(count):
            self.append_glyph(state, "·", shape, metrics.HANGUL, device_round(x + pitch + k * step), baseline)

    def paragraph_head(self, view: ParagraphView) -> heads.HeadGeometry | None:
        """문단 머리 영역. 번호는 문단을 처음 놓을 때 한 번만 센다(문단 노드별로 기억한다)."""
        if not view.para_shape.head_type:
            return None
        cache = self.__dict__.setdefault("head_cache", {})
        key = id(view.node)
        if key not in cache:
            cache[key] = self.measure_head(view)
        return cache[key]

    def measure_head(self, view: ParagraphView) -> heads.HeadGeometry | None:
        para_shape = view.para_shape
        if para_shape.head_type == heads.HEAD_BULLET:
            spec = heads.bullet_spec(self.styles, para_shape)
        else:
            if "numbering_counter" not in self.__dict__:
                self.numbering_counter = heads.NumberingCounter()
                self.outline_ids = heads.outline_numbering_ids(self.document)
            section = next((i for i, s in enumerate(self.document.sections) if view.node in s.nodes), 0)
            outline_id = self.outline_ids[section] if section < len(self.outline_ids) else 1
            spec = heads.numbering_spec(self.styles, para_shape, outline_id, self.numbering_counter)
        if spec is None:
            return None
        first_shape = view.shape_ids[0] if view.shape_ids else 0
        shape = self.styles.char_shapes[heads.head_shape_id(spec, first_shape, self.styles)]
        if spec.bullet and not spec.text.isascii():
            # 한/글은 ASCII가 아닌 글머리표 폭을 글자 크기 1em으로 계산한다(rhwp list_marker_geometry_for).
            glyph_width = float(shape.base_size * len(spec.text))
        else:
            glyph_width = self.text_width("-" if spec.text == "\u00ad" else spec.text, shape)
        return heads.head_geometry(spec, shape, glyph_width, para_shape.indent / 2)

    def item_width(self, view: ParagraphView, char_index: int) -> float:
        item = view.chars[char_index]
        if isinstance(item, ControlChar):
            if item.code == TAB:
                return item.tab_width
            ctrl = view.controls.get(char_index)
            if ctrl is not None and isinstance(ctrl.model, AutoNumberCtrl):
                text, shape = self.auto_number(view, char_index, ctrl.model)
                return self.text_width(text, shape)
            if ctrl is not None and isinstance(ctrl.model, NoteCtrl):
                text, shape = self.note_mark(view, char_index, ctrl.model)
                return self.text_width(text, shape)
            if ctrl is not None and isinstance(ctrl.model, OverlapCtrl):
                # 글자 겹침은 겹친 글자 중 가장 넓은 글자 폭을 차지한다(수출입 현황 3쪽 PDF: 17pt 네모 숫자 한 칸 17pt).
                shape = self.styles.char_shapes[view.shape_ids[char_index]]
                return max((self.text_width(char, shape) for char in ctrl.model.text or ""), default=0)
            if ctrl is not None and isinstance(ctrl.model, ObjectCtrlHeader) and ctrl.model.treat_as_char:
                margins = ctrl.model.outer_margins or [0, 0, 0, 0]
                return ctrl.model.width + margins[0] + margins[1]
            return 0
        if item.char in ("\r", "\n") or ord(item.char) == PARA_BREAK:
            return 0
        shape = self.styles.char_shapes[item.shape_id]
        slot = self.measurer.slot(item.char, shape)
        return self.measurer.advance(item.char, shape, slot)

    def append_glyph(self, state: PageState, char: str, shape: CharShape, slot: int, x: float, baseline: float) -> None:
        """그림자가 있으면 그림자 글자를 본 글자보다 먼저(아래에) 넣는다."""
        glyph = self.make_glyph(char, shape, slot, x, baseline)
        if shape.shadow_type:
            # 그림자 간격은 글자 크기의 %, 양수는 오른쪽·아래(tac-img-02 1쪽 PDF: 28pt·8% → 2.28pt).
            dx = device_round(shape.base_size * shape.shadow_gap_x / 100)
            dy = device_round(shape.base_size * shape.shadow_gap_y / 100)
            color = colorref(shape.shadow_color) or "#000000"
            # 연속 그림자(2)는 0부터 간격까지 12HU 한 칸씩 겹쳐 찍는다(같은 PDF 20벌 실측). 비연속(1)은 한 벌.
            steps = max(abs(dx), abs(dy)) // DEVICE_UNIT if shape.shadow_type == 2 else 1
            for step in range(steps + 1 if shape.shadow_type == 2 else 1):
                ratio = step / steps if shape.shadow_type == 2 and steps else 1.0
                state.page.items.append(
                    dataclasses.replace(glyph, x=glyph.x + dx * ratio, y=glyph.y + dy * ratio, color=color)
                )
        state.page.items.append(glyph)

    def make_glyph(self, char: str, shape: CharShape, slot: int, x: float, baseline: float) -> Glyph:
        size = shape.base_size * shape.rel_sizes[slot] / 100
        y = baseline + shape.base_size * shape.offsets[slot] / 100
        scale = script_scale(shape)
        if shape.superscript:
            size *= scale
            y -= (1 - scale) * TAC_ASCENT * shape.base_size
        elif shape.subscript:
            size *= scale
            y += shape.base_size * 0.15
        return Glyph(
            char,
            x,
            y,
            size,
            self.styles.face_name(shape, slot),
            colorref(shape.text_color) or "#000000",
            shape.bold,
            shape.italic,
            shape.ratios[slot] / 100,
        )

    def place_text(self, state: PageState, text: str, shape: CharShape, x: float, baseline: float) -> float:
        """번호 같은 생성 글자를 놓고 끝 x를 돌려준다."""
        for char in text:
            slot = self.measurer.slot(char, shape)
            if char.strip():
                self.append_glyph(state, char, shape, slot, device_round(x), baseline)
            x += self.measurer.advance(char, shape, slot)
        return x

    def text_width(self, text: str, shape: CharShape) -> float:
        return sum(self.measurer.advance(char, shape, self.measurer.slot(char, shape)) for char in text)

    def place_control(
        self,
        state: PageState,
        view: ParagraphView,
        ctrl: Node,
        x: float,
        line_top: float,
        baseline: float,
        area: Area,
        char_index: int,
    ) -> None:
        model = ctrl.model
        if id(ctrl) in self.__dict__.get("_preplaced", ()):
            return
        if isinstance(model, ObjectCtrlHeader):
            in_body = self.in_body
            # 종이·쪽 기준으로 놓은 개체는 본문 흐름을 따라 쪽을 넘지 않는다(21_언어_기출 p206: 종이 아래 기준 표가
            # 본문 바닥 아래에 있어도 한글 PDF는 같은 쪽에 그린다).
            self.in_body = in_body and (model.treat_as_char or model.vert_rel_to == VertRelTo.PARA)
            try:
                self.place_object(state, view, ctrl, x, baseline, area)
            finally:
                self.in_body = in_body
        elif isinstance(model, HeaderFooterCtrl):
            target = (
                self.section.headers
                if ctrl.record.payload[:4] == tags.HEADER_CTRL.to_bytes(4, "little")
                else self.section.footers
            )
            target[model.apply_page] = ctrl
        elif isinstance(model, PageNumberPosCtrl):
            self.section.page_number_pos = model
        elif isinstance(model, NewNumberCtrl):
            if model.kind == 0:
                self.page_number = model.number
                self.page_numbers[-1] = self.page_number
        elif isinstance(model, PageHideCtrl):
            state.hide |= model.attr
        elif isinstance(model, NoteCtrl):
            is_footnote = ctrl.record.payload[:4] == tags.FOOTNOTE_CTRL.to_bytes(4, "little")
            (state.footnotes if is_footnote else self.endnotes).append(ctrl)
            text, shape = self.note_mark(view, char_index, model)
            self.place_text(state, text, shape, x, baseline)
        elif isinstance(model, AutoNumberCtrl):
            text, shape = self.auto_number(view, char_index, model)
            self.place_text(state, text, shape, x, baseline)
        elif isinstance(model, OverlapCtrl):
            self.place_overlap(state, model, self.styles.char_shapes[view.shape_ids[char_index]], x, baseline)

    def place_overlap(self, state: PageState, model: OverlapCtrl, shape: CharShape, x: float, baseline: float) -> None:
        """글자 겹침. 한글 2022 PDF 실측(글자겹침 견본·kr_156369074·수출입 현황·table-vpos-01·k-water-rfp).

        테두리 없음: 겹칠 글자를 모두 같은 x에 겹쳐 찍는다. 원 테두리: 「◯」를 찍고 그 안에 원문자를 푼 글자를
        (100+10×내부 크기)% 크기로 가운데 맞춰 (크기-내부 크기)×0.4만큼 올려 찍는다. 한글 전용 문자(PUA)는
        테두리 없으면 U+F000 한 글자, 테두리가 있으면 글자를 찍지 않는다. 다른 테두리는 실측이 없어 찍지 않는다.
        """
        text = model.text or ""
        border = model.values.get("border_type") or 0
        if any(ord(char) >= PRIVATE_USE_PLANE for char in text):
            if border == 0:
                self.append_glyph(state, PUA_GLYPH, shape, metrics.HANGUL, device_round(x), baseline)
            return
        if border == 0:
            for char in text:
                if char.strip():
                    self.append_glyph(state, char, shape, self.measurer.slot(char, shape), device_round(x), baseline)
            return
        if border != CIRCLE_BORDER:
            return
        self.append_glyph(state, CIRCLE_GLYPH, shape, metrics.HANGUL, device_round(x), baseline)
        inner = type(shape)(values=dict(shape.values), tail=shape.tail)
        inner.values["base_size"] = round(
            shape.base_size * (100 + 10 * (model.values.get("inner_char_size") or 0)) / 100
        )
        raised = baseline - (shape.base_size - inner.base_size) * 0.4
        for char in (_circled_digit(char) for char in text):
            if not char.strip():
                continue
            slot = self.measurer.slot(char, inner)
            width = self.measurer.advance(char, inner, slot)
            self.append_glyph(state, char, inner, slot, device_round(x + (shape.base_size - width) / 2), raised)

    def auto_number(self, view: ParagraphView, char_index: int, model: AutoNumberCtrl) -> tuple[str, CharShape]:
        shape = self.styles.char_shapes[view.shape_ids[char_index]]
        number = self.page_numbers[-1] if model.kind == 0 else self.current_note_number
        text = _affix(model.prefix_char) + format_number(number, model.number_shape) + _affix(model.suffix_char)
        return text, _superscript(shape) if model.superscript else shape

    def note_mark(self, view: ParagraphView, char_index: int, model: NoteCtrl) -> tuple[str, CharShape]:
        """본문에 찍는 각주·미주 번호. 한글 2022 PDF에서 위첨자(0.7배)로 그리고 그 폭만큼 뒤 글자를 민다."""
        mark = _affix(model.prefix_char) + format_number(model.number, model.number_shape) + _affix(model.suffix_char)
        return mark, _superscript(self.styles.char_shapes[view.shape_ids[char_index]])


def _anchored_floats(view: ParagraphView) -> list[tuple[int, Node]]:
    """첫 줄에 놓인 문단 기준 자리차지(떠 있는) 개체들의 (글자 순번, 컨트롤)."""
    if not view.segments:
        return []
    first_line_end = view.segments[1].text_start if len(view.segments) > 1 else 1 << 31
    return [
        (index, ctrl)
        for index, ctrl in sorted(view.controls.items())
        if view.positions[index] < first_line_end
        and isinstance(ctrl.model, ObjectCtrlHeader)
        and not ctrl.model.treat_as_char
        and ctrl.model.text_wrap == TextWrap.TOP_AND_BOTTOM
        and ctrl.model.vert_rel_to == VertRelTo.PARA
    ]


def _floats_overlap_first_line(view: ParagraphView, body_height: int) -> bool:
    """첫 줄이 떠 있는 표들 뒤의 글자처럼 취급한 개체뿐이고(글자 없음), 그 줄과 표들이 한 쪽에 함께 들어가지 않는가.

    글자가 있는 줄(제목 뒤 표, kr_156489775)이나 함께 들어가는 줄(hwpx-01 1쪽)은 한글도 저장 위치에 그대로 둔다.
    """
    floats = _anchored_floats(view)
    if not floats or not any(ctrl.record.payload[:4] == tags.TABLE_CTRL.to_bytes(4, "little") for _, ctrl in floats):
        return False
    float_ids = {id(ctrl) for _, ctrl in floats}
    last_float = max(index for index, _ in floats)
    first, last = view.line_range(0)
    has_text, inline_after = False, False
    for order in range(first, last):
        item = view.chars[order]
        if isinstance(item, TextChar):
            has_text = has_text or bool(item.char.strip())
        elif order in view.controls and id(view.controls[order]) not in float_ids:
            model = view.controls[order].model
            inline_after = inline_after or (isinstance(model, ObjectCtrlHeader) and order > last_float)
    extent = sum(ctrl.model.height + sum((ctrl.model.outer_margins or [0, 0, 0, 0])[2:]) for _, ctrl in floats)
    first_line = view.segments[0]
    return (
        inline_after
        and not has_text
        and first_line.vertical_pos < extent
        and extent + first_line.line_height > body_height
    )


def _line_has_ink(view: ParagraphView, index: int) -> bool:
    """줄에 보이는 글자나 컨트롤이 있는가."""
    first, last = view.line_range(index)
    for order in range(first, last):
        item = view.chars[order]
        if item.char.strip() if isinstance(item, TextChar) else order in view.controls:
            return True
    return False


def _circled_digit(char: str) -> str:
    """원문자(①, ③ …)를 원 안에 찍는 글자(1, 3 …)로 푼다. 원문자가 아니면 그대로."""
    decomposition = unicodedata.decomposition(char)
    if decomposition.startswith("<circle> "):
        return "".join(chr(int(code, 16)) for code in decomposition.split()[1:])
    return char


def _section_def(nodes: list[Node]) -> SectionDefCtrl | None:
    for node in nodes:
        for child in node.children:
            if child.tag == tags.CTRL_HEADER and isinstance(child.model, SectionDefCtrl):
                return child.model
    return None


def _column_def(node: Node) -> ColumnDefCtrl | None:
    for child in node.children:
        if child.tag == tags.CTRL_HEADER and isinstance(child.model, ColumnDefCtrl):
            return child.model
    return None


def tab_fill(item: ControlChar) -> int:
    """인라인 탭의 채움 종류(명세 표25 선 종류). 탭 폭 4바이트 뒤 1바이트(rhwp text_measurement.rs 273)."""
    return item.data[4] if len(item.data) > 4 else 0


def layout_document(document: HwpDocument) -> list[Page]:
    return DocumentLayout(document).run()
