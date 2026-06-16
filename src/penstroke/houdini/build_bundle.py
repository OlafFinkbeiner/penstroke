"""Build hfont bundles — runs under hython (needs hou via the rep
builders). Driven by the penstroke::tops build_bundle stage as a
per-chunk command, so bundles fan out across cores with a live task
count instead of one in-process sweep.

The rep cache is keyed by a CONTENT fingerprint (sha1) of the source
file (the ttf for the outline rep, strokes.json for the stroke reps),
not its mtime or path — a re-clone that bumps mtimes, or discovering the
same font through a different root, never forces a rebuild. Existing
bundles are adopted (fingerprint recorded, no rebuild) on first contact.
"""

import hashlib
import json
import os


def _fingerprint(path):
    """sha1 of a file's bytes, or None if unreadable."""
    try:
        h = hashlib.sha1()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(1 << 20), b''):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _fp_file(bundle, rep):
    return os.path.join(bundle, 'reps', rep, '.src.sha1')


def _write_fp(bundle, rep, src_fp):
    if src_fp is None:
        return
    try:
        os.makedirs(os.path.dirname(_fp_file(bundle, rep)), exist_ok=True)
        with open(_fp_file(bundle, rep), 'w') as f:
            f.write(src_fp)
    except OSError:
        pass


def _rep_current(bundle, rep, geo_path, src_path, src_fp):
    """A rep is up to date if its geo exists and the source content
    matches the recorded fingerprint. First contact with no fingerprint
    yet: an existing, mtime-current geo is adopted (fingerprint written,
    no rebuild)."""
    if src_fp is None or not os.path.exists(geo_path):
        return False
    fpp = _fp_file(bundle, rep)
    if os.path.exists(fpp):
        try:
            with open(fpp) as f:
                return f.read().strip() == src_fp
        except OSError:
            return False
    if os.path.exists(src_path) and \
            os.path.getmtime(geo_path) >= os.path.getmtime(src_path):
        _write_fp(bundle, rep, src_fp)
        return True
    return False


def build_one_bundle(ttf, bundle, store, charset, category=None,
                     build_bezier=True):
    """Build or refresh one .hfont bundle. Returns a status string
    ('all cached' or 'rebuilt <reps>'). Never raises for a missing
    store — it just builds an outline-only bundle."""
    from penstroke import hfont
    from penstroke.houdini import rep_outline, rep_strokes

    have_store = bool(store) and os.path.exists(store)
    fp_ttf = _fingerprint(ttf)
    fp_store = _fingerprint(store) if have_store else None

    need_outline = True
    need_strokes = have_store
    need_bezier = have_store and bool(build_bezier)
    if os.path.exists(os.path.join(bundle, hfont.MANIFEST_NAME)):
        try:
            hf = hfont.HFont(bundle)
            reps = hf.manifest['reps']
            if 'outline' in reps and _rep_current(
                    bundle, 'outline', hf.rep_geo_path('outline'),
                    ttf, fp_ttf):
                need_outline = False
            if need_strokes and 'strokes' in reps and _rep_current(
                    bundle, 'strokes', hf.rep_geo_path('strokes'),
                    store, fp_store):
                need_strokes = False
            if need_bezier and 'strokes_bezier' in reps and _rep_current(
                    bundle, 'strokes_bezier',
                    hf.rep_geo_path('strokes_bezier'), store, fp_store):
                need_bezier = False
        except Exception:
            pass

    if need_outline:
        rep_outline.build_outline_rep(ttf, bundle, charset=charset,
                                      verbose=False, category=category)
        _write_fp(bundle, 'outline', fp_ttf)
    if need_strokes:
        rep_strokes.build_strokes_rep(store, bundle, verbose=False)
        _write_fp(bundle, 'strokes', fp_store)
    if need_bezier:
        rep_strokes.build_strokes_bezier_rep(store, bundle, verbose=False)
        _write_fp(bundle, 'strokes_bezier', fp_store)
    hfont.set_category(bundle, category)

    built = [r for r, n in (('outline', need_outline),
                            ('strokes', need_strokes),
                            ('bezier', need_bezier)) if n]
    return ('rebuilt ' + ','.join(built)) if built else 'all cached'


def build_chunk(spec_path):
    """Build every bundle in a chunk spec (a JSON list of font dicts:
    ttf, bundle, store, charset, category, build_bezier). One failure
    is reported and skipped; the rest of the chunk still builds."""
    with open(spec_path, encoding='utf-8') as f:
        specs = json.load(f)
    n_fail = 0
    for s in specs:
        name = os.path.basename(s['bundle'])
        if not (s.get('store') and os.path.exists(s['store'])):
            print('WARNING: no stroke store for', name,
                  '- building outline-only bundle')
        try:
            status = build_one_bundle(
                s['ttf'], s['bundle'], s.get('store'), s.get('charset'),
                s.get('category') or None,
                bool(s.get('build_bezier', True)))
            print('bundle ok:', name, status)
        except Exception:
            import traceback
            n_fail += 1
            print('BUNDLE FAILED:', name)
            traceback.print_exc()
    print('chunk done: %d bundles, %d failed' % (len(specs), n_fail))
    return n_fail
