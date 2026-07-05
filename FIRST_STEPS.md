# First steps

One-time setup for a fresh clone. Full usage documentation lives in
[docs/user_guide.md](docs/user_guide.md).

## 1. Install

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev,ocr]"
```

The `[dev,ocr]` extras pull in pytest + cairosvg (visual checks) and
pytesseract (OCR quality validation). The `tesseract` binary still
needs to be installed separately:

- macOS: `brew install tesseract`
- Linux: `sudo apt install tesseract-ocr`
- Windows: the UB Mannheim installer, then add it to PATH

Skip those if you don't want OCR validation — the pipeline runs fine
without it; the OCR metric is skipped and `metadata.json` records
that it didn't run (`qa.ocr_ran`).

Note for the Houdini side: the PDG job launchers expect the venv at
`<repo>/.venv` exactly.

## 2. Test

```bash
pytest
# on a Windows console:
PYTHONIOENCODING=utf-8 pytest
```

All tests should pass (26 at the time of writing) and take well under
a minute.

## 3. First trace

```bash
penstroke trace tests/fixtures/caveat.ttf output/caveat_first/
```

Open `output/caveat_first/preview.html` in a browser: the alphabet
writes itself, with play/pause/speed/scrub/wireframe controls. For
per-letter detail, look at `diagnostics/*.png`.

## 4. Houdini (optional)

```bash
hython scripts/install_houdini_package.py
```

Restart Houdini; `penstroke::tops` and `penstroke::text_layout` appear
in the Tab menu. Runbook: [docs/houdini_workflow.md](docs/houdini_workflow.md).

## Files worth reading first (in order)

1. **README.md** — what the tool does and the pipeline in 7 steps
2. **docs/user_guide.md** — every command and workflow, with gotchas
3. **CLAUDE.md** — module-by-module orientation, conventions, open items
4. **CHANGELOG.md** — design history, dead ends, empirical constants
5. **CONTRIBUTING.md** — code conventions, how to fix tracing defects
6. **design/code_concept_review.md** — current code-review findings and
   the prioritized fix list
