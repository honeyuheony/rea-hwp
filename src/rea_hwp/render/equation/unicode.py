"""수식 글꼴(HYhwpEQ·HancomEQN) PUA를 유니코드로 바꾸는 표. 수식 글꼴이 없는 PC에서 OFL 수학 글꼴로 그릴 때 쓴다.
엔진 표(ASCII_PUA·PUA_RANGES)에 있는 글자는 그 표를 뒤집고, 나머지는 HYHWPEQ.TTF 글리프 모양을 보고 정했다."""

from __future__ import annotations

from .tables import ASCII_PUA, PUA_RANGES

#: 수식 글꼴이 없을 때 쓰는 글꼴 이름 사슬. 앞의 셋은 OFL, Cambria Math는 Windows 기본 글꼴이다.
FALLBACK_FONT_CHAIN = "'STIX Two Math', 'Latin Modern Math', 'Noto Sans Math', 'Cambria Math', 'STIXGeneral', serif"
#: 옛 엔진 수식의 HFT 글꼴 이름. 브라우저는 HFT를 못 쓰므로 한컴이 깔린 PC에서도 FALLBACK_FONT_CHAIN으로 그린다.
LEGACY_EQUATION_FAMILY = "수식"

#: 모양으로 정한 대응(엔진 표에 없는 PUA). 중괄호 가로 조각(E106~E10D)과 빈 칸(E111)은 대응이 없어 뺐다.
_BY_SHAPE = {
    0xE058: "←",
    0xE059: "⎯",
    0xE05A: "→",
    0xE05B: "∫",
    0xE05C: "√",
    0xE05D: "´",
    0xE05E: "`",
    0xE05F: "˙",
    0xE060: "¨",
    0xE061: "→",
    0xE062: "↔",
    0xE063: "ˆ",
    0xE064: "ˇ",
    0xE065: "⌢",
    0xE066: "˜",
    0xE067: "∑",
    0xE068: "∏",
    0xE069: "∐",
    0xE06A: "⋂",
    0xE06B: "⋃",
    0xE06C: "←",
    0xE06D: "⎯",
    0xE06E: "→",
    0xE06F: "⇐",
    0xE070: "═",
    0xE071: "⇒",
    0xE072: "∫",
    0xE073: "∬",
    0xE074: "∭",
    0xE075: "∮",
    0xE076: "∯",
    0xE077: "∰",
    0xE078: "⎧",
    0xE079: "⎨",
    0xE07A: "⎩",
    0xE07B: "⎪",
    0xE07C: "⎛",
    0xE07D: "⎜",
    0xE07E: "⎝",
    0xE07F: "⎞",
    0xE080: "⎟",
    0xE081: "⎠",
    0xE082: "⎫",
    0xE083: "⎬",
    0xE084: "⎭",
    0xE0B5: "ℵ",
    0xE0B6: "ℏ",
    0xE0B7: "𝚤",
    0xE0B8: "𝚥",
    0xE0B9: "℧",
    0xE0BA: "ℓ",
    0xE0BB: "ℓ",
    0xE0BC: "℘",
    0xE0BD: "ℑ",
    0xE0BE: "ϑ",
    0xE0BF: "ϖ",
    0xE0C0: "ς",
    0xE0C1: "ϒ",
    0xE0C2: "φ",
    0xE0C3: "’",
    0xE0C4: "∂",
    0xE0C5: "¬",
    0xE0C6: "↔",
    0xE0C7: "⇔",
    0xE0C9: "†",
    0xE0CA: "‡",
    0xE100: "⎡",
    0xE101: "⎢",
    0xE102: "⎤",
    0xE103: "⎣",
    0xE104: "⎦",
    0xE105: "⎥",
    0xE10E: "𝜀",
    0xE10F: "⟌",
    0xE110: "⎯",
}

#: 겹선 대문자 중 Letterlike Symbols 블록에 따로 있는 글자.
_DOUBLE_STRUCK = {"C": "ℂ", "H": "ℍ", "N": "ℕ", "P": "ℙ", "Q": "ℚ", "R": "ℝ", "Z": "ℤ"}


def _math_italic(ch: str) -> str:
    """수학 기울임꼴(U+1D434~). h는 비어 있는 자리라 ℎ(U+210E)다."""
    if ch == "h":
        return "ℎ"
    if "a" <= ch <= "z":
        return chr(0x1D44E + ord(ch) - ord("a"))
    if "α" <= ch <= "ω":
        return chr(0x1D6FC + ord(ch) - ord("α"))
    return ch


def _build() -> dict[int, str]:
    table: dict[int, str] = {}
    for ch, code in ASCII_PUA:
        # E0E5~E0FE는 기울인 소문자, 나머지(대문자·숫자·기호)는 글자 그대로다. 대문자 기울임은 글꼴 기울임으로 찍는다.
        table[code] = _math_italic(ch) if 0xE0E5 <= code <= 0xE0FE else ("−" if ch == "-" else ch)
    for low, high, base in PUA_RANGES:
        for code in range(low, high + 1):
            pua = base - low + code
            if pua not in table and 0xE000 <= pua <= 0xF8FF:
                table[pua] = _math_italic(chr(code))
    for offset in range(26):
        # E01A~E033: 곧은 소문자 a~z
        table.setdefault(0xE01A + offset, chr(ord("a") + offset))
        letter = chr(ord("A") + offset)
        table[0xE0CB + offset] = _DOUBLE_STRUCK.get(letter, chr(0x1D538 + offset))
    table.update(_BY_SHAPE)
    return table


#: PUA 코드 → 유니코드 글자.
PUA_TO_UNICODE: dict[int, str] = _build()


def to_unicode(text: str) -> str:
    """수식 글꼴 PUA를 유니코드로 바꾼다. 대응이 없는 글자는 그대로 둔다."""
    return "".join(PUA_TO_UNICODE.get(ord(ch), ch) for ch in text)
