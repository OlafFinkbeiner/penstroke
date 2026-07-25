"""Score a stroke decomposition — the missing objective.

Until this existed, nothing in penstroke could say "decomposition A beats
decomposition B". Every tracer change was therefore judged by looking at
diagnostic PNGs, which is why the tuned constants could never be
calibrated: there was no number to calibrate against.

The score is a weighted sum of four terms, each in [0, 1] and each
independently meaningful, so a regression can be attributed rather than
just observed:

  reconstruction  Does the union of stroke ribbons reproduce the glyph's
                  ink? Symmetric: penalises both ink the strokes missed
                  and ribbon that spills outside the outline. This is the
                  only term that can detect "the pen drew through white
                  space".

  continuation    How smooth are the joins the decomposition actually
                  made, and how many times did the pen lift? A trace that
                  shatters a letter into fragments scores badly even if it
                  covers the ink perfectly.

  parsimony       Stroke count against the glyph's own topological lower
                  bound (an Eulerian argument: a component with 2k odd-
                  degree nodes needs at least max(1, k) open strokes).
                  Punishes over-splitting without hardcoding "an H has 3
                  strokes".

  smoothness      Total absolute curvature per unit length, normalised by
                  stroke width. A human pen does not wobble at high
                  frequency along a stem; serpentine traces do.

All four are computable from what the tracer already returns. Weights are
deliberately blunt and equal-ish: the point of the score is to RANK
decompositions, and a ranking that needs finely-tuned weights to work is
not measuring anything real.

KNOWN LIMITATION — read before calibrating anything with `total`.
`parsimony` measures stroke count against a bound derived from the
skeleton GRAPH, and pruning changes that graph. Prune harder and the
bound falls (fewer leaves -> fewer odd-degree vertices), so the ratio can
worsen even as the stroke count improves. `total` is therefore only
comparable between decompositions built on the SAME skeleton
configuration — comparing two tracer builds with different pruning is
invalid and will report a regression that is not there. This bit once
already: an A0 pruning change looked like a 0.6576 -> 0.5955 collapse in
`total` while `reconstruction` was flat to three decimals (0.9374 vs
0.9371) and missed ink actually improved.

For cross-configuration comparisons use `reconstruction` (and
`smoothness`), which depend only on the strokes and the glyph mask.
"""

import math

import numpy as np

WEIGHTS = {
    'reconstruction': 0.40,
    'continuation': 0.25,
    'parsimony': 0.20,
    'smoothness': 0.15,
}

# A pen lift costs this much of the continuation term, per lift beyond the
# topological minimum.
LIFT_PENALTY = 0.12


def _ribbon_mask(strokes, shape, width_scale=1.0):
    """Rasterise the stroke ribbons into a boolean mask.

    Each sample stamps a disk of radius width/2, which is exactly the
    medial-axis reconstruction model the tracer inverts.
    """
    H, W = shape
    out = np.zeros((H, W), dtype=bool)
    yy, xx = np.mgrid[0:H, 0:W]
    for stroke in strokes:
        if stroke is None or len(stroke) < 3:
            continue
        xs, ys, ws = stroke[0], stroke[1], stroke[2]
        for x, y, w in zip(np.asarray(xs), np.asarray(ys), np.asarray(ws)):
            r = float(w) * 0.5 * width_scale
            if r <= 0:
                continue
            y0 = max(0, int(y - r - 1)); y1 = min(H, int(y + r + 2))
            x0 = max(0, int(x - r - 1)); x1 = min(W, int(x + r + 2))
            if y1 <= y0 or x1 <= x0:
                continue
            sub_y = yy[y0:y1, x0:x1] - y
            sub_x = xx[y0:y1, x0:x1] - x
            out[y0:y1, x0:x1] |= (sub_y * sub_y + sub_x * sub_x) <= r * r
    return out


def reconstruction_score(strokes, mask):
    """Symmetric ink agreement between the ribbons and the glyph mask.

    Returns (score, detail). Score is the Jaccard index — intersection
    over union — so missing ink and spilled ink are both penalised, and
    the value is scale-free.
    """
    ink = np.asarray(mask).astype(bool)
    if not ink.any():
        return 1.0, {'missed': 0.0, 'spilled': 0.0}
    rib = _ribbon_mask(strokes, ink.shape)
    inter = float((ink & rib).sum())
    union = float((ink | rib).sum())
    score = inter / union if union > 0 else 0.0
    return score, {
        'missed': float((ink & ~rib).sum()) / float(ink.sum()),
        'spilled': float((rib & ~ink).sum()) / float(ink.sum()),
    }


def _turn_angles(xs, ys):
    """Absolute turn angle at each interior sample, in radians."""
    p = np.column_stack([np.asarray(xs, float), np.asarray(ys, float)])
    d = np.diff(p, axis=0)
    n = np.hypot(d[:, 0], d[:, 1])
    good = n > 1e-9
    d = d[good] / n[good, None]
    if len(d) < 2:
        return np.zeros(0), 0.0
    dot = np.clip((d[:-1] * d[1:]).sum(axis=1), -1.0, 1.0)
    return np.arccos(dot), float(n[good].sum())


def smoothness_score(strokes, scale):
    """1 / (1 + total absolute curvature per stroke-width of travel).

    Normalising the turning by W makes it comparable across fonts: a
    stroke that turns one radian over one stroke-width is genuinely
    sharp whatever the raster size.
    """
    total_turn = 0.0
    total_len = 0.0
    for stroke in strokes:
        if stroke is None or len(stroke) < 3:
            continue
        turns, length = _turn_angles(stroke[0], stroke[1])
        total_turn += float(turns.sum())
        total_len += length
    if total_len <= 0:
        return 1.0, {'turn_per_W': 0.0}
    turn_per_w = total_turn / (total_len / max(scale, 1e-6))
    return 1.0 / (1.0 + turn_per_w), {'turn_per_W': turn_per_w}


def continuation_score(strokes, scale, min_strokes=1):
    """Smoothness of the joins made, minus a penalty per extra pen lift.

    A decomposition that splits where the geometry actually continues
    shows up twice: the split itself costs a lift, and the strokes it
    leaves behind are shorter relative to their turning.
    """
    n = len([s for s in strokes if s is not None and len(s) >= 3])
    if n == 0:
        return 0.0, {'lifts': 0, 'excess_lifts': 0}
    excess = max(0, n - max(1, min_strokes))
    base, _ = smoothness_score(strokes, scale)
    score = max(0.0, base - LIFT_PENALTY * excess)
    return score, {'lifts': n, 'excess_lifts': excess}


def topological_min_strokes(graph):
    """Lower bound on open strokes for one skeleton graph.

    Eulerian argument: a connected component with 2k odd-degree vertices
    cannot be drawn in fewer than max(1, k) open strokes. Summed over
    components this is the fewest pen-downs any correct decomposition
    could use — parsimony is measured against this, not against a
    per-letter expectation.
    """
    import networkx as nx
    if graph is None or graph.number_of_edges() == 0:
        return 1
    total = 0
    for nodes in nx.connected_components(graph):
        sub = graph.subgraph(nodes)
        if sub.number_of_edges() == 0:
            continue
        odd = sum(1 for _n, d in sub.degree() if d % 2 == 1)
        total += max(1, odd // 2)
    return max(1, total)


def parsimony_score(strokes, min_strokes):
    """1 when the trace hits the topological minimum, decaying above it."""
    n = len([s for s in strokes if s is not None and len(s) >= 3])
    if n <= 0:
        return 0.0, {'strokes': 0, 'min': min_strokes}
    ratio = n / max(1, min_strokes)
    return 1.0 / ratio if ratio > 1 else 1.0, {'strokes': n,
                                               'min': min_strokes}


def score_glyph(strokes, mask, scale, graph=None, min_strokes=None):
    """Composite score for one glyph's decomposition.

    Args:
        strokes: list of (xs, ys, widths) from the tracer.
        mask: the glyph's binary ink mask.
        scale: W, the glyph's stroke width (tracer.glyph_scale).
        graph: the annotated skeleton graph, for the topological bound.
        min_strokes: override the bound directly if the graph is not handy.

    Returns a dict of the four terms, their details, and `total`.
    """
    if min_strokes is None:
        min_strokes = topological_min_strokes(graph) if graph is not None else 1

    rec, rec_d = reconstruction_score(strokes, mask)
    con, con_d = continuation_score(strokes, scale, min_strokes)
    par, par_d = parsimony_score(strokes, min_strokes)
    smo, smo_d = smoothness_score(strokes, scale)

    terms = {'reconstruction': rec, 'continuation': con,
             'parsimony': par, 'smoothness': smo}
    total = sum(WEIGHTS[k] * v for k, v in terms.items())
    return {
        'total': round(total, 4),
        'terms': {k: round(v, 4) for k, v in terms.items()},
        'detail': {'reconstruction': rec_d, 'continuation': con_d,
                   'parsimony': par_d, 'smoothness': smo_d},
    }


def score_font(font_path, chars, size=384):
    """Mean composite score over a charset — the number to compare traces by."""
    from penstroke.tracer import (trace_glyph_eulerian, glyph_scale,
                                  build_annotated_graph)
    rows = {}
    for ch in chars:
        try:
            mask, skel, dist, traced, _n, _m = trace_glyph_eulerian(
                font_path, ch, size=size)
        except Exception as e:
            rows[ch] = {'error': str(e)[:80]}
            continue
        if not traced:
            rows[ch] = {'error': 'no strokes'}
            continue
        W = glyph_scale(skel, dist)
        G = build_annotated_graph(skel, dist, mask=mask, scale=W)
        rows[ch] = score_glyph(traced, mask, W, graph=G)
    vals = [r['total'] for r in rows.values() if 'total' in r]
    return {
        'mean': round(float(np.mean(vals)), 4) if vals else 0.0,
        'n': len(vals),
        'glyphs': rows,
    }
