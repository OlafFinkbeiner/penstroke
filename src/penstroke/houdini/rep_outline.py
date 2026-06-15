"""Build the `outline` rep of an hfont: TTF beziers -> em-space curves.

Run under hython (needs `hou`):

    hython src/penstroke/houdini/rep_outline.py MyFont.ttf out/myfont.hfont

The outline rep is the "normal 2D font": the font's own bezier
outlines as Houdini curves, one packed prim per glyph, keyed by post
glyph name on the `name` attribute, baked to em space (origin at the
glyph origin, baseline on y=0, 1 unit = 1 em, XY plane). It needs only
the TTF — no tracing — so it works on any font instantly and is the
honest replacement for the Font SOP.

Geometry per glyph: one closed order-4 Bezier prim per contour, prim
attributes `contour_index` and `is_hole` (containment parity, so
counters can render as holes downstream).
"""

import os
import sys

# Bootstrap: allow running as a plain file under hython without
# penstroke installed in Houdini's python.
_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import argparse

from penstroke import hfont
from penstroke.charset import font_charset

REP_NAME = 'outline'
REP_KIND = 'curves2d'
GEO_RELPATH = os.path.join('reps', REP_NAME, 'glyphs.bgeo.sc')

# Flattening density for the is_hole containment test only (the saved
# geometry keeps true beziers).
HOLE_TEST_SAMPLES_PER_SEG = 8


# ---------------------------------------------------------------------------
# Outline extraction (pure fontTools — no hou)
# ---------------------------------------------------------------------------

def _quad_as_cubic(p0, c, p1):
    """Exact cubic equivalent of a quadratic Bezier (degree elevation)."""
    return (p0,
            (p0[0] + 2.0 / 3.0 * (c[0] - p0[0]),
             p0[1] + 2.0 / 3.0 * (c[1] - p0[1])),
            (p1[0] + 2.0 / 3.0 * (c[0] - p1[0]),
             p1[1] + 2.0 / 3.0 * (c[1] - p1[1])),
            p1)


def _mid(a, b):
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def _expand_qcurve(last, args):
    """TrueType qCurveTo -> exact cubic segments.

    `args` is off-curve points followed by the final on-curve point —
    or None as the final element for an on-curve-less contour (a legal
    TrueType compaction: the whole ring is off-curve points with the
    on-curves implied at every midpoint; fontTools emits it as a
    single qCurveTo ending in None with no moveTo). Implied on-curve
    points sit between consecutive off-curves. The conversion to
    cubic is exact (degree elevation), no approximation.

    Returns (segments, start_override): start_override is the contour
    start for the on-curve-less case, else None.
    """
    segs = []
    if args[-1] is None:
        offs = [tuple(p) for p in args[:-1]]
        start = _mid(offs[-1], offs[0])
        cur = start
        for i, c in enumerate(offs):
            end = _mid(c, offs[(i + 1) % len(offs)])
            segs.append(_quad_as_cubic(cur, c, end))
            cur = end
        return segs, start
    *offs, final = [tuple(p) for p in args]
    cur = last
    for i, c in enumerate(offs):
        end = _mid(c, offs[i + 1]) if i < len(offs) - 1 else final
        segs.append(_quad_as_cubic(cur, c, end))
        cur = end
    if not offs:                      # qCurveTo with no off-curves = line
        segs.append(_line_as_cubic(last, final))
    return segs, None


def extract_glyph_contours(glyph_set, glyph_name, scale):
    """Glyph outlines as closed cubic contours in em space.

    Returns [contour, ...] where contour = [(p0, c1, c2, p3), ...] of
    (x, y) tuples, each contour a closed chain (last p3 == first p0).
    Quadratic (glyf) outlines are lifted to cubics exactly; CFF cubics
    pass through (super-beziers decomposed). Components are decomposed
    by the glyph set's draw().
    """
    from fontTools.pens.recordingPen import RecordingPen
    from fontTools.pens.basePen import decomposeSuperBezierSegment

    rec = RecordingPen()
    glyph_set[glyph_name].draw(rec)

    contours = []
    current = None
    start = None
    last = None
    for op, args in rec.value:
        if op == 'moveTo':
            current = []
            contours.append(current)
            start = last = tuple(args[0])
        elif op == 'lineTo':
            p = tuple(args[0])
            current.append(_line_as_cubic(last, p))
            last = p
        elif op == 'curveTo':
            pts = [tuple(a) for a in args]
            if len(pts) == 3:
                current.append((last, pts[0], pts[1], pts[2]))
            else:                     # super-bezier (rare, CFF)
                for c1, c2, p in decomposeSuperBezierSegment(pts):
                    current.append((last, c1, c2, p))
                    last = p
            last = pts[-1]
        elif op == 'qCurveTo':
            if current is None:       # on-curve-less contour: no moveTo
                current = []
                contours.append(current)
                last = None
            segs, start_override = _expand_qcurve(last, args)
            if start_override is not None:
                start = start_override
            current.extend(segs)
            last = segs[-1][3] if segs else last
        elif op in ('closePath', 'endPath'):
            if current and last != start:
                current.append(_line_as_cubic(last, start))
            current = None
            last = start
    # Drop degenerate contours (single point, zero segments) and scale
    # to em space.
    return [[tuple((x * scale, y * scale) for (x, y) in seg)
             for seg in c] for c in contours if c]


def _line_as_cubic(p0, p1):
    third = ((p1[0] - p0[0]) / 3.0, (p1[1] - p0[1]) / 3.0)
    return (p0,
            (p0[0] + third[0], p0[1] + third[1]),
            (p1[0] - third[0], p1[1] - third[1]),
            p1)


def _flatten_contour(contour, samples_per_seg=HOLE_TEST_SAMPLES_PER_SEG):
    """Sample a cubic contour into a polygon for containment tests."""
    poly = []
    for (p0, c1, c2, p3) in contour:
        for i in range(samples_per_seg):
            t = i / samples_per_seg
            mt = 1.0 - t
            x = (mt ** 3 * p0[0] + 3 * mt * mt * t * c1[0]
                 + 3 * mt * t * t * c2[0] + t ** 3 * p3[0])
            y = (mt ** 3 * p0[1] + 3 * mt * mt * t * c1[1]
                 + 3 * mt * t * t * c2[1] + t ** 3 * p3[1])
            poly.append((x, y))
    return poly


def _point_in_polygon(pt, poly):
    """Even-odd ray casting."""
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) / (y2 - y1) * (x2 - x1)
            if x < xi:
                inside = not inside
    return inside


def _signed_area(poly):
    s = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def hole_flags(contours):
    """is_hole per contour, matching non-zero-winding font semantics.

    Containment alone is not enough: handwriting fonts often build a
    letter from OVERLAPPING outer contours (Caveat's K = stem contour +
    diagonals contour), where one contour starts inside another yet is
    not a hole. A true hole both sits inside other contours (odd
    depth) and winds OPPOSITE to the glyph's filled direction (taken
    from the largest contour).
    """
    polys = [_flatten_contour(c) for c in contours]
    areas = [_signed_area(p) for p in polys]
    dominant = areas[max(range(len(areas)), key=lambda i: abs(areas[i]))]
    flags = []
    for i, poly in enumerate(polys):
        pt = poly[0]
        depth = sum(1 for j, other in enumerate(polys)
                    if j != i and _point_in_polygon(pt, other))
        opposite = (areas[i] > 0) != (dominant > 0)
        flags.append(depth % 2 == 1 and opposite)
    return flags


def glyph_names_for_charset(tt, charset_chars):
    """Ordered, deduped post glyph names for the charset's characters."""
    cmap = tt.getBestCmap()
    seen = set()
    names = []
    for ch in charset_chars:
        gname = cmap.get(ord(ch))
        if gname and gname not in seen:
            seen.add(gname)
            names.append(gname)
    return names


# ---------------------------------------------------------------------------
# Houdini geometry build (needs hou)
# ---------------------------------------------------------------------------

def _reverse_contour(contour):
    """Reverse a cubic contour's direction (flips winding, same shape)."""
    return [(p3, c2, c1, p0) for (p0, c1, c2, p3) in reversed(contour)]


def build_glyph_geometry(contours, flags):
    """One glyph's contours as a hou.Geometry of closed bezier prims.

    Windings are normalized to a consistent convention — outer contours
    CW (negative signed area), holes CCW (positive) — matching what
    Houdini's Font SOP and hole-aware ops (Boolean, fills) expect, so
    the curves drop into a fill/extrude without per-glyph fixing. (The
    bundle stores clean separate closed contours, no bridge/keyhole
    seam; cut a real hole downstream with a Boolean.)
    """
    import hou
    geo = hou.Geometry()
    ci_attr = geo.addAttrib(hou.attribType.Prim, 'contour_index', 0)
    hole_attr = geo.addAttrib(hou.attribType.Prim, 'is_hole', 0)
    for ci, (contour, is_hole) in enumerate(zip(contours, flags)):
        # holes want positive (CCW) area, outer negative (CW)
        if (_signed_area(_flatten_contour(contour)) > 0) != bool(is_hole):
            contour = _reverse_contour(contour)
        prim = geo.createBezierCurve(3 * len(contour), is_closed=True,
                                     order=4)
        pts = prim.points()
        k = 0
        for (p0, c1, c2, _p3) in contour:
            for (x, y) in (p0, c1, c2):
                pts[k].setPosition((x, y, 0.0))
                k += 1
        prim.setAttribValue(ci_attr, ci)
        prim.setAttribValue(hole_attr, int(is_hole))
    return geo


def build_outline_rep(ttf_path, bundle_dir, charset='latin', verbose=True):
    """Build the outline rep into a bundle. Returns (n_glyphs, geo_path)."""
    import hou
    from fontTools.ttLib import TTFont

    hfont.create_bundle(bundle_dir, ttf_path)
    bundle_font = os.path.join(bundle_dir, hfont.FONT_NAME)

    tt = TTFont(bundle_font)
    upm = tt['head'].unitsPerEm
    scale = 1.0 / upm
    glyph_set = tt.getGlyphSet()
    names = glyph_names_for_charset(tt, font_charset(bundle_font, charset))

    container = hou.Geometry()
    name_attr = container.addAttrib(hou.attribType.Prim, 'name', '')

    built = []   # (glyph_name, contours, flags) kept for the QA sheet
    for gname in names:
        contours = extract_glyph_contours(glyph_set, gname, scale)
        if not contours:
            continue
        flags = hole_flags(contours)
        glyph_geo = build_glyph_geometry(contours, flags)
        point = container.createPoint()   # P = (0,0,0) = glyph origin
        packed = container.createPackedGeometry(glyph_geo, point)
        packed.setAttribValue(name_attr, gname)
        built.append((gname, contours, flags))
        if verbose:
            print(f'  {gname}: {len(contours)} contours, '
                  f'{sum(len(c) for c in contours)} segments')

    geo_path = os.path.join(bundle_dir, GEO_RELPATH)
    os.makedirs(os.path.dirname(geo_path), exist_ok=True)
    container.saveToFile(geo_path)

    hfont.register_rep(
        bundle_dir, REP_NAME, REP_KIND, GEO_RELPATH,
        attributes={'prim': ['contour_index', 'is_hole']},
        provenance={'builder': 'rep_outline',
                    'houdini': hou.applicationVersionString(),
                    'charset': charset,
                    'quad_to_cubic': 'exact degree elevation'})

    contact_sheet(built, os.path.join(bundle_dir, 'qa', 'outline.png'))
    return len(built), geo_path


# ---------------------------------------------------------------------------
# QA contact sheet (PIL — non-normative, lives under qa/)
# ---------------------------------------------------------------------------

def contact_sheet(built, png_path, cols=13, cell=96):
    """Grid of glyph outlines: black = outer contours, red = holes."""
    from PIL import Image, ImageDraw
    if not built:
        return
    os.makedirs(os.path.dirname(png_path), exist_ok=True)
    rows = (len(built) + cols - 1) // cols
    img = Image.new('RGB', (cols * cell, rows * cell), 'white')
    draw = ImageDraw.Draw(img)
    # Em box mapped into the cell: x 0..1em across ~70% of the cell,
    # baseline at 75% cell height (room for descenders below).
    s = cell * 0.62
    for i, (gname, contours, flags) in enumerate(built):
        ox = (i % cols) * cell + cell * 0.15
        oy = (i // cols) * cell + cell * 0.75
        for contour, is_hole in zip(contours, flags):
            poly = _flatten_contour(contour)
            pix = [(ox + x * s, oy - y * s) for (x, y) in poly]
            draw.polygon(pix, outline='red' if is_hole else 'black')
        draw.text((ox, oy + cell * 0.08), gname[:10], fill='gray')
    img.save(png_path)


# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Build the outline rep of an hfont bundle (hython).')
    ap.add_argument('ttf', help='Path to the source TTF font.')
    ap.add_argument('bundle', help='Bundle directory (e.g. out/foo.hfont).')
    ap.add_argument('--charset', default='latin',
                    choices=['ascii', 'latin', 'all'],
                    help='Charset preset, intersected with the font cmap.')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args(argv)

    n, geo_path = build_outline_rep(args.ttf, args.bundle,
                                    charset=args.charset,
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
