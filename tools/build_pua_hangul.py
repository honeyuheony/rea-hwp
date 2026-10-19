"""한글의 PUA 표시 대응표를 만든다: 옛한글 PUA → 첫가끝 자모(HncFoundation HncPUAStrToUniStr 0x10063580),
기호 영역 U+F000~F0FF → 글리프가 있는 대체 글꼴(HncBaseDraw FUN_10055910·FUN_10055820, fontinfo.dat 기본 글꼴 목록).

사용: uv run --with fonttools python tools/build_pua_hangul.py <HOffice120 폴더> <Windows Fonts 폴더> [출력]
"""

from __future__ import annotations

import gzip
import json
import re
import struct
import sys
from pathlib import Path

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "src/rea_hwp/render/data/pua_hangul.json.gz"
#: HncFoundation.dll(12.0.0.535)의 자모 표: 6바이트(초성, 중성, 종성 u16) × 150칸(HncPUAJamoToJamo 0x10063390).
JAMO_TABLE_VA = 0x100CB660
JAMO_ROWS = 150
#: PUA 옛한글 음절 구간 U+E0BC부터 0x15B3자. U+F000~F0FF(기호 글꼴 영역)는 빼고 그대로 둔다.
SYLLABLE_FIRST, SYLLABLE_COUNT = 0xE0BC, 0x15B3
SYMBOL_FONT_AREA = range(0xF000, 0xF100)
#: PUA 자모 구간: 초성 열 0x82칸, 중성 열 100칸, 종성 열 0x96칸이 차례로 이어진다.
JAMO_COLUMNS = ((0xF784, 0x82, 0), (0xF806, 100, 1), (0xF86A, 0x96, 2))


def pe_read(path: Path, va: int, size: int) -> bytes:
    """PE 파일의 가상 주소 va에서 size바이트를 읽는다."""
    data = path.read_bytes()
    header = struct.unpack_from("<I", data, 0x3C)[0]
    sections = struct.unpack_from("<H", data, header + 6)[0]
    optional = struct.unpack_from("<H", data, header + 20)[0]
    rva = va - struct.unpack_from("<I", data, header + 24 + 28)[0]
    for index in range(sections):
        _, virtual_size, address, raw_size, raw_pointer = struct.unpack_from(
            "<8sIIII", data, header + 24 + optional + 40 * index
        )
        if address <= rva < address + max(virtual_size, raw_size):
            offset = raw_pointer + rva - address
            return data[offset : offset + size]
    raise ValueError(f"{path.name}에 가상 주소 {va:#x}가 없다")


def pua_records(path: Path) -> list[tuple[int, int, int, int]]:
    """u300_pua.dat: U+E000부터 [한글 코드, 초성 번호, 중성 번호, 종성 번호] u16 레코드(LoadUnicodeTbl 0x10060de0)."""
    data = path.read_bytes()
    found = re.search(rb"Data count : (\d+) *\r\n", data)
    if found is None:
        raise ValueError(f"{path}: Data count 머리글이 없다")
    count = int(found.group(1))
    return [struct.unpack_from("<4H", data, found.end() + 8 * index) for index in range(count)]


#: HncDRGetCharLang 0x1006e390: U+F000~F0FF는 기호 언어로 분류된다.
SYMBOL_LANGUAGE_AREA = range(0xF000, 0xF100)
#: 한국어 시스템이 읽는 기본 글꼴 절과 기호 언어 줄(FUN_10070be0: [Default Fonts <언어 코드>]를 먼저 찾는다).
DEFAULT_FONTS_SECTION, SYMBOL_KEY = "[Default Fonts KOR]", "Symbol"


def default_symbol_fonts(fontinfo: Path) -> list[str]:
    data = fontinfo.read_bytes()
    # 설치본 fontinfo.dat는 BOM 있는 UTF-16이다.
    text = data.decode("utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else data.decode("cp949")
    section = None
    for line in text.splitlines():
        if line.startswith("["):
            section = line.strip()
        elif section == DEFAULT_FONTS_SECTION and line.startswith(SYMBOL_KEY + "="):
            # 「글꼴,T,글꼴,T,…」: 글꼴 이름과 종류(T = TrueType)가 번갈아 온다.
            return [name for name in line.split("=", 1)[1].split(",")[0::2] if name]
    raise ValueError(f"{fontinfo}: {DEFAULT_FONTS_SECTION} {SYMBOL_KEY} 줄이 없다")


def symbol_coverage(fonts_dir: Path) -> dict[str, set[int]]:
    """설치 글꼴 이름(모든 언어의 글꼴 계열·전체 이름)마다 기호 영역 안에 글리프가 있는 코드."""
    from fontTools.ttLib import TTCollection, TTFont

    coverage: dict[str, set[int]] = {}
    for path in sorted(fonts_dir.iterdir()):
        suffix = path.suffix.lower()
        if suffix not in (".ttf", ".otf", ".ttc"):
            continue
        fonts = TTCollection(str(path)).fonts if suffix == ".ttc" else [TTFont(str(path), lazy=True)]
        for font in fonts:
            codes: set[int] = set()
            for table in font["cmap"].tables:
                codes |= {code for code in table.cmap if code in SYMBOL_LANGUAGE_AREA}
            if not codes:
                continue
            for record in font["name"].names:
                if record.nameID in (1, 4):
                    coverage.setdefault(record.toUnicode(), set()).update(codes)
    return coverage


def symbol_fallbacks(office: Path, fonts_dir: Path) -> tuple[dict[str, str], dict[str, list[int]]]:
    """기호 영역 코드마다 기본 글꼴 목록에서 글리프가 있는 첫 글꼴과, 그 영역 글리프가 있는 글꼴별 코드."""
    order = default_symbol_fonts(office / "Bin/FontDB/fontinfo.dat")
    coverage = symbol_coverage(fonts_dir)
    fallbacks = {}
    for code in SYMBOL_LANGUAGE_AREA:
        face = next((name for name in order if code in coverage.get(name, ())), None)
        if face is not None:
            fallbacks[f"{code:X}"] = face
    return fallbacks, {name: sorted(codes) for name, codes in sorted(coverage.items())}


def build(office: Path) -> dict[str, str]:
    raw = pe_read(office / "Bin/HncFoundation.dll", JAMO_TABLE_VA, JAMO_ROWS * 6)
    jamo = [struct.unpack_from("<3H", raw, 6 * row) for row in range(JAMO_ROWS)]
    records = pua_records(office / "Bin/Code/u300_pua.dat")

    def column(first: int, size: int, which: int, code: int) -> int:
        return jamo[code - first][which] if first <= code < first + size else 0

    mapping: dict[str, str] = {}
    for code in range(SYLLABLE_FIRST, SYLLABLE_FIRST + SYLLABLE_COUNT):
        if code in SYMBOL_FONT_AREA:
            continue
        _, lead, vowel, tail = records[code - 0xE000]
        text = ""
        for (first, size, which), number in zip(JAMO_COLUMNS, (lead, vowel, tail), strict=True):
            converted = column(first, size, which, first + number)
            if not converted:
                # HncPUASyllableToPUAJamo는 [초, 중, 종, 0]을 wcscpy_s로 이어 붙여 첫 0에서 끝난다.
                break
            text += chr(converted)
        mapping[f"{code:X}"] = text
    for first, size, which in JAMO_COLUMNS:
        for code in range(first, first + size):
            converted = column(first, size, which, code)
            # 대응 값이 0인 자모는 변환 때 지워진다(HncPUAJamoToJamo).
            mapping[f"{code:X}"] = chr(converted) if converted else ""
    return mapping


def main() -> int:
    office, fonts_dir = Path(sys.argv[1]), Path(sys.argv[2])
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_OUT
    jamo = build(office)
    fallbacks, coverage = symbol_fallbacks(office, fonts_dir)
    table = {"jamo": jamo, "symbol_fallbacks": fallbacks, "symbol_coverage": coverage}
    out.write_bytes(gzip.compress(json.dumps(table, ensure_ascii=False, sort_keys=True).encode("utf-8"), mtime=0))
    print(f"{out}: jamo {len(jamo)}, symbol fallbacks {len(fallbacks)} {sorted(set(fallbacks.values()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
