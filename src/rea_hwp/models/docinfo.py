"""DocInfo 레코드 모델(명세 4.2절). 명세와 실물이 다른 곳은 실물을 따르고, 선택 필드는 남은 바이트 수로 가른다."""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, ClassVar, NamedTuple

from .. import tags
from ..binio import Reader, Writer
from .base import FieldSpec, RecordModel
from .registry import register

LANGUAGES = ("hangul", "latin", "hanja", "japanese", "other", "symbol", "user")


@dataclass(frozen=True)
class _Part:
    """FieldSpec으로 적기 어려운 덩어리(구조체 배열, 길이가 앞에 오는 문자열 묶음)를 읽고 쓰는 함수 쌍."""

    name: str
    read: Callable[[Reader], Any]
    write: Callable[[Writer, Any], None]


Stage = list[FieldSpec] | _Part


@dataclass
class _StagedModel(RecordModel):
    """stages()가 내놓는 묶음을 차례로 읽고 쓴다. 앞 묶음에서 읽은 값으로 다음 묶음을 고른다.

    한 묶음이라도 payload가 모자라면 거기서 멈추고, 그 뒤 바이트는 tail로 남는다.
    """

    #: 조건에 따라 읽지 않을 수도 있는 이름. 읽지 않았으면 None이다.
    NAMES: ClassVar[tuple[str, ...]] = ()

    def stages(self) -> Iterator[Stage]:
        raise NotImplementedError

    def decode_fields(self, reader: Reader) -> None:
        self.values.update(dict.fromkeys(self.NAMES))
        for stage in self.stages():
            if not self._read_stage(reader, stage):
                return

    def _read_stage(self, reader: Reader, stage: Stage) -> bool:
        if not isinstance(stage, _Part):
            return self.read_spec(reader, stage)
        start = reader.offset
        try:
            self.values[stage.name] = stage.read(reader)
        except ValueError:
            reader.offset = start
            self.values[stage.name] = None
            return False
        return True

    def encode_fields(self, writer: Writer) -> None:
        for stage in self.stages():
            if isinstance(stage, _Part):
                value = self.values.get(stage.name)
                if value is None:
                    return
                stage.write(writer, value)
            elif not self.write_spec(writer, stage):
                return


def _struct_part(name: str, layout: struct.Struct, item: Callable[..., Any], count: int | None = None) -> _Part:
    """고정 크기 구조체 하나(count가 None) 또는 count개 배열."""

    def read(reader: Reader) -> Any:
        if count is None:
            return item(*layout.unpack(reader.bytes(layout.size)))
        chunk = reader.bytes(layout.size * count)
        return [item(*layout.unpack_from(chunk, index * layout.size)) for index in range(count)]

    def write(writer: Writer, value: Any) -> None:
        for one in [value] if count is None else value:
            writer.bytes(layout.pack(*one))

    return _Part(name, read, write)


def _read_chars(reader: Reader, count: int) -> str:
    return reader.bytes(count * 2).decode("utf-16-le", errors="surrogatepass")


def _bit(value: int | None, bit: int) -> bool:
    return bool(value is not None and value >> bit & 1)


def _bits(value: int | None, low: int, width: int) -> int:
    return 0 if value is None else value >> low & ((1 << width) - 1)


# --- 4.2.1 문서 속성 -------------------------------------------------------------------------------


PASSWORD_CHECK_SIZE = 256
DOCUMENT_PROPERTIES_SIZE = 26


@register(tags.DOCUMENT_PROPERTIES)
@dataclass
class DocumentProperties(RecordModel):
    FIELDS: ClassVar[list[FieldSpec]] = [
        ("section_count", "u16"),
        ("page_start", "u16"),
        ("footnote_start", "u16"),
        ("endnote_start", "u16"),
        ("picture_start", "u16"),
        ("table_start", "u16"),
        ("equation_start", "u16"),
        ("caret_list_id", "u32"),
        ("caret_para_id", "u32"),
        ("caret_char_pos", "u32"),
    ]
    #: 암호 문서가 속성 앞에 두는 암호 확인 자료(같은 문서의 암호 없는 판과 비교해 확인: 282 = 256 + 26바이트).
    password_check: bytes = b""

    def decode_fields(self, reader: Reader) -> None:
        if reader.remaining == PASSWORD_CHECK_SIZE + DOCUMENT_PROPERTIES_SIZE:
            self.password_check = reader.bytes(PASSWORD_CHECK_SIZE)
        self.read_spec(reader, self.FIELDS)

    def encode_fields(self, writer: Writer) -> None:
        writer.bytes(self.password_check)
        self.write_spec(writer, self.FIELDS)


# --- 4.2.2 아이디 매핑 헤더 -----------------------------------------------------------------------


@register(tags.ID_MAPPINGS)
@dataclass
class IdMappings(RecordModel):
    """명세 표16 순서의 개수 배열. 버전마다 길이가 달라 남은 바이트 수만큼 읽는다(실물 16개·18개)."""

    counts: list[int] = field(default_factory=list)

    def decode_fields(self, reader: Reader) -> None:
        self.counts = [reader.read("i32") for _ in range(reader.remaining // 4)]

    def encode_fields(self, writer: Writer) -> None:
        for count in self.counts:
            writer.write("i32", count)

    def _count(self, index: int) -> int:
        return self.counts[index] if index < len(self.counts) else 0

    @property
    def bin_data_count(self) -> int:
        return self._count(0)

    @property
    def face_name_counts(self) -> list[int]:
        """언어별(LANGUAGES 순서) 글꼴 수. FACE_NAME 레코드는 이 순서로 이어 붙어 있다."""
        return [self._count(1 + index) for index in range(7)]

    @property
    def border_fill_count(self) -> int:
        return self._count(8)

    @property
    def char_shape_count(self) -> int:
        return self._count(9)

    @property
    def tab_def_count(self) -> int:
        return self._count(10)

    @property
    def numbering_count(self) -> int:
        return self._count(11)

    @property
    def bullet_count(self) -> int:
        return self._count(12)

    @property
    def para_shape_count(self) -> int:
        return self._count(13)

    @property
    def style_count(self) -> int:
        return self._count(14)

    @property
    def memo_shape_count(self) -> int:
        return self._count(15)


# --- 4.2.3 바이너리 데이터 ------------------------------------------------------------------------

BIN_LINK = 0
BIN_EMBEDDING = 1
BIN_STORAGE = 2


@register(tags.BIN_DATA)
@dataclass
class BinData(_StagedModel):
    """LINK면 절대·상대 경로, EMBEDDING·STORAGE면 저장소 ID와 확장자(실물은 STORAGE에도 확장자가 있다)."""

    NAMES = ("abs_path", "rel_path", "bin_id", "extension")

    def stages(self) -> Iterator[Stage]:
        yield [("attr", "u16")]
        if self.kind in (BIN_EMBEDDING, BIN_STORAGE):
            yield [("bin_id", "u16"), ("extension", "wstr")]
        else:
            yield [("abs_path", "wstr"), ("rel_path", "wstr")]

    @property
    def kind(self) -> int:
        """BIN_LINK·BIN_EMBEDDING·BIN_STORAGE."""
        return _bits(self.values.get("attr"), 0, 4)

    @property
    def compression(self) -> int:
        """0 저장소 기본값, 1 압축, 2 압축 안 함."""
        return _bits(self.values.get("attr"), 4, 2)

    @property
    def status(self) -> int:
        """0 접근 전, 1 성공, 2 실패, 3 실패 무시."""
        return _bits(self.values.get("attr"), 8, 2)

    @property
    def storage_name(self) -> str | None:
        """BinData 저장소 안의 스트림 경로. LINK이거나 ID가 없으면 None."""
        if self.kind == BIN_LINK or self.values.get("bin_id") is None:
            return None
        extension = self.values.get("extension") or ""
        return f"BinData/BIN{self.bin_id:04X}.{extension}"


# --- 4.2.4 글꼴 -----------------------------------------------------------------------------------


@register(tags.FACE_NAME)
@dataclass
class FaceName(_StagedModel):
    NAMES = ("alt_type", "alt_name", "panose", "default_name")

    def stages(self) -> Iterator[Stage]:
        yield [("attr", "u8"), ("name", "wstr")]
        if _bit(self.attr, 7):
            yield [("alt_type", "u8"), ("alt_name", "wstr")]
        if _bit(self.attr, 6):
            yield [("panose", "bytes:10")]
        if _bit(self.attr, 5):
            yield [("default_name", "wstr")]

    @property
    def font_type(self) -> int:
        """0 알 수 없음, 1 TTF, 2 HFT."""
        return _bits(self.values.get("attr"), 0, 2)


# --- 4.2.5 테두리/배경 -----------------------------------------------------------------------------


class BorderLine(NamedTuple):
    #: 명세 표25 선 종류(대각선이면 표27 대각선 종류).
    line_type: int
    #: 명세 표26 굵기 인덱스(0 = 0.1mm … 15 = 5.0mm).
    width: int
    color: int


class SolidFill(NamedTuple):
    background_color: int
    pattern_color: int
    #: -1 무늬 없음, 0~5 명세 표29(값 1~6)에서 1을 뺀 번호.
    pattern_type: int


class ImageFill(NamedTuple):
    #: 명세 표31 이미지 채우기 유형.
    mode: int
    brightness: int
    contrast: int
    effect: int
    bin_id: int


@dataclass
class GradientFill:
    #: 1 줄무늬형, 2 원형, 3 원뿔형, 4 사각형.
    kind: int
    angle: int
    center_x: int
    center_y: int
    blur: int
    colors: list[int]
    #: 색이 바뀌는 위치. 색이 셋 이상일 때만 저장된다.
    positions: list[int] = field(default_factory=list)


_BORDER_LINE = struct.Struct("<BBI")
_SOLID_FILL = struct.Struct("<IIi")
_IMAGE_FILL = struct.Struct("<BbbBH")
_GRADIENT_HEAD = struct.Struct("<BiiiiI")

FILL_SOLID = 0x1
FILL_IMAGE = 0x2
FILL_GRADIENT = 0x4


def _read_gradient(reader: Reader) -> GradientFill:
    # 명세는 INT16 필드들이라고 적었지만 실물은 유형 1바이트 + 4바이트 필드들이다.
    kind, angle, center_x, center_y, blur, count = _GRADIENT_HEAD.unpack(reader.bytes(_GRADIENT_HEAD.size))
    positions = list(struct.unpack(f"<{count}i", reader.bytes(4 * count))) if count > 2 else []
    colors = list(struct.unpack(f"<{count}I", reader.bytes(4 * count)))
    return GradientFill(kind, angle, center_x, center_y, blur, colors, positions)


def _write_gradient(writer: Writer, fill: GradientFill) -> None:
    count = len(fill.colors)
    writer.bytes(_GRADIENT_HEAD.pack(fill.kind, fill.angle, fill.center_x, fill.center_y, fill.blur, count))
    if count > 2:
        writer.bytes(struct.pack(f"<{count}i", *fill.positions))
    writer.bytes(struct.pack(f"<{count}I", *fill.colors))


def _read_sized(reader: Reader) -> bytes:
    return reader.bytes(reader.read("u32"))


def _write_sized(writer: Writer, chunk: bytes) -> None:
    writer.write("u32", len(chunk))
    writer.bytes(chunk)


def _fill_stages(fill_type: int | None) -> Iterator[Stage]:
    """채우기 정보(명세 표28): 종류 비트 순서가 아니라 단색 → 그러데이션 → 이미지 순서로 온다."""
    if fill_type is None:
        return
    if fill_type & FILL_SOLID:
        yield _struct_part("solid", _SOLID_FILL, SolidFill)
    if fill_type & FILL_GRADIENT:
        yield _Part("gradient", _read_gradient, _write_gradient)
    if fill_type & FILL_IMAGE:
        yield _struct_part("image", _IMAGE_FILL, ImageFill)
    # 추가 속성: 그러데이션이면 번짐 중심 1바이트, 아니면 보통 0바이트다. 채우기 없음이어도 길이 u32는 있다.
    yield _Part("fill_extra", _read_sized, _write_sized)
    # 검증 안 됨: rhwp parse_fill — 켜진 채우기 종류마다 투명도 1바이트(단색·그러데이션·이미지 순). 구판에는 없다.
    alpha_count = (fill_type & 0x7).bit_count()
    if alpha_count:
        yield _Part(
            "fill_alphas",
            lambda reader: list(reader.bytes(alpha_count)),
            lambda writer, alphas: writer.bytes(bytes(alphas)),
        )


FILL_NAMES = ("solid", "gradient", "image", "fill_extra", "fill_alphas")


@register(tags.BORDER_FILL)
@dataclass
class BorderFill(_StagedModel):
    """명세는 4방향 종류·굵기·색을 배열로 적었지만 실물은 방향마다 [종류, 굵기, 색]이 번갈아 온다."""

    NAMES = ("borders", "diagonal", "fill_type", *FILL_NAMES)

    def stages(self) -> Iterator[Stage]:
        yield [("attr", "u16")]
        yield _struct_part("borders", _BORDER_LINE, BorderLine, 4)
        yield _struct_part("diagonal", _BORDER_LINE, BorderLine)
        yield [("fill_type", "u32")]
        yield from _fill_stages(self.fill_type)

    @property
    def left(self) -> BorderLine | None:
        return self.borders[0] if self.borders else None

    @property
    def right(self) -> BorderLine | None:
        return self.borders[1] if self.borders else None

    @property
    def top(self) -> BorderLine | None:
        return self.borders[2] if self.borders else None

    @property
    def bottom(self) -> BorderLine | None:
        return self.borders[3] if self.borders else None

    @property
    def effect_3d(self) -> bool:
        return _bit(self.values.get("attr"), 0)

    @property
    def effect_shadow(self) -> bool:
        return _bit(self.values.get("attr"), 1)

    @property
    def slash_shape(self) -> int:
        return _bits(self.values.get("attr"), 2, 3)

    @property
    def backslash_shape(self) -> int:
        return _bits(self.values.get("attr"), 5, 3)

    @property
    def has_center_line(self) -> bool:
        return _bit(self.values.get("attr"), 13)

    @property
    def background_color(self) -> int | None:
        """단색 채우기의 배경색. 단색 채우기가 아니면 None."""
        return self.solid.background_color if self.solid else None


# --- 4.2.6 글자 모양 ------------------------------------------------------------------------------


@register(tags.CHAR_SHAPE)
@dataclass
class CharShape(RecordModel):
    """언어별 배열은 LANGUAGES 순서다. 글자 테두리 ID(5.0.2.1)·취소선 색(5.0.3.0)은 있을 때만 읽는다."""

    FIELDS: ClassVar[list[FieldSpec]] = [
        ("face_ids", "u16*7"),
        ("ratios", "u8*7"),
        ("spacings", "i8*7"),
        ("rel_sizes", "u8*7"),
        ("offsets", "i8*7"),
        ("base_size", "i32"),
        ("attr", "u32"),
        ("shadow_gap_x", "i8"),
        ("shadow_gap_y", "i8"),
        ("text_color", "u32"),
        ("underline_color", "u32"),
        ("shade_color", "u32"),
        ("shadow_color", "u32"),
        ("border_fill_id", "u16"),
        ("strike_color", "u32"),
    ]

    @property
    def italic(self) -> bool:
        return _bit(self.attr, 0)

    @property
    def bold(self) -> bool:
        return _bit(self.attr, 1)

    @property
    def underline_type(self) -> int:
        """0 없음, 1 글자 아래, 3 글자 위(2는 명세에 없다)."""
        return _bits(self.attr, 2, 2)

    @property
    def underline_shape(self) -> int:
        """명세 표25 선 종류."""
        return _bits(self.attr, 4, 4)

    @property
    def outline_type(self) -> int:
        return _bits(self.attr, 8, 3)

    @property
    def shadow_type(self) -> int:
        """0 없음, 1 비연속, 2 연속."""
        return _bits(self.attr, 11, 2)

    @property
    def emboss(self) -> bool:
        return _bit(self.attr, 13)

    @property
    def engrave(self) -> bool:
        return _bit(self.attr, 14)

    @property
    def superscript(self) -> bool:
        return _bit(self.attr, 15)

    @property
    def subscript(self) -> bool:
        return _bit(self.attr, 16)

    @property
    def strike_shape(self) -> int:
        return _bits(self.attr, 26, 4)

    @property
    def strike(self) -> bool:
        # 한컴은 취소선이 없어도 bit 18~20을 채우고 모양에 선 종류 밖의 값(15 등)을 넣는다(rhwp 확인).
        return _bits(self.attr, 18, 3) != 0 and self.strike_shape <= 12

    @property
    def emphasis_mark(self) -> int:
        return _bits(self.attr, 21, 4)

    @property
    def use_font_space(self) -> bool:
        return _bit(self.attr, 25)

    @property
    def kerning(self) -> bool:
        return _bit(self.attr, 30)


# --- 4.2.7 탭 정의 --------------------------------------------------------------------------------


class Tab(NamedTuple):
    position: int
    #: 0 왼쪽, 1 오른쪽, 2 가운데, 3 소수점.
    kind: int
    #: 명세 표25 선 종류.
    fill_type: int
    reserved: int


_TAB = struct.Struct("<IBBH")


@register(tags.TAB_DEF)
@dataclass
class TabDef(_StagedModel):
    """개수는 명세(INT16, 4바이트)와 달리 u32다."""

    NAMES = ("count", "tabs")

    def stages(self) -> Iterator[Stage]:
        yield [("attr", "u32"), ("count", "u32")]
        if self.count is not None:
            yield _struct_part("tabs", _TAB, Tab, self.count)

    @property
    def auto_tab_left(self) -> bool:
        return _bit(self.attr, 0)

    @property
    def auto_tab_right(self) -> bool:
        return _bit(self.attr, 1)


# --- 4.2.8 문단 번호 / 4.2.9 글머리표 -------------------------------------------------------------


@dataclass
class NumberingLevel:
    """문단 머리 정보(명세 표39, 실물 12바이트)와 번호 형식 문자열."""

    attr: int
    width_adjust: int
    distance: int
    #: -1이면 지정 안 함.
    char_shape_id: int
    #: '^1.' 같은 형식. ^n은 수준 n의 번호 자리다.
    format: str

    @property
    def align(self) -> int:
        """0 왼쪽, 1 가운데, 2 오른쪽."""
        return _bits(self.attr, 0, 2)

    @property
    def auto_width(self) -> bool:
        return _bit(self.attr, 2)

    @property
    def auto_indent(self) -> bool:
        return _bit(self.attr, 3)

    @property
    def distance_is_value(self) -> bool:
        """False면 distance가 글자 크기에 대한 비율(%)이다."""
        return _bit(self.attr, 4)

    @property
    def number_format(self) -> int:
        """명세 표41 번호 모양(0 = 1,2,3 …)."""
        # 검증 안 됨: rhwp parse_numbering — 명세 표40에는 bit 5 이상이 없다.
        return _bits(self.attr, 5, 4)


_PARA_HEAD = struct.Struct("<Ihhi")


def _levels_part(name: str, count: int) -> _Part:
    def read(reader: Reader) -> list[NumberingLevel]:
        levels = []
        for _ in range(count):
            head = _PARA_HEAD.unpack(reader.bytes(_PARA_HEAD.size))
            levels.append(NumberingLevel(*head, reader.wstr()))
        return levels

    def write(writer: Writer, levels: list[NumberingLevel]) -> None:
        for level in levels:
            writer.bytes(_PARA_HEAD.pack(level.attr, level.width_adjust, level.distance, level.char_shape_id))
            writer.wstr(level.format)

    return _Part(name, read, write)


@register(tags.NUMBERING)
@dataclass
class Numbering(_StagedModel):
    """수준 1~7 → 시작 번호 → (5.0.2.5) 수준별 시작 번호 → (5.1) 수준 8~10과 그 시작 번호. 뒤 셋은 있을 때만 읽는다."""

    NAMES = ("levels", "start_number", "level_start_numbers", "extended_levels", "extended_start_numbers")

    def stages(self) -> Iterator[Stage]:
        yield _levels_part("levels", 7)
        yield [("start_number", "u16")]
        yield [("level_start_numbers", "u32*7")]
        yield _levels_part("extended_levels", 3)
        yield [("extended_start_numbers", "u32*3")]

    @property
    def all_levels(self) -> list[NumberingLevel]:
        return [*(self.levels or []), *(self.extended_levels or [])]


@register(tags.BULLET)
@dataclass
class Bullet(RecordModel):
    """명세 표42(20바이트)와 달리 실물은 14바이트(문자까지)·23바이트·25바이트(체크 문자까지)가 있다."""

    FIELDS: ClassVar[list[FieldSpec]] = [
        ("attr", "u32"),
        ("width_adjust", "i16"),
        ("distance", "i16"),
        ("char_shape_id", "i32"),
        ("char", "u16"),
        ("image_bullet_id", "i32"),
        # 검증 안 됨: 명세 표42 '대비, 밝기, 효과, ID' 4바이트 — 실물 23바이트에 맞추면 ID가 u16이다.
        ("image_contrast", "i8"),
        ("image_brightness", "i8"),
        ("image_effect", "u8"),
        ("image_id", "u16"),
        ("check_char", "u16"),
    ]

    @property
    def bullet_char(self) -> str:
        return chr(self.char) if self.char is not None else ""

    @property
    def align(self) -> int:
        return _bits(self.attr, 0, 2)


# --- 4.2.10 문단 모양 -----------------------------------------------------------------------------


@register(tags.PARA_SHAPE)
@dataclass
class ParaShape(RecordModel):
    """속성2(5.0.1.7)·속성3과 새 줄 간격(5.0.2.5)·개요 수준은 있을 때만 읽는다(실물 42·46·54·58바이트)."""

    FIELDS: ClassVar[list[FieldSpec]] = [
        ("attr1", "u32"),
        ("left_margin", "i32"),
        ("right_margin", "i32"),
        ("indent", "i32"),
        ("space_before", "i32"),
        ("space_after", "i32"),
        ("legacy_line_spacing", "i32"),
        ("tab_def_id", "u16"),
        ("numbering_id", "u16"),
        ("border_fill_id", "u16"),
        ("border_spacing", "i16*4"),
        ("attr2", "u32"),
        ("attr3", "u32"),
        ("new_line_spacing", "u32"),
        # 명세에 없는 끝 4바이트. rhwp(#2734)는 0~9로 1~10수준을 담는 개요 수준으로 읽는다.
        ("outline_level", "u32"),
    ]

    @property
    def alignment(self) -> int:
        """0 양쪽, 1 왼쪽, 2 오른쪽, 3 가운데, 4 배분, 5 나눔."""
        return _bits(self.attr1, 2, 3)

    @property
    def line_spacing_type(self) -> int:
        """0 글자에 따라(%), 1 고정 값, 2 여백만 지정, 3 최소. 속성3이 있으면 그 값을 쓴다."""
        if self.attr3 is not None:
            return _bits(self.attr3, 0, 5)
        return _bits(self.attr1, 0, 2)

    @property
    def line_spacing(self) -> int:
        return self.new_line_spacing if self.new_line_spacing is not None else self.legacy_line_spacing

    @property
    def break_latin_word(self) -> int:
        """0 단어, 1 하이픈, 2 글자."""
        return _bits(self.attr1, 5, 2)

    @property
    def break_non_latin_word(self) -> int:
        """0 어절, 1 글자."""
        return _bits(self.attr1, 7, 1)

    @property
    def widow_orphan(self) -> bool:
        return _bit(self.attr1, 16)

    @property
    def keep_with_next(self) -> bool:
        return _bit(self.attr1, 17)

    @property
    def keep_lines(self) -> bool:
        return _bit(self.attr1, 18)

    @property
    def page_break_before(self) -> bool:
        return _bit(self.attr1, 19)

    @property
    def vertical_align(self) -> int:
        """0 글꼴 기준, 1 위, 2 가운데, 3 아래."""
        return _bits(self.attr1, 20, 2)

    @property
    def head_type(self) -> int:
        """0 없음, 1 개요, 2 번호, 3 글머리표."""
        return _bits(self.attr1, 23, 2)

    @property
    def level(self) -> int:
        """문단 수준(0 = 1수준). 속성1의 3비트는 6에서 멈추므로 개요 수준 필드와 큰 쪽을 쓴다."""
        return max(_bits(self.attr1, 25, 3), min(self.outline_level or 0, 9))


# --- 4.2.11 스타일 --------------------------------------------------------------------------------


@register(tags.STYLE)
@dataclass
class Style(RecordModel):
    FIELDS: ClassVar[list[FieldSpec]] = [
        ("local_name", "wstr"),
        ("name", "wstr"),
        ("attr", "u8"),
        ("next_style_id", "u8"),
        ("lang_id", "i16"),
        ("para_shape_id", "u16"),
        ("char_shape_id", "u16"),
        # 명세에 없는 끝 2바이트(실물은 모두 있다). 검증 안 됨: rhwp parse_style은 버린다.
        ("unknown_0", "u16"),
    ]

    @property
    def kind(self) -> int:
        """0 문단 스타일, 1 글자 스타일."""
        return _bits(self.attr, 0, 3)


# --- 4.2.12 문서 임의의 데이터 --------------------------------------------------------------------

PIT_BSTR = 1
PIT_SET = 0x8000
PIT_ARRAY = 0x8001
PIT_BINDATA = 0x8002
#: 값 자리가 4바이트인 종류(NULL, I1~UI).
_PIT_FIXED = frozenset(range(10)) - {PIT_BSTR}


@dataclass
class ParamItem:
    item_id: int
    kind: int
    #: 정수, 문자열, ParamSet, ParamSet 목록 중 하나.
    value: Any


@dataclass
class ParamSet:
    set_id: int
    items: list[ParamItem]
    # 명세 표50은 개수(INT16) 뒤에 아무것도 없지만 실물은 2바이트가 더 있다.
    reserved: int = 0


def _read_param_set(reader: Reader) -> ParamSet:
    set_id = reader.read("u16")
    count = reader.read("i16")
    reserved = reader.read("u16")
    items = []
    for _ in range(count):
        item_id = reader.read("u16")
        kind = reader.read("u16")
        if kind in _PIT_FIXED:
            value: Any = reader.read("u32")
        elif kind == PIT_BSTR:
            value = reader.wstr()
        elif kind == PIT_SET:
            value = _read_param_set(reader)
        elif kind == PIT_ARRAY:
            # 검증 안 됨: 명세 표52 — 실물 표본이 없다.
            value = [_read_param_set(reader) for _ in range(reader.read("i16"))]
        elif kind == PIT_BINDATA:
            value = reader.read("u16")
        else:
            raise ValueError(f"unknown parameter item kind {kind:#x}")
        items.append(ParamItem(item_id, kind, value))
    return ParamSet(set_id, items, reserved)


def _write_param_set(writer: Writer, params: ParamSet) -> None:
    writer.write("u16", params.set_id)
    writer.write("i16", len(params.items))
    writer.write("u16", params.reserved)
    for item in params.items:
        writer.write("u16", item.item_id)
        writer.write("u16", item.kind)
        if item.kind in _PIT_FIXED:
            writer.write("u32", item.value)
        elif item.kind == PIT_BSTR:
            writer.wstr(item.value)
        elif item.kind == PIT_SET:
            _write_param_set(writer, item.value)
        elif item.kind == PIT_ARRAY:
            writer.write("i16", len(item.value))
            for one in item.value:
                _write_param_set(writer, one)
        else:
            writer.write("u16", item.value)


@register(tags.DOC_DATA)
@dataclass
class DocData(_StagedModel):
    """파라미터 셋 하나(명세 표50). 해석할 수 없는 종류가 나오면 전부 tail에 둔다."""

    NAMES = ("params",)

    def stages(self) -> Iterator[Stage]:
        yield _Part("params", _read_param_set, _write_param_set)


@register(tags.DISTRIBUTE_DOC_DATA)
@dataclass
class DistributeDocData(RecordModel):
    FIELDS: ClassVar[list[FieldSpec]] = [("data", "bytes:256")]


# --- 4.2.14 호환 문서 / 4.2.15 레이아웃 호환성 -----------------------------------------------------


@register(tags.COMPATIBLE_DOCUMENT)
@dataclass
class CompatibleDocument(RecordModel):
    """target_program: 0 한글 문서, 1 한글 2007 호환, 2 MS 워드 호환."""

    FIELDS: ClassVar[list[FieldSpec]] = [("target_program", "u32")]


@register(tags.LAYOUT_COMPATIBILITY)
@dataclass
class LayoutCompatibility(RecordModel):
    FIELDS: ClassVar[list[FieldSpec]] = [
        ("char_format", "u32"),
        ("para_format", "u32"),
        ("section_format", "u32"),
        ("object_format", "u32"),
        ("field_format", "u32"),
    ]


# --- 변경 추적·메모·금칙 문자(명세에 구조가 없다) -------------------------------------------------


@register(tags.TRACKCHANGE)
@dataclass
class TrackChangeInfo(RecordModel):
    """변경 추적 정보. 실물은 모두 1032바이트이고 앞 4바이트 뒤는 해석하지 않는다."""

    # 검증 안 됨: 실물 표본 — 앞 4바이트는 늘 0x38이다.
    FIELDS: ClassVar[list[FieldSpec]] = [("unknown_0", "u32")]


@register(tags.MEMO_SHAPE)
@dataclass
class MemoShape(RecordModel):
    # 검증 안 됨: 실물 22바이트 표본 2종과 HWPX memoPr 속성 순서로 추정.
    FIELDS: ClassVar[list[FieldSpec]] = [
        ("width", "u32"),
        ("line_type", "u8"),
        ("line_width", "u8"),
        ("line_color", "u32"),
        ("fill_color", "u32"),
        ("active_color", "u32"),
        ("unknown_0", "u32"),
    ]


def _read_forbidden(reader: Reader) -> tuple[str, str]:
    start_count = reader.read("u32")
    end_count = reader.read("u32")
    return _read_chars(reader, start_count), _read_chars(reader, end_count)


def _write_forbidden(writer: Writer, lists: tuple[str, str]) -> None:
    encoded = [text.encode("utf-16-le", errors="surrogatepass") for text in lists]
    for chunk in encoded:
        writer.write("u32", len(chunk) // 2)
    for chunk in encoded:
        writer.bytes(chunk)


@register(tags.FORBIDDEN_CHAR)
@dataclass
class ForbiddenChar(_StagedModel):
    """금칙 문자. 실물: 8바이트 + 두 목록의 글자 수(u32 둘) + 두 목록의 글자."""

    NAMES = ("lists",)

    def stages(self) -> Iterator[Stage]:
        # 검증 안 됨: 실물 표본 — 앞 8바이트는 늘 0이다.
        yield [("unknown_0", "u32"), ("unknown_1", "u32")]
        yield _Part("lists", _read_forbidden, _write_forbidden)

    @property
    def not_at_line_start(self) -> str:
        """줄 머리에 올 수 없는 글자(닫는 괄호·마침표 등)."""
        return self.lists[0] if self.lists else ""

    @property
    def not_at_line_end(self) -> str:
        """줄 끝에 올 수 없는 글자(여는 괄호·통화 기호 등)."""
        return self.lists[1] if self.lists else ""


@register(tags.TRACK_CHANGE)
@dataclass
class TrackChange(RecordModel):
    # 검증 안 됨: 실물 26바이트 표본 1건 — u32 뒤에 SYSTEMTIME(u16×8)으로 보이는 값이 있다.
    FIELDS: ClassVar[list[FieldSpec]] = [("unknown_0", "u32"), ("time", "u16*8")]


def _read_u32_string(reader: Reader) -> str:
    return _read_chars(reader, reader.read("u32"))


def _write_u32_string(writer: Writer, text: str) -> None:
    encoded = text.encode("utf-16-le", errors="surrogatepass")
    writer.write("u32", len(encoded) // 2)
    writer.bytes(encoded)


@register(tags.TRACK_CHANGE_AUTHOR)
@dataclass
class TrackChangeAuthor(_StagedModel):
    NAMES = ("name",)

    def stages(self) -> Iterator[Stage]:
        # 검증 안 됨: 실물 24바이트 표본 1건 — 이름 길이가 u16이 아니라 u32다.
        yield _Part("name", _read_u32_string, _write_u32_string)
        yield [("unknown_0", "u32"), ("unknown_1", "u32")]
