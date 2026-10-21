"""수식 객체 트리와 상자 계산. EqAppModule.dll의 객체(종류는 +0x04)와 CalcSize(가상 함수 2번 칸)를 옮겼다.
y는 아래로 늘고 수식 축이 0이다. top은 상자 위 끝(+0x54), w·h는 폭·높이(+0x58·+0x5c)다."""

from __future__ import annotations

from dataclasses import dataclass

from .glyphs import (
    DEVICE_UNIT,
    EQUATION_ASCENT,
    EQUATION_FONT,
    FALLBACK_FONT,
    STRING_ADVANCES,
    UNITS_PER_PIXEL,
    glyph_table,
)
from .parse import Node, Parser
from .tables import ASCII_PUA, ASCII_PUA_FIRST, CHAR_NAMES, FUNCTION_NAMES, NAMED_CLASS, PUA_RANGES, SPACING


def muldiv(a: int, b: int, c: int) -> int:
    """Windows MulDiv: a*b/c를 가장 가까운 정수로(반은 0에서 먼 쪽)."""
    n = a * b
    q = (abs(n) + abs(c) // 2) // abs(c)
    return q if (n >= 0) == (c > 0) else -q


def cdiv(a: int, b: int) -> int:
    """C 정수 나눗셈(0 쪽으로 버림)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


_NAMED_CLASS: dict[int, int] = {}
for _code, _cls in NAMED_CLASS:
    _NAMED_CLASS.setdefault(_code, _cls)
_PUA_TO_ASCII = {pua: ord(ch) for ch, pua in ASCII_PUA}
_PUA_TO_SYMBOL = {pua: ord(ch) for ch, pua in ASCII_PUA[ASCII_PUA_FIRST:]}
_ASCII_TO_PUA = {ord(ch): pua for ch, pua in ASCII_PUA[ASCII_PUA_FIRST:]}


def to_pua(c: int) -> int:
    """FUN_10012c50: 글자 → 한컴 PUA. 바꿀 것이 없으면 0."""
    for lo, hi, base in PUA_RANGES:
        if lo <= c <= hi:
            return base - lo + c
    return _ASCII_TO_PUA.get(c, 0)


@dataclass
class DrawnGlyph:
    """Draw가 찍은 글자. 좌표·크기는 수식 좌표(HWPUNIT)다."""

    text: str
    x: int
    #: 기준선 y. top_aligned면 글자 칸 위 끝 y(글꼴 ascent를 더해야 기준선)다.
    y: int
    top_aligned: bool
    #: 글꼴 높이(lfHeight)와 lfWidth. lfWidth가 높이의 절반이면 늘리지 않은 글자다.
    height: int
    width: int
    font: str
    italic: bool
    bold: bool
    #: COLOR가 정한 글자 색(COLORREF 0x00BBGGRR). None이면 수식 색을 따른다.
    color: int | None = None

    @property
    def baseline(self) -> int:
        """기준선 y. 위 끝 맞춤 글자는 수식 글꼴 ascent를 더한다(위 끝 맞춤은 수식 글꼴 PUA 조각에만 쓴다)."""
        return self.y + round(self.height * EQUATION_ASCENT) if self.top_aligned else self.y


@dataclass
class DrawnBox:
    """Draw가 흰 붓과 기본 펜(장치 1점 검은 선)으로 그린 상자(BOX 악센트). 좌표는 수식 좌표다."""

    x: int
    y: int
    w: int
    h: int
    #: 흰 붓으로 칠하는가(BOX). BIGG 테두리는 선만 긋는다(DrawRectangle 채움 0).
    filled: bool = True


class Context:
    """글꼴 문맥(CHncDrawContext와 HNCLOGFONT를 가진 구조체). 필드 옆 숫자는 오프셋이다."""

    def __init__(self, font: str) -> None:
        self.font = font  # +0x858
        self.size = 1000  # +0x848
        self.half = 500  # +0x84c
        self.italic = 1  # +0x850 (0이면 똑바른 꼴)
        self.top_flag = 0  # +0x0e
        self.empty_zero = 1  # +0x880, HwpApp이 vt[0x24](h, 1)로 1을 넘긴다
        self.bold = 0  # +0x854, 글꼴 가중치 700
        self.weight = 400  # +0x04 HNCLOGFONT 가중치
        self.table = glyph_table()
        #: CHncDrawContext::SetTextColor로 정한 글자 색. None이면 수식 색이다.
        self.text_color: int | None = None
        #: Draw가 찍은 글자와 상자(그리는 순서).
        self.drawn: list[DrawnGlyph | DrawnBox] = []

    def set_font(self, size: int) -> None:
        """FUN_10011f10."""
        self.size = size
        self.half = cdiv(size, 2)

    def set_extent(self, height: int, width: int) -> None:
        """FUN_10011e80: 글꼴 높이와 lfWidth를 따로 정한다(늘린 글자)."""
        self.size = height
        self.half = width

    def draw_char(self, x: int, y: int, code: int) -> None:
        """FUN_100122f0: 상자 위 끝 y에 글자 하나를 찍는다."""
        if code < 0x20:
            return
        if code == 0x2026 and self.font == EQUATION_FONT:
            y += int(self.size * 0.35)
        italic = False
        if self.italic == 0:
            if 0xE0E5 <= code <= 0xE0FE:
                code, y = code - 0xE0E5 + 0x61, y - muldiv(self.size, 0x43, 1000)
            elif 0xE000 <= code <= 0xE019:
                code, y = code - 0xE000 + 0x41, y - muldiv(self.size, 0x41, 1000)
            elif code == 0xE05B:
                code = 0xE072
        elif 0xE000 <= code <= 0xE019:
            # 기울인 대문자는 PUA 글자를 그대로 두고 글꼴 기울임(+0x0e)으로 찍는다
            italic = True
        font = self.font if italic or self.has_glyph(code) else FALLBACK_FONT
        # 괄호·근호 조각(E044~E084, E100~E105)은 위 끝 맞춤(TA_TOP), 나머지는 기준선 맞춤(TA_BASELINE)이다
        top_aligned = not italic and (0xE044 <= code <= 0xE084 or 0xE100 <= code <= 0xE105)
        self._emit(chr(code), x, y if top_aligned else y + muldiv(self.size, 0x55, 100), top_aligned, font, italic)

    def draw_text(self, x: int, y: int, text: str) -> None:
        """FUN_100126b0: 글자열을 기준선 맞춤으로 찍는다. 글리프가 하나라도 없으면 대체 글꼴로 찍는다."""
        font = self.font if all(self.has_glyph(ord(ch)) for ch in text) else FALLBACK_FONT
        baseline = y + muldiv(self.size, 0x55, 100)
        if len(text) == 2 and 0xD800 <= ord(text[0]) <= 0xDBFF:
            # 대리 쌍은 글자 하나로 찍는다
            self._emit(text.encode("utf-16-le", "surrogatepass").decode("utf-16-le"), x, baseline, False, font, False)
            return
        advances = STRING_ADVANCES.get(font, {})
        ppem = muldiv(self.size, 1, DEVICE_UNIT)
        for ch in text:
            self._emit(ch, x, baseline, False, font, False)
            if ch in advances:
                x += muldiv(advances[ch], ppem, 1024) * DEVICE_UNIT
            else:
                # 검증 안 됨: 전진 폭 표에 없는 글자는 화면(96DPI) 폭으로 잇는다
                x += self.extent(font, ord(ch))[0]

    def _emit(self, text: str, x: int, y: int, top_aligned: bool, font: str, italic: bool) -> None:
        glyph = DrawnGlyph(text, x, y, top_aligned, self.size, self.half, font, italic, self.weight == 700)
        glyph.color = self.text_color
        self.drawn.append(glyph)

    def has_glyph(self, code: int) -> bool:
        """FUN_10011fd0."""
        return code != 0xAC and (self.has(self.font, code) or self.has(EQUATION_FONT, code))

    def has(self, font: str, code: int) -> bool:
        return self.table.has_glyph(font, code)

    def extent(self, font: str, code: int) -> tuple[int, int]:
        width, height = self.table.extent(font, code, self.size)
        # 굵은 꼴 흉내(GDI 합성 굵게)는 전진 폭을 1픽셀 늘린다. 8·12픽셀 자료로만 확인했다
        return (width + UNITS_PER_PIXEL if self.bold else width), height

    def plain(self, code: int) -> tuple[int, int]:
        """FUN_10012bd0: 대체 없이 현재 글꼴로 GetTextExtentPointW."""
        if code == 0:
            return 0, 0
        return self.extent(self.font, code)

    def checked(self, code: int) -> tuple[int, int]:
        """FUN_10012960의 글리프 검사 경로(FUN_10011fd0). 두 글꼴 모두 없으면 한컴바탕으로 측정한다."""
        missing = code == 0xAC or not (self.has(self.font, code) or self.has(EQUATION_FONT, code))
        return self.extent(FALLBACK_FONT if missing else self.font, code)

    def measure(self, code: int) -> tuple[int, int]:
        """FUN_10012960: 글자 하나 측정. 똑바른 꼴이면 PUA 영문자를 ASCII로 되돌린다."""
        if code < 0x20:
            return 0, 0
        if self.italic == 0:
            if 0xE0E5 <= code <= 0xE0FE:
                code = code - 0xE0E5 + 0x61
            elif 0xE000 <= code <= 0xE019:
                code = code - 0xE000 + 0x41
            return self.checked(code)
        if 0xE000 <= code <= 0xE019:
            return self.checked(code)
        return self.plain(code)


class Obj:
    """객체 공통부(FUN_1000cfc0)."""

    kind = 0x71

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.size = 1000  # +0x08
        self.name = ""  # +0x24
        self.style = 0  # +0x40, RM 객체 뒤면 1
        self.dirty = 1  # +0x44
        self.x = 0  # +0x50
        self.top = 0
        self.w = 0
        self.h = 0
        self.prev: Obj | None = None  # +0x60
        self.next: Obj | None = None  # +0x64
        self.parent: Line | None = None  # +0x68
        self.rows: list[Line] = []  # +0x6c 첫 자식부터 +0x64로 이은 순서

    def inherit(self) -> None:
        """FUN_1000d3b0: 앞 형제에서 크기와 모양 상태를 물려받는다(글꼴 이름은 측정에 쓰지 않아 옮기지 않았다)."""
        prev = self.prev
        if prev is None:
            self.style = 0
            self.size = 1000
            self.x = 0
            return
        self.size = prev.size
        if self.kind not in (0x1F, 0x20):
            self.style = prev.style
        self.x = prev.x + prev.w if prev.prev is not None else 0

    def calc(self, cache: int) -> tuple[int, int]:
        """FUN_1000d4c0: 일반 객체는 (0, 0)."""
        return 0, 0

    def draw(self) -> None:
        """Draw(가상 함수 +4). 일반 객체(FUN_10008260)는 아무것도 그리지 않는다."""

    def origin(self) -> tuple[int, int]:
        """가상 함수 +0x10(FUN_100076f0)이 돌려주는 부모 줄의 원점(+0x80 x, +0x7c 수식 축 y)과 이 객체 x를 더한 값."""
        line = self.parent
        return self.x + line.ox, line.oy

    def cached(self, cache: int) -> bool:
        return bool(cache) and not self.dirty

    def new_row(self) -> Line:
        return Line(self.ctx, self)


class Line(Obj):
    """줄 컨테이너(종류 0x78, FUN_1000d680). 머리(0x79)와 끝 표지(0x7a) 사이에 객체가 있다."""

    kind = 0x78
    #: 부모 객체가 이 종류면 빈 줄 크기가 0이다(FUN_1000fb00).
    EMPTY_ZERO_OWNERS = frozenset((2, 7, 3, 5, 8, 0x4D, 0x77, 0x42, 0x44, 0x45, 0x43, 0x46, 0x6B, 0x6C))

    def __init__(self, ctx: Context, owner: Obj | None) -> None:
        super().__init__(ctx)
        self.owner = owner  # +0x94
        self.gap = 0 if owner is not None else 10  # +0x84 줄 간격(백분율)
        self.amp = 0  # +0x78
        self.next_line: Line | None = None  # +0x98
        self.prev_line: Line | None = None  # +0x9c
        self.ox = 0  # +0x80, Draw 때 부모가 정하는 원점 x
        self.oy = 0  # +0x7c, 원점의 수식 축 y
        self.own_w = 0  # +0xa0, 다음 줄을 더하기 전 이 줄의 폭
        self.own_h = 0  # +0xa4, 다음 줄을 더하기 전 이 줄의 높이
        self.head = Marker(ctx, 0x79, self)
        self.end = Marker(ctx, 0x7A, self)
        self.head.next = self.end
        self.end.prev = self.head

    def items(self):
        item = self.head.next
        while item is not None and item is not self.end:
            yield item
            item = item.next

    def calc(self, cache: int) -> tuple[int, int]:
        """FUN_1000fa30."""
        if self.cached(cache):
            return self.w, self.h
        self.dirty = 0
        if self.prev_line is None:
            self.amp = 0
            column = 0
            while True:
                while True:
                    width = self.layout(column, 0, cache)
                    column += 1
                    if width == 0:
                        break
                if column >= 2:
                    break
        self.own_w, self.own_h = self.w, self.h
        if self.next_line is not None:
            gap = muldiv(self.size, self.gap, 100)
            _, next_h = self.next_line.calc(cache)
            self.h = self.h + gap + next_h
        return self.w, self.h

    def layout(self, column: int, width_floor: int, cache: int) -> int:
        """FUN_1000fb00: & 칸 하나를 배치하고 모든 줄에서 그 칸의 최대 폭을 돌려준다."""
        ctx = self.ctx
        remaining = column
        height = top = bottom = width = base_w = 0
        first = self.head.next
        if column == 0:
            self.head.size = self.size
            self.head.style = self.style
            if self.owner is None:
                ctx.top_flag &= 1
                if ctx.top_flag == 0:
                    ctx.italic = 1
            if first is self.end:
                owner = self.owner
                if not (owner is not None and ctx.empty_zero == 1 and owner.kind in self.EMPTY_ZERO_OWNERS):
                    ctx.set_font(self.size)
                    height = ctx.size
                    width = ctx.half
                top = -cdiv(height, 2)
        else:
            base_w, height, top = self.w, self.h, self.top
        for item in self.items():
            if item.kind == 0x6F:
                mark_function_name(item)
        item = first
        while item is not None and item is not self.end:
            if item.kind == 100:
                if remaining == 0:
                    if column == 0:
                        self.first_line().amp = 1
                    break
                remaining -= 1
            if remaining == 0:
                item_w, _ = item.calc(cache)
                width += item_w
                top = min(top, item.top)
                bottom = max(bottom, item.top + item.h)
                height = max(height, bottom - top)
            item = item.next
        width_floor = max(width_floor, width)
        result = 0
        if self.next_line is not None:
            if column == 0:
                self.next_line.size = self.size
            result = self.next_line.layout(column, width_floor, cache)
        result = max(result, width_floor)
        if item is not None:
            item.x = base_w + result
        if column == 0 and self.first_line().amp and item is not None:
            x = item.x
            back = item.prev
            while back is not None:
                x -= back.w
                back.x = x
                back = back.prev
        self.w = base_w + result
        self.top = top
        self.h = height
        return result

    def draw(self) -> None:
        """FUN_1000f550: 항목을 차례로 그리고 다음 줄을 이 줄 아래에 놓는다."""
        ctx = self.ctx
        if self.owner is None:
            ctx.top_flag &= 1
            if ctx.top_flag == 0:
                ctx.italic = 1
        for item in self.items():
            item.draw()
        if self.next_line is not None:
            gap = muldiv(self.size, self.gap, 100)
            self.next_line.oy = self.own_h - self.next_line.top + gap + self.oy + self.top
            self.next_line.ox = self.ox
            self.next_line.draw()

    def first_line(self) -> Line:
        line = self
        while line.prev_line is not None:
            line = line.prev_line
        return line


class Marker(Obj):
    """줄 머리(0x79)·끝 표지(0x7a)."""

    def __init__(self, ctx: Context, kind: int, line: Line) -> None:
        super().__init__(ctx)
        self.kind = kind
        self.parent = line


def atom_class(obj: Obj) -> int:
    """FUN_10021070: 간격 표에서 쓰는 원자 종류."""
    if obj.kind != 0x6F:
        return 1
    code = obj.code
    if code in _NAMED_CLASS:
        return _NAMED_CLASS[code]
    ch = chr(_PUA_TO_ASCII.get(code, 0))
    if ch in ",*":
        return 7
    if ch in "+-":
        return 3
    if ch in "=<>:~":
        return 4
    if ch in "({[":
        return 5
    if ch in ")}]":
        return 6
    return 1


def spacing_after(obj: Obj, factor: int) -> int:
    """다음 원자와의 간격(FUN_100105d0·FUN_100155d0). 글자는 x7, 첨자는 x10."""
    nxt = obj.next
    if nxt is None or nxt.kind == 0x7A:
        return 0
    t = SPACING[atom_class(obj)][atom_class(nxt)]
    return muldiv(obj.ctx.size, t, 100) * factor if t else 0


class Glyph(Obj):
    """글자 객체(종류 0x6f)."""

    kind = 0x6F

    def __init__(self, ctx: Context, code: int) -> None:
        super().__init__(ctx)
        self.code = code  # +0x9c
        self.roman = 0  # +0x94 함수 이름 글자
        self.quoted = 0  # +0x98

    def calc(self, cache: int) -> tuple[int, int]:
        """FUN_100105d0."""
        if self.cached(cache):
            return self.w, self.h
        self.dirty = 0
        self.inherit()
        ctx = self.ctx
        style = self.style
        if self.roman:
            ctx.italic = 0
        ctx.set_font(self.size)
        gap = spacing_after(self, 7)
        code = self.code
        if code not in (0x20, 0x7E, 0x60):
            cx, cy = ctx.measure(code)
            if self.roman:
                ctx.italic = 1 if style == 0 else 0
            self.w, self.h, self.top = cx + gap, cy, -cdiv(cy, 2)
            return self.w, self.h
        owner = self.parent.owner if self.parent is not None else None
        if owner is not None and owner.kind == 0x6D:
            width, height = ctx.measure(to_pua(0x31))
        else:
            width, height = ctx.half, ctx.size
        if code in (0x20, 0x60):
            width = cdiv(width, 4)
        self.w, self.h, self.top = width + gap, height, -cdiv(height, 2)
        return self.w, self.h

    def draw(self) -> None:
        """FUN_100100f0. 대리 쌍은 상위 글자가 둘을 함께 찍고 하위 글자는 건너뛴다."""
        ctx = self.ctx
        style = self.style
        if self.roman:
            ctx.italic = 0
        ctx.set_font(self.size)
        x, y = self.origin()
        y += self.top
        code = self.code
        if code not in (0x20, 0x7E, 0x60):
            nxt = self.next
            if 0xD800 <= code <= 0xDBFF:
                if isinstance(nxt, Glyph) and 0xDC00 <= nxt.code <= 0xDFFF:
                    ctx.draw_text(x, y, chr(code) + chr(nxt.code))
            elif not 0xDC00 <= code <= 0xDFFF:
                ctx.draw_char(x, y, code)
        if self.roman:
            ctx.italic = 1 if style == 0 else 0


def mark_function_name(glyph: Glyph) -> None:
    """FUN_10010c80: 앞 글자들과 이어 함수 이름(sin 등)이 되면 똑바른 꼴 표시를 한다."""
    text = ""
    matched = False
    item: Obj | None = glyph
    steps = 0
    while item is not None and item.kind == 0x6F and not item.quoted:
        text = item.name + text
        if text in FUNCTION_NAMES:
            before = item.prev
            if not (
                before is not None and before.name[:1].isascii() and before.name[:1].isalpha() and before.kind == 0x6F
            ):
                mark: Obj | None = item
                while mark is not None and mark is not glyph:
                    mark.roman = 1
                    mark = mark.next
                glyph.roman = 1
                matched = True
        item = item.prev
        if item is None or item.prev is None:
            break
        steps += 1
        if steps > 5:
            break
    if not matched:
        glyph.roman = 0


class Shape(Obj):
    """RM(0x1f)·IT(0x20) 객체. FUN_1001d9b0·FUN_1001dc90."""

    def __init__(self, ctx: Context, kind: int) -> None:
        super().__init__(ctx)
        self.kind = kind
        self.style = 1 if kind == 0x1F else 0
        self.name = "rm" if kind == 0x1F else "it"

    def calc(self, cache: int) -> tuple[int, int]:
        self.inherit()
        if self.cached(cache):
            return self.w, self.h
        self.dirty = 0
        self.inherit()
        self.ctx.italic = 0 if self.kind == 0x1F else 1
        self.w, self.h, self.top = 0, self.size, -cdiv(self.size, 2)
        return self.w, self.h

    def draw(self) -> None:
        """FUN_1001d870(RM)·FUN_1001db50(IT)."""
        self.ctx.italic = 0 if self.kind == 0x1F else 1
        self.ctx.set_font(self.size)


class Script(Obj):
    """첨자(0x77, FUN_10014cd0). sub = +0x78, sup = +0x7c, 기준은 앞 형제(LSUP·LSUB는 다음 형제)."""

    kind = 0x77

    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.name = "SUBSUP"
        self.sub = self.new_row()
        self.sup = self.new_row()
        self.has_sub = 0  # +0x88
        self.has_sup = 0  # +0x8c

    def _relink(self) -> None:
        self.rows = [row for row, on in ((self.sup, self.has_sup), (self.sub, self.has_sub)) if on]

    def add_sup(self) -> None:
        """FUN_10015ac0."""
        self.has_sup = 1
        self._relink()

    def add_sub(self) -> None:
        """FUN_10015a70."""
        self.has_sub = 1
        self._relink()

    def calc(self, cache: int) -> tuple[int, int]:
        """FUN_100155d0."""
        if self.cached(cache):
            return self.w, self.h
        self.dirty = 0
        self.inherit()
        ctx = self.ctx
        ctx.set_font(self.size)
        extra = 0
        prev = self.prev
        if self.has_sup and prev is not None and prev.kind == 0x6F and prev.name and "A" <= prev.name[0] <= "Z":
            extra = muldiv(ctx.size, 15, 100)
        nxt = self.next
        if nxt is not None and nxt.kind != 0x7A:
            t = SPACING[atom_class(self)][atom_class(nxt)]
            if t:
                extra = muldiv(ctx.size, t, 100) * 10
        small = muldiv(self.size, 0x44, 100)
        ctx.set_font(self.size)
        sub_w = sub_h = sup_w = sup_h = 0
        if self.has_sub:
            self.sub.size = small
            sub_w, sub_h = self.sub.calc(0)
        if self.has_sup:
            self.sup.size = small
            sup_w, sup_h = self.sup.calc(0)
        width = max(sup_w, sub_w)
        s = ctx.size
        if self.name in ("LSUP", "LSUB"):
            base = self.next
            if base is None:
                base_bottom = base_top = 0
            elif base.kind == 0x7A:
                base_bottom, base_top = cdiv(s, 2), -cdiv(s, 2)
            else:
                base.calc(1)
                base.dirty = 1
                base_bottom, base_top = base.h + base.top, base.top
        else:
            base = self.prev
            if base is None:
                base_bottom = base_top = 0
            elif base.prev is None:
                base_bottom, base_top = cdiv(s, 2), -cdiv(s, 2)
            else:
                base_bottom, base_top = base.h + base.top, base.top
        sub_y = base_bottom - muldiv(s, 20, 100)
        sup_y = muldiv(s, 25, 100) + base_top
        sub_top = self.sub.top + sub_y
        sup_bottom = sup_h + self.sup.top + sup_y
        if self.has_sup and sup_bottom > 0:
            sup_y -= sup_bottom
        if self.has_sub and sub_top < 0:
            sub_y -= sub_top
        self.sub_y, self.sup_y = sub_y, sup_y  # +0x80, +0x84
        top, bottom = base_top, base_bottom
        if self.has_sup:
            top = self.sup.top + sup_y
        if self.has_sub:
            bottom = self.sub.top + sub_y + sub_h
        self.w, self.top, self.h = width + extra, top, bottom - top
        return self.w, self.h

    def draw(self) -> None:
        """FUN_100152f0. 위 첨자는 앞 글자가 대문자로 시작하면 직전 글꼴 크기의 15%만큼 오른쪽으로 민다."""
        ctx = self.ctx
        prev = self.prev
        capital = self.has_sup and isinstance(prev, Glyph) and prev.name[:1] >= "A" and prev.name[:1] < "["
        shift = muldiv(ctx.size, 15, 100) if capital else 0
        x, oy = self.origin()
        ctx.set_font(self.size)
        if self.has_sub:
            self.sub.ox, self.sub.oy = x, self.sub_y + oy
            self.sub.draw()
        if self.has_sup:
            self.sup.ox, self.sup.oy = x + shift, self.sup_y + oy
            self.sup.draw()


class Fraction(Obj):
    """OVER(1, FUN_100144b0)·ATOP(4, FUN_10014b50). 첫 자식 +0x7c가 위, +0x78이 아래."""

    def __init__(self, ctx: Context, kind: int) -> None:
        super().__init__(ctx)
        self.kind = kind
        self.upper = self.new_row()  # +0x7c
        self.lower = self.new_row()  # +0x78
        self.rows = [self.upper, self.lower]

    def calc(self, cache: int) -> tuple[int, int]:
        if self.cached(cache):
            return self.w, self.h
        self.dirty = 0
        self.inherit()
        ctx = self.ctx
        s = self.size
        if self.kind == 1:
            ctx.set_font(s)
            min_w = ctx.half
            gap = muldiv(ctx.size, 30, 100)
            self.lower.size = s
            lower_w, lower_h = self.lower.calc(cache)
            self.upper.size = s
            upper_w, upper_h = self.upper.calc(cache)
            ctx.set_font(s)
            width = max(min_w, upper_w, lower_w) + muldiv(ctx.size, 50, 100)
        else:
            # ATOP(FUN_10014b50): 간격은 글리프 E05B 높이의 1/10
            ctx.set_font(s)
            _, glyph_h = ctx.plain(0xE05B)
            gap = cdiv(glyph_h, 10)
            glyph_w, _ = ctx.plain(0xE05B)
            self.lower.size = s
            lower_w, lower_h = self.lower.calc(cache)
            self.upper.size = s
            upper_w, upper_h = self.upper.calc(cache)
            width = max(glyph_w, lower_w, upper_w) + muldiv(gap, 500, 100)
        self.w = width
        self.h = upper_h + gap + lower_h
        self.top = -cdiv(gap, 2) - upper_h
        return self.w, self.h

    def draw(self) -> None:
        """OVER FUN_100141b0(분수선은 U+E06D를 폭에 맞게 늘려 찍는다)·ATOP FUN_100148e0."""
        ctx = self.ctx
        x, oy = self.origin()
        ctx.set_font(self.size)
        if self.kind == 1:
            inset = muldiv(ctx.size, 10, 100)
            ctx.set_extent(ctx.size, self.w - inset * 2)
            ctx.draw_char(inset + x, self.upper.h + self.top + oy, 0xE06D)
            ctx.set_font(self.size)
            gap = muldiv(ctx.size, 30, 100)
        else:
            gap = cdiv(ctx.plain(0xE05B)[1], 10)
        center = cdiv(self.w, 2) + x
        self.lower.ox = center - cdiv(self.lower.w, 2)
        self.lower.oy = cdiv(gap, 2) - self.lower.top + oy
        self.lower.draw()
        if self.kind != 1:
            ctx.set_font(self.size)
        self.upper.ox = center - cdiv(self.upper.w, 2)
        self.upper.oy = oy - cdiv(gap, 2) - self.upper.h - self.upper.top
        self.upper.draw()
        ctx.set_font(self.size)


class Root(Obj):
    """근호(6, FUN_10016990). +0x78 = 색인, +0x7c = 본문."""

    kind = 6

    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.index = self.new_row()
        self.body = self.new_row()
        self.rows = [self.index, self.body]

    def calc(self, cache: int) -> tuple[int, int]:
        if self.cached(cache):
            return self.w, self.h
        self.dirty = 0
        self.inherit()
        ctx = self.ctx
        s = self.size
        index_size = muldiv(s, 50, 100)
        self.body.size = s
        body_w, body_h = self.body.calc(cache)
        self.index.size = index_size
        index_w, index_h = self.index.calc(cache)
        ctx.set_font(s)
        sign_w, sign_h = ctx.plain(0xE05C)
        scale = cdiv((muldiv(s, 10, 100) + body_h) * 100, sign_h)
        scale = max(scale, 100)
        sign_h = muldiv(sign_h, scale, 100)
        self.scale, self.sign_h = scale, sign_h  # +0x80, +0x88
        self.index_shift = cdiv(sign_w, 2) - index_w  # +0x8c
        sign_w = sign_w + max(0, index_w - cdiv(sign_w, 2))
        self.sign_w = sign_w  # +0x84
        width = muldiv(s, 17, 100) + sign_w + body_w
        extra = index_h - muldiv(sign_h, 52, 100)
        self.index_rise = extra  # +0x90
        if extra <= muldiv(s, 5, 100):
            extra = muldiv(s, 5, 100)
        self.w = width
        self.h = sign_h + extra
        self.top = self.body.top - muldiv(s, 10, 100) - extra
        return self.w, self.h

    def draw(self) -> None:
        """FUN_10016810·FUN_10016ba0: 근호(U+E05C)를 세로로 늘려 찍고 윗선(U+E06D)을 본문 폭에 맞춰 늘린다."""
        ctx = self.ctx
        ctx.set_font(self.size)
        ctx.set_extent(muldiv(ctx.size, self.scale, 100), ctx.half)
        x, oy = self.origin()
        sign_y = oy + self.top + max(self.index_rise, muldiv(self.size, 5, 100))
        ctx.draw_char(max(-self.index_shift, 0) + x, sign_y, 0xE05C)
        self.index.ox = max(self.index_shift, 0) + x
        self.index.oy = oy - min(self.index_rise, 0) - self.index.top + self.top
        self.index.draw()
        ctx.set_font(self.size)
        ctx.set_extent(ctx.size, muldiv(self.size, 17, 100) + self.body.w)
        ctx.draw_char(x + self.sign_w - muldiv(self.size, 2, 100), sign_y - muldiv(ctx.size, 18, 100), 0xE06D)
        ctx.set_font(self.size)
        self.body.ox, self.body.oy = self.sign_w + x, oy
        self.body.draw()
        ctx.set_font(self.size)


class BigOperator(Obj):
    """SUM·PROD 등(3, 0x43~0x46; FUN_10013830·FUN_10013d90). +0x78 = 아래 한계, +0x7c = 위 한계."""

    GLYPHS = {3: 0xE067, 0x43: 0xE068, 0x44: 0xE069, 0x45: 0xE06A, 0x46: 0xE06B}

    def __init__(self, ctx: Context, kind: int) -> None:
        super().__init__(ctx)
        self.kind = kind
        self.glyph = self.GLYPHS[kind]
        self.scale = 180  # +0x9c
        self.lower = self.new_row()
        self.upper = self.new_row()
        self.rows = [self.lower, self.upper]

    def calc(self, cache: int) -> tuple[int, int]:
        if self.cached(cache):
            return self.w, self.h
        self.dirty = 0
        self.inherit()
        ctx = self.ctx
        limit = muldiv(self.size, 0x44, 100)
        ctx.set_font(muldiv(self.size, self.scale, 100))
        glyph_w, glyph_h = ctx.plain(self.glyph)
        core = muldiv(glyph_h, 75, 100)
        self.lower.size = limit
        lower_w, lower_h = self.lower.calc(cache)
        self.upper.size = limit
        upper_w, upper_h = self.upper.calc(cache)
        self.glyph_w, self.core = glyph_w, core  # +0xa0, +0xa4
        self.w = max(glyph_w, lower_w, upper_w)
        self.h = core + upper_h + lower_h
        self.top = -upper_h - cdiv(core, 2)
        return self.w, self.h

    def draw(self) -> None:
        """FUN_10013b00."""
        ctx = self.ctx
        ctx.set_font(muldiv(self.size, self.scale, 100))
        x, oy = self.origin()
        core = self.core
        center = cdiv(self.w, 2) + x
        ctx.draw_char(center - cdiv(self.glyph_w, 2), oy - cdiv(core, 2) - muldiv(core, 10, 100), self.glyph)
        self.lower.ox = center - cdiv(self.lower.w, 2)
        self.lower.oy = cdiv(core, 2) - self.lower.top + oy
        self.lower.draw()
        self.upper.ox = center - cdiv(self.upper.w, 2)
        self.upper.oy = oy - cdiv(core, 2) - self.upper.h - self.upper.top
        self.upper.draw()
        ctx.set_font(self.size)


class Delimiter:
    """괄호 글리프(FUN_10011580·FUN_100116b0·FUN_10011850)."""

    #: 종류별 +0x20·+0x22·+0x24·+0x26 조각 글리프. 가운데(+0x26)의 폭이 괄호 폭이다.
    PIECES = {
        1: (0xE07C, 0xE07E, 0, 0xE07D),
        2: (0xE078, 0xE07A, 0xE07B, 0xE079),
        3: (0xE100, 0xE103, 0, 0xE101),
        9: (0xE07F, 0xE081, 0, 0xE080),
        10: (0xE082, 0xE084, 0xE07B, 0xE083),
        11: (0xE102, 0xE104, 0, 0xE105),
    }
    #: 조각 합보다 낮은 괄호를 한 글자로 찍을 때 쓰는 글리프(+0x28).
    SMALL = {1: 0xE044, 2: 0xE04B, 3: 0xE049, 9: 0xE045, 10: 0xE04C, 11: 0xE04A}
    #: 그 밖 종류의 가운데 글리프(표 0x100a0434).
    SINGLE = {
        4: 0x2329,
        5: 0xE101,
        6: 0x2225,
        7: 0x2308,
        8: 0x230A,
        12: 0x232A,
        13: 0xE101,
        14: 0x2225,
        15: 0x2309,
        16: 0x230B,
    }
    CHAR_KIND = {0x28: 1, 0x29: 9, 0x3C: 4, 0x3E: 0xC, 0x5B: 3, 0x5D: 0xB, 0x7B: 2, 0x7C: 5, 0x7D: 10}

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.kind = 0
        self.top_piece = self.bottom_piece = self.extend_piece = 0  # +0x20, +0x22, +0x24
        self.middle = 0  # +0x26
        self.small = 0  # +0x28
        self.top_h = self.bottom_h = self.middle_h = 0  # +0x30, +0x38(+0x22 조각), +0x48
        self.whole_w = 0  # +0x44
        self.target = 0  # +0x10
        self.least = 0  # +0x14, 조각으로 그릴 수 있는 가장 낮은 높이
        self.size = 0  # +0x18
        self.x = self.y = 0  # +0x0c, +0x08 Draw 때 정하는 위 끝 왼쪽

    def set_kind(self, kind: int) -> None:
        self.kind = kind
        if kind in self.PIECES:
            self.top_piece, self.bottom_piece, self.extend_piece, self.middle = self.PIECES[kind]
            self.small = self.SMALL[kind]
        elif 0 <= kind < 0x15:
            self.top_piece = self.bottom_piece = self.extend_piece = 0
            self.middle = self.small = self.SINGLE.get(kind, 0)

    def set_char(self, ch: int) -> None:
        if ch in (0x20, 0x2E):
            self.set_kind(0)
        elif ch in self.CHAR_KIND:
            self.set_kind(self.CHAR_KIND[ch])
        else:
            self.kind = 0x13
            self.middle = self.small = ch

    def measure(self, target: int, size: int) -> None:
        ctx = self.ctx
        ctx.set_font(size)
        self.size = size
        self.target = target or ctx.size
        if self.kind == 0x13:
            if self.middle:
                self.whole_w, self.middle_h = ctx.measure(self.middle)
        else:
            if self.top_piece:
                self.top_h = ctx.plain(self.top_piece)[1]
            if self.bottom_piece:
                self.bottom_h = ctx.plain(self.bottom_piece)[1]
            if self.middle:
                self.whole_w, self.middle_h = ctx.plain(self.middle)
            if self.middle in (0x2329, 0x232A):
                self.whole_w = cdiv(self.whole_w * 2, 3)
        if self.kind in (1, 2, 9, 10):
            self.least = self.middle_h + self.bottom_h + self.top_h
        elif self.kind in (3, 0xB):
            self.least = self.bottom_h + self.top_h
        else:
            self.least = self.middle_h

    def draw(self) -> None:
        """FUN_100119e0: 낮으면 한 글자를 괄호 높이로 늘려 찍고, 높으면 위·아래 조각 사이를 가운데 조각으로 채운다."""
        ctx = self.ctx
        ctx.set_font(self.size)
        s = ctx.size
        x, y, kind, target = self.x, self.y, self.kind, self.target
        if target < self.least:
            ctx.set_extent(target, ctx.half)
            if kind in (4, 0xC):
                x -= cdiv(s, 8)
            elif kind in (3, 0xB):
                x += cdiv(s, 8)
            ctx.draw_char(x, y, self.small)
            return
        weight, ctx.bold, ctx.weight = ctx.weight, 0, 400
        half = ctx.half
        if kind in (1, 9):
            rest = self.middle_h - self.least + target
            ctx.draw_char(x, y, self.top_piece)
            ctx.draw_char(x, self.top_h + y + rest, self.bottom_piece)
            top_cut = muldiv(self.top_h, 5, 100)
            # 가운데 조각 글리프는 em의 9.8%를 덮는다. 글꼴 높이 50000마다 4900씩 이어 찍는다
            length = int((muldiv(self.bottom_h, 5, 100) + top_cut + rest) / 0.098)
            offset = 0
            while length > 0:
                ctx.set_extent(min(length, 50000), half)
                ctx.draw_char(x, self.top_h - top_cut + y + offset, self.middle)
                offset += 0x1324
                length -= 50000
        elif kind in (2, 10):
            cut = muldiv(ctx.size, 5, 100)
            half_gap = cdiv(target - self.least, 2)
            rise = int(half_gap * 0.26 / 0.48)
            ctx.draw_char(x, y, self.top_piece)
            ctx.draw_char(x, half_gap + y + self.top_h, self.middle)
            ctx.draw_char(x, y + half_gap * 2 + self.top_h + self.middle_h, self.bottom_piece)
            stretch = cut * 2 - int(half_gap / -0.48)
            ctx.set_extent(stretch, half)
            ctx.draw_char(x, y + self.top_h - rise - cut, self.extend_piece)
            ctx.set_extent(stretch, half)
            ctx.draw_char(x, y - rise - cut + self.top_h + self.middle_h + half_gap, self.extend_piece)
        elif kind in (3, 0xB):
            rest = target - self.least
            ctx.draw_char(x, y, self.top_piece)
            ctx.draw_char(x, self.top_h + y + rest, self.bottom_piece)
            top_cut = muldiv(self.top_h, 80, 100)
            ctx.set_extent(muldiv(self.bottom_h, 5, 100) + top_cut + rest, half)
            ctx.draw_char(x, self.top_h - top_cut + y, self.middle)
        elif kind in (4, 0xC):
            ctx.set_extent(target, half if half * 6 > target else cdiv(target, 6))
            ctx.draw_char(x - cdiv(s, 8), y, self.small)
        else:
            ctx.set_extent(target, half)
            ctx.draw_char(x, y, self.small)
        ctx.weight, ctx.bold = weight, int(weight == 700)

    def width(self) -> int:
        if self.kind in (0x13, 4, 0xC):
            return max(self.whole_w, cdiv(self.target, 4))
        return self.whole_w


class Bracket(Obj):
    """LEFT·RIGHT(0x69)와 고정 괄호 쌍(FUN_1001f7d0, CalcSize FUN_10020010). 본문 하나, 높이는 본문 그대로."""

    def __init__(self, ctx: Context, kind: int) -> None:
        super().__init__(ctx)
        self.kind = kind
        self.body = self.new_row()
        self.rows = [self.body]
        self.left = Delimiter(ctx)
        self.right = Delimiter(ctx)

    def calc(self, cache: int) -> tuple[int, int]:
        if self.cached(cache):
            return self.w, self.h
        self.dirty = 0
        self.inherit()
        self.body.size = self.size
        body_w, body_h = self.body.calc(cache)
        self.left.measure(body_h, self.size)
        self.right.measure(body_h, self.size)
        self.w = self.left.width() + self.right.width() + body_w
        self.h = body_h
        self.top = self.body.top
        return self.w, self.h

    def draw(self) -> None:
        """FUN_1001fdf0."""
        x, oy = self.origin()
        top = oy + self.body.top
        self.left.x, self.left.y = x, top
        self.left.draw()
        self.body.ox, self.body.oy = self.left.width() + x, oy
        self.body.draw()
        self.right.x, self.right.y = self.body.w + self.left.width() + x, top
        self.right.draw()
        self.ctx.set_font(self.size)


class Accent(Obj):
    """악센트(0x17~0x25·0x4e; ACUTE FUN_1001ad10, BAR FUN_10019890, VEC FUN_100192e0, BOX FUN_1001a820 등)."""

    GLYPHS = {
        0x17: 0xE05D,
        0x18: 0xE05E,
        0x19: 0xE05F,
        0x1A: 0xE060,
        0x1B: 0xE06D,
        0x1C: 0xE06D,
        0x1D: 0xE06D,
        0x1E: 0xE06D,
        0x22: 0xE063,
        0x23: 0xE064,
        0x24: 0xE065,
        0x25: 0xE066,
        0x4E: 0xE06D,
    }

    def __init__(self, ctx: Context, kind: int) -> None:
        super().__init__(ctx)
        self.kind = kind
        self.body = self.new_row()
        self.rows = [self.body]

    def calc(self, cache: int) -> tuple[int, int]:
        if self.cached(cache):
            return self.w, self.h
        self.dirty = 0
        self.inherit()
        ctx = self.ctx
        ctx.set_font(self.size)
        glyph_w, glyph_h = ctx.plain(self.GLYPHS[self.kind])
        extra = muldiv(glyph_h, 20, 100)
        self.glyph_w, self.extra = glyph_w, extra  # +0x80, +0x84
        self.body.size = self.size
        body_w, body_h = self.body.calc(cache)
        kind = self.kind
        # 폭: VEC는 max(본문 + 글리프 폭 20%, 글리프), BAR·DYAD·UNDER·BOX는 max(글리프, 본문) + 글리프 폭 20%
        if kind == 0x1C:
            width = max(body_w + muldiv(glyph_w, 20, 100), glyph_w)
        elif kind in (0x1B, 0x1D, 0x1E, 0x4E):
            width = max(glyph_w, body_w) + muldiv(glyph_w, 20, 100)
        else:
            width = max(glyph_w, body_w)
        height = body_h + extra
        if kind == 0x24:
            height += cdiv(extra, 3)
        if kind == 0x1E:
            top = self.body.top
        elif kind == 0x4E:
            top = self.body.top - muldiv(glyph_w, 15, 100)
        else:
            top = self.body.top - extra
        self.w, self.h, self.top = width, height, top
        return self.w, self.h

    def _capital_end(self, item: Obj | None) -> bool:
        """기울인 대문자(E000~E019, 글꼴 기울임 +0x0e)인 글자인가. 판정하며 +0x0e를 0·1로 줄인다."""
        if not (isinstance(item, Glyph) and 0xE000 <= item.code <= 0xE019):
            return False
        self.ctx.top_flag &= 1
        return bool(self.ctx.top_flag)

    def draw(self) -> None:
        """BAR FUN_10019ca0, VEC FUN_10019140·FUN_10019430, BOX FUN_1001a520. 막대 U+E06D, 화살촉 U+E06E."""
        ctx = self.ctx
        x, oy = self.origin()
        if self.kind == 0x1B:
            leaning = self._capital_end(self.body.head.next)
            ctx.set_font(self.size)
            lift = 0 if is_flat(self.body) else cdiv(self.extra, 2)
            ctx.set_extent(ctx.size, self.body.w)
            ctx.draw_char(x + cdiv(self.size, 4 if leaning else 20), self.top - lift + oy, 0xE06D)
        elif self.kind == 0x1C:
            first = self._capital_end(self.body.head.next)
            last = self._capital_end(self.body.end.prev)
            y = oy - self.extra + self.top
            span = self.body.w - (cdiv(self.size, 4) if first and not last else 0)
            length = span - muldiv(self.glyph_w, 55, 100)
            offset = cdiv(self.size, 5 if first else 40)
            if length >= 0:
                ctx.set_font(self.size)
                ctx.set_extent(ctx.size, span)
                ctx.draw_char(offset + x, y, 0xE06D)
                ctx.set_font(self.size)
                offset += length
            # 짧은 본문이면 화살촉만 직전 글꼴 상태 그대로 찍는다(FUN_10019430은 글꼴을 다시 정하지 않는다)
            ctx.draw_char(offset + x, y, 0xE06E)
        elif self.kind == 0x4E:
            # BOX(FUN_1001a520): 흰 붓으로 상자를 칠하고 선택된 기본 펜으로 테두리를 그린 뒤 본문을 찍는다
            ctx.set_font(self.size)
            ctx.drawn.append(DrawnBox(x, self.top + oy, self.w, self.h))
        else:
            raise NotImplementedError(f"수식 악센트 0x{self.kind:X} 그리기는 아직 옮기지 않았습니다")
        ctx.set_font(self.size)
        self.body.ox, self.body.oy = cdiv(self.w, 2) + x - cdiv(self.body.w, 2), oy
        self.body.draw()
        ctx.set_font(self.size)


class Wrapper(Obj):
    """본문 상자를 그대로 쓰는 객체: NOT(0x4b, FUN_1001e180), PHANTOM(0x6e, FUN_10018d60), COLOR(0x75, FUN_1001f4c0)."""

    def __init__(self, ctx: Context, kind: int) -> None:
        super().__init__(ctx)
        self.kind = kind
        self.body = self.new_row()
        # COLOR는 첫 줄(+0x78)이 색 지정이고 둘째 줄(+0x7c)이 본문이다
        self.rows = [self.new_row(), self.body] if kind == 0x75 else [self.body]

    def calc(self, cache: int) -> tuple[int, int]:
        self.inherit()
        if self.cached(cache):
            return self.w, self.h
        self.dirty = 0
        self.inherit()
        self.body.size = self.size
        self.w, self.h = self.body.calc(cache)
        self.top = self.body.top
        return self.w, self.h

    def draw(self) -> None:
        """NOT FUN_1001dfb0(본문 위에 사선 U+E054를 상자 크기로 늘려 찍는다), PHANTOM FUN_10018c40(그리지 않는다)."""
        if self.kind == 0x6E:
            return
        if self.kind == 0x75:
            self.draw_color()
            return
        ctx = self.ctx
        x, oy = self.origin()
        self.body.ox, self.body.oy = x, oy
        self.body.draw()
        first = self.body.head.next
        width = self.w
        if not (isinstance(first, Glyph) and first.code in (0xE047, 0xE0E5)):
            width = muldiv(width, 0x78, 100)
        ctx.set_extent(self.h, width)
        ctx.draw_char(x, self.top + oy, 0xE054)
        ctx.set_font(self.size)

    def draw_color(self) -> None:
        """FUN_1001f2a0: 첫 줄 글에서 R·G·B 값을 차례로 읽어 글자 색을 바꾸고 본문을 찍은 뒤 되돌린다."""
        ctx = self.ctx
        x, oy = self.origin()
        text = "".join(item.name for item in self.rows[0].items())
        red, rest = _color_value(text)
        green, rest = _color_value(rest)
        blue, _ = _color_value(rest)
        previous, ctx.text_color = ctx.text_color, (blue << 8 | green) << 8 | red
        self.body.ox, self.body.oy = x, oy
        self.body.draw()
        ctx.text_color = previous
        ctx.set_font(self.size)


def _color_value(text: str) -> tuple[int, str]:
    """FUN_1001f540: 숫자를 모으다 숫자 뒤 첫 비숫자에서 멈춘다. 네 자리 이상은 255, 값은 0~255. (값, 남은 글)."""
    digits = ""
    while text:
        ch, text = text[0], text[1:]
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    if not digits:
        return 0, text
    return (255 if len(digits) > 3 else min(int(digits), 255)), text


class Limit(Obj):
    """lim·Lim(7·8, FUN_10015b60·FUN_100161e0). +0x78 = 아래 첨자, +0x7c = 오른쪽 본문."""

    def __init__(self, ctx: Context, kind: int) -> None:
        super().__init__(ctx)
        self.kind = kind
        self.capital = 1 if kind == 8 else 0  # +0x80
        self.scale = 0x78  # +0x84
        self.lower = self.new_row()
        self.body = self.new_row()
        self.rows = [self.lower, self.body]

    def text_size(self) -> tuple[int, int]:
        """FUN_10016310: 'lim'(또는 'Lim') 세 글자 폭의 합과 첫 글자 높이."""
        ctx = self.ctx
        ctx.top_flag &= 1
        if ctx.top_flag:
            ctx.italic = 0
        ctx.set_font(muldiv(self.size, self.scale, 100))
        width, height = ctx.plain(0x4C if self.capital == 1 else 0x6C)
        width += ctx.plain(0x69)[0] + ctx.plain(0x6D)[0]
        if ctx.top_flag:
            ctx.italic = 1
        return width, height

    def calc(self, cache: int) -> tuple[int, int]:
        if self.cached(cache):
            return self.w, self.h
        self.dirty = 0
        self.inherit()
        small = muldiv(self.size, 0x44, 100)
        text_w, text_h = self.text_size()
        self.text_w, self.text_h = text_w, text_h  # +0x88, +0x8c
        self.lower.size = small
        lower_w, _ = self.lower.calc(cache)
        self.body.size = self.size
        body_w, body_h = self.body.calc(cache)
        top = min(-cdiv(text_h, 2), self.body.top)
        bottom = max(body_h + self.body.top, self.lower.h + cdiv(text_h, 2))
        self.w = body_w + max(text_w, lower_w)
        self.top, self.h = top, bottom - top
        return self.w, self.h

    def draw(self) -> None:
        """FUN_10015f20: 'lim'은 글자열로 찍는다(PUA로 바꾸지 않는다)."""
        ctx = self.ctx
        upright = ctx.top_flag & 1
        ctx.top_flag = upright
        if upright:
            ctx.italic = 0
        ctx.set_font(muldiv(self.size, self.scale, 100))
        x, oy = self.origin()
        left = cdiv(self.w - self.body.w, 2) + x
        ctx.draw_text(left - cdiv(self.text_w, 2), oy - cdiv(self.text_h, 2), "Lim" if self.capital == 1 else "lim")
        if upright:
            ctx.italic = 1
        self.lower.ox = left - cdiv(self.lower.w, 2)
        self.lower.oy = cdiv(self.text_h, 2) - self.lower.top + oy
        self.lower.draw()
        self.body.ox, self.body.oy = self.w - self.body.w + x, oy
        self.body.draw()
        ctx.set_font(self.size)


class Integral(Obj):
    """적분(2, 0x2b~0x2f; FUN_10012df0·FUN_10013520). +0x78 = 아래, +0x7c = 위, +0x80 = 본문."""

    GLYPHS = {2: 0xE05B, 0x2B: 0xE073, 0x2C: 0xE074, 0x2D: 0xE075, 0x2E: 0xE076, 0x2F: 0xE077}

    #: FUN_10012f90은 변형(DINT·OINT 등)과 상관없이 종류를 2로 쓴다. 빈 위아래 줄이 크기 0이 되는 조건에 쓰인다.
    kind = 2

    def __init__(self, ctx: Context, kind: int) -> None:
        super().__init__(ctx)
        self.glyph = self.GLYPHS[kind]
        self.scale = 200  # +0xa0
        self.lower = self.new_row()
        self.upper = self.new_row()
        self.body = self.new_row()
        self.rows = [self.lower, self.upper, self.body]

    def calc(self, cache: int) -> tuple[int, int]:
        if self.cached(cache):
            return self.w, self.h
        self.dirty = 0
        self.inherit()
        ctx = self.ctx
        small = muldiv(self.size, 0x44, 100)
        ctx.set_font(muldiv(self.size, self.scale, 100))
        glyph_w, glyph_h = ctx.plain(self.glyph)
        self.lower.size = small
        lower_w, lower_h = self.lower.calc(cache)
        self.upper.size = small
        upper_w, _ = self.upper.calc(cache)
        self.body.size = self.size
        body_w, body_h = self.body.calc(cache)
        ctx.set_font(self.size)
        s = ctx.size
        right_gap = muldiv(s, 5, 100)
        tail = muldiv(s, 1, 100)
        lower_shift = muldiv(s, -50, 100)
        lower_y = cdiv(glyph_h, 2)
        upper_y = cdiv(muldiv(s, 0x44, 100), 6) - cdiv(glyph_h, 2)
        if self.lower.top + lower_y < 0:
            lower_y -= self.lower.top + lower_y
        upper_bottom = self.upper.h + upper_y + self.upper.top
        if upper_bottom > 0:
            upper_y -= upper_bottom
        top = min(self.upper.top + upper_y, self.body.top)
        bottom = max(self.lower.top + lower_y + lower_h, self.body.top + body_h)
        self.glyph_w, self.glyph_h = glyph_w, glyph_h  # +0xa4, +0xa8
        self.lower_shift, self.right_gap, self.tail = lower_shift, right_gap, tail  # +0xac, +0xb0, +0xb4
        self.lower_y, self.upper_y = lower_y, upper_y  # +0xb8, +0xbc
        self.w = max(lower_w + lower_shift, upper_w + right_gap) + body_w + tail + glyph_w
        self.top, self.h = top, bottom - top
        return self.w, self.h

    def draw(self) -> None:
        """FUN_100132d0."""
        ctx = self.ctx
        x, oy = self.origin()
        ctx.set_font(muldiv(self.size, self.scale, 100))
        ctx.draw_char(x, oy - cdiv(self.glyph_h, 2), self.glyph)
        right = self.glyph_w + x
        self.lower.ox, self.lower.oy = self.lower_shift + right, self.lower_y + oy
        self.lower.draw()
        self.upper.ox, self.upper.oy = self.right_gap + right, self.upper_y + oy
        self.upper.draw()
        limits = max(self.lower_shift + self.lower.w, self.right_gap + self.upper.w)
        self.body.ox, self.body.oy = self.tail + limits + right, oy
        self.body.draw()


class Grid(Obj):
    """PILE류(0x10~0x12, FUN_1000a530)와 괄호 행렬(9~0xc·0x13, FUN_10008d00). 칸은 행 우선 순서의 줄이다."""

    #: 행렬 종류 → (왼쪽, 오른쪽) 괄호 종류(FUN_10008490).
    DELIMITERS = {9: (0, 0), 10: (1, 9), 11: (3, 0xB), 12: (5, 0xD), 0x13: (2, 0)}

    def __init__(self, ctx: Context, kind: int, rows: int, cols: int) -> None:
        super().__init__(ctx)
        self.kind = kind
        if rows * cols == 0 and (kind in self.DELIMITERS or kind in (0x73, 0x74)):
            # 괄호 행렬·LADDER의 칸 생성(FUN_100077a0)은 빈 격자를 1행 1열로 만든다. PILE류(FUN_10009bc0)는 그대로 둔다
            rows = cols = 1
        self.nrows = rows  # +0x78
        self.ncols = cols  # +0x7c
        self.rows = [self.new_row() for _ in range(max(rows * cols, 1))]
        self.left = self.right = None
        if kind in self.DELIMITERS:
            self.left, self.right = Delimiter(ctx), Delimiter(ctx)
            left, right = self.DELIMITERS[kind]
            self.left.set_kind(left)
            self.right.set_kind(right)

    def calc(self, cache: int) -> tuple[int, int]:
        if self.cached(cache):
            return self.w, self.h
        self.dirty = 0
        self.inherit()
        s = self.size
        col_w = [0] * self.ncols
        row_h = [0] * self.nrows
        for i, cell in enumerate(self.rows):
            cell.size = s
            cell_w, cell_h = cell.calc(cache)
            r, c = divmod(i, self.ncols) if self.ncols else (0, 0)
            if r < self.nrows and c < self.ncols:
                col_w[c] = max(col_w[c], cell_w)
                row_h[r] = max(row_h[r], cell_h)
        height = sum(row_h) + max(self.nrows - 1, 0) * muldiv(s, 15, 100)
        width = sum(col_w) + max(self.ncols - 1, 0) * muldiv(s, 20, 100)
        if self.left is not None:
            self.left.measure(height, s)
            self.right.measure(height, s)
            width += self.left.width() + self.right.width()
        self.col_w, self.row_h = col_w, row_h  # +0x84, +0x80
        self.w, self.h, self.top = width, height, -cdiv(height, 2)
        return self.w, self.h

    def draw(self) -> None:
        """PILE류 FUN_1000a2d0(칸은 행 위 끝에 붙인다), 괄호 행렬 FUN_10008a00(칸은 행 가운데, cases만 왼쪽 맞춤)."""
        if type(self) is not Grid:
            raise NotImplementedError(f"수식 격자 0x{self.kind:X} 그리기는 아직 옮기지 않았습니다")
        ctx = self.ctx
        x, oy = self.origin()
        s = self.size
        col_gap, row_gap = muldiv(s, 20, 100), muldiv(s, 15, 100)
        top = oy - cdiv(self.h, 2)
        if self.left is None:
            align = {0x11: 1, 0x12: 2}.get(self.kind, 0)
            left = x
        else:
            align = 1 if self.kind == 0x13 else 0
            self.left.x, self.left.y = x, top
            self.right.x, self.right.y = self.w - self.right.width() + x, top
            self.left.draw()
            self.right.draw()
            left = self.left.width() + x
        row_top = 0
        for r in range(self.nrows):
            cell_x = left
            for c in range(self.ncols):
                cell = self.rows[r * self.ncols + c]
                if align == 1:
                    cell.ox = cell_x
                elif align == 2:
                    cell.ox = self.col_w[c] - cell.w + cell_x
                elif self.left is None:
                    cell.ox = cdiv(self.col_w[c], 2) - cdiv(cell.w, 2) + cell_x
                else:
                    cell.ox = cdiv(self.col_w[c] - cell.w, 2) + cell_x
                if self.left is None:
                    cell.oy = row_top - cell.top + top
                else:
                    cell.oy = cdiv(self.row_h[r] - cell.h, 2) - cell.top + top + row_top
                cell.draw()
                cell_x += col_gap + self.col_w[c]
            row_top += row_gap + self.row_h[r]
        ctx.set_font(s)


class Ladder(Grid):
    """LADDER(0x73)·SLADDER(0x74), FUN_10009480. 마지막 줄은 한 칸 오른쪽에서 시작한다."""

    def __init__(self, ctx: Context, kind: int, rows: int, cols: int) -> None:
        super().__init__(ctx, kind, rows, cols)

    def calc(self, cache: int) -> tuple[int, int]:
        if self.cached(cache):
            return self.w, self.h
        self.dirty = 0
        self.inherit()
        s = self.size
        ctx = self.ctx
        ctx.set_font(s)
        # FUN_100097a0: 사다리 모양 글리프 U+E101의 폭과 높이의 20%
        corner, corner_h = ctx.plain(0xE101)
        self.corner_h = muldiv(corner_h, 20, 100)  # +0x8c
        dots = ctx.plain(0x22EF)[0] + muldiv(s, 20, 100) if self.kind == 0x74 else 0
        self.corner_w, self.dots_w = corner, dots  # +0x88, +0x94
        col_w = [0] * self.ncols
        self.row_h = []
        width = height = 0
        for r in range(self.nrows):
            row_h = 0
            column = 1 if r == self.nrows - 1 and r > 0 else 0
            x = 0
            for c in range(self.ncols):
                x += muldiv(s, 20, 100)
                start = x
                if c == self.ncols - 1:
                    if r == self.nrows - 1 and r > 0:
                        x = col_w[0] + start
                        break
                    if self.kind == 0x74 and r == 0 and self.ncols > 2:
                        break
                if column == self.ncols:
                    break
                cell = self.rows[r * self.ncols + c]
                cell.size = s
                cell_w, cell_h = cell.calc(cache)
                row_h = max(row_h, cell_h)
                col_w[column] = max(col_w[column], cell_w)
                x = col_w[column] + start
                column += 1
            width = max(width, x)
            height += muldiv(s, 15, 100) + row_h
            self.row_h.append(row_h)
        if self.ncols > 1:
            col_w[0] += corner
            width += corner
        if self.kind == 0x74 and self.ncols > 2 and self.nrows > 1:
            col_w[-1] += dots
            width += dots
        self.col_w = col_w
        self.w, self.h, self.top = width, height, -cdiv(height, 2)
        return self.w, self.h

    def draw(self) -> None:
        """FUN_10009170. 마지막 줄 칸은 한 열 오른쪽에 놓는다."""
        x, oy = self.origin()
        s, ncols, last = self.size, self.ncols, self.nrows - 1
        sladder = self.kind == 0x74 and ncols > 2
        row_top = oy - cdiv(self.h, 2)
        for r in range(self.nrows):
            if ncols > 1:
                self._draw_corner(x, oy, r)
            left = shifted = x
            for c in range(ncols):
                if c == ncols - 1 and ((r == last and r > 0) or (sladder and r == 0)):
                    break
                cell = self.rows[r * ncols + c]
                if r == last and r > 0:
                    shifted += muldiv(s, 20, 100) + self.col_w[c]
                    if sladder and c == ncols - 2:
                        shifted += cdiv(self.dots_w, 2)
                    cell.ox = cdiv(self.col_w[c + 1] - cell.w, 2) + shifted
                else:
                    if sladder and c == ncols - 1:
                        left += cdiv(self.dots_w, 2)
                    cell.ox = cdiv(self.col_w[c] - cell.w, 2) + left
                left += muldiv(s, 20, 100) + self.col_w[c]
                cell.oy = cdiv(self.row_h[r] - cell.h, 2) - cell.top + row_top
                cell.draw()
            row_top += self.row_h[r] + muldiv(s, 15, 100)
        self.ctx.set_font(s)

    def _draw_corner(self, x: int, oy: int, row: int) -> None:
        """FUN_10009850: 행 왼쪽 세로 모서리(U+E101)와 행 아래 가로선(U+E06D), SLADDER의 ⋯."""
        ctx = self.ctx
        ctx.set_font(self.size)
        s20, s15 = muldiv(self.size, 20, 100), muldiv(self.size, 15, 100)
        s2, s6 = muldiv(self.size, 2, 100), muldiv(self.size, 6, 100)
        left = x + self.col_w[0]
        bottom = oy + self.top + sum(h + s15 for h in self.row_h[: row + 1])
        bar = self.w - s20 - self.col_w[0] + s2
        if self.kind == 0x74 and self.ncols > 2:
            dots_x = bar - self.col_w[-1]
            bar = dots_x - s20
            if row > 0:
                ctx.draw_char(left + dots_x, bottom - self.row_h[row] - s15, 0x22EF)
        if row != self.nrows - 1 or row < 1:
            ctx.set_extent(cdiv(s15, 2) + self.row_h[row], ctx.half)
            ctx.draw_char(left - cdiv(self.corner_w, 2), bottom - self.row_h[row] - s15, 0xE101)
            ctx.set_extent(muldiv(self.size, 125, 100), bar)
            ctx.draw_char(left - s2, -self.corner_h - cdiv(s15, 2) - s6 + bottom, 0xE06D)
            ctx.set_font(self.size)


class LoneDelimiter(Obj):
    """짝 없는 RIGHT(0x6a, FUN_10020170·FUN_10020800). 줄의 앞 내용을 본문으로 감싸고 오른쪽 괄호 하나를 붙인다."""

    def __init__(self, ctx: Context, kind: int) -> None:
        super().__init__(ctx)
        self.kind = kind
        self.body = self.new_row()
        self.rows = [self.body]
        self.delimiter = Delimiter(ctx)
        self.delimiter.set_kind(0x13)

    def calc(self, cache: int) -> tuple[int, int]:
        if self.cached(cache):
            return self.w, self.h
        self.dirty = 0
        self.inherit()
        self.body.size = self.size
        body_w, body_h = self.body.calc(cache)
        self.delimiter.measure(body_h, self.size)
        self.w = self.delimiter.width() + body_w
        self.h = body_h
        self.top = self.body.top
        return self.w, self.h

    def draw(self) -> None:
        """FUN_10020600: 종류 0x36 이상(RIGHT)은 본문 뒤에 괄호를 찍는다."""
        x, oy = self.origin()
        self.body.ox, self.body.oy = x, oy
        self.body.draw()
        self.delimiter.x, self.delimiter.y = self.body.w + x, self.body.top + oy
        self.delimiter.draw()
        self.ctx.set_font(self.size)


class Bold(Obj):
    """BOLD(0x21, FUN_1001d580). 본문을 굵게(가중치 700) 측정하고 상자는 본문 그대로다."""

    kind = 0x21

    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.body = self.new_row()
        self.rows = [self.body]

    def calc(self, cache: int) -> tuple[int, int]:
        self.inherit()
        if self.cached(cache):
            return self.w, self.h
        self.dirty = 0
        self.inherit()
        self.body.size = self.size
        self.ctx.bold = 1
        try:
            self.w, self.h = self.body.calc(cache)
        finally:
            self.ctx.bold = 0
        self.top = self.body.top
        return self.w, self.h

    def draw(self) -> None:
        """FUN_1001d400."""
        ctx = self.ctx
        self.body.ox, self.body.oy = self.origin()
        ctx.bold, ctx.weight = 1, 700
        self.body.draw()
        ctx.bold, ctx.weight = 0, 400
        ctx.set_font(self.size)


class UnderOver(Obj):
    """UNDEROVER와 SUM 밖 큰 연산자(0x42, FUN_10017930·FUN_10017f30). +0x80 = 가운데, +0x78 = 아래, +0x7c = 위."""

    kind = 0x42
    #: 가운데 첫 글자가 이 글자면 180%로 키우고 높이의 75%만 쓴다.
    LARGE = frozenset((0x2294, 0x2293, 0x228E, 0x2295, 0x2296, 0x2297, 0x2298, 0x2299, 0x2228, 0x2227))

    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.center = self.new_row()
        self.lower = self.new_row()
        self.upper = self.new_row()
        self.rows = [self.center, self.lower, self.upper]

    def calc(self, cache: int) -> tuple[int, int]:
        if self.cached(cache):
            return self.w, self.h
        self.dirty = 0
        self.inherit()
        small = muldiv(self.size, 0x44, 100)
        first = self.center.head.next
        large = isinstance(first, Glyph) and first.code in self.LARGE
        self.center.size = muldiv(self.size, 180, 100) if large else self.size
        center_w, center_h = self.center.calc(cache)
        core = muldiv(center_h, 75, 100) if large else center_h
        self.lower.size = small
        lower_w, lower_h = self.lower.calc(cache)
        self.upper.size = small
        upper_w, upper_h = self.upper.calc(cache)
        self.w = max(center_w, lower_w, upper_w)
        self.h = lower_h + upper_h + core
        center_top = muldiv(self.center.top, 75, 100) if large else self.center.top
        self.top = center_top - upper_h
        return self.w, self.h

    def draw(self) -> None:
        """FUN_10017d50: 세 줄을 가운데 맞춤으로 아래 줄은 상자 아래 끝, 위 줄은 위 끝, 가운데 줄은 축에 둔다."""
        x, oy = self.origin()
        center = cdiv(self.w, 2) + x
        self.lower.ox = center - cdiv(self.lower.w, 2)
        self.lower.oy = oy + self.top - self.lower.h - self.lower.top + self.h
        self.lower.draw()
        self.upper.ox = center - cdiv(self.upper.w, 2)
        self.upper.oy = oy + self.top - self.upper.top
        self.upper.draw()
        self.center.ox = center - cdiv(self.center.w, 2)
        self.center.oy = oy
        self.center.draw()


class Bigg(Obj):
    """BIGG(0x47, FUN_10020da0). 앞 형제들 중 가장 큰 높이를 쓰고 폭은 글자 하나의 폭이다."""

    kind = 0x47

    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.body = self.new_row()
        self.rows = [self.body]

    def calc(self, cache: int) -> tuple[int, int]:
        if self.cached(cache):
            return self.w, self.h
        self.dirty = 0
        self.inherit()
        # 높이 시작값은 직전 계산이 남긴 글꼴 크기(+0x848)다
        height = self.ctx.size
        item = self.prev
        while item is not None:
            before = item.prev
            if (before is None or before.kind != self.kind) and height <= item.h:
                height = item.h
            item = before
        first = self.body.head.next
        if first.kind == 0x6F:
            first = first.next
        while first is not self.body.end:
            after = first.next
            first.prev.next = after
            after.prev = first.prev
            first = after
        self.body.size = self.size
        self.w, _ = self.body.calc(cache)
        self.h, self.top = height, -cdiv(height, 2)
        return self.w, self.h

    def draw(self) -> None:
        """FUN_10020b30: 본문 첫 글자를 상자 높이·폭으로 늘려 찍는다(글자가 아니면 테두리 상자).

        +0x80 괄호 객체는 생성(FUN_10020910)이 0으로 두고 채우는 곳을 찾지 못해 없는 경로만 옮겼다.
        BIGG는 다음 글자를 괄호 글자로 받아 테두리 상자 경로는 시험으로 만들지 못했다(검증 안 됨).
        글꼴은 되돌리지 않는다.
        """
        ctx = self.ctx
        x, oy = self.origin()
        self.body.oy = oy
        ctx.set_extent(self.h, self.w)
        first = self.body.head.next
        if isinstance(first, Glyph):
            ctx.draw_char(x, self.top + oy, first.code)
        else:
            ctx.drawn.append(DrawnBox(x, self.top + oy, self.w, self.h, filled=False))


class Relation(Obj):
    """REL(0x4D)·BUILDREL(5), FUN_100174a0. +0x7c = 화살표 위, +0x78 = 화살표 아래.

    아래 줄을 건너뛰게 하는 +0x90은 쓰는 곳을 확인하지 못해 늘 0으로 본다(재저장본 크기 시험 99/99는 0과 맞다).
    """

    #: 조각으로 잇는 화살표 → 모양 번호(0x10017683 분기). 그 밖의 글자는 글꼴 글자로 측정한다.
    DRAWN = {0x2192: 0, 0x2190: 1, 0x2194: 2, 0x21D2: 3, 0x21D0: 4, 0x21D4: 5, 0x21C4: 6}

    #: FUN_10016f20이 종류를 0으로 써 두고 FUN_1000dab0도 다시 쓰지 않는다. 그래서 빈 줄도 크기를 가진다.
    kind = 0

    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.name = "REL "  # FUN_10016f20
        self.arrow = 0  # +0x8c, FUN_100466f0
        self.upper = self.new_row()  # +0x7c
        self.lower = self.new_row()  # +0x78
        self.rows = [self.upper, self.lower]

    def set_arrow(self, ch: int) -> None:
        """FUN_100466f0: 화살표 글자를 정하고 이름 뒤에 글자 이름을 붙인다."""
        self.arrow = ch
        self.name += char_name(ch)

    def calc(self, cache: int) -> tuple[int, int]:
        if self.cached(cache):
            return self.w, self.h
        self.dirty = 0
        self.inherit()
        ctx = self.ctx
        s = self.size
        self.lower.size = s
        lower_w, lower_h = self.lower.calc(cache)
        self.upper.size = s
        upper_w, upper_h = self.upper.calc(cache)
        ctx.set_font(s)
        width = max(lower_w, upper_w) + muldiv(ctx.size, 50, 100)
        if self.arrow == 0:
            self.arrow = 0x21D4
        self.shape = shape = self.DRAWN.get(self.arrow)
        if shape is None:
            self.arrow_w, arrow_h = ctx.plain(self.arrow)  # +0x84, +0x88
        else:
            # 화살표 객체(+0x94, 0x34바이트): 왼쪽·오른쪽·가운데 이음 조각과 그 크기(0x100176e0~0x100177c0)
            self.left_piece, self.right_piece, self.middle_piece = (
                (0xE058, 0xE05A, 0xE059) if shape in (0, 1, 2, 6) else (0xE06F, 0xE071, 0xE070)
            )
            self.arrow_span = width  # +0x10
            ctx.set_font(s)
            self.middle_w, self.middle_h = ctx.plain(self.middle_piece)  # +0x24, +0x28
            right_w, self.right_h = ctx.plain(self.right_piece)  # +0x2c, +0x30
            self.right_w = muldiv(right_w, 80, 100)
            # 가운데 조각을 늘려 찍는 가장 짧은 폭(+0x14). 양쪽 화살표는 1.5배
            self.least = muldiv(self.right_w, 150, 100) if shape in (2, 5) else self.right_w
            # 0x10017718: 가운데 조각 높이의 35%가 화살표 두께, 모양 6(⇄)은 두 배
            margin = muldiv(self.middle_h, 5, 100)
            self.arrow_w = max(self.arrow_span, self.least) + margin
            arrow_h = muldiv(self.middle_h, 30, 100) + margin
            if shape == 6:
                arrow_h *= 2
        self.arrow_h = arrow_h
        self.w = width
        self.h = arrow_h + upper_h + lower_h
        self.top = -upper_h - cdiv(arrow_h, 2)
        return self.w, self.h

    def draw(self) -> None:
        """FUN_10017230: 아래·위 줄을 가운데 맞춰 찍고 화살표를 축에 둔다."""
        ctx = self.ctx
        x, oy = self.origin()
        center = cdiv(self.w, 2) + x
        self.lower.ox = center - cdiv(self.lower.w, 2)
        self.lower.oy = cdiv(self.arrow_h, 2) - self.lower.top + oy
        self.lower.draw()
        self.upper.ox = center - cdiv(self.upper.w, 2)
        self.upper.oy = self.top - self.upper.top + oy
        self.upper.draw()
        left = center - cdiv(self.arrow_w, 2)
        if self.shape is None:
            ctx.draw_char(left, oy - cdiv(self.arrow_h, 2), self.arrow)
        else:
            self.draw_arrow(left, oy)

    def draw_arrow(self, x: int, y: int) -> None:
        """FUN_10021230: 짧으면 화살촉 조각만, 길면 가운데 조각을 늘려 잇고 화살촉을 양 끝에 찍는다. ⇄는 두 줄이다."""
        ctx = self.ctx
        shape, span = self.shape, self.arrow_span
        x += muldiv(self.middle_h, 3, 100)
        top = y - cdiv(self.right_h, 2) - muldiv(self.middle_h, 5, 100)
        half = cdiv(self.right_w, 2)
        right_x = x - self.right_w + span
        middle_x = self.right_w - half + x
        left_x = x - (self.middle_w - self.right_w)
        gap = cdiv(muldiv(self.middle_h, 30, 100), 2)
        ctx.set_font(self.size)
        stretch = span + (half - self.right_w) * 2 if shape in (2, 5) else span - self.right_w + half
        short = span < self.least
        if shape in (0, 3):
            if short:
                ctx.draw_char(x, top, self.right_piece)
                return
            ctx.set_extent(ctx.size, stretch)
            ctx.draw_char(x, top, self.middle_piece)
            ctx.set_font(self.size)
            ctx.draw_char(right_x, top, self.right_piece)
        elif shape in (1, 4):
            if not short:
                ctx.set_extent(ctx.size, stretch)
                ctx.draw_char(middle_x, top, self.middle_piece)
                ctx.set_font(self.size)
            ctx.draw_char(left_x, top, self.left_piece)
        elif shape in (2, 5):
            if short:
                ctx.draw_char(left_x, top, self.left_piece)
                right_x = half + x
            else:
                ctx.set_extent(ctx.size, stretch)
                ctx.draw_char(middle_x, top, self.middle_piece)
                ctx.set_font(self.size)
                ctx.draw_char(left_x, top, self.left_piece)
            ctx.draw_char(right_x, top, self.right_piece)
        else:
            upper, lower = top - gap, top + gap
            if short:
                ctx.draw_char(left_x, lower, self.left_piece)
                ctx.draw_char(x, upper, self.right_piece)
                return
            ctx.set_extent(ctx.size, stretch)
            ctx.draw_char(middle_x, lower, self.middle_piece)
            ctx.draw_char(x, upper, self.middle_piece)
            ctx.set_font(self.size)
            ctx.draw_char(left_x, lower, self.left_piece)
            ctx.draw_char(right_x, upper, self.right_piece)


#: FUN_10018de0이 위로 솟지 않았다고 보는 PUA 글자(a c e g i j m~z 기울임 등).
_FLAT_CODES = frozenset(
    [0xE0E5, 0xE0E7, 0xE0E9, 0xE0EB, 0xE0ED, 0xE0EE, 0xE01A, 0xE01C, 0xE020, 0xE053]
    + list(range(0xE0F1, 0xE0FF))
    + list(range(0xE026, 0xE034))
    + list(range(0xE09D, 0xE0A4))
    + list(range(0xE0A5, 0xE0B5))
)


def is_flat(line: Line) -> bool:
    """FUN_10018de0: 줄이 위로 솟지 않은 글자와 RM·IT·BOLD 객체만으로 이루어졌는가."""
    for item in line.items():
        if item.kind == 0x6F:
            if item.code not in _FLAT_CODES:
                return False
        elif item.kind not in (0x1F, 0x20, 0x21):
            return False
    return True


class Brace(Obj):
    """OVERBRACE(0x6B)·UNDERBRACE(0x6C), FUN_1001e8d0. 첫 줄은 +0x7c, 둘째 줄은 +0x78이다."""

    #: 괄호 조각 글자 +0xac·+0xae·+0xb0·+0xb2(FUN_1001e250): 왼쪽, 가운데, 이음, 오른쪽.
    PIECES = {0x6B: (0xE106, 0xE107, 0xE108, 0xE109), 0x6C: (0xE10A, 0xE10B, 0xE10C, 0xE10D)}

    def __init__(self, ctx: Context, kind: int) -> None:
        super().__init__(ctx)
        self.kind = kind
        self.first = self.new_row()  # +0x7c
        self.second = self.new_row()  # +0x78
        self.rows = [self.first, self.second]

    def calc(self, cache: int) -> tuple[int, int]:
        if self.cached(cache):
            return self.w, self.h
        self.dirty = 0
        self.inherit()
        ctx = self.ctx
        s = self.size
        under = self.kind == 0x6C
        # FUN_1001ed20: 폭은 +0xac·+0xae·+0xb2 조각 폭의 합, 높이는 +0xae 조각 높이의 35%
        ctx.set_font(s)
        left, middle, _, right = self.PIECES[self.kind]
        self.left_w = ctx.plain(left)[0]  # +0x8c
        self.middle_w, self.middle_h = ctx.plain(middle)  # +0x94, +0x98
        self.brace_w = brace_w = self.middle_w + ctx.plain(right)[0] + self.left_w  # +0x84
        brace_h = muldiv(self.middle_h, 35, 100)
        small = muldiv(s, 0x44, 100)
        self.second.size = s if under else small
        self.first.size = small if under else s
        second_w, second_h = self.second.calc(cache)
        first_w, first_h = self.first.calc(cache)
        gap = muldiv(s, 5 if under else 15, 100) if is_flat(self.first) else 0
        self.w = max(second_w, first_w, brace_w)
        self.h = brace_h - gap + second_h + first_h
        self.top = self.second.top if under else self.first.top - brace_h - second_h + gap
        return self.w, self.h

    def draw(self) -> None:
        """FUN_1001e6e0: 괄호를 찍고 본문 줄은 축, 작은 줄은 위(OVERBRACE) 또는 아래(UNDERBRACE) 끝에 둔다."""
        self.draw_brace()
        x, oy = self.origin()
        center = cdiv(self.w, 2) + x
        self.first.ox = center - cdiv(self.first.w, 2)
        self.second.ox = center - cdiv(self.second.w, 2)
        if self.kind == 0x6C:
            self.second.oy = oy
            self.first.oy = self.h - self.first.h - self.first.top + self.top + oy
        else:
            self.second.oy = self.top - self.second.top + oy
            self.first.oy = oy
        self.first.draw()
        self.second.draw()
        self.ctx.set_font(self.size)

    def draw_brace(self) -> None:
        """FUN_1001ea10: 본문보다 짧으면 왼쪽·가운데·오른쪽 조각만, 길면 사이를 이음 조각 둘을 늘려 채운다."""
        ctx = self.ctx
        left, middle, extend, right = self.PIECES[self.kind]
        first_line = self.first
        # 첫 줄이 기울인 대문자로 시작하면 괄호를 오른쪽으로 옮기고, 그 대문자로 끝나지도 않으면 덜 줄인다
        starts = self.capital(first_line.head.next)
        ends = self.capital(first_line.end.prev)
        x, oy = self.origin()
        y = self.second.h + self.top + oy - muldiv(self.middle_h, 30, 100)
        body = self.second if self.kind == 0x6C else self.first
        slack = muldiv(self.size, 7 if starts and not ends else 20, 100) + body.w - self.brace_w
        x += muldiv(self.size, 15, 100) if starts else 0
        cut = muldiv(self.size, 3, 100)
        if self.w > self.brace_w:
            x += cdiv(self.w - body.w, 2)
        if slack < 0:
            ctx.set_extent(ctx.size, cdiv(self.left_w, 2) + cut)
            ctx.draw_char(x, y, left)
            ctx.draw_char(self.left_w - cut + x, y, middle)
            ctx.draw_char(self.middle_w - cut * 2 + self.left_w + x, y, right)
            return
        ctx.set_extent(ctx.size, muldiv(slack, 26, 100) + cut)
        ctx.draw_char(self.left_w - cut + x, y, extend)
        half = cdiv(slack, 2)
        ctx.draw_char(self.middle_w - cut * 3 + half + self.left_w + x, y, extend)
        ctx.set_extent(ctx.size, cdiv(self.left_w, 2) + cut)
        ctx.draw_char(x, y, left)
        ctx.draw_char(half - cut * 2 + self.left_w + x, y, middle)
        ctx.draw_char(self.middle_w - cut * 4 + self.left_w + x + slack - 1, y, right)

    def capital(self, item: Obj | None) -> bool:
        """기울인 대문자(E000~E019, 글꼴 기울임 +0x0e)인 글자인가. 판정하며 +0x0e를 0·1로 줄인다(Accent와 같다)."""
        if not (isinstance(item, Glyph) and 0xE000 <= item.code <= 0xE019):
            return False
        self.ctx.top_flag &= 1
        return bool(self.ctx.top_flag)


class LongDivision(Obj):
    """LONGDIV(0x72), FUN_10018660. +0x78 = 나누는 수, +0x7c = 몫, +0x80 = 나눗셈 격자(0x6D)."""

    kind = 0x72

    def __init__(self, ctx: Context) -> None:
        super().__init__(ctx)
        self.divisor = self.new_row()
        self.quotient = self.new_row()
        self.body = self.new_row()
        self.rows = [self.divisor, self.quotient, self.body]

    def calc(self, cache: int) -> tuple[int, int]:
        if self.cached(cache):
            return self.w, self.h
        self.dirty = 0
        self.inherit()
        ctx = self.ctx
        s = self.size
        self.body.size = s
        body_w, body_h = self.body.calc(cache)
        self.quotient.size = s
        quotient_w, quotient_h = self.quotient.calc(cache)
        self.divisor.size = s
        divisor_w, _ = self.divisor.calc(cache)
        ctx.set_font(s)
        # 나눗셈 기호 E10F: 폭은 그대로, 높이의 20%가 몫과 격자 사이 간격이다. 나누는 수 높이는 쓰지 않는다
        sign_w, sign_h = ctx.plain(0xE10F)
        gap = muldiv(sign_h, 20, 100)
        self.sign_w, self.gap = sign_w, gap  # +0x84, +0x88
        height = gap + quotient_h + body_h
        if height == gap:
            height += ctx.size
        self.w = max(quotient_w, body_w) + sign_w + divisor_w + muldiv(s, 20, 100)
        self.h = height
        self.top = -quotient_h - cdiv(gap, 2)
        return self.w, self.h

    def draw(self) -> None:
        """FUN_10018460: 기호를 찍고 나누는 수는 왼쪽, 몫은 격자 오른쪽 끝에 맞춰 위, 격자는 기호 오른쪽에 둔다."""
        self.draw_sign()
        x, oy = self.origin()
        body_x = self.sign_w + self.divisor.w + x
        half = cdiv(self.gap, 2)
        self.divisor.ox, self.divisor.oy = x, half - self.divisor.top + oy
        self.divisor.draw()
        self.quotient.ox = max(self.body.w - self.quotient.w + body_x, body_x)
        self.quotient.oy = oy - half - self.quotient.h - self.quotient.top
        self.quotient.draw()
        self.body.ox, self.body.oy = body_x, half - self.body.top + oy
        self.body.draw()

    def draw_sign(self) -> None:
        """FUN_10018820: 나눗셈 기호 E10F를 세로로 늘려 찍고, 몫 아래 선 E110을 몫·격자 중 넓은 폭으로 늘린다."""
        ctx = self.ctx
        ctx.set_font(self.size)
        x, oy = self.origin()
        sign_x = muldiv(self.sign_w, 10, 100) + self.divisor.w + x
        y = muldiv(self.gap, 12, 100) + oy + self.top + self.quotient.h
        overlap = muldiv(self.sign_w, 30, 100)
        ctx.set_extent(cdiv(self.gap, 2) + ctx.size, ctx.half)
        ctx.draw_char(sign_x, y, 0xE10F)
        # 높이는 위에서 늘린 크기를 그대로 쓴다(0x10018926 +0x848)
        ctx.set_extent(ctx.size, cdiv(max(self.body.w, self.quotient.w) + overlap, 2))
        ctx.draw_char(self.sign_w - overlap + sign_x, y, 0xE110)
        ctx.set_font(self.size)


def wtoi(digits: str) -> int:
    """MSVC _wtoi(숫자만 든 문자열). 넘치면 LONG_MAX에서 멈춘다."""
    return min(int(digits), 0x7FFFFFFF) if digits else 0


class LongDivisionGrid(Grid):
    """LONGDIV 셋째 줄의 격자(0x6D), FUN_1000a980. 나눗셈을 따라가며 행마다 들여 쓸 숫자 칸 수를 정한다."""

    def __init__(self, ctx: Context, rows: int, cols: int) -> None:
        super().__init__(ctx, 0x6D, rows, cols)

    @staticmethod
    def _digits(line: Line) -> str | None:
        """줄의 글자 이름을 이어 붙인다. 숫자로 시작하지 않는 이름이 있으면 None."""
        text = ""
        for item in line.items():
            if not item.name[:1].isdigit():
                return None
            text += item.name
        return text

    def _offsets(self, cells: list[Line]) -> tuple[list[int], bool]:
        """0x1000ab82~0x1000b56d: 행마다 첫 칸 앞에 비울 폭과 숫자 나눗셈으로 읽혔는지."""
        offsets = [0] * self.nrows
        for cell in cells[: self.nrows]:
            first = cell.head.next
            if first is not cell.end and first.name[:1] in (" ", "~", "`"):
                return offsets, False
        digit_w = self.ctx.measure(to_pua(0x31))[0]
        shift = 0  # +local_a0, 직전 빼기에서 남은 자릿수
        for r in range(1, min(self.nrows, len(cells))):
            cell, prev = cells[r], cells[r - 1]
            if r & 1:
                shift = borrow = 0
                upper = self._digits(prev)
                lower = self._digits(cell) if upper is not None else None
                if upper is None or lower is None:
                    return offsets, False
                if upper and lower:
                    borrow = int(int(upper[0]) < int(lower[0]))
                    upper = upper[: len(lower) + borrow].ljust(len(lower) + borrow, "0")
                    diff = wtoi(upper) - wtoi(lower)
                    shift = 0 if diff < 0 else len(lower) if diff == 0 else len(lower) - len(str(diff))
                offsets[r] = digit_w * borrow
            else:
                first = cell.head.next
                if first is not cell.end and first.name == "0":
                    shift -= 1
                offsets[r] = digit_w * shift
        return offsets, True

    def calc(self, cache: int) -> tuple[int, int]:
        if self.cached(cache):
            return self.w, self.h
        self.dirty = 0
        self.inherit()
        s = self.size
        cells = self.rows
        for cell in cells:
            cell.size = s
            cell.calc(cache)
        offsets, numeric = self._offsets(cells)
        self.offsets, self.numeric = offsets, numeric  # +0x8c, +0x88
        col_w = [0] * self.ncols
        row_h = [0] * self.nrows
        for i, cell in enumerate(cells):
            r, c = divmod(i, self.ncols) if self.ncols else (0, 0)
            if r >= self.nrows:
                break
            shifted = cell.w + offsets[r] if c == 0 and numeric else cell.w
            col_w[c] = max(col_w[c], shifted)
            row_h[r] = max(row_h[r], cell.h)
        height = sum(row_h) + max(self.nrows - 1, 0) * muldiv(s, 15, 100)
        width = sum(col_w) + max(self.ncols - 1, 0) * muldiv(s, 20, 100)
        self.col_w, self.row_h = col_w, row_h  # +0x84, +0x80
        self.w, self.h, self.top = width, height, -cdiv(height, 2)
        return self.w, self.h

    def draw(self) -> None:
        """FUN_1000b760: 칸을 행마다 놓고, 홀수 행(빼는 수) 아래에 E06D를 그 행 폭으로 늘린 선을 긋는다."""
        ctx = self.ctx
        x, oy = self.origin()
        s = self.size
        top = oy - cdiv(self.h, 2)
        row_gap, col_gap = muldiv(s, 15, 100), muldiv(s, 20, 100)
        cells = iter(self.rows)
        row_y = 0
        for r in range(self.nrows):
            col_x = lead = span = 0
            for c in range(self.ncols):
                cell = next(cells, None)
                if cell is None:
                    return self._finish()
                if r & 1:
                    span += cell.w + (col_gap if c > 0 else 0)
                    if not self.numeric and c == 0:
                        # 숫자로 읽지 못한 격자는 첫 칸 앞 공백(공백·~·`) 폭만큼 선을 짧게 시작한다
                        lead = sum(item.w for item in _leading_spaces(cell))
                        span -= lead
                if c == 0 and self.numeric:
                    # 들여쓰기는 행마다 앞 행 위치에 더해진다(0x1000b934)
                    x += self.offsets[r]
                cell.ox, cell.oy = x + col_x, top - cell.top + row_y
                cell.draw()
                col_x += self.col_w[c] + col_gap
            row_y += self.row_h[r] + row_gap
            if r & 1:
                ctx.set_extent(s, span)
                ctx.draw_char(lead + x, top - cdiv(row_gap, 2) - col_gap + row_y, 0xE06D)
        self._finish()

    def _finish(self) -> None:
        self.ctx.set_font(self.size)


def _leading_spaces(line: Line) -> list[Obj]:
    """줄 앞부분에서 이름이 공백·~·`로 시작하는 항목들."""
    items = []
    for item in line.items():
        if item.name[:1] not in (" ", "~", "`"):
            break
        items.append(item)
    return items


def make_object(ctx: Context, kind: int) -> Obj | None:
    """FUN_1000dab0의 생성 분기."""
    if kind in (1, 4):
        return Fraction(ctx, kind)
    if kind == 6:
        return Root(ctx)
    if kind in BigOperator.GLYPHS:
        return BigOperator(ctx, kind)
    if kind in (0x1F, 0x20):
        return Shape(ctx, kind)
    if kind in (0xD, 0xE, 0xF, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x69):
        return Bracket(ctx, kind)
    if kind == 0x65:
        script = Script(ctx)
        script.add_sup()
        return script
    if kind == 0x66:
        script = Script(ctx)
        script.add_sub()
        return script
    if kind in Accent.GLYPHS:
        return Accent(ctx, kind)
    if kind in (0x4B, 0x6E, 0x75):
        return Wrapper(ctx, kind)
    if kind == 0x21:
        return Bold(ctx)
    if kind == 0x42:
        return UnderOver(ctx)
    if kind == 0x47:
        return Bigg(ctx)
    if kind in (7, 8):
        return Limit(ctx, kind)
    if kind in Integral.GLYPHS:
        return Integral(ctx, kind)
    if kind in (0x4D, 5):
        return Relation(ctx)
    if kind in Brace.PIECES:
        return Brace(ctx, kind)
    if kind == 0x72:
        return LongDivision(ctx)
    return None


def char_name(ch: int) -> str:
    """FUN_10010b20: 글자 이름 표 이진 탐색. 표에 같은 글자가 둘 있는 곳이 있어 탐색 순서를 그대로 따른다."""
    mid, low, high = 0x99, 0, 0x133
    while True:
        code = CHAR_NAMES[mid][0]
        if code == ch:
            break
        if code > ch:
            new_high = mid
        else:
            new_high, low = high, mid
        mid = (new_high + low) // 2
        high = new_high
        if mid <= low:
            return chr(ch)
    return CHAR_NAMES[mid][1] + " " if mid < 0x133 else chr(ch)


def glyph_name(ch: int, prev: Obj | None) -> str:
    """FUN_10010790: 글자 객체 이름."""
    prev_name = prev.name if prev is not None else ""
    prev_kind = prev.kind if prev is not None else 0
    if ch < 0xB1 and ch != 0xD7 and ch != 0x7C:
        if ch == 0x20:
            return "`"
        # 앞 이름이 공백으로 시작하면 그 공백을 뺀 길이로 판단한다
        prev_len = len(prev_name) - 1 if len(prev_name) > 1 and prev_name[0] == " " else len(prev_name)
        return " " + chr(ch) if prev_len > 1 and prev_kind == 0x6F else chr(ch)
    name = char_name(ch)
    if len(name) > 1 and prev_kind == 0x6F:
        name = " " + name
    return name


class Converter:
    """구문 트리 → 객체 트리(FUN_1004d350). cursor는 다음 객체를 넣을 위치(param_5 +0x34)다."""

    def __init__(self, parser: Parser, root: Line) -> None:
        self.parser = parser
        self.nodes = parser.nodes
        self.root = root
        self.ctx = root.ctx
        self.cursor: Obj = root.head
        self.matrix_depth = 0  # DAT_100a5c78
        self.pending_relation = 0  # +0x50·+0x44, REL은 화살표 글자를 받은 뒤에 만든다

    def parent_line(self, obj: Obj) -> Line:
        return obj if isinstance(obj, Line) else obj.parent

    def insert(self, obj: Obj) -> None:
        cursor = self.cursor
        if cursor.kind == 0x7A:
            cursor = cursor.prev
        line = self.parent_line(cursor)
        obj.parent = line
        obj.prev = cursor
        obj.next = cursor.next
        cursor.next.prev = obj
        cursor.next = obj

    def move(self) -> None:
        """FUN_10048620·FUN_10048d40: 문서 순서에서 다음 입력 위치로 옮긴다."""
        found = self._find_next(self.root.head, [False])
        if found is not None:
            self.cursor = found

    def _find_next(self, obj: Obj | None, passed: list[bool]) -> Obj | None:
        while obj is not None:
            if passed[0] and obj.kind in (0x1F, 0x20, 100, 0x6F, 0x71, 0x79):
                return obj
            if obj is self.cursor:
                passed[0] = True
            else:
                if obj.rows:
                    for row in obj.rows:
                        found = self._find_next(row.head, passed)
                        if found is not None:
                            return found
                    if passed[0]:
                        return obj
                if obj.kind == 0x7A and obj.parent.next_line is not None:
                    obj = obj.parent.next_line.head
                    continue
            obj = obj.next
        return None

    def create(self, kind: int, name: str = "") -> Obj | None:
        """FUN_100471e0."""
        cursor = self.cursor
        if isinstance(cursor, Line):
            cursor = self.cursor = cursor.head
        if kind == 0x66 and isinstance(cursor, Script) and not cursor.has_sub:
            cursor.add_sub()
            self.cursor = cursor.sub.head
            return cursor
        if kind == 0x65 and isinstance(cursor, Script) and not cursor.has_sup:
            cursor.add_sup()
            self.cursor = cursor.sup.head
            return cursor
        if kind == 0x6A:
            owner = self.parent_line(cursor).owner
            if isinstance(owner, Bracket) and owner.kind == 0x69:
                owner.right.set_char(ord(name[-1]))
                self.cursor = owner
                return owner
            return self.lone_right(ord(name[-1]))
        if kind > 0x75 and kind != 0x69:
            return None
        if kind in (0x4D, 5) and not self.pending_relation:
            self.pending_relation = kind
            return None
        obj = make_object(self.ctx, kind)
        if obj is None:
            raise NotImplementedError(f"수식 객체 0x{kind:X} 생성은 아직 옮기지 않았습니다")
        self.insert(obj)
        if name:
            obj.name = name
        if kind == 0x69:
            obj.left.set_char(ord(name[-1]))
            obj.right.set_char(0x20)
        self.cursor = obj.rows[0].head if obj.rows else obj
        return obj

    def lone_right(self, ch: int) -> Obj:
        """FUN_1000ee80: 줄의 첫 객체(앞의 & 하나는 건너뜀)부터 끝까지를 새 객체 본문으로 옮긴다(FUN_1000d930)."""
        line = self.parent_line(self.cursor)
        obj = LoneDelimiter(self.ctx, 0x6A)
        first = line.head.next
        if first.kind == 100:
            first = first.next
        if first is not line.end:
            last = line.end.prev
            before = first.prev
            before.next = line.end
            line.end.prev = before
            body = obj.body
            first.prev = body.head
            last.next = body.end
            body.head.next = first
            body.end.prev = last
            item = first
            while item is not body.end:
                item.parent = body
                item = item.next
        self.cursor = line.end.prev
        self.insert(obj)
        obj.delimiter.set_char(ch)
        self.cursor = obj
        return obj

    def glyph(self, kind: int, ch: int) -> None:
        """FUN_10046c40."""
        cursor = self.cursor
        if kind == 0xB6 and cursor.kind == 0x6F and cursor.name[:1].isascii() and cursor.name[:1].isalpha():
            self.glyph(0x6F, 0x60)
            cursor = self.cursor
        if ch == 0x15:
            return
        owner = self.parent_line(cursor).owner
        inside_bigg = owner is not None and owner.kind == 0x47
        if inside_bigg and cursor.kind != 0x79 and cursor.next.kind != 0x7A:
            self.cursor = owner
            return
        code = to_pua(ch) or ch
        name_ch = _PUA_TO_SYMBOL.get(ch, ch) if to_pua(ch) == 0 else ch
        if self.pending_relation and name_ch:
            # LAB_10046dd7: 보류한 REL을 만들고 이 글자를 화살표로 준다
            relation = self.create(self.pending_relation)
            self.pending_relation = 0
            relation.set_arrow(name_ch)
            return
        obj = Glyph(self.ctx, code)
        self.insert(obj)
        obj.name = glyph_name(name_ch, obj.prev)
        if kind == 0xB5:
            obj.quoted = 1
        self.cursor = owner if inside_bigg else obj

    def new_line(self) -> None:
        """FUN_10048b20."""
        line = self.parent_line(self.cursor)
        new = Line(self.ctx, line.owner)
        new.size = line.size
        new.next_line = line.next_line
        if line.next_line is not None:
            line.next_line.prev_line = new
        new.prev_line = line
        line.next_line = new
        self.cursor = new.head

    def scripts(self, node: Node, left: bool) -> None:
        """FUN_1004e9f0(left)·FUN_1004e8f0."""
        pairs = (
            ((node.lsup, 0x65, "LSUP"), (node.lsub, 0x66, "LSUB"))
            if left
            else ((node.a, 0x65, "^"), (node.b, 0x66, "_"))
        )
        for index, kind, name in pairs:
            if index:
                self.create(kind, name)
                self.convert(index)
                self.move()

    def convert(self, index: int) -> None:
        nodes = self.nodes
        pending_move = False
        while index:
            node = nodes[index]
            kind = node.kind
            if kind in (1, 0x21):
                if kind == 0x21:
                    self.create(0x4B, "not")
                self.text(node)
            elif kind == 3:
                if node.text_at(2) == ord("i"):
                    lim = "".join(map(chr, node.text[1:4])) == "Lim" and node.text_at(4) == 0
                    self.create(8 if lim else 7, "Lim" if lim else "lim")
                    if node.b:
                        self.convert(node.b)
                    self.move()
                    pending_move = self._after_limits(node)
                else:
                    ch = node.text_at(1)
                    op = {0x2211: 3, 0x220F: 0x43, 0x2210: 0x44, 0x222A: 0x46, 0x2229: 0x45}.get(ch, 0x42)
                    self.create(op)
                    if op == 0x42:
                        self.glyph(0x6F, ch)
                        self.move()
                    if node.b:
                        self.convert(node.b)
                    self.move()
                    if node.a:
                        self.convert(node.a)
                    self.move()
            elif kind == 0x46:
                ch = node.text_at(1)
                op = {0xE073: 0x2B, 0x222B: 2, 0x222E: 0x2D, 0xE074: 0x2C, 0xE076: 0x2E, 0xE077: 0x2F}.get(ch)
                if op is not None:
                    self.create(op)
                if node.b:
                    self.convert(node.b)
                self.move()
                if node.a:
                    self.convert(node.a)
                self.move()
                pending_move = self._after_limits(node)
            elif kind == 4:
                accent = node.text_at(0)
                obj_kind = _ACCENTS.get(accent)
                if accent == 0x2101:
                    self.create(0x21)
                elif obj_kind is not None:
                    self.create(obj_kind)
                self.convert(node.child)
                self.move()
            elif kind in (8, 9, 0xB, 0x23):
                if kind == 8:
                    self.create(1)
                elif kind == 9:
                    self.create(4)
                elif kind == 0xB:
                    self.grid(10, 2, 1)
                self.convert(node.a)
                self.move()
                self.convert(node.b)
                self.move()
            elif kind == 0xC:
                self.create(0x1F, "rm")
            elif kind == 0xD:
                self.create(0x20, "it")
            elif kind in (0xF, 0x15, 0x16, 0x17, 0x1C, 0x2E, 0x30, 0x31):
                self.matrix(node)
            elif kind == 0x13:
                self.create(0x6E, "phantom")
                self.convert(node.child)
                self.move()
            elif kind in (0x18, 0x19):
                self.create(0x4D if kind == 0x18 else 5)
                self.glyph(0x6F, node.text_at(0))
                self.convert(node.a)
                self.move()
                if kind == 0x19:
                    self.move()
                self.convert(node.b)
                self.move()
            elif kind == 0x1D:
                row = node.child
                while row:
                    self.convert(nodes[row].next)
                    if nodes[row].child:
                        self.new_line()
                    row = nodes[row].child
            elif kind in (0x1E, 0x1F):
                self.create(kind + 0x4D)
                self.convert(node.child)
                self.move()
                self.convert(node.a)
                self.move()
            elif kind == 0x20:
                self.create(0x42, "UNDEROVER")
                self.convert(node.child)
                self.move()
                self.convert(node.b)
                self.move()
                self.convert(node.a)
                self.move()
            elif kind == 0x26:
                if self.matrix_depth > 0:
                    self.move()
            elif kind in (0x28, 0x29):
                self.scripts(node, left=True)
                ch = chr(node.text_at(0))
                self.create(0x69 if kind == 0x28 else 0x6A, ("LEFT " if kind == 0x28 else "RIGHT ") + ch)
                self.scripts(node, left=False)
            elif kind == 0x2B:
                self.scripts(node, left=True)
                self.create(0x47)
                self.glyph(0x6F, node.text_at(0))
                self.move()
                self.scripts(node, left=False)
            elif kind == 0x32:
                self.create(0x75, "COLOR")
                self.convert(node.b)
                self.move()
                self.convert(node.child)
                self.move()
            elif kind == 0x2F:
                self.create(0x72, "LONGDIV")
                self.convert(node.b)
                self.move()
                self.convert(node.a)
                self.move()
                self.convert(node.child)
                self.move()
            elif kind == 0x4B:
                if node.b:
                    self.create(6, "root")
                    self.convert(node.b)
                else:
                    self.create(6, "sqrt")
                self.move()
                self.convert(node.child)
                self.move()
            elif kind == 0x7B:
                self.scripts(node, left=True)
                if node.child:
                    self.convert(node.child)
                    if pending_move:
                        self.move()
                        pending_move = False
                self.scripts(node, left=False)
            index = node.next

    def grid(self, kind: int, rows: int, cols: int) -> None:
        """FUN_10048650(PILE류)·FUN_10048750(괄호 행렬): 커서 뒤에 넣고 첫 칸으로 간다."""
        obj = Grid(self.ctx, kind, rows, cols)
        self.insert(obj)
        self.cursor = obj.rows[0].head

    def matrix(self, node: Node) -> None:
        """FUN_1004d350 case 0xf·0x15~0x17·0x1c·0x2e·0x30·0x31."""
        kind = node.kind
        self.matrix_depth += 1
        rows = 1 if kind in (0xF, 0x2E) and node.row_form == 0 else node.rows
        cols = node.cols
        if kind == 0x1C:
            self.grid(0x13, rows, cols)
        elif kind in (0x15, 0x16, 0x17):
            self.grid(kind - 5, rows, cols)
        elif kind in (0x2E, 0x31) and node.text_at(0) == 0:
            obj = Ladder(self.ctx, 0x73 if kind == 0x2E else 0x74, rows, cols)
            self.insert(obj)
            self.cursor = obj.rows[0].head
        elif kind == 0x30:
            obj = LongDivisionGrid(self.ctx, rows, cols)
            self.insert(obj)
            self.cursor = obj.rows[0].head
        else:
            bracket = {0: 9, 0x28: 10, 0x5B: 11, 0x7C: 12}.get(node.text_at(1) if node.text_at(0) else 0)
            if bracket is not None:
                self.grid(bracket, rows, cols)
            if node.row_form == 0:
                self.convert(node.child)
                self.move()
                return
        row = node.child
        while row:
            self.convert(self.nodes[row].next)
            self.move()
            row = self.nodes[row].child
        self.matrix_depth -= 1

    def _after_limits(self, node: Node) -> bool:
        """LAB_1004dcc7: 다음 형제가 묶음이면 그 내용을 마지막 칸에 넣고 나서 옮긴다."""
        if node.next and self.nodes[node.next].kind == 0x7B:
            return True
        self.move()
        return False

    def text(self, node: Node) -> None:
        """FUN_1004d350 case 1: 글자마다 글자 객체를 만든다."""
        self.scripts(node, left=True)
        quoted = node.text_at(0) == 0x22 and node.kind != 0x21
        if quoted:
            start = node.text_at(1)
            chars = self.parser.buf[start : start + 0x7F]
        else:
            chars = node.text[:0x7F]
        if 0 in chars:
            chars = chars[: chars.index(0)]
        i = 0
        while i < len(chars):
            c = chars[i]
            if c == 0x22:
                break
            if c in (0x60, 0x7E):
                self.glyph(0x6F, c)
            elif c == 6:
                self.create(0x1F, "rm")
            elif c == 4:
                self.create(0x20, "it")
            elif c == 8:
                i += 1
                self.glyph(0xB6, chars[i] if i < len(chars) else 0)
            elif len(chars) - i > 1:
                if c == 0x20:
                    self.glyph(0x6F, 0x60)
                else:
                    self.glyph(0xB5 if quoted else 0x6F, c)
            else:
                self.glyph(0x6F, 0x7E if c == 0x20 else c)
            i += 1
        self.scripts(node, left=False)
        if node.kind == 0x21:
            self.move()


#: 악센트 값 → 객체 종류(FUN_1004d350 case 4).
_ACCENTS = {
    0x4A0: 0x18,
    0x76: 0x4E,
    0x20E9: 0x24,
    0x4A1: 0x17,
    0x4A2: 0x22,
    0x4A3: 0x25,
    0x4A5: 0x1B,
    0x4A7: 0x19,
    0x4A8: 0x1A,
    0x4AC: 0x23,
    0x4D2: 0x1E,
    0x4F9: 0x1C,
    0x4FB: 0x1D,
}
