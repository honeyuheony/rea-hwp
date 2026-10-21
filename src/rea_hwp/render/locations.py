"""조판 결과를 원본 위치("구역:index")와 잇는다. 검색 결과의 쪽 번호와 화면 글줄 칠하기에 쓴다."""

from __future__ import annotations

from .scene import Page
from .svg import HU_PER_PT, copy_text


def paragraph_pages(pages: list[Page]) -> dict[str, int]:
    """원본 위치 → 그것이 놓인 첫 쪽 번호(Page.number, 1부터).

    키는 글줄이 있는 문단(PARA_HEADER)과 놓인 표(TABLE)·그림(SHAPE_COMPONENT_PICTURE)이다. 문단은 첫 글줄이 놓인 쪽,
    표는 첫 조각이 놓인 쪽이다. 글줄이 없는 문단(빈 문단, 개체만 든 문단, 세로쓰기 문단)과 바탕쪽 글은 들어가지 않는다.
    """
    first_pages: dict[str, int] = {}
    for page in sorted(pages, key=lambda page: page.number):
        for line in page.text_lines:
            if line.paragraph is not None:
                first_pages.setdefault(line.paragraph, page.number)
        for located in page.objects:
            first_pages.setdefault(located, page.number)
    return first_pages


def table_row_pages(pages: list[Page]) -> dict[str, dict[int, int]]:
    """표(TABLE) 원본 위치 → {행 번호(0부터, 셀의 행 주소): 그 행이 처음 놓인 쪽 번호(Page.number)}.

    쪽 경계에서 나뉜 행은 처음 놓인 쪽이다. 표 안(칸·캡션)의 표와 바탕쪽 표는 키가 없다(칸 안 표는 그것을 담은 바깥 표
    행으로 센다). 머리말·꼬리말 표는 처음 놓인 쪽이다. 표의 첫 행 쪽은 paragraph_pages의 표 쪽과 같다.
    """
    first_pages: dict[str, dict[int, int]] = {}
    for page in sorted(pages, key=lambda page: page.number):
        for located, rows in page.table_rows.items():
            table = first_pages.setdefault(located, {})
            for row in rows:
                table.setdefault(row, page.number)
    return {located: dict(sorted(rows.items())) for located, rows in first_pages.items()}


def page_text_lines(page: Page) -> list[dict]:
    """쪽 글줄을 JSON으로 바로 쓰는 dict 목록으로. 좌표는 SVG와 같은 pt, text는 HTML 글 층과 같은 복사용 글이다."""
    return [
        {
            "text": copy_text(line.text),
            "x_pt": round(line.x / HU_PER_PT, 3),
            "baseline_pt": round(line.baseline / HU_PER_PT, 3),
            "width_pt": round(line.width / HU_PER_PT, 3),
            "size_pt": round(line.size / HU_PER_PT, 3),
            "paragraph": line.paragraph,
        }
        for line in page.text_lines
    ]
