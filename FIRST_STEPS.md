# First steps after importing this package

This document captures the one-time setup when you've just unzipped
the package and want to start working with it in Claude Code (or
any normal dev environment).

## 1. Set up the repo

```bash
cd penstroke
git init
git add -A
git commit -m "Initial import from sandbox"
```

## 2. Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,ocr]"
```

The `[dev,ocr]` extras pull in pytest + cairosvg (visual checks) and
pytesseract (OCR quality validation). The `tesseract` binary still
needs to be installed separately:

- macOS: `brew install tesseract`
- Linux: `sudo apt install tesseract-ocr`

Skip those if you don't want OCR validation — the pipeline runs fine
without it; the OCR metric just gets skipped.

## 3. Smoke test

```bash
make test
# or:
python tests/test_smoke.py
```

Expect 5 ✓ lines and "All smoke tests passed."

## 4. First trace

```bash
make trace
# or:
penstroke trace tests/fixtures/caveat.ttf /tmp/caveat_out/
```

Then open `/tmp/caveat_out/alphabet_static.svg` to see all 52 letters
laid out as a grid, and `/tmp/caveat_out/word_demo.html` to see
"hello world" composed from the per-letter SVGs.

## 5. Open in Claude Code

```bash
claude .
```

The agent will read `CLAUDE.md` first and immediately know:
- What the project does
- Where each module lives
- What conventions to follow
- What the open work items are

## Files worth reading first (in order)

1. **README.md** — what the tool does and how to invoke it
2. **CLAUDE.md** — module-by-module orientation, conventions, open items
3. **CHANGELOG.md** — design history, dead ends, empirical constants
4. **CONTRIBUTING.md** — code conventions, common workflows

## What's NOT in this zip (and that's intentional)

- **The 30+ generated font outputs from the original session.** Those
  are reproducible from any TTF by running `penstroke trace` or
  `python scripts/batch_google_fonts.py`. Carrying ~50 MB of reproducible
  output around is wasteful.
- **A `.git/` directory.** You should initialize git yourself so the
  history starts clean from your end.
- **A `.venv/`.** Build your own.

## What IS in this zip

- Full source code
- Caveat TTF fixture for tests (OFL-licensed, safe to redistribute)
- HTML viewer template with play/pause/speed/scrub/wireframe
- Smoke tests
- Documentation (CLAUDE.md, CHANGELOG.md, CONTRIBUTING.md, README.md)
- Makefile with common commands
- pyproject.toml ready for `pip install -e .`
