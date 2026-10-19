"""WMF(Windows Metafile) 그림을 글자를 뺀 SVG 도형과 글자 목록으로 바꾼다.

레코드 배치는 MS-WMF 명세를 따른다. 좌표는 첫 창(SetWindowOrg/Ext) 또는 배치 헤더 bbox 기준 논리 단위다.
"""

from __future__ import annotations

import base64
import copy
import math
import struct
from collections.abc import Iterator
from dataclasses import dataclass, field

PLACEABLE_KEY = 0x9AC6CDD7
PLACEABLE_SIZE = 22
META_HEADER_SIZE = 18

META_EOF = 0x0000
META_SAVEDC = 0x001E
META_SETBKMODE = 0x0102
META_SETMAPMODE = 0x0103
META_SETPOLYFILLMODE = 0x0106
META_SELECTCLIPREGION = 0x012C
META_SELECTOBJECT = 0x012D
META_SETTEXTALIGN = 0x012E
META_RESTOREDC = 0x0127
META_DIBCREATEPATTERNBRUSH = 0x0142
META_DELETEOBJECT = 0x01F0
META_CREATEPATTERNBRUSH = 0x01F9
META_CREATEPALETTE = 0x00F7
META_SETBKCOLOR = 0x0201
META_SETTEXTCOLOR = 0x0209
META_SETWINDOWORG = 0x020B
META_SETWINDOWEXT = 0x020C
META_LINETO = 0x0213
META_MOVETO = 0x0214
META_CREATEPENINDIRECT = 0x02FA
META_CREATEFONTINDIRECT = 0x02FB
META_CREATEBRUSHINDIRECT = 0x02FC
META_POLYGON = 0x0324
META_POLYLINE = 0x0325
META_INTERSECTCLIPRECT = 0x0416
META_ELLIPSE = 0x0418
META_RECTANGLE = 0x041B
META_TEXTOUT = 0x0521
META_POLYPOLYGON = 0x0538
META_PATBLT = 0x061D
META_ROUNDRECT = 0x061C
META_CREATEREGION = 0x06FF
META_DIBBITBLT = 0x0940
META_EXTTEXTOUT = 0x0A32
META_DIBSTRETCHBLT = 0x0B41
META_STRETCHDIB = 0x0F43

#: 객체 표 슬롯만 차지하고 그리기에는 쓰지 않는 객체를 만드는 레코드. 슬롯 번호를 맞추려면 자리를 채워야 한다.
PLACEHOLDER_OBJECT_RECORDS = (META_CREATEPATTERNBRUSH, META_CREATEPALETTE, META_CREATEREGION)

PS_DASH, PS_DOT, PS_DASHDOT, PS_DASHDOTDOT, PS_NULL = 1, 2, 3, 4, 5
BS_SOLID, BS_NULL, BS_HATCHED = 0, 1, 2
OPAQUE = 2
ALTERNATE = 1
TA_UPDATECP = 0x0001
TA_HORIZONTAL_MASK = 0x0006
TA_RIGHT, TA_CENTER = 0x0002, 0x0006
TA_VERTICAL_MASK = 0x0018
TA_BOTTOM, TA_BASELINE = 0x0008, 0x0018
ETO_OPAQUE, ETO_CLIPPED, ETO_GLYPH_INDEX = 0x0002, 0x0004, 0x0010
FW_SEMIBOLD = 600
ANSI_CHARSET = 0
SRCCOPY = 0x00CC0020
#: 투명 합성에 쓰는 래스터 연산(MS-WMF 2.1.1.31 TernaryRasterOperation): 바탕 OR 원본, 바탕 AND 원본.
SRCPAINT = 0x00EE0086
SRCAND = 0x008800C6
PATCOPY, BLACKNESS, WHITENESS = 0x00F00021, 0x00000042, 0x00FF0062
BI_BITFIELDS = 3

#: 굴림(gulim.ttc) OS/2 winAscent 879·winDescent 145, unitsPerEm 1024. 다른 글꼴은 측정하지 않았다.
FONT_ASCENT, FONT_DESCENT = 879 / 1024, 145 / 1024
#: 글자 폭을 모를 때(dx 배열 없음) 가운데·오른쪽 정렬 환산에 쓰는 추정 폭. 검증 안 됨: 한글 전각·영문 반각 가정.
WIDE_ADVANCE, NARROW_ADVANCE = 1.0, 0.5
#: 화면 1픽셀을 논리 단위로 환산할 때의 가정. 배치 헤더가 있으면 Inch 값/96을 쓴다.
#: 검증 안 됨: 헤더가 없을 때 그림 긴 변을 600픽셀로 본 추정값.
ASSUMED_LONG_SIDE_PIXELS = 600
#: 기본 글꼴(System)의 높이. 검증 안 됨: 화면 16픽셀 가정.
DEFAULT_FONT_PIXELS = 16
#: 펜 모양별 점선 길이(픽셀). 검증 안 됨: GDI 표시와 대조하지 않은 근삿값.
DASH_PATTERNS = {PS_DASH: (18, 6), PS_DOT: (3, 3), PS_DASHDOT: (9, 6, 3, 6), PS_DASHDOTDOT: (9, 3, 3, 3, 3, 3)}
#: 빗금 붓(HS_*) 무늬 한 칸의 픽셀 크기. GDI 빗금 무늬는 8×8 픽셀이다.
HATCH_PIXELS = 8


@dataclass
class WmfText:
    #: 글자 기준선 왼쪽 끝. 창 원점 기준 논리 좌표.
    x: float
    y: float
    text: str
    size: float
    face: str
    bold: bool
    italic: bool
    color: str
    advances: list[float] | None = None
    #: 글줄 방향 각도(도, 반시계). 글꼴 escapement에서 온다.
    angle: float = 0.0


@dataclass
class WmfDrawing:
    width: float
    height: float
    svg: str
    texts: list[WmfText] = field(default_factory=list)


@dataclass(frozen=True)
class _Pen:
    style: int
    width: float
    color: str


@dataclass(frozen=True)
class _Brush:
    style: int
    color: str
    hatch: int = 0
    #: DIB 무늬 붓의 BMP 파일 바이트와 픽셀 크기.
    pattern: tuple[bytes, int, int] | None = None


@dataclass(frozen=True)
class _Font:
    height: int
    weight: int
    italic: bool
    charset: int
    face: str
    #: 글줄 방향(0.1도, 반시계, 장치 x축 기준. MS-WMF 2.2.1.2 Font).
    escapement: int = 0


@dataclass
class _DcState:
    pen: _Pen | None = field(default_factory=lambda: _Pen(0, 0, "#000000"))
    brush: _Brush | None = field(default_factory=lambda: _Brush(BS_SOLID, "#ffffff"))
    font: _Font | None = None
    text_color: str = "#000000"
    bk_color: str = "#ffffff"
    bk_mode: int = OPAQUE
    text_align: int = 0
    poly_fill_mode: int = ALTERNATE
    window_org: tuple[int, int] = (0, 0)
    window_ext: tuple[int, int] = (1, 1)
    #: 프레임 좌표로 환산한 잘라내기 사각형(x0, y0, x1, y1).
    clip: tuple[float, float, float, float] | None = None
    position: tuple[int, int] = (0, 0)


def convert_wmf(data: bytes) -> WmfDrawing | None:
    start = 0
    header_frame: tuple[int, int, int, int] | None = None
    pixels_per_unit: float | None = None
    if len(data) >= PLACEABLE_SIZE and struct.unpack_from("<I", data, 0)[0] == PLACEABLE_KEY:
        left, top, right, bottom, inch = struct.unpack_from("<4hH", data, 6)
        header_frame = (left, top, right - left, bottom - top)
        pixels_per_unit = 96 / inch if inch else None
        start = PLACEABLE_SIZE
    if len(data) < start + META_HEADER_SIZE:
        return None
    meta_type, header_words = struct.unpack_from("<HH", data, start)
    if meta_type not in (1, 2) or header_words != META_HEADER_SIZE // 2:
        return None
    records = list(_iter_records(data, start + META_HEADER_SIZE))
    frame = _window_frame(records) or header_frame
    if frame is None or frame[2] == 0 or frame[3] == 0:
        return None
    converter = _Converter(frame, pixels_per_unit)
    for function, params in records:
        try:
            converter.play(function, params)
        except struct.error:
            # 매개변수가 잘린 레코드 하나 때문에 그림 전체를 버리지 않는다.
            continue
    return converter.finish()


def _iter_records(data: bytes, pos: int) -> Iterator[tuple[int, bytes]]:
    while pos + 6 <= len(data):
        words, function = struct.unpack_from("<IH", data, pos)
        if function == META_EOF or words < 3 or pos + words * 2 > len(data):
            return
        yield function, data[pos + 6 : pos + words * 2]
        pos += words * 2


def _window_frame(records: list[tuple[int, bytes]]) -> tuple[int, int, int, int] | None:
    org: tuple[int, int] | None = None
    for function, params in records:
        if function == META_SETWINDOWORG and org is None and len(params) >= 4:
            y, x = struct.unpack_from("<hh", params)
            org = (x, y)
        elif function == META_SETWINDOWEXT and len(params) >= 4:
            height, width = struct.unpack_from("<hh", params)
            x, y = org or (0, 0)
            return (x, y, width, height)
    return None


def _color(params: bytes, offset: int = 0) -> str:
    red, green, blue = params[offset : offset + 3]
    return f"#{red:02x}{green:02x}{blue:02x}"


def _num(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def _points(params: bytes, offset: int, count: int) -> list[tuple[int, int]]:
    values = struct.unpack_from(f"<{count * 2}h", params, offset)
    return list(zip(values[0::2], values[1::2], strict=True))


def _decode_text(raw: bytes, charset: int) -> list[tuple[str, int]]:
    """글자마다 푼 글자와 그 글자가 차지한 바이트 수. CP949로 풀되 ANSI 글꼴에서 풀리지 않으면 cp1252로 푼다."""
    if charset == ANSI_CHARSET:
        try:
            raw.decode("cp949")
        except UnicodeDecodeError:
            # 한글 글꼴도 charset 0으로 기록되므로 CP949로 풀리지 않을 때만 서유럽 문자(‘ ’ 등)로 본다.
            return [(chr(byte) if byte < 0x80 else bytes([byte]).decode("cp1252", errors="replace"), 1) for byte in raw]
    chars: list[tuple[str, int]] = []
    index = 0
    while index < len(raw):
        width = 2 if raw[index] >= 0x81 and index + 1 < len(raw) else 1
        # 깨진 바이트는 대체 문자로 남겨 글자와 dx 배열의 짝을 유지한다.
        char = raw[index : index + width].decode("cp949", errors="replace")
        if len(char) != 1:
            width = 1
            char = raw[index : index + 1].decode("cp949", errors="replace")
        chars.append((char, width))
        index += width
    return chars


def _dib_to_bmp(dib: bytes) -> tuple[bytes, int, int] | None:
    """DIB(BITMAPINFO+픽셀)를 BMP 파일 바이트로 감싸고 픽셀 너비·높이를 함께 돌려준다."""
    if len(dib) < 12:
        return None
    header_size = struct.unpack_from("<I", dib)[0]
    if header_size == 12:
        width, height, _, bit_count = struct.unpack_from("<hhHH", dib, 4)
        palette_bytes = 3 * (1 << bit_count) if bit_count <= 8 else 0
    elif header_size >= 40 and len(dib) >= 40:
        width, height, _, bit_count, compression = struct.unpack_from("<iiHHI", dib, 4)
        colors_used = struct.unpack_from("<I", dib, 32)[0]
        palette_count = colors_used or (1 << bit_count if bit_count <= 8 else 0)
        palette_bytes = 4 * palette_count
        if compression == BI_BITFIELDS and header_size == 40:
            palette_bytes += 12
    else:
        return None
    if width == 0 or height == 0:
        return None
    pixel_offset = 14 + header_size + palette_bytes
    file_header = struct.pack("<2sIHHI", b"BM", 14 + len(dib), 0, 0, pixel_offset)
    return file_header + dib, abs(width), abs(height)


def _dib_height(dib: bytes) -> int:
    """DIB 헤더의 높이. 양수면 아래에서 위로 저장한 비트맵이다."""
    if len(dib) < 12:
        return 0
    if struct.unpack_from("<I", dib)[0] == 12:
        return struct.unpack_from("<h", dib, 6)[0]
    return struct.unpack_from("<i", dib, 8)[0]


class _Converter:
    def __init__(self, frame: tuple[int, int, int, int], pixels_per_unit: float | None) -> None:
        org_x, org_y, ext_x, ext_y = frame
        self.width = abs(ext_x)
        self.height = abs(ext_y)
        self.state = _DcState(window_org=(org_x, org_y), window_ext=(ext_x, ext_y))
        self.saved: list[_DcState] = []
        self.objects: list[_Pen | _Brush | _Font | None | object] = []
        self.pixel = 1 / pixels_per_unit if pixels_per_unit else max(self.width, self.height) / ASSUMED_LONG_SIDE_PIXELS
        self.elements: list[str] = []
        self.defs: list[str] = []
        self.def_ids: dict[object, str] = {}
        self.texts: list[WmfText] = []

    def finish(self) -> WmfDrawing:
        width, height = _num(self.width), _num(self.height)
        defs = f"<defs>{''.join(self.defs)}</defs>" if self.defs else ""
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" preserveAspectRatio="none">{defs}{"".join(self.elements)}</svg>'
        )
        return WmfDrawing(self.width, self.height, svg, self.texts)

    # 좌표 환산

    @property
    def scale(self) -> tuple[float, float]:
        ext_x, ext_y = self.state.window_ext
        return (self.width / ext_x if ext_x else 1.0, self.height / ext_y if ext_y else 1.0)

    def to_frame(self, x: float, y: float) -> tuple[float, float]:
        org_x, org_y = self.state.window_org
        scale_x, scale_y = self.scale
        return (x - org_x) * scale_x, (y - org_y) * scale_y

    def box(self, left: int, top: int, right: int, bottom: int) -> tuple[float, float, float, float]:
        x0, y0 = self.to_frame(left, top)
        x1, y1 = self.to_frame(right, bottom)
        return min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)

    # 레코드 재생

    def play(self, function: int, params: bytes) -> None:
        handler = self.HANDLERS.get(function)
        if handler is not None:
            handler(self, params)
        elif function in PLACEHOLDER_OBJECT_RECORDS:
            self.add_object(object())

    def add_object(self, gdi_object: object) -> None:
        for index, slot in enumerate(self.objects):
            if slot is None:
                self.objects[index] = gdi_object
                return
        self.objects.append(gdi_object)

    def set_window_org(self, params: bytes) -> None:
        y, x = struct.unpack_from("<hh", params)
        self.state.window_org = (x, y)

    def set_window_ext(self, params: bytes) -> None:
        height, width = struct.unpack_from("<hh", params)
        self.state.window_ext = (width, height)

    def save_dc(self, params: bytes) -> None:
        self.saved.append(copy.copy(self.state))

    def restore_dc(self, params: bytes) -> None:
        (which,) = struct.unpack_from("<h", params)
        index = len(self.saved) + which if which < 0 else which - 1
        if 0 <= index < len(self.saved):
            self.state = self.saved[index]
            del self.saved[index:]

    def create_pen(self, params: bytes) -> None:
        style, width = struct.unpack_from("<Hh", params)
        self.add_object(_Pen(style, abs(width), _color(params, 6)))

    def create_brush(self, params: bytes) -> None:
        style, hatch = struct.unpack_from("<H4xH", params)
        self.add_object(_Brush(style, _color(params, 2), hatch))

    def create_dib_pattern_brush(self, params: bytes) -> None:
        pattern = _dib_to_bmp(params[4:])
        self.add_object(_Brush(BS_SOLID, "#808080", pattern=pattern) if pattern else _Brush(BS_NULL, "#000000"))

    def create_font(self, params: bytes) -> None:
        height, _, escapement, _, weight, italic, _, _, charset = struct.unpack_from("<5h4B", params)
        face = params[18:50].split(b"\0", 1)[0].decode("cp949", errors="replace")
        self.add_object(_Font(height, weight, bool(italic), charset, face, escapement))

    def select_object(self, params: bytes) -> None:
        (index,) = struct.unpack_from("<H", params)
        selected = self.objects[index] if index < len(self.objects) else None
        if isinstance(selected, _Pen):
            self.state.pen = selected
        elif isinstance(selected, _Brush):
            self.state.brush = selected
        elif isinstance(selected, _Font):
            self.state.font = selected

    def delete_object(self, params: bytes) -> None:
        (index,) = struct.unpack_from("<H", params)
        if index < len(self.objects):
            self.objects[index] = None

    def set_text_color(self, params: bytes) -> None:
        self.state.text_color = _color(params)

    def set_bk_color(self, params: bytes) -> None:
        self.state.bk_color = _color(params)

    def set_bk_mode(self, params: bytes) -> None:
        self.state.bk_mode = struct.unpack_from("<H", params)[0]

    def set_text_align(self, params: bytes) -> None:
        self.state.text_align = struct.unpack_from("<H", params)[0]

    def set_poly_fill_mode(self, params: bytes) -> None:
        self.state.poly_fill_mode = struct.unpack_from("<H", params)[0]

    def intersect_clip_rect(self, params: bytes) -> None:
        bottom, right, top, left = struct.unpack_from("<4h", params)
        x, y, width, height = self.box(left, top, right, bottom)
        clip = (x, y, x + width, y + height)
        if self.state.clip is not None:
            old = self.state.clip
            clip = (max(old[0], clip[0]), max(old[1], clip[1]), min(old[2], clip[2]), min(old[3], clip[3]))
        self.state.clip = clip

    def select_clip_region(self, params: bytes) -> None:
        # 영역 객체는 해석하지 않으므로 어떤 영역을 고르든 잘라내기를 푼다.
        self.state.clip = None

    def move_to(self, params: bytes) -> None:
        y, x = struct.unpack_from("<hh", params)
        self.state.position = (x, y)

    def line_to(self, params: bytes) -> None:
        y, x = struct.unpack_from("<hh", params)
        self.add_path(self.path_data([self.state.position, (x, y)], close=False), fill=False)
        self.state.position = (x, y)

    def polyline(self, params: bytes) -> None:
        (count,) = struct.unpack_from("<h", params)
        self.add_path(self.path_data(_points(params, 2, count), close=False), fill=False)

    def polygon(self, params: bytes) -> None:
        (count,) = struct.unpack_from("<h", params)
        self.add_path(self.path_data(_points(params, 2, count), close=True), fill=True)

    def polypolygon(self, params: bytes) -> None:
        (polygons,) = struct.unpack_from("<H", params)
        counts = struct.unpack_from(f"<{polygons}H", params, 2)
        offset = 2 + polygons * 2
        subpaths = []
        for count in counts:
            subpaths.append(self.path_data(_points(params, offset, count), close=True))
            offset += count * 4
        self.add_path(" ".join(subpaths), fill=True)

    def rectangle(self, params: bytes) -> None:
        bottom, right, top, left = struct.unpack_from("<4h", params)
        x, y, width, height = self.box(left, top, right, bottom)
        self.add_shape(f'<rect x="{_num(x)}" y="{_num(y)}" width="{_num(width)}" height="{_num(height)}"', fill=True)

    def round_rect(self, params: bytes) -> None:
        corner_height, corner_width, bottom, right, top, left = struct.unpack_from("<6h", params)
        x, y, width, height = self.box(left, top, right, bottom)
        scale_x, scale_y = self.scale
        rx, ry = abs(corner_width * scale_x) / 2, abs(corner_height * scale_y) / 2
        self.add_shape(
            f'<rect x="{_num(x)}" y="{_num(y)}" width="{_num(width)}" height="{_num(height)}" '
            f'rx="{_num(rx)}" ry="{_num(ry)}"',
            fill=True,
        )

    def ellipse(self, params: bytes) -> None:
        bottom, right, top, left = struct.unpack_from("<4h", params)
        x, y, width, height = self.box(left, top, right, bottom)
        self.add_shape(
            f'<ellipse cx="{_num(x + width / 2)}" cy="{_num(y + height / 2)}" '
            f'rx="{_num(width / 2)}" ry="{_num(height / 2)}"',
            fill=True,
        )

    def pat_blt(self, params: bytes) -> None:
        rop, height, width, y, x = struct.unpack_from("<I4h", params)
        fills = {BLACKNESS: "#000000", WHITENESS: "#ffffff"}
        if rop not in fills and rop != PATCOPY:
            return
        left, top, box_width, box_height = self.box(x, y, x + width, y + height)
        rect = f'<rect x="{_num(left)}" y="{_num(top)}" width="{_num(box_width)}" height="{_num(box_height)}"'
        fill = f'fill="{fills[rop]}"' if rop in fills else self.fill_attrs()
        if fill:
            self.elements.append(f"{rect} {fill}{self.clip_attr()}/>")

    def text_out(self, params: bytes) -> None:
        (length,) = struct.unpack_from("<H", params)
        offset = 2 + length + (length & 1)
        y, x = struct.unpack_from("<hh", params, offset)
        self.add_text(x, y, params[2 : 2 + length], None)

    def ext_text_out(self, params: bytes) -> None:
        y, x, length, options = struct.unpack_from("<hhhH", params)
        offset = 8
        if options & (ETO_OPAQUE | ETO_CLIPPED):
            if options & ETO_OPAQUE:
                left, top, right, bottom = struct.unpack_from("<4h", params, offset)
                rx, ry, width, height = self.box(left, top, right, bottom)
                self.elements.append(
                    f'<rect x="{_num(rx)}" y="{_num(ry)}" width="{_num(width)}" height="{_num(height)}" '
                    f'fill="{self.state.bk_color}"/>'
                )
            offset += 8
        if options & ETO_GLYPH_INDEX or length <= 0:
            return
        raw = params[offset : offset + length]
        offset += length + (length & 1)
        dx = None
        if len(params) >= offset + length * 2:
            dx = list(struct.unpack_from(f"<{length}h", params, offset))
        self.add_text(x, y, raw, dx)

    def dib_bit_blt(self, params: bytes) -> None:
        if len(params) == (META_DIBBITBLT >> 8) * 2:
            return
        rop, y_src, x_src, height, width, y_dest, x_dest = struct.unpack_from("<I6h", params)
        self.add_image(rop, params[16:], (x_src, y_src, width, height), (x_dest, y_dest, width, height))

    def dib_stretch_blt(self, params: bytes) -> None:
        if len(params) == (META_DIBSTRETCHBLT >> 8) * 2:
            return
        rop, src_h, src_w, y_src, x_src, dest_h, dest_w, y_dest, x_dest = struct.unpack_from("<I8h", params)
        self.add_image(rop, params[20:], (x_src, y_src, src_w, src_h), (x_dest, y_dest, dest_w, dest_h))

    def stretch_dib(self, params: bytes) -> None:
        rop, _, src_h, src_w, y_src, x_src, dest_h, dest_w, y_dest, x_dest = struct.unpack_from("<IH8h", params)
        dib = params[22:]
        bitmap_height = _dib_height(dib)
        if bitmap_height > 0:
            # StretchDIBits는 아래에서 위로 저장한 DIB의 원본 y를 아래쪽 끝부터 센다.
            y_src = bitmap_height - y_src - src_h
        self.add_image(rop, dib, (x_src, y_src, src_w, src_h), (x_dest, y_dest, dest_w, dest_h))

    HANDLERS = {
        META_SETWINDOWORG: set_window_org,
        META_SETWINDOWEXT: set_window_ext,
        META_SAVEDC: save_dc,
        META_RESTOREDC: restore_dc,
        META_CREATEPENINDIRECT: create_pen,
        META_CREATEBRUSHINDIRECT: create_brush,
        META_DIBCREATEPATTERNBRUSH: create_dib_pattern_brush,
        META_CREATEFONTINDIRECT: create_font,
        META_SELECTOBJECT: select_object,
        META_DELETEOBJECT: delete_object,
        META_SETTEXTCOLOR: set_text_color,
        META_SETBKCOLOR: set_bk_color,
        META_SETBKMODE: set_bk_mode,
        META_SETTEXTALIGN: set_text_align,
        META_SETPOLYFILLMODE: set_poly_fill_mode,
        META_INTERSECTCLIPRECT: intersect_clip_rect,
        META_SELECTCLIPREGION: select_clip_region,
        META_MOVETO: move_to,
        META_LINETO: line_to,
        META_POLYLINE: polyline,
        META_POLYGON: polygon,
        META_POLYPOLYGON: polypolygon,
        META_RECTANGLE: rectangle,
        META_ROUNDRECT: round_rect,
        META_ELLIPSE: ellipse,
        META_PATBLT: pat_blt,
        META_TEXTOUT: text_out,
        META_EXTTEXTOUT: ext_text_out,
        META_DIBBITBLT: dib_bit_blt,
        META_DIBSTRETCHBLT: dib_stretch_blt,
        META_STRETCHDIB: stretch_dib,
    }

    # SVG 요소 만들기

    def path_data(self, points: list[tuple[int, int]], close: bool) -> str:
        if not points:
            return ""
        coords = [self.to_frame(x, y) for x, y in points]
        head = f"M{_num(coords[0][0])} {_num(coords[0][1])}"
        tail = "".join(f"L{_num(x)} {_num(y)}" for x, y in coords[1:])
        return head + tail + ("Z" if close else "")

    def add_path(self, path: str, fill: bool) -> None:
        if path:
            self.add_shape(f'<path d="{path}"', fill=fill)

    def add_shape(self, opening: str, fill: bool) -> None:
        fill_attrs = self.fill_attrs() if fill else 'fill="none"'
        stroke_attrs = self.stroke_attrs()
        if fill_attrs == 'fill="none"' and stroke_attrs == 'stroke="none"':
            return
        self.elements.append(f"{opening} {fill_attrs} {stroke_attrs}{self.clip_attr()}/>")

    def fill_attrs(self) -> str:
        brush = self.state.brush
        if brush is None or brush.style == BS_NULL:
            return 'fill="none"'
        rule = ' fill-rule="evenodd"' if self.state.poly_fill_mode == ALTERNATE else ""
        if brush.pattern is not None:
            return f'fill="url(#{self.pattern_id(brush)})"{rule}'
        if brush.style == BS_HATCHED:
            return f'fill="url(#{self.hatch_id(brush)})"{rule}'
        return f'fill="{brush.color}"{rule}'

    def stroke_attrs(self) -> str:
        pen = self.state.pen
        if pen is None or pen.style & 0x0F == PS_NULL:
            return 'stroke="none"'
        width = max(pen.width * abs(self.scale[0]), self.pixel)
        caps = {0x0000: "round", 0x0100: "square", 0x0200: "butt"}
        joins = {0x0000: "round", 0x1000: "bevel", 0x2000: "miter"}
        attrs = (
            f'stroke="{pen.color}" stroke-width="{_num(width)}" '
            f'stroke-linecap="{caps.get(pen.style & 0x0F00, "round")}" '
            f'stroke-linejoin="{joins.get(pen.style & 0xF000, "round")}"'
        )
        dashes = DASH_PATTERNS.get(pen.style & 0x0F)
        if dashes:
            attrs += f' stroke-dasharray="{" ".join(_num(dash * self.pixel) for dash in dashes)}"'
        return attrs

    def clip_attr(self) -> str:
        clip = self.state.clip
        if clip is None:
            return ""
        key = ("clip", clip)
        if key not in self.def_ids:
            clip_id = f"wmf-clip-{len(self.def_ids)}"
            x0, y0, x1, y1 = clip
            self.defs.append(
                f'<clipPath id="{clip_id}"><rect x="{_num(x0)}" y="{_num(y0)}" '
                f'width="{_num(max(x1 - x0, 0))}" height="{_num(max(y1 - y0, 0))}"/></clipPath>'
            )
            self.def_ids[key] = clip_id
        return f' clip-path="url(#{self.def_ids[key]})"'

    def hatch_id(self, brush: _Brush) -> str:
        background = self.state.bk_color if self.state.bk_mode == OPAQUE else None
        key = ("hatch", brush.hatch, brush.color, background)
        if key not in self.def_ids:
            hatch_id = f"wmf-hatch-{len(self.def_ids)}"
            size = HATCH_PIXELS * self.pixel
            half, end = _num(size / 2), _num(size)
            # HS_HORIZONTAL·VERTICAL·FDIAGONAL·BDIAGONAL·CROSS·DIAGCROSS 순서.
            lines = [
                [f"M0 {half}H{end}"],
                [f"M{half} 0V{end}"],
                [f"M0 0L{end} {end}"],
                [f"M0 {end}L{end} 0"],
                [f"M0 {half}H{end}", f"M{half} 0V{end}"],
                [f"M0 0L{end} {end}", f"M0 {end}L{end} 0"],
            ][brush.hatch if brush.hatch < 6 else 0]
            fill = f'<rect width="{end}" height="{end}" fill="{background}"/>' if background else ""
            strokes = "".join(
                f'<path d="{line}" stroke="{brush.color}" stroke-width="{_num(self.pixel)}"/>' for line in lines
            )
            self.defs.append(
                f'<pattern id="{hatch_id}" patternUnits="userSpaceOnUse" width="{end}" height="{end}">'
                f"{fill}{strokes}</pattern>"
            )
            self.def_ids[key] = hatch_id
        return self.def_ids[key]

    def pattern_id(self, brush: _Brush) -> str:
        assert brush.pattern is not None
        key = ("pattern", brush.pattern[0])
        if key not in self.def_ids:
            pattern_id = f"wmf-pattern-{len(self.def_ids)}"
            bmp, pixels_x, pixels_y = brush.pattern
            width, height = _num(pixels_x * self.pixel), _num(pixels_y * self.pixel)
            self.defs.append(
                f'<pattern id="{pattern_id}" patternUnits="userSpaceOnUse" width="{width}" height="{height}">'
                f'<image width="{width}" height="{height}" preserveAspectRatio="none" '
                f'href="data:image/bmp;base64,{base64.b64encode(bmp).decode("ascii")}"/></pattern>'
            )
            self.def_ids[key] = pattern_id
        return self.def_ids[key]

    def add_image(
        self, rop: int, dib: bytes, source: tuple[int, int, int, int], dest: tuple[int, int, int, int]
    ) -> None:
        # 비트 OR·AND는 채널마다 최댓값·최솟값(lighten·darken 혼합)으로 그린다. 한쪽 값이 0·255인 화소에서는 같다.
        blend = {SRCCOPY: "", SRCPAINT: ' style="mix-blend-mode:lighten"', SRCAND: ' style="mix-blend-mode:darken"'}
        if rop not in blend:
            return
        bitmap = _dib_to_bmp(dib)
        if bitmap is None:
            return
        bmp, pixels_x, pixels_y = bitmap
        x_src, y_src, src_w, src_h = source
        x_dest, y_dest, dest_w, dest_h = dest
        x0, y0 = self.to_frame(x_dest, y_dest)
        x1, y1 = self.to_frame(x_dest + dest_w, y_dest + dest_h)
        href = f"data:image/bmp;base64,{base64.b64encode(bmp).decode('ascii')}"
        image = f'<image width="{pixels_x}" height="{pixels_y}" preserveAspectRatio="none"{blend[rop]} href="{href}"/>'
        # 원본 사각형을 viewBox로 잘라 내고, 목적지 모서리 순서가 뒤집혔으면 거울상으로 놓는다.
        flip = f"translate({_num(x0)} {_num(y0)}) scale({1 if x1 >= x0 else -1} {1 if y1 >= y0 else -1})"
        self.elements.append(
            f'<g transform="{flip}"{self.clip_attr()}><svg width="{_num(abs(x1 - x0))}" height="{_num(abs(y1 - y0))}" '
            f'viewBox="{x_src} {y_src} {abs(src_w)} {abs(src_h)}" preserveAspectRatio="none">{image}</svg></g>'
        )

    # 글자

    def add_text(self, x: int, y: int, raw: bytes, dx: list[int] | None) -> None:
        state = self.state
        if state.text_align & TA_UPDATECP:
            x, y = state.position
        raw = raw.rstrip(b"\0")
        chars = _decode_text(raw, state.font.charset if state.font else ANSI_CHARSET)
        if not chars:
            return
        scale_x, scale_y = self.scale
        advances = None
        if dx is not None:
            advances = []
            byte_index = 0
            for _, width in chars:
                advances.append(sum(dx[byte_index : byte_index + width]) * abs(scale_x))
                byte_index += width
        font = state.font
        if font is not None:
            size = abs(font.height) * abs(scale_y)
            em = size if font.height < 0 else size / (FONT_ASCENT + FONT_DESCENT)
        else:
            size = em = DEFAULT_FONT_PIXELS * self.pixel
        if advances is not None:
            extent = sum(advances)
        else:
            extent = sum(em * (WIDE_ADVANCE if ord(char) >= 0x1100 else NARROW_ADVANCE) for char, _ in chars)
        origin_x, origin_y = self.to_frame(x, y)
        horizontal = state.text_align & TA_HORIZONTAL_MASK
        along = -extent if horizontal == TA_RIGHT else -extent / 2 if horizontal == TA_CENTER else 0.0
        vertical = state.text_align & TA_VERTICAL_MASK
        if vertical == TA_BOTTOM:
            down = -em * FONT_DESCENT
        elif vertical == TA_BASELINE:
            down = 0.0
        else:
            down = em * FONT_ASCENT
        # 정렬 기준점은 글줄 방향(along)과 그에 수직인 아래쪽(down)으로 옮긴다. 장치 좌표는 y가 아래로 자란다.
        angle = font.escapement / 10 if font is not None else 0.0
        cos, sin = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        left = origin_x + along * cos + down * sin
        baseline = origin_y - along * sin + down * cos
        if state.text_align & TA_UPDATECP:
            state.position = (x + round(extent / scale_x) if scale_x else x, y)
        self.texts.append(
            WmfText(
                x=left,
                y=baseline,
                text="".join(char for char, _ in chars),
                size=size,
                face=font.face if font else "",
                bold=bool(font and font.weight >= FW_SEMIBOLD),
                italic=bool(font and font.italic),
                color=state.text_color,
                advances=advances,
                angle=angle,
            )
        )
