"""옛 수식 엔진(HwpApp.dll) 그리기. FUN_106578c0이 그리기 문맥을 받으면 FUN_10651430으로 노드를 돌며 글자와 선을 찍는다.
좌표는 수식 상자 왼쪽 위가 (0, 0)인 HWPUNIT이고 글자 y는 기준선이다."""

from __future__ import annotations

from dataclasses import dataclass

from .engine import cdiv, muldiv
from .glyphs import EQUATION_FONT
from .legacy import (
    ACCENT,
    BIG_OPERATOR,
    BIGG,
    BOLD,
    GROUP,
    INTEGRAL,
    IT,
    LEFT,
    MATRIX,
    OVER,
    PRINT_UNITS_PER_PIXEL,
    QUOTE,
    RIGHT,
    RM,
    SCALE,
    SQRT,
    TEXT,
    Layout,
    atom_kind,
    laid_out_legacy,
)
from .legacy_tables import SPACING

#: HFT 글꼴 descent / em = 100/512(HMKMM 계열, 쪽 번호 PDF 실측과 같은 값). 글자는 아래 끝 맞춤(TA_BOTTOM)으로 찍는다.
HFT_DESCENT = 100 / 512
#: HyHwpEQ usWinDescent / unitsPerEm(괄호 조각을 이 글꼴로 찍는다).
EQ_DESCENT = 204 / 1024
#: 괄호 조각 코드 → HyHwpEQ PUA(표 0x10740390, 코드·PUA 쌍). 표에 든 값이면 글꼴을 HyHwpEQ로 바꿔 찍는다(FUN_106569d0).
_PIECES = {
    0xA00A: 0xE078,
    0xA008: 0xE079,
    0xA016: 0xE07A,
    0xA00B: 0xE082,
    0xA009: 0xE083,
    0xA017: 0xE084,
    0xE07D: 0xE07D,
    0xE080: 0xE080,
    0xA00C: 0xE07C,
    0xA018: 0xE07E,
    0xA00D: 0xE07F,
    0xA019: 0xE081,
    0xA00E: 0xE100,
    0xA01A: 0xE103,
    0xA00F: 0xE102,
    0xA01B: 0xE104,
    0xFE33: 0xE105,
}
_EQ_FONT_CODES = frozenset(_PIECES) | frozenset(_PIECES.values())
#: 괄호 글자 → (위 조각, 가운데·이음 조각, 아래 조각) 코드(표 0x107403e0의 +2·+4·+6, 열쇠는 0x10740418).
_BRACKET_PIECES = {
    0x7B: (0xA00A, 0xA008, 0xA016),
    0x7D: (0xA00B, 0xA009, 0xA017),
    0x28: (0xA00C, 0xFE33, 0xA018),
    0x29: (0xA00D, 0xFE33, 0xA019),
    0x5B: (0xA00E, 0xFE33, 0xA01A),
    0x5D: (0xA00F, 0xFE33, 0xA01B),
    0x7C: (0xFE33, 0xFE33, 0xFE33),
}
#: 화살표 글자 → (왼쪽, 가운데 이음, 오른쪽) 조각(표 0x10740430).
_ARROWS = {
    0x2190: (0x2190, 0x2500, 0x2500),
    0x2192: (0x2500, 0x2500, 0x2192),
    0x2194: (0x2190, 0x2500, 0x2192),
    0x21D0: (0x21D0, 0x2550, 0x2550),
    0x21D2: (0x2550, 0x2550, 0x21D2),
    0x21D4: (0x21D0, 0x2550, 0x21D2),
    0x21C4: (0x218C, 0x21FD, 0x218F),
    0x21C6: (0x218E, 0x21FD, 0x218D),
}


@dataclass
class LegacyGlyph:
    text: str
    x: int
    #: 기준선 y.
    y: int
    height: int
    #: 장평(백분율).
    ratio: int
    font: str
    italic: bool
    bold: bool


@dataclass
class LegacyLine:
    x1: int
    y1: int
    x2: int
    y2: int
    width: int


class LegacyPainter:
    """FUN_10651430의 this(Layout)와 그리기 문맥(+0x28 위쪽, +0x38~+0x54 상자, +0x58 방식)을 함께 쥔다."""

    def __init__(self, layout: Layout, box_width: int, box_height: int, centered: bool) -> None:
        self.layout = layout
        self.nodes = layout.nodes
        self.tables = layout.tables
        fs = layout.font_size
        root = self.nodes[0]
        self.ascent = muldiv(root.top, fs, 100)  # +0x28
        self.calc_w, self.calc_h = root.width, muldiv(root.top - root.bottom + 20, fs, 100)  # +0x50, +0x54
        self.box_w, self.box_h = box_width, box_height
        # FUN_1026ea60: 방식 = (EQEDIT attr != 0) + 2. 3이면 상자 가운데에 두고, 2면 상자에 맞춰 늘린다
        self.centered = centered
        self.pen_x = self.pen_y = 0
        self.drawn: list[LegacyGlyph | LegacyLine] = []

    # FUN_10657070·FUN_10657020
    def _x(self, x: int) -> int:
        if self.centered:
            return x + cdiv(self.box_w - self.calc_w, 2)
        return int(float(self.box_w) * float(x) / float(self.calc_w)) if self.calc_w else x

    def _y(self, y: int) -> int:
        return int(float(self.box_h) * float(y) / float(self.calc_h)) if self.calc_h else y

    def _font(self) -> tuple[int, bool, bool, int]:
        layout = self.layout
        return layout.height, bool(layout.flags & 1), bool(layout.flags & 2), layout.ratio

    def _set_flags(self, flags: int) -> None:
        """DAT_1089372c를 정하고 FUN_10650f40으로 글꼴을 다시 만든다(장평은 그대로)."""
        self.layout.flags = flags
        self.layout.create_font()

    def width(self, code: int) -> int:
        """GetTextExtentPointW cx. 폭이 0인 글자(공백 제외)는 공백 폭이다(FUN_10650e10)."""
        pixel = self.layout.pixel
        cx = self.tables.width(code, *self._font(), pixel=pixel)
        return self.tables.width(0x20, *self._font(), pixel=pixel) if cx == 0 and code != 0x20 else cx

    def char(self, x: int, y: int, code: int) -> None:
        """FUN_106569d0: 아래 끝 맞춤으로 글자 하나. 괄호 조각 코드는 HyHwpEQ PUA로 바꿔 찍는다."""
        height, italic, bold, ratio = self._font()
        if code in _EQ_FONT_CODES:
            code, font, descent = _PIECES.get(code, code), EQUATION_FONT, EQ_DESCENT
        else:
            font, descent = self.tables.family(code, italic), HFT_DESCENT
        if code in (0x307, 0x308, 0x301, 0x300, 0x2032):
            # 결합 악센트는 자기 폭만큼 오른쪽에 찍는다
            x += self.width(code)
        bottom = self._y(y) + self.ascent + int(self.layout.font_size * 0.15)
        baseline = bottom - round(height * descent)
        self.drawn.append(LegacyGlyph(chr(code), self._x(x), baseline, height, ratio, font, italic, bold))

    def move_to(self, x: int, y: int) -> None:
        """FUN_10656f50."""
        self.pen_x, self.pen_y = self._x(x), self._y(y) + self.ascent

    def line_to(self, x: int, y: int, width: int) -> None:
        """FUN_10656e30: 펜 굵기 width(논리 단위)로 선을 긋는다."""
        x2, y2 = self._x(x), self._y(y) + self.ascent
        self.drawn.append(LegacyLine(self.pen_x, self.pen_y, x2, y2, width))
        self.pen_x, self.pen_y = x2, y2

    def set_height(self, size: int) -> None:
        """FUN_106568e0: 높이를 정하고 장평을 100으로 되돌린다."""
        self.layout.set_height(size)
        self.layout.ratio = 100

    def text(self, x: int, y: int, chars: list[int]) -> None:
        """FUN_106562d0: 글자열. 6은 똑바른 꼴, 4는 기울임 표지이고 ` 는 공백 1/4이다."""
        layout = self.layout
        space = self.width(0x20)
        i = 0
        if chars[0] == 6:
            i, style = 1, 6
            x += cdiv(space, 4)
        else:
            style = layout.style
        self._set_flags(layout.bold | (1 if style == 4 else 0))
        prev = atom_kind(_at(chars, i))
        while True:
            c = _at(chars, i)
            if c in (0, QUOTE):
                return
            if c == 4:
                style = 4
                x += cdiv(space, 4)
                i += 1
                continue
            if style == 4:
                letter = 0x41 <= c <= 0x5A or 0x61 <= c <= 0x7A
                self._set_flags(layout.bold | (1 if letter else 0))
            if c in (0x2032, 0x2033, 0x2034):
                self._set_flags(layout.bold | 1)
                for _ in range(c - 0x2031):
                    self.char(x, y, 0x27)
                    x += self.width(0x27)
            elif c == 0x60:
                x += cdiv(space, 4)
            else:
                self.char(x, y, c)
                x += self.width(c)
            i += 1
            nxt = _at(chars, i)
            if nxt != 0:
                kind = atom_kind(nxt)
                x += muldiv(space, SPACING[prev][kind], 18)
                prev = kind

    def accent_line(self, x: int, y: int, width: int, em: int, accent: int) -> None:
        """FUN_10652110: 윗줄·밑줄(0x305·0x332)과 화살표(0x4f9 오른쪽, 0x4fb 양쪽)."""
        right = width + x
        head = cdiv(em, 8)
        pen = max(cdiv(em, 24), 1)
        self.move_to(x, y)
        self.line_to(right, y, pen)
        if accent in (0x4F9, 0x4FB):
            self.move_to(right - head * 2, y - head)
            self.line_to(right, y, pen)
            self.line_to(right - head * 2, y + head, pen)
            if accent == 0x4FB:
                self.move_to(head * 2 + x, y - head)
                self.line_to(x, y, pen)
                self.line_to(head * 2 + x, y + head, pen)

    def accent_glyph(self, x: int, y: int, width: int, code: int, size: int) -> None:
        """FUN_10652200: 악센트 글자를 본문 폭에 맞춰 장평으로 늘린다. 2.5배를 넘으면 글자를 키운다."""
        layout = self.layout
        self._set_flags(layout.bold)
        glyph_w = self.width(code)
        ratio = muldiv(muldiv(width, 100, glyph_w), 100, size)
        if size * 5 < ratio * 2:
            enlarged = muldiv(width, size * 2, glyph_w * 5)
            layout.height = muldiv(enlarged, layout.font_size, 100)
            self.layout.ratio = 250
            scaled = muldiv(size, layout.scale, 100) if layout.scale != 100 else size
            rise = (muldiv(enlarged, layout.font_size, 100) - muldiv(scaled, layout.font_size, 100)) * 3
            y += cdiv(rise, 4)
        else:
            layout.height = muldiv(size, layout.font_size, 100)
            self.layout.ratio = muldiv(ratio, 100, size) & 0xFF
        self.char(x, y, code)
        self.set_height(size)

    def bracket(self, x: int, y: int, top: int, bottom: int, code: int, size: int) -> None:
        """FUN_106523e0: 괄호. 낮으면 글자 하나, 높으면 장평으로 늘리거나 조각으로 잇는다."""
        layout = self.layout
        fs = layout.font_size
        self._set_flags(layout.bold)
        if top <= size:
            self.char(x, y, code)
            return
        span = top - bottom
        pieces = _BRACKET_PIECES.get(code, (0, 0, 0))
        if code in (0x7B, 0x7D) and size * 5 <= span:
            self._pieces_ends(top, bottom, size, pieces, x, y)
            middle = cdiv(top - size + bottom, 2)
            self.char(x, y - muldiv(middle - cdiv(size, 8), fs, 100), pieces[1])
            self._pieces_fill(top, middle, size, 0xFE33, x, y)
            self._pieces_fill(middle + size, bottom, size, 0xFE33, x, y)
            return
        if span < size * 3 or code not in (0x28, 0x29, 0x5B, 0x5D, 0x7C) and not 0x2066 <= code <= 0x206B:
            # FUN_10656850: 높이를 늘리고 장평을 줄여 글자 하나로 찍는다
            stretch = muldiv(span, 0x5F, size)
            layout.height = muldiv(stretch, fs, 100)
            self.layout.ratio = muldiv(size, 100, stretch) & 0xFF
            self.char(x, y - muldiv(muldiv(bottom, 0x46, size), fs, 100), code)
            return
        glyph_w = self.width(code)
        shifted = glyph_w * 0x33 // 100 + x if code == 0x28 else x - glyph_w * 0x13 // 100 if code == 0x29 else x
        self._pieces_ends(top, bottom, size, pieces, shifted, y)
        self._pieces_fill(top, bottom, size, pieces[1], x, y)

    def _pieces_ends(self, top: int, bottom: int, size: int, pieces: tuple[int, int, int], x: int, y: int) -> None:
        """FUN_106526d0: 괄호 위·아래 조각."""
        layout = self.layout
        layout.height = muldiv(size, layout.font_size, 100)
        self.layout.ratio = 100
        self.char(x, y - muldiv(top - cdiv(size, 8) - size, layout.font_size, 100), pieces[0])
        self.char(x, y - muldiv(bottom, layout.font_size, 100), pieces[2])

    def _pieces_fill(self, top: int, bottom: int, size: int, code: int, x: int, y: int) -> None:
        """FUN_106527d0: 위 조각 아래부터 아래 조각 위까지 이음 조각을 size/4 간격으로 찍는다."""
        step = cdiv(size, 4)
        current = top - size * 2
        stop = bottom - step + size
        while current > stop:
            self.char(x, y - muldiv(current, self.layout.font_size, 100), code)
            current -= step

    def arrow(self, x: int, y: int, width: int, em: int, code: int) -> None:
        """FUN_10651430 case 0x18·0x19: 화살표를 왼쪽·이음·오른쪽 조각으로 본문 폭만큼 잇는다."""
        self._set_flags(self.layout.bold)
        left_x = cdiv(em, 6) + x
        end = left_x + width - cdiv(em, 3)
        pieces = _ARROWS.get(code)
        if pieces is None:
            self.char(left_x, y, code)
            return
        glyph_w = self.width(pieces[2])
        step = glyph_w - cdiv(glyph_w, 10)
        self.char(left_x, y, pieces[0])
        remaining = width - cdiv(em, 3) - step
        while step < remaining:
            left_x += step
            self.char(left_x, y, pieces[1])
            remaining -= step
        self.char(end - step, y, pieces[2])

    # FUN_10651430
    def draw(self, index: int, x: int) -> None:
        layout, nodes = self.layout, self.nodes
        if index == 0:
            return
        saved = (layout.style, layout.height, layout.scale)
        fs = layout.font_size
        y = -muldiv(nodes[index].y, fs, 100)
        size = nodes[index].size
        em = muldiv(muldiv(size, layout.scale, 100) if layout.scale != 100 else size, fs, 100)
        self.set_height(nodes[index].size)
        while index:
            node = nodes[index]
            x += node.x
            k = node.kind
            scripts = False
            if k == TEXT:
                chars = layout.quoted(node) if node.text[0] == QUOTE else node.text
                self.text(x, y, chars)
                scripts = True
            elif k == BIG_OPERATOR:
                if node.text[2] != 0x69:
                    scaled = muldiv(node.size, 100, 0x55)
                    if layout.scale != 100:
                        scaled = muldiv(scaled, layout.scale, 100)
                    layout.height = muldiv(scaled, fs, 100)
                    self.layout.ratio = 100
                    self._set_flags(layout.bold)
                    # 큰 연산자 글자는 기준선을 글자 크기 백분율 값만큼 내린다(0x106516b7, 단위를 섞어 더한다)
                    self.char(x, y + node.size, node.text[1])
                    self.set_height(node.size)
                else:
                    self.text(x, y, node.text)
                scripts = True
            elif k in (ACCENT, 0x13):
                accent = node.text[0]
                saved_bold = layout.bold
                if accent == 0x2101:
                    layout.bold = 2
                self.draw(node.child, x)
                if accent == 0x2101:
                    layout.bold = saved_bold
                elif accent in (0x305, 0x4F9, 0x4FB, 0x332):
                    if accent == 0x332:
                        lift, ratio = muldiv(node.bottom, fs, 100), 14
                    else:
                        lift, ratio = muldiv(node.top, fs, 100), 23
                    self.accent_line(x, em * ratio // 100 + y - lift, node.width, em, accent)
                else:
                    top = muldiv(node.top, fs, 100)
                    code = accent + 0x1C50 if (accent & 0xFFF0) == 0x4A0 else accent
                    self.accent_glyph(x, y - (top - em), node.width, code & 0xFFFF, node.size)
            elif k in (OVER, 0x18, 0x19):
                bar = y - muldiv(em, 0x23, 100)
                if k == OVER:
                    self.accent_line(cdiv(em, 16) + x, bar, node.width - cdiv(em, 4), em, node.text[0])
                else:
                    self.arrow(x, y, node.width, em, node.text[0])
                self.draw(node.sup, x)
                self.draw(node.sub, x)
            elif k in (9, 0xB, 0x23):
                self.draw(node.sup, x)
                self.draw(node.sub, x)
                if k == 0xB:
                    self._side_brackets(node, x, y)
            elif k in (RM, IT):
                layout.style = 6 if k == RM else 4
            elif k == BOLD:
                pass
            elif k == MATRIX:
                if node.text[0] != 0:
                    self._side_brackets(node, x, y)
                if node.link != 0:
                    self._rows(node, x)
                else:
                    self.draw(node.child, x)
            elif k in (0x15, 0x16, 0x17, 0x1D):
                self._rows(node, x)
            elif k == 0x1C:
                half = node.text[7] >> 1
                self.bracket(x, y, cdiv(em, 8) + half, -half, node.text[1], node.size)
                self._rows(node, x)
            elif k == SCALE:
                layout.scale = node.text[0]
                self.set_height(node.size)
                scaled = muldiv(node.size, layout.scale, 100) if layout.scale != 100 else node.size
                em = muldiv(scaled, fs, 100)
            elif k == 0x21:
                slash = self.width(0x5C)
                self.text(cdiv(node.width - slash, 2) + x, y, node.text[5:])
                self.text(x, y, node.text)
                scripts = True
            elif k in (LEFT, RIGHT, BIGG):
                self.bracket(x, y, node.top, node.bottom, node.text[0], node.size)
                self.set_height(node.size)
                scripts = k != LEFT
            elif k == INTEGRAL:
                rise = muldiv(em, 9, 0x14)
                self._set_flags(layout.bold)
                left = x + cdiv(em, 0x14)
                self.char(left, y - rise, 0x2320)
                self.char(left, y + rise, 0x2321)
                if node.text[1] == 0x222E:
                    self.char(left, y, 0x25CB)
                self._set_flags(layout.bold | (1 if layout.style == 4 else 0))
                scripts = True
            elif k == SQRT:
                self.draw(node.child, x)
                self._radical(node, x, y, em)
            elif k == GROUP:
                if node.child:
                    self.draw(node.child, x)
                scripts = True
            if scripts:
                if node.sup:
                    self.draw(node.sup, x)
                if node.sub:
                    self.draw(node.sub, x)
                if node.sup or node.sub:
                    self.set_height(node.size)
            x += node.width
            index = node.next
        layout.style, layout.height, layout.scale = saved

    def _side_brackets(self, node, x: int, y: int) -> None:
        """BINOM·MATRIX 괄호: 왼쪽은 상자 앞, 오른쪽은 끝에서 괄호 글자 폭의 절반 앞."""
        self.bracket(x, y, node.top, node.bottom, node.text[1] if node.kind == 0xB else node.text[1], node.size)
        width = self.layout.text_width(node.text)
        self.bracket(node.width - cdiv(width, 2) + x, y, node.top, node.bottom, node.text[2], node.size)
        self.set_height(node.size)

    def _rows(self, node, x: int) -> None:
        nodes = self.nodes
        row = node.child
        while row:
            self.draw(nodes[row].next, nodes[row].x + x)
            row = nodes[row].child

    def _radical(self, node, x: int, y: int, em: int) -> None:
        """FUN_10651430 case 0x4b: 근호를 선 네 개로 긋는다."""
        fs = self.layout.font_size
        half = cdiv(em, 2)
        pen = cdiv(half, 12) or 1
        self.move_to(x, pen - half + y)
        self.line_to(x + pen, y - half, pen)
        bottom = muldiv(node.bottom, fs, 100)
        foot = cdiv(half, 3) + pen + x
        self.line_to(foot, y - bottom - pen, half >> 3 if half >= 0 else -((-half) >> 3))
        self.move_to(foot, y - bottom)
        top = em * 15 // 100 + y - muldiv(node.top, fs, 100)
        self.line_to(half + x, top, pen)
        self.line_to(node.width - pen + x, top, pen)


def draw_legacy(
    script: str, font_size: int, box_width: int, box_height: int, centered: bool, flags: int = 0
) -> list[LegacyGlyph | LegacyLine]:
    """옛 엔진 수식 글자와 선. flags는 배치 전 글꼴 플래그(측정 때와 같은 값을 넘긴다)."""
    layout, root = laid_out_legacy(script, font_size, flags, PRINT_UNITS_PER_PIXEL)
    # FUN_106578c0 그리기 분기: 기울임 스타일·굵게 없음으로 FUN_10651430을 x 0부터 부른다
    layout.style, layout.bold = 4, 0
    painter = LegacyPainter(layout, box_width, box_height, centered)
    painter.draw(root, 0)
    return painter.drawn


def _at(buf: list[int], i: int) -> int:
    return buf[i] if 0 <= i < len(buf) else 0


__all__ = ["LegacyGlyph", "LegacyLine", "LegacyPainter", "draw_legacy"]
