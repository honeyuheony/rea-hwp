"""정답 PDF에 내장된 글꼴의 폭과 한글 2022의 글꼴 대체를 모아 폭 표 보충 파일을 만든다.

사용: uv run --extra bench python tools/build_pdf_font_metrics.py <코퍼스 폴더> <출력 .json.gz> [--cache 파일]
기존 폭 표는 rhwp 표(font_metrics.json.gz)만 기준으로 삼는다. 출력은 render/data/font_metrics_pdf.json.gz에 둔다.
"""

from __future__ import annotations

import argparse
import collections
import difflib
import gzip
import json
import re
import sys
from multiprocessing import Pool
from pathlib import Path

import pymupdf

from rea_hwp.document import HwpDocument
from rea_hwp.render.layout import layout_document
from rea_hwp.render.metrics import rhwp_database
from rea_hwp.render.options import RenderOptions

#: 글꼴 이름이 다른 PDF 글꼴로 대체됐다고 볼 최소 비율·글자 수.
SUBSTITUTE_SHARE, SUBSTITUTE_MIN = 0.9, 100
_NUMBER = re.compile(rb"-?\d*\.?\d+")
_SUBSET = re.compile(r"^[A-Z]{6}\+")


def _pdf_family(name: str) -> str:
    """PDF 글꼴 이름에서 부분 집합 접두어를 떼고, CP949를 Latin-1로 잘못 읽은 이름은 되돌린다."""
    name = _SUBSET.sub("", name)
    try:
        return name.encode("latin-1").decode("cp949")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def _normalized(name: str) -> str:
    return re.sub(r"[\s_-]|PSMT|MT$", "", name).lower()


def _to_unicode(document: pymupdf.Document, xref: int) -> dict[int, str]:
    kind, value = document.xref_get_key(xref, "ToUnicode")
    if kind != "xref":
        return {}
    cmap = document.xref_stream(int(value.split()[0])).decode("latin-1")
    mapping: dict[int, str] = {}
    for block in re.findall(r"beginbfchar(.*?)endbfchar", cmap, re.S):
        for code, text in re.findall(r"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>", block):
            mapping[int(code, 16)] = bytes.fromhex(text).decode("utf-16-be", "replace")
    for block in re.findall(r"beginbfrange(.*?)endbfrange", cmap, re.S):
        for start, end, text in re.findall(r"<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>", block):
            base = int(text, 16)
            for offset in range(int(end, 16) - int(start, 16) + 1):
                mapping[int(start, 16) + offset] = chr(base + offset)
    return mapping


def _numbers(raw: str) -> list[float]:
    return [float(token) for token in _NUMBER.findall(raw.encode())]


def _resolve(document: pymupdf.Document, xref: int, key: str) -> str:
    kind, value = document.xref_get_key(xref, key)
    if kind == "xref":
        return document.xref_object(int(value.split()[0]))
    return value


def font_widths(document: pymupdf.Document, xref: int) -> dict[str, int]:
    """PDF 글꼴 하나의 유니코드 글자별 전진폭(1000분의 1 em)."""
    to_unicode = _to_unicode(document, xref)
    subtype = document.xref_get_key(xref, "Subtype")[1]
    widths: dict[int, float] = {}
    if subtype == "/Type0":
        descendant = int(re.search(r"(\d+) 0 R", document.xref_get_key(xref, "DescendantFonts")[1]).group(1))
        raw = _resolve(document, descendant, "W")
        # /W는 "c [w1 w2 …]"와 "c_first c_last w"가 섞인 배열이다.
        tokens = re.findall(r"\[|\]|-?\d*\.?\d+", raw.strip()[1:-1])
        index = 0
        while index < len(tokens):
            first = int(tokens[index])
            if tokens[index + 1] == "[":
                index += 2
                code = first
                while tokens[index] != "]":
                    widths[code] = float(tokens[index])
                    code += 1
                    index += 1
                index += 1
            else:
                last, width = int(tokens[index + 1]), float(tokens[index + 2])
                for code in range(first, last + 1):
                    widths[code] = width
                index += 3
        scale = 1.0
    else:
        first = int(document.xref_get_key(xref, "FirstChar")[1])
        values = _numbers(_resolve(document, xref, "Widths"))
        widths = {first + offset: value for offset, value in enumerate(values)}
        scale = 1.0
        if subtype == "/Type3":
            scale = _numbers(document.xref_get_key(xref, "FontMatrix")[1])[0] * 1000
    result = {}
    for code, width in widths.items():
        text = to_unicode.get(code)
        if text and len(text) == 1 and width > 0:
            result[text] = round(width * scale)
    return result


def scan(paths: tuple[Path, Path]) -> dict:
    """문서 하나: 우리 배치와 정답 PDF 글자를 맞대어 PDF 글꼴마다 어느 HWP 글꼴이 차지했는지 세고 폭을 읽는다."""
    hwp, pdf = paths
    try:
        pages = layout_document(HwpDocument.open(hwp), RenderOptions.from_environment())
        document = pymupdf.open(str(pdf))
    except Exception as error:  # 열리지 않는 문서는 건너뛰고 건수를 보고한다.
        return {"error": f"{hwp.name}: {type(error).__name__}"}
    # PDF 글꼴·글자마다 그 글자를 그린 HWP 글꼴(이름, 굵게, 기울임)을 센다.
    owners: dict[tuple[str, str], collections.Counter] = collections.defaultdict(collections.Counter)
    xrefs: dict[str, int] = {}
    for index, page in enumerate(document):
        for xref, _, _, basefont, _, _, *_ in page.get_fonts(full=True):
            xrefs.setdefault(_SUBSET.sub("", basefont), xref)
        if index >= len(pages):
            continue
        reference = []
        for block in page.get_text("rawdict")["blocks"]:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    reference.extend((char["c"], span["font"]) for char in span["chars"] if char["c"].strip())
        rendered = [glyph for glyph in pages[index].glyphs if glyph.char.strip()]
        matcher = difflib.SequenceMatcher(None, [c for c, _ in reference], [g.char for g in rendered], autojunk=False)
        for block in matcher.get_matching_blocks():
            for k in range(block.size):
                glyph = rendered[block.b + k]
                owners[reference[block.a + k]][(glyph.font_family, glyph.bold, glyph.italic)] += 1
    widths_by_font = {name: font_widths(document, xref) for name, xref in xrefs.items()}
    uses = []
    for (char, pdf_font), counter in owners.items():
        width = widths_by_font.get(pdf_font, {}).get(char)
        for owner, count in counter.items():
            uses.append({"pdf": pdf_font, "char": char, "owner": list(owner), "count": count, "width": width})
    return {"document": hwp.name, "uses": uses}


def _pairs(corpus: Path) -> list[tuple[Path, Path]]:
    pairs = []
    for row in (corpus / "manifest.csv").read_text(encoding="utf-8").splitlines()[1:]:
        relative = row.split(",", 1)[0]
        pdf = corpus / "ref2022" / Path(relative).with_suffix(".pdf")
        if pdf.exists():
            pairs.append((corpus / relative, pdf))
    return pairs


def build(scans: list[dict]) -> dict:
    database = rhwp_database()
    usage: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    widths: dict[tuple, dict[str, collections.Counter]] = collections.defaultdict(
        lambda: collections.defaultdict(collections.Counter)
    )
    sources: dict[tuple, set] = collections.defaultdict(set)
    for scanned in scans:
        for use in scanned.get("uses", []):
            face, bold, italic = use["owner"]
            usage[face][_pdf_family(use["pdf"])] += use["count"]
            if use["width"] is not None:
                widths[(face, bold, italic)][use["char"]][use["width"]] += use["count"]
                sources[(face, bold, italic)].add(scanned["document"])
    by_normalized = {_normalized(name): name for name in database.fonts}
    substitutes = {}
    for face, families in sorted(usage.items()):
        family, count = families.most_common(1)[0]
        total = sum(families.values())
        target = by_normalized.get(_normalized(family))
        own = database.find(face)
        if target is None or count < SUBSTITUTE_MIN or count / total < SUBSTITUTE_SHARE:
            continue
        if own is not None and _normalized(own.name) == _normalized(target):
            continue
        substitutes[face] = {"target": target, "chars": count, "share": round(count / total, 3)}
    fonts, agreement = [], {}
    for (face, bold, italic), table in sorted(widths.items()):
        chosen = {ord(char): counter.most_common(1)[0][0] for char, counter in table.items() if char != " "}
        known = database.find(face, bold, italic)
        # 폭 표에 이미 있는 글꼴은 그 표를 쓰고, PDF 폭과 맞는 비율만 검증 기록으로 남긴다.
        if face in substitutes or known is not None:
            if known is not None and face not in substitutes:
                same = sum(
                    1
                    for code, width in chosen.items()
                    if known.width(code) is not None and abs(known.width(code) * 1000 / known.em - width) <= 2
                )
                agreement[f"{face}/{int(bold)}{int(italic)}"] = [same, len(chosen)]
            continue
        conflicts = sum(1 for counter in table.values() if len(counter) > 1)
        hangul = collections.Counter(w for code, w in chosen.items() if 0xAC00 <= code <= 0xD7A3)
        fonts.append(
            {
                "name": face,
                "bold": bold,
                "italic": italic,
                "em": 1000,
                "widths": {str(code): width for code, width in sorted(chosen.items())},
                "hangul_default": hangul.most_common(1)[0][0] if hangul else None,
                "conflicts": conflicts,
                "documents": sorted(sources[(face, bold, italic)]),
            }
        )
    return {"substitutes": substitutes, "fonts": fonts, "agreement": agreement}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("corpus", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--cache", type=Path, help="문서별 정렬 결과를 저장·재사용할 JSON 파일")
    args = parser.parse_args()
    pairs = _pairs(args.corpus)
    if args.cache and args.cache.exists():
        scans = json.loads(args.cache.read_text(encoding="utf-8"))
    else:
        with Pool(args.jobs) as pool:
            scans = pool.map(scan, pairs, chunksize=1)
        if args.cache:
            args.cache.write_text(json.dumps(scans, ensure_ascii=False), encoding="utf-8")
    errors = [s["error"] for s in scans if "error" in s]
    result = build(scans)
    result["source"] = {
        "tool": "tools/build_pdf_font_metrics.py",
        "documents": len(scans),
        "unreadable": errors,
        "method": "글자 정렬로 PDF 글꼴의 주인 HWP 글꼴을 정하고 그 PDF 글꼴의 /Widths·/W를 ToUnicode로 읽음",
    }
    args.output.write_bytes(gzip.compress(json.dumps(result, ensure_ascii=False, sort_keys=True).encode("utf-8")))
    counts = {key: len(result[key]) for key in ("substitutes", "fonts")}
    print(f"documents {len(pairs)} unreadable {len(errors)} {counts}")
    for face, info in result["substitutes"].items():
        print(f"  substitute {face} -> {info['target']} ({info['chars']} chars, {info['share']})")
    for face, (same, total) in sorted(result["agreement"].items(), key=lambda kv: -kv[1][1])[:30]:
        print(f"  check {face}: PDF 폭과 기존 표 일치 {same}/{total}")
    for font in result["fonts"]:
        label = f"{font['name']}/{int(font['bold'])}{int(font['italic'])}"
        print(
            f"  font {label} chars={len(font['widths'])} conflicts={font['conflicts']} hangul={font['hangul_default']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
