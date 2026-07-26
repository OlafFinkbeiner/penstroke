"""Standalone report harness for the vector medial axis (design/tracer_math_
plan.md B0). The actual implementation lives in
`penstroke.core.vector_skeleton` (graduated there from this script's
original prototype) -- this file is now just the FONTS/CHARS/SIZES probe
set and the two report functions used to reproduce the measurements cited
in the design doc.

Run it to reproduce both numbers:

    python scripts/proto_vector_medial_axis.py            # invariance table
    python scripts/proto_vector_medial_axis.py --theta    # theta sweep

See penstroke/core/vector_skeleton.py's module docstring for the winding-
rule, overlap-resolution, and known-unresolved-noise findings.
"""
import sys

from penstroke.core.vector_skeleton import (
    DEFAULT_THETA_DEG, glyph_vector_skeleton, topology,
)

FONTS = {'Arvo': 'test_fonts/Arvo.ttf',
         'Lato': 'test_fonts/Lato.ttf',
         'EBGaramond': 'test_fonts/EBGaramond.ttf',
         'Lobster': 'test_fonts/Lobster.ttf',
         'DancingScript': 'test_fonts/DancingScript.ttf'}
CHARS = 'HKXAemo8'
SIZES = [256, 384, 768, 1536]


def signature(fpath, ch, size, theta=DEFAULT_THETA_DEG):
    """Topology signature of one glyph at one raster size."""
    G, _V, _r = glyph_vector_skeleton(fpath, ch, size=size, theta_deg=theta)
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
