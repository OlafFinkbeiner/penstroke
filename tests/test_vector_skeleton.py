"""Vector medial axis tests (design/tracer_math_plan.md B0).

Synthetic geometry for the parts that have a known-correct answer
(overlap resolution, winding rule); the Caveat fixture for the end-to-end
glyph path and the resolution-invariance property that's the whole point
of B0.
"""

import numpy as np

from penstroke.core.vector_skeleton import (
    resolve_overlaps, inside_mask, glyph_vector_skeleton, topology,
)
from pathlib import Path

CAVEAT = str(Path(__file__).parent / 'fixtures' / 'caveat.ttf')


def _square(cx, cy, half, ccw=True):
    # positive shoelace-area order (matches _signed_area's convention)
    pts = [(cx - half, cy - half), (cx + half, cy - half),
           (cx + half, cy + half), (cx - half, cy + half)]
    return np.array(pts if ccw else pts[::-1], dtype=float)


def test_resolve_overlaps_dissolves_seam():
    """Two overlapping same-signed squares -> one clean union, area = the
    true union area (not double-counted, not split by the seam)."""
    a = _square(0, 0, 10)
    b = _square(12, 0, 10)  # overlaps a in x in [2, 10]
    clean = resolve_overlaps([a, b])
    # Should merge into ONE exterior contour (no holes: both solid, overlap
    # doesn't create an enclosed gap for these two squares).
    assert len(clean) == 1
    # Shoelace area of the merged contour should equal the true union area:
    # two 20x20 squares (400 each) overlapping in an 8x20 strip (160).
    p = clean[0]
    x, y = p[:, 0], p[:, 1]
    area = abs(0.5 * np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))
    assert abs(area - (400 + 400 - 160)) < 1.0, area


def test_resolve_overlaps_keeps_hole():
    """A solid square with an opposite-wound hole inside it stays a hole
    after resolution (no accidental overlap-dissolving of real holes)."""
    outer = _square(0, 0, 20, ccw=True)
    hole = _square(0, 0, 5, ccw=False)
    clean = resolve_overlaps([outer, hole])
    assert len(clean) == 2  # exterior + 1 interior ring


def test_winding_rule_nonzero_not_evenodd():
    """Two overlapping same-signed (CCW) squares: nonzero winding keeps the
    overlap region filled (winding number 2, still != 0) -- even-odd would
    wrongly punch a hole there (2 mod 2 == 0). This is the exact defect
    that made even-odd wrong on ~10% of real multi-contour glyphs."""
    a = _square(0, 0, 10)
    b = _square(12, 0, 10)
    overlap_pt = np.array([[6.0, 0.0]])  # inside both squares
    assert inside_mask([a, b], overlap_pt)[0]


def test_caveat_glyph_runs_end_to_end():
    G, V, r = glyph_vector_skeleton(CAVEAT, 'o', size=384)
    assert G is not None
    assert G.number_of_nodes() > 0
    ends, jcts, cyc = topology(G)
    # Caveat is a script font -- its 'o' may have a small entry-stroke
    # tail, so don't assume a pure closed loop with zero ends. It must at
    # least have the bowl's closed structure.
    assert cyc >= 1, (ends, jcts, cyc)
    print(f"✓ vector_skeleton: Caveat 'o' -> {G.number_of_nodes()} verts, "
          f"topology {(ends, jcts, cyc)}")


def test_resolution_invariance_on_caveat():
    """The whole point of B0: same topology regardless of `size`, because
    every length is em-relative. Checked on a few Caveat glyphs across a
    4x size range."""
    sizes = (256, 384, 768, 1024)
    for ch in ('o', 'l', 'e'):
        sigs = [topology(glyph_vector_skeleton(CAVEAT, ch, size=s)[0])
                for s in sizes]
        assert len(set(sigs)) == 1, (ch, sigs)
    print("✓ vector_skeleton: resolution-invariant on Caveat o/l/e "
          f"across sizes {sizes}")


def test_empty_glyph_returns_none():
    G, V, r = glyph_vector_skeleton(CAVEAT, ' ', size=384)
    assert G is None
