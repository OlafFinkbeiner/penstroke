# CHANGELOG

Notes on the design's evolution. The current shape of this code is a
product of trial and error; this file preserves the reasoning so that
future readers (human or agent) don't repeat the same dead ends.

## 0.2.0 — EPST: junction-first graph tracer becomes the default

### What changed

- **New default tracer** (`templates/eulerian.py`): the glyph skeleton
  is treated as a multigraph and stroke decomposition is solved as a
  graph problem. Per glyph: build annotated multigraph → analyse ALL
  junctions globally (optimal pairing of edge-ends by tangent
  continuation, ≤ 75° turn) → follow pairings into chains → orient,
  order, smooth. The Hershey tracer remains as `--tracer template`.
- **Diagnostics for self-QA** (`render/diagnostic.py`): every trace
  emits per-letter PNGs with rainbow-ordered strokes, numbered starts,
  direction arrows, per-stroke panels, and an animation flipbook —
  readable by humans and vision models alike.
- **Interactive preview** (`preview.html`) ships with every trace:
  play/pause/speed/scrub/wireframe.
- **AI-assisted QA tooling**: `quality/glyph_image.py` (vision input),
  `quality/spec_validate.py` (AI spec vs trace), `quality/cascade.py`
  (layered geometric/outline checks), `core/outline.py` (exact TTF
  outlines for underlay + coverage).
- Deleted: the post-trace heuristic stack (`templates/fixes.py`) and
  font-regime classification (`quality/regime.py`) — both were
  band-aids over the Hershey tracer's structural limits, obsoleted by
  the graph decomposition.

### Dead ends preserved for posterity

- **Stacked per-symptom heuristics** (extend/merge/split/dedup/trim
  stages bolted onto the Hershey tracer). Each stage fixed one font's
  symptom and broke another's. The lesson: when the decomposition is
  structurally wrong, post-processing cannot save it.
- **Greedy Eulerian walking** (T-join + Hierholzer with tangent
  tie-breaking). Mathematically elegant, but corner decisions at
  junctions depended on which edges were already consumed — and
  minimising trail count produced serpentine outline-circuits on
  multi-stroke capitals (H, A, E, M). The fix was junction-FIRST
  analysis: decide every junction globally before walking anything.
- **Non-determinism bites silently**: skimage's `medial_axis` uses
  random tie-breaking; the same glyph yielded different skeletons,
  topologies, and stroke counts across runs. Always pass `rng=0`.
- **Trust nothing about graph input**: `skeleton_to_graph` emits
  duplicate edges whose pixel order can be scrambled; resampling a
  scrambled path interpolates across white space. Hygiene passes
  (reorder + dedup) in `build_annotated_graph` are load-bearing.

## 0.1.0 — Initial release

### What works

- TTF → animated stroke SVG pipeline, working end-to-end across 30+
  Google Fonts spanning handwriting, serif, sans, display, monospace,
  Greek, and Hebrew.
- Per-letter SVGs with positioning metadata so letters compose into
  words. The fixed-canvas, consistent-baseline approach is essential
  for typesetting.
- Hershey-template-guided tracing as the production path. Falls back
  to geometric stroke decomposition for non-Latin scripts.
- OCR-based quality validation (optional, requires tesseract).
- Interactive HTML viewer with play/pause/speed/scrub/wireframe.

### Dead ends — approaches that didn't work

**Geometric stroke decomposition as the primary path.** Early
iterations tried to infer stroke decomposition from skeleton topology
alone — tangent continuity at junctions, length thresholds, junction
classification. This works for simple letters (X, L, T) but fails
unpredictably on complex ones (R, B, A's internal triangle).
The fundamental problem: the question "what strokes are in this
skeleton?" is underspecified. Different decompositions are equally
valid topologically.

The fix was conceptual: stop trying to *derive* stroke decomposition
and instead *adopt* it from a known source. Hershey fonts encode
"an A is 3 strokes" as data. We use Hershey as a per-letter prior,
then trace the *target font's* actual centerline geometry through the
Hershey-prescribed waypoints. This converts an underspecified problem
into a constrained one (shortest-path between known waypoints),
which is robust.

Kept the geometric pipeline as a fallback in `core/strokes.py` for
characters Hershey doesn't cover (Hebrew, Arabic, etc.).

**Hard-pruning short skeleton branches.** Aggressive spur removal
killed the legitimate short strokes (serifs, the tail of a 'Q', the
top of a 't'). Switched to length-relative-to-stroke-thickness
pruning: `max(12, local_thickness * 2.2)`. A serif on a thick stem
is allowed to be longer than a serif on a thin stem. Verified
empirically on test fonts.

**Forbidding pixel reuse in shortest-path traversal.** Originally I
tried to hard-prevent the second stroke from passing through pixels
used by the first stroke. This breaks at junctions where two strokes
legitimately must cross (X, +, t-crossbar). Now I use a soft 8×
edge-weight penalty: strokes prefer fresh pixels but can cross through
shared ones when necessary.

**Treating every Hershey font equally.** Topology scoring alone picks
cursive for letters like 'A' and 'Z' because the cursive templates'
endpoint count happens to match the target skeleton — but the
resulting traces (1-stroke A, 1-stroke Z) look like a 4 and a 2.
The fix: a per-letter `LETTER_STRATEGY` map that constrains which
Hershey variants are candidates for each character. Most uppercase
letters are locked to rowmans.

**Cropping each letter's SVG to its inked bounding box.** Earlier
output looked fine in isolation but couldn't compose into words —
all letters appeared on the same Y and at the same scale. The fix
was to render every letter onto an identical canvas (full ascender +
descender + pad) with the baseline at a fixed Y, and embed font
metrics as SVG data attributes. Letters now compose naturally by
horizontal stacking driven by advance widths.

**Pixel-coverage as the only quality metric.** A traced 'z' could
cover 95% of the original z's ink while looking like a 2 to a human
(or to OCR). Coverage alone says "this is mostly there" but misses
*semantic* wrongness. Added OCR validation as a complementary check:
render the trace back to a raster, OCR it, compare to the input
character. Catches cases where the geometry is right but the result
isn't recognizable.

### Key empirical constants

These were tuned by trial and error on the test fonts. Document them
here so a future change doesn't accidentally drift them:

- **Junction cluster merge distance: 22 pixels** at 384–512 render
  size. The medial axis of an X-crossing typically produces 3–5
  degree-≥3 nodes within 10–15px. 22px catches all of them. Below
  ~15 they're undercaught; above ~30 you start merging genuinely
  separate junctions.

- **Skeleton spur prune threshold: max(12px, 2.2 × local_thickness)**.
  Below this, serifs disappear. Above this, junction-corner spurs
  survive and become spurious strokes.

- **Parallel-edge separation threshold: 1.6 × local stroke width**.
  Below this is a medial-axis split of one thick stroke.

- **Parallel-edge enclosed-area threshold: 1.0 × local stroke width**
  (area divided by path length). Below this is a sliver
  (medial-axis split). Above this is a real enclosed region (bowl of
  A or B) — keep both edges. Both tests must pass to collapse.

- **Path-reuse penalty: 8× edge weight**. Soft enough that crossings
  still work, strong enough to redirect parallel runs.

- **Waypoint count per Hershey stroke: 5 (10 for closed loops)**.
  More gives stricter shape matching but produces brittle paths when
  the Hershey-to-target coordinate mapping has slight misalignment.

- **Guide stroke-width factor: 0.8 × average filled width**. Used
  for the animated centerline rendering. At 0.55 the wireframe mode
  looked anemic; at 1.0 it visually replaced the ribbon. 0.8 reads
  as "thin pen line" matching each font's natural weight.

- **Wobble amplitude: 0.18 std-dev, scale: 25**. OU-process noise
  applied perpendicular to local tangent. Larger amplitude looks
  drunk; smaller looks robotic.

- **Taper profile: 12% entry, 14% exit, 5% min width**. Pen-landing
  and pen-lifting feel without making strokes look pointy.
