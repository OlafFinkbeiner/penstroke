"""Template-guided glyph tracing — the production path.

Pipeline per character:
    1. Rasterize the target glyph at fixed canvas size.
    2. Skeletonize and compute the distance transform.
    3. Pick the best Hershey template via topology matching.
    4. For each Hershey stroke, sample N evenly-spaced waypoints along it.
    5. Map the waypoints from Hershey coordinates into the target glyph's
       bounding box.
    6. Snap each waypoint to its nearest skeleton pixel.
    7. Walk shortest paths through the pixel-level skeleton graph between
       consecutive waypoints, with previously-used pixels penalized.
    8. Smooth each path into a spline + sample per-point widths from the
       distance transform.

The key reframing from open-ended decomposition: we no longer ask "what
strokes are in this skeleton?" (which depends on tricky heuristics about
junctions and splits). We ask "given that there should be a stroke from
here to there, what skeleton path most closely matches that stroke?"
— a constrained, robust question with a shortest-path answer.
"""

import numpy as np
import networkx as nx

from penstroke.core.rasterize import rasterize_glyph
from penstroke.core.skeleton import skeletonize
from penstroke.core.graph import (
    build_skel_pixel_graph, skeleton_to_graph,
    merge_nearby_junctions, collapse_parallel_edges,
)
from penstroke.core.smoothing import smooth_and_wobble
from penstroke.core.strokes import build_strokes, trace_closed_loops, order_strokes
from penstroke.templates.selection import choose_template


def _geometric_fallback(skel, dist, seed=0):
    """Template-free stroke decomposition for characters with no Hershey template.

    Uses the pure-geometric pipeline from core/strokes.py: build skeleton
    graph, merge junctions, collapse parallel edges, decompose by tangent
    continuity. Quality is worse than the template-guided path (no prior
    on stroke count or order), but produces *something* drawable.

    Used for non-Latin scripts (Hebrew, Arabic, CJK) where no Hershey
    reference exists.
    """
    G = skeleton_to_graph(skel)
    G = merge_nearby_junctions(G)
    G = collapse_parallel_edges(G, dist_map=dist)
    strokes = build_strokes(G)
    strokes.extend(trace_closed_loops(skel))
    strokes = order_strokes(strokes)

    traced = []
    for i, s in enumerate(strokes):
        result = smooth_and_wobble(s, dist, seed=seed + i)
        if result is not None:
            traced.append(result)
    return traced


def _trace_dots(mask, seed=0):
    """Trace dot-like glyphs (period, colon parts, accents).

    These are roughly circular ink blobs whose skeleton collapses to a single
    pixel — there's no path to walk. Detect connected components and render
    each as a tiny "tap" stroke: a 4-point arc at the centroid, with width
    set to the blob's diameter so it renders as a filled disc.

    This also handles the colon ':' (two dots), ellipsis '…', etc.
    """
    from scipy.ndimage import label, center_of_mass
    structure = np.ones((3, 3), dtype=int)
    labeled, n_components = label(mask, structure=structure)
    traced = []
    for cc_id in range(1, n_components + 1):
        cc_mask = (labeled == cc_id)
        n_pixels = int(cc_mask.sum())
        if n_pixels < 30:
            continue  # too small even for a dot
        cy, cx = center_of_mass(cc_mask)
        # Diameter ≈ 2 * sqrt(area / π); approximation, good enough for a dot
        radius = (n_pixels / np.pi) ** 0.5
        # Build a 4-point arc through the centroid so smooth_and_wobble has
        # enough points to spline. The arc is tiny — render width does the work.
        r = 1.0  # tiny path radius, we just need >=4 distinct points
        xs = np.array([cx - r, cx, cx + r, cx])
        ys = np.array([cy, cy - r, cy, cy + r])
        widths = np.array([radius * 2, radius * 2, radius * 2, radius * 2])
        traced.append((xs, ys, widths))
    return traced


def trace_glyph(font_path, char, size=512, seed=1, n_waypoints=5,
                regime='print'):
    """Trace one glyph using a topology-matched Hershey template.

    Args:
        font_path: path to the target TTF file.
        char: single character to trace.
        size: em-square pixel size (controls render quality vs speed).
        seed: per-letter random seed for the wobble noise.
        n_waypoints: how many evenly-spaced waypoints to sample per stroke
            from the Hershey template. More = stricter path-shape match.
            For closed-loop strokes (the 'O' shape), we use 2× this.

    Returns:
        (mask, skel, dist, traced, template_font, meta) where:
            mask       — uint8 array of the rasterized glyph
            skel       — bool array of the centerline
            dist       — float array, distance-to-boundary
            traced     — list of (xs, ys, widths) per stroke
            template_font — name of the chosen Hershey variant ('rowmans' etc.)
                            or None if no template found
            meta       — font-metric dict from rasterize_glyph
    """
    mask, meta = rasterize_glyph(font_path, char, size=size)
    skel, dist = skeletonize(mask)

    # Some glyphs have multiple disconnected parts: 'i' has a stem + a tittle,
    # ':' is just two dots, '!' is a stem + dot. We separate them by connected
    # components on the MASK (not skeleton — dots may not survive skeletonization).
    # For each component: if it's dot-like (no usable skeleton), draw a tap;
    # otherwise add it to the "main shape" to be Hershey-traced.
    from scipy.ndimage import label
    structure = np.ones((3, 3), dtype=int)
    labeled, n_components = label(mask, structure=structure)

    dot_traces = []
    main_mask = np.zeros_like(mask)
    if n_components > 1:
        # Multi-component glyph: check each component individually.
        for cc_id in range(1, n_components + 1):
            cc_mask = (labeled == cc_id).astype(np.uint8)
            cc_skel = skel & (labeled == cc_id)
            if int(cc_skel.sum()) < 8 and int(cc_mask.sum()) > 30:
                # Tiny component → tap stroke
                dot_traces.extend(_trace_dots(cc_mask, seed=seed))
            else:
                # Real component → include in the main pass below
                main_mask |= cc_mask
        if main_mask.sum() == 0:
            # All components were dots (e.g., ':')
            return mask, skel, dist, dot_traces, 'dot', meta
        # Re-derive skel/dist from just the main components
        skel, dist = skeletonize(main_mask)
    else:
        # Single-component glyph. If it's a single dot, render as tap.
        if int(skel.sum()) < 8 and int(mask.sum()) > 30:
            return mask, skel, dist, _trace_dots(mask, seed=seed), 'dot', meta
        main_mask = mask

    tmpl_data, tmpl_font, _score = choose_template(char, skel)
    if tmpl_data is None:
        # No Hershey template — most likely a non-Latin script (Hebrew, Arabic,
        # Devanagari, CJK). Fall back to template-free geometric stroke
        # decomposition. Quality varies: works decently for simple topology
        # (vertical bars, simple curves), less well for complex letterforms.
        geom_traced = _geometric_fallback(skel, dist, seed=seed)
        # Include any dots we collected at the connected-component stage
        return mask, skel, dist, geom_traced + dot_traces, 'geometric', meta
    hershey_strokes, hershey_bbox = tmpl_data

    pixel_G = build_skel_pixel_graph(skel)
    if len(pixel_G) == 0:
        return mask, skel, dist, [], tmpl_font, meta

    # Pre-cache skeleton pixel list as an ndarray for fast nearest-pixel queries
    skel_pixels_list = list(pixel_G.nodes())
    arr = np.array(skel_pixels_list)

    def snap(pt, forbidden=None):
        """Nearest skeleton pixel to `pt`, optionally avoiding `forbidden` ones."""
        tr, tc = pt
        d = np.hypot(arr[:, 0] - tr, arr[:, 1] - tc)
        if forbidden:
            for i, p in enumerate(skel_pixels_list):
                if p in forbidden:
                    d[i] = 1e9  # effectively excluded
        return tuple(arr[int(np.argmin(d))])

    # Build the Hershey→mask coordinate transform: linear stretch from
    # Hershey's bounding box to the target glyph's ink bounding box.
    # Use main_mask (not the full mask) so that disconnected dots like
    # the tittle of 'i' don't skew the bbox for the stem.
    ys_mask, xs_mask = np.where(main_mask)
    my0, my1 = ys_mask.min(), ys_mask.max()
    mx0, mx1 = xs_mask.min(), xs_mask.max()
    mh, mw = my1 - my0, mx1 - mx0
    hx0, hy0, hx1, hy1 = hershey_bbox
    hw = max(1, hx1 - hx0)
    hh = max(1, hy1 - hy0)

    def hershey_to_mask(hx, hy):
        fx = (hx - hx0) / hw
        fy = (hy - hy0) / hh
        col = mx0 + fx * mw
        row = my0 + fy * mh
        return row, col

    # When the glyph had disconnected components that we captured separately
    # as dots (the tittle of 'i', dot of 'j', dot of '!', etc.), we need to
    # detect — and skip — any Hershey template stroke that corresponds to
    # them. Without this, the template walker coils the dot-template (e.g.
    # rowmans 'i' encodes the tittle as a 4-segment diamond) into the top of
    # the stem, producing a wound-up phantom.
    #
    # The check is based on vertical position: tittles, accents, j-dots sit
    # ABOVE or BELOW the main component, never overlapping vertically. So if
    # we map a Hershey stroke into the full-glyph coordinate system, its
    # row-range either overlaps main_mask's row-range (= stem/main feature)
    # or it doesn't (= separated dot feature). Y-overlap is more robust than
    # pixel-exact main_mask lookup because the full glyph's x-extent often
    # differs from main_mask's (a wide tittle over a narrow stem).
    if dot_traces:
        ys_full, _ = np.where(mask)
        my0_f, my1_f = ys_full.min(), ys_full.max()
        mh_f = max(1, my1_f - my0_f)
        ys_main, _ = np.where(main_mask)
        main_row_lo, main_row_hi = ys_main.min(), ys_main.max()

        def hershey_stroke_overlaps_main(spts):
            hys = spts[:, 1]
            fy_lo = (hys.min() - hy0) / hh
            fy_hi = (hys.max() - hy0) / hh
            row_lo = my0_f + fy_lo * mh_f
            row_hi = my0_f + fy_hi * mh_f
            return row_hi >= main_row_lo and row_lo <= main_row_hi
    else:
        hershey_stroke_overlaps_main = None

    # As we trace each stroke we accumulate the pixels it uses. Later strokes
    # then PENALIZE (not forbid) those pixels in their path-finding graph,
    # so they prefer to take a different route through any shared junctions.
    used_pixels: set = set()
    strokes_pixels: list[list] = []

    for stroke in hershey_strokes:
        spts = np.array(stroke)

        # Skip Hershey template strokes that correspond to a tittle/dot
        # already captured by the connected-component pass — they'd otherwise
        # coil into the top of the stem (see comment above on the i/j
        # rowmans diamond template).
        if (hershey_stroke_overlaps_main is not None
                and not hershey_stroke_overlaps_main(spts)):
            continue

        # Arc-length parameterization: cumulative distance along the polyline
        dists = np.cumsum(np.r_[0, np.hypot(np.diff(spts[:, 0]), np.diff(spts[:, 1]))])
        total = dists[-1]

        is_closed_loop = (tuple(stroke[0]) == tuple(stroke[-1]) and len(stroke) > 4)
        if is_closed_loop:
            # More waypoints to capture the full circumference; drop the last
            # sample (= the first, since closed)
            n_pts = max(n_waypoints * 2, 8)
            sample_dists = np.linspace(0, total, n_pts + 1)[:-1]
        else:
            # One waypoint per Hershey polyline vertex — trust the template.
            # Adding extra anchors along straight 2-vertex segments (like the
            # diagonals of M and the legs of N) lets intermediate waypoints
            # snap to neighbouring skeleton branches near junctions, which
            # makes the shortest-path walker zigzag back and forth and
            # revisit pixels. Densely-sampled curves (Hershey's 'e', 's',
            # etc.) already carry many vertices, so they keep their shape.
            n_pts = max(2, len(stroke))
            sample_dists = np.linspace(0, total, n_pts)

        # Interpolate Hershey-coordinate waypoints, then map and snap
        waypoints_skel = []
        for i, d in enumerate(sample_dists):
            hx = float(np.interp(d, dists, spts[:, 0]))
            hy = float(np.interp(d, dists, spts[:, 1]))
            row, col = hershey_to_mask(hx, hy)
            # Endpoints (i=0 or last): avoid already-used pixels so a fresh
            # stroke doesn't start mid-previous-stroke. Middle waypoints
            # snap freely (they're allowed to land on shared pixels).
            is_endpoint = (i == 0 or i == len(sample_dists) - 1)
            if is_endpoint:
                waypoints_skel.append(snap((row, col), forbidden=used_pixels))
            else:
                waypoints_skel.append(snap((row, col)))

        if is_closed_loop:
            waypoints_skel.append(waypoints_skel[0])  # close the loop

        # Build a per-stroke copy of the skeleton graph with used pixels
        # heavily upweighted (≈8×). The shortest-path finder will still go
        # through them if there's no alternative, but will detour if it can.
        G_temp = pixel_G.copy()
        for p in used_pixels:
            if p in G_temp:
                for nb in G_temp.neighbors(p):
                    G_temp[p][nb]['weight'] = G_temp[p][nb].get('weight', 1.0) * 8.0

        # Walk consecutive waypoint-to-waypoint shortest paths and stitch them
        full_path: list = []
        for i in range(len(waypoints_skel) - 1):
            a = waypoints_skel[i]
            b = waypoints_skel[i + 1]
            if a == b:
                continue
            try:
                seg = nx.shortest_path(G_temp, a, b, weight='weight')
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                continue
            if full_path:
                full_path.extend(seg[1:])  # skip the first pixel: it equals last of full_path
            else:
                full_path.extend(seg)

        if len(full_path) >= 4:
            strokes_pixels.append(full_path)
            used_pixels.update(full_path)

    # Stage 1 (before smoothing): extend each stroke into any adjacent
    # uncovered skeleton spurs. Converts a stem with a floating bottom
    # serif into a stem that flicks out into the serif as one continuous
    # pen motion — the natural "draw two in one" decomposition.
    from penstroke.templates.fixes import (
        extend_strokes_into_spurs, cover_missing_branches,
        merge_endpoint_adjacent_strokes, split_topmost_interior_strokes,
        deduplicate_overlapping_strokes, trim_winding_prefix,
        normalize_stroke_directions, order_strokes_main_first,
    )
    extend_strokes_into_spurs(strokes_pixels, pixel_G)

    traced = []
    for i, s in enumerate(strokes_pixels):
        result = smooth_and_wobble(s, dist, seed=seed + i)
        if result is not None:
            traced.append(result)

    # Stage 2: cover features still missing — typically things not
    # adjacent to any stroke endpoint, e.g. a free-floating crossbar.
    # Self-filtered against phantom/zigzag heuristics.
    extra = cover_missing_branches(skel, dist, traced, pixel_G, seed=seed)
    traced.extend(extra)

    # Stage 3: merge endpoint-adjacent strokes whose tangents align.
    # Applies in both regimes — joining a continuous curve that the
    # template walker emitted as two records is always right.
    merge_endpoint_adjacent_strokes(traced)

    # Stages 4 & 5 — print-regime cleanup.
    # Split fires on N-shape tour paths (topmost interior + returns
    # near start); dedup drops mutually-contained strokes. Both are
    # legitimate cleanup steps for print decompositions where there's
    # no biomechanical reason for the pipeline to produce duplicates
    # or N-tours. For script regime we instead use trim_winding_prefix
    # (stage 4b) which handles the cursive-template case where the
    # template walker walks UP a stem before walking down — that bit
    # would be a pen placement, not a drawn stroke.
    if regime == 'print':
        traced = split_topmost_interior_strokes(traced)
        traced = deduplicate_overlapping_strokes(traced)
    else:
        traced = trim_winding_prefix(traced)

    # Stage 6: enforce top-down by flipping strokes drawn bottom-up.
    traced = normalize_stroke_directions(traced)

    # Stage 7: order strokes so the main/dominant one comes first
    # (longest by path length, leftmost-x tiebreak). Makes animations
    # play in a natural drawing order — main stem, then crossbars,
    # then accessories. Applies in both regimes.
    traced = order_strokes_main_first(traced)

    # Append any dot traces (tittles, dot below 'j', etc.) collected at the
    # connected-component stage at the start of this function. Dots are
    # always drawn LAST, after the main letterform.
    traced.extend(dot_traces)
    return mask, skel, dist, traced, tmpl_font, meta
