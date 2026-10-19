"""쪽 SVG 파일들을 한 화면에서 넘겨 보는 단독 HTML 뷰어."""

from __future__ import annotations

from html import escape

from .scene import TextLine
from .svg import display_text

#: 줄 글 층의 글자 위 끝 = 기준선 − 글자 크기 × 이 비율(그리는 글자의 기준선 비율과 같다).
_ASCENT = 0.85

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
.page {{ position: relative; container-type: inline-size; background: #fff; box-shadow: var(--paper-shadow);
         width: calc(var(--zoom) * var(--w)); max-width: calc(100vw - 32px); }}
.page img {{ display: block; width: 100%; height: auto; user-select: none; }}
.text-layer {{ position: absolute; inset: 0; overflow: hidden; line-height: 1; }}
.text-layer span {{ position: absolute; white-space: pre; color: transparent; transform-origin: 0 0;
                    font-family: system-ui, sans-serif; cursor: text; }}
.text-layer span::selection {{ background: rgba(6, 55, 123, .3); }}
.text-layer br::selection {{ background: transparent; }}
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
// 줄 글 층의 각 줄을 그 줄이 그려진 폭(data-w, 쪽 폭에 대한 비율)으로 늘이거나 줄여 그림 글자와 겹친다.
function fitTextLayers() {{
  for (const page of document.querySelectorAll('.page')) {{
    const width = page.clientWidth;
    for (const span of page.querySelectorAll('.text-layer span')) {{
      span.style.transform = '';
      const natural = span.getBoundingClientRect().width;
      const target = Number(span.dataset.w) * width;
      if (natural > 0 && target > 0) span.style.transform = `scaleX(${{target / natural}})`;
    }}
  }}
}}
document.getElementById('zoom').addEventListener('change', (event) => {{
  document.body.style.setProperty('--zoom', event.target.value);
  fitTextLayers();
}});
window.addEventListener('resize', fitTextLayers);
document.fonts.ready.then(fitTextLayers);
</script>
</body>
</html>
"""


def _percent(value: float) -> str:
    return f"{value * 100:.4f}".rstrip("0").rstrip(".") + "%"


def text_layer(lines: list[TextLine], width: float, height: float) -> str:
    """쪽 크기(width·height, HWPUNIT)에 대한 비율로 놓은 투명한 줄 글. 줄마다 <br>을 두어 복사하면 줄이 나뉜다."""
    spans = []
    for line in lines:
        top = (line.baseline - line.size * _ASCENT) / height
        style = f"left: {_percent(line.x / width)}; top: {_percent(top)}; font-size: {line.size / width * 100:.4f}cqw"
        text = escape(display_text(line.text))
        spans.append(f'<span style="{style}" data-w="{line.width / width:.6f}">{text}</span><br>')
    return f'<div class="text-layer">{"".join(spans)}</div>'


def viewer_html(title: str, svg_files: list[tuple[str, float, float]], text_layers: list[str] | None = None) -> str:
    """svg_files: (상대 경로, 너비 pt, 높이 pt) 목록. text_layers는 쪽마다 text_layer() 결과(없으면 그림만)."""
    layers = text_layers or [""] * len(svg_files)
    pages = "\n".join(
        f'<figure class="page" style="--w: {width * 96 / 72:.1f}px; margin: 0">'
        f'<img src="{escape(path)}" alt="{index}쪽" width="{width * 96 / 72:.0f}" '
        f'height="{height * 96 / 72:.0f}" loading="lazy">{layer}'
        f'</figure><div class="label">{index}</div>'
        for index, ((path, width, height), layer) in enumerate(zip(svg_files, layers, strict=True), 1)
    )
    return _TEMPLATE.format(title=escape(title), count=len(svg_files), pages=pages)
