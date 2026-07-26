"""Build the penstroke::text_layout HDA (run under hython).

    hython scripts/build_text_layout_hda.py

Writes houdini/otls/penstroke_text_layout.hda. The HDA is a ~50-line
Python SOP shim around penstroke.layout (the engine stays in the
package — `hython -m pip install --user --no-deps -e .` makes it
importable; the shim raises a clear error if it isn't). Output is one
point per glyph with `name`, `pscale`, `line`, `word`, `cluster`,
`charinword`, `idx`, written with batch APIs. Downstream: Copy to
Points, id attribute `name`, source = any hfont rep.

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

import os
node = hou.pwd()
hda = node.parent()
geo = node.geometry()

# Bundle = the chosen font inside the selected folder.
bundle = os.path.normpath(os.path.join(
    hda.evalParm('hfont'), hda.parm('font').evalAsString() or '.'))
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
                       ('cluster', out.cluster),
                       ('charinword', out.char_in_word),
                       ('idx', out.index)):
        geo.addAttrib(hou.attribType.Point, attr, 0, create_local_variable=False)
        geo.setPointIntAttribValues(attr, [int(v) for v in vals])
'''


# Node help card (Houdini help markup), shown in the Help pane.
HDA_HELP = '''= Penstroke Text Layout =

"""Lay text out in an hfont bundle and assemble the glyphs — pick a
folder, pick the font, pick the rep, type."""

Shapes the __Text__ with HarfBuzz against the chosen font, places one
point per glyph in writing order, and (with __Assemble Glyphs__ on)
copies the chosen rep's geometry onto those points — so the output IS
the laid-out text, no extra File + Copy to Points wiring.

Pick the __Hfonts Folder__; optionally narrow by __Type__ (Sans Serif,
Serif, Handwriting, …); the __Font__ menu lists the matching `.hfont`
bundles (open it and type a letter to jump); and the __Rep__ menu lists
the reps that font contains (strokes, strokes_bezier, outline). Turn
on __Build Ribbon__ to output the strokes as filled variable-width
ribbon surfaces (the calligraphic stroke) instead of bare curves.

With __Assemble Glyphs__ off, the output is just the layout points —
`P` (glyph origin), `name` (glyph key), `pscale` (= font size), and
`line`, `word`, `cluster`, `charinword` (letter index within its word),
`idx` (running glyph index) — for your own Copy to Points (Piece
Attribute `name`). See `docs/houdini_workflow.md` for the full guide.

@parameters

Hfonts Folder:
    A folder containing `.hfont` bundles (e.g. the Penstroke TOPs
    output). Pick it; the Font menu then lists the bundles in it.

Type:
    Filter the Font menu by Google Fonts category (All / Sans Serif /
    Serif / Display / Handwriting / Monospace). Categories come from the
    cook's index.json; bundles with no recorded category show under All.
    The menu always lists every `.hfont` on disk, even before the cook
    has indexed them.

Font:
    Which `.hfont` bundle in the folder (filtered by Type).

Rep:
    Which representation to place — the reps present in the selected
    font (default rep first). Drives an internal File SOP.

Assemble Glyphs:
    On: copy the chosen rep onto the layout points (output = the text).
    Off: output the bare layout points for your own Copy to Points.

Build Ribbon:
    Turn the centerline curves into variable-width ribbon surfaces (the
    calligraphic stroke, filled) using the per-point `width`. Use a
    strokes / strokes_bezier rep. Off = place the raw curves.

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


# Font dropdown: list the .hfont bundles in the folder. Token = the
# bundle's path token ('<name>.hfont', or '.' when the folder is itself
# a bundle) so <folder>/<token> is the bundle; label = clean family
# name. hfont.list_bundles is the one ordering source.
FONT_MENU_SCRIPT = '''
import os, json, glob
node = kwargs['node']
folder = node.evalParm('hfont')
ftype = node.parm('fonttype').evalAsString()
# Availability comes from the bundles that ACTUALLY exist on disk, so a
# stale or partial index.json never hides a font. index.json (written
# by the cook) only supplies the category, used to drive the Type
# filter; a font absent from it has unknown category and shows under
# All only.
cats = {}
idx_path = os.path.join(folder, 'index.json')
if os.path.exists(idx_path):
    try:
        with open(idx_path, encoding='utf-8') as f:
            cats = {k: (v.get('category') or '')
                    for k, v in json.load(f).items()}
    except Exception:
        cats = {}
bundles = sorted(glob.glob(os.path.join(folder, '*.hfont')))
if not bundles and os.path.exists(os.path.join(folder, 'manifest.json')):
    bundles = [folder]      # the folder itself is a single bundle
items = []
for bundle in bundles:
    base = os.path.basename(bundle.rstrip('/\\\\'))
    token = '.' if bundle == folder else base
    if ftype and ftype != 'ALL' and cats.get(base, '') != ftype:
        continue
    label = base[:-6] if base.endswith('.hfont') else base
    items += [token, label]
if not items:
    items = ['', '(no fonts match)']
return items
'''


def _bundle_expr(node_ref):
    # token -> bundle path: <folder>/<font>, normalized.
    return ("os.path.normpath(os.path.join({0}.evalParm('hfont'), "
            "{0}.parm('font').evalAsString() or '.'))").format(node_ref)


# Dynamic Rep menu: the reps in the SELECTED font's manifest.
REP_MENU_SCRIPT = '''
import os, json
node = kwargs['node']
bundle = ''' + _bundle_expr('node') + '''
items = []
try:
    with open(os.path.join(bundle, 'manifest.json'), encoding='utf-8') as f:
        man = json.load(f)
    default = man.get('default_rep')
    reps = list(man.get('reps', {}))
    # default rep first, then the rest alphabetically
    reps.sort(key=lambda r: (r != default, r))
    for r in reps:
        items += [r, r]
except Exception:
    pass
if not items:
    items = ['strokes', 'strokes']
return items
'''


def hda_parm_templates():
    import hou
    return [
        hou.StringParmTemplate(
            'hfont', 'Hfonts Folder', 1,
            default_value=('$PENSTROKE/output/hfont_dev/hfonts',),
            string_type=hou.stringParmType.FileReference,
            help='Folder containing .hfont bundles. Pick the folder; the '
                 'Font menu then lists the bundles in it.'),
        hou.MenuParmTemplate(
            'fonttype', 'Type',
            ('ALL', 'SANS_SERIF', 'SERIF', 'DISPLAY', 'HANDWRITING',
             'MONOSPACE'),
            menu_labels=('All', 'Sans Serif', 'Serif', 'Display',
                         'Handwriting', 'Monospace'),
            help='Filter the Font menu by Google Fonts category (from the '
                 "cook's index.json). Fonts with no recorded category "
                 'show under All only; every bundle on disk is listed '
                 'regardless.'),
        hou.MenuParmTemplate(
            'font', 'Font', (), item_generator_script=FONT_MENU_SCRIPT,
            item_generator_script_language=hou.scriptLanguage.Python,
            help='Which bundle in the folder (filtered by Type). Open the '
                 'menu and type a letter to jump.'),
        hou.MenuParmTemplate(
            'rep', 'Rep', (), item_generator_script=REP_MENU_SCRIPT,
            item_generator_script_language=hou.scriptLanguage.Python,
            help='Which representation to place: the reps present in the '
                 'selected font (strokes, strokes_bezier, outline).'),
        hou.ToggleParmTemplate(
            'assemble', 'Assemble Glyphs', default_value=True,
            help='On: Copy the chosen rep onto the layout points (output '
                 '= the laid-out text). Off: output just the layout '
                 'points (P, name, pscale, line/word/cluster/charinword/'
                 'idx).'),
        hou.ToggleParmTemplate(
            'ribbon', 'Build Ribbon', default_value=False,
            disable_when='{ assemble == 0 }',
            help='Turn the centerline curves into variable-width ribbon '
                 'surfaces (the calligraphic stroke filled). Use a '
                 'strokes / strokes_bezier rep (they carry width).'),
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

    # Layout points (one per glyph, with `name`).
    shim = subnet.createNode('python', 'layout_shim')
    shim.parm('python').set(SHIM_CODE)

    # The chosen rep's packed glyph geometry, path derived from the
    # hfont + rep parms.
    rep_geo = subnet.createNode('file', 'rep_geo')
    rep_geo.parm('file').setExpression(
        'chs("../hfont") + "/" + chs("../font") + "/reps/" + '
        'chs("../rep") + "/glyphs.bgeo.sc"',
        hou.exprLanguage.Hscript)

    # Ribbon branch: turn the centerline curves into variable-width
    # ribbons. Unpack (carry `name` inward) -> rename width to pscale ->
    # Sweep a flat ribbon in the glyph plane (up = +Z). The Sweep
    # tessellates the bezier directly, so no resample is needed. `width`
    # is the full stroke width, so the sweep scale is 0.5 (pscale is the
    # half-width radius). Built in em space, so Copy to Points scales the
    # ribbon width with the glyph.
    rib_unpack = subnet.createNode('unpack', 'rib_unpack')
    rib_unpack.setInput(0, rep_geo)
    rib_unpack.parm('transfer_attributes').set('name')
    rib_width = subnet.createNode('attribwrangle', 'rib_width')
    rib_width.setInput(0, rib_unpack)
    # Rename the per-point `width` to `pscale` (what the Sweep reads).
    rib_width.parm('snippet').set('f@pscale = f@width;')
    rib_sweep = subnet.createNode('sweep::2.0', 'rib_sweep')
    rib_sweep.setInput(0, rib_width)
    rib_sweep.parm('surfaceshape').set('ribbon')
    rib_sweep.parm('surfacetype').set('quads')
    rib_sweep.parm('upvectortype').set('z')
    rib_sweep.parm('scale').set(0.5)

    # Ribbon toggle: source = ribbons (1) or the raw curves (0).
    rib_switch = subnet.createNode('switch', 'rib_switch')
    rib_switch.setInput(0, rep_geo)
    rib_switch.setInput(1, rib_sweep)
    rib_switch.parm('input').setExpression('ch("../ribbon")',
                                           hou.exprLanguage.Hscript)

    # Copy the glyph (curve or ribbon) onto each layout point by `name`.
    copy = subnet.createNode('copytopoints::2.0', 'assemble')
    copy.setInput(0, rib_switch)
    copy.setInput(1, shim)
    copy.parm('useidattrib').set(True)
    copy.parm('idattrib').set('name')
    # Forward the per-glyph layout metadata (idx, word, charinword,
    # line, cluster) from the target points onto each copy, so the
    # assembled text carries them too. `* ^name ^pscale` = every target
    # attribute except the piece id (already on the prims) and the copy
    # scale. Positions are untouched — Copy to Points always protects P.
    copy.parm('targetattribs').set(1)        # one "Attributes from Target" row
    copy.parm('useapply1').set(True)
    copy.parm('applyattribs1').set('* ^name ^pscale')

    # Assemble toggle: input1 = assembled glyphs, input0 = bare points.
    switch = subnet.createNode('switch', 'assemble_switch')
    switch.setInput(0, shim)
    switch.setInput(1, copy)
    switch.parm('input').setExpression('ch("../assemble")',
                                       hou.exprLanguage.Hscript)

    out = subnet.createNode('output', 'out')
    out.setInput(0, switch)
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
    # The Hfont Bundle parm points at a .hfont FOLDER — give it a
    # directory chooser (HOM emits `type file`; patch the .ds).
    from penstroke.houdini import dirify_dialog_script
    ds = definition.sections()['DialogScript'].contents()
    definition.addSection('DialogScript',
                          dirify_dialog_script(ds, {'hfont'}))
    asset.matchCurrentDefinition()
    print(f'wrote {HDA_PATH}')
    return asset


def build_demo(asset_node):
    import hou
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from hfont_demo import render_png

    # The HDA now assembles glyphs internally (Copy to Points), so the
    # demo is just the HDA + an unpack for rendering.
    demo = hou.node('/obj').createNode('geo', 'hfont_hda_demo')
    txt = demo.createNode(HDA_NAME, 'text_layout')
    txt.parm('hfont').set(os.path.dirname(DEMO_BUNDLE))
    txt.parm('font').set(os.path.basename(DEMO_BUNDLE))
    txt.parm('rep').set('outline')
    txt.parm('text').set(DEMO_TEXT.replace('\\n', '\n'))
    txt.parm('usewidth').set(True)
    txt.parm('width').set(9.0)
    txt.parm('align').set('justify')

    unpack = demo.createNode('unpack', 'unpack_for_render')
    unpack.setInput(0, txt)
    txt.setDisplayFlag(True)
    txt.setRenderFlag(True)
    demo.layoutChildren()

    n_copies = len(txt.geometry().prims())
    print(f'HDA cooked: {n_copies} glyph instances placed')

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
