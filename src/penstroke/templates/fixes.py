"""Post-trace repair: cover any skeleton branches the template walker missed.

The Hershey template walker is rigid — it only visits skeleton pixels along
its template strokes. Anything off the template (serif spurs, the tail of
'Q', a 'f' crossbar that the template doesn't include) is left untraced.

Two repair stages:

  1. extend_strokes_into_spurs (called BEFORE smoothing)
       For each raw skeleton-pixel stroke, BFS from each endpoint into
       any adjacent uncovered skeleton pixels. The walked spur is
       PREPENDED or APPENDED to the stroke, so a stem grows into its
       foot-serif as one continuous pen motion. This is what "draw two
       in one stroke" means: no extra pen lift, the serif is part of
       the same stroke.

  2. cover_missing_branches (called AFTER smoothing, as a fallback)
       Whatever uncovered fragments remain — typically things not
       adjacent to any stroke endpoint, like a crossbar that floats
       between two stems — get walked as their own new strokes.

Coverage is measured pixel-wise: a skeleton pixel is "covered" if it
lies within `tolerance_px` of some traced-stroke centerline sample
(stage 2) or in the visited-pixel set (stage 1).
"""

import numpy as np
import networkx as nx
from scipy.ndimage import label as cc_label

from penstroke.core.smoothing import smooth_and_wobble


def _build_centerline_mask(traced, shape, tolerance_px=3.0):
    """Boolean image: True where any traced stroke centerline passes within tol."""
    H, W = shape
    if not traced:
        return np.zeros((H, W), dtype=bool)

    # Splat each stroke's sample points with a disc of radius `tolerance_px`.
    # Vectorise via flat index + small kernel application.
    yy, xx = np.ogrid[-int(tolerance_px):int(tolerance_px) + 1,
                       -int(tolerance_px):int(tolerance_px) + 1]
    disc = (yy * yy + xx * xx) <= tolerance_px * tolerance_px
    mask = np.zeros((H, W), dtype=bool)
    r_off = int(tolerance_px)

    for xs, ys, _ in traced:
        rs = np.clip(np.round(ys).astype(int), 0, H - 1)
        cs = np.clip(np.round(xs).astype(int), 0, W - 1)
        for r, c in zip(rs, cs):
            r0, r1 = max(0, r - r_off), min(H, r + r_off + 1)
            c0, c1 = max(0, c - r_off), min(W, c + r_off + 1)
            dr0, dc0 = r0 - (r - r_off), c0 - (c - r_off)
            dr1 = dr0 + (r1 - r0)
            dc1 = dc0 + (c1 - c0)
            mask[r0:r1, c0:c1] |= disc[dr0:dr1, dc0:dc1]
    return mask


def extend_strokes_into_spurs(strokes_pixels, pixel_G,
                              max_extension=80, min_extension=3):
    """Extend each stroke's pixel path into adjacent uncovered skeleton spurs.

    Mutates `strokes_pixels` in place. For each stroke:
      - Look at its two endpoint pixels.
      - From each endpoint, walk into uncovered skeleton through the
        pixel graph: follow a chain of degree-≤2 nodes until we hit a
        junction, an endpoint of the skeleton, an already-covered pixel,
        or a length cap.
      - The walked chain is prepended (at start) or appended (at end).

    Two adjacent strokes can each try to extend into the same spur; the
    first one wins because covered-pixel tracking is updated as we go.

    Args:
        strokes_pixels: list[list[(r,c)]] — raw skeleton paths from the
            template walker (before smoothing).
        pixel_G: networkx graph of skeleton pixels.
        max_extension: hard cap on extension length, in pixels. Stops
            runaway extensions through long branches we shouldn't claim
            (e.g. the bowl of a 'b' shouldn't get sucked into the stem
            stroke's extension).
        min_extension: don't bother appending extensions shorter than
            this many pixels; not worth the noise.

    Returns:
        the modified strokes_pixels (same list, mutated).
    """
    covered = set()
    for s in strokes_pixels:
        covered.update(s)

    for s in strokes_pixels:
        if len(s) < 2:
            continue
        # Try to extend from the start
        ext_start = _walk_uncovered_chain(s[0], s[1], pixel_G, covered, max_extension)
        if len(ext_start) >= min_extension:
            # ext_start is ordered from inside-out (s[0] is at the
            # "anchor" end). We want to prepend in the outside-to-inside
            # direction so the new stroke reads as continuous.
            new_prefix = list(reversed(ext_start))
            # ext_start[0] is the first new pixel; the last in reversed
            # equals s[0] only if we included it. We didn't, so just
            # concatenate.
            s[:0] = new_prefix
            covered.update(new_prefix)

        # Re-fetch end (s changed after prefix insertion)
        end = s[-1]
        prev_end = s[-2] if len(s) >= 2 else None
        ext_end = _walk_uncovered_chain(end, prev_end, pixel_G, covered, max_extension)
        if len(ext_end) >= min_extension:
            s.extend(ext_end)
            covered.update(ext_end)
    return strokes_pixels


def merge_endpoint_adjacent_strokes(traced, tolerance_factor=1.5,
                                    tangent_alignment_min=0.75):
    """Join strokes only when they form a continuous curve at the join.

    Two stroke records may describe a single continuous pen motion split
    across stages of the pipeline. We merge them when BOTH:
      (a) their endpoints are within `tolerance_factor × max_width`, AND
      (b) the tangents at the join are nearly parallel (cosine ≥
          alignment_min — default 0.75 means angle ≤ 41°).

    The tight angle threshold means we only merge SMOOTH continuations,
    not corners or crossings. A serif foot extending the stem at the
    same downward angle = merge. A bowl exit meeting an exit-flick at
    a sharp angle = do NOT merge (the pen lifted there).

    Mutates `traced` in place by replacement and returns it.
    """
    if len(traced) < 2:
        return traced

    changed = True
    while changed and len(traced) > 1:
        changed = False
        n = len(traced)
        for i in range(n):
            best = None
            for j in range(n):
                if i == j:
                    continue
                A = traced[i]
                B = traced[j]
                a_w = float(np.mean(A[2]))
                b_w = float(np.mean(B[2]))
                tol = max(a_w, b_w) * tolerance_factor
                # 4 join orientations
                cands = [
                    ('end_start',
                     _endpoint_dist(A, -1, B, 0),
                     _tangent_align(A, -1, 'out', B, 0, 'in')),
                    ('end_end',
                     _endpoint_dist(A, -1, B, -1),
                     _tangent_align(A, -1, 'out', B, -1, 'out')),
                    ('start_start',
                     _endpoint_dist(A, 0, B, 0),
                     _tangent_align(A, 0, 'in', B, 0, 'in')),
                    ('start_end',
                     _endpoint_dist(A, 0, B, -1),
                     _tangent_align(A, 0, 'in', B, -1, 'out')),
                ]
                cands = [(k, d, t) for k, d, t in cands
                         if d < tol and t >= tangent_alignment_min]
                if not cands:
                    continue
                kind, d, _t = min(cands, key=lambda x: x[1])
                if best is None or d < best[1]:
                    best = (j, d, kind)
            if best is None:
                continue
            j, d, kind = best
            merged = _do_merge(traced[i], traced[j], kind)
            # Reject merges that produce a stroke whose topmost point sits
            # in the interior — that means we joined two motions that the
            # pen would naturally lift between (e.g. Caveat 'b': stem-up
            # plus bowl-around become a single weird path going bottom
            # to top to bottom to right). Skip those.
            if _topmost_in_interior(merged):
                continue
            new_list = [s for k, s in enumerate(traced) if k != i and k != j]
            new_list.append(merged)
            traced.clear()
            traced.extend(new_list)
            changed = True
            break
    return traced


def _topmost_in_interior(stroke, edge_fraction=0.15):
    """True if the topmost y is more than edge_fraction away from either end.

    A path that genuinely starts or ends at the top has argmin(ys) close
    to index 0 or len(ys)-1. If it sits in the middle, the stroke makes
    an N-shape: down-up-down (or up-down-up), which no natural pen motion
    would draw without a lift.

    Closed loops (start ≈ end) are excluded — their topmost can be
    anywhere because rotation around the loop is free.
    """
    xs, ys, ws = stroke
    end_to_start = float(np.hypot(xs[-1] - xs[0], ys[-1] - ys[0]))
    avg_w = float(np.mean(ws))
    if end_to_start < max(6.0, avg_w * 1.5):
        return False  # closed loop, topmost position arbitrary
    top_idx = int(np.argmin(ys))
    n = len(ys)
    edge = max(1, int(n * edge_fraction))
    return edge < top_idx < n - edge


def _tangent_align(A, ai, A_end, B, bi, B_end):
    """Cosine of the angle between two stroke tangents at the merge point.

    A_end and B_end are 'in' (vector pointing INTO the endpoint, i.e.
    from a few samples back to the endpoint) or 'out' (vector pointing
    AWAY from the endpoint into the stroke).

    For an end_start merge, we want A's outgoing tangent at its end to
    align with B's incoming-from-start (= away-from-start) direction.
    Two tangents along the same drawn direction give cos ≈ +1.
    """
    xa, ya, _ = A
    xb, yb, _ = B
    window = 8  # samples from endpoint to use for the tangent estimate
    if A_end == 'out':
        # vector from inner sample → endpoint pixel ai (which is endpoint)
        ia = max(0, ai - window) if ai == -1 else min(len(xa) - 1, ai + window)
        t_a = (float(xa[ai] - xa[ia]), float(ya[ai] - ya[ia]))
    else:  # 'in' = vector pointing INTO the endpoint from outside
        ia = min(len(xa) - 1, ai + window) if ai == 0 else max(0, ai - window)
        t_a = (float(xa[ia] - xa[ai]), float(ya[ia] - ya[ai]))
    if B_end == 'out':
        ib = max(0, bi - window) if bi == -1 else min(len(xb) - 1, bi + window)
        t_b = (float(xb[bi] - xb[ib]), float(yb[bi] - yb[ib]))
    else:
        ib = min(len(xb) - 1, bi + window) if bi == 0 else max(0, bi - window)
        t_b = (float(xb[ib] - xb[bi]), float(yb[ib] - yb[bi]))
    # For end_start: A's out tangent vs B's in tangent — both point in
    # the direction of continued drawing, should align.
    na = (t_a[0]**2 + t_a[1]**2) ** 0.5
    nb = (t_b[0]**2 + t_b[1]**2) ** 0.5
    if na < 1e-6 or nb < 1e-6:
        return 0.0
    return (t_a[0]*t_b[0] + t_a[1]*t_b[1]) / (na * nb)


def _endpoint_dist(A, ai, B, bi):
    xa, ya, _ = A
    xb, yb, _ = B
    return float(np.hypot(xa[ai] - xb[bi], ya[ai] - yb[bi]))


def _do_merge(A, B, kind):
    """Concatenate two strokes per the given orientation."""
    xa, ya, wa = A
    xb, yb, wb = B
    if kind == 'end_start':
        return (np.concatenate([xa, xb]),
                np.concatenate([ya, yb]),
                np.concatenate([wa, wb]))
    if kind == 'end_end':
        return (np.concatenate([xa, xb[::-1]]),
                np.concatenate([ya, yb[::-1]]),
                np.concatenate([wa, wb[::-1]]))
    if kind == 'start_start':
        return (np.concatenate([xa[::-1], xb]),
                np.concatenate([ya[::-1], yb]),
                np.concatenate([wa[::-1], wb]))
    # start_end
    return (np.concatenate([xb, xa]),
            np.concatenate([yb, ya]),
            np.concatenate([wb, wa]))


def split_topmost_interior_strokes(traced, edge_fraction=0.15):
    """Split N-shaped strokes (one-peak path) at their topmost point.

    A pen-drawn stroke either starts or ends at its highest point — you
    never write up-down-up without a lift. When the template walker or
    merging produces such an N-shape (e.g. Caveat 'b': bottom of stem →
    top → bottom → bowl), splitting at the topmost gives two clean
    strokes both starting from the top.

    BUT — multi-hump waves like Caveat 'm' or 'w' also have interior
    topmost points (at each hump). They're NOT N-shapes; they're the
    intended continuous motion. We distinguish by counting the number
    of significant y-direction reversals:
      • 1 reversal  → single peak, N-shape, split.
      • >1 reversal → wave, leave alone.

    Closed loops (start ≈ end) are skipped entirely.
    """
    out = []
    for xs, ys, ws in traced:
        avg_w = float(np.mean(ws))
        end_to_start = float(np.hypot(xs[-1] - xs[0], ys[-1] - ys[0]))
        if end_to_start < max(6.0, avg_w * 1.5):
            out.append((xs, ys, ws))  # closed loop
            continue
        n = len(ys)
        edge = max(1, int(n * edge_fraction))
        top_idx = int(np.argmin(ys))
        if not (edge < top_idx < n - edge):
            out.append((xs, ys, ws))
            continue
        # Distinguish a wave (m, w) from a tour (b's stem-then-bowl).
        # The tour revisits its starting region after going up — we'll
        # see an interior point that comes close to the start position.
        # A wave keeps moving across, so no interior point sits near
        # start. Check the interior (excluding first/last 10%) for a
        # near-start return.
        n_skip = max(3, int(n * 0.1))
        interior_xs = xs[n_skip:-n_skip]
        interior_ys = ys[n_skip:-n_skip]
        if len(interior_xs) > 0:
            d_to_start = np.hypot(interior_xs - xs[0], interior_ys - ys[0])
            d_to_end = np.hypot(interior_xs - xs[-1], interior_ys - ys[-1])
            returns_to_start = d_to_start.min() < max(8.0, avg_w * 1.5)
            returns_to_end = d_to_end.min() < max(8.0, avg_w * 1.5)
        else:
            returns_to_start = returns_to_end = False
        if not (returns_to_start or returns_to_end):
            out.append((xs, ys, ws))  # no revisit — likely a wave, leave alone
            continue
        # Tour / N-shape: split at topmost.
        a = (xs[top_idx::-1].copy(), ys[top_idx::-1].copy(), ws[top_idx::-1].copy())
        b = (xs[top_idx:].copy(), ys[top_idx:].copy(), ws[top_idx:].copy())
        out.append(a)
        out.append(b)
    return out


def _count_y_reversals(ys, min_prominence=4.0):
    """Number of significant up/down direction changes along `ys`.

    Smooths first (so spline wobble doesn't register as a reversal),
    then counts sign changes in the derivative whose prominence (the
    y-range traversed before the next reversal) exceeds the threshold.
    """
    if len(ys) < 3:
        return 0
    # Mild smoothing to drop wobble
    w = max(3, len(ys) // 30)
    kernel = np.ones(w) / w
    smoothed = np.convolve(ys, kernel, mode='valid')
    dy = np.diff(smoothed)
    # Find sign-change indices
    signs = np.sign(dy)
    signs[signs == 0] = 1
    sign_changes = np.where(np.diff(signs) != 0)[0]
    if len(sign_changes) == 0:
        return 0
    # Filter: prominence between consecutive extrema must exceed threshold.
    reversals = 0
    last_y = smoothed[0]
    for idx in sign_changes:
        y_here = smoothed[idx + 1]
        if abs(y_here - last_y) >= min_prominence:
            reversals += 1
            last_y = y_here
    return reversals


def deduplicate_overlapping_strokes(traced,
                                    contained_threshold=0.9,
                                    sample_tolerance_factor=1.0):
    """Drop strokes that are almost fully contained inside another stroke.

    For each pair (A, B): if ≥ contained_threshold of A's sample points
    lie within `tol` of any of B's sample points, A is essentially a
    subset of B — drop A. (A "containment" check, not a mutual-overlap
    check.)

    Crucial test cases:
      • Caveat 'b' stem-traced-twice: a short stem-only stroke is
        ~100% contained inside the whole-letter stroke → drop. The
        whole-letter stroke is NOT mostly inside the stem-only stroke
        (it has bowl content), so the whole-letter survives.
      • Caveat 'f' crossbar protruding to the right: only the part of
        the crossbar near the stem-curve overlaps; the protruding tip
        does NOT lie near any other stroke's path. So the crossbar's
        contained-fraction stays well below 0.9 — preserved.

    We process pairs in (shorter, longer) order so the shorter one is
    the candidate for being contained.
    """
    if len(traced) < 2:
        return traced

    dropped = set()
    pts = []
    widths = []
    lengths = []
    for xs, ys, ws in traced:
        pts.append(np.column_stack([xs, ys]))
        widths.append(float(np.mean(ws)))
        lengths.append(float(np.sqrt(np.diff(xs)**2 + np.diff(ys)**2).sum()))

    def cov_fraction(a_pts, b_pts, tol):
        if len(a_pts) == 0 or len(b_pts) == 0:
            return 0.0
        sub = a_pts if len(a_pts) <= 60 else a_pts[::len(a_pts) // 60]
        dists = np.empty(len(sub))
        for k, p in enumerate(sub):
            dists[k] = np.hypot(b_pts[:, 0] - p[0],
                                b_pts[:, 1] - p[1]).min()
        return float((dists < tol).mean())

    # Pairs sorted by length so we test the shorter (candidate-to-drop)
    # against the longer (potential container).
    order = sorted(range(len(traced)), key=lambda k: lengths[k])
    for short_idx_pos in range(len(order)):
        i = order[short_idx_pos]
        if i in dropped:
            continue
        for long_idx_pos in range(short_idx_pos + 1, len(order)):
            j = order[long_idx_pos]
            if j in dropped:
                continue
            tol = max(widths[i], widths[j]) * sample_tolerance_factor
            cov_ij = cov_fraction(pts[i], pts[j], tol)
            if cov_ij >= contained_threshold:
                dropped.add(i)
                break
    return [s for k, s in enumerate(traced) if k not in dropped]


def order_strokes_main_first(traced):
    """Reorder strokes so the main/dominant stroke comes first.

    Natural handwriting order: the main stem(s) get drawn first, then
    accessories (crossbars, arms, tails, descenders), with dots last.
    Without ordering, our trace produces strokes in whatever order the
    template walker and fix stages happened to emit — leading to
    animations where 'k' draws its arms before the stem or 'g' draws
    its descender before the bowl.

    Heuristic:
      1. Primary key: stroke physical length, descending. The main
         stem of a letter is almost always the longest stroke; the
         crossbar, arm, descender hook is shorter.
      2. Tiebreaker: start_x ascending. For strokes of similar length
         (e.g. the two diagonals of A or V), draw the leftmost first
         — the natural left-to-right writing direction.

    Dots are NOT reordered with this rule; they're appended separately
    after this function runs (see templates/trace.py).
    """
    if len(traced) < 2:
        return traced
    decorated = []
    for s in traced:
        xs, ys, ws = s
        plen = float(np.sqrt(np.diff(xs)**2 + np.diff(ys)**2).sum())
        decorated.append((s, plen, float(xs[0])))
    # Sort by (-length, start_x): longest first, leftmost first as tiebreak.
    decorated.sort(key=lambda kv: (-kv[1], kv[2]))
    return [d[0] for d in decorated]


def normalize_stroke_directions(traced):
    """Apply the top-down writing convention to each stroke.

    Pen-and-paper writing starts strokes at their TOPMOST point and
    proceeds downward — the natural gravity-with-the-hand direction.
    For each stroke we find the index of the topmost point (min y) and
    its index. If that point sits in the second half of the path, the
    stroke was drawn the wrong way around — flip it.

    This handles cases the simple "vertical-dominant" heuristic missed:
    a Caveat 'b' whose stroke spirals up the stem and around the bowl
    has its topmost point near the END (because the spiral ends at the
    top of the bowl); flipping makes it start at the top.

    Closed loops are detected by their start/end proximity and skipped
    (direction is arbitrary on a closed loop).
    """
    out = []
    for xs, ys, ws in traced:
        # Closed loop test: start ~ end relative to stroke width
        avg_w = float(np.mean(ws))
        end_to_start = float(np.hypot(xs[-1] - xs[0], ys[-1] - ys[0]))
        if end_to_start < max(6.0, avg_w * 1.5):
            out.append((xs, ys, ws))
            continue
        top_idx = int(np.argmin(ys))
        if top_idx > len(ys) // 2:
            out.append((xs[::-1], ys[::-1], ws[::-1]))
        else:
            out.append((xs, ys, ws))
    return out


def _walk_uncovered_chain(start, incoming_neighbor, pixel_G, covered, max_len):
    """Walk away from `start` along uncovered skeleton pixels.

    Used to extend a stroke past its current endpoint into an attached
    spur (serif, tail, etc.). The chain continues only along uncovered
    skeleton pixels.

    When the walk hits a junction (multiple uncovered branches diverging),
    we pick the LONGEST branch to continue along — that's typically the
    visually-dominant continuation (e.g. the longer half of a slab serif).
    Other branches remain for the second-stage `cover_missing_branches`
    to pick up as separate strokes if substantial enough.

    `incoming_neighbor` is the next-to-last pixel of the existing
    stroke — we won't immediately backtrack into it.

    Returns the chain WITHOUT `start` (which is already in the stroke),
    ordered from `start`-adjacent outward.
    """
    if start not in pixel_G:
        return []
    chain = []
    cur = start
    prev = incoming_neighbor
    while len(chain) < max_len:
        nbrs = [n for n in pixel_G.neighbors(cur)
                if n != prev and n not in covered and n not in chain]
        if not nbrs:
            break
        if len(nbrs) == 1:
            nxt = nbrs[0]
        else:
            # Junction: pick the branch with the longest uncovered run.
            best_n, best_len = None, -1
            for cand in nbrs:
                run = _measure_uncovered_run(cand, cur, pixel_G,
                                             covered | set(chain),
                                             cap=max_len - len(chain))
                if run > best_len:
                    best_len = run
                    best_n = cand
            if best_n is None or best_len < 3:
                # Junction with only stubby branches — not worth following
                break
            nxt = best_n
        chain.append(nxt)
        prev = cur
        cur = nxt
    return chain


def _measure_uncovered_run(start, prev, pixel_G, covered, cap=40):
    """Length of the longest uncovered chain reachable from `start`.

    Simple linear walk: follow degree-≤2 nodes until a junction or dead
    end. Approximates branch length without full graph search.
    """
    if start in covered or start not in pixel_G:
        return 0
    length = 1
    cur, p = start, prev
    visited = {start}
    while length < cap:
        nbrs = [n for n in pixel_G.neighbors(cur)
                if n != p and n not in covered and n not in visited]
        if len(nbrs) != 1:
            break
        nxt = nbrs[0]
        visited.add(nxt)
        length += 1
        p, cur = cur, nxt
    return length


def cover_missing_branches(skel, dist, traced, pixel_G,
                           tolerance_px=None, min_branch_len=10, seed=42):
    """Add strokes for skeleton branches the template walker missed.

    Args:
        skel: bool skeleton array.
        dist: distance-to-boundary field (for smooth_and_wobble).
        traced: list of (xs, ys, widths) already-traced strokes.
        pixel_G: skeleton pixel graph (from build_skel_pixel_graph).
        tolerance_px: a skeleton pixel within this many px of an existing
            stroke centerline counts as already covered.
        min_branch_len: ignore connected components smaller than this many
            pixels — those are just noise, not features.

    Returns:
        list of added (xs, ys, widths) strokes. Appendable to `traced`.
    """
    # Tolerance scales with median stroke half-width — thick strokes can
    # accept the spline drifting a few px from their original skeleton
    # path; thin strokes need a tighter band. Default to 1.0 × half-width
    # plus a 3-pixel floor.
    if tolerance_px is None:
        if traced:
            med_hw = float(np.median([np.median(ws) * 0.5 for _, _, ws in traced]))
            tolerance_px = max(3.0, med_hw)
        else:
            tolerance_px = 4.0
    cover = _build_centerline_mask(traced, skel.shape, tolerance_px)
    uncovered = skel & ~cover
    if not uncovered.any():
        return []

    # Connected components of the uncovered skeleton (8-connectivity).
    structure = np.ones((3, 3), dtype=int)
    components, n = cc_label(uncovered, structure=structure)

    additions = []
    for cid in range(1, n + 1):
        ys, xs = np.where(components == cid)
        if len(ys) < min_branch_len:
            continue
        pixels = [(int(r), int(c)) for r, c in zip(ys, xs)]

        # Walk this connected fragment as a path. The fragment is a sub-
        # graph of the skeleton; we want a longest simple path through it,
        # which is approximated by walking from one endpoint to the other.
        sub_G = pixel_G.subgraph(pixels).copy()
        if len(sub_G) == 0:
            continue
        path = _longest_path_in_sub(sub_G)
        if path is None or len(path) < min_branch_len:
            continue

        # Many missed features are tiny serif spurs hanging off a covered
        # branch. The fragment endpoint adjacent to the rest of the
        # skeleton is the "anchor"; extending the stroke to start AT that
        # anchor (snapping into the parent stroke) makes it visually
        # connect rather than float in mid-air.
        anchor = _find_anchor(path, pixel_G, sub_G)
        if anchor is not None and anchor != path[0]:
            # Walk shortest path from anchor to path[0] to get a clean
            # entry into the missed branch.
            try:
                approach = nx.shortest_path(pixel_G, anchor, path[0])
                if approach and approach[-1] == path[0]:
                    path = approach + path[1:]
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                pass

        smoothed = smooth_and_wobble(path, dist, seed=seed + cid)
        if smoothed is None:
            continue
        # Self-QA: drop the addition if it'd be flagged as a phantom or
        # zigzag by the cascade. We're trying to fix issues, not introduce
        # new ones.
        xs, ys, widths = smoothed
        plen = float(np.sqrt(np.diff(xs)**2 + np.diff(ys)**2).sum())
        disp = float(np.hypot(xs[-1] - xs[0], ys[-1] - ys[0]))
        extent = max(float(xs.max() - xs.min()), float(ys.max() - ys.min()))
        avg_w = float(np.mean(widths))
        if extent < avg_w:
            continue  # phantom: fits inside its own pen stamp
        if disp > 1 and plen / disp > 2.5:
            continue  # zigzag: backtracks substantially
        additions.append(smoothed)
    return additions


def _longest_path_in_sub(sub_G):
    """Approximate longest simple path in a small sub-graph.

    For tree-shaped fragments (which serif spurs almost always are) this is
    the diameter path. We compute via two-pass BFS — pick any node, BFS to
    find the farthest, BFS again from there.
    """
    nodes = list(sub_G.nodes())
    if not nodes:
        return None
    if len(nodes) == 1:
        return nodes
    # 1st BFS from arbitrary node
    start = nodes[0]
    far1, _ = _bfs_farthest(sub_G, start)
    far2, parent = _bfs_farthest(sub_G, far1)
    # Reconstruct path far1 → far2
    path = [far2]
    while path[-1] != far1:
        nxt = parent.get(path[-1])
        if nxt is None:
            break
        path.append(nxt)
    return list(reversed(path))


def _bfs_farthest(G, start):
    """Return (farthest_node, parent_dict) from BFS starting at `start`."""
    from collections import deque
    parent = {start: None}
    seen = {start}
    far = start
    q = deque([(start, 0)])
    max_d = 0
    while q:
        n, d = q.popleft()
        if d > max_d:
            max_d = d
            far = n
        for nb in G.neighbors(n):
            if nb not in seen:
                seen.add(nb)
                parent[nb] = n
                q.append((nb, d + 1))
    return far, parent


def _find_anchor(path, pixel_G, sub_G):
    """Find a pixel in the FULL skeleton graph that's adjacent to one of
    `path`'s endpoints — i.e. the point where this fragment attaches to
    the already-traced part of the skeleton. Returns None if disconnected.
    """
    sub_nodes = set(sub_G.nodes())
    for ep in (path[0], path[-1]):
        for nb in pixel_G.neighbors(ep):
            if nb not in sub_nodes:
                return nb
    return None
