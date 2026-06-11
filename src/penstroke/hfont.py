"""hfont: the Houdini font bundle format — spec and sole implementation.

An hfont is a folder bundle of interchangeable glyph REPRESENTATIONS
over a single source TTF. The layout engine shapes against the
untouched TTF (HarfBuzz reads it live — metrics and kerning are never
extracted or duplicated); each rep stores one packed prim per glyph,
and Copy to Points instantiates whichever rep is wired in.

    <family>.hfont/
    ├── manifest.json        source of truth — consumers never glob reps/
    ├── font.ttf             original font, untouched
    ├── LICENSE.txt          travels with the font (optional but encouraged)
    ├── qa/                  non-normative QA artifacts (contact sheets)
    └── reps/<name>/glyphs.bgeo.sc

Format invariants (what makes reps interchangeable):

1. GLYPH KEYING — every rep stores one packed prim per glyph with a
   string `name` attribute holding the TTF's POST GLYPH NAME ("A",
   "eacute", "f_i"). Glyph names cover ligature/alternate glyphs that
   have no codepoint, so shaping upgrades need no format change.
2. EM SPACE — all rep geometry is baked to em units at build time:
   glyph origin at (0,0,0), baseline on y=0, 1 unit = 1 em, XY plane.
   A layout point with `pscale = font_size` places any rep correctly.
3. MANIFEST — reps are discovered through manifest.json only. Each rep
   entry declares its `kind` and the attribute contract it promises.

Anything that reads or writes a bundle (rep builders, the layout SOP,
QA) goes through this module, which keeps the format defined in
exactly one place. Dependency-light on purpose: stdlib + fontTools,
so it imports identically in the project venv and Houdini's hython.
"""

import json
import os
import shutil

HFONT_VERSION = 1
MANIFEST_NAME = 'manifest.json'
FONT_NAME = 'font.ttf'
LICENSE_NAME = 'LICENSE.txt'
GLYPH_KEYS = 'post-names'

# License files commonly shipped next to a TTF (google/fonts layout
# first). Checked in order; first hit wins.
LICENSE_CANDIDATES = ('OFL.txt', 'LICENSE.txt', 'LICENSE', 'LICENSE.md',
                      'COPYING', 'UFL.txt')


class HFontError(Exception):
    """Malformed bundle or manifest."""


# ---------------------------------------------------------------------------
# Manifest I/O
# ---------------------------------------------------------------------------

def manifest_path(bundle_dir):
    return os.path.join(bundle_dir, MANIFEST_NAME)


def load_manifest(bundle_dir):
    path = manifest_path(bundle_dir)
    if not os.path.exists(path):
        raise HFontError(f'{bundle_dir}: no {MANIFEST_NAME} — not an hfont '
                         'bundle')
    with open(path, encoding='utf-8') as f:
        m = json.load(f)
    if m.get('hfont') != HFONT_VERSION:
        raise HFontError(f'{bundle_dir}: hfont version {m.get("hfont")!r} '
                         f'not supported (expected {HFONT_VERSION})')
    return m


def save_manifest(bundle_dir, manifest):
    with open(manifest_path(bundle_dir), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write('\n')


# ---------------------------------------------------------------------------
# Bundle creation / rep registration
# ---------------------------------------------------------------------------

def _find_license(ttf_path):
    folder = os.path.dirname(os.path.abspath(ttf_path))
    for name in LICENSE_CANDIDATES:
        cand = os.path.join(folder, name)
        if os.path.exists(cand):
            return cand
    return None


def _font_info(ttf_path):
    """(family, upm) read from the font's name/head tables."""
    from fontTools.ttLib import TTFont
    tt = TTFont(ttf_path)
    family = tt['name'].getBestFamilyName() or \
        os.path.splitext(os.path.basename(ttf_path))[0]
    upm = tt['head'].unitsPerEm
    tt.close()
    return family, upm


def create_bundle(bundle_dir, ttf_path, license_path=None):
    """Create (or refresh) a bundle skeleton around a source TTF.

    Idempotent: an existing bundle keeps its registered reps; only the
    source font, license, and source manifest fields are refreshed.
    Returns the bundle path.
    """
    os.makedirs(os.path.join(bundle_dir, 'reps'), exist_ok=True)
    family, upm = _font_info(ttf_path)

    dest_font = os.path.join(bundle_dir, FONT_NAME)
    if os.path.abspath(ttf_path) != os.path.abspath(dest_font):
        shutil.copyfile(ttf_path, dest_font)

    if license_path is None:
        license_path = _find_license(ttf_path)
    if license_path:
        shutil.copyfile(license_path, os.path.join(bundle_dir, LICENSE_NAME))

    try:
        manifest = load_manifest(bundle_dir)
    except HFontError:
        manifest = {'hfont': HFONT_VERSION, 'reps': {}}
    manifest.update({
        'family': family,
        'source': {
            'file': FONT_NAME,
            'upm': upm,
            'original': os.path.basename(ttf_path),
            'license_file': LICENSE_NAME if license_path else None,
        },
        'glyph_keys': GLYPH_KEYS,
    })
    save_manifest(bundle_dir, manifest)
    return bundle_dir


def register_rep(bundle_dir, name, kind, geo_relpath, attributes=None,
                 provenance=None, make_default=False):
    """Add or replace a rep entry in the manifest.

    `geo_relpath` is bundle-relative (e.g. "reps/outline/glyphs.bgeo.sc")
    and must exist by the time this is called.
    """
    manifest = load_manifest(bundle_dir)
    if not os.path.exists(os.path.join(bundle_dir, geo_relpath)):
        raise HFontError(f'{bundle_dir}: rep geometry {geo_relpath} '
                         'does not exist')
    entry = {'kind': kind, 'geo': geo_relpath.replace(os.sep, '/')}
    if attributes:
        entry['attributes'] = attributes
    if provenance:
        entry['provenance'] = provenance
    manifest.setdefault('reps', {})[name] = entry
    if make_default or 'default_rep' not in manifest:
        manifest['default_rep'] = name
    save_manifest(bundle_dir, manifest)
    return manifest


def validate(bundle_dir):
    """Return a list of problems (empty = valid bundle)."""
    problems = []
    try:
        manifest = load_manifest(bundle_dir)
    except HFontError as e:
        return [str(e)]
    for key in ('family', 'source', 'glyph_keys', 'reps'):
        if key not in manifest:
            problems.append(f'manifest missing key {key!r}')
    src = manifest.get('source', {})
    font_file = os.path.join(bundle_dir, src.get('file', FONT_NAME))
    if not os.path.exists(font_file):
        problems.append(f'source font {src.get("file")!r} missing')
    if manifest.get('glyph_keys') != GLYPH_KEYS:
        problems.append(f'unknown glyph_keys {manifest.get("glyph_keys")!r}')
    for name, rep in manifest.get('reps', {}).items():
        for key in ('kind', 'geo'):
            if key not in rep:
                problems.append(f'rep {name!r} missing key {key!r}')
        geo = rep.get('geo')
        if geo and not os.path.exists(os.path.join(bundle_dir, geo)):
            problems.append(f'rep {name!r} geometry {geo!r} missing')
    default = manifest.get('default_rep')
    if manifest.get('reps') and default not in manifest['reps']:
        problems.append(f'default_rep {default!r} not among reps')
    return problems


# ---------------------------------------------------------------------------
# Reading bundles
# ---------------------------------------------------------------------------

class HFont:
    """Read-side handle on a bundle: manifest, font, glyph-name mapping."""

    def __init__(self, bundle_dir):
        self.bundle_dir = os.path.abspath(bundle_dir)
        self.manifest = load_manifest(bundle_dir)
        self._tt = None
        self._cmap = None

    @property
    def family(self):
        return self.manifest['family']

    @property
    def upm(self):
        return self.manifest['source']['upm']

    @property
    def font_path(self):
        return os.path.join(self.bundle_dir, self.manifest['source']['file'])

    @property
    def ttfont(self):
        """Lazily opened fontTools TTFont for the source font."""
        if self._tt is None:
            from fontTools.ttLib import TTFont
            self._tt = TTFont(self.font_path)
        return self._tt

    def rep(self, name=None):
        """Manifest entry for a rep (default rep if name is None)."""
        reps = self.manifest.get('reps', {})
        if name is None:
            name = self.manifest.get('default_rep')
        if name not in reps:
            raise HFontError(f'{self.bundle_dir}: no rep {name!r} '
                             f'(available: {", ".join(reps) or "none"})')
        return reps[name]

    def rep_geo_path(self, name=None):
        """Absolute path to a rep's geometry file."""
        return os.path.join(self.bundle_dir, self.rep(name)['geo'])

    def glyph_name(self, char):
        """Post glyph name for a character, or None if not in the cmap."""
        if self._cmap is None:
            self._cmap = self.ttfont.getBestCmap()
        return self._cmap.get(ord(char))
