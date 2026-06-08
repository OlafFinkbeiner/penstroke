"""Rasterize a TTF glyph onto a fixed canvas with consistent baseline.

Every letter drawn by this module lands on a canvas of the same dimensions
with the baseline at the same Y coordinate. This is essential downstream:
the per-letter SVGs can be composed into words because they share a
coordinate frame.

The function returns both the raster mask AND a metadata dict with the
font metrics needed to typeset the result (advance width, baseline_y,
ascent/descent in pixels).
"""

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from fontTools.ttLib import TTFont


def rasterize_glyph(font_path, char, size=512, pad=40):
    """Rasterize a glyph onto a fixed-size canvas at a fixed baseline.

    Args:
        font_path: path to a TTF/OTF file.
        char: single character to render.
        size: nominal em-square height in pixels. Each glyph uses this as
              the font's pixel size; the canvas itself is taller (ascent +
              descent + pad) to fit any glyph from that font.
        pad: pixel margin around the em box.

    Returns:
        (mask, metadata) where mask is a uint8 binary array (1 = ink) and
        metadata is a dict with keys:
            canvas_w, canvas_h, baseline_y, pad,
            advance_px, ascent_px, descent_px, lsb_px,
            upem, px_per_em

    The metadata is what lets you compose multiple rasterized glyphs into a
    line of text — `baseline_y` is the same across letters, and `advance_px`
    tells you how far to step before the next letter.
    """
    # Read font metrics from the TTF tables directly. PIL's ImageFont
    # only exposes a subset, so we go to fontTools for the authoritative numbers.
    tt = TTFont(font_path)
    upem = tt['head'].unitsPerEm
    hhea = tt['hhea']
    ascent_em = hhea.ascent       # font units above baseline
    descent_em = -hhea.descent    # font units below (descent is stored negative)

    cmap = tt.getBestCmap()
    if ord(char) not in cmap:
        raise ValueError(f"glyph {char!r} not in font {font_path}")
    glyph_name = cmap[ord(char)]
    advance_em, lsb_em = tt['hmtx'][glyph_name]

    # Convert font units to pixels using `size` as the em-square height.
    px_per_em = size / upem
    ascent_px = int(round(ascent_em * px_per_em))
    descent_px = int(round(descent_em * px_per_em))
    advance_px = int(round(advance_em * px_per_em))

    # Canvas large enough for any glyph: full ascent + descent + margins.
    # Width is at least the advance, and at least `size` so wide glyphs fit.
    canvas_h = ascent_px + descent_px + 2 * pad
    canvas_w = max(advance_px, size) + 2 * pad

    img = Image.new("L", (canvas_w, canvas_h), 0)
    draw = ImageDraw.Draw(img)
    pil_font = ImageFont.truetype(font_path, size)

    # PIL anchors text at the top-left of its EM box. We want the baseline at
    # a *fixed* Y (= pad + ascent_px) regardless of which glyph we're drawing,
    # so we compute the EM-top accordingly.
    baseline_y = pad + ascent_px
    try:
        pil_ascent, _ = pil_font.getmetrics()
    except AttributeError:
        pil_ascent = ascent_px
    text_y = baseline_y - pil_ascent
    text_x = pad  # leave room on the left of the glyph

    draw.text((text_x, text_y), char, fill=255, font=pil_font)
    arr = np.array(img)
    mask = (arr > 127).astype(np.uint8)

    metadata = {
        'canvas_w': canvas_w,
        'canvas_h': canvas_h,
        'baseline_y': baseline_y,
        'pad': pad,
        'advance_px': advance_px,
        'ascent_px': ascent_px,
        'descent_px': descent_px,
        'lsb_px': int(round(lsb_em * px_per_em)),
        'upem': upem,
        'px_per_em': px_per_em,
    }
    return mask, metadata
