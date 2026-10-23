"""쪽마다 정답 PDF 줄과 렌더 줄을 맞대 어긋남이 시작되는 줄을 원인 종류와 문단 특징으로 센다.

사용: uv run --extra bench python bench/divergence.py --out <폴더> [--match 이름일부] [--jobs N]
"""

from __future__ import annotations

import argparse
import bisect
import collections
import difflib
import json
import math
import statistics
import struct
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from accuracy import (
    EQUATION_FONTS,
    TOLERANCE_PT,
    PlacedChar,
    _inside,
    merge_effect_strikes,
    pdf_pages,
    score_page,
)
from corpus import CORPUS_ROOT, load_corpus

from rea_hwp import tags
from rea_hwp.document import HwpDocument
from rea_hwp.models.controls import CaptionListHeader, CellListHeader, ObjectCtrlHeader, TextBoxListHeader
from rea_hwp.models.paragraph import TAB, ControlChar
from rea_hwp.render import layout
from rea_hwp.render.options import RenderOptions
from rea_hwp.render.svg import HU_PER_PT, page_glyph_table

REFERENCE_ROOT = CORPUS_ROOT / "ref2022"

#: 오른쪽으로 이어져도 새 줄로 보는 기준선 차이(pt). 위·아래 첨자는 남기고 다음 단 첫 줄은 끊으려는 추정값, 검증 안 됨.
LINE_JUMP_PT = 12.0
#: 이보다 짧은 줄 글은 이웃 쪽에서 찾지 않는다(한두 글자는 아무 쪽에나 있다).
NEIGHBOR_TEXT_MIN = 3

_CONTAINERS = {
    tags.TABLE_CTRL: "셀",
    tags.GSO_CTRL: "글상자",
    tags.HEADER_CTRL: "머리말",
    tags.FOOTER_CTRL: "꼬리말",
    tags.FOOTNOTE_CTRL: "각주",
    tags.ENDNOTE_CTRL: "미주",
}
_OBJECT_KINDS = {tags.TABLE_CTRL: "표", tags.GSO_CTRL: "개체", tags.EQUATION_CTRL: "수식"}
#: 표·개체 아래 목록은 컨트롤이 아니라 목록 머리 종류로 가른다(표 캡션은 셀이 아니다).
_LIST_KINDS = {CellListHeader: "셀", CaptionListHeader: "캡션", TextBoxListHeader: "글상자"}

#: 그린 TextLine의 id → (그 TextLine, 줄 특징). TextLine을 함께 쥐어 id가 다른 객체에 다시 쓰이지 않게 한다.
_TRACED: dict[int, tuple[object, dict]] = {}
#: 문서 id → 문단 레코드 id → (담은 목록 종류, 앞 문단 종류, 자기 문단 개체 종류).
_PARAGRAPH_CONTEXT: dict[int, dict[int, tuple[str, str, str]]] = {}


def _ctrl_id(node) -> int:
    payload = node.record.payload
    return struct.unpack_from("<I", payload, 0)[0] if node.tag == tags.CTRL_HEADER and len(payload) >= 4 else 0


def _objects_of(paragraph) -> str:
    kinds = []
    for child in paragraph.children:
        kind = _OBJECT_KINDS.get(_ctrl_id(child))
        if kind is None:
            continue
        if kind != "수식" and isinstance(child.model, ObjectCtrlHeader) and child.model.treat_as_char:
            kind += "(글자처럼)"
        kinds.append(kind)
    if kinds:
        return "+".join(sorted(set(kinds)))
    text = paragraph.find(tags.PARA_TEXT)
    return "글" if text is not None and text.record.payload else "빈 문단"


def _paragraph_context(document) -> dict[int, tuple[str, str, str]]:
    """문단마다 담은 목록(가장 안쪽 컨트롤), 같은 목록의 바로 앞 문단 종류, 자기 문단의 개체 종류."""
    found: dict[int, tuple[str, str, str]] = {}
    roots = [node for section in document.sections for node in section.nodes]
    pending = [(node, "본문", 0) for node in reversed(roots)]
    #: 목록(부모 Node id, 최상위는 0)마다 마지막으로 본 문단의 종류와 지금 목록의 종류.
    previous: dict[int, str] = {}
    list_kind: dict[int, str] = {}
    while pending:
        node, container, parent = pending.pop()
        if node.tag == tags.PARA_HEADER:
            own = _objects_of(node)
            found[id(node.record)] = (list_kind.get(parent, container), previous.get(parent, "목록 처음"), own)
            previous[parent] = own
        elif node.tag == tags.LIST_HEADER:
            # 셀·글상자·캡션 문단은 LIST_HEADER 뒤에 같은 수준으로 이어지므로 새 목록은 여기서 시작한다.
            previous.pop(parent, None)
            list_kind[parent] = _LIST_KINDS.get(type(node.model), container)
        inner = _CONTAINERS.get(_ctrl_id(node), container)
        if node.tag == tags.PARA_HEADER and parent in list_kind:
            inner = _CONTAINERS.get(_ctrl_id(node), list_kind[parent])
        pending.extend((child, inner, id(node)) for child in reversed(node.children))
    return found


def _line_state(view, index: int) -> str:
    """그린 줄이 저장 LINE_SEG를 그대로 쓴 것인가, 세로·가로 값만 바꾼 것인가, 다시 나눈 것인가."""
    seg_node = view.node.find(tags.PARA_LINE_SEG)
    stored = seg_node.model.__dict__.get("segments") if seg_node is not None else None
    if not stored:
        return "저장 줄 없음"
    if index >= len(stored):
        return "다시 나눔"
    drawn, saved = view.segments[index], stored[index]
    if drawn == saved:
        return "저장 줄 그대로"

    # 줄 수가 달라도 이 줄의 시작과 끝(다음 줄 시작)이 저장 줄과 같으면 다시 나눈 줄이 아니다.
    def end(segments) -> int | None:
        return segments[index + 1].text_start if index + 1 < len(segments) else None

    if drawn.text_start != saved.text_start or end(view.segments) != end(stored):
        return "다시 나눔"
    if (drawn.column_start, drawn.segment_width) != (saved.column_start, saved.segment_width):
        return "저장 줄 가로 바꿈"
    return "저장 줄 세로 바꿈"


def _line_flags(view, index: int) -> list[str]:
    first, last = view.line_range(index)
    flags = []
    chars = view.chars[first:last]
    if any(isinstance(c, ControlChar) and c.code == TAB for c in chars):
        flags.append("탭")
    in_line = {_ctrl_id(view.controls[i]) for i in range(first, last) if i in view.controls}
    inline_objects = [
        view.controls[i]
        for i in range(first, last)
        if i in view.controls
        and _ctrl_id(view.controls[i]) in (tags.TABLE_CTRL, tags.GSO_CTRL)
        and isinstance(view.controls[i].model, ObjectCtrlHeader)
        and view.controls[i].model.treat_as_char
    ]
    if tags.AUTO_NUMBER_CTRL in in_line:
        flags.append("자동 번호")
    if tags.EQUATION_CTRL in in_line:
        flags.append("수식")
    if inline_objects:
        flags.append("줄 안 개체")
    if index == 0 and view.para_shape.head_type:
        flags.append("문단 머리")
    return flags


def _line_info(engine, view, index: int) -> dict:
    document = engine.document
    context = _PARAGRAPH_CONTEXT.get(id(document))
    if context is None:
        context = _PARAGRAPH_CONTEXT[id(document)] = _paragraph_context(document)
    container, previous, own = context.get(id(view.node.record), ("모름", "모름", "모름"))
    if engine.__dict__.get("in_master_page"):
        container = "바탕쪽"
    return {
        "paragraph": engine.document.record_id(view.node.record),
        "container": container,
        "previous": previous,
        "own_objects": own,
        "line_state": _line_state(view, index),
        "line_number": index,
        "flags": _line_flags(view, index),
    }


def install_trace() -> None:
    """draw_line을 감싸 그 줄이 넣은 TextLine마다 특징을 적는다. 렌더 결과는 바꾸지 않는다."""
    draw_line = layout.DocumentLayout.draw_line
    if getattr(draw_line, "traced", False):
        return

    def traced(self, state, view, index, area):
        before = len(state.page.text_lines)
        draw_line(self, state, view, index, area)
        added = state.page.text_lines[before:]
        if added:
            info = _line_info(self, view, index)
            for line in added:
                # 줄 안 표 칸의 줄은 안쪽 draw_line이 먼저 적었다.
                _TRACED.setdefault(id(line), (line, info))

    traced.traced = True
    layout.DocumentLayout.draw_line = traced


def reference_lines(chars: list[PlacedChar]) -> list[list[int]]:
    """PDF에 찍힌 차례의 글자를 줄로 묶는다. 왼쪽으로 돌아가거나 기준선이 LINE_JUMP_PT 넘게 바뀌면 새 줄이다.

    같은 기준선의 옆 칸 글은 한 줄로 묶인다. 줄 나눔 판정은 그래서 같은 문단의 렌더 줄끼리만 비교한다(classify).
    """
    lines: list[list[int]] = []
    for index, char in enumerate(chars):
        if lines:
            previous = chars[lines[-1][-1]]
            if char.x >= previous.x - 1.0 and abs(char.y - previous.y) <= LINE_JUMP_PT:
                lines[-1].append(index)
                continue
        lines.append([index])
    return lines


def pair_chars(reference: list[PlacedChar], rendered: list[PlacedChar]) -> dict[int, int]:
    """accuracy.score_page와 같은 짝 맺기(글자 열 정렬 뒤 남은 글자는 허용 오차 안 같은 글자). 정답 번호 → 렌더 번호."""
    matcher = difflib.SequenceMatcher(None, [c.char for c in reference], [c.char for c in rendered], autojunk=False)
    pairs = {block.a + k: block.b + k for block in matcher.get_matching_blocks() for k in range(block.size)}
    used = set(pairs.values())
    remaining: dict[str, list[int]] = {}
    for index, char in enumerate(rendered):
        if index not in used:
            remaining.setdefault(char.char, []).append(index)
    for a, char in enumerate(reference):
        if a in pairs:
            continue
        candidates = remaining.get(char.char, [])
        best = min(candidates, key=lambda b: math.dist((rendered[b].x, rendered[b].y), (char.x, char.y)), default=None)
        if best is not None and math.dist((rendered[best].x, rendered[best].y), (char.x, char.y)) <= TOLERANCE_PT:
            candidates.remove(best)
            pairs[a] = best
    return pairs


def rendered_chars(page) -> tuple[list[PlacedChar], list[object]]:
    """evaluate.score_layout과 같은 렌더 글자 목록과, 글자마다 그 Glyph."""
    glyphs = [glyph for glyph in page.glyphs if glyph.char.strip()]
    table = [PlacedChar(c, x, y) for c, x, y in page_glyph_table(page)]
    kept = merge_effect_strikes(table)
    owners, cursor = [], 0
    for char in kept:
        while table[cursor] != char:
            cursor += 1
        owners.append(glyphs[cursor])
        cursor += 1
    return kept, owners


def glyph_lines(page, glyphs: list[object]) -> list[int | None]:
    """글자마다 그것을 담은 page.text_lines 번호. 기준선이 가장 가깝고 줄 폭 안에 있는 줄이다."""
    lines = page.text_lines
    order = sorted(range(len(lines)), key=lambda i: lines[i].baseline)
    baselines = [lines[i].baseline for i in order]
    owners: list[int | None] = []
    for glyph in glyphs:
        low = bisect.bisect_left(baselines, glyph.y - 6 * HU_PER_PT)
        high = bisect.bisect_right(baselines, glyph.y + 6 * HU_PER_PT)
        best, best_gap = None, None
        for k in order[low:high]:
            line = lines[k]
            gap = abs(glyph.y - line.baseline)
            if (
                gap > max(line.size * 0.5, HU_PER_PT)
                or not line.x - HU_PER_PT <= glyph.x <= line.x + line.width + HU_PER_PT
            ):
                continue
            if best_gap is None or gap < best_gap:
                best, best_gap = k, gap
        owners.append(best)
    return owners


def _find_run(haystack: list[str], needle: list[str]) -> int | None:
    """needle 글자 목록이 haystack 글자 목록에 처음 나오는 자리. 글자 하나가 여러 코드 포인트여도 자리가 맞는다."""
    first = needle[0]
    for at, char in enumerate(haystack):
        if char == first and haystack[at : at + len(needle)] == needle:
            return at
    return None


def classify(line, line_of, reference, rendered, pairs, owners, good, page, neighbor_texts):
    """시작 줄의 첫 1pt 밖 글자로 원인 종류, 그 원인을 맡은 렌더 줄 번호, 그 렌더 줄에 든 (정답, 렌더) 쌍을 정한다."""
    first_bad = next(a for a in line if a not in good)
    start = pairs.get(first_bad)
    if start is None:
        rest = line[line.index(first_bad) :]
        missing = [a for a in rest if a not in pairs]
        run = [reference[a].char for a in missing]
        if len(missing) * 2 > len(rest) and len(run) >= NEIGHBOR_TEXT_MIN:
            for neighbor in neighbor_texts:
                at = _find_run(neighbor[0], run) if neighbor is not None else None
                if at is not None:
                    return "쪽 나눔 다름", ("이웃 쪽", neighbor[1][at]), []
        before = [pairs[a] for a in line[: line.index(first_bad)] if a in pairs]
        return "글자 빠짐", owners[before[-1]] if before else None, []
    main = owners[start]
    if main is None:
        return "줄 밖 글자", None, []
    sub = [(a, pairs[a]) for a in line if a in pairs and owners[pairs[a]] == main]
    reverse = {b: a for a, b in pairs.items()}
    members = [b for b in range(len(rendered)) if owners[b] == main]
    # 렌더 줄이 한글의 다른 줄 글자를 품었다(한글은 거기서 나눴다).
    if any(b in reverse and line_of[reverse[b]] != line_of[first_bad] for b in members):
        return "줄 나눔 다름", main, sub
    # 한글이 한 줄에 둔 같은 문단 글자를 다른 렌더 줄에 나눠 놓았다. 옆 칸(다른 문단) 글은 따지지 않는다.
    paragraph = page.text_lines[main].paragraph
    split = {
        owners[pairs[a]]
        for a in line
        if a in pairs
        and owners[pairs[a]] not in (main, None)
        and paragraph is not None
        and page.text_lines[owners[pairs[a]]].paragraph == paragraph
    }
    if split:
        return "줄 나눔 다름", main, sub
    span = line[line.index(sub[0][0]) : line.index(sub[-1][0]) + 1]
    if any(b not in reverse for b in members) or any(a not in pairs for a in span):
        return "글자 빠짐·더함", main, sub
    if abs(statistics.median(rendered[b].y - reference[a].y for a, b in sub)) > TOLERANCE_PT:
        return "세로 밀림", main, sub
    if abs(rendered[sub[0][1]].x - reference[sub[0][0]].x) > TOLERANCE_PT:
        return "줄 안 x 어긋남(시작)", main, sub
    if x_jump(sub, reference, rendered) is not None:
        return "줄 안 x 어긋남(건너뜀)", main, sub
    return "줄 안 x 어긋남(누적)", main, sub


def x_jump(paired, reference, rendered) -> int | None:
    """이웃한 두 글자 사이에서 x 차이가 허용 오차 넘게 바뀌는 첫 자리(paired 번호). 고르게 쌓이면 None."""
    dxs = [rendered[b].x - reference[a].x for a, b in paired]
    return next((i for i in range(1, len(dxs)) if abs(dxs[i] - dxs[i - 1]) > TOLERANCE_PT), None)


def _line_record(page, index: int | None) -> dict:
    if index is None:
        return {"container": "모름"}
    line = page.text_lines[index]
    traced = _TRACED.get(id(line))
    info = dict(traced[1]) if traced is not None and traced[0] is line else {"container": "장식(쪽 번호 등)"}
    info["text"] = line.text[:40]
    info["baseline_pt"] = round(line.baseline / HU_PER_PT, 2)
    return info


def page_onsets(page_index, reference_all, pages, neighbor) -> dict:
    page = pages[page_index] if page_index < len(pages) else None
    boxes = [tuple(v / 100 for v in box) for box in page.excluded] if page is not None else []
    reference = [c for c in reference_all if not c.font.startswith(EQUATION_FONTS) and not _inside(c, boxes)]
    rendered, glyphs = rendered_chars(page) if page is not None else ([], [])
    owners = glyph_lines(page, glyphs) if page is not None else []
    pairs = pair_chars(reference, rendered)
    good = {
        a
        for a, b in pairs.items()
        if math.dist((reference[a].x, reference[a].y), (rendered[b].x, rendered[b].y)) <= TOLERANCE_PT
    }
    record = {
        "page": page_index + 1,
        "reference_chars": len(reference),
        "bad": len(reference) - len(good),
        "onsets": [],
    }
    if page is not None:
        check = score_page(
            reference_all, merge_effect_strikes([PlacedChar(c, x, y) for c, x, y in page_glyph_table(page)]), boxes
        )
        record["score_within"] = check.within
    lines = reference_lines(reference)
    line_of = {a: number for number, line in enumerate(lines) for a in line}
    neighbor_texts = [neighbor(page_index - 1), neighbor(page_index + 1)]
    previous_good = True
    for number, line in enumerate(lines):
        bad = [a for a in line if a not in good]
        if not bad:
            previous_good = True
            continue
        if previous_good:
            kind, owner, paired = classify(
                line, line_of, reference, rendered, pairs, owners, good, page, neighbor_texts
            )
            if isinstance(owner, tuple):
                neighbor_page, neighbor_owner = owner[1]
                where = _line_record(pages[neighbor_page], neighbor_owner) | {"neighbor_page": neighbor_page + 1}
            else:
                where = _line_record(page, owner) if page is not None else {"container": "쪽 없음"}
            onset = {
                "kind": kind,
                "line": number,
                "first_on_page": not record["onsets"],
                "reference_text": "".join(reference[a].char for a in line)[:40],
                "reference_y": round(reference[line[0]].y, 2),
                "dx": round(statistics.median(rendered[b].x - reference[a].x for a, b in paired), 2)
                if paired
                else None,
                "dy": round(statistics.median(rendered[b].y - reference[a].y for a, b in paired), 2)
                if paired
                else None,
                "damage": 0,
                **where,
            }
            jump = x_jump(paired, reference, rendered) if kind.startswith("줄 안 x") else None
            if jump is not None:
                onset["x_jump"] = [reference[paired[jump - 1][0]].char, reference[paired[jump][0]].char]
                onset["x_jump_pt"] = round(
                    (rendered[paired[jump][1]].x - reference[paired[jump][0]].x)
                    - (rendered[paired[jump - 1][1]].x - reference[paired[jump - 1][0]].x),
                    2,
                )
            record["onsets"].append(onset)
        record["onsets"][-1]["damage"] += len(bad)
        previous_good = False
    return record


def diverge(entry) -> dict:
    reference_path = REFERENCE_ROOT / entry.path.relative_to(CORPUS_ROOT).with_suffix(".pdf")
    if not reference_path.exists():
        return {"status": "unchecked", "reason": "no reference PDF"}
    install_trace()
    _TRACED.clear()
    _PARAGRAPH_CONTEXT.clear()
    try:
        pages = layout.layout_document(HwpDocument.open(entry.path, entry.password), RenderOptions.from_environment())
    except Exception as error:  # 렌더링 실패는 실패로 센다(evaluate.py와 같다).
        return {"status": "render-error", "reason": f"{type(error).__name__}: {error}"}
    try:
        return _diverge_pages(pages, pdf_pages(str(reference_path)))
    except Exception as error:  # 한 문서의 집계 실패가 전체 집계를 끝내지 않게 검사불가로 따로 센다.
        return {"status": "check-error", "reason": f"{type(error).__name__}: {error}"}
    finally:
        _TRACED.clear()


def _diverge_pages(pages, reference) -> dict:
    texts: dict[int, tuple[list[str], list] | None] = {}

    def neighbor(index: int):
        if not 0 <= index < len(pages):
            return None
        if index not in texts:
            chars, glyphs = rendered_chars(pages[index])
            owners = glyph_lines(pages[index], glyphs)
            texts[index] = ([c.char for c in chars], [(index, owner) for owner in owners])
        return texts[index]

    records = [page_onsets(index, chars, pages, neighbor) for index, chars in enumerate(reference)]
    return {"status": "ok", "pages": len(pages), "reference_pages": len(reference), "page_records": records}


def _char_class(char: str) -> str:
    if "\ue000" <= char <= "\uf8ff" or char >= "\U000f0000":
        return "PUA"
    if "\uac00" <= char <= "\ud7a3":
        return "한글"
    if char.isdigit():
        return "숫자"
    if char.isascii() and char.isalpha():
        return "라틴"
    if char.isalpha():
        return "한자 등"
    return "부호"


def _feature_keys(onset: dict) -> dict[str, str]:
    flags = "·".join(onset.get("flags", [])) or "없음"
    keys = {}
    if "x_jump" in onset:
        before, after = onset["x_jump"]
        keys["x 건너뜀 글자 종류"] = f"{_char_class(before[0])} 뒤 {_char_class(after[0])}"
        keys["x 건너뜀 글자 쌍"] = f"{before!r} 뒤 {after!r}"
    return keys | {
        "종류": onset["kind"],
        "종류 × 목록": f"{onset['kind']} / {onset.get('container', '모름')}",
        "종류 × 줄 상태": f"{onset['kind']} / {onset.get('line_state', '모름')}",
        "종류 × 앞 문단": f"{onset['kind']} / {onset.get('previous', '모름')}",
        "종류 × 줄 특징": f"{onset['kind']} / {flags}",
        "종합": " / ".join(
            [
                onset["kind"],
                onset.get("container", "모름"),
                onset.get("line_state", "모름"),
                "앞 " + onset.get("previous", "모름"),
                "자기 " + onset.get("own_objects", "모름"),
                "첫 줄" if onset.get("line_number") == 0 else "둘째 줄 이후",
                flags,
            ]
        ),
    }


def ranking(results: dict) -> dict[str, list[dict]]:
    tables: dict[str, dict[str, dict]] = collections.defaultdict(dict)
    for path, result in results.items():
        if result["status"] != "ok":
            continue
        for page in result["page_records"]:
            for onset in page["onsets"]:
                for table, key in _feature_keys(onset).items():
                    row = tables[table].setdefault(
                        key,
                        {"key": key, "onsets": 0, "first_pages": 0, "damage": 0, "documents": set(), "examples": []},
                    )
                    row["onsets"] += 1
                    row["first_pages"] += onset["first_on_page"]
                    row["damage"] += onset["damage"]
                    row["documents"].add(path)
                    row["examples"].append((onset["damage"], f"{Path(path).stem}:{page['page']}"))
    ranked = {}
    for table, rows in tables.items():
        ordered = sorted(rows.values(), key=lambda r: (-r["damage"], -r["onsets"]))
        for row in ordered:
            row["documents"] = len(row["documents"])
            row["examples"] = [name for _, name in sorted(row["examples"], reverse=True)[:3]]
        ranked[table] = ordered
    return ranked


def markdown(ranked: dict[str, list[dict]], totals: dict, limit: int) -> str:
    out = ["# 코퍼스 어긋남 시작 줄 집계", ""]
    out.append(
        f"문서 {totals['documents']}, 채점 {totals['checked']}, render-error {totals['errors']}, "
        f"검사불가 {totals['check_errors']}, "
        f"정답 글자 {totals['reference_chars']}, 1pt 밖 {totals['bad']}, 시작 줄 {totals['onsets']}, "
        f"score_page와 맞은 글자 수가 다른 쪽 {totals['score_mismatch_pages']}"
    )
    out.append("")
    out.append(
        "시작 줄은 바로 앞 정답 줄(PDF 찍힌 차례)이 모두 1pt 안인데 1pt 밖 글자가 있는 줄이다. 뒤따라 틀린 글자는"
    )
    out.append("그 줄부터 다음 맞는 줄 앞까지의 1pt 밖 글자 수다. 처음 어긋남 쪽은 그 쪽의 첫 시작 줄로 센 쪽 수다.")
    for table in (
        "종합",
        "종류",
        "종류 × 목록",
        "종류 × 줄 상태",
        "종류 × 앞 문단",
        "종류 × 줄 특징",
        "x 건너뜀 글자 종류",
        "x 건너뜀 글자 쌍",
    ):
        out += [
            "",
            f"## {table}",
            "",
            "| 순위 | 키 | 시작 줄 | 처음 어긋남 쪽 | 뒤따라 틀린 글자 | 문서 | 예(문서:쪽) |",
        ]
        out.append("|---|---|---|---|---|---|---|")
        for rank, row in enumerate(ranked.get(table, [])[:limit], start=1):
            out.append(
                f"| {rank} | {row['key']} | {row['onsets']} | {row['first_pages']} | {row['damage']} | "
                f"{row['documents']} | {', '.join(row['examples'])} |"
            )
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True, help="pages.json과 ranking.md를 쓸 폴더")
    parser.add_argument("--match", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--rows", type=int, default=30, help="표마다 보일 줄 수")
    args = parser.parse_args()
    entries = [entry for entry in load_corpus() if args.match in entry.path.name]
    if args.limit:
        entries = entries[: args.limit]
    results = {}
    with ProcessPoolExecutor(args.jobs) as pool:
        for entry, result in zip(entries, pool.map(diverge, entries), strict=True):
            results[str(entry.path.relative_to(CORPUS_ROOT))] = result
    checked = [r for r in results.values() if r["status"] == "ok"]
    page_records = [page for r in checked for page in r["page_records"]]
    totals = {
        "documents": len(results),
        "checked": len(checked),
        "errors": sum(r["status"] == "render-error" for r in results.values()),
        "check_errors": sum(r["status"] == "check-error" for r in results.values()),
        "reference_chars": sum(p["reference_chars"] for p in page_records),
        "bad": sum(p["bad"] for p in page_records),
        "onsets": sum(len(p["onsets"]) for p in page_records),
        "score_mismatch_pages": sum(
            "score_within" in p and p["score_within"] != p["reference_chars"] - p["bad"] for p in page_records
        ),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "pages.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    ranked = ranking(results)
    (args.out / "ranking.md").write_text(markdown(ranked, totals, args.rows), encoding="utf-8")
    for path, result in results.items():
        if result["status"] in ("render-error", "check-error"):
            print(f"{result['status']}: {path}: {result['reason']}")
    print(json.dumps(totals, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
