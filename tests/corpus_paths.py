"""시험이 쓰는 말뭉치 위치.

REA_HWP_CORPUS는 공개 말뭉치(manifest.csv가 있는 폴더), REA_HWP_USER_CORPUS는 사용자 표본 폴더를 가리킨다.
변수가 없으면 존재하지 않는 경로를 돌려주므로 그 문서를 쓰는 시험은 건너뛴다.
"""

import os
from pathlib import Path

MISSING_ROOT = Path(__file__).resolve().parent / "corpus-not-configured"


def _root(name: str) -> Path:
    value = os.environ.get(name)
    return Path(value) if value else MISSING_ROOT


CORPUS_ROOT = _root("REA_HWP_CORPUS")
USER_CORPUS_ROOT = _root("REA_HWP_USER_CORPUS")
