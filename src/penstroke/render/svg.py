"""SVG path-building primitives used by all render modules.

SVG `<path>` stroke-width is constant per path, but we want variable
thickness along each stroke (calligraphic taper, font-weight-derived
widths). So we render each stroke as a FILLED ribbon polygon: walk down
the centerline on the left side, walk back on the right side, fill.

`stroke_to_ribbon_path` builds that polygon as an SVG path-data string.
`stroke_to_centerline_path` builds just the centerline as a polyline —
this is what we animate with stroke-dasharray to get the "pen drawing"
effect.
"""

import numpy as np
from penstroke.core.smoothing import taper_profile


def stroke_to_ribbon_path(xs, ys, widths, taper=True):
    """Build SVG path data for a variable-width ribbon.

    Renders the stroke as a closed polygon: left side forward + right side
    backward. The result fills as a smooth variable-width ribbon when used
    as `<path fill="..." d="...">`.

    Args:
        xs, ys: centerline coordinates (numpy arrays of equal length).
        widths: full stroke width at each centerline point (= 2 × distance
            transform sample).
        taper: if True, multiply widths by the taper_profile so stroke
            ends pinch inward like a pen lifting off paper.

    Returns:
        SVG path-data string ready to drop into `<path d="...">`.
    """
    n = len(xs)
    if taper:
        widths = widths * taper_profile(n)

    # Unit normal at each point (perpendicular to local tangent)
    txs = np.gradient(xs)
    tys = np.gradient(ys)
    tlen = np.hypot(txs, tys) + 1e-9
    nxv, nyv = -tys / tlen, txs / tlen

    half = widths / 2
    left_x = xs + nxv * half
    left_y = ys + nyv * half
    right_x = xs - nxv * half
    right_y = ys - nyv * half

    # Polygon: walk left side forward, right side backward, close.
    pts_x = np.concatenate([left_x, right_x[::-1]])
    pts_y = np.concatenate([left_y, right_y[::-1]])
    parts = [f"M {pts_x[0]:.2f} {pts_y[0]:.2f}"]
    for x, y in zip(pts_x[1:], pts_y[1:]):
        parts.append(f"L {x:.2f} {y:.2f}")
    parts.append("Z")
    return " ".join(parts)


def stroke_to_centerline_path(xs, ys):
    """Build SVG path data for the bare centerline (no width).

    Used as the "guide path" in animated SVGs: drawn first with stroke-
    dasharray animation to look like a pen tip moving, then the filled
    ribbon fades in behind it.
    """
    parts = [f"M {xs[0]:.2f} {ys[0]:.2f}"]
    for x, y in zip(xs[1:], ys[1:]):
        parts.append(f"L {x:.2f} {y:.2f}")
    return " ".join(parts)


def path_length(xs, ys):
    """Total arc length of a polyline. Used to time animations so longer
    strokes take proportionally more time to draw."""
    return float(np.sum(np.hypot(np.diff(xs), np.diff(ys))))
