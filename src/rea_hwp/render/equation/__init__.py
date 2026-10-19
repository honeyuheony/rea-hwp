"""한글 2022 수식 엔진(Equation Version 60)의 상자 계산. measure()가 (폭, 높이, 기준선)을 돌려준다."""

from __future__ import annotations

from .engine import Context, Converter, Line, cdiv
from .glyphs import EQUATION_FONT
from .parse import parse_script

__all__ = ["measure"]


def measure(script: str, font_size: int, font: str = EQUATION_FONT) -> tuple[int, int, int]:
    """수식 상자의 (폭, 높이, 저장 기준선 백분율). HwpApp FUN_1026e540·FUN_1026f440과 같은 값을 낸다."""
    script = "".join(ch if ord(ch) >= 0x20 else " " for ch in script)
    parser = parse_script(script)
    ctx = Context(font)
    root = Line(ctx, None)
    root.size = font_size
    ctx.set_font(font_size)
    if parser.root:
        Converter(parser, root).convert(parser.root)
    width, height = root.calc(0)
    baseline = cdiv(font_size * 35, 100) - root.top
    width, height = max(width, 1), max(height, 1)
    return width, height, cdiv(baseline * 100, height) + 1
