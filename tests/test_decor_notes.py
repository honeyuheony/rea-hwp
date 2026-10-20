from pathlib import Path

import pytest

from rea_hwp.document import HwpDocument
from rea_hwp.render.layout import layout_document
from rea_hwp.render.notes import divider_length
from rea_hwp.render.svg import page_glyph_table

CORPUS = Path("/home/kwh1019/tools/hwp-corpus/oss")


def corpus_pages(name: str):
    return layout_document(HwpDocument.open(CORPUS / name))


def line_at(page, text: str) -> tuple[float, float]:
    glyphs = page_glyph_table(page)
    chars = "".join(char for char, _, _ in glyphs)
    start = chars.index(text)
    return glyphs[start][1], glyphs[start][2]


def test_divider_length_reads_low_word_and_presets():
    assert divider_length(-1, 50000) == 14173
    assert divider_length(-3, 30000) == 10000
    assert divider_length(-4, 30000) == 30000
    assert divider_length(0x00E03138, 50000) == 0x3138
    assert divider_length(20000, 15000) == 15000


def test_footnotes_get_their_own_numbers_and_spacing():
    page = corpus_pages("rhwp/footnote-01.hwp")[0]
    # 한글 2022 PDF: 1) 757.92pt, 2) 769.68pt.
    assert line_at(page, "1)플라스틱")[1] == pytest.approx(757.92, abs=0.5)
    assert line_at(page, "2)3D")[1] == pytest.approx(769.68, abs=0.5)


def test_bottom_page_number_sits_above_footer_bottom():
    page = corpus_pages("rhwp/footnote-01.hwp")[1]
    # 한글 2022 PDF: "- 2 -" 기준선 797.16pt.
    assert line_at(page, "-2-")[1] == pytest.approx(797.16, abs=1.0)


def test_bottom_aligned_footer():
    page = corpus_pages("pyhwp/tests__hwp5_tests__fixtures__headerfooter.hwp")[0]
    # 한글 2022 PDF: 꼬리말 기준선 797.13pt.
    assert line_at(page, "Footer")[1] == pytest.approx(797.13, abs=1.0)


def test_master_page_text_is_drawn_in_body_area():
    page = corpus_pages("hwplib/sample_hwp__basic__바탕쪽.hwp")[0]
    # 한글 2022 PDF: (85.03, 107.64)pt.
    x, y = line_at(page, "바타아")
    assert x == pytest.approx(85.03, abs=0.5)
    assert y == pytest.approx(107.64, abs=0.5)


def page_glyphs(page, text: str, below: float = 0.0) -> tuple[float, float]:
    """y가 below보다 큰 글자 가운데 text가 처음 나오는 곳의 원점."""
    glyphs = [glyph for glyph in page_glyph_table(page) if glyph[2] > below]
    chars = "".join(char for char, _, _ in glyphs)
    start = chars.index(text)
    return glyphs[start][1], glyphs[start][2]


def test_endnote_line_after_repeated_zero_vpos_starts_next_column():
    page = corpus_pages("rhwp/3-09월_교육_통합_2022.hwp")[18]
    # 한글 2022 PDF 19쪽: 미주 28의 둘째 문단은 왼쪽 단 맨 위(77.52pt), 미주 29는 오른쪽 단 322.92pt.
    assert line_at(page, "이므로삼각형")[1] == pytest.approx(77.52, abs=0.5)
    assert line_at(page, "문29")[1] == pytest.approx(322.92, abs=0.5)


def test_endnote_line_goes_below_top_and_bottom_object():
    pages = corpus_pages("rhwp/3-11월_실전_통합_2024-구분선위20미주사이0구분선아래20.hwp")
    assert len(pages) == 21
    # 한글 2022 PDF 12쪽: 개체 아래 첫 줄 「따라서」 기준선 396.6pt.
    assert page_glyphs(pages[11], "따라서")[1] == pytest.approx(396.6, abs=0.5)


def test_endnotes_start_new_page_when_page_has_footnotes():
    pages = corpus_pages("pyhwp/tests__hwp5_tests__fixtures__footnote-endnote.hwp")
    assert len(pages) == 2
    # 한글 2022 PDF 2쪽: (85.03, 107.64)pt.
    x, y = line_at(pages[1], "1)미주입니다")
    assert x == pytest.approx(85.03, abs=0.5)
    assert y == pytest.approx(107.64, abs=0.5)


def test_page_number_without_footer_margin_sits_in_bottom_margin():
    page = corpus_pages("rhwp/hwp3-sample5-hwp5.hwp")[1]
    # 한글 2022 PDF 2쪽: 꼬리말 여백 0, "- 2 -" 기준선 818.64pt.
    assert page_glyphs(page, "-2-", 800)[1] == pytest.approx(818.64, abs=0.05)


def test_page_number_descent_follows_font():
    page = layout_document(HwpDocument.open(CORPUS.parent / "public/kr_156489671_196610436.hwp"))[2]
    # 한글 2022 PDF 3쪽: 맑은 고딕 10.5pt "- 3 -" 기준선 807.96pt(0.12pt 격자).
    assert page_glyphs(page, "-3-", 780)[1] == pytest.approx(807.96, abs=0.05)


def test_bottom_aligned_footer_counts_floating_object():
    page = corpus_pages("rhwp/issue7333__aaaaaa.hwp")[1]
    # 한글 2022 PDF 2쪽: 꼬리말 둘째 문단의 쪽 번호 기준선 770.04pt.
    assert page_glyphs(page, "2", 760)[1] == pytest.approx(770.04, abs=0.5)


def test_extended_master_pages_replace_base_on_last_and_numbered_pages():
    pages = corpus_pages("rhwp/exam_kor.hwp")
    # 한글 2022 PDF 12쪽(1구역 마지막 쪽)·20쪽(3구역 넷째 쪽)의 확장 바탕쪽 「◦답안지의 해당란에」 첫 글자 원점.
    for index, expected in ((11, (446.5, 1001.9)), (19, (446.5, 1041.1))):
        assert "◦답안지의해당란에" in "".join(char for char, _, _ in page_glyph_table(pages[index]))
        x, y = line_at(pages[index], "◦답안지의해당란에")
        assert (x, y) == (pytest.approx(expected[0], abs=1), pytest.approx(expected[1], abs=1))
    # 기본 바탕쪽을 덮어 그리지 않고 바꾼다: 「홀수형」이 한 번만 있다.
    assert "".join(char for char, _, _ in page_glyph_table(pages[11])).count("홀수형") == 1


def test_dutmal_draws_main_and_sub_text_over_and_under():
    page = corpus_pages("hwp-rs/crates__hwp__tests__integration__project__files__dutmal.hwp")[0]
    # 한글 2022 PDF: 본문 85.08·112.8pt, 위 덧말 94.92·103.3pt, 아래 덧말 151.08·118.3pt.
    assert line_at(page, "본문입니다") == pytest.approx((85.08, 112.8), abs=0.5)
    assert line_at(page, "위덧말") == pytest.approx((94.92, 103.3), abs=0.5)
    assert line_at(page, "아래덧말") == pytest.approx((151.08, 118.3), abs=0.5)


def test_footnote_waits_for_room_below_own_paragraph_table():
    pages = corpus_pages("rhwp/정책연구용역사업 중간진도보고서(살아있는 간장 기증자의 의학적 선별기준 연구).hwp")
    # 한글 2022 PDF: 「표 28 … 147)」 문단의 표가 94쪽 바닥까지 차서 각주 147은 95쪽 아래 766.44pt.
    assert not any(line.text.startswith("147)") for line in pages[93].text_lines)
    assert line_at(pages[94], "147)")[1] == pytest.approx(766.44, abs=0.5)
