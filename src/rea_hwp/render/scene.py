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
    #: 원점을 축으로 돌린 각도(도, 반시계).
    rotation: float = 0.0


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
class Gradient:
    """쪽 좌표로 정한 그러데이션. radial이면 (x1, y1)이 중심, x2가 반지름이다."""

    radial: bool
    x1: float
    y1: float
    x2: float
    y2: float
    #: (0~1 위치, #rrggbb) 목록.
    stops: list[tuple[float, str]]


@dataclass
class Rect:
    x: float
    y: float
    width: float
    height: float
    fill: str | None = None
    stroke: str | None = None
    stroke_width: float = 0.0
    #: 있으면 fill 대신 이 그러데이션으로 칠한다.
    gradient: Gradient | None = None


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
class TextLine:
    """복사·검색용 줄 글(공백·탭 포함)과 그 줄이 놓인 자리. 그리는 글자는 Glyph가 맡는다."""

    text: str
    #: 줄 첫 글자 원점 x와 기준선 y.
    x: float
    baseline: float
    #: 줄 첫 글자부터 마지막 글자 끝까지의 폭.
    width: float
    #: 줄에서 가장 큰 글자 크기.
    size: float
    #: 이 줄이 속한 문단 PARA_HEADER의 원본 위치 "구역:index"(rea_hwp.RecordView.index). 바탕쪽·쪽 번호 글은 None.
    paragraph: str | None = None


@dataclass
class MissingObject:
    """그리지 못하고 빠뜨린 개체. 호출하는 쪽이 세어 사용자에게 알릴 수 있게 남긴다."""

    #: 개체 종류. 지금은 "textart"(글맵시)만 있다.
    kind: str
    #: 그리지 못한 이유(글꼴을 찾지 못함, fonttools 없음, 아직 옮기지 못한 모양 등).
    reason: str
    #: 개체 영역 (x, y, 너비, 높이).
    box: tuple[float, float, float, float]


@dataclass
class Page:
    width: float
    height: float
    number: int
    items: list[Glyph | Line | Rect | Image] = field(default_factory=list)
    #: 1단계 범위 밖이라 그리지 않은 개체(수식·차트·OLE·글맵시)의 영역 (x, y, 너비, 높이).
    excluded: list[tuple[float, float, float, float]] = field(default_factory=list)
    #: 놓은 순서대로의 줄 글. 뷰어가 투명한 글 층으로 깔아 글자를 고르고 복사할 수 있게 한다.
    text_lines: list[TextLine] = field(default_factory=list)
    #: 그리지 못하고 빠뜨린 개체. excluded(채점에서 빼는 영역)와 달리 이유를 함께 남긴다.
    missing: list[MissingObject] = field(default_factory=list)
    #: 이 쪽에 놓은 표(TABLE)·그림(SHAPE_COMPONENT_PICTURE)의 원본 위치 "구역:index". 여러 쪽에 걸친 표는 첫 쪽에만.
    objects: list[str] = field(default_factory=list)
    #: 표(TABLE) 원본 위치 "구역:index" → 이 쪽에 일부라도 놓인 행 번호(0부터, 셀의 행 주소) 목록. 이어지는 쪽 위에 다시
    #: 그린 제목 행은 넣지 않는다. 표 안(칸·캡션)의 표와 바탕쪽 표는 넣지 않는다(칸 안 표는 바깥 표 행으로 센다).
    table_rows: dict[str, list[int]] = field(default_factory=dict)

    @property
    def glyphs(self) -> list[Glyph]:
        return [item for item in self.items if isinstance(item, Glyph)]
