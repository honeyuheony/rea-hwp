"""번호 모양(명세 표41 문단 번호 형식) 코드를 글자로 바꾼다."""

from __future__ import annotations

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
HANGUL_SYLLABLE = "가나다라마바사아자차카타파하"
HANGUL_JAMO = "ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎ"
HANGUL_DIGITS = "영일이삼사오육칠팔구"
HANJA_DIGITS = "零一二三四五六七八九"


def _roman(number: int) -> str:
    pairs = (
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    )
    text = ""
    for value, letters in pairs:
        while number >= value:
            text += letters
            number -= value
    return text


def _cycle(alphabet: str, number: int) -> str:
    return alphabet[(number - 1) % len(alphabet)] if number > 0 else str(number)


def format_number(number: int, shape: int) -> str:
    """0 1,2,3 / 1 원문자 / 2 I,II / 3 i,ii / 4 A,B / 5 a,b / 8 가,나 / 10 ㄱ,ㄴ / 12 일,이 / 13 一,二(표41)."""
    if shape == 1:
        return CIRCLED[number - 1] if 1 <= number <= len(CIRCLED) else str(number)
    if shape == 2:
        return _roman(number)
    if shape == 3:
        return _roman(number).lower()
    if shape == 4:
        return _cycle("ABCDEFGHIJKLMNOPQRSTUVWXYZ", number)
    if shape == 5:
        return _cycle("abcdefghijklmnopqrstuvwxyz", number)
    if shape == 8:
        return _cycle(HANGUL_SYLLABLE, number)
    if shape == 10:
        return _cycle(HANGUL_JAMO, number)
    if shape == 12:
        return "".join(HANGUL_DIGITS[int(d)] for d in str(number))
    if shape == 13:
        return "".join(HANJA_DIGITS[int(d)] for d in str(number))
    return str(number)
