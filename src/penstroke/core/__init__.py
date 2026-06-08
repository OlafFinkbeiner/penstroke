"""penstroke.core — primitives shared by every tracing strategy.

  rasterize.py  TTF → fixed-canvas mask + font metrics
  skeleton.py   mask → centerline + distance transform
  graph.py      skeleton → graph; junction cleanup; parallel-edge collapse
  strokes.py    geometric stroke decomposition (template-free fallback)
  smoothing.py  spline + per-point widths + wobble + taper
"""
