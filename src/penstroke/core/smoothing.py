"""Smooth a stroke into renderable form: spline, wobble, taper.

`smooth_and_wobble` turns a list of pixel-precise centerline points into a
densely-resampled smooth curve with:
  - cubic-spline fit to remove pixel quantization
  - per-point stroke width sampled from the distance transform
  - smoothed widths (so the rendered ribbon doesn't have jagged edges from
    integer distance-transform values)
  - subtle pen wobble via Ornstein-Uhlenbeck noise applied perpendicular
    to the local tangent

`taper_profile` is the width multiplier curve applied at render time so
each stroke starts thin (pen landing), thickens through the middle, and
ends thin (pen lifting). The wider the entry/exit fraction, the more
pronounced the calligraphic feel.
"""

import numpy as np
from scipy.interpolate import splprep, splev
from scipy.ndimage import uniform_filter1d


def smooth_and_wobble(stroke_pts, dist_map, wobble_amp=0.18, wobble_scale=25,
                      n_samples=240, seed=0):
    """Resample a stroke as a smooth spline + per-point stroke widths.

    Args:
        stroke_pts: list of (y, x) pixel coordinates along the stroke
            centerline (skeleton walk output).
        dist_map: full-image distance transform (pixels to boundary).
        wobble_amp: standard deviation of OU-process noise applied
            perpendicular to the path. 0 disables wobble entirely.
        wobble_scale: correlation length of the wobble (larger = smoother).
        n_samples: how many points to resample the smoothed curve into.
        seed: per-stroke seed so each stroke wobbles differently.

    Returns:
        (xs, ys, widths) arrays of length n_samples, or None if the input
        was too short to fit a spline.

        widths is the FULL stroke width (= 2 × distance to boundary), so
        ribbon rendering should use width/2 on each side of the centerline.
    """
    pts = np.array(stroke_pts, dtype=float)
    if len(pts) < 4:
        return None

    # splprep doesn't like duplicate consecutive points
    keep = np.concatenate([[True], np.any(np.diff(pts, axis=0) != 0, axis=1)])
    pts = pts[keep]
    if len(pts) < 4:
        return None

    # Fit smoothing cubic spline. s ≈ len/2 gives a tight fit that still
    # removes pixel-stair-step noise from the discrete skeleton walk.
    try:
        tck, _u = splprep([pts[:, 1], pts[:, 0]], s=len(pts) * 0.5, k=3)
    except Exception:
        return None
    uu = np.linspace(0, 1, n_samples)
    xs, ys = splev(uu, tck)

    # Sample stroke width along the path. dist_map at a centerline pixel
    # equals half the local stroke width, so 2x gives the full width.
    yi = np.clip(np.round(ys).astype(int), 0, dist_map.shape[0] - 1)
    xi = np.clip(np.round(xs).astype(int), 0, dist_map.shape[1] - 1)
    widths = dist_map[yi, xi] * 2.0
    # Smooth widths so the rendered ribbon edges aren't jagged from the
    # integer-pixel distance transform.
    widths = uniform_filter1d(widths, size=9, mode="nearest")

    if wobble_amp > 0:
        # OU process: each step is a damped random walk. theta is the pull-
        # back-to-zero strength (1/scale), sigma is the per-step kick size.
        rng = np.random.default_rng(seed)
        theta = 1.0 / wobble_scale
        sigma = wobble_amp
        nx_, ny_ = np.zeros(n_samples), np.zeros(n_samples)
        for i in range(1, n_samples):
            nx_[i] = nx_[i - 1] * (1 - theta) + rng.normal(0, sigma)
            ny_[i] = ny_[i - 1] * (1 - theta) + rng.normal(0, sigma)

        # Apply wobble perpendicular to the local tangent (with a small
        # along-tangent component too — pure perpendicular looks too uniform).
        txs = np.gradient(xs)
        tys = np.gradient(ys)
        tlen = np.hypot(txs, tys) + 1e-9
        nxv, nyv = -tys / tlen, txs / tlen
        xs = xs + nx_ * nxv + 0.3 * nx_ * (txs / tlen)
        ys = ys + nx_ * nyv + 0.3 * ny_ * (tys / tlen)

    return xs, ys, widths


def taper_profile(n, entry_frac=0.12, exit_frac=0.14, min_scale=0.05):
    """Width multiplier curve: thin at ends, full in the middle.

    Returns an array of length n where the first `entry_frac` fraction
    ramps from min_scale up to 1.0 via a quarter-sine ease, the last
    `exit_frac` ramps back down, and the middle is 1.0.

    Applied multiplicatively to per-point widths at render time. The default
    parameters give a noticeable pen-landing/lifting effect without making
    the strokes look pointy.
    """
    t = np.linspace(0, 1, n)
    profile = np.ones(n)
    in_mask = t < entry_frac
    profile[in_mask] = (min_scale +
                       (1 - min_scale) * np.sin(t[in_mask] / entry_frac * np.pi / 2))
    out_mask = t > (1 - exit_frac)
    tt = (t[out_mask] - (1 - exit_frac)) / exit_frac
    profile[out_mask] = min_scale + (1 - min_scale) * np.cos(tt * np.pi / 2)
    return profile
