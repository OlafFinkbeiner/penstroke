"""Early geometric stroke-decomposition helpers.

Most of this module's decomposition logic predates the junction-first
tracer (penstroke.tracer) and is no longer on the production path. The
parts that ARE load-bearing for the tracer:

  - tangent_at: unit tangent at a path end, k-pixel baseline
  - trace_closed_loops: walk pure-cycle skeleton components ('o', 'O')
    that produce no graph nodes

Pipeline:
  decompose_strokes  →  for each edge incident to a junction, decide which
                        OTHER incident edge it should continue through
                        (the pairing that makes both into the same stroke).
                        Tangent continuity is the discriminator.
  build_strokes      →  walk the pairing graph to assemble pixel paths.
  trace_closed_loops →  handle glyphs whose skeleton is a closed loop with
                        no endpoints or junctions ('o', 'O', 'D' counter, etc.)
  order_strokes      →  sort the resulting strokes top-to-bottom, left-to-
                        right per the Latin writing convention.
"""

from collections import defaultdict
import numpy as np
from scipy.ndimage import label


def tangent_at(path, from_node, k=20):
    """Unit tangent vector at the end of `path` adjacent to `from_node`,
    pointing AWAY from from_node.

    Uses a `k`-pixel baseline (not just the first segment) so noisy junction
    geometry doesn't throw off the direction estimate.
    """
    pts = np.array(path)
    k = min(k, len(pts) - 1)
    if k < 1:
        return np.array([0.0, 0.0])
    if tuple(pts[0]) == from_node:
        seg = pts[:k + 1]
        v = seg[-1] - seg[0]
    else:
        seg = pts[-k - 1:]
        v = seg[0] - seg[-1]
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def decompose_strokes(G):
    """At each junction, decide which incident edges form a continuous stroke.

    For each node:
      - degree 1 (endpoint): no pairing, this is where strokes start/end
      - degree 2: pass through (smooth node, just a kink in one stroke)
      - degree ≥3 (real junction): pair edges greedily by "straightness".
        Two edges form one stroke if the angle between their outgoing
        tangents is close to 180° (they go in opposite directions out of
        the junction, meaning the stroke continues through). The cutoff
        score is 0.3 (dot product); anything more aligned than that is
        considered too sharp a turn to be one stroke.

    Returns:
        (pairing, edges_at) where pairing maps (node, edge_key) → partner
        edge_key (or None if the stroke terminates here), and edges_at
        maps each node to its list of (neighbor, edge_key, path) tuples.
    """
    edges_at = defaultdict(list)
    for u, v, k, data in G.edges(keys=True, data=True):
        edges_at[u].append((v, k, data['path']))
        edges_at[v].append((u, k, data['path']))

    pairing = {}
    for node, incident in edges_at.items():
        if len(incident) <= 1:
            for _, k, _ in incident:
                pairing[(node, k)] = None
            continue
        if len(incident) == 2:
            _, k1, _ = incident[0]
            _, k2, _ = incident[1]
            pairing[(node, k1)] = k2
            pairing[(node, k2)] = k1
            continue

        # Junction. Compute outgoing tangents and pair greedily.
        tans = []
        for (_nb, k, path) in incident:
            t = tangent_at(path, node)  # away from `node`
            tans.append((k, t))

        scores = []
        for i in range(len(tans)):
            for j in range(i + 1, len(tans)):
                # Score = -dot product. Maximally negative dot = strokes pointing
                # in opposite directions out of the junction = "straight through".
                score = -np.dot(tans[i][1], tans[j][1])
                scores.append((score, i, j))
        scores.sort(reverse=True)

        used = set()
        for s, i, j in scores:
            if i in used or j in used:
                continue
            if s < 0.3:  # too sharp; not the same stroke
                continue
            ki = tans[i][0]
            kj = tans[j][0]
            pairing[(node, ki)] = kj
            pairing[(node, kj)] = ki
            used.add(i)
            used.add(j)

        for idx, (k, _) in enumerate(tans):
            if idx not in used:
                pairing[(node, k)] = None
    return pairing, edges_at


def build_strokes(G):
    """Walk the pairing graph to build complete strokes.

    Each stroke is a continuous walk through G, jumping at junctions only
    via the pairing decisions made by `decompose_strokes`. Returns a list
    of pixel-coordinate lists.
    """
    pairing, edges_at = decompose_strokes(G)
    visited = set()
    strokes = []

    # Prefer starting walks at terminal (degree-1) nodes — these are natural
    # stroke endpoints.
    all_edges = list(G.edges(keys=True, data=True))
    deg = dict(G.degree())
    edges_sorted = sorted(
        all_edges,
        key=lambda e: -max(0, 2 - deg[e[0]]) - max(0, 2 - deg[e[1]]),
    )

    def walk(start_node, start_key, edges_at, pairing, visited):
        """Walk forward from start_node along start_key, following pairing
        decisions until we hit a non-paired endpoint."""
        out = []
        cur_node = start_node
        cur_key = start_key
        while True:
            partner_key = pairing.get((cur_node, cur_key))
            if partner_key is None:
                break
            next_edge = None
            for nb, kk, p in edges_at[cur_node]:
                if (kk == partner_key and
                        (cur_node, nb, kk) not in visited and
                        (nb, cur_node, kk) not in visited):
                    next_edge = (nb, kk, p)
                    break
            if next_edge is None:
                break
            nb, kk, p = next_edge
            visited.add((cur_node, nb, kk))
            p_oriented = list(p) if tuple(p[0]) == cur_node else list(p[::-1])
            out.append(p_oriented)
            cur_node = nb
            cur_key = kk
        return out

    for u, v, k, data in edges_sorted:
        if (u, v, k) in visited or (v, u, k) in visited:
            continue
        forward_pts = list(data['path'])
        if tuple(forward_pts[0]) != u:
            forward_pts = forward_pts[::-1]
        visited.add((u, v, k))

        # Extend forward from v
        for p_oriented in walk(v, k, edges_at, pairing, visited):
            forward_pts.extend(p_oriented[1:])

        # Extend backward from u
        backward_pts = []
        for p_oriented in walk(u, k, edges_at, pairing, visited):
            # Each step prepends (since we're walking backwards through the stroke)
            backward_pts = p_oriented[:-1] + backward_pts

        full = backward_pts + forward_pts
        if len(full) >= 4:
            strokes.append(full)
    return strokes


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


def order_strokes(strokes):
    """Sort strokes in human-Latin writing order: top-to-bottom into Y-bands,
    then left-to-right within each band, verticals before crossbars.

    Also orients each stroke so it's drawn the natural direction:
      - verticals: top → bottom
      - horizontals: left → right
    """
    def key(s):
        arr = np.array(s)
        ymin, ymax = arr[:, 0].min(), arr[:, 0].max()
        xmin, _xmax = arr[:, 1].min(), arr[:, 1].max()
        h = ymax - ymin
        w = _xmax - xmin
        is_vertical = 1 if h > w * 1.2 else 0
        # Y bands of 60px: strokes with similar vertical position sort together
        band = ymin // 60
        return (band, -is_vertical, xmin, ymin)

    oriented = []
    for s in sorted(strokes, key=key):
        arr = np.array(s)
        dy = arr[-1, 0] - arr[0, 0]
        dx = arr[-1, 1] - arr[0, 1]
        if abs(dy) > abs(dx):
            if dy < 0:  # bottom→top, flip
                s = s[::-1]
        else:
            if dx < 0:  # right→left, flip
                s = s[::-1]
        oriented.append(s)
    return oriented
