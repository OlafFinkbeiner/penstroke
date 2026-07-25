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

import math

import numpy as np
from scipy.interpolate import splprep, splev
from scipy.ndimage import uniform_filter1d


# Sampling density is expressed as a target spacing along the curve, not
# as a fixed point count: strokes in one glyph span a 119x range of arc
# length (measured: 4.2px .. 506px), and giving a 4px tittle the same 240
# samples as a 506px descender both wastes points and under-resolves the
# long stroke.
DEFAULT_PX_PER_SAMPLE = 0.85
MIN_SAMPLES = 48
MAX_SAMPLES = 2048

# Width-smoothing window, in pixels along the curve (was 9 samples against
# a fixed n_samples=240, i.e. ~7.5px for a typical stroke).
WIDTH_SMOOTH_PX = 7.5


def smooth_and_wobble(stroke_pts, dist_map, wobble_corr_px=20.0,
                      wobble_std_px=0.64, n_samples=None, seed=0,
                      px_per_sample=DEFAULT_PX_PER_SAMPLE):
    """Resample a stroke as a smooth spline + per-point stroke widths.

    Args:
        stroke_pts: list of (y, x) pixel coordinates along the stroke
            centerline (skeleton walk output).
        dist_map: full-image distance transform (pixels to boundary).
        wobble_corr_px: correlation length of the pen wobble, IN PIXELS
            (larger = smoother, longer-wavelength wander).
        wobble_std_px: stationary standard deviation of the wobble, IN
            PIXELS. 0 disables wobble entirely.
        n_samples: explicit sample count; None (the default) derives it
            from arc length via `px_per_sample`.
        seed: per-stroke seed so each stroke wobbles differently.
        px_per_sample: target spacing between output samples, in pixels.

    Returns:
        (xs, ys, widths) arrays, or None if the input was too short to fit
        a spline.

        widths is the FULL stroke width (= 2 × distance to boundary), so
        ribbon rendering should use width/2 on each side of the centerline.

    The wobble is specified in PIXELS rather than in samples so that it is
    invariant to sampling density. Previously `wobble_scale=25` and
    `wobble_amp=0.18` were both per-sample quantities against a fixed
    n_samples=240, which silently tied the wobble's physical wavelength to
    each stroke's own length. The defaults here reproduce the old
    behaviour for a ~200px stroke (theta 0.042 vs 0.040, sigma 0.183 vs
    0.180) and generalise correctly for every other length.
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

    # Sample count from arc length, so density is uniform along the curve
    # rather than uniform per stroke.
    arclen = float(np.hypot(np.diff(pts[:, 1]), np.diff(pts[:, 0])).sum())
    if n_samples is None:
        n_samples = int(np.clip(round(arclen / max(px_per_sample, 1e-6)) + 1,
                                MIN_SAMPLES, MAX_SAMPLES))
    uu = np.linspace(0, 1, n_samples)
    xs, ys = splev(uu, tck)

    # Sample stroke width along the path. dist_map at a centerline pixel
    # equals half the local stroke width, so 2x gives the full width.
    yi = np.clip(np.round(ys).astype(int), 0, dist_map.shape[0] - 1)
    xi = np.clip(np.round(xs).astype(int), 0, dist_map.shape[1] - 1)
    widths = dist_map[yi, xi] * 2.0
    # Smooth widths so the rendered ribbon edges aren't jagged from the
    # integer-pixel distance transform. The window is specified in PIXELS
    # and converted to samples, so it stays the same physical length
    # whatever the sampling density (it used to be a bare 9 samples).
    step_px_w = arclen / max(n_samples - 1, 1)
    win = int(round(WIDTH_SMOOTH_PX / max(step_px_w, 1e-6)))
    widths = uniform_filter1d(widths, size=max(3, win), mode="nearest")

    if wobble_std_px > 0:
        # OU process: each step is a damped random walk. theta is the pull-
        # back-to-zero strength, sigma the per-step kick size — both derived
        # from the PHYSICAL correlation length and amplitude so they do not
        # depend on how densely we happened to sample.
        rng = np.random.default_rng(seed)
        step_px = arclen / max(n_samples - 1, 1)
        theta = float(np.clip(step_px / max(wobble_corr_px, 1e-6), 1e-6, 1.0))
        # Stationary std of this OU discretisation is sigma/sqrt(2θ−θ²);
        # invert it so the amplitude is what the caller asked for.
        sigma = wobble_std_px * math.sqrt(max(2.0 * theta - theta * theta,
                                              1e-12))
        nx_, ny_ = np.zeros(n_samples), np.zeros(n_samples)
        for i in range(1, n_samples):
            nx_[i] = nx_[i - 1] * (1 - theta) + rng.normal(0, sigma)
            ny_[i] = ny_[i - 1] * (1 - theta) + rng.normal(0, sigma)

        # Apply wobble perpendicular to the local tangent (with a small
        # along-tangent component too — pure perpendicular looks too uniform).
        # Each displacement is ONE scalar times ONE unit vector: nx_ drives
        # the perpendicular offset, ny_ the along-tangent offset. (Previously
        # the tangential term mixed nx_ into x and ny_ into y, which is not a
        # displacement along the tangent at all — it skewed the wobble
        # direction and inflated the perpendicular amplitude.)
        txs = np.gradient(xs)
        tys = np.gradient(ys)
        tlen = np.hypot(txs, tys) + 1e-9
        nxv, nyv = -tys / tlen, txs / tlen
        xs = xs + nx_ * nxv + 0.3 * ny_ * (txs / tlen)
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
