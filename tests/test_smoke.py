"""Smoke tests: exercise the core paths end-to-end.

These aren't unit tests — they verify that the modules import, that the
public pipeline doesn't crash on a representative font, and that the
output has the expected shape. Real correctness tests would need a curated
fixture set with known-good outputs, which we don't have yet.

Run with:  python -m pytest tests/ -v
or just:   python tests/test_smoke.py
"""

import os
import sys
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

# Caveat is OFL-licensed and checked into tests/fixtures/.
CAVEAT = str(Path(__file__).parent / 'fixtures' / 'caveat.ttf')


def test_imports():
    """Every public module imports without errors."""
    import penstroke
    import penstroke.core.rasterize
    import penstroke.core.skeleton
    import penstroke.core.graph
    import penstroke.core.outline
    import penstroke.core.strokes
    import penstroke.core.smoothing
    import penstroke.tracer
    import penstroke.render.svg
    import penstroke.render.glyph
    import penstroke.render.alphabet
    import penstroke.render.diagnostic
    import penstroke.render.word
    import penstroke.render.houdini
    import penstroke.quality.metrics
    import penstroke.quality.cascade
    import penstroke.quality.report
    import penstroke.pipeline
    print("✓ all modules import")


def test_trace_glyph_basic():
    """The tracer produces sane output for a handful of letters.

    Uses min/max bounds rather than exact equality: stroke counts can
    shift by ±1 as decomposition details evolve, and that's fine — the
    assertions catch gross breakage (no strokes, letter exploded into
    fragments), not tuning drift.
    """
    from penstroke.tracer import trace_glyph_eulerian
    cases = [
        ('X', 1, 3),   # two crossing diagonals (1 chain if continuation pairs)
        ('a', 1, 3),   # bowl + spine
        ('o', 1, 1),   # closed loop
        ('i', 2, 3),   # stem + tittle (dot drawn last)
        ('m', 1, 4),   # cursive wave
    ]
    for ch, lo, hi in cases:
        mask, skel, dist, traced, tracer_name, meta = trace_glyph_eulerian(
            CAVEAT, ch, size=384)
        assert lo <= len(traced) <= hi, \
            f"{ch}: expected {lo}-{hi} strokes, got {len(traced)}"
        assert meta['canvas_w'] > 0
        assert meta['baseline_y'] > 0
        print(f"✓ {ch}: {len(traced)} strokes via {tracer_name}")


def test_full_pipeline_writes_expected_files():
    """trace_font produces the documented output structure."""
    from penstroke import trace_font
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / 'caveat'
        trace_font(CAVEAT, str(out), font_name='Caveat',
                   letters='abXo', verbose=False)

        expected = [
            'source/caveat.ttf',
            'glyphs/a.svg',
            'glyphs/b.svg',
            'glyphs/cap_X.svg',
            'glyphs/o.svg',
            'alphabet_animated.svg',
            'alphabet_static.svg',
            'word_demo.html',
            'metadata.json',
            'report.md',
        ]
        for rel in expected:
            assert (out / rel).exists(), f"missing: {rel}"

        meta = json.loads((out / 'metadata.json').read_text())
        assert meta['font_name'] == 'Caveat'
        assert set(meta['letters'].keys()) == set('abXo')
        for ch, info in meta['letters'].items():
            assert info['stroke_count'] > 0
            # Quality score can be 0 if OCR validation fails — that's a valid
            # report state. Just check the field exists.
            assert 'quality_score' in info
            assert 'file' in info
        print("✓ pipeline writes all expected files")


def test_glyph_svgs_have_metadata_attributes():
    """Each per-letter SVG includes the data-* attributes needed for
    word composition."""
    from penstroke import trace_font
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / 'caveat'
        trace_font(CAVEAT, str(out), font_name='Caveat',
                   letters='ab', verbose=False)
        svg_text = (out / 'glyphs/a.svg').read_text()
        for attr in ['data-baseline', 'data-pad', 'data-advance',
                     'data-ascent', 'data-descent']:
            assert attr in svg_text, f"missing {attr} in glyphs/a.svg"
        print("✓ per-letter SVGs include positioning metadata")


def test_houdini_export():
    """Houdini JSON has the right structure."""
    from penstroke.tracer import trace_glyph_eulerian
    from penstroke.render.houdini import trace_to_dict

    mask, _, _, traced, _, meta = trace_glyph_eulerian(CAVEAT, 'A', size=384)
    d = trace_to_dict('A', traced, meta, font_name='Caveat')
    assert d['letter'] == 'A'
    assert d['font'] == 'Caveat'
    assert d['canvas']['w'] > 0
    assert 'strokes' in d and len(d['strokes']) > 0
    for stroke in d['strokes']:
        assert 'points' in stroke
        assert 'length' in stroke
        for pt in stroke['points']:
            assert 'x' in pt and 'y' in pt and 'width' in pt
    print("✓ Houdini JSON export schema correct")


if __name__ == '__main__':
    test_imports()
    test_trace_glyph_basic()
    test_full_pipeline_writes_expected_files()
    test_glyph_svgs_have_metadata_attributes()
    test_houdini_export()
    print("\nAll smoke tests passed.")
