"""PROTOTYPE (not wired into the pipeline) — medial axis straight from the
TTF Bezier outline, with no raster anywhere.

This is the evidence behind step B0 of design/tracer_math_plan.md. Voronoi
diagram of densely-sampled outline points -> interior Voronoi vertices
approximate the medial axis; spurious branches are removed by the
SEPARATION ANGLE criterion (theta-medial axis), which is scale-free.

The point of the experiment: the computation happens in em space, so it is
resolution-invariant BY CONSTRUCTION -- the property the raster pipeline
structurally cannot hold (measured: raster 33/56, this 40/40).

Run it to reproduce both numbers:

    python scripts/proto_vector_medial_axis.py            # invariance table
    python scripts/proto_vector_medial_axis.py --theta    # theta sweep

CAVEAT: every length here must be em-relative or invariance leaks away. The
first version of this script scored 31/40 purely because extract_outlines'
tol_px defaults to an absolute 0.5px; passing tol_px scaled with `size`
takes it to 40/40. Uses even-odd fill, which is wrong for fonts with
overlapping contours -- a winding-rule pass is on B0's risk list.
"""
import sys
import numpy as np
import networkx as nx
from scipy.spatial import Voronoi, cKDTree
from penstroke.core.outline import extract_outlines


def _contains(poly, pts):
    """Vectorised even-odd ray cast of `pts` against one closed polygon."""
    p = np.asarray(poly, float)
    if np.hypot(*(p[0] - p[-1])) > 1e-9:
        p = np.vstack([p, p[0]])
    x, y = pts[:, 0][:, None], pts[:, 1][:, None]
    x0, y0 = p[:-1, 0][None, :], p[:-1, 1][None, :]
    x1, y1 = p[1:, 0][None, :], p[1:, 1][None, :]
    straddle = (y0 > y) != (y1 > y)
    with np.errstate(divide='ignore', invalid='ignore'):
        xint = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
    return (straddle & (x < xint)).sum(axis=1) % 2 == 1


def resample_closed(poly, step):
    """Uniform arc-length resample of a closed polygon."""
    p = np.asarray(poly, float)
    if np.hypot(*(p[0] - p[-1])) > 1e-9:
        p = np.vstack([p, p[0]])
    d = np.r_[0, np.cumsum(np.hypot(np.diff(p[:, 0]), np.diff(p[:, 1])))]
    if d[-1] < step:
        return p[:-1]
    n = max(int(np.ceil(d[-1] / step)), 8)
    t = np.linspace(0, d[-1], n, endpoint=False)
    return np.column_stack([np.interp(t, d, p[:, 0]), np.interp(t, d, p[:, 1])])


def inside_mask(polys, pts):
    """Even-odd point-in-shape over all contours."""
    inside = np.zeros(len(pts), bool)
    for poly in polys:
        inside ^= _contains(poly, pts)
    return inside


def vector_medial_axis(polys, step, theta_deg=70.0):
    """Return (graph, vertices, radius) for the theta-pruned medial axis."""
    samples = np.vstack([resample_closed(p, step) for p in polys])
    vor = Voronoi(samples)
    V = vor.vertices
    keep = inside_mask(polys, V)

    # Radius + separation angle at each Voronoi vertex, from its governors.
    tree = cKDTree(samples)
    r, _ = tree.query(V, k=1)
    # Governors: the sample points defining each Voronoi vertex. Use the
    # ridge structure -- every ridge_point pair contributes to its vertices.
    gov = {}
    for (p, q), verts in zip(vor.ridge_points, vor.ridge_vertices):
        for v in verts:
            if v >= 0:
                gov.setdefault(v, set()).update((p, q))

    sep = np.zeros(len(V))
    for v, ps in gov.items():
        if not keep[v]:
            continue
        d = samples[sorted(ps)] - V[v]
        d /= (np.linalg.norm(d, axis=1, keepdims=True) + 1e-12)
        # widest angle between any two governor directions
        c = np.clip(d @ d.T, -1, 1)
        sep[v] = np.degrees(np.arccos(c.min()))

    good = keep & (sep >= theta_deg)

    G = nx.Graph()
    for (a, b) in vor.ridge_vertices:
        if a >= 0 and b >= 0 and good[a] and good[b]:
            G.add_edge(a, b, weight=float(np.hypot(*(V[a] - V[b]))))
    G.remove_nodes_from([n for n in list(G.nodes) if G.degree(n) == 0])
    return G, V, r


def topology(G):
    """(endpoints, junctions, cycles) -- the scale-invariant signature."""
    if G.number_of_nodes() == 0:
        return (0, 0, 0)
    ends = sum(1 for n in G if G.degree(n) == 1)
    jcts = sum(1 for n in G if G.degree(n) >= 3)
    cyc = G.number_of_edges() - G.number_of_nodes() + nx.number_connected_components(G)
    return (ends, jcts, cyc)


FONTS = {'Arvo': 'test_fonts/Arvo.ttf',
         'Lato': 'test_fonts/Lato.ttf',
         'EBGaramond': 'test_fonts/EBGaramond.ttf',
         'Lobster': 'test_fonts/Lobster.ttf',
         'DancingScript': 'test_fonts/DancingScript.ttf'}
CHARS = 'HKXAemo8'
SIZES = [256, 384, 768, 1536]

# Reference raster size the em-relative constants are expressed against.
REF_SIZE = 384.0
SAMPLE_STEP_DIV = 128.0     # outline sample spacing = size / this
DEFAULT_THETA_DEG = 70.0    # mid-plateau; [50, 90] all give the same topology


def signature(fpath, ch, size, theta=DEFAULT_THETA_DEG):
    """Topology signature of one glyph at one raster size.

    EVERY length passed in is proportional to `size`, including the Bezier
    flattening tolerance -- that is what makes the result invariant.
    """
    polys = extract_outlines(fpath, ch, size=size, pad=40,
                             tol_px=0.5 * size / REF_SIZE)
    if not polys:
        return (0, 0, 0)
    G, _V, _r = vector_medial_axis(polys, step=size / SAMPLE_STEP_DIV,
                                   theta_deg=theta)
    return topology(G)


def report_invariance():
    print('Vector medial axis (em-space) -- signature (ends, junctions, loops)')
    print('every length em-relative; expect every row STABLE\n')
    stable = total = 0
    for fname, fpath in FONTS.items():
        for ch in CHARS:
            sigs = [signature(fpath, ch, s) for s in SIZES]
            ok = len(set(map(str, sigs))) == 1
            stable += ok; total += 1
            print(f'  {fname+"/"+ch:<20} {"STABLE " if ok else "VARIES "} {sigs}')
    print(f'\nresolution-invariant: {stable} / {total}')


def report_theta_sweep():
    """theta is a ROBUSTNESS knob with a wide plateau, not a spur knob:
    [50, 90] is flat, above ~110 the axis fragments (ends rise, junctions
    collapse to 0). Serif branches are real medial-axis features and need a
    separate significance test -- see B0 in design/tracer_math_plan.md."""
    thetas = (50, 70, 90, 110, 130, 150)
    print('theta sweep at size=384 -- "<ends>e/<junctions>j"\n')
    print('  ' + 'glyph'.ljust(20) + ''.join(f'{t:>10}' for t in thetas))
    for fname, fpath in FONTS.items():
        for ch in 'HXe':
            row = []
            for th in thetas:
                e, j, _c = signature(fpath, ch, 384, theta=th)
                row.append(f'{e}e/{j}j')
            print('  ' + f'{fname}/{ch}'.ljust(20)
                  + ''.join(f'{v:>10}' for v in row))


if __name__ == '__main__':
    if '--theta' in sys.argv:
        report_theta_sweep()
    else:
        report_invariance()
