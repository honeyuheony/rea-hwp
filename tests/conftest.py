import pytest
from corpus_paths import MISSING_ROOT


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    """말뭉치 변수를 지정하지 않아 문서를 못 연 시험은 실패 대신 건너뛴다."""
    try:
        return (yield)
    except FileNotFoundError as error:
        if error.filename and str(error.filename).startswith(str(MISSING_ROOT)):
            pytest.skip("REA_HWP_CORPUS·REA_HWP_USER_CORPUS가 지정되지 않음")
        raise
