# Penstroke — Claude Code project notes

## What this is

A Python pipeline that converts TTF fonts into hand-drawn stroke SVGs.
Each letter is decomposed into individual pen strokes using Hershey font
templates as a stroke-order prior, then re-rendered as animated SVG
that preserves the target font's variable-width calligraphic character.

The core insight: Hershey fonts (1960s public-domain stroke fonts)
already encode "an A is three strokes" as data. We use them as a
*template* and trace the target font's actual centerline geometry
through those template waypoints. This converts an underspecified
problem (what strokes are in this skeleton?) into a constrained one
(walk shortest paths between these known waypoints).

## Layout

```
src/penstroke/
├── __init__.py            Public API (lazy-imports trace_font)
├── __main__.py            `python -m penstroke` entry
├── cli.py                 argparse CLI (also installed as `penstroke` command)
├── pipeline.py            trace_font() — top-level: trace one font, emit folder
│
├── core/                  Primitives shared by all tracing strategies
│   ├── rasterize.py       TTF → fixed-canvas mask + font metrics
│   ├── skeleton.py        mask → medial axis + distance transform + spur pruning
│   ├── graph.py           skeleton → networkx graph, junction merging,
│   │                      parallel-edge collapse (medial-axis-split detection)
│   ├── strokes.py         template-free geometric stroke decomposition
│   │                      (fallback for non-Latin scripts)
│   └── smoothing.py       spline fit, per-point widths, OU-process wobble, taper
│
├── templates/             Hershey-template-guided tracing (production path)
│   ├── hershey.py         Load and cache Hershey font data
│   ├── topology.py        Skeleton/template topological fingerprints
│   ├── selection.py       Per-letter strategy: which Hershey font to use
│   ├── scripts.py         Unicode→Hershey mapping for Greek
│   └── trace.py           trace_glyph() — the production tracer
│
├── render/                Output formats
│   ├── svg.py             Path-building primitives (ribbon polygon, centerline)
│   ├── glyph.py           Single-letter SVG with positioning metadata
│   ├── alphabet.py        Grid view of all letters as one SVG
│   ├── word.py            HTML demo of letters composed into a word
│   └── houdini.py         JSON export for Houdini import (variable-width curves)
│
└── quality/               Automated QA
    ├── metrics.py         Coverage, stroke count match, has-strokes
    ├── ocr.py             Tesseract-based recognition check
    └── report.py          Assemble metrics into report.md and metadata.json

scripts/                   Batch runners
└── batch_google_fonts.py  Download + process many Google Fonts in one go

viewers/                   HTML template assets
└── wrapper_with_speed.html Interactive viewer (play/pause/speed/scrub/wireframe)

tests/
├── test_smoke.py          End-to-end smoke tests
└── fixtures/              Test fonts checked in
    ├── caveat.ttf         OFL-licensed; safe to commit
    └── LICENSE-Caveat.txt The SIL Open Font License

pyproject.toml             Install + dependencies
README.md                  User-facing docs
CLAUDE.md                  This file
```

## Common tasks

### Trace one font

```bash
penstroke trace path/to/MyFont.ttf output/myfont/
```

Produces:
```
output/myfont/
├── source/MyFont.ttf            Copy of original
├── glyphs/{a.svg, ..., cap_Z.svg}  Per-letter animated SVGs
├── alphabet_animated.svg        All letters as a grid, sequenced animation
├── alphabet_static.svg          Same grid, no animation
├── word_demo.html               "hello world" composition demo
├── metadata.json                Machine-readable per-letter index
└── report.md                    Human-readable QA report
```

### Run tests

```bash
python tests/test_smoke.py
```

(Also works as `pytest tests/` if pytest is installed.)

### Batch process Google Fonts

```bash
python scripts/batch_google_fonts.py output_root/
```

Downloads and processes a hardcoded list of Google Fonts. Edit the
`FONTS` list at the top to change which ones.

### Add fixes for specific letters

When a letter renders wrong consistently across fonts, the fix usually
goes in `src/penstroke/templates/selection.py` — the `LETTER_STRATEGY`
dict maps each character to an ordered list of Hershey fonts to try.

Examples already in there:
- `'a': ['cursive', 'rowmans']` — try single-story 'a' first
- `'Z': ['rowmans']` — force the 3-stroke template (cursive's 1-stroke Z
  looks like a 2)
- `'A': ['rowmans']` — same reason

### Add support for a new script (e.g., Cyrillic)

Hershey has `cyrillic` and `cyrilc_1` fonts with Cyrillic letters at
ASCII slot positions. Mirror the Greek pattern in `templates/scripts.py`:

1. Build a Unicode→ASCII slot mapping like `GREEK_MAP`.
2. Add a routing branch in `templates/selection.py::choose_template`
   that detects Cyrillic and routes to the right Hershey font.

For scripts without a Hershey template (Hebrew, Arabic, Devanagari),
the pipeline falls back to `core/strokes.py` geometric decomposition.
Quality varies; usable for simple letters, breaks down on complex ones.

## Conventions and design decisions worth knowing

- **Fixed canvas, consistent baseline.** Every letter is rendered onto
  the same-size canvas with the baseline at the same Y. This is what
  makes the per-letter SVGs composable into words — they share a
  coordinate frame. Metadata attributes (`data-baseline`, `data-advance`,
  `data-pad`) on each SVG let downstream tools typeset them.

- **Hershey templates as priors, not literal traces.** We sample
  waypoints along each Hershey stroke, map them into the target font's
  bounding box, snap to skeleton pixels, then walk shortest paths
  between them. The shape comes from the target font; only the
  decomposition (stroke count, order, roles) comes from Hershey.

- **Junction cluster merging.** The medial axis of an X-crossing
  produces ~3 degree-≥3 nodes within 10-15px of each other. We merge
  any cluster within 22px into a single junction (`graph.py
  ::merge_nearby_junctions`). Don't change this without checking
  rendering across the test fonts.

- **Parallel-edge collapse uses BOTH separation AND enclosed-area.**
  When a thick stroke's medial axis splits into two near-parallel
  branches, we want to collapse them. But the bowl of 'A' or 'B' also
  has two edges between the same endpoints — we don't want to collapse
  that. The discriminator is: small mean separation AND small enclosed
  polygon area. Either alone is insufficient; both required.

- **Wobble is post-spline-fit, perpendicular to local tangent.** An
  Ornstein-Uhlenbeck process produces correlated noise along each
  stroke; we apply it perpendicular to the tangent (plus a small
  along-tangent component for variety). Pure perpendicular wobble
  looks too uniform.

- **Taper profile at render time.** The width-multiplier curve in
  `core/smoothing.py::taper_profile` pinches stroke ends to ~5% of
  full width via a quarter-sine ease. Applied multiplicatively at
  ribbon-rendering time — not baked into the centerline widths.

- **OCR validation as feedback signal.** We render each traced glyph
  back to a raster, OCR it with tesseract, compare against the input
  character. Score reflects how well a neural OCR engine recognizes
  the trace. Confusable pairs (o/0/O, z/Z/2, l/I/1) don't penalize.

- **Two-path animation.** Each stroke has both an animated centerline
  guide (drawn-in via stroke-dasharray) and a filled ribbon (fades in
  behind the guide). The wireframe toggle hides the ribbon, leaving
  just the centerline animating. Guide stroke-width is 80% of the
  average filled width so wireframe matches each font's natural weight.

## Open work items

The full list from the original development sessions:

1. **Houdini JSON export exists but not wired into the pipeline.** The
   module `render/houdini.py` has `trace_to_dict()` and
   `write_letter_json()`. The pipeline's `trace_font()` doesn't call
   them — should emit a `glyphs.json` (or per-letter `glyphs/<x>.json`)
   alongside each font. ~10 lines of code.

2. **Hebrew templates would need hand-authoring.** No Hershey font
   covers Hebrew. The geometric fallback in `core/strokes.py` produces
   traceable but uneven results. Adding 22 hand-authored stroke
   templates in a new file like `templates/hebrew_templates.py` would
   bring Hebrew up to the same quality as Latin/Greek. ~4 hours.

3. **Cyrillic mapping (easy).** Hershey has cyrillic glyphs; just need
   the Unicode→ASCII slot map mirroring `GREEK_MAP` in
   `templates/scripts.py`. ~10 minutes.

4. **Coverage metric over-counts background for tall thin letters**
   (i, j, I, l). It uses disc-approximation along the centerline,
   which inflates pixel count for narrow glyphs. Should rasterize the
   ribbon polygon properly. ~30 minutes.

5. **Per-feature coverage metric.** Walk the skeleton's branches; for
   each branch, check that at least one traced stroke passes within
   tolerance. Catches missing serifs / dots / crossbars in a way
   pixel coverage doesn't. Discussed in the original session but not
   implemented. ~1 hour for the metric + a diagnostic visualization.

6. **Centerline-overlap metric.** Detect cases where two strokes
   retrace the same path (vs. legitimately crossing at a junction).
   Requires tangent-alignment test, not just position proximity.
   ~1 hour.

7. **Diagnostic visualization per letter.** A PNG showing original
   mask in gray + each traced stroke in distinct color + numbered
   start markers + skeleton overlay. Useful for human review and as
   input to AI-vision-based QA. ~30 minutes.

8. **Agent-based visual QA.** With the diagnostic visualization in
   place, an agent session can review per-letter PNGs and append
   findings to the report. Discussed but not built. The package is
   structured to support this workflow.

9. **Self-correcting pipeline.** If a letter fails OCR or feature-
   coverage, automatically retry with a different template and keep
   the best result. Infrastructure exists (the metrics return
   comparable scores), just needs wiring as a feedback loop in
   `templates/selection.py`.

10. **A regression. Some letters (notably 'A' historically) have
    flipped between template choices across runs.** The
    `LETTER_STRATEGY` map now locks several uppercase letters to
    rowmans to prevent this, but there may be more cases.

## What works well (worth not breaking)

- All ASCII printable characters trace successfully (tested on
  12+ fonts).
- All ASCII digits 0-9 work.
- Connected-component handling for multi-part glyphs (i, j, !, ?,
  ;, : — stem + dot) is robust.
- The Greek alphabet via Hershey `greek` is clean across all 48
  letters.
- Word composition from per-letter SVGs typesets correctly across
  fonts: baseline alignment, advance widths, descenders all work.
- The interactive HTML viewer (`viewers/wrapper_with_speed.html`)
  works in any modern browser without dependencies, supports
  pause/play/speed/scrub/wireframe.

## When working on this codebase

- **Run smoke tests after any change**: `python tests/test_smoke.py`.
  They cover: imports, basic trace correctness on a few letters, full
  pipeline file output, SVG metadata attributes, Houdini JSON schema.

- **Visual regression matters more than metrics.** A change that
  improves the coverage score from 0.85 to 0.90 might also make
  letter 'M' look subtly worse. Visually inspect alphabet_static.svg
  after non-trivial changes.

- **The Hershey font cache is process-global** (in `templates/hershey.py
  ::_cache`). Tests don't need to manage this.

- **Don't add per-letter hacks to `pipeline.py`.** New letter-specific
  behavior should go in `templates/selection.py` (template choice) or
  `templates/trace.py` (tracing logic). The pipeline is meant to be
  font-agnostic orchestration.

- **The diagnostic for "what went wrong with letter X"** is usually:
  look at the SVG, look at the metadata.json entry, look at any issues
  in report.md. If the template choice is wrong, edit
  `LETTER_STRATEGY`. If the trace shape is wrong even with the right
  template, dig into `templates/trace.py` waypoint snapping logic.

## External dependencies

All Python, all `pip install -e .` puts them in place:

- `numpy`, `scipy` — array math
- `scikit-image` — medial axis skeletonization
- `Pillow` — TTF rasterization to bitmap
- `fonttools` — read TTF metrics tables
- `networkx` — skeleton graph + shortest-path
- `Hershey-Fonts` — Hershey font data

Optional, used by `quality/ocr.py`:

- `pytesseract` (Python wrapper) AND `tesseract` (binary) — for the
  OCR validation metric. Pipeline runs fine without these; OCR metric
  is skipped if unavailable.
