"""File-handshake conventions for the Corel edit round-trip.

The TOPs graph never blocks on a human: manual steps communicate
through files in TWO global drop folders, picked up on the next cook
(design/hfont_houdini_plan.md, "Manual steps in TOPs").

    <inbox>/*.json      the GLOBAL selections inbox (one drop folder
                        for every font, e.g. <repo>/selections/).
                        Files are named sel-<font>-<content-hash>.json
                        and routed to their trace folder via the
                        "font" field inside the JSON.

    <corel>/*.csv       the GLOBAL Corel exchange folder (e.g.
                        <repo>/corel/), used in BOTH directions:
                        `penstroke sync-edits` writes
                        sel-<font>-<hash>.csv for the CorelDRAW import
                        macro (plus a .outgoing.json sidecar recording
                        what was written); the user edits in Corel and
                        exports BACK ONTO THE SAME FILE — or any name
                        keeping the sel-<font>- prefix. A CSV counts
                        as an edited return when it has no pristine
                        sidecar (mtime check), and as merged once its
                        .imported.json marker is newer. Nothing is
                        moved or deleted; mtimes drive the state.

Per-font fallbacks (the older convention, still supported):
<trace_dir>/selections/*.json, and <trace_dir>/edits/*.csv for
arbitrarily named Corel exports that can't be routed by prefix.

Selection JSON schema:

    {"penstroke_selection": 1, "font": "<name>", "glyphs": "abQ"}

("glyphs" may also be a list of single-character strings.)

This module is deliberately dependency-light (os/json/glob only) so
Houdini's hython can import it inside TOPs generate callbacks, where
scipy/skimage (pulled in by editround's heavy siblings) don't exist.
"""

import glob
import json
import os

SELECTIONS_DIR = 'selections'
SUBSETS_DIR = 'subsets'
EDITS_DIR = 'edits'
IMPORTED_MARKER_SUFFIX = '.imported.json'
OUTGOING_MARKER_SUFFIX = '.outgoing.json'
_MTIME_EPS = 1e-6


def selections_dir(trace_dir):
    return os.path.join(trace_dir, SELECTIONS_DIR)


def subsets_dir(trace_dir):
    return os.path.join(trace_dir, SUBSETS_DIR)


def edits_dir(trace_dir):
    return os.path.join(trace_dir, EDITS_DIR)


def subset_csv_path(trace_dir, selection_path, corel_dir=None):
    """The Corel CSV a selection file maps to. Name-derived from the
    selection (so re-syncing the same selection overwrites its own
    CSV): in the global exchange folder when one is configured, else
    the per-font subsets/ fallback."""
    stem = os.path.splitext(os.path.basename(selection_path))[0]
    if corel_dir:
        return os.path.join(corel_dir, stem + '.csv')
    return os.path.join(subsets_dir(trace_dir), stem + '_edit.csv')


def imported_marker_path(edited_csv):
    return edited_csv + IMPORTED_MARKER_SUFFIX


def outgoing_marker_path(csv_path):
    return csv_path + OUTGOING_MARKER_SUFFIX


def read_selection_record(selection_path):
    """Return the full selection dict ('glyphs' normalized to a string)."""
    # utf-8-sig: tolerate the BOM that Notepad / PowerShell 5.1 write.
    with open(selection_path, encoding='utf-8-sig') as f:
        data = json.load(f)
    if 'penstroke_selection' not in data:
        raise ValueError(f'{selection_path}: not a penstroke selection file')
    if isinstance(data['glyphs'], list):
        data['glyphs'] = ''.join(data['glyphs'])
    return data


def read_selection(selection_path):
    """Return the selection's glyphs as a single string."""
    return read_selection_record(selection_path)['glyphs']


def _norm(name):
    """Font-name normalization for routing: 'Aguafina Script' ==
    'aguafinascript' == 'aguafina-script'."""
    return ''.join(c for c in name.lower() if c.isalnum())


def _trace_dir_names(trace_dir):
    """All names a trace folder answers to: its basename plus the
    font_name recorded in metadata.json (they differ for older traces)."""
    names = {_norm(os.path.basename(os.path.abspath(trace_dir)))}
    meta_path = os.path.join(trace_dir, 'metadata.json')
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding='utf-8-sig') as f:
                names.add(_norm(json.load(f)['font_name']))
        except (ValueError, KeyError):
            pass
    return names


def _filename_slug(path):
    """Font slug from a sel-<slug>-<hash>* filename, or None."""
    stem = os.path.splitext(os.path.basename(path))[0]
    parts = stem.split('-')
    if len(parts) >= 3 and parts[0] == 'sel':
        return parts[1]
    return None


def selections_for(trace_dir, inbox=None):
    """All selection files addressed to this trace folder: its local
    selections/ dir plus any inbox files whose "font" field matches."""
    paths = sorted(glob.glob(os.path.join(selections_dir(trace_dir),
                                          '*.json')))
    if inbox and os.path.isdir(inbox):
        names = _trace_dir_names(trace_dir)
        for sel in sorted(glob.glob(os.path.join(inbox, '*.json'))):
            try:
                rec = read_selection_record(sel)
            except ValueError:
                continue   # foreign JSON in the inbox — not ours
            if _norm(rec.get('font', '')) in names:
                paths.append(sel)
    return paths


def write_outgoing_marker(csv_path, font, trace_dir):
    """Record that WE wrote this Corel CSV (export direction). The
    stored mtime is what later distinguishes the pristine export from
    the same file re-saved by Corel's export macro."""
    marker = {
        'csv': os.path.basename(csv_path),
        'csv_mtime': os.path.getmtime(csv_path),
        'font': _norm(font),
        'trace_dir': os.path.abspath(trace_dir),
    }
    with open(outgoing_marker_path(csv_path), 'w', encoding='utf-8') as f:
        json.dump(marker, f, ensure_ascii=False, indent=2)


def write_imported_marker(edited_csv, imported_glyphs):
    """Record a successful import of an edited CSV (sync idempotence)."""
    marker = {
        'csv': os.path.basename(edited_csv),
        'csv_mtime': os.path.getmtime(edited_csv),
        'imported_glyphs': sorted(imported_glyphs),
    }
    with open(imported_marker_path(edited_csv), 'w', encoding='utf-8') as f:
        json.dump(marker, f, ensure_ascii=False, indent=2)


def _newer(path, than):
    return os.path.getmtime(path) >= os.path.getmtime(than)


def _is_pristine_export(csv):
    """True while the CSV is still exactly what sync-edits wrote (the
    outgoing sidecar's recorded mtime matches). Corel re-saving the
    file bumps its mtime past the record and flips it to a return."""
    marker = outgoing_marker_path(csv)
    if not os.path.exists(marker):
        return False
    try:
        with open(marker, encoding='utf-8') as f:
            recorded = json.load(f).get('csv_mtime', 0.0)
    except ValueError:
        return False
    return os.path.getmtime(csv) <= recorded + _MTIME_EPS


def _routes_to(csv, names):
    """Does this exchange-folder CSV belong to a font with `names`?
    Filename prefix first (sel-<slug>-...), outgoing sidecar second
    (covers selections without a slug in their name)."""
    slug = _filename_slug(csv)
    if slug is not None:
        return slug in names
    marker = outgoing_marker_path(csv)
    if os.path.exists(marker):
        try:
            with open(marker, encoding='utf-8') as f:
                return json.load(f).get('font') in names
        except ValueError:
            pass
    return False


def pending_edits(trace_dir, corel_dir=None):
    """Edited CSVs awaiting import, oldest first so later edits win on
    overlapping glyphs. Sources: the font's edits/ folder (every CSV
    there is a return by definition) and the global Corel exchange
    folder (CSVs routed to this font that are no longer the pristine
    export). 'Awaiting' = no .imported.json marker newer than the CSV."""
    candidates = list(glob.glob(os.path.join(edits_dir(trace_dir), '*.csv')))
    if corel_dir and os.path.isdir(corel_dir):
        names = _trace_dir_names(trace_dir)
        for csv in glob.glob(os.path.join(corel_dir, '*.csv')):
            if _routes_to(csv, names) and not _is_pristine_export(csv):
                candidates.append(csv)
    out = []
    for csv in candidates:
        marker = imported_marker_path(csv)
        if not os.path.exists(marker) or not _newer(marker, csv):
            out.append(csv)
    return sorted(out, key=os.path.getmtime)


def pending_selections(trace_dir, inbox=None, corel_dir=None):
    """Selection files (local + routed from the inbox) whose Corel CSV
    is missing or stale. Returns [(selection_path, csv_path), ...].
    Nothing is moved or deleted — the name-derived CSV path and its
    mtime are what make the check idempotent."""
    out = []
    for sel in selections_for(trace_dir, inbox=inbox):
        csv = subset_csv_path(trace_dir, sel, corel_dir=corel_dir)
        if not os.path.exists(csv) or not _newer(csv, sel):
            out.append((sel, csv))
    return out


def has_pending(trace_dir, inbox=None, corel_dir=None):
    """Cheap check for the TOPs generate callback: any handshake work?"""
    return bool(pending_edits(trace_dir, corel_dir=corel_dir)
                or pending_selections(trace_dir, inbox=inbox,
                                      corel_dir=corel_dir))
