"""테두리 선 종류·굵기를 SVG 선으로, 그러데이션 채우기를 쪽 좌표 그러데이션으로 바꾼다."""

from __future__ import annotations

import math

from ..models.docinfo import BorderLine, GradientFill
from .scene import Gradient, Line
from .styles import colorref

#: 굵기 코드(명세 표26) → mm.
WIDTH_MM = (0.1, 0.12, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.7, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0)
HU_PER_MM = 7200 / 25.4
#: 선 종류 → stroke-dasharray(굵기 배수). 출처: rhwp svg.rs 점선 패턴. 물결·3D는 실선으로 그린다.
DASH = {2: (6, 3), 3: (2, 2), 4: (6, 3, 2, 3), 5: (6, 3, 2, 3, 2, 3), 6: (6, 3), 7: (2, 2)}
NONE = 0


def width_hu(code: int) -> float:
    return WIDTH_MM[code] * HU_PER_MM if 0 <= code < len(WIDTH_MM) else 0.1 * HU_PER_MM


def border_lines(border: BorderLine | None, x1: float, y1: float, x2: float, y2: float) -> list[Line]:
    if border is None or border.line_type == NONE:
        return []
    color = colorref(border.color) or "#000000"
    width = width_hu(border.width)
    if border.line_type in (8, 9, 10, 11):
        # 이중·삼중선: 가는 선 두 개를 굵기만큼 떨어뜨려 근사한다(rhwp border_rendering.rs 근사와 같은 방식).
        thin = max(width * 0.3, 0.1 * HU_PER_MM)
        gap = max(width * 0.4, 0.15 * HU_PER_MM)
        horizontal = y1 == y2
        dx, dy = (0, (gap + thin) / 2) if horizontal else ((gap + thin) / 2, 0)
        return [
            Line(x1 - dx, y1 - dy, x2 - dx, y2 - dy, thin, color),
            Line(x1 + dx, y1 + dy, x2 + dx, y2 + dy, thin, color),
        ]
    dash = DASH.get(border.line_type)
    dash_text = " ".join(f"{part * width / 100:.2f}" for part in dash) if dash else None
    return [Line(x1, y1, x2, y2, width, color, dash_text)]


def gradient_paint(fill: GradientFill, x: float, y: float, width: float, height: float) -> Gradient | None:
    """BorderFill 그러데이션을 상자(x, y, 폭, 높이)에 맞춘 쪽 좌표 그러데이션으로. 색이 둘 미만이면 None."""
    colors = [colorref(color) or "#ffffff" for color in fill.colors]
    if len(colors) < 2:
        return None
    if len(fill.positions) == len(colors):
        offsets = [min(max(position / 100, 0.0), 1.0) for position in fill.positions]
    else:
        offsets = [index / (len(colors) - 1) for index in range(len(colors))]
    stops = list(zip(offsets, colors, strict=True))
    if fill.kind == 1:
        # 줄무늬형: 축 방향 (sin a, −cos a), 첫 색이 축 시작(rhwp mod.rs linear_gradient_axis, 한/글 2024 PDF 세 각도).
        angle = math.radians(fill.angle)
        dx, dy = math.sin(angle), -math.cos(angle)
        cx, cy = x + width / 2, y + height / 2
        half = (abs(dx) * width + abs(dy) * height) / 2
        return Gradient(False, cx - dx * half, cy - dy * half, cx + dx * half, cy + dy * half, stops)
    # 원형: 중심은 상자 안 (center_x, center_y)%, 첫 색이 중심이고 가장 먼 꼭짓점에서 끝 색이 된다(kr_156489593 1쪽 PDF:
    # 왼쪽 위 중심 띠 448pt에서 400pt 지점 비율 0.87, 오른쪽 끝은 끝 색).
    # 검증 안 됨: 원뿔형(3)·사각형(4)도 원형으로 그리고 번짐 값은 쓰지 않는다.
    cx, cy = x + width * fill.center_x / 100, y + height * fill.center_y / 100
    radius = max(math.hypot(px - cx, py - cy) for px in (x, x + width) for py in (y, y + height))
    return Gradient(True, cx, cy, radius, radius, stops)
