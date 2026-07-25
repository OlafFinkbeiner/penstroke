"""Medial axis computed directly from the TTF Bezier outline — no raster.

This is step B0 of design/tracer_math_plan.md, graduated from
scripts/proto_vector_medial_axis.py into real, tested code. NOT wired into
`skeletonize()` / `trace_glyph_eulerian` yet — the graph this returns is
vertex-indexed in float em-relative space (Voronoi vertices), not a raster
pixel array, so downstream (`skeleton_to_graph`, the hygiene passes,
`analyze_junctions`) would need to consume it directly rather than through
the raster interface. That integration is future work.

Method: sample the Bezier contours at em-relative spacing, take the Voronoi
diagram of the samples, keep interior vertices (nonzero winding — see
`resolve_overlaps` for why not even-odd), and prune spurious vertices by
separation angle (theta-medial axis, a scale-free robustness criterion, not
a feature-significance one — see the module docstring in the prototype
script for the theta-plateau evidence).

Verified so far (design/tracer_math_plan.md B0 section has the full
measurements):
    - Resolution-invariant by construction: 40/40 on the 6-font/8-char
      probe set, vs. 33/56 for the raster pipeline.
    - Nonzero winding (not even-odd): even-odd silently mis-fills counters
      on 10.4% of multi-contour glyphs sampled across ~200 real fonts,
      including mainstream ones (Roboto, Sora, CascadiaCode).
    - Overlapping-contour seams (a standard variable-font authoring
      shortcut, also seen on Roboto): resolved via `resolve_overlaps`
      before sampling, or they read as spurious real edges.

KNOWN UNRESOLVED RISK: near-degenerate contours produce noise. At blunt
stroke tips and pinch points (confirmed on an intentionally hairline-weight
font, where stroke width approaches the sample step), Qhull produces
clusters of near-coincident vertices — sometimes small spurious loops —
instead of one clean point. Two merge-based fixes were tried and both had
worse side effects than the bug (see design/tracer_math_plan.md); nothing
is applied here. This means results on very thin strokes should not be
trusted without visual verification.
"""

import numpy as np
import networkx as nx
from scipy.spatial import Voronoi, cKDTree
from shapely.geometry import Polygon
from shapely.ops import unary_union

from penstroke.core.outline import extract_outlines

DEFAULT_THETA_DEG = 70.0    # mid-plateau; [50, 90] all give the same topology
SAMPLE_STEP_DIV = 128.0     # outline sample spacing = size / this
REF_SIZE = 384.0            # reference size the em-relative tol_px is pinned to


def _signed_area(poly):
    p = np.asarray(poly, float)
    x, y = p[:, 0], p[:, 1]
    return 0.5 * np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)


def resolve_overlaps(polys):
    """Dissolve overlapping-contour seams into a clean simple-polygon
    boundary (exterior + holes), via nonzero-winding-correct polygon union.

    Some real fonts (Roboto included) author glyphs as two or more
    OVERLAPPING solid contours by design — a standard variable-font
    shortcut that relies on the renderer's winding rule to composite them,
    rather than one contour with clean holes. Nonzero winding classifies
    fill correctly for that, but a naive Voronoi step over the raw contours
    samples the overlap SEAM (where two contours cross) as if it were a
    real outline edge, producing a degenerate medial axis (measured on
    Roboto 'B': 151 spurious junctions / 72 loops for a plain two-counter
    letterform).

    Fix: union the positively-wound (CCW) contours, union the
    negatively-wound (CW, i.e. holes) contours, then subtract hole-union
    from solid-union — exact nonzero-winding fill for the common case (each
    contour simple; only inter-contour overlap matters), and it dissolves
    the seam because overlap between same-signed contours is just still
    filled area. `.buffer(0)` on each input polygon first: GEOS otherwise
    raises (`TopologyException: side location conflict`) on the
    near-duplicate/near-zero-length segments Bezier flattening produces.
    Confirmed fix: Roboto 'B' 151j/72loop -> 4j/2loop.
    """
    pos = [Polygon(p).buffer(0) for p in polys if _signed_area(p) > 0]
    neg = [Polygon(p).buffer(0) for p in polys if _signed_area(p) < 0]
    if not pos:
        return []
    pos_u = unary_union(pos)
    result = pos_u.difference(unary_union(neg)) if neg else pos_u
    geoms = list(result.geoms) if hasattr(result, 'geoms') else [result]
    out = []
    for g in geoms:
        if g.is_empty:
            continue
        out.append(np.asarray(g.exterior.coords))
        out.extend(np.asarray(ring.coords) for ring in g.interiors)
    return out


def _winding(poly, pts):
    """Signed crossing count of `pts` against one closed polygon (the
    contribution to the nonzero-winding-number test)."""
    p = np.asarray(poly, float)
    if np.hypot(*(p[0] - p[-1])) > 1e-9:
        p = np.vstack([p, p[0]])
    x, y = pts[:, 0][:, None], pts[:, 1][:, None]
    x0, y0 = p[:-1, 0][None, :], p[:-1, 1][None, :]
    x1, y1 = p[1:, 0][None, :], p[1:, 1][None, :]
    upward = (y0 <= y) & (y1 > y)
    downward = (y1 <= y) & (y0 > y)
    with np.errstate(divide='ignore', invalid='ignore'):
        xint = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
    right = x < xint
    return (upward & right).sum(axis=1) - (downward & right).sum(axis=1)


def inside_mask(polys, pts):
    """Nonzero-winding point-in-shape over all contours (TrueType-spec
    correct — even-odd silently mis-fills counters on ~10% of real
    multi-contour glyphs, see the module docstring)."""
    winding = np.zeros(len(pts), int)
    for poly in polys:
        winding += _winding(poly, pts)
    return winding != 0


def resample_closed(poly, step):
    """Uniform arc-length resample of a closed polygon."""
    p = np.asarray(poly, float)
    if np.hypot(*(p[0] - p[-1])) > 1e-9:
        p = np.vstack([p, p[0]])
    d = np.r_[0, np.cumsum(np.hypot(np.diff(p[:, 0]), np.diff(p[:, 1])))]
    if d[-1] < step:
        return p[:-1]
    n = max(int(np.ceil(d[-1] / step)), 8)
    t = np.linspace(0, d[-1], n, endpoint=False)
    return np.column_stack([np.interp(t, d, p[:, 0]), np.interp(t, d, p[:, 1])])


def vector_medial_axis(polys, step, theta_deg=DEFAULT_THETA_DEG):
    """Theta-pruned medial axis of `polys` (already overlap-resolved).

    Returns (graph, vertices, radius): `graph` is a networkx Graph whose
    node ids are indices into `vertices` (Voronoi vertex positions, float
    em-relative coordinates); `radius[i]` is the local stroke half-width at
    vertices[i] (nearest-sample distance).
    """
    samples = np.vstack([resample_closed(p, step) for p in polys])
    vor = Voronoi(samples)
    V = vor.vertices
    keep = inside_mask(polys, V)

    tree = cKDTree(samples)
    r, _ = tree.query(V, k=1)
    # Governors: the sample points defining each Voronoi vertex, from the
    # ridge structure (every ridge_point pair contributes to its vertices).
    gov = {}
    for (p, q), verts in zip(vor.ridge_points, vor.ridge_vertices):
        for v in verts:
            if v >= 0:
                gov.setdefault(v, set()).update((p, q))

    sep = np.zeros(len(V))
    for v, ps in gov.items():
        if not keep[v]:
            continue
        d = samples[sorted(ps)] - V[v]
        d /= (np.linalg.norm(d, axis=1, keepdims=True) + 1e-12)
        c = np.clip(d @ d.T, -1, 1)
        sep[v] = np.degrees(np.arccos(c.min()))  # widest angle between governors

    good = keep & (sep >= theta_deg)

    G = nx.Graph()
    for (a, b) in vor.ridge_vertices:
        if a >= 0 and b >= 0 and good[a] and good[b]:
            G.add_edge(a, b, weight=float(np.hypot(*(V[a] - V[b]))))
    G.remove_nodes_from([n for n in list(G.nodes) if G.degree(n) == 0])
    return G, V, r


def topology(G):
    """(endpoints, junctions, cycles) -- the scale-invariant signature
    used to check resolution invariance."""
    if G.number_of_nodes() == 0:
        return (0, 0, 0)
    ends = sum(1 for n in G if G.degree(n) == 1)
    jcts = sum(1 for n in G if G.degree(n) >= 3)
    cyc = G.number_of_edges() - G.number_of_nodes() + nx.number_connected_components(G)
    return (ends, jcts, cyc)


def glyph_vector_skeleton(font_path, char, size=384, pad=40,
                          theta_deg=DEFAULT_THETA_DEG):
    """End to end: TTF glyph -> overlap-resolved outline -> theta-pruned
    vector medial axis. Returns (graph, vertices, radius) as in
    `vector_medial_axis`, or (None, None, None) if the glyph has no ink.

    `tol_px` (the Bezier flattening tolerance) and the sample `step` are
    both scaled by `size` — this is what keeps the result resolution-
    invariant; do not pass an absolute pixel constant here.
    """
    polys = extract_outlines(font_path, char, size=size, pad=pad,
                             tol_px=0.5 * size / REF_SIZE)
    if not polys:
        return None, None, None
    polys = resolve_overlaps(polys)
    if not polys:
        return None, None, None
    return vector_medial_axis(polys, step=size / SAMPLE_STEP_DIV,
                              theta_deg=theta_deg)
