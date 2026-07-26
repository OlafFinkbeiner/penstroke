"""Build a demo .hip showing an hfont in action (run under hython).

    hython scripts/hfont_demo.py <bundle.hfont> <out.hip> [--text ...]

Network: a Python SOP shapes the text with HarfBuzz against the
bundle's TTF (kerning live from the font, liga/calt off) and emits one
point per glyph with `name` + `pscale`; Copy to Points (id attribute
`name`) instantiates the bundle's packed outline glyphs. This is the
phase-2 idiom as a self-contained teaser — the real layout engine
(line breaking, justification) replaces the inline shaping code later.

Also renders the cooked result to a PNG next to the hip (via an
Unpack SOP, so the image shows the actual geometry Houdini produced).
"""

import argparse
import os
import sys

_SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

DEFAULT_TEXT = 'Penstroke\nWave away!'
FLATTEN_SAMPLES_PER_SEG = 12

# Self-contained Python SOP code: stdlib + uharfbuzz only, reads the
# bundle through its manifest. Glyph names come from HarfBuzz
# (post table) and match the hfont `name` keying.
TEXT_POINTS_CODE = '''
import json, os
import uharfbuzz as hb

node = hou.pwd()
geo = node.geometry()
bundle = node.evalParm('hfont')
text = node.evalParm('text')
size = node.evalParm('fontsize')

man = json.load(open(os.path.join(bundle, 'manifest.json'),
                     encoding='utf-8'))
blob = hb.Blob.from_file_path(os.path.join(bundle, man['source']['file']))
face = hb.Face(blob)
font = hb.Font(face)
s = size / face.upem

geo.addAttrib(hou.attribType.Point, 'name', '')
geo.addAttrib(hou.attribType.Point, 'pscale', 1.0)
geo.addAttrib(hou.attribType.Point, 'line', 0)
geo.addAttrib(hou.attribType.Point, 'cluster', 0)

y = 0.0
for li, line in enumerate(text.split(chr(10))):
    buf = hb.Buffer()
    buf.add_str(line)
    buf.guess_segment_properties()
    hb.shape(font, buf, {'liga': False, 'calt': False})
    x = 0.0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        gname = font.glyph_to_string(info.codepoint)
        if gname not in ('space', 'nbspace', 'uni00A0'):
            pt = geo.createPoint()
            pt.setPosition((x + pos.x_offset * s,
                            y + pos.y_offset * s, 0.0))
            pt.setAttribValue('name', gname)
            pt.setAttribValue('pscale', size)
            pt.setAttribValue('line', li)
            pt.setAttribValue('cluster', info.cluster)
        x += pos.x_advance * s
    y -= size * 1.2
'''


def build_network(bundle_dir, text):
    import hou
    obj = hou.node('/obj')
    container = obj.createNode('geo', 'hfont_demo')

    glyphs = container.createNode('file', 'hfont_glyphs')
    glyphs.parm('file').set(os.path.join(
        bundle_dir, 'reps', 'outline', 'glyphs.bgeo.sc'))

    pts = container.createNode('python', 'text_points')
    ptg = pts.parmTemplateGroup()
    ptg.append(hou.StringParmTemplate(
        'hfont', 'Hfont Bundle', 1,
        string_type=hou.stringParmType.FileReference))
    ptg.append(hou.StringParmTemplate('text', 'Text', 1))
    ptg.append(hou.FloatParmTemplate('fontsize', 'Font Size', 1,
                                     default_value=(1.0,)))
    pts.setParmTemplateGroup(ptg)
    pts.parm('python').set(TEXT_POINTS_CODE)
    pts.parm('hfont').set(bundle_dir)
    pts.parm('text').set(text)

    copy = container.createNode('copytopoints::2.0', 'copy_glyphs')
    copy.setInput(0, glyphs)
    copy.setInput(1, pts)
    copy.parm('useidattrib').set(True)
    copy.parm('idattrib').set('name')

    unpack = container.createNode('unpack', 'unpack_for_render')
    unpack.setInput(0, copy)

    copy.setDisplayFlag(True)
    copy.setRenderFlag(True)
    container.layoutChildren()
    return copy, unpack


def render_png(unpack_node, png_path, width=1600, margin=60):
    """Draw the cooked, unpacked bezier curves with PIL."""
    from PIL import Image, ImageDraw
    geo = unpack_node.geometry()
    polys = []
    for prim in geo.prims():
        pts = [p.position() for p in prim.points()]
        if len(pts) % 3 != 0:
            continue
        poly = []
        n = len(pts)
        for seg in range(n // 3):
            p0 = pts[3 * seg]
            c1 = pts[3 * seg + 1]
            c2 = pts[3 * seg + 2]
            p3 = pts[(3 * seg + 3) % n]
            for i in range(FLATTEN_SAMPLES_PER_SEG):
                t = i / FLATTEN_SAMPLES_PER_SEG
                mt = 1.0 - t
                poly.append((
                    mt**3 * p0[0] + 3*mt*mt*t * c1[0]
                    + 3*mt*t*t * c2[0] + t**3 * p3[0],
                    mt**3 * p0[1] + 3*mt*mt*t * c1[1]
                    + 3*mt*t*t * c2[1] + t**3 * p3[1]))
        polys.append(poly)
    if not polys:
        raise RuntimeError('no curves came out of the unpack — '
                           'copy/id matching failed?')
    xs = [x for poly in polys for (x, _y) in poly]
    ys = [y for poly in polys for (_x, y) in poly]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    s = (width - 2 * margin) / max(x1 - x0, 1e-9)
    height = int((y1 - y0) * s) + 2 * margin
    img = Image.new('RGB', (width, height), 'white')
    draw = ImageDraw.Draw(img)
    for poly in polys:
        pix = [(margin + (x - x0) * s, height - margin - (y - y0) * s)
               for (x, y) in poly]
        draw.line(pix + pix[:1], fill='black', width=2)
    img.save(png_path)
    return png_path


def main(argv=None):
    import hou
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('bundle', help='hfont bundle directory')
    ap.add_argument('hip', help='output .hip path')
    ap.add_argument('--text', default=DEFAULT_TEXT)
    args = ap.parse_args(argv)

    bundle = os.path.abspath(args.bundle)
    copy, unpack = build_network(bundle, args.text)
    n = len(copy.geometry().prims())
    print(f'copy_glyphs cooked: {n} packed glyph instances')

    os.makedirs(os.path.dirname(os.path.abspath(args.hip)), exist_ok=True)
    hou.hipFile.save(args.hip)
    print(f'saved {args.hip}')

    png = os.path.splitext(args.hip)[0] + '.png'
    render_png(unpack, png)
    print(f'rendered {png}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
