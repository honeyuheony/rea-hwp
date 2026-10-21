"""조판 결과와 원본 위치 잇기: 글줄의 문단 번호, 문단·표·그림이 놓인 쪽, 글줄 내보내기."""

from __future__ import annotations

import functools
import json

import pytest
from corpus_paths import CORPUS_ROOT

from rea_hwp import HwpDocument, Page, TextLine, page_text_lines, paragraph_pages, render_pages, subtree_end, tags

CORPUS = CORPUS_ROOT
needs_corpus = pytest.mark.skipif(not CORPUS.exists(), reason="corpus not available")
#: 각주·표(task1725), 머리말 그림·표(pic-in-head), 머리말·꼬리말 글(headerfooter), 바탕쪽(exam_kor), 그림(NewYear).
LOCATED_DOCS = (
    "oss/rhwp/basic__NewYear_s_Day.hwp",
    "oss/rhwp/task1725__text_footnote_tail_overpagination.hwp",
    "oss/rhwp/pic-in-head-01.hwp",
    "oss/pyhwp/tests__hwp5_tests__fixtures__headerfooter.hwp",
    "oss/rhwp/exam_kor.hwp",
)


@functools.cache
def _rendered(relative: str) -> tuple[HwpDocument, list[Page]]:
    document = HwpDocument.open(CORPUS / relative)
    return document, render_pages(document)


def _located_tag(document: HwpDocument, located: str) -> int:
    section, index = map(int, located.split(":"))
    return document.section_records(section)[index].tag


def _enclosing_controls(document: HwpDocument, located: str) -> set[bytes]:
    """원본 위치를 하위 범위에 품은 CTRL_HEADER들의 컨트롤 ID(4바이트)."""
    section, index = map(int, located.split(":"))
    records = document.section_records(section)
    return {
        view.payload[:4]
        for view in records[:index]
        if view.tag == tags.CTRL_HEADER and subtree_end(records, view.index) > index
    }


@needs_corpus
@pytest.mark.parametrize("relative", LOCATED_DOCS)
def test_every_line_paragraph_points_at_a_para_header_record(relative):
    document, pages = _rendered(relative)
    located = [line.paragraph for page in pages for line in page.text_lines if line.paragraph is not None]
    assert located
    assert {_located_tag(document, value) for value in located} == {tags.PARA_HEADER}


def _line_controls(document: HwpDocument, pages: list[Page]) -> set[bytes]:
    located = {line.paragraph for page in pages for line in page.text_lines if line.paragraph is not None}
    return set().union(*(_enclosing_controls(document, value) for value in located))


@needs_corpus
def test_footnote_and_table_cell_paragraphs_get_their_own_positions():
    controls = _line_controls(*_rendered("oss/rhwp/task1725__text_footnote_tail_overpagination.hwp"))
    assert tags.FOOTNOTE_CTRL.to_bytes(4, "little") in controls
    assert tags.TABLE_CTRL.to_bytes(4, "little") in controls


@needs_corpus
def test_header_and_footer_paragraphs_get_their_own_positions():
    controls = _line_controls(*_rendered("oss/pyhwp/tests__hwp5_tests__fixtures__headerfooter.hwp"))
    assert {tags.ctrl_id("head").to_bytes(4, "little"), tags.ctrl_id("foot").to_bytes(4, "little")} <= controls


@needs_corpus
def test_master_page_lines_have_no_paragraph():
    document, pages = _rendered("oss/rhwp/exam_kor.hwp")
    section_def = tags.ctrl_id("secd").to_bytes(4, "little")
    located = {line.paragraph for page in pages for line in page.text_lines if line.paragraph is not None}
    # 바탕쪽 문단은 구역 정의 컨트롤 아래에 저장된다.
    assert not [value for value in located if section_def in _enclosing_controls(document, value)]
    master = [line for page in pages for line in page.text_lines if line.text == "홀수형" and line.paragraph is None]
    assert len(master) > 1


@needs_corpus
@pytest.mark.parametrize("relative", LOCATED_DOCS)
def test_paragraph_pages_give_first_page_of_lines_tables_and_pictures(relative):
    document, pages = _rendered(relative)
    first_pages = paragraph_pages(pages)
    assert {_located_tag(document, value) for value in first_pages} <= {
        tags.PARA_HEADER,
        tags.TABLE,
        tags.SHAPE_COMPONENT_PICTURE,
    }
    line_pages: dict[str, int] = {}
    for page in pages:
        for line in page.text_lines:
            if line.paragraph is not None:
                line_pages[line.paragraph] = min(line_pages.get(line.paragraph, page.number), page.number)
    assert {key: value for key, value in first_pages.items() if key in line_pages} == line_pages
    # 표는 칸 안 글줄이 놓인 가장 이른 쪽보다 늦게 시작할 수 없다.
    for key, page_number in first_pages.items():
        if _located_tag(document, key) != tags.TABLE:
            continue
        section, index = map(int, key.split(":"))
        records = document.section_records(section)
        owner = next(i for i in range(index - 1, -1, -1) if records[i].level < records[index].level)
        inside = [
            number
            for located, number in line_pages.items()
            if located.startswith(f"{section}:") and owner < int(located.split(":")[1]) < subtree_end(records, owner)
        ]
        assert not inside or page_number <= min(inside)


@needs_corpus
def test_tables_and_pictures_are_located():
    document, pages = _rendered("oss/rhwp/pic-in-head-01.hwp")
    kinds = {_located_tag(document, value) for value in paragraph_pages(pages)}
    assert {tags.TABLE, tags.SHAPE_COMPONENT_PICTURE} <= kinds


def test_paragraph_pages_keeps_earliest_page_number():
    first = Page(10, 10, 1, text_lines=[TextLine("a", 0, 0, 1, 1, "0:4")], objects=["0:9"])
    second = Page(10, 10, 2, text_lines=[TextLine("b", 0, 0, 1, 1, "0:4"), TextLine("c", 0, 0, 1, 1, "1:2")])
    second.objects.append("0:9")
    third = Page(10, 10, 3, text_lines=[TextLine("p", 0, 0, 1, 1)])
    assert paragraph_pages([second, third, first]) == {"0:4": 1, "0:9": 1, "1:2": 2}


def test_page_text_lines_are_json_ready_in_points():
    page = Page(10000, 10000, 1, text_lines=[TextLine("가\x18\x1e나", 1234.5, 2000, 3001, 1000, "0:37")])
    page.text_lines.append(TextLine("쪽", 0, 100, 50, 900))
    exported = page_text_lines(page)
    assert exported == [
        {"text": "가 나", "x_pt": 12.345, "baseline_pt": 20.0, "width_pt": 30.01, "size_pt": 10.0, "paragraph": "0:37"},
        {"text": "쪽", "x_pt": 0.0, "baseline_pt": 1.0, "width_pt": 0.5, "size_pt": 9.0, "paragraph": None},
    ]
    assert json.loads(json.dumps(exported, ensure_ascii=False)) == exported
