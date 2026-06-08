"""High-level pipeline: trace a single font into a fully-populated output folder.

`trace_font(ttf_path, output_dir)` produces the following directory layout:

    output_dir/
        source/
            <font_name>.ttf       # copy of the original font
            LICENSE.txt           # if `license_text` is supplied
        glyphs/
            a.svg ... z.svg       # per-letter animated SVGs
            cap_A.svg ... cap_Z.svg
        alphabet_animated.svg     # all 52 letters in a grid, sequenced
        alphabet_static.svg       # same grid, no animation
        word_demo.html            # "hello world" composition demo
        metadata.json             # machine-readable index
        report.md                 # human-readable QA report

This is the single function the CLI, the future GUI, and the batch scripts
all call. Everything else in the package is a building block for this.
"""

import json
import os
import shutil
import string
from typing import Optional

from penstroke.templates.trace import trace_glyph
from penstroke.render.glyph import make_glyph_svg
from penstroke.render.alphabet import build_alphabet_svg
from penstroke.render.word import make_word_demo_html
from penstroke.quality.metrics import has_strokes, coverage, stroke_count_matches_template
from penstroke.quality.report import assess_letter, build_report, build_metadata_json


_DEFAULT_LETTERS = string.ascii_lowercase + string.ascii_uppercase


# Map special characters to filesystem-safe filenames.
# Filenames like '?.svg' or ':.svg' break on Windows; some other chars
# (slash, backslash) break universally. The mapping is reversible so a
# consumer can read the metadata.json and look up the original char.
_SPECIAL_FILENAME_MAP = {
    '!': 'excl',     '"': 'quot',    '#': 'hash',
    '$': 'dollar',   '%': 'percent', '&': 'amp',
    "'": 'apos',     '(': 'lparen',  ')': 'rparen',
    '*': 'star',     '+': 'plus',    ',': 'comma',
    '-': 'minus',    '.': 'period',  '/': 'slash',
    ':': 'colon',    ';': 'semi',    '<': 'lt',
    '=': 'eq',       '>': 'gt',      '?': 'question',
    '@': 'at',       '[': 'lbrack',  '\\': 'backslash',
    ']': 'rbrack',   '^': 'caret',   '_': 'underscore',
    '`': 'grave',    '{': 'lbrace',  '|': 'pipe',
    '}': 'rbrace',   '~': 'tilde',
}


def safe_filename(ch: str) -> str:
    """Return a filesystem-safe SVG filename for a character.

    Examples:
        'a'  → 'a.svg'
        'A'  → 'cap_A.svg'
        '@'  → 'at.svg'
        '0'  → '0.svg'
        '?'  → 'question.svg'
    """
    if ch.isalpha():
        return f'cap_{ch}.svg' if ch.isupper() else f'{ch}.svg'
    if ch.isdigit():
        return f'{ch}.svg'
    if ch in _SPECIAL_FILENAME_MAP:
        return f'{_SPECIAL_FILENAME_MAP[ch]}.svg'
    # Unknown char: use hex codepoint as a fallback
    return f'u{ord(ch):04x}.svg'


def trace_font(
    ttf_path: str,
    output_dir: str,
    font_name: Optional[str] = None,
    letters: str = _DEFAULT_LETTERS,
    size: int = 384,
    demo_word: str = "hello world",
    license_id: str = "OFL-1.1",
    license_text: Optional[str] = None,
    verbose: bool = True,
):
    """Process one TTF font end-to-end into a self-contained output folder.

    Args:
        ttf_path: path to the TTF file to process.
        output_dir: directory to write outputs into (created if missing).
        font_name: friendly font name; defaults to the TTF filename stem.
        letters: characters to trace (default: a-z + A-Z).
        size: pixel size for rasterization (higher = better quality, slower).
        demo_word: word to typeset in word_demo.html.
        license_id: SPDX license identifier for the font (recorded in metadata).
        license_text: full license text to copy into source/LICENSE.txt.
        verbose: print per-letter status as we go.

    Returns:
        Path to the output directory.
    """
    os.makedirs(output_dir, exist_ok=True)
    glyphs_dir = os.path.join(output_dir, 'glyphs')
    os.makedirs(glyphs_dir, exist_ok=True)
    source_dir = os.path.join(output_dir, 'source')
    os.makedirs(source_dir, exist_ok=True)

    if font_name is None:
        font_name = os.path.splitext(os.path.basename(ttf_path))[0]

    if verbose:
        print(f"Processing {font_name}...")

    # 1. Copy the original font + license
    shutil.copy(ttf_path, os.path.join(source_dir, os.path.basename(ttf_path)))
    if license_text:
        with open(os.path.join(source_dir, 'LICENSE.txt'), 'w', encoding='utf-8') as f:
            f.write(license_text)

    # 2. Trace each letter, write its individual SVG, run QA metrics
    per_letter_results = {}
    grid_items = []   # for the alphabet SVG
    canvas_dims = None
    baseline_y = None
    upem = None

    for ch in letters:
        try:
            mask, _skel, _dist, traced, tmpl, meta = trace_glyph(
                ttf_path, ch, size=size)
        except Exception as e:
            if verbose:
                print(f"  {ch}: ERROR {e}")
            continue

        if not traced:
            if verbose:
                print(f"  {ch}: no strokes produced")
            continue

        # Per-letter SVG
        svg_text = make_glyph_svg(traced, meta, animate=True)
        fname = safe_filename(ch)
        with open(os.path.join(glyphs_dir, fname), 'w', encoding='utf-8') as f:
            f.write(svg_text)

        # Capture canvas dims and baseline once (they're identical across letters)
        if canvas_dims is None:
            canvas_dims = (meta['canvas_w'], meta['canvas_h'])
            baseline_y = meta['baseline_y']
            upem = meta['upem']

        # Quality assessment (includes OCR if available)
        metrics = assess_letter(mask, traced, expected_stroke_count=None, char=ch)
        per_letter_results[ch] = {
            'metrics': metrics,
            'template_used': tmpl,
            'stroke_count': len(traced),
            'advance_px': meta['advance_px'],
        }
        grid_items.append((ch, traced, mask.shape, tmpl, meta))

        if verbose:
            overall, _ = metrics['overall_score']
            print(f"  {ch}: {len(traced)} strokes via {tmpl}, "
                  f"quality {overall:.2f}")

    # 3. Alphabet grid SVGs
    if grid_items:
        anim_svg, _dur = build_alphabet_svg(grid_items, animate=True)
        with open(os.path.join(output_dir, 'alphabet_animated.svg'), 'w', encoding='utf-8') as f:
            f.write(anim_svg)
        static_svg, _ = build_alphabet_svg(grid_items, animate=False)
        with open(os.path.join(output_dir, 'alphabet_static.svg'), 'w', encoding='utf-8') as f:
            f.write(static_svg)

    # 4. Word composition demo
    demo_html = make_word_demo_html(font_name, glyphs_dir, word=demo_word)
    if demo_html:
        with open(os.path.join(output_dir, 'word_demo.html'), 'w', encoding='utf-8') as f:
            f.write(demo_html)

    # 5. Metadata + report
    if canvas_dims is not None:
        metadata = build_metadata_json(
            font_name=font_name,
            font_file=f'source/{os.path.basename(ttf_path)}',
            license_id=license_id,
            per_letter_results=per_letter_results,
            canvas_dims=canvas_dims,
            baseline_y=baseline_y,
            upem=upem,
            filename_fn=safe_filename,
        )
        with open(os.path.join(output_dir, 'metadata.json'), 'w', encoding='utf-8') as f:
            f.write(metadata)

    report = build_report(font_name, per_letter_results)
    with open(os.path.join(output_dir, 'report.md'), 'w', encoding='utf-8') as f:
        f.write(report)

    if verbose:
        print(f"  → wrote {output_dir}")
    return output_dir
