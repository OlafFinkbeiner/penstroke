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


N_RESAMPLE = 240          # points per stroke after import (matches tracer)
EXPORT_STEP_PX = 3.0      # stroke-point spacing written to the CSV
MIN_WIDTH_PX = 1.5        # floor for re-sampled widths (off-ink safety)

# Bezier fitting (export): error tolerances in pixels. The stroke
# tolerance deliberately exceeds the hand-wobble amplitude so fitted
# curves come out SMOOTH — auto-smoothing falls out of the fit. A
# straight stem fits as one cubic segment (2 nodes); letters typically
# land at 2-6 segments.
STROKE_FIT_TOL_PX = 1.8
UNDERLAY_FIT_TOL_PX = 0.6
SMOOTH_WINDOW_PX = 9.0    # pre-fit moving-average window (kills wobble)


# ---------------------------------------------------------------------------
# Cubic-Bezier fitting (Schneider, "An Algorithm for Automatically
# Fitting Digitized Curves", Graphics Gems 1990). Returns a list of
# cubic segments [(p0, c1, c2, p3), ...] approximating the polyline
# within `tol` pixels.
# ---------------------------------------------------------------------------

def _chord_params(pts):
    d = np.hypot(np.diff(pts[:, 0]), np.diff(pts[:, 1]))
    u = np.concatenate([[0.0], np.cumsum(d)])
    return u / u[-1] if u[-1] > 0 else u


def _bezier_point(bez, t):
    p0, c1, c2, p3 = bez
    mt = 1.0 - t
    return (mt ** 3)[:, None] * p0 + 3 * (mt ** 2 * t)[:, None] * c1 \
        + 3 * (mt * t ** 2)[:, None] * c2 + (t ** 3)[:, None] * p3


def _generate_bezier(pts, u, t_hat1, t_hat2):
    """Least-squares cubic for given parameterization and end tangents."""
    n = len(pts)
    A = np.zeros((n, 2, 2))
    mt = 1.0 - u
    A[:, 0] = t_hat1[None, :] * (3 * mt ** 2 * u)[:, None]
    A[:, 1] = t_hat2[None, :] * (3 * mt * u ** 2)[:, None]
    p0, p3 = pts[0], pts[-1]
    base = (mt ** 3)[:, None] * p0 + (3 * mt ** 2 * u)[:, None] * p0 \
        + (3 * mt * u ** 2)[:, None] * p3 + (u ** 3)[:, None] * p3
    tmp = pts - base
    C = np.zeros((2, 2))
    X = np.zeros(2)
    C[0, 0] = np.sum(A[:, 0] * A[:, 0])
    C[0, 1] = C[1, 0] = np.sum(A[:, 0] * A[:, 1])
    C[1, 1] = np.sum(A[:, 1] * A[:, 1])
    X[0] = np.sum(A[:, 0] * tmp)
    X[1] = np.sum(A[:, 1] * tmp)
    det = C[0, 0] * C[1, 1] - C[0, 1] * C[1, 0]
    if abs(det) > 1e-12:
        alpha1 = (X[0] * C[1, 1] - X[1] * C[0, 1]) / det
        alpha2 = (C[0, 0] * X[1] - C[1, 0] * X[0]) / det
    else:
        alpha1 = alpha2 = 0.0
    seg_len = float(np.hypot(*(p3 - p0)))
    eps = 1e-6 * seg_len
    if alpha1 < eps or alpha2 < eps:
        alpha1 = alpha2 = seg_len / 3.0
    c1 = p0 + t_hat1 * alpha1
    c2 = p3 + t_hat2 * alpha2
    return (p0, c1, c2, p3)


def _max_error(pts, bez, u):
    q = _bezier_point(bez, u)
    err = np.hypot(q[:, 0] - pts[:, 0], q[:, 1] - pts[:, 1])
    i = int(np.argmax(err))
    return float(err[i]), i


def _tangent(pts, i, j):
    v = pts[j] - pts[i]
    n = np.linalg.norm(v)
    return v / n if n > 0 else np.array([1.0, 0.0])


def fit_beziers(pts, tol):
    """Fit cubic Beziers to an Nx2 polyline within `tol` px."""
    pts = np.asarray(pts, dtype=float)
    # Drop consecutive duplicates (degenerate params otherwise).
    keep = np.ones(len(pts), dtype=bool)
    keep[1:] = (np.hypot(np.diff(pts[:, 0]), np.diff(pts[:, 1])) > 1e-9)
    pts = pts[keep]
    if len(pts) < 2:
        return []
    if len(pts) == 2:
        third = (pts[1] - pts[0]) / 3.0
        return [(pts[0], pts[0] + third, pts[1] - third, pts[1])]
    t1 = _tangent(pts, 0, 1)
    t2 = _tangent(pts, -1, -2)
    return _fit_recursive(pts, t1, t2, tol)


def _fit_recursive(pts, t_hat1, t_hat2, tol, depth=0):
    if len(pts) == 2:
        third = (pts[1] - pts[0]) / 3.0
        return [(pts[0], pts[0] + third, pts[1] - third, pts[1])]
    u = _chord_params(pts)
    bez = _generate_bezier(pts, u, t_hat1, t_hat2)
    err, split = _max_error(pts, bez, u)
    if err <= tol or depth > 24:
        return [bez]
    # A couple of Newton-Raphson reparameterization passes often saves
    # a split.
    if err <= tol * 4:
        for _ in range(3):
            u = _reparameterize(pts, bez, u)
            bez = _generate_bezier(pts, u, t_hat1, t_hat2)
            err, split = _max_error(pts, bez, u)
            if err <= tol:
                return [bez]
    split = max(1, min(len(pts) - 2, split))
    center_tangent = _tangent(pts, min(split + 1, len(pts) - 1),
                              max(split - 1, 0))
    left = _fit_recursive(pts[:split + 1], t_hat1, center_tangent,
                          tol, depth + 1)
    right = _fit_recursive(pts[split:], -center_tangent, t_hat2,
                           tol, depth + 1)
    return left + right


def _reparameterize(pts, bez, u):
    p0, c1, c2, p3 = bez
    # Derivative control points
    d1 = 3 * (np.array([c1, c2, p3]) - np.array([p0, c1, c2]))
    d2 = 2 * (d1[1:] - d1[:-1])
    out = u.copy()
    for i in range(1, len(u) - 1):
        t = u[i]
        mt = 1 - t
        q = (mt ** 3) * p0 + 3 * mt ** 2 * t * c1 \
            + 3 * mt * t ** 2 * c2 + (t ** 3) * p3
        q1 = (mt ** 2) * d1[0] + 2 * mt * t * d1[1] + (t ** 2) * d1[2]
        q2 = mt * d2[0] + t * d2[1]
        num = float(np.dot(q - pts[i], q1))
        den = float(np.dot(q1, q1) + np.dot(q - pts[i], q2))
        if abs(den) > 1e-12:
            out[i] = t - num / den
    out = np.clip(out, 0.0, 1.0)
    out = np.maximum.accumulate(out)   # keep monotone
    return out


def _smooth_polyline(pts, window_px):
    """Moving-average smoothing with arc-length-derived window size."""
    arr = np.asarray(pts, dtype=float)
    if len(arr) < 5:
        return arr
    seg = np.hypot(np.diff(arr[:, 0]), np.diff(arr[:, 1]))
    mean_step = float(np.mean(seg)) if len(seg) else 1.0
    w = max(3, int(round(window_px / max(mean_step, 1e-6))))
    if w % 2 == 0:
        w += 1
    if w >= len(arr):
        return arr
    kernel = np.ones(w) / w
    sx = np.convolve(arr[:, 0], kernel, mode='same')
    sy = np.convolve(arr[:, 1], kernel, mode='same')
    # Keep endpoints exact (convolve corrupts the borders).
    half = w // 2
    sx[:half], sx[-half:] = arr[:half, 0], arr[-half:, 0]
    sy[:half], sy[-half:] = arr[:half, 1], arr[-half:, 1]
    return np.column_stack([sx, sy])


def _flatten_beziers(segments, step_px=2.0):
    """Sample a cubic-segment chain back into a polyline."""
    out = []
    for (p0, c1, c2, p3) in segments:
        # Rough length estimate via control polygon
        approx = (np.linalg.norm(c1 - p0) + np.linalg.norm(c2 - c1)
                  + np.linalg.norm(p3 - c2))
        n = max(4, int(approx / step_px))
        t = np.linspace(0, 1, n, endpoint=False)
        out.append(_bezier_point((p0, c1, c2, p3), t))
    out.append(segments[-1][3][None, :])
    return np.vstack(out)


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
    """Load {char: [(xs, ys, widths), ...]} or None if absent."""
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
    ordered list of dicts: {char, safe, strokes: [Nx2 array, ...]}
    with strokes ordered by their stroke index (= Corel object name).
    Underlay records are ignored — only S records carry edits.
    """
    header = None
    glyph_by_page = {}
    strokes_by_page = {}
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
                # Bezier stroke record (our own export format) — flatten
                # the cubic so import handles both formats uniformly.
                page = int(parts[1])
                si = int(parts[3])
                vals = [_f(v) for v in parts[4:12]]
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
        g['strokes'] = [np.asarray(per[si], dtype=float)
                        for si in sorted(per)]
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
    for g in glyphs:
        ch = safe_to_char.get(g['safe'])
        if ch is None and g['char'] != unknown_char:
            ch = g['char']
        if ch is None or not g['strokes']:
            continue
        traced = resample_widths(g['strokes'], ttf, ch, size)
        if traced:
            edited[ch] = traced

    # Exchange ONLY the glyphs present in the CSV; everything else
    # keeps its current strokes (from the store written at trace /
    # previous import time).
    current = load_stroke_store(output_dir) or {}
    current.update(edited)

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
