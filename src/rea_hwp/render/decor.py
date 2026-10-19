"""머리말·꼬리말·쪽 번호처럼 쪽마다 붙는 요소를 그린다."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .. import tags
from ..document import Node
from ..models.controls import MasterPageListHeader, ObjectCtrlHeader, PageNumberPosCtrl, SectionDefCtrl, VertRelTo
from ..models.docinfo import CharShape
from . import metrics
from .common import DEVICE_UNIT, Area, PageState, ParagraphView, SectionState, _list_paragraphs
from .numbering import format_number
from .paint import BAND_LIST, MASTER_LIST

#: 쪽 번호 글꼴의 아랫부분(winDescent/unitsPerEm). 아래쪽 쪽 번호 기준선은 꼬리말 영역 아래끝에서 이만큼 올라간다.
#: 값은 한글 2022 설치 글꼴 파일의 OS/2 표에서 읽었다.
#: 정답 PDF 쪽 번호 기준선(0.12pt 격자에 맞는 PDF 70여 개)과 맞춰 보았다.
PAGE_NUMBER_DESCENTS = {
    # gulim.ttc·HBATANG.TTF·HY*.TTF: 145/1024.
    "굴림": 0.1416,
    "Gulim": 0.1416,
    "돋움": 0.1416,
    "한컴바탕": 0.1416,
    "Haansoft Batang": 0.1416,
    # HANDotum.ttf·HANBatang.ttf: 230/1000.
    "함초롬돋움": 0.230,
    "함초롬바탕": 0.230,
    "HCR Dotum": 0.230,
    "HCR Batang": 0.230,
    # HMKMM.TTF(휴먼명조) 100/512. 한양·HCI 글꼴(HFT)도 10pt에서 같은 16칸이다(hwp3-sample16·kps-ai·kr_156489676).
    "휴먼명조": 0.1953,
    # 글꼴 파일 값(495/2048=0.2417)과 다르다. 정답 PDF 실측 10.5pt에서 23칸(kr_156489671 등 3문서).
    "맑은 고딕": 0.263,
}
PAGE_NUMBER_HFT_DESCENT = 0.1953
DEFAULT_PAGE_NUMBER_FACES = ("한컴바탕", "Haansoft Batang")
PAGE_NUMBER_DEFAULT_DESCENT = 0.1416
HEADER_ID = tags.HEADER_CTRL.to_bytes(4, "little")
#: 구역 정의 속성(명세 표129) bit 0~5: 구역 첫 쪽에서 머리말·꼬리말·바탕쪽·테두리·배경·쪽 번호 감추기.
SECTION_FIRST_PAGE_HIDE = 0x3F
#: 구역 정의 속성 bit 29·31·30: 양 쪽·홀수 쪽·짝수 쪽 바탕쪽이 있다. 바탕쪽 목록은 이 순서로 저장된다.
BOTH, ODD, EVEN = 1 << 29, 1 << 31, 1 << 30


@dataclass
class DecorCarry:
    """앞 쪽·앞 구역에서 이어받는 머리말·꼬리말(홀수 쪽 True)과 쪽 번호 위치(rhwp rendering.rs 구역 간 carry)."""

    headers: dict[bool, Node] = field(default_factory=dict)
    footers: dict[bool, Node] = field(default_factory=dict)
    page_number_pos: PageNumberPosCtrl | None = None
    masters: dict[bool, list[Node]] = field(default_factory=dict)


class DecorMixin:
    def record_header_footer(self, ctrl: Node, page_index: int) -> None:
        """본문 흐름에서 만난 머리말·꼬리말 컨트롤을 그 쪽(0부터)에 적어 둔다. 그 쪽부터 적용한다."""
        if not hasattr(self, "header_footer_events"):
            self.header_footer_events: list[tuple[int, Node]] = []
        self.header_footer_events.append((page_index, ctrl))

    def decorate_page(self, state: PageState, section: SectionState, number: int) -> None:
        """쪽 순서대로 불러야 한다. 머리말·꼬리말·쪽 번호가 없는 구역은 앞 구역 것을 이어 쓴다."""
        if not hasattr(self, "decor_carry"):
            self.decor_carry = DecorCarry()
        carry = self.decor_carry
        geometry = state.geometry
        page_def = geometry.page_def
        odd = number % 2 == 1
        width_area = Area(geometry.body_left, 0, geometry.body_width)
        events = getattr(self, "header_footer_events", None)
        if events is not None:
            for _, ctrl in (event for event in events if event[0] == state.page.number - 1):
                self.activate_header_footer(ctrl)
        else:
            for own, carried in ((section.headers, carry.headers), (section.footers, carry.footers)):
                chosen = own.get(2 if odd else 1) or own.get(0)
                if chosen is not None:
                    carried[odd] = chosen
        if section.page_number_pos is not None:
            carry.page_number_pos = section.page_number_pos
        section_def, first_page = self.section_def_of(state, section)
        hide = state.hide | (section_def.model.attr & SECTION_FIRST_PAGE_HIDE if section_def and first_page else 0)
        if section_def is not None:
            masters = master_pages(section_def)
            for is_odd in (True, False):
                chosen = masters.get(ODD if is_odd else EVEN) or masters.get(BOTH)
                if chosen is None and is_odd not in carry.masters and len(masters) == 1:
                    # 앞 구역에서 이어받을 것이 없으면 하나뿐인 바탕쪽을 양쪽에 쓴다(rhwp rendering.rs single_base_mp).
                    chosen = next(iter(masters.values()))
                if chosen is not None:
                    carry.masters[is_odd] = chosen
        # 한글 PDF는 바탕쪽·머리말·꼬리말·쪽 번호 글자를 본문보다 먼저 쓴다.
        body_items = state.page.items
        state.page.items = []
        recorded = len(self.__dict__.get("paint_units", []))
        saved = self.page_numbers[-1]
        self.page_numbers[-1] = number
        master = carry.masters.get(odd)
        if master is not None and not hide & 0x04:
            area = Area(geometry.body_left, geometry.body_top, geometry.body_width)
            with self.painting_list(MASTER_LIST):
                self.place_paragraphs(state, master, area)
        header = carry.headers.get(odd)
        footer = carry.footers.get(odd)
        with self.painting_list(BAND_LIST):
            if header is not None and not hide & 0x01:
                self.place_band(state, header, width_area, page_def.margin_top, page_def.margin_header)
            if footer is not None and not hide & 0x02:
                top = geometry.paper_height - page_def.margin_bottom - page_def.margin_footer
                self.place_band(state, footer, width_area, top, page_def.margin_footer)
            self.page_numbers[-1] = saved
            if carry.page_number_pos is not None and not hide & 0x20:
                with self.painting(state.page, None, 0):
                    self.draw_page_number(state, carry.page_number_pos, number)
        decor_count = len(state.page.items)
        state.page.items.extend(body_items)
        self.shift_paint_units(state.page, recorded, decor_count)
        if state.footnotes:
            self.draw_footnotes(state)

    def section_def_of(self, state: PageState, section: SectionState) -> tuple[Node | None, bool]:
        """쪽이 속한 구역의 구역 정의와, 그 쪽이 구역 첫 쪽인지. 구역과 쪽을 맞출 수 없으면 None."""
        if not hasattr(self, "decor_sections"):
            defs = [
                next((node for node in stream.walk() if isinstance(node.model, SectionDefCtrl)), None)
                for stream in self.document.sections
            ]
            order: list[SectionState] = []
            for page_section in self.page_sections:
                if not any(page_section is known for known in order):
                    order.append(page_section)
            # 쪽이 하나도 없는 구역이 있으면 순서로 짝지을 수 없다.
            self.decor_sections = list(zip(order, defs, strict=True)) if len(order) == len(defs) else []
        index = self.states.index(state)
        first_page = index == 0 or self.page_sections[index - 1] is not section
        found = next((node for known, node in self.decor_sections if known is section), None)
        return found, first_page

    def activate_header_footer(self, ctrl: Node) -> None:
        carried = self.decor_carry.headers if ctrl.record.payload[:4] == HEADER_ID else self.decor_carry.footers
        # 적용 쪽(명세 표141): 0 양쪽, 1 짝수 쪽, 2 홀수 쪽.
        apply_page = ctrl.model.apply_page
        if apply_page in (0, 2):
            carried[True] = ctrl
        if apply_page in (0, 1):
            carried[False] = ctrl

    def place_band(self, state: PageState, ctrl: Node, width_area: Area, top: float, height: float) -> None:
        """머리말·꼬리말 영역(top부터 height)에 문단을 놓는다. 리스트 헤더의 세로 정렬을 따른다."""
        views = self.paragraph_views(_list_paragraphs(ctrl), width_area.width)
        list_header = ctrl.find(tags.LIST_HEADER)
        align = list_header.model.vert_align if list_header is not None else 0
        # 내용이 영역보다 높으면 위에 붙인다(basic__Hyper 꼬리말 실측).
        content = max((max(view.content_height, floating_bottom(view)) for view in views), default=0)
        slack = max(0, height - content) * {1: 0.5, 2: 1}.get(align, 0)
        area = Area(width_area.x, top + slack, width_area.width)
        for view in views:
            for index in range(len(view.segments)):
                self.place_line(state, view, index, area)

    def draw_page_number(self, state: PageState, pos: PageNumberPosCtrl, number: int) -> None:
        position = pos.position
        if position == 0:
            return
        geometry = state.geometry
        page_def = geometry.page_def
        text = format_number(number, pos.number_shape)
        if pos.dash_char:
            text = f"{chr(pos.dash_char)} {text} {chr(pos.dash_char)}"
        shape = self.page_number_shape()
        width = self.text_width(text, shape)
        odd = number % 2 == 1
        horizontal = {
            1: 0,
            2: 1,
            3: 2,
            4: 0,
            5: 1,
            6: 2,
            7: 2 if odd else 0,
            8: 2 if odd else 0,
            9: 0 if odd else 2,
            10: 0 if odd else 2,
        }
        align = horizontal.get(position, 1)
        x = geometry.body_left + (0, (geometry.body_width - width) / 2, geometry.body_width - width)[align]
        size = shape.base_size
        if position in (1, 2, 3, 7, 9):
            baseline = page_def.margin_top + page_def.margin_header / 2 + size / 3
        else:
            baseline = self.bottom_page_number_baseline(state, shape)
        self.place_text(state, text, shape, x, baseline)

    def bottom_page_number_baseline(self, state: PageState, shape: CharShape) -> int:
        """아래쪽 쪽 번호 기준선. 한글 2022 PDF는 0.12pt(12HU) 격자에 놓는다.

        꼬리말 영역 아래끝을 격자 칸으로 셈한다: 본문 위끝은 반올림, 본문 높이는 올림, 꼬리말 높이는 0.5를 내리는
        반올림이다(굴림 10pt 정답 18가지 여백 조합을 모두 맞히는 반올림 방식 가운데 하나). 꼬리말 여백이 0이면
        아래 여백의 가운데에 놓는다(kr_156369057·hwp3-sample5 실측). 검증 안 됨: 위쪽 쪽 번호는 실측 예가 없다.
        """
        page_def = state.geometry.page_def
        paper = state.geometry.paper_height
        descent = round(shape.base_size * page_number_descent(self.page_number_face()) / DEVICE_UNIT)
        if page_def.margin_footer <= 0:
            return DEVICE_UNIT * math.floor((paper - page_def.margin_bottom / 2) / DEVICE_UNIT - descent)
        top = page_def.margin_top + page_def.margin_header
        body = paper - top - page_def.margin_bottom - page_def.margin_footer
        cells = (
            math.floor(top / DEVICE_UNIT + 0.5)
            + math.ceil(body / DEVICE_UNIT)
            + math.ceil(page_def.margin_footer / DEVICE_UNIT - 0.5)
        )
        return DEVICE_UNIT * (cells - descent)

    def page_number_face(self) -> str:
        """쪽 번호를 실제로 찍는 글꼴. 글꼴 정보의 대체 글꼴, 그다음 PDF에서 모은 대체 표를 따른다
        (issue6132: 나눔고딕 → 대체 글꼴 함초롬바탕, issue6300: 함초롬돋움 확장 → 한컴바탕)."""
        if getattr(self, "_page_number_default", False):
            return "한컴바탕"
        shape = self.page_number_shape()
        faces = self.styles.faces[metrics.LATIN] or self.styles.faces[0]
        index = shape.face_ids[metrics.LATIN]
        record = faces[index] if 0 <= index < len(faces) else None
        face = self.styles.face_name(shape, metrics.LATIN)
        substitutes = self.measurer.database.substitutes
        if face not in substitutes:
            return face
        # 설치되지 않은 글꼴(PDF 대체 표에 있는 글꼴)만 대체한다. 문서가 정한 대체 글꼴이 먼저다.
        return record.alt_name if record is not None and record.alt_name else substitutes[face]

    def page_number_shape(self) -> CharShape:
        """「쪽 번호」 스타일의 글자 모양. 그 스타일이 없으면 한글은 한컴바탕 10pt로 찍는다
        (issue1921·issue6946·task2093·hwp3-sample PDF). 바탕글 모양에서 글꼴과 크기만 바꾼다."""
        if not hasattr(self, "_page_number_shape"):
            self._page_number_shape = self.find_page_number_shape()
        return self._page_number_shape

    def find_page_number_shape(self) -> CharShape:
        for style in self.styles.styles:
            if style.local_name == "쪽 번호" and style.char_shape_id < len(self.styles.char_shapes):
                return self.styles.char_shapes[style.char_shape_id]
        self._page_number_default = True
        base = self.styles.char_shapes[self.styles.styles[0].char_shape_id if self.styles.styles else 0]
        shape = type(base)(values=dict(base.values), tail=base.tail)
        ids = []
        for faces in self.styles.faces:
            found = next((i for i, face in enumerate(faces) if face.name in DEFAULT_PAGE_NUMBER_FACES), None)
            if found is None and faces:
                # 문서 글꼴 목록에 없으면 목록 끝에 한컴바탕을 더한다(글자 폭을 그 글꼴로 측정하기 위해).
                faces.append(type(faces[0])(values={**faces[0].values, "name": "한컴바탕", "alt_name": None}))
                found = len(faces) - 1
            ids.append(found or 0)
        shape.values.update(
            face_ids=ids,
            base_size=1000,
            attr=0,
            ratios=[100] * 7,
            spacings=[0] * 7,
            rel_sizes=[100] * 7,
            offsets=[0] * 7,
        )
        return shape


def page_number_descent(face: str) -> float:
    if face in PAGE_NUMBER_DESCENTS:
        return PAGE_NUMBER_DESCENTS[face]
    if face.startswith(("함초롬", "HCR ")):
        return PAGE_NUMBER_DESCENTS["함초롬돋움"]
    if face.startswith(("한양", "HCI ", "휴먼")):
        return PAGE_NUMBER_HFT_DESCENT
    return PAGE_NUMBER_DEFAULT_DESCENT


def floating_bottom(view: ParagraphView) -> int:
    """문단에 붙은 글자처럼 취급하지 않는 개체의 아래끝(문단 목록 위끝 기준). 없으면 0.

    꼬리말을 아래에 붙일 때 내용 높이에 들어간다(issue7333 꼬리말: 둘째 문단 1352 + 세로 오프셋 404 + 높이 3357
    = 5113, 정답 PDF 쪽 번호 770.04pt와 맞는다).
    """
    if not view.segments:
        return 0
    bottom = 0
    for ctrl in view.controls.values():
        model = ctrl.model
        if isinstance(model, ObjectCtrlHeader) and not model.treat_as_char and model.vert_rel_to == VertRelTo.PARA:
            margins = model.outer_margins or [0, 0, 0, 0]
            bottom = max(bottom, view.segments[0].vertical_pos + model.vert_offset + model.height + margins[3])
    return bottom


def master_pages(section_def: Node) -> dict[int, list[Node]]:
    """구역 정의 아래 바탕쪽 문단 목록을 종류(BOTH·ODD·EVEN)별로 나눈다."""
    # 바탕쪽 목록은 양쪽·짝수·홀수 순서로 저장된다
    # (실측: 편람 1구역 홀짝 바탕쪽, 정답 홀수 쪽 「제1장」·짝수 쪽 「2025 편람」).
    kinds = [kind for kind in (BOTH, EVEN, ODD) if section_def.model.attr & kind]
    groups: list[list[Node]] = []
    for child in section_def.children:
        if isinstance(child.model, MasterPageListHeader):
            groups.append([])
        elif child.tag == tags.PARA_HEADER and groups:
            groups[-1].append(child)
    # 확장 바탕쪽(마지막 쪽·임의 쪽)이 뒤에 더 붙을 수 있다. 아직 그리지 않는다.
    return dict(zip(kinds, groups, strict=False))
