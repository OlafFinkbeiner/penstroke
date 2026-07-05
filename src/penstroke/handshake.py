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
                        exports BACK ONTO THE SAME FILE — or under any
                        name that contains the font's name (Corel's
                        default '<doctitle>_edited.csv' qualifies as
                        long as the document kept the font in its
                        name). A CSV counts as an edited return when
                        its CONTENT differs from the pristine export
                        recorded in the .outgoing.json sidecar, and as
                        merged once its .imported.json marker records
                        the same content hash. mtimes are only a fast
                        path — a cloud-sync or copy that bumps the
                        mtime without changing bytes changes nothing.
                        A CSV that fails to import is quarantined with
                        a .failed.json marker and retried only when
                        its content changes, so one malformed file
                        can't wedge the sync. Nothing is moved or
                        deleted.

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
import hashlib
import json
import os

SELECTIONS_DIR = 'selections'
SUBSETS_DIR = 'subsets'
EDITS_DIR = 'edits'
IMPORTED_MARKER_SUFFIX = '.imported.json'
OUTGOING_MARKER_SUFFIX = '.outgoing.json'
FAILED_MARKER_SUFFIX = '.failed.json'
_MTIME_EPS = 1e-6


def file_sha1(path):
    """Content hash of a file — the handshake's identity primitive.

    State decisions compare content, never mtime alone: cloud sync,
    robocopy, or a disk move bump mtimes without editing anything, and
    treating that as an edit would silently re-import a pristine export
    (replacing raw traced strokes with the export's smoothed refit).
    """
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


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


def failed_marker_path(edited_csv):
    return edited_csv + FAILED_MARKER_SUFFIX


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


def _name_candidates(path):
    """Every font slug a filename could be naming: all concatenations
    of consecutive alphanumeric runs in the stem. Covers our own
    sel-<slug>-<hash>.csv as well as Corel's free-form save names
    ('allison-e570ba_edited.csv', 'aguafina script fix 2.csv', ...) —
    matching is exact against full normalized family names, so a
    fragment like 'sans' never routes to 'opensans'."""
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    tokens, cur = [], ''
    for c in stem:
        if c.isalnum():
            cur += c
        elif cur:
            tokens.append(cur)
            cur = ''
    if cur:
        tokens.append(cur)
    cands = set()
    for i in range(len(tokens)):
        acc = ''
        for j in range(i, len(tokens)):
            acc += tokens[j]
            cands.add(acc)
    return cands


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
    stored content hash is what later distinguishes the pristine export
    from a genuinely edited return; the mtime is only a fast path."""
    marker = {
        'csv': os.path.basename(csv_path),
        'csv_mtime': os.path.getmtime(csv_path),
        'csv_sha1': file_sha1(csv_path),
        'font': _norm(font),
        'trace_dir': os.path.abspath(trace_dir),
    }
    with open(outgoing_marker_path(csv_path), 'w', encoding='utf-8') as f:
        json.dump(marker, f, ensure_ascii=False, indent=2)


def write_imported_marker(edited_csv, imported_glyphs, csv_sha1=None):
    """Record a successful import of an edited CSV (sync idempotence).

    `csv_sha1` should be the hash of the content that was actually
    parsed (snapshotted BEFORE the import read the file). If the file
    keeps being written while we import — Corel mid-export — the marker
    then records the pre-read content, the current file hashes
    differently, and the completed edit is re-imported on the next
    sync instead of being silently lost.
    """
    marker = {
        'csv': os.path.basename(edited_csv),
        'csv_mtime': os.path.getmtime(edited_csv),
        'csv_sha1': csv_sha1 or file_sha1(edited_csv),
        'imported_glyphs': sorted(imported_glyphs),
    }
    with open(imported_marker_path(edited_csv), 'w', encoding='utf-8') as f:
        json.dump(marker, f, ensure_ascii=False, indent=2)


def write_failed_marker(edited_csv, error):
    """Quarantine a CSV that failed to import. The recorded content
    hash means it is retried only once the file actually changes —
    one malformed file must never wedge sync-edits forever."""
    marker = {
        'csv': os.path.basename(edited_csv),
        'csv_sha1': file_sha1(edited_csv),
        'error': str(error),
    }
    with open(failed_marker_path(edited_csv), 'w', encoding='utf-8') as f:
        json.dump(marker, f, ensure_ascii=False, indent=2)


def _newer(path, than):
    return os.path.getmtime(path) >= os.path.getmtime(than)


def _read_marker(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except ValueError:
        return None


def _is_pristine_export(csv):
    """True while the CSV's CONTENT is still exactly what sync-edits
    wrote. A matching mtime short-circuits (no hashing); a bumped mtime
    with identical bytes (cloud sync, copy, touch) is still pristine —
    the recorded mtime is refreshed so the next check stays cheap.
    Legacy sidecars without a hash keep the old mtime-only rule."""
    rec = _read_marker(outgoing_marker_path(csv))
    if rec is None:
        return False
    if os.path.getmtime(csv) <= rec.get('csv_mtime', 0.0) + _MTIME_EPS:
        return True
    sha = rec.get('csv_sha1')
    if sha is None:
        return False                    # legacy marker: mtime rules
    if file_sha1(csv) != sha:
        return False                    # real content change → a return
    rec['csv_mtime'] = os.path.getmtime(csv)
    try:
        with open(outgoing_marker_path(csv), 'w', encoding='utf-8') as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
    except OSError:
        pass                            # refresh is only an optimization
    return True


def _already_imported(csv):
    """Has this exact content already been merged? Content-hash check;
    legacy markers without a hash fall back to the mtime rule."""
    rec = _read_marker(imported_marker_path(csv))
    if rec is None:
        return False
    sha = rec.get('csv_sha1')
    if sha is not None:
        return file_sha1(csv) == sha
    marker = imported_marker_path(csv)
    return os.path.exists(marker) and _newer(marker, csv)


def _quarantined(csv):
    """True while the CSV still has the exact content that failed to
    import. Any content change lifts the quarantine (retry)."""
    rec = _read_marker(failed_marker_path(csv))
    if rec is None:
        return False
    return rec.get('csv_sha1') == file_sha1(csv)


def _routes_to(csv, names):
    """Does this exchange-folder CSV belong to a font with `names`?
    The outgoing sidecar is authoritative when present — it names the
    exact font, so a prefix-family neighbour ('Exo' vs 'Exo 2') can
    never claim the file by filename tokens. Token matching is the
    fallback for Corel's free-form save names, which carry no sidecar."""
    marker = outgoing_marker_path(csv)
    if os.path.exists(marker):
        try:
            with open(marker, encoding='utf-8') as f:
                return json.load(f).get('font') in names
        except ValueError:
            pass
    return bool(_name_candidates(csv) & names)


def pending_edits(trace_dir, corel_dir=None):
    """Edited CSVs awaiting import, oldest first so later edits win on
    overlapping glyphs. Sources: the font's edits/ folder (every CSV
    there is a return by definition) and the global Corel exchange
    folder (CSVs routed to this font whose content differs from the
    pristine export). 'Awaiting' = content not yet recorded by an
    .imported.json marker, and not quarantined by a .failed.json
    marker from a previous failed import of the same content."""
    candidates = list(glob.glob(os.path.join(edits_dir(trace_dir), '*.csv')))
    if corel_dir and os.path.isdir(corel_dir):
        names = _trace_dir_names(trace_dir)
        for csv in glob.glob(os.path.join(corel_dir, '*.csv')):
            if _routes_to(csv, names) and not _is_pristine_export(csv):
                candidates.append(csv)
    out = []
    for csv in candidates:
        if not _already_imported(csv) and not _quarantined(csv):
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
