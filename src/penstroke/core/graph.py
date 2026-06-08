"""Build and clean up a graph representation of a glyph skeleton.

Two graph builders here:

  `skeleton_to_graph` produces a high-level graph where nodes are endpoints
  and junctions (degree 1 or ≥3 in the skeleton), and edges are the smooth
  paths between them. This is what the geometric stroke-decomposition code
  reasons over.

  `build_skel_pixel_graph` produces a pixel-level graph (every skeleton pixel
  is a node, edges connect 8-neighbors). This is what the template-guided
  pipeline uses for shortest-path queries between waypoints.

Two cleanup operations that the template pipeline depends on:

  `merge_nearby_junctions` collapses clusters of close-together junction
  nodes into one. The medial axis of a perpendicular crossing (like an X)
  produces a tight cluster of 3-5 degree-≥3 nodes within ~15px of each
  other; treating them as one clean junction makes everything downstream
  saner.

  `collapse_parallel_edges` handles the medial-axis-split-of-thick-strokes
  case. When a stroke is thick enough, its centerline splits into two
  parallel branches; this function detects and averages them into one.
  Crucially, it doesn't collapse the two sides of an *enclosed region* (like
  the bowl of an 'A' or 'B') — it tests both the path separation *and* the
  enclosed area, requiring both to be small.
"""

import math
import numpy as np
import networkx as nx


# ─────────────────────────────────────────────────────────────────────────────
# Pixel-level graph (used by the template-guided shortest-path tracer)
# ─────────────────────────────────────────────────────────────────────────────

def build_skel_pixel_graph(skel):
    """Build a graph where each skeleton pixel is a node connected to its
    8-neighbors. Edge weights are Euclidean distance (1 for cardinal,
    sqrt(2) for diagonal), so `nx.shortest_path` returns geometrically
    sensible routes.
    """
    G = nx.Graph()
    H, W = skel.shape
    pixels = list(map(tuple, np.argwhere(skel)))
    for p in pixels:
        G.add_node(p)
    pset = set(pixels)
    for (y, x) in pixels:
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                nb = (y + dy, x + dx)
                if nb in pset:
                    weight = 1.0 if abs(dy) + abs(dx) == 1 else 1.414
                    G.add_edge((y, x), nb, weight=weight)
    return G


# ─────────────────────────────────────────────────────────────────────────────
# High-level graph (used by the fallback geometric stroke decomposition)
# ─────────────────────────────────────────────────────────────────────────────

def skeleton_to_graph(skel):
    """Compress the skeleton into a graph of endpoints + junctions.

    Nodes: skeleton pixels with exactly 1 neighbor (endpoints) or ≥3 neighbors
    (junctions). Smooth degree-2 pixels are absorbed into edges.

    Edges: each carries `path`, the ordered list of all skeleton pixels along
    that edge (from one node to the next), so we can recover the exact
    geometry later.
    """
    H, W = skel.shape

    # Mark each skeleton pixel with its neighbor count
    neigh = np.zeros_like(skel, dtype=np.uint8)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            shifted = np.roll(np.roll(skel, dy, 0), dx, 1)
            neigh += shifted.astype(np.uint8)
    neigh *= skel.astype(np.uint8)

    is_special = skel & ((neigh == 1) | (neigh >= 3))
    special_pts = set(map(tuple, np.argwhere(is_special)))

    G = nx.MultiGraph()
    for p in special_pts:
        G.add_node(p)

    def neighbors(y, x):
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                ny, nx_ = y + dy, x + dx
                if 0 <= ny < H and 0 <= nx_ < W and skel[ny, nx_]:
                    yield (ny, nx_)

    # For each special point, walk outward along every adjacent direction
    # until we reach the next special point. That walk becomes an edge.
    visited_edges = set()
    for start in special_pts:
        for nb in neighbors(*start):
            path = [start, nb]
            prev, cur = start, nb
            while cur not in special_pts:
                nxts = [n for n in neighbors(*cur) if n != prev]
                if not nxts:
                    break
                prev, cur = cur, nxts[0]
                path.append(cur)
            # Identify edges by their endpoints + length + a midpoint, to
            # disambiguate parallel edges between the same pair of nodes.
            sig = (path[0], path[-1], len(path), tuple(path[len(path) // 2]))
            rev_sig = (path[-1], path[0], len(path), tuple(path[len(path) // 2]))
            if sig in visited_edges or rev_sig in visited_edges:
                continue
            visited_edges.add(sig)
            G.add_edge(path[0], path[-1], path=path)
    return G


# ─────────────────────────────────────────────────────────────────────────────
# Junction cluster merging
# ─────────────────────────────────────────────────────────────────────────────

def merge_nearby_junctions(G, max_dist=22):
    """Collapse clusters of junction nodes that are close together.

    Why this exists: the medial axis of an X-crossing creates ~3 degree-≥3
    nodes within ~10-15 pixels of each other, plus short "bridge" edges
    between them. Without merging, downstream stroke decomposition sees the
    crossing as a tangle of 5+ short edges; with merging it sees one clean
    4-way junction. The default 22px distance was tuned on 384-512px renders.

    Self-loops (an edge from a junction back to itself, length > max_dist*2)
    are preserved — these are real closed loops like the bowl of an 'a'.
    Self-loops shorter than that are inter-junction stubs and discarded.
    """
    for _ in range(5):  # iterate: merging can expose more merge opportunities
        junctions = [n for n, d in G.degree() if d >= 3]
        if not junctions:
            break

        # Union-find: cluster junctions that are either (a) within max_dist
        # in space, or (b) connected by a short edge.
        parent = {n: n for n in junctions}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i, a in enumerate(junctions):
            for b in junctions[i + 1:]:
                if math.hypot(a[0] - b[0], a[1] - b[1]) <= max_dist:
                    union(a, b)
        jset = set(junctions)
        for u, v, k, data in G.edges(keys=True, data=True):
            if u in jset and v in jset and u != v:
                if len(data['path']) <= max_dist:
                    union(u, v)

        clusters = {}
        for n in junctions:
            clusters.setdefault(find(n), []).append(n)

        if all(len(members) == 1 for members in clusters.values()):
            break  # nothing to merge

        # For each cluster, pick the member closest to the cluster centroid
        # as the representative.
        node_to_rep = {}
        for _root, members in clusters.items():
            cy = sum(m[0] for m in members) / len(members)
            cx = sum(m[1] for m in members) / len(members)
            rep = min(members, key=lambda m: (m[0] - cy) ** 2 + (m[1] - cx) ** 2)
            for m in members:
                node_to_rep[m] = rep

        # Rebuild the graph with rewired endpoints.
        H = nx.MultiGraph()
        for n in G.nodes():
            H.add_node(node_to_rep.get(n, n))

        for u, v, k, data in G.edges(keys=True, data=True):
            u2 = node_to_rep.get(u, u)
            v2 = node_to_rep.get(v, v)
            path = list(data['path'])

            if u2 == v2:
                # Self-loop after merging. Real closed loops (bowl of 'a',
                # 'p', 'q') become self-loops on the merged junction and we
                # want to keep them. Tiny stubs between merged-away junctions
                # are noise — drop them.
                if len(path) < max_dist * 2:
                    continue
                if path[0] != u2:
                    path = [u2] + path
                if path[-1] != v2:
                    path = path + [v2]
                H.add_edge(u2, v2, path=path)
                continue

            if path[0] != u2:
                path = [u2] + path
            if path[-1] != v2:
                path = path + [v2]
            H.add_edge(u2, v2, path=path)
        G = H
    return G


# ─────────────────────────────────────────────────────────────────────────────
# Parallel-edge collapse (medial-axis-split of thick strokes)
# ─────────────────────────────────────────────────────────────────────────────

def collapse_parallel_edges(G, dist_map=None):
    """Detect and collapse medial-axis splits of thick strokes.

    The problem: when a stroke is thick enough, its centerline isn't a single
    line — it splits into two near-parallel lines that share their endpoints.
    A bold 'X' diagonal might appear as two edges between the same corner
    pair. We want to average those into one centerline.

    But we MUST NOT collapse the two sides of an enclosed region — the bowl
    of an 'A' or 'B' also produces two edges between the same node pair, but
    they enclose a fat shape rather than running alongside each other.

    Discriminator (both conditions required for "this is a medial split"):
      (a) Mean point-to-point separation between the two paths is small
          compared to the local stroke thickness (≤ ~1.6× thickness).
      (b) The enclosed area between the two paths, when divided by their
          length, is small (≤ ~1× local thickness). A medial split forms a
          sliver polygon; a bowl forms a fat polygon.

    Without the area test, you'd over-collapse bowls. Without the separation
    test, you'd collapse enclosed regions that happen to have small width-
    to-length ratios. Both tests together are robust.
    """
    edge_groups = {}
    for u, v, k, data in G.edges(keys=True, data=True):
        key = (u, v) if (u[0], u[1]) < (v[0], v[1]) else (v, u)
        edge_groups.setdefault(key, []).append((u, v, k, data))

    H = nx.MultiGraph()
    H.add_nodes_from(G.nodes())

    for (a, b), edges_list in edge_groups.items():
        if len(edges_list) == 1:
            u, v, k, data = edges_list[0]
            H.add_edge(u, v, path=data['path'])
            continue

        # Normalize all parallel paths to point from a→b for comparison.
        paths_ab = []
        for u, v, k, data in edges_list:
            p = list(data['path'])
            if tuple(p[0]) != a:
                p = p[::-1]
            paths_ab.append(np.array(p, dtype=float))

        n_samples = max(int(np.mean([len(p) for p in paths_ab])), 20)
        resampled = []
        path_lens = []
        for p in paths_ab:
            if len(p) < 2:
                continue
            d = np.cumsum(np.r_[0, np.hypot(np.diff(p[:, 0]), np.diff(p[:, 1]))])
            if d[-1] == 0:
                continue
            tt = np.linspace(0, d[-1], n_samples)
            y = np.interp(tt, d, p[:, 0])
            x = np.interp(tt, d, p[:, 1])
            resampled.append(np.column_stack([y, x]))
            path_lens.append(d[-1])
        if len(resampled) < 2:
            for u, v, k, data in edges_list:
                H.add_edge(u, v, path=data['path'])
            continue

        # Greedy grouping: for each path, check it against later paths in the
        # list and merge any that pass both tests.
        used = set()
        out_paths = []
        for i in range(len(resampled)):
            if i in used:
                continue
            group = [i]
            for j in range(i + 1, len(resampled)):
                if j in used:
                    continue
                sep = np.mean(np.hypot(
                    resampled[i][:, 0] - resampled[j][:, 0],
                    resampled[i][:, 1] - resampled[j][:, 1]))
                # Shoelace area of the polygon i_forward + j_reversed.
                poly_y = np.concatenate([resampled[i][:, 0], resampled[j][::-1, 0]])
                poly_x = np.concatenate([resampled[i][:, 1], resampled[j][::-1, 1]])
                area = 0.5 * abs(np.dot(poly_x, np.roll(poly_y, -1)) -
                                 np.dot(poly_y, np.roll(poly_x, -1)))
                avg_len = 0.5 * (path_lens[i] + path_lens[j])
                effective_w = area / max(1.0, avg_len)

                if dist_map is not None:
                    yi = np.clip(np.round(resampled[i][:, 0]).astype(int),
                                 0, dist_map.shape[0] - 1)
                    xi = np.clip(np.round(resampled[i][:, 1]).astype(int),
                                 0, dist_map.shape[1] - 1)
                    local_w = float(np.mean(dist_map[yi, xi]) * 2.0)
                    sep_threshold = 1.6 * local_w
                    area_threshold = 1.0 * local_w
                else:
                    sep_threshold = 30
                    area_threshold = 18

                if sep <= sep_threshold and effective_w <= area_threshold:
                    group.append(j)
                    used.add(j)
            used.add(i)
            if len(group) == 1:
                out_paths.append(resampled[i])
            else:
                # Average the medial split into one centerline
                avg = np.mean([resampled[g] for g in group], axis=0)
                out_paths.append(avg)

        for avg in out_paths:
            avg_path = [(int(round(y)), int(round(x))) for y, x in avg]
            avg_path[0] = a
            avg_path[-1] = b
            H.add_edge(a, b, path=avg_path)
    return H
