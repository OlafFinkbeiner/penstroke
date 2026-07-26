"""Quality metrics for traced glyphs.

Each metric returns a score (typically 0-1, higher = better) plus an
optional human-readable issue string when the score is below threshold.
Metrics are simple, fast, and meant to surface visible problems —
they're not a perfect proxy for "looks right" but they catch the
common failure modes.

The current metrics:

  coverage:     fraction of the original glyph's ink that gets covered by
                the rendered traced strokes. Low values mean strokes missed
                large parts of the glyph (e.g., the bowl of an 'a' got
                skipped). Threshold ~0.85 catches obvious omissions.

  stroke_count: did the produced stroke count match an expected count
                (when one is supplied, e.g. from an AI-derived spec)?

  has_strokes:  did we produce ANY strokes? (catches total trace failures)
"""

import numpy as np
from penstroke.core.smoothing import taper_profile


def has_strokes(traced):
    """Score 1.0 if any strokes were produced, 0.0 if none."""
    return (1.0, None) if traced else (0.0, "no strokes produced")


def coverage(mask, traced):
    """Fraction of original glyph ink covered by traced strokes.

    Renders each traced stroke as a filled ribbon and computes the overlap
    with the original mask. Returns:
        score: ratio of covered ink pixels (0-1)
        issue: brief description if score < 0.85
    """
    if not traced:
        return 0.0, "no strokes to compute coverage from"

    H, W = mask.shape
    # Rasterize the traced strokes into a binary mask of "ink we drew"
    drew = np.zeros((H, W), dtype=bool)
    for xs, ys, widths in traced:
        n = len(xs)
        widths = widths * taper_profile(n)
        # Approximate ribbon as a sequence of filled circles along the centerline.
        # Coarse but adequate for coverage estimation.
        for x, y, w in zip(xs, ys, widths):
            r = max(1.0, w / 2.0)
            # Clamp the stamp to the canvas. A point far off-canvas
            # would otherwise yield a NEGATIVE slice end, which wraps
            # around in numpy and breaks the broadcast.
            y0 = min(max(0, int(y - r)), H)
            y1 = max(y0, min(H, int(y + r) + 1))
            x0 = min(max(0, int(x - r)), W)
            x1 = max(x0, min(W, int(x + r) + 1))
            if y1 <= y0 or x1 <= x0:
                continue   # stamp entirely outside the canvas
            yy, xx = np.ogrid[y0:y1, x0:x1]
            disk = (yy - y) ** 2 + (xx - x) ** 2 <= r * r
            drew[y0:y1, x0:x1] |= disk

    original_pixels = int(mask.sum())
    if original_pixels == 0:
        return 1.0, None
    covered = int((mask & drew).sum())
    score = covered / original_pixels

    if score < 0.70:
        return score, f"low coverage ({score:.0%}): strokes missed large parts of glyph"
    if score < 0.85:
        return score, f"moderate coverage ({score:.0%}): some glyph features not traced"
    return score, None


def stroke_count_matches_template(traced, expected_count):
    """1.0 if the actual stroke count matches what the template specified,
    0.7 if off by one, 0.3 if more divergent."""
    if expected_count is None:
        return 1.0, None  # no template = no expectation
    actual = len(traced)
    diff = abs(actual - expected_count)
    if diff == 0:
        return 1.0, None
    if diff == 1:
        return 0.7, f"stroke count {actual} differs from template ({expected_count})"
    return 0.3, f"stroke count {actual} far from template ({expected_count})"
