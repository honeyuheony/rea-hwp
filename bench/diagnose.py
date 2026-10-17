"""문서 하나의 쪽별 글자 원점 오차를 x·y로 나눠 보여 준다. 사용: diagnose.py <문서 경로 일부> [쪽]"""

from __future__ import annotations

import collections
import difflib
import statistics
import sys

from accuracy import EQUATION_FONTS, PlacedChar, _inside, pdf_pages, score_page
from corpus import CORPUS_ROOT, load_corpus

from rea_hwp.document import HwpDocument
from rea_hwp.render.layout import layout_document
from rea_hwp.render.svg import page_glyph_table


def main() -> int:
    entry = next(e for e in load_corpus() if sys.argv[1] in str(e.path))
    only_page = int(sys.argv[2]) if len(sys.argv) > 2 else None
    reference = pdf_pages(str(CORPUS_ROOT / "ref2022" / entry.path.relative_to(CORPUS_ROOT).with_suffix(".pdf")))
    pages = layout_document(HwpDocument.open(entry.path, entry.password))
    for index, ref in enumerate(reference):
        if only_page is not None and index + 1 != only_page:
            continue
        out = [PlacedChar(c, x, y) for c, x, y in page_glyph_table(pages[index])] if index < len(pages) else []
        boxes = [tuple(v / 100 for v in box) for box in pages[index].excluded] if index < len(pages) else []
        score = score_page(ref, out, boxes)
        ref = [c for c in ref if not _inside(c, boxes) and not c.font.startswith(EQUATION_FONTS)]
        matcher = difflib.SequenceMatcher(None, [c.char for c in ref], [c.char for c in out], autojunk=False)
        lines = collections.OrderedDict()
        for block in matcher.get_matching_blocks():
            for k in range(block.size):
                a, b = ref[block.a + k], out[block.b + k]
                lines.setdefault(round(a.y, 1), []).append((a, b))
        print(
            f"page {index + 1}: ref {score.reference_chars} (excluded {score.excluded}) out {len(out)} "
            f"matched {score.matched} within {score.within}"
        )
        for y, pairs in list(lines.items())[:60]:
            dx = [b.x - a.x for a, b in pairs]
            dy = [b.y - a.y for a, b in pairs]
            text = "".join(a.char for a, _ in pairs)[:28]
            print(
                f"  y={y:7.1f} n={len(pairs):3d} dx first {dx[0]:+7.2f} last {dx[-1]:+7.2f}"
                f"  dy {statistics.median(dy):+7.2f}  {text}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())


def missing(path_part: str, page_no: int) -> None:
    """정답에는 있는데 짝을 못 찾은 글자를 줄별로 보여 준다."""
    entry = next(e for e in load_corpus() if path_part in str(e.path))
    reference = pdf_pages(str(CORPUS_ROOT / "ref2022" / entry.path.relative_to(CORPUS_ROOT).with_suffix(".pdf")))
    page = layout_document(HwpDocument.open(entry.path, entry.password))[page_no - 1]
    boxes = [tuple(v / 100 for v in box) for box in page.excluded]
    ref = [c for c in reference[page_no - 1] if not _inside(c, boxes) and not c.font.startswith(EQUATION_FONTS)]
    out = [PlacedChar(c, x, y) for c, x, y in page_glyph_table(page)]
    matcher = difflib.SequenceMatcher(None, [c.char for c in ref], [c.char for c in out], autojunk=False)
    for tag, a1, a2, _, _ in matcher.get_opcodes():
        if tag in ("delete", "replace"):
            print(f"  {tag} x={ref[a1].x:6.1f} y={ref[a1].y:6.1f} {''.join(c.char for c in ref[a1:a2])[:60]}")
