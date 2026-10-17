"""글꼴 폭 표 조회와 글자 전진폭 계산(HWPUNIT 단위). 규칙 출처: rhwp layout/text_measurement.rs."""

from __future__ import annotations

import dataclasses
import gzip
import json
import math
from dataclasses import dataclass
from functools import cache
from importlib import resources

#: 언어 슬롯(CHAR_SHAPE의 7개 슬롯 순서).
HANGUL, LATIN, HANJA, JAPANESE, OTHER, SYMBOL, USER = range(7)


@dataclass(frozen=True)
class FontMetric:
    name: str
    bold: bool
    italic: bool
    em: int
    ranges: tuple[tuple[int, int, tuple[int, ...]], ...]
    hangul: dict | None
    #: 표에 없는 한글 음절의 폭. PDF 폭 표는 문서에 쓰인 음절만 담으므로 가장 흔한 음절 폭으로 채운다.
    hangul_default: int | None = None
    #: 폭 표에 없는 글자의 보충 폭(TrueType 파일·PDF 내장 폭, 이 표의 em 단위).
    supplement: dict[int, int] | None = None

    def width(self, code: int) -> int | None:
        width = self.table_width(code)
        if width is None and self.supplement:
            return self.supplement.get(code)
        return width

    def table_width(self, code: int) -> int | None:
        """em 단위 폭. 표에 없거나 0이면 None(rhwp는 같은 범위에서 0을 만나면 다른 범위를 찾지 않는다)."""
        if 0xAC00 <= code <= 0xD7A3 and self.hangul:
            index = code - 0xAC00
            cho, jung, jong = index // (21 * 28), (index % (21 * 28)) // 28, index % 28
            _, jung_groups, jong_groups = self.hangul["groups"]
            group = (
                self.hangul["cho"][cho] * jung_groups * jong_groups
                + self.hangul["jung"][jung] * jong_groups
                + self.hangul["jong"][jong]
            )
            return self.hangul["widths"][group] or None
        for start, end, widths in self.ranges:
            if start <= code <= end:
                value = widths[code - start] if code - start < len(widths) else 0
                return value or None
        if 0xAC00 <= code <= 0xD7A3:
            return self.hangul_default
        return None


class FontDatabase:
    def __init__(self, exported: dict) -> None:
        self.fonts: dict[str, list[FontMetric]] = {}
        for font in exported["fonts"]:
            metric = FontMetric(
                font["name"],
                font["bold"],
                font["italic"],
                font["em"],
                tuple((s, e, tuple(w)) for s, e, w in font["ranges"]),
                font["hangul"],
            )
            self.fonts.setdefault(metric.name, []).append(metric)
        self.metric_alias = {rule["source"]: rule["target"] for rule in exported["aliases"]["layout-metric"]}
        self.substitutes: dict[str, str] = {}
        #: HFT 글꼴로 찍힌 글자의 PDF 폭(1000분의 1 em). 글꼴 이름 → 코드 → 폭.
        self.hft: dict[str, dict[int, int]] = {}

    def add_pdf_supplement(self, supplement: dict) -> None:
        """한글 2022 PDF에서 모은 글꼴 대체와 내장 글꼴 폭(tools/build_pdf_font_metrics.py 출력)을 더한다."""
        self.substitutes.update({face: info["target"] for face, info in supplement["substitutes"].items()})
        for font in supplement["fonts"]:
            codes = sorted(int(code) for code in font["widths"])
            ranges: list[tuple[int, int, tuple[int, ...]]] = []
            for code in codes:
                if ranges and ranges[-1][1] == code - 1:
                    start, _, widths = ranges[-1]
                    ranges[-1] = (start, code, (*widths, font["widths"][str(code)]))
                else:
                    ranges.append((code, code, (font["widths"][str(code)],)))
            metric = FontMetric(
                font["name"], font["bold"], font["italic"], font["em"], tuple(ranges), None, font["hangul_default"]
            )
            self.fonts.setdefault(metric.name, []).append(metric)

    def add_ttf_supplement(self, supplement: dict) -> None:
        """같은 이름 표의 빈 글자를 채운다(tools/build_ttf_font_metrics.py·build_pdf_fill_metrics.py 출력)."""
        for font in supplement["fonts"]:
            metrics = self.fonts.get(font["name"])
            if not metrics:
                continue
            scale = metrics[0].em / font["em"]
            widths = {int(code): round(width * scale) for code, width in font["widths"].items()}
            # 먼저 넣은 보충 표가 이긴다(TrueType 파일 폭이 PDF 폭보다 앞선다).
            self.fonts[font["name"]] = [
                dataclasses.replace(m, supplement={**widths, **(m.supplement or {})}) for m in metrics
            ]

    def find(self, face: str, bold: bool = False, italic: bool = False) -> FontMetric | None:
        """대체 글꼴 → 이름 일치 → (굵게, 기울임) → 굵게만 일치 → 그 이름의 첫 항목 순(rhwp font_metrics_data.rs)."""
        face = self.substitutes.get(face, face)
        candidates = self.fonts.get(face) or self.fonts.get(self.metric_alias.get(face, ""))
        if not candidates:
            return None
        for metric in candidates:
            if metric.bold == bold and metric.italic == italic:
                return metric
        for metric in candidates:
            if metric.bold == bold:
                return metric
        return candidates[0]


def _load(name: str) -> dict:
    return json.loads(gzip.decompress(resources.files("rea_hwp.render").joinpath(f"data/{name}").read_bytes()))


def rhwp_database() -> FontDatabase:
    """rhwp에서 옮긴 폭 표만 담은 DB. PDF 보충 표를 만들 때 기준으로 쓴다."""
    return FontDatabase(_load("font_metrics.json.gz"))


@cache
def default_database() -> FontDatabase:
    database = rhwp_database()
    database.add_pdf_supplement(_load("font_metrics_pdf.json.gz"))
    database.add_ttf_supplement(_load("font_metrics_ttf.json.gz"))
    fill = _load("font_metrics_pdf_fill.json.gz")
    database.add_ttf_supplement(fill)
    database.hft = {font["name"]: {int(code): width for code, width in font["widths"].items()} for font in fill["hft"]}
    return database


def is_cjk_wide(code: int) -> bool:
    return (
        0x1100 <= code <= 0x11FF
        or 0x3130 <= code <= 0x318F
        or 0xAC00 <= code <= 0xD7AF
        or 0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0xF900 <= code <= 0xFAFF
        or 0x3040 <= code <= 0x30FF
        or 0xFF00 <= code <= 0xFF60
        or 0xFFE0 <= code <= 0xFFE6
        or code in (0x20A9, 0x20AC, 0x00A3, 0x00A5)
        or 0x2190 <= code <= 0x21FF
        or 0x2460 <= code <= 0x24FF
        or 0x25A0 <= code <= 0x27BF
        or 0x3200 <= code <= 0x33FF
        or 0x2160 <= code <= 0x217F
        or 0x3000 <= code <= 0x303F
    )


#: 숫자·로마자만 가진 옛 한컴 영문 글꼴. 그 밖의 ASCII는 이 글꼴 폭으로 영문 슬롯에 놓인다(rhwp style_resolver.rs
#: ascii_punct_latin_slot, hwp3-sample16 7쪽 PDF: 「(」·「-」 폭 3.96pt = 13pt −8%에서 Palatino 0.333em).
LEGACY_LATIN = {"HCI Poppy": "Palatino Linotype"}

#: 빈칸 제어 문자(명세 표7: 30 묶음 빈칸, 31 고정폭 빈칸) → 폭이 같은 유니코드 공백(rhwp body_text.rs).
#: 고정폭 빈칸 폭은 글자 크기의 1/4(편람 4쪽 PDF: 12pt 줄에서 탭 끝 → 쪽 번호 3pt).
CONTROL_SPACES = {"\x1e": "\u00a0", "\x1f": "\u2007"}


_NARROW_PUNCT = frozenset(",.:;'\"`·‘’‧〮〯")


def fallback_em(char: str) -> float:
    """폭 표에 없는 글자의 폭(em 비율). rhwp text_measurement.rs 1488-1553."""
    code = ord(char)
    if 0xFF61 <= code <= 0xFFDC or 0xFFE8 <= code <= 0xFFEE:
        return 0.5
    # 「․」(U+2024)는 KS X 1001 전각 기호다(2010-01-06·hwp3-sample16 PDF: 휴먼명조·한양신명조 줄에서 1em).
    # 한컴 전용 기호(평면 15 PUA)는 전각이다(hwp3-sample16 4쪽 PDF: U+F03C5 글머리 기호가 12.96pt 글자에서 12.96pt).
    if is_cjk_wide(code) or 0xF0000 <= code <= 0xFFFFD or code == 0x2024:
        return 1.0
    if char in _NARROW_PUNCT:
        return 0.3
    return 0.5


def glyph_em(char: str, metric: FontMetric | None) -> float:
    """글자 하나의 폭(em 비율). 공백은 em/2(rhwp hancom_pdf_space_width)."""
    if char in (" ", " "):
        return 0.5
    if char == " ":
        return 0.25
    if metric is None:
        return fallback_em(char)
    code = ord(char)
    width = metric.width(code)
    if width is None:
        return fallback_em(char)
    em = metric.em
    if code in (0x2018, 0x2019, 0x2027) and width >= em:
        return 0.3
    if 0x2018 <= code <= 0x2027 and width >= em:
        return 0.5
    return width / em


def slot_of(char: str, previous_slot: int) -> int:
    """글자의 언어 슬롯. 공백·ASCII 구두점 같은 중립 글자는 앞 글자의 슬롯을 따른다(rhwp composer.rs)."""
    code = ord(char)
    if code == 0x318D:
        return USER
    if (
        0x1100 <= code <= 0x11FF
        or 0x3130 <= code <= 0x318F
        or 0xAC00 <= code <= 0xD7AF
        or 0xA960 <= code <= 0xA97F
        or 0xD7B0 <= code <= 0xD7FF
    ):
        return HANGUL
    if char.isascii() and char.isalnum() or 0x00C0 <= code <= 0x024F or 0x1E00 <= code <= 0x1EFF:
        return LATIN
    if 0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF or 0xF900 <= code <= 0xFAFF or 0x20000 <= code <= 0x2A6DF:
        return HANJA
    if 0x3040 <= code <= 0x30FF or 0x31F0 <= code <= 0x31FF:
        return JAPANESE
    if 0x2190 <= code <= 0x23FF or 0x2460 <= code <= 0x24FF or 0x2500 <= code <= 0x27BF or 0x3000 <= code <= 0x303F:
        return SYMBOL
    if code <= 0x20 or (code < 0x80 and not char.isalnum()) or 0xA0 <= code <= 0xBF:
        return previous_slot
    return HANGUL


def mul_div(a: int, b: int, c: int) -> int:
    """a·b/c를 0에서 먼 쪽으로 반올림한다(Win32 MulDiv)."""
    product = a * b
    return math.trunc((product - c / 2) / c if product < 0 else (product + c / 2) / c)


def hancom_width(char: str, em1000: float, height: int, ratio: int, spacing: int) -> int:
    """한컴 조판의 글자 전진폭(HU). 글자 크기를 4HU 단위로 내리고 폭·자간도 4HU 단위로 정한다.

    출처: kordoc(MIT) hwpx/text-metrics.ts hancomCharWidth. 한글 2022 PDF 간격으로 재확인(함초롬바탕 10pt 95% -5%: 872).
    """
    if char == " ":
        quarter = height // 16
        width = 4 * (quarter if ratio == 100 else mul_div(quarter, ratio, 100))
    elif char in (" ", " "):
        half = height // 8
        width = 4 * (half if ratio == 100 else mul_div(half, ratio, 100))
    else:
        quarter = height // 4
        em = round(em1000)
        width = 4 * mul_div(em, quarter, 1000) if ratio == 100 else 4 * math.trunc(em * quarter * ratio / 100000)
    if spacing:
        width += 4 * mul_div(math.trunc(width / 4), spacing, 100)
    return width


#: justify_offsets 항목 종류. FIXED(개체·생성 번호)는 늘이지도 줄이지도 않고, TAB이 있는 줄은 넘쳐도 줄이지 않는다.
SPACE, GLYPH, FIXED, TAB = "space", "glyph", "fixed", "tab"
#: 줄이 넘칠 때 공백 하나가 줄어드는 최대 비율. 2025 행정업무운영 편람 실측(공백 최소값 30·50·70 문단, 0·15는 미확인).
OVERFLOW_SPACE_SHRINK = 0.2


def justify_offsets(
    widths: list[float], kinds: list[str], slack: float, stretch: bool, distribute: bool = False
) -> list[float]:
    """줄 안 항목(줄 끝 공백 제외)의 전진폭에 더할 값(HU). 근거: 편람 11~13쪽 한글 2022 PDF 글자 원점.

    남는 폭은 distribute(배분 정렬)면 항목 사이 N−1곳에 고르게, stretch일 때는 공백(없으면 글자)에 나눈다.
    넘친 폭은 정렬과 상관없이 공백을 상한까지 줄이고, 나머지는 마지막 글자를 뺀 글자에서 같은 양씩 뺀다.
    """
    extras = [0.0] * len(widths)
    # 줄 앞 공백은 늘이거나 줄이지 않는다(kr_156489756 2쪽 PDF: 「 ○ 위중증」 줄은 첫 공백만 제 폭).
    leading = next((i for i, kind in enumerate(kinds) if kind != SPACE), len(kinds))
    spaces = [i for i, kind in enumerate(kinds) if kind == SPACE and i >= leading]
    if slack >= 0:
        if distribute and len(widths) > 1:
            # 배분 정렬은 오른쪽 끝을 줄 폭에 맞춘다(task2287 6쪽 PDF 「연구책임」: 글자 사이 8.75pt씩, rhwp #4657).
            for i in range(len(widths) - 1):
                extras[i] = slack / (len(widths) - 1)
        elif stretch and spaces:
            for i in spaces:
                extras[i] = slack / len(spaces)
        elif stretch and len(widths) > 1:
            # 공백 없는 양쪽 정렬 줄은 글자마다 나눈다(re-eng-nospace-* PDF, rhwp paragraph_layout.rs 공백 없음 분기).
            for i in range(len(widths)):
                extras[i] = slack / len(widths)
        return extras
    overflow = -slack
    # 탭이 있는 줄은 저장된 탭 폭을 그대로 두고 넘친다(편람 4쪽·국립국어원 업무계획 2쪽 목차: 탭 뒤 쪽 번호가 줄 폭 밖).
    if TAB in kinds:
        return extras
    caps = [widths[i] * OVERFLOW_SPACE_SHRINK for i in spaces]
    shrinkable = sum(caps)
    if shrinkable >= overflow:
        for i, cap in zip(spaces, caps, strict=True):
            extras[i] = -overflow * cap / shrinkable
        return extras
    for i, cap in zip(spaces, caps, strict=True):
        extras[i] = -cap
    glyphs = [i for i, kind in enumerate(kinds) if kind == GLYPH]
    if glyphs and glyphs[-1] == len(kinds) - 1:
        glyphs.pop()
    for i in glyphs:
        extras[i] -= (overflow - shrinkable) / len(glyphs)
    return extras


def advance_hu(
    char: str,
    metric: FontMetric | None,
    base_size: int,
    ratio: int,
    spacing: int,
    relative_size: int,
    half_space: bool = True,
) -> float:
    """글자 전진폭(HWPUNIT). base_size는 CHAR_SHAPE 기준 크기(HU), ratio·spacing·relative_size는 %."""
    size = base_size * relative_size / 100
    em = 0.5 if (half_space and char == " ") else glyph_em(char, metric)
    base = math.floor(em * size)
    scaled = base * ratio / 100
    letter_spacing = base_size * spacing / 100
    width = scaled + (letter_spacing * scaled / size if size else 0.0)
    if letter_spacing < 0:
        width = max(width, scaled * 0.5)
    return width
