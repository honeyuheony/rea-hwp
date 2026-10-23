"""쪽마다 정답 PDF와 렌더를 맞대 깨진 쪽(세로 밀림·겹침·빠짐·넘침)과 처음 깨진 줄을 센다.
문서는 통과(깨진 쪽 없음)·실패·검사불가(렌더·검사 실패, 정답 없음)로 나눠 센다.

사용: uv run --extra bench python bench/breakage.py --out <폴더> [--match 이름일부] [--limit N] [--jobs N]
REA_HWP_USER_CORPUS가 있으면 사용자 표본(그 폴더의 *.hwp, 정답은 ref2022/)도 함께 센다.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from accuracy import EQUATION_FONTS, PlacedChar, _inside, pdf_image_boxes, pdf_pages
from corpus import CORPUS_ROOT, load_corpus
from divergence import (
    _PARAGRAPH_CONTEXT,
    _TRACED,
    REFERENCE_ROOT,
    _line_record,
    _paragraph_context,
    glyph_lines,
    install_trace,
    pair_chars,
    reference_lines,
    rendered_chars,
)

from rea_hwp.document import HwpDocument
from rea_hwp.render import layout
from rea_hwp.render.options import RenderOptions
from rea_hwp.render.scene import Image, Line, Rect
from rea_hwp.render.svg import HU_PER_PT

#: 밀림 문턱(pt). 정답 글자와 같은 글자가 렌더에서 이 거리 안에 없으면 제자리에 있지 않은 글자다.
#: 사용자가 문서 댓글로 바꿀 수 있는 값이라 하나로 둔다. 근거는 표본 그림 확인(작업 1 결과, 2026-10-24).
VERTICAL_SHIFT_PT = 5.0
#: 세로 밀림으로 볼 정답 줄의 최소 짝 글자 수. 한 글자 짝은 다른 자리 같은 글자와 잘못 맺히기 쉽다(추정).
SHIFT_MIN_CHARS = 2
#: 겹침으로 볼 두 글자 몸통의 겹친 넓이 비율(작은 글자 기준). 추정값.
OVERLAP_RATIO = 0.3

#: 큰 글자의 제자리 문턱(글자 크기 배수). 쪽 크기 장식 숫자가 글꼴 차이로 문턱 넘게 옮겨진 표본 하나로 정한 추정값.
BIG_GLYPH_SLACK = 0.1

KINDS = ("세로 밀림", "겹침", "빠짐", "넘침")


@dataclass(frozen=True)
class Target:
    """검사할 문서 하나. name은 결과 열쇠, groups는 출발선을 나눠 셀 문서군 이름."""

    name: str
    path: Path
    reference: Path
    password: str | None
    groups: tuple[str, ...]


@dataclass(frozen=True)
class Box:
    """글자 몸통(pt). 원점에서 위로 0.7em, 가로는 전진폭 가운데 80%(이웃 줄과 닿는 것은 겹침이 아니다)."""

    x0: float
    y0: float
    x1: float
    y1: float

    def area(self) -> float:
        return max(self.x1 - self.x0, 0.0) * max(self.y1 - self.y0, 0.0)

    def overlap(self, other: Box) -> float:
        width = min(self.x1, other.x1) - max(self.x0, other.x0)
        height = min(self.y1, other.y1) - max(self.y0, other.y0)
        return width * height if width > 0 and height > 0 else 0.0

    def moved(self, dx: float, dy: float) -> Box:
        return Box(self.x0 + dx, self.y0 + dy, self.x1 + dx, self.y1 + dy)


def glyph_box(glyph, char: PlacedChar) -> Box:
    size = glyph.size / HU_PER_PT
    advance = (glyph.advance / HU_PER_PT) if glyph.advance else size * glyph.scale_x * 0.5
    return Box(char.x + advance * 0.1, char.y - size * 0.7, char.x + advance * 0.9, char.y - size * 0.05)


@dataclass(frozen=True)
class Rule:
    """표 선·가로줄 하나(pt). 가로선이면 y0 == y1, 세로선이면 x0 == x1."""

    x0: float
    y0: float
    x1: float
    y1: float

    def crosses(self, box: Box, margin: float = 0.0) -> bool:
        """선이 글자 몸통 안쪽을 지나는가. margin(pt)만큼 몸통을 넓혀 본다."""
        if self.y0 == self.y1:
            return box.y0 - margin < self.y0 < box.y1 + margin and self.x0 < box.x1 and self.x1 > box.x0
        return box.x0 - margin < self.x0 < box.x1 + margin and self.y0 < box.y1 and self.y1 > box.y0


def rendered_rules(page) -> list[Rule]:
    rules = []
    for item in page.items:
        if isinstance(item, Line):
            x0, y0, x1, y1 = (v / HU_PER_PT for v in (item.x1, item.y1, item.x2, item.y2))
            if abs(y0 - y1) < 0.2:
                rules.append(Rule(min(x0, x1), y0, max(x0, x1), y0))
            elif abs(x0 - x1) < 0.2:
                rules.append(Rule(x0, min(y0, y1), x0, max(y0, y1)))
        elif isinstance(item, Rect) and (item.stroke or item.fill) and min(item.width, item.height) < 1.5 * HU_PER_PT:
            x, y, w, h = (v / HU_PER_PT for v in (item.x, item.y, item.width, item.height))
            rules.append(Rule(x, y + h / 2, x + w, y + h / 2) if h < w else Rule(x + w / 2, y, x + w / 2, y + h))
    return rules


def reference_rules(pdf_page) -> list[Rule]:
    """정답 쪽의 가로·세로 선. 선분(l)과 두께 1.5pt 아래 사각형(re)을 선으로 본다."""
    rules = []
    for drawing in pdf_page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                (x0, y0), (x1, y1) = (item[1].x, item[1].y), (item[2].x, item[2].y)
                if abs(y0 - y1) < 0.2:
                    rules.append(Rule(min(x0, x1), y0, max(x0, x1), y0))
                elif abs(x0 - x1) < 0.2:
                    rules.append(Rule(x0, min(y0, y1), x0, max(y0, y1)))
            elif item[0] == "re":
                rect = item[1]
                if rect.height < 1.5 and rect.width >= 1.5:
                    rules.append(Rule(rect.x0, (rect.y0 + rect.y1) / 2, rect.x1, (rect.y0 + rect.y1) / 2))
                elif rect.width < 1.5 and rect.height >= 1.5:
                    rules.append(Rule((rect.x0 + rect.x1) / 2, rect.y0, (rect.x0 + rect.x1) / 2, rect.y1))
                elif rect.width >= 1.5 and rect.height >= 1.5 and drawing.get("color") is not None:
                    # 테두리만 그린 사각형(셀·글상자 테두리)은 네 변을 선으로 본다.
                    rules += [
                        Rule(rect.x0, rect.y0, rect.x1, rect.y0),
                        Rule(rect.x0, rect.y1, rect.x1, rect.y1),
                        Rule(rect.x0, rect.y0, rect.x0, rect.y1),
                        Rule(rect.x1, rect.y0, rect.x1, rect.y1),
                    ]
    return rules


@dataclass
class PageInput:
    """쪽 하나의 판정 입력(pt). 렌더 없이 시험할 수 있게 레이아웃·PDF와 떼어 둔다."""

    width: float
    height: float
    reference: list[PlacedChar]
    rendered: list[PlacedChar]
    #: rendered와 같은 차례의 Glyph(size·advance·scale_x·rotation만 쓴다).
    glyphs: list[object]
    #: rendered 글자마다 담은 렌더 줄 번호(없으면 None).
    owners: list[int | None]
    rules: list[Rule]
    #: 렌더 비트맵 그림 상자(x, y, 폭, 높이).
    images: list[tuple[float, float, float, float]]
    reference_images: list[tuple[float, float, float, float]]
    #: 렌더가 글자 대신 벡터 그림(글맵시·그리기 개체 SVG)으로 그린 상자. 그 안 정답 글자는 빠짐으로 세지 않는다.
    drawn_as_picture: list[tuple[float, float, float, float]]
    #: 정답 선은 필요할 때만 PDF에서 읽는다(get_drawings가 느리다).
    reference_rules: object = None
    #: 수식 글꼴로 찍혀 reference에서 뺀 정답 글자. 렌더 수식 글자가 제자리인지 볼 때만 쓴다.
    equation: list[PlacedChar] = field(default_factory=list)


def _event(kind: str, line: float, y: float, detail: str, rendered_index: int | None = None) -> dict:
    return {"kind": kind, "line": line, "y": round(y, 2), "detail": detail, "rendered": rendered_index}


class _Nearby:
    """같은 글자가 문턱 안에 있는지 찾는다. 짝 맺기가 옆 단 같은 글자와 잘못 맺어도 흔들리지 않는다.

    sizes(pt)를 주면 문턱을 글자 크기의 BIG_GLYPH_SLACK배까지 넓힌다(큰 장식 숫자는 원점이 크게 옮겨진다).
    """

    def __init__(self, chars: list[PlacedChar], sizes: list[float] | None = None):
        self.chars = chars
        self.tolerance = [max(VERTICAL_SHIFT_PT, size * BIG_GLYPH_SLACK) for size in sizes] if sizes else None
        self.by_char: dict[str, list[int]] = collections.defaultdict(list)
        for index, char in enumerate(chars):
            self.by_char[char.char].append(index)

    def nearest(self, char: PlacedChar, tolerance: float | None = None) -> int | None:
        # 기본값을 호출 때 읽어 VERTICAL_SHIFT_PT 하나만 바꾸면 모든 판정이 따라가게 한다.
        tolerance = VERTICAL_SHIFT_PT if tolerance is None else tolerance
        best, best_distance = None, None
        for index in self.by_char.get(char.char, []):
            limit = max(tolerance, self.tolerance[index]) if self.tolerance else tolerance
            distance = math.dist((self.chars[index].x, self.chars[index].y), (char.x, char.y))
            if distance <= limit and (best_distance is None or distance < best_distance):
                best, best_distance = index, distance
        return best


def _is_pua(char: str) -> bool:
    return "\ue000" <= char[0] <= "\uf8ff" or char[0] >= "\U000f0000"


def _in_box(x: float, y: float, boxes) -> bool:
    return any(bx <= x <= bx + bw and by <= y <= by + bh for bx, by, bw, bh in boxes)


def judge_page(data: PageInput) -> list[dict]:
    """깨진 자리 목록(정답 줄 번호 차례). 비어 있으면 안 깨진 쪽이다."""
    reference, rendered = data.reference, data.rendered
    pairs = pair_chars(reference, rendered)
    lines = reference_lines(reference)
    line_of = {a: number for number, line in enumerate(lines) for a in line}
    sizes = [glyph.size / HU_PER_PT for glyph in data.glyphs]
    near_rendered, near_reference = _Nearby(rendered, sizes), _Nearby(reference)
    #: 렌더 글자마다 문턱 안 같은 정답 글자. 없으면 제자리에 있지 않은 글자다.
    home = [
        near_reference.nearest(char, max(VERTICAL_SHIFT_PT, size * BIG_GLYPH_SLACK))
        for char, size in zip(rendered, sizes, strict=True)
    ]
    near_equation = _Nearby(data.equation)
    #: 정답 글자(수식 글자 포함) 가운데 문턱 안에 같은 글자가 있는 렌더 글자.
    in_place = [
        home[b] is not None
        or near_equation.nearest(char, max(VERTICAL_SHIFT_PT, sizes[b] * BIG_GLYPH_SLACK)) is not None
        for b, char in enumerate(rendered)
    ]
    equation_glyph = [glyph.font_family.upper().startswith(EQUATION_FONTS[1]) for glyph in data.glyphs]
    events: list[dict] = []

    # 줄마다 상태: 글자의 반 넘게 문턱 안에 같은 렌더 글자가 없으면 밀린 줄(짝이 다른 자리에 있음)이나 빠진 줄이다.
    # PUA 글자는 한글이 글꼴 안 코드로 찍어 렌더와 글자 코드가 다를 수 있어(『』 등) 세지 않는다.
    deficit = collections.Counter(c.char for c in reference) - collections.Counter(c.char for c in rendered)
    states: list[tuple[str, list[int]]] = []
    for line in lines:
        counted = [a for a in line if not _is_pua(reference[a].char)]
        displaced = [
            a
            for a in counted
            if near_rendered.nearest(reference[a]) is None
            # 렌더가 벡터 그림(글맵시 등)으로 그린 자리의 글자는 글자로 찾지 않는다.
            and not _in_box(reference[a].x, reference[a].y, data.drawn_as_picture)
        ]
        if not displaced or len(displaced) * 2 <= len(counted):
            states.append(("제자리", displaced))
            continue
        # 빠진 글자: 짝이 없고, 쪽 전체에서도 렌더에 그 글자가 모자란다(줄 나눔이 달라 옮겨진 글자는 빠짐이 아니다).
        missing = []
        for a in displaced:
            if a not in pairs and deficit[reference[a].char] > 0:
                deficit[reference[a].char] -= 1
                missing.append(a)
        if missing and len(missing) * 2 >= len(displaced):
            states.append(("빠짐", missing))
            continue
        paired = [a for a in displaced if a in pairs]
        if len(paired) < SHIFT_MIN_CHARS or len(paired) * 2 < len(displaced):
            states.append(("제자리", displaced))
            continue
        dy = statistics.median(rendered[pairs[a]].y - reference[a].y for a in paired)
        dx = statistics.median(rendered[pairs[a]].x - reference[a].x for a in paired)
        # 옆으로만 옮겨진 줄(줄 나눔·자간 차이)은 표본에서 깨져 보이지 않았다.
        states.append(("세로 밀림" if abs(dy) >= abs(dx) else "제자리", displaced))

    line_y = [reference[line[0]].y for line in lines]
    for number, (state, displaced) in enumerate(states):
        if state == "빠짐":
            text = "".join(reference[a].char for a in displaced)[:30]
            events.append(
                _event("빠짐", number, line_y[number], f"{len(displaced)}/{len(lines[number])}자 없음: {text}")
            )
        elif state == "세로 밀림":
            # 「그 쪽 끝까지 어긋남」: 아래 줄의 반 이상도 밀리거나 빠졌을 때만 센다(칸 안 줄 나눔 차이는 뺀다).
            below = [states[k][0] for k in range(len(lines)) if line_y[k] > line_y[number] + 1]
            if below and sum(s != "제자리" for s in below) * 2 < len(below):
                continue
            paired = [a for a in displaced if a in pairs]
            dy = statistics.median(rendered[pairs[a]].y - reference[a].y for a in paired)
            first = min((pairs[a] for a in paired), key=lambda b: rendered[b].x)
            events.append(_event("세로 밀림", number, line_y[number], f"dy {dy:+.1f}pt", first))

    boxes = [
        None if glyph.rotation else glyph_box(glyph, char) for glyph, char in zip(data.glyphs, rendered, strict=True)
    ]

    def place(b: int) -> tuple[float, float]:
        if home[b] is not None:
            return line_of[home[b]], reference[home[b]].y
        # 제자리 아닌 글자는 그 아래 첫 정답 줄 앞에 둔다.
        below = [n for n, line in enumerate(lines) if reference[line[0]].y >= rendered[b].y]
        return (below[0] if below else len(lines)) - 0.5, rendered[b].y

    # 겹침(글자·글자): 다른 렌더 줄의 두 글자 몸통이 겹치는데, 둘 중 하나는 제자리에 있지 않다.
    # 둘 다 제자리면 정답도 그렇게 겹쳐 있다(덧말·겹친 개체).
    grid: dict[tuple[int, int], list[int]] = collections.defaultdict(list)
    for b, box in enumerate(boxes):
        if box is None or box.area() <= 0:
            continue
        for gx in range(int(box.x0 // 20), int(box.x1 // 20) + 1):
            for gy in range(int(box.y0 // 20), int(box.y1 // 20) + 1):
                grid[gx, gy].append(b)
    seen: set[tuple[int, int]] = set()
    for cell in grid.values():
        for i, b in enumerate(cell):
            for c in cell[i + 1 :]:
                key = (min(b, c), max(b, c))
                if key in seen or (data.owners[b] == data.owners[c] and data.owners[b] is not None):
                    continue
                seen.add(key)
                # 수식 안 글자 배치는 1단계 채점 범위 밖이고, 정답의 수식 획은 글자가 아닌 선으로 찍혀 비교할 수 없다.
                if (in_place[b] and in_place[c]) or equation_glyph[b] or equation_glyph[c]:
                    continue
                if boxes[b].overlap(boxes[c]) <= OVERLAP_RATIO * min(boxes[b].area(), boxes[c].area()):
                    continue
                line, y = place(b if home[b] is not None else c)
                events.append(_event("겹침", line, y, f"글자 {rendered[b].char!r}·{rendered[c].char!r}", b))

    # 겹침(글자·선): 렌더 선이 글자 몸통을 가르는데, 정답에서는 같은 글자를 가르는 선이 없다.
    crossed = [b for b, box in enumerate(boxes) if box is not None and any(rule.crosses(box) for rule in data.rules)]
    if crossed:
        reference_rules = data.reference_rules() if callable(data.reference_rules) else data.reference_rules
        for b in crossed:
            a = home[b]
            moved = (
                boxes[b]
                if a is None
                else boxes[b].moved(reference[a].x - rendered[b].x, reference[a].y - rendered[b].y)
            )
            if a is not None and any(rule.crosses(moved, margin=1.0) for rule in reference_rules):
                continue
            if a is None and in_place[b]:
                continue
            line, y = place(b)
            events.append(_event("겹침", line, y, f"선이 글자 {rendered[b].char!r}를 가름", b))

    # 겹침(글자·그림): 정답과 다른 자리에 놓인 렌더 비트맵 위의 글자가 정답에서는 어느 비트맵 위에도 없다.
    # 정답 PDF는 그림을 벡터로 바꿔 저장하기도 하므로(사용자 표본 도장 그림), 정답 쪽에 비트맵이 없으면 판정하지 않는다.
    moved_images = [
        (x, y, w, h)
        for x, y, w, h in data.images
        if data.reference_images
        and not any(
            max(abs(x - rx), abs(y - ry), abs(x + w - rx - rw), abs(y + h - ry - rh)) <= VERTICAL_SHIFT_PT
            for rx, ry, rw, rh in data.reference_images
        )
    ]
    expanded = [(x - 1, y - 1, w + 2, h + 2) for x, y, w, h in data.reference_images]
    for b, box in enumerate(boxes):
        if box is None or home[b] is None or not moved_images:
            continue
        if not any(
            x + 1 < box.x0 and box.x1 < x + w - 1 and y + 1 < box.y0 and box.y1 < y + h - 1
            for x, y, w, h in moved_images
        ):
            continue
        if _inside(reference[home[b]], expanded):
            continue
        line, y = place(b)
        events.append(_event("겹침", line, y, f"그림 위 글자 {rendered[b].char!r}", b))

    # 넘침: 쪽 밖으로 나간 글자. 정답 글 맨 아래보다 아래 글자는 세지 않는다(정답 PDF가 글자로 꺼내지 못한 글이 있다).
    # 수식 글자도 세지 않는다(수식 엔진이 쪽 아래에 둔 늘린 괄호 획이 있으나 보이지 않는다, 3-09월_교육_통합_2022 11쪽).
    for b, char in enumerate(rendered):
        box = boxes[b]
        if (
            box is not None
            and not equation_glyph[b]
            and (box.x0 < 0 or box.y0 < 0 or box.x1 > data.width or box.y1 > data.height)
        ):
            line, y = place(b)
            events.append(_event("넘침", line, y, f"쪽 밖 글자 {char.char!r}", b))

    events.sort(key=lambda event: (event["line"], KINDS.index(event["kind"])))
    return events


def _page_input(page, reference_all: list[PlacedChar], image_boxes, pdf_document, index: int) -> PageInput:
    boxes = [tuple(v / HU_PER_PT for v in box) for box in page.excluded]
    reference = [c for c in reference_all if not c.font.startswith(EQUATION_FONTS) and not _inside(c, boxes)]
    rendered, glyphs = rendered_chars(page)
    pictures = [item for item in page.items if isinstance(item, Image)]

    def box_of(item) -> tuple[float, float, float, float]:
        return tuple(v / HU_PER_PT for v in (item.x, item.y, item.width, item.height))

    cache: list[list[Rule]] = []

    def lazy_rules() -> list[Rule]:
        if not cache:
            cache.append(reference_rules(pdf_document[index]))
        return cache[0]

    return PageInput(
        width=page.width / HU_PER_PT,
        height=page.height / HU_PER_PT,
        reference=reference,
        equation=[c for c in reference_all if c.font.startswith(EQUATION_FONTS)],
        rendered=rendered,
        glyphs=glyphs,
        owners=glyph_lines(page, glyphs),
        rules=rendered_rules(page),
        images=[box_of(item) for item in pictures if not item.href.startswith("data:image/svg")],
        reference_images=image_boxes,
        drawn_as_picture=[box_of(item) for item in pictures if item.href.startswith("data:image/svg")],
        reference_rules=lazy_rules,
    )


def _has_nested_table(document) -> bool:
    """셀 안 문단이 표를 품은 문서인가(표 안의 표 문서군)."""
    context = _paragraph_context(document)
    return any(
        container == "셀" and any(kind.startswith("표") for kind in own.split("+"))
        for container, _, own, _ in context.values()
    )


def measure(target: Target) -> dict:
    if not target.reference.exists():
        return {"status": "unchecked", "reason": "정답 PDF 없음", "groups": list(target.groups)}
    install_trace()
    _TRACED.clear()
    _PARAGRAPH_CONTEXT.clear()
    groups = list(target.groups)
    try:
        document = HwpDocument.open(target.path, target.password)
        pages = layout.layout_document(document, RenderOptions.from_environment())
    except Exception as error:  # 렌더링 실패도 검사불가로 센다. 멈추지 않는다.
        return {"status": "render-error", "reason": f"{type(error).__name__}: {error}", "groups": groups}
    try:
        if _has_nested_table(document):
            groups.append("표 안의 표")
        reference = pdf_pages(str(target.reference))
        image_boxes = pdf_image_boxes(str(target.reference))
        records = []
        with pymupdf.open(str(target.reference)) as pdf_document:
            for index in range(max(len(pages), len(reference))):
                records.append(_page_record(index, pages, reference, image_boxes, pdf_document))
        return {
            "status": "ok",
            "groups": groups,
            "pages": len(pages),
            "reference_pages": len(reference),
            "page_records": records,
        }
    except Exception as error:  # 한 문서의 검사 실패는 검사불가로 따로 센다.
        return {"status": "check-error", "reason": f"{type(error).__name__}: {error}", "groups": groups}
    finally:
        _TRACED.clear()
        _PARAGRAPH_CONTEXT.clear()


def _page_record(index: int, pages, reference, image_boxes, pdf_document) -> dict:
    record = {"page": index + 1, "kinds": [], "first": None, "events": 0}
    if index >= len(reference):
        record["kinds"] = ["넘침"]
        record["first"] = {"kind": "넘침", "detail": "정답에 없는 쪽", "container": "쪽 수 다름"}
        record["events"] = 1
        return record
    if index >= len(pages):
        record["kinds"] = ["빠짐"]
        record["first"] = {"kind": "빠짐", "detail": "렌더에 없는 쪽", "container": "쪽 수 다름"}
        record["events"] = 1
        return record
    page = pages[index]
    data = _page_input(page, reference[index], image_boxes[index], pdf_document, index)
    events = judge_page(data)
    if not events:
        return record
    record["kinds"] = [kind for kind in KINDS if any(event["kind"] == kind for event in events)]
    record["events"] = len(events)
    first = events[0]
    owner = data.owners[first["rendered"]] if first["rendered"] is not None else None
    lines = reference_lines(data.reference)
    line_number = math.floor(first["line"]) if first["line"] >= 0 else 0
    reference_text = (
        "".join(data.reference[a].char for a in lines[line_number])[:40] if line_number < len(lines) else ""
    )
    record["first"] = {
        "kind": first["kind"],
        "line": first["line"],
        "y": first["y"],
        "detail": first["detail"],
        "reference_text": reference_text,
        **_line_record(page, owner),
    }
    record["by_kind"] = dict(collections.Counter(event["kind"] for event in events))
    return record


def cause_key(first: dict) -> str:
    """처음 깨진 줄의 특징으로 묶는 원인 열쇠: 종류 / 담은 목록 / 앞 문단 / 줄 상태 / 첫 줄 여부."""
    parts = [first["kind"], first.get("container", "모름")]
    if "previous" in first:
        parts.append("앞 " + first["previous"])
    if "line_state" in first:
        parts.append(first["line_state"])
    if "line_number" in first:
        parts.append("첫 줄" if first["line_number"] == 0 else "둘째 줄 이후")
    return " / ".join(parts)


def summarize(results: dict[str, dict]) -> dict:
    groups: dict[str, dict] = {}

    def group(name: str) -> dict:
        return groups.setdefault(
            name,
            {
                "documents": 0,
                "passed": 0,
                "failed": 0,
                "unchecked": 0,
                "pages": 0,
                "broken_pages": 0,
                "kinds": dict.fromkeys(KINDS, 0),
            },
        )

    causes: dict[str, dict] = {}
    for name, result in results.items():
        for group_name in result.get("groups", []):
            row = group(group_name)
            row["documents"] += 1
            if result["status"] != "ok":
                row["unchecked"] += 1
                continue
            broken = [page for page in result["page_records"] if page["kinds"]]
            row["pages"] += len(result["page_records"])
            row["broken_pages"] += len(broken)
            row["failed" if broken else "passed"] += 1
            for page in broken:
                for kind in page["kinds"]:
                    row["kinds"][kind] += 1
        if result["status"] != "ok":
            continue
        user = "사용자 표본" in result.get("groups", [])
        for page in result["page_records"]:
            if not page["first"]:
                continue
            key = cause_key(page["first"])
            row = causes.setdefault(key, {"key": key, "pages": 0, "user_pages": 0, "documents": set(), "examples": []})
            row["pages"] += 1
            row["user_pages"] += user
            row["documents"].add(name)
            row["examples"].append(f"{Path(name).stem}:{page['page']}")
    ranked = sorted(causes.values(), key=lambda row: (row["user_pages"] == 0, -row["pages"]))
    for row in ranked:
        row["documents"] = len(row["documents"])
        row["examples"] = row["examples"][:3]
    return {"groups": groups, "causes": ranked}


def markdown(summary: dict, limit: int) -> str:
    out = ["# 깨진 쪽 집계", "", f"세로 밀림 문턱 {VERTICAL_SHIFT_PT}pt.", ""]
    out += ["| 문서군 | 문서 | 통과 | 실패 | 검사불가 | 쪽 | 깨진 쪽 | " + " | ".join(KINDS) + " |"]
    out.append("|---" * (7 + len(KINDS)) + "|")
    for name, row in summary["groups"].items():
        kinds = " | ".join(str(row["kinds"][kind]) for kind in KINDS)
        out.append(
            f"| {name} | {row['documents']} | {row['passed']} | {row['failed']} | {row['unchecked']} | "
            f"{row['pages']} | {row['broken_pages']} | {kinds} |"
        )
    out += ["", "## 원인 묶음(처음 깨진 줄 특징, 사용자 표본에서 나온 묶음 먼저)", ""]
    out.append("| 순위 | 묶음 | 깨진 쪽 | 사용자 표본 쪽 | 문서 | 예(문서:쪽) |")
    out.append("|---|---|---|---|---|---|")
    for rank, row in enumerate(summary["causes"][:limit], start=1):
        out.append(
            f"| {rank} | {row['key']} | {row['pages']} | {row['user_pages']} | {row['documents']} | "
            f"{', '.join(row['examples'])} |"
        )
    return "\n".join(out) + "\n"


def targets(match: str = "") -> list[Target]:
    found = []
    for entry in load_corpus():
        relative = entry.path.relative_to(CORPUS_ROOT)
        groups = ["전체"]
        if relative.parts[0] == "public" and entry.path.name.startswith("kr_"):
            groups.append("공공 kr_*")
        found.append(
            Target(
                str(relative), entry.path, REFERENCE_ROOT / relative.with_suffix(".pdf"), entry.password, tuple(groups)
            )
        )
    user_root = os.environ.get("REA_HWP_USER_CORPUS")
    if user_root:
        for path in sorted(Path(user_root).glob("*.hwp")):
            reference = path.parent / "ref2022" / path.with_suffix(".pdf").name
            found.append(Target(f"user/{path.name}", path, reference, None, ("사용자 표본",)))
    return [target for target in found if match in target.name]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True, help="pages.json과 summary.md를 쓸 폴더")
    parser.add_argument("--match", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--rows", type=int, default=10, help="원인 묶음 표에 보일 줄 수")
    args = parser.parse_args()
    chosen = targets(args.match)
    if args.limit:
        chosen = chosen[: args.limit]
    results = {}
    with ProcessPoolExecutor(args.jobs) as pool:
        for target, result in zip(chosen, pool.map(measure, chosen), strict=True):
            results[target.name] = result
    summary = summarize(results)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "pages.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (args.out / "summary.md").write_text(markdown(summary, args.rows), encoding="utf-8")
    for name, result in results.items():
        if result["status"] != "ok":
            print(f"검사불가 {result['status']}: {name}: {result['reason']}")
    print(json.dumps(summary["groups"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
