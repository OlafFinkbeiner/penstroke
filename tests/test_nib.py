"""Nib recovery tests — synthesize strokes from a KNOWN ellipse and check
that fit_nib inverts back to it."""

import math

import numpy as np

from penstroke.core.nib import fit_nib


def _synth_strokes(a, b, alpha_deg, n_dirs=24, n_pts=200, noise=0.0, seed=0):
    """Straight strokes in many directions, widths set by the true nib.

    For travel direction theta the elliptical nib of semi-axes a >= b at
    angle alpha draws width 2*sqrt(a^2 sin^2(t) + b^2 cos^2(t)),
    t = theta - alpha.
    """
    rng = np.random.default_rng(seed)
    alpha = math.radians(alpha_deg)
    out = []
    for i in range(n_dirs):
        theta = math.pi * i / n_dirs
        t = theta - alpha
        w = 2.0 * math.sqrt((a * math.sin(t)) ** 2 + (b * math.cos(t)) ** 2)
        s = np.linspace(0, 300, n_pts)
        xs = s * math.cos(theta)
        ys = s * math.sin(theta)
        ws = np.full(n_pts, w)
        if noise:
            ws = ws + rng.normal(0, noise, n_pts)
        out.append((xs, ys, ws))
    return out


def test_recovers_known_broad_nib():
    a, b, alpha = 10.0, 4.0, 30.0
    got = fit_nib(_synth_strokes(a, b, alpha))
    assert got is not None
    assert abs(got['width_thick'] - 2 * a) < 0.05, got
    assert abs(got['width_thin'] - 2 * b) < 0.05, got
    assert abs(got['contrast'] - a / b) < 0.02, got
    # Angle is mod 180 and is the THIN travel direction.
    d = abs(got['nib_angle_deg'] - alpha) % 180.0
    assert min(d, 180.0 - d) < 1.0, got
    assert got['r2'] > 0.99, got
    print(f"✓ nib: recovered a={got['width_thick']/2:.2f} b={got['width_thin']/2:.2f} "
          f"angle={got['nib_angle_deg']:.1f} R2={got['r2']:.3f}")


def test_monoline_reports_low_confidence():
    """A round nib has no direction to find; contrast ~1 and R^2 ~0."""
    got = fit_nib(_synth_strokes(6.0, 6.0, 0.0, noise=0.4))
    assert got is not None
    assert abs(got['contrast'] - 1.0) < 0.15, got
    assert got['r2'] < 0.2, got
    print(f"✓ nib: monoline -> contrast {got['contrast']:.3f}, "
          f"R2 {got['r2']:.3f} (correctly says 'no nib effect')")


def test_survives_noise():
    a, b, alpha = 9.0, 3.0, 115.0
    got = fit_nib(_synth_strokes(a, b, alpha, noise=0.8, seed=3))
    assert got is not None
    assert abs(got['contrast'] - a / b) < 0.35, got
    d = abs(got['nib_angle_deg'] - alpha) % 180.0
    assert min(d, 180.0 - d) < 5.0, got
    print(f"✓ nib: with noise -> contrast {got['contrast']:.2f} "
          f"(true {a/b:.2f}), angle {got['nib_angle_deg']:.1f} (true {alpha})")


def test_too_little_data_returns_none():
    assert fit_nib([]) is None
    assert fit_nib([(np.zeros(3), np.zeros(3), np.ones(3))]) is None
