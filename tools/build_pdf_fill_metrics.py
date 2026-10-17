"""정답 PDF 내장 글꼴 폭(/Widths·/W)으로 보충 파일을 만든다.

fonts: 폭 표에 이미 있는 글꼴의 빈 글자.
hft: 한글이 HFT 글꼴을 Type3 글꼴(T1, T2 …)로 찍은 글자의 폭(같은 이름 TrueType과 다름).

사용: uv run python tools/build_pdf_fill_metrics.py <build_pdf_font_metrics.py --cache 파일> <출력 .json.gz>
한 글자의 PDF 폭이 90% 이상 한 값으로 모일 때만 넣는다(fonts는 대체 폭과 다를 때만).
출력은 render/data/font_metrics_pdf_fill.json.gz에 둔다.
"""

from __future__ import annotations

import collections
import gzip
import json
import re
import sys
from pathlib import Path

from rea_hwp.render.metrics import _load, fallback_em, rhwp_database

#: 한 글자의 PDF 폭이 이 비율 이상 한 값일 때만 믿는다.
AGREEMENT = 0.9


def main() -> int:
    cache, output = Path(sys.argv[1]), Path(sys.argv[2])
    database = rhwp_database()
    database.add_pdf_supplement(_load("font_metrics_pdf.json.gz"))
    database.add_ttf_supplement(_load("font_metrics_ttf.json.gz"))
    widths: dict[str, dict[str, collections.Counter]] = collections.defaultdict(
        lambda: collections.defaultdict(collections.Counter)
    )
    hft: dict[str, dict[str, collections.Counter]] = collections.defaultdict(
        lambda: collections.defaultdict(collections.Counter)
    )
    for scanned in json.loads(cache.read_text(encoding="utf-8")):
        for use in scanned.get("uses", []):
            face, bold, italic = use["owner"]
            if not bold and not italic and use["width"] is not None and len(use["char"]) == 1:
                widths[face][use["char"]][use["width"]] += use["count"]
                if re.fullmatch(r"T\d+", use["pdf"]):
                    hft[face][use["char"]][use["width"]] += use["count"]
    fonts = []
    for face, table in sorted(widths.items()):
        metric = database.find(face)
        if metric is None:
            continue
        chosen = {}
        for char, counter in table.items():
            width, count = counter.most_common(1)[0]
            if metric.width(ord(char)) is None and count >= AGREEMENT * sum(counter.values()):
                if round(fallback_em(char) * 1000) != width:
                    chosen[str(ord(char))] = width
        if chosen:
            fonts.append({"name": face, "em": 1000, "widths": chosen})
    hft_fonts = []
    for face, table in sorted(hft.items()):
        chosen = {}
        for char, counter in table.items():
            width, count = counter.most_common(1)[0]
            if count >= AGREEMENT * sum(counter.values()) and not ("가" <= char <= "힣"):
                chosen[str(ord(char))] = width
        if chosen:
            hft_fonts.append({"name": face, "em": 1000, "widths": chosen})
    payload = {
        "source": "정답 PDF 내장 글꼴 /Widths (tools/build_pdf_font_metrics.py 정렬 결과)",
        "fonts": fonts,
        "hft": hft_fonts,
    }
    output.write_bytes(gzip.compress(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode(), mtime=0))
    for font in fonts:
        print(font["name"], {chr(int(c)): w for c, w in font["widths"].items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
