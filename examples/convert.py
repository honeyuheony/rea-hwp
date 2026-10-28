"""HWP 파일 하나를 쪽 SVG 묶음과 glyphs.json으로 바꾼다. `rea-hwp render --glyphs`에서 HTML 뷰어만 뺀 것.

python convert.py example.hwp out [password]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from rea_hwp import HwpDocument, page_to_svg, render_pages

# 두 함수는 __all__ 밖이지만 rea-hwp render가 .eq.svg와 glyphs.json을 만들 때 쓰는 경로다.
from rea_hwp.render.svg import equation_fonts, page_glyph_table


def convert(path: str | Path, out_dir: str | Path, password: str | None = None) -> int:
    pages = render_pages(HwpDocument.open(path, password))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    glyphs = []
    for page in pages:
        stem = f"page-{page.number:04d}"
        (out / f"{stem}.svg").write_text(page_to_svg(page), encoding="utf-8")
        if equation_fonts(page):
            (out / f"{stem}.eq.svg").write_text(page_to_svg(page, equation_fallback=True), encoding="utf-8")
        glyphs.append(page_glyph_table(page))
    (out / "glyphs.json").write_text(json.dumps(glyphs, ensure_ascii=False), encoding="utf-8")
    return len(pages)


if __name__ == "__main__":
    source, target, *rest = sys.argv[1:]
    count = convert(source, target, rest[0] if rest else None)
    print(f"{count} pages -> {target}")
