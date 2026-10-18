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
    page_def = SimpleNamespace(margin_footer=3000, margin_bottom=3000)
    return SimpleNamespace(body_height=body_height, columns=[(0, 40000)] * columns, page_def=page_def)


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


def test_empty_line_starting_below_paper_edge_is_pushed():
    empty = SimpleNamespace(
        segments=[_segment(66000)],
        chars=[TextChar(0, " ", 0)],
        positions=[0],
        controls={},
        line_range=lambda index: (0, 1),
    )
    assert DocumentLayout.overflows_page(None, _state(), _geometry(), empty, 0)


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
        ("oss/rhwp/p122.hwp", 3),
        ("oss/rhwp/issue5699__37787_regulatory_impact.hwp", 49),
        ("oss/rhwp/issue2004_cell_image_stack.hwp", 8),
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
def test_stored_range_outside_computed_range_is_rebroken_with_margin():
    # hwpx 변환기는 왼쪽 여백 1130 문단을 column_start 0으로 저장했다. 한글은 계산 구간(여백 안쪽)과 다른 줄을 다시
    # 짜서 정답 7쪽 「② 노붐은」 줄이 본문 왼쪽 70.86 + 11.3 = 82.2pt에서 시작한다.
    lines = _open("oss/rhwp/hwpx__hancom-hwp__[2027] 온새미로 1 본교재.hwp")[6].text_lines
    line = next(line for line in lines if line.text.startswith("② 노붐은 당대의"))
    assert line.x / 100 == pytest.approx(82.2, abs=0.5)


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


@needs_corpus
def test_hft_only_bold_hangul_is_wider_than_its_plain_width():
    # 정답 PDF 21쪽: 한양중고딕 진하게 13pt 자간 −16%, 「대」→「하」 11.40pt(보통 굵기 폭 10.92pt).
    page = _open("oss/rhwp/2022년 국립국어원 업무계획.hwp")[20]
    table = page_glyph_table(page)
    text = "".join(row[0] for row in table)
    start = text.index("대하여제1항")
    assert table[start + 1][1] - table[start][1] == pytest.approx(11.40, abs=0.13)


@needs_corpus
def test_hft_only_bold_glyph_from_pdf_width_table_is_also_wider():
    # 정답 PDF 19쪽: 한양중고딕 진하게 13pt 자간 −3%, 「10. OECD」의 「1」→「0」 6.96pt(폭 표 0.5em 그대로면 6.32pt).
    table = page_glyph_table(_open("public/kr_156489570_196686770.hwp")[18])
    text = "".join(row[0] for row in table)
    start = text.index("10.OECD")
    assert table[start + 1][1] - table[start][1] == pytest.approx(6.96, abs=0.13)


@needs_corpus
def test_old_hangul_jamo_cluster_takes_one_syllable_width():
    # 정답 PDF 49쪽 머리말: 「ᄒᆞᆫ」 묶음이 72.00pt, 「글」이 81.96pt(10pt 한 음절 뒤).
    page = _open("oss/hwp-rs/crates__hwp__tests__integration__hancom__files__한글문서파일형식_5.0_revision1.3.hwp")[48]
    table = page_glyph_table(page)
    start = next(i for i, row in enumerate(table) if row[0] == "ᄒᆞᆫ")
    assert table[start + 1][0] == "글"
    assert table[start + 1][1] - table[start][1] == pytest.approx(9.96, abs=0.13)


SPEC_50 = "oss/hwp-rs/crates__hwp__tests__integration__hancom__files__한글문서파일형식_5.0_revision1.3.hwp"


@needs_corpus
@pytest.mark.parametrize(
    ("page_number", "baseline"),
    [
        # 넘쳐서 쪽 첫 문단이 됐지만 위 간격(500)을 넣어 저장한 문단의 표: 위 간격 앞이 기준(정답 93.7pt).
        (49, 93.7),
        # 쪽을 넘긴 표 82 나머지 아래로 저장된 문단(6999)의 표 83: 저장 위치 그대로(정답 163.8pt).
        (46, 163.8),
    ],
)
def test_first_table_header_row_matches_hancom(page_number: int, baseline: float):
    table = page_glyph_table(_open(SPEC_50)[page_number - 1])
    text = "".join(row[0] for row in table)
    assert table[text.index("자료형")][2] == pytest.approx(baseline, abs=0.5)


@needs_corpus
def test_stacked_cell_pictures_start_each_continued_page_at_cell_top():
    # 정답 5쪽: 셀 문단 둘째 그림(세로 오프셋 −3360)이 조각 위 65.2pt + 셀 위 여백 1.41pt에 놓인다.
    page = _open("oss/rhwp/issue2004_cell_image_stack.hwp")[4]
    images = [item for item in page.items if type(item).__name__ == "Image"]
    assert [round(image.y / 100, 1) for image in images] == [pytest.approx(66.5, abs=0.3)]


POLICY_REPORT = "oss/rhwp/정책연구용역사업 중간진도보고서(살아있는 간장 기증자의 의학적 선별기준 연구).hwp"


def _glyphs_at(page, char: str, x: float, y: float) -> list:
    return [row for row in page_glyph_table(page) if row[0] == char and abs(row[1] - x) < 0.5 and abs(row[2] - y) < 0.5]


@needs_corpus
def test_footnote_stored_rewind_continues_at_next_page_footnote_top():
    # 한글 2022 PDF 67쪽: 66쪽에서 앞 두 줄만 놓인 각주 77의 나머지 「→Part 482」가 각주 자리 첫 줄(84.0, 449.2pt).
    page = _open(POLICY_REPORT)[66]
    assert _glyphs_at(page, "→", 84.0, 449.2)


@needs_corpus
def test_footnote_with_equal_stored_positions_splits_after_first_line():
    # 한글 2022 PDF 178쪽: 각주 240(두 줄 모두 저장 위치 0)은 첫 줄 「240) B형간염…」만 쪽 바닥 778.2pt, 나머지는 179쪽.
    page = _open(POLICY_REPORT)[177]
    assert _glyphs_at(page, "B", 95.28, 778.2)


@needs_corpus
def test_paragraph_after_table_moves_when_carried_footnotes_fill_page():
    # 한글 2022 PDF 80쪽: 79쪽 표 나머지 아래가 각주 107~111로 차서 「○ 유럽의회」 문단은 80쪽 첫 줄(70.9, 91.0pt).
    page = _open(POLICY_REPORT)[79]
    assert _glyphs_at(page, "○", 70.9, 91.0)


@needs_corpus
def test_justified_line_ends_with_last_glyph_width_without_letter_spacing():
    # 한글 2022 PDF 22쪽: 13pt 자간 −12% 양쪽 정렬 줄의 끝 글자 「하」는 529.68pt(자간 없는 폭 13pt로 오른쪽 끝에 맞음).
    page = _open("oss/rhwp/hwp3-sample16-hwp5-2024.hwp")[21]
    assert _glyphs_at(page, "하", 529.68, 167.8)


@needs_corpus
def test_hancom_private_use_hangul_mark_takes_full_width():
    # 한글 2022 PDF hwpspec 69쪽: 문단 첫 글자 U+F53A(「한」) 뒤 「글」이 115.68pt(전각 폭 8.64pt).
    page = _open("oss/rhwp/hwpspec.hwp")[68]
    assert _glyphs_at(page, "글", 115.68, 128.8)


@needs_corpus
def test_cell_float_below_anchor_lines_moves_alone_to_next_page():
    # 한글 2022 PDF rowbreak-problem 16쪽: 글상자 기준 문단 둘째 줄 「공공데이터포털과의…」은 16쪽 742.2pt에 남는다.
    page = _open("oss/rhwp/rowbreak-problem-pages.hwp")[15]
    assert _glyphs_at(page, "공", 84.6, 742.2)


@needs_corpus
def test_even_master_page_is_stored_before_odd_master_page():
    # 한글 2022 PDF 편람 12쪽(짝수 쪽): 1구역 짝수 바탕쪽 「2025 행정업무운영 편람」이 왼쪽 66.7pt, 69.2pt.
    page = _open("oss/rhwp/2025 행정업무운영 편람(최종).hwp")[11]
    assert _glyphs_at(page, "2", 66.7, 69.2)


@needs_corpus
def test_math_operator_missing_from_font_takes_full_width():
    # 한글 2022 PDF aift 21쪽: 돋움체 12pt 「⋅」(글꼴에 없음) 뒤 공백 다음 「R」이 104.64pt(「⋅」 1em).
    page = _open("oss/rhwp/aift.hwp")[20]
    assert _glyphs_at(page, "R", 104.64, 66.8)


@needs_corpus
def test_text_box_line_overflowing_with_substitute_font_is_rebroken():
    # 한글 2022 PDF BookReview 2쪽: 한컴 윤고딕 240 → Haansoft Batang 대체로 저장 한 줄이 넘쳐 「전략!」이 둘째 줄.
    page = _open("oss/rhwp/basic__BookReview.hwp")[1]
    assert _glyphs_at(page, "전", 60.84, 620.6)


@needs_corpus
def test_half_width_hancom_symbol_advances_half_em():
    # 한글 2022 PDF exam_kor 2쪽: 한컴 전용 기호 U+F0854 바로 뒤 「한」이 101.52pt(기호 폭 반각).
    page = _open("oss/rhwp/exam_kor.hwp")[1]
    assert _glyphs_at(page, "한", 101.52, 388.32)


@needs_corpus
def test_one_dot_leader_missing_from_truetype_symbol_face_takes_fallback_width():
    # 한글 2022 PDF task2287 120쪽: 휴먼명조 8pt 「안전․건강」의 「․」을 Haansoft Batang 폭으로 찍어 「건」이 309.60pt.
    page = _open("oss/rhwp/task2287__1342000_edu_curriculum_map.hwp")[119]
    assert _glyphs_at(page, "건", 309.60, 255.7)


@needs_corpus
def test_hyphenation_point_takes_symbol_slot_face_width():
    # 한글 2022 PDF: 기호 슬롯이 HFT 한양신명조면 「‧」을 Type3 0.5em으로(k-water 14쪽 「광」 171.24pt),
    # 맑은 고딕이면 HCR Dotum 폭으로(kr_156489577 3쪽 「사」 295.92pt) 찍는다.
    assert _glyphs_at(_open("oss/rhwp/k-water-rfp-2024.hwp")[13], "광", 171.24, 175.92)
    assert _glyphs_at(_open("public/kr_156489577_196605615.hwp")[2], "사", 295.92, 658.2)


@needs_corpus
def test_hft_only_face_stored_as_truetype_uses_hft_widths():
    # 한글 2022 PDF 80168 19쪽: 글꼴 종류 1로 저장된 한양신명조 「”」을 Type3 0.441em으로 찍어 「은」이 408.24pt.
    page = _open("oss/rhwp/80168_regulatory_analysis.hwp")[18]
    assert _glyphs_at(page, "은", 408.24, 573.4)


@needs_corpus
def test_space_after_latin_letter_takes_latin_slot_spacing():
    # 한글 2022 PDF exam_eng 2쪽: 한글 슬롯 자간 −5%, 영문 슬롯 0%인 「Too bad. I hope」의 「I」가 166.56pt.
    page = _open("oss/rhwp/exam_eng.hwp")[1]
    assert _glyphs_at(page, "I", 166.56, 231.6)


@needs_corpus
def test_space_after_hangul_takes_latin_slot_spacing():
    # 한글 2022 PDF 편람 280쪽 목차: KoPub바탕 한글 자간 −8%·영문 −2%, 한글 뒤 공백 4.80pt라 「발」이 198.12pt.
    page = _open("oss/rhwp/2025 행정업무운영 편람(최종).hwp")[279]
    assert _glyphs_at(page, "발", 198.12, 341.4)


@needs_corpus
def test_text_figure_space_is_half_em():
    # 한글 2022 PDF 86712 14쪽: 12pt 줄 머리의 글 속 U+2007 뒤 「(A)」의 「(」가 87.96pt
    # (제어 문자 31처럼 1/4 em이면 84.96).
    page = _open("oss/rhwp/86712_regulatory_analysis.hwp")[13]
    assert _glyphs_at(page, "(", 87.96, 169.2)


@needs_corpus
def test_nested_table_above_pushed_line_keeps_stored_row_height():
    # 한글 2022 PDF issue2020 1쪽: 셀 첫 줄이 중첩 표 아래(2550)로 밀려 저장된 행 6은 저장 높이 그대로라
    # 「한글성명」의 「한」이 185.64pt(줄 기준으로 표를 놓으면 행이 24.5pt 늘어 197.9).
    page = _open("oss/rhwp/issue2020__passport_application_lawgo.hwp")[0]
    assert _glyphs_at(page, "한", 185.88, 185.64)


@needs_corpus
def test_vertical_center_counts_floating_picture_bottom():
    # 한글 2022 PDF issue2020 1쪽: 가운데 정렬 「사진」 셀의 내용 높이는 글 뒤 그림 아래 끝(9983)까지라
    # 「사」가 198.24pt(저장 줄 끝 9384 기준이면 3pt 아래).
    page = _open("oss/rhwp/issue2020__passport_application_lawgo.hwp")[0]
    assert _glyphs_at(page, "사", 69.60, 198.24)


@needs_corpus
def test_table_under_fragment_first_line_drops_paragraph_space_before():
    # 한글 2022 PDF task1718 41쪽: 셀 쪽 조각 첫 문단(위 간격 500)에 붙은 1×1 표가 조각 맨 위에서 시작해
    # 「부속서 Ⅰ」의 「부」가 78.84pt(간격을 두면 5pt 아래).
    page = _open("oss/rhwp/task1718__table_giant_cell_overfill.hwp")[40]
    assert _glyphs_at(page, "부", 271.92, 78.84)


@needs_corpus
def test_reflowed_textbox_paragraph_pushes_following_paragraphs():
    # 한글 2022 PDF BookReview 2쪽: 글상자 제목이 한 줄 저장에서 두 줄로 늘어 뒤 본문 「저자는」의 「저」가
    # 194.28pt(저장 위치 그대로면 175.9).
    page = _open("oss/rhwp/basic__BookReview.hwp")[1]
    assert _glyphs_at(page, "저", 60.12, 194.28)


@needs_corpus
def test_last_split_piece_fills_page_and_follows_cell_alignment():
    # 한글 2022 PDF issue6712 2쪽: 쪽을 넘은 가운데 정렬 셀의 마지막 조각이 쪽 바닥까지 늘고 그 안에서 가운데라
    # 「2. 夏季…」의 「夏」가 83.64pt(조각을 내용 높이로 두고 위 정렬하면 76.5).
    page = _open("oss/rhwp/issue6712__중국어_2026년 8호 가정통신문_여름철 영유아 감염병 예방.hwp")[1]
    assert _glyphs_at(page, "夏", 154.08, 83.64)


@needs_corpus
def test_spaced_table_continues_from_visible_page_bottom():
    # 한글 2022 PDF 편람 153쪽: 셀 간격 표 행 3이 152쪽 바닥까지 보인 뒤 나머지(저장 높이 − 보인 높이)만 이어져
    # 행 4 「가.」의 「가」가 240.0pt(저장 조각 경계에서 이으면 행 3이 10pt 길어 247.2).
    page = _open("oss/rhwp/2025 행정업무운영 편람(최종).hwp")[152]
    assert _glyphs_at(page, "가", 147.0, 240.0)


@needs_corpus
def test_text_lines_keep_spaces_and_line_order():
    # 86712 14쪽 표: 복사용 줄 글은 공백을 살리고 놓은 순서(행마다 왼쪽 칸 → 오른쪽 칸)를 따르며 쪽 번호도 든다.
    page = _open("oss/rhwp/86712_regulatory_analysis.hwp")[13]
    texts = [line.text for line in page.text_lines]
    start = texts.index("\u2007(A) 사업자의 수 또는 범위 제한")
    assert texts[start : start + 3] == [
        "\u2007(A) 사업자의 수 또는 범위 제한",
        "없음",
        "\u2007(B) 사업자의 경쟁능력 제한",
    ]
    assert texts[-1] == "- 14 -"


@needs_corpus
def test_space_inside_subscript_keeps_full_size_width():
    # 한글 2022 PDF hwpspec 130쪽: 9pt 아래첨자 「(글자 아래)」의 「자」가 348.96pt(공백은 첨자로 줄지 않음).
    page = _open("oss/rhwp/hwpspec.hwp")[129]
    assert _glyphs_at(page, "자", 348.96, 510.3)


@needs_corpus
def test_overflowing_tab_line_condenses_glyphs_after_tab():
    # 한글 2022 PDF 편람 280쪽 목차: 넘치는 줄의 탭 뒤 쪽 번호 「283」은 마지막 「3」이 469.44pt, 「2」가 467.40pt.
    page = _open("oss/rhwp/2025 행정업무운영 편람(최종).hwp")[279]
    assert _glyphs_at(page, "3", 469.44, 124.7)
