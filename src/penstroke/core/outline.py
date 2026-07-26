"""Extract a glyph's exact vector outlines from a TTF/OTF.

Where `rasterize_glyph` gives us a binary mask (good for skeletonization
but loses curve information), this module gives us the actual Bezier
outlines flattened to polygons in the SAME canvas coordinate frame.

Used for:
  - The yellow underlay in the alphabet preview (crisp vectors, no
    antialiasing fuzz).
  - Outline-based feature detection: corners in the polygon are
    candidate serif tips.
  - Coverage QA: does every traced stroke pass close to every outline
    contour? (a stem with no nearby outline = stroke went into white;
    an outline with no nearby stroke = feature missed.)

The output coordinate system matches `rasterize_glyph(font_path, ch, size)`
exactly, so outlines and mask overlay without further transformation.
"""

import numpy as np
from fontTools.ttLib import TTFont
from fontTools.pens.basePen import BasePen


class _PolylinePen(BasePen):
    """Record glyph outlines as a list of polygons (closed polylines).

    Each subpath (moveTo ... closePath) becomes one polygon. Beziers are
    flattened by adaptive subdivision until segment length is below a
    pixel-space tolerance.
    """

    def __init__(self, glyph_set, tol):
        super().__init__(glyph_set)
        self.tol = float(tol)
        self.polygons = []
        self._current = []

    def _moveTo(self, pt):
        if self._current:
            self.polygons.append(self._current)
        self._current = [pt]

    def _lineTo(self, pt):
        self._current.append(pt)

    def _qCurveToOne(self, pt1, pt2):
        # Quadratic Bezier from current point through control pt1 to pt2.
        p0 = self._current[-1]
        self._current.extend(_flatten_quadratic(p0, pt1, pt2, self.tol))

    def _curveToOne(self, pt1, pt2, pt3):
        # Cubic Bezier from current point through pt1, pt2 to pt3.
        p0 = self._current[-1]
        self._current.extend(_flatten_cubic(p0, pt1, pt2, pt3, self.tol))

    def _closePath(self):
        if self._current:
            # Close the polygon by returning to the start point if not already.
            if self._current[0] != self._current[-1]:
                self._current.append(self._current[0])
            self.polygons.append(self._current)
            self._current = []

    def _endPath(self):
        # Open subpath — still keep it.
        if self._current:
            self.polygons.append(self._current)
            self._current = []


def _flatten_quadratic(p0, p1, p2, tol, depth=0, max_depth=10):
    """Recursive subdivision of a quadratic Bezier until flat enough."""
    # Distance from p1 to the line p0–p2 is a flatness proxy.
    flat = _point_line_distance(p1, p0, p2)
    if flat < tol or depth >= max_depth:
        return [p2]
    m01 = _mid(p0, p1)
    m12 = _mid(p1, p2)
    m012 = _mid(m01, m12)
    left = _flatten_quadratic(p0, m01, m012, tol, depth + 1, max_depth)
    right = _flatten_quadratic(m012, m12, p2, tol, depth + 1, max_depth)
    return left + right


def _flatten_cubic(p0, p1, p2, p3, tol, depth=0, max_depth=10):
    """Recursive subdivision of a cubic Bezier until flat enough."""
    # Max distance of control points from the chord.
    flat = max(_point_line_distance(p1, p0, p3),
               _point_line_distance(p2, p0, p3))
    if flat < tol or depth >= max_depth:
        return [p3]
    m01 = _mid(p0, p1)
    m12 = _mid(p1, p2)
    m23 = _mid(p2, p3)
    m012 = _mid(m01, m12)
    m123 = _mid(m12, m23)
    m0123 = _mid(m012, m123)
    left = _flatten_cubic(p0, m01, m012, m0123, tol, depth + 1, max_depth)
    right = _flatten_cubic(m0123, m123, m23, p3, tol, depth + 1, max_depth)
    return left + right


def _mid(a, b):
    return ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)


def _point_line_distance(p, a, b):
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    denom = (dx * dx + dy * dy) ** 0.5
    if denom < 1e-9:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    return abs(dy * px - dx * py + bx * ay - by * ax) / denom


def extract_outlines(font_path, char, size=512, pad=40, tol_px=0.5):
    """Return a glyph's outlines as polygons in the rasterize_glyph canvas frame.

    Args:
        font_path: TTF/OTF path. Must be the same file used by rasterize_glyph
            for the result to overlay correctly.
        char: single character.
        size, pad: must match rasterize_glyph's parameters — these set the
            canvas coordinate system.
        tol_px: maximum allowed sagitta when flattening Beziers, in pixels.
            0.5 is sub-pixel and effectively perfect for rendering.

    Returns:
        list of polygons. Each polygon is an Nx2 numpy array of (x, y) pixel
        coordinates, with y growing DOWN (PIL convention) — same as the mask
        from rasterize_glyph.
    """
    tt = TTFont(font_path)
    upem = tt['head'].unitsPerEm
    hhea = tt['hhea']
    ascent_em = hhea.ascent
    cmap = tt.getBestCmap()
    if ord(char) not in cmap:
        raise ValueError(f"glyph {char!r} not in font {font_path}")
    glyph_name = cmap[ord(char)]

    px_per_em = size / upem
    ascent_px = int(round(ascent_em * px_per_em))

    baseline_y = pad + ascent_px

    # Em-units -> canvas pixels. Y flips (em up, canvas down).
    def to_canvas(em_x, em_y):
        cx = pad + em_x * px_per_em
        cy = baseline_y - em_y * px_per_em
        return cx, cy

    glyph_set = tt.getGlyphSet()
    # Flatten in em-units; convert tol_px back to em-units via px_per_em.
    pen = _PolylinePen(glyph_set, tol=tol_px / max(1e-9, px_per_em))
    glyph_set[glyph_name].draw(pen)

    out = []
    for poly in pen.polygons:
        arr = np.array([to_canvas(x, y) for x, y in poly], dtype=float)
        out.append(arr)
    return out
