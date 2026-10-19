"""OLE 차트 개체(VtChart Contents, CLSID 5A721580)를 한글 2022 HncChartBase.dll이 그리는 대로 재현한다.
문서 좌표는 twips(y 위로 증가), 측정은 2048 em 글꼴(VtTxGdi2), 글자는 GDI+ DrawString 규칙으로 찍는다."""

from __future__ import annotations

import io
import math
import struct
from dataclasses import dataclass, field

import olefile

from .metrics import default_database

# ---------------------------------------------------------------------------------------------------------------
# 1. VtArchive 직렬화
# ---------------------------------------------------------------------------------------------------------------

#: ``Contents`` 스트림 머리 길이. 0x20의 32비트 값이 뒤따르는 아카이브 길이다.
_HEADER_SIZE = 0x24
#: VtApplication::s_bParentHWP. 한글 안의 차트라 여러 ReadFrom이 일부 필드를 건너뛴다.
_PARENT_HWP = True


class VtObject(dict):
    """VtArchive가 만든 개체. 클래스 이름은 ``cls``, 판(version)은 ``ver``에 둔다."""

    @property
    def cls(self) -> str:
        return self.get("_class", "")


class _Archive:
    """VtStream 읽기: long 4·short/int 2·float 4·double 8·uchar 1바이트, 문자열은 u16 길이 + NUL 포함 바이트."""

    def __init__(self, data: bytes, pos: int) -> None:
        self.data = data
        self.pos = pos
        self.types: dict[int, tuple[str, int]] = {}
        self.objects: dict[int, VtObject] = {}

    def take(self, size: int) -> bytes:
        chunk = self.data[self.pos : self.pos + size]
        if len(chunk) != size:
            raise ValueError("VtArchive: 스트림이 끝났다")
        self.pos += size
        return chunk

    def i16(self) -> int:
        return struct.unpack("<h", self.take(2))[0]

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def i32(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def f32(self) -> float:
        return struct.unpack("<f", self.take(4))[0]

    def f64(self) -> float:
        return struct.unpack("<d", self.take(8))[0]

    def u8(self) -> int:
        return self.take(1)[0]

    def name(self) -> str:
        size = self.u16()
        return self.take(size).rstrip(b"\0").decode("latin-1")

    def read_type(self) -> tuple[str, int]:
        """VtArchive::ReadType(0x10003680): 형식 번호, 처음 나오면 클래스 이름과 판."""
        index = self.i32()
        known = self.types.get(index)
        if known is not None:
            return known
        name = self.name()
        version = self.i16()
        self.types[index] = (name, version)
        return name, version

    def read_object(self) -> VtObject | None:
        """VtArchive::ReadObject(0x10003400): 개체 번호(-1이면 없음), 처음 나오면 형식과 본문."""
        number = self.i32()
        if number == -1:
            return None
        known = self.objects.get(number)
        if known is not None:
            return known
        name, version = self.read_type()
        obj = VtObject(_class=name, _ver=version)
        self.objects[number] = obj
        _read_as(self, obj, name, version)
        return obj

    def read_base(self, obj: VtObject | None = None) -> VtObject:
        """ReadAsBaseFrom: 형식 머리를 읽고 그 클래스 본문을 같은 개체에 채운다."""
        name, version = self.read_type()
        if obj is None:
            obj = VtObject(_class=name, _ver=version)
        _read_as(self, obj, name, version)
        return obj

    def member(self, cls: str) -> VtObject:
        """멤버 ReadAsBaseFrom: 좌표는 머리 없이(0x100bf8f0), 작은 클래스는 판만(0x1008c510), 나머지는 형식 머리."""
        obj = VtObject(_class=cls)
        if cls in ("VtRect", "VtLRect"):
            coor = "VtCoor" if cls == "VtRect" else "VtLCoor"
            obj["a"] = self.member(coor)
            obj["b"] = self.member(coor)
        elif cls in ("VtCoor", "VtLCoor", "VtCoor3", "VtVect3"):
            _read_as(self, obj, cls, 0)
        elif cls in _VERSION_ONLY:
            version = self.i16()
            obj["_ver"] = version
            _read_as(self, obj, cls, version)
        else:
            return self.read_base(obj)
        return obj

    def skip_type(self) -> None:
        self.read_type()


#: ReadAsBaseFrom이 판(short)만 읽는 클래스.
_VERSION_ONLY = frozenset(
    {
        "VtChartLocation",
        "VtBrush",
        "VtColor",
        "VtPen",
        "VtGradient",
        "VtShadow",
        "VtView3",
        "VtMarker",
        "VtPoly",
        "VtFrame",
        "VtTextLayout",
    }
)

_READERS: dict[str, object] = {}


def _reader(*names: str):
    def register(function):
        for name in names:
            _READERS[name] = function
        return function

    return register


def _read_as(archive: _Archive, obj: VtObject, name: str, version: int) -> None:
    reader = _READERS.get(name)
    if reader is None:
        raise ValueError(f"VtArchive: 모르는 클래스 {name}")
    reader(archive, obj, version, name)


@_reader("VtObject", "VtValue", "VtPict")
def _read_object(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtValue::ReadFrom 0x10016c10, VtPict::ReadFrom 0x100b9660: 기반 형식 머리만.
    if name != "VtObject":
        a.skip_type()


@_reader("VtCollection")
def _read_collection(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtCollection::ReadFrom 0x10008480
    o["count"] = a.i16()
    a.skip_type()


@_reader("VtMatrix", "VtDataGrid")
def _read_matrix(a: _Archive, o: VtObject, v: int, name: str) -> None:
    if name == "VtDataGrid":
        # VtDataGrid::ReadFrom 0x1000b410
        a.read_base(o)
        o["grid"] = [a.i16() for _ in range(4)]
        return
    # VtMatrix::ReadFrom 0x10012e30: 행·열 수 뒤 칸 개체.
    a.read_base(VtObject())
    o["rows"] = a.i16()
    o["cols"] = a.i16()
    o["cells"] = [a.read_object() for _ in range(o["rows"] * o["cols"])]


@_reader("VtString")
def _read_string(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtString::ReadFrom 0x100155b0: ANSI(949) 문자열이 길이보다 짧으면 NUL 다음 1바이트 뒤가 UTF-16이다.
    size = a.i16() if (v < 2 or _PARENT_HWP) else a.i32()
    text = ""
    if size:
        raw = a.take(size + 1)
        end = raw.index(0)
        if end == size:
            text = raw[:end].decode("cp949", "replace")
        else:
            text = raw[end + 2 :].decode("utf-16-le", "replace").split("\0")[0]
    o["s"] = text
    a.skip_type()
    a.skip_type()


@_reader("VtInteger")
def _read_integer(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtInteger ReadFrom(vtable 0x1014af08 칸 1, FUN_10010170)
    o["v"] = float(a.i32())
    a.skip_type()
    a.skip_type()


@_reader("VtDouble")
def _read_double(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtDouble::ReadFrom 0x1000f980
    o["v"] = a.f64()
    o["flag"] = a.i16()
    a.skip_type()
    a.skip_type()


@_reader("VtDateTime")
def _read_datetime(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtDateTime::ReadFrom 0x1000f080
    o["v"] = a.f64()
    if not (v < 2 or _PARENT_HWP):
        a.i32()
    a.skip_type()
    a.skip_type()


@_reader("VtBytes")
def _read_bytes(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtBytes::ReadFrom 0x10007c70
    o["b"] = a.take(a.i32())
    a.skip_type()
    a.skip_type()


@_reader("VtColor")
def _read_color(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtColor::ReadFrom 0x10089a10: RGB와 깃발 1바이트.
    o["rgb"] = (a.u8(), a.u8(), a.u8())
    o["flags"] = a.u8()


@_reader("VtCoor")
def _read_coor(a: _Archive, o: VtObject, v: int, name: str) -> None:
    o["xy"] = (a.f32(), a.f32())  # VtCoor::ReadFrom 0x100bf0b0


@_reader("VtLCoor")
def _read_lcoor(a: _Archive, o: VtObject, v: int, name: str) -> None:
    o["xy"] = (a.i32(), a.i32())  # VtLCoor::ReadFrom 0x100bf1f0


@_reader("VtCoor3", "VtVect3")
def _read_coor3(a: _Archive, o: VtObject, v: int, name: str) -> None:
    o["xyz"] = (a.f32(), a.f32(), a.f32())  # VtCoor3::ReadFrom 0x100bf750


@_reader("VtBrush")
def _read_brush(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtBrush::ReadFrom 0x1008a030
    o["style"] = a.i16()
    o["fg"] = a.member("VtColor")
    o["bg"] = a.member("VtColor")
    o["index"] = a.i16()


@_reader("VtPen")
def _read_pen(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtPen::ReadFrom 0x1008a880
    o["style"], o["join"], o["cap"] = a.i16(), a.i16(), a.i16()
    o["color"] = a.member("VtColor")
    o["width"] = a.f32()
    o["limit"] = a.f32()


@_reader("VtShadow")
def _read_shadow(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtShadow::ReadFrom 0x1008d040
    o["brush"] = a.member("VtBrush")
    o["offset"] = a.member("VtCoor")
    o["style"] = a.i16()


@_reader("VtFrame")
def _read_frame(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtFrame::ReadFrom 0x1008d830
    o["color"] = a.member("VtColor")
    o["color2"] = a.member("VtColor")
    o["width"] = a.f32()
    o["style"] = a.i16()


@_reader("VtGradient")
def _read_gradient(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtGradient::ReadFrom 0x1008e1c0
    o["from"] = a.member("VtColor")
    o["to"] = a.member("VtColor")
    o["style"] = a.i16()


@_reader("VtFill")
def _read_fill(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtFill::ReadFrom 0x1008e840
    o["brush"] = a.member("VtBrush")
    o["gradient"] = a.member("VtGradient")
    o["picture"] = a.read_object()
    o["style"] = a.i16()
    a.skip_type()


@_reader("VtPicture")
def _read_picture(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtPicture::ReadFrom 0x100b8d40
    o["type"], o["embedded"] = a.i16(), a.i16()
    o["source"] = a.read_object()
    a.skip_type()


@_reader("VtBackdrop")
def _read_backdrop(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtBackdrop::ReadFrom 0x1008ee00
    o["shadow"] = a.member("VtShadow")
    o["frame"] = a.member("VtFrame")
    o["fill"] = a.read_object()
    a.skip_type()


@_reader("VtTextLayout")
def _read_text_layout(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtTextLayout::ReadFrom 0x100d7b20: 줄바꿈(0x10)·가로 맞춤(4)·세로 맞춤(8)·방향(0xc).
    o["wrap"] = a.i16()
    o["h"] = a.i16()
    o["v"] = a.i16()
    o["orient"] = a.i16()


@_reader("VtFont")
def _read_font(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtFont::ReadFrom 0x1008c120
    o["name"] = a.read_object()
    o["size"] = a.f32()
    o["style"] = a.i16()
    o["effect"] = a.i16()
    o["color"] = a.member("VtColor")
    a.skip_type()


@_reader("VtTextBlock", "VtSeriesLabel")
def _read_text_block(a: _Archive, o: VtObject, v: int, name: str) -> None:
    if name == "VtSeriesLabel":
        a.read_base(o)  # VtSeriesLabel::ReadFrom 0x10078760
        return
    # VtTextBlock::ReadFrom 0x100d9730: rect 0xb0, valid 0xc8(캐시 유효), measured 0xd0(측정 크기 캐시).
    o["layout"] = a.member("VtTextLayout")
    o["autosize"] = a.i16()
    o["backdrop"] = a.read_object()
    o["font"] = a.read_object()
    o["valid_rect"] = a.i16()
    o["margin"] = a.i32()
    o["rect"] = a.member("VtLRect")
    o["valid"] = a.i16()
    o["text"] = a.read_object()
    o["measured"] = a.member("VtLCoor")
    o["inner"] = a.member("VtLRect")
    if v >= 2:
        o["text_length_type"] = a.i16()
    a.skip_type()


@_reader("VtChartLocation")
def _read_location(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtChartLocation::ReadFrom 0x10044430
    o["visible"] = a.i16()
    o["rect"] = a.member("VtRect")
    o["type"] = a.i16()


@_reader("VtChartSection")
def _read_section(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtChartSection::ReadFrom 0x10044140: 바깥 여백(0x34)·안 여백(0x36)·위치·배경.
    o["outer"] = a.i16()
    o["inner"] = a.i16()
    o["location"] = a.member("VtChartLocation")
    o["backdrop"] = a.read_object()
    a.skip_type()


@_reader("VtChartText", "VtChartTitle", "VtChartFootnote")
def _read_chart_text(a: _Archive, o: VtObject, v: int, name: str) -> None:
    if name != "VtChartText":
        a.read_base(o)  # FUN_1004f550
        return
    # VtChartText::ReadFrom 0x1004f110
    o["block"] = a.read_object()
    a.read_base(o)


@_reader("VtChartLegend")
def _read_legend(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtChartLegend::ReadFrom 0x10050f90
    o["font"] = a.read_object()
    o["text_layout"] = a.member("VtTextLayout")
    a.read_base(o)


@_reader("VtArray")
def _read_array(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtArray::ReadFrom 0x100048e0
    count = a.i16()
    if not (v < 2 or _PARENT_HWP):
        a.i16()
    a.read_base(VtObject())
    o["items"] = [a.read_object() for _ in range(count)]


@_reader("VtList")
def _read_list(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtList::ReadFrom 0x10010e00
    head = a.read_base(VtObject())
    count = head["count"] if (v < 2 or _PARENT_HWP) else a.i16()
    o["items"] = [a.read_object() for _ in range(count)]


@_reader("VtBtree")
def _read_btree(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtBtree::ReadFrom 0x10006a50
    head = a.read_base(VtObject())
    o["items"] = [a.read_object() for _ in range(head["count"])]


@_reader("VtLight3")
def _read_light(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtLight3::ReadFrom 0x100a98a0
    o["sources"] = a.read_object()
    o["ambient"], o["edge"] = a.f32(), a.f32()
    o["edge_visible"] = a.i16()
    a.skip_type()


@_reader("VtInfLight3")
def _read_inf_light(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtInfLight3::ReadFrom 0x100a8b20
    o["direction"] = a.member("VtVect3")
    o["intensity"] = a.f32()
    a.skip_type()


@_reader("VtView3")
def _read_view(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtView3::ReadFrom 0x100a7490
    o["projection"] = a.i16()
    o["rotation"] = a.f32()
    o["elevation"] = a.f32()
    o["center"] = a.member("VtCoor3")
    o["zoom"] = a.f32()


@_reader("VtMarker")
def _read_marker(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtMarker::ReadFrom 0x100b8800
    o["pen"] = a.member("VtPen")
    o["size"] = a.f32()
    o["visible"] = a.i16()
    if v >= 2:
        o["fill"] = a.member("VtColor")
        o["picture"] = a.read_object()
    o["style"] = a.i16()


@_reader("VtDigitSet")
def _read_digit_set(a: _Archive, o: VtObject, v: int, name: str) -> None:
    a.skip_type()  # VtDigitSet::ReadFrom 0x100af8e0
    o["digits"] = (a.i16(), a.i16(), a.i16())


@_reader("VtFormatter")
def _read_formatter(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtFormatter::ReadFrom 0x100b5e60
    a.skip_type()
    o["fields"] = [a.i16() for _ in range(8)]
    if v >= 3:
        o["extra"] = a.i16()
    o["value"] = a.f64()
    o["color"] = a.member("VtColor")
    o["text"] = a.read_object()
    o["digits"] = [a.read_object() for _ in range(3)]


@_reader("VtFormatGroup")
def _read_format_group(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtFormatGroup::ReadFrom 0x100b69f0
    a.skip_type()
    o["count"] = a.i16()
    o["formatters"] = [a.read_object() for _ in range(4)]


@_reader("VtTextFormat")
def _read_text_format(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtTextFormat::ReadFrom 0x100af330
    a.skip_type()
    o["kind"] = a.i16()
    if v < 2 or _PARENT_HWP:
        o["format"] = a.read_object()
    else:
        o["group"] = a.read_object()


@_reader("VtValueBlock")
def _read_value_block(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtValueBlock::ReadFrom 0x100db2e0
    o["value"] = a.read_object()
    o["format"] = a.read_object()
    o["format_kind"] = a.i16()
    o["string"] = a.read_object()
    o["flag"] = a.u8()
    o["flag2"] = a.i16()
    a.read_base(o)


@_reader("VtAxisScaleBlock")
def _read_scale_block(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtAxisScaleBlock::ReadFrom 0x10026140
    o["array"] = a.read_object()
    o["s1a4"] = a.i16()
    o["s188"] = a.i16()
    a.read_base(o)


@_reader("VtCLineItem")
def _read_cline(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtCLineItem::ReadFrom 0x100850d0
    o["value"] = a.f64()
    o["brush"] = a.member("VtBrush")
    o["pen"] = a.member("VtPen")
    o["label"] = a.read_object()
    a.skip_type()


@_reader("VtAxis")
def _read_axis(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtAxis::ReadFrom 0x1001c350: 한글 안에서는 판 5 이상 필드 일부를 읽지 않는다.
    outside_hwp = not _PARENT_HWP
    o["s3c"], o["s40"] = a.i16(), a.i16()
    if v > 5 and outside_hwp:
        o["bytes"] = a.read_object()
    o["s90"], o["manual_scale"] = a.i16(), a.i16()
    o["pen"], o["major_pen"], o["minor_pen"] = a.member("VtPen"), a.member("VtPen"), a.member("VtPen")
    o["tick_length"], o["minor_tick_length"] = a.f32(), a.f32()
    o["title"] = a.read_object()
    o["blocks"] = a.read_object()
    o["s2ac"], o["f2b4"], o["s2b8"], o["f2bc"], o["sa8"], o["sa4"] = (
        a.i16(),
        a.f32(),
        a.i16(),
        a.f32(),
        a.i16(),
        a.i16(),
    )
    if not (v < 5 or not outside_hwp):
        o["sac"], o["sb0"] = a.i16(), a.i16()
    o["sb8"], o["s2c0"], o["sc4"], o["fc8"], o["sd0"] = a.i16(), a.i16(), a.i16(), a.f64(), a.i16()
    if v > 2:
        o["sbc"] = a.i16()
    if not (v < 4 or not outside_hwp):
        o["f400"] = a.f32()
    if o["manual_scale"] != 0:
        o["minimum"], o["maximum"] = a.f64(), a.f64()
        if v < 2:
            a.f64()
        o["divisions"] = (a.i16(), a.i16(), a.i16(), a.i16())
    o["scale_type"], o["percent_basis"] = a.i16(), a.i16()
    o["tick_style"], o["s2b0"], o["s98"], o["s94"] = a.i16(), a.i16(), a.i16(), a.i16()
    if not (v < 5 or not outside_hwp):
        o["s9c"], o["sa0"] = a.i16(), a.i16()
    o["sb4"] = a.i16()
    if v > 1:
        o["s3d4"] = a.i16()
    a.skip_type()


@_reader("VtSurfaceDesc")
def _read_surface(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtSurfaceDesc::ReadFrom 0x10082b80
    o["brush"], o["pen"] = a.member("VtBrush"), a.member("VtPen")
    o["u"] = (a.u16(), a.u16(), a.i16())
    o["array"] = a.read_object()
    o["axis"] = a.read_object()
    o["s9c"] = a.i16()
    o["line1"], o["line2"] = a.read_object(), a.read_object()
    o["s"] = [a.i16() for _ in range(7)]
    a.skip_type()


@_reader("VtSeriesPoint")
def _read_series_point(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtSeriesPoint ReadFrom(vtable 0x10153790 칸 1, FUN_10078d50)
    o["label"] = a.read_object()
    o["offset"] = a.f32()
    o["position"] = a.member("VtLCoor")
    o["o210"], o["o214"] = a.read_object(), a.read_object()
    a.skip_type()


@_reader("VtSeries")
def _read_series(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtSeries::ReadFrom 0x100813d0
    outside_hwp = not _PARENT_HWP
    o["pen"], o["pen2"], o["brush"] = a.member("VtPen"), a.member("VtPen"), a.member("VtBrush")
    o["picture"] = a.read_object()
    o["points"] = a.read_object()
    o["s148"] = [a.i16() for _ in range(4)]
    o["pen3"] = a.member("VtPen")
    o["s150"] = [a.i16() for _ in range(8)]
    o["f1ac"] = a.f32()
    o["c1b0"], o["c1bc"] = a.member("VtLCoor"), a.member("VtLCoor")
    if v > 3 and outside_hwp:
        o["pen4"] = a.member("VtPen")
    if not (v < 5 or not outside_hwp):
        o["sdac"] = a.i16()
    if v >= 2:
        o["name"] = a.read_object()
    o["label"] = a.read_object()
    o["legend_block"] = a.read_object()
    o["s6a8"] = a.i16()
    o["format1"], o["format2"] = a.read_object(), a.read_object()
    if not (v < 3 or not outside_hwp):
        o["label4e0"] = a.read_object()
    o["sd18"] = (a.i16(), a.i16())
    o["marker"] = a.member("VtMarker")
    o["sd70"] = (a.i16(), a.i16())
    o["fd78"] = a.f32()
    o["color"], o["color2"] = a.member("VtColor"), a.member("VtColor")
    o["sd98"] = a.i16()
    o["methods"] = [a.i16() for _ in range(a.i16())]
    o["arr158"] = [a.i16() for _ in range(a.i16())]
    o["tail"] = [a.i16() for _ in range(5)]
    a.skip_type()


@_reader("VtChartPlot")
def _read_plot(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtChartPlot::ReadFrom 0x10067f90
    o["array120"] = a.read_object()
    o["f13c"] = [a.f32() for _ in range(6)]
    o["wall_brush"], o["wall_pen"] = a.member("VtBrush"), a.member("VtPen")
    o["f190"] = a.f32()
    o["base_brush"], o["base_pen"] = a.member("VtBrush"), a.member("VtPen")
    o["view"] = a.member("VtView3")
    o["light"] = a.read_object()
    o["x_axis"] = a.read_object()
    if v < 5 or _PARENT_HWP:
        o["y_axes"] = [a.read_object()]
        o["y2_axes"] = [a.read_object()]
    else:
        o["y_axes"] = a.read_object()["items"]
        o["y2_axes"] = a.read_object()["items"]
    o["z_axis"] = a.read_object()
    o["s268"] = a.i16()
    o["f26c"] = [a.f32() for _ in range(5)]
    o["u290"] = a.u16()
    o["f29c"] = a.f32()
    o["s2a8"] = a.i16()
    if v > 1:
        o["surface"] = a.read_object()
    if v >= 3:
        o["s264"] = a.i16()
    if v >= 4:
        o["s274"] = a.i16()
    o["s1d0"] = [a.i16() for _ in range(6)]
    a.read_base(o)


@_reader("VtWindow")
def _read_window(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtWindow::ReadFrom 0x100e3130
    a.skip_type()
    if v != 1:
        o["sd8"] = a.i16()


@_reader("VtChart")
def _read_chart(a: _Archive, o: VtObject, v: int, name: str) -> None:
    # VtChart::ReadFrom 0x10031f80: 0x1e0은 각주(VtChartFootnote), 0x230은 제목(VtChartTitle).
    o["grid"] = a.read_object()
    if v >= 6:
        o["s264"] = a.i16()
    o["rect"] = a.member("VtRect")
    o["backdrop"] = a.read_object()
    o["footnote"] = a.read_object()
    o["legend"] = a.read_object()
    o["plot"] = a.read_object()
    o["series"] = a.read_object()
    o["title"] = a.read_object()
    o["list"] = a.read_object()
    o["s268"] = a.i16()
    o["rotation"] = a.f32()
    o["o440"] = a.read_object()
    if v > 3:
        o["flags"], o["s3fc"], o["s400"] = a.i16(), a.i16(), a.i16()
    if v > 4:
        o["s3f4"], o["s3f8"] = a.i16(), a.i16()
    o["type"] = a.i16()
    if v >= 2:
        o["s404"] = a.i16()
    if v >= 3:
        o["s408"] = a.i16()
    o["window"] = a.read_base()


#: VtChart OLE 서버 CLSID. 새 차트(CLSID 4C3DA137…, OOXMLChartContents)는 한글이 HncDrawingEngine으로 그린다.
_VTCHART_CLSID = "5A721580-5AF0-11CE-8384-0020AF2337F2"
#: BinData 앞 4바이트는 뒤따르는 복합 파일 길이다(ole.py와 같다).
_LENGTH_PREFIX = 4


def vtchart_contents(bin_data: bytes) -> bytes | None:
    """OLE BinData가 VtChart 차트면 Contents 스트림, 아니면 None."""
    body = io.BytesIO(bin_data[_LENGTH_PREFIX:])
    if not olefile.isOleFile(body):
        return None
    with olefile.OleFileIO(body) as storage:
        if storage.root.clsid != _VTCHART_CLSID or not storage.exists("Contents"):
            return None
        return storage.openstream("Contents").read()


def parse_vtchart(contents: bytes) -> VtObject:
    """``Contents`` 스트림을 VtChart 개체 나무로 읽는다. 형식이 맞지 않으면 ValueError."""
    archive = _Archive(contents, _HEADER_SIZE)
    chart = archive.read_object()
    if chart is None or chart.cls != "VtChart":
        raise ValueError("VtChart가 아니다")
    return chart


# ---------------------------------------------------------------------------------------------------------------
# 2. 글자 측정
# ---------------------------------------------------------------------------------------------------------------

#: 측정용 글꼴 높이(VtFont::GetLogFont 0x1008b5d0 lfHeight -0x800). 폭·지표는 이 em의 정수 값이다.
_REFERENCE_EM = 2048
#: 장치 해상도. 한글 PDF는 600DPI 장치 점으로 찍는다(bench/accuracy.py DEVICE_PT).
_DEVICE_DPI = 600
_TWIPS_PER_INCH = 1440

#: (em, OS/2 sTypoAscender, -hhea descender, usWinAscent, usWinDescent, hhea lineGap). 출처: 각 TrueType 파일.
_VERTICAL_METRICS: dict[str, tuple[int, int, int, int, int, int]] = {
    "HCR Dotum": (1000, 830, 230, 1070, 230, 0),
    "HCR Batang": (1000, 830, 230, 1070, 230, 0),
    "Gulim": (1024, 879, 145, 879, 145, 152),
    "GulimChe": (1024, 879, 145, 879, 145, 152),
    "Dotum": (1024, 879, 145, 879, 145, 152),
    "DotumChe": (1024, 879, 145, 879, 145, 152),
    "Batang": (1024, 879, 145, 879, 145, 152),
    "BatangChe": (1024, 879, 145, 879, 145, 152),
    "Gungsuh": (1024, 879, 145, 879, 145, 152),
    "GungsuhChe": (1024, 879, 145, 879, 145, 152),
    "Malgun Gothic": (2048, 1638, 495, 2229, 495, 0),
    "한컴 고딕": (1000, 750, 300, 1000, 300, 0),
    "Arial": (2048, 1491, 434, 1854, 434, 67),
    "Times New Roman": (2048, 1420, 443, 1825, 443, 87),
    "Tahoma": (2048, 1566, 423, 2049, 423, 0),
    "Verdana": (2048, 1566, 430, 2059, 430, 0),
}


@dataclass(frozen=True)
class FontFace:
    """글꼴 이름과 크기(pt). 폭은 저장소 글꼴 폭 표, 세로 지표는 _VERTICAL_METRICS에서 찾는다."""

    name: str
    size: float

    @property
    def metric(self):
        found = default_database().find(self.name)
        if found is None:
            raise UnsupportedChart(f"차트 글꼴 폭 표가 없다: {self.name}")
        return found

    @property
    def vertical(self) -> tuple[int, int, int, int, int, int]:
        vertical = _VERTICAL_METRICS.get(self.metric.name)
        if vertical is None:
            raise UnsupportedChart(f"차트 글꼴 세로 지표가 없다: {self.name}")
        return vertical

    def design_advance(self, char: str) -> int:
        width = self.metric.width(ord(char))
        if width is None:
            raise UnsupportedChart(f"{self.name}에 {char!r} 폭이 없다")
        return width


def _scaled_up(value: int, em: int) -> int:
    """2048 em 값을 올림한 정수(GDI). 확인: 저장된 15pt 캐시 '100' 495tw·'열1' 456tw·높이 387tw와 같다."""
    return -((-value * _REFERENCE_EM) // em)


def text_length(text: str, face: FontFace) -> int:
    """VtTxGdi2 GetTextLength(FUN_10095890): 2048 em 정수 폭 합 × (크기·20/2048)을 버림한 twips."""
    em = face.metric.em
    total = sum(_scaled_up(face.design_advance(char), em) for char in text)
    return int(total * (face.size * 20 / _REFERENCE_EM))


def text_metrics(face: FontFace) -> tuple[int, int, int]:
    """VtTxGdi2 글꼴 지표(FUN_10095250·FUN_10095590): (otmAscent, -otmMacDescent, otmMacLineGap) twips."""
    em, typo_ascent, descent, _, _, line_gap = face.vertical
    scale = face.size * 20 / _REFERENCE_EM
    return (
        int(_scaled_up(typo_ascent, em) * scale),
        int(_scaled_up(descent, em) * scale),
        int(_scaled_up(line_gap, em) * scale),
    )


def block_size(text: str, face: FontFace) -> tuple[int, int]:
    """VtTextBlock::Layout(0x100d8ae0) 한 줄 글상자 크기: 폭, 높이 = 위 지표 + (줄 수+1)×아래 지표."""
    ascent, descent, _ = text_metrics(face)
    return text_length(text, face), ascent + 2 * descent


# ---------------------------------------------------------------------------------------------------------------
# 3. 결과
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class ChartGlyph:
    """차트가 찍는 글자 하나. x·y는 쪽 좌표 pt(기준선 원점), angle은 반시계 방향 도."""

    char: str
    x: float
    y: float
    size: float
    font: str
    angle: float = 0.0


@dataclass
class ChartShape:
    """선(kind='line', points 두 점)이나 채운 사각형(kind='rect', points 왼쪽 위·오른쪽 아래). 쪽 좌표 pt."""

    kind: str
    points: tuple[tuple[float, float], ...]
    color: tuple[int, int, int]


@dataclass
class ChartDrawing:
    glyphs: list[ChartGlyph] = field(default_factory=list)
    shapes: list[ChartShape] = field(default_factory=list)


# ---------------------------------------------------------------------------------------------------------------
# 4. 배치
# ---------------------------------------------------------------------------------------------------------------

#: 안 여백 150·바깥 여백 160(VtChartSection 생성자 0x100427c0). 확대 배율(VtChart::SetZoom 0x1002e3f0)은 1로 본다.
_SECTION_INNER, _SECTION_OUTER = 150, 160
#: VtChartSection::GetMinimumInteriorMargin(0x100447f0).
_MIN_INTERIOR_MARGIN = 40
#: VtChartSection::Layout(0x10043010)이 크기를 정할 때 오른쪽·위 여백에 더하는 값(0x10157474).
_LAYOUT_PAD = 50.0
#: 축 글자와 축 사이 간격(FUN_10029820·FUN_1001f230의 DAT_101574a4).
_AXIS_LABEL_GAP = 100.0
#: 항목 축 글자 간격 판정에 더하는 값(FUN_1001f230의 DAT_10157480, 글상자 배경이 없을 때).
_CATEGORY_LABEL_SPACING = 60.0
#: 축 눈금 맞춤 표(FUN_100262c0, 0x1014a140·0x1014a158).
_NICE_THRESHOLDS = (1.414214, 3.162278, 7.071068)
_NICE_STEPS = (1.0, 2.0, 5.0, 10.0)
#: 2차원 막대(VtChChartType 1, 계열 painter VtBar2dMethod 1 — FUN_10042070)만 옮겼다.
_BAR_2D = 1


@dataclass
class _Rect:
    """문서 좌표 사각형(twips, y는 위로 증가)."""

    x0: float
    y0: float
    x1: float
    y1: float

    def inset(self, left: float, bottom: float, right: float, top: float) -> _Rect:
        return _Rect(self.x0 + left, self.y0 + bottom, self.x1 - right, self.y1 - top)


@dataclass
class _Text:
    """VtTextBlock 하나를 그릴 자료: 안쪽 사각형(정수), 글상자 높이, 맞춤, 글꼴."""

    text: str
    rect: tuple[int, int, int, int]
    block_height: int
    face: FontFace
    h_align: int
    v_align: int


def _frame_metric(backdrop: VtObject | None) -> int:
    """VtBackdrop::GetMetrics(0x1008ebb0): 틀(VtFrame 0x1008d3c0) 두께를 장치 해상도로. 투영 드라이버 해상도는 1440."""
    if backdrop is None:
        return 0
    frame = backdrop["frame"]
    if frame["style"] == 0:
        return 0
    return (int(abs(frame["width"])) * _TWIPS_PER_INCH + 720) // _TWIPS_PER_INCH


def _backdrop_is_null(backdrop: VtObject | None) -> bool:
    """VtBackdrop::IsNull(0x1001fd20): 그림자·틀·채우기 모양이 모두 0."""
    if backdrop is None:
        return True
    fill = backdrop["fill"]
    return backdrop["shadow"]["style"] == 0 and backdrop["frame"]["style"] == 0 and (fill is None or fill["style"] == 0)


def _section_margins(section: VtObject, inner: int, minimum: int) -> tuple[float, float, float, float]:
    """GetInteriorRect(0x10042980) 여백(왼, 아래, 오른, 위). 배경이 없으면 GetMinimumInteriorMargin."""
    backdrop = section["backdrop"]
    if _backdrop_is_null(backdrop):
        return (minimum, minimum, minimum, minimum)
    metric = _frame_metric(backdrop)
    return (metric + inner, metric + inner, metric + inner, metric + inner)


def _nice_scale(low: float, high: float, divisions: int) -> tuple[float, float, int, float]:
    """FUN_100262c0: 나눔 수를 줄여 가며 1·2·5·10 배수 간격을 고른다. (최소, 최대, 나눔 수, 간격)."""
    if high <= low or divisions < 1:
        return low, high, divisions, 1.0
    step = 1.0
    new_low, new_high = low, high
    count = float(divisions)
    while count > 0:
        raw = (high - low) / count
        if raw < 1e-31:
            raw = 0.0
        exponent = int(math.log10(raw)) if raw > 0 else 0
        if raw < 1.0:
            exponent -= 1
        base = 10.0**exponent
        index = 0
        while index < 3 and raw / base >= _NICE_THRESHOLDS[index]:
            index += 1
        step = _NICE_STEPS[index] * base
        quotient = low / step
        start = int(quotient) - (1 if quotient < 0 else 0)
        if abs((start + 1.0) - quotient) < 2e-05:
            start += 1
        quotient = high / step
        end = int(quotient + 1.0) - (1 if quotient < -1.0 else 0)
        if abs((quotient + 1.0) - end) < 2e-05:
            end -= 1
        new_low = min(low, start * step)
        new_high = max(high, end * step)
        if (new_high - new_low) / step <= divisions:
            break
        count -= 1.0
    if count <= 1.0:
        return new_low, new_high, 1, new_high - new_low
    return new_low, new_high, int((new_high - new_low) / step + 2e-05), step


def _format_value(value: float) -> str:
    """값 축 기본 표시 형식(VtFormatter 일반 형식). 정수면 소수점 없이."""
    rounded = round(value, 10)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:g}"


class _Layout:
    """VtChart::Layout(0x10030a20)과 구역별 Layout을 문서 twips로 옮긴다."""

    def __init__(self, chart: VtObject, device: tuple[int, int, int, int]) -> None:
        self.chart = chart
        left, top, right, bottom = device
        scale = _TWIPS_PER_INCH / _DEVICE_DPI
        # FUN_100ef820·VtChart::Draw(0x1002ecf0): 장치 사각형을 twips 문서 사각형으로 바꾼다.
        doc_left = left * _TWIPS_PER_INCH // _DEVICE_DPI
        doc_top = top * _TWIPS_PER_INCH // _DEVICE_DPI
        self.width = (right * _TWIPS_PER_INCH - _TWIPS_PER_INCH) // _DEVICE_DPI - doc_left
        self.height = (bottom * _TWIPS_PER_INCH - _TWIPS_PER_INCH) // _DEVICE_DPI - doc_top
        # FUN_100a8360: 장치 = 원점 + (x, 위 − y) × 600/1440, 원점은 문서 시작의 장치 좌표 + 0.5.
        self.origin_x = doc_left / scale + 0.5
        self.origin_y = doc_top / scale + 0.5
        self.texts: list[_Text] = []
        self.shapes: list[tuple[str, tuple[tuple[float, float], ...], tuple[int, int, int]]] = []

    def device(self, x: float, y: float) -> tuple[float, float]:
        factor = _DEVICE_DPI / _TWIPS_PER_INCH
        return self.origin_x + x * factor, self.origin_y + (self.height - y) * factor

    # -- 구역 ------------------------------------------------------------------------------------------------

    def place_section(self, section: VtObject, avail: _Rect, size: tuple[float, float], minimum: int) -> _Rect:
        """VtChartSection::Layout(0x10043010): 위치 종류별로 구역을 놓고 남은 사각형을 줄인다(avail을 바꾼다)."""
        outer, inner = _SECTION_OUTER, _SECTION_INNER
        bounds = avail.inset(outer, outer, outer, outer)
        left, bottom, right, top = _section_margins(section, inner, minimum)
        right += _LAYOUT_PAD
        top += _LAYOUT_PAD
        width = left + size[0] + right
        height = bottom + size[1] + top
        kind = section["location"]["type"]
        if kind == 0:
            x0 = avail.x0 + outer
            y1 = avail.y1 - outer
            rect = _Rect(x0, y1 - height, x0 + width, y1)
            avail.y1 = rect.y0
        elif kind == 1:
            x0 = ((avail.x1 + avail.x0) - width) * 0.5
            y1 = avail.y1 - outer
            rect = _Rect(x0, y1 - height, x0 + width, y1)
            avail.y1 = rect.y0
        elif kind == 2:
            x1 = avail.x1 - outer
            y1 = avail.y1 - outer
            rect = _Rect(x1 - width, y1 - height, x1, y1)
            avail.y1 = rect.y0
        elif kind == 3:
            x0 = avail.x0 + outer
            y0 = ((avail.y1 + avail.y0) - height) * 0.5
            rect = _Rect(x0, y0, x0 + width, y0 + height)
            avail.x0 = rect.x1
        elif kind == 4:
            x1 = avail.x1 - outer
            y0 = ((avail.y1 + avail.y0) - height) * 0.5
            rect = _Rect(x1 - width, y0, x1, y0 + height)
            avail.x1 = rect.x0
        elif kind == 5:
            x0 = avail.x0 + outer
            y0 = avail.y0 + outer
            rect = _Rect(x0, y0, x0 + width, y0 + height)
            avail.y0 = rect.y1
        elif kind == 6:
            x0 = ((avail.x1 + avail.x0) - width) * 0.5
            y0 = avail.y0 + outer
            rect = _Rect(x0, y0, x0 + width, y0 + height)
            avail.y0 = rect.y1
        elif kind == 7:
            x1 = avail.x1 - outer
            y0 = avail.y0 + outer
            rect = _Rect(x1 - width, y0, x1, y0 + height)
            avail.y0 = rect.y1
        else:
            raise UnsupportedChart(f"차트 구역 위치 종류 {kind}")
        return _Rect(max(rect.x0, bounds.x0), max(rect.y0, bounds.y0), min(rect.x1, bounds.x1), min(rect.y1, bounds.y1))

    def available_size(self, section: VtObject, avail: _Rect, minimum: int) -> tuple[float, float]:
        left, bottom, right, top = _section_margins(section, _SECTION_INNER, minimum)
        outer = _SECTION_OUTER
        return (
            (avail.x1 - avail.x0 - 2 * outer) - left - (right + _LAYOUT_PAD),
            (avail.y1 - avail.y0 - 2 * outer) - bottom - (top + _LAYOUT_PAD),
        )

    @staticmethod
    def interior(section: VtObject, rect: _Rect, minimum: int) -> _Rect:
        left, bottom, right, top = _section_margins(section, _SECTION_INNER, minimum)
        return rect.inset(left, bottom, right, top)

    # -- 글상자 ------------------------------------------------------------------------------------------------

    @staticmethod
    def cached_or_measured(block: VtObject, text: str, face: FontFace) -> tuple[int, int]:
        """VtTextBlock::Layout: 캐시가 유효하고(0xc8) 글꼴 크기가 같으면 저장된 측정 크기(0xd0)를 쓴다."""
        if block["valid"] and block["measured"]["xy"] != (0, 0):
            return block["measured"]["xy"]
        return block_size(text, face)

    def add_text(self, text: str, rect: _Rect, height: int, face: FontFace, h_align: int, v_align: int) -> None:
        """SetPosition·SetSize(0x100d9330·0x100d9430)는 정수로 버린다."""
        x0, y0 = int(rect.x0), int(rect.y0)
        box = (x0, y0, x0 + int(rect.x1 - rect.x0), y0 + int(rect.y1 - rect.y0))
        self.texts.append(_Text(text, box, height, face, h_align, v_align))

    # -- 범례 ------------------------------------------------------------------------------------------------

    def legend_items(self) -> list[tuple[VtObject, str, tuple[int, int]]]:
        """FUN_10051110·FUN_10051ab0: 범례에 넣는 계열(0x190이 0)과 계열별 범례 글상자 크기."""
        legend = self.chart["legend"]
        face = _face(legend["font"])
        items = []
        for series in self.chart["series"]["items"]:
            if series is None or series["s150"][3] != 0:
                continue
            block = series["legend_block"]
            name = series["name"]["s"] if series.get("name") else ""
            items.append((series, name, self.cached_or_measured(block, name, face)))
        return items

    def layout_legend(self, avail: _Rect) -> None:
        legend = self.chart["legend"]
        if not legend["location"]["visible"]:
            return
        items = self.legend_items()
        count = len(items)
        text_w = max((size[0] for _, _, size in items), default=0)
        text_h = max((size[1] for _, _, size in items), default=0)
        # FUN_10041f40·FUN_10051ab0: 막대 범례 기호는 글상자 높이의 정사각형이다.
        symbol = float(text_h)
        inner = _SECTION_INNER
        half = symbol * 0.5
        item_w = text_h * 0.5 + symbol + text_w
        avail_w, avail_h = self.available_size(legend, avail, _MIN_INTERIOR_MARGIN)
        # FUN_10051440: 왼·오른쪽(3·4)과 사용자 위치(8)는 세로로 쌓고 나머지는 가로로 늘어놓는다.
        if legend["location"]["type"] in (3, 4, 8):
            per = int((avail_h + half) / (half + symbol)) if half + symbol else 0
            cols = (count - 1 + per) // per if per else 0
            rows = (count - 1 + cols) // cols if cols else 0
        else:
            per = int((avail_w + inner) / (inner + item_w))
            rows = (count - 1 + per) // per if per else 0
            cols = (count - 1 + rows) // rows if rows else 0
        size = ((inner + item_w) * cols - inner, (half + symbol) * rows - half)
        rect = self.place_section(legend, avail, size, _MIN_INTERIOR_MARGIN)
        inside = self.interior(legend, rect, _MIN_INTERIOR_MARGIN)
        # FUN_100512f0: 칸 크기와, 칸이 넉넉할 때 가운데 맞춤 이동.
        cell_w = (inside.x1 - inside.x0) / max(cols, 1)
        cell_h = (inside.y1 - inside.y0) / max(rows, 1)
        need_w = item_w + (inner if cols > 1 else 0)
        need_h = symbol + (half if rows > 1 else 0)
        step_w, step_h = max(cell_w, need_w), max(cell_h, need_h)
        if need_w <= cell_w and item_w < step_w:
            inside.x0 += (step_w - item_w) * 0.5
        if need_h <= cell_h and symbol < step_h:
            inside.y1 -= (step_h - symbol) * 0.5
        face = _face(legend["font"])
        # VtChart::ReadFrom이 그림 영역 0x84c에 둔 값(깃발 & 0xc0 == 0)이면 계열을 거꾸로 놓는다(FUN_10051600).
        reverse = (self.chart.get("flags", -1) & 0xC0) == 0
        index = 0
        for col in range(cols):
            top = inside.y1
            for row in range(rows):
                if index >= count:
                    break
                series, name, (_, block_h) = items[count - 1 - index if reverse else index]
                x0 = inside.x0 + col * step_w
                # FUN_10051600: 기호 사각형과 글 사각형. 첫 줄 글 사각형만 아래 끝이 기호 아래가 아니다.
                self.shapes.append(("rect", ((x0, top), (x0 + symbol, top - symbol)), _series_color(series)))
                text_top = top + text_h * 0.25
                text_bottom = text_top - text_h if row == 0 else top - symbol
                text_rect = _Rect(x0 + symbol + _LAYOUT_PAD, text_bottom, x0 + symbol + _LAYOUT_PAD + text_w, text_top)
                block = series["legend_block"]
                layout = block["layout"]
                self.add_text(name, text_rect, block_h, face, layout["h"], layout["v"])
                top -= step_h
                index += 1

    # -- 제목·각주 ---------------------------------------------------------------------------------------------

    def layout_chart_text(self, section: VtObject | None, avail: _Rect) -> None:
        """VtChartText::Size(0x1004ead0)는 글상자를 자동 크기로 바꿔 캐시를 버리고 다시 측정한다."""
        if section is None or not section["location"]["visible"]:
            return
        block = section["block"]
        text = block["text"]["s"] if block["text"] else ""
        face = _face(block["font"])
        width, height = block_size(text, face)
        margin = _SECTION_INNER  # VtChartText::GetMinimumInteriorMargin = GetInternalMargin(0x100333d0)
        rect = self.place_section(section, avail, (width, height), margin)
        inside = self.interior(section, rect, margin)
        layout = block["layout"]
        self.add_text(text, inside, height, face, layout["h"], layout["v"])

    # -- 그림 영역 ---------------------------------------------------------------------------------------------

    def data_table(self) -> tuple[list[str], list[str], list[list[float | None]]]:
        """VtDataGrid: 첫 행은 계열 이름, 첫 열은 항목 이름, 나머지는 값."""
        grid = self.chart["grid"]
        rows, cols, cells = grid["rows"], grid["cols"], grid["cells"]

        def cell(row: int, col: int):
            return cells[row * cols + col]

        names = [cell(0, col)["s"] if cell(0, col) else "" for col in range(1, cols)]
        labels = [cell(row, 0)["s"] if cell(row, 0) else "" for row in range(1, rows)]
        values = [[_number(cell(row, col)) for col in range(1, cols)] for row in range(1, rows)]
        return names, labels, values

    def layout_plot(self, avail: _Rect) -> None:
        plot = self.chart["plot"]
        if not plot["location"]["visible"]:
            return
        # VtChartSection::Size(0x10044780): 그림 영역은 남은 크기를 모두 쓴다.
        size = self.available_size(plot, avail, _MIN_INTERIOR_MARGIN)
        rect = self.place_section(plot, avail, size, _MIN_INTERIOR_MARGIN)
        inside = self.interior(plot, rect, _MIN_INTERIOR_MARGIN)
        _, labels, values = self.data_table()
        x_axis, y_axis = plot["x_axis"], plot["y_axes"][0]
        x_face = _face(x_axis["blocks"]["items"][0]["font"])
        y_face = _face(y_axis["blocks"]["items"][0]["font"])
        label_sizes = [block_size(label, x_face) for label in labels]
        x_label_w = max((w for w, _ in label_sizes), default=0)
        x_label_h = max((h for _, h in label_sizes), default=0)
        numbers = [v for row in values for v in row if v is not None]
        low, high = min([0.0, *numbers]), max([0.0, *numbers])
        y_tick = _tick_extent(y_axis)
        x_tick = 0.0  # FUN_1001f230: 0xc0·0xbc가 모두 서야 눈금 길이를 더한다.
        margins = (0.0, 0.0, 0.0, 0.0)
        for _ in range(8):  # FUN_100699a0은 여백이 바뀌지 않을 때까지 되풀이한다. 8은 무한 반복을 막는 상한이다.
            box = inside.inset(*margins)
            y_length = box.y1 - box.y0
            scale = _value_scale(y_axis, low, high, y_length, y_face)
            y_label_w, y_label_h = scale[4], scale[5]
            # 왼쪽 값 글자 폭+눈금+100, 위·아래 높이 절반(FUN_10029820), 아래 항목 높이+100(FUN_1001f230).
            new = (
                y_label_w + y_tick + _AXIS_LABEL_GAP,
                max(x_label_h + x_tick + _AXIS_LABEL_GAP, y_label_h * 0.5),
                0.0,
                y_label_h * 0.5,
            )
            if new == margins:
                break
            margins = new
        box = inside.inset(*margins)
        low_v, high_v, count, step, label_w, label_h = scale
        # FUN_100299c0: 값 글상자(최대 폭·높이)를 눈금 높이 가운데, 축에서 눈금+100 왼쪽에 놓는다.
        y_layout = y_axis["blocks"]["items"][0]["layout"]
        for index in range(count + 1):
            value = low_v + step * index
            y = box.y0 + (box.y1 - box.y0) * (value - low_v) / (high_v - low_v)
            right = box.x0 - (y_tick + _AXIS_LABEL_GAP)
            self.add_text(
                _format_value(value),
                _Rect(right - label_w, y - label_h * 0.5, right, y + label_h * 0.5),
                label_h,
                y_face,
                y_layout["h"],
                y_layout["v"],
            )
            self.shapes.append(("line", ((box.x0, y), (box.x1, y)), tuple(y_axis["major_pen"]["color"]["rgb"])))
        # FUN_1001e0a0: 나눔 칸 폭 글상자를 축 아래 100에 놓는다. 세로 맞춤은 FUN_1001ed40이 2로 둔다.
        division = (box.x1 - box.x0) / max(len(labels), 1)
        per_label = _labels_per_division(len(labels), box.x1 - box.x0, x_label_w)
        x_layout = x_axis["blocks"]["items"][0]["layout"]
        for index in range(0, len(labels), per_label):
            x0 = box.x0 + division * index
            top = box.y0 - (x_tick + _AXIS_LABEL_GAP)
            label_rect = _Rect(x0, top - x_label_h, x0 + division * per_label, top)
            self.add_text(labels[index], label_rect, label_sizes[index][1], x_face, x_layout["h"], 2)
        self.shapes.append(("line", ((box.x0, box.y0), (box.x0, box.y1)), tuple(y_axis["pen"]["color"]["rgb"])))
        self.shapes.append(("line", ((box.x0, box.y0), (box.x1, box.y0)), tuple(x_axis["pen"]["color"]["rgb"])))


def _tick_extent(axis: VtObject) -> float:
    """VtAxis::SetupSize(0x1001b6a0): 눈금 모양 1이면 길이의 절반, 3이면 전부, 그 밖은 0."""
    style = axis["tick_style"]
    if style == 1:
        return axis["tick_length"] * 0.5
    if style == 3:
        return axis["tick_length"]
    return 0.0


def _value_scale(
    axis: VtObject, low: float, high: float, length: float, face: FontFace
) -> tuple[float, float, int, float, int, int]:
    """FUN_10029820: (최소, 최대, 나눔, 간격, 글자 폭, 글자 높이). 수동 눈금(0x64)이면 저장값을 쓴다."""

    def labels(new_low: float, step: float, count: int) -> tuple[int, int]:
        sizes = [block_size(_format_value(new_low + step * i), face) for i in range(count + 1)]
        return max(w for w, _ in sizes), max(h for _, h in sizes)

    if axis["manual_scale"]:
        count = max(axis["divisions"][0], 1)
        step = (axis["maximum"] - axis["minimum"]) / count
        return axis["minimum"], axis["maximum"], count, step, *labels(axis["minimum"], step, count)
    # FUN_10029300: 나눔 10에서 시작해 글자 높이가 들어갈 때까지 줄인다. FUN_10029610은 "0"으로 높이를 측정한다.
    _, sample_h = block_size("0", face)
    divisions = 10 if int(length / (sample_h * 1.5)) > 0 else 1
    while True:
        new_low, new_high, count, step = _nice_scale(low, high, divisions)
        if new_high - step * 0.5 < high:
            # FUN_100266b0이 최대를 한 간격 늘리고 FUN_10026a00이 나눔 수로 간격을 다시 낸다.
            new_high += step
            step = (new_high - new_low) / count
        label_w, label_h = labels(new_low, step, count)
        if count <= int(length / label_h) or count <= 1:
            return new_low, new_high, count, step, label_w, label_h
        divisions = count - 1


def _labels_per_division(count: int, length: float, label_w: float) -> int:
    """FUN_1001f230: 글자 폭+60이 들어가는 나눔 수로 몇 칸마다 글자를 쓸지 정한다."""
    fit = int(length / (label_w + _CATEGORY_LABEL_SPACING)) if label_w + _CATEGORY_LABEL_SPACING else 1
    fit = max(fit, 1)
    return (count - 1) // fit + 1


#: VtFont::GetName(0x1008b740): 이름이 비면 리소스 문자열 16000, 그것도 없으면 "Gulim"을 쓴다.
_DEFAULT_FONT = "Gulim"


def _face(font: VtObject) -> FontFace:
    name = font["name"]["s"] if font["name"] is not None else ""
    return FontFace(name or _DEFAULT_FONT, font["size"])


def _number(cell: VtObject | None) -> float | None:
    return cell["v"] if cell is not None and "v" in cell else None


def _series_color(series: VtObject) -> tuple[int, int, int]:
    return tuple(series["color"]["rgb"])


# ---------------------------------------------------------------------------------------------------------------
# 5. 글자 찍기
# ---------------------------------------------------------------------------------------------------------------

#: GDI+ 기본 StringFormat 글줄 양옆 여백(em의 1/6). PowerShell GDI+ 측정: 함초롬돋움 9pt '0' 67.45px = 2×12.5 + 42.45.
_GDIPLUS_PADDING = 1 / 6
#: GDI+ MeasureString 높이에 더해지는 값(em의 1/8). PowerShell GDI+ 메타파일 Graphics 확인: 굴림체 10pt '2000' 106.12px.
_GDIPLUS_EXTRA_HEIGHT = 1 / 8
#: ShapeRenderDraw::Text(0x100c1370)가 측정 높이를 나누는 값(0x101572a0).
_TEXT_HEIGHT_DIVISOR = 1.3


def _gdiplus_height(face: FontFace, em: float) -> float:
    """GDI+ MeasureString 한 줄 높이(메타파일 Graphics). 한글은 차트를 EMF로 기록한다(HncEmfPlusConverter.dll)."""
    upem, _, _, win_ascent, win_descent, line_gap = face.vertical
    return em * (win_ascent + win_descent + line_gap) / upem + em * _GDIPLUS_EXTRA_HEIGHT


def _place_glyphs(layout: _Layout, item: _Text, drawing: ChartDrawing) -> None:
    """VtTextBlock::DrawTextW(0x100d8600)·GetPosition(0x100d8340) → ShapeRenderDraw::Text → GDI+ DrawString."""
    x0, y0, x1, y1 = item.rect
    height = item.block_height
    ascent, descent, _ = text_metrics(item.face)
    if item.h_align == 2:
        x, align = (x0 + x1) // 2, 1
    elif item.h_align == 1:
        x, align = x1, 2
    else:
        x, align = x0, 0
    base = height - descent - ascent
    if item.v_align == 2:
        y = base + ((y1 + y0) // 2 - height // 2)
    elif item.v_align == 1:
        y = base + y0
    else:
        y = base + (y1 - height)
    device_x, device_y = layout.device(x, y)
    device_x, device_y = int(device_x), int(device_y)  # FUN_1009dc00이 장치 좌표를 정수로 버린다.
    face = item.face
    upem = face.vertical[0]
    em = face.size * _DEVICE_DPI / 72
    top = device_y - _gdiplus_height(face, em) / _TEXT_HEIGHT_DIVISOR
    baseline = top + em * face.vertical[3] / upem
    advances = [em * face.design_advance(char) / face.metric.em for char in item.text]
    total = sum(advances)
    if align == 1:
        start = device_x - total / 2
    elif align == 2:
        start = device_x - total - em * _GDIPLUS_PADDING
    else:
        start = device_x + em * _GDIPLUS_PADDING
    pen = start
    for char, advance in zip(item.text, advances, strict=True):
        if not char.isspace():
            drawing.glyphs.append(
                ChartGlyph(char, pen * 72 / _DEVICE_DPI, baseline * 72 / _DEVICE_DPI, face.size, face.name)
            )
        pen += advance


class UnsupportedChart(Exception):
    """옮기지 않은 차트 기능(3차원·사용자 위치·폭 표 없는 글꼴 등)."""


def draw_vtchart(contents: bytes, left: float, top: float, width: float, height: float) -> ChartDrawing | None:
    """쪽 좌표 pt 상자에 그린 VtChart 차트. 옮기지 않은 차트면 None(OOXMLChartContents가 있는 개체는 대상이 아니다)."""
    chart = parse_vtchart(contents)
    series = [s for s in chart["series"]["items"] if s is not None]
    if chart["type"] != _BAR_2D or any(s["methods"][_BAR_2D] != _BAR_2D for s in series):
        return None
    unit = 72 / _DEVICE_DPI
    # 개체 모서리를 장치 점으로 올림한다. 확인: sample 차트(8504·9920 HU, 27750×20250) 장치 709·827·3022·2515.
    device = tuple(math.ceil(value / unit - 1e-6) for value in (left, top, left + width, top + height))
    layout = _Layout(chart, device)
    metric = _frame_metric(chart["backdrop"])
    avail = _Rect(metric, metric, layout.width - metric, layout.height - metric)
    drawing = ChartDrawing()
    try:
        layout.layout_chart_text(chart["title"], avail)
        layout.layout_chart_text(chart["footnote"], avail)
        layout.layout_legend(avail)
        layout.layout_plot(avail)
        for item in layout.texts:
            _place_glyphs(layout, item, drawing)
    except UnsupportedChart:
        return None
    for kind, points, color in layout.shapes:
        mapped = tuple((x * unit, y * unit) for x, y in (layout.device(px, py) for px, py in points))
        drawing.shapes.append(ChartShape(kind, mapped, color))
    return drawing
