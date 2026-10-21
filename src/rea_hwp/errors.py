"""문서를 열고 읽고 그리다 나는 오류. 호출하는 쪽은 자료형으로 사용자 안내를 고른다.

예전 이름 HwpFormatError·HwpDecryptError·TruncatedPayload는 상위 자료형으로 남아 그 이름을 잡던 코드도 그대로 동작한다.
"""

from __future__ import annotations


class ReaHwpError(Exception):
    """rea-hwp가 내는 모든 오류의 기반."""


class HwpFormatError(ReaHwpError, ValueError):
    """HWP 5.0 문서로 열 수 없다(지원하지 않는 형식, DRM, 깨진 파일의 공통 상위)."""


class HwpDecryptError(ReaHwpError, ValueError):
    """암호를 풀지 못했다(암호 없음, 틀린 암호, 깨진 암호 스트림의 공통 상위)."""


class UnsupportedFormatError(HwpFormatError):
    """HWP 3.0, HWPX, OLE가 아닌 파일, 지원하지 않는 암호 방식."""


class DrmProtectedError(HwpFormatError):
    """DRM으로 보호한 문서."""


class PasswordRequiredError(HwpDecryptError):
    """열기 암호가 걸렸는데 암호를 주지 않았다."""


class WrongPasswordError(HwpDecryptError):
    """준 암호로 푼 스트림이 HWP 레코드가 아니다."""


class CorruptFileError(HwpFormatError):
    """잘린 레코드·스트림, 있어야 할 스트림이 없음, 깨진 OLE 그릇, 풀 수 없는 압축."""


class CorruptEncryptedStreamError(CorruptFileError, HwpDecryptError):
    """배포용 문서의 암호 머리(DISTRIBUTE_DOC_DATA)가 깨졌다."""


class LimitExceededError(ReaHwpError):
    """푼 크기나 레코드 수가 Limits 상한을 넘었다. 렌더 코드의 except ValueError에 묻히지 않게 ValueError가 아니다."""
