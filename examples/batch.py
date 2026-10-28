"""폴더 안 *.hwp를 문서마다 하위 폴더로 변환한다. 열 수 없는 문서는 건너뛰고 이유를 찍는다.

python batch.py hwp_dir out_dir
"""

from __future__ import annotations

import sys
from pathlib import Path

from convert import convert

from rea_hwp import ReaHwpError


def convert_folder(source_dir: Path, out_dir: Path) -> list[tuple[Path, str]]:
    failures = []
    for path in sorted(source_dir.glob("*.hwp")):
        try:
            count = convert(path, out_dir / path.stem)
        except ReaHwpError as error:
            failures.append((path, f"{type(error).__name__}: {error}"))
            continue
        print(f"{path.name}: {count} pages")
    return failures


if __name__ == "__main__":
    failures = convert_folder(Path(sys.argv[1]), Path(sys.argv[2]))
    for path, reason in failures:
        print(f"skipped {path.name}: {reason}")
    sys.exit(1 if failures else 0)
