import pytest
from corpus_paths import CORPUS_ROOT

from rea_hwp.document import HwpDocument
from rea_hwp.render.forms import caption_text, form_properties
from rea_hwp.render.layout import layout_document
from rea_hwp.render.svg import page_glyph_table

CORPUS = CORPUS_ROOT / "oss"


def glyph(page, char: str, below: float = 0.0) -> tuple[float, float]:
    found = next(g for g in page_glyph_table(page) if g[0] == char and g[2] > below)
    return found[1], found[2]


def test_form_properties_reads_length_prefixed_strings():
    text = (
        "CommonSet:set:30:Name:wstring:8:CheckBox Printable:bool:1  "
        "ButtonSet:set:40:Caption:wstring:5:선택 상자 Value:int:1  "
    )
    assert form_properties(text) == {"Name": "CheckBox", "Printable": "1", "Caption": "선택 상자", "Value": "1"}


def test_caption_ampersands_follow_windows_rule():
    assert caption_text("R&&D 자율성트랙") == "R&D 자율성트랙"
    assert caption_text("&File") == "File"


def test_form_captions_and_marlett_frames():
    page = layout_document(HwpDocument.open(CORPUS / "rhwp/form-01.hwp"))[0]
    # 한글 2022 PDF: 명령 단추 캡션 「명」 (98.88, 112.68), 선택 상자 「선」 (98.04, 154.56),
    # 선택 상자 Marlett 틀 (84.75, 155.79), 콤보 상자 「계」 (87.36, 193.68).
    for char, below, x, y in (
        ("명", 0, 98.88, 112.68),
        ("선", 140, 98.04, 154.56),
        ("g", 0, 84.75, 155.79),
        ("계", 0, 87.36, 193.68),
    ):
        gx, gy = glyph(page, char, below)
        assert gx == pytest.approx(x, abs=0.5)
        assert gy == pytest.approx(y, abs=0.5)
    chars = "".join(g[0] for g in page_glyph_table(page))
    # 값이 1인 선택 상자만 선택 표시 'b'를 더 찍는다. 라디오 단추(값 0)는 'i'가 없다.
    assert "gfedcb" in chars
    assert "nmlkj" in chars and "nmlkji" not in chars


@pytest.mark.skipif(not (CORPUS / "rhwp" / "80168_regulatory_analysis.hwp").exists(), reason="corpus not available")
def test_unmodified_click_here_guide_text_is_not_printed():
    pages = layout_document(HwpDocument.open(CORPUS / "rhwp" / "80168_regulatory_analysis.hwp"))

    # 한글 2022 PDF 28쪽에는 수정하지 않은 누름틀의 안내문 「적용이면 O표시, 미적용은 공란으로처리」가 없다.
    text = "".join(glyph.char for glyph in pages[27].glyphs)
    assert "적용이면" not in text
