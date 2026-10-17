"""코퍼스 전체를 열어 무손실 왕복과 모델 해석률을 센다. 통과·실패·검사불가를 따로 보고한다."""

from __future__ import annotations

import collections
import sys

from corpus import load_corpus

from rea_hwp import tags
from rea_hwp.document import HwpDocument


def main() -> int:
    outcome = collections.Counter()
    raw_reasons = collections.Counter()
    problems = []
    for entry in load_corpus():
        try:
            document = HwpDocument.open(entry.path, entry.password)
        except Exception as error:  # 검사불가로 세고 다음 문서로 넘어간다.
            outcome["unchecked"] += 1
            problems.append(("unchecked", entry.path.name, f"{type(error).__name__}: {error}"))
            continue
        mismatched = [
            stream.name
            for stream in document.record_streams
            if stream.to_bytes() != document.container.record_stream(stream.name)
        ]
        if mismatched:
            outcome["fail"] += 1
            problems.append(("fail", entry.path.name, ", ".join(mismatched)))
        else:
            outcome["pass"] += 1
        for _, node, raw in document.raw_records():
            raw_reasons[(tags.TAG_NAMES.get(node.tag, hex(node.tag)), raw.reason.split(":")[0])] += 1
    print("roundtrip:", dict(outcome))
    print("raw records by (tag, reason):")
    for (tag_name, reason), count in raw_reasons.most_common():
        print(f"  {count:7d}  {tag_name:28s} {reason}")
    for kind, name, detail in problems:
        print(f"{kind}: {name}: {detail}")
    return 0 if outcome["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
