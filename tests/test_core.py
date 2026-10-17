from __future__ import annotations

import struct
from pathlib import Path

import pytest

from rea_hwp import crypto
from rea_hwp.cli import main as cli_main
from rea_hwp.container import HwpContainer, HwpFormatError
from rea_hwp.document import HwpDocument
from rea_hwp.record import Record, RecordStream, build_tree, parse_records

CORPUS = Path.home() / "tools" / "hwp-corpus"
SIMPLE_DOC = CORPUS / "oss" / "rhwp" / "basic__NewYear_s_Day.hwp"
DISTRIBUTED_DOC = next(iter(sorted((CORPUS / "oss").rglob("*distribution*.hwp"))), None) if CORPUS.exists() else None
PASSWORD_DOC = CORPUS / "oss" / "rhwp" / "hwp3-sample16-hwp5-2024-password-123456.hwp"
needs_corpus = pytest.mark.skipif(not SIMPLE_DOC.exists(), reason="corpus not available")


def test_record_header_roundtrip_keeps_extended_size_marker():
    small_extended = struct.pack("<II", 0x43 | (1 << 10) | (0xFFF << 20), 3) + b"abc"
    normal = struct.pack("<I", 0x42 | (2 << 20)) + b"xy"
    large = struct.pack("<II", 0x44 | (0xFFF << 20), 5000) + bytes(5000)
    stream = small_extended + normal + large + b"\x01\x02"
    parsed = parse_records(stream)
    assert [r.tag for r in parsed.records] == [0x43, 0x42, 0x44]
    assert parsed.records[0].extended_size
    assert parsed.trailing == b"\x01\x02"
    assert parsed.to_bytes() == stream


def test_build_tree_nests_by_level():
    records = [Record(66, 0, b""), Record(67, 1, b""), Record(71, 1, b""), Record(72, 2, b""), Record(66, 0, b"")]
    roots = build_tree(records)
    assert [r.tag for r in roots] == [66, 66]
    assert [c.tag for c in roots[0].children] == [67, 71]
    assert [c.tag for c in roots[0].children[1].children] == [72]
    assert RecordStream(records).to_bytes() == b"".join(r.header_bytes() for r in records)


def test_msvc_rand_matches_crt_sequence():
    rand = crypto._MsvcRand(1)
    assert [rand.next() for _ in range(3)] == [41, 18467, 6334]


def test_password_key_is_sha1_of_interleaved_password():
    assert len(crypto.password_key("123456")) == 16
    assert crypto.password_key("a") != crypto.password_key("b")


def test_non_ole_input_is_rejected():
    with pytest.raises(HwpFormatError):
        HwpContainer.open(b"HWP Document File V3.00" + bytes(100))


@needs_corpus
def test_document_roundtrip_is_byte_identical_and_fully_typed():
    document = HwpDocument.open(SIMPLE_DOC)
    for stream in document.record_streams:
        assert stream.to_bytes() == document.container.record_stream(stream.name)
    assert document.raw_records() == []


@pytest.mark.skipif(not PASSWORD_DOC.exists(), reason="password sample not available")
def test_password_document_decrypts_with_right_password_only():
    document = HwpDocument.open(PASSWORD_DOC, "123456")
    assert document.sections
    with pytest.raises(crypto.HwpDecryptError):
        HwpContainer.open(PASSWORD_DOC)


@pytest.mark.skipif(DISTRIBUTED_DOC is None, reason="distribution sample not available")
def test_distributed_document_reads_view_text():
    document = HwpDocument.open(DISTRIBUTED_DOC)
    assert document.container.header.distributed
    assert all(stream.name.startswith("ViewText/") for stream in document.sections)


@needs_corpus
def test_cli_render_writes_pages_and_viewer(tmp_path):
    assert cli_main(["render", str(SIMPLE_DOC), "-o", str(tmp_path), "--glyphs"]) == 0
    assert (tmp_path / "index.html").exists()
    assert sorted(p.name for p in tmp_path.glob("page-*.svg"))[0] == "page-0001.svg"
    assert (tmp_path / "glyphs.json").read_text(encoding="utf-8").startswith("[[")
