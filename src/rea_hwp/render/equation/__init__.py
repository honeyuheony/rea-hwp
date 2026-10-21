"""한글 2022 수식 엔진(Equation Version 60)의 상자 계산. measure()가 (폭, 높이, 기준선)을 돌려준다."""

from __future__ import annotations

from .engine import Context, Converter, DrawnBox, DrawnGlyph, Line, cdiv
from .glyphs import EQUATION_FONT
from .legacy import LegacyEngineState, LegacyEquationError, clean_script, measure_legacy
from .parse import parse_script

__all__ = ["DrawnBox", "DrawnGlyph", "LegacyEngineState", "LegacyEquationError", "draw", "measure", "measure_legacy"]


def _laid_out(script: str, font_size: int, font: str) -> Line:
    parser = parse_script(clean_script(script))
    ctx = Context(font)
    root = Line(ctx, None)
    root.size = font_size
    ctx.set_font(font_size)
    if parser.root:
        Converter(parser, root).convert(parser.root)
    root.calc(0)
    return root


def measure(script: str, font_size: int, font: str = EQUATION_FONT) -> tuple[int, int, int]:
    """수식 상자의 (폭, 높이, 저장 기준선 백분율). HwpApp FUN_1026e540·FUN_1026f440과 같은 값을 낸다."""
    root = _laid_out(script, font_size, font)
    baseline = cdiv(font_size * 35, 100) - root.top
    width, height = max(root.w, 1), max(root.h, 1)
    return width, height, cdiv(baseline * 100, height) + 1


def draw(script: str, font_size: int, font: str = EQUATION_FONT) -> list[DrawnGlyph | DrawnBox]:
    """수식 글자와 상자를 그리는 순서대로 돌려준다. 좌표는 상자 왼쪽 위 끝이 (0, 0)인 HWPUNIT이다."""
    root = _laid_out(script, font_size, font)
    root.ctx.set_font(font_size)
    root.ox, root.oy = 0, -root.top
    root.draw()
    return root.ctx.drawn
