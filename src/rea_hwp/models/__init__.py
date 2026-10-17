"""레코드 타입 모델. load_all()이 각 모듈을 불러와 registry에 등록한다."""

from __future__ import annotations

import importlib

_MODULES = ("paragraph", "docinfo", "controls", "shapes")


def load_all() -> None:
    for module in _MODULES:
        importlib.import_module(f"{__name__}.{module}")
