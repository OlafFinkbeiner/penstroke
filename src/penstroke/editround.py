"""CorelDRAW edit round-trip: export traces for hand-editing, re-import.

Why not PDF/SVG: the user edits stroke decompositions by hand in
CorelDRAW and needs every stroke to be a NAMED object (s01, s02, ...)
whose name survives editing. PDF paths are anonymous and SVG has no
pages, so instead the document is built natively inside CorelDRAW by a
VBA macro (corel/penstroke_corel.bas) reading a CSV that this module
writes. A second macro walks the edited document and writes the same
CSV format back; `read_edit_csv` + `resample_widths` turn that into
traced strokes and the pipeline re-renders everything.

CSV format (one record per line, ';'-separated, utf-8):

    H;penstroke-edit;1;<font_name>;<canvas_w>;<canvas_h>;<size>
    G;<page_index>;<char_hex>;<safe_name>          one per glyph page
    U;<page_index>;<poly_index>;<x>;<y>            underlay outline pts
    S;<page_index>;<stroke_index>;<x>;<y>          stroke centerline pts

Coordinates are canvas pixels (y DOWN, like the rest of the pipeline).
The VBA macro flips y for Corel's bottom-up page coordinates; the
import side receives canvas pixels back (the macro flips again on
export), so Python never sees Corel coordinates.

Stroke index comes from the OBJECT NAME in Corel ("s01" -> 1). Widths
are NOT stored: on import they are re-sampled from the font's distance
transform, exactly as the tracer does — the user edits pure geometry.
"""

import json
import os

import numpy as np

from penstroke.core.rasterize import rasterize_glyph
from penstroke.core.skeleton import skeletonize
from penstroke.core.outline import extract_outlines
# Cubic-Bezier fitting lives in penstroke.curvefit (numpy-only, so it
# imports under hython too). Re-exported here for existing callers.
from penstroke.curvefit import (  # noqa: F401
    fit_beziers, smooth_polyline as _smooth_polyline,
    flatten_beziers as _flatten_beziers, _bezier_point,
    STROKE_FIT_TOL_PX, UNDERLAY_FIT_TOL_PX, SMOOTH_WINDOW_PX)


N_RESAMPLE = 240          # points per stroke after import (matches tracer)
EXPORT_STEP_PX = 3.0      # stroke-point spacing written to the CSV
MIN_WIDTH_PX = 1.5        # floor for re-sampled widths (off-ink safety)


# ---------------------------------------------------------------------------
# Stroke store: output_dir/strokes.json is the SOURCE OF TRUTH for a
# font's current decomposition. trace_font writes it; import-corel
# merges edited glyphs into it; rendering and export-corel read it.
# This is what makes partial edit rounds accumulate instead of reset.
# ---------------------------------------------------------------------------

def store_path(output_dir):
    return os.path.join(output_dir, 'strokes.json')


def save_stroke_store(output_dir, traced_map):
    """Persist {char: [(xs, ys, widths), ...]} as JSON."""
    data = {}
    for ch, strokes in traced_map.items():
        data[ch] = [
            {'x': [round(float(v), 2) for v in xs],
             'y': [round(float(v), 2) for v in ys],
             'w': [round(float(v), 2) for v in ws]}
            for (xs, ys, ws) in strokes
        ]
    with open(store_path(output_dir), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)


def load_stroke_store(output_dir):
    """Load {char: [(xs, ys, widths), ...]} or None if absent.

    The optional per-stroke `bez` field (exact Corel cubics) is NOT
    returned here — the (xs, ys, ws) shape is what the pipeline and
    renderers consume. Use load_stroke_bez() for the cubics.
    """
    path = store_path(output_dir)
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    out = {}
    for ch, strokes in data.items():
        out[ch] = [(np.asarray(s['x'], dtype=float),
                    np.asarray(s['y'], dtype=float),
                    np.asarray(s['w'], dtype=float)) for s in strokes]
    return out


def load_stroke_bez(output_dir):
    """Load the exact per-stroke cubics from the store, if any.

    Returns {char: [segments-or-None, ...]} where segments is a list of
    [x0,y0,c1x,c1y,c2x,c2y,x1,y1] px cubics (canvas frame, y down) for a
    stroke that came from hand-edited Corel beziers, else None. Empty
    dict if the store is absent or carries no cubics.
    """
    path = store_path(output_dir)
    if not os.path.exists(path):
        return {}
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    out = {}
    for ch, strokes in data.items():
        if any('bez' in s for s in strokes):
            out[ch] = [s.get('bez') for s in strokes]
    return out


def merge_stroke_bez(output_dir, bez_map):
    """Attach exact cubics to the store's strokes, in place.

    `bez_map` is {char: [segments-or-None per stroke]}. Re-reads the
    store JSON (which trace_font just wrote, polyline only), adds a
    `bez` field to each matching stroke (by index; a count mismatch
    drops that glyph's cubics rather than misalign), and rewrites it.
    """
    path = store_path(output_dir)
    if not bez_map or not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    for ch, segs_per_stroke in bez_map.items():
        strokes = data.get(ch)
        if not strokes or len(strokes) != len(segs_per_stroke):
            continue
        for s, segs in zip(strokes, segs_per_stroke):
            if segs:
                s['bez'] = [[round(float(v), 2) for v in seg]
                            for seg in segs]
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)


# Charset presets live in penstroke.charset (dependency-light so
# hython can import them); re-exported here for existing callers.
from penstroke.charset import CHARSETS, font_charset  # noqa: F401


def _resample_polyline(pts, n=None, step=None):
    """Resample an Nx2 polyline to `n` points or ~`step` spacing."""
    arr = np.asarray(pts, dtype=float)
    if len(arr) < 2:
        return arr
    seg = np.hypot(np.diff(arr[:, 0]), np.diff(arr[:, 1]))
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(cum[-1])
    if total <= 0:
        return arr[:1]
    if n is None:
        n = max(2, int(round(total / step)) + 1)
    t = np.linspace(0.0, total, n)
    x = np.interp(t, cum, arr[:, 0])
    y = np.interp(t, cum, arr[:, 1])
    return np.column_stack([x, y])


def _bez_records(tag, page, idx, segments):
    """Format cubic segments as B records (y stays canvas-pixel, y-down).

    B;<page>;<kind S|U>;<idx>;x0;y0;c1x;c1y;c2x;c2y;x1;y1
    """
    out = []
    for (p0, c1, c2, p3) in segments:
        out.append('B;{};{};{};'.format(page, tag, idx)
                   + ';'.join(f'{v:.2f}' for v in
                              (p0[0], p0[1], c1[0], c1[1],
                               c2[0], c2[1], p3[0], p3[1])))
    return out


def write_edit_csv(output_dir, csv_path, font_name, ttf_path, letters,
                   size, safe_filename_fn, stroke_source=None):
    """Write the edit CSV for the Corel macro.

    Strokes come from `stroke_source` (the stroke store — includes any
    previous hand edits) when given, otherwise from a fresh trace.
    Strokes and the original font outline are emitted as FITTED cubic
    Beziers (B records): a straight stem is one segment (2 nodes), a
    typical letter 2-6 segments. The stroke fit tolerance exceeds the
    hand-wobble amplitude, so the exported curves come out smooth.
    """
    from penstroke.tracer import trace_glyph_eulerian

    lines = []
    canvas_w = canvas_h = None
    page = 0
    glyph_pages = []
    n_segments = 0
    n_strokes = 0
    for ch in letters:
        try:
            if stroke_source is not None and ch in stroke_source:
                traced = stroke_source[ch]
                _mask, meta = rasterize_glyph(ttf_path, ch, size=size)
            else:
                mask, _skel, _dist, traced, _t, meta = trace_glyph_eulerian(
                    ttf_path, ch, size=size)
        except Exception:
            continue
        if not traced:
            continue
        if canvas_w is None:
            canvas_w, canvas_h = meta['canvas_w'], meta['canvas_h']
        safe = safe_filename_fn(ch).rsplit('.', 1)[0]
        glyph_pages.append((page, ch, safe))
        lines.append(f'G;{page};{ord(ch):04x};{safe}')
        # Original font outline, fitted tight (one closed subpath per
        # contour; the macro combines them into ONE shape per page so
        # counters render as holes).
        try:
            outlines = extract_outlines(ttf_path, ch, size=size)
        except Exception:
            outlines = []
        for pi, poly in enumerate(outlines):
            segs = fit_beziers(np.asarray(poly, dtype=float),
                               UNDERLAY_FIT_TOL_PX)
            lines.extend(_bez_records('U', page, pi, segs))
        # Strokes: smooth first (kills wobble), then fit loose.
        for si, (xs, ys, _ws) in enumerate(traced):
            pts = _smooth_polyline(np.column_stack([xs, ys]),
                                   SMOOTH_WINDOW_PX)
            segs = fit_beziers(pts, STROKE_FIT_TOL_PX)
            lines.extend(_bez_records('S', page, si, segs))
            n_segments += len(segs)
            n_strokes += 1
        page += 1

    header = (f'H;penstroke-edit;2;{font_name};'
              f'{canvas_w};{canvas_h};{size}')
    with open(csv_path, 'w', encoding='utf-8') as f:
        f.write(header + '\n')
        f.write('\n'.join(lines) + '\n')
    if n_strokes:
        print(f'  bezier fit: {n_segments / n_strokes:.1f} segments/stroke '
              f'average across {n_strokes} strokes')
    return len(glyph_pages)


def _f(s):
    """Locale-tolerant float: VBA's Format$ writes decimal COMMAS on
    e.g. German Windows. The CSV field separator is ';', so a comma
    inside a field is always a decimal mark."""
    return float(s.replace(',', '.'))


def read_edit_csv(csv_path):
    """Parse an edit CSV (as written by us OR by the Corel export macro).

    Returns (header, glyphs) where header is a dict and glyphs is an
    ordered list of dicts: {char, safe, strokes: [Nx2 array, ...],
    bez: [segments-or-None, ...]} with strokes ordered by their stroke
    index (= Corel object name). `bez` carries the EXACT cubic-Bezier
    control points when the CSV had B records (our export, or a Corel
    export macro that writes nodes instead of sampling) — each entry is
    a list of [x0,y0,c1x,c1y,c2x,c2y,x1,y1] px segments, or None when
    that stroke arrived as sampled S points only. Underlay records are
    ignored — only S/B stroke records carry edits.
    """
    header = None
    glyph_by_page = {}
    strokes_by_page = {}
    bez_by_page = {}            # page -> {si: [segment, ...]} (raw cubics)
    with open(csv_path, encoding='utf-8') as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            parts = raw.split(';')
            tag = parts[0]
            if tag == 'H':
                header = {
                    'version': parts[2],
                    'font_name': parts[3],
                    'canvas_w': int(_f(parts[4])),
                    'canvas_h': int(_f(parts[5])),
                    'size': int(_f(parts[6])),
                }
            elif tag == 'G':
                page = int(parts[1])
                glyph_by_page[page] = {
                    'char': chr(int(parts[2], 16)),
                    'safe': parts[3],
                }
            elif tag == 'S':
                page = int(parts[1])
                si = int(parts[2])
                x, y = _f(parts[3]), _f(parts[4])
                strokes_by_page.setdefault(page, {}).setdefault(
                    si, []).append((x, y))
            elif tag == 'B' and parts[2] == 'S':
                # Bezier stroke record. Keep the EXACT cubic (handle
                # fidelity) AND flatten it to points so the polyline /
                # width path handles B and S formats uniformly.
                page = int(parts[1])
                si = int(parts[3])
                vals = [_f(v) for v in parts[4:12]]
                bez_by_page.setdefault(page, {}).setdefault(
                    si, []).append(vals)
                p0 = np.array(vals[0:2]); c1 = np.array(vals[2:4])
                c2 = np.array(vals[4:6]); p3 = np.array(vals[6:8])
                flat = _flatten_beziers([(p0, c1, c2, p3)], step_px=2.0)
                strokes_by_page.setdefault(page, {}).setdefault(
                    si, []).extend(map(tuple, flat))
            # 'U' / 'B;..;U' underlay records: ignored on import.
    if header is None:
        raise ValueError(f'{csv_path}: missing H header record')
    glyphs = []
    for page in sorted(glyph_by_page):
        g = glyph_by_page[page]
        per = strokes_by_page.get(page, {})
        bez = bez_by_page.get(page, {})
        order = sorted(per)
        g['strokes'] = [np.asarray(per[si], dtype=float) for si in order]
        g['bez'] = [bez.get(si) for si in order]   # exact cubics or None
        glyphs.append(g)
    return header, glyphs


def import_edit_csv(output_dir, edited_csv, size=384, verbose=True):
    """Merge an edited CSV into a trace folder and rebuild it.

    The full import-corel operation: parse the CSV, resolve glyph
    identity via the page name (the Corel export macro doesn't know
    codepoints), resample widths from the font ink, exchange ONLY the
    glyphs present in the CSV in the stroke store, re-render the whole
    output folder, and write the .imported.json marker that makes
    `penstroke sync-edits` idempotent.

    Returns (n_exchanged, n_kept).
    """
    from penstroke import handshake
    from penstroke.pipeline import trace_font, safe_filename

    with open(os.path.join(output_dir, 'metadata.json'),
              encoding='utf-8') as f:
        meta = json.load(f)
    import glob as _glob
    fonts = sorted(_glob.glob(os.path.join(output_dir, 'source', '*.ttf')))
    if not fonts:
        raise FileNotFoundError(
            f'no source font found under {output_dir}/source/')
    ttf = fonts[0]
    header, glyphs = read_edit_csv(edited_csv)

    # Glyph identity travels via the page name (= safe filename stem);
    # build the reverse map from metadata. chr(0) = "unknown" from the
    # Corel macro.
    safe_to_char = {safe_filename(ch).rsplit('.', 1)[0]: ch
                    for ch in meta['letters']}
    unknown_char = chr(0)

    edited = {}
    edited_bez = {}        # ch -> [segments-or-None per kept stroke]
    for g in glyphs:
        ch = safe_to_char.get(g['safe'])
        if ch is None and g['char'] != unknown_char:
            ch = g['char']
        if ch is None or not g['strokes']:
            continue
        traced = resample_widths(g['strokes'], ttf, ch, size)
        if traced:
            edited[ch] = traced
            # Pair the exact cubics with the resampled strokes by index
            # (resample_widths drops <2-pt strokes, so realign on the
            # kept ones — both come from g in stroke order).
            if any(b is not None for b in g.get('bez', [])):
                edited_bez[ch] = [b for b, pts in zip(g['bez'], g['strokes'])
                                  if len(pts) >= 2]

    # Exchange ONLY the glyphs present in the CSV; everything else
    # keeps its current strokes (from the store written at trace /
    # previous import time).
    current = load_stroke_store(output_dir) or {}
    current.update(edited)

    # Carry forward the exact Corel cubics already in the store (other
    # glyphs) so trace_font's rewrite doesn't drop them; this round's
    # edits win for their glyphs.
    prior_bez = load_stroke_bez(output_dir)
    prior_bez.update(edited_bez)

    def glyph_source(ch):
        if ch not in current:
            return None
        mask, m = rasterize_glyph(ttf, ch, size=size)
        return mask, current[ch], 'edited' if ch in edited else 'kept', m

    trace_font(ttf, output_dir,
               font_name=meta['font_name'],
               letters=''.join(current.keys()),
               size=size,
               glyph_source=glyph_source,
               verbose=verbose)
    # trace_font rewrote strokes.json (polyline only) — merge the exact
    # cubics back in per stroke.
    merge_stroke_bez(output_dir, prior_bez)
    handshake.write_imported_marker(edited_csv, list(edited.keys()))
    return len(edited), len(current) - len(edited)


def resample_widths(strokes_xy, ttf_path, char, size):
    """Turn edited centerlines into (xs, ys, widths) strokes.

    Widths come from the font's distance transform — the user edits
    geometry only. Geometry is preserved (no smoothing, no wobble:
    hand edits are intentional), only resampled to the tracer's point
    density so animation timing behaves identically.
    """
    mask, _meta = rasterize_glyph(ttf_path, char, size=size)
    _skel, dist = skeletonize(mask)
    H, W = dist.shape
    out = []
    for pts in strokes_xy:
        rp = _resample_polyline(pts, n=N_RESAMPLE)
        if len(rp) < 2:
            continue
        xi = np.clip(np.round(rp[:, 0]).astype(int), 0, W - 1)
        yi = np.clip(np.round(rp[:, 1]).astype(int), 0, H - 1)
        widths = np.maximum(dist[yi, xi] * 2.0, MIN_WIDTH_PX)
        out.append((rp[:, 0], rp[:, 1], widths))
    return out
