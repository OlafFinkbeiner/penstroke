"""Export traced strokes to JSON suitable for Houdini import.

Houdini reads JSON cleanly via Python SOPs, and the `pscale` (point scale)
attribute lets it sweep a curve at variable width without any extra work.
Per-point widths from the distance transform map directly onto `pscale`.

JSON schema:
{
  "font": "Caveat",
  "letter": "A",
  "canvas": {"w": 464, "h": 564},
  "baseline_y": 409,
  "advance_px": 251,
  "strokes": [
    {
      "points": [{"x": 73.1, "y": 145.2, "width": 12.4}, ...],
      "length": 234.5
    },
    ...
  ]
}
"""

import json
from penstroke.render.svg import path_length
from penstroke.core.smoothing import taper_profile


def trace_to_dict(letter, traced, meta, font_name='', apply_taper=True):
    """Convert one traced glyph into a JSON-ready dict.

    Args:
        letter: the character.
        traced: list of (xs, ys, widths) from trace_glyph.
        meta: font-metric dict from rasterize_glyph.
        font_name: optional human-readable font name to embed.
        apply_taper: if True, bake the taper profile into the per-point
            widths so the consumer doesn't have to. Recommended for
            Houdini import (pscale is used directly for sweep).

    Returns: dict matching the schema in the module docstring.
    """
    strokes = []
    for xs, ys, widths in traced:
        n = len(xs)
        if apply_taper:
            widths = widths * taper_profile(n)
        points = [
            {"x": float(x), "y": float(y), "width": float(w)}
            for x, y, w in zip(xs, ys, widths)
        ]
        strokes.append({
            "points": points,
            "length": path_length(xs, ys),
        })

    return {
        "font": font_name,
        "letter": letter,
        "canvas": {"w": meta['canvas_w'], "h": meta['canvas_h']},
        "baseline_y": meta['baseline_y'],
        "advance_px": meta['advance_px'],
        "ascent_px": meta['ascent_px'],
        "descent_px": meta['descent_px'],
        "strokes": strokes,
    }


def write_letter_json(letter, traced, meta, path, font_name=''):
    """Write a single letter's strokes to a JSON file."""
    data = trace_to_dict(letter, traced, meta, font_name=font_name)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
