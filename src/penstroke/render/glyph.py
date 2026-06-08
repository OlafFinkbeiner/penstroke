"""Build a standalone SVG for one letter, with metadata for word composition.

Each SVG is rendered onto the FULL canvas (ascender + descender + pad)
rather than cropped to the inked region. The baseline lands at the same
Y coordinate across all letters of a font, and the SVG carries data
attributes describing its font-metric position so word composition is
trivial: stack the SVGs horizontally at offsets driven by advance_px.

Data attributes on the root <svg>:
    data-baseline   Y of the baseline within the canvas
    data-pad        margin on all sides
    data-advance    horizontal step for the next letter
    data-ascent     pixels above baseline
    data-descent    pixels below baseline
"""

import numpy as np
from penstroke.core.smoothing import taper_profile
from penstroke.render.svg import (
    stroke_to_ribbon_path, stroke_to_centerline_path, path_length,
)


# Animation timing: gap between strokes, total duration scales with stroke
# count so longer letters animate longer.
_GAP_BETWEEN_STROKES = 0.12


def _compute_timings(traced):
    """Per-stroke (start, duration) pairs for one glyph's animation.

    Stroke durations scale with arc length so the pen-tip "speed" is
    roughly constant.
    """
    lengths = [path_length(xs, ys) for xs, ys, _ in traced]
    total_len = sum(lengths) or 1
    glyph_time = max(0.5, 0.06 * sum(lengths) ** 0.5)
    n_strokes = len(traced)
    gap_time = _GAP_BETWEEN_STROKES * max(0, n_strokes - 1)
    drawing_time = max(0.4, glyph_time - gap_time)
    durs = [drawing_time * (L / total_len) for L in lengths]
    starts: list[float] = []
    t = 0.0
    for d in durs:
        starts.append(t)
        t += d + _GAP_BETWEEN_STROKES
    return starts, durs, t  # last value = total animation length


def make_glyph_svg(traced, meta, animate=True, ink_color='#1a1a2e'):
    """Build a standalone SVG for one letter.

    Args:
        traced: list of (xs, ys, widths) per stroke from trace_glyph.
        meta: font-metric dict from rasterize_glyph (or trace_glyph).
        animate: if True, include SMIL animations that play the letter
            being drawn stroke by stroke, then loop indefinitely.
        ink_color: CSS color string for the rendered strokes.

    Returns:
        SVG document as a string.
    """
    canvas_w = meta['canvas_w']
    canvas_h = meta['canvas_h']
    starts, durs, total_anim = _compute_timings(traced)

    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {canvas_w} {canvas_h}" '
        f'width="{canvas_w}" height="{canvas_h}" '
        # Position metadata for downstream word composition
        f'data-baseline="{meta["baseline_y"]}" '
        f'data-pad="{meta["pad"]}" '
        f'data-advance="{meta["advance_px"]}" '
        f'data-ascent="{meta["ascent_px"]}" '
        f'data-descent="{meta["descent_px"]}" '
        f'preserveAspectRatio="xMinYMin meet">',
        '<defs><style>',
        f'.ribbon {{ fill: {ink_color}; opacity: 0; }}',
        f'.guide  {{ fill: none; stroke: {ink_color}; '
        f'stroke-linecap: round; stroke-linejoin: round; }}',
        '</style></defs>',
    ]

    for (xs, ys, widths), start, dur in zip(traced, starts, durs):
        ribbon_d = stroke_to_ribbon_path(xs, ys, widths)
        center_d = stroke_to_centerline_path(xs, ys)
        L = path_length(xs, ys)
        avg_w = float(np.mean(widths * taper_profile(len(xs))))

        if animate:
            # Two-layer animation: guide path "draws in" via stroke-dasharray
            # decreasing offset; ribbon fades in after the guide is partially
            # drawn so it looks like the ink settles behind the pen tip.
            # Guide width is 80% of the filled width — close enough that
            # wireframe mode (which hides the ribbon) looks visually consistent
            # with the original letterform, just rendered as a path.
            guide_w = max(1.2, avg_w * 0.8)
            svg.append(
                f'<path class="guide" d="{center_d}" '
                f'stroke-width="{guide_w:.2f}" '
                f'stroke-dasharray="{L:.2f}" '
                f'stroke-dashoffset="{L:.2f}">'
                f'<animate attributeName="stroke-dashoffset" '
                f'from="{L:.2f}" to="0" '
                f'begin="loopAnim.begin+{start:.3f}s" '
                f'dur="{dur:.3f}s" fill="freeze" />'
                f'</path>'
            )
            svg.append(
                f'<path class="ribbon" d="{ribbon_d}">'
                f'<animate attributeName="opacity" from="0" to="1" '
                f'begin="loopAnim.begin+{start + dur * 0.55:.3f}s" '
                f'dur="{dur * 0.45:.3f}s" fill="freeze" />'
                f'</path>'
            )
        else:
            svg.append(f'<path class="ribbon" style="opacity:1" d="{ribbon_d}" />')

    if animate:
        # Hidden element whose end event re-fires its own begin: makes the
        # whole animation loop indefinitely without JavaScript.
        svg.append(
            f'<g opacity="0">'
            f'<animate id="loopAnim" attributeName="opacity" '
            f'from="0" to="0" begin="0s;loopAnim.end" '
            f'dur="{total_anim + 0.8:.2f}s" />'
            f'</g>'
        )
    svg.append('</svg>')
    return "\n".join(svg)
