"""문단 머리(개요·번호·글머리표)의 글자와 영역. 좌표는 HWPUNIT. 규칙 출처: rhwp layout/paragraph_layout.rs."""

from __future__ import annotations

from dataclasses import dataclass

from ..document import HwpDocument
from ..models.controls import SectionDefCtrl
from ..models.docinfo import CharShape, ParaShape
from .numbering import format_number
from .styles import DocStyles

#: ParaShape.head_type 값.
HEAD_NONE, HEAD_OUTLINE, HEAD_NUMBER, HEAD_BULLET = range(4)
#: 그림 글머리표 표시 글자.
IMAGE_BULLET = 0xFFFF
LEVELS = 10


@dataclass(frozen=True)
class HeadSpec:
    """머리 글자와 그 모양(문단 머리 정보 명세 표39·표42)."""

    text: str
    #: 머리 글자 모양 ID. 음수면 문단 첫 글자 모양을 쓴다.
    char_shape_id: int
    attr: int
    width_adjust: int
    distance: int
    bullet: bool


@dataclass(frozen=True)
class HeadGeometry:
    text: str
    shape: CharShape
    #: 첫 줄·이어지는 줄 시작에 더하는 양.
    first: float
    rest: float
    #: 머리 글자 x(첫 줄 본문 시작 기준).
    marker_dx: float


class NumberingCounter:
    """번호 문단 계수기. 같은 번호 ID면 잇고, 다른 ID로 바뀌면 그 ID의 앞 상태로 돌아간다(rhwp NumberingState)."""

    def __init__(self) -> None:
        self.current: int | None = None
        self.counters = [0] * LEVELS
        self.history: dict[int, list[int]] = {}

    def advance(self, numbering_id: int, level: int) -> list[int]:
        if self.current != numbering_id:
            if self.current is not None:
                self.history[self.current] = self.counters
            saved = self.history.get(numbering_id)
            self.counters = list(saved) if saved else self.counters[:level] + [0] * (LEVELS - level)
            self.current = numbering_id
        self.counters[level] += 1
        self.counters[level + 1 :] = [0] * (LEVELS - level - 1)
        return list(self.counters)


def outline_numbering_ids(document: HwpDocument) -> list[int]:
    """구역마다 개요 번호 모양 ID(구역 정의의 번호 문단 모양 ID)."""
    ids = []
    for section in document.sections:
        ctrl = next((n.model for n in section.walk() if isinstance(n.model, SectionDefCtrl)), None)
        ids.append(ctrl.numbering_para_shape_id if ctrl is not None and ctrl.numbering_para_shape_id else 1)
    return ids


def bullet_spec(styles: DocStyles, para_shape: ParaShape) -> HeadSpec | None:
    if not 1 <= para_shape.numbering_id <= len(styles.bullets):
        return None
    bullet = styles.bullets[para_shape.numbering_id - 1]
    if bullet.char is None or bullet.char == IMAGE_BULLET:
        return None
    return HeadSpec(
        chr(bullet.char), bullet.char_shape_id, bullet.attr, bullet.width_adjust, bullet.distance, bullet=True
    )


def numbering_spec(
    styles: DocStyles, para_shape: ParaShape, outline_id: int, counter: NumberingCounter
) -> HeadSpec | None:
    numbering_id = para_shape.numbering_id
    if para_shape.head_type == HEAD_OUTLINE and numbering_id == 0:
        numbering_id = outline_id
    if not 1 <= numbering_id <= len(styles.numberings):
        return None
    numbering = styles.numberings[numbering_id - 1]
    levels = numbering.all_levels
    level = min(para_shape.level, len(levels) - 1)
    if level < 0:
        return None
    counters = counter.advance(numbering_id, level)
    starts = [*(numbering.level_start_numbers or []), *(numbering.extended_start_numbers or [])]

    def number(index: int) -> str:
        start = starts[index] if index < len(starts) else 1
        value = start - 1 + counters[index] if counters[index] > 0 else start
        return format_number(value, levels[index].number_format)

    head = levels[level]
    text, i = "", 0
    while i < len(head.format):
        char = head.format[i]
        following = head.format[i + 1] if i + 1 < len(head.format) else ""
        if char == "^" and following.isdigit() and 1 <= int(following) <= len(levels):
            text += number(int(following) - 1)
            i += 2
        elif char == "^" and following in ("n", "N"):
            text += ".".join(number(k) for k in range(level + 1)) + ("." if following == "N" else "")
            i += 2
        else:
            text += char
            i += 1
    if not text:
        return None
    return HeadSpec(text, head.char_shape_id, head.attr, head.width_adjust, head.distance, bullet=False)


def head_geometry(spec: HeadSpec, shape: CharShape, glyph_width: float, indent: float) -> HeadGeometry | None:
    """머리 영역 = 글자 폭 + 너비 보정 + 본문과의 거리(attr bit4가 꺼져 있으면 글자 크기의 %).

    자동 내어쓰기(attr bit3)면 첫 줄·이어지는 줄 모두 max(영역 − max(들여쓰기, 0), 0)만큼 밀고, 아니면 첫 줄만
    영역만큼 민다(rhwp list_marker_geometry_for, 한/글 2024 합성 문서 43조합 실측). indent는 HU 들여쓰기.
    """
    size = shape.base_size
    distance = spec.distance if spec.attr & 0x10 else size * spec.distance / 100
    area = max(glyph_width + spec.width_adjust + distance, 0.0)
    if area <= 0:
        return None
    if spec.attr & 0x08:
        first = rest = max(area - max(indent, 0.0), 0.0)
    else:
        first, rest = area, 0.0
    cell = area - distance
    align_dx = {1: max((cell - glyph_width) / 2, 0.0), 2: max(cell - glyph_width, 0.0)}.get(spec.attr & 0x03, 0.0)
    # 소프트 하이픈 글머리표는 하이픈 폭으로 재지만 글자는 그대로 둔다(정답 PDF 텍스트도 U+00AD, hydrogen 3쪽).
    return HeadGeometry(spec.text, shape, first, rest, align_dx - area)


def head_shape_id(spec: HeadSpec, first_shape_id: int, styles: DocStyles) -> int:
    """머리 글자 모양. 0도 실제 참조다(basic__sungeo 3쪽 PDF: 글자 모양 0 = 14pt 글머리표, 문단 글자는 13pt)."""
    if 0 <= spec.char_shape_id < len(styles.char_shapes):
        return spec.char_shape_id
    return first_shape_id
