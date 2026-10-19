import pytest

from rea_hwp.render.equation import LegacyEngineState, LegacyEquationError, measure, measure_legacy
from rea_hwp.render.equation.engine import cdiv, muldiv
from rea_hwp.render.equation.glyphs import ppem
from rea_hwp.render.equation.parse import lex, preprocess

# 기대값은 한글 2022가 다시 저장한 문서의 수식 크기(폭, 높이, 기준선 백분율 + 1)다.
SAVED_BY_HANGUL_2022 = [
    ("x", 900, (525, 900, 86)),
    ("a", 900, (450, 900, 86)),
    ("1", 1100, (525, 1125, 85)),
    ("2t", 1100, (900, 1125, 85)),
    ("x`", 1100, (737, 1125, 85)),
    ("=4", 900, (1314, 900, 86)),
    ("=2^2", 900, (1614, 1050, 88)),
    ("x_1", 900, (825, 1078, 71)),
    ("a over b", 1100, (1100, 2580, 65)),
    ("root 3 over 3 ", 900, (1953, 2205, 68)),
    ("root5", 900, (1503, 1035, 87)),
    ("y`'", 900, (862, 900, 86)),
    ("rmP'", 900, (900, 900, 86)),
    ("sin theta", 900, (1725, 900, 86)),
    ("cos`theta", 900, (1987, 900, 86)),
    ("f LEFT ( x RIGHT )", 900, (1725, 900, 86)),
    ("LEFT ( 1,~1 RIGHT )", 900, (2388, 900, 86)),
    ("bar{rmAP}", 900, (1365, 1080, 88)),
    ("vec{p}", 900, (540, 1080, 88)),
    ("lim _{x rarrow  a-} {f(x)}", 900, (3660, 1650, 51)),
    ("int _{0} ^{2} {v(t)dt}", 900, (3855, 2298, 62)),
    ("sum _{k=1} ^{10} a _{k}", 900, (2052, 2438, 63)),
    ("{rm", 900, (1, 900, 86)),
]


@pytest.mark.parametrize(("script", "size", "expected"), SAVED_BY_HANGUL_2022)
def test_measure_matches_hangul_2022(script, size, expected):
    assert measure(script, size) == expected


def test_single_glyph_box_is_hinted_advance_and_pixel_height():
    width, height, baseline = measure("m", 900)
    assert (width, height) == (825, 900)
    assert baseline == (900 * 35 // 100 + 450) * 100 // 900 + 1


def test_ppem_uses_half_width_logfont():
    assert ppem(900) == (12, 12)
    assert ppem(1000) == (14, 13)
    assert ppem(1100) == (14, 15)


def test_muldiv_rounds_half_away_from_zero_and_cdiv_truncates():
    assert muldiv(900, 25, 100) == 225
    assert muldiv(612, 50, 100) == 306
    assert muldiv(15, 1, 2) == 8
    assert muldiv(-15, 1, 2) == -8
    assert cdiv(-7, 2) == -3


def _codes(text):
    return [ord(ch) for ch in text]


def test_preprocess_maps_commands_and_names():
    assert preprocess(_codes("a over b")) == [0x61, 0x20, 0xFF, 8, 0x20, 0x62]
    assert preprocess(_codes("alpha")) == [0xE09D]
    assert preprocess(_codes("y''")) == [0x79, 0x2033]
    out = preprocess(_codes("sin x"))
    assert out[0] == 2 and out[2:] == [0x20, 0x78]


def test_table_search_reaches_last_entry_of_each_table():
    # FUN_1004eaf0은 이진 탐색 첫 위치로 표 끝 항목(소문자 표 zeta, 대문자 표 ZETA)을 본다
    assert preprocess(_codes("zeta")) == [0xE0A2]
    assert preprocess(_codes("ZETA")) == [0xE08A]


def test_lex_splits_text_and_commands():
    buf = preprocess(_codes("ab^2"))
    assert lex(buf, 0) == (1, [0x61, 0x62])
    assert lex(buf, 2) == (0x5E, [0x5E])


# 옛 엔진(버전 문자열이 비었거나 "Equation Version 60"이 아님) 기대값: 한글 2022가 시험 사본을 다시 저장한 크기다.
LEGACY_SAVED_BY_HANGUL_2022 = [
    ("L", 1000, (984, 1200, 84)),
    ("LL", 1000, (1734, 1200, 84)),
    ("A", 1000, (1059, 1200, 84)),
    ("l", 800, (425, 960, 84)),
    ("L", 1200, (1166, 1440, 84)),
    ("rm L", 1000, (1217, 1200, 84)),
    ("가", 1000, (1209, 1200, 84)),
    ("TIMES", 1000, (834, 1200, 84)),
    ("INT", 1000, (1209, 2200, 64)),
    ("(", 800, (600, 960, 84)),
    ("△", 1200, (1466, 1440, 84)),
]


@pytest.mark.parametrize(("script", "size", "expected"), LEGACY_SAVED_BY_HANGUL_2022)
def test_legacy_measure_matches_hangul_2022(script, size, expected):
    assert measure_legacy(script, size) == expected


def test_legacy_subscript_and_superscript_boxes():
    # 코퍼스 옛 수식(issue1949·crates 시험 문서)이 한글 2022 재저장 뒤에도 가진 크기
    assert measure_legacy("d_B\r\n", 1000) == (1401, 1400, 72)
    assert measure_legacy("a^3\r\n", 1000) == (1251, 1340, 86)
    assert measure_legacy("1 over 4\r\n", 1000) == (1376, 2340, 67)


def test_legacy_parse_failure_keeps_stored_size():
    # "coll"은 대문자 표의 COL(쌓기)로 읽혀 '{'가 없어 실패한다. 한글도 저장값(96868x32)을 그대로 둔다
    with pytest.raises(LegacyEquationError):
        measure_legacy("g_coll = 1.2 ( P over {g} )", 1000)


def test_surrogate_pair_is_one_notdef_advance():
    # 대리 쌍 U+F0040 하나 = 한컴바탕 .notdef 한 칸(가로 ppem 픽셀). 시험 사본 재저장 값
    assert measure("A\U000f0040", 900) == (1575, 900, 86)
    assert measure("\U000f0040", 1000) == (1050, 975, 86)


def test_ladder_last_row_starts_one_column_right():
    # 한글 2022 재저장 값(시험 사본·코퍼스 수식-문자처럼취급-아님 문서). 마지막 줄 "2 & 3 &"는 둘째 칸부터 놓인다
    assert measure("LADDER{ 2 & 12 & 18 # 3 & 6 & 9 # 2 & 3 & }", 1600) == (5910, 5445, 61)
    assert measure("LADDER{ 2 & 12 & 18 # 3 & 6 & 9 }", 1000) == (3750, 2250, 66)


def test_sladder_adds_dots_column():
    assert measure("SLADDER{ 2 & 12 & 18 # 3 & 6 & 9 # 2 & 3 & }", 1000) == (4475, 3375, 61)


# 기대값은 시험 사본 수식을 한글 2022로 재저장해 얻은 크기다.
def test_rel_keeps_empty_lower_row_height():
    # REL 객체의 종류는 0이라 빈 아래 줄도 글자 크기만큼 높다
    assert measure("REL -> {x} {}", 1000) == (1100, 2317, 65)
    assert measure("BUILDREL -> {abc}", 1200) == (2250, 2820, 65)


def test_rel_arrow_is_drawn_or_measured_glyph():
    assert measure("a REL -> {xyz} {abc} b", 1000) == (3125, 2292, 66)
    assert measure("REL EXARROW {m} {n}", 1000) == (1400, 2634, 64)
    assert measure("REL = {abc} {def}", 1000) == (2075, 2925, 62)


def test_brace_gap_depends_on_flat_letters():
    assert measure("OVERBRACE {a+b+c} {n}", 1000) == (3635, 1991, 94)
    assert measure("OVERBRACE {ace} {mn}", 1000) == (3150, 1841, 93)
    assert measure("UNDERBRACE {ace} {mn}", 1000) == (3150, 1941, 44)
    assert measure("UNDERBRACE {abc} {x over y}", 900) == (2700, 2985, 46)


def test_longdiv_indents_rows_by_simulated_subtraction():
    assert measure("LONGDIV {12} {21} {252 # 24 # 12 # 12 # 0}", 1000) == (3200, 6645, 22)
    assert measure("LONGDIV {5} {3} {17 # 15 # 2}", 1000) == (2150, 4395, 33)
    assert measure("LONGDIV {3} {} {}", 1000) == (1600, 2195, 66)
    assert measure("LONGDIV {x+1} {x} {x ^{2} +x}", 1200) == (6234, 2865, 61)
    # 받아내림(1 < 2)과 남은 자릿수만큼 들여 쓴 행이 첫 열 폭을 넓힌다. 숫자가 아닌 행이 있으면 들여 쓰지 않는다
    assert measure("LONGDIV {} {} {1 # 23}", 1000) == (2650, 3295, 44)
    assert measure("LONGDIV {1} {} {5 # 5 # 1234}", 1000) == (3725, 4420, 33)
    assert measure("LONGDIV {} {} {12 # 12 # 0 # 56789}", 1000) == (4225, 5545, 27)
    assert measure("LONGDIV {} {} {1 # a # 2345}", 1000) == (3175, 4420, 33)


def test_multiple_integral_has_kind_two_so_empty_limits_are_zero():
    assert measure("DINT {a} {b} {c}", 1000) == (3135, 1911, 66)


def test_matrix_without_argument_gets_one_empty_cell_but_pile_does_not():
    assert measure("x MATRIX y", 1000) == (1100, 1000, 86)
    assert measure("x BMATRIX y", 1000) == (2150, 1000, 86)
    assert measure("x LADDER y", 1000) == (1300, 1150, 81)
    assert measure("x PILE y", 1000) == (600, 975, 86)


def test_glyph_width_follows_gdi_not_freetype_hinting():
    # HYhwpEQ U+E0B7(IMATH)은 13픽셀에서 GDI 4픽셀, FreeType 5픽셀이다
    assert measure("x IMATH y", 1000) == (1425, 975, 86)


def test_hanja_high_surrogate_is_measured_with_hanja_default_font():
    # U+20000 상위 대리 D840은 한자로 분류되어 함초롬바탕 .notdef로 측정하고 높이는 tmHeight + tmInternalLeading이다
    assert measure("\U00020000", 900) == (450, 1125, 78)
    assert measure("\U00020000", 1200) == (600, 1500, 79)


def test_legacy_bold_adds_synthetic_emboldening_and_flags_carry_over():
    # 굵은 HFT 글자는 em폭에 (em+10)/20을 더하고, 다음 수식 첫 글꼴(공백 폭)은 앞 수식의 굵게 플래그를 이어받는다
    state = LegacyEngineState()
    assert measure_legacy("bold l", 1200, state) == (849, 1620, 86)
    assert measure_legacy("bold ll", 1200, state) == (1350, 1620, 86)
    assert measure_legacy("W", 1200, state) == (1275, 1440, 84)


def test_legacy_greek_letter_follows_symbol_substitution_chain_to_batang():
    # 그리스 문자는 기호 언어라 수식·한양신명조·명조 기호 HFT를 거쳐 바탕(TTF) GDI 폭으로 측정한다
    assert measure_legacy("α", 1000) == (684, 1200, 84)
    assert measure_legacy("β", 800) == (725, 960, 84)
    assert measure_legacy("β", 1200) == (1091, 1440, 84)
