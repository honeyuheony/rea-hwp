from rea_hwp.render import metrics
from rea_hwp.render.metrics import FIXED, GLYPH, SPACE, TAB, FontDatabase, justify_offsets


def test_positive_slack_goes_to_spaces_only_when_stretching():
    widths, kinds = [1000, 500, 1000, 500, 1000], [GLYPH, SPACE, GLYPH, SPACE, GLYPH]
    assert justify_offsets(widths, kinds, 300, stretch=True) == [0, 150, 0, 150, 0]
    assert justify_offsets(widths, kinds, 300, stretch=False) == [0.0] * 5


def test_small_overflow_shrinks_spaces_only():
    widths, kinds = [1000, 500, 1000, 500, 1000], [GLYPH, SPACE, GLYPH, SPACE, GLYPH]
    assert justify_offsets(widths, kinds, -100, stretch=False) == [0, -50, 0, -50, 0]


def test_large_overflow_caps_spaces_and_shrinks_glyphs_but_last():
    widths, kinds = [1000, 500, 1000, 500, 1000], [GLYPH, SPACE, GLYPH, SPACE, GLYPH]
    extras = justify_offsets(widths, kinds, -400, stretch=True)
    assert extras == [-100, -100, -100, -100, 0]


def test_overflow_shrinks_every_glyph_when_line_ends_with_fixed_item():
    extras = justify_offsets([1000, 1000, 2000], [GLYPH, GLYPH, FIXED], -200, stretch=True)
    assert extras == [-100, -100, 0]


def test_overflowing_line_with_tab_keeps_stored_widths():
    widths, kinds = [1000, 3000, 500, 1000], [GLYPH, TAB, SPACE, GLYPH]
    assert justify_offsets(widths, kinds, -1200, stretch=False) == [0, 0, 0, 0]


def test_leading_spaces_are_neither_stretched_nor_shrunk():
    widths, kinds = [500, 1000, 500, 1000], [SPACE, GLYPH, SPACE, GLYPH]
    assert justify_offsets(widths, kinds, 300, stretch=True) == [0, 0, 300, 0]
    assert justify_offsets(widths, kinds, -50, stretch=True) == [0, 0, -50, 0]


def test_justified_line_without_spaces_spreads_slack_over_glyphs():
    assert justify_offsets([1000, 1000, 1000], [GLYPH] * 3, 300, stretch=True) == [100, 100, 100]


def test_leading_spaces_get_no_share_when_only_items_after_them_spread():
    # kps-ai 21쪽: 공백 셋 + 글자처럼 취급한 표 + 글자. 앞 공백 몫이 없어야 표가 제자리에 놓인다.
    widths, kinds = [640, 640, 640, 47340, 1000], [SPACE, SPACE, SPACE, FIXED, GLYPH]
    assert justify_offsets(widths, kinds, 1000, stretch=True) == [0, 0, 0, 500, 500]


def test_distribute_puts_slack_between_items_so_last_ends_at_line_end():
    extras = justify_offsets([1000, 500, 1000, 1000], [GLYPH, SPACE, GLYPH, GLYPH], 600, False, distribute=True)
    assert extras == [200, 200, 200, 0]


def test_control_spaces_and_hancom_pua_widths():
    assert metrics.glyph_em(metrics.CONTROL_SPACES["\x1f"], None) == 0.25
    assert metrics.glyph_em(metrics.CONTROL_SPACES["\x1e"], None) == 0.5
    assert metrics.fallback_em("\U000f03c5") == 1.0


def test_overflowing_kopub_line_matches_hancom_pdf_advance():
    # 2025 행정업무운영 편람 12쪽 "운영의 주된 임무는…" 줄: 12pt 장평 99% 자간 -2%, 저장 줄 폭 36722HU.
    text = "운영의 주된 임무는 조직목표를 설정하고 이를 성취하는 것이다. 운영은 현재의"
    width_of = {" ": 584, ".": 336}
    widths = [width_of.get(char, 1164) for char in text]
    kinds = [SPACE if char == " " else GLYPH for char in text]
    extras = justify_offsets(widths, kinds, 36722 - sum(widths), stretch=True)
    hangul = [w + e for char, w, e in zip(text[:-1], widths, extras, strict=False) if char not in " ."]
    spaces = [w + e for char, w, e in zip(text, widths, extras, strict=True) if char == " "]
    # 한글 2022 PDF: 한글 평균 전진폭 1006HU, 공백 462.7HU(12HU 격자로 반올림된 값의 평균).
    assert abs(sum(hangul) / len(hangul) - 1006) < 3
    assert abs(sum(spaces) / len(spaces) - 462.7) < 6


def _database(supplement: dict) -> FontDatabase:
    database = FontDatabase({"fonts": [], "aliases": {"layout-metric": []}})
    database.add_pdf_supplement(supplement)
    return database


def test_pdf_supplement_builds_ranges_and_hangul_default():
    font = {
        "name": "신명 신신명조",
        "bold": False,
        "italic": False,
        "em": 1000,
        "widths": {"40": 333, "41": 333, "46": 250, "44032": 1000},
        "hangul_default": 950,
    }
    metric = _database({"substitutes": {}, "fonts": [font]}).find("신명 신신명조")
    assert metric.width(ord("(")) == 333
    assert metric.width(ord(".")) == 250
    assert metric.width(ord("가")) == 1000
    assert metric.width(ord("힣")) == 950
    assert metric.width(ord("A")) is None


def test_substitute_redirects_lookup():
    font = {"name": "Haansoft Batang", "bold": False, "italic": False, "em": 1000, "widths": {"65": 600}}
    database = _database(
        {
            "substitutes": {"KoPub바탕체 Light": {"target": "Haansoft Batang"}},
            "fonts": [font | {"hangul_default": None}],
        }
    )
    assert database.find("KoPub바탕체 Light").name == "Haansoft Batang"


def test_default_database_substitutes_missing_kopub_with_haansoft_batang():
    assert metrics.default_database().find("KoPub바탕체 Light").name == "Haansoft Batang"


def test_ttf_supplement_fills_only_missing_codes_and_first_supplement_wins():
    database = FontDatabase(
        {
            "fonts": [
                {
                    "name": "F",
                    "bold": False,
                    "italic": False,
                    "em": 2048,
                    "ranges": [[65, 66, [1000, 0]]],
                    "hangul": None,
                }
            ],
            "aliases": {"layout-metric": []},
        }
    )
    database.add_ttf_supplement({"fonts": [{"name": "F", "em": 1000, "widths": {"65": 10, "66": 500, "8594": 950}}]})
    database.add_ttf_supplement({"fonts": [{"name": "F", "em": 1000, "widths": {"8594": 100}}]})
    metric = database.find("F")
    assert (metric.width(65), metric.width(66), metric.width(8594)) == (1000, 1024, 1946)
