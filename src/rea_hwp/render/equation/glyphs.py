"""Windows GDI로 측정한 수식 글꼴 폭 표(data/eq_glyph_metrics.json.gz)로 엔진의 글자 측정을 재현한다.
표는 tools/build_eq_glyph_metrics.py로 만든다."""

from __future__ import annotations

import bisect
import gzip
import json
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data" / "eq_glyph_metrics.json.gz"

#: 글리프가 없을 때 쓰는 글꼴. EqAppModule FUN_10012960이 리소스 IDS_EQ_LOG_FONTNAME(= 한컴바탕)으로 바꿔 측정한다.
FALLBACK_FONT = "한컴바탕"
#: 수식 글꼴. FUN_10011fd0은 현재 글꼴 다음에 이 글꼴에서 글리프를 찾는다.
EQUATION_FONT = "HYhwpEQ"
#: PUA에 수식 기호를 둔 글꼴(폭 표에 있는 수식 글꼴). 이 글꼴의 PUA는 옛한글이 아니다.
EQUATION_FONTS = frozenset((EQUATION_FONT, "HancomEQN"))
#: 두 수식 글꼴의 usWinAscent / unitsPerEm(= 820/1024). 위 끝 맞춤(TA_TOP)으로 찍은 글자의 기준선 = 위 끝 + ascent.
EQUATION_ASCENT = 820 / 1024
#: 'lim'·'Lim' 글자열의 전진 폭(hmtx, em 1024). 글자열은 장치 폭으로 이어 찍는다(FUN_100126b0, ExtTextOutW lpDx 없음).
STRING_ADVANCES = {
    "HYhwpEQ": {"L": 640, "l": 298, "i": 298, "m": 853},
    "HancomEQN": {"L": 681, "l": 303, "i": 289, "m": 892},
}
#: 정답 PDF를 찍은 인쇄 장치의 1점(600dpi = 12 HWPUNIT). BOX 테두리 0.12pt와 lim 글자 간격 3.12pt로 확인했다.
DEVICE_UNIT = 12
#: 96DPI 화면 DC에서 1픽셀 = 7200/96 HWPUNIT(FUN_1026e540의 창·뷰포트 범위).
UNITS_PER_PIXEL = 75


def to_pixels(units: int) -> int:
    """논리 단위 → 장치 픽셀(반올림)."""
    return (units * 2 + UNITS_PER_PIXEL) // (2 * UNITS_PER_PIXEL)


def ppem(size: int) -> tuple[int, int]:
    # 글꼴 높이 = 크기, lfWidth = 크기/2(FUN_10011f10). 세 글꼴 모두 xAvgCharWidth = em/2라 가로 ppem = 2 x 폭 픽셀
    height = to_pixels(size)
    width = to_pixels(size // 2)
    # 검증 안 됨: 폭이 0픽셀이면 GDI가 lfWidth 0(종횡비 기본값)처럼 다룬다고 보았다(크기 112 미만에서만 생김)
    return (2 * width if width else height), height


class _FontRuns:
    def __init__(self, runs: list[list[int]], vectors: list[list[int]]) -> None:
        self.starts = [run[0] for run in runs]
        self.runs = runs
        self.vectors = vectors

    def vector(self, code: int) -> list[int] | None:
        i = bisect.bisect_right(self.starts, code) - 1
        if i < 0 or self.runs[i][1] < code:
            return None
        return self.vectors[self.runs[i][2]]


class GlyphTable:
    def __init__(self, raw: dict) -> None:
        self.pair_index = {tuple(pair): i for i, pair in enumerate(raw["pairs"])}
        self.fonts = {name: _FontRuns(font["runs"], font["vectors"]) for name, font in raw["fonts"].items()}
        #: 쌍마다 함초롬바탕 단독 상위 대리의 [폭, 높이] 픽셀(tools/gdi_surrogate_box.ps1).
        self.hanja_surrogate: list[list[int]] = raw["hanja_surrogate"]

    def has_glyph(self, font: str, code: int) -> bool:
        runs = self.fonts.get(font)
        return runs is not None and runs.vector(code) is not None

    def extent(self, font: str, code: int, size: int) -> tuple[int, int]:
        """GetTextExtentPointW(글자 하나)의 (cx, cy). 글리프가 없으면 대체 글꼴로 측정한다."""
        if not self.has_glyph(font, code):
            font = FALLBACK_FONT
        vector = self.fonts[font].vector(code)
        pair = ppem(size)
        if vector is None and 0xD840 <= code <= 0xD87F and pair in self.pair_index:
            # HncBaseDraw는 단독 상위 대리 D840~D87F를 한자로 분류해 한자 기본 글꼴(함초롬바탕) .notdef로 측정하고
            # 높이는 tmHeight + tmInternalLeading이다(HncDRGetCharLang, FUN_10055820, FUN_1005c030)
            width, height = self.hanja_surrogate[self.pair_index[pair]]
            return width * UNITS_PER_PIXEL, height * UNITS_PER_PIXEL
        if vector is None and 0xD800 <= code <= 0xDFFF:
            # 대리 쌍은 wchar 하나씩 측정한다. 영문·기호로 분류된 상위 대리는 한컴바탕 .notdef(1024/1024 em) 한 칸,
            # 하위 대리는 폭 0이다(FUN_1005c6f0 0x1005cd19, 시험 30건)
            return (pair[0] * UNITS_PER_PIXEL if code < 0xDC00 else 0), pair[1] * UNITS_PER_PIXEL
        if vector is None:
            raise KeyError(f"수식 글꼴 표에 U+{code:04X} 글자가 없어 크기를 계산할 수 없습니다. 저장된 크기를 쓰세요")
        index = self.pair_index.get(pair)
        if index is None:
            raise KeyError(f"수식 글꼴 표에 ppem {pair}가 없습니다(글자 크기 {size})")
        return vector[index] * UNITS_PER_PIXEL, pair[1] * UNITS_PER_PIXEL


@lru_cache(maxsize=1)
def glyph_table() -> GlyphTable:
    with gzip.open(_DATA, "rt", encoding="utf-8") as fh:
        return GlyphTable(json.load(fh))
