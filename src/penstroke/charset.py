"""Charset presets: which characters of a font to process.

Deliberately dependency-light (stdlib + fontTools only) so it imports
under Houdini's hython, where penstroke's heavy deps (scipy,
scikit-image) are not installed. editround re-exports these names for
backwards compatibility.
"""

import unicodedata


# Unicode block ranges per preset, intersected with whatever the
# font's cmap actually carries. None = no range filter (full cmap).
CHARSETS = {
    'ascii': [(0x21, 0x7E)],
    'latin': [(0x21, 0x7E), (0xA1, 0xFF)],
    'all': None,
}


def font_charset(ttf_path, charset='latin'):
    """Drawable characters in the font's cmap for a named charset preset.

    Control characters, whitespace, and combining marks are skipped
    (combining marks have no standalone letterform). Glyphs that
    rasterize to nothing are filtered later by the trace loop itself.
    """
    from fontTools.ttLib import TTFont
    if charset not in CHARSETS:
        raise ValueError(f'unknown charset {charset!r}; '
                         f'options: {", ".join(CHARSETS)}')
    ranges = CHARSETS[charset]
    tt = TTFont(ttf_path)
    cmap = tt.getBestCmap()
    chars = []
    for cp in sorted(cmap):
        ch = chr(cp)
        if ch.isspace():
            continue
        cat = unicodedata.category(ch)
        if cat.startswith('C'):
            continue
        if cat == 'Mn':
            continue   # combining marks: no standalone letterform
        if ranges is not None and not any(a <= cp <= b for a, b in ranges):
            continue
        chars.append(ch)
    return ''.join(chars)
