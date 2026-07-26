"""Recover the pen nib that drew a font — in closed form, no tuning.

Model the nib as an ellipse with semi-axes a >= b at angle alpha. The
support width of that ellipse measured perpendicular to a travel
direction theta is

    w(theta)/2 = sqrt( a^2 sin^2(theta-alpha) + b^2 cos^2(theta-alpha) )

Squaring and applying the double-angle identity turns it into something
LINEAR in three unknowns:

    w(theta)^2 = A + P*cos(2 theta) + Q*sin(2 theta)

    with  A = 2(a^2+b^2),  P = -2(a^2-b^2) cos 2alpha,
                           Q = -2(a^2-b^2) sin 2alpha

So one `lstsq` over every (tangent angle, width) sample in the font
recovers the whole nib. No iteration, no thresholds, no per-font tuning.
Inverting:

    B = hypot(P, Q) = 2(a^2 - b^2)
    a = sqrt(A + B) / 2        (half the THICKEST stroke)
    b = sqrt(A - B) / 2        (half the THINNEST stroke)
    alpha = atan2(-Q, -P) / 2  (travel direction giving the THINNEST stroke)
    contrast = a / b

R^2 comes free and is the honest confidence measure: a monoline font has
no nib effect to find and scores near zero, which is the correct answer
rather than a failure.

numpy-only on purpose — this must import under hython alongside hfont.py.
"""

import math

import numpy as np

# Samples near a stroke's ends are taper, and samples near junctions have
# an inflated distance transform (the inscribed disk grows into the
# crossing). Trim a fraction off each end of every stroke before fitting.
END_TRIM_FRAC = 0.12

# Below this many usable samples the fit is not meaningful.
MIN_SAMPLES = 40


def _stroke_samples(xs, ys, widths):
    """(tangent angle, width) pairs for the usable interior of one stroke."""
    n = len(xs)
    if n < 8:
        return None
    k = int(n * END_TRIM_FRAC)
    sl = slice(k, n - k) if n - 2 * k >= 4 else slice(0, n)
    tx = np.gradient(np.asarray(xs, dtype=float))[sl]
    ty = np.gradient(np.asarray(ys, dtype=float))[sl]
    w = np.asarray(widths, dtype=float)[sl]
    good = (np.hypot(tx, ty) > 1e-9) & (w > 0)
    if not np.any(good):
        return None
    return np.arctan2(ty[good], tx[good]), w[good]


def fit_nib(strokes):
    """Fit the elliptical nib over every stroke of a font.

    Args:
        strokes: iterable of (xs, ys, widths) as produced by the tracer.
            Widths are FULL stroke widths.

    Returns:
        dict with keys:
          nib_angle_deg  travel direction producing the thinnest stroke
          contrast       thick/thin ratio (1.0 = monoline)
          width_thick    widest stroke the nib can draw
          width_thin     narrowest stroke the nib can draw
          width_nominal  sqrt(A), an overall scale for the nib
          r2             goodness of fit; near 0 means "no nib effect"
          n_samples      how many samples the fit used
        or None if there was not enough usable data.

    Uniform weighting per sample is correct because the tracer samples at
    constant arc-length spacing (see core.smoothing) — every sample stands
    for the same amount of pen travel.
    """
    thetas, ws = [], []
    for stroke in strokes:
        if stroke is None or len(stroke) < 3:
            continue
        got = _stroke_samples(stroke[0], stroke[1], stroke[2])
        if got is None:
            continue
        thetas.append(got[0])
        ws.append(got[1])
    if not thetas:
        return None
    theta = np.concatenate(thetas)
    w = np.concatenate(ws)
    if len(theta) < MIN_SAMPLES:
        return None

    y = w * w
    M = np.column_stack([np.ones_like(theta),
                         np.cos(2.0 * theta),
                         np.sin(2.0 * theta)])
    (A, P, Q), *_ = np.linalg.lstsq(M, y, rcond=None)

    resid = y - M @ np.array([A, P, Q])
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float((resid ** 2).sum()) / ss_tot if ss_tot > 0 else 0.0

    B = math.hypot(P, Q)
    # The ellipse is only recoverable while A > B; clamp the degenerate
    # case (a perfectly monoline font drives B -> 0 and P, Q into noise).
    B = min(B, max(A - 1e-9, 0.0))
    if A <= 0:
        return None
    a = math.sqrt(max(A + B, 0.0)) / 2.0
    b = math.sqrt(max(A - B, 0.0)) / 2.0
    alpha = 0.5 * math.atan2(-Q, -P)

    return {
        'nib_angle_deg': round(math.degrees(alpha) % 180.0, 2),
        'contrast': round(a / b, 4) if b > 1e-9 else None,
        'width_thick': round(2.0 * a, 3),
        'width_thin': round(2.0 * b, 3),
        'width_nominal': round(math.sqrt(A), 3),
        'r2': round(r2, 4),
        'n_samples': int(len(theta)),
    }
