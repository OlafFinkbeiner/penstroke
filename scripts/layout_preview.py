"""Render a layout.py result to PNG — standalone visual QA, no Houdini.

    python scripts/layout_preview.py <ttf> <out.png> [--width-em 14]
                                     [--align justify] [--text ...]

Glyph outlines come from the same fontTools extraction the outline rep
uses, so what you see is what the hfont will carry. Vertical guides
mark the measure (wrap width) to make justification visually checkable.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from penstroke.layout import layout, get_font
from penstroke.houdini.rep_outline import (extract_glyph_contours,
                                           _flatten_contour)

DEFAULT_TEXT = ('the quick brown fox jumps over the lazy dog '
                'while penstroke shapes every word with real kerning '
                'and justifies the measure')


def render(out, ttf_path, png_path, measure=None, px_per_em=110):
    from fontTools.ttLib import TTFont
    from PIL import Image, ImageDraw

    tt = TTFont(ttf_path)
    scale = 1.0 / tt['head'].unitsPerEm
    glyph_set = tt.getGlyphSet()
    cache = {}

    margin = px_per_em
    width_px = int((measure if measure else max(out.line_widths, default=1))
                   * px_per_em) + 2 * margin
    height_px = int(out.n_lines * 1.2 * out.size * px_per_em) + 2 * margin
    img = Image.new('RGB', (width_px, height_px), 'white')
    draw = ImageDraw.Draw(img)

    if measure:
        for gx in (margin, margin + measure * px_per_em):
            draw.line([(gx, 0), (gx, height_px)], fill='#bbccee', width=2)

    for gname, (gx, gy) in zip(out.names, out.positions):
        if gname not in cache:
            cache[gname] = [_flatten_contour(c) for c in
                            extract_glyph_contours(glyph_set, gname, scale)]
        for poly in cache[gname]:
            pix = [(margin + (gx + x * out.size) * px_per_em,
                    margin - (gy + y * out.size) * px_per_em)
                   for (x, y) in poly]
            draw.line(pix + pix[:1], fill='black', width=2)
    img.save(png_path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('ttf')
    ap.add_argument('png')
    ap.add_argument('--text', default=DEFAULT_TEXT)
    ap.add_argument('--width-em', type=float, default=14.0)
    ap.add_argument('--align', default='justify',
                    choices=['left', 'center', 'right', 'justify'])
    args = ap.parse_args(argv)

    out = layout(args.text, args.ttf, size=1.0, width=args.width_em,
                 align=args.align)
    render(out, args.ttf, args.png, measure=args.width_em)
    print(f'{len(out.names)} glyphs, {out.n_lines} lines -> {args.png}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
