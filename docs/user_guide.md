# Penstroke — User Guide

## What penstroke is

Penstroke turns ordinary font files (TTF) into **hand-drawn writing**. Give it
a font and it works out, for every letter, the individual pen strokes a person
would use to write that letter — where each stroke starts, where it ends, in
what order the strokes are drawn, and how the pen's width swells and thins
along the way. It then re-renders the alphabet as animated SVG that literally
writes itself, stroke by stroke, preserving the font's calligraphic character.

There are no per-font templates and no per-letter rules: the glyph's skeleton
is treated as a graph, and stroke decomposition is solved as a graph problem.
That makes it work on unseen fonts of any style — script, serif, slab, sans,
display — with no configuration.

What you can do with the results:

- **Animated typography** — self-writing titles, handwriting reveals, karaoke
  text (browser SVG out of the box; Houdini for film/motion-graphics work).
- **Hand-tune the strokes** — a round-trip editing workflow through CorelDRAW
  lets you fix any stroke by hand as real Bézier curves and merge the edits
  back losslessly.
- **Typeset traced fonts** — a built-in text layout engine (real HarfBuzz
  shaping, kerning, wrapping, justification) plus Houdini digital assets turn
  traced fonts into laid-out, animatable text geometry.

The toolset has three layers, each usable on its own:

| Layer | What it gives you | Needs |
|---|---|---|
| **Python CLI** (`penstroke`) | Trace fonts → animated SVGs, previews, QA reports, editable stroke data | Python only |
| **Corel edit round** | Hand-edit any stroke in CorelDRAW, merge back | CorelDRAW + the bundled VBA macro |
| **Houdini integration** | Batch TOPs pipeline, `.hfont` bundles, text-layout HDA, draw-on animation | SideFX Houdini |

---

## Installation

Requires **Python ≥ 3.10**.

```bash
git clone <repo>
cd penstroke
pip install -e .            # core
pip install -e .[dev,ocr]   # + pytest/cairosvg + OCR quality metric
```

Core dependencies (installed automatically): numpy, scipy, scikit-image,
Pillow, fonttools, networkx, uharfbuzz.

Additionally:

- **tesseract** (binary on PATH) — only for the optional OCR quality metric;
  everything works without it, the metric is simply skipped.

This installs the `penstroke` command (also runnable as
`python -m penstroke`).

Verify:

```bash
python tests/test_smoke.py        # end-to-end smoke tests
pytest                            # all test files (smoke + layout + fontscan)
```

On Windows, prefix with `PYTHONIOENCODING=utf-8` (the tests print ✓ marks).

---

## Quick start

```bash
penstroke trace path/to/MyFont.ttf output/myfont/
```

Then open `output/myfont/preview.html` in any browser. You'll see the whole
alphabet writing itself, with play/pause, speed control, a scrubber, and a
wireframe mode that shows the raw centerlines. No server, no dependencies —
it's a self-contained HTML file.

---

## What a trace produces

```
output/myfont/
├── preview.html             ← START HERE. Interactive animated viewer
│                              (play/pause/speed/scrub/wireframe/width-band,
│                              plus glyph selection → "Save selection")
├── glyphs/                  Per-letter animated SVGs: a.svg, cap_A.svg,
│                              0.svg, named specials (at.svg, question.svg…),
│                              uXXXX.svg fallback. Each carries
│                              data-baseline/-advance/-pad/-ascent/-descent
│                              so downstream tools can typeset them.
├── glyphs_raw/              Plain per-letter PNGs (input for vision-model QA)
├── diagnostics/             Per-letter QA images: strokes in rainbow order,
│                              numbered starts, direction arrows, per-stroke
│                              panels, animation flipbook strip
├── strokes.json             THE STROKE STORE — source of truth for the
│                              decomposition. Corel edits merge into this
│                              file; everything else re-renders from it.
├── alphabet_animated.svg    All glyphs as one sequenced, animated grid
├── alphabet_static.svg      Same grid, no animation
├── word_demo.html           A demo word typeset from the per-letter SVGs
├── metadata.json            Machine-readable index: per-char file, advance,
│                              baseline, canvas dims, upem. Its presence marks
│                              the trace complete (the Houdini pipeline skips
│                              fonts that have one).
├── report.md                Human-readable quality report
└── source/MyFont.ttf        Copy of the input font
```

**The one mental model that matters:** `strokes.json` is the source of truth.
SVGs, previews, and Houdini bundles are all disposable renders of it. Hand
edits (the Corel round) accumulate in the store and survive re-rendering; a
re-**trace** rebuilds the store from scratch and discards edits, so don't
re-trace a font you've hand-tuned.

---

## CLI reference

### `penstroke trace <font.ttf> <output_dir>`

Trace one font into a self-contained output folder.

| Option | Meaning | Default |
|---|---|---|
| `--name NAME` | Human-readable font name used in previews | TTF filename stem |
| `--letters "abcXYZ"` | Trace exactly these characters (overrides `--charset`) | — |
| `--charset {ascii,latin}` | Character preset, intersected with what the font actually contains | `latin` (ASCII + Latin-1) |
| `--size N` | Rasterization canvas size in pixels | `384` |
| `--word WORD` | Word for `word_demo.html` | `hello world` |
| `--quiet` | Suppress per-letter progress | off |

The trace size/pad are recorded in `metadata.json` (under `trace`), and
every later command reads them from there — you don't need to remember a
non-default `--size`. Only traces made before this was recorded need the
flag passed manually.

A glyph that fails to trace is skipped with a message; the rest of the font
still completes.

### `penstroke qa <output_dir>`

Run the layered QA cascade against the stroke store: geometric checks
(phantom strokes, backtracking), outline-coverage (missed serifs/features),
off-ink strays, and — when a `spec.json` exists — AI-spec count validation.
Judges the *current* decomposition, including any merged Corel edits.

Writes `qa.json` (machine-readable issues) and a `## Cascade QA` section
into `report.md` (replaced on re-run, never stacked). `--letters "abQ"`
restricts the check. Reading the results: an issue confirmed by multiple
layers is usually real; single-layer fires are often noise.

### `penstroke export-corel <output_dir>`

Write an edit CSV for the CorelDRAW macro (one page per glyph, one named
polyline per stroke, in draw order).

| Option | Meaning | Default |
|---|---|---|
| `--csv PATH` | Output CSV path | `<output_dir>/<font>_edit.csv` |
| `--glyphs "abQ"` | Export only these glyphs | all |
| `--selection PATH` | Take the glyph set from a `sel-*.json` saved by preview.html | — |
| `--size N` | Trace rasterization size | read from metadata.json |

### `penstroke import-corel <output_dir> <edited.csv>`

Merge an edited CSV back into `strokes.json` and re-render the whole output
folder (SVGs, previews, report). Only geometry is taken from the edit —
stroke widths are always re-derived from the font's ink, so hand edits can't
corrupt width data. The trace size comes from metadata.json automatically.

### `penstroke sync-edits <output_dir>`

The "no commands to remember" file handshake, one call does both directions:
imports any pending edited CSVs (oldest first), then exports CSVs for any
pending preview.html selections. Idempotent — safe to run repeatedly; this is
what the Houdini TOPs graph calls on every cook.

| Option | Meaning | Default |
|---|---|---|
| `--inbox DIR` | Global selections drop folder | `$PENSTROKE_SELECTIONS` env var, else `<output_dir>/selections/` |
| `--corel DIR` | Global Corel exchange folder (CSV out **and** edited CSV back, same folder) | `$PENSTROKE_COREL` env var, else per-font folders |
| `--size N` | Trace rasterization size | read from metadata.json |

State is content-based: a CSV counts as an edited return only when its
*bytes* changed — a cloud sync or copy that merely bumps mtimes changes
nothing. A CSV that fails to import is quarantined with a `.failed.json`
marker (the sync completes and reports it) and is retried automatically
once the file's content changes.

### `penstroke refresh-previews <root> [<root>…]`

Regenerate `preview.html` for every trace found under the given roots (looks
for `alphabet_animated.svg` recursively). Use after the viewer template
changes — no re-trace, seconds per font.

### `penstroke build-bundles <spec.json> […]`

Build `.hfont` bundles (compiled Houdini geometry + manifest) from spec
files. **Runs under hython only** (the rep builders need Houdini's `hou`
module) — normally invoked by the TOPs graph, not by hand.

---

## Workflow: judging trace quality

1. Open `preview.html`, set the speed low, and watch. The real test:
   **does it look like a person writing?** Stroke order should be natural
   (stems before crossbars, dots last), strokes should stay on the ink.
2. For a suspect letter, open `diagnostics/<letter>.png`. Each stroke is
   shown in rainbow order with a numbered start marker and direction arrow,
   plus isolated per-stroke panels and a flipbook of animation frames —
   enough to see exactly what the tracer decided and where it went wrong.
3. `report.md` triages the whole font into clean / minor / significant, with
   per-letter issue notes. Take the OCR-based entries with a grain of salt
   (punctuation scores poorly by construction); trust your eyes and the
   diagnostics first.

---

## Workflow: hand-editing strokes in CorelDRAW

For when a traced stroke needs human judgment. Round-trips exact Bézier
handles — what you sculpt in Corel is what renders.

**One-time setup:** import `corel/penstroke_corel.bas` into CorelDRAW
(Tools → Macros → Macro Manager). It provides `PenstrokeImport` and
`PenstrokeExportEdits`.

**The loop (with the global drop folders / Houdini cook):**

1. In `preview.html`, click the glyphs that need fixing → **Save selection**
   → drop the `sel-*.json` into the repo's `selections/` folder.
2. Run `penstroke sync-edits <output_dir>` (or cook the Houdini graph).
   A CSV for your selection appears in `corel/`.
3. In CorelDRAW: `PenstrokeImport` the CSV. One page per glyph; each stroke
   is a named object (`s01`, `s02`, … = draw order). Edit nodes, reshape
   curves. **Don't duplicate or rename the stroke objects** — identity is
   by name.
4. `PenstrokeExportEdits` writes the edited CSV back into `corel/`
   (same folder, same file both directions).
5. Run `sync-edits` again. The edit merges into `strokes.json` and the font
   re-renders. Check `preview.html`.

**The pure-CLI variant (no drop folders):** `export-corel` → edit in Corel →
`import-corel <output_dir> <edited.csv>`.

Notes: widths are never round-tripped — they are re-derived from the font's
ink on import, so you only ever edit geometry. Edits accumulate in
`strokes.json` and survive everything except a re-trace.

---

## Workflow: batch tracing

**Everything hand-written from a Google Fonts checkout:**

```bash
git clone --depth 1 https://github.com/google/fonts.git
python scripts/batch_handwriting.py path/to/fonts output/handwriting/
```

Scans `METADATA.pb` for every HANDWRITING-category family, picks the best
TTF (Regular > variable-upright > first), traces each, and writes a browsable
`index.html`. Resumable — re-running skips fonts that already have a
`metadata.json`.

For arbitrary batches, loop `penstroke trace` yourself, or use the Houdini
TOPs graph (below), which adds caching, parallelism, and bundle building.

---

## Workflow: Houdini

Full runbook: [houdini_workflow.md](houdini_workflow.md). The short version:

**Setup (once):**

```
hython scripts/install_houdini_package.py
```

Writes a Houdini package file that wires `$PENSTROKE`, the Python path, and
the HDA scan path. Restart Houdini; `penstroke::tops` and
`penstroke::text_layout` appear in the Tab menu.

**`penstroke::tops`** — the whole pipeline as one TOPs asset:
`font_scan → trace_missing → sync_edits → build_bundle → make_index`.
Point *Font Roots* at TTF folders or a google/fonts checkout, cook
`make_index`, and every font gets traced (cached — already-traced fonts are
skipped), pending Corel edits get synced, and `.hfont` bundles get built
(content-hash cached). Parameters cover name/category filters, charset,
limit, and the drop-folder locations.

**`.hfont` bundles** — a manifest plus compiled geometry "reps" over the
untouched source TTF:

- `strokes` — dense polyline centerlines (default)
- `strokes_bezier` — reduced order-4 Béziers, ~20× fewer CVs, tessellate in
  Houdini on demand
- `outline` — the exact TTF outlines as cubics, winding-normalized

Stroke reps carry the animation attributes `width`, `u`, `stroke_index`,
`arclength` — everything needed for draw-on/reveal effects (see
[../design/animation_handoff.md](../design/animation_handoff.md)).

**`penstroke::text_layout`** — typesets a string in a chosen hfont: real
HarfBuzz shaping and kerning, wrapping, justification. Emits either one point
per glyph (with `name`/`pscale`/`line`/`word`/`charinword`/`idx` for
Copy-to-Points) or assembled glyph geometry, optionally with swept ribbons.

**Demos:** `hython scripts/handwriting_demo.py <bundle.hfont> <out.hip>`
builds a complete draw-on animation scene (constant-speed timing driven by
`arclength`, trim by `u`) and renders a flipbook.

---

## The layout engine, standalone

The text layout engine works without Houdini — shaping, greedy line breaking,
alignment including justify:

```bash
python scripts/layout_preview.py MyFont.ttf out.png --text "the quick brown fox" --width-em 14 --align justify
```

Renders the layout to PNG with measure guides. Needs `uharfbuzz`.
Limitations: no bidi (Arabic/Hebrew word order is not reordered), ligatures
off by default.

---

## Scripts reference

| Script | Runs under | Purpose |
|---|---|---|
| `scripts/batch_handwriting.py` | python | Trace every handwriting family from a google/fonts checkout (resumable, ETA, index.html) |
| `scripts/batch_google_fonts.py` | python | Small fixed demo set (6 fonts, downloads TTFs); edit the `FONTS` list at top |
| `scripts/layout_preview.py` | python | Standalone layout-engine QA render to PNG |
| `scripts/make_title_demo.py` | python | Interactive brush title-card demo HTML from a trace's `strokes.json` |
| `scripts/build_tops_graph.py` | hython | Build `penstroke_tops.hip`; `--make-hda` packages the `penstroke::tops` HDA |
| `scripts/build_text_layout_hda.py` | hython | Build the `penstroke::text_layout` HDA (+ demo hip and render proof) |
| `scripts/install_houdini_package.py` | hython | One-time Houdini package install |
| `scripts/hfont_demo.py` | hython | Minimal hfont usage demo (shaped text + copy-to-points) |
| `scripts/handwriting_demo.py` | hython | Draw-on animation demo scene + flipbook |
| `scripts/run_trace.cmd` / `run_penstroke.cmd` / `run_build.cmd` | cmd | PDG job launchers (scrub Houdini's PYTHONPATH; assume the venv at `<repo>/.venv`) |

---

## Environment variables

| Variable | Meaning |
|---|---|
| `PENSTROKE_SELECTIONS` | Global drop folder for preview.html selection files (default inbox for `sync-edits`) |
| `PENSTROKE_COREL` | Global Corel CSV exchange folder, both directions |

The repo convention is `selections/` and `corel/` at the repo root; the
Houdini package file sets these up for cooked graphs.

---

## Gotchas

- **Re-tracing discards hand edits.** The Houdini pipeline only traces fonts
  *missing* `metadata.json` for exactly this reason. To force a re-trace,
  delete the trace folder — and accept the loss of its edit history.
- **Don't rename or duplicate stroke objects in Corel** — identity is by
  object name (`s01`…); a duplicated name merges two strokes into one.
- **Don't re-save the exchange CSV from Excel** — it will mangle the format
  (the sync detects this, quarantines the file, and tells you; but the edit
  itself is lost). Only the Corel macros should write it.
- **The OCR metric is differential and optional.** It only judges glyphs
  the original font itself OCRs correctly (letters/digits), so it can't
  punish punctuation or hard-to-OCR script fonts. `metadata.json` records
  whether it ran (`qa.ocr_ran`) so reports from machines with and without
  tesseract stay comparable. Per-metric scores live in each letter's
  `metric_scores`, with `worst_metric` naming what drove the headline
  score. `penstroke qa` gives a deeper second opinion.
- **Windows encodings**: the codebase writes UTF-8 everywhere; when running
  the test scripts in a console, `PYTHONIOENCODING=utf-8` avoids cp1252
  crashes on the ✓ output.
