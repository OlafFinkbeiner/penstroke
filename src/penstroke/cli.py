"""Command-line interface for penstroke.

Usage:
    penstroke trace <font.ttf> <output_dir> [options]
    penstroke export-corel <output_dir> [--csv PATH] [--size N]
    penstroke import-corel <output_dir> <edited.csv>
    penstroke sync-edits <output_dir> [--size N]
    penstroke refresh-previews <root> [<root> ...]

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

The file-handshake variant (no commands to remember; what the TOPs
graph runs — see penstroke/handshake.py for the folder conventions).
Two global drop folders, any font:
    1. preview.html: select glyphs, "Save selection" -> drop the
       sel-<font>-<hash>.json into the selections inbox
       (<repo>/selections/).
    2. penstroke sync-edits <output_dir> --inbox ... --corel ...
       -> writes <corel>/sel-<font>-<hash>.csv
    3. CorelDRAW: import that CSV, edit, export BACK ONTO THE SAME
       FILE (or any name containing the font's name, e.g. Corel's
       default '<doctitle>_edited.csv', in the same folder).
    4. The next sync detects the re-saved file as an edited return,
       merges it, marks it imported, rebuilds the output folder.
Per-font <output_dir>/selections/ and <output_dir>/edits/ still work
as fallbacks (e.g. for arbitrarily named Corel exports).
"""

import argparse
import glob
import json
import os
import sys

# NB: penstroke.pipeline (and the tracer it pulls in) needs networkx /
# scipy / skimage, which live in the venv but NOT in hython. Import it
# lazily inside the commands that trace, so the light commands
# (build-bundles, refresh-previews) stay importable under hython.


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
    p_exp.add_argument('--selection', default=None,
                       help='Read the characters from a selection JSON '
                            '(saved by preview.html); the CSV defaults to '
                            'the matching subsets/ path.')
    p_exp.add_argument('--size', type=int, default=384,
                       help='Rasterization size used for the trace (default: 384).')

    p_imp = sub.add_parser('import-corel',
                           help='Re-render an output folder from an edited CSV.')
    p_imp.add_argument('output_dir', help='The trace output folder to rebuild.')
    p_imp.add_argument('edited_csv', help='CSV written by PenstrokeExportEdits.')
    p_imp.add_argument('--size', type=int, default=384,
                       help='Rasterization size used for the trace (default: 384).')

    p_sync = sub.add_parser('sync-edits',
                            help='File-handshake sync: import pending '
                                 'edits/*.csv, then write subset CSVs for '
                                 'pending selections/*.json.')
    p_sync.add_argument('output_dir', help='A finished trace output folder.')
    p_sync.add_argument('--size', type=int, default=384,
                        help='Rasterization size used for the trace '
                             '(default: 384).')
    p_sync.add_argument('--inbox', default=None,
                        help='Global selections drop folder; files are '
                             'routed to this font via their "font" field. '
                             'Default: $PENSTROKE_SELECTIONS if set.')
    p_sync.add_argument('--corel', default=None,
                        help='Global Corel exchange folder: subset CSVs '
                             'are written there and edited returns are '
                             'picked up from there (same file or any '
                             'sel-<font>-* name). Default: '
                             '$PENSTROKE_COREL if set.')

    p_ref = sub.add_parser('refresh-previews',
                           help='Regenerate preview.html from the existing '
                                'alphabet_animated.svg (viewer-template '
                                'update, no re-trace).')
    p_ref.add_argument('roots', nargs='+',
                       help='Directories searched recursively for trace '
                            'output folders.')

    p_bb = sub.add_parser('build-bundles',
                          help='Build the .hfont bundles listed in chunk '
                               'spec JSON files. Runs under hython (the rep '
                               'builders need hou); the penstroke::tops '
                               'build_bundle stage calls it, one command '
                               'per chunk, so the build fans out across '
                               'cores with a live task count.')
    p_bb.add_argument('specs', nargs='+',
                      help='Chunk spec JSON files; each a list of font '
                           'dicts (ttf, bundle, store, charset, category, '
                           'build_bezier).')

    args = parser.parse_args(argv)

    if args.command == 'trace':
        from penstroke.pipeline import trace_font
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
        from penstroke import handshake
        from penstroke.editround import write_edit_csv, load_stroke_store
        from penstroke.pipeline import safe_filename
        meta = _load_metadata(args.output_dir)
        ttf = _find_source_font(args.output_dir)
        if args.selection:
            letters = handshake.read_selection(args.selection)
            default_csv = handshake.subset_csv_path(args.output_dir,
                                                    args.selection)
        else:
            letters = args.glyphs or ''.join(meta['letters'].keys())
            default_csv = os.path.join(args.output_dir,
                                       f"{meta['font_name']}_edit.csv")
        csv_path = args.csv or default_csv
        os.makedirs(os.path.dirname(os.path.abspath(csv_path)), exist_ok=True)
        store = load_stroke_store(args.output_dir)
        n = write_edit_csv(args.output_dir, csv_path, meta['font_name'],
                           ttf, letters, args.size, safe_filename,
                           stroke_source=store)
        # Mark it as OUR pristine export so a later sync-edits doesn't
        # mistake the un-edited file for a Corel return (it becomes a
        # return only once Corel re-saves it and bumps the mtime).
        handshake.write_outgoing_marker(csv_path, meta['font_name'],
                                        args.output_dir)
        print(f'wrote {csv_path} ({n} glyph pages)')
        print('Next: in CorelDRAW run the PenstrokeImport macro '
              '(corel/penstroke_corel.bas) and select this CSV.')

    elif args.command == 'import-corel':
        from penstroke.editround import import_edit_csv
        n_edit, n_kept = import_edit_csv(args.output_dir, args.edited_csv,
                                         size=args.size)
        print(f'rebuilt {args.output_dir}: {n_edit} glyphs exchanged, '
              f'{n_kept} kept')

    elif args.command == 'sync-edits':
        from penstroke import handshake
        from penstroke.editround import (import_edit_csv, write_edit_csv,
                                         load_stroke_store)
        from penstroke.pipeline import safe_filename

        inbox = args.inbox or os.environ.get('PENSTROKE_SELECTIONS')
        corel = args.corel or os.environ.get('PENSTROKE_COREL')

        # 1. Merge edited returns that appeared since the last sync
        #    (oldest first so later edits win), marking each.
        edits = handshake.pending_edits(args.output_dir, corel_dir=corel)
        for csv in edits:
            n_edit, n_kept = import_edit_csv(args.output_dir, csv,
                                             size=args.size, verbose=False)
            print(f'imported {csv}: {n_edit} glyphs exchanged, '
                  f'{n_kept} kept')

        # 2. Write Corel CSVs for new/changed selections — AFTER the
        #    merge so they include the freshest strokes.
        selections = handshake.pending_selections(args.output_dir,
                                                  inbox=inbox,
                                                  corel_dir=corel)
        if selections:
            meta = _load_metadata(args.output_dir)
            ttf = _find_source_font(args.output_dir)
            store = load_stroke_store(args.output_dir)
            for sel, csv_path in selections:
                letters = handshake.read_selection(sel)
                os.makedirs(os.path.dirname(csv_path), exist_ok=True)
                n = write_edit_csv(args.output_dir, csv_path,
                                   meta['font_name'], ttf, letters,
                                   args.size, safe_filename,
                                   stroke_source=store)
                handshake.write_outgoing_marker(csv_path,
                                                meta['font_name'],
                                                args.output_dir)
                print(f'wrote {csv_path} ({n} glyph pages) from '
                      f'{os.path.basename(sel)}')

        if not edits and not selections:
            print(f'{args.output_dir}: nothing pending')

    elif args.command == 'refresh-previews':
        from penstroke.render.alphabet import make_preview_html
        n = 0
        for root in args.roots:
            # A trace folder is any dir holding the animated grid SVG;
            # font_name comes from its metadata.json when present.
            for svg_path in sorted(glob.glob(
                    os.path.join(root, '**', 'alphabet_animated.svg'),
                    recursive=True)):
                out_dir = os.path.dirname(svg_path)
                try:
                    font_name = _load_metadata(out_dir)['font_name']
                except (OSError, KeyError, json.JSONDecodeError):
                    font_name = os.path.basename(out_dir)
                with open(svg_path, encoding='utf-8') as f:
                    anim_svg = f.read()
                html = make_preview_html(anim_svg, font_name=font_name)
                with open(os.path.join(out_dir, 'preview.html'), 'w',
                          encoding='utf-8') as f:
                    f.write(html)
                n += 1
        print(f'refreshed {n} preview.html files')

    elif args.command == 'build-bundles':
        # Lazy import: the rep builders need hou, so this branch only
        # works under hython — but importing it lazily keeps the rest of
        # the CLI usable from the plain venv.
        from penstroke.houdini.build_bundle import build_chunk
        fails = sum(build_chunk(spec) for spec in args.specs)
        if fails:
            raise SystemExit(f'{fails} bundle(s) failed')


if __name__ == '__main__':
    sys.exit(main())
