"""설치된 TrueType 글꼴 파일의 실제 전진폭으로, 폭 표(rhwp 표·PDF 보충 표)에 없는 글자의 폭을 채운 보충 파일을 만든다.

사용: uv run python tools/build_ttf_font_metrics.py <출력 .json.gz> <글꼴 폴더>...
폭 표의 글꼴 이름과 TTF name 표의 패밀리 이름(영문·한글)이 같은 보통 굵기 파일만 쓴다.
출력은 render/data/font_metrics_ttf.json.gz에 둔다.
"""

from __future__ import annotations

import argparse
import gzip
import json
import struct
from pathlib import Path

from rea_hwp.render.metrics import _load, fallback_em, rhwp_database

#: 채우는 글자 범위. 한글 음절은 폭 표가 이미 갖고 있어 뺀다.
RANGES = [(0x00A0, 0x024F), (0x2000, 0x33FF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0xFF00, 0xFFEF)]


def _tables(data: bytes, offset: int) -> dict[str, tuple[int, int]]:
    count = struct.unpack_from(">H", data, offset + 4)[0]
    tables = {}
    for i in range(count):
        tag, _, start, length = struct.unpack_from(">4sIII", data, offset + 12 + 16 * i)
        tables[tag.decode("latin-1")] = (start, length)
    return tables


def _names(data: bytes, start: int) -> set[str]:
    """보통 굵기·바로 선 글꼴의 패밀리 이름들(영문·한글). 다른 스타일 파일이면 빈 집합."""
    _, count, strings = struct.unpack_from(">HHH", data, start)
    names, styles = set(), set()
    for i in range(count):
        platform, _, _, name_id, length, offset = struct.unpack_from(">6H", data, start + 6 + 12 * i)
        if name_id not in (1, 2) or platform != 3:
            continue
        text = data[start + strings + offset : start + strings + offset + length].decode("utf-16-be", errors="ignore")
        (names if name_id == 1 else styles).add(text)
    return names if styles & {"Regular", "Normal", "Book"} else set()


def _cmap(data: bytes, start: int) -> dict[int, int]:
    count = struct.unpack_from(">H", data, start + 2)[0]
    subtables = [struct.unpack_from(">HHI", data, start + 4 + 8 * i) for i in range(count)]
    for platform, _, offset in sorted(subtables, key=lambda s: (s[0] != 3, s[1] != 10)):
        base = start + offset
        fmt = struct.unpack_from(">H", data, base)[0]
        mapping: dict[int, int] = {}
        if fmt == 12:
            groups = struct.unpack_from(">I", data, base + 12)[0]
            for g in range(groups):
                first, last, glyph = struct.unpack_from(">III", data, base + 16 + 12 * g)
                for code in range(first, last + 1):
                    mapping[code] = glyph + code - first
            return mapping
        if fmt == 4 and platform == 3:
            segs = struct.unpack_from(">H", data, base + 6)[0] // 2
            ends = struct.unpack_from(f">{segs}H", data, base + 14)
            starts = struct.unpack_from(f">{segs}H", data, base + 16 + 2 * segs)
            deltas = struct.unpack_from(f">{segs}h", data, base + 16 + 4 * segs)
            range_base = base + 16 + 6 * segs
            offsets = struct.unpack_from(f">{segs}H", data, range_base)
            for s in range(segs):
                for code in range(starts[s], ends[s] + 1):
                    if code == 0xFFFF:
                        continue
                    if offsets[s] == 0:
                        glyph = (code + deltas[s]) & 0xFFFF
                    else:
                        where = range_base + 2 * s + offsets[s] + 2 * (code - starts[s])
                        glyph = struct.unpack_from(">H", data, where)[0]
                        glyph = (glyph + deltas[s]) & 0xFFFF if glyph else 0
                    if glyph:
                        mapping[code] = glyph
            return mapping
    return {}


def read_font(data: bytes, offset: int) -> tuple[set[str], int, dict[int, int]]:
    """(글꼴 이름들, em, 코드 → 전진폭)."""
    tables = _tables(data, offset)
    em = struct.unpack_from(">H", data, tables["head"][0] + 18)[0]
    metrics_count = struct.unpack_from(">H", data, tables["hhea"][0] + 34)[0]
    hmtx = tables["hmtx"][0]
    advances = struct.unpack_from(f">{metrics_count * 2}H", data, hmtx)[0::2]
    widths = {}
    for code, glyph in _cmap(data, tables["cmap"][0]).items():
        if glyph:
            widths[code] = advances[min(glyph, metrics_count - 1)]
    return _names(data, tables["name"][0]), em, widths


def fonts_in(path: Path):
    data = path.read_bytes()
    if data[:4] == b"ttcf":
        count = struct.unpack_from(">I", data, 8)[0]
        for i in range(count):
            yield read_font(data, struct.unpack_from(">I", data, 12 + 4 * i)[0])
    else:
        yield read_font(data, 0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output")
    parser.add_argument("folders", nargs="+")
    args = parser.parse_args()
    # 보충 표 자신은 빼고 rhwp 표와 PDF 보충 표만으로 빈 글자를 찾는다.
    database = rhwp_database()
    database.add_pdf_supplement(_load("font_metrics_pdf.json.gz"))
    out = []
    for folder in args.folders:
        for path in sorted(Path(folder).iterdir()):
            if path.suffix.lower() not in (".ttf", ".ttc"):
                continue
            try:
                fonts = list(fonts_in(path))
            except (struct.error, KeyError):
                continue
            for names, em, widths in fonts:
                for name in sorted(names & set(database.fonts)):
                    metric = database.find(name)
                    missing = {
                        str(code): width
                        for code, width in widths.items()
                        if any(s <= code <= e for s, e in RANGES)
                        and metric.width(code) is None
                        and round(fallback_em(chr(code)) * em) != width
                    }
                    if missing:
                        out.append({"name": name, "em": em, "file": path.name, "widths": missing})
    payload = {"source": "TrueType hmtx (Windows·한컴오피스 2022 글꼴 폴더)", "fonts": out}
    Path(args.output).write_bytes(gzip.compress(json.dumps(payload, ensure_ascii=False).encode(), mtime=0))
    for font in out:
        print(font["name"], font["file"], len(font["widths"]))


if __name__ == "__main__":
    main()
