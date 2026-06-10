"""Per-letter diagnostic PNG: colored strokes, numbered starts, faint underlay.

Designed for visual self-QA — both human and vision-model. Each PNG shows:

  - The original TTF glyph outline in faint yellow background.
  - Each traced stroke drawn in a distinct rainbow color in stroke order
    (red → orange → yellow → green → blue → indigo → violet, repeating).
  - A numbered circle at the START of each stroke (so you can see order).
  - A small arrow at the END of each stroke (so you can see direction).
  - A strip of per-stroke mini-panels (one stroke per panel) below the
    main view so order and shape are unambiguous when strokes overlap.
  - A FLIPBOOK strip below that showing the strokes drawn cumulatively
    at five time-points — answers "does this look like writing in
    motion?", which is the real test of whether the trace is right.

Loaded with `Read`, these images let me judge stroke decomposition
correctness directly: is the stroke order natural? are the directions
top-down? did any feature get missed? are there phantom strokes
inside a single pen-width? Does it animate like a person writing?
"""

import os
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from penstroke.core.outline import extract_outlines


# Rainbow palette in stroke order. Distinct enough to tell apart at a glance.
_RAINBOW = [
    (220, 30, 30),    # red
    (230, 110, 20),   # orange
    (210, 180, 0),    # yellow (darkened so it's visible on yellow underlay)
    (40, 160, 50),    # green
    (40, 110, 200),   # blue
    (100, 50, 180),   # indigo
    (170, 60, 180),   # violet
    (40, 180, 180),   # teal
    (180, 100, 80),   # brown
]


def _stroke_color(i):
    return _RAINBOW[i % len(_RAINBOW)]


def _polygon_to_pil(poly, scale, offset):
    """Convert an Nx2 polygon (in canvas coords) to flat PIL polygon list."""
    pts = []
    for x, y in poly:
        pts.append((float(x) * scale + offset[0],
                    float(y) * scale + offset[1]))
    return pts


def _draw_arrow(draw, x_from, y_from, x_to, y_to, color, head_len=8):
    """Draw a small arrowhead at (x_to, y_to) pointing away from (x_from, y_from)."""
    dx = x_to - x_from
    dy = y_to - y_from
    L = math.hypot(dx, dy)
    if L < 1e-3:
        return
    ux, uy = dx / L, dy / L
    # two flanks at ±25°
    angle = math.radians(25)
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    f1x = x_to - head_len * (ux * cos_a - uy * sin_a)
    f1y = y_to - head_len * (uy * cos_a + ux * sin_a)
    f2x = x_to - head_len * (ux * cos_a + uy * sin_a)
    f2y = y_to - head_len * (uy * cos_a - ux * sin_a)
    draw.polygon([(x_to, y_to), (f1x, f1y), (f2x, f2y)], fill=color)


def render_letter_diagnostic(char, mask, traced, outlines, out_path,
                             canvas_size=512, font_path=None,
                             panel_size=110, panel_pad=6):
    """Render the per-letter diagnostic PNG.

    Layout:
      - Main panel at top: all strokes overlaid in rainbow order, with
        numbered start markers and end-arrows.
      - Strip of small per-stroke panels below: one tiny view per stroke,
        in stroke order, showing JUST that stroke against the faint
        glyph outline. Makes order and shape readable even when the
        main view has overlapping strokes.

    Args:
        char: the character (for the corner label).
        mask: the rasterized glyph mask in original coords.
        traced: list of (xs, ys, widths) strokes.
        outlines: list of TTF outline polygons (same coords as mask).
        out_path: where to save the .png.
        canvas_size: width of the main panel in pixels.
        panel_size: side length of each per-stroke mini panel.
        panel_pad: padding between mini panels.
    """
    H, W = mask.shape
    pad = 16
    scale = (canvas_size - 2 * pad) / max(H, W)
    fit_w = W * scale
    fit_h = H * scale
    ox = (canvas_size - fit_w) / 2
    oy = (canvas_size - fit_h) / 2

    n = len(traced)
    cols = max(1, (canvas_size - panel_pad) // (panel_size + panel_pad))
    strip_rows = (n + cols - 1) // cols if n > 0 else 0
    strip_h = strip_rows * (panel_size + panel_pad) + panel_pad + 24 if n else 0
    # Flipbook strip: 5 frames in one row. Same panel size as per-stroke.
    flipbook_frames = [0.2, 0.4, 0.6, 0.8, 1.0] if n > 0 else []
    flipbook_h = (panel_size + panel_pad + 24) if flipbook_frames else 0
    total_h = canvas_size + strip_h + flipbook_h

    img = Image.new('RGB', (canvas_size, total_h), (250, 250, 248))
    draw = ImageDraw.Draw(img, 'RGBA')

    # 1. Original outline in faint yellow.
    for poly in outlines:
        if len(poly) < 3:
            continue
        pts = _polygon_to_pil(poly, scale, (ox, oy))
        draw.polygon(pts, fill=(253, 230, 138, 180))

    # 2. Each stroke as a THIN crisp centerline — distinct color in
    # rainbow order. Thin so overlapping strokes don't hide each other.
    placed_markers = []  # list of (cx, cy, r) so we can offset overlaps
    for i, (xs, ys, widths) in enumerate(traced):
        color = _stroke_color(i)
        pts = [(float(x) * scale + ox, float(y) * scale + oy)
               for x, y in zip(xs, ys)]
        line_w = 3
        for a, b in zip(pts[:-1], pts[1:]):
            draw.line([a, b], fill=color, width=line_w)
        if len(pts) >= 6:
            _draw_arrow(draw, pts[-6][0], pts[-6][1], pts[-1][0], pts[-1][1],
                        color, head_len=12)
        # Numbered start marker. If it would overlap a previously-placed
        # marker, nudge it outward so the digit stays readable.
        sx, sy = pts[0]
        r = 13
        # Direction from stroke[0] to stroke[1] — used to nudge backwards
        # so the marker doesn't sit on top of the line.
        if len(pts) >= 2:
            ddx = pts[1][0] - pts[0][0]
            ddy = pts[1][1] - pts[0][1]
            dlen = (ddx * ddx + ddy * ddy) ** 0.5
            if dlen > 1e-3:
                ddx, ddy = ddx / dlen, ddy / dlen
            else:
                ddx, ddy = 0.0, -1.0
        else:
            ddx, ddy = 0.0, -1.0
        # Backwards-nudge (away from stroke direction) by r so the marker
        # sits just BEFORE the stroke starts.
        cx, cy = sx - ddx * r, sy - ddy * r
        # If still overlapping a previous marker, push further out.
        for _ in range(10):
            collision = False
            for (px, py, pr) in placed_markers:
                if (cx - px) ** 2 + (cy - py) ** 2 < (r + pr) ** 2:
                    cx -= ddx * (r + pr) * 0.6
                    cy -= ddy * (r + pr) * 0.6
                    collision = True
                    break
            if not collision:
                break
        placed_markers.append((cx, cy, r))
        # Halo + circle + number.
        draw.ellipse([cx - r - 2, cy - r - 2, cx + r + 2, cy + r + 2],
                     fill=(255, 255, 255))
        draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                     fill=color, outline=(40, 40, 40), width=1)
        label = str(i + 1)
        try:
            tfont = ImageFont.truetype("arial.ttf", 16)
        except (OSError, IOError):
            tfont = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), label, font=tfont)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text((cx - tw / 2, cy - th / 2 - 2), label,
                  fill=(255, 255, 255), font=tfont)

    # 3. Character label in corner.
    try:
        ltfont = ImageFont.truetype("arial.ttf", 28)
    except (OSError, IOError):
        ltfont = ImageFont.load_default()
    draw.text((8, 4), char, fill=(80, 80, 80), font=ltfont)
    # Stroke count subtitle.
    try:
        sub_font = ImageFont.truetype("arial.ttf", 14)
    except (OSError, IOError):
        sub_font = ImageFont.load_default()
    draw.text((8, 38), f'{len(traced)} stroke{"s" if len(traced) != 1 else ""}',
              fill=(120, 120, 120), font=sub_font)

    # 4. Per-stroke mini panels in a strip below the main panel.
    if n > 0:
        strip_y0 = canvas_size + panel_pad
        # Header
        try:
            hdr_font = ImageFont.truetype("arial.ttf", 12)
        except (OSError, IOError):
            hdr_font = ImageFont.load_default()
        draw.text((panel_pad, strip_y0), 'per-stroke order:',
                  fill=(120, 120, 120), font=hdr_font)
        strip_y0 += 20
        # Common scale for the mini panels (all the same so shapes
        # compare directly).
        mini_scale = (panel_size - 14) / max(H, W)
        mini_w = W * mini_scale
        mini_h = H * mini_scale
        m_ox_offset = (panel_size - mini_w) / 2
        m_oy_offset = (panel_size - mini_h) / 2
        for i, (xs, ys, widths) in enumerate(traced):
            color = _stroke_color(i)
            col_i = i % cols
            row_i = i // cols
            px = panel_pad + col_i * (panel_size + panel_pad)
            py = strip_y0 + row_i * (panel_size + panel_pad)
            # Panel background.
            draw.rectangle([px, py, px + panel_size, py + panel_size],
                           fill=(255, 255, 255),
                           outline=(220, 220, 220), width=1)
            # Faint outlines in this panel.
            for poly in outlines:
                if len(poly) < 3:
                    continue
                pts = [(float(x) * mini_scale + px + m_ox_offset,
                        float(y) * mini_scale + py + m_oy_offset)
                       for x, y in poly]
                draw.polygon(pts, fill=(253, 230, 138, 100))
            # This stroke only.
            pts = [(float(x) * mini_scale + px + m_ox_offset,
                    float(y) * mini_scale + py + m_oy_offset)
                   for x, y in zip(xs, ys)]
            for a, b in zip(pts[:-1], pts[1:]):
                draw.line([a, b], fill=color, width=2)
            # Start marker.
            if pts:
                sx0, sy0 = pts[0]
                draw.ellipse([sx0 - 7, sy0 - 7, sx0 + 7, sy0 + 7],
                             fill=color, outline=(40, 40, 40), width=1)
                # Direction arrow at end.
                if len(pts) >= 6:
                    _draw_arrow(draw, pts[-6][0], pts[-6][1],
                                pts[-1][0], pts[-1][1], color, head_len=8)
            # Number label top-left of the panel.
            try:
                nfont = ImageFont.truetype("arial.ttf", 11)
            except (OSError, IOError):
                nfont = ImageFont.load_default()
            draw.text((px + 4, py + 2), f'{i + 1}',
                      fill=(100, 100, 100), font=nfont)

    # 5. Flipbook strip — cumulative animation at 5 time-points.
    # Lets us answer the real correctness question: "when this plays,
    # does it look like a person drawing?".
    if flipbook_frames:
        flip_y0 = canvas_size + strip_h + panel_pad
        draw.text((panel_pad, flip_y0), 'flipbook (animation frames):',
                  fill=(120, 120, 120), font=hdr_font)
        flip_y0 += 20
        # Treat each stroke as taking equal time (1/n of total).
        for fi, t in enumerate(flipbook_frames):
            px = panel_pad + fi * (panel_size + panel_pad)
            py = flip_y0
            draw.rectangle([px, py, px + panel_size, py + panel_size],
                           fill=(255, 255, 255),
                           outline=(220, 220, 220), width=1)
            # Faint outline of glyph for context
            for poly in outlines:
                if len(poly) < 3:
                    continue
                pts = [(float(x) * mini_scale + px + m_ox_offset,
                        float(y) * mini_scale + py + m_oy_offset)
                       for x, y in poly]
                draw.polygon(pts, fill=(253, 230, 138, 80))
            # Draw the cumulative stroke state at time t.
            # Stroke k is fully drawn by time (k+1)/n; partial in (k/n, (k+1)/n).
            for sk, (xs, ys, widths) in enumerate(traced):
                color = _stroke_color(sk)
                sk_start = sk / n
                sk_end = (sk + 1) / n
                if t <= sk_start:
                    continue
                if t >= sk_end:
                    progress = 1.0
                else:
                    progress = (t - sk_start) / (sk_end - sk_start)
                cut = max(1, int(round(progress * len(xs))))
                pts = [(float(x) * mini_scale + px + m_ox_offset,
                        float(y) * mini_scale + py + m_oy_offset)
                       for x, y in zip(xs[:cut], ys[:cut])]
                for a, b in zip(pts[:-1], pts[1:]):
                    draw.line([a, b], fill=color, width=2)
            # Time label top-left.
            draw.text((px + 4, py + 2), f'{int(t * 100)}%',
                      fill=(100, 100, 100), font=nfont)

    img.save(out_path)


def render_all_diagnostics(font_path, traced_per_letter, output_dir,
                           letters, size=384, filename_fn=None):
    """Render diagnostic PNGs for every letter into output_dir/diagnostics/.

    Args:
        font_path: TTF path (for re-rasterizing + outline extraction).
        traced_per_letter: dict char -> (mask, traced) or None.
        output_dir: trace output directory.
        letters: which letters to render.
        size: original rasterize_glyph size (for outline coords).
    """
    diag_dir = os.path.join(output_dir, 'diagnostics')
    os.makedirs(diag_dir, exist_ok=True)
    from penstroke.core.rasterize import rasterize_glyph

    for ch in letters:
        info = traced_per_letter.get(ch)
        if info is None:
            continue
        mask, traced = info
        try:
            outlines = extract_outlines(font_path, ch, size=size)
        except Exception:
            outlines = []
        if filename_fn is not None:
            fname = filename_fn(ch)
        else:
            fname = f'cap_{ch}.png' if ch.isupper() else f'{ch}.png'
        out_path = os.path.join(diag_dir, fname)
        try:
            render_letter_diagnostic(ch, mask, traced, outlines, out_path)
        except Exception:
            pass
    return diag_dir
