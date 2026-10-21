"""Page를 SVG 문자열로 바꾼다. SVG 사용자 단위는 pt(HWPUNIT/100)로 PDF 좌표와 같다."""

from __future__ import annotations

import base64
import functools
import gzip
import json
import re
import urllib.parse
from collections.abc import Callable
from importlib import resources
from xml.sax.saxutils import escape, quoteattr

from .scene import Glyph, Image, Line, Page, Rect

HU_PER_PT = 100.0
#: XML 1.0이 허용하지 않는 글자: 탭·줄바꿈·CR을 뺀 C0 제어 문자, 짝 없는 대리 코드, U+FFFE·U+FFFF.
_XML_FORBIDDEN = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
#: 한글 글자 코드 30(묶음 빈칸)·31(고정폭 빈칸)은 빈칸으로 보이므로 복사할 글에서는 지우지 않고 빈칸으로 바꾼다.
_SPACE_CONTROLS = str.maketrans({"\x1e": " ", "\x1f": " "})


@functools.cache
def _pua_table() -> dict:
    data = resources.files("rea_hwp.render").joinpath("data/pua_hangul.json.gz").read_bytes()
    return json.loads(gzip.decompress(data))


@functools.cache
def _pua_hangul() -> dict[int, str]:
    return {int(code, 16): text for code, text in _pua_table()["jamo"].items()}


def display_font(char: str, family: str) -> str:
    """기호 영역(U+F000~F0FF) 글자는 글꼴에 글리프가 없으면 기본 글꼴 목록에서 글리프가 있는 첫 글꼴로 그린다.

    HncBaseDraw FUN_10055910·FUN_10055820과 fontinfo.dat [Default Fonts KOR] Symbol 줄(tools/build_pua_hangul.py).
    """
    table = _pua_table()
    fallback = table["symbol_fallbacks"].get(f"{ord(char):X}") if len(char) == 1 else None
    if fallback is not None and ord(char) not in table["symbol_coverage"].get(family, ()):
        return fallback
    if fallback is None and any(_needs_hancom_font(code) for code in map(ord, display_text(char))):
        # 한컴 전용 PUA(네모 숫자·글머리·겹낫표)와 옛한글 자모는 지정 글꼴에 없으면 브라우저 기본 글꼴의 빈 네모·빈칸이
        # 된다. 이 PC에서 그 글리프를 모두 가진 글꼴은 함초롬·한컴바탕뿐이라(fc-list: U+F02B1·F02F2·F0854·E38A·ECF0)
        # 함초롬바탕을 다음 글꼴로 둔다. 정답 PDF는 함초롬바탕 지정 글자만 같은 모양이고 나머지는 한글이 고른 글꼴이다.
        return f"'{family}', '함초롬바탕', 'HCR Batang'"
    return family


def _needs_hancom_font(code: int) -> bool:
    """PUA와 첫가끝 자모(옛한글 조합)·방점. 대부분의 글꼴에 없어 빈 네모나 빈칸이 된다."""
    return (
        0xE000 <= code <= 0xF8FF
        or code >= 0xF0000
        or 0x1100 <= code <= 0x11FF
        or 0xA960 <= code <= 0xA97F
        or 0xD7B0 <= code <= 0xD7FF
        or code in (0x302E, 0x302F)
    )


def display_text(text: str) -> str:
    """보여 주고 복사하는 글: 옛한글 PUA를 한글처럼 첫가끝 자모로 바꾼다(HncPUAStrToUniStr, tools/build_pua_hangul.py).

    기호 PUA와 평면 15·16 PUA는 한글에도 표준 글자 대응이 없어 그대로 두고 글꼴 글리프로 그린다.
    """
    table = _pua_hangul()
    return "".join(table.get(ord(char), char) for char in text)


def xml_safe(text: str) -> str:
    return _XML_FORBIDDEN.sub("", text)


def copy_text(text: str) -> str:
    """복사·검색용 줄 글: 보여 주는 글(display_text)에서 빈칸 제어 문자는 빈칸으로, 나머지 XML 금지 글자는 지운다."""
    return xml_safe(display_text(text).translate(_SPACE_CONTROLS))


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
    family = quoteattr(xml_safe(display_font(glyph.char, glyph.font_family)))
    # 한글은 U+00AD를 글꼴의 하이픈 모양으로 찍는다(task2093 3쪽 PDF: 텍스트는 U+00AD, 그림은 「-」 글머리표).
    # 브라우저는 소프트 하이픈으로 숨기므로 그릴 때만 「-」로 바꾸고 글자 값(복사·평가)은 그대로 둔다.
    text = escape(xml_safe(display_text(glyph.char).replace("\u00ad", "-")))
    if abs(glyph.scale_x - 1.0) > 0.001 or glyph.rotation:
        # 장평은 가로로만 줄이거나 늘린다. 원점을 기준으로 변환해 x 위치가 흔들리지 않게 한다.
        rotate = f" rotate({-glyph.rotation:g})" if glyph.rotation else ""
        return (
            f'<text transform="translate({_pt(glyph.x)} {_pt(glyph.y)}){rotate} scale({glyph.scale_x:.4f} 1)" '
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


def _rect(rect: Rect, index: int) -> str:
    fill = rect.fill or "none"
    definition = ""
    if rect.gradient is not None:
        gradient = rect.gradient
        stops = "".join(f'<stop offset="{offset:g}" stop-color="{color}"/>' for offset, color in gradient.stops)
        if gradient.radial:
            center = f'cx="{_pt(gradient.x1)}" cy="{_pt(gradient.y1)}"'
            shape = f'radialGradient id="grad{index}" {center} r="{_pt(gradient.x2)}"'
        else:
            shape = (
                f'linearGradient id="grad{index}" x1="{_pt(gradient.x1)}" y1="{_pt(gradient.y1)}" '
                f'x2="{_pt(gradient.x2)}" y2="{_pt(gradient.y2)}"'
            )
        definition = f'<defs><{shape} gradientUnits="userSpaceOnUse">{stops}</{shape.split()[0]}></defs>'
        fill = f"url(#grad{index})"
    stroke = f' stroke="{rect.stroke}" stroke-width="{_pt(rect.stroke_width)}"' if rect.stroke else ""
    return (
        f'{definition}<rect x="{_pt(rect.x)}" y="{_pt(rect.y)}" width="{_pt(rect.width)}" height="{_pt(rect.height)}" '
        f'fill="{fill}"{stroke}/>'
    )


def _image(image: Image, index: int, href: str) -> str:
    href = quoteattr(xml_safe(href))
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


#: 그림 바이트와 mime을 받아 SVG <image href>에 넣을 주소를 돌려주는 함수.
ImageHref = Callable[[bytes, str], str]


def image_data(href: str) -> tuple[bytes, str] | None:
    """data: URI의 (바이트, mime). data: URI가 아니면 None."""
    if not href.startswith("data:"):
        return None
    meta, _, payload = href[5:].partition(",")
    mime, *parameters = meta.split(";")
    if "base64" in parameters:
        return base64.b64decode(payload), mime or "text/plain"
    return urllib.parse.unquote_to_bytes(payload), mime or "text/plain"


def page_to_svg(page: Page, *, image_href: ImageHref | None = None) -> str:
    """image_href를 주면 그림마다 (바이트, mime)으로 불러 돌려준 주소를 쓰고, 주지 않으면 data: URI를 그대로 넣는다.

    mime은 래스터 그림이면 image/png·image/jpeg·image/gif 등(BMP·TIFF·PCX는 PNG로 바꿔 넘긴다, 바꾸지 못한 BMP는
    image/bmp), 그리기 도형·WMF·글맵시를 옮긴 벡터 그림이면 image/svg+xml이다. 같은 그림은 같은 바이트로 넘어온다.
    """
    resolved: dict[str, str] = {}

    def href_of(image: Image) -> str:
        if image_href is None:
            return image.href
        if image.href not in resolved:
            decoded = image_data(image.href)
            resolved[image.href] = image.href if decoded is None else image_href(*decoded)
        return resolved[image.href]

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
            parts.append(_rect(item, index))
        elif isinstance(item, Image):
            parts.append(_image(item, index, href_of(item)))
    parts.append("</svg>")
    return "\n".join(parts)


def symbol_text(char: str) -> str:
    """기호 글꼴 PUA(U+F020~F0FF)는 PDF 텍스트에서 아래 바이트의 cp1252 글자로 나온다(basic__sungeo: U+F09F → Ÿ).

    한컴 전용 기호(평면 15 PUA)는 PDF 텍스트에서 모두 U+F000이다(hwp3-sample16: U+F03C5, 해외직접투자: U+F02B1 등).
    """
    if len(char) != 1:
        return char
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
