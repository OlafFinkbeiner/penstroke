# penstroke

Convert TTF fonts into hand-drawn pen-stroke SVGs. Each letter is
decomposed into the right number of strokes in the right order, then
re-rendered as an animated SVG that mimics a pen drawing the letter —
preserving the font's variable-width calligraphic character.

Works on handwriting fonts (Caveat, Indie Flower, Kalam, Pacifico),
display fonts (Lobster, Bebas Neue), serif fonts (EB Garamond, Playfair),
sans-serif fonts (Roboto, DM Sans), monospace (JetBrains Mono), and
non-Latin scripts (Greek; Hebrew via geometric fallback).

## Quick start

```bash
git clone <this-repo>
cd penstroke
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
penstroke trace path/to/MyFont.ttf output/myfont/
```

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
   junctions, edges carry their pixel paths, arc lengths, and end
   tangents. Hygiene passes repair scrambled paths, drop duplicate
   edges, and collapse medial-axis splits of thick strokes.
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
├── source/                          # Original font + license
│   ├── MyFont.ttf
│   └── LICENSE.txt
├── glyphs/                          # Per-letter animated SVGs
│   ├── a.svg ... z.svg
│   └── cap_A.svg ... cap_Z.svg
├── glyphs_raw/                      # Plain per-letter PNGs (vision-QA input)
├── diagnostics/                     # Per-letter QA images: coloured strokes,
│                                    #   numbered starts, per-stroke panels,
│                                    #   animation flipbook
├── alphabet_animated.svg            # All letters as a grid, sequenced
├── alphabet_static.svg              # Same grid, no animation
├── preview.html                     # Interactive viewer (play/pause/speed/
│                                    #   scrub/wireframe)
├── word_demo.html                   # "hello world" composition demo
├── metadata.json                    # Machine-readable per-letter index
└── report.md                        # Human-readable QA report
```

Each per-letter SVG is rendered on the same-size canvas with the
baseline at the same Y, and carries data attributes describing its
font-metric position. Letters can be composed into words by simple
horizontal stacking — see `word_demo.html` for the working pattern.

## Batch processing

```bash
python scripts/batch_google_fonts.py output_root/
```

Downloads a hardcoded list of Google Fonts and processes each.
Produces a top-level `index.html` linking to each font's outputs.

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
python tests/test_smoke.py
```

The smoke tests exercise: imports, basic trace correctness, the full
pipeline file output, per-letter SVG positioning metadata, and the
Houdini JSON export schema.

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
