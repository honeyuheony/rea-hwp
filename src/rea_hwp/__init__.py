"""HWP 5.0 파서와 쪽 단위 고정 배치 렌더러."""

from .container import FileHeader, Limits
from .document import HwpDocument
from .errors import (
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
from .render.styles import DocStyles
from .structure import BinDataItem, Paragraph, RecordView, picture_bin_data_id, record_id, subtree_end

#: pyproject.toml의 판과 같게 둔다. 공개 API나 읽기·렌더 결과가 바뀌는 커밋마다 올린다.
__version__ = "0.1.4"

__all__ = [
    "BinDataItem",
    "ControlChar",
    "CorruptFileError",
    "DocStyles",
    "DrmProtectedError",
    "FileHeader",
    "HwpDecryptError",
    "HwpDocument",
    "HwpFormatError",
    "LimitExceededError",
    "Limits",
    "Paragraph",
    "PasswordRequiredError",
    "ReaHwpError",
    "RecordView",
    "UnsupportedFormatError",
    "WrongPasswordError",
    "__version__",
    "picture_bin_data_id",
    "record_id",
    "subtree_end",
]
