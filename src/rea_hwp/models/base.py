"""레코드 payload를 필드 선언대로 해석하고, 남은 바이트를 그대로 되돌리는 모델 틀."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from ..binio import Reader, TruncatedPayload, Writer

#: 필드 형식: "u8"·"i32" 같은 정수, "u16*7" 같은 고정 길이 정수 배열, "wstr"(u16 길이 + UTF-16LE), "bytes:N".
FieldSpec = tuple[str, str]


def _read_field(reader: Reader, kind: str) -> Any:
    if kind == "wstr":
        return reader.wstr()
    if kind.startswith("bytes:"):
        return reader.bytes(int(kind[6:]))
    if "*" in kind:
        base, count = kind.split("*")
        return [reader.read(base) for _ in range(int(count))]
    return reader.read(kind)


def _write_field(writer: Writer, kind: str, value: Any) -> None:
    if kind == "wstr":
        writer.wstr(value)
    elif kind.startswith("bytes:"):
        writer.bytes(value)
    elif "*" in kind:
        base = kind.split("*")[0]
        for item in value:
            writer.write(base, item)
    else:
        writer.write(kind, value)


@dataclass
class RecordModel:
    """FIELDS를 앞에서부터 읽는다. 필드를 다 읽기 전에 payload가 끝나면 뒤 필드는 None이다(버전별 선택 필드)."""

    TAG: ClassVar[int] = -1
    FIELDS: ClassVar[list[FieldSpec]] = []

    values: dict[str, Any] = field(default_factory=dict)
    #: 선언한 필드 뒤에 남은 바이트. 명세에 없거나 아직 해석하지 않은 부분이다.
    tail: bytes = b""

    def __getattr__(self, name: str) -> Any:
        values = self.__dict__.get("values")
        if values is not None and name in values:
            return values[name]
        raise AttributeError(name)

    @classmethod
    def decode(cls, payload: bytes) -> RecordModel:
        model = cls()
        reader = Reader(payload)
        model.decode_fields(reader)
        model.tail = reader.rest()
        return model

    def decode_fields(self, reader: Reader) -> None:
        self.read_spec(reader, self.FIELDS)

    def read_spec(self, reader: Reader, spec: list[FieldSpec]) -> bool:
        """spec을 읽는다. 중간에 payload가 끝나면 그 필드부터 None으로 두고 False를 돌려준다."""
        for position, (name, kind) in enumerate(spec):
            start = reader.offset
            try:
                self.values[name] = _read_field(reader, kind)
            except TruncatedPayload:
                reader.offset = start
                for missing, _ in spec[position:]:
                    self.values[missing] = None
                return False
        return True

    def encode(self) -> bytes:
        writer = Writer()
        self.encode_fields(writer)
        writer.bytes(self.tail)
        return writer.getvalue()

    def encode_fields(self, writer: Writer) -> None:
        self.write_spec(writer, self.FIELDS)

    def write_spec(self, writer: Writer, spec: list[FieldSpec]) -> bool:
        for name, kind in spec:
            value = self.values.get(name)
            if value is None:
                return False
            _write_field(writer, kind, value)
        return True


@dataclass
class RawRecord(RecordModel):
    """해석하지 않거나 해석 결과가 원본과 달라 원본 바이트를 그대로 보관한 레코드."""

    tag: int = -1
    reason: str = ""

    @classmethod
    def keep(cls, tag: int, payload: bytes, reason: str) -> RawRecord:
        return cls(tail=payload, tag=tag, reason=reason)
