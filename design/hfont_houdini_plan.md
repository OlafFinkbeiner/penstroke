# Hfont + Houdini TOPs — implementation plan

Status: agreed concept, not yet started.
Scope: move the penstroke workflow into Houdini TOPs and replace the
Font-SOP-based hfont hack with a real "hfont standard" — a bundle of
interchangeable glyph representations over a single source TTF, driven
by a Python text-layout SOP.

## Decisions already made (rationale in session history)

| Decision | Choice |
|---|---|
| Shaping library | `uharfbuzz` (HarfBuzz official binding) + `fontTools` (already a dep). FreeType rejected (no GPOS kerning), Pango/Skia rejected (weight), Qt rejected (headless), HDK deferred (compile-per-version tax; Python glue is not the bottleneck). |
| Line breaking / justification | Our own: greedy breaker v1, Knuth–Plass later. Justify = distribute slack across word gaps. |
| Glyph keying | TTF `post` glyph names (`A`, `eacute`, `f_i`) on a string `name` attribute. Covers ligature/alternate glyphs with no codepoint → v2 shaping upgrade needs no format change. |
| Coordinate space | All rep geometry baked to em space at build time: origin = glyph origin, baseline at y=0, 1 unit = 1 em. Layout emits `pscale = font_size`. |
| Bundle | Folder `<family>.hfont/` containing `manifest.json` (source of truth — nothing globs `reps/`), the untouched `font.ttf`, `LICENSE.txt`, and `reps/<kind>/glyphs.bgeo.sc`. Layout reads metrics/kerning live from the TTF — no extraction step. |
| Ligatures | v1 shapes with `liga`/`calt` OFF (1:1 char↔glyph, matches per-char tracing). v2 keys traces by glyph name and turns features on. |
| TOPs granularity | One work item per font. Per-glyph items only for the vision-QA fan-out. |
| Manual steps in TOPs | File handshakes: preview selection saves `selections/*.json`; Corel exports land in `edits/*.csv`; File Pattern TOPs pick them up on recook. No blocking nodes. |
| Environments | penstroke stays in its own venv, invoked as CLI by out-of-process TOPs. Only rep builders + layout SOP run in hython (`hython -m pip install uharfbuzz` once per Houdini install). |
| Performance plan | Cache hb.Face/Font by (path, mtime); memoize shaped words; write geometry via batch numpy APIs (one call per attribute, never per point). Layout core is pure Python module, `hou`-free, benchmarkable standalone. |

## Manifest schema v1 (draft)

```json
{
  "hfont": 1,
  "family": "Caveat",
  "source": {"file": "font.ttf", "upm": 1000, "license": "OFL"},
  "glyph_keys": "post-names",
  "reps": {
    "strokes": {
      "kind": "centerline",
      "geo": "reps/strokes/glyphs.bgeo.sc",
      "attributes": {"point": ["width", "u"], "prim": ["stroke_index", "arclength"]},
      "provenance": {"tracer": "penstroke 0.2", "edit_rounds": 5, "charset": "latin"}
    },
    "outline": {
      "kind": "curves2d",
      "geo": "reps/outline/glyphs.bgeo.sc",
      "attributes": {"prim": ["contour_index", "is_hole"]}
    }
  },
  "default_rep": "strokes"
}
```

Rep entries declare `kind` + the attribute contract they promise; new
kinds (extrude, sdf, points, animated/coloured) extend the manifest
without touching existing consumers. Rep revisions are siblings
(content-hash suffix), manifest points at current — never overwrite.

## Layout SOP output contract

One point per glyph; point order = writing order.

| attribute | meaning |
|---|---|
| `P` | glyph origin on the baseline |
| `glyph` (string) | piece-attribute key for Copy to Points |
| `pscale` | font size (em scale) |
| `line`, `word`, `cluster` | line number, word index, source char index |

Downstream idiom: Copy to Points, piece attribute `glyph`, source =
chosen rep's packed prims.

## Phases

### Phase 1 — hfont standard core (no penstroke dependency) — DONE
- [x] Environment pinned: Houdini 21.0.729 (hython Python 3.11.7),
      `uharfbuzz 0.55.0` (HarfBuzz 14.2.1) via
      `hython -m pip install --user uharfbuzz`. GOTCHA: Houdini ships
      a STRIPPED fontTools (4.55.4, head/name table parsers removed —
      symptom: DefaultTable AttributeError). Full fontTools must be
      installed with `hython -m pip install --user --ignore-installed
      fonttools`; the user site precedes Houdini's site-packages.
      Houdini bundles numpy 1.26.4 and PIL 9.0.1. (Benchmark gate
      dropped: HarfBuzz throughput not in genuine doubt; `layout.py`
      stays benchmarkable standalone on demand.)
- [x] `src/penstroke/hfont.py`: create/open/validate bundle, manifest,
      register_rep, char↔glyph-name mapping. The ONE implementation of
      the format. (`charset.py` split out of editround so hython can
      import presets without scipy/skimage.)
- [x] `src/penstroke/houdini/rep_outline.py` (hython): TTF beziers →
      em-space curves2d rep via fontTools Qu2CuPen (all-cubic), closed
      order-4 Bezier prim per contour, packed per glyph via
      `hou.Geometry.createPackedGeometry` (pivot = glyph origin),
      `name` = post glyph name. is_hole = containment parity AND
      opposite winding to the glyph's dominant contour (containment
      alone misfires on overlapping contours — Caveat's K/Y/y).
- [x] Bundle finalize folded into create_bundle: copies TTF + license
      (auto-detects OFL.txt/LICENSE* next to the source TTF).
- [x] QA contact sheet (`qa/outline.png`, holes drawn red) — verified
      on Caveat: 135 glyphs, baseline y=0, descenders negative,
      counters flagged, em-space bboxes correct.
- Acceptance: a valid `.hfont` for one OFL font, loadable via
  `hfont.py`, glyphs visible in Houdini.

### Phase 2 — layout engine + text SOP — DONE
- [x] `src/penstroke/layout.py`: pure module, no `hou`. HarfBuzz
      shaping (liga/calt off), greedy breaker, left/center/right/
      justify, tracking, line height → flat numpy arrays +
      line/word/cluster. Fonts cached by (path, mtime), shaped words
      memoized. Tests: tests/test_layout.py (9 checks incl. justify
      fills measure exactly, cluster indices, caching identity).
- [x] Standalone preview renderer: scripts/layout_preview.py (PNG via
      PIL, measure guides drawn — justification visually verified on
      a 4-line Caveat paragraph).
- [x] `penstroke::text_layout` HDA at
      houdini/otls/penstroke_text_layout.hda, built by
      scripts/build_text_layout_hda.py. Python SOP shim reading HDA
      parms (hfont bundle, multiline text, size, wrap toggle+width,
      align menu, tracking, line height); batch attribute writes.
      Engine import requires `hython -m pip install --user --no-deps
      -e .` (done on this machine; proper Houdini package in phase 4).
- [x] Demo scenes: output/hfont_dev/hfont_demo.hip (inline shaping
      teaser) and hfont_demo_hda.hip (the real HDA + Copy to Points,
      justified two-paragraph text from packed Caveat glyphs).
- Acceptance met: text parm relayouts interactively; HDA output
  matches the standalone preview renderer.

### Phase 3 — strokes rep (penstroke integration) — DONE
- [x] `src/penstroke/houdini/rep_strokes.py`: stroke store
      (strokes.json) → em-space centerline rep. Canvas→em transform
      reconstructed from the bundle TTF with rasterize.py's formula
      (1 em = trace `size` px, origin (pad, baseline_y), y down) —
      verified against the outline rep's bboxes before building.
      Per-point `width` + `u`, prim `stroke_index` + `arclength`.
      Registered as default_rep. QA sheet qa/strokes.png (stroke-order
      rainbow). Caveat: all 188 hand-edited glyphs converted from
      fonts/caveat/strokes.json (the canonical 5-round store).
- [x] Handwriting demo: scripts/handwriting_demo.py →
      output/hfont_dev/handwriting_demo.hip. Two wrangles only:
      compute_timing (detail VEX, constant pen speed from cumulative
      arclength → per-prim t0/t1) and draw_on (point VEX, Progress
      parm trims via `u`). Timing fully art-directable in SOPs; hfont
      stays geometric. Flipbook strip rendered from cooked geometry.
- Acceptance met: "hello world" writes on progressively in the
  hand-edited Caveat strokes with variable width.

### Phase 4 — TOPs graph (MVP DONE; round-trip + wedging open)
- [x] `src/penstroke/fontscan.py`: recursive discovery over three
      source layouts (Google Fonts METADATA.pb family dirs, penstroke
      trace outputs — existing stroke store attached, plain TTF
      folders). Filters: family regex, category, dedupe, limit.
      CLI: `python -m penstroke.fontscan`.
- [x] TOPs graph (scripts/build_tops_graph.py → penstroke_tops.hip):
      font_scan (Python Processor, in-process fontscan.scan, one item
      per font) → trace_missing (Generic Generator, out-of-process
      venv `penstroke trace` per item; expected output = `store`
      attribute + Automatic cache mode, so already-traced fonts skip
      the command entirely) → build_bundle (Python Script in-process:
      outline rep always, strokes rep when a store exists, mtime
      early-exit) → waitforall → make_index (index.html over all
      bundles with QA sheets). Warm full-graph re-cook ≈ 7 s incl.
      Houdini startup. Validated: 8-font cook, then full 81-bundle
      corpus from output/handwriting.
- [x] GUI scene for the full Google Fonts run: all config (roots,
      filters, charset, output dirs) is spare parms on
      /obj/penstroke_tops; the saved penstroke_tops.hip is
      self-contained (cold-load verified: 355 HANDWRITING work items,
      92 cached, 263 to trace from the D:\google-fonts sparse clone).
      Trace stage hardened along the way: commands BAKED per item
      (this node expands no @attrib tokens, verified empirically),
      run_trace.cmd scrubs PYTHONPATH/PYTHONHOME (PDG jobs inherit
      Houdini's → SRE module mismatch in the venv), --name passed so
      trace identity = folder basename (no duplicate bundles from
      TTF-stem names), embedded callbacks import hou explicitly
      (absent in cold-loaded generate callbacks).
- [x] Preview selection tool + file-handshake round-trip stages.
      preview.html "Save selection" downloads `sel-<font>-<hash>.json`
      (copy-command stays as fallback) for the GLOBAL selections inbox
      `<repo>/selections/` (one drop folder for all fonts, routed per
      font via the JSON's "font" field, normalized matching; the
      `selectionsroot` parm / `sync-edits --inbox` /
      `$PENSTROKE_SELECTIONS`). The Corel exchange is ONE global
      folder too (`<repo>/corel/`, `corelroot` parm / `--corel` /
      `$PENSTROKE_COREL`): sync writes `sel-<font>-<hash>.csv` (+
      `.outgoing.json` sidecar) there; Corel exports back ONTO THE
      SAME FILE (or any `sel-<font>-*` name) and the mtime-vs-sidecar
      check flips it to a pending return — nothing is moved or
      deleted. Per-font `<trace_dir>/selections/` and `edits/` still
      work as fallbacks (e.g. arbitrarily named Corel exports).
      Conventions + pending checks live in `penstroke/handshake.py`
      (dependency-light, hython-importable); `penstroke sync-edits`
      merges pending `edits/*.csv` (Corel exports; `.imported.json`
      markers make it idempotent), then writes `subsets/*_edit.csv`
      for pending selections. make_subset/merge_edits collapsed into
      ONE `sync_edits` TOPs stage (trace → sync_edits → build_bundle):
      the command is baked only for fonts with pending files, and a
      merged edit touches strokes.json so build_bundle's mtime check
      rebuilds the strokes rep in the same cook. Validated end-to-end
      in TOPs (selection drop → recook → subset CSV; idempotent) and
      in tests/test_smoke.py (full no-Corel round-trip: selection →
      subset → edits drop → merge → marker). Launcher:
      scripts/run_penstroke.cmd (generic env-scrubbing sibling of
      run_trace.cmd). penstroke_tops.hip rebuilt with the new stage,
      same GUI config.
- [x] Reduced bezier strokes rep (`strokes_bezier`). The dense 240-pt
      polyline is RAW data (the source of truth — width per point,
      timing); the bundle now ALSO carries the strokes fitted to
      order-4 Bézier curves via penstroke/curvefit.py (Schneider's
      algorithm extracted from editround into a numpy-only,
      hython-importable module — the SAME fit the Corel export uses).
      ~20× fewer control points (Allison: 4665 CVs vs 91464 polyline
      points); width/u carried per CV (handles interpolated, u kept
      monotone for draw-on). A B-spline-style curve Houdini tessellates
      on demand (Resample/Convert) — verified width survives. Built
      for all 355 bundles by build_bundle (own mtime check); NOT the
      default rep. Polyline stays raw/canonical (per-point width,
      timing); the bezier rep is the clean reduced view.
- [x] Handle fidelity: the EXACT hand-edited cubics are preserved, not
      re-fitted. read_edit_csv keeps B-record control points; a
      glyph's `bez` field (optional, per stroke) is merged into
      strokes.json on import (editround.merge_stroke_bez), carried
      across edit rounds, and the strokes_bezier builder uses those
      cubics verbatim when present (else fits). Verified end to end via
      the B-record export path (tests/test_smoke: exact cubics land in
      the store; rep build reports "N glyphs from exact hand-edited
      cubics"). The Corel macro now exports nodes as B records
      (EXPORT_BEZIER, control-point read mirrors the import's
      AppendCurveSegment2) — NEEDS A COREL TEST (no Corel here); S-record
      sampling stays as a one-flag fallback.
- [ ] Wobble/taper as a Houdini SOP layer (NOT a penstroke wedge —
      decided: all font styling happens in Houdini on the strokes rep,
      which carries width/u/arclength for exactly this). An optional
      `penstroke::handwrite` HDA (wobble offset by `u`, taper width
      ramp, draw-on) would package the handwriting_demo pattern.
      Seed/size wedging, if wanted, rides Houdini's wedge TOP.
- [x] Packaged as `penstroke::tops` HDA + Houdini package file.
      `build_tops_graph.py --make-hda` → houdini/otls/
      penstroke_tops.hda (a topnet can't become an HDA directly —
      wrapped in an OBJ subnet carrying the parm interface; embedded
      callbacks find their config by walking ancestors for the parms
      and the repo via the imported penstroke package, so NO absolute
      paths are baked in; parm defaults reference $PENSTROKE).
      `install_houdini_package.py` writes <prefs>/packages/
      penstroke.json ($PENSTROKE, PYTHONPATH += src, HOUDINI_PATH +=
      houdini/ → otls auto-scan), replacing the pip --user editable
      install. Verified in a fresh hython session: tab-create a
      renamed instance, defaults expand, 2-font cook through the HDA;
      plain-topnet hip path re-verified too. GOTCHA: `self` IS
      available in pythonprocessor/pythonscript TOP callbacks and
      self.topNode() returns the hou node (probed empirically).
- Acceptance met (2026-06-12): one real Corel pass through the
  handshake — Allison, 71 glyphs selected in preview.html, exported
  via the inbox, edited in Corel, saved back as the free-form
  'allison-e570ba_edited.csv' (which prompted generalizing return
  routing to filename-token matching), merged + strokes rep rebuilt.

### Phase 5 — later / optional
- v2 glyph-ID pipeline: trace by glyph name, enable liga/calt,
  contextual alternates flow through automatically.
- Knuth–Plass breaker, pyphen hyphenation, RTL via python-bidi.
- More reps: extrude/sdf/points (decide per kind: baked into bundle
  vs derived live from outline rep in SOPs).
- Vision-QA fan-out as per-glyph TOPs stage (open work item #5).

## Build order rationale

Outline rep before strokes rep: it exercises the entire standard
(keying, em space, manifest, packed prims, Copy to Points idiom) with
zero penstroke coupling, so the layout SOP develops against a proven
contract and the strokes rep is just a second conforming producer.
The expensive trace + hand-edit loop becomes the premium rep added to
fonts that earn it.

## Open items

- Houdini version (affects bundled Python; H20.x = 3.10/3.11) — pin
  at the start of phase 1.
- Does Corel remain the editor long-term, or does hand-editing move
  into Houdini SOPs? CSV supports both; revisit after phase 4.
- Single-file hfont export (TTF bytes embedded as detail attrib) —
  optional later convenience, folder bundle is the default.
