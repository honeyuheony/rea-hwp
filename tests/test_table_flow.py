from types import SimpleNamespace

import pytest
from corpus_paths import CORPUS_ROOT, USER_CORPUS_ROOT

from rea_hwp import tags
from rea_hwp.document import HwpDocument
from rea_hwp.render.layout import DocumentLayout, layout_document
from rea_hwp.render.scene import Glyph, Image
from rea_hwp.render.table_flow import (
    BIG_ROOM,
    CellLine,
    TableFlowMixin,
    assign_fragments,
    inline_table_rooms,
    paging_eligible,
)
from rea_hwp.render.tables import cell_margins, stored_pieces


def _paragraph(*positions: tuple[int, int]) -> list:
    return [SimpleNamespace(vertical_pos=top, line_height=height, text_height=height) for top, height in positions]


def _line(top: float, origin: float, carry: int = 0, cell_top: float = 0.0) -> CellLine:
    return CellLine(top, top + 100, None, 0, 0.0, 100.0, origin, carry, cell_top, 141.0)


def test_rewound_vertical_pos_starts_new_piece():
    paragraphs = [_paragraph((0, 1200), (1920, 1200)), _paragraph((0, 1200)), _paragraph((1920, 1000))]
    pieces, heights = stored_pieces(paragraphs)
    assert pieces == [[0, 0], [1], [1]]
    assert heights == [3120, 2920]


def test_cell_margins_fall_back_to_table_without_inner_margin_bit():
    table = SimpleNamespace(margins=[56, 56, 141, 141])
    own = SimpleNamespace(header=SimpleNamespace(attr=1 << 16, margins=[510, 510, 141, 141]))
    unset = SimpleNamespace(header=SimpleNamespace(attr=0x20, margins=[65535] * 4))
    assert cell_margins(table, own) == [510, 510, 141, 141]
    assert cell_margins(table, unset) == [56, 56, 141, 141]


def test_lines_go_to_fragment_holding_their_top():
    fragments = [(0.0, 1000.0), (1000.0, 2500.0)]
    assigned = assign_fragments([_line(200, 150), _line(1200, 1100)], fragments)
    assert [offset for _, offset in assigned[0]] == [150]
    assert [offset for _, offset in assigned[1]] == [100]


def test_carried_piece_starts_at_top_of_later_fragment():
    fragments = [(0.0, 0.0), (0.0, 1000.0), (1000.0, 2000.0), (2000.0, 3000.0)]
    assigned = assign_fragments([_line(500, 400, carry=2, cell_top=300)], fragments)
    assert list(assigned) == [3]
    assert assigned[3][0][1] == 141.0


def test_fragment_continuing_inside_cell_starts_below_cell_spacing():
    grid = SimpleNamespace(row_y=[0.0, 2000.0, 9000.0], spacing=255.0, table=SimpleNamespace(repeat_header=False))
    state = SimpleNamespace(geometry=SimpleNamespace(body_top=5000.0))
    inside = TableFlowMixin.fragment_origins(None, state, grid, None, 7000.0, 0.0, [(0.0, 4000.0), (4000.0, 9000.0)])
    at_row = TableFlowMixin.fragment_origins(None, state, grid, None, 7000.0, 0.0, [(0.0, 2000.0), (2000.0, 9000.0)])
    assert inside == [7000.0, 5255.0]
    assert at_row == [7000.0, 5000.0]


def test_paging_engine_takes_spaced_and_unsplit_tables():
    # 셀 간격 표, 나누지 않는 표(쪽 나눔 방식 0), 글자처럼 취급한 표도 엔진으로 나눈다. 크기 보호 표는 아직 제외한다.
    def eligible(spacing=0, page_break=1, tac=False, protect=False):
        grid = SimpleNamespace(spacing=spacing, table=SimpleNamespace(page_break=page_break), cells=[])
        model = SimpleNamespace(treat_as_char=tac, protect_size=protect)
        return paging_eligible(grid, {}, model, lambda ctrl: True)

    assert eligible(spacing=283)
    assert eligible(page_break=0)
    assert eligible(tac=True)
    assert not eligible(protect=True)


def test_inline_table_is_never_split():
    # 옛 기대(쪽 맨 위에서 많이 넘치면 나눔)는 한글 2022 실험과 다르다: 쪽 맨 위 표는 넘쳐도 통째로 그린다.
    def rooms(height, first=10000.0, top=False):
        return inline_table_rooms(height, first, 60000.0, top)

    assert rooms(9999.0) == (BIG_ROOM, 60000.0)
    assert rooms(20000.0, top=True) == (BIG_ROOM, 60000.0)
    assert rooms(70000.0) == (BIG_ROOM, 60000.0)
    assert rooms(12000.0) == (0.0, BIG_ROOM)


SOFTWARE_DOC = CORPUS_ROOT / "oss" / "hwpjs" / "legacy__rust__crates__hwp-core__tests__fixtures__software.hwp"


@pytest.mark.skipif(not SOFTWARE_DOC.exists(), reason="corpus not available")
def test_inline_table_that_does_not_fit_moves_whole_to_next_page():
    pages = layout_document(HwpDocument.open(SOFTWARE_DOC))

    # 한글 2022 PDF: 1쪽은 「4. 작품 설계」로 끝나고 표 머리 「SW 내부 설계」는 2쪽 첫 글이다.
    first = "".join(g.char for g in pages[0].glyphs if g.char.strip())
    second = "".join(g.char for g in pages[1].glyphs if g.char.strip())
    assert first.endswith("4.작품설계")
    assert second.startswith("SW내부설계")


NESTED_SQUARE_DOC = CORPUS_ROOT / "oss" / "rhwp" / "issue5787__nested_square_table_horz_offset.hwp"


@pytest.mark.skipif(not NESTED_SQUARE_DOC.exists(), reason="corpus not available")
def test_paging_engine_takes_table_with_nested_square_table():
    # 셀 안 어울림 표는 기준 줄과 함께 통째로 그리고 셀 내용 높이에 넣으므로 바깥 표도 엔진으로 나눈다.
    document = HwpDocument.open(NESTED_SQUARE_DOC)
    flow = DocumentLayout(document)
    tables = [
        node
        for node in document.sections[0].walk()
        if node.tag == tags.CTRL_HEADER and node.model.ctrl_id == tags.TABLE_CTRL
    ]
    outer = [table for table in tables if any(_contains(table, other) for other in tables if other is not table)]
    assert outer
    grid, views = flow.table_layout_grid(outer[0])
    assert paging_eligible(grid, views, outer[0].model, flow.nested_eligible)


def _contains(node, target) -> bool:
    return any(child is target or _contains(child, target) for child in node.children)


RHWP = CORPUS_ROOT / "oss" / "rhwp"


def _baseline_of(page, text: str) -> float:
    """text로 시작하는 글줄의 기준선(pt)."""
    return next(line.baseline / 100 for line in page.text_lines if line.text.replace(" ", "").startswith(text))


@pytest.mark.skipif(not (RHWP / "80168_regulatory_analysis.hwp").exists(), reason="corpus not available")
def test_reflowed_lines_avoid_table_moved_whole_to_next_page():
    page = layout_document(HwpDocument.open(RHWP / "80168_regulatory_analysis.hwp"))[92]
    # 한글 2022 PDF 93쪽: 통째로 옮겨 온 표(60.9~140.4pt) 아래 「③ 대안의 선택 및 근거」 171.84pt.
    assert _baseline_of(page, "③대안의선택") == pytest.approx(171.84, abs=0.5)
    assert _baseline_of(page, "3.규제목표") == pytest.approx(281.04, abs=0.5)


@pytest.mark.skipif(not (RHWP / "rowbreak-problem-pages.hwp").exists(), reason="corpus not available")
def test_float_straddling_stored_cell_piece_starts_next_piece():
    pages = layout_document(HwpDocument.open(RHWP / "rowbreak-problem-pages.hwp"))
    # 한글 2022 PDF: 17쪽 「예시」 상자 머리 382.20pt, 표는 17쪽에서 끝나고 「보안 분야」는 18쪽 84.48pt.
    assert len(pages) == 18
    assert _baseline_of(pages[16], "【예시") == pytest.approx(382.20, abs=0.5)
    assert _baseline_of(pages[17], "\U000f02b8보안분야") == pytest.approx(84.48, abs=0.5)


@pytest.mark.skipif(not (RHWP / "float-stack-defer.hwp").exists(), reason="corpus not available")
def test_stacked_table_that_cannot_start_below_anchor_line_moves_whole():
    pages = layout_document(HwpDocument.open(RHWP / "float-stack-defer.hwp"))
    # 한글 2022 PDF: 1쪽은 제목과 표 첫째, 2쪽은 표 둘째 전체(머리 「평가항목」 기준선 111.00pt).
    assert len(pages) == 2
    assert sum(line.text.replace(" ", "").startswith("평가항목") for line in pages[0].text_lines) == 1
    assert _baseline_of(pages[1], "평가항목") == pytest.approx(111.00, abs=0.5)


@pytest.mark.skipif(not (RHWP / "rnote__labnote-001.hwp").exists(), reason="corpus not available")
def test_floats_of_paragraph_overflowing_to_new_page_start_at_body_top():
    page = layout_document(HwpDocument.open(RHWP / "rnote__labnote-001.hwp"))[1]
    # 한글 2022 PDF 2쪽: 넘어온 문단(저장 줄 76450)의 표 셋이 쪽 위부터 쌓인다. 머리 표 「실험제목」 43.92pt,
    # 아래 표 「기록자」는 줄 표 아래라 한 번만 있고 그 위치는 머리 표보다 아래다.
    assert _baseline_of(page, "실험제목") == pytest.approx(43.92, abs=0.5)
    assert _baseline_of(page, "기록자") > 700


@pytest.mark.skipif(not (RHWP / "exam_math_no.hwp").exists(), reason="corpus not available")
def test_paragraph_without_line_info_starts_below_its_own_floats():
    page = layout_document(HwpDocument.open(RHWP / "exam_math_no.hwp"))[8]
    # 한글 2022 PDF 9쪽: 줄 정보 없는 구역 첫 문단 「23.」은 종이 기준 「5지선다형」 표 아래 239.88pt,
    # 다음 문단 「①」(저장 줄 15006)은 270.36pt 그대로다.
    assert _baseline_of(page, "23.") == pytest.approx(239.88, abs=1.0)
    assert _baseline_of(page, "①") == pytest.approx(270.36, abs=0.5)


@pytest.mark.skipif(not (RHWP / "exam_science.hwp").exists(), reason="corpus not available")
def test_inline_table_on_line_with_floored_half_baseline_starts_at_line_top():
    page = layout_document(HwpDocument.open(RHWP / "exam_science.hwp"))[2]
    # 한글 2022 PDF 3쪽: 기준선이 높이 23931의 내림한 절반 11965로 저장된 표 줄, 첫 셀 줄 「[실험 과정]」 154.20pt.
    assert _baseline_of(page, "[실험과정]") == pytest.approx(154.20, abs=0.5)


@pytest.mark.skipif(not (RHWP / "issue1921__59043_regulatory_analysis.hwp").exists(), reason="corpus not available")
def test_no_overlap_cell_pictures_stack_below_earlier_ones():
    pages = layout_document(HwpDocument.open(RHWP / "issue1921__59043_regulatory_analysis.hwp"))

    def tops(page) -> list[float]:
        return sorted(item.y / 100 for item in page.items if isinstance(item, Image) and item.width > 5000)

    # 한글 2022 PDF: 셀 그림 위 끝 11쪽 515.9·693.1pt(앞 그림 아래), 넷째 그림은 12쪽 조각 위 59.6pt, 13쪽엔 그림 없음.
    assert tops(pages[10])[-2:] == pytest.approx([515.9, 693.1], abs=1.0)
    assert tops(pages[11])[0] == pytest.approx(59.6, abs=1.0)
    assert tops(pages[12]) == []


INTERIM_DOC = RHWP / "정책연구용역사업 중간진도보고서(살아있는 간장 기증자의 의학적 선별기준 연구).hwp"


@pytest.mark.skipif(not INTERIM_DOC.exists(), reason="corpus not available")
def test_square_float_overflowing_last_column_moves_to_next_page_top():
    pages = layout_document(HwpDocument.open(INTERIM_DOC))

    def picture_tops(page) -> list[float]:
        return [item.y / 100 for item in page.items if isinstance(item, Image) and item.height > 2000]

    # 한글 2022 PDF: 155쪽 문단의 그림 64(어울림, 본문 영역 제한)는 156쪽 본문 위 + 바깥 위 여백 67.5pt에 놓인다.
    assert picture_tops(pages[154]) == []
    assert picture_tops(pages[155]) == pytest.approx([67.5], abs=0.5)


USER_FORM = USER_CORPUS_ROOT / "user-report-sensitive.hwp"


@pytest.mark.skipif(not USER_FORM.exists(), reason="user sample not available")
def test_distributed_line_with_single_inline_table_stays_left():
    page = layout_document(HwpDocument.open(USER_FORM))[0]
    glyphs = (item for item in page.items if isinstance(item, Glyph) and abs(item.y / 100 - 151.7) < 1)
    first = next(glyph for glyph in glyphs if glyph.char == "보")
    # 한글 2022 PDF: 배분 정렬 문단의 글자처럼 취급한 표 첫 칸 「보고일시」 61.44pt(가운데로 두면 69.48pt).
    assert first.x / 100 == pytest.approx(61.44, abs=0.3)


USER_PARSING = USER_CORPUS_ROOT / "user-parsing-test.hwp"


@pytest.mark.skipif(not USER_PARSING.exists(), reason="user sample not available")
def test_overfull_cell_keeps_lines_narrowed_by_square_wrap_picture():
    page = layout_document(HwpDocument.open(USER_PARSING))[2]
    glyphs = [item for item in page.items if isinstance(item, Glyph) and abs(item.y / 100 - 426.6) < 1]
    # 한글 2022 PDF 3쪽: 그림 옆에서 줄을 바꿔 둘째 줄 「제목ㆍ위치가」가 94.44pt에서 시작한다.
    assert glyphs[0].char == "제"
    assert glyphs[0].x / 100 == pytest.approx(94.44, abs=0.3)


@pytest.mark.skipif(not USER_PARSING.exists(), reason="user sample not available")
def test_paragraph_relative_picture_in_cell_starts_above_space_before():
    page = layout_document(HwpDocument.open(USER_PARSING))[2]
    label = next(item for item in page.items if isinstance(item, Glyph) and item.char == "꺾" and item.x > 35000)
    # 한글 2022 PDF 3쪽: 셀 첫 문단(위 간격 1100) 그림 안 글상자 「중꺾마」 기준선 379.92pt(첫 줄 기준이면 390.88pt).
    assert label.y / 100 == pytest.approx(379.92, abs=0.5)
