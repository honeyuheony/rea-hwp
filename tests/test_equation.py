import pytest

from rea_hwp.render.equation import measure
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
