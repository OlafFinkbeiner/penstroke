"""Text layout engine: string -> positioned glyphs. Pure module, no hou.

Shaping (kerning, OpenType features) is HarfBuzz via uharfbuzz —
metrics and kerning are read live from the TTF, never extracted.
Layout (line breaking, alignment, justification) is ours: greedy
breaker over shaped word widths; justification distributes the line's
slack across word gaps. v1 shapes with liga/calt OFF so chars map 1:1
to glyphs (matches hfont reps keyed by post glyph name through cmap);
v2 flips the features on once reps are traced per glyph.

Coordinates: x right, y up, baseline of the first line at y=0,
subsequent lines at negative y. All outputs in world units where
`size` = one em. Whitespace produces no glyph records, only advance.

Performance contract (this module is what a Houdini SOP calls per
cook): fonts are cached by (path, mtime), shaped words are memoized,
and results come back as flat numpy arrays so the SOP can use batch
attribute writes. Re-layout of edited text costs zero re-shaping for
unchanged words.
"""

import os
import re
from dataclasses import dataclass, field

import numpy as np

DEFAULT_FEATURES = {'liga': False, 'calt': False}
_WORD_RE = re.compile(r'\S+')

# Glyph names that are pure whitespace if they ever come out of
# shaping a word (defensive — words are split on whitespace already).
_SPACE_NAMES = frozenset(('space', 'nbspace', 'uni00A0'))

_FONT_CACHE = {}
_WORD_CACHE = {}


class _Font:
    """Cached HarfBuzz font + the bits of metadata layout needs."""

    def __init__(self, path):
        import uharfbuzz as hb
        self.path = os.path.abspath(path)
        blob = hb.Blob.from_file_path(self.path)
        face = hb.Face(blob)
        self.hb_font = hb.Font(face)
        self.upem = face.upem
        self.key = (self.path, os.path.getmtime(self.path))


def get_font(path):
    """Font handle cached by (abspath, mtime)."""
    key = (os.path.abspath(path), os.path.getmtime(path))
    font = _FONT_CACHE.get(key)
    if font is None:
        font = _Font(path)
        _FONT_CACHE[key] = font
        # A changed mtime for the same path leaves a stale entry
        # behind; harmless (bounded by font count, not cook count).
    return font


def shape(font, text, features=None):
    """Shape a string. Returns (records, advance) in FONT UNITS.

    records: [(glyph_name, x_off, y_off, x_adv, cluster), ...]
    """
    import uharfbuzz as hb
    feats = DEFAULT_FEATURES if features is None else features
    cache_key = (font.key, text, tuple(sorted(feats.items())))
    hit = _WORD_CACHE.get(cache_key)
    if hit is not None:
        return hit
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font.hb_font, buf, feats)
    records = []
    advance = 0.0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        gname = font.hb_font.glyph_to_string(info.codepoint)
        records.append((gname, pos.x_offset, pos.y_offset,
                        pos.x_advance, info.cluster))
        advance += pos.x_advance
    result = (tuple(records), advance)
    _WORD_CACHE[cache_key] = result
    return result


@dataclass
class Layout:
    """Flat per-glyph arrays, ready for batch attribute writes."""
    names: list                 # glyph name per emitted glyph
    positions: np.ndarray       # (N, 2) world units, baseline origins
    line: np.ndarray            # (N,) int line index
    word: np.ndarray            # (N,) int word index (global)
    cluster: np.ndarray         # (N,) int char index in source text
    char_in_word: np.ndarray    # (N,) int letter index within its word
    index: np.ndarray           # (N,) int running glyph index (writing order)
    size: float                 # world units per em (uniform pscale)
    n_lines: int = 0
    line_widths: list = field(default_factory=list)   # world units


def layout(text, font_path, size=1.0, width=None, align='left',
           tracking=0.0, line_height=1.2, features=None):
    """Lay out text. Returns a Layout.

    Args:
        text: the string; '\\n' separates paragraphs.
        font_path: TTF to shape against (an hfont bundle's font.ttf).
        size: world units per em.
        width: wrap width in world units; None = no wrapping
            (paragraphs stay single lines).
        align: 'left' | 'center' | 'right' | 'justify'. Justify
            applies to wrapped lines only — the last line of each
            paragraph (and single-word lines) falls back to left.
        tracking: extra advance per glyph, in em.
        line_height: baseline-to-baseline distance, in em.
        features: OpenType feature dict (default: liga/calt off).
    """
    if align not in ('left', 'center', 'right', 'justify'):
        raise ValueError(f'unknown align {align!r}')
    font = get_font(font_path)
    scale = size / font.upem
    track_fu = tracking * font.upem    # tracking in font units

    # --- collect lines
    # Each line: (words, is_last_of_paragraph, para_char_base) where
    # words = [(char_offset_in_paragraph, records, advance_fu,
    #           gap_before_fu), ...]. gap_before is the width of the
    # actual whitespace run preceding the word, so runs of spaces (or
    # tabs) are honored rather than collapsed to a single space.
    # para_char_base lifts word offsets to text-global cluster indices.
    lines = []
    char_base = 0
    for para in text.split('\n'):
        words = []
        prev_end = 0
        for m in _WORD_RE.finditer(para):
            gap_text = para[prev_end:m.start()]
            if gap_text:
                _, gap_adv = shape(font, gap_text, features)
                gap_adv += track_fu * len(gap_text)
            else:
                gap_adv = 0.0
            records, adv = shape(font, m.group(), features)
            adv += track_fu * len(records)
            words.append((m.start(), records, adv, gap_adv))
            prev_end = m.end()
        if not words:
            lines.append(([], True, char_base))      # blank line
        elif width is None:
            lines.append((words, True, char_base))
        else:
            width_fu = width / scale
            current = []
            run = 0.0
            for w in words:
                w_adv = w[2]
                gap = w[3]
                needed = run + (gap if current else 0.0) + w_adv
                if current and needed > width_fu:
                    lines.append((current, False, char_base))
                    current, run = [w], w_adv
                else:
                    current.append(w)
                    run = needed
            lines.append((current, True, char_base))
        char_base += len(para) + 1   # +1 for the '\n'

    # --- emit
    names = []
    pos = []
    line_idx = []
    word_idx = []
    cluster = []
    char_in_word = []
    line_widths = []
    word_counter = -1
    y = 0.0
    for li, (wlist, is_last, pbase) in enumerate(lines):
        # Inter-word gaps come from the source whitespace (word[3]); the
        # first word on a line has no leading gap.
        gaps_fu = sum(w[3] for w in wlist[1:])
        total_fu = sum(w[2] for w in wlist) + gaps_fu
        extra_gap = 0.0       # even slack added per gap when justifying
        x_fu = 0.0
        if width is not None:
            width_fu = width / scale
            slack = width_fu - total_fu
            if align == 'justify' and not is_last and len(wlist) > 1:
                extra_gap = slack / (len(wlist) - 1)
            elif align == 'center':
                x_fu = slack / 2.0
            elif align == 'right':
                x_fu = slack
        for wi, (char_off, records, w_adv, gap_before) in enumerate(wlist):
            if wi > 0:
                x_fu += gap_before + extra_gap
            word_counter += 1
            giw = 0                       # letter index within this word
            for (gname, x_off, y_off, x_adv, cl) in records:
                if gname not in _SPACE_NAMES:
                    names.append(gname)
                    pos.append(((x_fu + x_off) * scale,
                                y + y_off * scale))
                    line_idx.append(li)
                    word_idx.append(word_counter)
                    cluster.append(pbase + char_off + cl)
                    char_in_word.append(giw)
                    giw += 1
                x_fu += x_adv + track_fu
        line_widths.append(max(0.0, x_fu) * scale if wlist else 0.0)
        y -= line_height * size

    return Layout(
        names=names,
        positions=np.asarray(pos, dtype=float).reshape(-1, 2),
        line=np.asarray(line_idx, dtype=int),
        word=np.asarray(word_idx, dtype=int),
        cluster=np.asarray(cluster, dtype=int),
        char_in_word=np.asarray(char_in_word, dtype=int),
        index=np.arange(len(names), dtype=int),
        size=size,
        n_lines=len(lines),
        line_widths=line_widths,
    )
