"""테두리 선 종류·굵기를 SVG 선으로 바꾼다."""

from __future__ import annotations

from ..models.docinfo import BorderLine
from .scene import Line
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
