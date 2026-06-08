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

1. **Rasterize** the TTF glyph onto a fixed canvas with consistent
   baseline (so per-letter outputs share a coordinate frame).
2. **Skeletonize** to get the centerline plus distance-to-boundary
   transform.
3. **Pick a Hershey template** — Hershey's 1960s stroke fonts already
   encode "an 'A' is 3 strokes drawn in this order". We use topology
   matching to pick the right variant per-letter (single-story vs
   double-story 'a', open-tail vs closed-tail 'g', etc.).
4. **Snap Hershey waypoints** onto the target font's skeleton, walk
   shortest paths through the skeleton between them.
5. **Render** as animated SVG with per-point widths from the distance
   transform and tapered entry/exit.

The key trick: instead of asking "what strokes are in this skeleton?"
(under-specified, needs tricky heuristics), we ask "given that there
should be a stroke from here to there to there, what skeleton path
matches?" — a constrained, robust question with a shortest-path answer.

## Output structure

```
output/myfont/
├── source/                          # Original font + license
│   ├── MyFont.ttf
│   └── LICENSE.txt
├── glyphs/                          # Per-letter animated SVGs
│   ├── a.svg ... z.svg
│   └── cap_A.svg ... cap_Z.svg
├── alphabet_animated.svg            # All letters as a grid, sequenced
├── alphabet_static.svg              # Same grid, no animation
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

## Repo layout

See [CLAUDE.md](CLAUDE.md) for the full module-by-module breakdown.
Short version:

- `src/penstroke/core/` — rasterize, skeletonize, graph, smoothing
- `src/penstroke/templates/` — Hershey-template-guided tracing
- `src/penstroke/render/` — SVG, alphabet, word, Houdini outputs
- `src/penstroke/quality/` — coverage + OCR + report assembly
- `src/penstroke/pipeline.py` — top-level `trace_font()` entry
- `scripts/` — batch runners
- `viewers/` — HTML templates
- `tests/` — smoke tests, fixtures

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

Built around the [Hershey font catalog](https://en.wikipedia.org/wiki/Hershey_fonts)
by A.V. Hershey at the U.S. Naval Weapons Laboratory (1967), now
public domain. Without those 60-year-old stroke definitions, the
template-guided approach in this package wouldn't be possible.
