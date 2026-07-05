"""Regression sweep for tracer changes: trace a whole charset, record
per-glyph stroke counts + geometry fingerprints, and diff two sweeps.

The tracer has no ground truth, but it has a strong invariant: an
unrelated change must not move stroke counts, and a geometry refinement
must move arc lengths only slightly. Sweeping before/after a change and
diffing catches regressions that the loose smoke-test bounds cannot —
this workflow caught a real one (bridging junction splices before
duplicate removal broke pixel-set dedup: arc lengths doubled on 81
glyphs; see design/code_concept_review.md item 9).

Typical use with a baseline worktree:

    git worktree add /tmp/baseline HEAD
    PYTHONPATH=/tmp/baseline/src python scripts/trace_sweep.py \
        tests/fixtures/caveat.ttf before.json
    python scripts/trace_sweep.py tests/fixtures/caveat.ttf after.json
    python scripts/trace_sweep.py --compare before.json after.json
    git worktree remove /tmp/baseline
"""

import argparse
import json
import sys


def sweep(font, out_path, charset, size):
    import numpy as np
    from penstroke.charset import font_charset
    from penstroke.tracer import trace_glyph_eulerian

    results = {}
    for ch in font_charset(font, charset):
        try:
            _, _, _, traced, _name, _ = trace_glyph_eulerian(
                font, ch, size=size)
        except Exception as e:
            results[ch] = {'error': str(e)}
            continue
        results[ch] = {
            'strokes': len(traced),
            'arclen': round(sum(
                float(np.hypot(np.diff(xs), np.diff(ys)).sum())
                for xs, ys, _ in traced), 1),
        }
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=0)
    print(f'{len(results)} glyphs -> {out_path}')


def compare(before_path, after_path, arclen_tol_pct):
    with open(before_path, encoding='utf-8') as f:
        before = json.load(f)
    with open(after_path, encoding='utf-8') as f:
        after = json.load(f)

    count_changes = []
    arclen_shifts = []
    for ch in before:
        b, a = before[ch], after.get(ch)
        if a is None:
            count_changes.append((ch, b, {'missing': True}))
            continue
        if b == a:
            continue
        if b.get('strokes') != a.get('strokes'):
            count_changes.append((ch, b, a))
        else:
            arclen_shifts.append((ch, b, a))

    for ch, b, a in count_changes:
        print(f'COUNT   {ch!r}: {b.get("strokes")} -> {a.get("strokes")} '
              f'| arclen {b.get("arclen")} -> {a.get("arclen")}')
    big = 0
    for ch, b, a in arclen_shifts:
        pct = abs(a['arclen'] - b['arclen']) / max(b['arclen'], 1e-6) * 100
        marker = ' <-- LARGE' if pct > arclen_tol_pct else ''
        if marker:
            big += 1
        print(f'arclen  {ch!r}: {b["arclen"]} -> {a["arclen"]} '
              f'({pct:.1f}%){marker}')

    total = len(before)
    print(f'\n{len(count_changes)} stroke-count changes, '
          f'{len(arclen_shifts)} arc-length shifts '
          f'({big} above {arclen_tol_pct}%), {total} glyphs total')
    # Exit nonzero on count changes so CI-style use can gate on it.
    return 1 if count_changes else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('inputs', nargs=2,
                    help='sweep: <font.ttf> <out.json>; '
                         'with --compare: <before.json> <after.json>')
    ap.add_argument('--compare', action='store_true',
                    help='Diff two sweep JSONs instead of tracing.')
    ap.add_argument('--charset', default='latin',
                    help='Charset preset for the sweep (default: latin).')
    ap.add_argument('--size', type=int, default=384,
                    help='Trace rasterization size (default: 384).')
    ap.add_argument('--arclen-tol', type=float, default=5.0,
                    help='Flag arc-length shifts above this percent '
                         '(default: 5).')
    args = ap.parse_args(argv)

    if args.compare:
        return compare(args.inputs[0], args.inputs[1], args.arclen_tol)
    sweep(args.inputs[0], args.inputs[1], args.charset, args.size)
    return 0


if __name__ == '__main__':
    sys.exit(main())
