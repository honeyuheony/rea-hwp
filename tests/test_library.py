"""라이브러리로 쓰는 쪽이 기대는 약속: XML로 읽히는 SVG, 입력 상한, 오류 자료형, 공개 API."""

from __future__ import annotations

import base64
import collections
import hashlib
import io
import os
import re
import tracemalloc
import xml.etree.ElementTree as ET
import zipfile
import zlib

import pytest
from corpus_paths import CORPUS_ROOT
from ole_builder import build_ole, deflate, file_header, record

from rea_hwp import (
    CorruptFileError,
    DrmProtectedError,
    HwpDecryptError,
    HwpFormatError,
    LimitExceededError,
    Limits,
    PasswordRequiredError,
    ReaHwpError,
    UnsupportedFormatError,
    WrongPasswordError,
    tags,
)
from rea_hwp.binio import TruncatedPayload
from rea_hwp.container import HwpContainer
from rea_hwp.document import HwpDocument
from rea_hwp.render.html import text_layer
from rea_hwp.render.layout import layout_document
from rea_hwp.render.options import RenderOptions
from rea_hwp.render.scene import Glyph, Image, Page, TextLine
from rea_hwp.render.svg import page_to_svg

CORPUS = CORPUS_ROOT
needs_corpus = pytest.mark.skipif(not CORPUS.exists(), reason="corpus not available")
#: 문단 글에 C0 제어 문자(U+0018·U+001E·U+001F)가 있어 14ea115의 SVG나 글 층에 그대로 나가던 문서.
CONTROL_CHAR_DOCS = (
    "oss/rhwp/[2027] 온새미로 1 본교재.hwp",
    "oss/rhwp/hwpx__hancom-hwp__[2027] 온새미로 1 본교재.hwp",
    "oss/rhwp/task1725__text_footnote_tail_overpagination.hwp",
)
PASSWORD_DOC = CORPUS / "oss" / "rhwp" / "hwp3-sample16-hwp5-2024-password-123456.hwp"
SIMPLE_DOC = CORPUS / "oss" / "rhwp" / "basic__NewYear_s_Day.hwp"
C0_FORBIDDEN = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def test_svg_drops_xml_forbidden_control_chars_from_glyph_text():
    page = Page(width=10000, height=10000, number=1, items=[Glyph("\x18", 100, 200, 1000, "바탕\x01")])
    page.items.append(Glyph("가", 1100, 200, 1000, "바탕"))
    root = ET.fromstring(page_to_svg(page))
    texts = [element for element in root.iter() if element.tag.endswith("text")]
    assert [(element.text, element.get("font-family")) for element in texts] == [(None, "바탕"), ("가", "바탕")]


def test_text_layer_drops_control_chars_but_keeps_tab():
    layer = text_layer([TextLine("가\x18\t나\x0b", x=0, baseline=1000, width=100, size=1000)], 10000, 10000)
    assert ">가\t나</span>" in layer


def test_text_layer_copies_bound_and_fixed_width_spaces_as_spaces():
    layer = text_layer([TextLine("가\x1e나\x1f다", x=0, baseline=1000, width=100, size=1000)], 10000, 10000)
    assert ">가 나 다</span>" in layer


@needs_corpus
@pytest.mark.parametrize("relative", CONTROL_CHAR_DOCS)
def test_every_page_of_control_char_document_is_xml_clean(relative):
    pages = layout_document(HwpDocument.open(CORPUS / relative))
    for page in pages:
        ET.fromstring(page_to_svg(page))
        assert not C0_FORBIDDEN.search(text_layer(page.text_lines, page.width, page.height)), page.number
    assert len(pages) > 1


def _hwp(section: bytes = b"", docinfo: bytes = b"", flags: int = 0x01, extra: dict[str, bytes] | None = None) -> bytes:
    """압축 문서(flags bit0)면 DocInfo·본문을 deflate로 싼다. extra는 더 넣을 스트림이다."""
    docinfo = docinfo or record(16, 0, bytes(26))
    section = section or record(66, 0, bytes(24))
    pack = deflate if flags & 0x01 else bytes
    streams = {"FileHeader": file_header(flags), "DocInfo": pack(docinfo), "BodyText/Section0": pack(section)}
    streams |= extra or {}
    return build_ole(streams)


def _zero_bomb(size: int) -> bytes:
    compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
    chunk = bytes(1 << 20)
    return b"".join(compressor.compress(chunk) for _ in range(size >> 20)) + compressor.flush()


def test_zero_filled_deflate_bomb_stops_at_stream_limit_without_inflating_it_all():
    bomb = _zero_bomb(1 << 30)
    data = build_ole({"FileHeader": file_header(), "DocInfo": bomb, "BodyText/Section0": deflate(b"")})
    tracemalloc.start()
    try:
        with pytest.raises(LimitExceededError, match="record streams exceed 104857600 bytes"):
            HwpDocument.open(data)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # 1GiB를 다 풀지 않고 기본 상한 100MB 근처에서 멈춘다.
    assert peak < 160 * (1 << 20)


def test_zero_filled_section_stops_at_record_limit():
    data = _hwp(section=bytes(64 << 20))
    with pytest.raises(LimitExceededError, match="more than 1000000 records"):
        HwpDocument.open(data)


def test_record_limit_counts_docinfo_and_sections_together():
    section = record(66, 0, bytes(24)) * 600
    docinfo = record(16, 0, bytes(26)) * 500
    with pytest.raises(LimitExceededError):
        HwpDocument.open(_hwp(section, docinfo), limits=Limits(max_records=1000))
    assert (
        len(HwpDocument.open(_hwp(section, docinfo), limits=Limits(max_records=1100)).sections[0].stream.records) == 600
    )


def test_stream_limit_is_shared_by_docinfo_and_sections():
    # DocInfo 레코드 30바이트 + 본문 레코드 28바이트 × 120 = 3390바이트.
    section = record(66, 0, bytes(24)) * 120
    with pytest.raises(LimitExceededError, match="record streams exceed 3389 bytes"):
        HwpDocument.open(_hwp(section), limits=Limits(max_stream_bytes=3389))
    assert len(HwpDocument.open(_hwp(section), limits=Limits(max_stream_bytes=3390)).sections[0].stream.records) == 120


def test_uncompressed_streams_count_against_stream_limit():
    with pytest.raises(LimitExceededError):
        HwpDocument.open(_hwp(record(66, 0, bytes(5000)), flags=0), limits=Limits(max_stream_bytes=4000))


def test_bin_data_limit_applies_to_each_item_and_to_the_document_total():
    data = _hwp(extra={"BinData/BIN0001.png": deflate(bytes(3000)), "BinData/BIN0002.png": deflate(bytes(3000))})
    container = HwpContainer.open(data, limits=Limits(max_bin_data_bytes=2000))
    with pytest.raises(LimitExceededError, match="BIN0001"):
        container.bin_data("BinData/BIN0001.png")
    container = HwpContainer.open(data, limits=Limits(max_total_bin_data_bytes=5000))
    assert len(container.bin_data("BinData/BIN0001.png")) == 3000
    assert len(container.bin_data("BinData/BIN0001.png")) == 3000
    with pytest.raises(LimitExceededError, match="BIN0002"):
        container.bin_data("BinData/BIN0002.png")


def test_bin_data_bomb_stops_while_inflating():
    data = _hwp(extra={"BinData/BIN0001.bmp": _zero_bomb(256 << 20)})
    container = HwpContainer.open(data)
    tracemalloc.start()
    try:
        with pytest.raises(LimitExceededError):
            container.bin_data("BinData/BIN0001.bmp")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 100 * (1 << 20)


@needs_corpus
def test_default_limits_leave_corpus_document_with_most_records_readable():
    document = HwpDocument.open(CORPUS / "oss" / "hwplib" / "sample_hwp__big_file.hwp")
    assert sum(len(stream.stream.records) for stream in document.record_streams) == 639_058


@needs_corpus
def test_default_limits_leave_corpus_document_with_most_pictures_readable():
    document = HwpDocument.open(CORPUS / "oss" / "rhwp" / "exam_kor.hwp")
    entries = [node.model for node in document.doc_info.walk() if node.tag == tags.BIN_DATA]
    total = sum(
        len(document.container.bin_data(entry.storage_name, {0: None, 1: True, 2: False}[entry.compression]))
        for entry in entries
        if entry.storage_name in document.container.raw_streams
    )
    assert total == 181_354_217


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (b"HWP Document File V3.00 \x1a\x01\x02\x03\x04\x05" + bytes(100), UnsupportedFormatError),
        (b"plain text, not a document", UnsupportedFormatError),
        (build_ole({"WordDocument": bytes(600)}), UnsupportedFormatError),
        (build_ole({"FileHeader": b"Not an HWP header".ljust(256, b"\x00")}), UnsupportedFormatError),
        (
            build_ole({"FileHeader": file_header(version=(3, 0, 0, 0)), "DocInfo": b"", "BodyText/Section0": b""}),
            UnsupportedFormatError,
        ),
        (_hwp(flags=0x01 | 0x10), DrmProtectedError),
        (_hwp(flags=0x01 | 0x02), PasswordRequiredError),
        (build_ole({"FileHeader": file_header(), "BodyText/Section0": deflate(b"")}), CorruptFileError),
        (build_ole({"FileHeader": file_header(), "DocInfo": deflate(record(16, 0, bytes(26)))}), CorruptFileError),
        (_hwp(section=record(66, 0, bytes(24))[:-3]), CorruptFileError),
        (
            build_ole({"FileHeader": file_header(), "DocInfo": b"not deflate", "BodyText/Section0": b""}),
            CorruptFileError,
        ),
        (_hwp(section=b"".join(record(66, level, b"") for level in range(1024))), CorruptFileError),
    ],
    ids=[
        "hwp3",
        "text",
        "ole-without-fileheader",
        "bad-signature",
        "version-3-header",
        "drm",
        "password-missing",
        "docinfo-missing",
        "section-missing",
        "truncated-record",
        "broken-deflate",
        "nested-too-deep",
    ],
)
def test_open_raises_typed_error(data, kind):
    with pytest.raises(kind) as raised:
        HwpDocument.open(data)
    assert isinstance(raised.value, ReaHwpError)


def test_hwpx_zip_is_unsupported_format():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mimetype", "application/hwp+zip")
    with pytest.raises(UnsupportedFormatError):
        HwpDocument.open(buffer.getvalue())


def test_damaged_ole_sector_table_becomes_corrupt_file_error():
    data = bytearray(_hwp(extra={"BinData/BIN0001.png": bytes(9000)}))
    # 첫 FAT 섹터 번호(DIFAT[0])를 파일 밖으로 보낸다. olefile은 여기서 자기 예외를 낸다.
    data[76:80] = (0x7FFFFF00).to_bytes(4, "little")
    with pytest.raises(CorruptFileError, match="OLE container is damaged"):
        HwpDocument.open(bytes(data))


@needs_corpus
def test_wrong_password_is_reported_as_wrong_password():
    with pytest.raises(WrongPasswordError):
        HwpDocument.open(PASSWORD_DOC, "000000")
    with pytest.raises(PasswordRequiredError):
        HwpDocument.open(PASSWORD_DOC)
    assert HwpDocument.open(PASSWORD_DOC, "123456").sections


def test_legacy_error_names_still_catch_the_new_kinds():
    assert issubclass(UnsupportedFormatError, HwpFormatError)
    assert issubclass(DrmProtectedError, HwpFormatError)
    assert issubclass(CorruptFileError, HwpFormatError)
    assert issubclass(TruncatedPayload, CorruptFileError)
    assert issubclass(PasswordRequiredError, HwpDecryptError)
    assert issubclass(WrongPasswordError, HwpDecryptError)
    assert issubclass(HwpFormatError, ValueError) and issubclass(HwpDecryptError, ValueError)
    assert not issubclass(LimitExceededError, ValueError)
    from rea_hwp.container import HwpFormatError as container_name
    from rea_hwp.crypto import HwpDecryptError as crypto_name

    assert container_name is HwpFormatError and crypto_name is HwpDecryptError


def test_render_options_from_environment_reads_only_the_three_variables(tmp_path):
    assert RenderOptions.from_environment({}) == RenderOptions()
    environ = {
        "REA_HWP_TABLE_ENGINE": "0",
        "REA_HWP_ENGINE_RECOMPOSE": "0",
        "REA_HWP_FONT_DIRS": os.pathsep.join([str(tmp_path / "a"), "", str(tmp_path / "b")]),
    }
    assert RenderOptions.from_environment(environ) == RenderOptions(
        table_engine=False, engine_recompose=False, font_dirs=(tmp_path / "a", tmp_path / "b")
    )


def _data_uri(mime: str, data: bytes) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def test_image_href_receives_bytes_and_mime_once_per_picture_and_replaces_data_uris():
    raster, vector = b"\x89PNG fake", b"<svg xmlns='http://www.w3.org/2000/svg'/>"
    page = Page(width=10000, height=10000, number=1)
    page.items += [
        Image(0, 0, 100, 100, _data_uri("image/png", raster)),
        Image(200, 0, 100, 100, _data_uri("image/svg+xml", vector)),
        Image(400, 0, 100, 100, _data_uri("image/png", raster)),
        Image(600, 0, 100, 100, "already-stored.png"),
    ]
    calls = []

    def store(data: bytes, mime: str) -> str:
        calls.append((data, mime))
        return f"pic-{len(calls)}{'.svg' if mime == 'image/svg+xml' else '.png'}"

    svg = page_to_svg(page, image_href=store)
    assert calls == [(raster, "image/png"), (vector, "image/svg+xml")]
    hrefs = [element.get("href") for element in ET.fromstring(svg).iter() if element.tag.endswith("image")]
    assert hrefs == ["pic-1.png", "pic-2.svg", "pic-1.png", "already-stored.png"]
    assert "data:" not in svg


def test_page_to_svg_without_image_href_keeps_data_uris():
    page = Page(width=10000, height=10000, number=1, items=[Image(0, 0, 100, 100, _data_uri("image/png", b"x"))])
    assert _data_uri("image/png", b"x") in page_to_svg(page)


@needs_corpus
def test_header_picture_repeated_on_every_page_arrives_as_the_same_bytes():
    pages = layout_document(HwpDocument.open(CORPUS / "oss" / "rhwp" / "pic-in-head-01.hwp"))
    stored: dict[str, tuple[bytes, str]] = {}
    pages_by_digest: dict[str, set[int]] = collections.defaultdict(set)
    for page in pages:

        def store(data: bytes, mime: str, number: int = page.number) -> str:
            digest = hashlib.sha256(data).hexdigest()
            stored[digest] = (data, mime)
            pages_by_digest[digest].add(number)
            return f"{digest}.bin"

        svg = page_to_svg(page, image_href=store)
        assert "data:" not in svg
        ET.fromstring(svg)
    raster = [digest for digest, (_, mime) in stored.items() if mime == "image/png"]
    assert any(pages_by_digest[digest] == {page.number for page in pages} for digest in raster)
    assert {mime for _, mime in stored.values()} <= {"image/png", "image/jpeg", "image/gif", "image/svg+xml"}


@needs_corpus
@pytest.mark.parametrize(
    "relative", ["oss/rhwp/issue5875__nested_table_text_caption.hwp", "oss/rhwp/issue2217__20200830.hwp"]
)
def test_bmp_pictures_reach_svg_as_png(relative):
    document = HwpDocument.open(CORPUS / relative)
    stored_bmp = [item for item in map(document.bin_data, range(1, len(document.styles.bin_data) + 1)) if item]
    assert any(item.mime == "image/bmp" for item in stored_bmp)
    mimes = []
    for page in layout_document(document):
        page_to_svg(page, image_href=lambda data, mime: mimes.append(mime) or "x")
    assert "image/png" in mimes and "image/bmp" not in mimes
