"""파싱 공개 API: 머리 정보, 구역별 평평한 레코드 목록, DocInfo 목록, 문단 글, 그림 바이트."""

from __future__ import annotations

import pickle
import struct

import olefile
import pytest
from corpus_paths import CORPUS_ROOT
from ole_builder import build_ole, deflate, file_header, record

from rea_hwp import (
    BinDataItem,
    ControlChar,
    CorruptEncryptedStreamError,
    CorruptFileError,
    HwpDocument,
    LimitExceededError,
    Limits,
    RecordView,
    picture_bin_data_id,
    subtree_end,
    tags,
)
from rea_hwp.document import MAX_NESTING
from rea_hwp.models.base import RawRecord
from rea_hwp.models.docinfo import IdMappings
from rea_hwp.record import parse_records

CORPUS = CORPUS_ROOT
needs_corpus = pytest.mark.skipif(not CORPUS.exists(), reason="corpus not available")
SIMPLE_DOC = CORPUS / "oss" / "rhwp" / "basic__NewYear_s_Day.hwp"
FOOTNOTE_DOC = CORPUS / "oss" / "rhwp" / "task1725__text_footnote_tail_overpagination.hwp"
#: 배포용 비트 없이 ViewText 저장소를 함께 가진 문서. BodyText를 읽어야 한다.
VIEWTEXT_WITHOUT_FLAG_DOC = CORPUS / "oss" / "rhwp" / "20250130-hongbo.hwp"
DISTRIBUTED_DOC = next(iter(sorted((CORPUS / "oss").rglob("*distribution*.hwp"))), None) if CORPUS.exists() else None


def _units(text: str) -> bytes:
    return text.encode("utf-16-le")


def _control(code: int, data: bytes) -> bytes:
    """8 WCHAR 컨트롤: 코드, 12바이트 값, 코드."""
    return struct.pack("<H", code) + data.ljust(12, b"\x00") + struct.pack("<H", code)


def _tab_and_footnote_document() -> HwpDocument:
    # 문단 0: 「가」 탭 「나」 각주 문단 끝. 탭 값 12바이트는 글자로 읽히면 안 되는 0x0041('A') 등으로 채운다.
    text = _units("가") + _control(9, b"A\x00B\x00C\x00D\x00E\x00F\x00") + _units("나")
    text += _control(17, tags.FOOTNOTE_CTRL.to_bytes(4, "little")) + _units("\r")
    header = struct.pack("<IIHBBHHHIH", 19, 0, 3, 2, 0, 2, 0, 0, 0, 0)
    char_shapes = struct.pack("<IIII", 0, 5, 9, 6)
    note_header = tags.FOOTNOTE_CTRL.to_bytes(4, "little") + bytes(4)
    inner = struct.pack("<IIHBBHHHIH", 2, 0, 0, 0, 0, 1, 0, 0, 0, 0)
    section = (
        record(tags.PARA_HEADER, 0, header)
        + record(tags.PARA_TEXT, 1, text)
        + record(tags.PARA_CHAR_SHAPE, 1, char_shapes)
        + record(tags.CTRL_HEADER, 1, note_header)
        + record(tags.LIST_HEADER, 2, bytes(8))
        + record(tags.PARA_HEADER, 2, inner)
        + record(tags.PARA_TEXT, 3, _units("주\r"))
    )
    docinfo = record(tags.DOCUMENT_PROPERTIES, 0, bytes(26))
    data = build_ole({"FileHeader": file_header(), "DocInfo": deflate(docinfo), "BodyText/Section0": deflate(section)})
    return HwpDocument.open(data)


def test_paragraph_keeps_tab_values_out_of_text():
    paragraph = _tab_and_footnote_document().paragraph(0, 0)
    assert [(position, item if isinstance(item, str) else item.code) for position, item in paragraph.items] == [
        (0, "가"),
        (1, 9),
        (9, "나"),
        (10, 17),
        (18, "\r"),
    ]
    assert paragraph.text == "가\t나\r"


def test_paragraph_pairs_footnote_control_with_its_ctrl_header_record():
    document = _tab_and_footnote_document()
    paragraph = document.paragraph(0, 0)
    footnote = paragraph.items[3][1]
    assert isinstance(footnote, ControlChar) and footnote.is_extended and footnote.ctrl_id == tags.FOOTNOTE_CTRL
    assert paragraph.control_indices == [3]
    assert document.section_records(0)[3].tag == tags.CTRL_HEADER
    inner = document.paragraph(0, 5)
    assert (inner.id, inner.text) == ("0:5", "주\r")


def test_paragraph_exposes_shape_ids_and_char_shape_runs():
    paragraph = _tab_and_footnote_document().paragraph(0, 0)
    assert (paragraph.para_shape_id, paragraph.style_id) == (3, 2)
    assert paragraph.char_shape_runs == [(0, 5), (9, 6)]
    assert [paragraph.char_shape_at(position) for position in (0, 1, 8, 9, 18)] == [5, 5, 5, 6, 6]


def test_paragraph_rejects_index_that_is_not_para_header():
    with pytest.raises(ValueError, match="not a PARA_HEADER"):
        _tab_and_footnote_document().paragraph(0, 1)


def test_subtree_end_stops_at_first_record_not_deeper():
    document = _tab_and_footnote_document()
    records = document.section_records(0)
    assert [subtree_end(records, index) for index in range(len(records))] == [7, 2, 3, 7, 5, 7, 7]


@needs_corpus
def test_section_records_follow_stream_order_and_count_raw_records():
    document = HwpDocument.open(SIMPLE_DOC)
    records = document.section_records(0)
    expected = parse_records(document.container.record_stream("BodyText/Section0")).records
    assert [(view.index, view.tag, view.level, view.payload) for view in records] == [
        (index, item.tag, item.level, item.payload) for index, item in enumerate(expected)
    ]
    assert all(isinstance(view, RecordView) and view.model is not None for view in records)
    assert document.section_records(0) is records


@needs_corpus
def test_section_records_model_is_typed_or_raw_bytes():
    document = HwpDocument.open(SIMPLE_DOC)
    for view in document.section_records(0):
        if isinstance(view.model, RawRecord):
            assert view.model.tail == view.payload
        else:
            assert view.model.encode() == view.payload


@needs_corpus
@pytest.mark.parametrize("path", [SIMPLE_DOC, FOOTNOTE_DOC, VIEWTEXT_WITHOUT_FLAG_DOC, DISTRIBUTED_DOC])
def test_docinfo_records_follow_doc_info_stream_order(path):
    document = HwpDocument.open(path)
    records = document.docinfo_records()
    assert [(view.index, view.tag, view.level, view.payload) for view in records] == [
        (index, item.tag, item.level, item.payload) for index, item in enumerate(document.doc_info.stream.records)
    ]
    models = [node.model for node in document.doc_info.walk()]
    assert all(view.model is model for view, model in zip(records, models, strict=True))
    assert records[0].tag == tags.DOCUMENT_PROPERTIES
    assert document.docinfo_records() is records


@needs_corpus
def test_real_footnote_paragraphs_point_at_footnote_ctrl_headers():
    document = HwpDocument.open(FOOTNOTE_DOC)
    records = document.section_records(0)
    checked = 0
    for view in records:
        if view.tag != tags.PARA_HEADER:
            continue
        paragraph = document.paragraph(0, view.index)
        extended = [item for _, item in paragraph.items if isinstance(item, ControlChar) and item.is_extended]
        for control, index in zip(extended, paragraph.control_indices, strict=False):
            assert records[index].tag == tags.CTRL_HEADER
            assert records[index].payload[:4] == control.ctrl_id.to_bytes(4, "little")
            checked += control.ctrl_id == tags.FOOTNOTE_CTRL
        positions = [position for position, _ in paragraph.items]
        for (position, item), following in zip(paragraph.items, positions[1:], strict=False):
            if isinstance(item, ControlChar):
                assert following == position + 8
    footnote_headers = [
        view
        for view in records
        if view.tag == tags.CTRL_HEADER and view.payload[:4] == tags.FOOTNOTE_CTRL.to_bytes(4, "little")
    ]
    assert checked == len(footnote_headers) > 0


@needs_corpus
def test_body_storage_is_view_text_only_when_distribution_bit_is_set():
    plain = HwpDocument.open(VIEWTEXT_WITHOUT_FLAG_DOC)
    assert any(name.startswith("ViewText/") for name in plain.container.raw_streams)
    assert (plain.header.distributed, plain.body_storage) == (False, "BodyText")
    assert all(stream.name.startswith("BodyText/") for stream in plain.sections)


@pytest.mark.skipif(DISTRIBUTED_DOC is None, reason="distribution sample not available")
def test_distributed_document_reads_view_text_sections():
    document = HwpDocument.open(DISTRIBUTED_DOC)
    assert (document.header.distributed, document.body_storage) == (True, "ViewText")
    assert document.section_numbers == list(range(len(document.sections)))
    assert document.section_records(0)[0].tag == tags.PARA_HEADER


@pytest.mark.skipif(DISTRIBUTED_DOC is None, reason="distribution sample not available")
def test_distributed_document_without_view_text_is_unreadable_not_empty():
    with olefile.OleFileIO(DISTRIBUTED_DOC) as ole:
        streams = {"/".join(path): ole.openstream(path).read() for path in ole.listdir(streams=True)}
    assert "BodyText/Section0" in streams
    stripped = {name: data for name, data in streams.items() if not name.startswith("ViewText/")}
    with pytest.raises(CorruptEncryptedStreamError, match="no ViewText/Section streams"):
        HwpDocument.open(build_ole(stripped))


def test_plain_document_without_body_text_stays_corrupt_file():
    docinfo = record(tags.DOCUMENT_PROPERTIES, 0, bytes(26))
    data = build_ole({"FileHeader": file_header(), "DocInfo": deflate(docinfo)})
    with pytest.raises(CorruptFileError, match="BodyText has no Section streams") as raised:
        HwpDocument.open(data)
    assert not isinstance(raised.value, CorruptEncryptedStreamError)


@needs_corpus
def test_header_exposes_file_header_flags():
    header = HwpDocument.open(SIMPLE_DOC).header
    assert (header.version, header.compressed, header.password_protected, header.distributed, header.drm) == (
        (5, 0, 3, 0),
        True,
        False,
        False,
        False,
    )


@needs_corpus
def test_styles_lists_match_id_mapping_counts():
    document = HwpDocument.open(FOOTNOTE_DOC)
    mappings = next(node.model for node in document.doc_info.walk() if isinstance(node.model, IdMappings))
    styles = document.styles
    assert [len(faces) for faces in styles.faces] == mappings.face_name_counts
    assert len(styles.char_shapes) == mappings.char_shape_count
    assert len(styles.para_shapes) == mappings.para_shape_count
    assert len(styles.border_fills) == mappings.border_fill_count
    assert len(styles.numberings) == mappings.numbering_count
    assert len(styles.bullets) == mappings.bullet_count
    assert len(styles.styles) == mappings.style_count
    assert len(styles.bin_data) == mappings.bin_data_count
    assert styles.styles[0].name == "Normal"


@needs_corpus
def test_bin_data_returns_inflated_bytes_and_format():
    document = HwpDocument.open(SIMPLE_DOC)
    item = document.bin_data(1)
    assert isinstance(item, BinDataItem)
    assert (item.bin_data_id, item.extension, item.mime, item.data[:6]) == (1, "gif", "image/gif", b"GIF89a")
    assert document.bin_data(0) is None
    assert document.bin_data(len(document.styles.bin_data) + 1) is None


@needs_corpus
def test_bin_data_obeys_picture_limit():
    document = HwpDocument.open(SIMPLE_DOC, limits=Limits(max_bin_data_bytes=100))
    with pytest.raises(LimitExceededError):
        document.bin_data(1)


@needs_corpus
def test_picture_records_name_bin_data_that_can_be_read():
    document = HwpDocument.open(SIMPLE_DOC)
    pictures = [view for view in document.section_records(0) if view.tag == tags.SHAPE_COMPONENT_PICTURE]
    ids = [picture_bin_data_id(view) for view in pictures]
    assert ids and all(document.bin_data(bin_id).data for bin_id in ids)
    with pytest.raises(ValueError, match="not a SHAPE_COMPONENT_PICTURE"):
        picture_bin_data_id(document.section_records(0)[0])


def test_paragraph_and_subtree_end_reject_positions_outside_the_section():
    document = _tab_and_footnote_document()
    with pytest.raises(IndexError):
        document.paragraph(0, -2)
    with pytest.raises(IndexError):
        document.paragraph(0, 7)
    with pytest.raises(IndexError):
        subtree_end(document.section_records(0), -1)


def _single_record_document(tag: int, payload: bytes) -> HwpDocument:
    section = record(tag, 0, payload)
    docinfo = record(tags.DOCUMENT_PROPERTIES, 0, bytes(26))
    return HwpDocument.open(
        build_ole({"FileHeader": file_header(), "DocInfo": deflate(docinfo), "BodyText/Section0": deflate(section)})
    )


def test_truncated_para_header_is_corrupt_file():
    with pytest.raises(CorruptFileError):
        _single_record_document(tags.PARA_HEADER, bytes(6)).paragraph(0, 0)


def test_truncated_picture_record_is_corrupt_file():
    document = _single_record_document(tags.SHAPE_COMPONENT_PICTURE, bytes(10))
    with pytest.raises(CorruptFileError):
        picture_bin_data_id(document.section_records(0)[0])


def test_record_models_are_decoded_when_first_read_and_shared_with_the_tree(monkeypatch):
    import rea_hwp.document as document_module

    calls = []
    original = document_module.decode_record
    monkeypatch.setattr(document_module, "decode_record", lambda *args: calls.append(args[0].tag) or original(*args))
    document = _tab_and_footnote_document()
    records = document.section_records(0)
    assert calls == []
    assert [view.tag for view in records[:2]] == [tags.PARA_HEADER, tags.PARA_TEXT] and calls == []
    text = records[1].model
    assert calls == [tags.PARA_TEXT]
    assert records[1].model is text and calls == [tags.PARA_TEXT]
    nodes = list(document.sections[0].walk())
    assert nodes[1].model is text
    assert all(view.model is node.model for view, node in zip(records, nodes, strict=True))
    assert len(calls) == len(records)


def test_record_view_with_deferred_model_pickles_and_compares_with_decoded_model():
    view = _tab_and_footnote_document().section_records(0)[1]
    restored = pickle.loads(pickle.dumps(view))
    assert restored == view and restored.model == view.model


def _nested_document(depth: int) -> bytes:
    section = b"".join(
        record(tags.PARA_HEADER if level == 0 else tags.CTRL_HEADER, level, bytes(4)) for level in range(depth)
    )
    docinfo = record(tags.DOCUMENT_PROPERTIES, 0, bytes(26))
    return build_ole({"FileHeader": file_header(), "DocInfo": deflate(docinfo), "BodyText/Section0": deflate(section)})


def test_records_nested_deeper_than_the_limit_are_rejected_when_opening():
    assert len(HwpDocument.open(_nested_document(MAX_NESTING + 1)).sections[0].nodes) == 1
    with pytest.raises(CorruptFileError, match="nested too deeply"):
        HwpDocument.open(_nested_document(MAX_NESTING + 2))
