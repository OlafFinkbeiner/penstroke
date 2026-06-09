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
