"""쪽 SVG 파일들을 한 화면에서 넘겨 보는 단독 HTML 뷰어."""

from __future__ import annotations

from html import escape
from typing import NamedTuple

from .scene import Glyph, TextLine
from .svg import copy_text, display_font, format_pt

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
.page {{ position: relative; background: #fff; box-shadow: var(--paper-shadow);
         width: calc(var(--zoom) * var(--w)); max-width: calc(100vw - 32px); }}
.page img {{ display: block; width: 100%; height: auto; user-select: none; }}
.text-layer {{ position: absolute; inset: 0; width: 100%; height: 100%; overflow: hidden; }}
.text-layer text {{ fill: transparent; white-space: pre; cursor: text; }}
.text-layer .fit {{ font-family: system-ui, sans-serif; }}
.text-layer ::selection {{ fill: transparent; background: rgba(6, 55, 123, .3); }}
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
// 글 층은 화면 가까운 쪽부터 붙이고 나머지는 한가할 때 붙인다(편람 384쪽: 한꺼번에 7.3초, 먼저 보이는 쪽만 2.8초).
// 나머지도 붙여야 브라우저 찾기가 모든 쪽 글을 찾는다. 배치 계산은 다음 프레임에 몰리므로 한 번에 한 쪽씩 붙인다.
function attachLayer(page) {{
  const template = page.querySelector('template.text-layer-source');
  if (template) page.replaceChild(template.content.cloneNode(true), template);
}}
const layerObserver = new IntersectionObserver((entries) => {{
  for (const entry of entries) {{
    if (!entry.isIntersecting) continue;
    attachLayer(entry.target);
    layerObserver.unobserve(entry.target);
  }}
}}, {{ rootMargin: '200% 0px' }});
const waitingPages = [...document.querySelectorAll('.page')];
for (const page of waitingPages) layerObserver.observe(page);
const whenIdle = window.requestIdleCallback || ((work) => setTimeout(work, 50));
function attachWhenIdle() {{
  if (waitingPages.length) attachLayer(waitingPages.shift());
  if (waitingPages.length) whenIdle(attachWhenIdle);
}}
whenIdle(attachWhenIdle);
// 줄마다 따로 놓인 SVG 글은 브라우저 기본 복사로는 줄바꿈 없이 붙으므로, 고른 범위를 줄 단위로 모아 줄바꿈으로 잇는다.
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
document.addEventListener('copy', (event) => {{
  const selection = document.getSelection();
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return;
  const range = selection.getRangeAt(0);
  const lines = [...document.querySelectorAll('.text-layer .ln')].filter((line) => range.intersectsNode(line));
  if (!lines.length) return;
  event.clipboardData.setData('text/plain', lines.map((line) => selectedText(line, range)).join('\\n'));
  event.preventDefault();
}});
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
    return (display_font(glyph.char, glyph.font_family), glyph.size, glyph.bold, glyph.italic)


def _font_attrs(key: tuple) -> str:
    family, size, bold, italic = key
    attrs = f'font-family="{escape(family)}" font-size="{format_pt(size)}"'
    if bold:
        attrs += ' font-weight="bold"'
    if italic:
        attrs += ' font-style="italic"'
    return attrs


def _fit_line(line: TextLine) -> str:
    """글자별 자리가 없거나 그린 글자와 하나씩 맞지 않는 줄(수식 스크립트)은 줄 폭에 맞춰 늘이거나 줄인다."""
    text = escape(copy_text(line.text))
    length = f' textLength="{format_pt(line.width)}" lengthAdjust="spacingAndGlyphs"' if line.width > 0 else ""
    return (
        f'<text class="ln fit" x="{format_pt(line.x)}" y="{format_pt(line.baseline)}" '
        f'font-size="{format_pt(line.size)}"{length}>'
        f"{text}</text>"
    )


class _LayerChar(NamedTuple):
    text: str
    #: None이면 앞 글자와 한 글리프로 그린 글자(이은 자모)라 x·y를 주지 않고 앞 글자에 이어 흐르게 둔다.
    x: float | None
    y: float
    font: tuple
    #: (장평, 회전 각도).
    transform: tuple[float, float]


def _glyph_line(line: TextLine) -> str | None:
    """글자마다 그린 글자와 같은 x·y·글꼴·크기·장평으로 놓은 줄. 그린 글리프가 없는 보이는 글자가 있으면 None."""
    pairs = list(zip(line.text, line.char_x, line.glyphs, strict=False))
    if len(line.char_x) != len(line.text) or any(glyph is None and char.strip() for char, _, glyph in pairs):
        return None
    drawn = [glyph for glyph in line.glyphs if glyph is not None]
    if not drawn:
        return None
    chars: list[_LayerChar] = []
    # 공백·탭은 앞 글자(줄 머리면 뒤 글자) 모양으로 기준선에 둔다.
    style: Glyph = drawn[0]
    font, transform = _style_key(style), (round(style.scale_x, 4), style.rotation)
    previous: Glyph | None = None
    for char, x, glyph in pairs:
        shown = copy_text(char)
        joined = glyph is not None and glyph is previous
        previous = glyph
        if glyph is not None and glyph is not style:
            style = glyph
            font, transform = _style_key(style), (round(style.scale_x, 4), style.rotation)
        if not shown:
            continue
        y = glyph.y if glyph is not None else line.baseline
        # 이은 자모와 여러 자모로 풀린 옛한글 PUA의 뒤 글자에도 같은 x를 주면 Chrome이 자모를 하나씩 겹쳐 놓는다.
        for k, part in enumerate(shown):
            own_x = None if joined or k else x
            chars.append(_LayerChar(part, own_x, y, font, transform))
    groups: list[list[_LayerChar]] = []
    for char in chars:
        if groups and groups[-1][0].transform == char.transform and not char.transform[1]:
            groups[-1].append(char)
        else:
            groups.append([char])
    if len(groups) == 1:
        return _transformed_text(groups[0], ' class="ln"')
    return f'<g class="ln">{"".join(_transformed_text(group) for group in groups)}</g>'


def _transformed_text(group: list[_LayerChar], attrs: str = "") -> str:
    """장평은 원점을 첫 글자 x에 두고 가로로만 줄이므로 글자 x를 그 비율로 나눈다(그린 글자와 같은 폭)."""
    scale, rotation = group[0].transform
    origin_x, origin_y = group[0].x or 0.0, group[0].y
    if scale == 1.0 and not rotation:
        transform, origin_x, origin_y = "", 0.0, 0.0
    elif not rotation:
        transform, origin_y = f' transform="translate({format_pt(origin_x)} 0) scale({scale:.4f} 1)"', 0.0
    else:
        scale = 1.0
        transform = (
            f' transform="translate({format_pt(origin_x)} {format_pt(origin_y)}) '
            f'rotate({-rotation:g}) scale({group[0].transform[0]:.4f} 1)"'
        )
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
        ys = " ".join(format_pt(char.y - origin_y) for char in placed)
        position = f'x="{xs}" y="{ys}" ' if placed else ""
        text = escape("".join(char.text for char in run))
        spans.append(f"<tspan {position}{_font_attrs(run[0].font)}>{text}</tspan>")
    return f"<text{attrs}{transform}>{''.join(spans)}</text>"


def text_layer(lines: list[TextLine], width: float, height: float) -> str:
    """쪽 크기(width·height, HWPUNIT) viewBox의 투명한 SVG 글 층. 줄마다 .ln 하나이며 복사하면 줄이 나뉜다."""
    body = "".join(_glyph_line(line) or _fit_line(line) for line in lines)
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
        f'<figure class="page" style="--w: {width * 96 / 72:.1f}px; margin: 0">'
        f'<img src="{escape(path)}" alt="{index}쪽" width="{width * 96 / 72:.0f}" '
        f'height="{height * 96 / 72:.0f}" loading="lazy"{_fallback_attrs(fallback)}>'
        f"{_layer_source(layer)}"
        f'</figure><div class="label">{index}</div>'
        for index, ((path, width, height), layer, fallback) in enumerate(
            zip(svg_files, layers, fallbacks, strict=True), 1
        )
    )
    return _TEMPLATE.format(title=escape(title), count=len(svg_files), pages=pages)


def _layer_source(layer: str) -> str:
    return f'<template class="text-layer-source">{layer}</template>' if layer else ""
