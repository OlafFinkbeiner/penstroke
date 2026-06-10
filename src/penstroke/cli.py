"""Command-line interface for penstroke.

Usage:
    penstroke trace <font.ttf> <output_dir> [options]
    penstroke export-corel <output_dir> [--csv PATH] [--size N]
    penstroke import-corel <output_dir> <edited.csv>

trace options:
    --name NAME       human-readable font name (default: TTF filename stem)
    --letters STR     characters to trace (default: a-z + A-Z + symbols)
    --size N          rasterization pixel size (default: 384)
    --word WORD       word to typeset in word_demo.html (default: "hello world")
    --quiet           suppress per-letter progress output

The Corel round-trip (hand-editing strokes in CorelDRAW):
    1. penstroke export-corel output/myfont/        -> myfont_edit.csv
    2. CorelDRAW: run PenstrokeImport macro (corel/penstroke_corel.bas),
       pick the CSV. Edit strokes; the object NAME (s01, s02, ...) is
       the draw order.
    3. CorelDRAW: run PenstrokeExportEdits          -> edited CSV
    4. penstroke import-corel output/myfont/ edited.csv
       Widths are re-sampled from the font ink; the whole output folder
       (SVGs, preview, diagnostics, report) regenerates.
"""

import argparse
import glob
import json
import os
import sys

from penstroke.pipeline import trace_font, safe_filename


def _find_source_font(output_dir):
    fonts = sorted(glob.glob(os.path.join(output_dir, 'source', '*.ttf')))
    if not fonts:
        raise SystemExit(f'no source font found under {output_dir}/source/')
    return fonts[0]


def _load_metadata(output_dir):
    path = os.path.join(output_dir, 'metadata.json')
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='penstroke',
        description='Convert a TTF font into hand-drawn stroke SVGs.',
    )
    sub = parser.add_subparsers(dest='command', required=True)

    p_trace = sub.add_parser('trace', help='Trace one font into an output folder.')
    p_trace.add_argument('ttf', help='Path to the TTF font file.')
    p_trace.add_argument('output_dir', help='Directory to write outputs into.')
    p_trace.add_argument('--name', default=None, help='Human-readable font name.')
    p_trace.add_argument('--letters', default=None,
                         help='Explicit characters to trace (overrides '
                              '--charset).')
    p_trace.add_argument('--charset', default='latin',
                         choices=['ascii', 'latin'],
                         help='Charset preset, intersected with the font '
                              "cmap (default: latin = ASCII + Latin-1).")
    p_trace.add_argument('--size', type=int, default=384,
                         help='Rasterization pixel size (default: 384).')
    p_trace.add_argument('--word', default='hello world',
                         help='Word for word_demo.html (default: "hello world").')
    p_trace.add_argument('--quiet', action='store_true',
                         help='Suppress per-letter progress output.')

    p_exp = sub.add_parser('export-corel',
                           help='Write the edit CSV for the CorelDRAW macro.')
    p_exp.add_argument('output_dir', help='A finished trace output folder.')
    p_exp.add_argument('--csv', default=None,
                       help='CSV path (default: <output_dir>/<font>_edit.csv).')
    p_exp.add_argument('--glyphs', default=None,
                       help='Only export these characters (e.g. from the '
                            'preview selection tool). Default: all.')
    p_exp.add_argument('--size', type=int, default=384,
                       help='Rasterization size used for the trace (default: 384).')

    p_imp = sub.add_parser('import-corel',
                           help='Re-render an output folder from an edited CSV.')
    p_imp.add_argument('output_dir', help='The trace output folder to rebuild.')
    p_imp.add_argument('edited_csv', help='CSV written by PenstrokeExportEdits.')
    p_imp.add_argument('--size', type=int, default=384,
                       help='Rasterization size used for the trace (default: 384).')

    args = parser.parse_args(argv)

    if args.command == 'trace':
        kwargs = {
            'font_name': args.name,
            'size': args.size,
            'demo_word': args.word,
            'verbose': not args.quiet,
        }
        if args.letters is not None:
            kwargs['letters'] = args.letters
        else:
            kwargs['charset'] = args.charset
        trace_font(args.ttf, args.output_dir, **kwargs)

    elif args.command == 'export-corel':
        from penstroke.editround import write_edit_csv, load_stroke_store
        meta = _load_metadata(args.output_dir)
        ttf = _find_source_font(args.output_dir)
        letters = args.glyphs if args.glyphs else ''.join(meta['letters'].keys())
        store = load_stroke_store(args.output_dir)
        csv_path = args.csv or os.path.join(
            args.output_dir, f"{meta['font_name']}_edit.csv")
        n = write_edit_csv(args.output_dir, csv_path, meta['font_name'],
                           ttf, letters, args.size, safe_filename,
                           stroke_source=store)
        print(f'wrote {csv_path} ({n} glyph pages)')
        print('Next: in CorelDRAW run the PenstrokeImport macro '
              '(corel/penstroke_corel.bas) and select this CSV.')

    elif args.command == 'import-corel':
        from penstroke.editround import (read_edit_csv, resample_widths,
                                         load_stroke_store)
        meta = _load_metadata(args.output_dir)
        ttf = _find_source_font(args.output_dir)
        header, glyphs = read_edit_csv(args.edited_csv)

        # Resolve chars: the Corel export macro doesn't know codepoints
        # (it writes 0000), so glyph identity travels via the page name
        # (= safe filename stem). Build the reverse map from metadata.
        safe_to_char = {safe_filename(ch).rsplit('.', 1)[0]: ch
                        for ch in meta['letters']}
        unknown_char = chr(0)

        edited = {}
        for g in glyphs:
            ch = safe_to_char.get(g['safe'])
            if ch is None and g['char'] != unknown_char:
                ch = g['char']
            if ch is None or not g['strokes']:
                continue
            traced = resample_widths(g['strokes'], ttf, ch, args.size)
            if traced:
                edited[ch] = traced

        # Exchange ONLY the glyphs present in the CSV; everything else
        # keeps its current strokes (from the store written at trace /
        # previous import time).
        current = load_stroke_store(args.output_dir) or {}
        current.update(edited)

        from penstroke.core.rasterize import rasterize_glyph

        def glyph_source(ch):
            if ch not in current:
                return None
            mask, m = rasterize_glyph(ttf, ch, size=args.size)
            return mask, current[ch], 'edited' if ch in edited else 'kept', m

        trace_font(ttf, args.output_dir,
                   font_name=meta['font_name'],
                   letters=''.join(current.keys()),
                   size=args.size,
                   glyph_source=glyph_source)
        print(f'rebuilt {args.output_dir}: {len(edited)} glyphs exchanged, '
              f'{len(current) - len(edited)} kept')


if __name__ == '__main__':
    sys.exit(main())
