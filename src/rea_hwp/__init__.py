"""HWP 5.0 파서와 쪽 단위 고정 배치 렌더러."""

from .container import Limits
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

#: pyproject.toml의 판과 같게 둔다. 공개 API나 읽기·렌더 결과가 바뀌는 커밋마다 올린다.
__version__ = "0.1.3"

__all__ = [
    "CorruptFileError",
    "DrmProtectedError",
    "HwpDecryptError",
    "HwpDocument",
    "HwpFormatError",
    "LimitExceededError",
    "Limits",
    "PasswordRequiredError",
    "ReaHwpError",
    "UnsupportedFormatError",
    "WrongPasswordError",
    "__version__",
]
