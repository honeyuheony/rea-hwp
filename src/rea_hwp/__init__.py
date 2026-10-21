"""HWP 5.0 파서와 쪽 단위 고정 배치 렌더러."""

from .container import FileHeader, Limits
from .document import HwpDocument
from .errors import (
    CorruptEncryptedStreamError,
    CorruptFileError,
    DrmProtectedError,
    HwpDecryptError,
    HwpFormatError,
    LimitExceededError,
    PasswordRequiredError,
    ReaHwpError,
    UnsupportedFormatError,
    WrongPasswordError,
)
from .models.paragraph import ControlChar
from .render.layout import render_pages
from .render.locations import page_text_lines, paragraph_pages
from .render.options import RenderOptions
from .render.scene import Image, MissingObject, Page, TextLine
from .render.styles import DocStyles
from .render.svg import page_to_svg
from .structure import BinDataItem, Paragraph, RecordView, picture_bin_data_id, record_id, subtree_end

#: pyproject.toml의 버전과 같게 둔다. 공개 API나 읽기·렌더 결과가 바뀌는 커밋마다 올린다.
__version__ = "0.2.3"

__all__ = [
    "BinDataItem",
    "ControlChar",
    "CorruptEncryptedStreamError",
    "CorruptFileError",
    "DocStyles",
    "DrmProtectedError",
    "FileHeader",
    "HwpDecryptError",
    "HwpDocument",
    "HwpFormatError",
    "Image",
    "LimitExceededError",
    "Limits",
    "MissingObject",
    "Page",
    "Paragraph",
    "PasswordRequiredError",
    "ReaHwpError",
    "RecordView",
    "RenderOptions",
    "TextLine",
    "UnsupportedFormatError",
    "WrongPasswordError",
    "__version__",
    "page_text_lines",
    "page_to_svg",
    "paragraph_pages",
    "picture_bin_data_id",
    "record_id",
    "render_pages",
    "subtree_end",
]
