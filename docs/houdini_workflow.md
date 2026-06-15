# Penstroke in Houdini — workflow guide

How to run the whole pipeline from inside Houdini: trace fonts, edit
their strokes in CorelDRAW, and lay out text with the resulting
hand-drawn glyphs. No Python knowledge required — after a one-time
install, everything is driven by cooking a TOPs network.

> If you only want to trace a font to animated SVGs (no Houdini), see
> the main [README](../README.md) — that covers the Python CLI side.

---

## Mental model

There are **two sides** and they live in separate folders on purpose:

| | Where | What it is |
|---|---|---|
| **Trace workshop** | `output/handwriting/<font>/` | The Python side. One folder per font: the source TTF, `strokes.json` (the source of truth for the stroke decomposition), an interactive `preview.html`, diagnostics. This is what gets edited. |
| **hfont bundles** | `output/hfont_dev/hfonts/<font>.hfont/` | The compiled product Houdini consumes: em-space geometry packed as `.bgeo.sc`, one prim per glyph, plus a `manifest.json` and a copy of the TTF. Disposable — rebuildable from the workshop at any time. |

Manual steps (selecting glyphs to fix, editing in Corel) never block
the cook. They hand work over through **two global drop folders** at
the repo root:

| Folder | Direction | Files |
|---|---|---|
| `selections/` | preview.html → pipeline | `sel-<font>-<hash>.json` — which glyphs you want to fix |
| `corel/` | pipeline ↔ CorelDRAW | `sel-<font>-<hash>.csv` out for editing; the edited CSV comes **back into the same folder** |

The TOPs graph picks up whatever is in those folders on the next cook.

---

## One-time setup

Install the Houdini package (replaces the old editable pip install):

```
hython scripts/install_houdini_package.py
```

This writes `<houdini-prefs>/packages/penstroke.json`, which on every
future Houdini/hython session sets:

- `$PENSTROKE` → this repo
- `PYTHONPATH` += `$PENSTROKE/src` (so `import penstroke` works)
- `HOUDINI_PATH` += `$PENSTROKE/houdini` (so the OTLs auto-load)

After this, both digital assets are available in the Tab menu:

- **`penstroke::tops`** — the batch pipeline (this guide)
- **`penstroke::text_layout`** — lay text out in an hfont (below)

Rebuild the package file only if you move the repo. Rebuild the HDA
(`hython scripts/build_tops_graph.py --make-hda`) only after changing
the graph's Python code.

---

## The graph at a glance

Drop a **`penstroke::tops`** node in `/obj` (or open the ready-made
`output/hfont_dev/penstroke_tops.hip`). Inside, the chain is:

```
font_scan → trace_missing → sync_edits → build_bundle → wait_all → make_index
```

- **font_scan** — discovers fonts under the configured roots, one work
  item each.
- **trace_missing** — runs `penstroke trace` for any font not yet
  traced. Already-traced fonts (their `metadata.json` exists) are
  skipped instantly — this is the cache/resume mechanism, so re-cooking
  is cheap.
- **sync_edits** — the Corel handshake. Only fonts with pending
  selections or edited CSVs get a command; everything else is free.
- **build_bundle** — (re)builds each `.hfont`. An mtime check rebuilds
  the strokes rep whenever `strokes.json` changed, so a freshly merged
  edit produces an updated bundle **in the same cook**.
- **make_index** — writes `hfonts/index.html` (the bundle library) and
  `handwriting/index.html` (links every font's preview.html).

Cook the **make_index** node (the display node) to run the whole thing.

### Parameters (top level of the node)

| Parm | Meaning |
|---|---|
| **Font Roots** | Directories to scan, one per line: a google/fonts checkout, penstroke trace outputs, or plain TTF folders. |
| **Traces Root** | Where fresh traces are written (default `$PENSTROKE/output/handwriting`). |
| **Hfonts Root** | Where bundles are written (default `$PENSTROKE/output/hfont_dev/hfonts`). |
| **Selections Inbox** | Global selection drop folder (default `$PENSTROKE/selections`). |
| **Corel Exchange** | Global Corel CSV folder, both directions (default `$PENSTROKE/corel`). |
| **Family Name Regex** | Restrict to matching families. Empty = all. |
| **Google Fonts Category** | e.g. `HANDWRITING`. Only applies to google/fonts METADATA.pb records. |
| **Charset** | `ascii` / `latin` / `all`. |
| **Limit** | Cap the number of fonts (0 = all). |
| **Build Reduced Bézier Rep** | Also build `strokes_bezier` (order-4 Bézier curves, ~20× fewer points, tessellate on demand). On by default. |

---

## Workflow 1 — trace fonts into bundles

1. Set **Font Roots** to a folder of TTFs (or a google/fonts checkout,
   with **Category** = `HANDWRITING` to filter).
2. Cook **make_index**.
3. New fonts get traced; bundles land under **Hfonts Root**; open
   `hfonts/index.html` to browse them.

Re-cooking later only traces fonts that are new — existing ones are
skipped, so this is safe to run repeatedly.

---

## Workflow 2 — fix a font's strokes (CorelDRAW round-trip)

The tracer is good but not perfect. To hand-correct a glyph:

1. **Select.** Open the font's `output/handwriting/<font>/preview.html`
   in a browser. Click the glyph cells you want to fix, then
   **Save selection**. It downloads `sel-<font>-<hash>.json`. Move that
   file into the **`selections/`** folder.
2. **Cook.** Cook the graph. `sync_edits` writes
   `corel/sel-<font>-<hash>.csv`.
3. **Edit in CorelDRAW.** Run the **PenstrokeImport** macro
   (`corel/penstroke_corel.bas`) and pick that CSV. Each glyph is a
   page: the original outline as a backdrop, the strokes as named
   objects (`s01`, `s02`, …). The object **name is the draw order** —
   keep it. Fix the strokes.
4. **Export back.** Run **PenstrokeExportEdits** and save into the
   **`corel/`** folder. Same filename is fine; any name containing the
   font's name works too (Corel's default `<doc>_edited.csv` is fine as
   long as the doc kept the font in its name).
5. **Cook again.** One cook imports the edit (re-sampling widths from
   the font ink — you only edit geometry), updates `strokes.json`,
   regenerates the trace folder, and rebuilds the bundle. Done.

Your exact Bézier handles are preserved: the export macro writes the
control points (not a re-sampled polyline), they are stored per stroke
in `strokes.json`, and the `strokes_bezier` rep uses them verbatim — so
the curve in Houdini is the one you drew in Corel. (The macro constant
`EXPORT_BEZIER` toggles this; the polyline rep is still a dense
re-sample for faithful width.)

Notes:
- Only the glyphs in the CSV are exchanged; everything else is kept.
- Imports are idempotent: a merged CSV gets an `.imported.json` marker
  and won't be re-imported until you save a newer version.
- No command line at any step — it's all cooking + Corel + a file move.

---

## Using hfonts in a scene

### Lay out text

Drop **`penstroke::text_layout`**, point **Hfonts Folder** at a folder
of `.hfont` bundles (e.g. the TOPs output), optionally narrow by
**Type** (Sans Serif / Serif / Display / Handwriting / Monospace — uses
the cook's `index.json`), pick the bundle from the **Font** dropdown
(open it and type a letter to jump), pick the **Rep**, and type into
**Text**. Turn on **Build Ribbon** to output the strokes as filled
variable-width ribbon surfaces (the calligraphic stroke, built from the
centerline + per-point width) instead of bare curves — use a strokes /
strokes_bezier rep. With **Assemble Glyphs** on (default)
the node outputs the laid-out text directly (it does the Copy to Points
internally). Other parms: **Font Size**, **Wrap** + **Wrap Width**,
**Align** (Left/Center/Right/Justify), **Tracking (em)**, **Line Height
(em)**.

With **Assemble Glyphs** off it outputs one point per glyph in writing
order instead, with these attributes (for your own Copy to Points):

| Attribute | Meaning |
|---|---|
| `P` | glyph origin on the baseline |
| `name` (string) | glyph key — the Copy to Points piece attribute |
| `pscale` | font size (em scale) |
| `line`, `word`, `cluster` | line number, word index, source char index |
| `charinword` | letter index within its word (0-based) |
| `idx` | running glyph index in writing order (0-based) |

### Place the glyph geometry

Load the bundle's rep geometry with a **File** SOP, then **Copy to
Points** with **Piece Attribute** = `name`:

- Strokes rep (raw): `reps/strokes/glyphs.bgeo.sc` — dense polyline,
  ~240 points/stroke, faithful width profile.
- Strokes rep (reduced): `reps/strokes_bezier/glyphs.bgeo.sc` — order-4
  Bézier curves (~20× fewer control points). Hand-edited glyphs use
  your exact Corel handles; the rest are fitted (Schneider, same as the
  Corel export). A B-spline-style curve you **tessellate on demand**
  with a Resample or Convert SOP — set the density you want at use time
  instead of carrying 240 points everywhere. Carries `width`/`u` on the
  CVs, so the swept ribbon still works after resampling.
- Outline rep: `reps/outline/glyphs.bgeo.sc` — the font's exact cubic
  Bézier outlines (closed curves), winding-normalized: outer contours
  CW, holes (counters) CCW, with an `is_hole` prim attribute. Clean
  separate contours — no bridge/keyhole seam like the Font SOP. To get
  a filled surface with real holes, **Boolean** the outer against the
  holes (or convert + a hole-aware fill); the consistent winding makes
  that predictable.

All are packed, one prim per glyph, keyed by `name` (the TTF post
glyph name: `A`, `eacute`, …). The polyline `strokes` rep is the
bundle's `default_rep`; `strokes_bezier` is the reduced sibling for
when you want clean curves.

### Animate / style it (wobble, taper, draw-on)

The bundle stores **clean** geometry; the calligraphic styling is yours
to add in SOPs. The strokes rep carries everything you need:

| Attribute | Class | Use |
|---|---|---|
| `width` | point | ribbon half-width (sweep / `pscale`) |
| `u` | point | 0→1 along the stroke (draw-on trim, taper profile) |
| `stroke_index` | prim | per-stroke ordering within the glyph |
| `arclength` | prim | em length (constant-speed pen timing) |

`handwriting_demo.hip` shows the pattern: two wrangles compute a per-
stroke draw-on time from cumulative `arclength` and trim by `u`. Wobble
and taper would be added the same way — a perpendicular offset driven
by `u`, a width multiplier ramped at the ends — leaving the bundle
geometry untouched.

---

## Folder map

```
output/handwriting/<font>/   trace workshop (strokes.json = source of truth)
  preview.html               select glyphs to fix here
output/hfont_dev/hfonts/     compiled .hfont bundles + index.html
output/hfont_dev/penstroke_tops.hip   ready-made scene (a penstroke::tops instance)
selections/                  drop sel-<font>-<hash>.json here
corel/                       CSVs out + edited CSVs back; penstroke_corel.bas (the macro)
houdini/otls/                penstroke_tops.hda, penstroke_text_layout.hda
```

`selections/` and `corel/` working files are gitignored; the Corel
macro and the HDAs are tracked.

---

## Troubleshooting

- **`import penstroke` fails in a SOP / NodeError about penstroke** —
  the package isn't installed. Run
  `hython scripts/install_houdini_package.py` and restart Houdini.
- **A trace job fails with "SRE module mismatch"** — a PDG job inherited
  Houdini's `PYTHONPATH`. The launchers `scripts/run_trace.cmd` and
  `scripts/run_penstroke.cmd` scrub it; make sure the trace stage uses
  them (it does by default).
- **A dropped selection isn't picked up** — check the JSON's `"font"`
  field matches the font (matching ignores case/spaces/punctuation), and
  that the file is valid (a UTF-8 BOM from Notepad is tolerated).
- **An edited CSV isn't merged** — it must be *newer* than the CSV the
  pipeline wrote (Corel re-saving bumps the mtime, which flips it from
  "pristine export" to "pending return"), and its name must contain the
  font's name. Files with no font in the name and no sidecar are
  ignored — use the per-font `output/handwriting/<font>/edits/` folder
  for those, or `penstroke import-corel` directly.
- **Re-cooking re-traces everything** — it shouldn't: tracing is skipped
  when `metadata.json` exists. If it re-traces, that file is missing or
  the trace folder name doesn't match the family.
- **Changed the graph code but instances look old** — re-run
  `hython scripts/build_tops_graph.py --make-hda`; scenes pick up the
  new definition on reload.
```
