"""Batch-trace every HANDWRITING-category font from a local Google Fonts
repository checkout.

Usage:
    python scripts/batch_handwriting.py <google_fonts_repo> <output_root>

Scans <repo>/{ofl,apache,ufl}/*/METADATA.pb for `category: "HANDWRITING"`,
picks each family's best TTF (Regular preferred, then variable-weight,
then the first .ttf), traces it with the default charset, and writes
<output_root>/index.html linking every font's preview.

Families that already have a finished output folder (metadata.json
present) are skipped, so the batch is resumable.
"""

import glob
import html
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from penstroke.pipeline import trace_font  # noqa: E402


def find_handwriting_dirs(repo):
    out = []
    for licdir in ('ofl', 'apache', 'ufl'):
        base = os.path.join(repo, licdir)
        if not os.path.isdir(base):
            continue
        for meta in sorted(glob.glob(os.path.join(base, '*', 'METADATA.pb'))):
            try:
                text = open(meta, encoding='utf-8', errors='replace').read()
            except OSError:
                continue
            if 'category: "HANDWRITING"' in text:
                out.append(os.path.dirname(meta))
    return out


def pick_ttf(family_dir):
    ttfs = sorted(glob.glob(os.path.join(family_dir, '*.ttf')))
    if not ttfs:
        return None
    for t in ttfs:
        if t.endswith('-Regular.ttf'):
            return t
    for t in ttfs:
        base = os.path.basename(t)
        if '[' in base and 'Italic' not in base:
            return t   # variable font, upright
    return ttfs[0]


def write_index(output_root, entries):
    rows = []
    for name, ok, err in entries:
        if ok:
            rows.append(f'<li><a href="{html.escape(name)}/preview.html">'
                        f'{html.escape(name)}</a></li>')
        else:
            rows.append(f'<li>{html.escape(name)} — FAILED: '
                        f'{html.escape(err or "?")}</li>')
    doc = ('<!DOCTYPE html><meta charset="utf-8">'
           '<title>penstroke — handwriting batch</title>'
           '<style>body{font-family:system-ui;margin:40px;}'
           'li{margin:3px 0;}</style>'
           f'<h1>Handwriting fonts ({sum(1 for _, ok, _ in entries if ok)}'
           f'/{len(entries)})</h1><ul>' + '\n'.join(rows) + '</ul>')
    with open(os.path.join(output_root, 'index.html'), 'w',
              encoding='utf-8') as f:
        f.write(doc)


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 1
    repo, output_root = sys.argv[1], sys.argv[2]
    os.makedirs(output_root, exist_ok=True)

    families = find_handwriting_dirs(repo)
    print(f'{len(families)} handwriting families found', flush=True)

    entries = []
    t0 = time.time()
    for i, fam_dir in enumerate(families):
        name = os.path.basename(fam_dir)
        out_dir = os.path.join(output_root, name)
        if os.path.exists(os.path.join(out_dir, 'metadata.json')):
            entries.append((name, True, None))
            continue
        ttf = pick_ttf(fam_dir)
        if ttf is None:
            entries.append((name, False, 'no ttf'))
            continue
        try:
            trace_font(ttf, out_dir, font_name=name, verbose=False)
            entries.append((name, True, None))
            status = 'ok'
        except Exception as e:
            entries.append((name, False, str(e)[:120]))
            status = f'FAILED {e}'
            traceback.print_exc(limit=2)
        elapsed = time.time() - t0
        rate = elapsed / max(1, i + 1)
        eta_h = rate * (len(families) - i - 1) / 3600
        print(f'[{i + 1}/{len(families)}] {name}: {status} '
              f'(eta {eta_h:.1f}h)', flush=True)
        # Refresh the index as we go so it's usable mid-batch.
        if (i + 1) % 5 == 0:
            write_index(output_root, entries)

    write_index(output_root, entries)
    n_ok = sum(1 for _, ok, _ in entries if ok)
    print(f'done: {n_ok}/{len(entries)} fonts traced', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
