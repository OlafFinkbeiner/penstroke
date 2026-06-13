"""Build the penstroke::text_layout HDA (run under hython).

    hython scripts/build_text_layout_hda.py

Writes houdini/otls/penstroke_text_layout.hda. The HDA is a ~50-line
Python SOP shim around penstroke.layout (the engine stays in the
package — `hython -m pip install --user --no-deps -e .` makes it
importable; the shim raises a clear error if it isn't). Output is one
point per glyph with `name`, `pscale`, `line`, `word`, `cluster`,
written with batch APIs. Downstream: Copy to Points, id attribute
`name`, source = any hfont rep.

Also rebuilds the demo hip (output/hfont_dev/hfont_demo_hda.hip) using
the HDA + the Caveat bundle, and renders a PNG proof from the cooked
geometry.
"""

import os
import sys

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

HDA_PATH = os.path.join(_REPO, 'houdini', 'otls',
                        'penstroke_text_layout.hda')
HDA_NAME = 'penstroke::text_layout'
DEMO_HIP = os.path.join(_REPO, 'output', 'hfont_dev', 'hfont_demo_hda.hip')
DEMO_BUNDLE = os.path.join(_REPO, 'output', 'hfont_dev', 'caveat.hfont')
DEMO_TEXT = ('the quick brown fox jumps over\n'
             'the lazy dog and waves away')

SHIM_CODE = '''
try:
    from penstroke import hfont as _hfont
    from penstroke.layout import layout as _layout
except ImportError:
    raise hou.NodeError(
        'penstroke not importable in Houdini python. Run: '
        'hython -m pip install --user --no-deps -e <penstroke repo>')

node = hou.pwd()
hda = node.parent()
geo = node.geometry()

bundle = hda.evalParm('hfont')
text = hda.evalParm('text')
size = hda.evalParm('fontsize')
use_width = hda.evalParm('usewidth')
width = hda.evalParm('width') if use_width else None
align = hda.parm('align').evalAsString()
tracking = hda.evalParm('tracking')
line_height = hda.evalParm('lineheight')

if not bundle or not text:
    raise hou.NodeError('set Hfont Bundle and Text')
try:
    hf = _hfont.HFont(bundle)
except Exception as e:
    raise hou.NodeError(str(e))

out = _layout(text, hf.font_path, size=size, width=width, align=align,
              tracking=tracking, line_height=line_height)

n = len(out.names)
if n:
    import numpy as np
    p = np.zeros((n, 3))
    p[:, :2] = out.positions
    geo.createPoints([hou.Vector3(*row) for row in p])
    geo.addAttrib(hou.attribType.Point, 'name', '', create_local_variable=False)
    geo.setPointStringAttribValues('name', tuple(out.names))
    geo.addAttrib(hou.attribType.Point, 'pscale', 1.0, create_local_variable=False)
    geo.setPointFloatAttribValues('pscale', [out.size] * n)
    for attr, vals in (('line', out.line), ('word', out.word),
                       ('cluster', out.cluster)):
        geo.addAttrib(hou.attribType.Point, attr, 0, create_local_variable=False)
        geo.setPointIntAttribValues(attr, [int(v) for v in vals])
'''


# Node help card (Houdini help markup), shown in the Help pane.
HDA_HELP = '''= Penstroke Text Layout =

"""Lay text out in an hfont bundle: one point per glyph, ready for
Copy to Points."""

Shapes the __Text__ with HarfBuzz against the chosen `.hfont` bundle
and outputs one point per glyph in writing order. Feed that into a
Copy to Points (Piece Attribute = `name`) with the bundle's glyph
geometry as the source to assemble the text.

Output point attributes: `P` (glyph origin on the baseline), `name`
(glyph key for Copy to Points), `pscale` (= font size), and `line`,
`word`, `cluster` (line number, word index, source char index).

Load glyph geometry from the bundle's `reps/strokes/glyphs.bgeo.sc`
(hand-drawn strokes) or `reps/outline/glyphs.bgeo.sc` (filled
outlines). See `docs/houdini_workflow.md` for the full guide.

@parameters

Hfont Bundle:
    Path to a `.hfont` bundle folder (built by the Penstroke TOPs
    node).

Text:
    The text to lay out. Newlines start new lines.

Font Size:
    Em scale, written to `pscale` for each glyph.

Wrap / Wrap Width:
    Enable word-wrapping at the given width (em units).

Align:
    Left, Center, Right, or Justify (justify distributes slack across
    word gaps; the last line stays left).

Tracking (em):
    Extra space added between glyphs, in em units.

Line Height (em):
    Baseline-to-baseline distance, in em units.
'''


def hda_parm_templates():
    import hou
    return [
        hou.StringParmTemplate(
            'hfont', 'Hfont Bundle', 1,
            string_type=hou.stringParmType.FileReference,
            help='Path to a .hfont bundle folder.'),
        hou.StringParmTemplate(
            'text', 'Text', 1, default_value=('hello world',),
            tags={'editor': '1', 'editorlines': '5'},
            help='Text to lay out; newlines start new lines.'),
        hou.FloatParmTemplate('fontsize', 'Font Size', 1,
                              default_value=(1.0,), min=0.0, max=10.0,
                              help='Em scale; written to pscale.'),
        hou.ToggleParmTemplate('usewidth', 'Wrap', default_value=False,
                               help='Word-wrap at Wrap Width.'),
        hou.FloatParmTemplate('width', 'Wrap Width', 1,
                              default_value=(10.0,), min=0.0, max=100.0,
                              disable_when='{ usewidth == 0 }',
                              help='Wrap measure in em units.'),
        hou.MenuParmTemplate('align', 'Align',
                             ('left', 'center', 'right', 'justify'),
                             menu_labels=('Left', 'Center', 'Right',
                                          'Justify'),
                             help='Justify spreads slack across word '
                                  'gaps; last line stays left.'),
        hou.FloatParmTemplate('tracking', 'Tracking (em)', 1,
                              default_value=(0.0,), min=-0.2, max=2.0,
                              help='Extra space between glyphs (em).'),
        hou.FloatParmTemplate('lineheight', 'Line Height (em)', 1,
                              default_value=(1.2,), min=0.0, max=5.0,
                              help='Baseline-to-baseline distance (em).'),
    ]


def build_hda():
    import hou
    os.makedirs(os.path.dirname(HDA_PATH), exist_ok=True)
    staging = hou.node('/obj').createNode('geo', 'hda_staging')
    subnet = staging.createNode('subnet', 'text_layout')
    shim = subnet.createNode('python', 'layout_shim')
    shim.parm('python').set(SHIM_CODE)
    out = subnet.createNode('output', 'out')
    out.setInput(0, shim)
    subnet.layoutChildren()

    asset = subnet.createDigitalAsset(
        name=HDA_NAME, hda_file_name=HDA_PATH,
        description='Penstroke Text Layout',
        min_num_inputs=0, max_num_inputs=0)
    definition = asset.type().definition()
    ptg = definition.parmTemplateGroup()
    for pt in hda_parm_templates():
        ptg.append(pt)
    definition.setParmTemplateGroup(ptg)
    definition.addSection('Help', HDA_HELP)   # node "?" help card
    asset.matchCurrentDefinition()
    print(f'wrote {HDA_PATH}')
    return asset


def build_demo(asset_node):
    import hou
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from hfont_demo import render_png

    demo = hou.node('/obj').createNode('geo', 'hfont_hda_demo')
    glyphs = demo.createNode('file', 'hfont_glyphs')
    glyphs.parm('file').set(os.path.join(
        DEMO_BUNDLE, 'reps', 'outline', 'glyphs.bgeo.sc'))

    txt = demo.createNode(HDA_NAME, 'text_layout')
    txt.parm('hfont').set(DEMO_BUNDLE)
    txt.parm('text').set(DEMO_TEXT.replace('\\n', '\n'))
    txt.parm('usewidth').set(True)
    txt.parm('width').set(9.0)
    txt.parm('align').set('justify')

    copy = demo.createNode('copytopoints::2.0', 'copy_glyphs')
    copy.setInput(0, glyphs)
    copy.setInput(1, txt)
    copy.parm('useidattrib').set(True)
    copy.parm('idattrib').set('name')

    unpack = demo.createNode('unpack', 'unpack_for_render')
    unpack.setInput(0, copy)
    copy.setDisplayFlag(True)
    copy.setRenderFlag(True)
    demo.layoutChildren()

    n_pts = len(txt.geometry().points())
    n_copies = len(copy.geometry().prims())
    print(f'HDA cooked: {n_pts} layout points, {n_copies} glyph instances')

    # Remove the staging container so the saved hip is clean.
    staging = hou.node('/obj/hda_staging')
    if staging:
        staging.destroy()

    os.makedirs(os.path.dirname(DEMO_HIP), exist_ok=True)
    import hou as _hou
    _hou.hipFile.save(DEMO_HIP)
    png = os.path.splitext(DEMO_HIP)[0] + '.png'
    render_png(unpack, png)
    print(f'saved {DEMO_HIP}')
    print(f'rendered {png}')


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--no-demo', action='store_true',
                    help='Only (re)build the HDA; skip the demo hip + '
                         'PNG render.')
    args = ap.parse_args(argv)
    asset = build_hda()
    if not args.no_demo:
        build_demo(asset)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
