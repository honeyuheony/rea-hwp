"""글자 겹침(tcps): 한글이 읽을 때 고치는 글·테두리, 테두리 글자와 안쪽 글자 배치, 칸 폭, 복사 층."""

from __future__ import annotations

import functools
from types import SimpleNamespace

import pytest
from corpus_paths import CORPUS_ROOT

from rea_hwp.document import HwpDocument
from rea_hwp.models.controls import OverlapCtrl
from rea_hwp.models.docinfo import CharShape
from rea_hwp.render import metrics
from rea_hwp.render.html import _glyph_line
from rea_hwp.render.layout import DocumentLayout, _overlap_text, layout_document
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


def test_overlap_text_keeps_leading_ideographic_space():
    # 정답 PDF(행정업무운영 편람 59쪽·aift 2쪽)는 확장 0인 「　」 칸의 폭을 남긴다.
    assert _overlap_text("　", 0) == ("　", 0)


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
    assert '<g mask="url(#hole0)"><text ' in svg
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


# 리뷰 지적(f392711) 재현: 테두리 4 실측만으로 바뀐 동작과 구멍 글자 처리.


@functools.cache
def _k_water_layout() -> DocumentLayout:
    return DocumentLayout(HwpDocument.open(K_WATER))


def _overlap(text: str, border: int, inner: int = -2) -> OverlapCtrl:
    model = next(
        node.model
        for section in _k_water_layout().document.sections
        for node in section.walk()
        if isinstance(node.model, OverlapCtrl)
    )
    values = dict(model.values, text=text, border_type=border, inner_char_size=inner, expansion=0)
    return type(model)(values=values, tail=model.tail)


def _shape(**changes) -> CharShape:
    base = _k_water_layout().styles.char_shapes[0]
    values = dict(base.values, base_size=1700, ratios=[100] * 7, rel_sizes=[100] * 7, spacings=[0] * 7)
    values.update(changes)
    return type(base)(values=values, tail=base.tail)


def _place(model: OverlapCtrl, shape: CharShape, x: float = 10000) -> list[Glyph]:
    state = SimpleNamespace(page=Page(60000, 80000, 1))
    _k_water_layout().place_overlap(state, model, shape, x, 30000)
    return [item for item in state.page.items if isinstance(item, Glyph)]


def test_holed_glyph_with_ratio_keeps_mask_in_page_coordinates():
    # 지적 1: 장평·회전 글자는 transform으로 찍히므로 마스크를 글자 바깥 묶음(g)에 걸어야 쪽 좌표로 풀린다.
    box = Glyph("■", 1000, 2000, 1700, "Test", scale_x=0.9, holes=[Glyph("3", 1500, 1900, 1360, "Test")])
    svg = page_to_svg(Page(10000, 10000, 1, items=[box]))
    assert '<g mask="url(#hole0)"><text transform=' in svg
    assert "<text mask=" not in svg


@needs_k_water
def test_overlap_width_measures_frame_in_drawn_slot():
    # 지적 2: 테두리 글자는 한글 슬롯으로 찍으므로 폭도 한글 슬롯으로 잰다(기호 슬롯 상대 크기 50%여도 같다).
    layout = _k_water_layout()
    shape = _shape(rel_sizes=[100, 100, 100, 100, 100, 50, 100])
    frame = layout.measurer.advance("■", shape, metrics.HANGUL, 0)
    assert layout.overlap_width(_overlap("\U000f02d0", 4), shape) == pytest.approx(frame)


@pytest.mark.parametrize(("text", "expected"), [("⑩", "10"), ("Ⓐ", "A"), ("㉠", "\u1100"), ("⑳", "20")])
def test_overlap_text_still_unpacks_other_circled_chars_in_circle(text, expected):
    # 지적 3: 숫자 표에 없는 원문자는 전처럼 유니코드 원 분해로 원 안 글자를 푼다.
    assert _overlap_text(text, 1) == (expected, 1)


@needs_k_water
def test_inverted_overlap_line_keeps_per_glyph_text_layer():
    # 지적 4: 구멍 숫자도 그린 ■에 이어 붙여 뷰어 글 층이 줄 폭 맞춤으로 떨어지지 않는다.
    lines = [line for line in _k_water_page_13().text_lines if line.text.startswith("■3")]
    assert lines
    assert all(_glyph_line(line) is not None for line in lines)


@needs_k_water
def test_overlap_wide_inner_text_stays_inside_cell():
    # 지적 5: 안쪽 글(세 자리 「123」, 내부 크기 100%)이 글자 크기보다 넓어도 칸 앞(앞 글자 쪽)으로 넘어가지 않는다.
    # 좁은 안쪽 글은 글자겹침 견본 PDF대로 글자 크기 가운데에 둔다(test_inverted_square_overlap… 참고).
    wide = _place(_overlap("\U000f0491\U000f049c\U000f04a7", 1, inner=0), _shape())
    # 칸 시작 10000은 장치 단위(12HU)로 내려 9996에 찍힌다.
    assert min(glyph.x for glyph in wide) >= 10000 - 12


@needs_k_water
def test_shadow_of_holed_glyph_has_own_shifted_holes():
    # 지적 6: 그림자 글자는 구멍 목록을 함께 쓰지 않고 그림자 간격만큼 옮긴 구멍을 갖는다.
    shape = _shape(shadow_gap_x=10, shadow_gap_y=10, attr=_shape().attr & ~(3 << 11) | (1 << 11))
    glyphs = [glyph for glyph in _place(_overlap("\U000f02d0", 4), shape) if glyph.char == "■"]
    assert len(glyphs) == 2
    shadow, box = glyphs
    assert shadow.holes is not box.holes
    assert shadow.holes[0].x - box.holes[0].x == pytest.approx(shadow.x - box.x)


@needs_k_water
def test_overlap_width_keeps_ideographic_space():
    # 지적 7: 폭과 그리기는 같은 글(overlap_text)을 쓴다. 정답 PDF대로 「　A」 칸은 U+3000 폭을 차지한다.
    layout = _k_water_layout()
    shape = _shape()
    model = _overlap("　A", 0)
    assert layout.overlap_text(model) == ("　A", 0)
    assert layout.overlap_width(model, shape) == pytest.approx(layout.plain_width("　", shape))


def test_overlap_text_keeps_border_of_empty_text():
    # 지적 9: 글 없는 테두리는 전처럼 테두리를 둔다.
    assert _overlap_text("", 1) == ("", 1)
