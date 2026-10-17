"""rhwp(MIT)의 글꼴 폭 표와 글꼴 별칭 규칙을 JSON으로 옮긴다.

사용: uv run python tools/export_rhwp_metrics.py <rhwp 저장소> <출력 .json.gz>
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from pathlib import Path

_INT_ARRAY = re.compile(r"static\s+(\w+)\s*:\s*\[u(?:8|16)\s*;\s*\d+\]\s*=\s*\[(.*?)\];", re.S)
_RANGE_ARRAY = re.compile(r"static\s+(\w+)\s*:\s*\[LatinRange\s*;\s*\d+\]\s*=\s*\[(.*?)\];", re.S)
_RANGE = re.compile(r"LatinRange\s*\{\s*start:\s*(\w+),\s*end:\s*(\w+),\s*widths:\s*&(\w+),?\s*\}", re.S)
_HANGUL = re.compile(r"static\s+(\w+)\s*:\s*HangulMetric\s*=\s*HangulMetric\s*\{(.*?)\};", re.S)
_FIELD = re.compile(r"(\w+):\s*&?(\w+)")
_METRIC_LIST = re.compile(r"static\s+(\w+)\s*:\s*\[FontMetric\s*;\s*\d+\]\s*=\s*\[(.*)\];", re.S)
_METRIC = re.compile(
    r"FontMetric\s*\{\s*name:\s*\"((?:[^\"\\]|\\.)*)\",\s*bold:\s*(\w+),\s*italic:\s*(\w+),\s*"
    r"em_size:\s*(\w+),\s*latin_ranges:\s*&(\w+),\s*hangul:\s*(None|Some\(&(\w+)\)),?\s*\}",
    re.S,
)


def _strip_comments(source: str) -> str:
    return re.sub(r"//[^\n]*", "", source)


def _number(token: str) -> int:
    return int(token, 0)


def export(rhwp_root: Path) -> dict:
    renderer = rhwp_root / "src" / "renderer"
    sources = [
        _strip_comments((renderer / "font_metrics_generated.rs").read_text(encoding="utf-8")),
        _strip_comments((renderer / "font_metrics_overlays.rs").read_text(encoding="utf-8")),
    ]
    arrays: dict[str, list[int]] = {}
    ranges: dict[str, list] = {}
    hangul: dict[str, dict] = {}
    for source in sources:
        for name, body in _INT_ARRAY.findall(source):
            arrays[name] = [_number(token) for token in re.findall(r"0x[0-9A-Fa-f]+|\d+", body)]
    for source in sources:
        for name, body in _RANGE_ARRAY.findall(source):
            ranges[name] = [[_number(s), _number(e), arrays[w]] for s, e, w in _RANGE.findall(body)]
        for name, body in _HANGUL.findall(source):
            fields = dict(_FIELD.findall(body))
            hangul[name] = {
                "groups": [int(fields["cho_groups"]), int(fields["jung_groups"]), int(fields["jong_groups"])],
                "cho": arrays[fields["cho_map"]],
                "jung": arrays[fields["jung_map"]],
                "jong": arrays[fields["jong_map"]],
                "widths": arrays[fields["widths"]],
            }
    fonts = []
    # 생성 표 뒤에 측정 덮어쓰기 표를 잇는다. 조회는 이름별 첫 항목이 이긴다(rhwp font_metrics_data.rs).
    for source in sources:
        for _, body in _METRIC_LIST.findall(source):
            for name, bold, italic, em, latin, _, hangul_name in _METRIC.findall(body):
                fonts.append(
                    {
                        "name": json.loads(f'"{name}"'),
                        "bold": bold == "true",
                        "italic": italic == "true",
                        "em": _number(em),
                        "ranges": ranges[latin],
                        "hangul": hangul[hangul_name] if hangul_name else None,
                    }
                )
    registry = json.loads((rhwp_root / "assets" / "font-rules" / "font_rule_registry_v2.json").read_text("utf-8"))
    aliases = {"layout-metric": [], "layout-name": []}
    for rule in registry["rules"]:
        plane = rule["decisionPlane"]
        if plane in aliases and rule.get("status") == "active":
            aliases[plane].append(
                {"source": rule["sourceFace"], "target": rule["targetFaceOrPolicy"], "conditions": rule["conditions"]}
            )
    return {
        "source": "rhwp (MIT) src/renderer/font_metrics_*.rs, assets/font-rules",
        "fonts": fonts,
        "aliases": aliases,
    }


def main() -> int:
    rhwp_root, output = Path(sys.argv[1]), Path(sys.argv[2])
    exported = export(rhwp_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output, "wt", encoding="utf-8") as stream:
        json.dump(exported, stream, ensure_ascii=False, separators=(",", ":"))
    print(f"fonts={len(exported['fonts'])} aliases={ {k: len(v) for k, v in exported['aliases'].items()} }")
    return 0


if __name__ == "__main__":
    sys.exit(main())
