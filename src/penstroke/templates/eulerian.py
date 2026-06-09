"""Eulerian Pen-Stroke Tracer (EPST).

Graph-theoretic replacement for the Hershey-template-driven tracer in
templates/trace.py. The skeleton of a glyph IS its decomposition prior —
we don't need an external font to tell us how many strokes; the topology
already does.

Algorithm (designed via multi-lens workflow; see design/graph_tracer_spec.json):

  1. Build cleaned skeleton multigraph, annotate each edge with arc
     length and per-end tangent vectors.
  2. Split into connected components. Pure closed-loop components ('o',
     'O', the counter of 'D') are picked up separately via
     trace_closed_loops on the raster skeleton.
  3. Per component: minimum-weight T-join to repair parity (Chinese
     Postman). Picks min-cost pairing of odd-degree vertices, deciding
     for each pair whether to RETRACE the shortest path (duplicate
     edges) or LIFT (keep both as trail endpoints).
  4. Hierholzer with tangent-continuity tie-breaking: at each junction,
     prefer the outgoing edge whose tangent most closely continues the
     incoming tangent. This naturally keeps stems straight through
     X-junctions and Y-junctions instead of veering off into a serif.
  5. Sharp-turn split: cut the Hierholzer trail at junctions where the
     turn angle is ≥ SHARP_TURN_DEG (95° default). This is where a
     human writer would naturally lift the pen — at right-angle
     corners like the t crossbar joining the stem.
  6. Closed/multi-loop component handling: rotate to start at topmost
     vertex, force clockwise orientation.
  7. Open-component handling: T-join repair → Hierholzer → split.
  8. Inter-component ordering: large body first, dots/tittles last;
     within bands, top-to-bottom, verticals before horizontals.

Per-walk widths are sampled downstream from the distance transform via
core.smoothing.smooth_and_wobble — same as the existing tracer. EPST's
output (one (xs, ys, widths) per walk) is drop-in compatible.
"""

import math
import itertools
from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

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

SHARP_TURN_DEG = 95.0          # >= this turn at a junction triggers a pen-lift
MIN_WALK_LEN_PX = 6.0           # drop walks shorter than this (skeleton noise)
MIN_COMPONENT_LEN_PX = 4.0      # drop components shorter than this entirely

# T-join cost parameters: balance retrace vs lift when there are > 2 odd vertices.
ALPHA_RETRACE = 0.6             # cost coefficient on retrace path length
MAX_RETRACE_FRAC = 0.35         # never retrace more than this fraction of component
LIFT_COST_PX = 150.0            # base cost for a pen lift (encourages retrace
                                # when it's short, lift when it's far)

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
# Step 3: T-join parity repair
# ---------------------------------------------------------------------------

def tjoin_repair(spec):
    """Reduce odd-degree count to leave 0 or 2 trail endpoints per component.

    Returns (G_aug, keep_odd):
      G_aug: copy of spec.subgraph with some edges duplicated to balance parity
      keep_odd: subset of original odd vertices that will remain as trail
        start/end points (the "lift" pairs); empty list means the component
        is now Eulerian (closed circuit).
    """
    Gc = spec.subgraph
    odd = sorted(spec.odd_vertices)
    if len(odd) <= 2:
        return Gc.copy(), odd

    # Build complete graph K over odd vertices. Each edge weight =
    # min(retrace_cost, lift_cost). Retrace = duplicate the shortest path
    # between the two odd vertices (paying ALPHA × path length). Lift =
    # leave both as trail endpoints (paying LIFT_COST + euclid distance).
    K = nx.Graph()
    sp_cache = {}
    for a, b in itertools.combinations(odd, 2):
        try:
            d_sp = nx.shortest_path_length(Gc, a, b, weight='length')
            path = nx.shortest_path(Gc, a, b, weight='length')
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            d_sp, path = float('inf'), None
        sp_cache[(a, b)] = (d_sp, path)
        retrace_cost = (ALPHA_RETRACE * d_sp
                        if d_sp <= MAX_RETRACE_FRAC * spec.total_length
                        else float('inf'))
        lift_cost = LIFT_COST_PX + float(np.hypot(a[0] - b[0], a[1] - b[1]))
        cost = min(retrace_cost, lift_cost)
        K.add_edge(a, b, weight=cost,
                   kind=('retrace' if retrace_cost <= lift_cost else 'lift'))

    # Min-weight perfect matching (Blossom).
    matching = nx.min_weight_matching(K)

    G_aug = Gc.copy()
    keep_odd = []
    for (a, b) in matching:
        if K[a][b]['kind'] == 'retrace':
            d_sp, path = sp_cache.get((a, b), sp_cache.get((b, a), (None, None)))
            if path is None:
                keep_odd.extend([a, b])
                continue
            for u, v in zip(path[:-1], path[1:]):
                # Duplicate the shortest-existing edge between u and v.
                k0 = min(Gc[u][v], key=lambda kk: Gc[u][v][kk]['length'])
                src = Gc[u][v][k0]
                G_aug.add_edge(u, v,
                               path=list(src['path']),
                               length=src['length'],
                               tan_u=src['tan_u'],
                               tan_v=src['tan_v'],
                               retrace=True)
        else:
            keep_odd.extend([a, b])
    return G_aug, keep_odd


# ---------------------------------------------------------------------------
# Step 4: Hierholzer with tangent continuity
# ---------------------------------------------------------------------------

def _turn_angle(t_arrive, t_leave):
    """Angle in radians between the direction we arrived in (t_arrive,
    pointing INTO the node) and the direction we'd leave in (t_leave,
    pointing AWAY from the node). 0 = straight through; pi = U-turn."""
    if t_arrive is None or t_leave is None:
        return math.pi
    d = float(np.clip(np.dot(t_arrive, t_leave), -1.0, 1.0))
    return math.acos(d)


def hierholzer_continuity(G_aug, start):
    """Iterative Hierholzer that picks the locally most-straight outgoing
    edge at each step (tangent-continuity tie-breaking).

    Returns a list of (u, v, k) edge triples in the order they were
    traversed. Each triple records the exact multigraph key — downstream
    code must use these keys directly rather than guessing keys from a
    node trail, otherwise ghost / retrace duplicate edges produce wrong
    paths.
    """
    def canon(u, v, k):
        return (u, v, k) if u <= v else (v, u, k)

    used = set()
    # Stack entries: (node, t_arrive, incoming_edge_to_node_or_None)
    stack = [(start, None, None)]
    # We build the Hierholzer trail of EDGES via the standard recursive
    # algorithm. The node trail can be recovered from the edges.
    edge_trail = []
    # Track the "current sub-circuit" of edges
    sub_circuit_edges = []   # edges popped off in order

    while stack:
        v, t_arrive, in_edge = stack[-1]
        # Collect unused outgoing edges at v.
        cands = []
        for nb in G_aug.neighbors(v):
            for k in G_aug[v][nb]:
                if canon(v, nb, k) in used:
                    continue
                ed = G_aug[v][nb][k]
                if tuple(ed['path'][0]) == v:
                    t_out = ed['tan_u']
                else:
                    t_out = ed['tan_v']
                cands.append((nb, k, t_out, ed))
        if not cands:
            # Backtrack: pop this node and record its incoming edge.
            _, _, in_e = stack.pop()
            if in_e is not None:
                sub_circuit_edges.append(in_e)
            continue
        if t_arrive is None:
            cands.sort(key=lambda c: (-c[2][0], c[2][1]))
        else:
            cands.sort(key=lambda c: (
                _turn_angle(t_arrive, c[2]),
                1 if c[3].get('retrace', False) else 0,
                -c[3]['length'],
            ))
        nb, k, t_out, ed = cands[0]
        used.add(canon(v, nb, k))
        # Push the next node with the edge we used to reach it.
        stack.append((nb, t_out, (v, nb, k)))

    # sub_circuit_edges is in reverse traversal order (Hierholzer's
    # standard property). Reverse it to get forward order.
    sub_circuit_edges.reverse()
    return sub_circuit_edges


# ---------------------------------------------------------------------------
# Step 5: sharp-turn split + node-trail to pixel walks
# ---------------------------------------------------------------------------

def _polyline_length(pixels):
    if len(pixels) < 2:
        return 0.0
    arr = np.asarray(pixels, dtype=float)
    return float(np.hypot(np.diff(arr[:, 0]), np.diff(arr[:, 1])).sum())


def edges_to_walks(edge_trail, G_aug):
    """Convert an ordered list of (u, v, k) edge triples into pixel walks,
    splitting at sharp-turn junctions where a human writer would lift.

    Ghost edges (path=[a,b] zero-length straight-line connectors added
    by _handle_multi_trail) are recognised by the 'ghost' flag and
    always start a new walk — they're never themselves rendered.
    """
    if not edge_trail:
        return []

    # Hydrate each (u, v, k) into an oriented pixel polyline.
    consumed = []
    for (u, v, k) in edge_trail:
        ed = G_aug[u][v][k]
        path = list(ed['path'])
        if tuple(path[0]) != u:
            path = path[::-1]
        consumed.append({
            'u': u, 'v': v, 'k': k,
            'path': path,
            'ghost': bool(ed.get('ghost', False)),
        })

    sharp_threshold = math.radians(SHARP_TURN_DEG)
    walks = []
    current = []
    for i, e in enumerate(consumed):
        if e['ghost']:
            # Ghost edge — flush current walk, do not draw the ghost.
            if _polyline_length(current) >= MIN_WALK_LEN_PX:
                walks.append(current)
            current = []
            continue
        if not current:
            current = list(e['path'])
            continue
        prev_e = consumed[i - 1]
        # If the previous edge was a ghost we already restarted; treat as
        # a fresh continuation.
        if prev_e['ghost']:
            current = list(e['path'])
            continue
        v = prev_e['v']
        t_in_arrive = -tangent_at(prev_e['path'], v, k=20)
        t_out_leave = tangent_at(e['path'], v, k=20)
        theta = _turn_angle(t_in_arrive, t_out_leave)
        is_junction = (G_aug.degree(v) >= 3)
        if is_junction and theta >= sharp_threshold:
            if _polyline_length(current) >= MIN_WALK_LEN_PX:
                walks.append(current)
            current = list(e['path'])
        else:
            current.extend(e['path'][1:])
    if _polyline_length(current) >= MIN_WALK_LEN_PX:
        walks.append(current)
    return walks


# ---------------------------------------------------------------------------
# Step 6: closed-component handling
# ---------------------------------------------------------------------------

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


def handle_closed_component(spec):
    Gc = spec.subgraph
    if Gc.number_of_nodes() == 0:
        return []
    start = min(Gc.nodes, key=lambda n: (n[0], n[1]))
    edge_trail = hierholzer_continuity(Gc.copy(), start)
    walks = edges_to_walks(edge_trail, Gc)
    return [_orient_clockwise_if_closed(w) for w in walks]


# ---------------------------------------------------------------------------
# Step 7: open-component handling
# ---------------------------------------------------------------------------

def handle_open_component(spec):
    G_aug, keep_odd = tjoin_repair(spec)
    if len(keep_odd) == 2:
        keep_odd_sorted = sorted(keep_odd, key=lambda n: (n[0], n[1]))
        start = keep_odd_sorted[0]
        edge_trail = hierholzer_continuity(G_aug, start)
        return edges_to_walks(edge_trail, G_aug)
    elif len(keep_odd) == 0:
        start = min(G_aug.nodes, key=lambda n: (n[0], n[1]))
        edge_trail = hierholzer_continuity(G_aug, start)
        return edges_to_walks(edge_trail, G_aug)
    else:
        return _handle_multi_trail(G_aug, keep_odd)


def _handle_multi_trail(G_aug, keep_odd):
    """Multi-trail handler: > 2 odd vertices means we need multiple open
    trails. Pair the odd vertices geometrically, add zero-length GHOST
    edges between paired endpoints, then run Hierholzer once over the
    now-Eulerian graph. The ghost edges have `ghost=True` and a
    straight-line path; edges_to_walks recognises the flag and
    flushes the current walk WITHOUT rendering the ghost. Net effect:
    one Hierholzer pass produces N independent walks where N = lift
    pairs."""
    sorted_odd = sorted(keep_odd, key=lambda n: (n[0], n[1]))
    pairs = list(zip(sorted_odd[0::2], sorted_odd[1::2]))
    for (a, b) in pairs[1:]:
        G_aug.add_edge(a, b,
                       path=[a, b], length=0.0,
                       tan_u=np.zeros(2), tan_v=np.zeros(2),
                       retrace=False, ghost=True)
    start = pairs[0][0]
    edge_trail = hierholzer_continuity(G_aug, start)
    return edges_to_walks(edge_trail, G_aug)


# ---------------------------------------------------------------------------
# Step 8: inter-component ordering and per-walk orientation
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

    # Collect (walk, bbox, total_len) entries from each component.
    entries = []
    for spec in components:
        if spec.topology_class in ('CLOSED_LOOP', 'MULTI_LOOP') and not spec.odd_vertices:
            walks = handle_closed_component(spec)
        else:
            walks = handle_open_component(spec)
        for w in walks:
            if len(w) < 2:
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
