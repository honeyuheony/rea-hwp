"""수식 엔진이 쓰는 글꼴의 (가로 ppem, 세로 ppem) 쌍별 전진 폭 표를 Windows GDI 측정으로 만든다.

사용: uv run --with fonttools python tools/build_eq_glyph_metrics.py HYHWPEQ.TTF HancomEQN.ttf HBATANG.TTF [출력]
WSL에서 powershell.exe로 tools/gdi_char_widths.ps1을 부른다. 글꼴 파일은 Windows 경로로 열 수 있는 곳에 있어야 한다.
"""

from __future__ import annotations

import gzip
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from fontTools.ttLib import TTFont

#: 표 안 글꼴 이름(GDI 글꼴 이름). 한컴바탕(HBATANG.TTF)은 수식 글꼴에 없는 글자를 측정한다(IDS_EQ_LOG_FONTNAME).
FONT_KEYS = ("HYhwpEQ", "HancomEQN", "한컴바탕")
MAX_PPEM = 128
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "src/rea_hwp/render/data/eq_glyph_metrics.json.gz"
GDI_SCRIPT = Path(__file__).resolve().parent / "gdi_char_widths.ps1"
SURROGATE_SCRIPT = Path(__file__).resolve().parent / "gdi_surrogate_box.ps1"


def ppem_pairs() -> list[tuple[int, int]]:
    # 엔진 글꼴은 높이 = 크기, lfWidth = 크기/2(FUN_10011f10)이고 xAvgCharWidth = em/2라 가로 ppem = 2 x 폭 픽셀
    # 가로 픽셀이 0이 되는 작은 크기용으로 홀수 높이에도 (h, h)를 넣는다(glyphs.ppem 참고)
    pairs = []
    for h in range(1, MAX_PPEM + 1):
        for x in (h - 1, h, h + 1):
            if x >= 1 and (x == h or h % 2 == 1):
                pairs.append((x, h))
    return pairs


def windows_path(path: Path) -> str:
    return subprocess.run(["wslpath", "-w", str(path)], check=True, capture_output=True, text=True).stdout.strip()


def gdi_widths(font_file: Path, face: str, codes: list[int], pairs: list[tuple[int, int]]) -> dict[int, list[int]]:
    """GetCharWidth32W로 측정한 전진 폭(픽셀). FreeType 힌팅은 GDI와 1픽셀씩 다른 글자가 있다(HYhwpEQ U+E0B7)."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "codes.txt").write_text("".join(f"{c:X}\n" for c in codes))
        (work / "pairs.txt").write_text("".join(f"{x} {h}\n" for x, h in pairs))
        (work / "face.txt").write_text(face, encoding="utf-8")
        args = ["-FontFile", font_file, "-FaceFile", work / "face.txt", "-Codes", work / "codes.txt"]
        args += ["-Pairs", work / "pairs.txt", "-Out", work / "out.txt"]
        command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", windows_path(GDI_SCRIPT)]
        subprocess.run(command + [a if isinstance(a, str) else windows_path(a) for a in args], check=True)
        columns: dict[int, list[int]] = {code: [0] * len(pairs) for code in codes}
        pair_index = {pair: i for i, pair in enumerate(pairs)}
        for line in (work / "out.txt").read_text().splitlines():
            head, _, rest = line.partition(":")
            index = pair_index[tuple(map(int, head.split()))]
            for code, width in zip(codes, map(int, rest.split()), strict=True):
                columns[code][index] = width
    return columns


def hanja_surrogate_boxes(pairs: list[tuple[int, int]]) -> list[list[int]]:
    """쌍마다 함초롬바탕 단독 상위 대리 D840의 [폭, tmHeight + tmInternalLeading](픽셀)."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "pairs.txt").write_text("".join(f"{x} {h}\n" for x, h in pairs))
        command = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            windows_path(SURROGATE_SCRIPT),
        ]
        args = ["-Pairs", windows_path(work / "pairs.txt"), "-Out", windows_path(work / "out.txt")]
        subprocess.run(command + args, check=True)
        boxes = {}
        for line in (work / "out.txt").read_text().splitlines():
            head, _, rest = line.partition(":")
            boxes[tuple(map(int, head.split()))] = list(map(int, rest.split()))
    return [boxes[pair] for pair in pairs]


def font_table(path: Path, face: str, pairs: list[tuple[int, int]]) -> dict:
    """글자 코드를 연속 구간으로 묶는다: runs = [[첫 코드, 끝 코드, 폭 벡터 번호]], 폭 벡터는 pairs 순서."""
    codes = sorted(code for code in TTFont(str(path), fontNumber=0).getBestCmap() if code <= 0xFFFF)
    columns = gdi_widths(path, face, codes, pairs)
    vectors: list[list[int]] = []
    vector_index: dict[tuple[int, ...], int] = {}
    runs: list[list[int]] = []
    for code in codes:
        key = tuple(columns[code])
        if key not in vector_index:
            vector_index[key] = len(vectors)
            vectors.append(list(key))
        index = vector_index[key]
        if runs and runs[-1][1] == code - 1 and runs[-1][2] == index:
            runs[-1][1] = code
        else:
            runs.append([code, code, index])
    return {"runs": runs, "vectors": vectors}


def main() -> None:
    if len(sys.argv) < 4:
        sys.exit("사용: build_eq_glyph_metrics.py <HYHWPEQ.TTF> <HancomEQN.ttf> <HBATANG.TTF> [출력 .json.gz]")
    paths = [Path(p).resolve() for p in sys.argv[1:4]]
    target = Path(sys.argv[4]) if len(sys.argv) > 4 else DEFAULT_OUT
    pairs = ppem_pairs()
    table = {"pairs": [list(p) for p in pairs], "fonts": {}}
    for key, path in zip(FONT_KEYS, paths, strict=True):
        table["fonts"][key] = font_table(path, key, pairs)
    table["hanja_surrogate"] = hanja_surrogate_boxes(pairs)
    with gzip.open(target, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(table, ensure_ascii=False, separators=(",", ":")))
    print(target, target.stat().st_size)


if __name__ == "__main__":
    main()
