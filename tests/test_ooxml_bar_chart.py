from __future__ import annotations

import base64
import contextlib
import re
import signal

import pytest
from corpus_paths import CORPUS_ROOT

from rea_hwp.document import HwpDocument
from rea_hwp.render.chart import UnsupportedChart
from rea_hwp.render.layout import layout_document
from rea_hwp.render.ooxml_chart import draw_ooxml_bar_chart, ooxml_chart_contents, value_axis_scale
from rea_hwp.render.scene import Image

CORPUS = CORPUS_ROOT / "oss" / "rhwp"
SERIES_REMOVED_DOC = CORPUS / "issue5447__묶은세로막대형-계열삭제.hwp"
CUSTOM_STYLE_DOC = CORPUS / "2022년 국립국어원 업무계획.hwp"

# 한글 정답 PDF의 값 축(축 길이 147.7px, 라벨 높이 23.67px): 최댓값 -> (최대, 주단위, 칸 수)
HANCOM_VALUE_AXES = [(5.0, (6.0, 2.0, 3)), (6.0, (8.0, 2.0, 4)), (45.0, (50.0, 10.0, 5)), (12.3, (15.0, 5.0, 3))]


def chart_svgs(page) -> list[str]:
    prefix = "data:image/svg+xml;base64,"
    return [
        base64.b64decode(item.href[len(prefix) :]).decode("utf-8")
        for item in page.items
        if isinstance(item, Image) and item.href.startswith(prefix)
    ]


def svg_text(svg: str) -> str:
    return "".join(re.findall(r"<text [^>]*>([^<]*)</text>", svg))


@pytest.mark.parametrize(("maximum", "expected"), HANCOM_VALUE_AXES)
def test_value_axis_scale_matches_hancom_pdf_axes(maximum, expected):
    lo, hi, major, count = value_axis_scale(0.0, maximum, 147.7, 23.67)

    assert lo == 0.0
    assert (hi, major, count) == expected


@pytest.mark.skipif(not SERIES_REMOVED_DOC.exists(), reason="corpus not available")
def test_clustered_column_chart_draws_current_series_not_stale_preview():
    page = layout_document(HwpDocument.open(SERIES_REMOVED_DOC))[0]

    [svg] = chart_svgs(page)
    text = svg_text(svg)
    assert "계열1" in text and "계열3" in text
    assert "계열2" not in text
    assert len(page.excluded) == 1


@pytest.mark.skipif(not SERIES_REMOVED_DOC.exists(), reason="corpus not available")
def test_chart_text_stays_inside_chart_picture_like_hancom_pdf():
    page = layout_document(HwpDocument.open(SERIES_REMOVED_DOC))[0]

    assert page.glyphs == []


@pytest.mark.skipif(not CUSTOM_STYLE_DOC.exists(), reason="corpus not available")
def test_chart_with_custom_series_style_keeps_preview_picture():
    pages = layout_document(HwpDocument.open(CUSTOM_STYLE_DOC))

    [svg] = chart_svgs(pages[23])
    assert "<image" in svg
    assert "<text" not in svg


SINGLE_SERIES_BAR_DOC = CORPUS / "chart__특이케이스__가로막대형_하나만있을떄_단일시리즈제목.hwp"


def test_horizontal_value_axis_adds_step_when_maximum_sits_on_float_threshold():
    # 한글 정답 PDF: 가로 막대 최댓값 4.3 -> 0~5, 주단위 0.5(4.5-4.3이 문턱 0.2와 float로 같다)
    assert value_axis_scale(0.0, 4.3, 300.0, 20.0, True) == (0.0, 5.0, 0.5, 10)


@pytest.mark.skipif(not SINGLE_SERIES_BAR_DOC.exists(), reason="corpus not available")
def test_single_series_bar_chart_titles_with_series_name():
    page = layout_document(HwpDocument.open(SINGLE_SERIES_BAR_DOC))[0]

    [svg] = chart_svgs(page)
    assert svg_text(svg).startswith("계열1")


STOCK_SERIES_REMOVED_DOC = CORPUS / "issue5447__시가고가저가종가-계열삭제.hwp"


@pytest.mark.skipif(not STOCK_SERIES_REMOVED_DOC.exists(), reason="corpus not available")
def test_stock_chart_draws_down_bars_from_first_to_last_series():
    page = layout_document(HwpDocument.open(STOCK_SERIES_REMOVED_DOC))[0]

    [svg] = chart_svgs(page)
    # 한글 정답 PDF: 고가(첫 계열)에서 종가(끝 계열)로 내려가는 검은 막대 넷과 종가 세모 표식(범례 포함 다섯)
    assert svg.count('fill="#404040" stroke="#000000"') == 4
    assert svg.count("<polygon") == 5


STACKED_SERIES_REMOVED_DOC = CORPUS / "issue5447__누적세로막대형-계열삭제.hwp"


@pytest.mark.skipif(not STACKED_SERIES_REMOVED_DOC.exists(), reason="corpus not available")
def test_stacked_column_value_axis_covers_column_sums():
    page = layout_document(HwpDocument.open(STACKED_SERIES_REMOVED_DOC))[0]

    [svg] = chart_svgs(page)
    # 한글 정답 PDF: 두 계열 합 최대 9.5 -> 값 축 0~10, 주단위 2
    assert "0246810" in svg_text(svg)


LINE_ROW_ADDED_DOC = CORPUS / "issue5447__표식이있는꺽은선형-행추가.hwp"


@pytest.mark.skipif(not LINE_ROW_ADDED_DOC.exists(), reason="corpus not available")
def test_line_chart_marks_every_point_and_legend_key():
    page = layout_document(HwpDocument.open(LINE_ROW_ADDED_DOC))[0]

    [svg] = chart_svgs(page)
    # 항목 5 x 계열 3 표식과 범례 표식 3(마름모·네모·세모)
    assert svg.count("<polygon") == 18
    assert svg.count('stroke-width="2.250"') == 3 * 4 + 3


SCATTER_POINT_ADDED_DOC = CORPUS / "issue5447__직선및표식이있는분산형-점추가.hwp"


@pytest.mark.skipif(not SCATTER_POINT_ADDED_DOC.exists(), reason="corpus not available")
def test_scatter_chart_x_axis_reaches_added_point():
    page = layout_document(HwpDocument.open(SCATTER_POINT_ADDED_DOC))[0]

    [svg] = chart_svgs(page)
    # 한글 정답 PDF: 더한 점 x=9 -> x 축 0~10, 주단위 1
    assert "012345678910" in svg_text(svg)


STOCK_MIDDLE_ADDED_DOC = CORPUS / "issue6037__engine__시가고가저가종가-중간계열추가.hwp"


@pytest.mark.skipif(not STOCK_MIDDLE_ADDED_DOC.exists(), reason="corpus not available")
def test_stock_chart_draws_series_without_shape_as_line():
    page = layout_document(HwpDocument.open(STOCK_MIDDLE_ADDED_DOC))[0]

    [svg] = chart_svgs(page)
    # 모양(spPr) 없는 추가계열은 노란 2.25pt 선: 항목 사이 3개와 범례 1개
    assert svg.count('stroke="#ffd700" stroke-width="2.250"') == 4


STACKED_DOC = CORPUS / "issue5447__누적세로막대형-계열삭제.hwp"
_NUMBER = re.compile(r"(<c:v>)-?[0-9.]+(</c:v>)")


@contextlib.contextmanager
def time_limit(seconds: int):
    def stop(signum, frame):
        raise TimeoutError(f"{seconds}초 안에 끝나지 않았다")

    previous = signal.signal(signal.SIGALRM, stop)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def chart_xml(path) -> str:
    document = HwpDocument.open(path)
    for bin_data_id in range(1, len(document.styles.bin_data) + 1):
        item = document.bin_data(bin_data_id)
        contents = ooxml_chart_contents(item.data) if item is not None else None
        if contents is not None:
            return contents.decode("utf-8")
    raise AssertionError(f"새 차트가 없다: {path}")


def first_value(xml: str, text: str) -> str:
    return _NUMBER.sub(lambda m: m.group(1) + text + m.group(2), xml, count=1)


def draw(xml: str):
    with time_limit(5):
        return draw_ooxml_bar_chart(xml.encode("utf-8"), 0.0, 0.0, 322.5, 187.5)


@pytest.mark.parametrize(("low", "high"), [(0.0, 0.0), (float("nan"), 1.0), (0.0, float("inf")), (0.0, float("nan"))])
def test_value_axis_scale_refuses_ranges_it_cannot_scale(low, high):
    with time_limit(5), pytest.raises(UnsupportedChart):
        value_axis_scale(low, high, 147.7, 23.67)


@pytest.mark.skipif(not SERIES_REMOVED_DOC.exists(), reason="corpus not available")
@pytest.mark.parametrize(
    "broken",
    [
        lambda xml: _NUMBER.sub(r"\g<1>0\g<2>", xml),
        lambda xml: first_value(xml, "NaN"),
        lambda xml: first_value(xml, "INF"),
        lambda xml: first_value(xml, "1e300"),
        lambda xml: xml.replace('<c:gapWidth val="150"/>', '<c:gapWidth val="150%"/>'),
        lambda xml: xml.replace('<c:idx val="0"/>', '<c:idx val="x"/>', 1),
    ],
    ids=["all-zero", "nan", "inf", "1e300", "strict-percent", "bad-idx"],
)
def test_broken_chart_numbers_fall_back_to_preview(broken):
    xml = chart_xml(SERIES_REMOVED_DOC)
    assert draw(xml) is not None

    assert draw(broken(xml)) is None


@pytest.mark.skipif(not STACKED_DOC.exists(), reason="corpus not available")
def test_stacked_column_with_negative_value_falls_back_to_preview():
    xml = chart_xml(STACKED_DOC)
    assert draw(xml) is not None

    assert draw(first_value(xml, "-3")) is None
