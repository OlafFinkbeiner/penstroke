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
    import penstroke.curvefit
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


def test_trace_determinism():
    """Tracing the same glyph twice yields identical strokes.

    Determinism is load-bearing: skeletonization must run with a fixed
    rng (core/skeleton.py passes rng=0) or the same glyph yields
    different graph topologies across runs — the historic symptom was
    intermittent stray strokes and unstable stroke counts.
    """
    import numpy as np
    from penstroke.tracer import trace_glyph_eulerian
    for ch in ('a', 'g', '8', 'X'):
        runs = []
        for _ in range(2):
            _, _, _, traced, _, _ = trace_glyph_eulerian(CAVEAT, ch,
                                                         size=384)
            runs.append(traced)
        first, second = runs
        assert len(first) == len(second), \
            f"{ch}: stroke count differs across runs " \
            f"({len(first)} vs {len(second)})"
        for i, (s1, s2) in enumerate(zip(first, second)):
            for a1, a2 in zip(s1, s2):
                assert np.array_equal(a1, a2), \
                    f"{ch}: stroke {i} geometry differs across runs"
    print("✓ tracing is deterministic (a, g, 8, X twice → identical)")


def test_closed_loops_not_double_traced():
    """Glyphs whose skeleton has junctions but no endpoints ('8') are
    decomposed exactly once.

    Regression: trace_closed_loops used to skip only components with an
    endpoint, so an '8' (junctions, no endpoints) was decomposed by the
    junction-first pipeline AND re-walked as an orphan loop — a
    duplicated stroke over the same ink.
    """
    import numpy as np
    from penstroke.tracer import trace_glyph_eulerian

    def overlap_fraction(s1, s2):
        """Fraction of s1's points lying within 2px of some s2 point."""
        p1 = np.column_stack([s1[0], s1[1]])
        p2 = np.column_stack([s2[0], s2[1]])
        d = np.sqrt(((p1[:, None, :] - p2[None, :, :]) ** 2).sum(-1))
        return float((d.min(axis=1) < 2.0).mean())

    for ch in ('8', 'o', 'B'):
        _, _, _, traced, _, _ = trace_glyph_eulerian(CAVEAT, ch, size=384)
        assert traced, f"{ch}: no strokes"
        for i in range(len(traced)):
            for j in range(i + 1, len(traced)):
                frac = overlap_fraction(traced[i], traced[j])
                assert frac < 0.8, \
                    f"{ch}: strokes {i} and {j} overlap {frac:.0%} — " \
                    f"double-traced loop"
    print("✓ no double-traced loops (8, o, B)")


def test_store_write_is_atomic():
    """A crash mid-write never truncates strokes.json.

    The store accumulates hand edits (Corel rounds), so a partial write
    would destroy them. fileio.write_json_atomic writes a temp file and
    os.replace()s it; a failure during serialization must leave the old
    file byte-identical and no temp litter behind.
    """
    import numpy as np
    import penstroke.fileio as fileio
    from penstroke.editround import save_stroke_store, load_stroke_store

    with tempfile.TemporaryDirectory() as tmpdir:
        stroke = (np.array([1.0, 2.0]), np.array([3.0, 4.0]),
                  np.array([1.5, 1.5]))
        save_stroke_store(tmpdir, {'a': [stroke]})
        store_file = Path(tmpdir) / 'strokes.json'
        before = store_file.read_bytes()

        class Boom(Exception):
            pass

        real_dump = fileio.json.dump

        def exploding_dump(data, f, **kw):
            f.write('{"partial": ')   # simulate a write cut short
            raise Boom()

        fileio.json.dump = exploding_dump
        try:
            try:
                save_stroke_store(tmpdir, {'b': [stroke]})
                raise AssertionError('exploding dump did not raise')
            except Boom:
                pass
        finally:
            fileio.json.dump = real_dump

        assert store_file.read_bytes() == before, \
            'crashed write corrupted strokes.json'
        leftovers = [p for p in Path(tmpdir).iterdir()
                     if p.suffix == '.tmp']
        assert not leftovers, f'temp litter left behind: {leftovers}'
        assert load_stroke_store(tmpdir), 'old store no longer loads'
    print("✓ store writes are atomic (crash mid-write leaves old store)")


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
        # The trace block records the canvas the stroke store's
        # coordinates live in; edit-round commands and the hfont rep
        # builders resolve size/pad from here.
        assert meta['trace']['size'] == 384
        assert meta['trace']['pad'] == 40
        for ch, info in meta['letters'].items():
            assert info['stroke_count'] > 0
            # Quality score can be 0 if OCR validation fails — that's a valid
            # report state. Just check the field exists.
            assert 'quality_score' in info
            assert 'file' in info
        print("✓ pipeline writes all expected files")


def test_qa_verb():
    """`penstroke qa` runs the cascade against the stroke store and
    writes qa.json plus an idempotent report.md section."""
    from penstroke import trace_font
    from penstroke.cli import main as cli_main
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / 'caveat'
        trace_font(CAVEAT, str(out), font_name='Caveat',
                   letters='ao', verbose=False)
        cli_main(['qa', str(out)])
        assert (out / 'qa.json').exists()
        report = (out / 'report.md').read_text(encoding='utf-8')
        assert report.count('## Cascade QA') == 1
        # Re-running must replace the section, not stack a second one.
        cli_main(['qa', str(out)])
        report = (out / 'report.md').read_text(encoding='utf-8')
        assert report.count('## Cascade QA') == 1
        # A clean 'o' must not fire the zigzag detector: a closed loop
        # ends where it starts, which is not backtracking.
        qa = json.loads((out / 'qa.json').read_text(encoding='utf-8'))
        for iss in qa.get('o', []):
            assert iss['kind'] != 'zigzag', \
                'closed loop flagged as zigzag'
    print("✓ penstroke qa (store-based cascade, idempotent report section)")


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


def test_curvefit_reduces_polyline():
    """curvefit.fit_beziers turns a dense polyline into a few cubic
    segments that still track the original within tolerance — the
    numpy-only core shared by the Corel export and the bezier rep."""
    import numpy as np
    from penstroke import curvefit

    # 240-sample arc (like a real stroke): a smooth quarter-circle.
    t = np.linspace(0, np.pi / 2, 240)
    pts = np.column_stack([np.cos(t) * 100, np.sin(t) * 100])
    segs = curvefit.fit_beziers(pts, curvefit.STROKE_FIT_TOL_PX)
    # Dramatic reduction: a smooth arc is a handful of cubics, not 240.
    assert 1 <= len(segs) <= 8, f'expected few segments, got {len(segs)}'
    n_cv = 3 * len(segs) + 1
    assert n_cv < len(pts) / 5, f'{n_cv} CVs is not a real reduction'

    # Flattened curve stays close to the original samples.
    flat = curvefit.flatten_beziers([tuple(np.asarray(p) for p in s)
                                     for s in segs])
    # nearest-distance from each original point to the flattened curve
    d = np.min(np.hypot(pts[:, None, 0] - flat[None, :, 0],
                        pts[:, None, 1] - flat[None, :, 1]), axis=1)
    assert float(d.max()) < 3.0, f'fit drifted {d.max():.2f}px off'

    # The Corel export still re-exports through the moved code path.
    from penstroke.editround import fit_beziers as ee_fit
    assert ee_fit is curvefit.fit_beziers
    print(f"✓ curvefit: 240-pt arc -> {len(segs)} cubic segments, "
          f"max drift {d.max():.2f}px")


def test_handshake_pending_logic():
    """Pure-os pending checks behind `penstroke sync-edits`."""
    import time
    from penstroke import handshake

    with tempfile.TemporaryDirectory() as tmpdir:
        td = Path(tmpdir)
        assert not handshake.has_pending(tmpdir)

        # A selection with no subset CSV is pending; writing the CSV
        # (later mtime) clears it; touching the selection re-arms it.
        sel_dir = td / 'selections'
        sel_dir.mkdir()
        sel = sel_dir / 'sel-a3f2c1.json'
        sel.write_text(json.dumps(
            {'penstroke_selection': 1, 'font': 'X', 'glyphs': 'ab'}),
            encoding='utf-8')
        pend = handshake.pending_selections(tmpdir)
        assert len(pend) == 1 and pend[0][0] == str(sel)
        csv = Path(pend[0][1])
        assert csv.name == 'sel-a3f2c1_edit.csv'
        csv.parent.mkdir()
        time.sleep(0.01)
        csv.write_text('H;...', encoding='utf-8')
        assert not handshake.pending_selections(tmpdir)
        time.sleep(0.01)
        sel.write_text(sel.read_text(encoding='utf-8'), encoding='utf-8')
        assert handshake.pending_selections(tmpdir)

        # An edited CSV without a marker is pending; the marker (later
        # mtime) clears it; a re-saved CSV is pending again.
        edits = td / 'edits'
        edits.mkdir()
        edited = edits / 'caveat_edited.csv'
        edited.write_text('H;...', encoding='utf-8')
        assert handshake.pending_edits(tmpdir) == [str(edited)]
        time.sleep(0.01)
        handshake.write_imported_marker(str(edited), ['a'])
        assert not handshake.pending_edits(tmpdir)
        time.sleep(0.01)
        edited.write_text('H;...2', encoding='utf-8')
        assert handshake.pending_edits(tmpdir) == [str(edited)]

        assert handshake.read_selection(str(sel)) == 'ab'

        # Global inbox: a selection lands in ONE shared folder and is
        # routed via its "font" field (normalized: 'Aguafina Script'
        # matches a trace dir named aguafinascript). The trace dir here
        # is `td` itself, whose random tmp basename the selection must
        # NOT match; then one that does.
        inbox = td / 'inbox'
        inbox.mkdir()
        (inbox / 'sel-otherfont-111111.json').write_text(json.dumps(
            {'penstroke_selection': 1, 'font': 'Other Font',
             'glyphs': 'z'}), encoding='utf-8')
        (inbox / 'sel-x-222222.json').write_text(json.dumps(
            {'penstroke_selection': 1,
             'font': os.path.basename(tmpdir).upper(),   # case-insensitive
             'glyphs': 'q'}), encoding='utf-8')
        pend = handshake.pending_selections(tmpdir, inbox=str(inbox))
        routed = [p for p, _ in pend if str(inbox) in p]
        assert routed == [str(inbox / 'sel-x-222222.json')], routed
        # Without a Corel exchange folder the CSV falls back to the
        # trace dir's subsets/, named after the selection file.
        assert pend[-1][1].endswith('sel-x-222222_edit.csv')
        assert (Path(pend[-1][1]).parent == td / 'subsets')

        # Clear the legacy pending edit so the corel-exchange section
        # starts from a clean slate.
        time.sleep(0.01)
        handshake.write_imported_marker(str(edited), ['a'])

        # Global Corel exchange folder: one folder, both directions.
        corel = td / 'corel'
        corel.mkdir()
        myslug = handshake._norm(os.path.basename(tmpdir))
        out_csv = corel / f'sel-{myslug}-c0ffee.csv'
        # With corel_dir, the selection's target CSV moves there.
        sel2 = sel_dir / f'sel-{myslug}-c0ffee.json'
        time.sleep(0.01)
        sel2.write_text(json.dumps(
            {'penstroke_selection': 1, 'font': myslug, 'glyphs': 'ab'}),
            encoding='utf-8')
        pend = handshake.pending_selections(tmpdir, corel_dir=str(corel))
        assert str(out_csv) in [c for _, c in pend]
        # Pristine export (CSV + outgoing sidecar): NOT an edit return.
        time.sleep(0.01)
        out_csv.write_text('H;...', encoding='utf-8')
        handshake.write_outgoing_marker(str(out_csv), myslug, tmpdir)
        assert handshake.pending_edits(tmpdir,
                                       corel_dir=str(corel)) == []
        # A bumped mtime with IDENTICAL bytes (cloud sync, copy, touch)
        # is still pristine — content, not mtime, decides. Importing a
        # pristine export would replace raw traced strokes with the
        # export's smoothed refit.
        time.sleep(0.01)
        out_csv.write_text('H;...', encoding='utf-8')
        assert handshake.pending_edits(tmpdir,
                                       corel_dir=str(corel)) == [], \
            'identical-content rewrite must stay pristine'
        # Corel re-saves with CHANGED content -> it IS a pending return
        # now; the imported marker then clears it.
        time.sleep(0.01)
        out_csv.write_text('H;...edited', encoding='utf-8')
        assert handshake.pending_edits(
            tmpdir, corel_dir=str(corel)) == [str(out_csv)]
        time.sleep(0.01)
        handshake.write_imported_marker(str(out_csv), ['a'])
        assert handshake.pending_edits(tmpdir,
                                       corel_dir=str(corel)) == []
        # Quarantine: a CSV that failed to import is skipped while its
        # content is unchanged, retried once the content changes.
        time.sleep(0.01)
        out_csv.write_text('H;...broken', encoding='utf-8')
        assert handshake.pending_edits(
            tmpdir, corel_dir=str(corel)) == [str(out_csv)]
        handshake.write_failed_marker(str(out_csv), 'parse error')
        assert handshake.pending_edits(tmpdir,
                                       corel_dir=str(corel)) == [], \
            'quarantined CSV must not stay pending'
        time.sleep(0.01)
        out_csv.write_text('H;...fixed', encoding='utf-8')
        assert handshake.pending_edits(
            tmpdir, corel_dir=str(corel)) == [str(out_csv)], \
            'content change must lift the quarantine'
        time.sleep(0.01)
        handshake.write_imported_marker(str(out_csv), ['a'])
        assert handshake.pending_edits(tmpdir,
                                       corel_dir=str(corel)) == []
        # A foreign font's CSV in the same folder never routes here.
        (corel / 'sel-otherfont-111111.csv').write_text('H;...',
                                                        encoding='utf-8')
        assert handshake.pending_edits(tmpdir,
                                       corel_dir=str(corel)) == []
        # Corel's free-form save names route too, as long as the font
        # name appears in the filename ('<doctitle>_edited.csv').
        time.sleep(0.01)
        freeform = corel / f'{myslug}-c0ffee_edited.csv'
        freeform.write_text('H;...edited2', encoding='utf-8')
        assert handshake.pending_edits(
            tmpdir, corel_dir=str(corel)) == [str(freeform)]
        time.sleep(0.01)
        handshake.write_imported_marker(str(freeform), ['b'])
        assert handshake.pending_edits(tmpdir,
                                       corel_dir=str(corel)) == []
        print("✓ handshake pending logic (selections + edits + markers + "
              "inbox routing + corel exchange)")


def test_sync_edits_roundtrip():
    """Full file handshake without Corel, global-folders flow: one
    selections inbox + one corel exchange folder; the 'edited' file
    is the export re-saved in place, exactly as Corel would."""
    import time
    from penstroke import handshake
    from penstroke.cli import main as cli_main
    from penstroke import trace_font

    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / 'caveat'
        inbox = Path(tmpdir) / 'selections'
        corel = Path(tmpdir) / 'corel'
        inbox.mkdir(); corel.mkdir()
        trace_font(CAVEAT, str(out), font_name='Caveat',
                   letters='ab', verbose=False)

        # Drop a selection into the GLOBAL inbox (what preview.html
        # saves); routed via the "font" field.
        (inbox / 'sel-caveat-9f86d0.json').write_text(json.dumps(
            {'penstroke_selection': 1, 'font': 'Caveat', 'glyphs': 'a'}),
            encoding='utf-8')

        # A direct export-corel into the exchange folder writes its
        # outgoing sidecar, so it is NOT mistaken for a Corel return.
        cli_main(['export-corel', str(out),
                  '--csv', str(corel / 'caveat-manual.csv')])
        assert (corel / 'caveat-manual.csv.outgoing.json').exists()
        assert not handshake.pending_edits(str(out), corel_dir=str(corel)), \
            'a fresh export must not look like a return'

        sync = ['sync-edits', str(out),
                '--inbox', str(inbox), '--corel', str(corel)]
        cli_main(sync)
        csv = corel / 'sel-caveat-9f86d0.csv'
        assert csv.exists(), 'sync did not write the Corel CSV'
        assert (corel / 'sel-caveat-9f86d0.csv.outgoing.json').exists()
        assert not handshake.has_pending(str(out), inbox=str(inbox),
                                         corel_dir=str(corel))

        # A byte-identical rewrite (what a cloud sync or copy does) must
        # NOT count as an edited return — content decides, not mtime.
        time.sleep(0.05)
        csv.write_text(csv.read_text(encoding='utf-8'), encoding='utf-8')
        assert not handshake.pending_edits(str(out), corel_dir=str(corel)), \
            'identical-content rewrite must not become a return'

        # "Corel" re-saves the file with a real change (our own export
        # format is importable — B records flatten on read; a trailing
        # blank line is enough to change the content hash and is
        # skipped by the parser).
        time.sleep(0.05)
        csv.write_text(csv.read_text(encoding='utf-8') + '\n',
                       encoding='utf-8')

        store_before = (out / 'strokes.json').stat().st_mtime
        cli_main(sync)
        marker = json.loads(
            (corel / 'sel-caveat-9f86d0.csv.imported.json')
            .read_text(encoding='utf-8'))
        assert marker['imported_glyphs'] == ['a']
        assert (out / 'strokes.json').stat().st_mtime >= store_before
        assert not handshake.has_pending(str(out), inbox=str(inbox),
                                         corel_dir=str(corel))

        # Handle fidelity: the CSV carried B records, so the edited
        # glyph's exact cubics land in the store (for the bezier rep);
        # the un-edited glyph keeps none.
        from penstroke.editround import load_stroke_bez
        bez = load_stroke_bez(str(out))
        assert bez.get('a') and any(bez['a']), 'exact cubics not stored'
        assert 'b' not in bez, 'un-edited glyph should carry no cubics'

        # Third run: nothing pending, nothing rewritten.
        store_after = (out / 'strokes.json').stat().st_mtime
        cli_main(sync)
        assert (out / 'strokes.json').stat().st_mtime == store_after

        # A malformed CSV routed to this font (e.g. re-saved by Excel)
        # must not wedge the sync: it is quarantined with a .failed.json
        # marker and the sync completes.
        bad = corel / 'sel-caveat-baaaad.csv'
        bad.write_text('char,stroke,x,y\na,0,1,2\n', encoding='utf-8')
        cli_main(sync)   # must not raise
        assert (corel / 'sel-caveat-baaaad.csv.failed.json').exists(), \
            'failed import not quarantined'
        assert not handshake.has_pending(str(out), inbox=str(inbox),
                                         corel_dir=str(corel)), \
            'quarantined CSV still pending'
        print("✓ sync-edits roundtrip (inbox selection → corel CSV → "
              "edited return → merge → idempotent; exact cubics kept; "
              "malformed CSV quarantined)")


if __name__ == '__main__':
    test_imports()
    test_curvefit_reduces_polyline()
    test_trace_glyph_basic()
    test_trace_determinism()
    test_closed_loops_not_double_traced()
    test_store_write_is_atomic()
    test_full_pipeline_writes_expected_files()
    test_qa_verb()
    test_glyph_svgs_have_metadata_attributes()
    test_houdini_export()
    test_handshake_pending_logic()
    test_sync_edits_roundtrip()
    print("\nAll smoke tests passed.")
