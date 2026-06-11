"""Font discovery and filtering — the front door of the TOPs pipeline.

Scans one or more roots and returns one record per font:

    {family, ttf, category, license, license_file, store, trace_dir}

Three source layouts are recognized per directory encountered:

1. Google Fonts checkout family dir (has METADATA.pb): category and
   license come from the metadata, the TTF is picked by preference
   (Regular > upright variable > first).
2. Penstroke trace output dir (has metadata.json + source/*.ttf):
   the existing stroke store is attached as `store`, so downstream
   stages can skip tracing.
3. Plain folder of TTFs: every .ttf is a candidate; family read from
   the font's name table.

Dependency-light (stdlib + fontTools): imports under hython, so the
TOPs scan node calls scan() in-process.

CLI:  python -m penstroke.fontscan <root>... [--name RE] [--category C]
          [--limit N] [--json out.json]
"""

import argparse
import glob
import json
import os
import re


_TTF_PREF_REGULAR = re.compile(r'-Regular\.ttf$', re.IGNORECASE)


def _pick_ttf(ttfs):
    """Best single TTF of a family: Regular > upright variable > first."""
    for t in ttfs:
        if _TTF_PREF_REGULAR.search(os.path.basename(t)):
            return t
    for t in ttfs:
        base = os.path.basename(t)
        if '[' in base and 'Italic' not in base:
            return t
    return ttfs[0] if ttfs else None


def _family_from_ttf(ttf):
    try:
        from fontTools.ttLib import TTFont
        tt = TTFont(ttf, fontNumber=0)
        fam = tt['name'].getBestFamilyName()
        tt.close()
        if fam:
            return fam
    except Exception:
        pass
    return os.path.splitext(os.path.basename(ttf))[0]


def _parse_metadata_pb(path):
    """name/category/license from a Google Fonts METADATA.pb (text proto)."""
    out = {}
    try:
        text = open(path, encoding='utf-8', errors='replace').read()
    except OSError:
        return out
    m = re.search(r'^name:\s*"([^"]*)"', text, re.MULTILINE)
    if m:
        out['family'] = m.group(1)
    m = re.search(r'^category:\s*"([^"]*)"', text, re.MULTILINE)
    if m:
        out['category'] = m.group(1)
    m = re.search(r'^license:\s*"([^"]*)"', text, re.MULTILINE)
    if m:
        out['license'] = m.group(1)
    return out


def _find_license_file(folder):
    for name in ('OFL.txt', 'LICENSE.txt', 'LICENSE', 'LICENSE.md',
                 'COPYING', 'UFL.txt'):
        p = os.path.join(folder, name)
        if os.path.exists(p):
            return p
    return None


def _record(family, ttf, category=None, license_id=None,
            license_file=None, store=None, trace_dir=None):
    return {
        'family': family,
        'ttf': os.path.abspath(ttf),
        'category': category,
        'license': license_id,
        'license_file': os.path.abspath(license_file)
        if license_file else None,
        'store': os.path.abspath(store) if store else None,
        'trace_dir': os.path.abspath(trace_dir) if trace_dir else None,
    }


def _scan_dir(folder):
    """Classify one directory; returns a record or None."""
    meta_pb = os.path.join(folder, 'METADATA.pb')
    trace_meta = os.path.join(folder, 'metadata.json')
    ttfs = sorted(glob.glob(os.path.join(folder, '*.ttf')))

    if os.path.exists(meta_pb):
        info = _parse_metadata_pb(meta_pb)
        ttf = _pick_ttf(ttfs)
        if ttf is None:
            return None
        return _record(info.get('family') or _family_from_ttf(ttf), ttf,
                       category=info.get('category'),
                       license_id=info.get('license'),
                       license_file=_find_license_file(folder))

    src_ttfs = sorted(glob.glob(os.path.join(folder, 'source', '*.ttf')))
    if src_ttfs:
        # Penstroke trace output dir. metadata.json is written LAST by
        # trace_font, so its presence marks a COMPLETE trace; without
        # it the store (if any) is partial and the font needs
        # (re)tracing — store stays None so downstream re-runs it.
        ttf = _pick_ttf(src_ttfs)
        complete = os.path.exists(trace_meta)
        store = os.path.join(folder, 'strokes.json')
        family = None
        if complete:
            try:
                with open(trace_meta, encoding='utf-8') as f:
                    family = json.load(f).get('font_name')
            except Exception:
                pass
        return _record(family or os.path.basename(folder), ttf,
                       license_file=_find_license_file(
                           os.path.join(folder, 'source')),
                       store=store if complete and os.path.exists(store)
                       else None,
                       trace_dir=folder)
    return None


def scan(roots, name=None, category=None, dedupe=True, limit=None):
    """Discover fonts under the given roots. Returns a list of records.

    Args:
        roots: directories to walk.
        name: case-insensitive regex matched against the family name.
        category: Google Fonts category filter (e.g. 'HANDWRITING');
            records without a category pass only if category is None.
        dedupe: keep one record per family (first found).
        limit: stop after N records (post-filter).
    """
    name_re = re.compile(name, re.IGNORECASE) if name else None
    records = []
    seen_families = set()
    for root in roots:
        root = os.path.abspath(root)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            rec = _scan_dir(dirpath)
            if rec is None:
                # Plain TTFs (only where the dir is no known layout).
                loose = [f for f in sorted(filenames)
                         if f.lower().endswith('.ttf')]
                if not loose or os.path.basename(dirpath) == 'source':
                    continue
                ttf = _pick_ttf([os.path.join(dirpath, f) for f in loose])
                rec = _record(_family_from_ttf(ttf), ttf,
                              license_file=_find_license_file(dirpath))
            else:
                dirnames[:] = []   # classified dir: don't descend further
            if name_re and not name_re.search(rec['family']):
                continue
            if category and rec['category'] != category:
                continue
            if dedupe:
                key = rec['family'].lower()
                if key in seen_families:
                    continue
                seen_families.add(key)
            records.append(rec)
            if limit and len(records) >= limit:
                return records
    return records


def main(argv=None):
    ap = argparse.ArgumentParser(
        description='Discover and filter fonts for the penstroke pipeline.')
    ap.add_argument('roots', nargs='+')
    ap.add_argument('--name', default=None,
                    help='Regex on the family name (case-insensitive).')
    ap.add_argument('--category', default=None,
                    help="Google Fonts category, e.g. HANDWRITING.")
    ap.add_argument('--no-dedupe', action='store_true')
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--json', default=None,
                    help='Write the manifest here (default: stdout).')
    args = ap.parse_args(argv)

    records = scan(args.roots, name=args.name, category=args.category,
                   dedupe=not args.no_dedupe, limit=args.limit)
    payload = json.dumps(records, indent=2, ensure_ascii=False)
    if args.json:
        with open(args.json, 'w', encoding='utf-8') as f:
            f.write(payload + '\n')
        print(f'{len(records)} fonts -> {args.json}')
    else:
        print(payload)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
