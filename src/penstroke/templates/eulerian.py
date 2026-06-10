"""EPST — the junction-first graph tracer (penstroke's default).

The skeleton of a glyph IS its stroke-decomposition prior — no external
stroke font needed; the topology already encodes how many strokes there
are and where they run.

Pipeline per glyph:

  1. Rasterize + skeletonize (deterministic medial axis), pull out
     dot-like disconnected components (i/j tittles, accents) as
     tap-strokes.
  2. Build the cleaned skeleton multigraph: junction merging, parallel-
     edge collapse, scrambled-path repair, duplicate-edge removal. Each
     edge carries its pixel path, arc length, and end tangents.
  3. JUNCTION ANALYSIS (the heart): at every node, examine all incident
     edge-ends together and choose the optimal pairing — two ends pair
     when the pen can continue from one into the other without turning
     more than MAX_CONTINUATION_TURN_DEG. Ends left unpaired are stroke
     terminals. All junctions are decided globally BEFORE any walking,
     so no decision ever depends on traversal order.
  4. CHAIN BUILDING: follow the pairings mechanically. Every edge lands
     in exactly one chain; chains run terminal-to-terminal or close
     into loops. Each chain is one natural pen stroke.
  5. Orientation + ordering: closed loops start at their topmost pixel
     and run clockwise; open strokes run top-to-bottom / left-to-right;
     strokes are emitted big-first with dots/tittles last.
  6. Smoothing: each chain goes through core.smoothing.smooth_and_wobble
     for spline fit, per-point widths from the distance transform, and
     the hand-drawn wobble.

Output is a list of (xs, ys, widths) strokes — drop-in compatible with
the legacy Hershey tracer's output.
"""

import math
from dataclasses import dataclass
from typing import Tuple

import numpy as np
import networkx as nx

from penstroke.core.rasterize import rasterize_glyph
from penstroke.core.skeleton import skeletonize
from penstroke.core.graph import (
    skeleton_to_graph, merge_nearby_junctions, collapse_parallel_edges,
)
from penstroke.core.strokes import tangent_at, trace_closed_loops
from penstroke.core.smoothing import smooth_and_wobble


# ---------------------------------------------------------------------------
# Tuning constants (the only knobs the algorithm exposes).
# ---------------------------------------------------------------------------

MIN_WALK_LEN_PX = 6.0           # drop walks shorter than this (skeleton noise)
MIN_COMPONENT_LEN_PX = 4.0      # drop components shorter than this entirely

# Inter-component ordering: tiny components below this fraction of the median
# component length are dots/tittles and get pushed to the end of the order.
SMALL_FRAC = 0.30

# y-band height for ordering across multi-row glyphs (rare in single chars,
# but matters for accents that sit above the body).
Y_BAND_PX = 60


# ---------------------------------------------------------------------------
# Step 1: annotated multigraph
# ---------------------------------------------------------------------------

def _path_arc_length(path):
    arr = np.asarray(path, dtype=float)
    if len(arr) < 2:
        return 0.0
    diffs = np.diff(arr, axis=0)
    return float(np.hypot(diffs[:, 0], diffs[:, 1]).sum())


def _reorder_scrambled_path(path, u):
    """Repair a pixel path whose ordering is scrambled.

    A proper 8-connected pixel chain has each consecutive step ≤ √2, so
    arc_length ≈ (n-1) × ~1.2. A scrambled path (same pixels, wrong
    order) has jumps, inflating arc length. We detect via arc_length /
    (n-1) > 1.6 and repair with a nearest-neighbour walk from the
    endpoint nearest `u`.

    Nearest-neighbour reordering is exact for true chains and a decent
    approximation otherwise — and since the pixels DO form a chain (they
    came from a skeleton), it reconstructs the correct order.
    """
    n = len(path)
    if n < 3:
        return list(path)
    arc = _path_arc_length(path)
    if arc / max(n - 1, 1) <= 1.6:
        return list(path)   # ordering is fine
    pts = [tuple(p) for p in path]
    # Start from whichever endpoint pixel is closest to u.
    start = min(pts, key=lambda p: (p[0] - u[0]) ** 2 + (p[1] - u[1]) ** 2)
    remaining = set(pts)
    remaining.discard(start)
    ordered = [start]
    cur = start
    while remaining:
        nxt = min(remaining,
                  key=lambda p: (p[0] - cur[0]) ** 2 + (p[1] - cur[1]) ** 2)
        ordered.append(nxt)
        remaining.discard(nxt)
        cur = nxt
    return ordered


def build_annotated_graph(skel, dist_map):
    """Build the cleaned skeleton multigraph with arc-length + tangent edge
    annotations.

    Each edge d carries:
      - d['path']: ordered list of (y, x) pixel coords along the edge
      - d['length']: arc length in pixels
      - d['tan_u'], d['tan_v']: unit tangents at endpoints u and v,
        each pointing AWAY from its endpoint (into the edge)
      - d['retrace']: False initially; set True by T-join repair if this
        edge was duplicated to fix parity

    Hygiene applied before annotation (both target real defects observed
    from skeleton_to_graph + merge_nearby_junctions output):
      1. Scrambled-path repair: an edge whose pixel ordering is jumbled
         (arc length far exceeding what an 8-connected chain allows)
         is re-ordered via nearest-neighbour walk. Without this the
         resampled path cuts across white space.
      2. Duplicate-edge removal: parallel edges between the same node
         pair whose PIXEL SETS are identical are collapsed to one.
         skeleton_to_graph emits some edges twice; the duplicates
         corrupt degree parity (every node looks even) and make the
         Eulerian tracer retrace every line.
    """
    G = skeleton_to_graph(skel)
    G = merge_nearby_junctions(G, max_dist=22)

    # --- Hygiene pass 1: repair scrambled paths ----------------------
    for u, v, k, d in list(G.edges(keys=True, data=True)):
        d['path'] = _reorder_scrambled_path(d['path'], u)

    # --- Hygiene pass 2: drop identical-pixel-set duplicates ---------
    seen_sets = {}
    to_drop = []
    for u, v, k, d in G.edges(keys=True, data=True):
        node_key = frozenset((tuple(u), tuple(v)))
        pix_key = frozenset(map(tuple, d['path']))
        sig = (node_key, pix_key)
        if sig in seen_sets:
            to_drop.append((u, v, k))
        else:
            seen_sets[sig] = (u, v, k)
    for (u, v, k) in to_drop:
        G.remove_edge(u, v, key=k)

    G = collapse_parallel_edges(G, dist_map)
    for u, v, k, d in list(G.edges(keys=True, data=True)):
        path = d['path']
        d['length'] = _path_arc_length(path)
        d['tan_u'] = tangent_at(path, u, k=20)
        d['tan_v'] = tangent_at(path, v, k=20)
        d['retrace'] = False
    return G


# ---------------------------------------------------------------------------
# Step 2: component classification
# ---------------------------------------------------------------------------

@dataclass
class ComponentSpec:
    subgraph: nx.MultiGraph
    odd_vertices: list
    betti: int
    topology_class: str
    bbox: Tuple[int, int, int, int]   # (ymin, xmin, ymax, xmax)
    total_length: float


def _classify(odd, betti, Gc):
    if not odd and betti == 1:
        return 'CLOSED_LOOP'
    if not odd and betti >= 2:
        return 'MULTI_LOOP'
    if len(odd) == 2 and betti == 0:
        return 'OPEN_LINE'
    if len(odd) == 2 and betti >= 1:
        return 'LINE_WITH_LOOP'
    if len(odd) >= 4 and betti == 0:
        return 'TREE'
    return 'MIXED'


def split_components(G, skel):
    """Split annotated graph into ComponentSpec list; also identify orphan
    closed loops that produced no graph nodes (pure cycles like 'o')."""
    components = []
    for nodes in nx.connected_components(G):
        Gc = G.subgraph(nodes).copy()
        if Gc.number_of_edges() == 0:
            continue
        odd = [n for n in Gc.nodes if Gc.degree(n) % 2 == 1]
        betti = Gc.number_of_edges() - Gc.number_of_nodes() + 1
        ys = [n[0] for n in Gc.nodes]
        xs = [n[1] for n in Gc.nodes]
        total = sum(d['length'] for _, _, d in Gc.edges(data=True))
        if total < MIN_COMPONENT_LEN_PX:
            continue
        components.append(ComponentSpec(
            subgraph=Gc,
            odd_vertices=odd,
            betti=betti,
            topology_class=_classify(odd, betti, Gc),
            bbox=(min(ys), min(xs), max(ys), max(xs)),
            total_length=total,
        ))
    # Orphan loops: pure cycles that the graph builder couldn't represent
    # because they have no nodes (the 'o' has no endpoints or junctions).
    orphans = trace_closed_loops(skel)
    return components, orphans


# ---------------------------------------------------------------------------
# Junction-first decomposition
#
# Instead of walking the graph greedily (Hierholzer) and deciding at each
# junction mid-walk — where already-consumed edges force wrong turns — we
# analyse ALL junctions globally first, then mechanically follow the
# decided pairings.
#
#   1. analyze_junctions: at each node, look at every incident edge-end
#      and its outward tangent. Two ends CONTINUE into each other when
#      the motion direction is preserved (turn angle ≤ threshold).
#      Choose the optimal pairing (minimum total turn) among all valid
#      pairings; ends left unpaired are STROKE TERMINALS at that node.
#
#   2. build_chains: every edge has two ends; each end either continues
#      into its paired end or terminates. Following pairings end-to-end
#      partitions ALL edges into chains — each chain is one natural
#      stroke. No walk-order dependence, no consumed-edge corners.
#
# This single pass replaces the T-join + Hierholzer + sharp-turn-split
# machinery for decomposition purposes (those remain below for
# reference until fully retired).
# ---------------------------------------------------------------------------

MAX_CONTINUATION_TURN_DEG = 75.0   # max turn angle for two edge-ends to
                                   # count as one continuing pen motion


def _edge_ends_at(G, v):
    """All edge-ends incident to node v.

    Returns a list of dicts: {eid: (u, v, k), side: 0|1, tangent: vec}
    where side identifies which end of the stored path sits at v
    (0 = path[0], 1 = path[-1]) and tangent is the outward unit tangent
    at that end (pointing away from v into the edge). Self-loops
    contribute two distinct ends.
    """
    ends = []
    seen_eids = set()
    vt = tuple(v)
    for nb in G.neighbors(v):
        for k in G[v][nb]:
            u_, v_ = (v, nb) if v <= nb else (nb, v)
            eid = (u_, v_, k)
            if eid in seen_eids:
                continue
            seen_eids.add(eid)
            path = list(G[u_][v_][k]['path'])
            if len(path) < 2:
                continue
            # An end belongs to v for each path extremity that sits at v.
            # Normal edge: exactly one matches. Self-loop: both match.
            if tuple(path[0]) == vt:
                # Outward tangent from path start: direction path[0]→inward.
                t = np.asarray(path[min(20, len(path) - 1)], dtype=float) \
                    - np.asarray(path[0], dtype=float)
                n_ = np.linalg.norm(t)
                ends.append({'eid': eid, 'side': 0,
                             'tangent': t / n_ if n_ > 0 else t})
            if tuple(path[-1]) == vt:
                t = np.asarray(path[max(0, len(path) - 1 - 20)], dtype=float) \
                    - np.asarray(path[-1], dtype=float)
                n_ = np.linalg.norm(t)
                ends.append({'eid': eid, 'side': 1,
                             'tangent': t / n_ if n_ > 0 else t})
    return ends


def _pair_score(t_a, t_b):
    """Turn angle if motion continues from end a into end b.

    Both tangents point OUTWARD from the node. Continuing straight
    through means leaving along -t_a's motion... concretely: motion
    arrives along -t_a and departs along t_b, so a perfect continuation
    has t_b ≈ -t_a → dot(t_a, t_b) ≈ -1. Score = angle between -t_a
    and t_b (0 = straight, pi = U-turn back into the same direction).
    """
    d = float(np.clip(-np.dot(t_a, t_b), -1.0, 1.0))
    return math.acos(d)


def analyze_junctions(G):
    """Globally decide, for every node, which edge-ends continue into
    which.

    Returns dict: (eid, side) -> (eid, side) for every paired end.
    Unpaired ends are absent from the dict — they are stroke terminals.

    At each node the pairing is chosen by exhaustive search over all
    ways to pair up the ends (degree is small — rarely above 6),
    minimising total turn angle, with pairs above
    MAX_CONTINUATION_TURN_DEG disallowed entirely. Leaving an end
    unpaired costs a fixed penalty slightly above the threshold so
    that two ends pair exactly when their turn is acceptable.
    """
    max_turn = math.radians(MAX_CONTINUATION_TURN_DEG)
    unpaired_cost = max_turn * 1.05
    pairing = {}

    for v in G.nodes:
        ends = _edge_ends_at(G, v)
        n = len(ends)
        if n < 2:
            continue

        # Precompute pair scores; None = disallowed.
        score = {}
        for i in range(n):
            for j in range(i + 1, n):
                s = _pair_score(ends[i]['tangent'], ends[j]['tangent'])
                score[(i, j)] = s if s <= max_turn else None

        # Exhaustive best pairing over index set.
        best = {'cost': None, 'pairs': []}

        def search(remaining, pairs, cost):
            if cost is not None and best['cost'] is not None \
                    and cost >= best['cost']:
                return
            if not remaining:
                if best['cost'] is None or cost < best['cost']:
                    best['cost'] = cost
                    best['pairs'] = list(pairs)
                return
            i = remaining[0]
            rest = remaining[1:]
            # Option: leave i unpaired.
            search(rest, pairs, cost + unpaired_cost)
            # Option: pair i with each j.
            for j in rest:
                s = score.get((min(i, j), max(i, j)))
                if s is None:
                    continue
                rest2 = [x for x in rest if x != j]
                pairs.append((i, j))
                search(rest2, pairs, cost + s)
                pairs.pop()

        search(list(range(n)), [], 0.0)

        for (i, j) in best['pairs']:
            a = (ends[i]['eid'], ends[i]['side'])
            b = (ends[j]['eid'], ends[j]['side'])
            pairing[a] = b
            pairing[b] = a

    return pairing


def build_chains(G, pairing):
    """Partition all edges into chains (strokes) by following pairings.

    Each chain is a list of oriented pixel paths concatenated into one
    pixel walk. Every edge appears in exactly one chain. Chains either
    run terminal-to-terminal or close into a loop.
    """
    visited = set()
    chains = []

    def oriented_path(eid, entry_side):
        """Edge pixel path oriented so traversal ENTERS at entry_side."""
        u_, v_, k = eid
        path = list(G[u_][v_][k]['path'])
        if entry_side == 1:
            path = path[::-1]
        return path

    def walk_from(eid, entry_side):
        """Follow pairings from (eid, entered at entry_side) until a
        terminal or until the chain closes. Returns pixel walk."""
        walk = []
        cur_eid, cur_entry = eid, entry_side
        while True:
            visited.add(cur_eid)
            p = oriented_path(cur_eid, cur_entry)
            if walk:
                walk.extend(p[1:])
            else:
                walk.extend(p)
            exit_side = 1 - cur_entry
            nxt = pairing.get((cur_eid, exit_side))
            if nxt is None:
                break
            nxt_eid, nxt_side = nxt
            if nxt_eid in visited:
                break  # chain closed into a loop
            cur_eid, cur_entry = nxt_eid, nxt_side
        return walk

    # 1. Chains starting at terminals (ends with no pairing).
    all_eids = []
    for u_, v_, k in G.edges(keys=True):
        eid = (u_, v_, k) if u_ <= v_ else (v_, u_, k)
        if eid not in all_eids:
            all_eids.append(eid)
    for eid in all_eids:
        if eid in visited:
            continue
        for side in (0, 1):
            if (eid, side) not in pairing:
                if eid not in visited:
                    chains.append(walk_from(eid, side))
                break

    # 2. Remaining edges are in closed pairing-cycles; walk each.
    for eid in all_eids:
        if eid not in visited:
            chains.append(walk_from(eid, 0))

    return [c for c in chains if len(c) >= 2]


# ---------------------------------------------------------------------------
# Walk utilities
# ---------------------------------------------------------------------------

def _polyline_length(pixels):
    if len(pixels) < 2:
        return 0.0
    arr = np.asarray(pixels, dtype=float)
    return float(np.hypot(np.diff(arr[:, 0]), np.diff(arr[:, 1])).sum())


def _orient_clockwise_if_closed(walk):
    """For a closed walk, ensure clockwise orientation (positive shoelace in
    image-y-down coords) and rotate so the topmost pixel is first."""
    if not walk or walk[0] != walk[-1]:
        return walk
    arr = np.asarray(walk, dtype=float)
    x = arr[:, 1]
    y = arr[:, 0]
    area2 = float(np.sum(x[:-1] * y[1:] - x[1:] * y[:-1]))
    if area2 < 0:
        walk = walk[::-1]
    top_idx = int(np.argmin([p[0] for p in walk[:-1]]))
    walk = walk[top_idx:-1] + walk[:top_idx] + [walk[top_idx]]
    return walk


# ---------------------------------------------------------------------------
# Inter-component ordering and per-walk orientation
# ---------------------------------------------------------------------------

def _orient_walk_for_writing(w):
    """Force vertical strokes top-to-bottom, horizontals left-to-right."""
    if not w or w[0] == w[-1]:
        return w
    arr = np.asarray(w)
    dy = arr[-1, 0] - arr[0, 0]
    dx = arr[-1, 1] - arr[0, 1]
    if abs(dy) > abs(dx):
        if dy < 0:
            w = w[::-1]
    else:
        if dx < 0:
            w = w[::-1]
    return w


def order_all_walks(walks_with_bbox_len):
    """Sort walks across components into a natural drawing order.

    Input: list of (walk, bbox=(ymin,xmin,ymax,xmax), total_len) tuples.
    Returns the walks in final order, each oriented for writing.
    """
    if not walks_with_bbox_len:
        return []
    median_len = float(np.median([t for (_, _, t) in walks_with_bbox_len]))

    def key(entry):
        w, bbox, tot = entry
        ymin, xmin, ymax, xmax = bbox
        is_tiny = 1 if tot < SMALL_FRAC * median_len else 0
        band = ymin // Y_BAND_PX
        warr = np.asarray(w)
        wymin, wymax = warr[:, 0].min(), warr[:, 0].max()
        wxmin, wxmax = warr[:, 1].min(), warr[:, 1].max()
        is_vertical = 1 if (wymax - wymin) > 1.2 * (wxmax - wxmin) else 0
        return (is_tiny, band, -is_vertical, wxmin, wymin)

    sorted_entries = sorted(walks_with_bbox_len, key=key)
    return [_orient_walk_for_writing(w) for (w, _, _) in sorted_entries]


# ---------------------------------------------------------------------------
# Step 9: top-level entry point
# ---------------------------------------------------------------------------

def _split_mask_dots(mask, skel, min_dot_area=30, max_dot_skel=8,
                     min_roundness=0.55):
    """Separate a glyph mask into 'main' pixels and 'dot' connected
    components (tittles, accents, punctuation dots).

    A connected component is treated as a dot only if ALL of:
      - skeleton has fewer than `max_dot_skel` pixels (degenerate)
      - mask has at least `min_dot_area` pixels (not skeleton noise)
      - the component is roughly ROUND, not a sliver (roundness ≥
        `min_roundness`). Roundness = area / (π × (max_radius)²),
        where max_radius = max distance from centroid to any pixel.
        Real tittles are near-circular (roundness ≈ 0.6-0.95). A
        narrow connector/sliver that got disconnected from the main
        component has low roundness (< 0.3) and should NOT be
        emitted as a dot — it gets pushed into main_mask instead so
        the graph builder sees it.

    Returns (main_mask, dot_taps) where dot_taps is a list of
    (xs, ys, widths) tap-strokes ready to append at the end.
    """
    from scipy.ndimage import label, center_of_mass
    structure = np.ones((3, 3), dtype=int)
    labeled, n_components = label(mask, structure=structure)
    main_mask = np.zeros_like(mask)
    dot_taps = []
    for cc_id in range(1, n_components + 1):
        cc_mask = (labeled == cc_id)
        cc_skel = skel & cc_mask
        n_skel = int(cc_skel.sum())
        n_mask = int(cc_mask.sum())
        is_dot_candidate = (n_skel < max_dot_skel and n_mask >= min_dot_area)
        if is_dot_candidate:
            # Roundness check: compare mask area against the disc that
            # would just contain it.
            ys, xs = np.where(cc_mask)
            cy, cx = float(ys.mean()), float(xs.mean())
            max_r = float(np.hypot(ys - cy, xs - cx).max())
            disc_area = math.pi * max_r * max_r
            roundness = n_mask / disc_area if disc_area > 0 else 0.0
            if roundness >= min_roundness:
                cy2, cx2 = center_of_mass(cc_mask)
                radius = (n_mask / math.pi) ** 0.5
                r = 1.0
                xs_pt = np.array([cx2 - r, cx2, cx2 + r, cx2])
                ys_pt = np.array([cy2, cy2 - r, cy2, cy2 + r])
                widths = np.array([radius * 2.0] * 4)
                dot_taps.append((xs_pt, ys_pt, widths))
                continue
        main_mask |= cc_mask.astype(mask.dtype)
    return main_mask, dot_taps


def trace_glyph_eulerian(font_path, char, size=384, seed=1):
    """Trace a glyph via the Eulerian/Chinese-postman tracer.

    Returns the same shape as trace_glyph: (mask, skel, dist, traced,
    template_font, meta) with template_font == 'eulerian'. The `traced`
    list is [(xs, ys, widths), ...] per walk, with the same smoothing
    pipeline (smooth_and_wobble) the legacy tracer uses.
    """
    mask, meta = rasterize_glyph(font_path, char, size=size)
    skel, dist = skeletonize(mask)

    # Pull out dot-like components (i/j tittles, accent marks, punctuation
    # dots) — they bypass the graph-theoretic pipeline entirely.
    main_mask, dot_taps = _split_mask_dots(mask, skel)
    if dot_taps:
        # Rebuild the skeleton on just the main component(s) so the
        # graph builder doesn't see the tittle pixels.
        skel, dist = skeletonize(main_mask)

    G = build_annotated_graph(skel, dist)
    components, orphan_loops = split_components(G, skel)

    # Junction-first decomposition: analyse ALL junctions globally
    # (pair edge-ends by tangent continuation), then mechanically follow
    # the pairings into chains. No greedy walking, no consumed-edge
    # corners, and multi-stroke letters (H, A, E) split naturally at
    # junctions where the geometry doesn't continue.
    pairing = analyze_junctions(G)

    entries = []
    for spec in components:
        Gc = spec.subgraph
        walks = build_chains(Gc, pairing)
        walks = [_orient_clockwise_if_closed(w) if (w and w[0] == w[-1])
                 else w for w in walks]
        for w in walks:
            if len(w) < 2 or _polyline_length(w) < MIN_WALK_LEN_PX:
                continue
            arr = np.asarray(w)
            bbox = (int(arr[:, 0].min()), int(arr[:, 1].min()),
                    int(arr[:, 0].max()), int(arr[:, 1].max()))
            entries.append((w, bbox, spec.total_length))

    # Orphan closed loops (pure cycles like 'o').
    for w in orphan_loops:
        if len(w) < 2:
            continue
        arr = np.asarray(w)
        bbox = (int(arr[:, 0].min()), int(arr[:, 1].min()),
                int(arr[:, 0].max()), int(arr[:, 1].max()))
        entries.append((w, bbox, _polyline_length(w)))

    ordered = order_all_walks(entries)

    # Convert each walk (pixel list) into the (xs, ys, widths) smoothed
    # form via the shared smoothing module.
    traced = []
    for i, w in enumerate(ordered):
        result = smooth_and_wobble(w, dist, seed=seed + i)
        if result is not None:
            traced.append(result)

    # Append dot taps (tittles, accents, punctuation dots) at the very
    # end — dots are always drawn last in handwriting conventions.
    traced.extend(dot_taps)

    return mask, skel, dist, traced, 'eulerian', meta
