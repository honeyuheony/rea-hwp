"""코퍼스를 렌더링해 한글 2022 PDF와 쪽 수·글자 원점 위치를 비교한다(goal 종료 조건 4).

사용: uv run --extra bench python bench/evaluate.py [--limit N] [--match 이름일부] [--details]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from accuracy import (
    TOLERANCE_PT,
    PlacedChar,
    _inside,
    letters,
    merge_effect_strikes,
    order_match,
    pdf_image_boxes,
    pdf_pages,
    score_page,
)
from corpus import CORPUS_ROOT, load_corpus
from equation_oracle import graft_equation_layout
from hancom_resave import RESAVE_ROOT

from rea_hwp.document import HwpDocument
from rea_hwp.render.layout import layout_document
from rea_hwp.render.options import RenderOptions
from rea_hwp.render.svg import HU_PER_PT, page_glyph_table, symbol_text

REFERENCE_ROOT = CORPUS_ROOT / "ref2022"


def score_layout(pages, reference_pages, bitmap_boxes=None) -> dict:
    """bitmap_boxes는 정답 PDF 쪽마다 비트맵 그림 상자다. 그 안의 짝 없는 렌더 글자는 손실과 따로 센다."""
    totals = {"pages": len(pages), "reference_pages": len(reference_pages)}
    totals |= {"reference_chars": 0, "within": 0, "matched": 0, "excluded": 0, "offsets": []}
    totals |= {"bitmap_rendered": 0, "bitmap_letters": 0}
    for index, reference_chars in enumerate(reference_pages):
        # 정답 PDF처럼 효과 겹침(그림자 등 1pt 안 같은 글자)은 먼저 놓인 하나만 센다.
        rendered = (
            merge_effect_strikes([PlacedChar(c, x, y) for c, x, y in page_glyph_table(pages[index])])
            if index < len(pages)
            else []
        )
        boxes = [tuple(v / 100 for v in box) for box in pages[index].excluded] if index < len(pages) else []
        score = score_page(reference_chars, rendered, boxes)
        if bitmap_boxes and index < len(bitmap_boxes):
            in_bitmap = [rendered[b] for b in score.unpaired_rendered if _inside(rendered[b], bitmap_boxes[index])]
            totals["bitmap_rendered"] += len(in_bitmap)
            totals["bitmap_letters"] += len(letters([c.char for c in in_bitmap]))
        totals["excluded"] += score.excluded
        totals["reference_chars"] += score.reference_chars
        totals["within"] += score.within
        totals["matched"] += score.matched
        totals["offsets"].extend(score.offsets)
    # 렌더 쪽도 정답 PDF와 같이 효과 겹침을 한 글자로 센다(그림자 글자는 두 번 놓인다).
    rendered_pages = [
        merge_effect_strikes([PlacedChar(symbol_text(g.char), g.x / HU_PER_PT, g.y / HU_PER_PT) for g in page.glyphs])
        for page in pages
    ]
    order = order_match(
        [[c.char for c in page] for page in reference_pages], [[c.char for c in page] for page in rendered_pages]
    )
    totals |= {f"order_{key}": value for key, value in order.items()}
    return totals


def equation_oracle(entry, reference_pages) -> dict | None:
    """한글 2022 재저장본의 수식 크기를 옮겨 심어 다시 채점한다. 재저장본이 없으면 None."""
    resaved_path = RESAVE_ROOT / entry.path.relative_to(CORPUS_ROOT).with_suffix(".hwp")
    if not resaved_path.exists():
        return None
    document = HwpDocument.open(entry.path, entry.password)
    try:
        report = graft_equation_layout(document, HwpDocument.open(resaved_path))
    except ValueError as error:  # 재저장으로 레코드 구조가 바뀐 문서는 예외를 적용하지 않고 따로 센다.
        return {"structure_mismatch": str(error)}
    outcome = {"changed_equations": report.changed_equations, "blocking": len(report.blocking)}
    if not report.applicable:
        return outcome
    totals = score_layout(layout_document(document, RenderOptions.from_environment()), reference_pages)
    del totals["offsets"]
    return outcome | totals


def evaluate(entry) -> dict:
    reference = REFERENCE_ROOT / entry.path.relative_to(CORPUS_ROOT).with_suffix(".pdf")
    if not reference.exists():
        return {"status": "unchecked", "reason": "no reference PDF"}
    started = time.monotonic()
    try:
        document = HwpDocument.open(entry.path, entry.password)
        pages = layout_document(document, RenderOptions.from_environment())
    except Exception as error:  # 렌더링 실패는 실패로 센다(검사불가 아님).
        return {"status": "render-error", "reason": f"{type(error).__name__}: {error}"}
    reference_pages = pdf_pages(str(reference))
    totals = score_layout(pages, reference_pages, pdf_image_boxes(str(reference)))
    offsets = totals.pop("offsets")
    result = {
        "status": "ok",
        **totals,
        "median_offset_pt": round(statistics.median(offsets), 3) if offsets else None,
    }
    oracle = equation_oracle(entry, reference_pages)
    if oracle is not None:
        result["equation_oracle"] = oracle
    result["seconds"] = round(time.monotonic() - started, 2)
    return result


def _with_equation_sizes(result: dict) -> dict:
    """수식 엔진 예외를 적용한 결과: 옮겨 심기가 된 문서는 그 점수, 아니면 원래 점수."""
    oracle = result.get("equation_oracle")
    return oracle if oracle and "within" in oracle else result


#: 문서별 하락 목록에서 비교하는 칸: (이름, 분자, 분모). 비율이 이전 결과보다 낮아지면 하락으로 센다.
REGRESSION_RATIOS = (
    ("origin", "within", "reference_chars"),
    ("recall", "order_matched", "order_reference"),
    ("precision", "order_matched", "order_rendered"),
)


def regressions(results: dict, baseline: dict) -> list[str]:
    """이전 결과와 문서별로 맞대 나빠진 칸을 적는다. 전체 평균이 그대로여도 일부 문서가 나빠진 것을 잡는다."""
    lines = []
    for path, result in results.items():
        old = baseline.get(path)
        if old is None or old.get("status") != "ok":
            continue
        if result["status"] != "ok":
            lines.append(f"{path}: {result['status']}")
            continue
        changes = []
        if result["pages"] != old["pages"] and old["pages"] == old["reference_pages"]:
            changes.append(f"pages {old['pages']}->{result['pages']}/{result['reference_pages']}")
        for name, numerator, denominator in REGRESSION_RATIOS:
            if old.get(numerator) is None or result.get(numerator) is None:
                continue
            before = old[numerator] / old[denominator] if old[denominator] else 1.0
            after = result[numerator] / result[denominator] if result[denominator] else 1.0
            if after < before - 1e-9:
                changes.append(f"{name} {before:.3%}->{after:.3%} ({result[numerator] - old[numerator]:+d})")
        if changes:
            lines.append(f"{path}: " + ", ".join(changes))
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--match", default="")
    parser.add_argument("--details", action="store_true")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--jobs", type=int, default=1, help="문서를 나눠 맡을 프로세스 수")
    parser.add_argument("--baseline", type=Path, help="이전 --json 결과. 문서별로 나빠진 칸을 목록으로 낸다")
    args = parser.parse_args()
    entries = [entry for entry in load_corpus() if args.match in entry.path.name]
    if args.limit:
        entries = entries[: args.limit]
    results = {}
    with ProcessPoolExecutor(args.jobs) as pool:
        for entry, result in zip(entries, pool.map(evaluate, entries), strict=True):
            results[str(entry.path.relative_to(CORPUS_ROOT))] = result
            if args.details:
                print(entry.path.name, json.dumps(result, ensure_ascii=False), flush=True)
    checked = [r for r in results.values() if r["status"] == "ok"]
    unchecked = [r for r in results.values() if r["status"] == "unchecked"]
    errors = [r for r in results.values() if r["status"] == "render-error"]
    page_match = sum(r["pages"] == r["reference_pages"] for r in checked)
    page_total = len(checked) + len(errors)
    chars = sum(r["reference_chars"] for r in checked)
    within = sum(r["within"] for r in checked)
    excluded = sum(r["excluded"] for r in checked)
    print(
        f"documents: {len(results)}  checked: {len(checked)}  render-error: {len(errors)}  unchecked: {len(unchecked)}"
    )
    if page_total:
        print(f"page count match: {page_match}/{page_total} = {page_match / page_total:.3%}")
    if chars:
        print(f"glyph origin within {TOLERANCE_PT}pt: {within}/{chars} = {within / chars:.3%}")
        print(f"excluded (out-of-scope objects: equation, chart, OLE, text art): {excluded} reference chars")
    ordered = [r for r in checked if r.get("order_matched") is not None]
    if ordered:
        order_matched = sum(r["order_matched"] for r in ordered)
        order_reference = sum(r["order_reference"] for r in ordered)
        order_rendered = sum(r["order_rendered"] for r in ordered)
        print(
            f"order recall (letters, reading order): {order_matched}/{order_reference} = "
            f"{order_matched / order_reference:.3%}  precision: {order_matched}/{order_rendered} = "
            f"{order_matched / order_rendered:.3%}  unscorable: {len(checked) - len(ordered)}"
        )
        bitmap_letters = sum(r.get("bitmap_letters", 0) for r in ordered)
        bitmap_rendered = sum(r.get("bitmap_rendered", 0) for r in checked)
        print(
            f"rendered only inside reference bitmaps (Hangul saved those areas as images, counted apart from loss): "
            f"{bitmap_rendered} chars, {bitmap_letters} letters; precision without them: {order_matched}/"
            f"{order_rendered - bitmap_letters} = {order_matched / (order_rendered - bitmap_letters):.3%}"
        )
    oracles = [r["equation_oracle"] for r in checked if "equation_oracle" in r]
    applied = [o for o in oracles if "within" in o]
    if applied:
        adjusted = [_with_equation_sizes(r) for r in checked]
        adjusted_pages = sum(r["pages"] == r["reference_pages"] for r in adjusted)
        adjusted_chars = sum(r["reference_chars"] for r in adjusted)
        adjusted_within = sum(r["within"] for r in adjusted)
        compared = [o for o in oracles if "structure_mismatch" not in o]
        blocked = sum(1 for o in compared if o["changed_equations"] and o["blocking"])
        unchanged = sum(1 for o in compared if not o["changed_equations"])
        mismatched = len(oracles) - len(compared)
        print(
            f"equation-engine exception (Hangul 2022 resaved equation sizes): applied {len(applied)}, "
            f"blocked by non-equation changes {blocked}, no equation change {unchanged}, "
            f"record structure changed by resave {mismatched}"
        )
        print(f"  page count match: {adjusted_pages}/{page_total} = {adjusted_pages / page_total:.3%}")
        print(
            f"  glyph origin within {TOLERANCE_PT}pt: {adjusted_within}/{adjusted_chars} = "
            f"{adjusted_within / adjusted_chars:.3%} (excepted {adjusted_within - within:+d})"
        )
    for path, result in results.items():
        if result["status"] == "render-error":
            print(f"render-error: {path}: {result['reason']}")
    if args.baseline:
        dropped = regressions(results, json.loads(args.baseline.read_text(encoding="utf-8")))
        print(f"documents worse than baseline: {len(dropped)}")
        for line in dropped:
            print(f"  {line}")
    if args.json:
        args.json.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
