"""렌더 결과를 바꾸는 설정. 라이브러리 경로는 환경 변수를 읽지 않고 이 객체만 본다."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

#: 글맵시 글꼴 기본 폴더: Windows 글꼴과 한컴 공용 TTF. 없는 폴더는 건너뛰므로 Linux 서버에서는 font_dirs를 준다.
DEFAULT_FONT_DIRS = (
    Path("C:/Windows/Fonts"),
    Path("/mnt/c/Windows/Fonts"),
    Path("/mnt/c/Program Files (x86)/HNC/Office 2022/HOffice120/Shared/TTF"),
)


@dataclass(frozen=True)
class RenderOptions:
    #: 본문 표를 table_paging(한글 2022 행 배치 루프)으로 나눈다. False면 경험 규칙 경로를 쓴다.
    table_engine: bool = True
    #: 표 엔진 입력에 낡은 셀 줄을 다시 나눈 줄(recompose)을 쓴다. False면 저장 줄만 쓴다.
    engine_recompose: bool = True
    #: 글맵시 글꼴(TTF·TTC)을 찾을 폴더. 찾지 못한 글맵시는 Page.missing에 남는다.
    font_dirs: tuple[Path, ...] = DEFAULT_FONT_DIRS

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] = os.environ) -> RenderOptions:
        """CLI·bench용. REA_HWP_TABLE_ENGINE=0, REA_HWP_ENGINE_RECOMPOSE=0, REA_HWP_FONT_DIRS(경로 구분자로 나눔)."""
        configured = environ.get("REA_HWP_FONT_DIRS")
        return cls(
            table_engine=environ.get("REA_HWP_TABLE_ENGINE", "1") != "0",
            engine_recompose=environ.get("REA_HWP_ENGINE_RECOMPOSE", "1") != "0",
            font_dirs=tuple(Path(p) for p in configured.split(os.pathsep) if p) if configured else DEFAULT_FONT_DIRS,
        )
