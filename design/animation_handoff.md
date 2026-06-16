# Animation — handoff

Factual starting point for animating penstroke output: what data is
available and how the existing draw-on works. No proposed effects here —
just the lay of the land.

## Two output surfaces

- **SVG / web** — per-glyph SVG + the interactive `preview.html` viewer.
- **Houdini** — the hfont reps (em-space `.bgeo.sc`) consumed through the
  `penstroke::text_layout` HDA.

## The animation handles (attributes)

### On the stroke geometry — `strokes` and `strokes_bezier` reps

| attr | class | meaning |
|---|---|---|
| `u` | point | 0→1 along the stroke (draw-on trim, taper profile) |
| `width` | point | stroke width at that point (ribbon half-width; drives the sweep via `pscale`) |
| `stroke_index` | prim | stroke order *within the glyph* (from the tracer) |
| `arclength` | prim | stroke length in em (constant-speed pen timing) |
| `name` | prim | glyph key (TTF post name; the Copy to Points piece id) |

`strokes` = dense polyline (~240 pts/stroke, faithful width).
`strokes_bezier` = reduced order-4 Bézier carrying `width`/`u` on the
CVs — tessellate on demand with a Resample/Convert SOP.

### On the laid-out text — `text_layout` HDA points

These are emitted one per glyph in **writing order**, and (with Assemble
Glyphs / Copy to Points) are forwarded onto the assembled stroke/ribbon
geometry too:

| attr | meaning |
|---|---|
| `idx` | running glyph index in writing order (0-based) |
| `word` | word index |
| `line` | line index |
| `charinword` | letter index within its own word (0-based) |
| `cluster` | source char index in the input string |
| `name` | glyph key; `pscale` | em size |

## How draw-on works today

### Houdini — `scripts/handwriting_demo.py` → `output/hfont_dev/handwriting_demo.hip`

Network: `text_layout` → Copy to Points (id attribute `name`) → Unpack →
two wrangles that do the entire animation:

- **compute_timing** (detail wrangle): walks the stroke prims in writing
  order and gives each `[t0, t1]` = cumulative `arclength` / total. Prim
  order *is* writing order — layout points come out in writing order, and
  strokes within a glyph carry `stroke_index` order from the tracer.
- **draw_on** (point wrangle, with a `progress` 0..1 parm): per point
  `local = (progress - t0) / (t1 - t0)`; `removepoint` if `u > local`. So
  the pen advances at constant speed and strokes appear in writing order.

The hfont geometry stays purely geometric — all timing lives in these two
wrangles. The script renders a flipbook strip PNG at several `progress`
values as proof.

### SVG — `preview.html`

Two paths per stroke: an animated centerline (stroke-dasharray draw-in,
hidden until its start time to avoid round-linecap dots) plus a filled
ribbon that fades in behind it. Wireframe mode hides the ribbon. The
viewer has play / pause / speed / scrub / wireframe, plus a width-band
view (the raw per-point width band before it goes onto Houdini points).

## The calligraphic ribbon (Houdini)

`text_layout` "Build Ribbon" sweeps the centerline into a flat
variable-width ribbon: per-point `width` → `pscale`, sweep `scale` 0.5,
up vector +Z. Off = bare curves. So either the centerline or the filled
ribbon can be the thing that draws on.

## Files

- `scripts/handwriting_demo.py` — the draw-on demo (the two wrangles)
- `output/hfont_dev/handwriting_demo.hip` — built demo scene
- `scripts/build_text_layout_hda.py` — text_layout HDA (layout, the
  attributes above, the ribbon branch)
- `src/penstroke/render/viewer_template.html` — the SVG viewer
- `src/penstroke/render/{svg,glyph,alphabet,diagnostic}.py` — SVG stroke
  & ribbon path building; `diagnostic.py` also emits a flipbook strip of
  animation frames
- `src/penstroke/core/smoothing.py` — spline fit, per-point widths,
  OU-process wobble, taper
- `docs/houdini_workflow.md` — "Animate / style it" section (attribute
  table + the demo pattern)
