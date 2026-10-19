"""수식 객체 트리와 상자 계산. EqAppModule.dll의 객체(종류는 +0x04)와 CalcSize(가상 함수 2번 칸)를 옮겼다.
y는 아래로 늘고 수식 축이 0이다. top은 상자 위 끝(+0x54), w·h는 폭·높이(+0x58·+0x5c)다."""

from __future__ import annotations

from .glyphs import EQUATION_FONT, FALLBACK_FONT, UNITS_PER_PIXEL, glyph_table
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
        self.table = glyph_table()

    def set_font(self, size: int) -> None:
        """FUN_10011f10."""
        self.size = size
        self.half = cdiv(size, 2)

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
        top, bottom = base_top, base_bottom
        if self.has_sup:
            top = self.sup.top + sup_y
        if self.has_sub:
            bottom = self.sub.top + sub_y + sub_h
        self.w, self.top, self.h = width + extra, top, bottom - top
        return self.w, self.h


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
        sign_w = sign_w + max(0, index_w - cdiv(sign_w, 2))
        width = muldiv(s, 17, 100) + sign_w + body_w
        extra = index_h - muldiv(sign_h, 52, 100)
        if extra <= muldiv(s, 5, 100):
            extra = muldiv(s, 5, 100)
        self.w = width
        self.h = sign_h + extra
        self.top = self.body.top - muldiv(s, 10, 100) - extra
        return self.w, self.h


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
        self.w = max(glyph_w, lower_w, upper_w)
        self.h = core + upper_h + lower_h
        self.top = -upper_h - cdiv(core, 2)
        return self.w, self.h


class Delimiter:
    """괄호 글리프(FUN_10011580·FUN_100116b0·FUN_10011850)."""

    #: 종류별 (위, 늘림, 아래, 가운데) 글리프. 가운데(+0x26)의 폭이 괄호 폭이다.
    PIECES = {
        1: (0xE07C, 0xE07E, 0, 0xE07D),
        2: (0xE078, 0xE07A, 0xE07B, 0xE079),
        3: (0xE100, 0xE103, 0, 0xE101),
        9: (0xE07F, 0xE081, 0, 0xE080),
        10: (0xE082, 0xE084, 0xE07B, 0xE083),
        11: (0xE102, 0xE104, 0, 0xE105),
    }
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
        self.middle = 0
        self.whole_w = 0  # +0x44
        self.target = 0  # +0x10

    def set_kind(self, kind: int) -> None:
        self.kind = kind
        if kind in self.PIECES:
            self.middle = self.PIECES[kind][3]
        elif 0 <= kind < 0x15:
            self.middle = self.SINGLE.get(kind, 0)

    def set_char(self, ch: int) -> None:
        if ch in (0x20, 0x2E):
            self.set_kind(0)
        elif ch in self.CHAR_KIND:
            self.set_kind(self.CHAR_KIND[ch])
        else:
            self.kind = 0x13
            self.middle = ch

    def measure(self, target: int, size: int) -> None:
        ctx = self.ctx
        ctx.set_font(size)
        self.target = target or ctx.size
        if self.kind == 0x13:
            if self.middle:
                self.whole_w, _ = ctx.measure(self.middle)
            return
        if self.middle:
            self.whole_w, _ = ctx.plain(self.middle)
        if self.middle in (0x2329, 0x232A):
            self.whole_w = cdiv(self.whole_w * 2, 3)

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
        self.lower.size = small
        lower_w, _ = self.lower.calc(cache)
        self.body.size = self.size
        body_w, body_h = self.body.calc(cache)
        top = min(-cdiv(text_h, 2), self.body.top)
        bottom = max(body_h + self.body.top, self.lower.h + cdiv(text_h, 2))
        self.w = body_w + max(text_w, lower_w)
        self.top, self.h = top, bottom - top
        return self.w, self.h


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
        self.w = max(lower_w + lower_shift, upper_w + right_gap) + body_w + tail + glyph_w
        self.top, self.h = top, bottom - top
        return self.w, self.h


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
        self.w, self.h, self.top = width, height, -cdiv(height, 2)
        return self.w, self.h


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
        # FUN_100097a0: 사다리 모양 글리프 U+E101의 폭
        corner = ctx.plain(0xE101)[0]
        dots = ctx.plain(0x22EF)[0] + muldiv(s, 20, 100) if self.kind == 0x74 else 0
        col_w = [0] * self.ncols
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
        if self.ncols > 1:
            col_w[0] += corner
            width += corner
        if self.kind == 0x74 and self.ncols > 2 and self.nrows > 1:
            width += dots
        self.w, self.h, self.top = width, height, -cdiv(height, 2)
        return self.w, self.h


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


class Relation(Obj):
    """REL(0x4D)·BUILDREL(5), FUN_100174a0. +0x7c = 화살표 위, +0x78 = 화살표 아래."""

    #: 선으로 그리는 화살표 → 모양 번호(0x10017683 분기). 그 밖의 글자는 글꼴 글자로 측정한다.
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
        shape = self.DRAWN.get(self.arrow)
        if shape is None:
            _, arrow_h = ctx.plain(self.arrow)
        else:
            # 0x10017718: 가운데 토막 글자(E059 또는 E070) 높이의 35%가 화살표 두께, 모양 6(⇄)은 두 배
            _, piece_h = ctx.plain(0xE059 if shape in (0, 1, 2, 6) else 0xE070)
            arrow_h = muldiv(piece_h, 30, 100) + muldiv(piece_h, 5, 100)
            if shape == 6:
                arrow_h *= 2
        self.w = width
        self.h = arrow_h + upper_h + lower_h
        self.top = -upper_h - cdiv(arrow_h, 2)
        return self.w, self.h


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

    #: 괄호 조각 글자 +0xac·+0xae·+0xb0·+0xb2(FUN_1001e250).
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
        brace_w = ctx.plain(middle)[0] + ctx.plain(right)[0] + ctx.plain(left)[0]
        brace_h = muldiv(ctx.plain(middle)[1], 35, 100)
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
        height = gap + quotient_h + body_h
        if height == gap:
            height += ctx.size
        self.w = max(quotient_w, body_w) + sign_w + divisor_w + muldiv(s, 20, 100)
        self.h = height
        self.top = -quotient_h - cdiv(gap, 2)
        return self.w, self.h


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
        self.w, self.h, self.top = width, height, -cdiv(height, 2)
        return self.w, self.h


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
