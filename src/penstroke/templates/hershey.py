"""Load and cache Hershey font stroke templates.

Hershey fonts (designed in the 1960s by A.V. Hershey at the US Naval
Weapons Laboratory) are public-domain *stroke* fonts — they're stored as
ordered lists of polylines, not as outline shapes. That makes them
unusually useful as priors for stroke decomposition: for each Latin
character, Hershey already tells us how many strokes there are, in what
order, and roughly where each stroke goes.

We use the Python `HersheyFonts` package, which ships several Hershey
variants. The ones relevant for us:
  rowmans   single-stroke Roman/serif (the standard reference)
  rowmand   duplex Roman (two strokes per "stroke" — too detailed)
  futural   single-stroke sans-serif
  cursive   single-stroke handwriting-style (single-story 'a' etc.)
  scripts   single-stroke script (cursive variant)

`get_template(char, font)` returns the strokes as `[[(x, y), ...], ...]`
plus the glyph's bounding box, which is what the matching code needs.
"""

from HersheyFonts import HersheyFonts


# Module-level cache: HersheyFonts.load_default_font() reads & parses a
# bundled .jhf file every time it's called, so we share one instance per
# font name across the process.
_cache: dict[str, HersheyFonts] = {}


def load_hershey_font(name: str) -> HersheyFonts:
    """Get a HersheyFonts instance for `name`, caching across calls."""
    if name not in _cache:
        hf = HersheyFonts()
        hf.load_default_font(name)
        _cache[name] = hf
    return _cache[name]


def get_template(char: str, font_name: str = 'rowmans'):
    """Retrieve a single character's stroke decomposition from a Hershey font.

    Args:
        char: the character to look up.
        font_name: which Hershey variant to use.

    Returns:
        (strokes, bbox) or None if the character is not in this font.
        strokes is a list of polylines: [[(x, y), (x, y), ...], ...] in
        Hershey-internal coordinates. bbox is (xmin, ymin, xmax, ymax)
        for the glyph in those same coordinates.
    """
    hf = load_hershey_font(font_name)
    # The HersheyFonts package stores glyphs in a private dict keyed by char.
    glyphs = hf._HersheyFonts__glyphs
    if char not in glyphs:
        return None

    glyph = glyphs[char]
    raw_strokes = glyph._HersheyGlyph__strokes
    if not raw_strokes:
        return None

    out = []
    for stroke in raw_strokes:
        if len(stroke) < 2:
            continue
        out.append([(float(p[0]), float(p[1])) for p in stroke])
    if not out:
        return None

    bbox = (glyph._HersheyGlyph__xmin, glyph._HersheyGlyph__ymin,
            glyph._HersheyGlyph__xmax, glyph._HersheyGlyph__ymax)
    return out, bbox
