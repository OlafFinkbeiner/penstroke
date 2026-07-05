# Penstroke — full code & concept review (2026-07-05)

Scope: every module in `src/penstroke/`, `scripts/`, `tests/`, packaging and
docs. Four parallel deep-review passes (core tracing, formats/integration,
rendering/QA/CLI, scripts/tests/docs), findings verified against source and —
for the tracer — empirically against the caveat.ttf fixture. Severity-ranked;
duplicated findings across passes merged.

---

## Verdict in one paragraph

The core concept — glyph skeleton as a multigraph, stroke decomposition as a
global junction-pairing (matching) problem decided before any walking — is
sound and elegantly implemented, and the codebase's documentation-of-rationale
discipline is far above typical research-pipeline code. The weaknesses are
almost all at the *seams*: trace-time parameters (`size`/`pad`) travel by
convention instead of data; the source-of-truth files are written
non-atomically; the file handshake trusts mtimes; the QA layer's best
detector (the cascade) is orphaned while its weakest (OCR) drives the report;
and the strongest single conceptual improvement available (width continuity
in junction pairing) is sitting unused next to data the pipeline already
computes.

---

## A. Confirmed bugs (fix first)

**A1. '8'-class glyphs are traced twice.** `core/strokes.py:210-225` —
`trace_closed_loops` skips components that have an *endpoint* (degree-1
pixel), but should skip components that produced *any* graph node (degree 1
or ≥3). A component with junctions but no endpoints ('8', 'θ', 'φ', 'Ø') is
decomposed by the junction-first pipeline *and* walked again as an orphan
loop → duplicated overlapping stroke. Verified on caveat.ttf '8'. One-line
concept fix: test for "contains any special point", not "has an endpoint".

**A2. Charset `all` is offered by the Houdini HDA but rejected by the CLI.**
`cli.py:84` (`choices=['ascii','latin']`) vs `scripts/build_tops_graph.py`
(HDA menu and argparse both offer `all`; `TRACE_CODE` bakes `--charset %s`
into the command). Selecting Charset=all in `penstroke::tops` makes every
trace work-item fail at cook time. `charset.py` and the pipeline docstring
both support/recommend `'all'`. Fix: add `'all'` to the CLI choices.

**A3. `uharfbuzz` is an undeclared dependency.** `layout.py:43,69` imports
it; it is in neither `pyproject.toml` dependencies nor any extra, nor the
README dependency list. Fresh install → `test_layout.py` and the
`text_layout` HDA fail with a bare ImportError. Declare it (dependency or
`[layout]` extra) and document it.

**A4. Trace `size`/`pad` travel by convention, not data — silent width
corruption.** Found independently by two review passes. The CSV header
carries `size` but `import_edit_csv` ignores it (`editround.py:282` parsed,
`:366` unused); `export-corel`/`import-corel`/`sync-edits` all default
`--size 384`; `metadata.json` records canvas dims but not `size`/`pad`
(`quality/report.py:151-158`); `rep_strokes.py:51-52` hand-mirrors
`DEFAULT_TRACE_SIZE=384 / PAD=40`. Trace at `--size 512`, sync at the
default, and `resample_widths` samples a wrong-scale distance field —
floored to `MIN_WIDTH_PX`, so it degrades *silently* into hairlines. Fix:
record `size`/`pad` (and charset, tracer version) in `metadata.json`, honor
the CSV header on import, and have the Houdini reps read the recorded values.
This is the project's own "discover, don't hardcode" rule applied to itself.

**A5. `collapse_parallel_edges` mutates paths it decides NOT to merge.**
`core/graph.py:344-355` — survivors of a rejected merge are emitted as
resampled, int-rounded versions; `n_samples` is the mean of all parallel
paths' lengths, so a 200-px arc paired with a 10-px chord is resampled to
~105 points (~2-px spacing, no longer an 8-connected chain). This widens the
tracer's 20-index tangent windows to ~40 px of geometry, silently changing
junction decisions for exactly the hook/loop shapes the length-ratio guard
protects. Fix: unmerged edges keep their original `data['path']`.

**A6. `merge_nearby_junctions` splices straight jumps (≤22 px) into paths.**
`core/graph.py:217-221` — rewiring prepends/appends the cluster
representative, creating a discontinuity that (a) dominates end tangents fed
to `analyze_junctions`, and (b) can spuriously trip the scrambled-path
detector (ratio > 1.6) on short edges. Fix: interpolate pixels across the
splice, or make tangent windows skip the splice segment.

**A7. Wobble's tangential noise component is miswired.**
`core/smoothing.py:89-90` — the "along-tangent" term uses `nx_` for x and
`ny_` for y, which is axis-aligned anisotropic noise, not a tangential
displacement. Intended was almost certainly the second OU process (`ny_`) as
the scalar for both components. Subtle visually; half the wobble model is
wasted as written.

---

## B. Data-integrity risks (the edit round)

The Corel round-trip's *concept* is right (see D below), but its state
substrate is fragile. All four fixes share one idea: **content hashes, not
mtimes** — the pattern already proven in `houdini/build_bundle.py:18`.

**B1. Non-atomic writes to the source of truth.** `editround.py:71,136`,
`hfont.py:92-94` — `strokes.json` (documented as "the SOURCE OF TRUTH",
accumulating hours of hand edits) and `manifest.json` are written with plain
`json.dump`; a crash mid-write truncates them, no backup, no temp+rename.
Compounding: a truncated manifest raises `json.JSONDecodeError`, which is not
an `HFontError`, so `hfont.validate()` crashes instead of reporting. One
`write-to-temp + os.replace` helper fixes the whole class.

**B2. Store-write failures are silently swallowed.** `pipeline.py:229-234`
wraps the stroke-store save in `except Exception: pass`. A disk/permission
error leaves a stale store; a later `export-corel` silently exports old
strokes. The store failure should raise (or at minimum warn loudly); the
sibling swallows for raw glyphs/diagnostics are cosmetic and fine.

**B3. mtime-as-content-proxy forges edits.** `handshake.py:196-208` — any
mtime bump (OneDrive/Dropbox sync, robocopy, disk move, `touch`) flips a
pristine export into an "edited return"; the next cook imports it. Because
`write_edit_csv` smooths and Bézier-fits on the way out, this **silently
replaces the store's raw traced polylines with flattened fitted curves** —
geometry mutation with no human in the loop. Record a content hash in the
sidecar; keep mtime only as a fast path.

**B4. One malformed CSV wedges `sync-edits` permanently.**
`editround.py:269-310` parses with bare `int()/float()`, never checks the
`penstroke-edit` magic/version; a failed import never writes
`.imported.json`, so `pending_edits` returns the same poison file every cook
and the crash blocks all other work. Triggers: Excel re-save, truncated
export, foreign CSV whose name matches a font token. Fix: validate, and
quarantine failures with a `.failed.json` marker instead of crashing.

**B5. Torn-read race can permanently drop the tail of an edit.**
`handshake.py:181-193` — if a cook reads a CSV while Corel is mid-write, the
partial file imports, and the marker (mtime sampled *after* the read) may
then be ≥ the completed file's mtime, so the finished edit never re-imports.
Content-hash markers (B3) close this too.

**B6. Filename routing multi-delivers on prefix families.**
`handshake.py:124-147,211-225` — `exo 2_edited.csv` routes to both Exo and
Exo 2; `alegreya sans fix.csv` to both Alegreya and Alegreya Sans. The wrong
font's store then gets polluted (persistently, per B1). Needs a
longest/unique-match rule, or require the exact sidecar route when ambiguous.

**B7. Duplicate Corel object names silently concatenate strokes.**
`editround.py:294-309` — two objects named `s03` (a one-keystroke accident in
Corel) merge into one polyline resampled *across the gap* — recreating the
interpolate-across-whitespace stray-stroke class the tracer eliminated.
Detect duplicate names and report, don't absorb.

---

## C. Quality-assurance layer

**C1. The QA cascade is well-designed and orphaned.** `quality/cascade.py`'s
layered design (geometric → outline-coverage → spec → off-ink →
invented-terminal-fork, with "multi-layer fires are real, single-layer fires
are noise") is the best QA thinking in the repo — and nothing runs it: no CLI
verb, not called by `trace_font`, findings never reach report.md. Meanwhile
the shipped report is driven by the weakest detectors. Also
`run_cascade_on_output` re-traces every glyph although the pipeline persists
the exact decomposition in `strokes.json`. Recommendation: add
`penstroke qa <output_dir>` reading the stroke store; fold cascade counts
into report.md/metadata.json.

**C2. The OCR metric poisons the report — non-reproducibly.**
`quality/ocr.py:67-68` whitelists letters+digits only, so every punctuation
glyph scores 0.5 with an issue string; `report.py` takes `overall = min(...)`
and tiers ≤0.6-with-issues as "significant". Result: a perfect trace reports
~30 punctuation glyphs as ❌ on machines with tesseract and ✅ without it,
and the report never says whether OCR ran. Skip non-recognizable chars,
record whether OCR ran, and stop letting one noisy metric own the tier.

**C3. `overall_score = min(metrics)` + free-text issues make metadata.json
machine-shaped, not machine-readable.** Per-metric scores are already
computed; ship them, plus which metric was the minimum.

**C4. `coverage` is recall-only.** `quality/metrics.py:31-75` — a stray
stroke through white space costs nothing (and can *raise* the score by
crossing ink). Since strays are a documented residual class, the headline
metric is blind to it. Add a precision term (ink-drawn-outside-glyph /
drawn).

**C5. Dangling references to a `spec` module/CLI that doesn't exist.**
`quality/glyph_image.py:3,33`, `pipeline.py:144-145` point at
`quality/spec.py` and a `penstroke spec` subcommand — neither exists; the
real workflow is the manual in-session agent fan-out.

---

## D. Concept review

**D1. Junction-first decomposition — keep.** Modeling pairing as a matching
on edge-*ends* makes chains provably simple paths/cycles that partition the
edge set; order-dependence bugs are eliminated by construction. The
docstrings record why greedy Hierholzer failed. This is the strongest idea in
the codebase and the right abstraction.

**D2. Highest-leverage improvement: width continuity in junction pairing.**
`analyze_junctions` decides continuation from tangent angle alone, yet the
distance transform (already computed, already passed around) encodes a second
discriminative signal: real pen strokes continue with continuous width. A
thin serif meeting a thick stem at a passable angle is today a "smooth
continuation" despite a 3× width discontinuity — precisely the documented
`over_split` serif-stub residual class (and its under-split dual). Add a
width-ratio term to the pair score.

**D3. The single 75° threshold is good, not stable.** A cursive apex and an
'x' crossing can present identical local angles; script wants permissive,
geometric sans wants strict. It's the most tuned-by-eye number in the system.
Consider per-font calibration from the glyph statistics the pipeline already
computes, or express the criterion with local curvature history.

**D4. Raster→skeleton is lossy, and the five hygiene passes are its rent.**
Rasterize exact Béziers → blur (σ=1.5) → skeletonize → spline-fit to undo the
quantization: spur pruning, junction merging, parallel collapse, scramble
repair, and leaf pruning all exist to compensate raster artifacts a
vector-side medial axis wouldn't produce. Raster MAT is robust and
script-agnostic — the choice is defensible — but as animation fidelity
demands rise (the "real centerline + brush" direction), a middle path is
attractive: keep the raster skeleton for *topology*, re-project chain
geometry onto the vector outline's midline before smoothing.

**D5. Resolution-dependence is pervasive while `size` is a public knob.**
σ=1.5, spur floor 12, merge radius 22, dot area 30, walk caps, tangent
windows, Y_BAND 60 … all in pixels, tuned at 384/512. Tracing at `size=192`
loses tittles, drops small counters, and σ erases hairlines. Either freeze
the canvas size as a named constant and document that changing it invalidates
tuning, or express thresholds in em-/stroke-width-relative units. (Related:
A4.)

**D6. Width model artifacts.** Nearest-pixel distance sampling bulges at
crossings (blob radius, not half-width) and *underestimates* wherever the
smoothing spline deviates from the ridge in tight curves. Cheap fix: sample
the distance field along the pre-spline pixel walk and carry widths through
resampling. The planned pen/nib analysis (open item 6) is the right longer
arc.

**D7. `core/strokes.py` is a divergent second theory, not a fallback.**
`decompose_strokes`/`build_strokes`/`order_strokes` have zero callers
(grep-verified) yet implement a *greedy* variant of junction pairing with
different constants (dot-product 0.3 ≈ 72.5° vs the tracer's global 75°).
CLAUDE.md still describes it as the non-Latin fallback; nothing routes to it.
Delete all but `tangent_at` and `trace_closed_loops`; if a fallback is ever
wanted, route it through `analyze_junctions` with different constants. Also
dead: `tan_u`/`tan_v`/`retrace` edge annotations (tracer.py:370-372, computed
per edge, never read) and `graph.py::build_skel_pixel_graph`.

**D8. .hfont format — well designed, three gaps.** Manifest-as-spec,
packed-prim-per-glyph keyed by post name, em-space baking, shaping against
the untouched TTF: clean. Gaps: (a) version is a single int with hard
equality — no additive evolution path; adopt major.minor or a features list
now, while there's one consumer. (b) `validate()` checks file existence but
never the declared attribute contract (`width`/`u`/`name`). (c) Latent wall:
the format keys glyphs by **post name** specifically to support ligatures,
but `strokes.json` is keyed by **character** — `f_i` has no codepoint, so the
strokes rep can never carry ligatures while the store is char-keyed. Decide
the store's key migration before flipping `liga` on.

**D9. Rep interchangeability is violated by `arclength`.** Dense rep:
polyline length. Bézier rep: CV-polygon length (overestimate, admitted in a
comment). Same attribute name, same declared contract — swapping reps changes
draw-on timing. Flatten-and-measure at build time.

**D10. Layout engine scope is right; state the exclusions.** HarfBuzz owns
shaping, the engine owns breaking/alignment — correct division. But: no bidi
(RTL word order comes out reversed even though each word shapes correctly),
tabs silently collapse unless the font maps U+0009, an overfull word
overflows the measure. All acceptable for v1; all should be documented as
loudly as the liga note. Also `layout.py:36` `_WORD_CACHE` grows without
bound in a long Houdini session — needs an LRU or per-font purge.

**D11. Two normalizations for one identity concept.** `fontscan.py:211`
(lower + strip spaces) vs `handshake._norm` (strip all non-alphanumerics),
with a comment claiming they're the same. Families with hyphens fork identity
between scan-dedupe and edit routing. One shared function.

**D12. Frame math is duplicated across the trace/Houdini boundary.**
`rep_strokes.py:55-70` re-derives `baseline_y` with a "Mirrors
core/rasterize.py" comment; a future rasterizer change silently misaligns
every bundle by a sub-pixel baseline shift. Move the formula into one
hython-importable module both sides import. Same disease: `N_RESAMPLE=240`
duplicated by comment-convention between `editround.py` and `smoothing.py`;
the timing formula triplicated across `glyph.py`/`alphabet.py`/the diagnostic
flipbook (which uses a *different* model, so the flipbook doesn't faithfully
preview the SVG's pacing).

---

## E. Rendering / animation

- **E1.** Standalone SVGs don't reset the ribbon at loop restart
  (`glyph.py:104-121`, same in alphabet.py): second loop shows fully-drawn
  letters that blink off as their redraw begins. Invisible in preview.html
  only because the viewer drives time manually. Add the same `<set>` reset
  the guide has.
- **E2.** Duplicate `id="loopAnim"` when word.py inlines multiple glyph SVGs
  into one HTML document — all letters' loop timing can bind to the first
  glyph's animator. Suffix ids per embedding.
- **E3.** Viewer scrubber floor of 100 s (`viewer_template.html:234`) — short
  traces get a dead tail; initialize from parsed durations.
- **E4.** `word.py` bypasses `safe_filename` (special chars in `--word`
  silently missing), iterates `set(word)` (nondeterministic spacing/baseline
  sample), and is the one pipeline step not wrapped in try/except — a
  malformed glyph SVG kills `trace_font` at the very end.
- **E5.** Two-path animation (dasharray guide + fading ribbon) is the right
  decomposition under SVG's constant-stroke-width constraint, and the
  opacity gating around round linecaps is clever. Known aesthetic limit: the
  ribbon fades in as a whole rather than trailing the pen tip — the planned
  real-centerline+brush direction supersedes this.
- **E6.** `diagnostic.py` reloads arial.ttf inside per-stroke loops and
  hardcodes a Windows font name (silent tiny-font fallback elsewhere).

---

## F. Tests, docs, packaging, hygiene

- **F1. The core algorithm is essentially untested.** tracer.py (869 lines):
  five loose stroke-count bounds. Untested: `analyze_junctions`,
  `build_chains`, orientation/ordering, `_split_mask_dots`,
  `collapse_parallel_edges` (with its load-bearing guard), `hfont.py`, and —
  cheapest, highest-value — **the determinism invariant**: a
  trace-twice-assert-identical test would have caught the historic
  "intermittent stray strokes" class and costs minutes to write. Well-tested,
  credit due: handshake (excellent), layout, fontscan, curvefit.
- **F2. `make test` runs only test_smoke.py** — test_layout (11 tests) and
  test_fontscan (3) are silently skipped by the documented workflow, though
  pytest is configured and would run all. Make `pytest` canonical.
- **F3. CONTRIBUTING.md documents the deleted Hershey stack** — including an
  "Adding letter-specific fixes" section that directly contradicts the
  project's own "never per-letter fixes" rule. Rewrite the three middle
  sections for the EPST tracer.
- **F4. README drift**: claims Greek/Hebrew support (removed in v0.2); omits
  `strokes.json` from the output map; documents one CLI command of six;
  FIRST_STEPS expects 5 test ✓s (now 8) and still gives "just unzipped"
  git-init instructions. houdini_workflow.md claims corel/*.cdr are
  gitignored; three are tracked. → docs/user_guide.md (written alongside this
  review) is now the user-facing source; fold the README down to a pointer
  plus quick start.
- **F5. Encoding-rule violations (project rule: every text open() passes
  encoding='utf-8')**: exactly three in the tree —
  `houdini/build_bundle.py:39,55` and `scripts/batch_google_fonts.py:122`
  (which writes U+00B7 and is cp1252-lucky, not correct). Everything else
  complies; handshake.py even correctly uses utf-8-sig.
- **F6. `batch_google_fonts.py` hardcodes a FONTS list** — the generalized
  replacements exist (`fontscan.py`, `batch_handwriting.py`); flag it
  [LEGACY/demo] or derive from a checkout per the no-hardcoding rule.
- **F7.** Makefile is unix-only (`/tmp`, `find -exec`) in a Windows-first
  project; works under Git Bash only, undocumented.
- **F8.** `render/houdini.py` is zombie API surface (only test_smoke imports
  it; superseded by the hfont strokes rep) — delete or explicitly bless.
- **F9.** Per-glyph font re-parsing: `rasterize_glyph` and `extract_outlines`
  each do a fresh `TTFont(path)` per glyph — ~140 parses per 70-glyph font,
  multiplied across batches. An lru_cache keyed by path fixes it.
- **F10.** Small correctness notes: `smooth_and_wobble`'s blanket
  `except Exception: return None` silently drops strokes with zero telemetry
  (count/log them); silent charset fallback to `_FALLBACK_LETTERS` on any
  `font_charset` error (pipeline.py:124-129); `np.roll` neighbor counting
  wraps at canvas borders (masked by pad today); the oldest-skimage fallback
  calls `np.random.seed(0)` (global RNG pollution — use RandomState);
  `hfont.py` never closes `TTFont` handles (Windows file locks — the very
  problem create_bundle works around); `curvefit.py:171-175` endpoint restore
  leaves a curvature kink exactly at stroke ends.

---

## G. Prioritized action list

**This week (small, high value):** — ✅ all five done 2026-07-05
1. ✅ A2 — CLI charset choices now derived from `charset.CHARSETS` (fixes
   `all` and removes the hardcoded list that caused the drift).
2. ✅ A3 — `uharfbuzz>=0.37` declared as a core dependency.
3. ✅ A1 — orphan-loop skip is now a covered-pixel test against the traced
   graph's edges (`split_components` passes the covered set to
   `trace_closed_loops`). Stronger than the "any special point" fix: it also
   rescues loops whose graph edge was dropped as a short self-loop during
   junction merging. Caveat '8': 2 strokes → 1, all other glyphs unchanged.
4. ✅ B1+B2 — new stdlib-only `fileio.write_json_atomic` (temp +
   `os.replace`) used by `save_stroke_store`, `merge_stroke_bez`,
   `save_manifest`; `load_manifest` wraps `JSONDecodeError` in `HFontError`;
   `pipeline.trace_font` no longer swallows store-write failures.
5. ✅ F1 — `test_trace_determinism` (trace a/g/8/X twice, assert identical
   arrays) + `test_closed_loops_not_double_traced` (pairwise stroke-overlap
   check on 8/o/B) added to test_smoke.py. Full suite: 24/24 pass.

**This month (structural):**
6. ✅ A4 (2026-07-05) — metadata.json records a `trace` block (size, pad,
   charset when used); export/import/sync `--size` defaults to reading it
   (`cli._resolve_size`); `import_edit_csv` cross-checks the CSV header and
   warns on conflict; rep builders resolve via
   `rep_strokes.trace_params_for_store`. Legacy traces fall back to 384/40.
7. ✅ B3/B4/B5 + B6-partial (2026-07-05) — handshake sidecars record
   `csv_sha1`; pristine/imported checks compare content (mtime only as a
   fast path; legacy markers keep old semantics); torn reads guarded by
   hashing before parse; failed imports quarantined with `.failed.json`
   (retried on content change) and the CSV header magic is validated with
   a clear error. Routing: the outgoing sidecar is now authoritative when
   present (fixes prefix-family multi-delivery for our own exports);
   free-form Corel names without a sidecar remain token-matched — the
   residual B6 ambiguity, acceptable.
8. ◐ C1 done / C2 open (2026-07-05) — `penstroke qa <output_dir>` runs the
   cascade against the STROKE STORE (judges the current decomposition incl.
   Corel edits; no re-trace), writes qa.json + an idempotent `## Cascade
   QA` report.md section. Bonus fix: the geometric layer's tortuosity
   check now exempts closed strokes (every o/0/8 used to fire as
   "zigzag"). Still open (C2): demote/fix the OCR metric, per-metric
   scores in metadata.json; and the outline-coverage layer looks
   over-eager on loop glyphs — calibrate before trusting it solo.
9. A5/A6 — parallel-collapse and junction-merge geometry fixes.
10. D7 — delete the dead strokes.py decomposition stack + dead annotations.

**The concept bets (when quality work resumes):**
11. D2 — width-continuity term in junction pairing (targets the documented
    over_split residual class directly).
12. D6 — sample widths along the pre-spline walk (removes the tight-curve
    width underestimate).
13. D4 — evaluate re-projecting chain geometry onto the vector outline
    midline (raster topology, vector geometry).
14. D8c — decide the strokes.json key migration (char → glyph name) before
    ligatures.

---

## H. What is genuinely strong (don't break)

- The matching-on-edge-ends formulation and its global-before-walking
  discipline (correctness by construction, not tuning).
- Derived thresholds with recorded derivations (`prune_redundant_leaves`'s
  square-cap geometry; the three performance caps, each naming its
  motivating pathological font class and guaranteeing byte-identical output
  for normal fonts).
- Determinism as a first-class, documented requirement (`rng=0` with the
  failure symptom written down).
- `hfont.py` as spec-and-sole-implementation; shaping delegated to HarfBuzz
  against the untouched TTF.
- Width re-derivation on Corel import — hand edits can't corrupt width data
  because width data is never editable.
- The diagnostic PNG as a purpose-built human+vision-model QA artifact; the
  dependency-free viewer's manual-clock scrubbing design.
- Windows reality handled deliberately: utf-8-sig for BOMs, decimal-comma
  tolerance for German-locale VBA, the mmap-lock fallback verified by
  filecmp, near-perfect encoding discipline (3 misses in 40+ call sites).
- Per-glyph fault isolation in the pipeline; `safe_filename` with a
  reversible map recorded in metadata.json.
- `rep_outline.py`'s quadratic→cubic conversion including the
  on-curve-less-contour case most converters get wrong.
