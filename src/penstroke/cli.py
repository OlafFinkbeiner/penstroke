"""Command-line interface for penstroke.

Usage:
    python -m penstroke trace <font.ttf> <output_dir> [options]

Options:
    --name NAME       human-readable font name (default: TTF filename stem)
    --letters STR     characters to trace (default: a-z + A-Z)
    --size N          rasterization pixel size (default: 384)
    --word WORD       word to typeset in word_demo.html (default: "hello world")
    --quiet           suppress per-letter progress output
"""

import argparse
import sys

from penstroke.pipeline import trace_font


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
                         help='Characters to trace (default: a-z + A-Z).')
    p_trace.add_argument('--size', type=int, default=384,
                         help='Rasterization pixel size (default: 384).')
    p_trace.add_argument('--word', default='hello world',
                         help='Word for word_demo.html (default: "hello world").')
    p_trace.add_argument('--quiet', action='store_true',
                         help='Suppress per-letter progress output.')
    p_trace.add_argument('--tracer', choices=['template', 'eulerian'],
                         default='eulerian',
                         help='Stroke-decomposition algorithm. "eulerian" '
                              '(default) runs the graph-theoretic '
                              'Chinese-Postman + Hierholzer tracer (EPST). '
                              '"template" is the legacy Hershey-template '
                              'tracer, kept as a fallback.')

    args = parser.parse_args(argv)

    if args.command == 'trace':
        kwargs = {
            'font_name': args.name,
            'size': args.size,
            'demo_word': args.word,
            'verbose': not args.quiet,
            'tracer': args.tracer,
        }
        if args.letters is not None:
            kwargs['letters'] = args.letters
        trace_font(args.ttf, args.output_dir, **kwargs)


if __name__ == '__main__':
    sys.exit(main())
