"""옛 수식 엔진이 쓰는 HFT 글꼴 폭 표, 언어별 글꼴 대체 사슬, 사슬 끝 TTF의 GDI 폭 표와
유니코드 → 한글 3.0 코드 대응표를 만든다.

사용: uv run --with fonttools python tools/build_legacy_eq_metrics.py <HOffice120 폴더> <batang.ttc> [출력]
바탕 폭은 WSL에서 powershell.exe로 tools/gdi_char_widths.ps1을 불러 측정한다.
"""

from __future__ import annotations

import gzip
import json
import struct
import sys
from pathlib import Path

from build_eq_glyph_metrics import gdi_widths
from fontTools.ttLib import TTFont

from rea_hwp.render.equation.legacy import LANGUAGES, char_language

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "src/rea_hwp/render/data/legacy_eq_metrics.json.gz"
#: 옛 엔진 글꼴 이름(HwpApp 0x10791f04). 모든 언어의 글꼴 이름이 이것이다.
EQUATION_FACE = "수식"
#: 사슬이 끝나도 글자가 없을 때 쓰는 계열. HncBaseDraw.dll 0xc0e94의 기본 글꼴 HGSMJ.HFT(한양신명조)와 같은 계열이다.
FALLBACK_FAMILY = "한양신명조"
#: GDI로 폭을 측정하는 TTF 글꼴. 사슬에서 그 뒤의 TTF 글꼴은 바탕에 없는 글자에만 쓰여 측정하지 않는다.
TTF_FACE = "바탕"
MAX_PPEM = 128


def ini_sections(path: Path) -> dict[str, list[str]]:
    """UTF-16 INI(hftinfo.dat·fontinfo.dat)를 [절] → 줄 목록으로 읽는다."""
    sections: dict[str, list[str]] = {}
    current: list[str] = []
    for line in path.read_text(encoding="utf-16").splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], [])
        elif line and not line.startswith(";"):
            current.append(line)
    return sections


def language_chains(font_db: Path) -> list[list[dict]]:
    """언어마다 글꼴 "수식"(HFT)에서 시작하는 대체 사슬(HncBaseDraw FUN_10055910).

    글꼴은 [Font Definition - 언어]의 "이름=보통,굵게,기울임,굵은기울임" 파일이고, 글자가 없으면
    HncDRGetSubstFontName이 [Subst Fonts - 언어]의 "이름,H=다음,H|T"를 따라간다. T는 TTF 글꼴이다.
    """
    hft = ini_sections(font_db / "hftinfo.dat")
    subst = ini_sections(font_db / "fontinfo.dat")
    chains = []
    for language in LANGUAGES:
        files = dict(line.split("=", 1) for line in hft[f"Font Definition - {language}"])
        links = dict(line.split("=", 1) for line in subst[f"Subst Fonts - {language}"])
        chain: list[dict] = []
        face, kind = EQUATION_FACE, "H"
        seen = set()
        while (face, kind) not in seen:
            seen.add((face, kind))
            if kind == "T":
                chain.append({"ttf": face})
            elif face in files:
                regular, _, italic = files[face].split(",")[:3]
                chain.append({"regular": regular.upper(), "italic": (italic or regular).upper()})
            following = links.get(f"{face},{kind}")
            if following is None:
                break
            face, kind = following.split(",")
        chains.append(chain)
    return chains


def ttf_widths(path: Path) -> dict:
    """사슬이 TTF까지 가는 언어의 글자 폭을 ppem 1~MAX_PPEM에서 측정한다. runs = [[첫 코드, 끝 코드, 폭 벡터 번호]]."""
    codes = [
        code
        for code in sorted(TTFont(str(path), fontNumber=0).getBestCmap())
        if code < 0xFF00 and char_language(code) in (1, 4, 5)
    ]
    # 쌍 (x, h)에서 x가 홀수면 lfWidth 0(gdi_char_widths.ps1). 옛 엔진 화면 글꼴은 높이만 준다
    pairs = [(1, h) for h in range(1, MAX_PPEM + 1)]
    columns = gdi_widths(path, TTF_FACE, codes, pairs)
    vectors: list[list[int]] = []
    vector_index: dict[tuple[int, ...], int] = {}
    runs: list[list[int]] = []
    for code in codes:
        key = tuple(columns[code])
        index = vector_index.setdefault(key, len(vectors))
        if index == len(vectors):
            vectors.append(list(key))
        if runs and runs[-1][1] == code - 1 and runs[-1][2] == index:
            runs[-1][1] = code
        else:
            runs.append([code, code, index])
    return {"face": TTF_FACE, "runs": runs, "vectors": vectors}


def family_name(head: bytes) -> str:
    return head[0x2A:0x4A].split(b"\0")[0][2:].decode("johab")


def width_ranges(data: bytes) -> list[list]:
    """0x200부터 [길이 u32][시작 u16][끝 u16][u16][폭...] 기록. 길이가 12면 폭 하나를 모든 글자에 쓴다."""
    offset, ranges = 0x200, []
    while offset + 12 <= len(data):
        size, first, last, _ = struct.unpack_from("<IHHH", data, offset)
        if size < 12 or first > last:
            break
        if size >= 10 + 2 * (last - first + 1):
            widths = list(struct.unpack_from(f"<{last - first + 1}H", data, offset + 10))
        else:
            widths = struct.unpack_from("<H", data, offset + 10)[0]
        ranges.append([first, last, widths])
        offset += size
    return ranges


def font_entry(path: Path) -> dict:
    data = path.read_bytes()
    em = struct.unpack_from("<H", data, 0x17A)[0]
    return {"file": path.name, "family": family_name(data), "em": em, "ranges": width_ranges(data)}


def table_body(path: Path) -> bytes:
    """u300_*.dat: 머리 줄들 뒤 「Data count」 줄 다음부터가 u16 배열이다(HncFoundation LoadUnicodeTbl)."""
    data = path.read_bytes()
    line = data.index(b"Data count")
    return data[data.index(b"\n", line) + 1 :]


def main() -> int:
    if len(sys.argv) < 3:
        sys.exit("사용: build_legacy_eq_metrics.py <HOffice120 폴더> <batang.ttc> [출력 .json.gz]")
    office = Path(sys.argv[1])
    batang = Path(sys.argv[2]).resolve()
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_OUT
    fonts_dir = office / "Shared/Fonts"
    chains = language_chains(office / "Bin/FontDB")
    entries = {path.name.upper(): font_entry(path) for path in sorted(fonts_dir.glob("*.HFT"))}
    fallback = [name for name, font in entries.items() if font["family"] == FALLBACK_FAMILY]
    used = {step[key] for chain in chains for step in chain for key in ("regular", "italic") if key in step}
    general = table_body(office / "Bin/Code/u300_gen.dat")
    pairs = table_body(office / "Bin/Code/u300_u2h.dat")
    tables = {
        # u300_gen.dat: 유니코드 0~0x3fff 순서의 한글 3.0 코드, 0x3441은 대응 없음(HncUnicodeToHCharBase)
        "general": list(struct.unpack_from("<16384H", general)),
        # u300_u2h.dat: (유니코드, 한글 3.0 코드) 쌍
        "pairs": list(struct.unpack_from(f"<{len(pairs) // 2}H", pairs)),
    }
    payload = {
        "fonts": {name: entries[name] for name in sorted(used | set(fallback))},
        "chains": chains,
        "fallback": fallback,
        "ttf": ttf_widths(batang),
        "codes": tables,
    }
    with gzip.open(out, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    print(out, chains)
    return 0


if __name__ == "__main__":
    sys.exit(main())
