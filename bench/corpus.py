"""~/tools/hwp-corpus/manifest.csv에서 HWP 5.0 문서 목록과 짝 PDF, 암호를 읽는다."""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from pathlib import Path

CORPUS_ROOT = Path(os.environ.get("REA_HWP_CORPUS", Path.home() / "tools" / "hwp-corpus"))


@dataclass(frozen=True)
class CorpusEntry:
    path: Path
    version: str
    password: str | None
    reference_pdf: Path | None


def load_corpus(root: Path = CORPUS_ROOT) -> list[CorpusEntry]:
    entries = []
    with open(root / "manifest.csv", encoding="utf-8") as manifest:
        for row in csv.DictReader(manifest):
            path = root / row["path"]
            if path.suffix.lower() != ".hwp" or row["path"].startswith("other/"):
                continue
            password = re.search(r"password-(\d+)", path.name)
            pdf = path.with_suffix(".pdf")
            entries.append(
                CorpusEntry(
                    path, row["hwp_version"], password.group(1) if password else None, pdf if pdf.exists() else None
                )
            )
    return entries
