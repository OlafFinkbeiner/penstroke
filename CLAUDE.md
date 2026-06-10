# Penstroke — Claude Code project notes

## What this is

A Python pipeline that converts TTF fonts into hand-drawn stroke SVGs.
Each letter is decomposed into individual pen strokes and re-rendered
as animated SVG that preserves the target font's variable-width
calligraphic character.

The core insight: **the glyph's skeleton is a graph, and stroke
decomposition is a graph problem.** The tracer (`src/penstroke/tracer.py`)
analyses every junction of the skeleton multigraph globally — pairing
edge-ends that continue smoothly into each other, terminating strokes
where the geometry turns sharply — then mechanically follows those
pairings into chains. One chain = one natural pen stroke. No stroke
templates, no per-letter rules, no order-dependent decisions.

(The original Hershey-template approach was removed in v0.2; it lives
in git history if ever needed.)

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
│   ├── skeleton.py        mask → medial axis (DETERMINISTIC, rng=0) +
│   │                      distance transform + spur pruning
│   ├── graph.py           skeleton → networkx multigraph, junction merging,
│   │                      parallel-edge collapse (with length-ratio guard)
│   ├── outline.py         TTF Bézier outlines → polygons in canvas coords
│   │                      (crisp underlay + outline-coverage QA)
│   ├── strokes.py         template-free geometric stroke decomposition
│   │                      (fallback for non-Latin scripts) + tangent_at +
│   │                      trace_closed_loops helpers
│   └── smoothing.py       spline fit, per-point widths, OU-process wobble, taper
│
├── tracer.py              THE tracer — junction-first graph decomposition.
│                          See module docstring for the full pipeline.
│
├── render/                Output formats
│   ├── svg.py             Path-building primitives (ribbon polygon, centerline)
│   ├── glyph.py           Single-letter SVG with positioning metadata
│   ├── alphabet.py        Grid view of all letters + make_preview_html
│   ├── diagnostic.py      Per-letter QA PNG: coloured strokes in order,
│   │                      numbered starts, per-stroke mini panels, flipbook
│   ├── viewer_template.html  Interactive preview (play/pause/speed/scrub/wireframe)
│   ├── word.py            HTML demo of letters composed into a word
│   └── houdini.py         JSON export for Houdini import
│
└── quality/               Automated QA
    ├── metrics.py         Coverage, stroke count match, has-strokes
    ├── glyph_image.py     Plain per-letter PNGs (input for vision-model QA)
    ├── spec_validate.py   Compare AI-derived spec.json vs traced output
    ├── cascade.py         Layered geometric/outline/spec checks → Issue list
    ├── ocr.py             Tesseract-based recognition check
    └── report.py          Assemble metrics into report.md and metadata.json

design/                    Active design docs
├── qa_cleanup_spec.json   QA/cleanup architecture (multi-lens synthesis)
├── epst_batch_qa_v2.json  Current 6-font QA: issue classes + verdicts
└── cascade_results_v2.json Deterministic detector findings + calibration notes

scripts/batch_google_fonts.py   Batch runner (edit FONTS list at top)
tests/test_smoke.py             End-to-end smoke tests
tests/fixtures/caveat.ttf       OFL-licensed test font
```

## Common tasks

### Trace one font

```bash
penstroke trace path/to/MyFont.ttf output/myfont/
```

Key outputs: `preview.html` (interactive viewer), `diagnostics/*.png`
(per-letter QA images — read these to judge trace quality),
`glyphs/*.svg`, `report.md`.

### Run tests

```bash
python tests/test_smoke.py
```

On Windows, prefix with `PYTHONIOENCODING=utf-8` (the tests print ✓).

### Judge trace quality (the workflow that matters)

1. Trace the font, open `output/<font>/preview.html`, watch at slow
   speed. The real test: **does it look like a person writing?**
2. For per-letter detail, Read `output/<font>/diagnostics/<letter>.png`.
   The diagnostic shows each stroke in rainbow order with numbered
   starts, direction arrows, per-stroke panels, and a flipbook strip
   of animation frames. An agent can judge these images directly.
3. For systematic review, the AI-spec workflow: render `glyphs_raw/`
   PNGs (automatic), fan out vision agents per letter to produce
   `spec.json`, then `quality/spec_validate.py` and
   `quality/cascade.py` compare the trace against it.

### Fix a tracing defect

**Never add per-letter fixes — always generalize** (explicit project
rule). When a specific letter looks wrong, treat it as a specimen of a
class: diagnose the mechanism, survey how many other letters share it,
fix the mechanism. Where to look:

- Stroke decomposition wrong (too many/few strokes, wrong split):
  `tracer.py::analyze_junctions` (the pairing threshold
  `MAX_CONTINUATION_TURN_DEG`) and `build_chains`.
- Strokes wander off the ink / strays: hygiene passes in
  `tracer.py::build_annotated_graph` (scrambled-path
  repair, duplicate removal) and
  `core/graph.py::collapse_parallel_edges`.
- Wrong direction/order: `_orient_walk_for_writing`,
  `_orient_clockwise_if_closed`, `order_all_walks` in tracer.py.
- Missing dots/tittles: `_split_mask_dots` in tracer.py.
- Skeleton itself wrong: `core/skeleton.py` (pruning) — but check the
  graph hygiene passes first.

## Conventions and design decisions worth knowing

- **Junction-first, never greedy.** All junction continuation decisions
  are made globally by `analyze_junctions` BEFORE any tracing. Walking
  happens only after every pairing is decided. This was a hard-won
  lesson: greedy Hierholzer walking made corner decisions depend on
  which edges were already consumed, producing serpentine traces.

- **Determinism is load-bearing.** `core/skeleton.py` passes `rng=0`
  to skimage's `medial_axis` — without it, the same glyph yields
  different skeletons across runs (random tie-breaking), which changes
  graph topology and stroke counts. Symptom was "intermittent stray
  strokes". Don't remove this.

- **Graph hygiene before decomposition.** `skeleton_to_graph` emits
  some edges twice, and the duplicate's pixel order can be SCRAMBLED
  (same pixel set, arc length 2-3× too long). `build_annotated_graph`
  repairs ordering (nearest-neighbour walk) and drops duplicates.
  Resampling a scrambled path interpolates across white space — that
  was the root cause of the stray-stroke class.

- **Parallel-edge collapse needs the length-ratio guard.** A true
  medial-axis split has two near-equal-length paths; a hook's chord
  and arc have very different lengths and must NOT be averaged
  (averaging draws a garbage line through the enclosed region). See
  `core/graph.py::collapse_parallel_edges`.

- **Fixed canvas, consistent baseline.** Every letter renders onto the
  same-size canvas with the baseline at the same Y; per-letter SVGs
  carry `data-baseline`/`data-advance`/`data-pad` so downstream tools
  can typeset them.

- **Wobble is post-spline-fit, perpendicular to local tangent**
  (OU process); taper applied multiplicatively at ribbon-render time.

- **Two-path animation.** Each stroke has an animated centerline guide
  (stroke-dasharray draw-in, hidden until its start time to avoid
  round-linecap dots) and a filled ribbon that fades in behind it.
  Wireframe mode hides the ribbon.

- **Windows portability**: every text-mode `open()` passes
  `encoding='utf-8'` — the cp1252 default crashes on the Unicode in
  generated SVG/HTML. Keep doing this.

## What works well (worth not breaking)

- EPST decomposes unseen fonts of every style — script (Caveat,
  DancingScript), serif (EB Garamond), slab (Arvo), sans (Lato),
  display (Lobster) — with no per-font configuration. Validated via a
  312-diagnostic agent QA sweep (see design/epst_batch_qa.json,
  pre-junction-first numbers; the junction-first rewrite fixed the two
  dominant issue classes found there).
- Serifed capitals write naturally: H = stem, stem, crossbar, serif
  flicks; A = diagonal, diagonal, crossbar, feet.
- Script letters stay flowing: cursive m is 1-2 strokes, not 5.
- Multi-part glyphs (i, j, !, ?, :, ;) — stem + dot, dots drawn last.
- Word composition from per-letter SVGs typesets correctly (baseline,
  advance widths, descenders).
- The interactive preview.html works in any browser, no dependencies.

## Open work items

1. **Re-run the 6-font agent QA sweep** post-junction-first to
   quantify the improvement (pre-rewrite baseline: ~60% clean letters,
   issue catalog in design/epst_batch_qa.json).
2. **Residual issue classes from that catalog**: over_split on heavy
   serif fonts (short serif stubs as separate mini-strokes — consider
   width-scaled spur handling), occasional stray on extreme cursive
   terminals (an on-ink clip pass would be a cheap safety net).
3. **Houdini JSON export not wired into the pipeline**
   (`render/houdini.py` exists; `trace_font` doesn't call it).
4. **Non-Latin scripts**: the tracer is script-agnostic by construction
   (pure geometry) but untested on Greek/Cyrillic/Hebrew since the
   Hershey-based Greek path was removed in v0.2.
5. **The AI-spec workflow is manual.** `glyphs_raw/` renders
   automatically, but producing `spec.json` requires running the
   vision-agent fan-out by hand (in-session workflow). Could become a
   CLI step.

## External dependencies

All Python, `pip install -e .`:

- `numpy`, `scipy` — array math
- `scikit-image` — medial axis skeletonization
- `Pillow` — TTF rasterization + diagnostic PNGs
- `fonttools` — TTF metrics + outline extraction
- `networkx` — skeleton multigraph

Optional: `pytesseract` + tesseract binary for the OCR metric
(skipped if unavailable).
