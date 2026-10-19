"""글맵시(HWPTAG_SHAPE_COMPONENT_TEXTART)를 한글 2022 엔진처럼 글자 윤곽을 외곽 네 변에 맞춰 휜 SVG 경로로 바꾼다.

엔진: HwpAppModule.dll 글맵시 플러그인(외곽·핸들·글자 배치), HncDrawingEngine.dll Hnc::Shape::TextWarp(FourLineWarp).
"""

from __future__ import annotations

import base64
import bisect
import math
import struct
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

#: 외곽과 변형 결과를 두는 정사각 좌표계의 한 변(HwpAppModule 0x1040a910·0x1040a8c8, FUN_100fcd40·FUN_100fbbd0).
WARP_SIZE = 14173.25
#: 외곽 표·핸들 좌표의 단위 범위(FUN_10106e90·FUN_10104fa0이 0~1000으로 다룬다).
HANDLE_RANGE = 1000
#: 원 모양 글맵시(48~54). HwpAppModule FUN_100fcd40 switch가 HncDrawingEngine HwpCircleAlgorithm(0x2a~0x30)에 넘긴다.
CIRCLE_SHAPES = range(0x30, 0x37)
#: 베지어 변 길이 표의 매개변수 간격(HncDrawingEngine 0x1055df9c, WarpBezierSegment FUN_1020dd80).
BEZIER_LENGTH_STEP = 0.0005
#: 직선을 나눠 휘는 간격 = max(최소, 외곽 대각선/나눗수)(FUN_10203880, 0x1055e1d0·0x1055e46c).
LINE_STEP_MIN, LINE_STEP_DIVISOR = 2.0, 200.0
#: 외곽 변을 직선으로 볼지 가르는 각도 비교 단위(도, FUN_100fcd40이 atan2×57.29577951308232를 정수로 자른다).
DEGREES_PER_RADIAN = 57.29577951308232
#: 줄 간격·글자 간격 백분율의 분모(HwpAppModule 0x1040a8ec).
PERCENT = 100.0
#: 글줄 바꿈. 엔진은 0x0D에서 줄을 나누고 0x0A는 건너뛴다(FUN_100fe610).
LINE_BREAK, LINE_FEED, SPACE = "\r", "\n", " "
#: 그림자 간격(레코드 값)의 단위. 백분율 x 0.01 x 정사각 한 변(FUN_100fbbd0·FUN_100fc390, 0x1040a820).
SHADOW_OFFSET_UNIT = 0.01
#: 글꼴이 없을 때 쓰는 글꼴. FUN_100ff2d0은 HncDRGetSubstFontName이 실패하면 HncDRGetDefaultFontName 글꼴로 그린다.
#: 그 표는 실행 중에 채워져 정적으로 못 읽어 이 PC 정답 PDF로 측정했다(없는 글꼴 11개 모두 한컴바탕에 가장 가깝다).
DEFAULT_FACE = "한컴바탕"

#: 모양 번호별 외곽 32점(int16 64개). HwpAppModule.dll 0x104bb720 표(FUN_10106c50이 0x80바이트씩 복사)를 zlib로 줄였다.
_ENVELOPE_TABLE = (
    "eNrtmHlwVdUdx38LVgvTFgXUYWkdwABKa0mVUiqYB1hQEoiYAIINKMtAoCoioCLEQRCkZQsQ05fIBEweKYq4zDChskkldaMLaF2mtUgNJh1M"
    "QVKX6Ei/v3Pvu+8YwjLDH05nzJvPe5N77/d8z++c39luETXwS5TJjZSgnkyO9aCtEC0NOQFmKlFtyFZN0ELN5BHawEXQT6QSSadY9MQ94N8g"
    "jwP2gyFSq9tD6iVDL9O4pPSZnA7/5N89oA7kccB+MBR12B5SLwm6zPN/ieLSSBnaEz7GetAWPktDvgQZWht9tuK/haE/EaPEUfh9JvKvoNmo"
    "0RE6zrezEZe3OEOHoZQXQz4G3VHSGA30ZWBwFP8GMBx1aAgpwdMxzx+tABaG+rHgCtCIMl50VNBwyeS3OeV/BNHNjtq/Vp8FY1CyeZv+12B7"
    "dDcBMj3/eJP4a3UY2CiB/gPEPsR6PYp/AxiOKw0hJXgq5vU/WhlMBMsp0K/CEz+N7lbQ3fD/Qo7zw464tEaPx1GTriEf4andoX4Z+CXoGflb"
    "JmR6/nGUnvEV/ziIoRzzNv1OUA+6slFBv+VM/p7n/wX87/bi7w2WS6A3hywmL/8S1AdXZuDOJkdcDiH+DvDLCXkE7HStm6phzGv/LZrFs3G3"
    "vyMu56H/X4PfmpA80ENS+p3gEZDjSFAHzuJDnv8MlN7Hi996OMvz7wH/PLDGkaDXJIvP8/xnw3+LF79lY9z5FzUz/u8AT3vt3xul3nua8TcR"
    "NUz3euhM+eePv+bG/5n6v+n803T83wGe9vx7w//eU8w/hFF+cv8X4MoJlFzARlxOoP0Lmmn/483W8MzjLyNq/0Cf2cT/TPmfcgjq3zT/pqB2"
    "ddLAUx1xqZOYTmmm/017H/gcjvM9/xGIvxzeHzviUo7SR3j+88Hn4L5Q/zGYiTocdSRoIPyLvPiL4DgQnkclYKbNoRzo74TmI/zOAp+SkcAM"
    "nMUjvPhHwL8Cfp+GzLI5BJjW+BDke/EXwX8QvD8SIy6D4F/kxZ8PPpRAf679f67tbxGWhTlwtvFb35d54/8G+D8GjjmC9l/rtf/asP2PScBj"
    "4AYv/9aHbdBIRgLXshB5A2YFIxVdY0hWuMom9YMlWCXqw9khOTvVixHHd8zVpF4CbEUcfBr/4L+Uf1J/tv7J+if9k+tvc/5zQt95Uf9bT1RS"
    "Y+SfAc9S8cffvLAec0K9lTMj8s+EZyWlxn+GHtRSSXobMyS4MyfU+fVodLNxJaXGv439Umn02n9OWLJpk77TotpZdlRSELuRAc9S8eefaZqs"
    "xzyqlhyaJFdQC5SxgY352H2dwHr8EF/suAB3VshK6qCd2BigK3iiHuOHNdfpN1Ka/pWWRLWbxen6H54n0yXfUSejJF9r4HePGq9qB63UKjH9"
    "7ZQrC+kY1rwVvI86OW7hlXQQNZkkFzvqpCdGd53trNjYqS1kjU6SaVrt9BlUJZ1QO+wcHfOpBiN2lDzA+Y7PUJeZkh61/iW6BPmQpqafgHZ/"
    "iI7T4zSFnkebG7dg7R/Hy9D2mx2lsoyel2mIkhz9dQrl6VGaj34w/UAS6UqL0UpXqfEOlemNvD/q/+2yT3+uccR+lWOHLpF1KmL6SXSXXE81"
    "nEbjEXEVGX+nwciBdvRfXDF2Snvqp1mIv8qxS8dxmdbwg3qXmH4RNWDPWKA7cNcYi73HbTwG3n0d62SM7pAH3I61u1t9CnS8fiKmD/ac48EX"
    "cCpyvE5v0FDuKFfKfjZ2yx0opS2n8r8duAJkUKDvgau/ie4W0jTJ4ZHaV6rFaK9TZIvb8y0ImQZyQ73t2Xbh99Zo/BXSPM7hQuorndlorw/y"
    "FrfnnqwB88BqUKmBfgk4AkaI8Tq10aH8Gup+sRi75X7pq697I+AA2BnqN4G1oABMcfShT2UfLeDN3NWxWtZQrhZ48eeBF0AaB/rd4I3o7mb6"
    "m3TjB2Q7X+oolT9xG20HvxwJqEdZSznQ90fE3UGbKP7NdBF34+m8nf/iKJX20kYPoOQ1HNDIwR5yjwT6m8FU8KCjDx2lfTRZNvOLjtWSo7k6"
    "1Yt/Keimpv8+3WgrDHT5oIXjSfT/RTyGP+PbHNWSx730IB1GdMZ7ehvqUkqvYB42/dV0gAdgDMyiuY73aJBeyI9E+X9c7lLV67EDXyBGmbZG"
    "PlSz6X9A6+QaaicD6TDPpl85DlE5/MdF+d8g91ILfRxn0MlsrNe3+GntLK9iHjc90TBljEam9x2b0f9teLk08mLHH2UpRv8F+gEiNg7pYnxn"
    "qun/Se9wMaXxeNqD2M93zCahH8F3JV/k6IcZplaKaZKms7EAuVCiuy0n2fQX8NWSzTXyO8RqfEvu1xulGzLuFTF+rIWyUhei/Vc43tF8+YP+"
    "UEz/M0SYRbt4Ms3HPNTLQfwoPcHX0Wi50PF7RHg3IkPJjiXIvWy9nL+tbzv95dRTvktT8dRyx2h6SKpwJmvLLzvWcZoe4vv0OrgbxfK+HJSf"
    "iOmDFWaPizZ54i6nfjyEO3jrfyXWoJHe+vckGOmtv2nwHR29MSinJ2QI75PU+puBOafQW3+uBYXe+j8SPBnl/7M0koZxZbT+lkkHrDn9OPVG"
    "YSXY4/kXwvda73RcqMNwYk/6l8k+GaBPeG8ERoM057+R+stBnHYu5S6czWvJ2Ia59wU+jzqi1I7u7HM+Zvc2dETWOi7RLij9Kc7HDGf6O+lV"
    "6U23RP5f0iicQYZhdzfIMVGytZVOQMz/cOzQmzB7VUug38R3Ys3rTSui+L+kVTiDxbGzLHdMRO610kUouZVjhz5Kq7Uz/J9i0x+kPnIp70UO"
    "3qzGNj6gL/C/tCMi7OjOPu/pYnlDj8jNjkt0L3qkr5i++fNvZ+yfBqHtArB/xv61M5/6/Hku55/m9t/J/V9zp+uz2X9X4/xbgfwNiEsF9o/V"
    "cur9d1P/YlzpBW1AXHpBX0ynj9/3z8bdKrRdQFyq0H7ZzZ6/sV+3dxh2hgd7yUiAk+Pfi5KT9ID3MnAs1OfiiW2gkxoJkNWk/WPaCZ5JtuFK"
    "rgT6udCJBO8fWjsS4GT/1igpib3/EOhNaxwH05tpHd/f7//paqtCoJ9uLoh7btT+5WCo51+Mp677Sv/Ptd01MK1Rw8H7jzcd5WCo519s856+"
    "ie8ktnbWcKA/8/m7pNn8yzzr/D/5/V/T83+Qf0HvB5mw7DT+MTjE0MIx9Z8YcFJ2nMo/pgNAicTO0n8sSt8FumiQYSfn/+n9x8J/F+gSZl8t"
    "/INa2fxrZIFU+29sMv59/y5N/AN9CebRAVELbWySf75/lyb+5tCSJvA1JLyKgs8JSseVlpH/ImmJM0g6ZvVVjudU+F2dwCn9IuxhDsuq8IkT"
    "tBV33438F8m7WidbdVT4eU4P48qi0P8TcJPtI6P4l2E9vJJL+BXe7XhT/syr9Rco6RkKsL3gEdsLhHo7+1/vdgjs1r+39VZ+WXtKd0cr3YR9"
    "13fgl80B14LLONCf6/unr/v91zf+3/j///r/DyPIMKk="
)


@lru_cache(maxsize=1)
def _envelope_table() -> tuple[tuple[int, ...], ...]:
    raw = zlib.decompress(base64.b64decode("".join(_ENVELOPE_TABLE)))
    return tuple(struct.unpack_from("<64h", raw, 128 * shape) for shape in range(len(raw) // 128))


@dataclass(frozen=True)
class TextArt:
    """글맵시 레코드. 글꼴 종류~그림자 색은 엔진 속성 +0x60~+0x80(FUN_100ff440·FUN_100fbbd0)과 같은 순서다."""

    #: 개체 원래 크기 기준 네 꼭짓점(왼쪽 위부터 시계 방향).
    corners: tuple[tuple[int, int], ...]
    text: str
    font_name: str
    font_style: str
    #: 1 TrueType, 2 HFT. HFT는 HncGetHFTCharWidth·FUN_10100060 경로라 아직 그리지 못한다.
    font_type: int
    shape: int
    line_spacing: int
    char_spacing: int
    align: int
    #: 0이면 그림자 없음. 0이 아닌 값끼리의 차이는 FUN_100fbbd0·FUN_100fc390에 없다.
    shadow_type: int
    #: 개체 크기에 대한 백분율. 양수면 그림자가 오른쪽·아래로 간다.
    shadow_offset: tuple[int, int]
    shadow_color: int
    #: 외곽 조절 핸들(0~1000 좌표). FUN_10104fa0이 차례로 적용한다.
    handles: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class TextArtDrawing:
    #: 개체 상자 왼쪽 위 기준 HWPUNIT 좌표의 글자 윤곽(SVG path d, nonzero 채우기). 채우기·선은 도형 요소 속성을 쓴다.
    path: str
    width: float
    height: float
    #: 그림자 윤곽. 선 없이 shadow_color로 채워 글자보다 먼저 그린다(FUN_100fc390). 그림자가 없으면 None.
    shadow_path: str | None = None
    #: 그림자 색 #rrggbb.
    shadow_color: str | None = None
    #: 실제로 그린 글꼴 이름. 레코드 글꼴이 없으면 DEFAULT_FACE다.
    face: str = ""


class TextArtFontError(LookupError):
    """글맵시 글꼴 파일이나 글자 모양을 찾지 못했다. 한글은 이때 대체 글꼴 DB(HncDRGetSubstFontName)를 쓴다."""


def _read_wstr(payload: bytes, offset: int) -> tuple[str, int]:
    (length,) = struct.unpack_from("<H", payload, offset)
    start = offset + 2
    return payload[start : start + 2 * length].decode("utf-16-le"), start + 2 * length


def parse_textart(payload: bytes) -> TextArt:
    coords = struct.unpack_from("<8i", payload, 0)
    text, offset = _read_wstr(payload, 32)
    font_name, offset = _read_wstr(payload, offset)
    font_style, offset = _read_wstr(payload, offset)
    values = struct.unpack_from("<8iI", payload, offset)
    offset += 36
    (count,) = struct.unpack_from("<i", payload, offset)
    handles = struct.unpack_from(f"<{2 * count}i", payload, offset + 4)
    return TextArt(
        corners=tuple((coords[i], coords[i + 1]) for i in range(0, 8, 2)),
        text=text,
        font_name=font_name,
        font_style=font_style,
        font_type=values[0],
        shape=values[1],
        line_spacing=values[2],
        char_spacing=values[3],
        align=values[4],
        shadow_type=values[5],
        shadow_offset=(values[6], values[7]),
        shadow_color=values[8],
        handles=tuple((handles[i], handles[i + 1]) for i in range(0, len(handles), 2)),
    )


def _cdiv(a: int, b: int) -> int:
    """C 정수 나눗셈(0 쪽으로 자름)."""
    quotient = abs(a) // abs(b)
    return quotient if (a >= 0) == (b > 0) else -quotient


class _Envelope:
    """글맵시 핸들 개체의 외곽 메모리. 정수 첨자 = 바이트 오프셋/4, 외곽 8묶음 x 4점은 오프셋 0x10부터, 모양은 0x110."""

    def __init__(self, shape: int) -> None:
        self.shape = shape
        self.mem = [0] * 0x45
        self.mem[4:0x44] = _envelope_table()[shape]
        self.handles: list[tuple[int, int]] = []

    def __getitem__(self, offset: int) -> int:
        return self.mem[offset // 4]

    def __setitem__(self, offset: int, value: int) -> None:
        self.mem[offset // 4] = value

    def point(self, offset: int) -> tuple[int, int]:
        return self[offset], self[offset + 4]

    def points(self) -> list[tuple[int, int]]:
        return [self.point(0x10 + 8 * i) for i in range(32)]

    def _line(self, start: tuple[int, int], end: tuple[int, int], group: int) -> None:
        """FUN_10106bb0: 묶음을 시작→끝 직선(조절점 1/3·2/3)으로 둔다."""
        if group >= 8:
            return
        base = 0x10 + group * 0x20
        dx, dy = _cdiv(end[0] - start[0], 3), _cdiv(end[1] - start[1], 3)
        self[base], self[base + 4] = start
        self[base + 8], self[base + 0xC] = start[0] + dx, start[1] + dy
        self[base + 0x10], self[base + 0x14] = end[0] - dx, end[1] - dy
        self[base + 0x18], self[base + 0x1C] = end

    def _reverse_copy(self, source: int, target: int, shift: tuple[int, int]) -> None:
        """FUN_10105e70: 묶음을 거꾸로 옮겨 놓고 shift만큼 민다."""
        src = [self.point(0x10 + source * 0x20 + 8 * i) for i in range(4)]
        base = 0x10 + target * 0x20
        for i, (x, y) in enumerate(reversed(src)):
            self[base + 8 * i], self[base + 8 * i + 4] = x + shift[0], y + shift[1]

    def _copy(self, source: int, target: int, shift: tuple[int, int]) -> None:
        """FUN_10105de0(count 1): 묶음을 같은 순서로 옮겨 놓고 shift만큼 민다."""
        src = [self.point(0x10 + source * 0x20 + 8 * i) for i in range(4)]
        base = 0x10 + target * 0x20
        for i, (x, y) in enumerate(src):
            self[base + 8 * i], self[base + 8 * i + 4] = x + shift[0], y + shift[1]

    def _mirror_next(self, group: int) -> None:
        """FUN_10105db0 → FUN_10105d10: 다음 묶음 = 이 묶음을 끝점 x 기준 좌우 반전해 거꾸로 놓은 것."""
        if group < 0 or group + 1 >= 8:
            return
        base, target = 0x10 + group * 0x20, 0x10 + (group + 1) * 0x20
        axis = self[base + 0x18]
        for i in range(4):
            self[target + 8 * i] = -self[base + 8 * i]
            self[target + 8 * i + 4] = self[base + 8 * i + 4]
        self._reverse_copy(group + 1, group + 1, (2 * axis, 0))

    def _point_reflect(self, source: int, target: int, center: tuple[int, int]) -> None:
        """FUN_10105f60: 묶음을 center 점대칭해 거꾸로 놓는다."""
        src = [self.point(0x10 + source * 0x20 + 8 * i) for i in range(4)]
        base = 0x10 + target * 0x20
        for i, (x, y) in enumerate(reversed(src)):
            self[base + 8 * i] = center[0] - (x - center[0])
            self[base + 8 * i + 4] = center[1] - (y - center[1])

    def _bounds(self, offset: int) -> tuple[int, int, int, int]:
        """FUN_101076b0: 점 4개의 (왼, 위, 오른, 아래)."""
        pts = [self.point(offset + 8 * i) for i in range(4)]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)

    def _top_from_bottom(self) -> None:
        """FUN_10106a60: 위 두 묶음 = 아래 두 묶음의 (1000-x, 1000-y)."""
        pairs = (
            (0x6C, 0xB4), (0x68, 0xB0), (0x60, 0xB8), (0x64, 0xBC), (0x58, 0xC0), (0x5C, 0xC4), (0x50, 0xC8),
            (0x54, 0xCC), (0x48, 0xD0), (0x4C, 0xD4), (0x40, 0xD8), (0x44, 0xDC), (0x38, 0xE0), (0x3C, 0xE4),
            (0x34, 0xEC), (0x30, 0xE8),
        )  # fmt: skip
        for target, source in pairs:
            self[target] = HANDLE_RANGE - self[source]

    def _straight_sides(self) -> None:
        """FUN_10106250: 왼·오른 변을 위·아래 끝점을 잇는 직선 두 묶음씩으로 다시 짠다."""
        mid_x, mid_y = _cdiv(self[0xB0] + self[0x30], 2), _cdiv(self[0xB4] + self[0x34], 2)
        self[0x14], self[0x10] = mid_y, mid_x
        dx, dy = _cdiv(self[0x30] - mid_x, 3), _cdiv(self[0x34] - mid_y, 3)
        self[0x18], self[0x1C] = mid_x + dx, mid_y + dy
        self[0x20], self[0x24] = self[0x30] - dx, self[0x34] - dy
        self[0x28], self[0x2C] = self[0x30], self[0x34]
        self[0x94], self[0x90] = mid_y, mid_x
        dx, dy = _cdiv(self[0xB0] - mid_x, 3), _cdiv(self[0xB4] - mid_y, 3)
        self[0x98], self[0x9C] = mid_x + dx, dy + mid_y
        self[0xA0], self[0xA4] = self[0xB0] - dx, self[0xB4] - dy
        self[0xA8], self[0xAC] = self[0xB0], self[0xB4]
        self[0x70] = self[0x68]
        mid_y = _cdiv(self[0x6C] + self[0xEC], 2)
        self[0x74] = self[0x6C]
        mid_x = _cdiv(self[0x68] + self[0xE8], 2)
        dx, dy = _cdiv(mid_x - self[0x68], 3), _cdiv(mid_y - self[0x6C], 3)
        self[0x78], self[0x7C] = self[0x68] + dx, self[0x6C] + dy
        self[0x84], self[0x80] = mid_y - dy, mid_x - dx
        self[0x8C], self[0x88] = mid_y, mid_x
        self[0xF0], self[0xF4] = self[0xE8], self[0xEC]
        dx, dy = _cdiv(mid_x - self[0xE8], 3), _cdiv(mid_y - self[0xEC], 3)
        self[0xF8], self[0xFC] = self[0xE8] + dx, self[0xEC] + dy
        self[0x108], self[0x100], self[0x104], self[0x10C] = mid_x, mid_x - dx, mid_y - dy, mid_y

    def _bottom_from_top_flipped(self) -> None:
        """FUN_10106900: 아래 두 묶음 = 위 두 묶음의 (x, 1000-y)."""
        for target, source in ((0xB0, 0x30), (0xB8, 0x38), (0xC0, 0x40), (0xC8, 0x48)):
            self[target], self[target + 4] = self[source], HANDLE_RANGE - self[source + 4]
        for target, source in ((0xD0, 0x50), (0xD8, 0x58), (0xE0, 0x60), (0xE8, 0x68)):
            self[target], self[target + 4] = self[source], HANDLE_RANGE - self[source + 4]

    def _shift_pair(self, to_top: bool) -> None:
        """FUN_101064a0: 위 두 묶음과 아래 두 묶음 가운데 한쪽을 다른 쪽을 세로로 민 것으로 둔다."""
        if to_top:
            top = min(self._bounds(0xB0)[1], self._bounds(0xD0)[1])
            for offset in range(0x30, 0x70, 8):
                self[offset], self[offset + 4] = self[offset + 0x80], self[offset + 0x84] - top
        else:
            bottom = HANDLE_RANGE - max(self._bounds(0x30)[3], self._bounds(0x50)[3])
            for offset in range(0xB0, 0xF0, 8):
                self[offset], self[offset + 4] = self[offset - 0x80], self[offset - 0x7C] + bottom

    def _shift_sides(self, to_right: bool) -> None:
        """FUN_10106710: 왼 변과 오른 변 가운데 한쪽을 다른 쪽을 거꾸로 가로로 민 것으로 둔다."""
        if to_right:
            shift = HANDLE_RANGE - max(self._bounds(0x90)[2], self._bounds(0x10)[2])
            for target, source in ((0x70, 0x28), (0x78, 0x20), (0x80, 0x18), (0x88, 0x10)):
                self[target], self[target + 4] = self[source] + shift, self[source + 4]
            for target, source in ((0xF0, 0xA8), (0xF8, 0xA0), (0x100, 0x98), (0x108, 0x90)):
                self[target], self[target + 4] = self[source] + shift, self[source + 4]
        else:
            shift = -min(self._bounds(0x70)[0], self._bounds(0xF0)[0])
            for target, source in ((0x10, 0x88), (0x18, 0x80), (0x20, 0x78), (0x28, 0x70)):
                self[target], self[target + 4] = self[source] + shift, self[source + 4]
            for target, source in ((0x90, 0x108), (0x98, 0x100), (0xA0, 0xF8), (0xA8, 0xF0)):
                self[target], self[target + 4] = self[source] + shift, self[source + 4]

    def _straight_top_bottom(self) -> None:
        """FUN_10106020: 위·아래 변을 왼·오른 변 끝점을 잇는 직선 두 묶음씩으로 다시 짠다."""
        for first, last, start in ((0x28, 0x70, 0x30), (0xA8, 0xF0, 0xB0)):
            x0, y0, x1, y1 = self[first], self[first + 4], self[last], self[last + 4]
            mid_x, mid_y = _cdiv(x0 + x1, 2), _cdiv(y0 + y1, 2)
            self._line((x0, y0), (mid_x, mid_y), (start - 0x10) // 0x20)
            self._line((mid_x, mid_y), (x1, y1), (start - 0x10) // 0x20 + 1)

    def clamp(self, index: int, x: int, y: int) -> tuple[int, int]:
        """FUN_10106e90: 모양마다 핸들이 움직일 수 있는 범위로 자른다."""
        shape = self.shape
        if index != 0:
            if index == 1 and 0x10 <= shape <= 0x13:
                return min(max(x, 400), 600), HANDLE_RANGE
            return x, y
        if shape in (0, 1, 0x2A):
            return min(max(x, 300), 700), HANDLE_RANGE
        if shape in (2, 3, 4, 5):
            return (HANDLE_RANGE if shape in (2, 4) else 0), min(max(y, 0), HANDLE_RANGE)
        if shape in (6, 7, 0xF, 0x27):
            return (HANDLE_RANGE if shape == 6 else 0), min(max(y, 0), 500)
        if shape == 8:
            return min(max(x, 0), 500), 0
        if shape in (9, 0x2B, 0x2C):
            return min(max(x, 0), 500), HANDLE_RANGE
        if shape == 0x2D:
            return min(max(x, 500), HANDLE_RANGE), HANDLE_RANGE
        limits = {
            0xA: (0, 700, 500), 0x24: (0, 700, 500), 0xB: (300, 1000, 500), 0x25: (300, 1000, 500),
            0xC: (300, 1000, 0), 0x29: (300, 1000, 0), 0xD: (0, 700, 0), 0x28: (0, 700, 0),
            0xE: (0, 500, 500), 0x1C: (0, 500, 500), 0x12: (750, 1000, 0), 0x13: (0, 250, 0),
            0x1D: (500, 1000, 500), 0x20: (0, 350, 500), 0x21: (0, 350, 500), 0x26: (0, 350, 500),
        }  # fmt: skip
        if shape in limits:
            low, high, fixed_x = limits[shape]
            return fixed_x, min(max(y, low), high)
        if shape in (0x10, 0x11):
            return (HANDLE_RANGE if shape == 0x10 else 0), min(max(y, 0), 250)
        return x, y

    def apply_handle(self, index: int, x: int, y: int) -> None:
        """FUN_10104fa0: 핸들 하나를 옮긴 결과로 외곽을 고친다."""
        x, y = self.clamp(index, x, y)
        p = (x, y)
        shape = self.shape
        if index != 0:
            if shape in (0x10, 0x11):
                left = max(x - 350, 0)
                self[0xB0] = left
                third = _cdiv(x - left, 3)
                self[0xB8] = third + left
                self[0xC8] = x
                self[0xC0] = x - third
                self[0xE8] = (x - left) + self[0xC8]
                self[0xEC] = (self[0xCC] - self[0xB4]) + self[0xCC]
                self[0xE0] = (self[0xC8] - self[0xB8]) + self[0xC8]
                self[0xE4] = (self[0xCC] - self[0xBC]) + self[0xCC]
                self[0xD8] = self[0xC8] - (self[0xC0] - self[0xC8])
                self[0xDC] = self[0xCC] - (self[0xC4] - self[0xCC])
                self[0xD0] = self[0xC8] - (x - self[0xC8])
                self[0xD4] = self[0xCC]
                self._top_from_bottom()
                self._straight_sides()
            elif shape in (0x12, 0x13):
                old_left, old_right = self[0xB0], self[0xC8]
                left = max(x - 350, 0)
                self[0xB0] = left
                self[0xC8] = x
                ratio = _f32(_f32(abs(x - left)) / _f32(abs(old_right - old_left)))
                self[0xB8] = int(_f32(self[0xB8] - old_left) * ratio) + left
                self[0xC0] = int(_f32(self[0xC0] - old_left) * ratio) + left
                for offset in (0xD0, 0xD8, 0xE0, 0xE8):
                    self[offset + 4] = self[offset - 0x1C]
                    self[offset] = -self[offset - 0x20]
                self._reverse_copy(6, 6, (2 * x, 0))
                self._shift_pair(to_top=True)
                for offset in range(0x30, 0x70, 8):
                    self[offset] += HANDLE_RANGE - 2 * x
                self._straight_sides()
            self._store(index, p)
            return
        if shape in (0, 1, 0x2A):
            self._line((max(x - 350, 0), y), p, 5)
            for offset in (0xD0, 0xD8, 0xE0, 0xE8):
                self[offset + 4] = self[offset - 0x1C]
                self[offset] = -self[offset - 0x20]
            self._reverse_copy(6, 6, (2 * self[0xC8], 0))
            self._top_from_bottom()
            self._straight_sides()
        elif shape in (2, 4):
            first = 0x30 if shape == 2 else 0xB0
            group = 1 if shape == 2 else 5
            start = self.point(first)
            mid = (_cdiv(start[0] + x, 2), _cdiv(start[1] + y, 2))
            self._line(start, mid, group)
            self._line(mid, p, group + 1)
            self._straight_sides()
        elif shape in (3, 5):
            last = 0x68 if shape == 3 else 0xE8
            group = 1 if shape == 3 else 5
            end = self.point(last)
            mid = (_cdiv(x + end[0], 2), _cdiv(y + end[1], 2))
            self._line(p, mid, group)
            self._line(mid, end, group + 1)
            self._straight_sides()
        elif shape in (6, 7):
            if shape == 6:
                start = self.point(0x30)
                mid = (_cdiv(start[0] + x, 2), _cdiv(start[1] + y, 2))
                self._line(start, mid, 1)
                self._line(mid, p, 2)
            else:
                end = self.point(0x68)
                mid = (_cdiv(x + end[0], 2), _cdiv(y + end[1], 2))
                self._line(p, mid, 1)
                self._line(mid, end, 2)
            self._bottom_from_top_flipped()
            self._straight_sides()
        elif shape in (8, 9):
            group = 5 if shape == 9 else 1
            self._line(p, self.point(0xC8 if shape == 9 else 0x48), group)
            self._mirror_next(group)
            self._straight_sides()
        elif shape in (0xA, 0xB):
            group = 5 if shape == 0xB else 1
            self._line(self.point(0xB0 if shape == 0xB else 0x30), p, group)
            self._mirror_next(group)
            self._straight_sides()
        elif shape in (0xC, 0xD):
            group = 5 if shape == 0xC else 1
            self._line(p, self.point(0xC8 if shape == 0xC else 0x48), group)
            self._mirror_next(group)
            self._shift_pair(to_top=shape == 0xC)
            self._straight_sides()
        elif shape in (0xE, 0xF, 0x26, 0x27):
            if shape == 0xE:
                self._line(self.point(0x30), p, 1)
            elif shape == 0xF:
                self._line(p, self.point(0x48), 1)
            elif shape == 0x26:
                start = self.point(0x30)
                third = _cdiv(x - start[0], 3)
                self[0x38], self[0x3C] = start[0] + third, start[1] + _cdiv(y - start[1], 3)
                self[0x48], self[0x40], self[0x4C], self[0x44] = x, x - third, y, y
            else:
                self[0x30], self[0x34] = x, y
                dx, dy = _cdiv(self[0x48] - x, 3), _cdiv(self[0x4C] - y, 3)
                self[0x38], self[0x3C] = x + dx, dy + y
                self[0x40], self[0x44] = self[0x48] - dx, self[0x4C]
            for offset in (0x50, 0x58, 0x60, 0x68):
                self[offset + 4] = self[offset - 0x1C]
                self[offset] = -self[offset - 0x20]
            self._reverse_copy(2, 2, (2 * self[0x48], 0))
            self._bottom_from_top_flipped()
            self._straight_sides()
        elif shape in (0x10, 0x11):
            if shape == 0x10:
                self[0x54], self[0x6C] = y, y
                self._point_reflect(2, 1, self.point(0x50))
            else:
                self[0x4C], self[0x34] = y, y
                self._point_reflect(1, 2, self.point(0x48))
            self._shift_pair(to_top=False)
            self._straight_sides()
        elif shape in (0x12, 0x13):
            group = 5 if shape == 0x12 else 1
            base = 0x10 + group * 0x20
            bulge = int(abs(self[base + 0x1C] - y) / 1.5)  # 1.5: HwpAppModule 0x1040a858
            self._line(p, self.point(base + 0x18), group)
            self[base + 8] += bulge
            self[base + 0x10] -= bulge
            self[base + 0xC] = y
            self[base + 0x14] = self[base + 0x1C]
            self._mirror_next(group)
            self._shift_pair(to_top=shape == 0x12)
            self._straight_sides()
        elif shape in (0x1C, 0x1D, 0x24, 0x25):
            group = 1 if shape in (0x1C, 0x24) else 5
            base = 0x10 + group * 0x20
            self._line(self.point(base), p, group)
            self[base + 0x14] = self[base + 0x1C]
            self._mirror_next(group)
        elif shape in (0x20, 0x21):
            self._line(self.point(0x30), p, 1)
            self._line(p, self.point(0x68), 2)
            self._bottom_from_top_flipped()
            self._straight_sides()
        elif shape in (0x28, 0x29):
            group = 1 if shape == 0x28 else 5
            base = 0x10 + group * 0x20
            self._line(p, self.point(base + 0x18), group)
            self[base + 0x14] = self[base + 0x1C]
            self._mirror_next(group)
            self._straight_sides()
        elif shape in (0x2B, 0x2C):
            start = self.point(0x90)
            self._line(start, p, 4)
            # FUN_10105c90: x 조절점을 시작점 쪽으로 다시 놓는다.
            third = _cdiv(x + start[0], 3)
            self[0x90], self[0xA0], self[0x98] = start[0], third - _cdiv(third - start[0], 3), start[0]
            for offset in range(0x10, 0x30, 8):
                self[offset], self[offset + 4] = self[offset + 0x80], -self[offset + 0x84]
            self._copy(0, 0, (0, 2 * self[0x94]))
            if shape == 0x2B:
                self._shift_sides(to_right=True)
            else:
                for offset in range(0x70, 0x90, 8):
                    self[offset + 4], self[offset] = self[offset - 0x5C], -self[offset - 0x60]
                self._reverse_copy(3, 3, (HANDLE_RANGE, 0))
                for offset in range(0xF0, 0x110, 8):
                    self[offset + 4], self[offset] = self[offset - 0x5C], -self[offset - 0x60]
                self._reverse_copy(7, 7, (HANDLE_RANGE, 0))
            self._straight_top_bottom()
        elif shape == 0x2D:
            self[0xF0], self[0xF4] = x, y
            dx, dy = _cdiv(self[0x108] - x, 3), _cdiv(self[0x10C] - y, 3)
            self[0xF8], self[0xFC] = x + dx, dy + y
            self[0x104] = self[0x10C] - dy
            right = self[0x108]
            self[0x100] = right
            self[0xF8] = _cdiv(_cdiv(right + x, 3) - x, 3) + x
            for offset in range(0x70, 0x90, 8):
                self[offset], self[offset + 4] = self[offset + 0x80], -self[offset + 0x84]
            self._copy(3, 3, (0, 2 * self[0x10C]))
            self._shift_sides(to_right=False)
            self._straight_top_bottom()
        else:
            self._store(index, p)
            return
        if len(self.handles) == 2:
            self.apply_handle(1, *self.handles[1])
        self._store(index, p)

    def _store(self, index: int, point: tuple[int, int]) -> None:
        if index < len(self.handles):
            self.handles[index] = point


def _f32(value: float) -> float:
    return struct.unpack("<f", struct.pack("<f", value))[0]


def envelope_points(shape: int, handles: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """모양 표 외곽에 저장된 핸들을 차례로 적용한 32점(0~1000 좌표)."""
    if not 0 <= shape < len(_envelope_table()):
        raise ValueError(f"글맵시 모양 번호 {shape}는 엔진 표(0~{len(_envelope_table()) - 1})에 없다")
    envelope = _Envelope(shape)
    envelope.handles = list(handles)
    for index, (x, y) in enumerate(handles):
        envelope.apply_handle(index, x, y)
    return envelope.points()


# ---- 외곽 네 변(HncDrawingEngine TextWarp 경로) ----


@dataclass(frozen=True)
class _Segment:
    #: 직선은 점 2개, 3차 베지어는 4개.
    points: tuple[tuple[float, float], ...]

    @property
    def length(self) -> float:
        return self._table()[0][-1]

    def _table(self) -> tuple[list[float], list[tuple[float, float]]]:
        cached = self.__dict__.get("_cache")
        if cached is None:
            if len(self.points) == 2:
                (x0, y0), (x1, y1) = self.points
                cached = ([0.0, math.hypot(x1 - x0, y1 - y0)], [self.points[0], self.points[1]])
            else:
                cached = _bezier_length_table(self.points)
            object.__setattr__(self, "_cache", cached)
        return cached

    def at(self, fraction: float) -> tuple[float, float]:
        if len(self.points) == 2:
            (x0, y0), (x1, y1) = self.points
            return x0 + (x1 - x0) * fraction, y0 + (y1 - y0) * fraction
        lengths, samples = self._table()
        # FUN_1020e040: 길이 표에서 이분 탐색으로 고른 표본점을 그대로 쓴다(사이를 보간하지 않는다).
        index = min(bisect.bisect_left(lengths, lengths[-1] * fraction), len(samples) - 1)
        return samples[index]


def _bezier_length_table(points: Sequence[tuple[float, float]]) -> tuple[list[float], list[tuple[float, float]]]:
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = points
    lengths, samples = [0.0], [(x0, y0)]
    steps = round(1 / BEZIER_LENGTH_STEP)
    for step in range(1, steps + 1):
        t = step / steps
        u = 1 - t
        x = u * u * u * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t * t * t * x3
        y = u * u * u * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t * t * t * y3
        lengths.append(lengths[-1] + math.hypot(x - samples[-1][0], y - samples[-1][1]))
        samples.append((x, y))
    return lengths, samples


def _degrees(dx: float, dy: float) -> int:
    return 0 if dx == 0 and dy == 0 else int(math.atan2(dy, dx) * DEGREES_PER_RADIAN)


def _group_segment(group: Sequence[tuple[float, float]]) -> _Segment:
    """FUN_100fcd40: 묶음 4점이 시작점에서 본 각도(정수 도)가 모두 같으면 직선, 아니면 3차 베지어."""
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = group
    chord = _degrees(x3 - x0, y3 - y0)
    if chord == _degrees(x1 - x0, y1 - y0) and chord == _degrees(x2 - x0, y2 - y0):
        return _Segment((group[0], group[3]))
    return _Segment(tuple(group))


class _Side:
    def __init__(self, segments: list[_Segment]) -> None:
        self.segments = segments
        self.length = sum(segment.length for segment in segments)

    def at(self, fraction: float) -> tuple[float, float]:
        """FUN_10205090 한 변: 변 전체 길이 비율 fraction에 놓인 구간을 찾아 그 구간 안 비율로 점을 고른다."""
        travelled = 0.0
        for segment in self.segments:
            start = travelled
            travelled += segment.length
            low = start / self.length if self.length else fraction
            high = travelled / self.length if self.length else fraction
            if low <= fraction <= high:
                inner = 0.0 if high - low == 0 else (fraction - low) / (high - low)
                return segment.at(inner)
        return self.segments[-1].at(1.0)


def _warp_sides(points: Sequence[tuple[int, int]]) -> tuple[_Side, _Side, _Side, _Side]:
    """외곽 32점을 정사각 좌표로 늘려 위·아래·왼·오른 변을 만든다(FUN_100fcd40 묶음 1,2 / 5,6 / 0r,4 / 3,7r)."""
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    left, top = min(xs), min(ys)
    width, height = max(xs) - left, max(ys) - top
    scaled = [((x - left) / width * WARP_SIZE, (y - top) / height * WARP_SIZE) for x, y in points]
    groups = [scaled[4 * g : 4 * g + 4] for g in range(8)]

    def side(order: Sequence[tuple[int, bool]]) -> _Side:
        segments: list[_Segment] = []
        for group, forward in order:
            pts = groups[group] if forward else groups[group][::-1]
            if segments and segments[-1].points[-1] != pts[0]:
                segments.append(_Segment((segments[-1].points[-1], pts[0])))
            segments.append(_group_segment(pts))
        return _Side(segments)

    return (
        side(((1, True), (2, True))),
        side(((5, True), (6, True))),
        side(((0, False), (4, True))),
        side(((3, True), (7, False))),
    )


def _warp_point(sides: tuple[_Side, _Side, _Side, _Side], u: float, v: float) -> tuple[float, float]:
    """FUN_10205090: y는 위·아래 변의 u 위치 y를 v로, x는 왼·오른 변의 v 위치 x를 u로 섞는다."""
    top, bottom, left, right = sides
    cu, cv = min(max(u, 0.0), 1.0), min(max(v, 0.0), 1.0)
    y = (1 - v) * top.at(cu)[1] + v * bottom.at(cu)[1]
    x = (1 - u) * left.at(cv)[0] + u * right.at(cv)[0]
    return x, y


# ---- 글자 윤곽(GDI+ GdipAddPathString 경로) ----

#: 윤곽 명령. ("M", 점) · ("L", 점) · ("C", 조절점1, 조절점2, 끝점) · ("Z",).
Command = tuple


class _OutlinePen:
    """fontTools 펜. GDI+ 경로처럼 TrueType 2차 곡선을 3차 베지어(조절점 2/3 지점)로 바꿔 모은다."""

    def __init__(self, glyph_set, dx: float, scale: float) -> None:
        from fontTools.pens.basePen import BasePen

        outer = self

        class _Pen(BasePen):
            def _moveTo(self, pt):
                outer.commands.append(("M", outer._map(pt)))

            def _lineTo(self, pt):
                outer.commands.append(("L", outer._map(pt)))

            def _curveToOne(self, pt1, pt2, pt3):
                outer.commands.append(("C", outer._map(pt1), outer._map(pt2), outer._map(pt3)))

            def _qCurveToOne(self, pt1, pt2):
                (x0, y0) = self._getCurrentPoint()
                c1 = (x0 + 2 / 3 * (pt1[0] - x0), y0 + 2 / 3 * (pt1[1] - y0))
                c2 = (pt2[0] + 2 / 3 * (pt1[0] - pt2[0]), pt2[1] + 2 / 3 * (pt1[1] - pt2[1]))
                self._curveToOne(c1, c2, pt2)

            def _closePath(self):
                outer.commands.append(("Z",))

            _endPath = _closePath

        self.commands: list[Command] = []
        self.dx = dx
        self.scale = scale
        self.pen = _Pen(glyph_set)

    def _map(self, pt) -> tuple[float, float]:
        # 글꼴 좌표(위로 +)를 GDI+ 경로 좌표(아래로 +, 기준선 0)로 뒤집는다.
        return self.dx + pt[0] * self.scale, -pt[1] * self.scale


class FontFiles:
    """글꼴 이름(name 표 ID 1·4, 모든 언어) → (파일, TTC 번호). 한글은 Shared/TTF도 개인 글꼴로 등록해 찾는다."""

    def __init__(self, directories: Sequence[Path]) -> None:
        self.directories = [Path(d) for d in directories]
        self._index: dict[str, tuple[Path, int]] | None = None

    def find(self, face: str) -> tuple[Path, int]:
        if self._index is None:
            self._index = self._scan()
        found = self._index.get(face)
        if found is None:
            raise TextArtFontError(f"글맵시 글꼴 '{face}'을 {', '.join(map(str, self.directories))}에서 찾지 못했다")
        return found

    def _scan(self) -> dict[str, tuple[Path, int]]:
        from fontTools.ttLib import TTCollection, TTFont, TTLibError

        index: dict[str, tuple[Path, int]] = {}
        for directory in self.directories:
            for path in sorted(directory.rglob("*")):
                if path.suffix.lower() not in (".ttf", ".ttc", ".otf"):
                    continue
                try:
                    fonts = (
                        TTCollection(str(path), lazy=True).fonts
                        if path.suffix.lower() == ".ttc"
                        else [TTFont(str(path), lazy=True)]
                    )
                except (TTLibError, OSError, struct.error):
                    continue  # 깨진 글꼴 파일은 후보에서 뺀다. 그 이름을 찾으면 find가 TextArtFontError를 낸다.
                for number, font in enumerate(fonts):
                    for record in font["name"].names:
                        if record.nameID in (1, 4):
                            index.setdefault(record.toUnicode(errors="replace"), (path, number))
        return index


@lru_cache(maxsize=32)
def _load_font(path: Path, number: int):
    from fontTools.ttLib import TTFont

    return TTFont(str(path), fontNumber=number)


def _layout(record: TextArt, fonts: Sequence) -> list[list[Command]]:
    """FUN_100fe610: 글자마다 GDI+ 윤곽을 펜 위치에 놓고 펜을 (글자 폭 x 글자 간격%)만큼 민다. 공백은 폭만 더한다.

    좌표는 em 단위다(GDI+는 같은 em 크기로 그리므로 글꼴마다 unitsPerEm으로 나눈다). 글자가 앞 글꼴에 없으면
    다음 글꼴에서 찾는다(FUN_100ff2d0이 글자마다 GlyphExist로 글꼴을 바꾼다).
    """
    lines: list[list[Command]] = [[]]
    pen_x = 0.0
    for char in record.text:
        if char == LINE_FEED:
            continue
        if char == LINE_BREAK:
            lines.append([])
            pen_x = 0.0
            continue
        for font in fonts:
            glyph = font.getBestCmap().get(ord(char))
            if glyph is not None:
                break
        else:
            raise TextArtFontError(f"글맵시 글꼴 '{record.font_name}'과 대체 글꼴에 글자 U+{ord(char):04X}가 없다")
        glyph_set = font.getGlyphSet()
        scale = 1 / font["head"].unitsPerEm
        if char != SPACE:
            outline = _OutlinePen(glyph_set, pen_x, scale)
            glyph_set[glyph].draw(outline.pen)
            lines[-1].extend(outline.commands)
        pen_x += glyph_set[glyph].width * scale * record.char_spacing / PERCENT
    return [line for line in lines if line]


def _command_points(commands: Sequence[Command]) -> list[tuple[float, float]]:
    return [pt for command in commands for pt in command[1:]]


def _warp_line(commands: Sequence[Command], sides, step: float) -> list[Command]:
    """FUN_10202350: 글줄 윤곽 bbox를 외곽 정사각에 맞춘 뒤 점마다 FUN_10205090을 적용한다.

    직선은 step 간격으로 나눠 휜다(FUN_10203880). 엔진은 나눈 점을 지나는 매끈한 곡선(GetCurveControlPoints)으로
    잇지만 여기서는 꺾은선으로 잇는다(간격이 외곽 한 변의 1/141이라 차이가 작다). 곡선은 조절점을 그대로 옮긴다.
    """
    pts = _command_points(commands)
    left, top = min(p[0] for p in pts), min(p[1] for p in pts)
    width, height = max(p[0] for p in pts) - left, max(p[1] for p in pts) - top

    def to_square(pt: tuple[float, float]) -> tuple[float, float]:
        return (pt[0] - left) / width * WARP_SIZE, (pt[1] - top) / height * WARP_SIZE

    def warp(pt: tuple[float, float]) -> tuple[float, float]:
        return _warp_point(sides, pt[0] / WARP_SIZE, pt[1] / WARP_SIZE)

    warped: list[Command] = []
    current = start = (0.0, 0.0)
    for command in commands:
        kind = command[0]
        if kind == "M":
            current = start = to_square(command[1])
            warped.append(("M", warp(current)))
        elif kind == "L" or kind == "Z":
            target = start if kind == "Z" else to_square(command[1])
            length = math.hypot(target[0] - current[0], target[1] - current[1])
            if length >= step:
                fraction = step / length
                t = fraction
                while t < 1.0:
                    sample = (current[0] + (target[0] - current[0]) * t, current[1] + (target[1] - current[1]) * t)
                    warped.append(("L", warp(sample)))
                    t += fraction
            warped.append(("L", warp(target)))
            if kind == "Z":
                warped.append(("Z",))
            current = target
        else:
            c1, c2, end = (to_square(pt) for pt in command[1:])
            warped.append(("C", warp(c1), warp(c2), warp(end)))
            current = end
    return warped


def _svg_path(commands: Sequence[Command], sx: float, sy: float) -> str:
    parts = []
    for command in commands:
        coords = " ".join(f"{x * sx:.2f} {y * sy:.2f}" for x, y in command[1:])
        parts.append(f"{command[0]}{coords}" if coords else command[0])
    return "".join(parts)


def _fit(commands: Sequence[Command], offset: tuple[int, int], shadow: bool) -> list[Command]:
    """FUN_100fbbd0(글자)·FUN_100fc390(그림자): 윤곽 bbox를 (1-|간격|%)로 줄여 간격 반대쪽(그림자는 간격 쪽)에 둔다."""
    pts = _command_points(commands)
    left, top = min(p[0] for p in pts), min(p[1] for p in pts)
    width, height = max(p[0] for p in pts) - left, max(p[1] for p in pts) - top
    scale_x = WARP_SIZE * (1 - abs(offset[0]) * SHADOW_OFFSET_UNIT)
    scale_y = WARP_SIZE * (1 - abs(offset[1]) * SHADOW_OFFSET_UNIT)
    shift_x = WARP_SIZE * abs(offset[0]) * SHADOW_OFFSET_UNIT if (offset[0] > 0) == shadow else 0.0
    shift_y = WARP_SIZE * abs(offset[1]) * SHADOW_OFFSET_UNIT if (offset[1] > 0) == shadow else 0.0
    return [
        (
            command[0],
            *(((x - left) * scale_x / width + shift_x, (y - top) * scale_y / height + shift_y) for x, y in command[1:]),
        )
        for command in commands
    ]


# ---- 원 모양(HncDrawingEngine HwpCircleAlgorithm, vtable 0x1055be44) ----

#: float π(HncDrawingEngine 0x1055e2a8).
CIRCLE_PI = 3.1415927410125732
#: 회전각 도→라디안(0x1055dfbc)과 호 길이 도→라디안(0x1055e108). 엔진이 서로 다른 상수를 쓴다.
CIRCLE_ROTATE_RADIANS, CIRCLE_ARC_RADIANS = 0.017453288659453392, 0.017453292519943295
#: 여러 줄 원 모양의 줄 간격 배율(0x1055e110).
CIRCLE_LINE_PITCH = 1.2
#: FontMetrics 줄 높이(InputData +0x18)→경로 단위 배율. 확인하지 못함: pt→px이면 4/3일 수 있다(한 줄은 영향 없음).
CIRCLE_METRIC_TO_PATH = 1.0
#: 레코드 모양 번호 → HwpCircleAlgorithm 종류(FUN_100fcd40).
_CIRCLE_KINDS = {48: 0x2A, 49: 0x2B, 50: 0x2C, 51: 0x2D, 52: 0x2E, 53: 0x2F, 54: 0x30}

#: 2x3 아핀 행렬 (m0, m1, m2, m3, m4, m5): x' = m0 x + m1 y + m2, y' = m3 x + m4 y + m5(FUN_1020fa10).
Matrix = tuple[float, float, float, float, float, float]


@dataclass
class _CircleGlyph:
    """FUN_100fe610 InputData. cmds는 셀 위쪽 원점(아래로 +) 글자 경로이고 기준선은 y = 셀 ascent다."""

    line: int
    #: InputData +4 펜 위치(정렬 반영)·+8 줄 y·+0xc 글자 폭 x 글자 간격%·+0x18 줄 높이.
    x: float
    y: float
    w: float
    line_height: float
    cmds: list[Command]


@dataclass
class _Rect:
    valid: bool = False
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0

    def union(self, other: _Rect) -> None:  # FUN_1046d050
        if not other.valid:
            return
        if not self.valid:
            self.valid, self.x, self.y, self.w, self.h = True, other.x, other.y, other.w, other.h
            return
        x0, y0 = min(self.x, other.x), min(self.y, other.y)
        x1, y1 = max(self.x + self.w, other.x + other.w), max(self.y + self.h, other.y + other.h)
        self.x, self.y, self.w, self.h = x0, y0, x1 - x0, y1 - y0


def _path_bounds(commands: Sequence[Command]) -> _Rect:  # TextWarpUtil::GetBoundsPath (조절점 포함)
    pts = _command_points(commands)
    if not pts:
        return _Rect()
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return _Rect(True, min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def _transform(commands: Sequence[Command], m: Matrix) -> list[Command]:  # FUN_1020fa10
    a, b, c, d, e, f = m
    return [(k[0], *((a * x + b * y + c, d * x + e * y + f) for x, y in k[1:])) for k in commands]


def _matmul(p: Matrix, q: Matrix) -> Matrix:
    """p·q(q를 먼저 적용한다)."""
    a, b, c, d, e, f = p
    g, h, i, j, k, l = q  # noqa: E741
    return (a * g + b * j, a * h + b * k, a * i + b * l + c, d * g + e * j, d * h + e * k, d * i + e * l + f)


def _translate(tx: float, ty: float) -> Matrix:
    return (1.0, 0.0, tx, 0.0, 1.0, ty)


def _scale(sx: float, sy: float) -> Matrix:
    return (sx, 0.0, 0.0, 0.0, sy, 0.0)


def _rotate(degrees: float, cx: float, cy: float) -> Matrix:
    t = degrees * CIRCLE_ROTATE_RADIANS
    c, s = math.cos(t), math.sin(t)
    return (c, -s, s * cy + (1 - c) * cx, s, c, (1 - c) * cy - s * cx)


def _circle_layout(record: TextArt, fonts: Sequence) -> list[_CircleGlyph]:
    """FUN_100fe610: 공백이 아닌 글자마다 InputData(줄, x, y, 폭, 줄 높이, 경로)를 만든다."""
    if record.align not in (0, 1, 2):
        # 정렬 3·4의 펜 위치 식은 옮기지 않았다.
        raise NotImplementedError(f"원 모양 글맵시 정렬 {record.align}은 아직 그리지 못한다")
    lines: list[list] = [[]]
    for char in record.text:
        if char == LINE_FEED:
            continue
        if char == LINE_BREAK:
            lines.append([])
            continue
        for font in fonts:
            glyph = font.getBestCmap().get(ord(char))
            if glyph is not None:
                break
        else:
            raise TextArtFontError(f"글맵시 글꼴 '{record.font_name}'과 대체 글꼴에 글자 U+{ord(char):04X}가 없다")
        lines[-1].append((char, font, glyph))
    widths = [
        sum(
            font.getGlyphSet()[glyph].width / font["head"].unitsPerEm * record.char_spacing / PERCENT
            for _, font, glyph in line
        )
        for line in lines
    ]
    max_width = max(widths) if widths else 0.0
    out: list[_CircleGlyph] = []
    y = 0.0
    for number, line in enumerate(lines):
        pen = 0.0
        if record.align == 1:
            pen = max_width - widths[number]
        elif record.align == 2:
            pen = (max_width - widths[number]) * 0.5
        line_height = 0.0
        for char, font, glyph in line:
            upem = font["head"].unitsPerEm
            glyph_set = font.getGlyphSet()
            os2 = font["OS/2"] if "OS/2" in font else None
            ascent = (os2.usWinAscent if os2 else font["hhea"].ascent) / upem
            descent = (os2.usWinDescent if os2 else -font["hhea"].descent) / upem
            line_height = record.line_spacing / PERCENT * (ascent + descent) * CIRCLE_METRIC_TO_PATH
            width = glyph_set[glyph].width / upem * record.char_spacing / PERCENT
            if char != SPACE:
                outline = _OutlinePen(glyph_set, 0.0, 1 / upem)
                glyph_set[glyph].draw(outline.pen)
                cmds = [(c[0], *((px, py + ascent) for px, py in c[1:])) for c in outline.commands]
                out.append(_CircleGlyph(number, pen, y, width, line_height, cmds))
            pen += width
        y += line_height
    return out


@dataclass
class _CirclePrepared:
    """FUN_1020c780 + 슬롯 1 FUN_10202170 결과(처리 입력)."""

    #: 줄별 +4: {x, y, 폭, 줄 높이} 합집합.
    rect_a: list[_Rect]
    #: 줄별 +0x18: 글자 잉크 범위 + (x, y) 합집합.
    rect_b: list[_Rect]
    #: inputs[7..0xb]: 전체 rect A 합집합(inputs[10] = 폭).
    total_a: _Rect
    #: inputs[0x16]: 안내선 4개 = 한 묶음의 잉크 높이 최댓값.
    max_ink_h: float


def _circle_prepare(glyphs: list[_CircleGlyph]) -> _CirclePrepared:
    count = max(g.line for g in glyphs) + 1
    rect_a = [_Rect() for _ in range(count)]
    rect_b = [_Rect() for _ in range(count)]
    total = _Rect()
    for g in glyphs:
        cell = _Rect(True, g.x, g.y, g.w, g.line_height)
        rect_a[g.line].union(cell)
        total.union(cell)
        ink = _path_bounds(g.cmds)
        if ink.valid:
            rect_b[g.line].union(_Rect(True, ink.x + g.x, ink.y + g.y, ink.w, ink.h))
    group = _Rect()  # 원 알고리즘은 안내선 4개 = 한 묶음
    for rect in rect_b:
        group.union(rect)
    return _CirclePrepared(rect_a, rect_b, total, group.h)


def _place_arc(kind: int, glyphs, prep: _CirclePrepared, start: int, end: int, rect, a: float, b: float, c: float):
    """FUN_10205620: start~end 줄 글자를 호 위에 세운다. rect = (x, y, w, h). 반환은 글자별 변환된 경로다."""
    rx, ry, rw, rh = rect
    half = a * 0.5
    radius = min(half, b)
    base, span, direction, cy = 180, 180, -1, 0.0
    if kind == 0x2A:
        cy = b
    elif kind == 0x2B:
        cy, direction = 0.0, 1
    elif kind in (0x2C, 0x2D):
        span, cy = 360, b * 0.5
        direction = 1 if kind == 0x2D else -1
        radius = min(half, cy)
    elif kind == 0x31:
        cy, base = 0.0, 0
    n_all = len(prep.rect_a)
    if end == -1:
        end = n_all
    n = end - start
    line_h = c if n == 1 else c / ((n - 1) * CIRCLE_LINE_PITCH + 1.0)
    pitch = c if n == 1 else line_h * CIRCLE_LINE_PITCH
    union = _Rect()
    for k in range(start, end):
        union.union(prep.rect_a[k])
    out = []
    if not union.w > 0:
        return out
    avg_h = prep.max_ink_h / n_all
    for g in glyphs:
        if not start <= g.line < end:
            continue
        off = (g.line - start) * pitch
        s = g.w * c / avg_h
        angle = ((g.w * 0.5 + (g.x - union.x)) * float(direction * span)) / union.w + base
        tx = half - s * 0.5
        ink = _path_bounds(g.cmds)
        cmds = _transform(g.cmds, _translate((g.w - ink.w) * 0.5 - ink.x, 0.0))
        if kind in (0x2B, 0x2D):
            ty = (b - off) - line_h
            rot = -90.0 - angle
        else:
            ty = (cy - radius) + off
            rot = 90.0 - angle
        m = _scale(s / g.w, line_h / prep.rect_b[g.line].h)
        m = _matmul(_translate(tx, ty), m)
        m = _matmul(_rotate(rot, half, cy), m)
        m = _matmul(_scale(rw / a, rh / b), m)
        m = _matmul(_translate(rx, ry), m)
        out.append(_transform(cmds, m))
    return out


def _place_straight(glyphs, prep: _CirclePrepared, start: int, end: int, rect, a: float, b: float):
    """FUN_102066c0: 가운데 줄을 회전 없이 a x b 상자에 놓는다."""
    rx, ry, rw, rh = rect
    if end == -1:
        end = len(prep.rect_a)
    n = end - start
    line_h = b if n == 1 else b / ((n - 1) * CIRCLE_LINE_PITCH + 1.0)
    pitch = b if n == 1 else line_h * CIRCLE_LINE_PITCH
    union = _Rect()
    for k in range(start, end):
        union.union(prep.rect_b[k])
    out = []
    if not union.w > 0:
        return out
    for g in glyphs:
        if not start <= g.line < end:
            continue
        tx = (a / union.w) * (g.x - union.x)
        ty = (g.line - start) * pitch
        m = _scale((a / union.w) * g.w / g.w, line_h / g.line_height)  # 엔진처럼 글자 폭을 곱했다가 나눈다
        m = _matmul(_translate(tx, ty), m)
        m = _matmul(_scale(rw / a, rh / b), m)
        m = _matmul(_translate(rx, ry), m)
        out.append(_transform(g.cmds, m))
    return out


def _circle_single(kind: int, glyphs, prep: _CirclePrepared, rect):
    """FUN_10207190 (0x2a~0x2d): 모든 줄을 반원(0x2a·0x2b) 또는 온원(0x2c·0x2d) 하나에 놓는다."""
    _, _, w, h = rect
    if kind in (0x2A, 0x2B):
        if h * 2.0 > w:
            h = w * 0.5
        else:
            w = h * 2.0
        radius, degrees = min(w * 0.5, h), 180
    else:
        if h > w:
            h = w
        else:
            w = h
        radius, degrees = min(h * 0.5, w * 0.5), 360
    n = len(prep.rect_a)
    theta = degrees * CIRCLE_ARC_RADIANS
    c = theta * radius / (prep.total_a.w / (prep.max_ink_h / n) + theta)
    return _place_arc(kind, glyphs, prep, 0, -1, rect, w, h, c)


def _circle_halves(glyphs, prep: _CirclePrepared, rect):
    """FUN_10207310 (0x30): 앞쪽 절반 줄은 위 반원, 뒤쪽 줄은 아래 반원에 놓는다."""
    x, y, w, h = rect
    n = len(prep.rect_a)
    if n < 2:
        return _circle_single(0x2A, glyphs, prep, rect)
    gap = h / 80.0
    side = min(w, h)
    half_h = h * 0.5 - gap
    radius = (side - side / 40.0) * 0.5
    top = (n + 1) // 2
    c = (radius * CIRCLE_PI) / (prep.total_a.w / (prep.max_ink_h / n) + CIRCLE_PI)
    out = _place_arc(0x2A, glyphs, prep, 0, top, (x, y, w, half_h), 2 * radius, radius, c)
    if top != n:
        out += _place_arc(0x2B, glyphs, prep, top, -1, (x, h * 0.5 + gap + y, w, half_h), 2 * radius, radius, c)
    return out


def _circle_bands(kind: int, glyphs, prep: _CirclePrepared, rect):
    """FUN_10207540 (0x2e·0x2f): 위 호(ceil(n/3)줄) + 가운데 곧은 줄 + 아래 호(0x2e는 0x2b, 0x2f는 0x31)."""
    x, y, w, h = rect
    n = len(prep.rect_a)
    if n < 2:
        return _circle_single(0x2A, glyphs, prep, rect)
    g1 = (n + 2) // 3
    g2 = (n - g1) // 2
    g12 = g1 + g2

    def band_width(lo: int, hi: int) -> float:
        union = _Rect()
        for k in range(lo, hi):
            union.union(prep.rect_a[k])
        return union.w

    wt, wm, wb = band_width(0, g1), band_width(g1, g12), band_width(g12, n)
    side = min(w, h)
    e = side / 40.0
    hbar = prep.max_ink_h / n
    kt = wt / hbar + CIRCLE_PI
    kb = wb / hbar + CIRCLE_PI
    r1 = (side - e) * 0.5
    c1 = min(r1, min(2 * (r1 - e) / (wm / hbar + 2.0), min(r1 * CIRCLE_PI / kb, r1 * CIRCLE_PI / kt)))
    mid1 = c1 * wm / hbar
    yoff1 = r1 - c1 * 0.5
    c0 = c1 - 10.0
    inner = side - 2 * e
    mid0 = c0 * wm / hbar
    r0 = (inner - c0) * 0.5
    yoff0 = r0 + e
    limit = min(r0 * CIRCLE_PI / kb, r0 * CIRCLE_PI / kt)
    while c0 < limit:
        if r0 <= c0 or 2 * r0 <= mid0:
            break
        c0 += 2.0
        r0 = (inner - c0) * 0.5
        mid0 = c0 * wm / hbar
        yoff0 = r0 + e
        limit = min(r0 * CIRCLE_PI / kb, r0 * CIRCLE_PI / kt)
    if c1 < c0:
        r, c, mid, yoff = r0, c0, mid0, yoff0
    else:
        r, c, mid, yoff = r1, c1, mid1, yoff1
    sy = h / side
    d = 2 * r
    out = _place_arc(0x2A, glyphs, prep, 0, g1, (x, y, w, r * sy), d, r, c)
    if g2 > 0:
        middle = ((d - mid) * 0.5 * (w / d) + x, yoff * sy + y, mid * (w / d), c * sy)
        out += _place_straight(glyphs, prep, g1, g12, middle, mid, c)
    bottom = 0x2B if kind == 0x2E else 0x31
    out += _place_arc(bottom, glyphs, prep, g12, n, (x, (side - r) * sy + y, w, r * sy), d, r, c)
    return out


def _circle_commands(record: TextArt, fonts: Sequence) -> list[Command]:
    """슬롯 2 FUN_10207aa0: 0x2a~0x2d → FUN_10207190, 0x2e·0x2f → FUN_10207540, 0x30 → FUN_10207310."""
    glyphs = _circle_layout(record, fonts)
    if not glyphs:
        return []
    prep = _circle_prepare(glyphs)
    kind = _CIRCLE_KINDS[record.shape]
    rect = (0.0, 0.0, WARP_SIZE, WARP_SIZE)  # HwpAppModule 0x1040aa30
    if kind <= 0x2D:
        parts = _circle_single(kind, glyphs, prep, rect)
    elif kind == 0x30:
        parts = _circle_halves(glyphs, prep, rect)
    else:
        parts = _circle_bands(kind, glyphs, prep, rect)
    return [command for part in parts for command in part]


def _four_line_commands(record: TextArt, fonts: Sequence) -> list[Command]:
    lines = _layout(record, fonts)
    if len(lines) > 1:
        # FUN_10202170·FUN_10202350은 줄마다 외곽 높이를 줄 수로 나누고 가로를 줄 폭 비율로 줄인다. 대조 전이라 막는다.
        raise NotImplementedError("여러 줄 글맵시는 아직 그리지 못한다")
    sides = _warp_sides(envelope_points(record.shape, record.handles))
    step = max(LINE_STEP_MIN, math.hypot(WARP_SIZE, WARP_SIZE) / LINE_STEP_DIVISOR)
    return [command for line in lines for command in _warp_line(line, sides, step)]


def draw_textart(
    payload: bytes, width: float, height: float, find_font: Callable[[str], tuple[Path, int]]
) -> TextArtDrawing:
    """글맵시 레코드를 개체 상자(width x height, HWPUNIT) 안 SVG 경로로 그린다.

    find_font는 글꼴 이름 → (파일, TTC 번호)이고 없으면 TextArtFontError를 낸다. 레코드 글꼴이 없으면 DEFAULT_FACE로
    한 번 더 찾고, 그것도 없으면 TextArtFontError를 그대로 올린다.
    """
    record = parse_textart(payload)
    if record.font_type != 1:
        raise NotImplementedError(f"글맵시 글꼴 종류 {record.font_type}(HFT)는 아직 그리지 못한다")
    face = record.font_name
    try:
        fonts = [_load_font(*find_font(face))]
    except TextArtFontError:
        face, fonts = DEFAULT_FACE, []
    try:
        fonts.append(_load_font(*find_font(DEFAULT_FACE)))
    except TextArtFontError:
        if not fonts:
            raise
        # 레코드 글꼴에 모든 글자가 있으면 기본 글꼴은 쓰지 않는다. 글자가 모자라면 _layout이 TextArtFontError를 낸다.
    if record.shape in CIRCLE_SHAPES:
        commands = _circle_commands(record, fonts)
    else:
        commands = _four_line_commands(record, fonts)
    if not commands:
        return TextArtDrawing("", width, height, face=face)
    # 결과는 0~WARP_SIZE 정사각이고, 개체 도형 행렬이 원래 크기(같은 정사각)를 현재 크기로 늘린다.
    scale_x, scale_y = width / WARP_SIZE, height / WARP_SIZE
    if not record.shadow_type:
        return TextArtDrawing(_svg_path(commands, scale_x, scale_y), width, height, face=face)
    color = record.shadow_color
    return TextArtDrawing(
        path=_svg_path(_fit(commands, record.shadow_offset, shadow=False), scale_x, scale_y),
        width=width,
        height=height,
        shadow_path=_svg_path(_fit(commands, record.shadow_offset, shadow=True), scale_x, scale_y),
        shadow_color=f"#{color & 0xFF:02x}{color >> 8 & 0xFF:02x}{color >> 16 & 0xFF:02x}",
        face=face,
    )
