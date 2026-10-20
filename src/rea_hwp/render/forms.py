"""양식 개체(명령 단추·선택 상자·라디오 단추·콤보 상자·입력란)의 글자를 그린다.

한글 2022 PDF는 양식 개체를 Windows 고전 컨트롤 모양으로 찍는다. 선택 상자·라디오 단추의 틀은 Marlett 글꼴
글자 여러 개를 한 자리에 겹쳐 찍고, 캡션·입력 글자는 양식의 글자 모양(CharShapeID)으로 찍는다.
"""

from __future__ import annotations

import re

from ..document import Node
from . import metrics
from .common import DEVICE_UNIT, PageState, device_round
from .scene import Glyph

#: 글자를 세로 가운데에 놓을 때 기준선이 개체 가운데보다 내려가는 양(글자 크기 비율).
#: 실측: form-01 명령 단추 Batang 10pt 기준선 = 위끝 + 높이/2 + 3.56pt, 콤보 상자 + 3.54pt
#: (rhwp form_appearance.rs 0.35em).
CENTERED_BASELINE = 0.36
#: 선택 상자·라디오 단추의 캡션이 개체 왼끝에서 떨어진 거리(캡션 글자 크기 비율).
#: 실측: form-01 10pt 13.00pt, form-002 10pt 12.87pt, basic__request 8.5pt 10.52pt.
CHECK_CAPTION_OFFSET = 1.3
#: 입력란·콤보 상자 글자의 왼쪽 안쪽 여백(HU). 고전 컨트롤 3px(96dpi) = 2.25pt. 실측: form-01 콤보 2.32pt,
#: basic__calendar_year 입력란 1.48pt.
EDIT_INSET = 225
#: Marlett 틀 글자의 크기(캡션 글자 크기 비율). 실측: Batang 10pt에서 10.49pt. 검증 안 됨: 8.5pt 글자에서는 8.25pt였다.
MARLETT_SCALE = 1.05
#: 선택 상자 틀(바탕·테두리 네 겹)과 선택 표시. 정답 PDF에 이 순서로 겹쳐 찍힌다.
CHECK_FRAME, CHECK_MARK = "gfedc", "b"
#: 라디오 단추 틀과 선택 표시.
RADIO_FRAME, RADIO_MARK = "nmlkj", "i"
#: Marlett 틀 글자별 색(고전 컨트롤 3D 색). 실측: form-01 정답 PDF 글자 색. 선택 표시(b·i)는 검정이다.
MARLETT_COLORS = {
    "g": "#ffffff", "f": "#e3e3e3", "e": "#696969", "d": "#ffffff", "c": "#a0a0a0",
    "n": "#ffffff", "m": "#e3e3e3", "l": "#696969", "k": "#ffffff", "j": "#a0a0a0",
}  # fmt: skip


def form_properties(text: str) -> dict[str, str]:
    """'CommonSet:set:218:Name:wstring:10:PushButton …' 꼴 속성 문자열을 이름 → 값으로 푼다.

    wstring 값은 길이 접두가 있어서 공백이 든 값도 그대로 읽는다. set은 안쪽 항목을 같은 사전에 펼친다.
    """
    values: dict[str, str] = {}
    i = 0
    while i < len(text):
        while i < len(text) and text[i] == " ":
            i += 1
        name_end = text.find(":", i)
        type_end = text.find(":", name_end + 1) if name_end >= 0 else -1
        if type_end < 0:
            break
        name, kind = text[i:name_end], text[name_end + 1 : type_end]
        if kind in ("set", "wstring"):
            length_end = text.find(":", type_end + 1)
            if length_end < 0 or not text[type_end + 1 : length_end].isdigit():
                break
            length = int(text[type_end + 1 : length_end])
            if kind == "set":
                i = length_end + 1
                continue
            values[name] = text[length_end + 1 : length_end + 1 + length]
            i = length_end + 1 + length
        else:
            value_end = text.find(" ", type_end + 1)
            value_end = len(text) if value_end < 0 else value_end
            values[name] = text[type_end + 1 : value_end]
            i = value_end
    return values


def draw_form(layout, state: PageState, form: Node, left: float, top: float, width: float, height: float) -> None:
    """양식 개체 상자(left, top, width, height) 안에 캡션·입력 글자와 Marlett 틀 글자를 놓는다."""
    props = form_properties(form.model.properties or "")
    if props.get("Printable") == "0":
        return
    kind = form.model.form_type_name.strip("+")
    shape_id = int(props.get("CharShapeID", "0") or 0)
    shapes = layout.styles.char_shapes
    shape = shapes[shape_id] if 0 <= shape_id < len(shapes) else shapes[0]
    size = shape.base_size
    center = top + height / 2
    baseline = center + CENTERED_BASELINE * size
    if kind == "pbt":
        text = caption_text(props.get("Caption", ""))
    elif kind in ("cbt", "rbt"):
        frame, mark = (CHECK_FRAME, CHECK_MARK) if kind == "cbt" else (RADIO_FRAME, RADIO_MARK)
        marks = frame + (mark if props.get("Value", "0") not in ("0", "") else "")
        marlett = size * MARLETT_SCALE
        for char in marks:
            color = MARLETT_COLORS.get(char, "#000000")
            state.page.items.append(Glyph(char, device_round(left), center + marlett / 2, marlett, "Marlett", color))
        text = caption_text(props.get("Caption", ""))
        x = left + CHECK_CAPTION_OFFSET * size
    elif kind in ("edt", "cob"):
        text = props.get("Text", "")
        x = left + EDIT_INSET
    else:
        return
    if kind == "pbt":
        x = left + (width - sum(ui_advance(layout, char, shape) for char in text)) / 2
    for char in text:
        slot = metrics.slot_of(char, metrics.HANGUL)
        if char.strip():
            layout.append_glyph(state, char, shape, slot, device_round(x), baseline)
        x += ui_advance(layout, char, shape)


def caption_text(caption: str) -> str:
    """Windows 컨트롤 캡션 규칙: '&&'는 '&' 하나로 찍고 홀로 선 '&'(바로 가기 표시)는 찍지 않는다.

    실측: form-002 캡션 'R&&D 자율성트랙(일반)'이 PDF에 'R&D 자율성트랙(일반)'으로 찍힌다.
    """
    return re.sub(r"&(&?)", lambda match: match.group(1), caption)


def ui_advance(layout, char: str, shape) -> int:
    """양식 글자의 전진폭(HU). 한글 조판 폭이 아니라 장치 점(600dpi) 단위로 맞춘 글꼴 크기와 글꼴 자체 폭을 쓴다.

    실측: form-01 Batang 10pt 캡션은 크기 9.96pt(83점), 한글 9.96pt·공백 3.36pt(28점) 간격으로 찍힌다.
    """
    slot = metrics.slot_of(char, metrics.HANGUL)
    pixels = round(shape.base_size * shape.rel_sizes[slot] / 100 / DEVICE_UNIT)
    metric = layout.measurer.database.find(layout.styles.face_name(shape, slot), shape.bold, shape.italic)
    width = metric.width(ord(char)) if metric is not None else None
    em = width / metric.em if width is not None else metrics.glyph_em(char, metric)
    return DEVICE_UNIT * round(em * pixels)
