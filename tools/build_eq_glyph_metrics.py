"""수식 엔진이 쓰는 글꼴의 (가로 ppem, 세로 ppem) 쌍별 힌팅 전진 폭 표를 만든다.

사용: uv run --with freetype-py python tools/build_eq_glyph_metrics.py HYHWPEQ.TTF HancomEQN.ttf HBATANG.TTF [출력]
"""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import freetype

#: 표 안 글꼴 이름. HBATANG.TTF(한컴바탕)는 수식 글꼴에 없는 글자를 측정하는 글꼴(리소스 IDS_EQ_LOG_FONTNAME)이다.
FONT_KEYS = ("HYhwpEQ", "HancomEQN", "한컴바탕")
MAX_PPEM = 128
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "src/rea_hwp/render/data/eq_glyph_metrics.json.gz"


def ppem_pairs() -> list[tuple[int, int]]:
    # 엔진 글꼴은 높이 = 크기, lfWidth = 크기/2(FUN_10011f10)이고 xAvgCharWidth = em/2라 가로 ppem = 2 x 폭 픽셀
    # 가로 픽셀이 0이 되는 작은 크기용으로 홀수 높이에도 (h, h)를 넣는다(glyphs.ppem 참고)
    pairs = []
    for h in range(1, MAX_PPEM + 1):
        for x in (h - 1, h, h + 1):
            if x >= 1 and (x == h or h % 2 == 1):
                pairs.append((x, h))
    return pairs


def font_table(path: Path, pairs: list[tuple[int, int]]) -> dict:
    """글자 코드를 연속 구간으로 묶는다: runs = [[첫 코드, 끝 코드, 폭 벡터 번호]], 폭 벡터는 pairs 순서."""
    face = freetype.Face(str(path))
    codes = sorted(code for code, _ in face.get_chars() if code <= 0xFFFF)
    columns: dict[int, list[int]] = {code: [] for code in codes}
    for x, h in pairs:
        face.set_pixel_sizes(x, h)
        for code in codes:
            face.load_char(code, freetype.FT_LOAD_DEFAULT)
            columns[code].append(face.glyph.advance.x // 64)
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
    paths = [Path(p) for p in sys.argv[1:4]]
    target = Path(sys.argv[4]) if len(sys.argv) > 4 else DEFAULT_OUT
    pairs = ppem_pairs()
    table = {"pairs": [list(p) for p in pairs], "fonts": {}}
    for key, path in zip(FONT_KEYS, paths, strict=True):
        table["fonts"][key] = font_table(path, pairs)
    with gzip.open(target, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(table, ensure_ascii=False, separators=(",", ":")))
    print(target, target.stat().st_size)


if __name__ == "__main__":
    main()
