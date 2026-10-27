"""글자 겹침(tcps): 숫자 표로 풀리는 반전 원·사각·반전 사각(테두리 2~4)만 새로 그리고, 나머지는 전 경로 그대로."""

from __future__ import annotations

import functools
import sys
from types import SimpleNamespace

import pytest
from corpus_paths import CORPUS_ROOT

from rea_hwp import tags
from rea_hwp.document import HwpDocument
from rea_hwp.models.controls import CellListHeader, OverlapCtrl
from rea_hwp.models.docinfo import CharShape
from rea_hwp.render import metrics
from rea_hwp.render.html import _glyph_line
from rea_hwp.render.layout import DocumentLayout, _overlap_digits, layout_document
from rea_hwp.render.scene import Glyph, Page
from rea_hwp.render.svg import page_to_svg

K_WATER = CORPUS_ROOT / "oss/rhwp/k-water-rfp.hwp"
CIRCLED = CORPUS_ROOT / "public/kr_156369074_189352360.hwp"
TABLE_IN_TBOX = CORPUS_ROOT / "oss/rhwp/table-in-tbox.hwp"
VERTICAL_TBOX = CORPUS_ROOT / "oss/rhwp/hwpx__hancom-hwp__tbox-v-flow-01.hwp"
needs_k_water = pytest.mark.skipif(not K_WATER.exists(), reason="corpus not available")
needs_circled = pytest.mark.skipif(not CIRCLED.exists(), reason="corpus not available")
needs_tbox = pytest.mark.skipif(not (TABLE_IN_TBOX.exists() and VERTICAL_TBOX.exists()), reason="corpus not available")


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
        # 표 한 자리 판정은 한 글자일 때만이다. BMP 두 글자 「①②」·「❶가」의 둘째 글자를 버리지 않는다.
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
def test_inverted_overlap_copies_only_drawn_box():
    # 줄 글에는 글자로 그린 것만 넣는다. 구멍 숫자는 글자가 아니므로 정답 PDF 글 층처럼 「■」만 남는다.
    page = _k_water_page_13()
    lines = [line for line in page.text_lines if line.text.startswith("■")]
    assert {"■ SaaS", "■ 데이터 허브", "■ 클라우드 도입"} <= {line.text for line in lines}
    drawn = {id(item) for item in page.items}
    assert all(id(glyph) in drawn for line in lines for glyph in line.glyphs if glyph is not None)
    assert all(_glyph_line(line) is not None for line in lines)


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


# 새 경로는 실측과 같은 조건(장평·상대 크기 100%, 첨자 아님, 숫자가 테두리 안)만 탄다. 나머지는 이전 경로다.


@needs_k_water
@pytest.mark.parametrize("inner", [-128, -10, 127])
def test_digit_inner_size_is_clamped_against_broken_upload_values(inner):
    # 업로드 파일의 깨진 내부 크기(i8)도 안쪽 숫자를 0보다 크고 글자 크기의 1.9배 이하로 둔다.
    layout = _k_water_layout()
    size = layout.digit_inner(_overlap("\U000f02d0", 4, inner), _shape()).base_size
    assert 0 < size <= 1.9 * 1700
    for glyph in _place(_overlap("\U000f02d0", 4, inner), _shape()):
        assert all(0 < hole.size <= 1.9 * 1700 for hole in glyph.holes)


@needs_k_water
def test_circle_path_keeps_inner_size_as_stored():
    # 원 테두리는 이전 경로라 내부 크기를 제한하지 않는다(−9보다 작은 −10이면 크기 0 글자다).
    sizes = sorted(glyph.size for glyph in _place(_overlap("③", 1, -10), _shape()))
    assert sizes == [0, 1700]


@needs_k_water
@pytest.mark.parametrize(
    "changes",
    [{"ratios": [90] * 7}, {"rel_sizes": [80] * 7}, {"offsets": [10] * 7}, {"superscript": True}],
    ids=["ratio-90", "relative-size-80", "raised", "superscript"],
)
def test_untested_shape_falls_back_to_old_path(changes):
    # 장평·상대 크기·글자 위치·위 첨자가 실측과 다르면 숫자가 테두리 가운데를 벗어나므로 이전 경로(그리지 않음)다.
    layout = _k_water_layout()
    changes = dict(changes)
    if changes.pop("superscript", False):
        changes["attr"] = _shape().attr | (1 << 15)
    shape = _shape(**changes)
    model = _overlap("\U000f02d0", 4)
    assert _place(model, shape) == []
    assert layout.overlap_width(model, shape) == layout.text_width("\U000f02d0", shape)


@needs_k_water
@pytest.mark.parametrize("text", ["", "●", "□", "■"])
def test_empty_or_frame_only_text_keeps_old_path(text):
    # 숫자 표로 풀리지 않는 글(빈 글, 테두리 글자뿐인 글)은 전처럼 테두리 2~4를 그리지 않고 원래 글자 폭이다.
    layout = _k_water_layout()
    shape = _shape()
    for border in (2, 3, 4):
        model = _overlap(text, border)
        assert _place(model, shape) == []
        assert layout.overlap_width(model, shape) == max((layout.text_width(c, shape) for c in text), default=0)


@needs_k_water
def test_digits_wider_than_frame_keep_old_path():
    # 세 자리 숫자(내부 크기 100%)는 테두리보다 넓어 구멍이 잘리므로 이전 경로(그리지 않음)다.
    layout = _k_water_layout()
    model = _overlap("\U000f04d7\U000f04e2\U000f04ed", 4, inner=0)
    assert _place(model, _shape()) == []
    assert layout.overlap_width(model, _shape()) == max(layout.text_width(c, _shape()) for c in model.text)


@needs_k_water
def test_two_circled_digits_keep_old_circle_path():
    # 「①②」는 숫자 표 한 자리가 아니므로 전처럼 ◯ 안에 1·2를 모두 찍는다.
    assert sorted(glyph.char for glyph in _place(_overlap("①②", 1), _shape())) == ["1", "2", "◯"]


@needs_k_water
def test_circle_with_several_chars_keeps_old_width():
    # 테두리 1 여러 글자 칸은 전처럼 가장 넓은 원래 글자 폭이다.
    layout = _k_water_layout()
    shape = _shape()
    assert layout.overlap_width(_overlap("AB", 1, inner=0), shape) == max(
        layout.text_width("A", shape), layout.text_width("B", shape)
    )


@needs_k_water
def test_square_digit_copies_box_and_drawn_digit():
    # 사각(□)은 숫자를 글자로 찍으므로 줄 글이 「□3」이고, 두 Glyph 모두 page.items 안 글자다.
    layout = _k_water_layout()
    layout.line_pieces, layout.line_marks = [], []
    try:
        glyphs = _place(_overlap("\U000f02b3", 3), _shape())
        assert "".join(layout.line_pieces) == "□3"
        assert [glyph for _, glyph in layout.line_marks] == glyphs
    finally:
        del layout.line_pieces, layout.line_marks


@needs_circled
def test_unicode_circled_digit_is_not_copied_as_drawn_parts():
    # 원래 경로(테두리 1 「①」)는 전처럼 줄 글에 넣지 않아 「◯1」로 복사되지 않는다.
    pages = layout_document(HwpDocument.open(CIRCLED))
    assert not any("◯" in line.text for page in pages for line in page.text_lines)


@needs_k_water
def test_digits_are_centered_on_drawn_box_in_narrow_box_font():
    # 굴림은 ■가 0.889em이라, 글자 크기 가운데에 두면 구멍이 ■ 가운데에서 약 1pt 오른쪽으로 밀리고 두 자리 숫자가
    # ■ 밖으로 나가 잘린다. 숫자는 그린 ■ 전진폭 가운데에 둔다.
    layout = _k_water_layout()
    shape = _shape(face_ids=[0] * 7)
    frame = layout.measurer.advance("■", shape, metrics.HANGUL, 0)
    assert frame < 0.9 * 1700
    for text, inner in (("\U000f02d0", -2), ("\U000f02d7\U000f02e0", -3)):
        box = _place(_overlap(text, 4, inner), shape)[0]
        hole_shape = layout.digit_inner(_overlap(text, 4, inner), shape)
        left = box.holes[0].x
        right = box.holes[-1].x + layout.text_width(box.holes[-1].char, hole_shape, spacing=0)
        assert (left + right) / 2 == pytest.approx(box.x + frame / 2, abs=13)
        assert box.x - 12 <= left and right <= box.x + frame + 12


@needs_k_water
@pytest.mark.parametrize("border", [5, 7, 14, 200])
def test_border_five_or_more_keeps_old_path(border):
    # 테두리 5 이상(삼각·마름모 등, 깨진 값)은 숫자 표로 풀지 않고 이전 경로(그리지 않음, 원래 글자 폭)다.
    layout = _k_water_layout()
    model = _overlap("\U000f02d0", border)
    assert _place(model, _shape()) == []
    assert layout.overlap_width(model, _shape()) == layout.text_width("\U000f02d0", _shape())


def _k_water_overlap_paragraph():
    layout = _k_water_layout()
    # 자손에 글자 겹침이 있는 문단만 보기를 만들어 본다(문서 전체 문단을 다시 짜지 않게 미리 거른다). 표를 담은 바깥
    # 문단도 자손에 글자 겹침이 있으므로, 보기에 글자 겹침 컨트롤이 직접 있는 문단을 고른다.
    return next(
        node
        for section in layout.document.sections
        for node in _paragraphs(section.nodes)
        if any(isinstance(child.model, OverlapCtrl) for child in _descendants(node))
        and any(
            isinstance(ctrl.model, OverlapCtrl) for ctrl in layout.paragraph_views([node], 50000)[0].controls.values()
        )
    )


@needs_k_water
def test_paragraph_view_direction_decides_overlap_width():
    # 가로쓰기로 확인된 문단 보기(vertical=False)만 숫자 테두리 새 폭을 쓴다. 세로쓰기(vertical=True)와 글 방향을
    # 모르는 보기(인자 없음)는 이전 폭이라, 새 호출부가 방향을 빠뜨려도 이전 경로로 안전하게 남는다.
    layout = _k_water_layout()
    node = _k_water_overlap_paragraph()
    flat = layout.paragraph_views([node], 50000, vertical=False)[0]
    upright = layout.paragraph_views([node], 50000, vertical=True)[0]
    unknown = layout.paragraph_views([node], 50000)[0]
    assert flat.horizontal and not upright.horizontal and not unknown.horizontal
    index = next(i for i, ctrl in flat.controls.items() if isinstance(ctrl.model, OverlapCtrl))
    model = flat.controls[index].model
    shape = layout.styles.char_shapes[flat.shape_ids[index]]
    assert layout.digit_overlap(model, shape) is not None
    old = max(layout.text_width(char, shape) for char in model.text)
    assert layout.item_width(upright, index) == layout.item_width(unknown, index) == old
    assert layout.item_width(flat, index) == layout.overlap_width(model, shape) != old


@needs_k_water
def test_vertical_table_cell_keeps_old_overlap_path(monkeypatch):
    # 세로쓰기 표 칸은 가로 줄로 그리는 경로(table_flow → place_control)라, 칸 보기의 세로 표시로 숫자 테두리를
    # 그리지 않는다(이전 경로: 테두리 4는 그리지 않음). k-water 번호 칸을 세로쓰기로 바꾸면 쪽 나눔이 바뀔 수 있어
    # 모든 쪽을 보고, 그 칸 글(「SaaS」)이 실제로 놓였는지도 확인한다.
    assert [item for item in _k_water_page_13().items if isinstance(item, Glyph) and item.holes]
    monkeypatch.setattr(CellListHeader, "text_direction", property(lambda self: 1))
    pages = layout_document(HwpDocument.open(K_WATER))
    glyphs = [item for page in pages for item in page.items if isinstance(item, Glyph)]
    assert "SaaS" in "".join(glyph.char for glyph in glyphs)
    assert not any(glyph.char == "■" or glyph.holes for glyph in glyphs)


@needs_tbox
def test_textbox_measuring_and_drawing_share_textbox_views(monkeypatch):
    # 글상자 크기 측정(textbox_growth)과 그리기(draw_component)가 같은 보기 도우미로 글 방향을 정한다. 어느 쪽이
    # 도우미를 거치지 않으면 이 시험이 실패한다.
    callers = set()
    original = DocumentLayout.textbox_views

    def spy(self, component, header, width, textbox=True):
        callers.add(sys._getframe(1).f_code.co_name)
        views = original(self, component, header, width, textbox)
        assert all(view.horizontal == (not header.text_direction) for view in views)
        return views

    monkeypatch.setattr(DocumentLayout, "textbox_views", spy)
    layout_document(HwpDocument.open(TABLE_IN_TBOX))
    assert {"draw_component", "textbox_growth"} <= callers
    callers.clear()
    layout_document(HwpDocument.open(VERTICAL_TBOX))
    assert "draw_component" in callers


def _descendants(node):
    for child in node.children:
        yield child
        yield from _descendants(child)


def _paragraphs(nodes):
    for node in nodes:
        if node.tag == tags.PARA_HEADER:
            yield node
        yield from _paragraphs(node.children)
