"""Build a single SVG that shows all letters as a grid with synchronized
sequential animation: the alphabet writes itself letter by letter.

The grid is purely a viewer convenience — each letter is positioned in a
fixed-size cell, and the per-letter animations are sequenced so the
viewer can watch the whole alphabet being drawn.

For typesetting actual words from these letters, use `render/word.py`
instead (it uses the per-letter advance widths).
"""

import numpy as np
from skimage import measure
from penstroke.core.smoothing import taper_profile
from penstroke.render.svg import (
    stroke_to_ribbon_path, stroke_to_centerline_path, path_length,
)


def _outlines_to_svg_path(outlines):
    """Build an SVG path string from a list of vector outline polygons.

    Each polygon is an Nx2 array of (x, y) canvas pixel coordinates from
    `core.outline.extract_outlines`. Uses the even-odd fill rule so
    counters (hole inside 'o', 'B', etc.) render as background.
    """
    parts = []
    for poly in outlines:
        if poly is None or len(poly) < 3:
            continue
        pts = ' '.join(f'{p[0]:.1f},{p[1]:.1f}' for p in poly)
        parts.append(f'M{pts}Z')
    return ''.join(parts)


def _mask_to_svg_path(mask):
    """Fallback: outline from rasterized mask via marching squares.

    Used when no vector outline is available (e.g. extraction failed).
    Cusps and serifs get rounded off because the mask is antialiased.
    """
    if mask.sum() == 0:
        return ''
    contours = measure.find_contours(mask.astype(float), 0.5)
    parts = []
    for c in contours:
        if len(c) < 3:
            continue
        pts = ' '.join(f'{p[1]:.1f},{p[0]:.1f}' for p in c)
        parts.append(f'M{pts}Z')
    return ''.join(parts)


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
        f'.underlay {{ fill: #fde68a; fill-rule: evenodd; opacity: 0.7; }}',
        '.label { font-size: 11px; fill: #888; text-anchor: middle; }',
        '.cell-bg { fill: white; stroke: #eee; stroke-width: 1; rx: 6; }',
        '</style></defs>',
    ]

    for idx, (item, gstart, (starts, durs)) in enumerate(
            zip(items, glyph_starts, glyph_timelines)):
        label, traced, mask, outlines, _picked, _meta = item
        H, W = mask.shape
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

        svg.append(f'<rect class="cell-bg" data-ch="{ord(label):04x}" '
                   f'x="{ox}" y="{oy}" '
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
        # Prefer crisp vector outlines from the TTF; fall back to mask
        # contours if extraction failed (non-Latin scripts mostly).
        underlay_d = _outlines_to_svg_path(outlines) if outlines else _mask_to_svg_path(mask)
        if underlay_d:
            svg.append(f'<path class="underlay" d="{underlay_d}" />')
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
                    f'stroke-dasharray="{L:.2f}" stroke-dashoffset="{L:.2f}" '
                    f'stroke-opacity="0">'
                    f'<set attributeName="stroke-opacity" to="0" '
                    f'begin="loopAnim.begin" />'
                    f'<set attributeName="stroke-opacity" to="1" '
                    f'begin="loopAnim.begin+{abs_start:.3f}s" fill="freeze" />'
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


def make_preview_html(animated_svg, font_name=''):
    """Wrap an animated alphabet SVG in the interactive viewer template.

    The viewer provides play/pause, restart, speed (0.1x-5x), time scrub,
    and a wireframe toggle that hides ribbons to show only the centerline.
    """
    from importlib.resources import files
    template = files('penstroke.render').joinpath('viewer_template.html').read_text(encoding='utf-8')
    if font_name:
        template = template.replace(
            'Caveat Alphabet — Pen Traced',
            f'{font_name} Alphabet — Pen Traced',
        )
    return template.replace('__SVG_GOES_HERE__', animated_svg)
