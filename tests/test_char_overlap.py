"""글자 겹침(tcps): 숫자 표로 풀리는 반전 원·사각·반전 사각(테두리 2~4)만 새로 그리고, 나머지는 전 경로 그대로."""

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
from rea_hwp.render.layout import DocumentLayout, _overlap_digits, layout_document
from rea_hwp.render.scene import Glyph, Page
from rea_hwp.render.svg import page_to_svg

K_WATER = CORPUS_ROOT / "oss/rhwp/k-water-rfp.hwp"
CIRCLED = CORPUS_ROOT / "public/kr_156369074_189352360.hwp"
needs_k_water = pytest.mark.skipif(not K_WATER.exists(), reason="corpus not available")
needs_circled = pytest.mark.skipif(not CIRCLED.exists(), reason="corpus not available")


@functools.cache
def _k_water_page_13():
    return layout_document(HwpDocument.open(K_WATER))[12]


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
    ],
)
def test_overlap_digits_follow_hancom_tables(text, border, expected):
    assert _overlap_digits(text, border) == expected


@pytest.mark.parametrize(
    ("text", "border"),
    [
        # 테두리 없으면 한글도 숫자 표를 보지 않는다.
        ("\U000f02d0", 0),
        ("", 4),
        ("A", 4),
        # 지적 4(6196e2b): 표 한 자리 판정은 한 글자일 때만이다. 「①②」·「❶가」는 풀지 않는다.
        ("①②", 1),
        ("❶가", 1),
    ],
)
def test_overlap_digits_leave_other_text(text, border):
    assert _overlap_digits(text, border) is None


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


def test_holed_glyph_is_masked_in_svg():
    box = Glyph("■", 1000, 2000, 1700, "Test", holes=[Glyph("3", 1500, 1900, 1360, "Test", "#ffffff")])
    svg = page_to_svg(Page(10000, 10000, 1, items=[box]))
    assert '<mask id="hole0"' in svg
    assert '<g mask="url(#hole0)"><text ' in svg
    # 구멍 글자는 마스크 안에서 검은색(비움)으로만 찍는다.
    assert svg.count(">3</text>") == 1
    assert 'fill="#ffffff">3' not in svg


def test_holed_glyph_with_ratio_keeps_mask_in_page_coordinates():
    # 장평·회전 글자는 transform으로 찍히므로 마스크를 글자 바깥 묶음(g)에 걸어야 쪽 좌표로 풀린다.
    box = Glyph("■", 1000, 2000, 1700, "Test", scale_x=0.9, holes=[Glyph("3", 1500, 1900, 1360, "Test")])
    svg = page_to_svg(Page(10000, 10000, 1, items=[box]))
    assert '<g mask="url(#hole0)"><text transform=' in svg
    assert "<text mask=" not in svg


@needs_k_water
def test_overlap_width_measures_frame_in_drawn_slot():
    # 테두리 글자는 한글 슬롯으로 찍으므로 폭도 한글 슬롯으로 측정한다(기호 슬롯 상대 크기 50%여도 같다).
    layout = _k_water_layout()
    shape = _shape(rel_sizes=[100, 100, 100, 100, 100, 50, 100])
    frame = layout.measurer.advance("■", shape, metrics.HANGUL, 0)
    assert layout.overlap_width(_overlap("\U000f02d0", 4), shape) == pytest.approx(frame)


@needs_k_water
def test_shadow_of_holed_glyph_has_own_shifted_holes():
    # 그림자 글자는 구멍 목록을 함께 쓰지 않고 그림자 간격만큼 옮긴 구멍을 갖는다.
    shape = _shape(shadow_gap_x=10, shadow_gap_y=10, attr=_shape().attr & ~(3 << 11) | (1 << 11))
    glyphs = [glyph for glyph in _place(_overlap("\U000f02d0", 4), shape) if glyph.char == "■"]
    assert len(glyphs) == 2
    shadow, box = glyphs
    assert shadow.holes is not box.holes
    assert shadow.holes[0].x - box.holes[0].x == pytest.approx(shadow.x - box.x)


# 6196e2b 리뷰 지적 재현.


@needs_k_water
@pytest.mark.parametrize("inner", [-128, -10, 127])
def test_broken_inner_size_stays_within_drawable_range(inner):
    # 지적 1: 깨진 내부 크기(i8)여도 안쪽 글자 크기는 0보다 크고 글자 크기의 1.9배를 넘지 않는다.
    layout = _k_water_layout()
    shape = _shape()
    for border in (4, 1):
        model = _overlap("\U000f02d0" if border == 4 else "③", border, inner)
        size = layout.overlap_inner(model, shape).base_size
        assert 0 < size <= 1.9 * 1700
        assert layout.overlap_width(model, shape) <= 2 * 1700


@needs_k_water
def test_hole_digit_sits_inside_box_in_text_layer():
    # 지적 2: 뷰어 글 층에서 구멍 숫자는 ■ 뒤로 흐르지 않고 구멍 자리(13.6pt, ■ 안)에 놓인다.
    line = next(line for line in _k_water_page_13().text_lines if line.text.startswith("■3"))
    box, digit = line.glyphs[0], line.glyphs[1]
    assert digit is not box and digit.size == 1360
    assert box.x < digit.x < box.x + 1700
    assert line.char_x[1] == digit.x
    assert _glyph_line(line) is not None


@needs_k_water
@pytest.mark.parametrize("text", ["", "●", "□", "■"])
def test_empty_or_frame_only_text_keeps_old_path(text):
    # 지적 3: 숫자 표로 풀리지 않는 글(빈 글, 테두리 글자뿐인 글)은 전처럼 테두리 2~4를 그리지 않고 원래 글자 폭이다.
    layout = _k_water_layout()
    shape = _shape()
    for border in (2, 3, 4):
        model = _overlap(text, border)
        assert _place(model, shape) == []
        assert layout.overlap_width(model, shape) == max((layout.text_width(c, shape) for c in text), default=0)


@needs_k_water
def test_two_circled_digits_keep_old_circle_path():
    # 지적 4: 「①②」는 전처럼 ◯ 안에 1·2를 모두 찍는다.
    assert sorted(glyph.char for glyph in _place(_overlap("①②", 1), _shape())) == ["1", "2", "◯"]


@needs_k_water
def test_circle_with_several_chars_keeps_old_width():
    # 지적 5: 테두리 1 여러 글자 칸은 전처럼 가장 넓은 원래 글자 폭이다.
    layout = _k_water_layout()
    shape = _shape()
    assert layout.overlap_width(_overlap("AB", 1, inner=0), shape) == max(
        layout.text_width("A", shape), layout.text_width("B", shape)
    )


@needs_k_water
def test_wide_inner_digits_are_centered_under_frame():
    # 지적 6: 세 자리 숫자(내부 크기 100%)가 테두리보다 넓으면 테두리를 안쪽 글 가운데에 둔다.
    layout = _k_water_layout()
    shape = _shape()
    box = _place(_overlap("\U000f04d7\U000f04e2\U000f04ed", 4, inner=0), shape)[0]
    assert [hole.char for hole in box.holes] == ["1", "2", "3"]
    inner = layout.overlap_inner(_overlap("1", 4, inner=0), shape)
    last = box.holes[-1]
    right = last.x + layout.measurer.advance(last.char, inner, layout.measurer.slot(last.char, inner), 0)
    frame = layout.measurer.advance("■", shape, metrics.HANGUL, 0)
    assert box.x + frame / 2 == pytest.approx((box.holes[0].x + right) / 2, abs=13)
    assert box.holes[0].x >= 10000 - 12


@needs_circled
def test_unicode_circled_digit_is_not_copied_as_drawn_parts():
    # 지적 7: 원래 경로(테두리 1 「①」)는 전처럼 줄 글에 넣지 않아 「◯1」로 복사되지 않는다.
    pages = layout_document(HwpDocument.open(CIRCLED))
    assert not any("◯" in line.text for page in pages for line in page.text_lines)
