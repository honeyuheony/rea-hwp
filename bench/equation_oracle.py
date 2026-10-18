"""한글 2022가 다시 계산한 수식 크기를 원본 문서에 옮겨 심어, 수식 엔진 오차와 배치 엔진 오차를 가른다.

한글은 문서를 열 때 수식 크기를 수식 엔진으로 다시 계산하고 그 문단의 줄을 다시 짠다
(실측: 3-09월_교육_통합_2022 수식 2,207개 중 65개).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from rea_hwp import tags
from rea_hwp.document import HwpDocument, Node
from rea_hwp.models.paragraph import LineSeg

#: 크기가 바뀐 수식을 담은 문단 아래에서 옮겨 심는 레코드(수식·개체·표·셀 크기와 줄).
EQUATION_SCOPE_TAGS = frozenset(
    {tags.CTRL_HEADER, tags.EQEDIT, tags.TABLE, tags.LIST_HEADER, tags.SHAPE_COMPONENT, tags.PARA_LINE_SEG}
)
#: 수식 범위 밖에서 다르면 배치가 수식 아닌 이유로도 바뀐 것이라 옮겨 심기를 포기하는 레코드.
LAYOUT_TAGS = frozenset({tags.CTRL_HEADER, tags.TABLE, tags.LIST_HEADER, tags.PARA_LINE_SEG})


@dataclass
class GraftReport:
    changed_equations: int = 0
    grafted: Counter = field(default_factory=Counter)
    #: 수식 범위 밖에서 배치에 영향을 주는 차이(경로, 태그). 하나라도 있으면 옮겨 심지 않는다.
    blocking: list[tuple[tuple, int]] = field(default_factory=list)
    #: 배치와 무관해 원본 값을 그대로 둔 차이(태그별 개수).
    kept: Counter = field(default_factory=Counter)

    @property
    def applicable(self) -> bool:
        return self.changed_equations > 0 and not self.blocking


def _is_equation(node: Node) -> bool:
    return node.tag == tags.CTRL_HEADER and int.from_bytes(node.record.payload[:4], "little") == tags.EQUATION_CTRL


def _is_resized_equation(original: Node, resaved: Node) -> bool:
    """크기가 바뀐 수식. 인스턴스 번호 같은 다른 필드만 바뀐 수식은 배치에 영향이 없어 세지 않는다."""
    return _is_equation(original) and _object_geometry(original) != _object_geometry(resaved)


def _matched_children(original: Node, resaved: Node, path: tuple) -> list[Node]:
    """resaved 자식을 original에 맞춘다. 줄 정보 없던 문단에 한글이 넣은 LINE_SEG는 뺀다(우리가 직접 나눈다)."""
    children = resaved.children
    if len(children) != len(original.children) and not original.find(tags.PARA_LINE_SEG):
        children = [child for child in children if child.tag != tags.PARA_LINE_SEG]
    if [child.tag for child in children] != [child.tag for child in original.children]:
        raise ValueError(f"재저장본의 레코드 구조가 원본과 다르다: {path}")
    return children


def _equation_sizes(node: Node) -> list[tuple]:
    sizes = [_object_geometry(node)] if _is_equation(node) else []
    for child in node.children:
        sizes += _equation_sizes(child)
    return sizes


def _has_changed_equation(original: Node, resaved: Node) -> bool:
    if _is_resized_equation(original, resaved):
        return True
    try:
        children = _matched_children(original, resaved, ())
    except ValueError:
        return _equation_sizes(original) != _equation_sizes(resaved)
    return any(_has_changed_equation(a, b) for a, b in zip(original.children, children, strict=True))


def _line_shape(segment: LineSeg) -> tuple:
    """세로 위치와 쪽·단 첫 줄 표지를 뺀 줄 모양. 수식 범위 밖에서는 이것만 같아야 한다."""
    return (
        segment.text_start,
        segment.line_height,
        segment.text_height,
        segment.baseline_gap,
        segment.line_spacing,
        segment.column_start,
        segment.segment_width,
    )


def _object_geometry(node: Node) -> tuple:
    model = node.model
    return tuple(getattr(model, name, None) for name in ("vert_offset", "horz_offset", "width", "height"))


def _layout_changed(original: Node, resaved: Node) -> bool:
    if original.tag == tags.PARA_LINE_SEG:
        a, b = original.model.segments, resaved.model.segments
        return len(a) != len(b) or any(_line_shape(x) != _line_shape(y) for x, y in zip(a, b, strict=True))
    if original.tag == tags.CTRL_HEADER:
        return _object_geometry(original) != _object_geometry(resaved)
    return True


def _walk(original: Node, resaved: Node, path: tuple, in_scope: bool, plan: list, report: GraftReport) -> None:
    if original.tag != resaved.tag or original.record.level != resaved.record.level:
        raise ValueError(f"재저장본의 레코드 구조가 원본과 다르다: {path}")
    try:
        children = _matched_children(original, resaved, path)
    except ValueError:
        # 줄 정보 없던 문단에 한글이 필드 같은 레코드를 더한 경우(task2070 책갈피)는 그 문단을 원본대로 둔다.
        if original.tag != tags.PARA_HEADER or original.find(tags.PARA_LINE_SEG):
            raise
        if _equation_sizes(original) != _equation_sizes(resaved):
            report.blocking.append((path, original.tag))
        report.kept["paragraph structure"] += 1
        return
    if original.record.payload != resaved.record.payload:
        if _is_resized_equation(original, resaved):
            report.changed_equations += 1
        if in_scope and original.tag in EQUATION_SCOPE_TAGS:
            plan.append((original, resaved))
        elif original.tag in LAYOUT_TAGS and _layout_changed(original, resaved):
            report.blocking.append((path, original.tag))
        elif original.tag == tags.PARA_LINE_SEG:
            plan.append((original, resaved))  # 세로 위치만 다른 줄: 앞 수식 줄 높이가 바뀌어 밀린 위치다.
        else:
            report.kept[original.tag] += 1
    if original.tag == tags.PARA_HEADER and not in_scope:
        in_scope = _has_changed_equation(original, resaved)
    for index, (a, b) in enumerate(zip(original.children, children, strict=True)):
        _walk(a, b, path + (index,), in_scope, plan, report)


def graft_equation_layout(original: HwpDocument, resaved: HwpDocument) -> GraftReport:
    """재저장본의 수식 크기와 그 여파(줄·표·셀 크기, 밀린 세로 위치)를 옮겨 심는다. 막는 차이가 있으면 그대로 둔다."""
    if len(original.sections) != len(resaved.sections):
        raise ValueError("재저장본의 구역 수가 원본과 다르다")
    report, plan = GraftReport(), []
    for a_stream, b_stream in zip(original.sections, resaved.sections, strict=True):
        if len(a_stream.nodes) != len(b_stream.nodes):
            raise ValueError(f"재저장본의 {a_stream.name} 최상위 문단 수가 원본과 다르다")
        for index, (a, b) in enumerate(zip(a_stream.nodes, b_stream.nodes, strict=True)):
            _walk(a, b, (a_stream.name, index), False, plan, report)
    if report.applicable:
        for target, source in plan:
            target.record, target.model = source.record, source.model
            report.grafted[target.tag] += 1
    return report
