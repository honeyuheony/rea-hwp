"""옛 수식 엔진(HwpApp.dll FUN_106578c0, 버전 문자열이 "Equation Version 60"이 아닌 수식)의 상자 계산.
세로는 글자 크기 백분율(y는 위로 증가), 가로는 HFT 글꼴 "수식"의 96DPI 픽셀 폭으로 계산한다."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .engine import cdiv, muldiv
from .legacy_tables import COMMANDS, LOWER_COUNT, SPACING, UPPER_BASE, UPPER_COUNT

_DATA = Path(__file__).resolve().parent.parent / "data" / "legacy_eq_metrics.json.gz"
#: FUN_106578c0이 스크립트 앞에 붙이는 문자열(0x10791ef0). 뒤에는 "}"를 붙인다.
PREFIX = "eqalign{"
#: 노드 풀 칸 수(FUN_10655920: 0x40바이트 노드, 0번은 머리).
POOL_SIZE = 255
#: 대응 없음을 뜻하는 한글 3.0 코드(HncUnicodeToHCharBase).
NO_HCHAR = 0x3441
#: 96DPI 화면 DC에서 1픽셀 = 7200/96 HWPUNIT(FUN_1026e540의 창·뷰포트 범위).
UNITS_PER_PIXEL = 75

SPACE, CARET, UNDERSCORE, HASH, LBRACE, RBRACE, AMP, QUOTE, TILDE, BACKQUOTE, BACKSLASH = (
    0x20,
    0x5E,
    0x5F,
    0x23,
    0x7B,
    0x7D,
    0x26,
    0x22,
    0x7E,
    0x60,
    0x5C,
)
#: 노드 종류(명령 값): FROM 6, TO 7, OVER 8, ATOP 9, CHOOSE 10, BINOM 0xb, RM 0xc, IT 0xd, BOLD 0xe, MATRIX 0xf.
FROM, TO, OVER, ATOP, CHOOSE, BINOM, RM, IT, BOLD, MATRIX = 6, 7, 8, 9, 10, 0xB, 0xC, 0xD, 0xE, 0xF
PILES = frozenset((0x15, 0x16, 0x17, 0x1C, 0x1D))
SCALE, LEFT, RIGHT, BIGG, SQRT, BIG_OPERATOR, INTEGRAL, ACCENT, GROUP, TEXT = (
    0x1B,
    0x28,
    0x29,
    0x2B,
    0x4B,
    3,
    0x46,
    4,
    0x7B,
    1,
)
_SPLIT_BEFORE = frozenset((SPACE, CARET, UNDERSCORE, HASH, LBRACE, RBRACE, AMP, TILDE, BACKQUOTE))
_SPLIT_AFTER = frozenset((SPACE, CARET, UNDERSCORE, HASH, LBRACE, RBRACE, AMP, QUOTE, TILDE, BACKQUOTE))
_WORD_STOP = frozenset((CARET, UNDERSCORE, HASH, LBRACE, RBRACE, AMP, QUOTE, BACKQUOTE, 0xFF, 4, 2, 3))
_NAME_STOP = frozenset((CARET, UNDERSCORE, HASH, LBRACE, RBRACE, AMP, QUOTE, TILDE, BACKQUOTE))
#: FUN_106541f0 switch에서 FUN_106547d0으로 넘기는 종류.
_ITEM_KINDS = frozenset(
    (1, 3, 4, 0xB, 0xF, 0x10, 0x11, 0x12, 0x13, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1B, 0x1C, 0x1D, 0x21, 0x28, 0x29, 0x2B)
    + (0x4B, 0x6F, 0x75)
)


class LegacyEquationError(Exception):
    """해석 실패(FUN_106541f0이 0이 아닌 값을 돌려줌). 한글은 이때 저장된 크기를 그대로 둔다."""


def _at(buf: list[int], i: int) -> int:
    return buf[i] if 0 <= i < len(buf) else 0


def _is_letter(c: int) -> bool:
    return 0x61 <= c <= 0x7A or 0x41 <= c <= 0x5A


def _is_upper(c: int) -> bool:
    return 0x41 <= c <= 0x5A


def _cstrlen(buf: list[int]) -> int:
    n = 0
    while n < len(buf) and buf[n] != 0:
        n += 1
    return n


# ---- 전처리(FUN_10657190)와 명령 탐색(FUN_106576b0) ----


def _compare(name: str, text: list[int], start: int) -> int:
    """이름이 text의 앞부분이면 0, 이름이 크면 1, 작으면 -1(문자를 부호 있는 char로 비교)."""
    for k, ch in enumerate(name.encode("latin-1")):
        c = ch - 256 if ch >= 0x80 else ch
        u = _at(text, start + k)
        if c > u:
            return 1
        if c < u:
            return -1
    return 0


def table_search(base: int, count: int, text: list[int], start: int) -> int:
    """앞부분이 맞는 명령 중 정렬 순서로 마지막 것의 표 안 번호(FUN_106576b0, 어셈블리로 확인). 없으면 0."""

    def name(j: int) -> str:
        return COMMANDS[base + j][0]

    def forward(j: int, found: int) -> int:
        while True:
            j += 1
            if count <= j:
                return found
            order = _compare(name(j), text, start)
            if order > 0:
                return found
            if order == 0:
                found = j

    order = _compare(name(1), text, start)
    if order > 0:
        return 0
    if order == 0:
        return forward(1, 1)
    lower, upper, probe = 1, count, count
    while probe != lower:
        order = _compare(name(probe), text, start)
        if order > 0:
            upper = probe
            probe = (probe + lower) // 2
        elif order < 0:
            lower = probe
            probe = (upper + probe) // 2
        else:
            return forward(probe, probe)
    if _compare("pi", text, start) == 0:
        return 0
    return lower - 1 if name(lower) == "pile" else 0


def _command_index(src: list[int], i: int) -> int:
    rel = table_search(0, LOWER_COUNT, src, i)
    if rel:
        return rel
    c, nxt = _at(src, i), _at(src, i + 1)
    if _is_upper(c) and _is_upper(nxt):
        word, start = src, i
    elif _is_letter(c) and 0x61 <= nxt <= 0x7A:
        word = [ord(chr(c).upper())]
        while len(word) < 10 and 0x61 <= _at(src, i + len(word)) <= 0x7A:
            word.append(_at(src, i + len(word)) - 0x20)
        start = 0
    else:
        return 0
    rel = table_search(UPPER_BASE, UPPER_COUNT, word, start)
    return UPPER_BASE + rel if rel else 0


def _copy_word(src: list[int], i: int, out: list[int]) -> int:
    c = src[i]
    out.append(c)
    i += 1
    if c == BACKSLASH:
        u = _at(src, i)
        if not _is_letter(u):
            if u != 0:
                out.append(u)
                i += 1
            return i
    elif not _is_letter(c):
        return i
    while _is_letter(_at(src, i)):
        out.append(src[i])
        i += 1
    return i


def preprocess(src: list[int]) -> list[int]:
    """명령 이름을 내부 코드로 바꾼다(FUN_10657190)."""
    out: list[int] = []
    i = 0
    while (c := _at(src, i)) != 0:
        if c == 0x27:
            double = _at(src, i + 1) == 0x27
            out.append(0x2033 if double else 0x2032)
            i += 2 if double else 1
        elif c == QUOTE:
            out.append(QUOTE)
            i += 1
            while (q := _at(src, i)) != 0:
                out.append(q)
                i += 1
                if q == QUOTE:
                    break
        elif c in _SPLIT_BEFORE or 0x30 <= c <= 0x39 or _at(src, i + 1) in _SPLIT_AFTER:
            out.append(c)
            i += 1
        else:
            index = _command_index(src, i) if c != BACKSLASH else 0
            if index == 0:
                i = _copy_word(src, i, out)
                continue
            name, value, kind = COMMANDS[index]
            kind &= 0xFE
            if kind == 2:
                out += [2, index]
            elif kind == 0x80:
                out += [0xFF, value]
            elif kind == 0xC0:
                out += [4, value]
            elif kind == 0xE0:
                out += [3, value]
            else:
                out.append(value)
            i += len(name)
    return out


# ---- 단어 나누기(FUN_10655a60) ----


def lex(buf: list[int], p: int) -> tuple[int, list[int]]:
    """(종류, 단어). 종류 1은 텍스트, 그 밖은 기호·명령 번호. 따옴표 글은 [", 시작 위치, 길이]."""
    c = _at(buf, p)
    if c == 0:
        return 0, []
    if c in (CARET, UNDERSCORE, HASH, LBRACE, RBRACE, AMP):
        return c, [c]
    if c == QUOTE:
        length = 1
        k = p + 1
        while (u := _at(buf, k)) != 0:
            if u == QUOTE:
                length += 1
                break
            k += 1
            length += 1
        return TEXT, [QUOTE, p + 1, length]
    if c in (TILDE, BACKQUOTE):
        fill = SPACE if c == TILDE else c
        tok = [fill]
        while _at(buf, p + len(tok)) == c and len(tok) + 1 < 10:
            tok.append(fill)
        return TEXT, tok
    if c in (3, 4):
        return c, [c, _at(buf, p + 1)]
    if c == 0xFF:
        return _at(buf, p + 1), [0xFF, _at(buf, p + 1)]
    if c == BACKSLASH:
        u = _at(buf, p + 1)
        if not _is_letter(u):
            return TEXT, [BACKSLASH] if u == 0 else [BACKSLASH, u]
        tok = [BACKSLASH]
        while _is_letter(u := _at(buf, p + len(tok))) and len(tok) < 10:
            tok.append(u)
        return TEXT, tok
    tok: list[int] = []
    count = 1
    k = p
    if c == 2:
        index = _at(buf, p + 1)
        tok = [2, index]
        count = len(COMMANDS[index][0]) + 3
        k = p + 2
        if _at(buf, k) in _NAME_STOP:
            return TEXT, tok
    u = _at(buf, k)
    if u != 0:
        while u != SPACE and count < 10:
            tok.append(SPACE if u == TILDE else u)
            count += 1
            k += 1
            u = _at(buf, k)
            if (u != TILDE and u in _WORD_STOP) or u == 0:
                break
    return TEXT, tok


# ---- 구문 트리(FUN_106541f0·FUN_106547d0·FUN_106552d0·FUN_106555e0) ----


class Node:
    """0x40바이트 노드. text는 +0x2c부터 wchar 10개이고 그 뒤쪽 칸을 다른 필드로도 쓴다(+0x3a, +0x3c, +0x3e)."""

    __slots__ = ("kind", "child", "next", "sup", "sub", "size", "y", "top", "bottom", "x", "width", "text")

    def __init__(self) -> None:
        self.kind = 0
        self.child = self.next = self.sup = self.sub = 0
        self.size = 0  # +0x14, 글자 크기 백분율
        self.y = 0  # +0x18, 기준선 높이(글자 크기 백분율, 위가 +). 그리기에만 쓴다
        self.top = self.bottom = 0  # +0x1c, +0x20
        self.x = self.width = 0  # +0x24, +0x28
        self.text = [0] * 10

    def set_text(self, chars: list[int]) -> None:
        for i, ch in enumerate(chars[:10]):
            self.text[i] = ch
        if len(chars) < 10:
            self.text[len(chars)] = 0

    @property
    def rows(self) -> int:  # +0x3c: 쌓기·행렬 줄 수, LEFT 겹침 깊이
        return self.text[8]

    @rows.setter
    def rows(self, value: int) -> None:
        self.text[8] = value & 0xFFFF

    @property
    def link(self) -> int:  # +0x3e: 행렬 &열 표시, 바깥 LEFT 노드 번호
        return self.text[9]

    @link.setter
    def link(self, value: int) -> None:
        self.text[9] = value & 0xFFFF


class Parser:
    def __init__(self, buf: list[int]) -> None:
        self.buf = buf
        self.p = 0
        self.nodes = [Node() for _ in range(POOL_SIZE)]
        self.free = 1

    def alloc(self) -> int:
        """FUN_106558b0. 풀이 바닥나면 0."""
        index = self.free
        if index == 0 or index >= POOL_SIZE - 1:
            return 0
        self.free = index + 1
        self.nodes[index] = Node()
        return index

    def skip_spaces(self) -> None:
        while _at(self.buf, self.p) == SPACE:
            self.p += 1

    def consume(self, tok: list[int]) -> None:
        self.p += _cstrlen(tok)

    def parent_of(self, target: int) -> int:
        """FUN_10655820: target을 가리키는 노드(첫 자식·다음·위첨자·아래첨자 중 하나)."""
        stack = [self.nodes[0].next]
        while stack:
            index = stack.pop()
            while index:
                node = self.nodes[index]
                if target in (node.child, node.next, node.sup, node.sub):
                    return index
                stack += [n for n in (node.child, node.sup, node.sub) if n]
                index = node.next
        return 0

    def parse_list(self, index: int, size: int) -> int:
        """FUN_106541f0: '}'·'#'·끝까지 원자 목록. 반환값은 오류 코드(어셈블리 0x10654618~0x10654731)."""
        self.skip_spaces()
        kind, tok = lex(self.buf, self.p)
        if kind in (CARET, UNDERSCORE) or FROM <= kind <= CHOOSE:
            return 5
        if _at(self.buf, self.p) == 0:
            return 0
        current = index
        error = 0
        while True:
            # LAB_10654297
            if kind in (RBRACE, HASH, 0) or error:
                return error
            node = self.nodes[current]
            to_followers = False
            if kind in _ITEM_KINDS:
                node.kind = kind
                error = self.parse_item(current, size)
            elif kind in (RM, IT, BOLD, AMP):
                node.size = size
                node.kind = kind
                self.consume(tok)
                to_followers = True
            elif kind in (CARET, UNDERSCORE):
                self.consume(tok)
                script = self.alloc()
                if script == 0:
                    return 1
                if kind == CARET:
                    node.sup = script
                else:
                    node.sub = script
                error = self.parse_item(script, size * 64 // 100)
            elif kind == GROUP:
                self.p += 1
                node.kind = kind
                node.child = self.alloc()
                if node.child == 0:
                    return 1
                node.size = size
                error = self.parse_list(node.child, size)
                if _at(self.buf, self.p) != RBRACE:
                    return 3
                self.p += 1
            else:
                return 9
            if error and not to_followers:
                return error
            # LAB_10654448: over·atop·choose는 앞 원자를 감싸고, ^·_는 앞 원자를 묶음으로 감싼다
            while True:
                if _at(self.buf, self.p) == 0:
                    return error
                self.skip_spaces()
                kind, tok = lex(self.buf, self.p)
                if kind in (RBRACE, HASH, 0):
                    return error
                if kind in (OVER, ATOP, CHOOSE):
                    self.consume(tok)
                elif kind not in (CARET, UNDERSCORE):
                    break
                wrapper = self.alloc()
                if wrapper == 0:
                    return 1
                outer = self.nodes[wrapper]
                outer.size = size
                if kind in (CARET, UNDERSCORE):
                    outer.kind = GROUP
                    outer.child = current
                elif kind == CHOOSE:
                    outer.kind = BINOM
                    outer.set_text([6] + [ord(ch) for ch in "choose"])
                    outer.sup = current
                else:
                    outer.kind = kind
                    outer.set_text([kind])
                    outer.sup = current
                parent = self.nodes[self.parent_of(current)]
                if parent.child == current:
                    parent.child = wrapper
                elif parent.next == current:
                    parent.next = wrapper
                elif parent.sup == current:
                    parent.sup = wrapper
                elif parent.sub == current:
                    parent.sub = wrapper
                else:
                    return 0xB
                current = wrapper
                if kind in (CARET, UNDERSCORE):
                    break
                outer.sub = self.alloc()
                if outer.sub == 0:
                    return 1
                error = self.parse_item(outer.sub, size)
                if error:
                    return error
            # LAB_1065462e: ^·_는 감싼 묶음에 붙이고, 이미 있으면 다음 원자를 새로 만든다
            if kind not in (CARET, UNDERSCORE) or (
                self.nodes[current].sup if kind == CARET else self.nodes[current].sub
            ):
                following = self.alloc()
                self.nodes[current].next = following
                if following == 0:
                    return 1
                current = following
            if _at(self.buf, self.p) == 0:
                return error

    def parse_item(self, index: int, size: int) -> int:
        """FUN_106547d0: 원자 하나와 뒤따르는 ^·_."""
        base = index
        base_size = size
        node = self.nodes[index]
        node.size = size
        while _at(self.buf, self.p) != 0:
            self.skip_spaces()
            kind, tok = lex(self.buf, self.p)
            if kind in (RBRACE, 0, HASH, AMP):
                return 5
            if kind in (CARET, UNDERSCORE) or FROM <= kind <= CHOOSE:
                return 5
            node.kind = kind
            error = self.parse_atom(index, kind, tok, size)
            if error:
                return error
            self.skip_spaces()
            kind, tok = lex(self.buf, self.p)
            if kind == CARET:
                if self.nodes[base].sup != 0:
                    return 0
            elif kind != UNDERSCORE:
                return 0
            self.consume(tok)
            script = self.alloc()
            if script == 0:
                return 1
            if kind == CARET:
                self.nodes[base].sup = script
                self.nodes[script].size = base_size * 64 // 100
                return self.parse_item(script, base_size * 64 // 100)
            if self.nodes[base].sub != 0:
                base = self.nodes[base].sub
                base_size = self.nodes[base].size
            self.nodes[base].sub = script
            size = base_size * 64 // 100
            index = script
            node = self.nodes[index]
            node.size = size
        return 0

    def parse_atom(self, index: int, kind: int, tok: list[int], size: int) -> int:
        node = self.nodes[index]
        if kind == TEXT:
            return self._text(node, tok)
        if kind == BIG_OPERATOR:
            return self._big_operator(index, tok, size)
        if kind == BINOM:
            self.consume(tok)
            node.set_text([6, ord("("), ord(")")])
            node.sup = self.alloc()
            if node.sup == 0:
                return 1
            error = self.parse_item(node.sup, size)
            if error:
                return error
            node.sub = self.alloc()
            return self.parse_item(node.sub, size) if node.sub else 1
        if kind in (MATRIX, 0x10, 0x11, 0x12):
            brackets = {MATRIX: [0], 0x10: [6, 0x28, 0x29], 0x11: [6, 0x5B, 0x5D], 0x12: [6, 0x7C, 0x7C]}[kind]
            node.set_text(brackets)
            node.kind = MATRIX
            self.consume(tok)
            return self.parse_matrix(index, size)
        if kind in PILES:
            if kind == 0x1C:
                node.set_text([6, LBRACE])
            self.consume(tok)
            return self.parse_pile(index, size)
        if kind in (0x18, 0x19):
            self.consume(tok)
            self.skip_spaces()
            kind2, _ = lex(self.buf, self.p)
            c = _at(self.buf, self.p)
            if c == 0xFF or kind2 == 0 or c == RBRACE:
                return 10
            self.p += 1
            node.text[0] = c
            node.sup = self.alloc()
            if node.sup == 0:
                return 1
            error = self.parse_item(node.sup, size)
            if node.kind != 0x18 or error:
                return error
            kind2, _ = lex(self.buf, self.p)
            if kind2 == 0 or _at(self.buf, self.p) == RBRACE:
                return 10
            node.sub = self.alloc()
            return self.parse_item(node.sub, size) if node.sub else 1
        if kind == SCALE:
            self.consume(tok)
            self.skip_spaces()
            if not 0x30 <= _at(self.buf, self.p) <= 0x39:
                return 10
            value = 0
            while 0x30 <= (d := _at(self.buf, self.p)) <= 0x39:
                value = (value * 10 + d - 0x30) & 0xFFFF
                self.p += 1
            node.text[0] = value if 0 < value <= 100 else 100
            return 0
        if kind in (0x21, LEFT, RIGHT, BIGG):
            if kind == 0x21:
                node.text[5], node.text[6] = 0x2F, 0
            self.consume(tok)
            self.skip_spaces()
            kind2, _ = lex(self.buf, self.p)
            c = _at(self.buf, self.p)
            if c == 0xFF or kind2 == 0 or (node.kind != RIGHT and c == RBRACE):
                return 10
            self.p += 1
            node.text[0] = SPACE if c in (0x2E, TILDE) else c
            return 0
        if kind in (ACCENT, 0x6F, 0x75, 0x13, SQRT):
            if kind in (ACCENT, 0x6F, 0x75):
                accent = {ACCENT: _at(tok, 1), 0x6F: 0x0305, 0x75: 0x0332}[kind]
                node.kind = kind = ACCENT
            self.consume(tok)
            node.set_text([accent] if kind == ACCENT else tok)
            node.child = self.alloc()
            return self.parse_item(node.child, size) if node.child else 1
        if kind == GROUP:
            self.p += 1
            node.child = self.alloc()
            if node.child == 0:
                return 1
            error = self.parse_list(node.child, size)
            if _at(self.buf, self.p) != RBRACE:
                return 10
            self.p += 1
            return error
        return 9

    def _text(self, node: Node, tok: list[int]) -> int:
        if tok[0] == QUOTE:
            k = tok[1]
            while (c := _at(self.buf, k)) != QUOTE:
                if c == 0:
                    return 10
                k += 1
            self.p += tok[2]
            node.set_text(tok)
            return 0
        self.consume(tok)
        if tok[0] == BACKSLASH:
            chars = [6]
            for c in tok[1:]:
                if c in (0, BACKSLASH) or len(chars) >= 9:
                    break
                chars.append(c)
            node.set_text(chars)
        elif tok[0] == 2:
            name = [ord(ch) for ch in COMMANDS[tok[1]][0]]
            rest = tok[2:] if len(name) <= 8 else []
            node.set_text([6] + name + [4] + rest)
        else:
            node.set_text(tok)
        return 0

    def _big_operator(self, index: int, tok: list[int], size: int) -> int:
        node = self.nodes[index]
        self.consume(tok)
        symbol = _at(tok, 1)
        if symbol in (0x222B, 0x222E):
            node.kind = INTEGRAL
        if symbol in (0x48, 0x49):
            node.set_text([6] + [ord(ch) for ch in ("Lim" if symbol == 0x49 else "lim")])
        else:
            node.set_text([6, symbol])
        while True:
            self.skip_spaces()
            if _at(self.buf, self.p) == 0:
                return 0
            kind, tok = lex(self.buf, self.p)
            if kind not in (FROM, TO) or (node.sub if kind == FROM else node.sup) != 0:
                return 0
            self.consume(tok)
            script = self.alloc()
            if script == 0:
                return 1
            if kind == FROM:
                node.sub = script
            else:
                node.sup = script
            error = self.parse_item(script, size * 64 // 100)
            if error:
                return error

    def parse_pile(self, index: int, size: int) -> int:
        """FUN_106555e0: { 줄 # 줄 ... }. 줄 머리 노드를 첫 자식 칸으로 잇고 줄 내용은 머리의 다음 칸에 둔다."""
        pile = self.nodes[index]
        self.skip_spaces()
        if _at(self.buf, self.p) != LBRACE:
            return 10
        self.p += 1
        count = 0
        holder = self.alloc()
        pile.child = holder
        while holder:
            count += 1
            self.nodes[holder].size = size
            content = self.alloc()
            self.nodes[holder].next = content
            if content == 0:
                break
            error = self.parse_list(content, size)
            if error:
                pile.rows = count
                return error
            self.skip_spaces()
            kind, tok = lex(self.buf, self.p)
            if kind != HASH:
                if _at(self.buf, self.p) == RBRACE:
                    self.p += 1
                break
            self.consume(tok)
            following = self.alloc()
            self.nodes[holder].child = following
            holder = following
        pile.rows = count
        return 0

    def parse_matrix(self, index: int, size: int) -> int:
        """FUN_106552d0: matrix{ 줄 & 칸 # ... } 또는 matrix{ pile{...} pile{...} }."""
        matrix = self.nodes[index]
        self.skip_spaces()
        if _at(self.buf, self.p) != LBRACE:
            return 10
        self.p += 1
        self.skip_spaces()
        kind, tok = lex(self.buf, self.p)
        count = 0
        if kind in (0x15, 0x16, 0x17):
            matrix.link = 0
            self.consume(tok)
            self.skip_spaces()
            if _at(self.buf, self.p) != LBRACE:
                return 10
            column = self.alloc()
            matrix.child = column
            while column:
                count += 1
                self.nodes[column].kind = kind
                self.nodes[column].size = size
                if self.parse_pile(column, size):
                    break
                self.skip_spaces()
                if _at(self.buf, self.p) != LBRACE:
                    kind, tok = lex(self.buf, self.p)
                    if kind not in (0x15, 0x16, 0x17):
                        break
                    self.consume(tok)
                    self.skip_spaces()
                    if _at(self.buf, self.p) != LBRACE:
                        return 10
                following = self.alloc()
                self.nodes[column].next = following
                column = following
        else:
            if kind in (0, RBRACE):
                return 10
            matrix.link = 1
            row = index
            while True:
                holder = self.alloc()
                self.nodes[row].child = holder
                if holder == 0:
                    break
                count += 1
                row = holder
                self.nodes[row].size = size
                content = self.alloc()
                self.nodes[row].next = content
                if content == 0 or self.parse_list(content, size):
                    break
                self.skip_spaces()
                kind, tok = lex(self.buf, self.p)
                if kind != HASH:
                    break
                self.consume(tok)
        if _at(self.buf, self.p) == RBRACE:
            self.p += 1
        matrix.rows = count
        return 0


# ---- 글자 폭(HFT 글꼴, FUN_10655ea0·FUN_10650f40) ----

#: 글꼴 언어 순서(HncDRGetCharLang 반환값, hftinfo.dat·fontinfo.dat 절 이름).
LANGUAGES = ("Hangul", "Latin", "Hanja", "Japanese", "Other", "Symbol", "User")


def char_language(code: int) -> int:
    """HncBaseDraw HncDRGetCharLang(0x1006e390)를 둘째 인자 1로 옮긴 것. U+FF00 이상은 내부 표(FUN_10081930)를
    찾으므로 옮기지 않았다."""
    if code < 0xAC00:
        if code < 0x2B0:
            return 1
        if code < 0x590:
            return 5
        if code < 0x700:
            return 4
        if code < 0x1100:
            return 5
        if code < 0x1200:
            return 0
        if code < 0x2000 or 0x2018 <= code < 0x2020:
            return 1
        if code < 0x2018:
            return 5
        if code < 0x3040:
            return 3 if code in (0x3005, 0x3006, 0x3012, 0x3013, 0x301E, 0x301F) else 5
        if code < 0x3100:
            return 3
        if code < 0x3130:
            return 2
        if code < 0x3190:
            return 0
        if code < 0x32D0:
            return 3 if code in (0x3231, 0x3232, 0x3239, 0x32A4, 0x32A5, 0x32A6, 0x32A7, 0x32A8) else 5
        if code < 0x3380 or code == 0x33CD:
            return 3
        if code < 0x3400:
            return 5
        if code < 0xA000:
            return 2
        return 0 if 0xA960 <= code < 0xA980 else 5
    if code < 0xD800:
        return 0
    if code < 0xE000:
        return 1 if code < 0xD840 else 2 if code < 0xD880 else 5
    if code < 0xE0BC or 0xF670 <= code < 0xF780:
        return 6
    if code < 0xF670:
        return 5 if 0xF000 <= code <= 0xF0FF else 0
    if code < 0xF900:
        return 0
    if code < 0xFB00:
        return 2
    if code < 0xFE00 or 0xFE70 <= code < 0xFF00:
        return 4
    if code < 0xFE70:
        return 5
    raise KeyError(f"U+{code:04X}의 글꼴 언어는 HncBaseDraw 내부 표에서 정해져 옮기지 않았습니다")


class FontTables:
    def __init__(self, raw: dict) -> None:
        self.fonts = raw["fonts"]
        self.chains = raw["chains"]
        self.fallback = [self.fonts[name] for name in raw["fallback"]]
        self.ttf = raw["ttf"]
        self.general = raw["codes"]["general"]
        pairs = raw["codes"]["pairs"]
        self.pairs = {pairs[i]: pairs[i + 1] for i in range(0, len(pairs) - 1, 2)}

    def hchar(self, code: int) -> int:
        """HncUnicodeToHChar(HncFoundation.dll): 0x4000 아래는 u300_gen 표, 한글 음절은 조합형, 나머지는 u2h 쌍."""
        if 0xAC00 <= code <= 0xD7A3:
            return int.from_bytes(chr(code).encode("johab"), "big")
        if code < 0x4000 and self.general[code] != NO_HCHAR:
            return self.general[code]
        return self.pairs.get(code, NO_HCHAR)

    def width(self, code: int, height: int, italic: bool, bold: bool, ratio: int = 100) -> int:
        """GetTextExtentPointW cx. ppem = 반올림(높이/75), 폭 = 반올림(em폭 x ppem / em) 픽셀(시험 496건으로 확인).

        글꼴은 HncBaseDraw FUN_10055910처럼 글자 언어의 "수식" 사슬을 앞에서부터 찾는다.
        """
        hchar = self.hchar(code)
        ppem = (height * 2 + UNITS_PER_PIXEL) // (2 * UNITS_PER_PIXEL)
        found = None
        for step in self.chains[char_language(code)]:
            if "ttf" in step:
                pixels = self._ttf_width(step["ttf"], code, ppem, bold)
                if pixels is not None:
                    return pixels * UNITS_PER_PIXEL
                continue
            found = self._find([self.fonts[step["italic" if italic else "regular"]]], hchar)
            if found is not None:
                break
        else:
            # 사슬이 끝나면 FUN_10055820의 기본 글꼴(한양신명조 계열)로 찾는다
            found = self._find(self.fallback, hchar)
        if found is None:
            raise KeyError(
                f"옛 수식 글꼴에 U+{code:04X}(한글 코드 0x{hchar:04X}) 글자가 없어 크기를 계산할 수 없습니다. "
                "저장된 크기를 쓰세요"
            )
        em_width, em = found
        if bold:
            # HncBaseDraw FUN_1001f7e0 0x1001f880: 찾은 HFT 파일이 굵은 꼴이 아니면 합성 굵게로
            # em폭에 (em+10)/20을 더한다.
            # 수식 글꼴 계열에는 굵은 파일이 없다. 대체 글꼴 파일의 꼴은 표에 없어 같은 식을 쓴다(확인하지 못함)
            em_width += (em + 10) // 20
        # 검증 안 됨: 장평(그리기의 늘린 괄호·악센트)은 em폭에 곱해 같은 식으로 반올림한다고 보았다
        return (em_width * ratio * ppem * 2 + em * 100) // (2 * em * 100) * UNITS_PER_PIXEL

    def family(self, code: int, italic: bool) -> str:
        """width()와 같은 사슬에서 그 글자를 가진 글꼴의 이름(HFT는 글꼴 family, TTF는 면 이름)."""
        hchar = self.hchar(code)
        for step in self.chains[char_language(code)]:
            if "ttf" in step:
                if self._ttf_has(step["ttf"], code):
                    return step["ttf"]
                continue
            font = self.fonts[step["italic" if italic else "regular"]]
            if self._find([font], hchar) is not None:
                return font["family"]
        for font in self.fallback:
            if self._find([font], hchar) is not None:
                return font["family"]
        raise KeyError(f"옛 수식 글꼴에 U+{code:04X} 글자가 없습니다")

    def _ttf_has(self, face: str, code: int) -> bool:
        return face == self.ttf["face"] and any(first <= code <= last for first, last, _ in self.ttf["runs"])

    def _ttf_width(self, face: str, code: int, ppem: int, bold: bool) -> int | None:
        """사슬의 TTF 글꼴 GDI 전진 폭(픽셀). 그 글꼴에 없는 글자는 None.
        기울임은 GDI 합성이라 폭이 같다(시험 s00~s02 그리스 문자 6건으로 확인)."""
        if face != self.ttf["face"]:
            raise KeyError(f"U+{code:04X}: TTF 글꼴 {face}의 폭은 표에 없습니다")
        for first, last, index in self.ttf["runs"]:
            if first <= code <= last:
                if bold or not 1 <= ppem <= len(self.ttf["vectors"][index]):
                    raise KeyError(f"U+{code:04X}: 바탕 글꼴의 굵게·ppem {ppem} 폭은 표에 없습니다")
                return self.ttf["vectors"][index][ppem - 1]
        return None

    @staticmethod
    def _find(fonts: list[dict], hchar: int) -> tuple[int, int] | None:
        for font in fonts:
            for first, last, widths in font["ranges"]:
                if first <= hchar <= last:
                    return (widths if isinstance(widths, int) else widths[hchar - first]), font["em"]
        return None


@lru_cache(maxsize=1)
def font_tables() -> FontTables:
    with gzip.open(_DATA, "rt", encoding="utf-8") as fh:
        return FontTables(json.load(fh))


def atom_kind(c: int) -> int:
    """FUN_10656730: 0 보통, 2 + - *, 3 = < >, 4 여는 괄호, 5 닫는 괄호, 6 쉼표."""
    if c in (0x2B, 0x2D, 0x2A):
        return 2
    if c in (0x3D, 0x3C, 0x3E):
        return 3
    if c in (0x2066, LBRACE, 0x5B, 0x28):
        return 4
    if c in (0x2067, RBRACE, 0x5D, 0x29):
        return 5
    return 6 if c == 0x2C else 0


class Layout:
    """FUN_106578c0의 지역 구조체(this). 필드 옆 숫자는 오프셋이다."""

    def __init__(self, parser: Parser, font_size: int, carried_flags: int = 0) -> None:
        self.parser = parser
        self.nodes = parser.nodes
        self.font_size = font_size  # +0x10
        self.scale = 100  # +0x08
        self.style = 4  # +0x14, 4 기울임(IT) / 6 똑바로(RM)
        self.bold = 0  # +0x18, BOLD 안에서 2
        self.flags = carried_flags  # DAT_1089372c: 비트0 기울임, 비트1 굵게(FUN_10657830)
        self.in_pile = 0  # +0x34
        self.height = font_size  # DAT_10893710, 다음에 만들 글꼴의 높이
        self.ratio = 100  # DAT_1089371b, 장평(그리기에서만 바뀐다)
        self.font = (font_size, bool(self.flags & 1), bool(self.flags & 2), 100)  # +0x60, 마지막으로 만든 글꼴
        self.tables = font_tables()

    def create_font(self) -> None:
        """FUN_10650f40: 지금 높이·플래그로 글꼴을 다시 만든다. 굵게(비트1)는 lfWeight 700이다."""
        self.font = (self.height, bool(self.flags & 1), bool(self.flags & 2), self.ratio)

    def measure(self, code: int) -> int:
        return self.tables.width(code, *self.font)

    def space(self) -> int:
        return self.measure(SPACE)

    def set_height(self, size: int) -> None:
        """FUN_106568e0."""
        if self.scale != 100:
            size = muldiv(size, self.scale, 100)
        self.height = muldiv(size, self.font_size, 100)

    # FUN_10655ea0
    def text_width(self, chars: list[int]) -> int:
        space = self.space()
        quarter = cdiv(space, 4)
        i = 0
        if chars[0] == 6:
            style = 6
            i = 1
        else:
            style = self.style
        width = quarter if chars[0] == 6 else 0
        self.flags = self.bold | (1 if style == 4 else 0)
        self.create_font()
        prev = atom_kind(_at(chars, i))
        while True:
            c = _at(chars, i)
            if c in (0, QUOTE):
                return width
            if c == 4:
                style = 4
                width += quarter
                i += 1
                continue
            if style == 4:
                self.flags = self.bold | (1 if _is_letter(c) else 0)
                self.create_font()
            if c in (0x2032, 0x2033, 0x2034):
                self.flags = self.bold | 1
                self.create_font()
                width += self.measure(0x27) * (c - 0x2031)
            elif c == BACKQUOTE:
                width += quarter
            else:
                width += self.measure(c)
            i += 1
            nxt = _at(chars, i)
            if nxt != 0:
                kind = atom_kind(nxt)
                width += muldiv(space, SPACING[prev][kind], 18)
                prev = kind

    def quoted(self, node: Node) -> list[int]:
        return self.parser.buf[node.text[1] :] + [0]

    def text_of(self, node: Node) -> list[int]:
        return self.quoted(node) if node.text[0] == QUOTE else node.text

    # FUN_10653940
    def vertical(self, index: int) -> None:
        nodes = self.nodes
        saved_height, saved_scale = self.height, self.scale
        if index == 0:
            return
        size = nodes[index].size
        if self.scale != 100:
            size = muldiv(size, self.scale, 100)
        top_max = size if nodes[index].kind != SCALE else 0
        bottom_min = 0
        depth = 0
        open_left = 0
        last_left = 0
        while index:
            node = nodes[index]
            node.top, node.bottom = size, 0
            k = node.kind
            skip_scripts = False
            if k in (ACCENT, 0x13):
                self.vertical(node.child)
                node.top, node.bottom = nodes[0].top, nodes[0].bottom
                if node.text[0] != 0x332 and k != 0x13:
                    node.top = cdiv(size * 15, 100) + nodes[0].top
                skip_scripts = True
            elif k in (OVER, ATOP, BINOM, 0x18, 0x19, 0x23):
                self.vertical(node.sup)
                shift = cdiv(size * 0x37, 100) - nodes[0].bottom
                node.top = nodes[0].top + shift
                self.shift(node.sup, shift)
                if node.sub:
                    self.vertical(node.sub)
                    shift = size - cdiv(size * 0x3B, 100) - nodes[0].top
                    node.bottom = nodes[0].bottom + shift
                    self.shift(node.sub, shift)
                skip_scripts = True
            elif k == MATRIX and node.link == 0:
                self.vertical(node.child)
                node.top, node.bottom = nodes[0].top, nodes[0].bottom
                skip_scripts = True
            elif k in PILES or k == MATRIX:
                self.vertical_pile(node, size)
                skip_scripts = True
            elif k == SCALE:
                self.scale = node.text[0]
                size = node.size if self.scale == 100 else muldiv(node.size, self.scale, 100)
                node.top = size
                skip_scripts = True
            elif k == LEFT:
                node.rows = depth
                if depth:
                    node.link = last_left
                depth += 1
                last_left = index
                open_left = index
                skip_scripts = True
            elif k == SQRT:
                self.vertical(node.child)
                node.top = cdiv(size * 10, 100) + nodes[0].top
                node.bottom = nodes[0].bottom
                skip_scripts = True
            elif k not in (TEXT, BIG_OPERATOR, RIGHT, BIGG, INTEGRAL, GROUP):
                skip_scripts = True
            if not skip_scripts:
                if k == INTEGRAL:
                    extra = cdiv(size * 0x28, 100)
                    node.bottom = extra - size
                    node.top = size + extra
                if k in (INTEGRAL, GROUP) and node.child:
                    self.vertical(node.child)
                    node.top, node.bottom = nodes[0].top, nodes[0].bottom
                if k in (RIGHT, BIGG):
                    if depth == 0 or k != RIGHT:
                        node.top, node.bottom = top_max, bottom_min
                    else:
                        depth -= 1
                        left = nodes[open_left]
                        node.top, node.bottom = left.top, left.bottom
                        if depth:
                            last_left = left.link
                            open_left = last_left
                top_max, bottom_min = self.vertical_scripts(node, size, top_max, bottom_min)
            if depth and k not in (LEFT, RIGHT, BIGG):
                left_index, remaining = open_left, depth
                while True:
                    left = nodes[left_index]
                    if left.top < node.top:
                        left.top = node.top
                    elif left.bottom <= node.bottom:
                        break
                    if node.bottom < left.bottom:
                        left.bottom = node.bottom
                    remaining -= 1
                    if remaining == 0:
                        break
                    left_index = left.link
            top_max = max(top_max, node.top)
            bottom_min = min(bottom_min, node.bottom)
            index = node.next
        nodes[0].top, nodes[0].bottom = top_max, bottom_min
        self.height, self.scale = saved_height, saved_scale

    def vertical_scripts(self, node: Node, size: int, top_max: int, bottom_min: int) -> tuple[int, int]:
        """FUN_10653940 caseD_1: 위첨자·아래첨자 자리(RIGHT의 첨자는 node가 아닌 목록 범위를 넓힌다)."""
        nodes = self.nodes
        k = node.kind
        if node.sup:
            self.vertical(node.sup)
            raise_by = cdiv(size * 0x32, 100) if k in (TEXT, RIGHT, GROUP) else size
            lift = node.top - size + raise_by
            if lift < raise_by or k == INTEGRAL:
                lift = raise_by
            shift = lift - nodes[0].bottom
            self.shift(node.sup, shift)
            reach = nodes[0].top + shift
            if k == RIGHT:
                top_max = max(reach, top_max)
            elif node.top < reach:
                node.top = reach
        if node.sub:
            self.vertical(node.sub)
            drop = cdiv(size * (-0x14 if k in (TEXT, RIGHT, GROUP) else -0x40), 100)
            if k != INTEGRAL:
                drop += node.bottom
            shift = drop + (cdiv(size << 6, 100) - nodes[0].top)
            self.shift(node.sub, shift)
            reach = nodes[0].bottom + shift
            if k == RIGHT:
                bottom_min = reach if node.bottom < bottom_min else bottom_min
            elif reach < node.bottom:
                node.bottom = reach
        return top_max, bottom_min

    def vertical_pile(self, pile: Node, size: int) -> None:
        """FUN_10653940 caseD_15: 줄 간격 크기의 20%, 가운데 줄이 축에 오게 놓는다."""
        nodes = self.nodes
        above = (pile.rows + 1) >> 1
        gap = muldiv(size, 0x14, 100)
        offset = 0
        prev_bottom = 0
        row = pile.child
        while row:
            self.vertical(nodes[row].next)
            nodes[row].top, nodes[row].bottom = nodes[0].top, nodes[0].bottom
            if above:
                if offset:
                    step = size - nodes[0].top + gap
                    offset += cdiv(gap, 2) - prev_bottom if step + prev_bottom < 0 else step
                offset += nodes[0].top
                prev_bottom = nodes[0].bottom
                above -= 1
            row = nodes[row].child
        if (pile.rows & 0xFFFE) == pile.rows:
            offset += cdiv(size, 2)
        pile.top = offset
        first = True
        row_index = pile.child
        while row_index:
            row = nodes[row_index]
            if not first:
                if gap < row.top - size - prev_bottom:
                    prev_bottom -= cdiv(gap, 2)
                else:
                    prev_bottom = row.top - size - gap
                offset += prev_bottom
            offset -= row.top
            if first:
                pile.bottom = offset
            first = False
            # 0x10653eaf: 줄 머리가 아니라 줄 내용(머리의 다음 칸)을 옮긴다. 머리의 자식(다음 줄)까지 옮기지 않는다
            self.shift(row.next, offset)
            prev_bottom = row.bottom
            row_index = row.child
        middle = pile.bottom
        pile.bottom = prev_bottom + offset
        if pile.kind == 0x1C:
            pile.text[7] = (middle - offset) & 0xFFFF
            half = cdiv(middle + offset, 2)
            row_index = pile.child
            while row_index:
                self.shift(nodes[row_index].next, -half)
                row_index = nodes[row_index].child
            pile.top -= half
            pile.bottom -= half

    def shift(self, index: int, dy: int) -> None:
        """FUN_10654190: 목록 전체와 자식의 y 이동(그리기용). 크기 계산에는 쓰이지 않는다."""
        nodes = self.nodes
        while index:
            node = nodes[index]
            node.y += dy
            for child in (node.child, node.sup, node.sub):
                if child:
                    self.shift(child, dy)
            index = node.next

    # FUN_10652ed0
    def horizontal(self, index: int) -> None:
        nodes = self.nodes
        saved = (self.style, self.height, self.scale)
        space = self.space()
        total = 0
        nodes[0].width = 0
        if index == 0:
            self.style, self.height, self.scale = saved
            return
        self.set_height(nodes[index].size)
        em = muldiv(
            nodes[index].size if self.scale == 100 else muldiv(nodes[index].size, self.scale, 100), self.font_size, 100
        )
        kind_now = 4
        while index:
            node = nodes[index]
            if node.kind == AMP and self.in_pile:
                node.width = space
                break
            kind_prev, kind_now = kind_now, 4
            k = node.kind
            if k == BIG_OPERATOR:
                kind_now = 1
                node.width = self.text_width(node.text)
                if node.sup or node.sub:
                    widest = 0
                    if node.sup:
                        self.horizontal(node.sup)
                        widest = nodes[0].width
                        nodes[node.sup].x += cdiv(node.width - widest, 2)
                    if node.sub:
                        self.horizontal(node.sub)
                        nodes[node.sub].x += cdiv(node.width - nodes[0].width, 2)
                        widest = max(widest, nodes[0].width)
                    if node.width < widest:
                        pad = cdiv(widest - node.width, 2)
                        node.x = pad
                        total += pad
                        node.width += pad
                    self.set_height(node.size)
            elif k in (ACCENT, 0x13):
                saved_bold = self.bold
                if node.text[0] == 0x2101:
                    self.bold = 2
                kind_now = 0
                self.horizontal(node.child)
                node.width = nodes[0].width
                if node.text[0] == 0x2101:
                    self.bold = saved_bold
            elif k in (OVER, ATOP, BINOM, 0x18, 0x19, 0x23):
                kind_now = 1
                self.horizontal(node.sup)
                upper = nodes[0].width
                self.horizontal(node.sub)
                lower = nodes[0].width
                if upper < lower:
                    nodes[node.sup].x += cdiv(lower - upper, 2)
                    upper = nodes[0].width
                    pad = 0
                else:
                    pad = cdiv(upper - lower, 2)
                    nodes[node.sub].x += pad
                if k == OVER or k == ATOP:
                    upper += (cdiv(em, 4)) * 2
                    pad = em >> 3 if em >= 0 else -((-em) >> 3)
                elif k == BINOM:
                    bracket = self.text_width(node.text)
                    pad = cdiv(bracket, 3)
                    upper += bracket
                elif k in (0x18, 0x19):
                    upper += em
                    pad = cdiv(em, 2)
                if k != 0x23:
                    nodes[node.sup].x += pad
                    nodes[node.sub].x += pad
                node.width = upper
            elif k in (RM, IT):
                node.width = 0
                self.style = 6 if k == RM else 4
            elif k == BOLD:
                node.width = 0
            elif k == MATRIX and node.link == 0:
                kind_now = 1
                self.horizontal(node.child)
                node.width = nodes[0].width
                if node.text[0] != 0:
                    bracket = self.text_width([0x5B, 0x5D] if node.text[0] == 0x7C else node.text)
                    node.width += bracket
                    nodes[node.child].x += cdiv(bracket, 2)
                column = nodes[node.child].next
                while column:
                    nodes[column].x += space
                    node.width += space
                    column = nodes[column].next
            elif k in PILES or k == MATRIX:
                kind_now = 1
                self.horizontal_pile(node, space)
            elif k == SCALE:
                node.width = 0
                self.scale = node.text[0]
                self.set_height(node.size)
                em = muldiv(muldiv(node.size, self.scale, 100) if self.scale != 100 else node.size, self.font_size, 100)
            elif k == AMP:
                node.width = space
            elif k in (RIGHT, 0x21, LEFT, BIGG, TEXT, GROUP):
                if k == RIGHT:
                    kind_now = 5
                if k in (RIGHT, 0x21, LEFT, BIGG):
                    node.width = self.text_width(node.text)
                if k == TEXT:
                    node.width = self.text_width(self.text_of(node))
                if k in (TEXT, 0x21):
                    kind_now = atom_kind(node.text[0])
                if k == GROUP:
                    self.horizontal(node.child)
                    node.width = nodes[0].width
                if node.sup or node.sub:
                    widest = 0
                    if node.sub:
                        self.horizontal(node.sub)
                        nodes[node.sub].x += node.width
                        widest = nodes[0].width
                    if node.sup:
                        self.horizontal(node.sup)
                        nodes[node.sup].x += node.width
                        widest = max(widest, nodes[0].width)
                    node.width += widest
                    self.set_height(node.size)
            elif k == INTEGRAL:
                kind_now = 1
                node.width = self.measure(0x2320)
                if node.sup or node.sub:
                    widest = 0
                    if node.sup:
                        self.horizontal(node.sup)
                        nodes[node.sup].x += node.width - cdiv(self.measure(0x69), 2)
                        widest = nodes[0].width - cdiv(self.measure(0x69), 2)
                    if node.sub:
                        self.horizontal(node.sub)
                        nodes[node.sub].x += node.width - self.measure(0x7A)
                        widest = max(widest, nodes[0].width - self.measure(0x7A))
                    self.set_height(node.size)
                    node.width += widest
            elif k == SQRT:
                kind_now = 1
                self.horizontal(node.child)
                node.width = nodes[0].width + cdiv(em, 2)
                nodes[node.child].x += cdiv(em, 2)
            gap = muldiv(space, SPACING[kind_prev][kind_now], 18)
            node.x += gap
            total += node.width + gap
            index = node.next
        nodes[0].width = total
        self.style, self.height, self.scale = saved

    def horizontal_pile(self, pile: Node, space: int) -> None:
        """FUN_10652ed0 caseD_15: 칸('&'로 나눈 열)마다 가장 넓은 줄에 맞춰 정렬한다."""
        nodes = self.nodes
        saved_in_pile = self.in_pile
        self.in_pile = 1
        column = 0
        while True:
            column += 1
            widest = 0
            row_index = pile.child
            while row_index:
                row = nodes[row_index]
                cell = self.cell_start(row, column - 1)
                if cell.next == 0:
                    row.width = 0
                else:
                    self.horizontal(cell.next)
                    row.width = nodes[0].width
                    widest = max(widest, nodes[0].width)
                row_index = row.child
            if widest == 0 and column != 1:
                break
            pile.width += widest + (space if column > 1 else 0)
            row_index = pile.child
            while row_index:
                row = nodes[row_index]
                cell = self.cell_start(row, column - 1)
                if cell.next:
                    target = nodes[cell.next] if column == 1 else cell
                    diff = widest - row.width
                    if pile.kind == 0x1D and column == 1 or pile.kind == 0x17:
                        target.x += diff
                    else:
                        if pile.kind in (MATRIX, 0x15):
                            target.x += cdiv(diff, 2)
                            diff -= cdiv(diff, 2)
                        if column > 1:
                            target = nodes[target.next]
                        while target.kind != AMP and target.next:
                            target = nodes[target.next]
                        if target.kind == AMP:
                            target.width += diff
                row_index = row.child
        self.in_pile = saved_in_pile
        if pile.kind == 0x1C or (pile.kind == MATRIX and pile.text[0] != 0):
            bracket = self.text_width(pile.text)
            pile.width += bracket
            if pile.kind == MATRIX:
                bracket >>= 1
            row_index = pile.child
            while row_index:
                nodes[row_index].x += bracket
                row_index = nodes[row_index].child

    def cell_start(self, row: Node, ampersands: int) -> Node:
        node = row
        while ampersands and node.next:
            node = self.nodes[node.next]
            if node.kind == AMP:
                ampersands -= 1
        return node


def clean_script(script: str) -> str:
    """FUN_1026def0: 제어 문자와 짝 없는 대리 문자를 공백으로 바꾼다(엔진에는 UTF-16 단위로 넘긴다)."""
    return "".join(" " if ord(ch) < 0x20 or 0xD800 <= ord(ch) <= 0xDFFF else ch for ch in script)


@dataclass
class LegacyEngineState:
    """수식 사이에 이어지는 엔진 전역 상태. FUN_106578c0은 글꼴 플래그 DAT_1089372c를 초기화하지 않아
    첫 글꼴(공백 폭)이 직전 수식이 끝날 때의 굵게·기울임을 따른다(시험 72건)."""

    flags: int = 0


def laid_out_legacy(script: str, font_size: int, flags: int) -> tuple[Layout, int]:
    """FUN_106578c0의 해석·세로·가로 배치. (배치 문맥, 뿌리 노드 번호). 해석에 실패하면 LegacyEquationError."""
    data = clean_script(script).encode("utf-16-le")
    source = [int.from_bytes(data[i : i + 2], "little") for i in range(0, len(data), 2)]
    buf = preprocess([ord(ch) for ch in PREFIX]) + preprocess(source) + [RBRACE]
    parser = Parser(buf)
    root = parser.alloc()
    parser.nodes[0].next = root
    error = parser.parse_list(root, 100)
    if error:
        raise LegacyEquationError(f"옛 수식 해석 실패(오류 {error})")
    layout = Layout(parser, font_size, flags)
    layout.vertical(root)
    layout.horizontal(root)
    return layout, root


def measure_legacy(script: str, font_size: int, state: LegacyEngineState | None = None) -> tuple[int, int, int]:
    """(폭, 높이, 저장 기준선 백분율). 해석에 실패하면 LegacyEquationError. state를 주면 플래그를 이어받고 남긴다."""
    layout, _ = laid_out_legacy(script, font_size, state.flags if state is not None else 0)
    parser = layout.parser
    if state is not None:
        state.flags = layout.flags
    top, bottom = parser.nodes[0].top, parser.nodes[0].bottom
    height = muldiv(top - bottom + 20, font_size, 100)
    baseline = cdiv(muldiv(top, font_size, 100) * 100, height)
    return parser.nodes[0].width, height, baseline + 1
