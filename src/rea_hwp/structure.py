"""저장된 레코드 구조를 버전이 바뀌어도 뜻이 같은 공개 자료형으로 내놓는다.

여기 정의한 위치 세는 규칙(RecordView.index)과 원본 위치 문자열("구역:위치")은 공개한 뒤 바꾸지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import tags
from .errors import CorruptFileError
from .models.base import RecordModel
from .models.paragraph import ControlChar, ParaCharShape, ParaHeader, ParaText
from .models.shapes import ShapePicture


@dataclass(frozen=True)
class RecordView:
    """구역 스트림 또는 DocInfo 스트림의 레코드 하나.

    index는 구역 스트림(압축·암호를 푼 BodyText/SectionN, 배포용 문서는 ViewText/SectionN)이나 DocInfo 스트림의 레코드
    머리를 앞에서부터 차례로 읽은 평평한 목록에서 0부터 센 위치다. level로 트리를 짜기 전, 스트림에 나온 차례 그대로
    세며, 모델로 해석하지 못한 레코드도 하나로 센다. 이 규칙은 바꾸지 않는다.
    """

    index: int
    tag: int
    level: int
    #: 레코드 머리를 뺀 원본 바이트.
    payload: bytes
    #: 타입 모델. 모델이 없거나 해석 결과가 원본과 다르면 원본 바이트를 담은 RawRecord다.
    model: RecordModel


def subtree_end(records: list[RecordView], index: int) -> int:
    """records[index]의 하위 범위(뒤따르는 level이 더 큰 레코드들) 바로 다음 위치. 하위가 없으면 index + 1이다."""
    if not 0 <= index < len(records):
        raise IndexError(f"record index {index} is outside 0..{len(records) - 1}")
    level = records[index].level
    end = index + 1
    while end < len(records) and records[end].level > level:
        end += 1
    return end


def record_id(section: int, index: int) -> str:
    """원본 위치 문자열 "{구역 번호}:{index}". TextLine.paragraph와 paragraph_pages의 키가 이 모양이다."""
    return f"{section}:{index}"


@dataclass(frozen=True)
class Paragraph:
    """PARA_HEADER 하나와 그 바로 아래 PARA_TEXT·PARA_CHAR_SHAPE·CTRL_HEADER를 묶은 문단."""

    section: int
    #: PARA_HEADER의 RecordView.index.
    index: int
    para_shape_id: int
    style_id: int
    #: (WCHAR 위치, 글자 또는 ControlChar) 목록. 탭(9)과 확장 컨트롤은 8 WCHAR짜리 ControlChar 하나이고, 1 WCHAR
    #: 제어 문자(10 줄바꿈, 13 문단 끝, 24 하이픈, 30 묶음 빈칸, 31 고정폭 빈칸)는 글자 그대로 들어 있다.
    items: list[tuple[int, str | ControlChar]]
    #: (시작 WCHAR 위치, 글자 모양 ID) 목록. 다음 구간 시작 전까지 그 ID가 적용된다.
    char_shape_runs: list[tuple[int, int]]
    #: 확장 컨트롤 문자와 차례대로 짝을 이루는 CTRL_HEADER 레코드의 index 목록.
    control_indices: list[int]

    @property
    def id(self) -> str:
        return record_id(self.section, self.index)

    @property
    def text(self) -> str:
        """글자만 이은 문자열. 탭은 "\\t"로 넣고 다른 8 WCHAR 컨트롤은 뺀다."""
        return "".join(item if isinstance(item, str) else "\t" if item.code == 9 else "" for _, item in self.items)

    def char_shape_at(self, position: int) -> int:
        """WCHAR 위치에 적용되는 글자 모양 ID."""
        shape_id = self.char_shape_runs[0][1] if self.char_shape_runs else 0
        for start, run_shape in self.char_shape_runs:
            if start > position:
                break
            shape_id = run_shape
        return shape_id


def read_paragraph(records: list[RecordView], section: int, index: int) -> Paragraph:
    if not 0 <= index < len(records):
        raise IndexError(f"section {section} has records 0..{len(records) - 1}, not {index}")
    header = records[index]
    if header.tag != tags.PARA_HEADER or not isinstance(header.model, ParaHeader):
        raise ValueError(f"record {index} of section {section} is not a PARA_HEADER (tag {header.tag})")
    if header.model.para_shape_id is None or header.model.style_id is None:
        raise CorruptFileError(f"PARA_HEADER {section}:{index} is truncated; the file is damaged")
    items: list[tuple[int, str | ControlChar]] = []
    runs: list[tuple[int, int]] = []
    controls: list[int] = []
    for child in records[index + 1 : subtree_end(records, index)]:
        if child.level != header.level + 1:
            continue
        if child.tag == tags.PARA_TEXT and isinstance(child.model, ParaText):
            items = child.model.positioned()
        elif child.tag == tags.PARA_CHAR_SHAPE and isinstance(child.model, ParaCharShape):
            runs = list(child.model.runs)
        elif child.tag == tags.CTRL_HEADER:
            controls.append(child.index)
    return Paragraph(section, index, header.model.para_shape_id, header.model.style_id, items, runs, controls)


def picture_bin_data_id(record: RecordView) -> int:
    """그림 개체 레코드(SHAPE_COMPONENT_PICTURE)가 가리키는 BinData ID(1부터, DocInfo BIN_DATA 차례)."""
    if record.tag != tags.SHAPE_COMPONENT_PICTURE or not isinstance(record.model, ShapePicture):
        raise ValueError(f"record {record.index} is not a SHAPE_COMPONENT_PICTURE (tag {record.tag})")
    if record.model.bin_data_id is None:
        raise CorruptFileError(f"picture record {record.index} is truncated; the file is damaged")
    return record.model.bin_data_id


@dataclass(frozen=True)
class BinDataItem:
    """압축·암호를 푼 BinData 항목."""

    bin_data_id: int
    #: DocInfo BIN_DATA에 적힌 확장자(소문자). 적히지 않았으면 "".
    extension: str
    #: 머리 바이트로 정한 래스터 형식, 그 밖에는 확장자로 정한 mime.
    mime: str
    data: bytes = field(repr=False)
