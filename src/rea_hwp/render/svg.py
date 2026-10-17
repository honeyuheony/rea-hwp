"""Page를 SVG 문자열로 바꾼다. SVG 사용자 단위는 pt(HWPUNIT/100)로 PDF 좌표와 같다."""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from .scene import Glyph, Image, Line, Page, Rect

HU_PER_PT = 100.0


def _pt(value: float) -> str:
    text = f"{value / HU_PER_PT:.3f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _glyph(glyph: Glyph) -> str:
    style = []
    if glyph.bold:
        style.append('font-weight="bold"')
    if glyph.italic:
        style.append('font-style="italic"')
    if glyph.color != "#000000":
        style.append(f'fill="{glyph.color}"')
    attrs = " ".join(style)
    family = quoteattr(glyph.font_family)
    text = escape(glyph.char)
    if abs(glyph.scale_x - 1.0) > 0.001:
        # 장평은 가로로만 줄이거나 늘린다. 원점을 기준으로 변환해 x 위치가 흔들리지 않게 한다.
        return (
            f'<text transform="translate({_pt(glyph.x)} {_pt(glyph.y)}) scale({glyph.scale_x:.4f} 1)" '
            f'font-size="{_pt(glyph.size)}" font-family={family} {attrs}>{text}</text>'
        )
    return (
        f'<text x="{_pt(glyph.x)}" y="{_pt(glyph.y)}" font-size="{_pt(glyph.size)}" '
        f"font-family={family} {attrs}>{text}</text>"
    )


def _line(line: Line) -> str:
    dash = f' stroke-dasharray="{line.dash}"' if line.dash else ""
    return (
        f'<line x1="{_pt(line.x1)}" y1="{_pt(line.y1)}" x2="{_pt(line.x2)}" y2="{_pt(line.y2)}" '
        f'stroke="{line.color}" stroke-width="{_pt(line.width)}"{dash}/>'
    )


def _rect(rect: Rect) -> str:
    fill = rect.fill or "none"
    stroke = f' stroke="{rect.stroke}" stroke-width="{_pt(rect.stroke_width)}"' if rect.stroke else ""
    return (
        f'<rect x="{_pt(rect.x)}" y="{_pt(rect.y)}" width="{_pt(rect.width)}" height="{_pt(rect.height)}" '
        f'fill="{fill}"{stroke}/>'
    )


def _image(image: Image, index: int) -> str:
    href = quoteattr(image.href)
    if image.crop is None:
        return (
            f'<image x="{_pt(image.x)}" y="{_pt(image.y)}" width="{_pt(image.width)}" height="{_pt(image.height)}" '
            f'preserveAspectRatio="none" href={href}/>'
        )
    left, top, right, bottom = image.crop
    span_x, span_y = max(right - left, 1e-9), max(bottom - top, 1e-9)
    full_w, full_h = image.width / span_x, image.height / span_y
    clip = f"clip{index}"
    return (
        f'<clipPath id="{clip}"><rect x="{_pt(image.x)}" y="{_pt(image.y)}" '
        f'width="{_pt(image.width)}" height="{_pt(image.height)}"/></clipPath>'
        f'<image clip-path="url(#{clip})" x="{_pt(image.x - left * full_w)}" y="{_pt(image.y - top * full_h)}" '
        f'width="{_pt(full_w)}" height="{_pt(full_h)}" preserveAspectRatio="none" href={href}/>'
    )


def page_to_svg(page: Page) -> str:
    width, height = _pt(page.width), _pt(page.height)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}pt" height="{height}pt" '
        f'viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
    ]
    for index, item in enumerate(page.items):
        if isinstance(item, Glyph):
            parts.append(_glyph(item))
        elif isinstance(item, Line):
            parts.append(_line(item))
        elif isinstance(item, Rect):
            parts.append(_rect(item))
        elif isinstance(item, Image):
            parts.append(_image(item, index))
    parts.append("</svg>")
    return "\n".join(parts)


def symbol_text(char: str) -> str:
    """기호 글꼴 PUA(U+F020~F0FF)는 PDF 텍스트에서 아래 바이트의 cp1252 글자로 나온다(basic__sungeo: U+F09F → Ÿ).

    한컴 전용 기호(평면 15 PUA)는 PDF 텍스트에서 모두 U+F000이다(hwp3-sample16: U+F03C5, 해외직접투자: U+F02B1 등).
    """
    code = ord(char)
    if 0xF0000 <= code <= 0xFFFFD:
        return "\uf000"
    if 0xF020 <= code <= 0xF0FF:
        try:
            return bytes([code & 0xFF]).decode("cp1252")
        except UnicodeDecodeError:
            return char
    return char


def page_glyph_table(page: Page) -> list[list]:
    """정확도 비교용 글자 원점 목록 [글자, x pt, y pt]."""
    return [
        [symbol_text(glyph.char), round(glyph.x / HU_PER_PT, 3), round(glyph.y / HU_PER_PT, 3)]
        for glyph in page.glyphs
        if glyph.char.strip()
    ]
