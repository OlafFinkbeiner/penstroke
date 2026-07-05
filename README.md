# penstroke

Convert TTF fonts into hand-drawn pen-stroke SVGs. Each letter is
decomposed into the right number of strokes in the right order, then
re-rendered as an animated SVG that mimics a pen drawing the letter —
preserving the font's variable-width calligraphic character.

Works on unseen fonts of every style with no per-font configuration:
script (Caveat, Dancing Script), serif (EB Garamond), slab (Arvo),
sans (Lato), display (Lobster). The tracer is pure geometry — no
stroke-order database, no templates — so it is script-agnostic by
construction, though it is currently only validated on Latin.

**Full user documentation: [docs/user_guide.md](docs/user_guide.md)**
(all CLI commands, the quality-judging workflow, the CorelDRAW edit
round-trip, batch tracing, Houdini).

## Quick start

```bash
git clone <this-repo>
cd penstroke
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
penstroke trace path/to/MyFont.ttf output/myfont/
```

Then open `output/myfont/preview.html` in a browser and watch the
alphabet write itself. The `penstroke` CLI has more verbs — `qa`,
`export-corel`, `import-corel`, `sync-edits`, `refresh-previews`,
`build-bundles` — documented in the [user guide](docs/user_guide.md).

For OCR-based quality validation, also install Tesseract:

```bash
# macOS:
brew install tesseract
# Linux:
sudo apt install tesseract-ocr
# Then:
pip install pytesseract
```

## How it works

The default tracer (EPST — the junction-first graph tracer) treats the
glyph's skeleton as a graph and lets the topology decide the strokes:

1. **Rasterize** the TTF glyph onto a fixed canvas with consistent
   baseline (so per-letter outputs share a coordinate frame).
2. **Skeletonize** (deterministic medial axis) to get the centerline
   plus distance-to-boundary transform. Disconnected dots (the tittles
   of i/j) are split off as tap-strokes.
3. **Build the skeleton multigraph** — nodes are endpoints and
   junctions, edges carry their pixel paths and arc lengths. Hygiene
   passes repair scrambled paths, drop duplicate edges, bridge
   junction-merge splices, and collapse medial-axis splits of thick
   strokes.
4. **Analyse all junctions globally** — at every junction, incident
   edge-ends are optimally paired by tangent continuity: two ends pair
   when a pen could continue from one into the other without turning
   sharply (≤ 75°). Unpaired ends are stroke terminals. Every junction
   is decided *before* any tracing, so no decision depends on
   traversal order.
5. **Build chains** — mechanically follow the pairings. Every skeleton
   edge lands in exactly one chain; each chain is one natural pen
   stroke. An 'H' comes out as two stems + a crossbar (+ serif flicks);
   a cursive 'm' as one continuous wave.
6. **Orient and order** — closed loops start at their topmost point and
   run clockwise; open strokes run top-to-bottom / left-to-right; main
   strokes are drawn before accessories, dots last.
7. **Render** as animated SVG with per-point widths from the distance
   transform, hand-drawn wobble, and tapered entry/exit.

No stroke-order database, no per-letter rules, no templates: a font
never seen before decomposes correctly because the rules are pure
geometry.

## Output structure

```
output/myfont/
├── source/MyFont.ttf                # Copy of the input font
├── glyphs/                          # Per-letter animated SVGs
│   ├── a.svg ... z.svg
│   └── cap_A.svg ... cap_Z.svg
├── glyphs_raw/                      # Plain per-letter PNGs (vision-QA input)
├── diagnostics/                     # Per-letter QA images: coloured strokes,
│                                    #   numbered starts, per-stroke panels,
│                                    #   animation flipbook
├── strokes.json                     # THE STROKE STORE — source of truth for
│                                    #   the decomposition; hand edits (Corel
│                                    #   round-trip) merge into this file
├── alphabet_animated.svg            # All letters as a grid, sequenced
├── alphabet_static.svg              # Same grid, no animation
├── preview.html                     # Interactive viewer (play/pause/speed/
│                                    #   scrub/wireframe + glyph selection)
├── word_demo.html                   # "hello world" composition demo
├── metadata.json                    # Machine-readable index: per-letter
│                                    #   files/metrics + trace parameters
└── report.md                        # Human-readable QA report
```

Each per-letter SVG is rendered on the same-size canvas with the
baseline at the same Y, and carries data attributes describing its
font-metric position. Letters can be composed into words by simple
horizontal stacking — see `word_demo.html` for the working pattern.

## Batch processing

```bash
# Every handwriting family from a google/fonts checkout (resumable):
python scripts/batch_handwriting.py path/to/google-fonts output/handwriting/

# Or the small fixed demo set (downloads 6 fonts):
python scripts/batch_google_fonts.py output_root/
```

Both produce a top-level `index.html` linking to each font's outputs.
The Houdini TOPs graph (below) is the batch runner with caching and
parallelism.

## Houdini pipeline

Beyond the SVG side, penstroke packs each font into an `.hfont` bundle
(em-space glyph geometry) that Houdini consumes for text layout and
hand-drawn-stroke animation, with a CorelDRAW round-trip for
hand-correcting strokes — all driven from a TOPs network, no Python
needed. See **[docs/houdini_workflow.md](docs/houdini_workflow.md)**.

## Repo layout

See [CLAUDE.md](CLAUDE.md) for the full module-by-module breakdown.
Short version:

- `src/penstroke/core/` — rasterize, skeletonize, graph, outline, smoothing
- `src/penstroke/tracer.py` — the junction-first graph tracer
- `src/penstroke/render/` — SVG, alphabet, diagnostics, word, Houdini outputs
- `src/penstroke/quality/` — coverage + OCR + cascade QA + report assembly
- `src/penstroke/pipeline.py` — top-level `trace_font()` entry
- `scripts/` — batch runners
- `tests/` — smoke tests, fixtures
- `design/` — active design specs + current QA catalog

## Testing

```bash
pytest                        # all suites (smoke + layout + fontscan)
python tests/test_smoke.py    # smoke suite alone, script mode
```

The suites cover: imports, trace correctness (determinism, loop
handling), the full pipeline output contract, atomic store writes, the
`qa` verb, the sync-edits file handshake, the text layout engine, and
font-source discovery. On Windows consoles, prefix with
`PYTHONIOENCODING=utf-8` (the tests print ✓ marks).

## License

This source code is MIT-licensed (see [LICENSE](LICENSE)).

Fonts in `tests/fixtures/` are separately licensed under SIL OFL;
see `tests/fixtures/LICENSE-*.txt` for each font's attribution.

## Acknowledgments

The original prototype was built around the
[Hershey font catalog](https://en.wikipedia.org/wiki/Hershey_fonts)
(A.V. Hershey, U.S. Naval Weapons Laboratory, 1967, public domain) as
a stroke-order prior. The project has since moved to a pure
graph-theoretic decomposition that needs no stroke templates; the
Hershey-based tracer lives on only in the git history.
