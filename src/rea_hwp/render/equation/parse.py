"""수식 스크립트 해석기. EqAppModule.dll의 전처리·단어 나누기·구문 트리(노드 풀)를 그대로 옮겼다.
주소는 EqAppModule.dll 기준이다(HwpPlugin.dll에 같은 코드가 있다)."""

from __future__ import annotations

from .tables import COMMANDS, LOWER_BASE, LOWER_COUNT, UPPER_BASE, UPPER_COUNT

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
#: 명령 번호(eqtable_v60): FROM 6, TO 7, OVER 8, ATOP 9, CHOOSE 10, LSUB 0x2c, LSUP 0x2d.
FROM, TO, OVER, ATOP, CHOOSE, LSUB, LSUP = 6, 7, 8, 9, 10, 0x2C, 0x2D
#: 노드 풀 크기(FUN_1004d0f0: 0x48바이트 노드 1000개, 0번은 머리).
POOL_SIZE = 1000

_SPLIT_BEFORE = frozenset((SPACE, CARET, UNDERSCORE, HASH, LBRACE, RBRACE, AMP, TILDE, BACKQUOTE))
_SPLIT_AFTER = frozenset((SPACE, CARET, UNDERSCORE, HASH, LBRACE, RBRACE, AMP, QUOTE, TILDE, BACKQUOTE))
_WORD_STOP = frozenset((CARET, UNDERSCORE, HASH, LBRACE, RBRACE, AMP, QUOTE, BACKQUOTE, 0xFF, 4, 2, 3))
_NAME_STOP = frozenset((CARET, UNDERSCORE, HASH, LBRACE, RBRACE, AMP, QUOTE, TILDE, BACKQUOTE))
_SCRIPT_TOKENS = frozenset((CARET, UNDERSCORE, LSUP, LSUB))


def _is_letter(c: int) -> bool:
    return 0x61 <= c <= 0x7A or 0x41 <= c <= 0x5A


def _at(buf: list[int], i: int) -> int:
    return buf[i] if 0 <= i < len(buf) else 0


def _name(index: int) -> str:
    return COMMANDS[index][0]


def _name_code(name: str, k: int) -> int:
    return ord(name[k]) if k < len(name) else 0


def table_search(base: int, count: int, text: list[int], start: int) -> int:
    """명령 표 이진 탐색(FUN_1004eaf0). 표 안 상대 번호를 돌려주고, 없으면 0."""

    def name(j: int) -> str:
        return _name(base + j)

    def at(k: int) -> int:
        return _at(text, start + k)

    lower = 1
    found = 0
    first = name(1)
    probe = -1
    if first:
        k, c = 0, _name_code(first, 0)
        while True:
            u = at(k)
            if u == LBRACE or c > u:
                return 0
            if c < u:
                probe = count
                break
            k += 1
            c = _name_code(first, k)
            if c == 0:
                break
    if probe < 0:
        return _scan_forward(name, at, 1, count)
    while True:
        j = probe
        if j == lower:
            return _scan_backward(name, at, j, found)
        nm = name(j)
        if not nm:
            return _scan_forward(name, at, j, count) if j != 0 else _scan_backward(name, at, j, j)
        k, c = 0, _name_code(nm, 0)
        while True:
            u = at(k)
            if u == LBRACE or c > u:
                probe = (j + lower) // 2
                count = j
                break
            if c < u:
                probe = (count + j) // 2
                lower = j
                break
            k += 1
            c = _name_code(nm, k)
            if c == 0:
                return _scan_forward(name, at, j, count)


def _scan_forward(name, at, found: int, count: int) -> int:
    j = found
    candidate = found
    while True:
        while True:
            found = candidate
            j += 1
            if count <= j:
                return found
            nm = name(j)
            candidate = j
            if nm:
                break
        k, c = 0, _name_code(nm, 0)
        while True:
            u = at(k)
            if u == LBRACE or c > u:
                return found
            candidate = found
            if c < u:
                break
            k += 1
            c = _name_code(nm, k)
            candidate = j
            if c == 0:
                break


def _scan_backward(name, at, j: int, found: int) -> int:
    j -= 1
    if j < 1:
        return found
    while True:
        nm = name(j)
        if _name_code(nm, 0) != at(0):
            return found
        order = 0
        if nm:
            k = 0
            while True:
                u = at(k)
                c = _name_code(nm, k)
                if u == LBRACE or c > u:
                    order = -1
                    break
                if c < u:
                    order = 1
                    break
                k += 1
                if _name_code(nm, k) == 0:
                    break
        if order == 0:
            found = j
        j -= 1
        if j < 1:
            return found


def preprocess(src: list[int]) -> list[int]:
    """명령 이름을 내부 코드로 바꾼다(FUN_1004ecb0)."""
    out: list[int] = []
    i = 0
    while True:
        c = _at(src, i)
        if c == 0:
            return out
        if c == 0x27:
            double = _at(src, i + 1) == 0x27
            out.append(0x2033 if double else 0x2032)
            i += 2 if double else 1
            continue
        if c == QUOTE:
            out.append(QUOTE)
            i += 1
            while (q := _at(src, i)) != 0:
                out.append(q)
                i += 1
                if q == QUOTE:
                    break
            continue
        if c in _SPLIT_BEFORE or 0x30 <= c <= 0x39:
            out.append(c)
            i += 1
            continue
        nxt = _at(src, i + 1)
        if nxt in _SPLIT_AFTER:
            out.append(c)
            i += 1
            continue
        index = 0
        if c != BACKSLASH:
            index = _command_index(src, i, c, nxt)
        if index == 0:
            i = _copy_word(src, i, c, out)
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


def _command_index(src: list[int], i: int, c: int, nxt: int) -> int:
    rel = table_search(LOWER_BASE, LOWER_COUNT, src, i)
    if rel:
        return LOWER_BASE + rel
    if 0x41 <= c <= 0x5A and 0x41 <= nxt <= 0x5A:
        rel = table_search(UPPER_BASE, UPPER_COUNT, src, i)
        return UPPER_BASE + rel if rel else 0
    if _is_letter(c) and 0x61 <= nxt <= 0x7A:
        word = [ord(chr(c).upper())]
        k = 1
        u = nxt
        while 0x60 < u:
            if u > 0x7A or k > 9:
                break
            word.append(u - 0x20)
            k += 1
            u = _at(src, i + k)
        rel = table_search(UPPER_BASE, UPPER_COUNT, word, 0)
        if not rel:
            return 0
        name, value, _ = COMMANDS[UPPER_BASE + rel]
        if value in (6, 7) and name != "".join(map(chr, word)):
            return 0
        return UPPER_BASE + rel
    return 0


def _copy_word(src: list[int], i: int, c: int, out: list[int]) -> int:
    out.append(c)
    i += 1
    if c == BACKSLASH:
        u = _at(src, i)
        if not _is_letter(u):
            if u != 0:
                out.append(u)
                i += 1
            return i
        while _is_letter(u := _at(src, i)):
            out.append(u)
            i += 1
    elif _is_letter(c):
        while _is_letter(u := _at(src, i)):
            out.append(u)
            i += 1
    return i


def lex(buf: list[int], p: int) -> tuple[int, list[int]]:
    """단어 하나(FUN_1004afd0). (종류, 단어 버퍼)를 돌려준다. 종류 1은 텍스트, 그 밖은 기호·명령 번호."""
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
        return 1, [QUOTE, p + 1, length]
    if c in (TILDE, BACKQUOTE):
        tok = [c]
        k = p + 1
        while _at(buf, k) == c and len(tok) + 1 < 10:
            tok.append(c)
            k += 1
        return 1, tok
    if c in (3, 4):
        return c, [c, _at(buf, p + 1)]
    if c == 0xFF:
        return _at(buf, p + 1), [0xFF, _at(buf, p + 1)]
    if c == BACKSLASH:
        u = _at(buf, p + 1)
        if not _is_letter(u):
            return 1, [BACKSLASH] if u in (SPACE, 0) else [BACKSLASH, u]
        tok = [BACKSLASH]
        k = p + 1
        while _is_letter(u := _at(buf, k)) and len(tok) < 10:
            tok.append(u)
            k += 1
        return 1, tok
    tok = []
    count = 1
    k = p
    if c == 2:
        index = _at(buf, p + 1)
        tok = [2, index]
        count = len(_name(index)) + 3
        k = p + 2
        if _at(buf, k) in _NAME_STOP:
            return 1, tok
    u = _at(buf, k)
    if u != 0:
        while u != SPACE and count < 10:
            tok.append(u)
            count += 1
            k += 1
            u = _at(buf, k)
            if (u != TILDE and u in _WORD_STOP) or u == 0:
                break
    return 1, tok


def node_text(tok: list[int]) -> list[int]:
    """텍스트 단어를 노드 글자로 바꾼다(FUN_1004aea0). 함수 이름은 8, 따옴표·역빗금 글은 6을 앞에 붙인다."""
    if not tok:
        return []
    first = tok[0]
    if first not in (QUOTE, BACKSLASH):
        out: list[int] = []
        i = 0
        if first == 2:
            out = [8] + [ord(ch) for ch in _name(tok[1])]
            i = 2
        while i < len(tok) and tok[i] != 0:
            if tok[i] == 2:
                out += [ord(ch) for ch in _name(tok[i + 1])]
                i += 2
            else:
                out.append(tok[i])
                i += 1
        return out
    out = [6]
    for u in tok[1:10]:
        if u == first or u == 0:
            break
        out.append(u)
    return out


class Node:
    """구문 노드(0x48바이트). 필드 이름 옆 숫자는 노드 안 오프셋이다."""

    __slots__ = ("kind", "child", "next", "a", "b", "lsup", "lsub", "size", "text", "cols", "rows", "row_form")

    def __init__(self) -> None:
        self.kind = 0  # +0x00
        self.child = 0  # +0x04 묶음 본문, 행렬 행
        self.next = 0  # +0x08 다음 형제
        self.a = 0  # +0x0c 위첨자·왼쪽 피연산자
        self.b = 0  # +0x10 아래첨자·오른쪽 피연산자
        self.lsup = 0  # +0x14
        self.lsub = 0  # +0x18
        self.size = 0  # +0x1c 크기 백분율
        self.text: list[int] = []  # +0x34
        self.cols = 0  # +0x40
        self.rows = 0  # +0x44
        self.row_form = 0  # +0x46

    def text_at(self, i: int) -> int:
        return self.text[i] if i < len(self.text) else 0


class Parser:
    """FUN_1004d0f0이 만드는 노드 풀과 해석 함수들. pos는 전처리 버퍼 안 위치(param_3)다."""

    def __init__(self, buf: list[int]) -> None:
        self.buf = buf
        self.pos = 0
        self.nodes = [Node()]
        self.root = 0

    def alloc(self) -> int:
        if len(self.nodes) >= POOL_SIZE:
            return 0
        self.nodes.append(Node())
        return len(self.nodes) - 1

    def at(self, i: int) -> int:
        return _at(self.buf, i)

    def skip_spaces(self) -> None:
        while self.at(self.pos) == SPACE:
            self.pos += 1

    def parse(self) -> int:
        self.root = self.alloc()
        self.nodes[0].next = self.root
        return self.parse_list(self.root, 100)

    def find_parent(self, start: int, target: int) -> int:
        """FUN_1004d000: target을 가리키는 노드."""
        i = start
        while i:
            n = self.nodes[i]
            if target in (n.child, n.next, n.a, n.b):
                return i
            for sub in (n.child, n.a, n.b, n.lsup, n.lsub):
                if sub:
                    found = self.find_parent(sub, target)
                    if found:
                        return found
            i = n.next
        return 0

    def parse_list(self, index: int, size: int) -> int:
        """묶음 목록(FUN_1004c7e0). 0이면 성공."""
        nodes = self.nodes
        current = index
        node = nodes[index]
        error = 0
        self.skip_spaces()
        code, tok = lex(self.buf, self.pos)
        if code in (CARET, UNDERSCORE, LSUP, LSUB, FROM, TO, OVER, ATOP, CHOOSE):
            return 5
        if self.at(self.pos) == 0:
            return 0
        while code not in (RBRACE, HASH, 0) and error == 0:
            plain_item = False
            if code in _ARG_CODES:
                node.kind = code
                error = self.parse_arg(current, size)
            elif code in (0xC, 0xD, 0xE, AMP):
                node.size = size
                node.kind = code
                self.pos += len(tok)
                plain_item = True
            elif code in (LSUB, LSUP, CARET, UNDERSCORE):
                self.pos += len(tok)
                sub = self.alloc()
                if sub == 0:
                    return 1
                slot, other = _SCRIPT_SLOTS[code]
                setattr(node, slot, sub)
                error = self.parse_arg(sub, size * 64 // 100)
                moved = getattr(nodes[sub], other)
                if moved:
                    setattr(node, other, moved)
                    setattr(nodes[sub], other, 0)
            elif code == LBRACE:
                self.pos += 1
                node.kind = LBRACE
                body = self.alloc()
                node.child = body
                if body == 0:
                    return 1
                node.size = size
                error = self.parse_list(body, size)
                if self.at(self.pos) != RBRACE:
                    return 3
                self.pos += 1
            else:
                return 9
            if not plain_item and error:
                break
            if self.at(self.pos) == 0:
                break
            while True:
                self.skip_spaces()
                peek = self.pos
                code, tok = lex(self.buf, self.pos)
                if code in (RBRACE, HASH, 0):
                    break
                wrap = code in (CARET, UNDERSCORE, LSUP, LSUB)
                if code not in (OVER, ATOP, CHOOSE) and not wrap:
                    current = self.alloc()
                    node.next = current
                    node = nodes[current]
                    if current == 0:
                        error = 1
                    break
                if not wrap:
                    self.pos += len(tok)
                    peek = self.pos
                new = self.alloc()
                if new == 0:
                    error = 1
                    break
                infix = nodes[new]
                infix.size = size
                if wrap:
                    infix.kind = LBRACE
                    infix.child = current
                else:
                    infix.kind = code
                    infix.text = [code]
                    if code == CHOOSE:
                        infix.kind = 0xB
                        infix.text = [6, ord("("), ord(")")]
                    infix.a = current
                parent = self.find_parent(nodes[0].next, current)
                p = nodes[parent]
                if p.child == current:
                    p.child = new
                elif p.next == current:
                    p.next = new
                elif p.a == current:
                    p.a = new
                elif p.b == current:
                    p.b = new
                else:
                    error = 10
                    break
                current = new
                node = infix
                if not wrap:
                    right = self.alloc()
                    infix.b = right
                    if right == 0:
                        error = 1
                        break
                    error = self.parse_arg(right, size)
                    peek = self.pos
                    if error:
                        break
                if wrap:
                    break
                if self.at(peek) == 0:
                    return error
            if self.at(peek) == 0:
                return error
        return error

    def parse_arg(self, index: int, size: int) -> int:
        """인자 하나(FUN_1004b9d0). 0이면 성공."""
        nodes = self.nodes
        target = node_index = index
        node = nodes[index]
        node.size = size
        status = 0
        target_size = size
        if self.at(self.pos) == 0:
            return 0
        while True:
            self.skip_spaces()
            start = self.pos
            code, tok = lex(self.buf, self.pos)
            if code in (CARET, UNDERSCORE, LSUP, LSUB, FROM, TO, OVER, ATOP, CHOOSE):
                status = 5
            if code in (RBRACE, 0, HASH, AMP) or status:
                break
            node.kind = code
            result = self._parse_arg_body(node, code, tok, start, size)
            if result is not None:
                if result:
                    return result
            # 뒤따르는 첨자(FUN_1004b9d0 끝부분)
            owner = nodes[target]
            self.skip_spaces()
            code, tok = lex(self.buf, self.pos)
            if code not in _SCRIPT_TOKENS:
                break
            self.pos += len(tok)
            sub = self.alloc()
            if sub == 0:
                break
            slot = {UNDERSCORE: "b", CARET: "a", LSUB: "lsub", LSUP: "lsup"}[code]
            if getattr(owner, slot):
                target = node_index
                target_size = node.size
                owner = node
            setattr(owner, slot, sub)
            size = target_size * 64 // 100
            nodes[sub].size = size
            if status or self.at(self.pos) == 0:
                break
            node, node_index = nodes[sub], sub
        return status

    def _parse_arg_body(self, node: Node, code: int, tok: list[int], start: int, size: int) -> int | None:
        """FUN_1004b9d0의 switch. 오류 코드, 또는 계속하면 None/0."""
        nodes = self.nodes
        if code == 1:
            if tok[0] == QUOTE:
                k = start + 1
                while self.at(k) != QUOTE:
                    if self.at(k) == 0:
                        return 8
                    k += 1
                self.pos += tok[2]
                node.text = list(tok[:3])
            else:
                self.pos += len(tok)
                node.text = node_text(tok)
            return None
        if code == 3:
            self.pos += len(tok)
            ch = tok[1]
            if ch in (0x222B, 0x222E, 0xE073, 0xE074, 0xE076, 0xE077):
                node.kind = 0x46
            if ch in (0x48, 0x49):
                node.text = [6, ord("L") if ch == 0x49 else ord("l"), ord("i"), ord("m")]
            else:
                node.text = [6, ch]
            while True:
                self.skip_spaces()
                if self.at(self.pos) == 0:
                    break
                limit, ltok = lex(self.buf, self.pos)
                if limit == FROM:
                    slot = "b"
                elif limit == TO:
                    slot = "a"
                else:
                    break
                if getattr(node, slot):
                    break
                self.pos += len(ltok)
                sub = self.alloc()
                if sub == 0:
                    return 1
                setattr(node, slot, sub)
                result = self.parse_arg(sub, size * 64 // 100)
                if result:
                    return result
            return None
        if code in (4, 0x6F, 0x75, 0x13, 0x4B):
            accent = 0
            if code == 4:
                accent = tok[1]
            elif code == 0x6F:
                accent = 0x4A5
            elif code == 0x75:
                accent = 0x4D2
            if code in (4, 0x6F, 0x75):
                node.kind = 4
            self.pos += len(tok)
            node.text = [accent] if node.kind == 4 else list(tok)
            body = self.alloc()
            node.child = body
            if body == 0:
                return 1
            result = self.parse_arg(body, size)
            if result:
                return result
            if [chr(self.at(self.pos)).lower(), chr(self.at(self.pos + 1)).lower()] == ["o", "f"]:
                self.pos += 2
                node.b = node.child
                body = self.alloc()
                node.child = body
                if body == 0:
                    return 1
                return self.parse_arg(body, size)
            return None
        if code == 0xB:
            self.pos += len(tok)
            node.text = [6, ord("("), ord(")")]
            left = self.alloc()
            node.a = left
            if left == 0:
                return 1
            result = self.parse_arg(left, size)
            if result:
                return result
            right = self.alloc()
            node.b = right
            if right == 0:
                return 1
            return self.parse_arg(right, size)
        if code in (0xF, 0x10, 0x11, 0x12):
            node.text = {0xF: [0], 0x10: [6, 0x28, 0x29], 0x11: [6, 0x5B, 0x5D], 0x12: [6, 0x7C, 0x7C]}[code]
            node.kind = 0xF
            return self._parse_block(node, tok, size)
        if code in (0x15, 0x16, 0x17, 0x1D, 0x2E, 0x30, 0x31):
            return self._parse_block(node, tok, size)
        if code == 0x1C:
            node.text = [6, LBRACE]
            return self._parse_block(node, tok, size)
        if code in (0x18, 0x19):
            self.pos += len(tok)
            self.skip_spaces()
            nxt, _ = lex(self.buf, self.pos)
            ch = self.at(self.pos)
            if ch == 0xFF or nxt == 0 or ch == RBRACE:
                return 1
            self.pos += 1
            node.text = [ch]
            upper = self.alloc()
            node.a = upper
            if upper == 0:
                return 1
            result = self.parse_arg(upper, size)
            if node.kind != 0x18:
                return result or None
            if result:
                return result
            nxt, _ = lex(self.buf, self.pos)
            if nxt == 0 or self.at(self.pos) == RBRACE:
                return 1
            lower = self.alloc()
            node.b = lower
            if lower == 0:
                return 1
            return self.parse_arg(lower, size)
        if code == 0x1B:
            self.pos += len(tok)
            self.skip_spaces()
            if not 0x30 <= self.at(self.pos) <= 0x39:
                return 1
            value = 0
            while 0x30 <= (d := self.at(self.pos)) <= 0x39:
                value = (value * 10 + d - 0x30) & 0xFFFF
                self.pos += 1
            node.text = [value if 0 < value <= 100 else 100]
            return None
        if code in (0x1E, 0x1F):
            self.pos += len(tok)
            node.text = list(tok)
            body = self.alloc()
            node.child = body
            if body == 0:
                return 1
            result = self.parse_arg(body, size)
            if result:
                return result
            label = self.alloc()
            node.a = label
            if label == 0:
                return 1
            return self.parse_arg(label, size)
        if code == 0x20:
            self.pos += len(tok)
            node.text = list(tok)
            body = self.alloc()
            node.child = body
            if body == 0:
                return 1
            result = self.parse_arg(body, size)
            inner = nodes[body]
            if inner.b:
                node.b = inner.b
            if inner.a:
                node.a = inner.a
            inner.a = inner.b = 0
            return result or None
        if code in (0x21, 0x28, 0x29, 0x2B):
            self.pos += len(tok)
            self.skip_spaces()
            nxt, _ = lex(self.buf, self.pos)
            ch = self.at(self.pos)
            if ch == 0xFF or nxt == 0 or (node.kind != 0x29 and ch == RBRACE):
                return 1
            if ch in (0x2E, TILDE):
                ch = SPACE
            self.pos += 1
            node.text = [ch]
            return None
        if code == 0x2F:
            self.pos += len(tok)
            node.text = list(tok)
            for slot in ("b", "a"):
                sub = self.alloc()
                setattr(node, slot, sub)
                if sub == 0:
                    return 1
                self.parse_arg(sub, size)
            rows = self.alloc()
            node.child = rows
            if rows == 0:
                return 1
            nodes[rows].kind = 0x30
            return self.parse_rows(rows, size) or None
        if code == 0x32:
            self.pos += len(tok)
            node.text = list(tok)
            spec = self.alloc()
            node.b = spec
            if spec == 0:
                return 1
            self.parse_arg(spec, size)
            body = self.alloc()
            node.child = body
            if body == 0:
                return 1
            return self.parse_arg(body, size) or None
        if code == LBRACE:
            self.pos += 1
            body = self.alloc()
            node.child = body
            if body == 0:
                return 1
            result = self.parse_list(body, size)
            if self.at(self.pos) != RBRACE:
                return 3
            self.pos += 1
            return result or None
        return None

    def _parse_block(self, node: Node, tok: list[int], size: int) -> int | None:
        self.pos += len(tok)
        index = self.nodes.index(node)  # 노드 수가 1000 이하라 선형 탐색으로 둔다
        if node.kind in (0xF, 0x2E, 0x31):
            return self.parse_matrix(index, size) or None
        return self.parse_rows(index, size) or None

    def _count_columns(self, start: int, end: int) -> int:
        columns, depth = 1, 0
        for k in range(start, end):
            c = self.at(k)
            if depth == 0 and c == AMP:
                columns += 1
            if c == LBRACE:
                depth += 1
            elif c == RBRACE:
                depth -= 1
        return columns

    def parse_rows(self, index: int, size: int) -> int:
        """{ 행 # 행 ... } 형식(FUN_1004b2d0). 행 노드는 child로 이어지고 내용은 행 노드의 next에 있다."""
        nodes = self.nodes
        owner = nodes[index]
        self.skip_spaces()
        if self.at(self.pos) != LBRACE:
            return 2
        self.pos += 1
        start = self.pos
        rows = columns = 0
        row = self.alloc()
        owner.child = row
        while row:
            rows += 1
            nodes[row].size = size
            body = self.alloc()
            nodes[row].next = body
            if body == 0:
                break
            result = self.parse_list(body, size)
            if result:
                owner.rows, owner.cols = rows, columns
                return result
            columns = max(columns, self._count_columns(start, self.pos))
            self.skip_spaces()
            code, tok = lex(self.buf, self.pos)
            if code != HASH:
                owner.rows, owner.cols = rows, columns
                if self.at(self.pos) == RBRACE:
                    self.pos += 1
                    return 0
                return 3
            self.pos += len(tok)
            start = self.pos
            nxt = self.alloc()
            nodes[row].child = nxt
            row = nxt
        owner.rows, owner.cols = rows, columns
        return 1

    def parse_matrix(self, index: int, size: int) -> int:
        """MATRIX류 인자(FUN_1004b5b0). PILE 열 형식(row_form 0)과 & # 행 형식(row_form 1)."""
        nodes = self.nodes
        owner = nodes[index]
        self.skip_spaces()
        if self.at(self.pos) != LBRACE:
            return 2
        self.pos += 1
        self.skip_spaces()
        start = self.pos
        count = columns = 0
        code, tok = lex(self.buf, self.pos)
        if code in (0x15, 0x16, 0x17):
            owner.row_form = 0
            self.pos += len(tok)
            self.skip_spaces()
            if self.at(self.pos) != LBRACE:
                return 2
            pile = self.alloc()
            owner.child = pile
            while pile:
                nodes[pile].kind = code
                nodes[pile].size = size
                count += 1
                if self.parse_rows(pile, size):
                    break
                columns = max(columns, self._count_columns(start, self.pos))
                start = self.pos
                self.skip_spaces()
                if self.at(self.pos) != LBRACE:
                    code, tok = lex(self.buf, self.pos)
                    if code not in (0x15, 0x16, 0x17):
                        break
                    self.pos += len(tok)
                    self.skip_spaces()
                    if self.at(self.pos) != LBRACE:
                        return 2
                nxt = self.alloc()
                nodes[pile].next = nxt
                pile = nxt
        else:
            if code in (0, RBRACE):
                return 2
            owner.row_form = 1
            holder = owner
            while True:
                row = self.alloc()
                holder.child = row
                if row == 0:
                    break
                count += 1
                nodes[row].size = size
                body = self.alloc()
                nodes[row].next = body
                if body == 0 or self.parse_list(body, size):
                    break
                columns = max(columns, self._count_columns(start, self.pos))
                self.skip_spaces()
                code, tok = lex(self.buf, self.pos)
                if code != HASH:
                    break
                self.pos += len(tok)
                start = self.pos
                holder = nodes[row]
        owner.rows, owner.cols = count, columns
        if self.at(self.pos) == RBRACE:
            self.pos += 1
        return 0


#: FUN_1004c7e0이 FUN_1004b9d0으로 넘기는 단어 종류.
_ARG_CODES = frozenset(
    (
        1,
        3,
        4,
        0xB,
        0xF,
        0x10,
        0x11,
        0x12,
        0x13,
        0x15,
        0x16,
        0x17,
        0x18,
        0x19,
        0x1B,
        0x1C,
        0x1D,
        0x1E,
        0x1F,
        0x20,
        0x21,
        0x28,
        0x29,
        0x2B,
        0x2E,
        0x2F,
        0x31,
        0x32,
        0x4B,
        0x6F,
        0x75,
    )
)
#: 목록 안 첨자 단어 → (채울 칸, 인자 해석 뒤 옮겨 올 칸).
_SCRIPT_SLOTS = {LSUB: ("lsub", "lsup"), LSUP: ("lsup", "lsub"), CARET: ("a", "b"), UNDERSCORE: ("b", "a")}


def parse_script(script: str) -> Parser:
    """FUN_1004d0f0: 'EQALIGN { ' + 스크립트 + ' }'를 전처리·해석한다. 실패해도 1번 노드부터 만든 데까지 쓴다."""
    wrapped = "EQALIGN { " + script + " }"
    data = wrapped.encode("utf-16-le")
    src = [int.from_bytes(data[i : i + 2], "little") for i in range(0, len(data), 2)]
    parser = Parser(preprocess(src))
    # 성공하면 시작점은 풀 머리의 next, 실패하면 FUN_1004aba0의 기본값 1번 노드다
    if parser.parse() == 0:
        parser.root = parser.nodes[0].next
    return parser
