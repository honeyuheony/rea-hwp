"""HWPTAG 값과 컨트롤 ID(명세 5.0 rev1.3 표13·표57, 실물로 확인한 값 포함)."""

from __future__ import annotations

BEGIN = 0x010

# DocInfo
DOCUMENT_PROPERTIES = BEGIN + 0
ID_MAPPINGS = BEGIN + 1
BIN_DATA = BEGIN + 2
FACE_NAME = BEGIN + 3
BORDER_FILL = BEGIN + 4
CHAR_SHAPE = BEGIN + 5
TAB_DEF = BEGIN + 6
NUMBERING = BEGIN + 7
BULLET = BEGIN + 8
PARA_SHAPE = BEGIN + 9
STYLE = BEGIN + 10
DOC_DATA = BEGIN + 11
DISTRIBUTE_DOC_DATA = BEGIN + 12
COMPATIBLE_DOCUMENT = BEGIN + 14
LAYOUT_COMPATIBILITY = BEGIN + 15
TRACKCHANGE = BEGIN + 16
MEMO_SHAPE = BEGIN + 76
FORBIDDEN_CHAR = BEGIN + 78
TRACK_CHANGE = BEGIN + 80
TRACK_CHANGE_AUTHOR = BEGIN + 81

# BodyText
PARA_HEADER = BEGIN + 50
PARA_TEXT = BEGIN + 51
PARA_CHAR_SHAPE = BEGIN + 52
PARA_LINE_SEG = BEGIN + 53
PARA_RANGE_TAG = BEGIN + 54
CTRL_HEADER = BEGIN + 55
LIST_HEADER = BEGIN + 56
PAGE_DEF = BEGIN + 57
FOOTNOTE_SHAPE = BEGIN + 58
PAGE_BORDER_FILL = BEGIN + 59
SHAPE_COMPONENT = BEGIN + 60
TABLE = BEGIN + 61
SHAPE_COMPONENT_LINE = BEGIN + 62
SHAPE_COMPONENT_RECTANGLE = BEGIN + 63
SHAPE_COMPONENT_ELLIPSE = BEGIN + 64
SHAPE_COMPONENT_ARC = BEGIN + 65
SHAPE_COMPONENT_POLYGON = BEGIN + 66
SHAPE_COMPONENT_CURVE = BEGIN + 67
SHAPE_COMPONENT_OLE = BEGIN + 68
SHAPE_COMPONENT_PICTURE = BEGIN + 69
SHAPE_COMPONENT_CONTAINER = BEGIN + 70
CTRL_DATA = BEGIN + 71
EQEDIT = BEGIN + 72
SHAPE_COMPONENT_TEXTART = BEGIN + 74
FORM_OBJECT = BEGIN + 75
MEMO_LIST = BEGIN + 77
CHART_DATA = BEGIN + 79
VIDEO_DATA = BEGIN + 82
SHAPE_COMPONENT_UNKNOWN = BEGIN + 99

TAG_NAMES = {value: name for name, value in globals().items() if name.isupper() and isinstance(value, int)}


def ctrl_id(text: str) -> int:
    """'tbl ' 같은 4글자 ID를 파일에 저장된 u32 값으로 바꾼다(MAKE_4CHID, 첫 글자가 최상위 바이트)."""
    a, b, c, d = (ord(ch) for ch in text)
    return (a << 24) | (b << 16) | (c << 8) | d


def ctrl_name(value: int) -> str:
    return "".join(chr((value >> shift) & 0xFF) for shift in (24, 16, 8, 0))


TABLE_CTRL = ctrl_id("tbl ")
GSO_CTRL = ctrl_id("gso ")
EQUATION_CTRL = ctrl_id("eqed")
SECTION_DEF_CTRL = ctrl_id("secd")
COLUMN_DEF_CTRL = ctrl_id("cold")
HEADER_CTRL = ctrl_id("head")
FOOTER_CTRL = ctrl_id("foot")
FOOTNOTE_CTRL = ctrl_id("fn  ")
ENDNOTE_CTRL = ctrl_id("en  ")
AUTO_NUMBER_CTRL = ctrl_id("atno")
NEW_NUMBER_CTRL = ctrl_id("nwno")
PAGE_HIDE_CTRL = ctrl_id("pghd")
PAGE_ODD_EVEN_CTRL = ctrl_id("pgct")
PAGE_NUMBER_POS_CTRL = ctrl_id("pgnp")
INDEX_MARK_CTRL = ctrl_id("idxm")
BOOKMARK_CTRL = ctrl_id("bokm")
OVERLAP_CTRL = ctrl_id("tcps")
DUTMAL_CTRL = ctrl_id("tdut")
HIDDEN_COMMENT_CTRL = ctrl_id("tcmt")
CLICK_HERE_CTRL = ctrl_id("%clk")
