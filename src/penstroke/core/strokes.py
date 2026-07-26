"""Closed-loop tracing for the junction-first tracer.

  - trace_closed_loops: walk pure-cycle skeleton components ('o', 'O')
    that produce no graph nodes

The original greedy stroke-decomposition pipeline that lived here
(decompose_strokes / build_strokes / order_strokes / tangent_at)
predated the junction-first tracer and was removed once it had no
callers — it was a divergent second implementation of the same pairing
concept, with different constants. If a fallback decomposition is ever
needed, route it through tracer.analyze_junctions/build_chains with
different parameters instead of resurrecting the greedy walker (git
history has it if needed for reference).
"""

import numpy as np
from scipy.ndimage import label


def trace_closed_loops(skel, covered=None):
    """Handle glyphs whose skeleton is a closed loop with no endpoints
    or junctions ('o', 'O', etc., and the inside loops of 'D', 'Q', etc.).

    These have no special points, so `skeleton_to_graph` produces no nodes
    to walk from. Instead we identify each connected component that is a
    closed loop (no degree-1 pixels) and walk it as one stroke.

    `covered` is the set of (y, x) pixels already carried by the traced
    graph's edges. Components that overlap it are decomposed by the
    regular pipeline and must be skipped here — walking them again would
    trace them twice (e.g. '8': junctions but no endpoints, so an
    endpoint-only test misses it). The overlap test also rescues loops
    whose graph edge was discarded as a short self-loop during junction
    merging: their pixels are NOT covered, so they are walked here.
    Without `covered`, falls back to skipping components that contain
    any special point (endpoint or junction, mirroring
    skeleton_to_graph's node criterion).

    Convention: start at the topmost pixel and go clockwise. This matches
    how most people draw an 'o' — start at the top and go around.
    """
    structure = np.ones((3, 3), dtype=int)  # 8-connectivity
    labeled, n_components = label(skel, structure=structure)
    loop_strokes = []

    for cc_id in range(1, n_components + 1):
        cc_pixels = np.argwhere(labeled == cc_id)
        if len(cc_pixels) < 8:
            continue

        cc_set = set(map(tuple, cc_pixels))
        if covered is not None:
            if cc_set & covered:
                continue
        else:
            has_special = False
            for (y, x) in cc_set:
                nbrs = 0
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dy == 0 and dx == 0:
                            continue
                        if (y + dy, x + dx) in cc_set:
                            nbrs += 1
                if nbrs == 1 or nbrs >= 3:
                    has_special = True
                    break
            if has_special:
                continue

        # Walk the loop from an arbitrary starting pixel.
        start = tuple(cc_pixels[0])
        path = [start]
        seen = {start}
        cur = start
        prev = None
        while True:
            y, x = cur
            next_pt = None
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dy == 0 and dx == 0:
                        continue
                    cand = (y + dy, x + dx)
                    if cand in cc_set and cand != prev and cand not in seen:
                        next_pt = cand
                        break
                if next_pt:
                    break
            if next_pt is None:
                # Loop is closed if we're adjacent to where we started
                if (abs(cur[0] - start[0]) <= 1 and
                        abs(cur[1] - start[1]) <= 1 and len(path) > 5):
                    path.append(start)
                break
            path.append(next_pt)
            seen.add(next_pt)
            prev = cur
            cur = next_pt

        if len(path) >= 8:
            # Rotate so the topmost pixel is first, then go clockwise
            arr = np.array(path)
            top_idx = int(np.argmin(arr[:, 0]))
            path = path[top_idx:] + path[:top_idx]
            if len(path) > 3 and path[2][1] < path[0][1]:
                # Walking counterclockwise; reverse
                path = [path[0]] + path[:0:-1]
            loop_strokes.append(path)
    return loop_strokes
