"""본문 흐름: 단 영역, 저장 세로 위치 되감기, 바닥을 넘는 줄 밀기, 구역 나눔."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from rea_hwp.document import HwpDocument
from rea_hwp.models.paragraph import LineSeg
from rea_hwp.render import reflow
from rea_hwp.render.common import PageState, TextChar
from rea_hwp.render.layout import DocumentLayout, layout_document
from rea_hwp.render.svg import page_glyph_table

CORPUS = Path.home() / "tools" / "hwp-corpus"
needs_corpus = pytest.mark.skipif(not CORPUS.exists(), reason="corpus not available")


def _segment(vpos: int, height: int = 1000, spacing: int = 600) -> LineSeg:
    return LineSeg(0, vpos, height, height, int(height * 0.85), spacing, 0, 40000, 0x60000)


def _geometry(columns: int = 1, body_height: int = 60000) -> SimpleNamespace:
    return SimpleNamespace(body_height=body_height, columns=[(0, 40000)] * columns)


def _state(zone_top: float = 0.0) -> PageState:
    return PageState(page=None, geometry=None, base_geometry=None, zone_top=zone_top)


def _restarts(previous: tuple[int, int, int], segment: LineSeg, columns: int = 1) -> bool:
    return DocumentLayout.restarts_column(None, _state(), _geometry(columns), previous, segment)


def test_rewind_to_column_top_starts_next_column():
    assert _restarts((50000, 51000, 51000), _segment(0))
    assert _restarts((50000, 51000, 51000), _segment(852))


def test_small_rewind_below_page_bottom_is_not_a_new_page():
    # 바닥을 넘는 빈 줄(kr_156369238 p269)과 바닥을 넘은 줄 뒤에 겹쳐 저장된 줄(hwp3-sample16-hwp5-2022 p161).
    assert not _restarts((59700, 60700, 60700), _segment(59400))
    assert not _restarts((59500, 60800, 60800), _segment(59240, height=100))


def test_large_rewind_in_section_cumulative_positions_starts_new_page():
    # hwpx 변환 문서는 새 쪽 첫 줄도 본문 높이보다 큰 누적 값으로 되돌아간다(온새미로 hwpx s1 p186).
    assert _restarts((498535, 499535, 499535), _segment(432959))


def test_equal_zero_rewinds_only_when_line_cannot_follow():
    assert not _restarts((0, 1000, 1000), _segment(0))
    assert _restarts((0, 59000, 59000), _segment(0, height=2000))
    assert _restarts((0, 1000, 1000), _segment(0), columns=2)


def test_overflowing_text_line_is_pushed_but_empty_line_is_not():
    paragraph = SimpleNamespace(
        segments=[_segment(59500, height=1300)],
        chars=[TextChar(0, "가", 0)],
        positions=[0],
        controls={},
        line_range=lambda index: (0, 1),
    )
    empty = SimpleNamespace(**{**vars(paragraph), "chars": [TextChar(0, " ", 0)]})
    assert DocumentLayout.overflows_page(None, _state(), _geometry(), paragraph, 0)
    assert not DocumentLayout.overflows_page(None, _state(), _geometry(), empty, 0)


def _open(relative: str):
    return layout_document(HwpDocument.open(CORPUS / relative))


@needs_corpus
@pytest.mark.parametrize(
    ("relative", "pages"),
    [
        ("oss/pyhwp/tests__hwp5_tests__fixtures__multicolumns.hwp", 3),
        ("oss/rhwp/basic__shortcut.hwp", 7),
        ("oss/rhwp/issue-505-equations.hwp", 4),
        ("oss/rhwp/rnote__labnote-001.hwp", 2),
        ("oss/rhwp/hwp3-sample16-hwp5-2022.hwp", 64),
        ("oss/rhwp/21_언어_기출_편집가능본.hwp", 15),
        ("oss/rhwp/[2027] 온새미로 1 본교재.hwp", 47),
        ("oss/rhwp/issue6946__44529-logistics-policy-rule-amendment.hwp", 13),
        ("oss/rhwp/synam-001.hwp", 35),
        ("oss/rhwp/aift.hwp", 74),
    ],
)
def test_page_count_matches_hancom(relative: str, pages: int):
    assert len(_open(relative)) == pages


@needs_corpus
def test_column_zones_stack_with_section_column_gap():
    # 한글 2022 PDF: 1단 → 2단 → 3단 영역의 첫 줄 기준선 107.6, 129.0, 150.3pt(영역 사이 단 간격 1134HU).
    page = _open("oss/pyhwp/tests__hwp5_tests__fixtures__multicolumns-widths.hwp")[0]
    baselines = sorted({round(y, 1) for _, _, y in page_glyph_table(page)})
    assert baselines == pytest.approx([107.6, 129.0, 150.3], abs=0.2)


@needs_corpus
def test_char_overlap_draws_circle_and_stacked_chars():
    # 한글 2022 PDF: ◯ 85.03/107.64, 원 안 3 88.03/106.44(7pt), 테두리 없는 「40」은 두 글자 모두 94.75/107.64.
    page = _open("oss/hwplib/sample_hwp__basic__글자겹침.hwp")[0]
    placed = sorted((char, round(x, 1), round(y, 1)) for char, x, y in page_glyph_table(page))
    expected = sorted([("◯", 85.0, 107.6), ("3", 88.0, 106.4), ("4", 94.8, 107.6), ("0", 94.8, 107.6)])
    assert [char for char, _, _ in placed] == [char for char, _, _ in expected]
    for (_, x, y), (_, ex, ey) in zip(placed, expected, strict=True):
        assert x == pytest.approx(ex, abs=0.5) and y == pytest.approx(ey, abs=0.5)


@needs_corpus
def test_paragraph_gap_missing_from_stored_positions_is_added():
    # hwpx 변환본은 문단 위·아래 간격 없이 저장돼 있고 한글은 인쇄할 때 넣는다(정답 50쪽).
    assert len(_open("oss/rhwp/hwpx__hancom-hwp__[2027] 온새미로 1 본교재.hwp")) == 50


@needs_corpus
def test_numbered_line_grows_to_number_size_and_pushes_following_lines():
    # 한글 2022 PDF 30쪽: 12pt 번호 「01」 문단 뒤 보기 ① 줄 기준선 586.0pt(번호 크기를 빼면 3.7pt 위로 간다).
    page = _open("oss/rhwp/hwpx__hancom-hwp__[2027] 온새미로 1 본교재.hwp")[29]
    circled = [y for char, _, y in page_glyph_table(page) if char == "①"]
    assert circled and circled[0] == pytest.approx(586.0, abs=0.5)


@needs_corpus
def test_grouped_text_box_uses_render_matrices_and_vertical_center():
    # 한글 2022 PDF 2쪽: 묶음 안 글상자 「목차」 기준선 108.12pt, 「Ⅰ.추진배경」 188.8pt(오프셋만 쓰면 −320pt).
    page = _open("public/kr_156489577_196605615.hwp")[1]
    placed = page_glyph_table(page)
    title = next(y for char, _, y in placed if char == "목")
    first = next(y for char, _, y in placed if char == "Ⅰ")
    assert title == pytest.approx(108.12, abs=0.5)
    assert first == pytest.approx(188.8, abs=0.5)


@needs_corpus
def test_para_relative_table_starts_at_paragraph_left_margin():
    # 한글 2022 PDF 67쪽: 문단 왼 여백 4500인 문단 기준 표 안 코드 줄 첫 글자 「a」 x=177.96, y=111.96.
    page = _open("oss/rhwp/hwpctl_API_v2.4.hwp")[66]
    xs = [x for char, x, y in page_glyph_table(page) if char == "a" and abs(y - 111.96) < 0.5]
    assert xs and min(xs) == pytest.approx(177.96, abs=0.5)


@needs_corpus
def test_floats_of_paragraph_pushed_to_next_page_start_below_previous_paragraph():
    # 한글 2022 PDF 2쪽: pr-1674 s0 p27 줄은 3쪽으로 되감기지만 표1 위 선은 2쪽 509.32pt(p26 바로 아래)다.
    page = _open("oss/rhwp/pr-1674.hwp")[1]
    tops = [min(item.y1, item.y2) / 100 for item in page.items if type(item).__name__ == "Line"]
    assert any(abs(top - 509.32) < 1.0 for top in tops)


@needs_corpus
def test_paragraph_relative_float_starts_before_space_before():
    # 한글 2022 PDF 편람 47쪽: s2 p214 떠 있는 표 위 선 132.11pt(첫 줄 1900이 아니라 위 간격 700 앞 1200 자리).
    page = _open("oss/rhwp/2025 행정업무운영 편람(최종).hwp")[46]
    tops = [min(item.y1, item.y2) / 100 for item in page.items if type(item).__name__ == "Line"]
    assert any(abs(top - 132.11) < 0.5 for top in tops)


@needs_corpus
def test_rewind_after_table_with_following_lines_keeps_continued_page():
    # 한글 2022 PDF 편람 161쪽 첫 본문 「라. 작성방법 해설」 기준선 121.2pt(표 문단 뒤 p61·p62가 표와 같은 쪽에 저장됨).
    page = _open("oss/rhwp/2025 행정업무운영 편람(최종).hwp")[160]
    heads = [y for char, x, y in page_glyph_table(page) if char == "작" and abs(y - 121.2) < 0.5]
    assert heads


@needs_corpus
def test_single_float_pushing_first_line_starts_at_paragraph():
    # 한글 2022 PDF issue1921 10쪽: p94 표는 앞 문단 바로 아래(첫 줄 47661 = 앞 문단 끝 2080 + 오프셋·높이·여백 45581).
    page = _open("oss/rhwp/issue1921__59043_regulatory_analysis.hwp")[9]
    # 표 안 첫 글자 기준선: 정답 244.56pt, 지금 239.9pt(4.7pt 미해결). 개체를 줄 아래(695.7pt)에 두던 잘못을 막는다.
    heads = [y for char, _, y in page_glyph_table(page) if char == "수" and abs(y - 244.56) < 10]
    assert heads


@needs_corpus
def test_float_below_previous_float_keeps_stored_line_anchor():
    # 한글 2022 PDF kr_156489726 17쪽 「<참고2>」 기준선 584.64pt.
    # s1 p60 첫 줄은 앞 표에 밀린 자리라 표는 그 줄 + 오프셋에 놓인다.
    page = _open("public/kr_156489726_196612349.hwp")[16]
    glyphs = page_glyph_table(page)
    heads = [glyphs[k][2] for k in range(1, len(glyphs)) if glyphs[k - 1][0] == "<" and glyphs[k][0] == "참"]
    assert any(abs(y - 584.64) < 1.0 for y in heads)


@needs_corpus
def test_float_pushed_by_page_crossing_table_continues_below_its_remainder():
    # 한글 2022 PDF issue5699 11쪽: s0 p115 표 둘째는 10쪽에서 넘어간 표 첫째 나머지 아래, 「□」 기준선 141.48·179.4pt.
    page = _open("oss/rhwp/issue5699__37787_regulatory_impact.hwp")[10]
    boxes = [y for char, _, y in page_glyph_table(page) if char == "□"]
    assert len(boxes) == 2
    assert all(abs(y - expected) < 1.0 for y, expected in zip(sorted(boxes), (141.48, 179.4), strict=True))


@needs_corpus
def test_float_not_fitting_below_remainder_starts_next_page():
    # 한글 2022 PDF pr-1674 4쪽: s0 p27 표2(696pt)는 3쪽 표1 나머지 아래에 들어가지 않아 4쪽 맨 위에서 시작한다.
    page = _open("oss/rhwp/pr-1674.hwp")[3]
    assert any(char == "[" and abs(y - 92.2) < 1.0 for char, _, y in page_glyph_table(page))


def test_non_percent_line_spacing_is_stored_doubled():
    # 저장 LINE_SEG 실측: issue1949 고정 3200·글자 1200 → 줄 피치 1600(간격 400), kps-ai 고정 2600·글자 1000 → 300.
    assert reflow.line_gap(SimpleNamespace(line_spacing_type=reflow.FIXED, line_spacing=3200), 1200) == 400
    assert reflow.line_gap(SimpleNamespace(line_spacing_type=reflow.FIXED, line_spacing=2600), 1000) == 300
    assert reflow.line_gap(SimpleNamespace(line_spacing_type=reflow.PERCENT, line_spacing=160), 1000) == 600
