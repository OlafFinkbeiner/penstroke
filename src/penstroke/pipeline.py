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
        preview.html              # interactive viewer (play/pause/speed/scrub/wireframe)
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

from penstroke.tracer import trace_glyph_eulerian
from penstroke.core.outline import extract_outlines
from penstroke.render.glyph import make_glyph_svg
from penstroke.render.alphabet import build_alphabet_svg, make_preview_html
from penstroke.render.word import make_word_demo_html
from penstroke.quality.metrics import has_strokes, coverage, stroke_count_matches_template
from penstroke.quality.report import assess_letter, build_report, build_metadata_json
from penstroke.quality.glyph_image import write_raw_glyphs


# Fallback set, used only when the font's cmap can't be enumerated.
# The normal default is ALL drawable glyphs in the font (letters=None).
_FALLBACK_LETTERS = (string.ascii_lowercase + string.ascii_uppercase
                     + '@€$§&?#+*/ÜÖÄ')

# Glyphs per row in the alphabet grid / preview.
GRID_COLS = 13


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
    letters: Optional[str] = None,
    charset: str = 'latin',
    size: int = 384,
    demo_word: str = "hello world",
    license_id: str = "OFL-1.1",
    license_text: Optional[str] = None,
    verbose: bool = True,
    glyph_source=None,
):
    """Process one TTF font end-to-end into a self-contained output folder.

    `glyph_source` optionally overrides where stroke decompositions come
    from: a callable (ch) -> (mask, traced, tracer_name, meta) or None.
    Default is the graph tracer. The CorelDRAW edit round-trip uses this
    to re-render the full output folder from hand-edited centerlines.

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
    # Default: the chosen charset preset, intersected with the font's
    # cmap ('latin' covers ASCII + Latin-1; use charset='all' for the
    # complete font).
    letters_from_charset = letters is None
    if letters is None:
        try:
            from penstroke.charset import font_charset
            letters = font_charset(ttf_path, charset=charset)
        except Exception:
            letters = _FALLBACK_LETTERS

    os.makedirs(output_dir, exist_ok=True)
    glyphs_dir = os.path.join(output_dir, 'glyphs')
    os.makedirs(glyphs_dir, exist_ok=True)
    source_dir = os.path.join(output_dir, 'source')
    os.makedirs(source_dir, exist_ok=True)

    if font_name is None:
        font_name = os.path.splitext(os.path.basename(ttf_path))[0]

    if verbose:
        print(f"Processing {font_name}...")

    # 0. Render plain glyph PNGs for downstream vision-model spec extraction
    #    (see quality/spec.py and the `penstroke spec` CLI). Cheap to do here;
    #    skipped silently if anything errors.
    try:
        write_raw_glyphs(
            ttf_path, output_dir, letters,
            filename_fn=lambda ch: safe_filename(ch).replace('.svg', '.png'))
    except Exception:
        pass


    # 1. Copy the original font + license (skip if re-rendering in place
    #    and the source font already lives in this output folder)
    font_dest = os.path.join(source_dir, os.path.basename(ttf_path))
    if not (os.path.exists(font_dest)
            and os.path.samefile(ttf_path, font_dest)):
        shutil.copy(ttf_path, font_dest)
    if license_text:
        with open(os.path.join(source_dir, 'LICENSE.txt'), 'w', encoding='utf-8') as f:
            f.write(license_text)

    # 2. Trace each letter, write its individual SVG, run QA metrics
    per_letter_results = {}
    grid_items = []   # for the alphabet SVG
    traced_per_letter = {}   # for the diagnostic renderer
    canvas_dims = None
    baseline_y = None
    upem = None
    canvas_pad = None

    if glyph_source is None:
        def glyph_source(ch):
            mask, _skel, _dist, traced, tmpl, meta = trace_glyph_eulerian(
                ttf_path, ch, size=size)
            return mask, traced, tmpl, meta

    for ch in letters:
        try:
            result = glyph_source(ch)
            if result is None:
                continue
            mask, traced, tmpl, meta = result
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
            canvas_pad = meta.get('pad')

        # Quality assessment (includes OCR if available)
        metrics = assess_letter(mask, traced, expected_stroke_count=None, char=ch)
        per_letter_results[ch] = {
            'metrics': metrics,
            'template_used': tmpl,
            'stroke_count': len(traced),
            'advance_px': meta['advance_px'],
        }
        try:
            outlines = extract_outlines(ttf_path, ch, size=size)
        except Exception:
            outlines = []
        grid_items.append((ch, traced, mask, outlines, tmpl, meta))
        traced_per_letter[ch] = (mask, traced)

        if verbose:
            overall, _ = metrics['overall_score']
            print(f"  {ch}: {len(traced)} strokes via {tmpl}, "
                  f"quality {overall:.2f}")

    # 2b. Persist the stroke store — the SOURCE OF TRUTH for this font's
    # current decomposition. Corel edit rounds merge into this file, so
    # partial edits accumulate across rounds. A failure here must be
    # loud: everything downstream (export-corel, bundles, re-renders)
    # reads this file, and a stale store silently ships old strokes.
    from penstroke.editround import save_stroke_store
    save_stroke_store(output_dir,
                      {ch: t for ch, (m, t) in traced_per_letter.items()})

    # 3. Alphabet grid SVGs
    if grid_items:
        anim_svg, _dur = build_alphabet_svg(grid_items, cols=GRID_COLS,
                                            animate=True)
        with open(os.path.join(output_dir, 'alphabet_animated.svg'), 'w', encoding='utf-8') as f:
            f.write(anim_svg)
        static_svg, _ = build_alphabet_svg(grid_items, cols=GRID_COLS,
                                           animate=False)
        with open(os.path.join(output_dir, 'alphabet_static.svg'), 'w', encoding='utf-8') as f:
            f.write(static_svg)

        preview_html = make_preview_html(anim_svg, font_name=font_name)
        with open(os.path.join(output_dir, 'preview.html'), 'w', encoding='utf-8') as f:
            f.write(preview_html)

    # 3b. Per-letter diagnostic PNGs (colored strokes, numbered starts)
    try:
        from penstroke.render.diagnostic import render_all_diagnostics
        render_all_diagnostics(
            ttf_path, traced_per_letter, output_dir, letters, size=size,
            filename_fn=lambda ch: safe_filename(ch).replace('.svg', '.png'))
    except Exception:
        pass

    # 4. Word composition demo
    demo_html = make_word_demo_html(font_name, glyphs_dir, word=demo_word)
    if demo_html:
        with open(os.path.join(output_dir, 'word_demo.html'), 'w', encoding='utf-8') as f:
            f.write(demo_html)

    # 5. Metadata + report
    if canvas_dims is not None:
        # Record the trace-time canvas parameters: the stroke store's
        # coordinates live in this canvas, and export/import-corel,
        # sync-edits and the hfont rep builders must all rasterize at
        # the SAME size/pad or widths silently resample against the
        # wrong-scale ink.
        trace_params = {'size': size}
        if canvas_pad is not None:
            trace_params['pad'] = canvas_pad
        if letters_from_charset:
            trace_params['charset'] = charset
        metadata = build_metadata_json(
            font_name=font_name,
            font_file=f'source/{os.path.basename(ttf_path)}',
            license_id=license_id,
            per_letter_results=per_letter_results,
            canvas_dims=canvas_dims,
            baseline_y=baseline_y,
            upem=upem,
            filename_fn=safe_filename,
            trace_params=trace_params,
        )
        with open(os.path.join(output_dir, 'metadata.json'), 'w', encoding='utf-8') as f:
            f.write(metadata)

    report = build_report(font_name, per_letter_results)
    # If a spec.json is present, append AI-spec validation to the report.
    try:
        from penstroke.quality.spec_validate import build_validation_report
        validation = build_validation_report(output_dir)
        if validation:
            report = report + '\n\n' + validation
    except Exception:
        pass
    with open(os.path.join(output_dir, 'report.md'), 'w', encoding='utf-8') as f:
        f.write(report)

    if verbose:
        print(f"  → wrote {output_dir}")
    return output_dir
