"""Topological fingerprints for matching Hershey templates to target glyphs.

Given a target font's skeleton and a Hershey template, we want to decide
whether they describe the same letter topology. A "double-story 'a'" template
(bowl + separate stem) shouldn't be matched to a single-story handwritten 'a'
even though both are called 'a'.

The fingerprint we compute on each side:
  - number of endpoints (open stroke terminals)
  - number of closed loops (strokes that start and end at the same point)
  - number of distinct strokes / connected components

`score_template_match` compares the two fingerprints. Lower scores mean
better matches. The biggest penalty is for closed-loop mismatch — that's
the signal that catches the single-story/double-story 'a' distinction.
"""

import numpy as np
import scipy.ndimage as ndi


def template_topology(strokes):
    """Fingerprint a Hershey template by stroke count and open/closed mix.

    Returns dict with:
        n_strokes:    total strokes
        n_open:       strokes that don't return to their start
        n_closed:     strokes that form a closed loop (start ≈ end)
        n_endpoints:  2 × n_open (each open stroke has two ends)
    """
    n_open = 0
    n_closed = 0
    for s in strokes:
        # A stroke is closed if its first and last points coincide AND it
        # has enough points to actually be a loop (degenerate 2-point
        # "loops" are just zero-length lines).
        if tuple(s[0]) == tuple(s[-1]) and len(s) > 4:
            n_closed += 1
        else:
            n_open += 1
    return {
        'n_strokes': len(strokes),
        'n_open': n_open,
        'n_closed': n_closed,
        'n_endpoints': 2 * n_open,
    }


def skeleton_topology(skel):
    """Fingerprint a target glyph's skeleton.

    Counts skeleton pixels by their neighbor count (endpoints = degree 1,
    junctions = degree ≥3), and counts 8-connected components that have
    no endpoints (= closed loops).

    Returns dict with:
        n_endpoints:    skeleton pixels with exactly one neighbor
        n_junctions:    skeleton pixels with three or more neighbors
        n_components:   total 8-connected components
        n_closed_loops: components that contain no endpoint
                        (= the skeleton wraps around with no terminal)
    """
    H, W = skel.shape

    # Per-pixel neighbor count using the same trick as skeleton_to_graph
    neigh = np.zeros_like(skel, dtype=np.uint8)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            neigh += np.roll(np.roll(skel, dy, 0), dx, 1).astype(np.uint8)
    neigh *= skel.astype(np.uint8)

    n_endpoints = int((skel & (neigh == 1)).sum())
    n_junctions = int((skel & (neigh >= 3)).sum())

    structure = np.ones((3, 3), dtype=int)  # 8-connectivity
    labeled, n_components = ndi.label(skel, structure=structure)

    # A closed loop component is one where no pixel has degree 1.
    n_closed_loops = 0
    endpoint_mask = (skel & (neigh == 1))
    for cc in range(1, n_components + 1):
        cc_mask = (labeled == cc)
        if cc_mask.sum() < 8:
            continue  # too small to be a real stroke
        if int((endpoint_mask & cc_mask).sum()) == 0:
            n_closed_loops += 1

    return {
        'n_endpoints': n_endpoints,
        'n_junctions': n_junctions,
        'n_components': n_components,
        'n_closed_loops': n_closed_loops,
    }


def score_template_match(skel_topo, tmpl_topo):
    """Score how well a template's topology matches a target skeleton's.

    Lower is better; 0 is perfect. The closed-loop count carries a 5×
    penalty weight because it's the strongest signal — it distinguishes
    single-story vs double-story letterforms and open-tail vs closed-tail
    descenders, which the other counts can't reliably tell apart.

    A small per-stroke penalty makes the scorer prefer simpler templates
    on otherwise-equal matches.
    """
    closed_diff = abs(skel_topo['n_closed_loops'] - tmpl_topo['n_closed'])
    endpoint_diff = abs(skel_topo['n_endpoints'] - tmpl_topo['n_endpoints'])
    n_strokes_penalty = tmpl_topo['n_strokes'] * 0.1
    return 5.0 * closed_diff + endpoint_diff + n_strokes_penalty
