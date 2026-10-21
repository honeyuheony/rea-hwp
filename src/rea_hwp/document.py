"""HWP 5.0 문서를 레코드 트리와 타입 모델로 읽고, 같은 바이트로 되돌린다."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from . import models, tags
from .container import HwpContainer, Limits
from .errors import CorruptFileError, LimitExceededError
from .models.base import RawRecord, RecordModel
from .models.registry import DecodeContext, decode_record
from .record import Record, RecordStream, build_tree, parse_records


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
