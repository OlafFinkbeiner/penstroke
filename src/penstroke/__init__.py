"""penstroke — convert TTF fonts into hand-drawn stroke decompositions.

Given a font (especially handwriting fonts from Google Fonts), penstroke
extracts each letter's centerline skeleton, decomposes it into individual
pen strokes via junction-first graph analysis (see penstroke.tracer), then
emits the result as animated SVGs that mimic a pen drawing each letter.

Public entry point:
    >>> from penstroke import trace_font
    >>> trace_font("Caveat.ttf", output_dir="output/caveat/")

Library entry point (for one-letter use):
    >>> from penstroke.tracer import trace_glyph_eulerian
    >>> mask, skel, dist, strokes, tracer_name, meta = trace_glyph_eulerian(
    ...     "Caveat.ttf", "A")
"""

__version__ = "0.2.0"

# `trace_font` is imported lazily so that `import penstroke` stays cheap
# for library users who only want the one-letter tracer.
def trace_font(*args, **kwargs):
    from penstroke.pipeline import trace_font as _impl
    return _impl(*args, **kwargs)


__all__ = ["trace_font"]
