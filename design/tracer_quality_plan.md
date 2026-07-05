# Tracer quality plan — implementation specs for the remaining concept bets

Written 2026-07-05, after the code/concept review
([code_concept_review.md](code_concept_review.md)) whose action list is
otherwise complete. These are the four remaining items, specced at
implementation level so they can be executed incrementally. Order
matters: P1 and P2 are independent quick-ish wins; P3 is a larger
bet to evaluate only if animation fidelity demands it; P4 is a format
decision to make before enabling ligatures.

**Non-negotiable protocol for every tracer change** (this caught a real
regression during the A5/A6 work):

1. `python scripts/trace_sweep.py tests/fixtures/caveat.ttf before.json`
   with baseline code (git worktree), same with changed code → `after.json`,
   then `--compare`. Zero unexplained stroke-count changes.
2. Repeat on the 6-font QA set (Caveat, DancingScript, EB Garamond,
   Arvo, Lato, Lobster) — style diversity is the point; Caveat alone
   under-tests serif behavior.
3. Visual: trace, open preview.html slow + diagnostics of changed glyphs.
4. `pytest` (determinism + loop-coverage tests are the tripwires).

---

## P1. Width continuity in junction pairing (review D2)

**Target defect class:** `over_split` on serif fonts — short serif stubs
paired into (or split from) stems purely by angle. A thin serif meeting
a thick stem at a passable angle currently reads as one continuing pen
motion despite a 3× width discontinuity. Dual failure: a stem smoothly
tangenting into a serif exit gets *joined* when a human would lift the
pen. This is the dominant residual class in the epst_batch_qa catalog.

**Where the code decides today:**
- `tracer.py::_edge_ends_at(G, v)` — collects each incident edge-end at
  node v with an outward unit tangent (20-pixel index window).
- `tracer.py::_pair_score(t_a, t_b)` — angle-only: turn degrees between
  ends; `analyze_junctions` accepts pairs with turn ≤
  `MAX_CONTINUATION_TURN_DEG` (75°) and picks the min-total-turn
  matching.

**Change:**
1. In `build_annotated_graph` (which has `dist_map`), annotate each edge
   with end widths: `d['w_u']`, `d['w_v']` = mean of
   `dist_map[path pixels] * 2` over the first/last
   `min(20, len(path)//2)` pixels, SKIPPING the ~3 pixels nearest the
   node (junction blobs inflate the distance transform — that bulge is
   exactly what we must not measure). NB: annotate AFTER
   `fill_path_gaps` so bridge pixels are on-ink-ish; even so, exclude
   bridge-origin pixels if trivially identifiable, else accept the
   small bias (bridges are short).
2. Thread the widths through `_edge_ends_at` into the pairing:
   `_pair_score` gains a width term. Two candidate formulations, pick
   by calibration:
   - **Soft penalty (preferred):** effective_turn = turn_deg +
     `WIDTH_PENALTY_DEG * max(0, ratio - RATIO_FREE)` where
     `ratio = max(w_a, w_b) / max(min(w_a, w_b), 0.5)`. Start
     `RATIO_FREE = 1.6` (free modulation range),
     `WIDTH_PENALTY_DEG = 25.0` per ratio unit. A 3× discontinuity then
     adds ~35° — enough to push a 45° serif joint past the 75° gate
     without banning genuine thick-thin transitions.
   - **Hard gate:** ratio > `RATIO_MAX` (≈3.0) → pair invalid
     regardless of angle. Simpler but brittle on contrast fonts.
3. Keep the global optimal matching machinery unchanged — only the
   pairwise score changes.

**The big pitfall — width modulation is a FEATURE in script/contrast
fonts.** A Caveat stroke legitimately thins 2-3× through a turn;
Playfair-class contrast fonts alternate thick/thin *within* one stroke.
A naive ratio gate fragments exactly the fonts that work best today.
Mitigations, in order of preference:
- Measure widths a few px away from the junction (step 1 above) — most
  modulation happens along the stroke, not discontinuously at the meet.
- Normalize the ratio by a font-level modulation statistic: collect all
  end widths per glyph (already cheap), let
  `RATIO_FREE = max(1.6, p90(end_width_ratios_of_paired_degree2_nodes))`
  — i.e. learn the font's tolerated modulation from its own smooth
  continuations. Optional second iteration; ship the constant first.

**Calibration/verification:** sweep the 6-font set. Success = serif
fonts (EB Garamond, Arvo) lose over_split mini-strokes (counts DROP on
serifed capitals like H/E/T), script fonts (Caveat, DancingScript)
change NOTHING (any count increase there = the pitfall fired; raise
`RATIO_FREE`). Then re-run the agent QA sweep on diagnostics to confirm
the class is gone rather than moved.

---

## P2. Widths sampled along the pixel walk, not the spline (review D6)

**Target defect:** in tight curves the smoothing spline deviates from
the skeleton ridge; sampling the distance transform at *spline* points
under-measures width by ~2× the deviation (dist falls off ~1 px/px off
the ridge). Visible as anemic ribbons in small bowls and hooks.

**Where:** `core/smoothing.py::smooth_and_wobble` — widths currently
come from `dist_map` sampled at the fitted-spline sample positions
(pre-wobble — that part is correct and must stay).

**Change:** sample `w_raw[i] = dist_map[path[i]] * 2` along the RAW
pixel walk (on-ridge by construction), with cumulative arc length
`s_raw`. After spline fitting produces 240 samples with arc-length
fractions `u`, set `widths = np.interp(u * s_raw[-1], s_raw, w_raw)`,
then keep the existing `uniform_filter1d` smoothing and the wobble
sequencing untouched. Width now follows the *material* the pen covers,
not wherever the spline drifted.

**Interactions:** the junction-bulge artifact (width spikes where
strokes cross, because dist measures the blob) is UNCHANGED by this —
it's a separate, smaller fix (median-filter the width sequence with a
window ~ the local width, or cap at the per-stroke p85) and should be
its own commit if pursued. Don't conflate the two in one change.

**Verification:** sweep must show ZERO stroke-count changes (this
touches rendering inputs only). Judge visually: hooks/bowls in Caveat
'e', 'g', Lobster terminals — ribbons should fill the ink where they
previously pinched. The coverage metric should tick UP slightly across
the board; any decrease = bug.

---

## P3. Vector re-projection of chain geometry (review D4) — evaluate, not build, first

**Idea:** keep the raster skeleton for TOPOLOGY (junction/pairing
decisions are robust there) but re-project each chain's geometry onto
the true centerline derived from the vector outline
(`core/outline.py` already extracts exact polygons): for each chain
sample, intersect the local normal with the outline on both sides,
midpoint = exact centerline; fall back to the raster point where the
normal intersects ≠ 2 boundary walls (junction zones, terminals).

**Why not now:** P1+P2 likely deliver most of the visible quality; this
one risks a new class of artifacts (normal-shooting is ill-posed at
junctions and high curvature) for sub-pixel gains that only matter if
the animation work ("real centerline + brush" direction) demands
smoother-than-raster centerlines.

**Decision gate:** after the re-trace + P2, render a handful of brush
title cards at large scale (scripts/make_title_demo.py). If centerline
quantization is visible at final resolution, build P3 as an optional
post-pass (`--vector-refine`), never as a default until sweep-clean on
all 6 fonts.

---

## P4. Stroke-store key migration: char → glyph name (review D8c)

**The wall:** `.hfont` keys glyphs by post name specifically so
ligatures need no format change, but `strokes.json` is keyed by
CHARACTER and bridged via cmap in `houdini/rep_strokes.py`. `f_i` has
no codepoint → the strokes rep can never carry ligatures while the
store is char-keyed. Flipping `liga` on in `layout.py` dead-ends here.

**Decision (make before any liga work):** migrate the store to glyph
names. Sketch:
- Store format v2: top-level `{"glyphs": {<glyph_name>: [...]}}` plus
  `{"chars": {<char>: <glyph_name>}}` for reverse lookup; loader
  accepts v1 (char-keyed) transparently and upgrades on next save.
- Trace time: the tracer already resolves char → glyph name via cmap in
  `rasterize_glyph`; record it.
- Corel round-trip: page identity is the safe filename (already
  char-derived); keep char-based for editing, translate at store
  boundary.
- Everything that reads the store (`editround`, `rep_strokes`,
  `render/*` via pipeline, `qa`) goes through
  `load_stroke_store`/`save_stroke_store`, so the format change is
  contained to editround.py + one migration test.
- Only after that: shape ligature-aware layout (layout.py note) and
  trace ligature glyphs (they have outlines but no codepoint — the
  tracer needs a glyph-name entry point beside the char one;
  `rasterize_glyph` currently takes `char`).

---

## Post-re-trace verification checklist (do once the running TOPs cook finishes)

1. Spot-check loop-heavy glyphs (8 % & g B 0) against the
   `_pre_retrace` tree — the '8'-class duplicate should be gone
   everywhere.
2. `penstroke qa <trace_dir>` per font; aggregate `qa.json` issue
   counts. Remember the calibration note: outline-coverage solo fires
   on loop glyphs are noise.
3. Re-run the 6-font agent QA sweep (vision agents over
   `diagnostics/*.png`, catalog per epst_batch_qa_v2 issue classes) to
   quantify against the ~60%-clean pre-junction-first baseline. This
   also produces the fresh over_split numbers P1 calibrates against.
4. Re-apply wanted Corel edits: delete the matching
   `corel/*.csv.imported.json` markers, cook again.
5. Delete `output/handwriting_pre_retrace/` once satisfied.
