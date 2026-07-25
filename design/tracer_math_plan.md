# Tracer math plan — replacing tuned thresholds with principled math

Written 2026-07-25 after a critical review of `tracer.py`, `core/skeleton.py`,
`core/graph.py` and `core/smoothing.py`; **revised the same day** after a
second measurement pass that (a) found the resolution problem is far worse
than first recorded, (b) pinned its exact mechanism, and (c) prototyped a
substrate that does not have it. This plan supersedes parts of
[tracer_quality_plan.md](tracer_quality_plan.md) — see *Relationship to the
old plan* at the end.

**The thesis:** the tracer's ceiling is not any individual heuristic. It is
that every stage makes a local decision against a hand-tuned absolute-pixel
threshold, and nothing scores the finished decomposition. So nothing can be
calibrated, nothing can be optimized, and every change is validated by
eyeballing diagnostics.

**The revision:** the raster skeleton is not merely *tuned* to one
resolution — it is **structurally incapable** of being resolution-invariant,
because the quantity the pruning rule is anchored to is a property of the
blur kernel rather than of the glyph. Reparameterizing the constants (old
A1) mitigates this; it does not fix it. A medial axis computed from the
Bézier outline does fix it, exactly, and was prototyped to confirm.

---

## The evidence (measured, not assumed)

Figures from probes over Caveat / EB Garamond / Arvo / Lato / Lobster /
DancingScript unless noted.

### The headline: the trace is not resolution-stable, and it degrades

Tracing 56 glyph×font combinations at 256 / 384 / 768 px:

| | |
|---|---|
| glyphs whose **stroke count changes** with raster size | **23 of 56 (41%)** |
| direction of the change | **monotonically worse** with resolution |
| worst cases | Arvo `H` **3 → 7 → 12** strokes; Arvo `K` 4 → 8 → 10; Arvo `X` 4 → 7 → 11; Lato `E` 2 → 2 → 5 |
| font classes affected | serif/slab worst (Arvo 8/13, Lato 9/13); script fonts largely stable (DancingScript 2/13) |

This is worse than the first pass recorded (4 of 9) and it inverts a
scheduled step: **"raise the default raster size" would actively damage the
output.** Quality at 384px is an accident of the constants happening to suit
that scale.

### The mechanism (exact)

Endpoint count in the raw skeleton is **constant across resolution** — 16 for
Arvo `H` at every size. The skeleton topology is stable. What changes is
*which spurs `prune_skeleton` removes*, and the reason is a scale mismatch on
the two sides of one comparison:

| Arvo `H` | 256px | 384px | 768px | 1536px |
|---|---|---|---|---|
| stroke width `W` | 24.0px | 38.0px | 72.0px | 139.9px |
| median spur length | 7.0px | 11.0px | 29.0px | 60.0px |
| …as a fraction of `W` | 0.29 | 0.29 | 0.40 | 0.43 |
| median `dist` at spur tip | **5.00px** | **6.00px** | **5.00px** | **6.00px** |
| resulting threshold | 12.0px | 13.2px | 12.0px | 13.2px |
| spurs pruned | **16/16** | 9/16 | **0/16** | **0/16** |

`prune_skeleton` tests `len(path) < max(12, dist[tip] · 2.2)`.

- `len(path)` is **scale-proportional** (a stable 0.29–0.43 × `W`).
- `dist[tip]` is **pinned at 5–6px at every resolution**, because a spur
  terminates *at a boundary corner*, where the distance transform measures
  the corner rounding introduced by the `σ=1.5` Gaussian pre-smooth — a
  property of the blur kernel, not of the glyph. Confirmed directly: scaling
  σ with the raster size moves `dist[tip]` (5.00 → 1.00) while spur length
  stays put.

So the comparison is *scale-proportional quantity < constant*, and the
inequality flips as resolution rises. Worse, the `· 2.2` branch that was
added specifically to make the rule scale with stroke weight **never
binds**: 5×2.2 = 11 < 12, and 6×2.2 = 13.2 ≈ 12. The rule is an absolute
pixel constant wearing a proportional disguise.

The fix is to anchor the threshold at the **junction** end of the spur
(where `dist` really is the parent stem's half-width) instead of the tip —
which is what the newer `prune_redundant_leaves` already does correctly
(`r_j = dist_map[jct]`). Measured: Arvo `H` drops from 16 surviving spurs to
2–4, and stability improves markedly across all five fonts — **but it does
not reach invariance.** It is a strict improvement, not a cure.

### The substrate that does not have the problem

Prototyped in `scripts/proto_vector_medial_axis.py` (run it to reproduce both
numbers below): medial axis computed as the Voronoi
diagram of densely-sampled **Bézier outline** points — no raster anywhere —
pruned by the scale-free separation-angle (θ-medial-axis) criterion.

| | raster pipeline | vector medial axis |
|---|---|---|
| resolution-invariant glyphs | 33 / 56 (59%) | **40 / 40 (100%)** |

Invariance is *exact and by construction*: the computation happens in em
space, so there is no resolution to be sensitive to. Two findings from the
prototype worth carrying forward:

- **Every length must be em-relative, including ones you forget.** The first
  run scored only 31/40 — the leak was `extract_outlines`' hardcoded
  `tol_px=0.5` flattening tolerance, an absolute pixel constant in the
  *vector* path. Making it em-relative took the score to 40/40. Same disease,
  different file.
- **θ has a wide plateau, and it is not a spur knob.** θ ∈ [50°, 90°] gives
  identical topology on nearly every glyph; above ~110° the axis *fragments*
  (endpoints rise while junctions collapse to 0) rather than simplifying.
  That is the right behaviour for a foundation constant — insensitive over a
  broad band. But it means θ does **not** remove serif branches: those are
  genuine medial-axis features (Arvo `H` legitimately has 16 endpoints) and
  still need a separate feature-significance test. `prune_redundant_leaves`'
  exclusive-ink argument is already the right shape for that job and is
  already scale-free.

### Everything else measured

| Finding | Measurement |
|---|---|
| Absolute-pixel constants mean different things per font | `merge_nearby_junctions(max_dist=22)` = **1.94** stroke half-widths on Caveat, **0.67** on Anton |
| …and per resolution | the same 22px = **1.97** half-widths at 384px, **0.37** at 2048px |
| Memory is not a constraint | a 2048px glyph mask is **5.7 MB**; peak for a 9-glyph sweep was 440 MB |
| Junction pairing is solved super-exponentially for no reason | Blossom matching gives the **identical** result (3000/3000 random configs), 4.85ms → 0.78ms at degree 14 |
| …and the greedy fallback is dead code on real fonts | measured junction degree **never exceeded 4**; `MAX_EXHAUSTIVE_DEGREE = 8` only fires on texture fonts |
| The nib is recoverable in closed form | one `lstsq` per font recovers contrast **1.09× Caveat / 1.14× Arvo / 1.29× Anton / 1.73× EB Garamond** — the correct typographic ordering |
| P1's proposed width gate is below natural variation | EB Garamond's **median** within-stroke end-to-end width ratio is **2.07** (p90 3.63); P1 proposes `RATIO_FREE = 1.6` |
| Sampling density is uniform where geometry is not | strokes span **4.2px…506px** arc length (119×); all get exactly `n_samples=240` |
| Cost of a trace is dominated by resolution, not algorithm | 768px traces run 2–10× slower than 384px; no glyph exceeded 4.2s |

---

## Implementation status (2026-07-25)

| Step | State | Evidence |
|---|---|---|
| A0 junction-anchored spur threshold | **done, revised 2026-07-25 (2)** | see the tip-clearance bug note below — factor dropped to 0.5, `TIP_CLEARANCE_SIGMAS` 3.0→6.0 |
| A1 scale-free constants | **done** | 6 lengths now multiples of `W`; `TIP_CLEARANCE` moved to σ units |
| A2 Blossom matching | **done** | 0 stroke-count changes across 1128 glyphs; ~45 lines deleted |
| A3 sampling density + physical wobble | **done** | 60/60 tests; geometry-only |
| B1 nib recovery | **done** | contrast order correct on 6 fonts; exact inversion in unit tests |
| C0 objective | **done** | ranks post-fix above pre-fix on **39/44** fonts, uniform weights |
| C3 joint order/orientation | **done** | pen-up travel **−42.8%** across 6 fonts × 62 glyphs |
| B0 vector medial axis | **de-risked, not integrated** | winding rule + overlap seams fixed and verified; near-degenerate noise root-caused but unfixed; graduated to `core/vector_skeleton.py` with tests, NOT wired into `skeletonize()`/the tracer -- see below |
| C1 Euler-spiral pairing | **not started** | gated on C0, which now exists |
| C2 λ/θ filtration | **scoped 2026-07-25, not started** | formal definition + implementation plan written; a quick arc-length proxy tried and correctly discarded (didn't separate noise from real junctions either) -- see below |

### A0 — the factor, and a trap in the objective

A0 works: the serif/slab glyphs that drove the resolution collapse are now
stable (Arvo `H`/`K`/`X`/`R`/`f`/`m` and Lato `H`/`R`/`E`/`B`/`f` all moved
from VARIES to STABLE; 17 newly stable against 6 newly unstable, the latter
mostly bowls — `e`, `g`, `a`, `8` — which is a different mechanism and
still open).

Factor sweep (6 fonts, objective at 384px, invariance over 384/768):

| factor | objective | reconstruction | parsimony | invariant | mean strokes |
|---|---|---|---|---|---|
| 0.5 | 0.6067 | 0.944 | 0.946 | 34/42 | 3.05 |
| 0.9 | 0.5955 | 0.940 | 0.903 | 39/42 | 2.74 |
| **1.0** | **0.5937** | **0.939** | **0.897** | **40/42** | **2.71** |
| 1.3 | 0.5919 | 0.939 | 0.891 | 40/42 | 2.71 |
| 1.6 | 0.5885 | 0.939 | 0.879 | 38/42 | 2.74 |

**Shipped: 1.0.** Reconstruction — the term that actually measures whether
the trace reproduces the glyph — is flat across the whole sweep, so
invariance is the only thing left to optimise, and 1.0 maximises it.

**The trap, recorded so nobody repeats it.** Comparing `total` against the
pre-A0 baseline showed 0.6576 → 0.5955 and looked like a serious quality
regression. It is not. `parsimony` scores stroke count against a bound
derived from the skeleton *graph*, and pruning changes that graph — prune
harder, the bound drops, and the ratio worsens even as the trace improves.
Measured head-to-head, reconstruction is **identical** (baseline 0.9374 vs
current 0.9371) and missed ink is slightly **better** (0.0396 → 0.0386).

So `total` is only comparable between decompositions built on the same
skeleton configuration. Cross-configuration comparisons must use
`reconstruction` and `smoothness`. This is now documented at the top of
`quality/objective.py`.

### A0 revised — the factor sweep above was run inside a live bug

The "shipped: 1.0" call above optimised a false tradeoff. `prune_skeleton`
(pixel-level, length-only) and `tracer.py::prune_redundant_leaves`
(graph-level, ink-coverage-based — it decides real-feature-vs-corner-noise
from actual geometry, not a threshold) are two different mechanisms for the
same job, and only the second one can actually tell the two populations
apart. `prune_redundant_leaves` has its own gate, `TIP_CLEARANCE_SIGMAS`
(pre-filters which leaf branches are even corner-fork *candidates*), and it
was set to 3.0 (→ 4.5px). Directly instrumented on Arvo `Z`: every real
corner-artifact candidate has a measured tip distance of **5–8px** — all
above the gate — so `prune_redundant_leaves` never evaluated a single one of
them; they all survived by default, gate-bypassed, regardless of what the
ink-coverage test would have said. The factor sweep above never exercised
the mechanism it was implicitly relying on to separate noise from feature —
`prune_skeleton`'s length threshold was doing 100% of that job by itself,
which is exactly the thing A0's own docstring says a length-only rule can't
do (confirmed directly: no factor in [0.5, 2.2] gets both Arvo `x`'s real
corner flicks *and* Arvo `Z`'s noise fragments right at once).

**Fix, verified 2026-07-25:**
- `TIP_CLEARANCE_SIGMAS`: 3.0 → **6.0**, so the gate actually admits the
  measured 5–8px population instead of excluding it.
- `prune_skeleton`'s `min_branch_len_factor` default: 1.0 → **0.5**. Its job
  is now explicitly narrowed to "kill raster noise too short to be anything
  else" — not feature-vs-noise judgment — leaving that decision to
  `prune_redundant_leaves`, which can now actually make it.

Effect, inspected visually (not just counted) on Arvo: `Z` goes from one
unbroken zigzag stroke through two sharp corners to a natural 4-stroke
pen-lift pattern (top bar / corner tick / diagonal / bottom bar). `K` is
unchanged (stem + 2 real serif flicks + 2 diagonals — 5 strokes). Lowercase
`x` drops to 2 clean diagonals (its corner nubs carry no exclusive ink —
correctly judged redundant with the parent stroke's own cap). Capital `X`
keeps 6 (2 diagonals + 4 real corner ticks) — the **same** test giving
**different, individually correct** answers per glyph, which is the point:
this is no longer one global count target, it's per-branch geometry.

6-font ASCII sweep vs. the previously-shipped factor=1.0: Arvo 23
stroke-count changes, Lato 14, Lobster 7, EB Garamond 4, DancingScript 2,
Caveat 2 — serif/slab fonts move the most (expected), script fonts barely
move (expected), and the changes run in both directions (some glyphs gain a
real feature back, some lose a redundant one), not uniformly more or fewer.
60/60 tests pass.
`quality/objective.py`, and it is the first thing C1/C2 will need to
respect.

## Cleanup applied in this pass

All verified together: **56/56 tests pass**, and a 168-combination
before/after sweep shows **zero stroke-count changes**, arc length moving
0.074% median / 1.14% max (the wobble correction alone).

| Change | File | Why |
|---|---|---|
| Deleted `_classify` + 4 of `ComponentSpec`'s 6 fields (`odd_vertices`, `betti`, `topology_class`, `bbox`) | `tracer.py` | Computed for every component of every glyph and **never read**. Only `.subgraph` and `.total_length` are consumed. ~25 lines and a per-component computation removed. |
| Removed dead `binary_closing` import | `core/skeleton.py` | Imported, never used. |
| Fixed the wobble's tangential term | `core/smoothing.py` | It applied `nx_` to the x-component and `ny_` to the y-component of a single along-tangent displacement — two independent noise processes driving one vector. That is not a displacement along the tangent; it skewed the wobble direction and inflated perpendicular amplitude by ~11.8%. Now one scalar per unit vector. |

**Note:** the wobble fix changes stroke *geometry* (not counts) library-wide,
so it should land with a re-trace.

### Flagged, not actioned (each is a judgement call, not a mechanical fix)

- `render/houdini.py` — legacy per-letter JSON export, superseded by the
  hfont strokes rep per CLAUDE.md; still referenced by `tests/test_smoke.py`.
  Deleting it is a product decision.
- `test_fonts/EBGaramond-Regular.ttf` is **corrupt** — `fontTools` rejects it
  ("bad sfntVersion"). `EBGaramond.ttf` beside it is fine. Any sweep naming
  the `-Regular` file has been silently skipping that font.
- `MAX_EXHAUSTIVE_DEGREE` greedy branch and `PRUNE_MAX_EDGES` — both are
  performance guards that silently change the algorithm on texture fonts.
  A2 and C2 below delete them properly rather than papering over them.

---

## Tier A — Foundations (no judgment calls, verifiable mechanically)

### A0. Anchor the spur threshold at the junction — the one-line win

Change `prune_skeleton`'s threshold from `dist[tip]` to `dist[junction]`, and
drop the `max(12, …)` floor that currently dominates it. This is the single
highest-value change measured in this pass: it is small, it is a correction
of a stated-but-unmet intent ("proportional to local stroke width"), and it
takes Arvo `H` from 16 surviving spurs to 2–4.

**Gate:** run the sweep protocol. Expect large, *intended* stroke-count
changes on serif fonts at ≥768px and small ones at 384px — this is a
behaviour fix, not a reparameterization, so the gate is visual diagnostics
plus the resolution-invariance table improving, not zero change.

### A1. Scale-free constants

`max_dist=22` (graph.py), `max(12, 2.2·r)` and the `len(path) > 80` walk
bound (skeleton.py), `TIP_CLEARANCE_MAX = 4.5` (tracer.py), **and
`extract_outlines`' `tol_px=0.5`** are absolute pixel lengths. Derive one
per-glyph scale `W = median(dist_map[skel]) · 2` and express every length
constant as a multiple of it. Calibrate so that at size=384 on the 6-font set
the output is unchanged — a *reparameterization*, not a retune.

Note A0 first: reparameterizing a rule whose anchor is wrong just relocates
the bug.

**Gate:** tracing the 6-font set at 384px and 1024px must produce identical
stroke counts. This is a sharper regression test than the current eyeball
protocol and becomes the standing tripwire for Tiers B and C. **Today that
test scores 33/56.**

### A2. Exact junction matching via Blossom

Replace the recursive exhaustive search *and* the greedy fallback in
`analyze_junctions` with `networkx.max_weight_matching`. The reduction,
verified algebraically: for `n` ends and matching `M`, cost is
`Σ_M s_ij + U·(n − 2|M|)` = `nU − Σ_M (2U − s_ij)`, so minimising cost is
maximising `Σ_M (2U − s_ij)`. Since allowed pairs satisfy `s ≤ max_turn` and
`U = 1.05·max_turn`, every allowed weight `2U − s ≥ 1.1·max_turn > 0` — so
plain max-weight matching suffices, no `maxcardinality` needed.

Deletes `MAX_EXHAUSTIVE_DEGREE`, the `search()` closure, and the greedy
branch — roughly 45 lines for 3, with the optimality guarantee restored on
the texture fonts that currently silently lose it.

**Gate:** zero stroke-count changes on the 6-font set (provably the same
answer); texture-font glyphs may legitimately change — inspect those.

### A3. Decouple sampling density

`n_samples` becomes proportional to arc length (a target px-per-sample), so a
506px stroke is not sampled at the same count as a 4px tittle. (The wobble
half of the original A3 is **done** — see Cleanup above.)

**Gate:** no stroke-count change (geometry only). Lands with a re-trace.

---

## Tier B — Measurement

### B0. Vector medial axis — promoted from "someday" to the main bet

Replace `rasterize → gaussian → medial_axis → prune → graph` with a medial
axis computed directly from the outline: sample the Bézier contours at
em-relative spacing, take the Voronoi diagram, keep interior vertices, prune
by separation angle. `core/outline.py` already extracts the contours, and
`scipy.spatial.Voronoi` is already a dependency — **no new dependencies.**

What it deletes: the `σ=1.5` blur (which is currently rounding off real
corners before we ever look at them), the distance-transform quantization,
the scrambled-pixel-path repair, `fill_path_gaps`, the 8-connected walk
bounds, and the whole class of resolution bugs above. Widths come out exact
and sub-pixel (distance to the governing outline points), which also
subsumes B2.

What it does **not** solve, and must not be assumed to: spur/feature
significance (θ is a robustness knob, not a pruning knob — measured above),
and stroke decomposition itself, which stays exactly as it is. This is a
substrate swap under an unchanged junction-first tracer.

**Risks to retire before committing:** Voronoi-based medial axes are
noise-sensitive on near-degenerate contours — **root-caused 2026-07-25, not
yet fixed** (see below, after the winding-rule and overlap items).

**Winding rule — investigated and fixed 2026-07-25.** The
prototype's even-odd fill is wrong more often than expected: measured
directly (even-odd vs. nonzero fill of the same polygons, not vs. a raster
ground truth — that conflates it with unrelated AA/discretization noise),
**172 of 1660 multi-contour glyph instances (10.4%) across ~200 sampled
real fonts disagree**, and NOT only on decorative/texture fonts — Roboto
`B`/`g`, Sora `B`, CascadiaCode, ArchivoNarrow, SofiaSans all show it. Fixed
in the prototype (nonzero winding, the TrueType-spec-correct rule);
resolution-invariance holds at 40/40 with the fix, so it's free.

**Overlapping-contour seams — investigated and fixed 2026-07-25.** Fixing
the fill rule surfaced a deeper issue: Roboto `B` traced through the
corrected prototype gave a degenerate result — 151 junctions, 72 loops, for
a plain two-counter letterform. Root cause: its 2 contours have heavily
overlapping bounding boxes (one spans y 123-396, the other y 239-396,
overlapping across the whole middle third) — a standard variable-font
authoring shortcut ("overlapping simple contours": draw two full solid
shapes and let the renderer's winding rule composite them, rather than
authoring one shape with clean hole contours per master). Nonzero winding
gets the fill right, but the raw contour boundary still contains the
overlap SEAM where the two shapes cross — the Voronoi step samples that
seam as a real outline edge, since it doesn't know the fill rule dissolves
it away, and θ-pruning doesn't clean up the resulting structure.

Fix: `resolve_overlaps()` in the prototype — union the positively-wound
(CCW) contours via `shapely.ops.unary_union`, union the negatively-wound
(CW / hole) contours, subtract hole-union from solid-union. Exact
nonzero-winding fill for the common case (each contour simple; only
inter-contour overlap matters), and it dissolves the seam because overlap
between same-signed contours is just... still filled.
`Polygon(...).buffer(0)` on each input first — GEOS otherwise raises
(`TopologyException: side location conflict`) on the near-duplicate /
near-zero-length segments Bezier flattening produces.

**Verified:** Roboto `B` 151j/72loop → **4j/2loop** (sane: stem + 2 bowl
counters); `g` 202j/98loop → **4j/1loop**. Resolution-invariance still
40/40 with the union step wired into `signature()`. Texture-font
performance unaffected — JacquardaBastarda9Charted (160 contours, none of
which actually overlap each other) runs the union step in <0.1s, total
trace time unchanged (~0.5s), and correctly returns all 160 contours
untouched since there's nothing to merge. `shapely` added as a prototype-
only dependency (not yet in the core pipeline's dependency list).

**Gate:** resolution-invariance 40/40 holds on the full 6-font charset;
outline-coverage metric ≥ current; visual diagnostics on the 6-font set at
least as good.

**Near-degenerate-contour noise — root-caused 2026-07-25, fix attempted and
reverted.** Tested against BungeeHairline (an intentionally hairline-weight
display font, stroke width ≈ sample step — a genuine stress case). Two
symptoms, both confirmed **visually**, not just from topology counts (a
first pass at this trusted the counts alone and drew a wrong conclusion —
see below):

- At a blunt stroke tip (`m`'s stem end), Qhull produces 2-3 near-coincident
  vertices instead of one clean endpoint — visually a ~1px cluster sitting
  right on the tip. Increasing sample density does **not** fix this and
  initially looked like it did (topology went from a correct 2 ends/0
  junctions to a wrong-looking 4 ends/2 junctions) — that was a false
  positive from trusting counts without rendering the graph. The 2 extra
  nodes are the same artifact, just more visible at higher density.
- At a pinch point (`8`'s waist, where the two bowls meet), the artifact is
  worse: not simply duplicate points, but a genuine tiny 7-node CYCLE
  (~5x4px bounding box) woven from several distinct-but-nearby vertices
  that are NOT directly graph-adjacent to each other. Confirmed via
  `nx.cycle_basis`: 2 real bowl loops (268 and 302 vertices) plus 2 spurious
  micro-loops (7 vertices each) at the two pinches, plus 2 real junctions
  each split into a 3-node cluster (6 junction-degree nodes total instead
  of 2).

**Two merge-based fixes were tried and both had real side effects, so
neither is committed:**
1. Contracting graph edges shorter than a radius-relative epsilon: the
   pinch-cluster nodes turned out not to be directly graph-adjacent (they
   connect through other nearby nodes), so this didn't reach them at all.
2. Spatial (not graph-adjacency) clustering of same-degree-class nodes
   within a radius-relative epsilon: works partially, but at the epsilon
   needed to merge the pinch cluster, `8`'s topology comes out as 4 ends /
   2 junctions instead of the correct 0 ends / 2 junctions — the merge is
   breaking a closed loop into open paths in some cases, a worse bug than
   the one it's fixing. An unrestricted version of the same idea (no
   degree filter) is worse: at the epsilon needed for `m`'s tip, entire
   straight stems collapse into a single point, because ordinary
   consecutive path vertices are also "spatially close" once epsilon
   approaches the sample step.
3. (2026-07-25, second pass) Suspected attempt 2's real bug was the plain
   `nx.Graph` silently deduplicating a second parallel edge whenever a
   merged cluster reconnected to the same external neighbor twice (e.g.
   both lobes of `8`'s pinch reaching the same real junction along two
   distinct arcs) — a plain graph can't represent two edges between one
   node pair, so the second arc just vanishes, which is exactly what would
   turn a closed loop into an open path. Retried the identical clustering
   with a `nx.MultiGraph` so parallel edges survive. Result: `8` still
   doesn't land on the correct (0, 2) ends/junctions at any epsilon in
   [0.5, 3.0] — it jumps straight from the RAW noisy signature (0, 6, 4)
   to an OVER-merged (0, 2, 8), never landing on the right answer. Cause:
   the noise isn't only vertex-position degeneracy, it's also *ridge*
   degeneracy — Qhull's near-degenerate triangulation emits multiple
   slightly-different ridges for what should be a single real medial-axis
   edge, so preserving "all parallel edges after clustering" preserves
   those spurious duplicates too. Distinguishing a genuinely-distinct arc
   (keep both) from a duplicate ridge (keep one) needs actual geometric
   reasoning about the edges, not just node proximity — confirms this
   family of fix (cluster-then-reconnect, any bookkeeping variant) is the
   wrong tool for this bug.

**Not fixed, three attempts in.** The right fix likely isn't a post-hoc
merge heuristic at all — it's probably the same disease C2's λ/θ
filtration is meant to cure (a single principled criterion instead of an
ad-hoc distance threshold). Scale of the bug: small (sub-pixel to a few
pixels, only manifests at stroke widths approaching the sample step —
i.e. hairline/thin weights). Whoever picks this up next: render the graph
before trusting topology counts (attempt 1's false-positive `m` "fix" was
exactly that mistake), and don't reach for a fourth merge-heuristic
variant without a plan for telling real parallel arcs apart from
duplicate ridges — that's the part all three attempts actually foundered
on.

**Graduated to real code, 2026-07-25.** With winding rule and overlap
seams fixed, the prototype moved from `scripts/proto_vector_medial_axis.py`
into `src/penstroke/core/vector_skeleton.py` — a real, tested module
(`tests/test_vector_skeleton.py`: synthetic overlap/winding-rule cases with
known-correct answers, plus resolution-invariance on the Caveat fixture),
not a scratch script. `proto_vector_medial_axis.py` now just imports it and
keeps the FONTS/CHARS/SIZES report harness. `shapely` stays a **dev-only**
dependency (`pyproject.toml`) — nothing in the shipped pipeline imports
`vector_skeleton`, so `penstroke trace` is unaffected.

**Still NOT integrated.** `glyph_vector_skeleton()` returns a `networkx`
graph over float em-space Voronoi vertices; `skeletonize()` returns a raster
pixel array. Everything downstream of `skeletonize()` in `tracer.py`
(`skeleton_to_graph`, the scrambled-path/duplicate-edge hygiene passes,
`analyze_junctions`, `prune_redundant_leaves`) is built around the raster
interface and would need real restructuring to consume the vector graph
directly — a separate, larger task, not attempted here. Wiring this in as
the default is also blocked on the unresolved near-degenerate-noise risk
above: swapping it in now would silently regress hairline/thin-stroke
fonts that the current raster+A0 pipeline handles correctly (if not
provably resolution-invariant).

### B1. Recover the pen — closed-form, per font

Model the nib as an ellipse (semi-axes a ≥ b, angle α). A stroke travelling
in direction θ has width

> **w(θ)² = A + P·cos 2θ + Q·sin 2θ**

which is **linear** in (A, P, Q) — one least-squares solve, no iteration, no
tuning. Then nib angle `α = ½·atan2(Q, P)`, contrast `√((A+B)/(A−B))` with
`B = hypot(P, Q)`, nominal width `√A`, and **R² is a free confidence
measure** — it correctly reports ≈0.1 on monoline Caveat and 0.38 on EB
Garamond.

Output goes into the `.hfont` manifest as a `pen` block. **This closes open
work item #6** in CLAUDE.md and supplies the per-font statistics Tier C's
parameters are expressed in.

**Gate:** contrast ordering must match typographic ground truth across a
20-font spread (monoline < slab < grotesk < old-style < didone).

### B2. Width by ray-cast against the outline

The distance transform returns the *maximal inscribed disk* radius — not the
width perpendicular to pen travel — and it inflates near crossings (the
`clean_widths` median-filter hack in `make_title_demo.py` is a symptom).
Cast the local normal against the exact outline polygons and take the two
hits separately, following [Epshtein's Stroke Width Transform](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/201020CVPR20TextDetection.pdf)
but against exact geometry rather than a raster. Left/right asymmetry is a
free QA signal: it means the centerline has drifted off the true ridge.

**Largely subsumed by B0** — if the axis is computed from the outline, the
governing outline points are already known at every axis vertex. Keep as a
separate item only if B0 is deferred.

---

## Tier C — Decision quality (needs the objective first)

### C0. The objective function — build before C1–C3

Nothing today can say "decomposition A beats decomposition B", which is why
every change is judged by looking at pictures. Proposed composite score per
glyph, all terms computable after Tiers A–B:

1. **Reconstruction:** ink covered by the union of stroke ribbons vs. the
   true outline polygon (symmetric difference, both directions).
2. **Continuation cost:** total completion energy at every join actually
   made (C1's metric), plus a penalty per pen lift.
3. **Kinematic plausibility:** how well the velocity profile fits a sum of
   lognormals (C4).
4. **Stroke-count parsimony** against the glyph's topological lower bound.
5. **Resolution invariance** — now known to be a discriminating signal, and
   free to compute.

**Gate:** the score must rank the known-good post-junction-first traces above
the archived `output/handwriting_pre_retrace/` ones without hand-weighting
per font. If it cannot, the score is wrong and C1–C3 stay unbuilt.

### C1. Euler-spiral continuation instead of the angle threshold

`_pair_score` is `acos(-dot(t_a, t_b))` over 20-pixel tangent windows, gated
at 75° — a zeroth-order test that ignores curvature and width entirely, and
whose 20-pixel window is itself an absolute constant (A1).

Score a candidate pairing by the energy of the minimal **Euler spiral**
(clothoid) connecting the two ends with their tangents and curvatures.
Minimising *curvature variation* — rather than curvature² as elastica does —
is the established answer to this "complete the curve across an occlusion"
problem ([Kimia & Frankel](https://link.springer.com/article/10.1023/A:1023713602895)),
and it is scale-consistent, so normalising by stroke width makes it a
threshold-free, font-independent cost.

The stroke-extraction literature converged independently on the same triple —
[curvature, width **and** direction deviation together](https://www.sciencedirect.com/science/article/abs/pii/S0031320321005926).
We use one of three. **P1 of the old plan (width continuity) becomes one term
of this cost** rather than a bolted-on penalty in degrees — which the EB
Garamond measurement (median 2.07 natural variation vs. a proposed 1.6 gate)
says is the only way it can work. Feeds straight into A2's matching: same
algorithm, better weights.

### C2. λ-medial axis instead of three pruning mechanisms

Skeleton instability is currently fought in three places — the σ=1.5 blur,
`prune_skeleton`'s spur-length rule, and `prune_redundant_leaves`' 140-line
disk-coverage argument. The [λ-medial axis](https://geometrica.saclay.inria.fr/team/Fred.Chazal/papers/cl-lma-05/mlambda.pdf)
is a single-parameter filtration *with a proven stability theorem* and an
efficient discrete form ([Chaussard, Couprie & Talbot](https://perso.esiee.fr/~talboth/articles/Mine/Chaussard_Lambda_medial_PRL_2011.pdf)).
Also deletes `PRUNE_MAX_EDGES`.

**If B0 lands, this changes shape:** λ (and its θ sibling) apply naturally to
the Voronoi axis, and the σ=1.5 blur disappears on its own. The measurement
above says λ/θ handles *robustness* but not *feature significance*, so
`prune_redundant_leaves`' exclusive-ink test survives as the second stage
rather than being replaced.

**Scoped 2026-07-25, not started.** Three ad-hoc merge-heuristic attempts at
B0's near-degenerate-noise bug (see B0 above) all failed the same way —
none could tell a genuinely distinct medial-axis branch apart from a
spurious duplicate Qhull produces on near-degenerate input. This is the
actual reason to build C2 for real rather than reach for a fourth
heuristic: the formal λ-medial-axis significance test is defined precisely
to make that distinction principled instead of tuned. Scoping it properly
means being exact about the definition, because a quick proxy already
failed once today (below) — the *correct* algorithm is not a drop-in
one-liner.

**The formal definition (Chazal-Lieutier):** for a medial-axis point `x`,
let its *contact set* Γ(x) be the boundary points at exactly the minimum
distance `r(x)` (not "nearby" points — the actual minimizers). λ(x) is
(informally) the smallest extra radius you'd need to add to the ball at
`x` before Γ(x)'s connected components, grown by that radius, merge into
one — i.e. how much "slack" separates the ≥2 distinct boundary features
`x` is equidistant from. A real corner/junction has large λ (its contact
points belong to boundary regions that stay separate under a healthy
radius of growth); a numerically-forced near-duplicate vertex has small λ
(its contact points are already almost touching). Crucially this is a
property of the **boundary**, evaluated through the ball-growth/merge
process — not a property of the medial-axis graph's edges or the raw
Euclidean/angular relationship between whichever samples happen to be
Voronoi-ridge-incident to `x`.

**A quick proxy was tried today and correctly discarded, not silently
adopted.** Before writing this up, checked whether a cheap arc-length
stand-in ("how far apart along the contour are this vertex's Voronoi-ridge
governor samples") already fixes what angle (θ) can't, on BungeeHairline's
`8` pinch — using `nx.cycle_basis` cycle size as ground truth for
noise-cluster vs. real-bowl-junction nodes. Neither aggregation worked:
widest-pair arc separation saturated to "different contour = infinite" for
almost every vertex (real and noise alike) whenever *any* governor came
from the other contour, and narrowest-pair arc separation was ≈1 sample
step for **both** groups (noise median 1.0, real median 1.0) — because
*any* Voronoi vertex, real or spurious, generically picks up some locally-
adjacent sample as one of its ridge governors; that's a normal feature of
Voronoi ridge structure, not evidence of degeneracy. **Conclusion: the
governor set (`vor.ridge_points` incident to a vertex) is not the same
thing as the true contact set Γ(x), and substituting one for the other —
the shortcut both this proxy and the original θ computation take — is
exactly why neither one is a reliable significance signal near degenerate
input.** Getting Γ(x) right (true nearest-distance minimizers, within a
numerical tolerance, not "whatever Qhull happened to link via a ridge")
is the first real implementation task, before λ itself.

**Implementation plan, in order:**
1. Compute the true contact set per vertex: boundary samples within a
   small tolerance of `r(x)` (the already-computed nearest distance),
   scale-free relative to local sample step, not an absolute pixel epsilon.
2. Implement the discrete λ via the Chaussard/Couprie/Talbot merge process
   (union-find over boundary samples as the ball radius grows from `r(x)`
   upward; λ(x) = the radius increment at which the contact set's
   components first merge into one) rather than any single-pair distance
   proxy.
3. Validate λ alone (no θ) separates BungeeHairline `8`'s noise-cluster
   nodes from its real bowl junctions with a clean gap, not just a
   different-but-still-overlapping range — the bar the two proxies above
   both failed to clear.
4. Re-run the existing 40/40 resolution-invariance sweep on the 6-font/
   8-char probe set — must not regress.
5. Sweep λ's threshold for a wide flat plateau (the same evidence type
   that validated θ's own [50°, 90°] plateau) — if there's no plateau,
   λ is just a differently-shaped tuned constant, not a principled fix.
6. Only then does replacing `prune_redundant_leaves`'s exclusive-ink test
   or wiring into `skeletonize()`/the tracer become a live question — both
   stay explicitly out of scope until 1-5 hold.

**Non-goal for now:** this scoping only concerns the vector (B0) substrate.
It does not resolve or touch the raster pipeline (`core/skeleton.py`,
`tracer.py`'s σ/spur-length/ink-coverage stack), which stays exactly as it
is — untouched and still the shipped default — until B0 itself is fully
de-risked and integrated (a separate, larger, still-unscheduled task; see
B0's own "still NOT integrated" note above).

### C3. Stroke order and direction as one optimization

`order_all_walks` is a 5-key sort; `_orient_walk_for_writing` independently
forces vertical strokes top-to-bottom. Nothing connects them, so stroke *n*'s
direction is chosen without reference to where stroke *n−1* ended — the pen
teleports. Choose order and orientation jointly to minimise total pen-up
travel subject to convention constraints: an asymmetric TSP-path where each
stroke has two orientations, **exactly** solvable by Held–Karp in
milliseconds at ≤15 strokes per glyph.

Also feeds the animation layer: real pen-lift distances would drive lift
timing in `penstroke::animate` instead of a constant gap.

### C4. Kinematic plausibility (research bet, not scheduled)

Plamondon's Kinematic Theory holds that a rapid human movement is a sum of
overlapping lognormal velocity primitives ([Sigma-Lognormal](https://www.sciencedirect.com/science/article/abs/pii/S0031320308004470)).
That makes it a *generative model of what a real pen stroke is* — so
reconstruction error against a lognormal sum is a principled term in C0's
objective, and the fitted σ/μ are per-font "writer" parameters in the same
spirit as B1's nib.

---

## Alternatives surveyed and rejected (for the record)

Researched during this pass; none displaces the junction-first tracer.

- **Learned stroke extraction** ([Chinese character stroke extraction via deep
  structure deformable image registration](https://arxiv.org/abs/2307.04341),
  AAAI 2023; [GAN-based stroke extraction with attention and stroke grouping](https://link.springer.com/chapter/10.1007/978-981-97-5678-0_32))
  — genuinely better than geometry on *stroke semantics*, but the whole class
  depends on per-character **reference stroke priors** (a template set giving
  the expected strokes). Penstroke's core claim is that no per-letter
  template exists anywhere in the system. Also script-specific, where the
  current tracer is script-agnostic by construction. **Rejected on
  architecture, not on quality.**
- **Neural vectorization** ([General Virtual Sketching Framework](https://markmohr.github.io/virtual_sketching/),
  SIGGRAPH 2021; [Deep Sketch Vectorization via Implicit Surface Extraction](https://cragl.cs.gmu.edu/sketchvector/Deep%20Sketch%20Vectorization%20via%20Implicit%20Surface%20Extraction%20(Chuan%20Yan,%20Yong%20Li,%20Deepali%20Aneja,%20Matthew%20Fisher,%20Edgar%20Simo-Serra,%20Yotam%20Gingold%202024%20SIGGRAPH).pdf),
  SIGGRAPH 2024) — solves raster→vector for *noisy scanned* input. We have
  the exact Bézier source. Using a network to recover what the TTF already
  states exactly is strictly lossy. The one idea worth stealing is the
  centerline-as-distance-field representation, which is what B0 gets
  analytically.
- **Writing-order recovery** ([Recovery of drawing order from single-stroke
  handwriting images](https://dl.acm.org/doi/10.1109/34.877517);
  [Writing Order Recovery in Complex and Long Static Handwriting](https://arxiv.org/pdf/2406.03194))
  — this literature targets exactly C3, and confirms the framing (smooth
  continuation at junctions + global order optimization). Worth mining for
  C3's convention constraints; the learned variants need online-handwriting
  training data we do not have.
- **Straight skeleton** ([CGAL 2D Straight Skeleton](https://doc.cgal.org/latest/Straight_skeleton_2/index.html))
  — tempting because it is exact, fast and defined on polygons directly, but
  it is **not** the medial axis for non-convex shapes: its bisectors are
  equidistant to the *supporting lines* of edges, not the edges. Glyphs are
  full of reflex vertices, so it would systematically misplace the
  centerline. Rejected.
- **Topology-driven vectorization** ([Noris et al. 2013](https://cgl.ethz.ch/publications/papers/paperNor13.php))
  — its "reverse drawing" junction procedure reconstructs all possible
  pre-junction drawing states and picks the most likely. This is a
  strictly-more-powerful version of `analyze_junctions`' pairwise tangent
  matching, and is the best available reference for C1. Directly relevant;
  not rejected, folded into C1.

---

## Parameters — two tiers, replacing ~15 module-level globals

**Tier 1 — discovered by the workflow, written into the `.hfont` manifest.**
Measurable statistics of the font: median stroke width (A1/B1), nib angle +
contrast + nominal width and their R² confidence (B1), the font's own
distribution of continuation angles at unambiguous degree-2 nodes, slant.

**Tier 2 — user-set, few, semantic, in discovered units.** Not
`MAX_CONTINUATION_TURN_DEG = 75.0` but a *join eagerness* in units of that
font's own continuation-angle distribution. Not `max_dist = 22` but a merge
radius in stroke-widths. Roughly three knobs, each meaning the same thing on
every font.

---

## Sequencing and gates

| Step | Depends on | Gate |
|---|---|---|
| **A0 junction-anchored spur threshold** | — | sweep protocol; invariance table improves; serif diagnostics inspected |
| A2 Blossom matching | — | zero change on normal fonts (provably identical) |
| A3 sampling density | — | geometry-only; lands with a re-trace |
| A1 scale-free constants | A0 | identical stroke counts at 384px and 1024px on the 6-font set |
| **B0 vector medial axis** | A1 | invariance 40/40 on full charset; coverage ≥ current; diagnostics ≥ current |
| raise default raster size | A1 (moot if B0 lands) | invariance holds at the new default |
| B1 nib fit | A1 | contrast ordering correct across a 20-font spread |
| B2 ray-cast widths | A1 | coverage up, zero stroke-count change (skip if B0 lands) |
| C0 objective | A+B | ranks post-junction-first traces above `_pre_retrace` |
| C1 Euler-spiral pairing | C0 | objective improves on the 6-font set; script fonts unchanged |
| C2 λ/θ filtration | C0, B0 | objective improves; removes 3 mechanisms |
| C3 joint order/direction | C0 | pen-up travel drops; order stays human-plausible |

A0 and A2 are independent, mechanical, and both shrink the code — do them
first. B0 is the large bet; A1 is worth doing regardless because it is the
prerequisite for B0's gate being meaningful.

Every step keeps the sweep protocol from
[tracer_quality_plan.md](tracer_quality_plan.md) — but the
resolution-invariance test is a stronger tripwire and should become its first
check.

## Relationship to the old plan

- **P1** (width continuity in pairing) — *absorbed into C1.* Its standalone
  constant (`RATIO_FREE = 1.6`) sits below EB Garamond's median natural
  variation (2.07), so shipping it as specced would damage the font class it
  targets.
- **P2** (widths along the pixel walk) — *superseded by B2*, itself largely
  subsumed by B0.
- **P3** (vector re-projection) — *promoted into B0.* Filed originally as
  "evaluate, not build"; the measurements say it is the correct domain for
  the geometry half of the pipeline, not a polish pass.
- **P4** (store key migration, char → glyph name) — *unaffected*, stays in
  the old plan; a format decision, not a math one.
