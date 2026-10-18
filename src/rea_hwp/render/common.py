"""레이아웃 모듈들이 함께 쓰는 자료형·상수. 좌표는 HWPUNIT."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import NamedTuple

from .. import tags
from ..document import Node
from ..models.controls import (
    ColumnDefCtrl,
    PageDef,
    PageNumberPosCtrl,
)
from ..models.docinfo import CharShape, ParaShape
from ..models.paragraph import ControlChar, LineSeg, ParaText
from . import metrics
from .scene import Page
from .styles import DocStyles

#: 한글 PDF는 글자 원점을 600dpi 장치 격자(12 HWPUNIT)에 맞춘다. 한글 2022 PDF 글자 간격 실측(2026-10-17).
DEVICE_UNIT = 12
JUSTIFY, LEFT, RIGHT, CENTER, DISTRIBUTE, SPLIT = range(6)
#: LINE_SEG 태그 bit20: 이 줄에 문단 들여쓰기·내어쓰기를 적용한다.
INDENT_APPLIED = 1 << 20
#: 개체 기준(명세 표70). 세로: 0 종이, 1 쪽, 2 문단. 가로: 0 종이, 1 쪽, 2 단, 3 문단.
REL_PAPER, REL_PAGE, REL_COLUMN_OR_PARA, REL_PARA = 0, 1, 2, 3
ALIGN_START, ALIGN_CENTER, ALIGN_END = 0, 1, 2
TAC_ASCENT = 0.85
#: 용지 설정 제본 방식(명세 표132): 1 맞쪽 편집.
MIRRORED = 1
#: 표 쪽 나눔(명세 표76): 1 셀 단위로 나눔(행 안에서도 나눔), 2 행 단위로만 나눔.
SPLIT_CELLS = 1
#: 표 바닥이 본문 바닥을 이만큼(HU, 약 0.5mm) 넘는 것은 넘친 것으로 보지 않는다(검증 안 됨: 반올림 오차 흡수용).
TABLE_FIT_TOLERANCE = 142
MAX_TABLE_PAGES = 2000
#: 위첨자·각주 표시 크기 비율과, 줄인 글자 윗부분을 원래 글자 윗부분(크기의 0.85)에 맞추는 올림.
#: 실측: 한글 2022 PDF 미주 표시 9pt → 6.715pt, 기준선 1.92pt 위(3-09월_교육_통합_2022). 일반 위첨자는 아직 검증 안 됨.
SCRIPT_SCALE = 0.75
#: 글자 모양으로 지정한 위·아래첨자 크기 비율. 한글 2022 PDF 앞 200문서 실측(위첨자 238/259자, 아래첨자 12/12자).
SHAPE_SCRIPT_SCALE = 0.64
#: 렌더러가 만든 각주·미주·자동 번호 표시임을 CharShape 사본에 표시하는 키.
GENERATED_MARK = "generated_mark"


def script_scale(shape: CharShape) -> float:
    """위·아래첨자 크기 비율. 렌더러가 만든 번호 표시는 0.75, 글자 모양 첨자는 0.64."""
    return SCRIPT_SCALE if shape.values.get(GENERATED_MARK) else SHAPE_SCRIPT_SCALE


def device_round(value: float) -> int:
    """0.5칸은 내린다(실측: 1470→1464, 1410→1404, 1350→1344)."""
    return DEVICE_UNIT * math.ceil(value / DEVICE_UNIT - 0.5)


@dataclass
class TextChar:
    position: int
    char: str
    shape_id: int


@dataclass
class ParagraphView:
    """문단 하나의 글자, 글자 모양, 컨트롤, 줄 배치를 위치 좌표(WCHAR)로 묶은 것."""

    node: Node
    para_shape: ParaShape
    chars: list[TextChar | ControlChar]
    positions: list[int]
    segments: list[LineSeg]
    #: chars 순번 → 그 확장 제어 문자와 짝인 CTRL_HEADER.
    controls: dict[int, Node]
    #: chars 순번 → 그 위치에 적용되는 글자 모양 ID(컨트롤 문자 포함).
    shape_ids: list[int]

    @classmethod
    def build(cls, node: Node, styles: DocStyles) -> ParagraphView:
        text_node = node.find(tags.PARA_TEXT)
        shape_node = node.find(tags.PARA_CHAR_SHAPE)
        seg_node = node.find(tags.PARA_LINE_SEG)
        runs = shape_node.model.runs if shape_node and shape_node.model.__dict__.get("runs") else [(0, 0)]
        placed = text_node.model.positioned() if text_node and isinstance(text_node.model, ParaText) else []
        ctrl_nodes = iter(node.find_all(tags.CTRL_HEADER))
        chars: list[TextChar | ControlChar] = []
        positions: list[int] = []
        shape_ids: list[int] = []
        controls: dict[int, Node] = {}
        run_index = 0
        for position, item in placed:
            while run_index + 1 < len(runs) and runs[run_index + 1][0] <= position:
                run_index += 1
            shape_id = runs[run_index][1]
            if isinstance(item, ControlChar):
                if item.is_extended:
                    ctrl = next(ctrl_nodes, None)
                    if ctrl is not None:
                        controls[len(chars)] = ctrl
                chars.append(item)
            else:
                chars.append(TextChar(position, item, shape_id))
            positions.append(position)
            shape_ids.append(shape_id)
        segments = seg_node.model.segments if seg_node and seg_node.model.__dict__.get("segments") is not None else []
        shape_index = node.model.para_shape_id
        para_shape = styles.para_shapes[shape_index] if shape_index < len(styles.para_shapes) else styles.para_shapes[0]
        return cls(node, para_shape, chars, positions, segments, controls, shape_ids)

    def line_range(self, index: int) -> tuple[int, int]:
        start = self.segments[index].text_start
        end = self.segments[index + 1].text_start if index + 1 < len(self.segments) else 1 << 31
        first = next((i for i, p in enumerate(self.positions) if p >= start), len(self.positions))
        last = next((i for i, p in enumerate(self.positions) if p >= end), len(self.positions))
        return first, last

    @property
    def content_height(self) -> int:
        if not self.segments:
            return 0
        last = self.segments[-1]
        return last.vertical_pos + last.line_height


@dataclass
class SectionGeometry:
    page_def: PageDef
    paper_width: int
    paper_height: int
    body_left: int
    body_top: int
    body_width: int
    body_height: int
    columns: list[tuple[int, int]] = field(default_factory=list)
    #: 지금 단 영역의 단 정의(없으면 1단).
    column_def: ColumnDefCtrl | None = None

    @classmethod
    def from_page_def(cls, page_def: PageDef, column_def: ColumnDefCtrl | None) -> SectionGeometry:
        width, height = page_def.paper_width, page_def.paper_height
        if page_def.attr & 1:
            width, height = height, width
        left = page_def.margin_left + page_def.margin_gutter
        top = page_def.margin_top + page_def.margin_header
        body_width = width - left - page_def.margin_right
        body_height = height - top - page_def.margin_bottom - page_def.margin_footer
        geometry = cls(page_def, width, height, left, top, body_width, body_height)
        geometry.columns = column_layout(column_def, body_width)
        geometry.column_def = column_def
        return geometry


def column_layout(column_def: ColumnDefCtrl | None, body_width: int) -> list[tuple[int, int]]:
    """(단 x 오프셋, 단 너비) 목록."""
    count = column_def.count if column_def else 1
    if count <= 1 or column_def is None:
        return [(0, body_width)]
    gap = column_def.gap or 0
    widths = column_def.widths if not column_def.same_width else []
    if not widths:
        width = (body_width - gap * (count - 1)) / count
        return [(round(i * (width + gap)), round(width)) for i in range(count)]
    # 개별 너비·간격은 비례값이다(rhwp page_layout.rs): 합이 본문 폭이 되도록 늘인다.
    gaps = column_def.gaps
    scale = body_width / (sum(widths) + sum(gaps))
    layout, x = [], 0.0
    for index, width in enumerate(widths):
        layout.append((round(x), round(width * scale)))
        x += width * scale + (gaps[index] * scale if index < len(gaps) else 0)
    return layout


@dataclass
class Area:
    """문단 목록을 놓을 영역. y는 LINE_SEG vertical_pos 0이 놓이는 위치다."""

    x: float
    y: float
    width: float


class FootnotePiece(NamedTuple):
    """쪽 아래에 놓는 각주 조각. 한글은 쪽을 넘긴 각주의 뒤 조각 줄 위치를 0부터 다시 저장한다."""

    ctrl: Node
    piece: int = 0


@dataclass
class PageState:
    page: Page
    geometry: SectionGeometry
    #: 맞쪽 보정 전의 구역 기하.
    base_geometry: SectionGeometry
    column: int = 0
    footnotes: list[FootnotePiece] = field(default_factory=list)
    #: footnotes 앞쪽에서 앞 쪽이 넘긴 조각 수.
    carried_footnotes: int = 0
    hide: int = 0
    #: 쪽을 넘은 표가 만든 쪽. 다음 문단의 세로 위치가 되돌아가도 이 쪽을 이어 쓴다.
    continued_by_table: bool = False
    #: 지금 단 영역(단 정의가 바뀔 때마다 새로 시작)의 시작 y(본문 위 기준). 줄 vertical_pos의 0 위치다.
    zone_top: float = 0.0
    #: 지금 단 영역에 놓은 줄 가운데 가장 낮은 줄의 아래(줄 간격 제외, 본문 위 기준).
    zone_bottom: float = 0.0
    #: 앞 쪽 바닥을 넘어 이 쪽으로 밀려 온 줄들의 아래(줄 간격 포함). 다음 쪽 첫 줄은 여기서부터 이어진다.
    pushed_bottom: float | None = None
    #: 이 쪽 본문에 놓은 어울림 개체의 (단 번호, 바깥 상자 왼쪽·위·폭·높이, 글 흐름).
    #: 같은 단 뒤 줄의 가로 구간이 이 상자를 피한다.
    floats: list[tuple[int, float, float, float, float, int]] = field(default_factory=list)


@dataclass
class SectionState:
    headers: dict[int, Node] = field(default_factory=dict)
    footers: dict[int, Node] = field(default_factory=dict)
    page_number_pos: PageNumberPosCtrl | None = None


#: FaceName 글꼴 종류: 2 HFT.
HFT_FONT = 2
#: 기호 슬롯 글꼴로 찍는 문장 부호(「․」 U+2024, 「‧」 U+2027).
SYMBOL_SLOT_PUNCTUATION = frozenset("\u2024\u2027")
#: 영문 글꼴에 있으면 영문 슬롯으로 놓는 비ASCII 구두점(「·」, 작은·큰따옴표).
LATIN_PUNCTUATION = frozenset({0xB7, 0x2018, 0x2019, 0x201C, 0x201D})


class TextMeasurer:
    def __init__(self, styles: DocStyles) -> None:
        self.styles = styles
        self.database = metrics.default_database()

    def metric(self, char: str, shape: CharShape, slot: int) -> metrics.FontMetric | None:
        face = self.styles.face_name(shape, slot)
        # 진하게도 보통 굵기 폭으로 놓는다(hwp-multi-001 3쪽 PDF: 맑은 고딕 진하게 12pt 숫자 전진 6.60pt = 보통 0.55em).
        metric = self.database.find(face, False, shape.italic)
        legacy = metrics.LEGACY_LATIN.get(face) if slot == metrics.LATIN else None
        # 옛 영문 글꼴에 없는 ASCII 밖 글자는 대체 글꼴 폭이다(80168 80쪽 PDF: HCI Poppy 「·」가 Palatino 0.25em).
        if legacy and (metric is None or metric.width(ord(char)) is None or not char.isascii()):
            return self.database.find(legacy, False, shape.italic) or metric
        return metric

    def hft_em(self, char: str, shape: CharShape, slot: int) -> float | None:
        """HFT 글꼴로 선언된 면이면 한글이 그 글꼴을 찍은 PDF 폭(em). 같은 이름 TrueType 폭과 다르다.

        휴먼명조 「‘」: TrueType으로 찍은 PDF는 1em, HFT(Type3)로 찍은 PDF는 0.277em(font_metrics_pdf_fill.json.gz hft).
        """
        faces = self.styles.faces[slot] or self.styles.faces[0]
        index = shape.face_ids[slot]
        if not 0 <= index < len(faces):
            return None
        # 글꼴 종류가 TrueType으로 저장돼도 한글 PC에 TrueType이 없는 이름은 HFT로 찍는다
        # (80168 19쪽: 한양신명조 종류 1, 정답 PDF Type3 「”」 0.441em).
        if faces[index].font_type != HFT_FONT and not metrics.is_hft_only(faces[index].name):
            return None
        width = self.database.hft.get(faces[index].name, {}).get(ord(char))
        return width / 1000 if width is not None else None

    def symbol_slot_em(self, char: str, shape: CharShape) -> float:
        """「․」·「‧」 폭. 한글은 기호 슬롯 글꼴로 찍고, 그 글꼴이 TrueType인데 글자가 없으면 대체 글꼴 폭이다.

        정답 PDF 코퍼스 실측(U+2024·U+2027): 기호 슬롯이 HFT(한양신명조·한양중고딕 등)면 Type3(hwp3-sample16·k-water),
        TrueType(휴먼명조·바탕·굴림체 등)이면 Haansoft Batang(task2287), 맑은 고딕이면 HCR Dotum(kr_156489577).
        """
        faces = self.styles.faces[metrics.SYMBOL] or self.styles.faces[0]
        index = shape.face_ids[metrics.SYMBOL]
        face = faces[index] if 0 <= index < len(faces) else None
        name = face.name if face is not None else ""
        if face is not None and face.font_type == HFT_FONT or metrics.is_hft_only(name):
            width = self.database.hft.get(name, {}).get(ord(char))
            return width / 1000 if width is not None else metrics.fallback_em(char)
        # 맑은 고딕 TrueType 폭 표에는 이 글자가 있지만 한글 PDF는 HCR Dotum으로 찍는다.
        names = ("HCR Dotum",) if name in ("맑은 고딕", "Malgun Gothic") else (name, "Haansoft Batang")
        for metric in map(self.database.find, names):
            width = metric.width(ord(char)) if metric is not None else None
            if width is not None:
                return width / metric.em
        return metrics.fallback_em(char)

    def has_glyph(self, char: str, shape: CharShape, slot: int) -> bool:
        if self.hft_em(char, shape, slot) is not None:
            return True
        metric = self.metric(char, shape, slot)
        return metric is not None and metric.width(ord(char)) is not None

    def font_space_em(self, char: str, shape: CharShape) -> float | None:
        """「글꼴에 어울리는 빈칸」(글자 모양 속성 bit 25) 공백의 em. 영문 글꼴의 공백 폭을 쓴다.

        실측: task2070 107쪽 각주 8.5pt·장평 90% 공백 1.8pt = Times New Roman 공백 0.25em(휴먼명조는 0.5em).
        """
        if char != " " or not shape.use_font_space:
            return None
        metric = self.metric(char, shape, metrics.LATIN)
        width = metric.width(0x20) if metric is not None else None
        return width / metric.em if width is not None else None

    def slot(self, char: str, shape: CharShape) -> int:
        """글자의 언어 슬롯. ASCII 구두점·「·」·따옴표는 영문 글꼴에 그 글자가 있으면 영문 슬롯으로 놓는다.

        exam_kor·exam_eng PDF: 한글 사이 구두점·「’」가 영문 글꼴(Times New Roman) 폭으로 놓인다. 숫자·로마자만 가진
        영문 글꼴(HCI Poppy)이면 「‘」는 한글 글꼴로 찍힌다(hwp-img-001).
        """
        slot = metrics.slot_of(char, metrics.HANGUL)
        code = ord(char)
        if slot == metrics.HANGUL and (0x20 < code < 0x7F or code in LATIN_PUNCTUATION):
            if self.has_glyph(char, shape, metrics.LATIN):
                return metrics.LATIN
        return slot

    def advance(self, char: str, shape: CharShape, slot: int, spacing: int | None = None) -> int:
        text_figure_space = char == "\u2007"
        char = metrics.CONTROL_SPACES.get(char, char)
        font_space = self.font_space_em(char, shape)
        if font_space is not None:
            slot = metrics.LATIN
        size = round(shape.base_size * shape.rel_sizes[slot] / 100)
        # 위·아래첨자 안 공백은 줄이지 않는다(hwpspec 130쪽 PDF: 9pt 아래첨자 「글자 아래」 공백 4.2pt).
        if (shape.superscript or shape.subscript) and char != " ":
            size = round(size * script_scale(shape))
        em = font_space if font_space is not None else self.hft_em(char, shape, slot)
        if char in SYMBOL_SLOT_PUNCTUATION:
            em = self.symbol_slot_em(char, shape)
        if text_figure_space:
            em = metrics.TEXT_FIGURE_SPACE_EM
        if em is None:
            em = metrics.glyph_em(char, self.metric(char, shape, slot))
        # HFT 폭 표(PDF 실측)에 있는 글자도 진하게면 넓힌다(kr_156489570 19쪽 한양중고딕 13pt 진하게 「1」 6.96pt).
        if (
            font_space is None
            and shape.bold
            and char != " "
            and metrics.is_hft_only(self.styles.face_name(shape, slot))
        ):
            em += metrics.HFT_BOLD_EXTRA_EM
        # 글꼴 공백은 고정 0.5em 공백 규칙 대신 글꼴 폭으로 일반 글자처럼 계산한다.
        measured = metrics.FONT_SPACE if font_space is not None else char
        spacing = shape.spacings[slot] if spacing is None else spacing
        return metrics.hancom_width(measured, em * 1000, size, shape.ratios[slot], spacing)


def _is_space(item: TextChar | ControlChar) -> bool:
    return isinstance(item, TextChar) and item.char == " "


def _count_trailing_spaces(items: list) -> int:
    count = 0
    for item in reversed(items):
        if not _is_space(item):
            break
        count += 1
    return count


def _superscript(shape: CharShape) -> CharShape:
    raised = type(shape)(values=dict(shape.values), tail=shape.tail)
    raised.values["attr"] = shape.attr | (1 << 15)
    raised.values[GENERATED_MARK] = True
    return raised


def _affix(code: int | None) -> str:
    return chr(code) if code else ""


def _list_paragraphs(ctrl: Node) -> list[Node]:
    return [child for child in ctrl.children if child.tag == tags.PARA_HEADER]
