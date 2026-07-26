"""Layout engine tests — pure python, no Houdini required.

    python tests/test_layout.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import numpy as np

from penstroke.layout import layout, get_font, shape

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'caveat.ttf')
EPS = 1e-6


def test_basic():
    out = layout('hello world', FIXTURE, size=1.0)
    assert len(out.names) == 10, out.names          # no space point
    assert 'space' not in out.names
    x = out.positions[:, 0]
    assert np.all(np.diff(x) > 0), 'x must increase along a line'
    assert out.n_lines == 1
    assert np.all(out.word[:5] == 0) and np.all(out.word[5:] == 1)
    print('✓ basic: 10 glyphs, monotone x, word indices')


def test_paragraphs():
    out = layout('ab\ncd', FIXTURE, size=2.0, line_height=1.5)
    assert out.n_lines == 2
    assert list(out.line) == [0, 0, 1, 1]
    y0 = out.positions[0, 1]
    y1 = out.positions[2, 1]
    assert abs((y0 - y1) - 2.0 * 1.5) < EPS, 'line height in world units'
    print('✓ paragraphs: explicit newline, line height scales')


def test_cluster_indices():
    out = layout('ab ba', FIXTURE)
    assert list(out.cluster) == [0, 1, 3, 4], list(out.cluster)
    out = layout('a\nb', FIXTURE)
    assert list(out.cluster) == [0, 2], 'newline counts in cluster index'
    print('✓ cluster: source char indices survive layout')


def test_char_in_word_and_index():
    out = layout('hello world', FIXTURE)
    # idx is a plain running glyph index in writing order.
    assert list(out.index) == list(range(10)), list(out.index)
    # char_in_word is the letter's position within its own word.
    assert list(out.char_in_word) == [0, 1, 2, 3, 4, 0, 1, 2, 3, 4], \
        list(out.char_in_word)
    # Across a line break: index stays global, char_in_word resets per word.
    out = layout('ab\ncd', FIXTURE)
    assert list(out.index) == [0, 1, 2, 3]
    assert list(out.word) == [0, 0, 1, 1]
    assert list(out.char_in_word) == [0, 1, 0, 1]
    print('✓ char_in_word resets per word; index is global writing order')


def test_wrapping():
    text = 'the quick brown fox jumps over the lazy dog'
    out = layout(text, FIXTURE, size=1.0, width=8.0)
    assert out.n_lines > 1, 'expected wrapping at width=8em'
    assert all(w <= 8.0 + EPS for w in out.line_widths), out.line_widths
    # Every word intact: word indices contiguous, 9 words total.
    assert out.word.max() == 8
    print(f'✓ wrapping: {out.n_lines} lines, all within width')


def test_justify():
    text = 'the quick brown fox jumps over the lazy dog'
    out = layout(text, FIXTURE, size=1.0, width=8.0, align='justify')
    for li in range(out.n_lines - 1):     # all but the last line
        assert abs(out.line_widths[li] - 8.0) < 1e-3, \
            f'line {li} not justified: {out.line_widths[li]}'
    assert out.line_widths[-1] <= 8.0 + EPS, 'last line stays ragged'
    print('✓ justify: wrapped lines fill the measure exactly')


def test_align_right():
    out = layout('hi\nthere world', FIXTURE, size=1.0, width=10.0,
                 align='right')
    for w in out.line_widths:
        assert abs(w - 10.0) < 1e-3, 'right-aligned lines end at width'
    print('✓ right align: lines end on the right edge')


def test_tracking():
    plain = layout('mm', FIXTURE)
    spread = layout('mm', FIXTURE, tracking=0.5)
    dx_plain = plain.positions[1, 0] - plain.positions[0, 0]
    dx_spread = spread.positions[1, 0] - spread.positions[0, 0]
    assert abs((dx_spread - dx_plain) - 0.5) < EPS
    print('✓ tracking: +0.5em per glyph')


def test_multiple_spaces():
    font = get_font(FIXTURE)
    _, sp = shape(font, ' ')
    sp_world = sp / font.upem            # one space advance at size 1.0
    b1 = layout('a b', FIXTURE).positions[1, 0]
    b2 = layout('a  b', FIXTURE).positions[1, 0]
    b3 = layout('a   b', FIXTURE).positions[1, 0]
    # Each extra space widens the inter-word gap by one space advance.
    assert abs((b2 - b1) - sp_world) < EPS, (b2 - b1, sp_world)
    assert abs((b3 - b1) - 2 * sp_world) < EPS, (b3 - b1, sp_world)
    print('✓ multiple spaces: each extra space widens the inter-word gap')


def test_caching():
    font = get_font(FIXTURE)
    a = shape(font, 'memoized')
    b = shape(font, 'memoized')
    assert a is b, 'shaped words must be memoized'
    assert get_font(FIXTURE) is font, 'font cached by (path, mtime)'
    print('✓ caching: font and shaped words memoized')


def test_glyph_names_match_hfont_keying():
    out = layout('Ab!', FIXTURE)
    assert out.names == ['A', 'b', 'exclam'], out.names
    print('✓ keying: post glyph names, matching hfont reps')


if __name__ == '__main__':
    test_basic()
    test_paragraphs()
    test_cluster_indices()
    test_char_in_word_and_index()
    test_wrapping()
    test_justify()
    test_align_right()
    test_tracking()
    test_multiple_spaces()
    test_caching()
    test_glyph_names_match_hfont_keying()
    print('\nAll layout tests passed.')
