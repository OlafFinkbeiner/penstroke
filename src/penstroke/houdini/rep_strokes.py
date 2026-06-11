"""Build the `strokes` rep of an hfont: penstroke centerlines -> em space.

Run under hython (needs `hou`):

    hython src/penstroke/houdini/rep_strokes.py <strokes.json> <bundle.hfont>

The strokes rep is the handwriting representation: penstroke's traced
(and hand-edited) stroke decomposition as open polylines in writing
order — one packed prim per glyph, keyed by post glyph name, em space
like every rep.

Source is a stroke store (strokes.json: {char: [{x, y, w}, ...]}) in
canvas-pixel coordinates. The canvas frame is reconstructed from the
bundle's TTF with the same formula core/rasterize.py uses: 1 em =
`size` px, origin at (pad, baseline_y), y down. Defaults match
trace_font's defaults; pass --size/--pad if the trace used others.

Geometry per glyph: one open polyline per stroke, in writing order.
Point attributes `width` (em) and `u` (0..1 along the stroke); prim
attributes `stroke_index` and `arclength` (em) — everything a draw-on
animation needs, with timing left art-directable downstream.
"""

import os
import sys

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import argparse
import json
import math

from penstroke import hfont

REP_NAME = 'strokes'
REP_KIND = 'centerline'
GEO_RELPATH = os.path.join('reps', REP_NAME, 'glyphs.bgeo.sc')

# trace_font defaults (pipeline size=384, rasterize_glyph pad=40).
DEFAULT_TRACE_SIZE = 384
DEFAULT_TRACE_PAD = 40


def canvas_to_em_transform(ttf_path, size, pad):
    """(fn(x_px, y_px) -> (x_em, y_em), width_scale) for the trace canvas.

    Mirrors core/rasterize.py: px_per_em = size/upem, baseline_y =
    pad + round(ascent_px), glyph origin x = pad, y down.
    """
    from fontTools.ttLib import TTFont
    tt = TTFont(ttf_path)
    upem = tt['head'].unitsPerEm
    ascent_px = int(round(tt['hhea'].ascent * size / upem))
    baseline_y = pad + ascent_px

    def to_em(x_px, y_px):
        return ((x_px - pad) / size, (baseline_y - y_px) / size)

    return to_em, 1.0 / size


def load_store(path):
    """{char: [(xs, ys, ws), ...]} from a strokes.json."""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    return {ch: [(s['x'], s['y'], s['w']) for s in strokes]
            for ch, strokes in data.items()}


def build_glyph_geometry(strokes, to_em, w_scale):
    """One glyph's strokes as a hou.Geometry of open polylines."""
    import hou
    geo = hou.Geometry()
    w_attr = geo.addAttrib(hou.attribType.Point, 'width', 0.0)
    u_attr = geo.addAttrib(hou.attribType.Point, 'u', 0.0)
    si_attr = geo.addAttrib(hou.attribType.Prim, 'stroke_index', 0)
    al_attr = geo.addAttrib(hou.attribType.Prim, 'arclength', 0.0)
    for si, (xs, ys, ws) in enumerate(strokes):
        n = len(xs)
        if n < 2:
            continue
        poly = geo.createPolygon(is_closed=False)
        length = 0.0
        prev = None
        for i in range(n):
            x, y = to_em(xs[i], ys[i])
            pt = geo.createPoint()
            pt.setPosition((x, y, 0.0))
            pt.setAttribValue(w_attr, ws[i] * w_scale)
            pt.setAttribValue(u_attr, i / (n - 1))
            poly.addVertex(pt)
            if prev is not None:
                length += math.hypot(x - prev[0], y - prev[1])
            prev = (x, y)
        poly.setAttribValue(si_attr, si)
        poly.setAttribValue(al_attr, length)
    return geo


def build_strokes_rep(store_path, bundle_dir, size=DEFAULT_TRACE_SIZE,
                      pad=DEFAULT_TRACE_PAD, verbose=True):
    """Build the strokes rep into an existing bundle (font.ttf present)."""
    import hou
    from fontTools.ttLib import TTFont

    bundle_font = os.path.join(bundle_dir, hfont.FONT_NAME)
    if not os.path.exists(bundle_font):
        raise hfont.HFontError(
            f'{bundle_dir}: no {hfont.FONT_NAME} — create the bundle first '
            '(rep_outline does, or hfont.create_bundle)')

    store = load_store(store_path)
    to_em, w_scale = canvas_to_em_transform(bundle_font, size, pad)
    cmap = TTFont(bundle_font).getBestCmap()

    container = hou.Geometry()
    name_attr = container.addAttrib(hou.attribType.Prim, 'name', '')

    built = []   # (glyph_name, em-space strokes) for the QA sheet
    seen = set()
    skipped = []
    for ch, strokes in store.items():
        gname = cmap.get(ord(ch))
        if gname is None or gname in seen or not strokes:
            if gname is None:
                skipped.append(ch)
            continue
        seen.add(gname)
        glyph_geo = build_glyph_geometry(strokes, to_em, w_scale)
        point = container.createPoint()
        packed = container.createPackedGeometry(glyph_geo, point)
        packed.setAttribValue(name_attr, gname)
        built.append((gname, [
            [to_em(x, y) for x, y in zip(s[0], s[1])] for s in strokes]))
        if verbose:
            print(f'  {gname}: {len(strokes)} strokes')
    if skipped:
        print(f'  skipped (not in cmap): {"".join(skipped)}')

    geo_path = os.path.join(bundle_dir, GEO_RELPATH)
    os.makedirs(os.path.dirname(geo_path), exist_ok=True)
    container.saveToFile(geo_path)

    hfont.register_rep(
        bundle_dir, REP_NAME, REP_KIND, GEO_RELPATH,
        attributes={'point': ['width', 'u'],
                    'prim': ['stroke_index', 'arclength']},
        provenance={'builder': 'rep_strokes',
                    'houdini': hou.applicationVersionString(),
                    'store': os.path.basename(store_path),
                    'trace_size': size, 'trace_pad': pad},
        make_default=True)

    contact_sheet(built, os.path.join(bundle_dir, 'qa', 'strokes.png'))
    return len(built), geo_path


def contact_sheet(built, png_path, cols=13, cell=96):
    """Grid of stroke centerlines, rainbow per stroke order."""
    from PIL import Image, ImageDraw
    if not built:
        return
    os.makedirs(os.path.dirname(png_path), exist_ok=True)
    palette = ['#cc3333', '#dd8800', '#888800', '#118833', '#1188aa',
               '#3344cc', '#8833aa']
    rows = (len(built) + cols - 1) // cols
    img = Image.new('RGB', (cols * cell, rows * cell), 'white')
    draw = ImageDraw.Draw(img)
    s = cell * 0.62
    for i, (gname, strokes) in enumerate(built):
        ox = (i % cols) * cell + cell * 0.15
        oy = (i // cols) * cell + cell * 0.75
        for si, stroke in enumerate(strokes):
            pix = [(ox + x * s, oy - y * s) for (x, y) in stroke]
            if len(pix) > 1:
                draw.line(pix, fill=palette[si % len(palette)], width=2)
        draw.text((ox, oy + cell * 0.08), gname[:10], fill='gray')
    img.save(png_path)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Build the strokes rep of an hfont bundle (hython).')
    ap.add_argument('store', help='strokes.json (stroke store).')
    ap.add_argument('bundle', help='Existing bundle directory.')
    ap.add_argument('--size', type=int, default=DEFAULT_TRACE_SIZE,
                    help='Trace rasterization size in px (default 384).')
    ap.add_argument('--pad', type=int, default=DEFAULT_TRACE_PAD,
                    help='Trace canvas pad in px (default 40).')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args(argv)

    n, geo_path = build_strokes_rep(args.store, args.bundle,
                                    size=args.size, pad=args.pad,
                                    verbose=not args.quiet)
    problems = hfont.validate(args.bundle)
    print(f'{n} glyphs -> {geo_path}')
    if problems:
        print('VALIDATION PROBLEMS:')
        for p in problems:
            print(f'  - {p}')
        return 1
    print('bundle valid')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
