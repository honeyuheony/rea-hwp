"""렌더러 글자 배치와 한글 2022 PDF의 글자 원점을 비교한다.

분모는 PDF의 공백 아닌 글자 전체다. 짝을 못 찾은 PDF 글자는 실패로 센다.
"""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass, field

import pymupdf

#: 글자 원점 허용 오차(pt). goal 종료 조건 4.
TOLERANCE_PT = 1.0


#: 한글이 수식을 그릴 때 쓰는 글꼴. 이 글꼴로 찍힌 정답 글자는 수식(1단계 범위 밖)으로 센다.
EQUATION_FONTS = ("HyhwpEQ", "HYHWPEQ")


@dataclass(frozen=True)
class PlacedChar:
    char: str
    x: float
    y: float
    font: str = ""


@dataclass
class PageScore:
    reference_chars: int = 0
    within: int = 0
    matched: int = 0
    offsets: list[float] = field(default_factory=list)
    #: 범위 밖 개체 영역에 들어 있어 분모에서 뺀 정답 글자 수.
    excluded: int = 0
    #: 어느 정답 글자와도 짝을 맺지 못한 렌더 글자의 번호.
    unpaired_rendered: list[int] = field(default_factory=list)


#: 한글 PDF는 글자를 600dpi 장치 점(0.12pt) 단위로 찍는다.
DEVICE_PT = 0.12
#: 쪽 맞춤 행렬로 볼 0.12와의 차이 한계. 실측 쪽 맞춤 행렬은 0.119869~0.119935다.
FIT_WINDOW = 0.0005
#: 쪽 맞춤 변환 행렬 `sx 0 0 -sy 0 h cm`. 한글 PDF 작성기는 일부 글자 묶음을 장치 쪽(예: 7016점)을 쪽 상자(841pt)에
#: 맞춰 줄인 행렬로 찍는다(0.119935 × 0.119869). 같은 PDF 안에서도 묶음마다 다르다(kr_156489570, 2026-10-17 실측).
_FIT_CM = re.compile(rb"([\d.]+) 0(?:\.0+)? 0(?:\.0+)? -([\d.]+) 0(?:\.0+)? [\d.]+ cm")


def _fit_scale(page) -> tuple[float, float] | None:
    for match in _FIT_CM.finditer(page.read_contents()):
        sx, sy = float(match.group(1)), float(match.group(2))
        # 600dpi 공간 행렬만 본다. 양식 개체는 96dpi 공간(0.75 × 0.749)에 찍혀 다른 행렬이다(form-01).
        near_device = abs(sx - DEVICE_PT) < FIT_WINDOW and abs(sy - DEVICE_PT) < FIT_WINDOW
        if near_device and (abs(sx - DEVICE_PT) > 1e-5 or abs(sy - DEVICE_PT) > 1e-5):
            return sx, sy
    return None


def _is_fitted(size: float, fit: tuple[float, float]) -> bool:
    """글꼴 크기가 장치 점 정수배에 더 가까운 쪽으로 정확한 행렬과 쪽 맞춤 행렬을 가른다."""
    fitted_unit = math.sqrt(fit[0] * fit[1])
    exact = abs(size / DEVICE_PT - round(size / DEVICE_PT))
    fitted = abs(size / fitted_unit - round(size / fitted_unit))
    return fitted < exact


def pdf_pages(path: str) -> list[list[PlacedChar]]:
    """쪽 맞춤 행렬로 찍힌 글자는 장치 좌표(0.12pt/점)로 되돌린다. 한글 조판 위치를 비교하기 위해서다."""
    pages = []
    with pymupdf.open(path) as document:
        for page in document:
            placed = []
            fit = _fit_scale(page)
            for block in page.get_text("rawdict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line["spans"]:
                        scale = (1.0, 1.0)
                        if fit is not None and _is_fitted(span["size"], fit):
                            scale = (DEVICE_PT / fit[0], DEVICE_PT / fit[1])
                        for char in span["chars"]:
                            if not char["c"].strip():
                                continue
                            x, y = char["origin"][0] * scale[0], char["origin"][1] * scale[1]
                            placed.append(PlacedChar(char["c"], x, y, span["font"]))
            pages.append(merge_effect_strikes(placed))
    return pages


def pdf_image_boxes(path: str) -> list[list[tuple[float, float, float, float]]]:
    """쪽마다 비트맵 그림 상자(x, y, 폭, 높이 pt). 한글은 일부 개체(도식·글맵시 등)를 글자 대신 비트맵으로 저장한다."""
    with pymupdf.open(path) as document:
        return [
            [(x0, y0, x1 - x0, y1 - y0) for x0, y0, x1, y1 in (info["bbox"] for info in page.get_image_info())]
            for page in document
        ]


def merge_effect_strikes(chars: list[PlacedChar]) -> list[PlacedChar]:
    """외곽선·그림자 효과는 같은 글자를 1pt 안쪽으로 비켜 여러 번 찍는다. 먼저 찍은 것 하나만 남긴다."""
    kept = []
    kept_by_char: dict[str, list[tuple[float, float]]] = {}
    for char in chars:
        nearby = kept_by_char.setdefault(char.char, [])
        if any(abs(char.x - kx) <= 1.0 and abs(char.y - ky) <= 1.0 for kx, ky in nearby):
            continue
        nearby.append((char.x, char.y))
        kept.append(char)
    return kept


def _inside(char: PlacedChar, boxes: list[tuple[float, float, float, float]]) -> bool:
    return any(x <= char.x <= x + w and y <= char.y <= y + h for x, y, w, h in boxes)


def score_page(
    reference: list[PlacedChar],
    rendered: list[PlacedChar],
    excluded_boxes: list[tuple[float, float, float, float]] | None = None,
) -> PageScore:
    """excluded_boxes(pt)는 1단계 범위 밖 개체 영역이다. 그 안의 정답 글자는 분모에서 빼고 따로 센다."""
    kept = [
        c
        for c in reference
        if not c.font.startswith(EQUATION_FONTS) and not (excluded_boxes and _inside(c, excluded_boxes))
    ]
    score = PageScore(reference_chars=len(kept), excluded=len(reference) - len(kept))
    reference = kept
    matcher = difflib.SequenceMatcher(None, [c.char for c in reference], [c.char for c in rendered], autojunk=False)
    pairs: list[tuple[int, int]] = []
    for block in matcher.get_matching_blocks():
        pairs.extend((block.a + k, block.b + k) for k in range(block.size))
    used_ref = {a for a, _ in pairs}
    used_out = {b for _, b in pairs}
    # 읽는 순서가 달라 정렬로 못 맺은 글자는 같은 글자이면서 허용 오차 안에 있는 것과 일대일로 맺는다.
    remaining_out: dict[str, list[int]] = {}
    for index, char in enumerate(rendered):
        if index not in used_out:
            remaining_out.setdefault(char.char, []).append(index)
    for a, char in enumerate(reference):
        if a in used_ref:
            continue
        candidates = remaining_out.get(char.char, [])
        best = min(
            candidates,
            key=lambda b: math.dist((rendered[b].x, rendered[b].y), (char.x, char.y)),
            default=None,
        )
        if best is not None and math.dist((rendered[best].x, rendered[best].y), (char.x, char.y)) <= TOLERANCE_PT:
            candidates.remove(best)
            pairs.append((a, best))
    paired_rendered = {b for _, b in pairs}
    score.unpaired_rendered = [b for b in range(len(rendered)) if b not in paired_rendered]
    for a, b in pairs:
        distance = math.dist((reference[a].x, reference[a].y), (rendered[b].x, rendered[b].y))
        score.matched += 1
        score.offsets.append(distance)
        if distance <= TOLERANCE_PT:
            score.within += 1
    return score


#: 문서 전체 글자 열 비교 상한. 넘는 문서는 쪽 수가 같을 때만 쪽별로 맞대고, 다르면 채점불가로 둔다.
ORDER_WHOLE_LIMIT = 40_000


def letters(chars) -> list[str]:
    """순서 지표가 세는 글자: str.isalnum()이 참인 한 글자. 공백·문장부호·PUA 기호는 빼고 센다."""
    return [c for c in chars if len(c) == 1 and c.isalnum()]


def order_match(reference_pages: list[list[str]], rendered_pages: list[list[str]]) -> dict:
    """글자 열을 읽는 순서대로 맞대 맞은 수를 센다. 순서가 바뀐 글은 맞지 않은 것으로 센다.

    재현율 = matched / reference, 정밀도 = matched / rendered. 채점불가면 matched가 None이다.
    """
    reference = [letters(page) for page in reference_pages]
    rendered = [letters(page) for page in rendered_pages]
    result = {"reference": sum(map(len, reference)), "rendered": sum(map(len, rendered)), "matched": None}
    if max(result["reference"], result["rendered"]) <= ORDER_WHOLE_LIMIT:
        pairs = [([c for page in reference for c in page], [c for page in rendered for c in page])]
    elif len(reference) == len(rendered):
        pairs = list(zip(reference, rendered, strict=True))
    else:
        return result
    result["matched"] = sum(
        block.size
        for a, b in pairs
        for block in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()
    )
    return result
