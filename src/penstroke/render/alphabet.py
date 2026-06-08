"""Build a single SVG that shows all letters as a grid with synchronized
sequential animation: the alphabet writes itself letter by letter.

The grid is purely a viewer convenience — each letter is positioned in a
fixed-size cell, and the per-letter animations are sequenced so the
viewer can watch the whole alphabet being drawn.

For typesetting actual words from these letters, use `render/word.py`
instead (it uses the per-letter advance widths).
"""

import numpy as np
from penstroke.core.smoothing import taper_profile
from penstroke.render.svg import (
    stroke_to_ribbon_path, stroke_to_centerline_path, path_length,
)


_GAP_BETWEEN_STROKES = 0.12
_GAP_BETWEEN_LETTERS = 0.2


def _per_glyph_timings(items):
    """Compute per-stroke timings and total duration for each glyph.

    Returns (per_glyph_timeline, glyph_durations) where
    per_glyph_timeline[i] = (starts, durs) for that glyph.
    """
    timelines = []
    durations = []
    for item in items:
        traced = item[1]
        lengths = [path_length(xs, ys) for xs, ys, _ in traced]
        total_len = sum(lengths) or 1
        # Slightly longer per glyph than single-letter SVGs, for visibility
        glyph_time = max(0.6, 2.5 * (sum(lengths) ** 0.5) / 30)
        n_strokes = len(traced)
        gap_time = _GAP_BETWEEN_STROKES * max(0, n_strokes - 1)
        drawing_time = max(0.4, glyph_time - gap_time)
        durs = [drawing_time * (L / total_len) for L in lengths]
        sts: list[float] = []
        t = 0.0
        for d in durs:
            sts.append(t)
            t += d + _GAP_BETWEEN_STROKES
        timelines.append((sts, durs))
        durations.append(t)
    return timelines, durations


def build_alphabet_svg(items, cols=7, glyph_size=180, gap=20,
                       animate=True, ink_color='#1a1a2e'):
    """Render a grid of letters as one big SVG.

    Args:
        items: list of (label, traced, mask_shape, template_used, meta)
            tuples from the tracing pipeline.
        cols: grid columns.
        glyph_size: pixel size of each cell.
        gap: pixel gap between cells.
        animate: if True, sequence the per-letter animations so each
            letter draws in turn.
        ink_color: stroke color.

    Returns:
        (svg_text, total_anim_duration). The duration is useful for the
        HTML wrapper to know when the animation completes.
    """
    n = len(items)
    rows = (n + cols - 1) // cols
    cell_w = glyph_size + gap
    cell_h = glyph_size + gap + 24   # extra row height for the per-cell label
    total_w = cols * cell_w + gap
    total_h = rows * cell_h + gap

    glyph_timelines, glyph_durations = _per_glyph_timings(items)

    # Sequential schedule: glyph i+1 begins after glyph i finishes + small gap.
    glyph_starts = [0.0]
    for d in glyph_durations[:-1]:
        glyph_starts.append(glyph_starts[-1] + d + _GAP_BETWEEN_LETTERS)
    total_anim = glyph_starts[-1] + glyph_durations[-1] + 0.5

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {total_w} {total_h}" '
        f'width="{total_w}" height="{total_h}" '
        f'style="background:#fafafa; font-family: system-ui, sans-serif">',
        '<defs><style>',
        f'.ribbon {{ fill: {ink_color}; opacity: 0; }}',
        f'.guide  {{ fill: none; stroke: {ink_color}; '
        f'stroke-linecap: round; stroke-linejoin: round; }}',
        '.label { font-size: 11px; fill: #888; text-anchor: middle; }',
        '.cell-bg { fill: white; stroke: #eee; stroke-width: 1; rx: 6; }',
        '</style></defs>',
    ]

    for idx, (item, gstart, (starts, durs)) in enumerate(
            zip(items, glyph_starts, glyph_timelines)):
        label, traced, (H, W), _picked, _meta = item
        row = idx // cols
        col = idx % cols
        ox = gap + col * cell_w
        oy = gap + row * cell_h

        # Fit the glyph into the cell uniformly, centered
        scale = (glyph_size - 16) / max(H, W)
        glyph_w = W * scale
        glyph_h = H * scale
        gx = ox + (glyph_size - glyph_w) / 2
        gy = oy + (glyph_size - glyph_h) / 2

        svg.append(f'<rect class="cell-bg" x="{ox}" y="{oy}" '
                   f'width="{glyph_size}" height="{glyph_size}" />')
        # XML-escape the label since special chars like '&', '<', '>'
        # would otherwise be parsed as markup
        label_escaped = (label
                         .replace('&', '&amp;')
                         .replace('<', '&lt;')
                         .replace('>', '&gt;')
                         .replace('"', '&quot;')
                         .replace("'", '&apos;'))
        svg.append(f'<text class="label" x="{ox + glyph_size / 2}" '
                   f'y="{oy + glyph_size + 16}">{label_escaped}</text>')

        svg.append(f'<g transform="translate({gx:.2f},{gy:.2f}) scale({scale:.4f})">')
        for (xs, ys, widths), start, dur in zip(traced, starts, durs):
            ribbon_d = stroke_to_ribbon_path(xs, ys, widths)
            center_d = stroke_to_centerline_path(xs, ys)
            L = path_length(xs, ys)
            avg_w = float(np.mean(widths * taper_profile(len(xs))))
            abs_start = gstart + start
            if animate:
                svg.append(
                    f'<path class="guide" d="{center_d}" '
                    f'stroke-width="{max(1.2, avg_w * 0.8):.2f}" '
                    f'stroke-dasharray="{L:.2f}" stroke-dashoffset="{L:.2f}">'
                    f'<animate attributeName="stroke-dashoffset" '
                    f'from="{L:.2f}" to="0" '
                    f'begin="loopAnim.begin+{abs_start:.3f}s" '
                    f'dur="{dur:.3f}s" fill="freeze" />'
                    f'</path>'
                )
                svg.append(
                    f'<path class="ribbon" d="{ribbon_d}">'
                    f'<animate attributeName="opacity" from="0" to="1" '
                    f'begin="loopAnim.begin+{abs_start + dur * 0.55:.3f}s" '
                    f'dur="{dur * 0.45:.3f}s" fill="freeze" />'
                    f'</path>'
                )
            else:
                svg.append(f'<path class="ribbon" style="opacity:1" d="{ribbon_d}" />')
        svg.append('</g>')

    if animate:
        svg.append(
            f'<g id="loop-anchor" opacity="0">'
            f'<animate id="loopAnim" attributeName="opacity" '
            f'from="0" to="0" begin="0s;loopAnim.end" '
            f'dur="{total_anim + 2.0:.2f}s" />'
            f'</g>'
        )
    svg.append('</svg>')
    return "\n".join(svg), total_anim
