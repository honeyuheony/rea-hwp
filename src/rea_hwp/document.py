"""HWP 5.0 문서를 레코드 트리와 타입 모델로 읽고, 같은 바이트로 되돌린다."""

from __future__ import annotations

import functools
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from . import models, tags
from .container import FileHeader, HwpContainer, Limits
from .errors import CorruptFileError, LimitExceededError
from .models.base import RawRecord, RecordModel
from .models.registry import DecodeContext, decode_record
from .record import Record, RecordStream, build_tree, parse_records
from .structure import BinDataItem, Paragraph, RecordView, read_paragraph, record_id

if TYPE_CHECKING:
    from .render.styles import DocStyles

#: TextLine.paragraph·paragraph_pages가 원본 위치를 붙이는 레코드.
_LOCATED_TAGS = frozenset({tags.PARA_HEADER, tags.TABLE, tags.SHAPE_COMPONENT_PICTURE})


@dataclass
class Node:
    record: Record
    model: RecordModel
    children: list[Node] = field(default_factory=list)

    @property
    def tag(self) -> int:
        return self.record.tag

    def find(self, tag: int) -> Node | None:
        return next((child for child in self.children if child.tag == tag), None)

    def find_all(self, tag: int) -> list[Node]:
        return [child for child in self.children if child.tag == tag]


@dataclass
class DecodedStream:
    name: str
    nodes: list[Node]
    stream: RecordStream

    def to_bytes(self) -> bytes:
        """모델을 다시 인코딩해 레코드 스트림을 만든다(원본 Record.payload는 쓰지 않는다)."""
        rebuilt = [
            Record(node.record.tag, node.record.level, node.model.encode(), node.record.extended_size)
            for node in self.walk()
        ]
        return RecordStream(rebuilt, self.stream.trailing).to_bytes()

    def walk(self):
        pending = list(reversed(self.nodes))
        while pending:
            node = pending.pop()
            yield node
            pending.extend(reversed(node.children))


def _ctrl_id_of(record: Record) -> int:
    if record.tag == tags.CTRL_HEADER and len(record.payload) >= 4:
        return struct.unpack_from("<I", record.payload, 0)[0]
    return 0


def decode_stream(name: str, stream_bytes: bytes, max_records: int | None = None) -> DecodedStream:
    stream = parse_records(stream_bytes, max_records)
    if stream.trailing:
        raise CorruptFileError(
            f"{name} ends with a truncated record ({len(stream.trailing)} bytes); the file is damaged"
        )
    roots = build_tree(stream.records)

    def convert(record: Record, ancestors: tuple[Record, ...], ctrl_id: int, preceding: frozenset[int]) -> Node:
        context = DecodeContext(ancestors, ctrl_id, preceding)
        node = Node(record, decode_record(record, context))
        own_ctrl = _ctrl_id_of(record) or ctrl_id
        # 새 태그 종류가 나올 때만 집합을 다시 만든다(형제가 많아도 형제 수에 비례하는 비용).
        seen: frozenset[int] = frozenset()
        for child in record.children:
            node.children.append(convert(child, ancestors + (record,), own_ctrl, seen))
            if child.tag not in seen:
                seen = seen | {child.tag}
        return node

    nodes = []
    seen_roots: frozenset[int] = frozenset()
    for root in roots:
        nodes.append(convert(root, (), 0, seen_roots))
        if root.tag not in seen_roots:
            seen_roots = seen_roots | {root.tag}
    return DecodedStream(name, nodes, stream)


class HwpDocument:
    def __init__(self, container: HwpContainer) -> None:
        models.load_all()
        self.container = container
        self.doc_info = self._decode("DocInfo", container.limits.max_records)
        remaining = container.limits.max_records - len(self.doc_info.stream.records)
        self.sections: list[DecodedStream] = []
        for name in container.section_names():
            self.sections.append(self._decode(name, remaining))
            remaining -= len(self.sections[-1].stream.records)

    def _decode(self, name: str, max_records: int) -> DecodedStream:
        stream_bytes = self.container.record_stream(name)
        try:
            return decode_stream(name, stream_bytes, max_records)
        except LimitExceededError as error:
            total = self.container.limits.max_records
            raise LimitExceededError(f"document has more than {total} records; raise Limits.max_records") from error
        except RecursionError as error:
            # level은 10비트라 1024단까지 겹칠 수 있다. 말뭉치 최대 12단이므로 그보다 깊으면 깨진 파일로 본다.
            raise CorruptFileError(f"{name} records are nested too deeply; the file is damaged") from error

    @classmethod
    def open(
        cls, source: str | Path | bytes, password: str | None = None, *, limits: Limits | None = None
    ) -> HwpDocument:
        """limits를 주지 않으면 Limits() 기본 상한을 쓴다."""
        return cls(HwpContainer.open(source, password, limits))

    @property
    def header(self) -> FileHeader:
        """FileHeader: version, compressed, password_protected, distributed, drm, encrypt_version."""
        return self.container.header

    @property
    def body_storage(self) -> str:
        """본문을 읽은 저장소 "BodyText" 또는 "ViewText". 배포용 비트가 켜진 문서만 ViewText다."""
        return self.container.body_storage

    @property
    def section_numbers(self) -> list[int]:
        """sections 차례대로의 구역 번호(본문 스트림 이름 SectionN의 N)."""
        return [_section_number(stream.name) for stream in self.sections]

    def section_records(self, section: int) -> list[RecordView]:
        """구역 번호 section의 레코드를 스트림에 나온 차례 그대로 담은 목록. 같은 문서에서는 같은 목록을 돌려준다."""
        cache = self.__dict__.setdefault("_section_records", {})
        if section not in cache:
            cache[section] = _record_views(self._section_stream(section))
        return cache[section]

    def docinfo_records(self) -> list[RecordView]:
        """DocInfo 레코드를 스트림에 나온 차례 그대로 담은 목록. index는 section_records와 같은 규칙으로 센다."""
        if "_docinfo_records" not in self.__dict__:
            self._docinfo_records = _record_views(self.doc_info)
        return self._docinfo_records

    def paragraph(self, section: int, index: int) -> Paragraph:
        """section_records(section)[index]가 PARA_HEADER인 문단의 글·글자 모양 구간·문단 모양·스타일."""
        return read_paragraph(self.section_records(section), section, index)

    @functools.cached_property
    def styles(self) -> DocStyles:
        """DocInfo를 종류별로 ID 차례대로 모은 목록(faces는 언어별). DocStyles 필드를 본다."""
        from .render.styles import DocStyles

        return DocStyles.from_document(self)

    def bin_data(self, bin_data_id: int) -> BinDataItem | None:
        """BinData ID(1부터, DocInfo BIN_DATA 차례)의 그림 바이트. 연결 그림이거나 스트림이 없으면 None."""
        from .render.raster import image_mime

        entries = self.styles.bin_data
        if not 1 <= bin_data_id <= len(entries):
            return None
        entry = entries[bin_data_id - 1]
        name = entry.storage_name
        if name is None or name not in self.container.raw_streams:
            return None
        data = self.container.bin_data(name, {0: None, 1: True, 2: False}.get(entry.compression))
        extension = (entry.values.get("extension") or "").lower()
        return BinDataItem(bin_data_id, extension, image_mime(data, name), data)

    def record_id(self, record: Record) -> str | None:
        """PARA_HEADER·TABLE·SHAPE_COMPONENT_PICTURE 레코드의 원본 위치 "구역:index". 다른 레코드는 None."""
        located = self.__dict__.get("_located_records")
        if located is None:
            located = {
                id(record): record_id(_section_number(stream.name), index)
                for stream in self.sections
                for index, record in enumerate(stream.stream.records)
                if record.tag in _LOCATED_TAGS
            }
            self._located_records = located
        return located.get(id(record))

    def _section_stream(self, section: int) -> DecodedStream:
        for stream in self.sections:
            if _section_number(stream.name) == section:
                return stream
        raise KeyError(f"no section {section}; available: {self.section_numbers}")

    @property
    def record_streams(self) -> list[DecodedStream]:
        return [self.doc_info, *self.sections]

    def raw_records(self) -> list[tuple[str, Node, RawRecord]]:
        """타입 모델로 해석하지 못하고 원본 바이트로 남긴 레코드 목록."""
        return [
            (stream.name, node, node.model)
            for stream in self.record_streams
            for node in stream.walk()
            if isinstance(node.model, RawRecord)
        ]


def _record_views(stream: DecodedStream) -> list[RecordView]:
    models_by_record = {id(node.record): node.model for node in stream.walk()}
    return [
        RecordView(index, record.tag, record.level, record.payload, models_by_record[id(record)])
        for index, record in enumerate(stream.stream.records)
    ]


def _section_number(stream_name: str) -> int:
    return int(re.fullmatch(r"(?:BodyText|ViewText)/Section(\d+)", stream_name).group(1))
