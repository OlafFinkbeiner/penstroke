"""Cascading per-letter QA: progressively more expensive checks, structured issues.

Each layer detects a different failure mode and emits an Issue object. A
letter may have zero or more issues. The fix loop (quality/fixes.py)
consumes the issue list and proposes a retrace strategy.

Layers, cheapest first:

  1. geometric   — per-stroke tortuosity, pixel revisits in skeleton walk,
                   stroke count vs the template that produced the trace.
                   These catch zigzag/backtracking artifacts.

  2. outline     — for every TTF outline point, distance to nearest stroke
                   centerline. Long stretches of un-covered outline mean a
                   feature (serif, bowl extension, descender) was missed.

  3. spec        — AI-derived expected stroke / dot count vs actual.
                   Coarsest signal but cheap once spec.json exists.

The cascade is deliberately MULTI-MODAL: geometric finds noisy strokes,
outline finds missing strokes, spec sanity-checks the overall count.
A serious bug usually fires multiple layers — single-layer fires are
often false positives worth ignoring.
"""

from dataclasses import dataclass, field, asdict
from typing import Optional
import json
import math
import os

import numpy as np


@dataclass
class Issue:
    """One detected defect in a letter trace."""
    char: str
    kind: str           # 'phantom_stroke', 'missing_feature', 'count_mismatch', 'open_bowl', ...
    severity: str       # 'low' | 'medium' | 'high'
    detail: str         # human-readable description
    data: dict = field(default_factory=dict)  # numeric specifics

    def to_dict(self):
        return asdict(self)


# ----- Layer 1: geometric -----

def _stroke_geometry(xs, ys):
    plen = float(np.sqrt(np.diff(xs)**2 + np.diff(ys)**2).sum())
    disp = float(np.hypot(xs[-1] - xs[0], ys[-1] - ys[0]))
    bbox_w = float(xs.max() - xs.min())
    bbox_h = float(ys.max() - ys.min())
    return plen, disp, max(bbox_w, bbox_h)


def geometric_issues(char, traced, tortuosity_high=1.8, max_extent_factor=1.0):
    """Detect zigzag, backtracking, and degenerate strokes.

    - tortuosity > tortuosity_high (path length / displacement) signals
      a stroke that goes back and forth (e.g. M's diagonals before the
      waypoint fix).
    - max_extent < avg_width signals a stroke coiled inside its own pen
      stamp (the historical i/j phantom).
    """
    issues = []
    for i, (xs, ys, ws) in enumerate(traced):
        plen, disp, extent = _stroke_geometry(xs, ys)
        avg_w = float(np.mean(ws))
        tort = plen / max(disp, 1e-6)
        if extent < avg_w * max_extent_factor:
            issues.append(Issue(
                char=char, kind='phantom_stroke', severity='high',
                detail=f'stroke {i} fits inside its own pen width '
                       f'(extent {extent:.1f} < width {avg_w:.1f})',
                data={'stroke': i, 'extent': extent, 'width': avg_w},
            ))
        elif tort > tortuosity_high:
            issues.append(Issue(
                char=char, kind='zigzag', severity='medium',
                detail=f'stroke {i} backtracks (tortuosity {tort:.2f})',
                data={'stroke': i, 'tortuosity': tort,
                      'path_len': plen, 'displacement': disp},
            ))
    return issues


# ----- Layer 2: outline coverage -----

def _resample_polyline(poly, step=2.0):
    """Resample a polyline to roughly uniform spacing along its arc length."""
    if len(poly) < 2:
        return poly
    diffs = np.diff(poly, axis=0)
    seg_lens = np.hypot(diffs[:, 0], diffs[:, 1])
    cum = np.concatenate([[0], np.cumsum(seg_lens)])
    total = cum[-1]
    if total < step:
        return poly
    n = max(2, int(round(total / step)))
    targets = np.linspace(0, total, n)
    xs = np.interp(targets, cum, poly[:, 0])
    ys = np.interp(targets, cum, poly[:, 1])
    return np.column_stack([xs, ys])


def outline_coverage(outlines, traced, dist_field=None, tolerance_factor=1.3):
    """For each outline point, compute distance to nearest stroke centerline.

    Returns (covered_mask, per_outline_max_dist, uncovered_runs).

    `covered_mask`: list (one per outline) of bool arrays; True = point is
        within the per-pixel tolerance of some stroke centerline.

    `tolerance_factor`: multiplier on the local stroke half-width (from
        the distance transform if provided; otherwise from the nearest
        stroke's own width). Default 1.3 allows for spline smoothing
        moving the centerline a little inside the boundary.

    `uncovered_runs`: list of (outline_idx, start_pt_idx, end_pt_idx, max_dist)
        for contiguous runs of uncovered points. These are the actionable
        "feature missed" signals.
    """
    if not traced:
        return [], [], [(i, 0, len(o) - 1, float('inf')) for i, o in enumerate(outlines)]

    # Pool every stroke centerline point with its half-width into a single
    # array for fast nearest-neighbour lookup.
    all_pts = []
    all_widths = []
    for xs, ys, ws in traced:
        all_pts.append(np.column_stack([xs, ys]))
        all_widths.append(np.asarray(ws, dtype=float) * 0.5)  # half-width
    all_pts = np.vstack(all_pts)
    all_widths = np.concatenate(all_widths)

    covered_mask = []
    uncovered_runs = []
    per_outline_max = []

    for oi, poly in enumerate(outlines):
        if len(poly) < 2:
            covered_mask.append(np.array([], dtype=bool))
            per_outline_max.append(0.0)
            continue
        # Resample to ~1px spacing so we get reliable runs for serifs.
        rp = _resample_polyline(poly, step=2.0)
        # For each outline sample, find nearest centerline point.
        # Vectorise with broadcasting; chunked to keep memory reasonable.
        chunk = 256
        dists = np.empty(len(rp), dtype=float)
        nearest_idx = np.empty(len(rp), dtype=int)
        for c0 in range(0, len(rp), chunk):
            block = rp[c0:c0 + chunk]
            d = np.hypot(block[:, 0, None] - all_pts[:, 0],
                         block[:, 1, None] - all_pts[:, 1])
            dists[c0:c0 + chunk] = d.min(axis=1)
            nearest_idx[c0:c0 + chunk] = d.argmin(axis=1)

        # Tolerance: nearest stroke's half-width × factor, clamped low.
        tol = np.maximum(all_widths[nearest_idx] * tolerance_factor, 2.0)
        covered = dists < tol
        covered_mask.append(covered)
        per_outline_max.append(float(dists.max()) if len(dists) else 0.0)

        # Find contiguous runs of uncovered points. Outlines wrap, so the
        # "first" and "last" runs may join.
        if covered.any() and not covered.all():
            # Find run boundaries
            uncov = ~covered
            edges = np.diff(uncov.astype(int))
            starts = list(np.where(edges == 1)[0] + 1)
            ends = list(np.where(edges == -1)[0])
            if uncov[0]:
                starts.insert(0, 0)
            if uncov[-1]:
                ends.append(len(uncov) - 1)
            for s, e in zip(starts, ends):
                run_len_px = float(np.hypot(rp[s, 0] - rp[e, 0],
                                            rp[s, 1] - rp[e, 1]))
                # Only report meaningful runs (a few pixels of un-covered
                # boundary isn't a missing feature; antialiasing alone
                # accounts for ~2px noise).
                if (e - s) > 2 and run_len_px > 4:
                    uncovered_runs.append(
                        (oi, int(s), int(e),
                         float(dists[s:e+1].max()),
                         (float(rp[s, 0]), float(rp[s, 1])),
                         (float(rp[e, 0]), float(rp[e, 1]))))
        elif not covered.any():
            uncovered_runs.append((oi, 0, len(rp) - 1, float(dists.max()),
                                   (float(rp[0, 0]), float(rp[0, 1])),
                                   (float(rp[-1, 0]), float(rp[-1, 1]))))

    return covered_mask, per_outline_max, uncovered_runs


def outline_issues(char, outlines, traced):
    """Wrap outline_coverage into Issue objects."""
    if not outlines:
        return []
    _covered, per_outline_max, uncovered_runs = outline_coverage(outlines, traced)
    issues = []
    for oi, s, e, max_d, p0, p1 in uncovered_runs:
        # Severity: how far out the run goes relative to typical stroke width.
        sev = 'high' if max_d > 12 else 'medium' if max_d > 6 else 'low'
        issues.append(Issue(
            char=char, kind='missing_outline_region', severity=sev,
            detail=f'outline {oi} pts {s}-{e} ({e-s+1} samples) > tolerance '
                   f'(max gap {max_d:.1f}px) near ({p0[0]:.0f},{p0[1]:.0f})',
            data={'outline': oi, 'start': s, 'end': e,
                  'max_dist': max_d, 'start_xy': p0, 'end_xy': p1},
        ))
    return issues


# ----- Layer 3: spec count match -----

def spec_issues(char, traced, spec_entry):
    """Compare against AI-derived spec.json entry."""
    if spec_entry is None:
        return []
    issues = []
    expected = spec_entry.get('stroke_count')
    actual = len(traced)
    if expected is not None and abs(actual - expected) >= 1:
        sev = 'high' if abs(actual - expected) >= 2 else 'medium'
        kind = 'under_traced' if actual < expected else 'over_traced'
        issues.append(Issue(
            char=char, kind=kind, severity=sev,
            detail=f'spec says {expected} strokes, got {actual}. '
                   f'Spec features: {", ".join(spec_entry.get("features", []))}',
            data={'expected': expected, 'actual': actual,
                  'features': spec_entry.get('features', [])},
        ))
    return issues


# ----- Layer 4: on-ink check -----

def off_ink_issues(char, traced, mask, max_off_fraction=0.10,
                   min_run_px=6.0):
    """Detect stroke centerlines that leave the glyph's ink.

    A correct centerline lies inside the mask everywhere (the medial
    axis is inside by construction). Sample every stroke point against
    the mask; report strokes whose off-ink fraction exceeds
    `max_off_fraction`, plus any contiguous off-ink run longer than
    `min_run_px` (a short crossing at a junction edge is noise; a long
    run is a stray cutting through white space).
    """
    H, W = mask.shape
    issues = []
    for i, (xs, ys, _ws) in enumerate(traced):
        xi = np.clip(np.round(xs).astype(int), 0, W - 1)
        yi = np.clip(np.round(ys).astype(int), 0, H - 1)
        on_ink = mask[yi, xi].astype(bool)
        if on_ink.all():
            continue
        off_fraction = float((~on_ink).mean())
        # Longest contiguous off-ink run, in path-length pixels.
        longest_run = 0.0
        run = 0.0
        for j in range(1, len(xs)):
            step = float(np.hypot(xs[j] - xs[j - 1], ys[j] - ys[j - 1]))
            if not on_ink[j]:
                run += step
                longest_run = max(longest_run, run)
            else:
                run = 0.0
        if off_fraction > max_off_fraction or longest_run > min_run_px:
            sev = 'high' if longest_run > 3 * min_run_px else 'medium'
            issues.append(Issue(
                char=char, kind='off_ink', severity=sev,
                detail=f'stroke {i} leaves the ink: {off_fraction:.0%} of '
                       f'points off-mask, longest off-run {longest_run:.0f}px',
                data={'stroke': i, 'off_fraction': off_fraction,
                      'longest_run_px': longest_run},
            ))
    return issues


# ----- Layer 5: invented terminal forks (flat-cap medial-axis horns) -----

def terminal_hook_issues(char, traced, outlines,
                         turn_deg=35.0, flatness_tol_factor=0.35):
    """Detect terminal hooks the medial axis INVENTED at flat stroke caps.

    The medial axis of a wide stroke with a flat end cap bifurcates into
    two short diagonal branches running to the cap's corners (the medial
    axis of a rectangle is its spine plus four corner diagonals). The
    junction-pairing then continues the spine into one of those corner
    branches, so the traced stroke ends with a little hook — a serif-like
    flick that does not exist in the letterform. Lato's flat-terminal
    stems show this clearly.

    Detection per stroke END:
      1. Hook geometry: within the last ~1.5×width of path, the heading
         turns by more than `turn_deg` relative to the approach heading.
      2. Outline flatness: collect outline points within 2×width of the
         stroke end and measure their deviation from a fitted straight
         line. FLAT outline (deviation ≤ flatness_tol_factor × width)
         means there is no real serif there — the hook is invented.
    Both must hold. A real serif fails (2): its outline protrudes.
    """
    if not outlines:
        return []
    outline_pts = np.vstack([np.asarray(p, dtype=float)
                             for p in outlines if len(p) >= 2])
    issues = []
    for i, (xs, ys, ws) in enumerate(traced):
        n = len(xs)
        if n < 12:
            continue
        med_w = float(np.median(ws))
        for end_name, idx_end, direction in (('start', 0, 1), ('end', n - 1, -1)):
            # Path indices: the terminal segment ≈ last 1.5×width of arc
            # length, the approach segment is the preceding 2×width.
            seg_len = 0.0
            j = idx_end
            terminal_j = None
            while 0 <= j + direction < n:
                j += direction
                seg_len += float(np.hypot(xs[j] - xs[j - direction],
                                          ys[j] - ys[j - direction]))
                if terminal_j is None and seg_len >= 1.5 * med_w:
                    terminal_j = j
                if seg_len >= 3.5 * med_w:
                    break
            if terminal_j is None or abs(j - terminal_j) < 2:
                continue
            approach_j = j
            # Headings (pointing toward the stroke end).
            hx_t = xs[idx_end] - xs[terminal_j]
            hy_t = ys[idx_end] - ys[terminal_j]
            hx_a = xs[terminal_j] - xs[approach_j]
            hy_a = ys[terminal_j] - ys[approach_j]
            na = math.hypot(hx_a, hy_a)
            nt = math.hypot(hx_t, hy_t)
            if na < 1e-6 or nt < 1e-6:
                continue
            cosv = (hx_t * hx_a + hy_t * hy_a) / (na * nt)
            turn = math.degrees(math.acos(max(-1.0, min(1.0, cosv))))
            if turn < turn_deg:
                continue
            # Outline flatness near the end point. outline pts are (x, y).
            ex, ey = float(xs[idx_end]), float(ys[idx_end])
            d = np.hypot(outline_pts[:, 0] - ex, outline_pts[:, 1] - ey)
            near = outline_pts[d <= 2.0 * med_w]
            if len(near) < 4:
                continue
            # Fit a line via PCA; max perpendicular deviation = flatness.
            c = near - near.mean(axis=0)
            _, _, vt = np.linalg.svd(c, full_matrices=False)
            deviation = float(np.abs(c @ vt[1]).max())
            if deviation <= flatness_tol_factor * med_w:
                issues.append(Issue(
                    char=char, kind='invented_terminal_fork',
                    severity='medium',
                    detail=f'stroke {i} {end_name} hooks {turn:.0f}° within '
                           f'1.5×width of a FLAT outline cap (deviation '
                           f'{deviation:.1f}px vs width {med_w:.1f}px) — '
                           f'medial-axis corner branch, not a real feature',
                    data={'stroke': i, 'which_end': end_name,
                          'turn_deg': turn, 'outline_deviation': deviation,
                          'median_width': med_w},
                ))
    return issues


# ----- Top-level cascade -----

def run_cascade(char, traced, outlines=None, spec_entry=None, mask=None):
    """Run all layers and return a flat list of Issues."""
    issues = []
    issues.extend(geometric_issues(char, traced))
    if outlines:
        issues.extend(outline_issues(char, outlines, traced))
        issues.extend(terminal_hook_issues(char, traced, outlines))
    if spec_entry is not None:
        issues.extend(spec_issues(char, traced, spec_entry))
    if mask is not None:
        issues.extend(off_ink_issues(char, traced, mask))
    return issues


def run_cascade_on_output(output_dir, font_path, letters, size=384,
                          tracer='eulerian'):
    """Run cascade across a finished trace output directory.

    Reads spec.json if present, re-extracts outlines for every letter,
    re-traces with the SAME tracer the pipeline default uses (since
    trace results aren't saved in raw form). Returns a dict
    char -> [Issue, ...].
    """
    from penstroke.core.outline import extract_outlines
    if tracer == 'eulerian':
        from penstroke.templates.eulerian import trace_glyph_eulerian as _trace
    else:
        from penstroke.templates.trace import trace_glyph as _trace

    spec = None
    spec_path = os.path.join(output_dir, 'spec.json')
    if os.path.exists(spec_path):
        with open(spec_path, encoding='utf-8') as f:
            spec = json.load(f)

    per_letter_issues = {}
    for ch in letters:
        try:
            mask, _, _, traced, _, _ = _trace(font_path, ch, size=size)
        except Exception:
            continue
        try:
            outlines = extract_outlines(font_path, ch, size=size)
        except Exception:
            outlines = []
        spec_entry = spec.get(ch) if spec else None
        issues = run_cascade(ch, traced, outlines=outlines,
                             spec_entry=spec_entry, mask=mask)
        if issues:
            per_letter_issues[ch] = issues
    return per_letter_issues


def issues_to_markdown(per_letter_issues):
    """Render the cascade output as a readable report fragment."""
    if not per_letter_issues:
        return '## Cascade QA\n\n_No issues detected._\n'
    lines = ['## Cascade QA', '',
             f'Found issues in **{len(per_letter_issues)} letters**.', '']
    # Group by severity for the summary
    counts = {'high': 0, 'medium': 0, 'low': 0}
    kind_counts = {}
    for issues in per_letter_issues.values():
        for iss in issues:
            counts[iss.severity] = counts.get(iss.severity, 0) + 1
            kind_counts[iss.kind] = kind_counts.get(iss.kind, 0) + 1
    lines.append(f'Severity: {counts["high"]} high, '
                 f'{counts["medium"]} medium, {counts["low"]} low.')
    lines.append('')
    lines.append('Issue kinds: ' + ', '.join(
        f'{k} ×{v}' for k, v in sorted(kind_counts.items(), key=lambda kv: -kv[1])))
    lines.append('')
    lines.append('### Per-letter detail')
    lines.append('')
    for ch, issues in sorted(per_letter_issues.items()):
        lines.append(f'**{ch}** — {len(issues)} issue(s)')
        for iss in issues:
            lines.append(f'  - [{iss.severity}] _{iss.kind}_: {iss.detail}')
        lines.append('')
    return '\n'.join(lines)
