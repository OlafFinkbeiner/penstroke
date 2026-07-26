# Contributing

## Development setup

```bash
git clone <this-repo>
cd penstroke
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# For OCR validation (optional):
pip install pytesseract
brew install tesseract       # macOS, or:
sudo apt install tesseract-ocr   # Linux
```

## Running tests

```bash
pytest                        # all suites (smoke + layout + fontscan)
python tests/test_smoke.py    # smoke suite alone, script mode
```

These run end-to-end against the Caveat TTF in `tests/fixtures/`.
After any non-trivial change to `core/`, `tracer.py`, or
`pipeline.py`, also do a visual check:

```bash
penstroke trace tests/fixtures/caveat.ttf /tmp/visual_check/
open /tmp/visual_check/preview.html
```

Watch at slow speed — the real test is "does it look like a person
writing?" — and read `diagnostics/*.png` for suspect letters. Metrics
improving while the visual gets worse is a known failure mode — a
coverage score from 0.85 → 0.90 can come with subtle ribbon-shape
regressions. For tracer-geometry changes, also sweep the full charset
before/after and compare stroke counts (see
design/code_concept_review.md item 9 for a sweep that caught a real
regression this way).

## Fixing a tracing defect

**Never add per-letter fixes — always generalize.** When a specific
letter looks wrong, treat it as a specimen of a class: diagnose the
mechanism, survey how many other letters share it, fix the mechanism.
Where to look, by symptom:

- **Wrong stroke decomposition** (too many/few strokes, wrong split):
  `tracer.py::analyze_junctions` (the pairing threshold
  `MAX_CONTINUATION_TURN_DEG`) and `build_chains`.
- **Strokes wander off the ink / strays**: the hygiene passes in
  `tracer.py::build_annotated_graph` and
  `core/graph.py::collapse_parallel_edges`.
- **Wrong direction/order**: `_orient_walk_for_writing`,
  `_orient_clockwise_if_closed`, `order_all_walks` in tracer.py.
- **Missing dots/tittles**: `_split_mask_dots` in tracer.py.
- **Skeleton itself wrong**: `core/skeleton.py` — but check the graph
  hygiene passes first.

## Non-Latin scripts

The tracer is script-agnostic by construction — pure geometry, no
stroke-order database — but currently only validated on Latin. To add
a script, no per-script code should be needed: trace a font of that
script, judge the diagnostics, and fix whatever *mechanism* fails
(most likely the writing-order conventions in `order_all_walks`,
which encode Latin habits like top-to-bottom / left-to-right).

## Adding a new output format

`render/` modules each take the standard `traced` data structure
(list of `(xs, ys, widths)` tuples) plus the font-metric `meta` dict
and produce some output. To add a new format (e.g., OBJ for 3D
import, Lottie for web animation, USD for film pipelines):

1. Add a new module under `src/penstroke/render/`.
2. Expose a `make_<format>()` function that takes traced + meta.
3. Wire it into `pipeline.trace_font()` if you want it emitted by
   default, or leave it as a library function callable by user code.

## Code conventions

- **Comments explain why, not what.** Type hints and short names
  cover the "what". Save comment budget for the painful insights
  that aren't obvious from reading the code (junction merge
  distance, why we use 22px specifically, etc.).

- **Module docstrings** describe the module's role and its
  relationship to neighboring modules. The first line should be
  scannable as part of the file listing.

- **One file per concern.** When a file exceeds ~400 lines, split
  it. The original `trace.py` was 816 lines and became unmaintainable.

- **No `print()` in library code.** Use the `verbose` flag in
  `trace_font()` to gate progress output. Library calls should be
  silent by default.

- **Empirical constants get a comment.** Anywhere a tuned threshold
  appears (22px, 0.18, 1.6×), document the reason and the rough
  acceptable range. See `CHANGELOG.md` for the catalog of these.

## Things to avoid

- **Don't add per-letter hacks anywhere.** There is no per-letter
  routing in this codebase by design; a letter-specific fix is a
  class-of-letters fix that hasn't been diagnosed yet.

- **Don't remove `rng=0` from `core/skeleton.py`.** Determinism is
  load-bearing: without it the same glyph yields different skeletons
  across runs and the symptom is intermittent stray strokes. There is
  a regression test (`test_trace_determinism`) — keep it passing.

- **Don't write to absolute paths.** All test fixtures and
  intermediate files should use paths relative to the repo root or
  go through `tempfile`. The original development sessions accreted
  hardcoded `/home/claude/...` paths that broke when the package
  was moved.

- **Don't disable the smoke tests to "make CI pass".** If a smoke
  test breaks, that's signal. Either the change is wrong, or the
  test is wrong (in which case the test should be updated with a
  justifying comment, not skipped).
