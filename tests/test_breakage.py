from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

from rea_hwp.render.scene import Glyph

BENCH = Path(__file__).resolve().parent.parent / "bench"


@pytest.fixture
def breakage(monkeypatch, tmp_path):
    # bench/corpus.py는 들여올 때 REA_HWP_CORPUS를 요구한다. 판정 함수 시험은 말뭉치를 읽지 않는다.
    monkeypatch.setenv("REA_HWP_CORPUS", os.environ.get("REA_HWP_CORPUS") or str(tmp_path))
    monkeypatch.syspath_prepend(str(BENCH))
    return importlib.import_module("breakage")


def _line(text: str, x: float, y: float, size: float = 10.0) -> list[tuple[str, float, float, float]]:
    return [(char, x + index * size, y, size) for index, char in enumerate(text)]


def _page(breakage, reference_rows, rendered_rows, owners=None, rules=(), reference_rules=(), width=600.0):
    reference = [breakage.PlacedChar(c, x, y) for c, x, y, _ in reference_rows]
    rendered = [breakage.PlacedChar(c, x, y) for c, x, y, _ in rendered_rows]
    glyphs = [Glyph(c, x * 100, y * 100, size * 100, "바탕", advance=size * 100) for c, x, y, size in rendered_rows]
    return breakage.PageInput(
        width=width,
        height=800.0,
        reference=reference,
        rendered=rendered,
        glyphs=glyphs,
        owners=owners if owners is not None else [round(y) for _, _, y, _ in rendered_rows],
        rules=list(rules),
        images=[],
        reference_images=[],
        drawn_as_picture=[],
        reference_rules=list(reference_rules),
    )


def test_same_page_is_not_broken(breakage):
    rows = _line("가나다라마", 50, 100) + _line("바사아자차", 50, 120)
    assert breakage.judge_page(_page(breakage, rows, rows)) == []


def _breaks(events) -> list[tuple[str, float]]:
    return [(event["kind"], event["line"]) for event in events if event["kind"] != "꼬리 밀림"]


def test_vertical_shift_counts_only_over_threshold(breakage):
    texts = ("가나다라마", "바사아자차", "카타파하거")
    reference = [row for n, text in enumerate(texts) for row in _line(text, 50, 100 + 20 * n)]
    below = breakage.VERTICAL_SHIFT_PT - 1
    over = breakage.VERTICAL_SHIFT_PT + 1
    near = [row for n, text in enumerate(texts) for row in _line(text, 50, 100 + 20 * n + (below if n else 0))]
    far = [row for n, text in enumerate(texts) for row in _line(text, 50, 100 + 20 * n + (over if n else 0))]

    assert breakage.judge_page(_page(breakage, reference, near)) == []
    assert _breaks(breakage.judge_page(_page(breakage, reference, far))) == [("세로 밀림", 1)]


def test_sideways_offset_with_small_vertical_offset_is_not_vertical_shift(breakage):
    texts = ("가나다라마", "바사아자차", "카타파하거")
    reference = [row for n, text in enumerate(texts) for row in _line(text, 50, 100 + 20 * n)]
    # 대각선으로 6pt 넘게 옮겨 제자리는 아니지만 세로 차(4.5pt)는 문턱 아래다.
    moved = [row for n, text in enumerate(texts) for row in _line(text, 54, 104.5 + 20 * n)]

    assert _breaks(breakage.judge_page(_page(breakage, reference, moved))) == []


def test_only_bottom_line_shifted_is_tail_shift_not_break(breakage):
    reference = _line("가나다라마", 50, 100) + _line("바사아자차", 50, 120) + _line("쪽번호", 250, 700)
    rendered = _line("가나다라마", 50, 100) + _line("바사아자차", 50, 120) + _line("쪽번호", 250, 708)

    events = breakage.judge_page(_page(breakage, reference, rendered))

    assert [(event["kind"], event["reference_text"]) for event in events] == [("꼬리 밀림", "쪽번호")]


def test_shift_in_one_column_is_not_hidden_by_other_column(breakage):
    left, right = (
        ("가나다라마", "바사아자차", "카타파하거", "너더러머버"),
        ("서어저처커", "터퍼허고노", "도로모보소", "오조초코토"),
    )
    reference = [row for n in range(4) for row in _line(left[n], 50, 100 + 20 * n) + _line(right[n], 300, 100 + 20 * n)]
    # 왼쪽 단 둘째 줄부터 10pt 아래로 밀리고 오른쪽 단은 그대로다. 같은 기준선의 두 단은 정답 줄 하나로 묶인다.
    rendered = [
        row
        for n in range(4)
        for row in _line(left[n], 50, 100 + 20 * n + (10 if n else 0)) + _line(right[n], 300, 100 + 20 * n)
    ]

    events = breakage.judge_page(_page(breakage, reference, rendered))

    assert ("세로 밀림", "바사아자차") in [(event["kind"], event["reference_text"]) for event in events]


def test_missing_line_is_reported_with_its_text(breakage):
    reference = _line("가나다라마", 50, 100) + _line("바사아자차", 50, 120)
    rendered = _line("가나다라마", 50, 100)

    events = breakage.judge_page(_page(breakage, reference, rendered))

    assert [(event["kind"], event["line"]) for event in events] == [("빠짐", 1)]
    assert "바사아자차" in events[0]["detail"]


def test_glyphs_of_two_lines_drawn_on_each_other_overlap(breakage):
    reference = _line("가나다", 50, 100) + _line("라마바", 50, 120)
    # 첫 줄만 둘째 줄 위로 옮겨 겹친다. 아래 줄은 제자리라 쪽 끝까지 밀린 것이 아니므로 세로 밀림은 아니다.
    rendered = _line("가나다", 50, 119) + _line("라마바", 50, 120)

    events = breakage.judge_page(_page(breakage, reference, rendered, owners=[0, 0, 0, 1, 1, 1]))

    assert {event["kind"] for event in events} == {"겹침"}


def test_shift_that_does_not_reach_page_end_is_not_vertical_shift(breakage):
    reference = _line("가나다라마", 50, 100) + _line("바사아자차", 50, 140) + _line("카타파하가", 50, 180)
    rendered = _line("가나다라마", 50, 110) + _line("바사아자차", 50, 140) + _line("카타파하가", 50, 180)
    to_end = _line("가나다라마", 50, 110) + _line("바사아자차", 50, 150) + _line("카타파하가", 50, 190)

    assert breakage.judge_page(_page(breakage, reference, rendered)) == []
    events = breakage.judge_page(_page(breakage, reference, to_end))
    # 맨 아래 줄은 아래에 견줄 줄이 없어 꼬리 밀림으로 따로 센다.
    assert _breaks(events) == [("세로 밀림", 0), ("세로 밀림", 1)]


def test_private_use_codes_are_not_missing(breakage):
    reference = _line("\U000f005d가나\U000f005e", 50, 100)
    rendered = _line("『가나』", 50, 100)

    assert breakage.judge_page(_page(breakage, reference, rendered)) == []


def test_overlap_already_in_reference_is_not_broken(breakage):
    rows = _line("가나다", 50, 100) + _line("라마바", 52, 101)

    assert breakage.judge_page(_page(breakage, rows, rows, owners=[0, 0, 0, 1, 1, 1])) == []


def test_rule_through_glyph_is_overlap_only_when_reference_has_no_such_rule(breakage):
    rows = _line("가나다", 50, 100)
    rule = breakage.Rule(40, 96, 200, 96)

    crossed = breakage.judge_page(_page(breakage, rows, rows, rules=[rule]))
    same_as_reference = breakage.judge_page(_page(breakage, rows, rows, rules=[rule], reference_rules=[rule]))

    assert {event["kind"] for event in crossed} == {"겹침"}
    assert same_as_reference == []


def test_glyph_pushed_outside_page_overflows_but_same_place_as_reference_does_not(breakage):
    reference = _line("가나다", 40, 100)
    pushed = _line("가나다", 60, 100)

    pushed_events = breakage.judge_page(_page(breakage, reference, pushed, width=75.0))
    same_events = breakage.judge_page(_page(breakage, reference, reference, width=65.0))

    assert {event["kind"] for event in pushed_events} == {"넘침"}
    assert same_events == []


def test_partly_found_line_gives_back_its_deficit_to_a_missing_line(breakage):
    # 첫 줄은 30pt 밀렸고 「다」만 렌더에 없다. 둘째 줄 「다다사」는 렌더에 없다(먼 곳에 「다」·「사」 하나씩만 있다).
    reference = _line("가나다라", 50, 100) + _line("다다사", 50, 200)
    rendered = _line("다사", 400, 600) + _line("가나라", 50, 130)

    events = breakage.judge_page(_page(breakage, reference, rendered))

    assert ("빠짐", 1) in _breaks(events)


def test_stroked_rect_edges_are_rules(breakage):
    from rea_hwp.render.scene import Page, Rect

    page = Page(width=60000, height=80000, number=1)
    page.items.append(Rect(1000, 2000, 20000, 4000, stroke="#000000", stroke_width=50))
    page.items.append(Rect(1000, 9000, 20000, 4000, fill="#eeeeee"))

    rules = breakage.rendered_rules(page)

    assert sorted((rule.x0, rule.y0, rule.x1, rule.y1) for rule in rules) == [
        (10.0, 20.0, 10.0, 60.0),
        (10.0, 20.0, 210.0, 20.0),
        (10.0, 60.0, 210.0, 60.0),
        (210.0, 20.0, 210.0, 60.0),
    ]


def test_summary_counts_pass_fail_and_unchecked_per_group(breakage):
    broken_page = {"page": 2, "kinds": ["빠짐"], "first": {"kind": "빠짐", "container": "본문"}}
    results = {
        "a.hwp": {"status": "ok", "groups": ["전체"], "page_records": [{"page": 1, "kinds": [], "first": None}]},
        "b.hwp": {
            "status": "ok",
            "groups": ["전체", "표 안의 표"],
            "page_records": [{"page": 1, "kinds": [], "first": None}, broken_page],
        },
        "c.hwp": {"status": "render-error", "reason": "x", "groups": ["전체"]},
    }

    summary = breakage.summarize(results)

    whole = summary["groups"]["전체"]
    assert (whole["documents"], whole["passed"], whole["failed"], whole["unchecked"]) == (3, 1, 1, 1)
    assert (whole["pages"], whole["broken_pages"], whole["kinds"]["빠짐"]) == (3, 1, 1)
    assert summary["groups"]["표 안의 표"]["broken_pages"] == 1
    assert [row["key"] for row in summary["causes"]] == ["빠짐 / 본문"]


def _crash_on_second(target):
    if target.name == "b.hwp":
        os._exit(1)
    return {"status": "ok", "groups": list(target.groups), "page_records": []}


def test_worker_crash_marks_only_that_document_unchecked(breakage, tmp_path):
    chosen = [
        breakage.Target(name, tmp_path / name, tmp_path / "x.pdf", None, ("전체",))
        for name in ("a.hwp", "b.hwp", "c.hwp")
    ]

    results = breakage.measure_all(chosen, 2, worker=_crash_on_second)

    assert {name: result["status"] for name, result in results.items()} == {
        "a.hwp": "ok",
        "b.hwp": "check-error",
        "c.hwp": "ok",
    }


def test_missing_reference_is_unchecked_not_failed(breakage, tmp_path):
    target = breakage.Target("x.hwp", tmp_path / "x.hwp", tmp_path / "x.pdf", None, ("전체",))

    assert breakage.measure(target)["status"] == "unchecked"


def test_corpus_page_pushed_down_by_table_is_vertical_shift_and_others_pass(breakage):
    from corpus_paths import CORPUS_ROOT

    from rea_hwp.document import HwpDocument

    path = CORPUS_ROOT / "oss/rhwp/KTX.hwp"
    HwpDocument.open(path)  # 말뭉치가 없으면 FileNotFoundError로 conftest가 건너뛴다.
    target = breakage.Target("KTX.hwp", path, CORPUS_ROOT / "ref2022/oss/rhwp/KTX.pdf", None, ("전체",))

    result = breakage.measure(target)

    broken = {page["page"]: page["kinds"] for page in result["page_records"] if page["kinds"]}
    assert result["status"] == "ok"
    assert broken == {25: ["세로 밀림", "빠짐"]}


def test_overlap_with_new_engine_equation_glyph_is_not_counted(breakage):
    reference = _line("가나다", 50, 100)
    rendered = _line("가나다", 50, 100) + [("", 52, 99, 10)]
    data = _page(breakage, reference, rendered, owners=[0, 0, 0, 1])
    data.glyphs[3].font_family = "HancomEQN"

    assert breakage.judge_page(data) == []
