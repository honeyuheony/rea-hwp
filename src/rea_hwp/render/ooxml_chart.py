"""한글 2022가 새 차트(OOXMLChartContents)를 HncDrawingEngine.dll로 다시 그릴 때 PDF 글자로 찍는 기울어진 범주 이름.
도형은 비트맵으로 찍혀 옮기지 않았고 3차원 묶은 세로 막대형만 옮겼다. 주소는 HncDrawingEngine.dll 기준이다."""

from __future__ import annotations

import io
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass

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
