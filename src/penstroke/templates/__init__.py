"""penstroke.templates — Hershey-template-guided stroke decomposition.

This is the production tracing path. Instead of inferring stroke
decomposition from skeleton topology alone, we use the Hershey font
catalog as a per-letter prior: stroke count, stroke order, and rough
shape are all read from a Hershey template, then snapped onto the
target font's actual geometry.

  hershey.py     load and cache Hershey font data
  topology.py    fingerprint a skeleton / template for matching
  selection.py   per-letter strategy: which Hershey font to use
  trace.py       the main `trace_glyph(font_path, char)` entry point
"""
