"""글자 겹침(tcps): 한글이 읽을 때 고치는 글·테두리, 테두리 글자와 안쪽 글자 배치, 칸 폭, 복사 층."""

from __future__ import annotations

import functools

import pytest
from corpus_paths import CORPUS_ROOT

from rea_hwp.document import HwpDocument
from rea_hwp.render.layout import _overlap_text, layout_document
from rea_hwp.render.scene import Glyph, Page
from rea_hwp.render.svg import page_to_svg

K_WATER = CORPUS_ROOT / "oss/rhwp/k-water-rfp.hwp"
needs_k_water = pytest.mark.skipif(not K_WATER.exists(), reason="corpus not available")


@functools.cache
def _k_water_page_13():
    return layout_document(HwpDocument.open(K_WATER))[12]


@pytest.mark.parametrize(
    ("text", "border", "expected"),
    [
        # 숫자 표(HwpApp DAT_1073f7c0) 넷째 묶음 = 반전 사각, U+F02CD가 0이다.
        ("\U000f02d0", 4, ("3", 4)),
        ("③", 1, ("3", 1)),
        # 풀린 글자의 묶음이 저장 테두리를 이긴다(❶은 반전 원 묶음).
        ("❶", 1, ("1", 2)),
        # 두 자리 표(DAT_1073f890) 셋째 묶음: 십의 자리 U+F02BA = 1, 일의 자리 U+F02C3 = 0.
        ("\U000f02ba\U000f02c3", 3, ("10", 3)),
        # 세 자리 표(DAT_1073f860) 첫 묶음: 자리마다 U+F0490·F049A·F04A4가 0이다.
        ("\U000f0491\U000f049c\U000f04a7", 1, ("123", 1)),
        # 풀리지 않으면 앞에 붙은 테두리 글자(DAT_1073c7d4)를 뗀다.
        ("◯A", 1, ("A", 1)),
        ("A", 20, ("A", 1)),
    ],
)
def test_overlap_text_follows_hancom_reader(text, border, expected):
    assert _overlap_text(text, border) == expected


def test_overlap_text_drops_leading_ideographic_space_only_without_expansion():
    assert _overlap_text("　", 0, 0) == ("", 0)
    assert _overlap_text("　", 0, 1) == ("　", 0)


@needs_k_water
def test_inverted_square_overlap_draws_filled_box_with_digit_hole():
    # 한글 2022 PDF 13쪽: 17.04pt ■ 원점 x 145.32, 기준선 345.24·392.64·449.28. 숫자는 글자가 아니라 13.6pt 「3」 모양
    # 구멍(왼쪽 150.91·150.79·151.15, 아래 343.67~343.8)이라 안쪽 숫자 기준선은 1.36pt 위다.
    glyphs = [
        item
        for item in _k_water_page_13().items
        if isinstance(item, Glyph) and 140 * 100 < item.x < 160 * 100 and 330 * 100 < item.y < 460 * 100
    ]
    assert {g.char for g in glyphs} == {"■"}
    boxes = sorted(glyphs, key=lambda g: g.y)
    assert [round(g.y / 100, 2) for g in boxes] == pytest.approx([345.24, 392.64, 449.28], abs=0.5)
    assert all(g.x / 100 == pytest.approx(145.32, abs=0.5) and g.size == 1700 for g in boxes)
    holes = [[(hole.char, hole.size) for hole in g.holes] for g in boxes]
    assert holes == [[("3", 1360)], [("2", 1360)], [("1", 1360)]]
    for box in boxes:
        assert (box.y - box.holes[0].y) / 100 == pytest.approx(1.36, abs=0.1)


def test_holed_glyph_is_masked_in_svg():
    box = Glyph("■", 1000, 2000, 1700, "Test", holes=[Glyph("3", 1500, 1900, 1360, "Test", "#ffffff")])
    svg = page_to_svg(Page(10000, 10000, 1, items=[box]))
    assert '<mask id="hole0"' in svg
    assert '<text mask="url(#hole0)"' in svg
    # 구멍 글자는 마스크 안에서 검은색(비움)으로만 찍는다.
    assert svg.count(">3</text>") == 1
    assert 'fill="#ffffff">3' not in svg


@needs_k_water
def test_overlap_cell_width_ignores_letter_spacing():
    # 정답 13쪽: 자간 −11·−16·−21%인 세 칸 모두 ■ 뒤 공백이 162.24, 다음 글자 「S」 169.32·「데」 168.96·「클」 167.76.
    glyphs = [item for item in _k_water_page_13().items if isinstance(item, Glyph)]
    first = {}
    for glyph in glyphs:
        if glyph.char in "S데클" and 160 * 100 < glyph.x < 175 * 100:
            first.setdefault(glyph.char, glyph.x / 100)
    assert first == pytest.approx({"S": 169.32, "데": 168.96, "클": 167.76}, abs=0.5)


@needs_k_water
def test_overlap_drawn_chars_go_into_copy_text():
    lines = [line.text for line in _k_water_page_13().text_lines]
    assert {"■3 SaaS", "■2 데이터 허브", "■1 클라우드 도입"} <= set(lines)
