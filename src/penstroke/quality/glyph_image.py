"""Render a clean per-glyph PNG (no animation, no overlay, no decoration).

These images are the input to the AI spec extractor (`quality/spec.py`).
A vision model looks at one PNG per letter and returns a structured
per-letter spec — how many strokes / dots / what features must be
covered — which the tracer then uses as a prior.

Kept deliberately minimal: just the rasterized mask of the glyph in
black on a white background, padded to a square canvas.
"""

import os
import numpy as np
from PIL import Image

from penstroke.core.rasterize import rasterize_glyph


def write_raw_glyphs(font_path, output_dir, letters, size=256, filename_fn=None):
    """Render plain glyph PNGs into output_dir/glyphs_raw/.

    Each PNG is the rasterized glyph mask, inverted to black ink on white
    so it reads as a normal letter image. Used as input to the AI spec
    extractor — see quality/spec.py.

    Args:
        font_path: path to the TTF.
        output_dir: trace output directory; glyphs_raw/ is created inside.
        letters: iterable of characters to render.
        size: pixel size for rasterization (256 is a sweet spot for vision
            models — large enough to see serifs, small enough to be cheap).
        filename_fn: callable char -> filename. Defaults to the pipeline's
            cap_X / x convention.

    Returns:
        path to the glyphs_raw directory.
    """
    raw_dir = os.path.join(output_dir, 'glyphs_raw')
    os.makedirs(raw_dir, exist_ok=True)

    if filename_fn is None:
        def filename_fn(ch):
            base = f'cap_{ch}' if ch.isupper() else ch
            return f'{base}.png'

    for ch in letters:
        try:
            mask, _meta = rasterize_glyph(font_path, ch, size=size)
        except Exception:
            continue
        # Invert mask: ink (1) -> black (0), background (0) -> white (255)
        img = Image.fromarray(((1 - mask) * 255).astype(np.uint8), mode='L')
        img.save(os.path.join(raw_dir, filename_fn(ch)))

    return raw_dir
