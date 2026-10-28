"""HWP 파일 하나를 쪽 SVG 묶음(page-0001.svg, ...)으로 바꾼다. HTML 뷰어와 .eq.svg는 `rea-hwp render`가 만든다.

python convert.py example.hwp out [password]
"""

from __future__ import annotations

import sys
from pathlib import Path

from rea_hwp import HwpDocument, page_to_svg, render_pages


def convert(path: str | Path, out_dir: str | Path, password: str | None = None) -> int:
    pages = render_pages(HwpDocument.open(path, password))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for page in pages:
        (out / f"page-{page.number:04d}.svg").write_text(page_to_svg(page), encoding="utf-8")
    return len(pages)


if __name__ == "__main__":
    source, target, *rest = sys.argv[1:]
    count = convert(source, target, rest[0] if rest else None)
    print(f"{count} pages -> {target}")
