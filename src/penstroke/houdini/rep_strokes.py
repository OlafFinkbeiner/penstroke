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

# The reduced-curve sibling rep: the SAME stroke store, fitted to cubic
# Beziers (Schneider, via penstroke.curvefit — exactly what the Corel
# export produces), emitted as order-4 Houdini Bezier curves. ~4-19 CVs
# per stroke instead of 240 polyline points. Not the default rep; the
# dense polyline stays default for faithful width/draw-on.
BEZIER_REP_NAME = 'strokes_bezier'
BEZIER_REP_KIND = 'centerline_bezier'
BEZIER_GEO_RELPATH = os.path.join('reps', BEZIER_REP_NAME, 'glyphs.bgeo.sc')

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


def load_store_bez(path):
    """{char: [segments-or-None, ...]} — the exact per-stroke cubics
    saved by hand edits (editround.merge_stroke_bez), or {} if none."""
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    return {ch: [s.get('bez') for s in strokes]
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


def _fit_stroke_cvs(xs, ys, ws, segments=None):
    """One stroke (canvas px) as a cubic-Bezier CV stream.

    With `segments` given (exact hand-edited Corel cubics, each
    [x0,y0,c1x,c1y,c2x,c2y,x1,y1]), use them verbatim — no fitting, so
    the user's handles are preserved. Otherwise fit the polyline
    (Schneider, same as the Corel export).

    Returns (cvs_px, widths_px, us) where cvs_px is the order-4 Bezier
    control-point list (3*S+1 points for S segments), or None if the
    stroke is too short. width/u are carried per CV: on-curve anchors
    sample the nearest original point; the two handles in each segment
    interpolate between their anchors, so u stays monotone for draw-on.
    """
    import numpy as np
    from penstroke import curvefit

    pts = np.column_stack([xs, ys]).astype(float)
    if len(pts) < 2:
        return None
    if segments:
        segs = [(np.array(s[0:2], float), np.array(s[2:4], float),
                 np.array(s[4:6], float), np.array(s[6:8], float))
                for s in segments]
    else:
        smoothed = curvefit.smooth_polyline(pts, curvefit.SMOOTH_WINDOW_PX)
        segs = curvefit.fit_beziers(smoothed, curvefit.STROKE_FIT_TOL_PX)
    if not segs:
        return None

    # Per-original-point arclength fraction + width, for nearest lookup.
    seg_len = np.hypot(np.diff(pts[:, 0]), np.diff(pts[:, 1]))
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])
    ofrac = cum / cum[-1] if cum[-1] > 0 else cum
    ow = np.asarray(ws, dtype=float)

    def anchor_uw(p):
        i = int(np.argmin(np.hypot(pts[:, 0] - p[0], pts[:, 1] - p[1])))
        return float(ofrac[i]), float(ow[i])

    cvs, us, widths = [], [], []
    u0, w0 = anchor_uw(segs[0][0])
    cvs.append(segs[0][0]); us.append(u0); widths.append(w0)
    for (p0, c1, c2, p3) in segs:
        u3, w3 = anchor_uw(p3)
        # handles interpolate between this segment's anchor u/w
        for frac, cv in ((1 / 3, c1), (2 / 3, c2)):
            cvs.append(cv)
            us.append(u0 + (u3 - u0) * frac)
            widths.append(w0 + (w3 - w0) * frac)
        cvs.append(p3); us.append(u3); widths.append(w3)
        u0, w0 = u3, w3
    # Keep u monotone non-decreasing (nearest-point lookup can wobble).
    for i in range(1, len(us)):
        if us[i] < us[i - 1]:
            us[i] = us[i - 1]
    return cvs, widths, us


def build_glyph_geometry_bezier(strokes, to_em, w_scale, bez=None):
    """One glyph's strokes as open order-4 Bezier curves.

    `bez` (optional) is a per-stroke list of exact Corel cubics; where
    present they are used verbatim (handle fidelity), else the stroke
    is fitted. Returns (geo, qa_polys_em) — qa_polys_em is each curve
    flattened to an em-space polyline for the contact sheet.
    """
    import hou
    from penstroke import curvefit
    geo = hou.Geometry()
    w_attr = geo.addAttrib(hou.attribType.Point, 'width', 0.0)
    u_attr = geo.addAttrib(hou.attribType.Point, 'u', 0.0)
    si_attr = geo.addAttrib(hou.attribType.Prim, 'stroke_index', 0)
    al_attr = geo.addAttrib(hou.attribType.Prim, 'arclength', 0.0)
    qa_polys = []
    for si, (xs, ys, ws) in enumerate(strokes):
        segments = bez[si] if (bez and si < len(bez)) else None
        fit = _fit_stroke_cvs(xs, ys, ws, segments=segments)
        if fit is None:
            continue
        cvs_px, widths_px, us = fit
        prim = geo.createBezierCurve(len(cvs_px), is_closed=False, order=4)
        pts = prim.points()
        length = 0.0
        prev = None
        for k, (cv, wpx, u) in enumerate(zip(cvs_px, widths_px, us)):
            x, y = to_em(cv[0], cv[1])
            pts[k].setPosition((x, y, 0.0))
            pts[k].setAttribValue(w_attr, wpx * w_scale)
            pts[k].setAttribValue(u_attr, u)
            if prev is not None:
                length += math.hypot(x - prev[0], y - prev[1])
            prev = (x, y)
        prim.setAttribValue(si_attr, si)
        prim.setAttribValue(al_attr, length)   # CV-polygon length (em)
        # Flatten px CVs (3 per segment + 1) back to a smooth polyline
        # for QA, in em.
        segs = [(cvs_px[i], cvs_px[i + 1], cvs_px[i + 2], cvs_px[i + 3])
                for i in range(0, len(cvs_px) - 1, 3)]
        import numpy as np
        flat = curvefit.flatten_beziers([tuple(np.asarray(p) for p in s)
                                         for s in segs])
        qa_polys.append([to_em(px, py) for px, py in flat])
    return geo, qa_polys


def build_strokes_bezier_rep(store_path, bundle_dir, size=DEFAULT_TRACE_SIZE,
                             pad=DEFAULT_TRACE_PAD, verbose=True):
    """Build the reduced bezier strokes rep into an existing bundle.

    Same stroke store as build_strokes_rep, fitted to cubic Beziers
    (penstroke.curvefit) and emitted as order-4 Houdini Bezier curves.
    Registered as a sibling rep (not the default)."""
    import hou
    from fontTools.ttLib import TTFont

    bundle_font = os.path.join(bundle_dir, hfont.FONT_NAME)
    if not os.path.exists(bundle_font):
        raise hfont.HFontError(
            f'{bundle_dir}: no {hfont.FONT_NAME} — create the bundle first')

    store = load_store(store_path)
    store_bez = load_store_bez(store_path)   # exact hand-edited cubics
    to_em, w_scale = canvas_to_em_transform(bundle_font, size, pad)
    cmap = TTFont(bundle_font).getBestCmap()

    container = hou.Geometry()
    name_attr = container.addAttrib(hou.attribType.Prim, 'name', '')

    built = []
    seen = set()
    n_cv = n_poly = n_exact = 0
    for ch, strokes in store.items():
        gname = cmap.get(ord(ch))
        if gname is None or gname in seen or not strokes:
            continue
        seen.add(gname)
        bez = store_bez.get(ch)
        if bez and any(b for b in bez):
            n_exact += 1
        glyph_geo, qa_polys = build_glyph_geometry_bezier(
            strokes, to_em, w_scale, bez=bez)
        point = container.createPoint()
        packed = container.createPackedGeometry(glyph_geo, point)
        packed.setAttribValue(name_attr, gname)
        built.append((gname, qa_polys))
        n_cv += len(glyph_geo.points())
        n_poly += sum(len(s[0]) for s in strokes)
        if verbose:
            print(f'  {gname}: {len(strokes)} strokes')

    geo_path = os.path.join(bundle_dir, BEZIER_GEO_RELPATH)
    os.makedirs(os.path.dirname(geo_path), exist_ok=True)
    container.saveToFile(geo_path)

    hfont.register_rep(
        bundle_dir, BEZIER_REP_NAME, BEZIER_REP_KIND, BEZIER_GEO_RELPATH,
        attributes={'point': ['width', 'u'],
                    'prim': ['stroke_index', 'arclength']},
        provenance={'builder': 'rep_strokes (bezier)',
                    'houdini': hou.applicationVersionString(),
                    'store': os.path.basename(store_path),
                    'fit': 'schneider cubic (curvefit) or exact hand-edited '
                           'cubics from the store',
                    'exact_glyphs': n_exact,
                    'trace_size': size, 'trace_pad': pad},
        make_default=False)

    contact_sheet(built, os.path.join(bundle_dir, 'qa', 'strokes_bezier.png'))
    if verbose and n_poly:
        print(f'  bezier rep: {n_cv} CVs vs {n_poly} polyline points '
              f'({100.0 * n_cv / n_poly:.0f}%); {n_exact} glyphs from '
              f'exact hand-edited cubics')
    return len(built), geo_path


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
    ap.add_argument('--no-bezier', action='store_true',
                    help='Skip the reduced strokes_bezier sibling rep.')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args(argv)

    n, geo_path = build_strokes_rep(args.store, args.bundle,
                                    size=args.size, pad=args.pad,
                                    verbose=not args.quiet)
    if not args.no_bezier:
        build_strokes_bezier_rep(args.store, args.bundle,
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
