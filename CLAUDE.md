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
│   ├── nib.py             Closed-form pen recovery: one lstsq over
│   │                      (tangent angle, width) gives nib angle,
│   │                      contrast, thick/thin widths + R² confidence
│   ├── strokes.py         trace_closed_loops (pure-cycle components that
│   │                      produce no graph nodes; the tracer's orphan-
│   │                      loop pass)
│   ├── smoothing.py       spline fit, per-point widths, OU-process wobble, taper
│   └── vector_skeleton.py Medial axis straight from the Bézier outline
│                          (Voronoi, no raster) — B0 in tracer_math_plan.md.
│                          NOT wired into skeletonize()/the tracer; real,
│                          tested code but a standalone alternate path.
│                          Needs shapely (dev-only dependency).
│
├── tracer.py              THE tracer — junction-first graph decomposition.
│                          See module docstring for the full pipeline.
│
├── hfont.py               The .hfont bundle format (manifest + reps over a
│                          source TTF) — the ONE implementation, hou-free
├── layout.py              Pure-python text layout engine (HarfBuzz shaping,
│                          greedy breaker, justify) — called by the Houdini
│                          text_layout SOP, benchmarkable standalone
├── charset.py             Charset presets, importable under hython
│                          (no scipy/skimage)
├── curvefit.py            Schneider cubic-Bézier fitting (numpy-only, so
│                          hython can import it) — shared by the Corel
│                          export and the bezier strokes rep
├── editround.py           Corel CSV edit-round workflow (export/merge)
├── handshake.py           File-handshake conventions for the edit round:
│                          TWO global drop folders at repo root —
│                          selections/ (preview.html sel-*.json, routed
│                          per font) and corel/ (CSV out AND edited
│                          return, same file both ways, mtime+sidecar
│                          state); dependency-light, hython-importable
├── fontscan.py            Font-source discovery (Google Fonts checkouts,
│                          trace outputs, plain TTF dirs) for the TOPs graph
│
├── houdini/               Builders that run under hython (need `hou`)
│   ├── rep_outline.py     TTF beziers → em-space curves2d rep
│   └── rep_strokes.py     strokes.json → em-space centerline rep (dense
│                          polyline = raw) + strokes_bezier rep (reduced
│                          order-4 Bézier curves via curvefit, tessellate
│                          on demand in Houdini)
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
    ├── objective.py       THE decomposition score (reconstruction,
    │                      continuation, parsimony, smoothness). Use it to
    │                      compare two traces. NOTE: `total` is only valid
    │                      within one skeleton configuration — across
    │                      different pruning use `reconstruction`.
    ├── cascade.py         Layered geometric/outline/spec checks → Issue list
    ├── ocr.py             Tesseract-based recognition check
    └── report.py          Assemble metrics into report.md and metadata.json

docs/houdini_workflow.md   End-user Houdini runbook (setup, trace,
                           Corel round-trip, text layout, troubleshooting)

design/                    Active design docs
├── hfont_houdini_plan.md  Houdini TOPs + hfont plan (phase status lives here)
├── qa_cleanup_spec.json   QA/cleanup architecture (multi-lens synthesis)
├── epst_batch_qa_v2.json  Current 6-font QA: issue classes + verdicts
├── cascade_results_v2.json Deterministic detector findings + calibration notes
├── animation_handoff.md   What's available for animating the output:
│                          stroke attributes (u/width/arclength/
│                          stroke_index) + layout attrs (idx/word/line/
│                          charinword), how the draw-on works today
├── code_concept_review.md Full code+concept review (2026-07) with the
│                          action list — items 1-10 done; concept bets
│                          remain
├── tracer_quality_plan.md Implementation specs for the remaining
│                          concept bets (width-continuity pairing,
│                          width sampling, vector reprojection, store
│                          key migration) + post-re-trace checklist —
│                          partly superseded by tracer_math_plan.md
└── tracer_math_plan.md    THE live tracer design doc: resolution-
                           invariance measurements, the A0-A3/B0-B2/C0-C4
                           plan, and dated findings as work lands. Read
                           this before any tracer or vector_skeleton change.

scripts/batch_google_fonts.py   Small fixed demo batch (edit FONTS list)
scripts/batch_handwriting.py    Batch runner: every HANDWRITING family
                                from a google/fonts checkout, resumable
scripts/trace_sweep.py          Tracer regression sweep: trace a charset,
                                dump counts/arclens, --compare two sweeps.
                                RUN THIS before/after ANY tracer change.
scripts/proto_vector_medial_axis.py  Report harness (invariance table,
                                theta sweep) for src/penstroke/core/
                                vector_skeleton.py — the implementation
                                lives there now, not in this script.
                                Resolution-invariant 40/40 vs raster 33/56.
scripts/build_tops_graph.py     Builds penstroke_tops.hip; --make-hda
                                packages it as the penstroke::tops HDA
scripts/build_text_layout_hda.py Builds penstroke::text_layout HDA
scripts/install_houdini_package.py Installs <prefs>/packages/penstroke.json
                                ($PENSTROKE, PYTHONPATH, otls auto-scan)
scripts/run_trace.cmd           PDG job launcher (scrubs Houdini's PYTHONPATH)
scripts/run_penstroke.cmd       Same, generic (any penstroke subcommand)
tests/test_smoke.py             End-to-end smoke tests
tests/test_layout.py            Layout engine tests (no Houdini needed)
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
pytest
```

On Windows, prefix with `PYTHONIOENCODING=utf-8` (the tests print ✓).

### Change the tracer (regression protocol)

Any change to tracer.py or core/ follows the sweep protocol in
design/tracer_quality_plan.md: `scripts/trace_sweep.py` before/after
(baseline via git worktree), `--compare`, zero unexplained stroke-count
changes, visual diagnostics check, `pytest`. This caught a real
regression (arclen doubling from a dedup interaction) during the A5/A6
fixes — do not skip it.

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
- Wrong direction/order: `tracer.py::order_all_walks` (role classification
  via `_classify_attachments` — main vs. secondary/attached strokes —
  plus closed-loop direction via `_reorient_loop`).
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

0. **Post-re-trace verification** (a full-library re-trace ran
   2026-07-05 after the review-batch tracer fixes — '8'-class
   double-trace, A5/A6 geometry): follow the checklist at the end of
   design/tracer_quality_plan.md. Old traces parked in
   `output/handwriting_pre_retrace/`; wanted Corel edits re-apply by
   deleting their `corel/*.csv.imported.json` markers and cooking.
1. **Re-run the 6-font agent QA sweep** to quantify the improvement
   (pre-junction-first baseline: ~60% clean letters, issue catalog in
   design/epst_batch_qa.json). Also produces the over_split numbers
   that calibrate P1 below.
2. **Residual issue classes**: over_split on heavy serif fonts — root cause
   found and fixed 2026-07-25 (see design/tracer_math_plan.md "A0 revised"):
   `prune_skeleton`'s length threshold and `prune_redundant_leaves`'
   ink-coverage test were both deciding feature-vs-noise, and a
   miscalibrated `TIP_CLEARANCE_SIGMAS` gate silently skipped the
   ink-coverage test for its entire target population. Now
   `prune_skeleton` is deliberately conservative (0.5) and defers that
   judgment to `prune_redundant_leaves`. Still architecturally unsettled:
   three separate hand-set pruning mechanisms (σ-blur, length threshold,
   disk-coverage) doing overlapping jobs — B0/C2 in tracer_math_plan.md
   propose collapsing them into one λ/θ-medial-axis filtration with a
   stability theorem instead of tuned constants. B0's prototype is now
   real code (`core/vector_skeleton.py`): winding-rule and overlapping-
   contour bugs found and fixed, a third risk (near-degenerate-contour
   noise — near-coincident Voronoi vertices at pinches/blunt tips) found
   and root-caused but NOT fixed (three merge heuristics tried across two
   sessions, all had worse side effects than the bug, or just moved the
   failure mode). C2 is now scoped for real (2026-07-25, tracer_math_plan.md):
   the formal λ-medial-axis definition, why a quick arc-length proxy also
   failed today (same root cause as the merge heuristics — conflating
   "whatever sample a Voronoi ridge happens to touch" with the true
   nearest-distance contact set), and a 6-step implementation/validation
   plan — but zero implementation of the real algorithm exists yet. Still
   not wired into `skeletonize()`/the tracer — read tracer_math_plan.md's
   B0 and C2 sections before attempting that integration or the noise fix.
   The width-underestimate in tight curves still has its old spec at
   design/tracer_quality_plan.md P2,
   now itself superseded by tracer_math_plan.md B2 (subsumed by B0 if
   that lands). Follow the sweep protocol in tracer_math_plan.md for any
   tracer change — the resolution-invariance check is the stronger
   tripwire.
3. **Houdini integration: phase 4 remainder.** Phases 1-3 of
   design/hfont_houdini_plan.md are done (hfont standard, layout
   engine + text_layout HDA, strokes rep, handwriting demo), and the
   TOPs graph includes the Corel file-handshake stage (sync_edits;
   `penstroke sync-edits`, validated with real Corel passes) and is
   packaged as the penstroke::tops HDA + Houdini package file. Still
   open: wedging variants. (`render/houdini.py` is the legacy
   per-letter JSON export, superseded by the hfont strokes rep.)
4. **Non-Latin scripts**: the tracer is script-agnostic by construction
   (pure geometry) but untested on Greek/Cyrillic/Hebrew since the
   Hershey-based Greek path was removed in v0.2.
5. **The AI-spec workflow is manual.** `glyphs_raw/` renders
   automatically, but producing `spec.json` requires running the
   vision-agent fan-out by hand (in-session workflow). Could become a
   CLI step.
6. ~~**Pen-width analysis for the strokes.**~~ **DONE** — `core/nib.py`
   fits an elliptical nib in closed form; the result lands in
   `metadata.json` under `pen` (angle, contrast, thick/thin, R²). Widths
   themselves still come from the distance transform — replacing that
   with an outline ray-cast is B2/B0 in design/tracer_math_plan.md.

7. **Scale-freeness is now load-bearing.** Every length in tracer.py is a
   multiple of `W = glyph_scale(skel, dist)` (the glyph's own stroke
   width) — see the SCALE block at the top of the file. The ONE
   exception is `TIP_CLEARANCE_SIGMAS`, which measures blur-induced
   corner rounding and therefore scales with `SKELETON_SIGMA`, not `W`.
   Adding a bare pixel constant re-introduces the resolution bug that
   A0/A1 fixed (invariance 58/84 → 69/84); express it in `W` instead.

## External dependencies

All Python, `pip install -e .`:

- `numpy`, `scipy` — array math
- `scikit-image` — medial axis skeletonization
- `Pillow` — TTF rasterization + diagnostic PNGs
- `fonttools` — TTF metrics + outline extraction
- `networkx` — skeleton multigraph

Optional: `pytesseract` + tesseract binary for the OCR metric
(skipped if unavailable).
