"""쪽 하나에 그릴 항목 목록. 좌표와 길이는 모두 HWPUNIT(1/7200인치)이다."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Glyph:
    char: str
    #: 글자 원점(기준선 왼쪽 끝).
    x: float
    y: float
    #: 실제로 그리는 크기(상대 크기 반영).
    size: float
    font_family: str
    color: str = "#000000"
    bold: bool = False
    italic: bool = False
    #: 장평(1.0 = 100%).
    scale_x: float = 1.0
    #: 레이아웃이 정한 전진폭. 브라우저 글꼴 폭과 달라도 이 폭에 맞춰 그린다.
    advance: float = 0.0


@dataclass
class Line:
    x1: float
    y1: float
    x2: float
    y2: float
    width: float
    color: str = "#000000"
    dash: str | None = None


@dataclass
class Rect:
    x: float
    y: float
    width: float
    height: float
    fill: str | None = None
    stroke: str | None = None
    stroke_width: float = 0.0


@dataclass
class Image:
    x: float
    y: float
    width: float
    height: float
    href: str
    #: 원본 이미지에서 잘라 쓸 영역(원본 픽셀 비율 0~1: left, top, right, bottom). None이면 전체.
    crop: tuple[float, float, float, float] | None = None


@dataclass
class Page:
    width: float
    height: float
    number: int
    items: list[Glyph | Line | Rect | Image] = field(default_factory=list)
    #: 1단계 범위 밖이라 그리지 않은 개체(수식·차트·OLE·글맵시)의 영역 (x, y, 너비, 높이).
    excluded: list[tuple[float, float, float, float]] = field(default_factory=list)

    @property
    def glyphs(self) -> list[Glyph]:
        return [item for item in self.items if isinstance(item, Glyph)]
