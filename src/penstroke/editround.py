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

import os

import numpy as np

from penstroke.core.rasterize import rasterize_glyph
from penstroke.core.skeleton import skeletonize
from penstroke.core.outline import extract_outlines


N_RESAMPLE = 240          # points per stroke after import (matches tracer)
EXPORT_STEP_PX = 3.0      # stroke-point spacing written to the CSV
MIN_WIDTH_PX = 1.5        # floor for re-sampled widths (off-ink safety)


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


def write_edit_csv(output_dir, csv_path, font_name, ttf_path, letters,
                   size, safe_filename_fn):
    """Trace every glyph and write the edit CSV for the Corel macro."""
    from penstroke.tracer import trace_glyph_eulerian

    lines = []
    canvas_w = canvas_h = None
    page = 0
    glyph_pages = []
    for ch in letters:
        try:
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
        try:
            outlines = extract_outlines(ttf_path, ch, size=size)
        except Exception:
            outlines = []
        for pi, poly in enumerate(outlines):
            rp = _resample_polyline(poly, step=EXPORT_STEP_PX)
            for (x, y) in rp:
                lines.append(f'U;{page};{pi};{x:.2f};{y:.2f}')
        for si, (xs, ys, _ws) in enumerate(traced):
            rp = _resample_polyline(np.column_stack([xs, ys]),
                                    step=EXPORT_STEP_PX)
            for (x, y) in rp:
                lines.append(f'S;{page};{si};{x:.2f};{y:.2f}')
        page += 1

    header = (f'H;penstroke-edit;1;{font_name};'
              f'{canvas_w};{canvas_h};{size}')
    with open(csv_path, 'w', encoding='utf-8') as f:
        f.write(header + '\n')
        f.write('\n'.join(lines) + '\n')
    return len(glyph_pages)


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
                    'canvas_w': int(float(parts[4])),
                    'canvas_h': int(float(parts[5])),
                    'size': int(float(parts[6])),
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
                x, y = float(parts[3]), float(parts[4])
                strokes_by_page.setdefault(page, {}).setdefault(
                    si, []).append((x, y))
            # 'U' underlay records: ignored on import.
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
