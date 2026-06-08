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
python tests/test_smoke.py
```

These run end-to-end against the Caveat TTF in `tests/fixtures/`.
After any non-trivial change to `core/`, `templates/`, or
`pipeline.py`, also do a visual check:

```bash
penstroke trace tests/fixtures/caveat.ttf /tmp/visual_check/
open /tmp/visual_check/alphabet_static.svg
```

Compare to the expected look across the typical letters (A, B, R,
X, a, g, e). Metrics improving while the visual gets worse is a
known failure mode — a coverage score from 0.85 → 0.90 can come
with subtle ribbon-shape regressions.

## Adding letter-specific fixes

When a letter renders wrong across multiple fonts, the fix usually
goes in `src/penstroke/templates/selection.py`. The `LETTER_STRATEGY`
dict maps each character to an ordered list of Hershey font variants
to try. Most uppercase letters and several lowercase ones are locked
to `rowmans` because the topology-based scoring would otherwise pick
a worse template (cursive's 1-stroke A, etc.).

```python
LETTER_STRATEGY = {
    'a': ['cursive', 'rowmans'],
    'A': ['rowmans'],
    # ...
}
```

If the chosen template is right but the trace shape is wrong, the
issue is in `src/penstroke/templates/trace.py` — usually the
Hershey-to-mask coordinate mapping or the waypoint snapping logic.

## Adding a new script

For scripts Hershey covers (Greek, Cyrillic, Gothic), follow the
Greek pattern in `src/penstroke/templates/scripts.py`:

1. Build a Unicode → ASCII-slot mapping dict.
2. Add a routing branch in `choose_template()` in `selection.py`.

For scripts Hershey doesn't cover (Hebrew, Arabic, Devanagari,
CJK), the pipeline falls back to geometric decomposition in
`core/strokes.py`. Quality is workable for simple letterforms,
patchy for complex ones. To improve: hand-author per-letter
templates in a new module like `templates/hebrew_templates.py`
and add a routing branch.

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

- **Don't add per-letter hacks to `pipeline.py`.** Letter-specific
  behavior belongs in `templates/selection.py` (template choice)
  or `templates/trace.py` (tracing logic).

- **Don't broaden the Hershey font search globally.** Adding a font
  to `DEFAULT_ORDER` in `selection.py` affects all 50+ characters
  and is hard to test exhaustively. Add it to `LETTER_STRATEGY` for
  specific characters instead.

- **Don't write to absolute paths.** All test fixtures and
  intermediate files should use paths relative to the repo root or
  go through `tempfile`. The original development sessions accreted
  hardcoded `/home/claude/...` paths that broke when the package
  was moved.

- **Don't disable the smoke tests to "make CI pass".** If a smoke
  test breaks, that's signal. Either the change is wrong, or the
  test is wrong (in which case the test should be updated with a
  justifying comment, not skipped).
