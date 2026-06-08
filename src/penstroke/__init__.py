"""penstroke — convert TTF fonts into hand-drawn stroke decompositions.

Given a font (especially handwriting fonts from Google Fonts), penstroke
extracts each letter's centerline skeleton, decomposes it into individual
strokes using the Hershey font catalog as a template prior, then emits
the result as animated SVGs that mimic a pen drawing each letter.

Public entry point:
    >>> from penstroke import trace_font
    >>> trace_font("Caveat.ttf", output_dir="output/caveat/")

Library entry points (for one-letter use):
    >>> from penstroke.templates.trace import trace_glyph
    >>> mask, skel, dist, strokes, template, meta = trace_glyph(
    ...     "Caveat.ttf", "A")
"""

__version__ = "0.1.0"

# `trace_font` is imported lazily because it pulls in matplotlib transitively
# via render modules; we don't want `import penstroke.templates.trace` to
# trigger that for library users.
def trace_font(*args, **kwargs):
    from penstroke.pipeline import trace_font as _impl
    return _impl(*args, **kwargs)


__all__ = ["trace_font"]

