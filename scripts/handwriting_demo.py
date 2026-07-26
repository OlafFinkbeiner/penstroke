"""Handwriting draw-on demo from an hfont strokes rep (run under hython).

    hython scripts/handwriting_demo.py <bundle.hfont> <out.hip> [--text ...]

Network: text_layout HDA -> Copy to Points (strokes rep, id attribute
`name`) -> Unpack -> two wrangles that do the entire animation:

  compute_timing (detail): pen progresses at constant speed over the
      total drawn length — each stroke prim gets [t0, t1] from the
      cumulative arclength. Prim order IS writing order (layout points
      are emitted in writing order, strokes within a glyph carry
      stroke_index order from the tracer).
  draw_on (points): a Progress parameter (0..1) removes the part of
      each stroke the pen hasn't reached, via the per-point `u`.

Timing lives entirely in these two wrangles — the hfont stays purely
geometric. Renders a flipbook strip PNG from the cooked geometry at
several Progress values as proof.
"""

import argparse
import os
import sys

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(_REPO, 'src'))

from penstroke import hfont

HDA_PATH = os.path.join(_REPO, 'houdini', 'otls',
                        'penstroke_text_layout.hda')
DEFAULT_TEXT = 'hello world'
FLIPBOOK_STEPS = (0.2, 0.4, 0.6, 0.8, 1.0)

TIMING_VEX = '''
int n = nprimitives(0);
float total = 0;
for (int i = 0; i < n; i++)
    total += prim(0, 'arclength', i);
float t = 0;
for (int i = 0; i < n; i++) {
    float L = prim(0, 'arclength', i);
    setprimattrib(0, 't0', i, t / max(total, 1e-9));
    setprimattrib(0, 't1', i, (t + L) / max(total, 1e-9));
    t += L;
}
'''

DRAW_ON_VEX = '''
float progress = chf('progress');
int pr = pointprims(0, @ptnum)[0];
float t0 = prim(0, 't0', pr);
float t1 = prim(0, 't1', pr);
float local = t1 > t0 ? (progress - t0) / (t1 - t0) : 1.0;
if (f@u > local)
    removepoint(0, @ptnum);
'''


def build_network(bundle_dir, text):
    import hou
    hou.hda.installFile(HDA_PATH)
    hf = hfont.HFont(bundle_dir)

    demo = hou.node('/obj').createNode('geo', 'handwriting_demo')
    glyphs = demo.createNode('file', 'strokes_glyphs')
    glyphs.parm('file').set(hf.rep_geo_path('strokes'))

    txt = demo.createNode('penstroke::text_layout', 'text_layout')
    txt.parm('hfont').set(bundle_dir)
    txt.parm('text').set(text)

    copy = demo.createNode('copytopoints::2.0', 'copy_glyphs')
    copy.setInput(0, glyphs)
    copy.setInput(1, txt)
    copy.parm('useidattrib').set(True)
    copy.parm('idattrib').set('name')

    unpack = demo.createNode('unpack', 'unpack_strokes')
    unpack.setInput(0, copy)

    timing = demo.createNode('attribwrangle', 'compute_timing')
    timing.setInput(0, unpack)
    timing.parm('class').set('detail')
    timing.parm('snippet').set(TIMING_VEX.strip())

    draw = demo.createNode('attribwrangle', 'draw_on')
    draw.setInput(0, timing)
    draw.parm('class').set('point')
    draw.parm('snippet').set(DRAW_ON_VEX.strip())
    ptg = draw.parmTemplateGroup()
    ptg.append(hou.FloatParmTemplate('progress', 'Progress', 1,
                                     default_value=(1.0,), min=0.0,
                                     max=1.0))
    draw.setParmTemplateGroup(ptg)

    draw.setDisplayFlag(True)
    draw.setRenderFlag(True)
    demo.layoutChildren()
    return draw


def render_flipbook(draw_node, png_path, steps=FLIPBOOK_STEPS,
                    px_per_em=150, margin=40):
    """Stacked strip: one row per Progress value, line width = stroke width."""
    from PIL import Image, ImageDraw

    # Cook the final frame first to size the canvas.
    draw_node.parm('progress').set(1.0)
    geo = draw_node.geometry()
    xs, ys = [], []
    for pt in geo.points():
        p = pt.position()
        xs.append(p[0])
        ys.append(p[1])
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    row_w = int((x1 - x0) * px_per_em) + 2 * margin
    row_h = int((y1 - y0) * px_per_em) + 2 * margin

    img = Image.new('RGB', (row_w, row_h * len(steps)), 'white')
    d = ImageDraw.Draw(img)
    for row, progress in enumerate(steps):
        draw_node.parm('progress').set(progress)
        geo = draw_node.geometry()
        oy = row * row_h
        d.line([(0, oy), (row_w, oy)], fill='#dddddd', width=1)
        for prim in geo.prims():
            pts = prim.points()
            for i in range(len(pts) - 1):
                a = pts[i].position()
                b = pts[i + 1].position()
                w = (pts[i].attribValue('width')
                     + pts[i + 1].attribValue('width')) * 0.5
                d.line(
                    [(margin + (a[0] - x0) * px_per_em,
                      oy + margin + (y1 - a[1]) * px_per_em),
                     (margin + (b[0] - x0) * px_per_em,
                      oy + margin + (y1 - b[1]) * px_per_em)],
                    fill='black',
                    width=max(1, int(w * px_per_em)), joint='curve')
    img.save(png_path)
    return png_path


def main(argv=None):
    import hou
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('bundle')
    ap.add_argument('hip')
    ap.add_argument('--text', default=DEFAULT_TEXT)
    args = ap.parse_args(argv)

    draw = build_network(os.path.abspath(args.bundle), args.text)
    n = len(draw.geometry().prims())
    print(f'draw_on cooked: {n} stroke prims at progress=1')

    os.makedirs(os.path.dirname(os.path.abspath(args.hip)), exist_ok=True)
    hou.hipFile.save(args.hip)
    png = os.path.splitext(args.hip)[0] + '.png'
    render_flipbook(draw, png)
    print(f'saved {args.hip}')
    print(f'rendered {png}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
