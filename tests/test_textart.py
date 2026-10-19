import functools
import re
import struct
from pathlib import Path

import pytest

from rea_hwp import tags
from rea_hwp.document import HwpDocument
from rea_hwp.render.layout import layout_document
from rea_hwp.render.scene import Image
from rea_hwp.render.textart import (
    DEFAULT_FACE,
    WARP_SIZE,
    FontFiles,
    _circle_commands,
    _command_points,
    _load_font,
    draw_textart,
    envelope_points,
    parse_textart,
)

#: 한글 2022 설치본에 들어 있는 글맵시 25개 문서(코퍼스에는 글맵시가 없다).
PRESET = Path("/mnt/c/Program Files (x86)/HNC/Office 2022/HOffice120/Bin/Hwp/Toolbox/Preset/textart_preset.hwp")
needs_preset = pytest.mark.skipif(not PRESET.exists(), reason="Hangul 2022 installation not available")
FONT_DIRS = (
    Path("/mnt/c/Windows/Fonts"),
    Path("/mnt/c/Program Files (x86)/HNC/Office 2022/HOffice120/Shared/TTF"),
)


def _first_textart(document: HwpDocument):
    pending = [node for section in document.sections for node in section.nodes]
    while pending:
        node = pending.pop(0)
        if node.tag == tags.SHAPE_COMPONENT_TEXTART:
            return node
        pending[:0] = node.children
    return None


@needs_preset
def test_parse_textart_reads_text_font_and_shape_of_preset():
    record = parse_textart(_first_textart(HwpDocument.open(PRESET)).record.payload)

    assert (record.text, record.font_name, record.font_style) == ("ㄱㄴㄷㄹㅁ", "HY견고딕", "Regular")
    assert (record.shape, record.line_spacing, record.char_spacing) == (42, 120, 100)


def test_shape_zero_envelope_is_parallelogram():
    # 모양 0은 OWPML textShape 첫 값 PARALLELOGRAM이다: 외곽 표(0~1000)의 위 변이 오른쪽으로 300 밀려 있다.
    points = envelope_points(0, [])

    top = sorted(x for x, y in points if y == 0)
    bottom = sorted(x for x, y in points if y == 1000)
    assert (top[0], top[-1]) == (300, 1000)
    assert (bottom[0], bottom[-1]) == (0, 700)


@needs_preset
def test_preset_textarts_are_drawn_instead_of_excluded():
    pytest.importorskip("fontTools")
    pages = layout_document(HwpDocument.open(PRESET))

    drawn = [item for page in pages for item in page.items if isinstance(item, Image) and "svg" in item.href[:30]]
    assert len(drawn) == 25
    assert all(not page.excluded for page in pages)


@functools.cache
def _font_files() -> FontFiles:
    return FontFiles([d for d in FONT_DIRS if d.is_dir()])


def _with_fields(payload: bytes, shape: int, text: str | None = None, align: int | None = None) -> bytes:
    """레코드의 모양 번호(필요하면 글자·정렬도)만 바꾼 사본. 글자·글꼴 이름·글꼴 모양 문자열 뒤가 정수 필드다."""
    offset, parts = 32, []
    for _ in range(3):
        (length,) = struct.unpack_from("<H", payload, offset)
        parts.append(payload[offset : offset + 2 + 2 * length])
        offset += 2 + 2 * length
    if text is not None:
        parts[0] = struct.pack("<H", len(text)) + text.encode("utf-16-le")
    rest = bytearray(payload[offset:])
    struct.pack_into("<i", rest, 4, shape)
    if align is not None:
        struct.pack_into("<i", rest, 16, align)
    return payload[:32] + b"".join(parts) + bytes(rest)


def _bounds(points) -> tuple[float, float, float, float]:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), max(xs), min(ys), max(ys)


def _svg_bounds(path: str) -> tuple[float, float, float, float]:
    numbers = [float(v) for v in re.findall(r"-?\d+\.\d+", path)]
    return _bounds(list(zip(numbers[0::2], numbers[1::2], strict=True)))


#: 원 모양 시험 구현(HwpCircleAlgorithm 역분석 옮김) extent.py가 낸 조절점 범위 (x 최소, x 최대, y 최소, y 최대).
#: 한컴 정답 PDF와 대조한 값이 아니라 옮긴 식이 바뀌지 않았는지 보는 회귀 기준이다.
CIRCLE_EXTENTS = [
    (48, None, (1028.5, 13139.8, 2174.0, 13366.6)),
    (50, None, (1696.7, 12393.1, 1443.0, 12798.0)),
    (54, None, (1028.5, 13139.8, 2174.0, 13366.6)),
    (54, "첫째 줄\r둘째 줄\r셋째 줄", (368.0, 14001.7, -83.1, 14383.7)),
]


@needs_preset
@pytest.mark.parametrize(("shape", "text", "extent"), CIRCLE_EXTENTS)
def test_circle_textart_extent_matches_ported_engine(shape, text, extent):
    pytest.importorskip("fontTools")
    payload = _first_textart(HwpDocument.open(PRESET)).record.payload
    record = parse_textart(_with_fields(payload, shape, text))
    fonts = [_load_font(*_font_files().find(record.font_name)), _load_font(*_font_files().find(DEFAULT_FACE))]

    bounds = _bounds(_command_points(_circle_commands(record, fonts)))

    assert bounds == pytest.approx(extent, abs=0.05)


@needs_preset
def test_circle_textart_is_drawn_with_shadow_instead_of_unsupported():
    pytest.importorskip("fontTools")
    payload = _with_fields(_first_textart(HwpDocument.open(PRESET)).record.payload, 48)

    drawing = draw_textart(payload, WARP_SIZE, WARP_SIZE, _font_files().find)

    # 프리셋 첫 글맵시의 그림자 간격은 (1, 4)%다: 글자는 왼쪽 위, 그림자는 오른쪽 아래로 줄여 놓인다.
    assert _svg_bounds(drawing.path) == pytest.approx((0, WARP_SIZE * 0.99, 0, WARP_SIZE * 0.96), abs=0.01)
    assert _svg_bounds(drawing.shadow_path) == pytest.approx(
        (WARP_SIZE * 0.01, WARP_SIZE, WARP_SIZE * 0.04, WARP_SIZE), abs=0.01
    )


@needs_preset
def test_circle_textart_rejects_unported_alignment():
    pytest.importorskip("fontTools")
    payload = _with_fields(_first_textart(HwpDocument.open(PRESET)).record.payload, 48, align=3)

    with pytest.raises(NotImplementedError, match="정렬 3"):
        draw_textart(payload, WARP_SIZE, WARP_SIZE, _font_files().find)
