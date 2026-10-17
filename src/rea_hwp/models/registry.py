"""태그(와 부모 컨트롤)로 모델을 고르고, 다시 인코딩한 결과가 원본과 다르면 원본 바이트로 보관한다."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..record import Record
from .base import RawRecord, RecordModel


@dataclass(frozen=True)
class DecodeContext:
    #: 바깥쪽부터 차례로 놓은 조상 레코드.
    ancestors: tuple[Record, ...] = ()
    #: 가장 가까운 조상 CTRL_HEADER의 컨트롤 ID(없으면 0).
    ctrl_id: int = 0
    #: 같은 부모 아래에서 이 레코드보다 앞선 형제 레코드에 나온 태그 종류.
    preceding_sibling_tags: frozenset[int] = frozenset()


Selector = Callable[[DecodeContext, bytes], type[RecordModel] | None]
_SELECTORS: dict[int, list[Selector]] = {}


def register(tag: int, when: Callable[[DecodeContext, bytes], bool] | None = None):
    """모델 클래스를 태그에 등록한다. when이 있으면 그 조건을 만족할 때만 고른다(먼저 등록한 것이 우선)."""

    def wrap(model: type[RecordModel]) -> type[RecordModel]:
        def select(context: DecodeContext, payload: bytes) -> type[RecordModel] | None:
            return model if when is None or when(context, payload) else None

        _SELECTORS.setdefault(tag, []).append(select)
        return model

    return wrap


def model_for(tag: int, context: DecodeContext, payload: bytes) -> type[RecordModel] | None:
    for select in _SELECTORS.get(tag, []):
        chosen = select(context, payload)
        if chosen is not None:
            return chosen
    return None


def decode_record(record: Record, context: DecodeContext) -> RecordModel:
    model_class = model_for(record.tag, context, record.payload)
    if model_class is None:
        return RawRecord.keep(record.tag, record.payload, "no model")
    try:
        model = model_class.decode(record.payload)
    except (ValueError, IndexError, KeyError) as error:
        return RawRecord.keep(record.tag, record.payload, f"{model_class.__name__}: {error}")
    if model.encode() != record.payload:
        return RawRecord.keep(record.tag, record.payload, f"{model_class.__name__}: re-encode mismatch")
    return model
