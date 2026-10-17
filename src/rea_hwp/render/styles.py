"""DocInfo 레코드를 ID로 찾는 표로 묶는다(ID는 각 종류 안에서 0부터 매긴 순번)."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..document import HwpDocument
from ..models.base import RecordModel
from ..models.docinfo import (
    BinData,
    BorderFill,
    Bullet,
    CharShape,
    FaceName,
    IdMappings,
    Numbering,
    ParaShape,
    Style,
    TabDef,
)


def colorref(value: int | None) -> str | None:
    """COLORREF(0x00BBGGRR) → #rrggbb. 상위 바이트가 켜져 있으면 색 없음으로 본다."""
    if value is None or value >> 24:
        return None
    return f"#{value & 0xFF:02x}{(value >> 8) & 0xFF:02x}{(value >> 16) & 0xFF:02x}"


@dataclass
class DocStyles:
    #: 언어 슬롯별 글꼴 목록(CHAR_SHAPE.face_ids[슬롯]이 이 목록의 순번).
    faces: list[list[FaceName]] = field(default_factory=lambda: [[] for _ in range(7)])
    char_shapes: list[CharShape] = field(default_factory=list)
    para_shapes: list[ParaShape] = field(default_factory=list)
    border_fills: list[BorderFill] = field(default_factory=list)
    tab_defs: list[TabDef] = field(default_factory=list)
    numberings: list[Numbering] = field(default_factory=list)
    bullets: list[Bullet] = field(default_factory=list)
    styles: list[Style] = field(default_factory=list)
    bin_data: list[BinData] = field(default_factory=list)

    @classmethod
    def from_document(cls, document: HwpDocument) -> DocStyles:
        resolved = cls()
        models: list[RecordModel] = [node.model for node in document.doc_info.walk()]
        mappings = next((m for m in models if isinstance(m, IdMappings)), None)
        face_counts = mappings.face_name_counts if mappings else [0] * 7
        face_list = [m for m in models if isinstance(m, FaceName)]
        start = 0
        for slot, count in enumerate(face_counts):
            resolved.faces[slot] = face_list[start : start + count]
            start += count
        typed = {
            CharShape: resolved.char_shapes,
            ParaShape: resolved.para_shapes,
            BorderFill: resolved.border_fills,
            TabDef: resolved.tab_defs,
            Numbering: resolved.numberings,
            Bullet: resolved.bullets,
            Style: resolved.styles,
            BinData: resolved.bin_data,
        }
        for model in models:
            target = typed.get(type(model))
            if target is not None:
                target.append(model)
        return resolved

    def face_name(self, char_shape: CharShape, slot: int) -> str:
        faces = self.faces[slot] or self.faces[0]
        index = char_shape.face_ids[slot]
        if 0 <= index < len(faces):
            return faces[index].name
        return self.faces[0][char_shape.face_ids[0]].name if self.faces[0] else ""

    def border_fill(self, one_based_id: int) -> BorderFill | None:
        """BORDER_FILL 참조는 1부터 센다(0은 없음)."""
        if 1 <= one_based_id <= len(self.border_fills):
            return self.border_fills[one_based_id - 1]
        return None
