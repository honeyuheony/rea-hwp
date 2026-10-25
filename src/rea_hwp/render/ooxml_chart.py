"""한글 2022가 새 차트(OOXMLChartContents)를 HncDrawingEngine.dll로 다시 그릴 때 PDF 글자로 찍는 기울어진 범주 이름.
도형은 비트맵으로 찍혀 옮기지 않았고 3차원 묶은 세로 막대형만 옮겼다. 주소는 HncDrawingEngine.dll 기준이다."""

from __future__ import annotations

import io
import itertools
import math
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import olefile

from .chart import ChartDrawing, ChartGlyph, FontFace, UnsupportedChart

_C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
#: BinData 앞 4바이트는 뒤따르는 복합 파일 길이다.
_LENGTH_PREFIX = 4

#: 창 1px = 15twip(FUN_10343d20).
_TW = 15.0
#: 그림 영역 여백 8px(0x1034d0e6).
_PLOT_MARGIN_PX = 8.0
_C45 = math.cos(math.radians(45))
#: 글자 측정 +5(FUN_103c2e50) + 라벨 상자 +2(FUN_102f8890, 0x1055e1d0).
_LABEL_PAD_PX = 5 + 2
#: 값 라벨 크기 = 측정(텍스트 엔진 +5) + 5(FUN_103cfb40).
_VALUE_LABEL_EXTRA_PX = 10.0
#: 축 여백에 더하는 값(FUN_102dfe40).
_AXIS_MARGIN_TW = 200.0
#: 종류 2 값 축(+0x58)의 XMM3 = 0(0x103cebc2) -> 200이 max.x 여백(사선 4번 슬롯 FUN_102d9640).
_MAX_X_MARGIN_TW = 200.0
#: 범주 라벨 기준점은 축 평면 (i*축길이/n, -200twip)(FUN_102f77f0).
_LABEL_OFFSET_TW = 200.0
#: 상자 깊이 비율(Plot3DRender+0x10, FUN_1037ad90).
_DEPTH_RATIO = 0.8
#: 글자 칸 높이 = 글꼴 크기 * 1.2(CharItemView 0x1021dce0, 0x1055e110, 가로쓰기).
_LINE_HEIGHT_RATIO = 1.2
#: 기본 제목 글꼴 크기(pt). 출처: 역분석 스크립트 label3d_engine.py의 표, 10pt -> 14pt만 두 문서 PDF로 맞춰 봄.
_TITLE_PT = {8.0: 12.0, 10.0: 14.0, 12.0: 18.0, 18.0: 21.0}
#: 한글 PDF 장치 해상도(bench/accuracy.py DEVICE_PT). DrawDriverString 위치는 장치 점 정수다.
_DEVICE_DPI = 600.0
#: 표에 넣은 모든 글꼴(TrueType 파일로 확인)에서 hhea ascender = usWinAscent, -hhea descender = 2번 칸이다.
_HHEA_ASCENT_FIELD, _HHEA_DESCENT_FIELD = 3, 2


def ooxml_chart_contents(bin_data: bytes) -> bytes | None:
    body = io.BytesIO(bin_data[_LENGTH_PREFIX:])
    if not olefile.isOleFile(body):
        return None
    with olefile.OleFileIO(body) as storage:
        if not storage.exists("OOXMLChartContents"):
            return None
        return storage.openstream("OOXMLChartContents").read()


def interval(mn: float, mx: float, n: int) -> tuple[float, float, float, int]:
    """ScaleInterval 자동 눈금(FUN_1039f950): (최소, 최대, 주단위, 눈금 수)."""
    if mx <= mn or n < 1:
        return mn, mx, 1.0, 1
    d = float(n)
    lo = hi = major = 0.0
    while d > 0:
        step = (mx - mn) / d
        if step < 1e-31:
            step = 0.0
        e = int(math.log10(step)) if step > 0 else 0
        if step < 1.0:
            e -= 1
        p = 100.0 if e == 2 else 10.0**e
        i = 0
        # 표 0x10556130(√2, √10, √50)과 0x105564e0(1, 2, 5, 10)
        while i < 3 and not (step / p < (1.414214, 3.162278, 7.071068)[i]):
            i += 1
        major = (1.0, 2.0, 5.0, 10.0)[i] * p
        q = mn / major
        k = int(q) - (1 if q < 0 else 0)
        if abs((k + 1.0) - q) < 2e-5:
            k += 1
        lo_c = k * major
        q = mx / major
        k = int(q + 1.0) - (1 if q < -1.0 else 0)
        if abs((q + 1.0) - k) < 2e-5:
            k -= 1
        hi_c = k * major
        lo = lo_c if lo_c <= mn else mn
        hi = hi_c if mx <= hi_c else mx
        if (hi - lo) / major <= n:
            break
        d -= 1.0
    if d <= 1.0:
        return lo, hi, hi - lo, 1
    return lo, hi, major, int((hi - lo) / major + 2e-5)


def value_scale(
    values: list[float], axis_len_tw: float, label_h_tw: float, include_zero: bool
) -> tuple[float, float, float, int]:
    """3D 값 축 눈금(FUN_103ce5c0 -> ScaleInterval 슬롯 3 0x1039fd10). include_zero는 슬롯 17(0x103a0080)."""
    mn, mx = min(values), max(values)
    if include_zero:
        if mn > 0:
            mn = 0.0
        elif mx < 0:
            mx = 0.0
    if mn == mx:
        mx = mn + 1.0
    n = 10 if axis_len_tw / (1.0 * 1.5) >= 1 else 1  # 첫 라벨 높이 배열 값 1.0(0x103cea20)
    lo, hi, major, count = mn, mx, 1.0, 1
    while n > 0:
        lo, hi, major, count = interval(mn, mx, n)
        mn, mx = lo, hi
        if count < 10 and (count - 1 <= axis_len_tw / (label_h_tw * 1.0) or count == 1):
            break
        n = count - 1
    return lo, hi, major, count


@dataclass(frozen=True)
class _Bar3D:
    labels: list[str]
    series: list[str]
    values: list[float]
    size_pt: float
    face: str
    rot_x: float
    rot_y: float


def _val(element: ET.Element | None, tag: str, default: str | None = None) -> str | None:
    found = element.find(_C + tag) if element is not None else None
    return found.get("val", default) if found is not None else default


def _has_manual_layout(element: ET.Element | None) -> bool:
    return element is not None and element.find(f"{_C}layout/{_C}manualLayout") is not None


def _parse_supported(contents: bytes) -> _Bar3D | None:
    """옮긴 범위(제목 기본 글, 범례 r, 기본 축, 자동 눈금, 묶은 3D 세로 막대 하나)의 차트만 읽는다. 밖이면 None."""
    try:
        root = ET.fromstring(contents)
    except ET.ParseError:
        return None
    chart = root.find(_C + "chart")
    if root.tag != _C + "chartSpace" or chart is None:
        return None
    title = chart.find(_C + "title")
    if (
        title is None
        or title.find(_C + "tx") is not None
        or _val(title, "overlay", "0") != "0"
        or _has_manual_layout(title)
        or title.find(f".//{_A}defRPr[@sz]") is not None
        or _val(chart, "autoTitleDeleted", "0") != "0"
    ):
        return None
    view = chart.find(_C + "view3D")
    if (
        view is None
        or _val(view, "rAngAx") != "1"
        or _val(view, "rotX") is None
        or _val(view, "rotY") is None
        or _val(view, "hPercent", "100") != "100"
        or _val(view, "depthPercent", "100") != "100"
    ):
        return None
    plot = chart.find(_C + "plotArea")
    if plot is None or _has_manual_layout(plot):
        return None
    kinds = [child for child in plot if child.tag.endswith("Chart")]
    if len(kinds) != 1 or kinds[0].tag != _C + "bar3DChart":
        return None
    bar = kinds[0]
    if (
        _val(bar, "barDir") != "col"
        or _val(bar, "grouping") != "clustered"
        or _val(bar, "gapDepth", "150") != "150"
        or _val(bar, "shape", "box") != "box"
    ):
        return None
    cat_axes, val_axes = plot.findall(_C + "catAx"), plot.findall(_C + "valAx")
    others = [child for child in plot if child.tag in (_C + "serAx", _C + "dateAx")]
    if len(cat_axes) != 1 or len(val_axes) != 1 or others:
        return None
    for axis, position in ((cat_axes[0], "b"), (val_axes[0], "l")):
        scaling = axis.find(_C + "scaling")
        if (
            axis.find(_C + "txPr") is not None
            or _val(axis, "delete", "0") != "0"
            or _val(axis, "axPos") != position
            or _val(axis, "tickLblPos", "nextTo") != "nextTo"
            or _val(scaling, "orientation", "minMax") != "minMax"
            or (scaling is not None and any(scaling.find(_C + tag) is not None for tag in ("min", "max", "logBase")))
            or axis.find(_C + "majorUnit") is not None
        ):
            return None
    number_format = val_axes[0].find(_C + "numFmt")
    if number_format is not None and number_format.get("formatCode") != "General":
        return None
    legend = chart.find(_C + "legend")
    if (
        legend is None
        or _val(legend, "legendPos", "r") != "r"
        or _has_manual_layout(legend)
        or _val(legend, "overlay", "0") != "0"
        or legend.find(_C + "txPr") is not None
        or legend.find(_C + "legendEntry") is not None
    ):
        return None
    text = root.find(_C + "txPr")
    body = text.find(_A + "bodyPr") if text is not None else None
    run = text.find(f".//{_A}defRPr") if text is not None else None
    latin = run.find(_A + "latin") if run is not None else None
    east_asian = run.find(_A + "ea") if run is not None else None
    if (
        body is None
        or body.get("vert", "horz") != "horz"  # BodyProperty::GetVert 기본(가로쓰기)만 옮겼다
        or body.get("rot", "0") != "0"
        or latin is None
        or (east_asian is not None and east_asian.get("typeface") != latin.get("typeface"))
    ):
        return None
    series = bar.findall(_C + "ser")
    if not series:
        return None
    names, values = [], []
    for ser in series:
        name = ser.find(f"{_C}tx//{_C}v")
        cache = ser.find(f"{_C}val/{_C}numRef/{_C}numCache")
        if name is None or name.text is None or cache is None:
            return None
        names.append(name.text)
        try:
            values += [float(point.text or "") for point in cache.iter(_C + "v")]
        except ValueError:
            return None
    points = series[0].findall(f"{_C}cat/{_C}strRef/{_C}strCache/{_C}pt")
    labels = [point.findtext(_C + "v") or "" for point in sorted(points, key=lambda p: int(p.get("idx", "0")))]
    if not labels or not values:
        return None
    return _Bar3D(
        labels,
        names,
        values,
        int(run.get("sz", "1000")) / 100,
        latin.get("typeface", ""),
        float(_val(view, "rotX") or 0),
        float(_val(view, "rotY") or 0),
    )


def draw_ooxml_chart(contents: bytes, left: float, top: float, width: float, height: float) -> ChartDrawing | None:
    """개체 상자(쪽 좌표 pt)에 놓인 새 차트의 기울어진 범주 이름. 이름이 돌지 않으면 빈 목록, 범위 밖이면 None."""
    chart = _parse_supported(contents)
    if chart is None or chart.size_pt not in _TITLE_PT:
        return None
    try:
        # 값 축 0 포함 규칙(0x103a0080)은 켜는 호출자를 확인하지 못해 두 경우의 결과가 같을 때만 쓴다.
        drawings = [_label_glyphs(chart, left, top, width, height, zero) for zero in (True, False)]
    except UnsupportedChart:
        return None  # 폭·세로 지표 표에 없는 글꼴·글자는 옮긴 범위 밖으로 둔다
    return drawings[0] if drawings[0] == drawings[1] else None


def _label_glyphs(
    chart: _Bar3D, left: float, top: float, width: float, height: float, include_zero: bool
) -> ChartDrawing | None:
    face = FontFace(chart.face, chart.size_pt)
    vertical = face.vertical
    em_px = lambda pt: pt * 96 / 72  # noqa: E731
    line_px = lambda pt: _LINE_HEIGHT_RATIO * em_px(pt)  # noqa: E731
    # 기준선 = 칸 위에서 높이 * otmMacAscent/(otmMacAscent+otmMacDescent)(CharItemView +0x40)
    ascent, descent = vertical[_HHEA_ASCENT_FIELD], vertical[_HHEA_DESCENT_FIELD]
    base_ratio = ascent / (ascent + descent)
    size = chart.size_pt

    def text_px(text: str) -> float:
        return sum(face.design_advance(char) / face.metric.em * em_px(size) for char in text)

    # 차트 공간 = 개체 상자(CreateChartForm 0x10304540, TransformUtil::GetTotal)
    box_w, box_h = width * 96 / 72, height * 96 / 72
    # 기본 제목 높이 = 줄높이 + 5(측정) + 5(FUN_103c5ae0)
    title_h = line_px(_TITLE_PT[size]) + 5 + 5
    # 범례 표식 = 줄높이*0.5, 여백 = 표식*0.8(FUN_10352e70), 폭 = 최대 글자폭 + 표식 + 2*여백(FUN_10353770)
    marker = line_px(size) * (0.35 if size > 16 else 0.5)  # 16pt 넘는 0.35는 역분석 스크립트 값, 확인하지 못함
    legend_w = min(max(text_px(name) for name in chart.series) + marker + 2 * 0.8 * marker, box_w / 3)
    # 그림 영역(FUN_10379410)
    plot_x, plot_y = _PLOT_MARGIN_PX, title_h + 2 * _PLOT_MARGIN_PX
    plot_w = box_w - (legend_w + _PLOT_MARGIN_PX) - 2 * _PLOT_MARGIN_PX
    plot_h = box_h - (title_h + _PLOT_MARGIN_PX) - 2 * _PLOT_MARGIN_PX
    # 창(FUN_10343d20): (w-1)*15 x (h-1)*15 twip, 원점 (x+0.5, y+0.5)px
    win_w, win_h = (plot_w - 1) * _TW, (plot_h - 1) * _TW
    origin_x, origin_y = plot_x + 0.5, plot_y + 0.5
    if win_w <= 0 or win_h <= 0:
        return None

    labels = chart.labels
    n = len(labels)
    label_w = [text_px(text) + _LABEL_PAD_PX for text in labels]
    label_h = line_px(size) + _LABEL_PAD_PX

    def category_label(axis_len_tw: float) -> tuple[float, int]:
        """회전 결정(FUN_102f8890): 라벨 상자 높이와 각도. 축 길이 +0x180이 0이면 돌지 않는다."""
        if axis_len_tw != 0:
            slot = axis_len_tw * 96 / 1440 / n
            if slot < max(label_w):
                if (label_h - 7) * 1.4 < slot:
                    return max(label_w) * _C45 + label_h * 0.5, 45
                return max(label_w), 90
        return label_h, 0

    value_h = line_px(size) + _VALUE_LABEL_EXTRA_PX

    def value_label_width(axis_len_tw: float) -> float:
        lo, _, major, count = value_scale(chart.values, axis_len_tw, value_h * _TW, include_zero)
        return max(text_px("%.10g" % (lo + i * major)) for i in range(count + 1))

    # 눈금은 축 길이에 따라 달라지므로 창 높이를 축 길이로 두고 측정하고, 맞춤 뒤 최종 상자 높이로 다시 측정한다.
    first_value_w = value_label_width(win_h)
    value_w = first_value_w + _VALUE_LABEL_EXTRA_PX
    # 값 축 여백(min.x) = max(Plot3DRender+0xa0, 값 라벨 폭) + 200(0x103cebcc, +1 없음)
    margin_value = value_w * _TW + _AXIS_MARGIN_TW
    rot_y, rot_x = chart.rot_y, chart.rot_x
    # 직각 축이면 rotY' = rotY/2+10(FUN_1037c7d0)
    if 0 < rot_y <= 90:
        rot_y = rot_y * 0.5 + 10
    if 85 < rot_x <= 90:
        rot_x = 84
    # 사선 투영(FUN_103d2b60 종류 1): x' = x + sin(rotY')z, y' = y + sin(rotX)cos(rotY')z
    sin_b = math.sin(math.radians(rot_y))
    sin_a_cos_b = math.sin(math.radians(rot_x)) * math.cos(math.radians(rot_y))

    def project(x: float, y: float, z: float) -> tuple[float, float]:
        return x + sin_b * z, y + sin_a_cos_b * z

    series_axis = 1  # 묶은 막대는 계열 축 칸이 하나다

    def bounds(box, margins) -> tuple[float, float, float, float]:
        """(상자 ± 여백) 투영 범위를 창 비율로 넓힌 것(0x103d32e1에서 다시 계산)."""
        (x0, y0, z0), (x1, y1, z1) = box
        points = [
            project(a, b, c)
            for a in (x0 - margins[0], x1 + margins[3])
            for b in (y0 - margins[1], y1 + margins[2])
            for c in (z0, z1)
        ]
        a0, a1 = min(p[0] for p in points), max(p[0] for p in points)
        b0, b1 = min(p[1] for p in points), max(p[1] for p in points)
        span_w, span_h = a1 - a0, b1 - b0
        if span_h * win_w <= span_w * win_h:
            extra = (span_w * win_h / win_w - span_h) * 0.5
            b0, b1 = b0 - extra, b1 + extra
        else:
            extra = (span_h * win_w / win_h - span_w) * 0.5
            a0, a1 = a0 - extra, a1 + extra
        return a0, a1, b0, b1

    def centered(size3, margins):
        cx = (-margins[0] + size3[0] + margins[3]) * 0.5
        cy = (-margins[1] + size3[1] + margins[2]) * 0.5
        cz = size3[2] * 0.5
        return ((-cx, -cy, -cz), (size3[0] - cx, size3[1] - cy, size3[2] - cz))

    def fit(ratio: float, margins) -> tuple[float, float, float]:
        """상자(FUN_1037ad90)를 맞춘 뒤 s = 창폭/투영 범위폭을 s > 0.99까지 곱한다(FUN_10344150)."""
        x = ratio * n / max(n, series_axis)
        size3 = (x, 1.0, _DEPTH_RATIO * series_axis / max(n, series_axis) * x)
        b = bounds(centered(size3, (0, 0, 0, 0)), (0, 0, 0, 0))
        size3 = tuple(c * win_w / (b[1] - b[0]) for c in size3)
        for _ in range(100):
            b = bounds(centered(size3, margins), margins)
            scale = win_w / (b[1] - b[0])
            size3 = tuple(c * scale for c in size3)
            if scale > 0.99:
                break
        return size3

    # 여백(twip): min.x, min.y, max.y, max.x. 패스 사이 유지하고 최댓값으로 갱신한다(FUN_102d91f0/9210/9230).
    margins = [0.0, 0.0, 0.0, 0.0]

    def update_margins(category_h_px: float) -> float:
        # 범주 여백(min.y) = 라벨 높이 + 1(0x102f757b) + 200(FUN_102dfe40), 값 라벨 높이/2 -> min.y·max.y
        margin_category = category_h_px * _TW + 1 + _AXIS_MARGIN_TW
        margins[0] = max(margins[0], margin_value)
        margins[1] = max(margins[1], margin_category, value_h * _TW / 2)
        margins[2] = max(margins[2], value_h * _TW / 2)
        margins[3] = max(margins[3], _MAX_X_MARGIN_TW)
        return margin_category

    # 두 패스(FUN_1037e940). 첫 패스 시작의 축 길이는 0(생성자 0x102df150)이라 돌지 않은 라벨로 측정한다.
    first_h, _ = category_label(0)
    first_margin_category = update_margins(first_h)
    first_size = fit(min(max(win_w / win_h, 0.05), 20), tuple(margins))
    second_h, angle = category_label(first_size[0])
    update_margins(second_h)
    # 상자 비율(FUN_103d4610) = (창폭 - 범주축 +0x184)/(창높이 - 값축 +0x184), 첫 패스 끝 값
    if win_h == margin_value:
        return None
    ratio = min(max((win_w - first_margin_category) / (win_h - margin_value), 0.05), 20)
    final_size = fit(ratio, tuple(margins))
    if angle == 0:
        return ChartDrawing()
    if angle != 45:
        return None
    final_value_w = value_label_width(final_size[1])
    if final_value_w != first_value_w:
        return None  # 값 라벨 폭이 바뀌면 셋째 배치가 필요한지 확인하지 못했다
    box = centered(final_size, tuple(margins))
    b = bounds(box, tuple(margins))
    k = win_w / (b[1] - b[0])

    def screen(x: float, y: float, z: float) -> tuple[float, float]:
        px, py = project(x, y, z)
        return origin_x + (px - b[0]) * k / _TW, origin_y + (b[3] - py) * k / _TW

    (x0, y0, z0), (x1, _, _) = box
    step = (x1 - x0) / n
    anchors = [screen(x0 + i * step, y0 - _LABEL_OFFSET_TW, z0) for i in range(n + 1)]
    baseline_shift = base_ratio * line_px(size) - line_px(size) / 2
    dot = 72.0 / _DEVICE_DPI
    glyphs = []
    for i, text in enumerate(labels):
        text_w = text_px(text)
        # 상자 배치(FUN_102f9750): W = w*cos45 + h/2, 왼쪽 = 두 기준점 가운데 - W, 위 = 기준점 y, 315도 글상자
        box_w45 = (text_w + _LABEL_PAD_PX) * _C45 + label_h * 0.5
        middle = (anchors[i][0] + anchors[i + 1][0]) / 2
        cx, cy = middle - box_w45 / 2, anchors[i][1] + box_w45 / 2
        # 한 줄을 상자 가운데에 두고 상자 중심으로 돌린다. 글자는 CharItemView::DrawDirect(0x10226730)가 하나씩 찍는다.
        start_x = cx - text_w / 2 * _C45 + baseline_shift * _C45
        start_y = cy + text_w / 2 * _C45 + baseline_shift * _C45
        pen = 0.0
        for char in text:
            if char.strip():
                x = round((left + (start_x + pen * _C45) * 72 / 96) / dot) * dot
                y = round((top + (start_y - pen * _C45) * 72 / 96) / dot) * dot
                glyphs.append(ChartGlyph(char, x, y, size, chart.face, 45.0))
            pen += text_px(char)
    return ChartDrawing(glyphs=glyphs)


# ---------------------------------------------------------------------------------------------------------------
# 2차원 막대(barChart). 도형과 글자를 엔진 px(1/96인치, 개체 상자 왼쪽 위 원점)로 놓고 pt로 바꾼다.
# ---------------------------------------------------------------------------------------------------------------

#: 계열 색 = 테마 강조색(idx % 6, FUN_1039bd60 표 0x10557cc0). 값은 미리보기 비트맵 8비트 팔레트로 확인한 것뿐이다.
_ACCENT_COLORS = ((0x61, 0x82, 0xD6), (0xFF, 0x84, 0x3A), (0xB2, 0xB2, 0xB2), (0xFF, 0xD7, 0x00), (0x28, 0x9B, 0x6E))
#: 자동 표식 모양 = 계열 번호 순서(OOXML ST_MarkerStyle 자동 차례). 정답 PDF로 본 셋(2·3·4)만 옮겼다.
_AUTO_MARKERS = {0: "diamond", 1: "square", 2: "triangle", 3: "x", 4: "star"}
#: 주식형 하강 막대 채움(정답 PDF 실측 #3a~#46 사이), 고저선·막대 테두리는 검정 1px.
_DOWN_BAR_FILL = (0x40, 0x40, 0x40)
#: 표식 기본 크기(pt). 주식형은 정답 PDF의 X 표식 폭 8.8px, 꺾은선은 OOXML 기본 5(표식 폭 7.7px 실측)다.
_MARKER_PT = 7.0
_LINE_MARKER_PT = 5.0
#: 계열 선 두께 2.25pt(a:ln w 기본 28575 EMU)를 px로.
_SERIES_LINE_PX = 3.0
#: 정답 PDF 실측(꺾은선 범례): 범례 폭 = 글 폭 + 19.4, 선 표식은 범례 왼쪽 + 2.1에서 15.4 길이, 글은 그 뒤 2.2,
#: 항목 간격 19.2, 가운데 보정 -2.7, 글 줄 위 보정 -1.1.
_LINE_LEGEND_EXTRA_PX = 19.4
_LINE_KEY_LEFT_PX, _LINE_KEY_PX, _LINE_KEY_TEXT_GAP_PX = 2.1, 15.4, 2.2
_LINE_LEGEND_PITCH_PX = 19.2
_LINE_LEGEND_CENTER_SHIFT_PX = -2.7
_LINE_LEGEND_TEXT_SHIFT_PX = -1.1
#: 축·격자선·차트 테두리 색(미리보기 비트맵 팔레트 #868686).
_LINE_COLOR = (0x86, 0x86, 0x86)
_WHITE = (0xFF, 0xFF, 0xFF)
_BLACK = (0, 0, 0)
#: 선 두께 1px.
_LINE_PX = 1.0
#: 라벨 크기 = 글 크기 + 2pt(CalculateLabels 0x102da650: (dpi+dpi)/72).
_LABEL_PAD_2D_PX = 2 * 96 / 72
#: 값 라벨과 축 사이 10pt(CalculateAxisMargin 0x102db1d0: dpi*10/72).
_AXIS_LABEL_GAP_PX = 10 * 96 / 72
#: 눈금 반복에서 라벨 높이에 곱하는 여유(0x1055dfdc = 0.085, FUN_103d1170).
_LABEL_SPACING = 0.085
#: 최댓값이 위 눈금에서 주단위/5 안이면 한 칸 늘린다(DAT_1055e228 = 5, FUN_103d1170).
_HEADROOM_DIVISOR = 5.0
#: 눈금 표시 길이(정답 PDF 실측 4.7~5px).
_TICK_PX = 5.0
#: 범례 표식 = 글 줄높이 * 0.5, 표식 여백 = 표식 * 0.8(FUN_10352e70).
_LEGEND_MARKER_RATIO, _LEGEND_MARGIN_RATIO = 0.5, 0.8
#: 글 높이 측정은 줄높이 + 5(FUN_103c2e50).
_MEASURE_PAD_PX = 5.0
#: 정답 PDF 실측(엔진 함수로 확인하지 못함): 항목 라벨 칸 아래 여백, 축에서 항목 글 줄 위까지, 값 축 x 보정,
#: 범례 항목 간격, 범례 가운데 보정, 표식과 글 사이, 범례 글 줄 위 보정.
_CATEGORY_BELOW_PX = 10.6
_CATEGORY_TEXT_TOP_PX = 14.64
_VALUE_AXIS_SHIFT_PX = 0.75
_LEGEND_PITCH_PX = 18.0
_LEGEND_CENTER_SHIFT_PX = -2.0
_LEGEND_TEXT_GAP_PX = 4.8
_LEGEND_TEXT_SHIFT_PX = -1.9
#: 정답 PDF 실측(가로 막대): 값 라벨 칸 아래 여백, 축에서 값 글 줄 위까지, 항목 축 x 보정, 항목 라벨과 축 사이.
_VALUE_BELOW_PX = 9.0
_VALUE_TEXT_TOP_PX = 13.1
_CATEGORY_AXIS_SHIFT_PX = 2.4
_CATEGORY_LABEL_GAP_PX = 14.4


@dataclass(frozen=True)
class OoxmlShape:
    """쪽 좌표 pt 도형. rect(왼쪽 위·오른쪽 아래, 채움·선), line(두 점, 선 두께 pt), polygon(꼭짓점, 채움)."""

    kind: str
    points: tuple[tuple[float, float], ...]
    fill: tuple[int, int, int] | None = None
    stroke: tuple[int, int, int] | None = None
    width: float = 0.0


@dataclass
class OoxmlChartDrawing:
    #: 개체 상자(쪽 좌표 pt): 왼쪽, 위, 너비, 높이.
    box: tuple[float, float, float, float]
    glyphs: list[ChartGlyph] = field(default_factory=list)
    shapes: list[OoxmlShape] = field(default_factory=list)


def ooxml_chart_svg(drawing: OoxmlChartDrawing) -> str:
    """개체 상자에 맞춘 SVG(단위 pt). 글자는 하나씩 놓은 text 요소다."""
    left, top, width, height = drawing.box

    def xy(point: tuple[float, float]) -> str:
        return f"{point[0] - left:.3f},{point[1] - top:.3f}"

    def rgb(color: tuple[int, int, int] | None) -> str:
        return "none" if color is None else "#{:02x}{:02x}{:02x}".format(*color)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.3f}pt" height="{height:.3f}pt" '
        f'viewBox="0 0 {width:.3f} {height:.3f}">'
    ]
    for shape in drawing.shapes:
        if shape.kind == "rect":
            (x0, y0), (x1, y1) = shape.points
            stroke = f' stroke="{rgb(shape.stroke)}" stroke-width="{shape.width:.3f}"' if shape.stroke else ""
            parts.append(
                f'<rect x="{x0 - left:.3f}" y="{y0 - top:.3f}" width="{x1 - x0:.3f}" height="{y1 - y0:.3f}" '
                f'fill="{rgb(shape.fill)}"{stroke}/>'
            )
        elif shape.kind == "line":
            (x0, y0), (x1, y1) = shape.points
            parts.append(
                f'<line x1="{x0 - left:.3f}" y1="{y0 - top:.3f}" x2="{x1 - left:.3f}" y2="{y1 - top:.3f}" '
                f'stroke="{rgb(shape.stroke)}" stroke-width="{shape.width:.3f}"/>'
            )
        else:
            points = " ".join(xy(point) for point in shape.points)
            parts.append(f'<polygon points="{points}" fill="{rgb(shape.fill)}"/>')
    for glyph in drawing.glyphs:
        char = glyph.char.replace("&", "&amp;").replace("<", "&lt;")
        parts.append(
            f'<text x="{glyph.x - left:.3f}" y="{glyph.y - top:.3f}" font-family="{glyph.font}" '
            f'font-size="{glyph.size:.3f}">{char}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


@dataclass(frozen=True)
class _Series:
    name: str
    index: int
    values: list[float]
    #: 표식 모양(triangle, x, star)과 크기 pt. 없으면 None.
    marker: str | None = None
    marker_pt: float = _MARKER_PT
    #: 분산형 x 값. 다른 차트는 None(항목 차례).
    xs: list[float] | None = None
    #: 주식형에서 계열 선을 그리는가(계열 모양 spPr가 없으면 2.25pt 선).
    line_visible: bool = False


@dataclass(frozen=True)
class _Bar2D:
    horizontal: bool
    categories: list[str]
    series: list[_Series]
    gap_width: float
    size_pt: float
    face: str
    #: 주식형이면 고저선과 올림·내림 막대 유무.
    stock: bool = False
    #: 누적 막대(grouping stacked, overlap 100).
    stacked: bool = False
    #: 꺾은선(lineChart standard): 계열마다 선과 표식.
    line: bool = False
    #: 직선과 표식이 있는 분산형(scatterChart lineMarker). line도 참이다.
    scatter: bool = False
    hi_low: bool = False
    up_down: bool = False


def _parse_bar2d(contents: bytes) -> _Bar2D | None:
    """옮긴 범위(제목 기본 글, 범례 r, 기본 축, 자동 눈금, 묶은 2차원 막대 하나, 기본 색)만 읽는다. 밖이면 None."""
    frame = _chart_frame(contents)
    if frame is None:
        return None
    plot, size_pt, face = frame
    kinds = [child for child in plot if child.tag.endswith("Chart")]
    if len(kinds) != 1 or kinds[0].tag != _C + "barChart":
        return None
    bar = kinds[0]
    if (
        _val(bar, "barDir") not in ("col", "bar")
        or (_val(bar, "grouping"), _val(bar, "overlap", "0")) not in (("clustered", "0"), ("stacked", "100"))
        or _val(bar, "varyColors", "0") != "0"
        or bar.find(_C + "dLbls") is not None
    ):
        return None
    horizontal = _val(bar, "barDir") == "bar"
    stacked = _val(bar, "grouping") == "stacked"
    if stacked and horizontal:
        return None  # 누적 가로 막대는 정답으로 확인하지 못했다
    if not _default_axes(plot, horizontal, gridlines=True):
        return None
    read = _read_series(bar, plain=True)
    if read is None:
        return None
    categories, series = read
    gap = float(_val(bar, "gapWidth", "150") or 150)
    return _Bar2D(horizontal, categories, series, gap, size_pt, face, stacked=stacked)


def _parse_stock(contents: bytes) -> _Bar2D | None:
    """주식형(고저선, 올림·내림 막대, 선 없는 계열과 자동 표식) 기본 모양만 읽는다. 밖이면 None."""
    frame = _chart_frame(contents)
    if frame is None:
        return None
    plot, size_pt, face = frame
    kinds = [child for child in plot if child.tag.endswith("Chart")]
    if len(kinds) != 1 or kinds[0].tag != _C + "stockChart" or not _default_axes(plot, False, gridlines=False):
        return None
    stock = kinds[0]
    hi_low, up_down = stock.find(_C + "hiLowLines"), stock.find(_C + "upDownBars")
    if stock.find(_C + "dropLines") is not None or stock.find(_C + "dLbls") is not None:
        return None
    if (hi_low is not None and len(hi_low)) or (up_down is not None and any(len(bar) for bar in up_down)):
        return None  # 고저선·막대 모양(spPr)을 바꾼 것은 옮기지 않았다
    read = _read_series(stock, plain=False)
    if read is None:
        return None
    categories, plain = read
    series = []
    for ser, item in zip(stock.findall(_C + "ser"), plain, strict=True):
        line = ser.find(f"{_C}spPr/{_A}ln")
        shape = ser.find(_C + "spPr")
        hidden = shape is not None and line is not None and line.find(_A + "noFill") is not None and len(shape) == 1
        if shape is not None and not hidden:
            return None  # 선 모양을 정한 계열은 옮기지 않았다
        marker = ser.find(_C + "marker")
        if marker is None or marker.find(_C + "spPr") is not None or _val(ser, "smooth", "0") != "0":
            return None
        symbol = _val(marker, "symbol")
        shape_name = _AUTO_MARKERS.get(item.index) if symbol is None else None if symbol == "none" else symbol
        if symbol is None and shape_name is None or shape_name not in (None, "triangle", "x", "star"):
            return None
        marker_pt = float(_val(marker, "size", str(_MARKER_PT)) or _MARKER_PT)
        series.append(_Series(item.name, item.index, item.values, shape_name, marker_pt, line_visible=shape is None))
    gap = float(_val(up_down, "gapWidth", "150") or 150) if up_down is not None else 150.0
    return _Bar2D(
        False,
        categories,
        series,
        gap,
        size_pt,
        face,
        stock=True,
        hi_low=hi_low is not None,
        up_down=up_down is not None,
    )


def _parse_line(contents: bytes) -> _Bar2D | None:
    """표식 있는 꺾은선(lineChart standard, 계열 모양 기본, 자동 표식) 기본 모양만 읽는다. 밖이면 None."""
    frame = _chart_frame(contents)
    if frame is None:
        return None
    plot, size_pt, face = frame
    kinds = [child for child in plot if child.tag.endswith("Chart")]
    if len(kinds) != 1 or kinds[0].tag != _C + "lineChart" or not _default_axes(plot, False, gridlines=True):
        return None
    group = kinds[0]
    if (
        _val(group, "grouping", "standard") != "standard"
        or _val(group, "varyColors", "0") != "0"
        or _val(group, "marker", "1") != "1"
        or any(group.find(_C + tag) is not None for tag in ("dLbls", "dropLines", "hiLowLines", "upDownBars"))
    ):
        return None
    read = _read_series(group, plain=True)
    if read is None:
        return None
    categories, plain = read
    series = []
    for ser, item in zip(group.findall(_C + "ser"), plain, strict=True):
        marker = ser.find(_C + "marker")
        if marker is None or len(marker) or _val(ser, "smooth", "0") != "0" or item.index not in _AUTO_MARKERS:
            return None  # 표식 모양·크기를 정한 것과 곡선은 옮기지 않았다
        series.append(_Series(item.name, item.index, item.values, _AUTO_MARKERS[item.index], _LINE_MARKER_PT))
    return _Bar2D(False, categories, series, 0.0, size_pt, face, line=True)


def _parse_scatter(contents: bytes) -> _Bar2D | None:
    """직선과 표식이 있는 분산형(scatterStyle lineMarker, 기본 값 축 둘, 자동 표식) 기본 모양만 읽는다. 밖이면 None."""
    frame = _chart_frame(contents)
    if frame is None:
        return None
    plot, size_pt, face = frame
    kinds = [child for child in plot if child.tag.endswith("Chart")]
    axes = plot.findall(_C + "valAx")
    if len(kinds) != 1 or kinds[0].tag != _C + "scatterChart" or plot.find(_C + "catAx") is not None:
        return None
    group = kinds[0]
    if _val(group, "scatterStyle") != "lineMarker" or _val(group, "varyColors", "0") != "0":
        return None
    if sorted(_val(axis, "axPos") or "" for axis in axes) != ["b", "l"]:
        return None
    for axis in axes:
        gridlines = _val(axis, "axPos") == "l"
        if not _plain_value_axis(axis) or (axis.find(_C + "majorGridlines") is not None) != gridlines:
            return None
    series = []
    for ser in group.findall(_C + "ser"):
        index, name = ser.find(_C + "idx"), ser.find(f"{_C}tx//{_C}v")
        if index is None or name is None or name.text is None:
            return None
        number = int(index.get("val", "-1"))
        extra = ("spPr", "marker", "dLbls", "trendline", "errBars", "dPt")
        if any(ser.find(_C + tag) is not None for tag in extra) or _val(ser, "smooth", "0") != "0":
            return None
        xs, ys = _number_cache(ser.find(_C + "xVal")), _number_cache(ser.find(_C + "yVal"))
        if xs is None or ys is None or len(xs) != len(ys) or not ys or number not in _AUTO_MARKERS:
            return None
        series.append(_Series(name.text, number, ys, _AUTO_MARKERS[number], _LINE_MARKER_PT, xs))
    if not series:
        return None
    return _Bar2D(False, [], series, 0.0, size_pt, face, line=True, scatter=True)


def _plain_value_axis(axis: ET.Element) -> bool:
    """자동 눈금, 바깥 눈금 표시, 일반 숫자 서식인 값 축인가."""
    scaling = axis.find(_C + "scaling")
    number_format = axis.find(_C + "numFmt")
    return not (
        any(axis.find(_C + tag) is not None for tag in ("txPr", "spPr", "title", "majorUnit", "minorGridlines"))
        or _val(axis, "delete", "0") != "0"
        or _val(axis, "tickLblPos", "nextTo") != "nextTo"
        or _val(axis, "majorTickMark", "out") != "out"
        or _val(axis, "minorTickMark", "none") != "none"
        or _val(axis, "crosses", "autoZero") != "autoZero"
        or _val(scaling, "orientation", "minMax") != "minMax"
        or (scaling is not None and any(scaling.find(_C + tag) is not None for tag in ("min", "max", "logBase")))
        or (number_format is not None and number_format.get("formatCode") != "General")
    )


def _number_cache(values: ET.Element | None) -> list[float] | None:
    """numRef 캐시나 numLit의 값 목록. 빈 칸이 있거나 숫자가 아니면 None."""
    if values is None:
        return None
    cache = values.find(f"{_C}numRef/{_C}numCache")
    if cache is None:
        cache = values.find(_C + "numLit")
    if cache is None:
        return None
    points = {int(point.get("idx", "-1")): point.findtext(_C + "v") for point in cache.findall(_C + "pt")}
    try:
        return [float(points[i] or "") for i in range(int(_val(cache, "ptCount", "0") or 0))]
    except (KeyError, ValueError):
        return None


def _chart_frame(contents: bytes) -> tuple[ET.Element, float, str] | None:
    """차트 공간·제목·범례·글꼴이 옮긴 기본 모양인지 보고 (그림 영역, 글 크기 pt, 글꼴)을 돌려준다. 밖이면 None."""
    try:
        root = ET.fromstring(contents)
    except ET.ParseError:
        return None
    chart = root.find(_C + "chart")
    if root.tag != _C + "chartSpace" or chart is None or root.find(_C + "spPr") is not None:
        return None
    title = chart.find(_C + "title")
    if (
        title is None
        or title.find(_C + "tx") is not None
        or title.find(_C + "spPr") is not None
        or _val(title, "overlay", "0") != "0"
        or _has_manual_layout(title)
        or title.find(f".//{_A}defRPr[@sz]") is not None
        or _val(chart, "autoTitleDeleted", "0") != "0"
        or chart.find(_C + "view3D") is not None
    ):
        return None
    style = root.find(".//{http://schemas.haansoft.com/office/8.0}hncChartStyle")
    if style is not None and (style.get("colorIndex", "0") != "0" or style.get("styleIndex", "0") != "0"):
        return None
    plot = chart.find(_C + "plotArea")
    if plot is None or _has_manual_layout(plot):
        return None
    legend = chart.find(_C + "legend")
    if (
        legend is None
        or _val(legend, "legendPos", "r") != "r"
        or _has_manual_layout(legend)
        or _val(legend, "overlay", "0") != "0"
        or legend.find(_C + "txPr") is not None
        or legend.find(_C + "spPr") is not None
        or legend.find(_C + "legendEntry") is not None
    ):
        return None
    text = root.find(_C + "txPr")
    body = text.find(_A + "bodyPr") if text is not None else None
    run = text.find(f".//{_A}defRPr") if text is not None else None
    latin = run.find(_A + "latin") if run is not None else None
    east_asian = run.find(_A + "ea") if run is not None else None
    if (
        body is None
        or run is None
        or body.get("vert", "horz") != "horz"
        or body.get("rot", "0") != "0"
        or latin is None
        or (east_asian is not None and east_asian.get("typeface") != latin.get("typeface"))
        or run.get("b", "0") != "0"
        or run.get("i", "0") != "0"
    ):
        return None
    return plot, int(run.get("sz", "1000")) / 100, latin.get("typeface", "")


def _default_axes(plot: ET.Element, horizontal: bool, gridlines: bool) -> bool:
    """항목 축 하나와 값 축 하나가 기본 모양(자동 눈금, 바깥 눈금 표시, 값 축 격자선 gridlines)인가."""
    cat_axes, val_axes = plot.findall(_C + "catAx"), plot.findall(_C + "valAx")
    others = [child for child in plot if child.tag in (_C + "serAx", _C + "dateAx")]
    if len(cat_axes) != 1 or len(val_axes) != 1 or others:
        return False
    for axis, position in ((cat_axes[0], "l" if horizontal else "b"), (val_axes[0], "b" if horizontal else "l")):
        scaling = axis.find(_C + "scaling")
        if (
            axis.find(_C + "txPr") is not None
            or axis.find(_C + "spPr") is not None
            or axis.find(_C + "title") is not None
            or _val(axis, "delete", "0") != "0"
            or _val(axis, "axPos") != position
            or _val(axis, "tickLblPos", "nextTo") != "nextTo"
            or _val(axis, "majorTickMark", "out") != "out"
            or _val(axis, "minorTickMark", "none") != "none"
            or _val(axis, "crosses", "autoZero") != "autoZero"
            or _val(scaling, "orientation", "minMax") != "minMax"
            or (scaling is not None and any(scaling.find(_C + tag) is not None for tag in ("min", "max", "logBase")))
            or axis.find(_C + "majorUnit") is not None
            or axis.find(_C + "minorGridlines") is not None
        ):
            return False
    if cat_axes[0].find(_C + "majorGridlines") is not None:
        return False
    if (val_axes[0].find(_C + "majorGridlines") is not None) != gridlines:
        return False
    if _val(val_axes[0], "crossBetween", "between") != "between":
        return False
    number_format = val_axes[0].find(_C + "numFmt")
    return number_format is None or number_format.get("formatCode") == "General"


def _read_series(group: ET.Element, plain: bool) -> tuple[list[str], list[_Series]] | None:
    """계열 이름·번호·값과 공통 항목 이름. plain이면 계열 모양(spPr)이 없는 것만 받는다. 밖이면 None."""
    series = []
    categories: list[str] | None = None
    for ser in group.findall(_C + "ser"):
        extra = ("dLbls", "trendline", "errBars", "dPt") + (("spPr",) if plain else ())
        if any(ser.find(_C + tag) is not None for tag in extra):
            return None
        name = ser.find(f"{_C}tx//{_C}v")
        # 값·항목은 참조 캐시(numRef/strRef)나 글자 그대로(numLit/strLit)로 저장된다
        cache = ser.find(f"{_C}val/{_C}numRef/{_C}numCache")
        if cache is None:
            cache = ser.find(f"{_C}val/{_C}numLit")
        index = ser.find(_C + "idx")
        if name is None or name.text is None or cache is None or index is None:
            return None
        count = int(_val(cache, "ptCount", "0") or 0)
        points = {int(point.get("idx", "-1")): point.findtext(_C + "v") for point in cache.findall(_C + "pt")}
        try:
            values = [float(points[i] or "") for i in range(count)]
        except (KeyError, ValueError):
            return None  # 빈 칸(dispBlanksAs)은 옮기지 않았다
        labels = [
            point.findtext(_C + "v") or ""
            for point in sorted(
                ser.findall(f"{_C}cat/{_C}strRef/{_C}strCache/{_C}pt") + ser.findall(f"{_C}cat/{_C}strLit/{_C}pt"),
                key=lambda p: int(p.get("idx", "0")),
            )
        ]
        if categories is None:
            categories = labels
        if labels != categories or len(values) != len(categories) or int(index.get("val", "-1")) not in range(5):
            return None
        series.append(_Series(name.text, int(index.get("val", "0")), values))
    if not series or not categories:
        return None
    return categories, series


def value_axis_scale(
    low: float, high: float, axis_len: float, label_size: float, horizontal: bool = False
) -> tuple[float, float, float, int]:
    """2차원 값 축 자동 눈금(ValueAxisShapeRender FUN_103d1170 -> AxisLayoutUtil::AutoScale 0x102da320).

    (최소, 최대, 주단위, 칸 수). 칸이 라벨(세로 축은 높이*1.085, 가로 축은 폭)보다 작으면 칸 수를 줄여 다시 맞춘다.
    """
    lo, hi, major = low, high, 0.0
    count = requested = 10
    while True:
        lo, hi, major, count = _auto_scale(lo, hi, major, requested)
        # 문턱은 float로 줄여 비교한다((double)(float)dVar26). 4.3 -> 4.5처럼 차이가 문턱과 같으면 늘린다.
        threshold = _as_float32(major / _HEADROOM_DIVISOR * (2 if count > 8 else 1))
        if count != 1 and hi >= major and hi - high <= threshold:
            hi += major
            count += 1
        threshold = _as_float32(major / _HEADROOM_DIVISOR * (2 if count > 9 else 1))
        if lo < 0 and abs(lo) - abs(low) <= threshold:
            lo -= major
            count += 1
        if count < 2:
            break
        if horizontal:
            if label_size <= axis_len / count and count < 11:
                break
        elif label_size * _LABEL_SPACING <= axis_len / count - label_size:
            if count < 11:
                break
        elif count <= 3:
            break
        requested = count - 1
        if requested <= 0:
            break
    return lo, hi, major, count


def _as_float32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def _auto_scale(lo: float, hi: float, major: float, count: int) -> tuple[float, float, float, int]:
    """AxisLayoutUtil::AutoScale(0x102da320)의 최소·최대·주단위 자동 경우."""
    if abs(hi) + abs(lo) < major:
        hi = major
    if not (lo < hi and count > 0):
        return lo, hi, major, count
    divisions = float(count)
    new_lo, new_hi = lo, hi
    while divisions > 0:
        step = (hi - lo) / divisions
        if step < 1e-31:
            step = 0.0
        exponent = int(math.log10(step)) if step > 0 else 0
        if step < 1.0:
            exponent -= 1
        power = 100.0 if exponent == 2 else 10.0**exponent
        i = 0
        # 표 0x1055e8e0(√2, √10, √50)과 0x1055e8d0(1, 2, 5, 10)
        while i < 3 and not (step / power < (1.414214, 3.162278, 7.071068)[i]):
            i += 1
        major = (1.0, 2.0, 5.0, 10.0)[i] * power
        q = lo / major
        k = int(q) - (1 if q < 0 else 0)
        if abs((k + 1.0) - q) < 2e-5:
            k += 1
        new_lo = min(k * major, lo)
        q = hi / major
        k = int(q + 1.0) - (1 if q < -1.0 else 0)
        if abs((q + 1.0) - k) < 2e-5:
            k -= 1
        new_hi = max(k * major, hi)
        if (new_hi - new_lo) / major <= count:
            break
        divisions -= 1.0
    if divisions <= 1.0:
        return new_lo, new_hi, new_hi - new_lo, 1
    return new_lo, new_hi, major, int((new_hi - new_lo) / major + 2e-5)


def draw_ooxml_bar_chart(
    contents: bytes, left: float, top: float, width: float, height: float
) -> OoxmlChartDrawing | None:
    """개체 상자(쪽 좌표 pt)에 새 차트 2차원 막대를 엔진처럼 그린다. 옮긴 범위 밖이면 None(미리보기 그림으로 대신)."""
    chart = _parse_bar2d(contents) or _parse_stock(contents) or _parse_line(contents) or _parse_scatter(contents)
    if chart is None or chart.size_pt not in _TITLE_PT:
        return None
    try:
        return _draw_bar2d(chart, left, top, width, height)
    except UnsupportedChart:
        return None  # 폭·세로 지표 표에 없는 글꼴·글자는 옮긴 범위 밖으로 둔다


def _draw_bar2d(chart: _Bar2D, left: float, top: float, width: float, height: float) -> OoxmlChartDrawing | None:
    em_px = lambda pt: pt * 96 / 72  # noqa: E731
    line_px = lambda pt: _LINE_HEIGHT_RATIO * em_px(pt)  # noqa: E731
    size = chart.size_pt
    face = FontFace(chart.face, size)
    ascent, descent = face.vertical[_HHEA_ASCENT_FIELD], face.vertical[_HHEA_DESCENT_FIELD]
    base_ratio = ascent / (ascent + descent)

    def text_px(text: str, pt: float = size) -> float:
        return sum(face.design_advance(char) / face.metric.em * em_px(pt) for char in text)

    drawing = OoxmlChartDrawing((left, top, width, height))
    to_pt = 72 / 96

    def rect(x0: float, y0: float, x1: float, y1: float, color: tuple[int, int, int]) -> None:
        corners = ((left + x0 * to_pt, top + y0 * to_pt), (left + x1 * to_pt, top + y1 * to_pt))
        drawing.shapes.append(OoxmlShape("rect", corners, fill=color))

    def point(x: float, y: float) -> tuple[float, float]:
        return left + x * to_pt, top + y * to_pt

    def marker_shape(name: str, x: float, y: float, size_pt: float, color: tuple[int, int, int]) -> None:
        """표식(가운데 x, y px). 마름모·네모·세모는 채우고 X·별은 1px 선으로 긋는다."""
        r = size_pt * 96 / 72 / 2
        filled = {
            "triangle": ((x, y - r), (x + r, y + r), (x - r, y + r)),
            # 꺾은선 표식은 같은 색 0.75pt 테두리까지 채운다(정답 PDF 폭 7.7px = 5pt + 1px)
            "diamond": ((x, y - r - 0.5), (x + r + 0.5, y), (x, y + r + 0.5), (x - r - 0.5, y)),
            "square": (
                (x - r - 0.5, y - r - 0.5),
                (x + r + 0.5, y - r - 0.5),
                (x + r + 0.5, y + r + 0.5),
                (x - r - 0.5, y + r + 0.5),
            ),
        }
        if name in filled:
            drawing.shapes.append(OoxmlShape("polygon", tuple(point(*c) for c in filled[name]), fill=color))
            return
        strokes = [((x - r, y - r), (x + r, y + r)), ((x - r, y + r), (x + r, y - r))]
        if name == "star":
            strokes.append(((x, y - r), (x, y + r)))
        for a, b in strokes:
            drawing.shapes.append(OoxmlShape("line", (point(*a), point(*b)), stroke=color, width=_LINE_PX * to_pt))

    def hline(x0: float, x1: float, y: float) -> None:
        rect(x0, y - _LINE_PX / 2, x1, y + _LINE_PX / 2, _LINE_COLOR)

    def vline(x: float, y0: float, y1: float) -> None:
        rect(x - _LINE_PX / 2, y0, x + _LINE_PX / 2, y1, _LINE_COLOR)

    def text(value: str, x: float, line_top: float, pt: float = size) -> None:
        """한 줄 글(왼쪽 x, 줄 위 line_top, px). 기준선 = 줄 위 + 줄높이*ascent/(ascent+descent)."""
        baseline = line_top + base_ratio * line_px(pt)
        pen = x
        for char in value:
            if char.strip():
                drawing.glyphs.append(ChartGlyph(char, left + pen * to_pt, top + baseline * to_pt, pt, chart.face))
            pen += text_px(char, pt)

    box_w, box_h = width * 96 / 72, height * 96 / 72
    # 차트 영역: 흰 바탕과 1px 테두리
    rect(0, 0, box_w, box_h, _WHITE)
    hline(0, box_w, _LINE_PX / 2)
    hline(0, box_w, box_h - _LINE_PX / 2)
    vline(_LINE_PX / 2, 0, box_h)
    vline(box_w - _LINE_PX / 2, 0, box_h)

    # 제목 = 기본 글 「차트 제목」, 높이 = 줄높이 + 5 + 5(FUN_103c5ae0), 가운데
    title_pt = _TITLE_PT[size]
    title_h = line_px(title_pt) + 5 + 5
    # 계열이 하나면 기본 제목은 계열 이름이다(정답 PDF: 가로막대형_하나만있을떄_단일시리즈제목)
    title = chart.series[0].name if len(chart.series) == 1 else "차트 제목"
    text(title, (box_w - text_px(title, title_pt)) / 2, _PLOT_MARGIN_PX + 5, title_pt)

    # 범례(오른쪽): 폭 = 최대 글자폭 + 표식 + 2*여백(FUN_10353770)
    marker = line_px(size) * _LEGEND_MARKER_RATIO
    gap = marker * _LEGEND_MARGIN_RATIO
    # 선을 그리는 계열이 하나라도 있으면 범례 전체가 꺾은선 범례 모양이다(정답 PDF: 주식형 중간계열추가)
    line_legend = chart.line or any(s.line_visible for s in chart.series)
    legend_extra = _LINE_LEGEND_EXTRA_PX if line_legend else marker + 2 * gap
    legend_w = min(max(text_px(s.name) for s in chart.series) + legend_extra, box_w / 3)
    legend_x = box_w - _PLOT_MARGIN_PX - legend_w

    # 그림 영역(FUN_10379410)
    plot_x0, plot_y0 = _PLOT_MARGIN_PX, title_h + 2 * _PLOT_MARGIN_PX
    plot_x1 = box_w - legend_w - 2 * _PLOT_MARGIN_PX
    plot_y1 = box_h - _PLOT_MARGIN_PX
    if plot_x1 <= plot_x0 or plot_y1 <= plot_y0:
        return None
    label_h = line_px(size) + _MEASURE_PAD_PX + _LABEL_PAD_2D_PX
    values = [v for s in chart.series for v in s.values]
    if chart.stacked:
        values = [sum(column) for column in zip(*(s.values for s in chart.series), strict=True)]
    low, high = min(values + [0.0]), max(values + [0.0])
    place = (
        _place_scatter
        if chart.scatter
        else _place_stock
        if chart.stock
        else _place_line
        if chart.line
        else _place_horizontal_bars
        if chart.horizontal
        else _place_vertical_bars
    )
    placed = place(chart, (plot_x0, plot_y0, plot_x1, plot_y1), label_h, low, high, text_px)
    if placed is None:
        return None
    for kind, *args in placed:
        if kind == "h":
            hline(*args)
        elif kind == "v":
            vline(*args)
        elif kind == "text":
            label, x, line_center = args
            text(label, x, line_center - line_px(size) / 2)
        elif kind == "marker":
            marker_shape(*args)
        elif kind == "series":
            x0, y0, x1, y1, color = args
            ends = (point(x0, y0), point(x1, y1))
            drawing.shapes.append(OoxmlShape("line", ends, stroke=color, width=_SERIES_LINE_PX * to_pt))
        elif kind == "line":
            x0, y0, x1, y1, color = args
            drawing.shapes.append(
                OoxmlShape("line", (point(x0, y0), point(x1, y1)), stroke=color, width=_LINE_PX * to_pt)
            )
        elif kind == "box":
            x0, y0, x1, y1, color = args
            drawing.shapes.append(
                OoxmlShape("rect", (point(x0, y0), point(x1, y1)), fill=color, stroke=_BLACK, width=_LINE_PX * to_pt)
            )
        else:
            rect(*args)

    # 범례 항목: 표식과 이름, 그림 영역 높이 가운데
    pitch = _LINE_LEGEND_PITCH_PX if line_legend else _LEGEND_PITCH_PX
    shift = _LINE_LEGEND_CENTER_SHIFT_PX if line_legend else _LEGEND_CENTER_SHIFT_PX
    first = (plot_y0 + plot_y1) / 2 - pitch * len(chart.series) / 2 + shift
    # 가로 막대와 누적 막대는 범례를 계열 역순으로 적는다(정답 PDF)
    for k, series in enumerate(reversed(chart.series) if chart.horizontal or chart.stacked else chart.series):
        center = first + (k + 0.5) * pitch
        color = _ACCENT_COLORS[series.index % len(_ACCENT_COLORS)]
        if line_legend:
            key_x = legend_x + _LINE_KEY_LEFT_PX
            if chart.line or series.line_visible:
                ends = (point(key_x, center), point(key_x + _LINE_KEY_PX, center))
                drawing.shapes.append(OoxmlShape("line", ends, stroke=color, width=_SERIES_LINE_PX * to_pt))
            if series.marker is not None:
                marker_shape(series.marker, key_x + _LINE_KEY_PX / 2, center, series.marker_pt, color)
            text_x = key_x + _LINE_KEY_PX + _LINE_KEY_TEXT_GAP_PX
            text(series.name, text_x, center - line_px(size) / 2 + _LINE_LEGEND_TEXT_SHIFT_PX)
            continue
        if not chart.stock:
            rect(legend_x + gap, center - marker / 2, legend_x + gap + marker, center + marker / 2, color)
        elif series.marker is not None:
            marker_shape(series.marker, legend_x + gap + marker / 2, center, series.marker_pt, color)
        text(
            series.name,
            legend_x + gap + marker + _LEGEND_TEXT_GAP_PX,
            center - line_px(size) / 2 + _LEGEND_TEXT_SHIFT_PX,
        )
    return drawing


def _place_vertical_bars(chart: _Bar2D, plot, label_h: float, low: float, high: float, text_px) -> list[tuple] | None:
    """세로 막대: 값 축 왼쪽, 항목 축 아래. ("h"|"v", 선) / ("text", 글, x, 줄 가운데 y) / ("rect", 사각형, 색), px."""
    axes = _vertical_axes(chart, plot, label_h, low, high, text_px, gridlines=True)
    if axes is None:
        return None
    placed, axis_x, slot, y_of = axes
    # 막대: 폭 = 항목 칸 / (나란한 막대 수 + 간격 비율), 양쪽 간격 절반씩(gapWidth). 누적은 한 막대에 쌓는다.
    columns = 1 if chart.stacked else len(chart.series)
    bar_w = slot / (columns + chart.gap_width / 100)
    base = [0.0] * len(chart.categories)
    for k, series in enumerate(chart.series):
        color = _ACCENT_COLORS[series.index % len(_ACCENT_COLORS)]
        for i, value in enumerate(series.values):
            x0 = axis_x + i * slot + chart.gap_width / 100 * bar_w / 2 + (0 if chart.stacked else k) * bar_w
            start = base[i] if chart.stacked else None
            y0, y1 = y_of(start), y_of(value + (start or 0.0))
            if chart.stacked:
                base[i] += value
            placed.append(("rect", x0, min(y0, y1), x0 + bar_w, max(y0, y1), color))
    return placed


def _vertical_axes(chart: _Bar2D, plot, label_h: float, low: float, high: float, text_px, gridlines: bool):
    """값 축 왼쪽·항목 축 아래인 축과 라벨. (놓을 것, 값 축 x, 항목 칸 폭, 값 -> y(None이면 0 기준선))."""
    plot_x0, plot_y0, plot_x1, plot_y1 = plot
    placed: list[tuple] = []
    axis_top = plot_y0 + label_h / 2
    axis_y = plot_y1 - (label_h + _CATEGORY_BELOW_PX)
    if axis_y <= axis_top:
        return None
    lo, hi, major, count = value_axis_scale(low, high, axis_y - axis_top, label_h)
    labels = ["%.10g" % (lo + i * major) for i in range(count + 1)]
    value_w = max(text_px(label) for label in labels)
    axis_x = plot_x0 + value_w + _LABEL_PAD_2D_PX + _AXIS_LABEL_GAP_PX + _VALUE_AXIS_SHIFT_PX
    if plot_x1 <= axis_x or hi <= lo:
        return None

    def y_of(value: float | None) -> float:
        value = min(max(0.0, lo), hi) if value is None else value
        return axis_y - (value - lo) / (hi - lo) * (axis_y - axis_top)

    # 격자선(또는 눈금 표시만)·값 라벨·값 축
    for i, label in enumerate(labels):
        y = y_of(lo + i * major)
        placed.append(("h", axis_x - _TICK_PX, plot_x1 if gridlines else axis_x, y))
        placed.append(("text", label, axis_x - _AXIS_LABEL_GAP_PX - text_px(label), y))
    placed.append(("v", axis_x, axis_top, axis_y + _LINE_PX / 2))
    # 항목 축: 눈금은 항목 경계, 라벨은 항목 가운데
    n = len(chart.categories)
    slot = (plot_x1 - axis_x) / n
    if not gridlines:
        placed.append(("h", axis_x, plot_x1, axis_y))
    for i in range(n + 1):
        placed.append(("v", axis_x + i * slot, axis_y, axis_y + _TICK_PX))
    line_center = axis_y + _CATEGORY_TEXT_TOP_PX + _LINE_HEIGHT_RATIO * chart.size_pt * 96 / 72 / 2
    for i, name in enumerate(chart.categories):
        placed.append(("text", name, axis_x + (i + 0.5) * slot - text_px(name) / 2, line_center))
    return placed, axis_x, slot, y_of


def _place_horizontal_bars(chart: _Bar2D, plot, label_h: float, low: float, high: float, text_px) -> list[tuple] | None:
    """가로 막대: 항목 축 왼쪽(아래에서 위로), 값 축 아래, 격자선 세로. 반환 형식은 _place_vertical_bars와 같다."""
    plot_x0, plot_y0, plot_x1, plot_y1 = plot
    placed: list[tuple] = []
    category_w = max(text_px(name) for name in chart.categories)
    axis_x = plot_x0 + category_w + _LABEL_PAD_2D_PX + _AXIS_LABEL_GAP_PX + _CATEGORY_AXIS_SHIFT_PX
    axis_y = plot_y1 - (label_h + _VALUE_BELOW_PX)
    axis_top = plot_y0

    def label_w(value: float) -> float:
        return text_px(f"{value:.10g}") + _MEASURE_PAD_PX + _LABEL_PAD_2D_PX

    # 오른쪽 끝은 마지막 값 라벨 폭의 절반만큼 비운다(정답 PDF 실측). 눈금이 바뀌면 다시 맞춘다.
    right, widest = plot_x1, label_w(high)
    lo = hi = major = 0.0
    count = 0
    for _ in range(4):
        lo, hi, major, count = value_axis_scale(low, high, right - axis_x, widest, True)
        widest = max(label_w(lo + i * major) for i in range(count + 1))
        if right == plot_x1 - label_w(hi) / 2:
            break
        right = plot_x1 - label_w(hi) / 2
    if right <= axis_x or axis_y <= axis_top or hi <= lo:
        return None

    def x_of(value: float) -> float:
        return axis_x + (value - lo) / (hi - lo) * (right - axis_x)

    line_h = _LINE_HEIGHT_RATIO * chart.size_pt * 96 / 72
    for i in range(count + 1):
        value = lo + i * major
        x = x_of(value)
        placed.append(("v", x, axis_top, axis_y + _TICK_PX))
        label = f"{value:.10g}"
        placed.append(("text", label, x - text_px(label) / 2, axis_y + _VALUE_TEXT_TOP_PX + line_h / 2))
    placed.append(("h", axis_x, right + _LINE_PX / 2, axis_y))
    n = len(chart.categories)
    slot = (axis_y - axis_top) / n
    for i in range(n + 1):
        placed.append(("h", axis_x - _TICK_PX, axis_x, axis_y - i * slot))
    for i, name in enumerate(chart.categories):
        placed.append(("text", name, axis_x - _CATEGORY_LABEL_GAP_PX - text_px(name), axis_y - (i + 0.5) * slot))
    bar_h = slot / (len(chart.series) + chart.gap_width / 100)
    zero = x_of(min(max(0.0, lo), hi))
    for k, series in enumerate(chart.series):
        color = _ACCENT_COLORS[series.index % len(_ACCENT_COLORS)]
        for i, value in enumerate(series.values):
            y1 = axis_y - i * slot - chart.gap_width / 100 * bar_h / 2 - k * bar_h
            x = x_of(value)
            placed.append(("rect", min(x, zero), y1 - bar_h, max(x, zero), y1, color))
    return placed


def _place_stock(chart: _Bar2D, plot, label_h: float, low: float, high: float, text_px) -> list[tuple] | None:
    """주식형: 고저선(모든 계열의 최솟값~최댓값), 표식, 그 위에 첫 계열~끝 계열 올림·내림 막대(정답 PDF 그리기 차례)."""
    axes = _vertical_axes(chart, plot, label_h, low, high, text_px, gridlines=False)
    if axes is None:
        return None
    placed, axis_x, slot, y_of = axes
    columns = list(zip(*(series.values for series in chart.series), strict=True))
    centers = [axis_x + (i + 0.5) * slot for i in range(len(columns))]
    if chart.hi_low:
        for x, column in zip(centers, columns, strict=True):
            placed.append(("line", x, y_of(max(column)), x, y_of(min(column)), _BLACK))
    for series in chart.series:
        color = _ACCENT_COLORS[series.index % len(_ACCENT_COLORS)]
        if series.line_visible:
            for (x0, v0), (x1, v1) in itertools.pairwise(zip(centers, series.values, strict=True)):
                placed.append(("series", x0, y_of(v0), x1, y_of(v1), color))
        if series.marker is not None:
            for x, value in zip(centers, series.values, strict=True):
                placed.append(("marker", series.marker, x, y_of(value), series.marker_pt, color))
    if chart.up_down:
        bar_w = slot / (1 + chart.gap_width / 100)
        for x, column in zip(centers, columns, strict=True):
            opening, closing = column[0], column[-1]
            fill = _WHITE if closing >= opening else _DOWN_BAR_FILL
            top, bottom = y_of(max(opening, closing)), y_of(min(opening, closing))
            placed.append(("box", x - bar_w / 2, top, x + bar_w / 2, bottom, fill))
    return placed


def _place_line(chart: _Bar2D, plot, label_h: float, low: float, high: float, text_px) -> list[tuple] | None:
    """꺾은선: 항목 가운데를 잇는 2.25pt 선과 표식을 계열 차례로 그린다(뒤 계열이 위)."""
    axes = _vertical_axes(chart, plot, label_h, low, high, text_px, gridlines=True)
    if axes is None:
        return None
    placed, axis_x, slot, y_of = axes
    for series in chart.series:
        color = _ACCENT_COLORS[series.index % len(_ACCENT_COLORS)]
        points = [(axis_x + (i + 0.5) * slot, y_of(value)) for i, value in enumerate(series.values)]
        for (x0, y0), (x1, y1) in itertools.pairwise(points):
            placed.append(("series", x0, y0, x1, y1, color))
        for x, y in points:
            placed.append(("marker", series.marker, x, y, series.marker_pt, color))
    return placed


def _place_scatter(chart: _Bar2D, plot, label_h: float, low: float, high: float, text_px) -> list[tuple] | None:
    """분산형: y 값 축 왼쪽(격자선), x 값 축 아래(눈금 표시만), 점을 잇는 선과 표식."""
    plot_x0, plot_y0, plot_x1, plot_y1 = plot
    placed: list[tuple] = []
    axis_top = plot_y0 + label_h / 2
    axis_y = plot_y1 - (label_h + _VALUE_BELOW_PX)
    if axis_y <= axis_top:
        return None
    y_lo, y_hi, y_major, y_count = value_axis_scale(low, high, axis_y - axis_top, label_h)
    y_labels = [f"{y_lo + i * y_major:.10g}" for i in range(y_count + 1)]
    axis_x = plot_x0 + max(text_px(label) for label in y_labels) + _LABEL_PAD_2D_PX + _AXIS_LABEL_GAP_PX
    axis_x += _VALUE_AXIS_SHIFT_PX
    xs = [x for series in chart.series for x in series.xs or []]
    x_low, x_high = min(xs + [0.0]), max(xs + [0.0])

    def label_w(value: float) -> float:
        return text_px(f"{value:.10g}") + _MEASURE_PAD_PX + _LABEL_PAD_2D_PX

    # 오른쪽 끝은 마지막 x 라벨 폭의 절반만큼 비운다(가로 막대와 같은 실측). 눈금이 바뀌면 다시 맞춘다.
    right, widest = plot_x1, label_w(x_high)
    x_lo = x_hi = x_major = 0.0
    x_count = 0
    for _ in range(4):
        x_lo, x_hi, x_major, x_count = value_axis_scale(x_low, x_high, right - axis_x, widest, True)
        widest = max(label_w(x_lo + i * x_major) for i in range(x_count + 1))
        if right == plot_x1 - label_w(x_hi) / 2:
            break
        right = plot_x1 - label_w(x_hi) / 2
    if right <= axis_x or y_hi <= y_lo or x_hi <= x_lo:
        return None

    def x_of(value: float) -> float:
        return axis_x + (value - x_lo) / (x_hi - x_lo) * (right - axis_x)

    def y_of(value: float) -> float:
        return axis_y - (value - y_lo) / (y_hi - y_lo) * (axis_y - axis_top)

    for i, label in enumerate(y_labels):
        y = y_of(y_lo + i * y_major)
        placed.append(("h", axis_x - _TICK_PX, right, y))
        placed.append(("text", label, axis_x - _AXIS_LABEL_GAP_PX - text_px(label), y))
    placed.append(("v", axis_x, axis_top, axis_y + _LINE_PX / 2))
    line_h = _LINE_HEIGHT_RATIO * chart.size_pt * 96 / 72
    for i in range(x_count + 1):
        value = x_lo + i * x_major
        x = x_of(value)
        label = f"{value:.10g}"
        placed.append(("v", x, axis_y, axis_y + _TICK_PX))
        placed.append(("text", label, x - text_px(label) / 2, axis_y + _VALUE_TEXT_TOP_PX + line_h / 2))
    for series in chart.series:
        color = _ACCENT_COLORS[series.index % len(_ACCENT_COLORS)]
        points = [(x_of(x), y_of(y)) for x, y in zip(series.xs or [], series.values, strict=True)]
        for (x0, y0), (x1, y1) in itertools.pairwise(points):
            placed.append(("series", x0, y0, x1, y1, color))
        for x, y in points:
            placed.append(("marker", series.marker, x, y, series.marker_pt, color))
    return placed
