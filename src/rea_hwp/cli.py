"""rea-hwp 명령: HWP 5.0 문서를 쪽 SVG와 HTML 뷰어로 렌더링하거나 구조를 점검한다."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .document import HwpDocument
from .render.html import text_layer, viewer_html
from .render.layout import layout_document
from .render.options import RenderOptions
from .render.svg import HU_PER_PT, equation_fonts, page_glyph_table, page_to_svg


def render(args: argparse.Namespace) -> int:
    document = HwpDocument.open(args.file, args.password)
    pages = layout_document(document, RenderOptions.from_environment())
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    listing = []
    layers = []
    glyphs = []
    fallbacks = []
    for page in pages:
        name = f"page-{page.number:04d}.svg"
        (output / name).write_text(page_to_svg(page), encoding="utf-8")
        listing.append((name, page.width / HU_PER_PT, page.height / HU_PER_PT))
        layers.append(text_layer(page.text_lines, page.width, page.height))
        glyphs.append(page_glyph_table(page))
        fonts = equation_fonts(page)
        if fonts:
            # 수식 글꼴이 없는 PC에서 뷰어가 바꿔 보여 줄 쪽(유니코드 글자 + OFL 수학 글꼴 이름 사슬)
            fallback = f"page-{page.number:04d}.eq.svg"
            (output / fallback).write_text(page_to_svg(page, equation_fallback=True), encoding="utf-8")
            fallbacks.append((fallback, sorted(fonts)))
        else:
            fallbacks.append(None)
    html = viewer_html(Path(args.file).name, listing, layers, fallbacks)
    (output / "index.html").write_text(html, encoding="utf-8")
    if args.glyphs:
        (output / "glyphs.json").write_text(json.dumps(glyphs, ensure_ascii=False), encoding="utf-8")
    print(f"{len(pages)} pages -> {output / 'index.html'}")
    return 0


def check(args: argparse.Namespace) -> int:
    """레코드를 모델로 다시 인코딩해 원본 스트림과 같은지, 원본 바이트로만 남은 레코드가 몇 개인지 보여 준다."""
    document = HwpDocument.open(args.file, args.password)
    exit_code = 0
    for stream in document.record_streams:
        same = stream.to_bytes() == document.container.record_stream(stream.name)
        exit_code |= 0 if same else 1
        print(f"{stream.name}: {'roundtrip ok' if same else 'ROUNDTRIP MISMATCH'}")
    raw = document.raw_records()
    print(f"records kept as raw bytes: {len(raw)}")
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rea-hwp", description="HWP 5.0 parser and page renderer")
    commands = parser.add_subparsers(dest="command", required=True)
    render_parser = commands.add_parser("render", help="render pages to SVG plus an HTML viewer")
    render_parser.add_argument("file")
    render_parser.add_argument("-o", "--output", required=True, help="output directory")
    render_parser.add_argument("--password", help="open password for encrypted documents")
    render_parser.add_argument("--glyphs", action="store_true", help="also write glyph origins (pt) as glyphs.json")
    render_parser.set_defaults(handler=render)
    check_parser = commands.add_parser("check", help="verify lossless record roundtrip")
    check_parser.add_argument("file")
    check_parser.add_argument("--password")
    check_parser.set_defaults(handler=check)
    args = parser.parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
