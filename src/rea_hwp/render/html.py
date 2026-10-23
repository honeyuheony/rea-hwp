"""쪽 SVG 파일들을 한 화면에서 넘겨 보는 단독 HTML 뷰어."""

from __future__ import annotations

from html import escape
from typing import NamedTuple

from .common import TAC_ASCENT
from .scene import Glyph, TextLine
from .svg import copy_text, format_pt, glyph_family_and_text, glyph_style_attrs

_TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ --bg: #e9ecf1; --paper-shadow: 0 1px 4px rgba(16, 24, 40, .18); --text: #1f2937; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg: #1d2128; --paper-shadow: 0 1px 6px rgba(0, 0, 0, .6); --text: #e5e7eb; }}
}}
body {{ margin: 0; background: var(--bg); color: var(--text); font: 14px/1.5 system-ui, sans-serif; }}
header {{ position: sticky; top: 0; z-index: 1; display: flex; gap: 12px; align-items: center;
         padding: 8px 16px; background: var(--bg); border-bottom: 1px solid rgba(127, 127, 127, .3); }}
header h1 {{ font-size: 14px; margin: 0; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
main {{ display: flex; flex-direction: column; align-items: center; gap: 16px; padding: 16px; }}
/* 글 층은 전체 복사·찾기를 위해 모든 쪽에 두고 화면 밖 쪽은 배치를 건너뛴다. 쪽 높이는 aspect-ratio로 정한다. */
.page {{ position: relative; background: #fff; box-shadow: var(--paper-shadow); content-visibility: auto;
         width: calc(var(--zoom) * var(--w)); max-width: calc(100vw - 32px); }}
.page img {{ display: block; width: 100%; height: auto; user-select: none; }}
.text-layer {{ position: absolute; inset: 0; width: 100%; height: 100%; overflow: hidden; }}
.text-layer text {{ fill: transparent; white-space: pre; cursor: text; }}
.text-layer .fit {{ font-family: system-ui, sans-serif; }}
.text-layer ::selection {{ fill: transparent; background: rgba(6, 55, 123, .3); }}
/* 띠 자료가 있는 줄은 브라우저 칠하기(대체 글꼴 상자라 겹치거나 끊김)를 끄고 띠(.bands rect)로 그린다. */
.text-layer [data-band]::selection, .text-layer [data-band] ::selection {{ background: transparent; }}
.text-layer .bands {{ fill: rgba(6, 55, 123, .3); pointer-events: none; }}
@media print {{ .text-layer .bands {{ display: none; }} }}
.label {{ font-size: 12px; opacity: .7; text-align: center; }}
</style>
</head>
<body style="--zoom: 1">
<header>
<h1>{title}</h1>
<label>배율 <select id="zoom">
<option value="0.75">75%</option><option value="1" selected>100%</option>
<option value="1.25">125%</option><option value="1.5">150%</option></select></label>
<span>{count}쪽</span>
</header>
<main>
{pages}
</main>
<script>
document.getElementById('zoom').addEventListener('change', (event) => {{
  document.body.style.setProperty('--zoom', event.target.value);
}});
// 줄마다 따로 놓인 SVG 글은 브라우저 기본 복사로는 줄바꿈 없이 붙으므로, 고른 범위를 줄 단위로 모아 줄바꿈으로 잇는다.
// 범위 경계가 줄 끝(끝 위치)이나 줄 머리(0)에 걸린 줄은 고른 글자가 없으므로 빈 줄로 넣지 않는다.
function selectedText(line, range) {{
  const walker = document.createTreeWalker(line, NodeFilter.SHOW_TEXT);
  let text = '';
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {{
    if (!range.intersectsNode(node)) continue;
    const start = node === range.startContainer ? range.startOffset : 0;
    const end = node === range.endContainer ? range.endOffset : node.data.length;
    text += node.data.slice(start, end);
  }}
  return text;
}}
// 범위가 고른 글 조각(줄과 함께). 글 층 줄에 닿지 않는 범위는 그 범위의 글 하나다(line은 null).
function rangePieces(range) {{
  const common = range.commonAncestorContainer;
  // 공통 조상이 Document면(문서 전체를 고른 범위 등) 부모 요소가 없어 문서 요소에서 찾는다.
  const scope = common instanceof Element ? common : common.parentElement ?? document.documentElement;
  const own = scope.closest('.ln');
  const candidates = own ? [own] : scope.querySelectorAll('.text-layer .ln');
  const lines = [...candidates].filter((line) => range.intersectsNode(line));
  if (!lines.length) return [{{line: null, text: range.toString()}}];
  return lines.map((line) => ({{line, text: selectedText(line, range)}}));
}}
document.addEventListener('copy', (event) => {{
  const pieces = selectedRanges().flatMap(rangePieces).filter((piece) => piece.text);
  // 글 층 글자를 하나도 고르지 않았으면(쪽 번호 글에서 다음 쪽 첫 줄 머리까지 등) 브라우저 기본 복사에 맡긴다.
  if (!pieces.some((piece) => piece.line)) return;
  // 같은 줄을 이어 고른 범위 조각은 한 줄로 붙이고, 나머지는 줄바꿈으로 잇는다.
  const text = pieces
    .map((piece, k) => (k && (!piece.line || piece.line !== pieces[k - 1].line) ? '\\n' : '') + piece.text)
    .join('');
  event.clipboardData.setData('text/plain', text);
  event.preventDefault();
}});
// 선택 띠: 화면에 보이는 쪽의 띠 자료가 있는 줄마다, 고른 범위마다 rect 하나를 다음 그림 차례에 그린다.
const SVG_NS = 'http://www.w3.org/2000/svg';
const shownPages = new Set();
const bandedPages = new Set();
const lineUnits = new WeakMap();
let bandFrame = 0;
function scheduleBands() {{
  if (!bandFrame) bandFrame = requestAnimationFrame(drawBands);
}}
// 글리프 줄의 UTF-16 단위별 시작 x. tspan x 목록 뒤 글자(이은 자모)와 대리쌍 뒤 단위는 null이다.
function glyphStarts(line) {{
  const starts = [];
  for (const text of line.matches('text') ? [line] : line.querySelectorAll('text')) {{
    // 장평 transform(translate·scale)을 읽기만 해서 곱한다. consolidate()는 transform 속성을 고쳐 쓴다.
    const transforms = text.transform.baseVal;
    let origin = 0, scale = 1;
    for (let k = 0; k < transforms.numberOfItems; k += 1) {{
      const matrix = transforms.getItem(k).matrix;
      origin += scale * matrix.e;
      scale *= matrix.a;
    }}
    for (const span of text.querySelectorAll('tspan')) {{
      const xs = span.x.baseVal;
      let k = 0;
      for (const char of span.textContent) {{
        starts.push(k < xs.numberOfItems ? origin + scale * xs.getItem(k).value : null);
        if (char.length === 2) starts.push(null);
        k += 1;
      }}
    }}
  }}
  return starts;
}}
// 줄 글 UTF-16 단위마다 그 단위가 든 글자 묶음의 시작 x(lefts)와 끝 x(rights, 다음 묶음 시작 또는 줄 끝).
function unitsOf(line) {{
  let units = lineUnits.get(line);
  if (units) return units;
  const fitStarts = line.dataset.x && line.dataset.x.split(' ').map((x) => (x === '-' ? null : Number(x)));
  const starts = fitStarts || glyphStarts(line);
  const end = Number(line.dataset.end);
  const lefts = [], rights = [];
  let left = starts.find((x) => x !== null) ?? end;
  for (const x of starts) {{
    if (x !== null) left = x;
    lefts.push(left);
  }}
  let right = end;
  for (let k = starts.length - 1; k >= 0; k -= 1) {{
    rights[k] = right;
    if (starts[k] !== null) right = starts[k];
  }}
  units = {{lefts, rights}};
  lineUnits.set(line, units);
  return units;
}}
// 범위 경계점(node, offset)이 줄 글의 몇째 UTF-16 위치인지. 줄 앞이면 0, 줄 뒤면 글 길이.
function offsetInLine(lineRange, length, node, offset) {{
  const order = lineRange.comparePoint(node, offset);
  if (order !== 0) return order < 0 ? 0 : length;
  const head = lineRange.cloneRange();
  head.setEnd(node, offset);
  return head.toString().length;
}}
function bandGroup(layer) {{
  let group = layer.querySelector(':scope > .bands');
  if (!group) {{
    group = document.createElementNS(SVG_NS, 'g');
    group.setAttribute('class', 'bands');
    layer.prepend(group);
  }}
  return group;
}}
function lineBand(line, lineRange, range) {{
  const length = line.textContent.length;
  const start = offsetInLine(lineRange, length, range.startContainer, range.startOffset);
  const end = offsetInLine(lineRange, length, range.endContainer, range.endOffset);
  if (start >= end) return null;
  // 고른 단위가 든 글자 묶음을 모두 덮는다(이은 자모·대리쌍의 일부만 골라도 묶음 전체).
  const {{lefts, rights}} = unitsOf(line);
  const left = Math.min(...lefts.slice(start, end)), right = Math.max(...rights.slice(start, end));
  if (right <= left) return null;
  const [top, height] = line.dataset.band.split(' ').map(Number);
  const rect = document.createElementNS(SVG_NS, 'rect');
  rect.setAttribute('x', left);
  rect.setAttribute('y', top);
  rect.setAttribute('width', right - left);
  rect.setAttribute('height', height);
  return rect;
}}
function drawPageBands(layer, ranges) {{
  const rects = [];
  for (const line of layer.querySelectorAll('.ln[data-band]')) {{
    const hits = ranges.filter((range) => range.intersectsNode(line));
    if (!hits.length) continue;
    const lineRange = document.createRange();
    lineRange.selectNodeContents(line);
    for (const range of hits) {{
      const rect = lineBand(line, lineRange, range);
      if (rect) rects.push(rect);
    }}
  }}
  bandGroup(layer).replaceChildren(...rects);
}}
function selectedRanges() {{
  const selection = document.getSelection();
  const ranges = [];
  for (let k = 0; selection && k < selection.rangeCount; k += 1) {{
    const range = selection.getRangeAt(k);
    if (!range.collapsed) ranges.push(range);
  }}
  return ranges;
}}
function drawBands() {{
  bandFrame = 0;
  const ranges = selectedRanges();
  const touched = (layer) => shownPages.has(layer) && ranges.some((range) => range.intersectsNode(layer));
  for (const layer of bandedPages) {{
    if (touched(layer)) continue;
    bandGroup(layer).replaceChildren();
    bandedPages.delete(layer);
  }}
  for (const layer of shownPages) {{
    if (!touched(layer)) continue;
    drawPageBands(layer, ranges);
    bandedPages.add(layer);
  }}
}}
const pageWatch = new IntersectionObserver((entries) => {{
  for (const entry of entries) {{
    const layer = entry.target.querySelector('svg.text-layer');
    if (entry.isIntersecting) shownPages.add(layer);
    else shownPages.delete(layer);
  }}
  scheduleBands();
}});
// content-visibility로 건너뛴 쪽 안의 요소 대신 쪽 자체를 관찰한다.
for (const layer of document.querySelectorAll('svg.text-layer')) pageWatch.observe(layer.closest('.page'));
document.addEventListener('selectionchange', scheduleBands);
// 수식 글꼴이 이 PC에 없으면(PUA 글자 폭이 대체 글꼴과 같으면) 유니코드 수식으로 그린 쪽으로 바꾼다.
function hasFont(name) {{
  const context = document.createElement('canvas').getContext('2d');
  const sample = '\ue0e5\ue048\ue06d\ue05c\ue044';
  context.font = '40px monospace';
  const fallback = context.measureText(sample).width;
  context.font = `40px "${{name}}", monospace`;
  return context.measureText(sample).width !== fallback;
}}
for (const img of document.querySelectorAll('img[data-eq-fallback]')) {{
  if (!img.dataset.eqFonts.split(' ').every(hasFont)) img.src = img.dataset.eqFallback;
}}
</script>
</body>
</html>
"""


def _style_key(glyph: Glyph) -> tuple:
    return (glyph_family_and_text(glyph)[0], glyph.size, tuple(glyph_style_attrs(glyph)))


def _font_attrs(key: tuple) -> str:
    family, size, style = key
    return " ".join([f'font-family={family} font-size="{format_pt(size)}"', *style])


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _markup_text(text: str) -> str:
    # HTML 파서는 글 속 \r을 \n으로 바꾸므로 문자 참조로 넣어 DOM 글을 copy_text와 같게 둔다.
    return escape(text).replace("\r", "&#13;")


def _glyph_box(line: TextLine) -> tuple[float, float]:
    """그린 글자들의 위·아래 끝. layout처럼 글자 원점 위로 크기 × TAC_ASCENT, 아래로 나머지를 차지한다."""
    drawn = [(glyph.y, glyph.size) for glyph in line.glyphs if glyph is not None] or [(line.baseline, line.size)]
    return min(y - size * TAC_ASCENT for y, size in drawn), max(y + size * (1 - TAC_ASCENT) for y, size in drawn)


def _bands(lines: list[TextLine]) -> list[str]:
    """줄마다 data-band 속성. layout 줄 몫(top·bottom)과 그린 글자 상자를 합친 띠의 위 끝과 높이다."""
    boxes = [_glyph_box(line) for line in lines]
    spans = []
    for line, (glyph_top, glyph_bottom) in zip(lines, boxes, strict=True):
        if line.top is not None and line.bottom is not None:
            spans.append([min(line.top, glyph_top), max(line.bottom, glyph_bottom)])
        else:
            spans.append([glyph_top, glyph_bottom])
    for k, (above, below) in enumerate(zip(lines, lines[1:], strict=False)):
        same = above.paragraph is not None and above.paragraph == below.paragraph
        if not same or above.bottom is None or spans[k][1] <= spans[k + 1][0]:
            continue
        # 같은 문단 이웃 줄 띠가 겹치면(글자 위치 오프셋으로 올린 글자 등) 경계를 저장 줄 경계에 가장 가깝게 두되
        # 윗줄 글자 아래 끝과 아랫줄 글자 위 끝 사이로 옮긴다. 글자 상자끼리 겹치면 겹친 채 둔다.
        # 한계: 덧말·줄 안 표·글상자 줄이 사이에 끼면 이웃으로 보지 않아 겹친 띠가 진하게 남을 수 있다.
        low, high = boxes[k][1], boxes[k + 1][0]
        if low <= high:
            spans[k][1] = spans[k + 1][0] = min(max(above.bottom, low), high)
    return [f' data-band="{format_pt(top)} {format_pt(bottom - top)}"' for top, bottom in spans]


def _fit_starts(line: TextLine, shown: str) -> list[float | None] | None:
    """줄 폭 맞춤 줄의 UTF-16 단위별 시작 x(글자 안 뒤 단위는 None). char_x가 없으면 고르게 나눈다. 폭이 없으면 None."""
    unit_x: list[float | None] = []
    if len(line.char_x) == len(line.text):
        for char, x in zip(line.text, line.char_x, strict=True):
            for k, part in enumerate(copy_text(char)):
                unit_x += [None if k else x] + [None] * (_utf16_len(part) - 1)
    if len(unit_x) != _utf16_len(shown):
        count = _utf16_len(shown)
        unit_x = [line.x + line.width * k / count for k in range(count)]
    placed = [x for x in unit_x if x is not None] + [line.x + line.width]
    return unit_x if max(placed) > min(placed) else None


def _fit_line(line: TextLine, band: str = "") -> str:
    """글자별 자리가 없거나 그린 글자와 하나씩 맞지 않는 줄(수식 스크립트)은 줄 폭에 맞춰 늘이거나 줄인다."""
    shown = copy_text(line.text)
    starts = _fit_starts(line, shown)
    # 띠를 그릴 수 없는 줄(폭 0)은 띠 자료를 넣지 않아 브라우저 선택 칠하기를 그대로 둔다.
    if starts is not None and band:
        xs = " ".join("-" if x is None else format_pt(x) for x in starts)
        band = f' data-x="{xs}" data-end="{format_pt(line.x + line.width)}"{band}'
    else:
        band = ""
    length = f' textLength="{format_pt(line.width)}" lengthAdjust="spacingAndGlyphs"' if line.width > 0 else ""
    return (
        f'<text class="ln fit" x="{format_pt(line.x)}" y="{format_pt(line.baseline)}" '
        f'font-size="{format_pt(line.size)}"{length}{band}>'
        f"{_markup_text(shown)}</text>"
    )


class _LayerChar(NamedTuple):
    text: str
    #: None이면 앞 글자와 한 글리프로 그린 글자(이은 자모)라 x·y를 주지 않고 앞 글자에 이어 흐르게 둔다.
    x: float | None
    y: float
    font: tuple
    #: 장평. 줄 글 글리프는 make_glyph로만 만들어 회전이 없다(회전 글자는 세로쓰기·그리기 개체·차트뿐, 줄 글 없음).
    scale: float


def _glyph_line(line: TextLine, band: str = "") -> str | None:
    """그린 글자와 같은 x·y·글꼴·크기·장평으로 놓은 줄. 글자별 위치가 없거나 글리프 없는 글자가 보이면 None."""
    if not len(line.char_x) == len(line.glyphs) == len(line.text):
        return None
    drawn = [glyph for glyph in line.glyphs if glyph is not None]
    if not drawn:
        return None
    chars: list[_LayerChar] = []
    # 공백·탭은 앞 글자(줄 머리면 뒤 글자) 모양으로 기준선에 둔다.
    style: Glyph = drawn[0]
    font, scale = _style_key(style), round(style.scale_x, 4)
    previous: Glyph | None = None
    for char, x, glyph in zip(line.text, line.char_x, line.glyphs, strict=True):
        shown = copy_text(char)
        # 복사 글에서 지워지는 제어 문자는 그린 글리프가 없어도 줄을 글자별로 놓는 데 방해되지 않는다.
        if glyph is None and shown.strip():
            return None
        # 회전 글자는 레이아웃 줄 글에 없지만 바깥 코드가 만든 TextLine에는 올 수 있어 줄 폭 맞춤으로 덮는다.
        if glyph is not None and glyph.rotation:
            return None
        joined = glyph is not None and glyph is previous
        previous = glyph
        if glyph is not None and glyph is not style:
            style = glyph
            font, scale = _style_key(style), round(style.scale_x, 4)
        if not shown:
            continue
        y = glyph.y if glyph is not None else line.baseline
        # 이은 자모와 여러 자모로 풀린 옛한글 PUA의 뒤 글자에도 같은 x를 주면 Chrome이 자모를 하나씩 겹쳐 놓는다.
        for k, part in enumerate(shown):
            own_x = None if joined or k else x
            chars.append(_LayerChar(part, own_x, y, font, scale))
    groups: list[list[_LayerChar]] = []
    for char in chars:
        if groups and groups[-1][0].scale == char.scale:
            groups[-1].append(char)
        else:
            groups.append([char])
    # 경계는 스크립트가 tspan x와 줄 끝(data-end)으로 만든다. 폭 0 줄은 띠 대신 브라우저 선택 칠하기를 둔다.
    if band and line.width > 0:
        band = f' data-end="{format_pt(line.x + line.width)}"{band}'
    else:
        band = ""
    attrs = f' class="ln"{band}'
    if len(groups) == 1:
        return _transformed_text(groups[0], attrs)
    return f"<g{attrs}>{''.join(_transformed_text(group) for group in groups)}</g>"


def _transformed_text(group: list[_LayerChar], attrs: str = "") -> str:
    """장평은 원점을 첫 글자 x에 두고 가로로만 줄이므로 글자 x를 그 비율로 나눈다(그린 글자와 같은 폭)."""
    scale = group[0].scale
    if scale == 1.0:
        origin_x, transform = 0.0, ""
    else:
        # 장평이 바뀌는 곳은 글리프가 바뀌는 곳이라 묶음 첫 글자는 늘 자기 x가 있다.
        origin_x = group[0].x or 0.0
        transform = f' transform="translate({format_pt(origin_x)} 0) scale({scale:.4f} 1)"'
    # tspan 하나의 x·y 목록은 앞 글자들에만 주고, 목록보다 많은 뒤 글자(이은 자모)는 앞 글자에 이어 흐른다.
    runs: list[list[_LayerChar]] = []
    for char in group:
        last = runs[-1] if runs else None
        if last is None or char.font != last[0].font or (char.x is not None and last[-1].x is None):
            runs.append([char])
        else:
            last.append(char)
    spans = []
    for run in runs:
        placed = [char for char in run if char.x is not None]
        xs = " ".join(format_pt((char.x - origin_x) / scale) for char in placed)
        ys = " ".join(format_pt(char.y) for char in placed)
        position = f'x="{xs}" y="{ys}" ' if placed else ""
        text = _markup_text("".join(char.text for char in run))
        spans.append(f"<tspan {position}{_font_attrs(run[0].font)}>{text}</tspan>")
    return f"<text{attrs}{transform}>{''.join(spans)}</text>"


def text_layer(lines: list[TextLine], width: float, height: float) -> str:
    """쪽 크기(width·height, HWPUNIT) viewBox의 투명한 SVG 글 층. 줄마다 .ln 하나이며 복사하면 줄이 나뉜다."""
    body = "".join(
        _glyph_line(line, band) or _fit_line(line, band) for line, band in zip(lines, _bands(lines), strict=True)
    )
    return f'<svg class="text-layer" viewBox="0 0 {format_pt(width)} {format_pt(height)}">{body}</svg>'


def _fallback_attrs(fallback: tuple[str, list[str]] | None) -> str:
    if fallback is None:
        return ""
    path, fonts = fallback
    return f' data-eq-fallback="{escape(path)}" data-eq-fonts="{escape(" ".join(fonts))}"'


def viewer_html(
    title: str,
    svg_files: list[tuple[str, float, float]],
    text_layers: list[str] | None = None,
    equation_fallbacks: list[tuple[str, list[str]] | None] | None = None,
) -> str:
    """svg_files: (상대 경로, 너비 pt, 높이 pt) 목록. text_layers는 쪽마다 text_layer() 결과(없으면 그림만).

    equation_fallbacks는 쪽마다 (수식 대체 SVG 경로, 쪽에 쓴 수식 글꼴) 또는 None이다.
    """
    layers = text_layers or [""] * len(svg_files)
    fallbacks = equation_fallbacks or [None] * len(svg_files)
    pages = "\n".join(
        f'<figure class="page" style="--w: {width * 96 / 72:.1f}px; aspect-ratio: {width:g} / {height:g}; margin: 0">'
        f'<img src="{escape(path)}" alt="{index}쪽" width="{width * 96 / 72:.0f}" '
        f'height="{height * 96 / 72:.0f}" loading="lazy"{_fallback_attrs(fallback)}>'
        f"{layer}"
        f'</figure><div class="label">{index}</div>'
        for index, ((path, width, height), layer, fallback) in enumerate(
            zip(svg_files, layers, fallbacks, strict=True), 1
        )
    )
    return _TEMPLATE.format(title=escape(title), count=len(svg_files), pages=pages)
