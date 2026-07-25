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

import numpy as np
import networkx as nx

from penstroke.core.rasterize import rasterize_glyph
from penstroke.core.skeleton import skeletonize, SKELETON_SIGMA
from penstroke.core.graph import (
    skeleton_to_graph, merge_nearby_junctions, collapse_parallel_edges,
    fill_path_gaps,
)
from penstroke.core.strokes import trace_closed_loops
from penstroke.core.smoothing import smooth_and_wobble


# ---------------------------------------------------------------------------
# Tuning constants (the only knobs the algorithm exposes).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# SCALE. Every length below is a multiple of W, the glyph's OWN stroke width
# (W = 2 * median distance-transform value along its skeleton). Absolute
# pixel constants silently re-tune with both font weight and raster size —
# measured, W spans 18.9px to 39.3px across the 6-font set at size=384, so a
# single pixel constant means a 2x different thing per font, and a 4x
# different thing between 384px and 1536px. The multipliers here were chosen
# to reproduce the previous absolute values at the 6-font median W of 27.1px,
# so this is a reparameterization, not a retune.
# ---------------------------------------------------------------------------

MIN_WALK_LEN_W = 0.22           # drop walks shorter than this (skeleton noise)
MIN_COMPONENT_LEN_W = 0.15      # drop components shorter than this entirely
MERGE_RADIUS_W = 0.81           # junction-cluster merge radius
TANGENT_WINDOW_W = 0.74         # look-ahead for end tangents at a junction
Y_BAND_W = 2.22                 # y-band height for multi-row ordering
CAP_FLOOR_W2 = 0.0123           # area floor for the redundant-leaf prune

# The one length that is NOT in units of W: a corner branch's tip clearance
# measures blur-induced corner rounding, so it scales with SKELETON_SIGMA.
# Measured directly (see design/tracer_math_plan.md and the A0 follow-up):
# genuine corner-blur tip distance runs 5-8px on Arvo at 384px (3.3-5.3
# sigmas at SKELETON_SIGMA=1.5). The previous value (3.0 -> 4.5px) sat BELOW
# that whole range, so prune_redundant_leaves' tip-clearance gate excluded
# every real corner-artifact candidate instead of admitting them for its
# ink-coverage test — the gate was silently disabling the mechanism it
# exists to feed. 6.0 covers the measured range with margin.
TIP_CLEARANCE_SIGMAS = 6.0

# Inter-component ordering: tiny components below this fraction of the median
# component length are dots/tittles and get pushed to the end of the order.
# Dimensionless — already scale-free.
SMALL_FRAC = 0.30


def glyph_scale(skel, dist_map):
    """W — the glyph's own stroke width, the unit every length is in.

    Falls back to a small positive number for degenerate skeletons so
    callers never divide by zero.
    """
    if skel is not None and np.any(skel):
        w = float(np.median(dist_map[skel])) * 2.0
        if w > 1e-6:
            return w
    d = dist_map[dist_map > 0]
    return float(np.median(d)) * 2.0 if d.size else 1.0


# ---------------------------------------------------------------------------
# Step 1: annotated multigraph
# ---------------------------------------------------------------------------

def _path_arc_length(path):
    arr = np.asarray(path, dtype=float)
    if len(arr) < 2:
        return 0.0
    diffs = np.diff(arr, axis=0)
    return float(np.hypot(diffs[:, 0], diffs[:, 1]).sum())


_NN_WALK_EXACT_MAX = 1000   # above this many pixels a scrambled edge is
                            # reordered with a KDTree (O(n log n)) instead
                            # of the exact O(n^2) scan. The threshold sits
                            # far above any real glyph edge; only texture
                            # fonts (Rubik Beastly/...) ever make an edge
                            # of several thousand scrambled pixels, where
                            # O(n^2) costs tens of seconds.


def _nn_walk_kdtree(pts, start):
    """Nearest-neighbour walk over `pts` from `start`, via a KDTree so it
    stays O(n log n) on very long scrambled edges. Same rule as the O(n^2)
    scan — nearest unvisited pixel at each step."""
    from scipy.spatial import cKDTree
    uniq = list(dict.fromkeys(pts))
    arr = np.asarray(uniq, dtype=float)
    tree = cKDTree(arr)
    n = len(uniq)
    visited = bytearray(n)
    cur = uniq.index(start)
    visited[cur] = 1
    order = [cur]
    for _ in range(n - 1):
        k = 4
        nxt = -1
        while True:
            kk = min(k, n)
            _, ii = tree.query(arr[cur], k=kk)
            for j in np.atleast_1d(ii):
                if not visited[j]:
                    nxt = int(j)
                    break
            if nxt >= 0 or kk >= n:
                break
            k *= 2
        if nxt < 0:
            break
        visited[nxt] = 1
        order.append(nxt)
        cur = nxt
    return [uniq[i] for i in order]


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
    if len(pts) > _NN_WALK_EXACT_MAX:
        return _nn_walk_kdtree(pts, start)
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


def prune_redundant_leaves(G, dist_map, mask, max_passes=3, scale=None):
    """Remove leaf branches that the pen doesn't need — threshold-free.

    The medial axis transform reconstructs the glyph exactly as the
    union of disks centred on skeleton pixels with radius = distance
    transform. Every branch therefore "explains" some ink, and the
    discriminator between a REAL feature and centreline scaffolding is
    causal, not geometric:

      - A serif / arm / tail branch explains its own ink. Erase it and
        the reconstruction loses that whole feature (hundreds of px²).
      - A flat-cap corner branch explains almost nothing: the parent
        spine's terminal disk already covers virtually the entire cap;
        the corner branch adds only sub-pixel corner slivers (the
        boundary is gaussian-smoothed upstream, so the true corners
        are rounded off anyway).

    Test per leaf edge: compute the ink covered ONLY by this branch's
    disks and by no other skeleton pixel's disk, then compare against
    the GEOMETRIC ceiling of what cap corners can contribute.

    The floor is derived, not tuned: for a square-capped stroke of
    half-width r, the cap ink that the spine's terminal disk cannot
    cover is at most (2 − π/2)·r² ≈ 0.43·r² (rectangle area 2r·r minus
    the inscribed half-disk πr²/2). Any leaf whose exclusive ink is
    ≤ 0.5·r² is therefore attributable to cap-corner geometry. A real
    protruding feature (serif, flick, arm) carries ink BEYOND the
    parent stroke's own disk — empirically ≥ 1.5·r² — so the two
    populations are separated by the theorem, not by a tuned knob.
    r is the distance-transform value at the junction the leaf hangs
    off (the parent's local half-width).

    Pruning can expose new leaves (a junction dropping to degree 1),
    so iterate to a fixpoint, capped at `max_passes`.
    """
    W = glyph_scale(None, dist_map) if scale is None else scale
    CAP_CORNER_COEFF = 0.5          # ≥ (2 − π/2) ≈ 0.43, the square-cap
                                    # uncovered-area bound, with slack
    area_floor = CAP_FLOOR_W2 * W * W   # sub-visible at render scale; guards
                                        # hairline strokes where r² → 0
    # A corner branch terminates AT a boundary corner, so its free tip's
    # distance-transform value is bounded by the corner ROUNDING left by the
    # boundary smoothing (≈ 3σ). That is a property of the blur kernel, not
    # of stroke weight, so this one scales with SKELETON_SIGMA and NOT with
    # W — see the note on SKELETON_SIGMA in core/skeleton.py. A real
    # feature's branch instead ends with the feature's own half-width.
    tip_clearance_max = TIP_CLEARANCE_SIGMAS * SKELETON_SIGMA
    COVER_EPS = 0.5                 # pixel-quantisation slack on disk radii

    H, W = mask.shape

    def all_skel_pixels():
        pts = set()
        for _, _, d in G.edges(data=True):
            pts.update(map(tuple, d['path']))
        return pts

    for _ in range(max_passes):
        pruned_any = False
        skel_pts = all_skel_pixels()
        # Deterministic edge order.
        edges = sorted(G.edges(keys=True),
                       key=lambda e: (tuple(e[0]), tuple(e[1]), e[2]))
        for (u, v, k) in edges:
            if not G.has_edge(u, v, key=k):
                continue
            deg_u, deg_v = G.degree(u), G.degree(v)
            if deg_u != 1 and deg_v != 1:
                continue   # not a leaf branch
            path = G[u][v][k]['path']
            if len(path) < 2:
                continue
            # Branch pixels, excluding the junction-side endpoint pixel
            # (it's shared with the rest of the skeleton).
            jct = v if deg_u == 1 else u
            tip = u if deg_u == 1 else v
            # Tip-clearance gate: only branches that actually run INTO a
            # boundary corner are cap-fork candidates. Real features
            # (crossbars, slab caps, arms) end with their own half-width.
            if float(dist_map[int(tip[0]), int(tip[1])]) > tip_clearance_max:
                continue
            branch = [tuple(p) for p in path if tuple(p) != tuple(jct)]
            if not branch:
                continue
            rest = skel_pts.difference(branch)
            if not rest:
                continue   # last branch of a component — never prune
            barr = np.asarray(branch, dtype=float)
            br = np.array([dist_map[int(p[0]), int(p[1])] for p in branch])
            # Local window around the branch's disks.
            r_max = float(br.max()) + COVER_EPS
            y0 = max(0, int(barr[:, 0].min() - r_max - 1))
            y1 = min(H, int(barr[:, 0].max() + r_max + 2))
            x0 = max(0, int(barr[:, 1].min() - r_max - 1))
            x1 = min(W, int(barr[:, 1].max() + r_max + 2))
            yy, xx = np.mgrid[y0:y1, x0:x1]
            qpts = np.column_stack([yy.ravel(), xx.ravel()]).astype(float)
            ink_q = mask[y0:y1, x0:x1].ravel().astype(bool)
            # Covered by the branch's disks?
            d_branch = np.min(
                np.hypot(qpts[:, None, 0] - barr[None, :, 0],
                         qpts[:, None, 1] - barr[None, :, 1])
                - br[None, :], axis=1)
            in_branch = d_branch <= COVER_EPS
            cand = ink_q & in_branch
            if not cand.any():
                G.remove_edge(u, v, key=k)
                pruned_any = True
                continue
            # Covered by the REST of the skeleton? Only rest-pixels near
            # the window matter.
            rarr = np.asarray(sorted(rest), dtype=float)
            near = ((rarr[:, 0] >= y0 - r_max - dist_map.max()) &
                    (rarr[:, 0] <= y1 + r_max + dist_map.max()) &
                    (rarr[:, 1] >= x0 - r_max - dist_map.max()) &
                    (rarr[:, 1] <= x1 + r_max + dist_map.max()))
            rarr = rarr[near]
            if len(rarr) == 0:
                continue
            rr = np.array([dist_map[int(p[0]), int(p[1])] for p in rarr])
            qc = qpts[cand]
            d_rest = np.min(
                np.hypot(qc[:, None, 0] - rarr[None, :, 0],
                         qc[:, None, 1] - rarr[None, :, 1])
                - rr[None, :], axis=1)
            exclusive_px2 = float((d_rest > COVER_EPS).sum())
            # Floor scales with the parent's local half-width at the
            # junction — see the cap-corner bound in the docstring.
            r_j = float(dist_map[int(jct[0]), int(jct[1])])
            floor = max(area_floor, CAP_CORNER_COEFF * r_j * r_j)
            if exclusive_px2 <= floor:
                G.remove_edge(u, v, key=k)
                pruned_any = True
        # Drop nodes that lost all their edges.
        for n in [n for n in G.nodes if G.degree(n) == 0]:
            G.remove_node(n)
        if not pruned_any:
            break
    return G


PRUNE_MAX_EDGES = 100   # above this many skeleton edges, skip the
                        # redundant-leaf prune: it is O(leaves x disk
                        # window) and a few decorative fonts (Rubik
                        # Beastly/...) make a glyph with hundreds of spur
                        # leaves that cost 10s+. Ordinary latin glyphs sit
                        # well under this (tens of edges), so their prune
                        # is unchanged; texture glyphs just keep the spurs
                        # as extra short strokes, which suits them.


def build_annotated_graph(skel, dist_map, mask=None, scale=None):
    """Build the cleaned skeleton multigraph with arc-length + tangent edge
    annotations.

    Each edge d carries:
      - d['path']: ordered list of (y, x) pixel coords along the edge
      - d['length']: arc length in pixels
    (End tangents are computed where they are consumed —
    analyze_junctions/_edge_ends_at — not cached on the edge.)

    Hygiene applied before annotation (each targets a real observed
    defect class):
      1. Scrambled-path repair: an edge whose pixel ordering is jumbled
         (arc length far exceeding what an 8-connected chain allows)
         is re-ordered via nearest-neighbour walk. Without this the
         resampled path cuts across white space.
      2. Duplicate-edge removal: parallel edges between the same node
         pair whose PIXEL SETS are identical are collapsed to one.
         skeleton_to_graph emits some edges twice; the duplicates
         corrupt degree parity (every node looks even) and make the
         Eulerian tracer retrace every line.
      3. Redundant-leaf pruning (requires `mask`): leaf branches whose
         ink contribution is already covered by the rest of the
         skeleton are centerline scaffolding, not features — see
         prune_redundant_leaves.
    """
    W = glyph_scale(skel, dist_map) if scale is None else scale
    G = skeleton_to_graph(skel)
    G = merge_nearby_junctions(G, max_dist=MERGE_RADIUS_W * W)

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

    # --- Hygiene pass 2b: repair pixel-continuity ---------------------
    # Junction merging splices rep coordinates onto rewired paths as a
    # bare jump (it must — duplicates are matched by pixel-set equality
    # above, so both copies need identical splices). Now that dupes are
    # gone, bridge those jumps so paths are true pixel chains again:
    # tangent windows and resampling read them correctly.
    fill_path_gaps(G)

    # --- Hygiene pass 3: prune redundant leaf branches ---------------
    # Skipped on texture-dense skeletons (see PRUNE_MAX_EDGES): the prune
    # is O(leaves x window) and would cost 10s+ on a glyph with hundreds
    # of spur leaves. Ordinary glyphs are far below the cap, so unchanged.
    if mask is not None and G.number_of_edges() <= PRUNE_MAX_EDGES:
        prune_redundant_leaves(G, dist_map, mask, scale=W)

    G = collapse_parallel_edges(G, dist_map)
    for u, v, k, d in list(G.edges(keys=True, data=True)):
        d['length'] = _path_arc_length(d['path'])
    return G


# ---------------------------------------------------------------------------
# Step 2: component classification
# ---------------------------------------------------------------------------

@dataclass
class ComponentSpec:
    subgraph: nx.MultiGraph
    total_length: float


def split_components(G, skel, scale):
    """Split annotated graph into ComponentSpec list; also identify orphan
    closed loops that produced no graph nodes (pure cycles like 'o')."""
    components = []
    for nodes in nx.connected_components(G):
        Gc = G.subgraph(nodes).copy()
        if Gc.number_of_edges() == 0:
            continue
        total = sum(d['length'] for _, _, d in Gc.edges(data=True))
        if total < MIN_COMPONENT_LEN_W * scale:
            continue
        components.append(ComponentSpec(subgraph=Gc, total_length=total))
    # Orphan loops: skeleton components whose pixels the graph does NOT
    # carry — pure cycles with no nodes ('o'), plus loops whose edge was
    # dropped during graph hygiene. Components the graph covers are
    # decomposed above; walking them again would trace them twice
    # (the '8' has junctions but no endpoints).
    covered = set()
    for _u, _v, d in G.edges(data=True):
        covered.update(map(tuple, d['path']))
    orphans = trace_closed_loops(skel, covered=covered)
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
# This single pass replaced the T-join + Hierholzer + sharp-turn-split
# machinery (removed; see git history).
# ---------------------------------------------------------------------------

MAX_CONTINUATION_TURN_DEG = 75.0   # max turn angle for two edge-ends to
                                   # count as one continuing pen motion


def _edge_ends_at(G, v, tangent_window):
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
                t = np.asarray(path[min(tangent_window, len(path) - 1)],
                               dtype=float) \
                    - np.asarray(path[0], dtype=float)
                n_ = np.linalg.norm(t)
                ends.append({'eid': eid, 'side': 0,
                             'tangent': t / n_ if n_ > 0 else t})
            if tuple(path[-1]) == vt:
                t = np.asarray(path[max(0, len(path) - 1 - tangent_window)],
                               dtype=float) \
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


def analyze_junctions(G, scale):
    """Globally decide, for every node, which edge-ends continue into
    which.

    Returns dict: (eid, side) -> (eid, side) for every paired end.
    Unpaired ends are absent from the dict — they are stroke terminals.

    At each node the pairing minimises total turn angle, with pairs above
    MAX_CONTINUATION_TURN_DEG disallowed entirely. Leaving an end
    unpaired costs a fixed penalty slightly above the threshold so
    that two ends pair exactly when their turn is acceptable.

    Solved exactly by maximum-weight matching (Blossom). For n ends and a
    matching M, the cost is

        Σ_M s_ij + U·(n − 2|M|)  =  n·U − Σ_M (2U − s_ij)

    so minimising the cost is maximising Σ_M (2U − s_ij). Every allowed
    pair has s ≤ max_turn and U = 1.05·max_turn, hence a weight of at
    least 1.1·max_turn > 0 — all weights are positive, so plain
    max-weight matching (no maxcardinality) gives the optimum.

    This replaced a super-exponential exhaustive search plus a greedy
    fallback for high-degree junctions. The greedy branch silently gave
    up optimality on texture fonts; Blossom is exact at every degree and
    polynomial, so the fallback is gone.
    """
    max_turn = math.radians(MAX_CONTINUATION_TURN_DEG)
    unpaired_cost = max_turn * 1.05
    # Tangent look-ahead in pixels, from the glyph's own stroke width.
    tangent_window = max(3, int(round(TANGENT_WINDOW_W * scale)))
    pairing = {}

    for v in G.nodes:
        ends = _edge_ends_at(G, v, tangent_window)
        n = len(ends)
        if n < 2:
            continue

        # Build the matching graph: one node per end, one edge per
        # ALLOWED pair, weighted so that max-weight == min-turn (see
        # the reduction in the docstring).
        M = nx.Graph()
        M.add_nodes_from(range(n))
        for i in range(n):
            for j in range(i + 1, n):
                s = _pair_score(ends[i]['tangent'], ends[j]['tangent'])
                if s <= max_turn:
                    M.add_edge(i, j, weight=2.0 * unpaired_cost - s)
        chosen = nx.max_weight_matching(M)

        for (i, j) in chosen:
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


# Cost, in units of W, charged for orienting a walk against the writing
# convention. Scaled by how strongly the walk is axis-aligned, so a clearly
# vertical stem is effectively pinned top-to-bottom while a diagonal or an
# ambiguous blob is free for the optimiser to flip.
CONVENTION_PENALTY_W = 3.0

# Above this many walks in one band, Held-Karp (O(2^n n^2)) is replaced by
# a greedy nearest-next walk. Real glyphs sit far below it.
MAX_EXACT_ORDER = 10


def _walk_axis_bias(w):
    """(prefers_reverse, strength) for the writing convention.

    strength is 0 for a walk with no clear axis and 1 for a fully
    axis-aligned one, so the penalty fades out exactly where the
    convention stops meaning anything.
    """
    arr = np.asarray(w, dtype=float)
    dy = arr[-1, 0] - arr[0, 0]
    dx = arr[-1, 1] - arr[0, 1]
    if abs(dy) >= abs(dx):
        span = abs(dy) + abs(dx)
        strength = abs(dy) / span if span > 0 else 0.0
        return (dy < 0), strength     # vertical wants top-to-bottom
    span = abs(dy) + abs(dx)
    strength = abs(dx) / span if span > 0 else 0.0
    return (dx < 0), strength         # horizontal wants left-to-right


def _order_band(walks, start_xy, scale):
    """Choose order AND orientation for one band of walks.

    Minimises total pen-up travel from `start_xy`, plus a convention
    penalty per reversed walk. Exact via Held-Karp over
    (visited, last, orientation); greedy above MAX_EXACT_ORDER.

    Returns (ordered_oriented_walks, end_xy, travel).
    """
    n = len(walks)
    if n == 0:
        return [], start_xy, 0.0

    # endpoints[i][o] = (start_point, end_point, penalty) for orientation o
    ends = []
    for w in walks:
        rev_pref, strength = _walk_axis_bias(w)
        pen = CONVENTION_PENALTY_W * scale * strength
        fwd = (np.asarray(w[0], float), np.asarray(w[-1], float),
               pen if rev_pref else 0.0)
        bwd = (np.asarray(w[-1], float), np.asarray(w[0], float),
               0.0 if rev_pref else pen)
        ends.append((fwd, bwd))

    def hop(p, q):
        return float(np.hypot(p[0] - q[0], p[1] - q[1]))

    if n > MAX_EXACT_ORDER:
        # Greedy: repeatedly take the cheapest (walk, orientation).
        remaining = set(range(n))
        cur = np.asarray(start_xy, float)
        order, travel = [], 0.0
        while remaining:
            best = min(((hop(cur, ends[i][o][0]) + ends[i][o][2], i, o)
                        for i in remaining for o in (0, 1)),
                       key=lambda t: (t[0], t[1], t[2]))
            _c, i, o = best
            travel += hop(cur, ends[i][o][0])
            order.append((i, o))
            cur = ends[i][o][1]
            remaining.discard(i)
    else:
        # Held-Karp over (visited mask, last walk, last orientation).
        INF = float('inf')
        full = 1 << n
        dp = [[[INF, None] for _ in range(2)] for _ in range(n)]
        best = {}
        for i in range(n):
            for o in (0, 1):
                c = hop(start_xy, ends[i][o][0]) + ends[i][o][2]
                best[(1 << i, i, o)] = (c, None)
        for mask in range(full):
            for i in range(n):
                if not mask & (1 << i):
                    continue
                for o in (0, 1):
                    cur = best.get((mask, i, o))
                    if cur is None or cur[0] == INF:
                        continue
                    for j in range(n):
                        if mask & (1 << j):
                            continue
                        for p in (0, 1):
                            nc = (cur[0] + hop(ends[i][o][1], ends[j][p][0])
                                  + ends[j][p][2])
                            key = (mask | (1 << j), j, p)
                            if key not in best or nc < best[key][0]:
                                best[key] = (nc, (mask, i, o))
        endk = min((k for k in best if k[0] == full - 1),
                   key=lambda k: (best[k][0], k[1], k[2]))
        chain = []
        k = endk
        while k is not None:
            chain.append((k[1], k[2]))
            k = best[k][1]
        order = list(reversed(chain))
        travel = 0.0
        cur = np.asarray(start_xy, float)
        for (i, o) in order:
            travel += hop(cur, ends[i][o][0])
            cur = ends[i][o][1]

    out = [walks[i] if o == 0 else walks[i][::-1] for (i, o) in order]
    end_xy = ends[order[-1][0]][order[-1][1]][1]
    return out, end_xy, travel


def order_all_walks(walks_with_bbox_len, scale):
    """Order walks into a natural drawing order, choosing direction jointly.

    Input: list of (walk, bbox=(ymin,xmin,ymax,xmax), total_len) tuples.
    Returns the walks in final order, each oriented for writing.

    Convention CONSTRAINS, optimisation fills the remaining freedom. The
    outer structure is unchanged and non-negotiable — dots and tittles
    last, top y-band before bottom — because those encode how people
    actually write, and a pure travel minimisation would happily draw an
    'E' bottom-up. Within a band, order and per-walk direction are chosen
    together to minimise pen-up travel (plus a convention penalty that
    fades out for non-axis-aligned walks).

    Previously the two decisions were independent: `order_all_walks` was a
    5-key sort and `_orient_walk_for_writing` forced axis directions with
    no reference to where the previous stroke ended, so the pen
    teleported between strokes.
    """
    if not walks_with_bbox_len:
        return []
    median_len = float(np.median([t for (_, _, t) in walks_with_bbox_len]))
    band_h = max(1.0, Y_BAND_W * scale)

    groups = {}
    for (w, bbox, tot) in walks_with_bbox_len:
        is_tiny = 1 if tot < SMALL_FRAC * median_len else 0
        groups.setdefault((is_tiny, bbox[0] // band_h), []).append(w)

    # Pen enters the glyph at its top-left — the conventional start.
    allpts = np.concatenate([np.asarray(w, float)
                             for (w, _, _) in walks_with_bbox_len])
    pen = np.array([allpts[:, 0].min(), allpts[:, 1].min()], dtype=float)

    ordered = []
    for key in sorted(groups):
        band_walks, pen, _travel = _order_band(groups[key], pen, scale)
        ordered.extend(band_walks)
    return ordered


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

    ink = main_mask if dot_taps else mask
    W = glyph_scale(skel, dist)
    G = build_annotated_graph(skel, dist, mask=ink, scale=W)
    components, orphan_loops = split_components(G, skel, W)

    # Junction-first decomposition: analyse ALL junctions globally
    # (pair edge-ends by tangent continuation), then mechanically follow
    # the pairings into chains. No greedy walking, no consumed-edge
    # corners, and multi-stroke letters (H, A, E) split naturally at
    # junctions where the geometry doesn't continue.
    pairing = analyze_junctions(G, W)

    entries = []
    for spec in components:
        Gc = spec.subgraph
        walks = build_chains(Gc, pairing)
        walks = [_orient_clockwise_if_closed(w) if (w and w[0] == w[-1])
                 else w for w in walks]
        for w in walks:
            if len(w) < 2 or _polyline_length(w) < MIN_WALK_LEN_W * W:
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

    ordered = order_all_walks(entries, W)

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
