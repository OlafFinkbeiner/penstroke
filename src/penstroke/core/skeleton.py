"""Reduce a binary glyph mask to a 1-pixel-wide centerline skeleton.

This is the foundation of the whole pipeline: every downstream operation
(stroke decomposition, template matching, variable-width rendering) reads
either the skeleton or the distance-from-boundary transform.

Two outputs from `skeletonize`:
    skel  — bool array, True at centerline pixels
    dist  — float array, distance from each pixel to the nearest boundary,
            in pixels. At skeleton pixels this equals the half-stroke-width;
            we sample this later to recover the original variable thickness.

`prune_skeleton` removes short branch "spurs" that the medial-axis algorithm
emits at corners and serifs. These would otherwise be treated as real strokes.
"""

import numpy as np
from skimage.morphology import medial_axis
from scipy.ndimage import gaussian_filter, distance_transform_edt

# Boundary pre-smoothing applied before the medial axis. Deliberately an
# ABSOLUTE pixel quantity, and it must stay that way: it exists to suppress
# corner ALIASING, which is a property of the pixel grid, not of the glyph.
# Thresholds describing corner ROUNDING (rather than stroke geometry) belong
# in multiples of this, not of the stroke width — see TIP_CLEARANCE_SIGMAS
# in tracer.py.
SKELETON_SIGMA = 1.5


def skeletonize(mask):
    """Compute centerline + distance transform of a glyph mask.

    Returns (skel, dist). Lightly smooths the boundary before computing the
    medial axis — without this, corner aliasing creates dozens of spurious
    spur branches that look like real strokes to downstream code.

    Deterministic: skimage's ``medial_axis`` uses random tie-breaking
    when multiple pixels are equidistant from the boundary, which means
    two runs on the SAME mask can return DIFFERENT skeletons (and thus
    different graph topologies, and thus different stroke counts in the
    Eulerian tracer). We pass a fixed random_state so the same input
    always yields the same skeleton.
    """
    # Gaussian + threshold smooths sharp corner artifacts. SKELETON_SIGMA is
    # the smallest value that kills the worst spurs without rounding off real
    # geometry (verified empirically on DejaVu/Caveat at 512px).
    smoothed = gaussian_filter(mask.astype(float), sigma=SKELETON_SIGMA)
    mask_clean = (smoothed > 0.5).astype(np.uint8)
    try:
        skel, dist = medial_axis(mask_clean, return_distance=True,
                                 rng=0)
    except TypeError:
        try:
            skel, dist = medial_axis(mask_clean, return_distance=True,
                                     random_state=0)
        except TypeError:
            # Older skimage without rng/random_state — fall back to a
            # seeded global numpy RNG so behaviour is at least
            # reproducible within one process.
            np.random.seed(0)
            skel, dist = medial_axis(mask_clean, return_distance=True)
    skel = prune_skeleton(skel, dist)
    # Recompute distance transform from the cleaned mask (not the skeleton-
    # derived one), so it reflects true stroke half-widths everywhere.
    dist_clean = distance_transform_edt(mask_clean)
    return skel, dist_clean


def prune_skeleton(skel, dist, min_branch_len_factor=0.5):
    """Remove short skeleton spurs at corners and serifs.

    The medial axis of a glyph corner has an unfortunate property: it sprouts
    a short branch toward each acute corner. Those branches look like real
    strokes ("A has six legs not three"). We iteratively remove any branch
    shorter than `factor * parent_stroke_half_width`.

    DELIBERATELY CONSERVATIVE (0.5, not the ~1.0-2.2 a length-only rule would
    need to fully clean up a glyph on its own). This pass has ONE job: kill
    branches too short to be anything but pixel/raster noise. It must NOT
    try to decide "is this short branch a real serif or corner-rounding
    artifact" — length alone can't tell those apart (both are short), and a
    branch pruned HERE never reaches the graph, so
    tracer.py::prune_redundant_leaves' ink-coverage test (which CAN tell
    them apart, per-glyph, from the actual geometry) never gets a chance to
    rule on it. A single global factor tuned to make that call by itself
    can't simultaneously keep real short features on some glyphs and drop
    corner noise on others (measured: no factor in [0.5, 2.2] gets both
    Arvo 'x' and Arvo 'Z' right at once) — so don't ask it to. Leave the
    feature-vs-noise judgment to the test built for it.

    SCALE ANCHOR (this is the subtle part). The half-width is read at the
    branch's JUNCTION end — the parent stem's own thickness — never at its
    free tip. A spur terminates *at a boundary corner*, where the distance
    transform reports the corner rounding left by the sigma=1.5 pre-smooth:
    a property of the blur kernel, not of the glyph. Measured on Arvo 'H',
    dist-at-tip is pinned at 5-6px at 256, 384, 768 AND 1536px, while spur
    length scales normally (7 -> 11 -> 29 -> 60px). Anchoring there compared
    a scaling quantity against a constant, so the rule inverted as
    resolution rose: 16/16 spurs pruned at 256px, 0/16 at 768px. That single
    mis-anchor was the dominant cause of the tracer's resolution
    instability. The junction anchor scales correctly by construction.
    """
    skel = skel.copy()
    H, W = skel.shape
    # Scale-free walk bound: no branch can be pruned once it is longer than
    # the largest threshold the glyph can produce, so stop walking there
    # rather than at a fixed pixel count.
    max_walk = int(max(8.0, min_branch_len_factor * float(dist.max()))) + 2

    for _ in range(6):  # iterate: pruning creates new endpoints
        # Count neighbors at every skeleton pixel
        neigh = np.zeros_like(skel, dtype=np.uint8)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                neigh += np.roll(np.roll(skel, dy, 0), dx, 1).astype(np.uint8)
        neigh *= skel.astype(np.uint8)

        endpoints = list(map(tuple, np.argwhere(skel & (neigh == 1))))
        if not endpoints:
            break

        removed_any = False
        for ep in endpoints:
            if not skel[ep[0], ep[1]]:  # already pruned this round
                continue
            # Walk from the endpoint until we hit a junction (deg ≥ 3) or
            # dead-end. The walk length tells us if this is a spur worth pruning.
            path = [ep]
            prev = None
            cur = ep
            while True:
                y, x = cur
                nbrs = []
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dy == 0 and dx == 0:
                            continue
                        ny, nx_ = y + dy, x + dx
                        if 0 <= ny < H and 0 <= nx_ < W and skel[ny, nx_] and (ny, nx_) != prev:
                            nbrs.append((ny, nx_))
                if len(nbrs) != 1:
                    break  # hit a junction or another endpoint
                prev = cur
                cur = nbrs[0]
                path.append(cur)
                if len(path) > max_walk:
                    break  # too long to be a spur under any threshold

            # Parent half-width, read at the junction end of the branch
            # (path[-1]) — see the SCALE ANCHOR note in the docstring.
            jy, jx = path[-1]
            parent_half_width = float(dist[jy, jx])
            if parent_half_width <= 0:
                parent_half_width = 3.0
            threshold = parent_half_width * min_branch_len_factor
            if len(path) < threshold:
                # Erase everything except the junction pixel itself
                for p in path[:-1]:
                    skel[p[0], p[1]] = False
                removed_any = True

        if not removed_any:
            break
    return skel
