"""쪽마다 정답 PDF와 렌더를 맞대 깨진 쪽(세로 밀림·겹침·빠짐·넘침·차트 깨짐)과 처음 깨진 줄을 센다.
쪽 나눔 위치만 다른 쪽은 「쪽 나눔 차이」로 따로 센다. 문서는 통과·실패·검사불가(렌더·검사 실패, 정답 없음)로 나눈다.

사용: uv run --extra bench python bench/breakage.py --out <폴더> [--match 이름일부] [--limit N] [--jobs N]
REA_HWP_USER_CORPUS가 있으면 사용자 표본(그 폴더의 *.hwp, 정답은 ref2022/)도 함께 센다.
"""

from __future__ import annotations

import argparse
import base64
import collections
import colorsys
import json
import math
import os
import re
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
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

from rea_hwp import tags
from rea_hwp.document import HwpDocument
from rea_hwp.models.controls import ObjectCtrlHeader
from rea_hwp.models.shapes import ShapeOle
from rea_hwp.render import layout
from rea_hwp.render.chart import vtchart_contents
from rea_hwp.render.ole import has_ooxml_chart
from rea_hwp.render.options import RenderOptions
from rea_hwp.render.scene import Glyph, Image, Line, Rect
from rea_hwp.render.svg import HU_PER_PT, image_data

#: 밀림 문턱(pt). 정답 글자와 같은 글자가 렌더에서 이 거리 안에 없으면 제자리에 있지 않은 글자다.
#: 사용자가 문서 댓글로 바꿀 수 있는 값이라 하나로 둔다. 근거는 표본 그림 확인(작업 1 결과, 2026-10-24).
VERTICAL_SHIFT_PT = 5.0
#: 세로 밀림으로 볼 정답 줄의 최소 짝 글자 수. 한 글자 짝은 다른 자리 같은 글자와 잘못 맺히기 쉽다(추정).
SHIFT_MIN_CHARS = 2
#: 단·칸 조각을 가르는 글자 원점 간격(pt)과 줄 간격 중앙값 배수. 글자 사이 띄어쓰기(한 칸+글자 폭)보다 크게 잡은 추정값.
COLUMN_GAP_PT = 15.0
COLUMN_GAP_STEPS = 2.5
#: 겹침으로 볼 두 글자 몸통의 겹친 넓이 비율(작은 글자 기준). 추정값.
OVERLAP_RATIO = 0.3

#: 큰 글자의 제자리 문턱(글자 크기 배수). 쪽 크기 장식 숫자가 글꼴 차이로 문턱 넘게 옮겨진 표본 하나로 정한 추정값.
BIG_GLYPH_SLACK = 0.1

KINDS = ("세로 밀림", "겹침", "빠짐", "넘침", "차트 깨짐")
#: 렌더 수식 글자의 글꼴 이름 앞부분(대문자). 옛 엔진은 HYhwpEQ, 새 엔진은 HancomEQN으로 그린다.
EQUATION_GLYPH_FONTS = ("HYHWPEQ", "HANCOMEQN")
#: 쪽 나눔 차이로 볼 때 옮겨 간 글을 찾는 이웃 쪽 앞·뒤 구간(그 쪽 글자 수 비율과 최소 글자 수). 추정값.
EDGE_SHARE = 0.4
EDGE_MIN_CHARS = 300
#: 차트 영역 비교 문턱. 차트 26영역 그림 확인(작업 3 결과, 2026-10-24)으로 정했다.
#: 우리 잉크(흰색 아닌 화소)가 정답의 이 비율 아래면 「빔」이다.
CHART_EMPTY_RATIO = 0.3
#: 칸(CHART_GRID×CHART_GRID)별 잉크 밀도 차 평균이 이보다 크면 「모양 다름」이다(같은 차트 0.016~0.029).
CHART_GRID = 8
CHART_GRID_DIFF = 0.035
#: 색상 구간별 색 화소 비율 차의 합이 영역의 이 비율을 넘고, 색 화소 가운데 CHART_COLOR_SHARE보다 크면 「색 다름」이다
#: (빠진 계열 표식처럼 작은 색 획을 잡는다).
CHART_HUE_BINS = 12
CHART_COLOR_DIFF = 0.0015
CHART_COLOR_SHARE = 0.5
#: 단·칸 맨 아래 조각만 밀린 경우. 꼬리말·쪽 번호가 쪽마다 밀려 문서 전체가 깨짐으로 잡히지 않게 깨짐과 따로 센다.
TAIL_SHIFT = "꼬리 밀림"


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
        elif isinstance(item, Rect) and (item.stroke or item.fill):
            x, y, w, h = (v / HU_PER_PT for v in (item.x, item.y, item.width, item.height))
            if min(w, h) < 1.5:
                rules.append(Rule(x, y + h / 2, x + w, y + h / 2) if h < w else Rule(x + w / 2, y, x + w / 2, y + h))
            elif item.stroke and item.stroke_width > 0:
                # 테두리만 그린 사각형(셀·글상자 테두리)은 네 변을 선으로 본다(reference_rules와 같은 기준).
                rules += [
                    Rule(x, y, x + w, y),
                    Rule(x, y + h, x + w, y + h),
                    Rule(x, y, x, y + h),
                    Rule(x + w, y, x + w, y + h),
                ]
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


def column_segments(reference: list[PlacedChar], lines: list[list[int]]) -> list[list[int]]:
    """정답 줄을 단·칸 조각으로 나눈다. reference_lines는 같은 기준선의 옆 단·옆 칸 글을 한 줄로 묶는다.

    이웃 글자 원점 간격이 그 줄 간격 중앙값의 COLUMN_GAP_STEPS배와 COLUMN_GAP_PT 가운데 큰 값을 넘으면 새 조각이다.
    """
    segments = []
    for line in lines:
        steps = [reference[b].x - reference[a].x for a, b in zip(line, line[1:], strict=False)]
        positive = [step for step in steps if step > 0]
        limit = max(COLUMN_GAP_PT, COLUMN_GAP_STEPS * statistics.median(positive)) if positive else COLUMN_GAP_PT
        current = [line[0]]
        for previous, index in zip(line, line[1:], strict=False):
            if reference[index].x - reference[previous].x > limit:
                segments.append(current)
                current = []
            current.append(index)
        segments.append(current)
    return segments


def _in_box(x: float, y: float, boxes) -> bool:
    return any(bx <= x <= bx + bw and by <= y <= by + bh for bx, by, bw, bh in boxes)


def judge_page(data: PageInput, notes: dict | None = None) -> list[dict]:
    """깨진 자리 목록(정답 줄 번호 차례). 비어 있으면 안 깨진 쪽이다.

    notes를 주면 쪽 나눔 차이 판정에 쓸 「정답 이 쪽에 없는 렌더 글 덩어리」(extra_runs, 렌더 차례)를 담는다.
    """
    reference, rendered = data.reference, data.rendered
    pairs = pair_chars(reference, rendered)
    lines = column_segments(reference, reference_lines(reference))
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
    equation_glyph = [glyph.font_family.upper().startswith(EQUATION_GLYPH_FONTS) for glyph in data.glyphs]
    events: list[dict] = []
    if notes is not None:
        paired_rendered = set(pairs.values())
        runs: list[str] = []
        current = ""
        for b, char in enumerate(rendered):
            if home[b] is None and b not in paired_rendered and not _is_pua(char.char):
                current += char.char
            elif current:
                runs.append(current)
                current = ""
        notes["extra_runs"] = [*runs, current] if current else runs

    def text_of(number: float) -> str:
        index = math.ceil(number)
        return "".join(reference[a].char for a in lines[index])[:40] if 0 <= index < len(lines) else ""

    def event(kind: str, number: float, y: float, detail: str, rendered_index: int | None = None) -> None:
        events.append(_event(kind, number, y, detail, rendered_index) | {"reference_text": text_of(number)})

    # 단·칸 조각마다 상태: 글자의 반 넘게 문턱 안에 같은 렌더 글자가 없으면 밀린 조각이나 빠진 조각이다.
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
        # 빠짐 조각이 아니면 쓴 부족 수를 돌려준다(뒤에 오는 진짜 빠진 조각이 쓸 수 있게).
        need: collections.Counter[str] = collections.Counter()
        missing = []
        for a in displaced:
            char = reference[a].char
            if a not in pairs and deficit[char] - need[char] > 0:
                need[char] += 1
                missing.append(a)
        if missing and len(missing) * 2 >= len(displaced):
            deficit.subtract(need)
            states.append(("빠짐", missing))
            continue
        paired = [a for a in displaced if a in pairs]
        if len(paired) < SHIFT_MIN_CHARS or len(paired) * 2 < len(displaced):
            states.append(("제자리", displaced))
            continue
        dy = statistics.median(rendered[pairs[a]].y - reference[a].y for a in paired)
        dx = statistics.median(rendered[pairs[a]].x - reference[a].x for a in paired)
        # 옆으로만 옮겨진 줄(줄 나눔·자간 차이)은 표본에서 깨져 보이지 않았다. 세로 차가 문턱 아래인 줄도 세지 않는다.
        shifted = abs(dy) > VERTICAL_SHIFT_PT and abs(dy) >= abs(dx)
        states.append(("세로 밀림" if shifted else "제자리", displaced))

    line_y = [reference[line[0]].y for line in lines]
    spans = [(min(reference[a].x for a in line), max(reference[a].x for a in line)) for line in lines]
    for number, (state, displaced) in enumerate(states):
        if state == "빠짐":
            run = "".join(reference[a].char for a in displaced)
            event("빠짐", number, line_y[number], f"{len(displaced)}/{len(lines[number])}자 없음: {run[:30]}")
            events[-1]["run"] = run
        elif state == "세로 밀림":
            # 「그 쪽 끝까지 어긋남」: 같은 단·칸(가로로 겹치는 조각)의 아래 조각 반 이상도 밀리거나 빠졌을 때만 센다.
            x0, x1 = spans[number]
            below = [
                states[k][0]
                for k in range(len(lines))
                if line_y[k] > line_y[number] + 1 and spans[k][0] <= x1 and spans[k][1] >= x0
            ]
            if not below:
                # 단·칸의 맨 아래 조각만 밀림(꼬리말·쪽 번호 등): 깨짐이 아니라 따로 센다.
                event(TAIL_SHIFT, number, line_y[number], "맨 아래 조각만 밀림")
                continue
            if sum(s != "제자리" for s in below) * 2 < len(below):
                continue
            paired = [a for a in displaced if a in pairs]
            dy = statistics.median(rendered[pairs[a]].y - reference[a].y for a in paired)
            first = min((pairs[a] for a in paired), key=lambda b: rendered[b].x)
            event("세로 밀림", number, line_y[number], f"dy {dy:+.1f}pt", first)

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
                event("겹침", line, y, f"글자 {rendered[b].char!r}·{rendered[c].char!r}", b)

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
            event("겹침", line, y, f"선이 글자 {rendered[b].char!r}를 가름", b)

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
        event("겹침", line, y, f"그림 위 글자 {rendered[b].char!r}", b)

    # 넘침: 쪽 밖으로 나간 글자. 정답도 같은 자리에 그 글자를 둔 경우(제자리)는 세지 않는다.
    # 수식 글자도 세지 않는다(수식 엔진이 쪽 아래에 둔 늘린 괄호 획이 있으나 보이지 않는다, 3-09월_교육_통합_2022 11쪽).
    for b, char in enumerate(rendered):
        box = boxes[b]
        if (
            box is not None
            and not equation_glyph[b]
            and home[b] is None
            and (box.x0 < 0 or box.y0 < 0 or box.x1 > data.width or box.y1 > data.height)
        ):
            line, y = place(b)
            event("넘침", line, y, f"쪽 밖 글자 {char.char!r}", b)

    events.sort(key=lambda event: (event["line"], (*KINDS, TAIL_SHIFT).index(event["kind"])))
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


def chart_object_sizes(document) -> list[tuple[int, int]]:
    """차트 OLE(VtChart 또는 OOXML 차트)를 담은 개체의 크기(HU). 채점 제외 영역 가운데 차트를 고르는 데 쓴다."""
    sizes = []
    pending = [(node, None) for section in document.sections for node in section.nodes]
    while pending:
        node, owner = pending.pop()
        if isinstance(node.model, ObjectCtrlHeader):
            owner = node.model
        if node.tag == tags.SHAPE_COMPONENT_OLE and isinstance(node.model, ShapeOle) and owner is not None:
            item = document.bin_data(node.model.bin_data_id)
            if item is not None and (vtchart_contents(item.data) is not None or has_ooxml_chart(item.data)):
                sizes.append((owner.width, owner.height))
        pending.extend((child, owner) for child in node.children)
    return sizes


def chart_regions(page, sizes: list[tuple[int, int]]) -> list[tuple[float, float, float, float]]:
    """채점에서 뺀 개체 영역(pt) 가운데 크기가 차트 OLE 개체와 같은 것. 수식·그림 OLE(수식 편집기 등)는 뺀다."""
    regions = []
    for box in page.excluded:
        x, y, w, h = box
        if any(abs(w - cw) <= max(2, cw * 0.01) and abs(h - ch) <= max(2, ch * 0.01) for cw, ch in sizes):
            regions.append(tuple(v / HU_PER_PT for v in box))
    return regions


def _rgb(color: str | None) -> tuple[float, float, float] | None:
    if not color or not color.startswith("#") or len(color) != 7:
        return None
    return tuple(int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))


_DATA_IMAGE = re.compile(rb'href="data:image/([a-z0-9.+-]+);base64,([^"]+)"')


def _svg_for_mupdf(content: bytes) -> bytes:
    """SVG 안 data URI 그림을 PNG로 바꾼다. MuPDF는 SVG 안 BMP·SVG data URI 그림을 무시한다(WMF 차트가 비어 보임)."""

    def as_png(match: re.Match) -> bytes:
        data = base64.b64decode(match.group(2))
        if match.group(1) == b"svg+xml":
            with pymupdf.open(stream=_svg_for_mupdf(data), filetype="svg") as svg:
                pixmap = svg[0].get_pixmap(alpha=True)
        else:
            pixmap = pymupdf.Pixmap(data)
        return b'href="data:image/png;base64,' + base64.b64encode(pixmap.tobytes("png")) + b'"'

    return _DATA_IMAGE.sub(as_png, content)


def render_region(page, box: tuple[float, float, float, float]) -> pymupdf.Pixmap:
    """우리 쪽 항목 가운데 box(pt)에 걸친 것을 1pt=1화소로 그린다. 그림 안 SVG는 PDF로 바꿔 넣는다(MuPDF가 무시한다)."""
    x, y, w, h = box
    canvas = pymupdf.open()
    target = canvas.new_page(width=w, height=h)
    area = pymupdf.Rect(0, 0, w, h)
    for item in page.items:
        if isinstance(item, Image):
            rect = pymupdf.Rect(item.x, item.y, item.x + item.width, item.y + item.height) / HU_PER_PT
            rect = rect + (-x, -y, -x, -y)
            decoded = image_data(item.href)
            if decoded is None or not rect.intersects(area) or rect.is_empty:
                continue
            content, mime = decoded
            if mime == "image/svg+xml":
                with pymupdf.open(stream=_svg_for_mupdf(content), filetype="svg") as svg:
                    pdf = pymupdf.open("pdf", svg.convert_to_pdf())
                target.show_pdf_page(rect, pdf, 0, keep_proportion=False)
            else:
                target.insert_image(rect, stream=content, keep_proportion=False)
        elif isinstance(item, Rect):
            rect = pymupdf.Rect(item.x, item.y, item.x + item.width, item.y + item.height) / HU_PER_PT
            rect = rect + (-x, -y, -x, -y)
            if rect.intersects(area):
                stroke = _rgb(item.stroke)
                target.draw_rect(
                    rect, color=stroke, fill=_rgb(item.fill), width=item.stroke_width / HU_PER_PT if stroke else 0
                )
        elif isinstance(item, Line):
            start = pymupdf.Point(item.x1 / HU_PER_PT - x, item.y1 / HU_PER_PT - y)
            end = pymupdf.Point(item.x2 / HU_PER_PT - x, item.y2 / HU_PER_PT - y)
            if pymupdf.Rect(start, end).normalize().intersects(area) or start in area:
                target.draw_line(start, end, color=_rgb(item.color) or (0, 0, 0), width=item.width / HU_PER_PT)
        elif isinstance(item, Glyph) and item.char.strip():
            origin = pymupdf.Point(item.x / HU_PER_PT - x, item.y / HU_PER_PT - y)
            if origin in area:
                target.insert_text(
                    origin, item.char, fontsize=item.size / HU_PER_PT, fontname="korea", color=_rgb(item.color)
                )
    return target.get_pixmap(alpha=False)


def ink_grid(pixmap: pymupdf.Pixmap) -> tuple[float, list[float], list[float]]:
    """흰색 아닌 화소 비율, CHART_GRID×CHART_GRID 칸별 비율, 색 화소의 색상 구간(CHART_HUE_BINS개)별 비율.

    흰색 아닌 화소는 세 채널 가운데 가장 어두운 값이 200 아래, 색 화소는 채널 차가 60 넘는 화소다.
    """
    width, height, samples, n = pixmap.width, pixmap.height, pixmap.samples, pixmap.n
    cells = [0] * (CHART_GRID * CHART_GRID)
    sizes = [0] * (CHART_GRID * CHART_GRID)
    hues = [0] * CHART_HUE_BINS
    total = 0
    for row in range(height):
        base = row * pixmap.stride
        cell_row = row * CHART_GRID // height * CHART_GRID
        for column in range(width):
            offset = base + column * n
            cell = cell_row + column * CHART_GRID // width
            sizes[cell] += 1
            red, green, blue = samples[offset : offset + 3]
            if min(red, green, blue) < 200:
                cells[cell] += 1
                total += 1
            if max(red, green, blue) - min(red, green, blue) > 60:
                hue = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)[0]
                hues[int(hue * CHART_HUE_BINS) % CHART_HUE_BINS] += 1
    area = max(width * height, 1)
    return total / area, [c / s if s else 0.0 for c, s in zip(cells, sizes, strict=True)], [h / area for h in hues]


def judge_chart(ours: pymupdf.Pixmap, reference: pymupdf.Pixmap) -> dict:
    """차트 영역 하나의 비교 값과 깨짐 이유(없으면 None)."""
    ink_ours, grid_ours, hues_ours = ink_grid(ours)
    ink_reference, grid_reference, hues_reference = ink_grid(reference)
    difference = statistics.fmean(abs(a - b) for a, b in zip(grid_ours, grid_reference, strict=True))
    color = sum(abs(a - b) for a, b in zip(hues_ours, hues_reference, strict=True))
    color_share = color / max(sum(hues_reference), sum(hues_ours), 1e-9)
    reason = None
    if ink_ours < CHART_EMPTY_RATIO * ink_reference:
        reason = "빔"
    elif difference > CHART_GRID_DIFF:
        reason = "모양 다름"
    elif color > CHART_COLOR_DIFF and color_share > CHART_COLOR_SHARE:
        reason = "색 다름"
    return {
        "ink_ours": round(ink_ours, 4),
        "ink_reference": round(ink_reference, 4),
        "grid_difference": round(difference, 4),
        "color_difference": round(color, 4),
        "color_share": round(color_share, 2),
        "reason": reason,
    }


def _search_runs(runs: list[str], text: str) -> list[int] | None:
    """runs가 text 안에 차례대로 나오는 자리. 하나라도 없으면 None."""
    found, start = [], 0
    for run in runs:
        at = text.find(run, start)
        if at < 0:
            return None
        found.append(at)
        start = at + len(run)
    return found


def _edge(text: str, head: bool) -> str:
    size = max(EDGE_MIN_CHARS, int(len(text) * EDGE_SHARE))
    return text[:size] if head else text[-size:]


def pagination_moves(index: int, records: list[dict], texts: list[dict]) -> list[str] | None:
    """index 쪽이 쪽 나눔 위치만 다른 쪽이면 옮겨 간 글 목록, 아니면 None.

    빠진 글은 우리 다음 쪽 앞이나 앞 쪽 끝에 차례대로 있어야 한다. 세로 밀림은 쪽 맨 위 내용이 바뀐 경우(앞 쪽에서
    넘어온 글이 있거나 맨 위 글이 앞 쪽으로 갔음)에만 받아들인다. 겹침·넘침·차트 깨짐이 있으면 깨진 쪽이다.
    """
    kinds = set(records[index]["kinds"])
    if not kinds or kinds - {"세로 밀림", "빠짐"}:
        return None
    previous = texts[index - 1] if index > 0 else None
    following = texts[index + 1] if index + 1 < len(texts) else None
    moves, down, up = [], [], []
    for run in texts[index]["missing_runs"]:
        if following is not None and _search_runs([*down, run], _edge(following["ours"], head=True)) is not None:
            down.append(run)
            moves.append(f"다음 쪽 앞으로: {run[:20]}")
        elif previous is not None and _search_runs([*up, run], _edge(previous["ours"], head=False)) is not None:
            up.append(run)
            moves.append(f"앞 쪽 끝으로: {run[:20]}")
        else:
            return None
    if "세로 밀림" in kinds:
        gained = [
            run
            for run in texts[index]["extra_runs"]
            if previous is not None and len(run) >= 2 and run in _edge(previous["reference"], head=False)
        ]
        if not up and not gained:
            return None
        moves += [f"앞 쪽에서 넘어옴: {run[:20]}" for run in gained]
    return moves


def _has_nested_table(document) -> bool:
    """셀 안 문단이 표를 품은 문서인가(표 안의 표 문서군)."""
    context = _paragraph_context(document)
    return any(
        container == "셀" and any(kind.startswith("표") for kind in own.split("+"))
        for container, _, own, _ in context.values()
    )


def measure(target: Target) -> dict:
    groups = list(target.groups)
    if not target.reference.exists():
        return {"status": "unchecked", "reason": "정답 PDF 없음", "groups": groups}
    try:
        install_trace()
        _TRACED.clear()
        _PARAGRAPH_CONTEXT.clear()
        try:
            document = HwpDocument.open(target.path, target.password)
            # 렌더가 실패해도 문서군에 들도록 레이아웃 전에 표 안의 표 문서인지 본다.
            if _has_nested_table(document):
                groups.append("표 안의 표")
            pages = layout.layout_document(document, RenderOptions.from_environment())
        except Exception as error:  # 렌더링 실패(MemoryError 포함)도 검사불가로 센다. 멈추지 않는다.
            return {"status": "render-error", "reason": f"{type(error).__name__}: {error}", "groups": groups}
        reference = pdf_pages(str(target.reference))
        image_boxes = pdf_image_boxes(str(target.reference))
        records, texts = [], []
        charts = chart_object_sizes(document)
        with pymupdf.open(str(target.reference)) as pdf_document:
            for index in range(max(len(pages), len(reference))):
                record, page_texts = _page_record(index, pages, reference, image_boxes, pdf_document, charts)
                records.append(record)
                texts.append(page_texts)
        # 이웃 쪽 글이 다 모인 뒤에 쪽 나눔 차이를 가른다. 원래 깨짐 종류(kinds)로 판정하므로 차례가 결과를 안 바꾼다.
        moves = [pagination_moves(index, records, texts) for index in range(len(records))]
        for record, moved in zip(records, moves, strict=True):
            if moved is not None:
                record["pagination"] = moved
                record["pagination_first"] = record["first"]
                record["kinds"], record["first"] = [], None
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


def measure_all(chosen: list[Target], jobs: int, worker=measure) -> dict[str, dict]:
    """문서마다 worker를 돌린다. 프로세스가 죽으면 남은 문서를 하나씩 다시 돌려 죽인 문서만 검사불가로 둔다."""
    results: dict[str, dict] = {}
    retry: list[Target] = []
    with ProcessPoolExecutor(jobs) as pool:
        futures = {pool.submit(worker, target): target for target in chosen}
        for future in as_completed(futures):
            target = futures[future]
            try:
                results[target.name] = future.result()
            except BrokenProcessPool:
                retry.append(target)
            except Exception as error:  # worker 밖으로 나온 실패도 그 문서만 검사불가로 센다.
                results[target.name] = _crashed(target, error)
    for target in retry:
        with ProcessPoolExecutor(1) as pool:
            try:
                results[target.name] = pool.submit(worker, target).result()
            except Exception as error:  # 혼자 돌려도 프로세스가 죽은 문서다.
                results[target.name] = _crashed(target, error)
    return {target.name: results[target.name] for target in chosen}


def _crashed(target: Target, error: BaseException) -> dict:
    return {"status": "check-error", "reason": f"{type(error).__name__}: {error}", "groups": list(target.groups)}


def _page_record(index: int, pages, reference, image_boxes, pdf_document, charts) -> tuple[dict, dict]:
    """쪽 하나의 기록과, 쪽 나눔 차이 판정에 쓸 글(우리·정답 쪽 글, 빠진 글, 정답 이 쪽에 없는 렌더 글)."""
    record = {"page": index + 1, "kinds": [], "first": None, "events": 0}
    texts = {"ours": "", "reference": "", "missing_runs": [], "extra_runs": []}
    if index >= len(reference):
        record["kinds"] = ["넘침"]
        record["first"] = {"kind": "넘침", "detail": "정답에 없는 쪽", "container": "쪽 수 다름"}
        record["events"] = 1
        return record, texts
    if index >= len(pages):
        record["kinds"] = ["빠짐"]
        record["first"] = {"kind": "빠짐", "detail": "렌더에 없는 쪽", "container": "쪽 수 다름"}
        record["events"] = 1
        return record, texts
    page = pages[index]
    data = _page_input(page, reference[index], image_boxes[index], pdf_document, index)
    notes: dict = {}
    found = judge_page(data, notes)
    found += _chart_events(page, pdf_document[index], record, charts)
    texts = {
        "ours": "".join(c.char for c in data.rendered if not _is_pua(c.char)),
        "reference": "".join(c.char for c in data.reference if not _is_pua(c.char)),
        "missing_runs": [event["run"] for event in found if event["kind"] == "빠짐"],
        "extra_runs": notes["extra_runs"],
    }
    tails = [event for event in found if event["kind"] == TAIL_SHIFT]
    if tails:
        record["tail_shift"] = [event["reference_text"] for event in tails]
    events = [event for event in found if event["kind"] != TAIL_SHIFT]
    if not events:
        return record, texts
    record["kinds"] = [kind for kind in KINDS if any(event["kind"] == kind for event in events)]
    record["events"] = len(events)
    first = events[0]
    owner = data.owners[first["rendered"]] if first["rendered"] is not None else None
    record["first"] = {
        "kind": first["kind"],
        "line": first["line"],
        "y": first["y"],
        "detail": first["detail"],
        "reference_text": first["reference_text"],
        **_line_record(page, owner),
    }
    record["by_kind"] = dict(collections.Counter(event["kind"] for event in events))
    return record, texts


def _chart_events(page, pdf_page, record: dict, charts: list[tuple[int, int]]) -> list[dict]:
    """차트·OLE 영역마다 우리 그림과 정답 PDF 그림을 견줘 깨진 영역을 「차트 깨짐」 사건으로 돌려준다."""
    events = []
    for box in chart_regions(page, charts):
        x, y, w, h = box
        reference = pdf_page.get_pixmap(clip=pymupdf.Rect(x, y, x + w, y + h), alpha=False)
        result = judge_chart(render_region(page, box), reference) | {"box": [round(v, 1) for v in box]}
        record.setdefault("charts", []).append(result)
        if result["reason"]:
            ink = f"잉크 {result['ink_ours']}/{result['ink_reference']}"
            detail = f"차트 {result['reason']}({ink}, 칸 차 {result['grid_difference']})"
            events.append(_event("차트 깨짐", -1, y, detail) | {"reference_text": ""})
    return events


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
                "tail_shift_pages": 0,
                "pagination_pages": 0,
                "chart_regions": 0,
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
            row["tail_shift_pages"] += sum("tail_shift" in page for page in result["page_records"])
            row["pagination_pages"] += sum("pagination" in page for page in result["page_records"])
            row["chart_regions"] += sum(len(page.get("charts", [])) for page in result["page_records"])
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
    out += [
        "| 문서군 | 문서 | 통과 | 실패 | 검사불가 | 쪽 | 깨진 쪽 | "
        + " | ".join(KINDS)
        + f" | {TAIL_SHIFT} 쪽 | 쪽 나눔 차이 쪽 | 차트 영역 |"
    ]
    out.append("|---" * (10 + len(KINDS)) + "|")
    for name, row in summary["groups"].items():
        kinds = " | ".join(str(row["kinds"][kind]) for kind in KINDS)
        out.append(
            f"| {name} | {row['documents']} | {row['passed']} | {row['failed']} | {row['unchecked']} | "
            f"{row['pages']} | {row['broken_pages']} | {kinds} | {row['tail_shift_pages']} | "
            f"{row['pagination_pages']} | {row['chart_regions']} |"
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
    results = measure_all(chosen, args.jobs)
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
