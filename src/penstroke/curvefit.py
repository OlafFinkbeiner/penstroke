"""Cubic-Bezier curve fitting — numpy only, no scipy/skimage.

Schneider's algorithm ("An Algorithm for Automatically Fitting
Digitized Curves", Graphics Gems 1990): approximate a polyline with a
chain of cubic Bezier segments within a pixel tolerance, plus a pre-fit
moving-average smoother.

This is the ONE implementation, kept dependency-light (numpy is the
only import) so it imports under Houdini's hython — where scipy and
skimage are absent — as well as in the project venv. Both the Corel
edit-round export (penstroke.editround) and the Houdini bezier strokes
rep (penstroke.houdini.rep_strokes) fit curves through here.
"""

import numpy as np

# Error tolerances in pixels. The stroke tolerance deliberately exceeds
# the hand-wobble amplitude so fitted curves come out SMOOTH —
# auto-smoothing falls out of the fit. A straight stem fits as one
# cubic segment (2 nodes); letters typically land at 2-6 segments.
STROKE_FIT_TOL_PX = 1.8
UNDERLAY_FIT_TOL_PX = 0.6
SMOOTH_WINDOW_PX = 9.0    # pre-fit moving-average window (kills wobble)


def _chord_params(pts):
    d = np.hypot(np.diff(pts[:, 0]), np.diff(pts[:, 1]))
    u = np.concatenate([[0.0], np.cumsum(d)])
    return u / u[-1] if u[-1] > 0 else u


def _bezier_point(bez, t):
    p0, c1, c2, p3 = bez
    mt = 1.0 - t
    return (mt ** 3)[:, None] * p0 + 3 * (mt ** 2 * t)[:, None] * c1 \
        + 3 * (mt * t ** 2)[:, None] * c2 + (t ** 3)[:, None] * p3


def _generate_bezier(pts, u, t_hat1, t_hat2):
    """Least-squares cubic for given parameterization and end tangents."""
    n = len(pts)
    A = np.zeros((n, 2, 2))
    mt = 1.0 - u
    A[:, 0] = t_hat1[None, :] * (3 * mt ** 2 * u)[:, None]
    A[:, 1] = t_hat2[None, :] * (3 * mt * u ** 2)[:, None]
    p0, p3 = pts[0], pts[-1]
    base = (mt ** 3)[:, None] * p0 + (3 * mt ** 2 * u)[:, None] * p0 \
        + (3 * mt * u ** 2)[:, None] * p3 + (u ** 3)[:, None] * p3
    tmp = pts - base
    C = np.zeros((2, 2))
    X = np.zeros(2)
    C[0, 0] = np.sum(A[:, 0] * A[:, 0])
    C[0, 1] = C[1, 0] = np.sum(A[:, 0] * A[:, 1])
    C[1, 1] = np.sum(A[:, 1] * A[:, 1])
    X[0] = np.sum(A[:, 0] * tmp)
    X[1] = np.sum(A[:, 1] * tmp)
    det = C[0, 0] * C[1, 1] - C[0, 1] * C[1, 0]
    if abs(det) > 1e-12:
        alpha1 = (X[0] * C[1, 1] - X[1] * C[0, 1]) / det
        alpha2 = (C[0, 0] * X[1] - C[1, 0] * X[0]) / det
    else:
        alpha1 = alpha2 = 0.0
    seg_len = float(np.hypot(*(p3 - p0)))
    eps = 1e-6 * seg_len
    if alpha1 < eps or alpha2 < eps:
        alpha1 = alpha2 = seg_len / 3.0
    c1 = p0 + t_hat1 * alpha1
    c2 = p3 + t_hat2 * alpha2
    return (p0, c1, c2, p3)


def _max_error(pts, bez, u):
    q = _bezier_point(bez, u)
    err = np.hypot(q[:, 0] - pts[:, 0], q[:, 1] - pts[:, 1])
    i = int(np.argmax(err))
    return float(err[i]), i


def _tangent(pts, i, j):
    v = pts[j] - pts[i]
    n = np.linalg.norm(v)
    return v / n if n > 0 else np.array([1.0, 0.0])


def fit_beziers(pts, tol):
    """Fit cubic Beziers to an Nx2 polyline within `tol` px.

    Returns a list of cubic segments [(p0, c1, c2, p3), ...] (each a
    tuple of length-2 numpy arrays).
    """
    pts = np.asarray(pts, dtype=float)
    # Drop consecutive duplicates (degenerate params otherwise).
    keep = np.ones(len(pts), dtype=bool)
    keep[1:] = (np.hypot(np.diff(pts[:, 0]), np.diff(pts[:, 1])) > 1e-9)
    pts = pts[keep]
    if len(pts) < 2:
        return []
    if len(pts) == 2:
        third = (pts[1] - pts[0]) / 3.0
        return [(pts[0], pts[0] + third, pts[1] - third, pts[1])]
    t1 = _tangent(pts, 0, 1)
    t2 = _tangent(pts, -1, -2)
    return _fit_recursive(pts, t1, t2, tol)


def _fit_recursive(pts, t_hat1, t_hat2, tol, depth=0):
    if len(pts) == 2:
        third = (pts[1] - pts[0]) / 3.0
        return [(pts[0], pts[0] + third, pts[1] - third, pts[1])]
    u = _chord_params(pts)
    bez = _generate_bezier(pts, u, t_hat1, t_hat2)
    err, split = _max_error(pts, bez, u)
    if err <= tol or depth > 24:
        return [bez]
    # A couple of Newton-Raphson reparameterization passes often saves
    # a split.
    if err <= tol * 4:
        for _ in range(3):
            u = _reparameterize(pts, bez, u)
            bez = _generate_bezier(pts, u, t_hat1, t_hat2)
            err, split = _max_error(pts, bez, u)
            if err <= tol:
                return [bez]
    split = max(1, min(len(pts) - 2, split))
    center_tangent = _tangent(pts, min(split + 1, len(pts) - 1),
                              max(split - 1, 0))
    left = _fit_recursive(pts[:split + 1], t_hat1, center_tangent,
                          tol, depth + 1)
    right = _fit_recursive(pts[split:], -center_tangent, t_hat2,
                           tol, depth + 1)
    return left + right


def _reparameterize(pts, bez, u):
    p0, c1, c2, p3 = bez
    # Derivative control points
    d1 = 3 * (np.array([c1, c2, p3]) - np.array([p0, c1, c2]))
    d2 = 2 * (d1[1:] - d1[:-1])
    out = u.copy()
    for i in range(1, len(u) - 1):
        t = u[i]
        mt = 1 - t
        q = (mt ** 3) * p0 + 3 * mt ** 2 * t * c1 \
            + 3 * mt * t ** 2 * c2 + (t ** 3) * p3
        q1 = (mt ** 2) * d1[0] + 2 * mt * t * d1[1] + (t ** 2) * d1[2]
        q2 = mt * d2[0] + t * d2[1]
        num = float(np.dot(q - pts[i], q1))
        den = float(np.dot(q1, q1) + np.dot(q - pts[i], q2))
        if abs(den) > 1e-12:
            out[i] = t - num / den
    out = np.clip(out, 0.0, 1.0)
    out = np.maximum.accumulate(out)   # keep monotone
    return out


def smooth_polyline(pts, window_px):
    """Moving-average smoothing with arc-length-derived window size."""
    arr = np.asarray(pts, dtype=float)
    if len(arr) < 5:
        return arr
    seg = np.hypot(np.diff(arr[:, 0]), np.diff(arr[:, 1]))
    mean_step = float(np.mean(seg)) if len(seg) else 1.0
    w = max(3, int(round(window_px / max(mean_step, 1e-6))))
    if w % 2 == 0:
        w += 1
    if w >= len(arr):
        return arr
    kernel = np.ones(w) / w
    sx = np.convolve(arr[:, 0], kernel, mode='same')
    sy = np.convolve(arr[:, 1], kernel, mode='same')
    # Keep endpoints exact (convolve corrupts the borders).
    half = w // 2
    sx[:half], sx[-half:] = arr[:half, 0], arr[-half:, 0]
    sy[:half], sy[-half:] = arr[:half, 1], arr[-half:, 1]
    return np.column_stack([sx, sy])


def flatten_beziers(segments, step_px=2.0):
    """Sample a cubic-segment chain back into a polyline."""
    out = []
    for (p0, c1, c2, p3) in segments:
        # Rough length estimate via control polygon
        approx = (np.linalg.norm(c1 - p0) + np.linalg.norm(c2 - c1)
                  + np.linalg.norm(p3 - c2))
        n = max(4, int(approx / step_px))
        t = np.linspace(0, 1, n, endpoint=False)
        out.append(_bezier_point((p0, c1, c2, p3), t))
    out.append(segments[-1][3][None, :])
    return np.vstack(out)
