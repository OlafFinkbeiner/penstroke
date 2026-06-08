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
from scipy.ndimage import binary_closing, gaussian_filter, distance_transform_edt


def skeletonize(mask):
    """Compute centerline + distance transform of a glyph mask.

    Returns (skel, dist). Lightly smooths the boundary before computing the
    medial axis — without this, corner aliasing creates dozens of spurious
    spur branches that look like real strokes to downstream code.
    """
    # Gaussian + threshold smooths sharp corner artifacts. sigma=1.5 is the
    # smallest value that kills the worst spurs without rounding off real
    # geometry (verified empirically on DejaVu/Caveat at 512px).
    smoothed = gaussian_filter(mask.astype(float), sigma=1.5)
    mask_clean = (smoothed > 0.5).astype(np.uint8)
    skel, dist = medial_axis(mask_clean, return_distance=True)
    skel = prune_skeleton(skel, dist)
    # Recompute distance transform from the cleaned mask (not the skeleton-
    # derived one), so it reflects true stroke half-widths everywhere.
    dist_clean = distance_transform_edt(mask_clean)
    return skel, dist_clean


def prune_skeleton(skel, dist, min_branch_len_factor=2.2):
    """Remove short skeleton spurs at corners and serifs.

    The medial axis of a glyph corner has an unfortunate property: it sprouts
    a short branch toward each acute corner. Those branches look like real
    strokes ("A has six legs not three"). We iteratively remove any branch
    shorter than `factor * local_stroke_thickness` (so the threshold scales
    with stroke weight — bold fonts allow longer spurs to be legitimately
    short relative to stroke width).
    """
    skel = skel.copy()
    H, W = skel.shape

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
                if len(path) > 80:
                    break  # safety bound: this isn't a spur if it's this long

            local_thickness = dist[ep[0], ep[1]] if dist[ep[0], ep[1]] > 0 else 3
            # Spur threshold: longer of (absolute floor) or (proportional to
            # local stroke width). The proportional rule catches the case where
            # a slab-serif on an 'I' creates a ~10px spur off a ~12px-thick
            # stem — abs threshold of 12 alone would miss it.
            threshold = max(12, local_thickness * min_branch_len_factor)
            if len(path) < threshold:
                # Erase everything except the junction pixel itself
                for p in path[:-1]:
                    skel[p[0], p[1]] = False
                removed_any = True

        if not removed_any:
            break
    return skel
