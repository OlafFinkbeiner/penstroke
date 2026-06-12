"""Build the penstroke TOPs graph (run under hython).

    hython scripts/build_tops_graph.py [--roots DIR ...] [--limit N]
        [--name RE] [--category C] [--charset latin]
        [--bundles DIR] [--traces DIR] [--hip PATH] [--no-cook]

The saved .hip is self-contained and GUI-friendly: all configuration
(roots, filters, charset, output dirs) lives as spare parameters on
the /obj/penstroke_tops network — edit them in Houdini and re-cook,
no code involved.

Graph (one work item per font):

    font_scan (Python Processor)
        penstroke.fontscan.scan() in-process, driven by the topnet
        parms; attributes per item: family, name_norm, ttf, store,
        trace_dir, trace_done, bundle, charset.
    trace_missing (Python Processor, command items)
        Out-of-process `penstroke trace` via scripts/run_trace.cmd
        (scrubs PYTHONPATH/PYTHONHOME — PDG jobs inherit Houdini's,
        which poisons the venv interpreter). The command is BAKED per
        item at generation time: this node's command parm performs no
        @attrib expansion (verified empirically). Items whose
        metadata.json (trace_font's LAST write) exists get no command
        — instant cook, which is the cache/resume mechanism.
    sync_edits (Python Processor, command items)
        The Corel file handshake (penstroke/handshake.py): a command
        (`penstroke sync-edits`, out-of-process via run_penstroke.cmd)
        is baked ONLY for fonts with pending selections or edits/*.csv,
        so the stage is free when nothing changed. Selections come from
        the global inbox (the `selectionsroot` parm — sel-*.json routed
        per font via their "font" field) or a font's own selections/;
        Corel CSVs go out to and come back from the global exchange
        folder (`corelroot` parm), same file both ways. Merged
        edits update strokes.json, which build_bundle's mtime check
        then picks up in the same cook.
    build_bundle (Python Script, in-process)
        Outline rep always; strokes rep when a store exists; cheap
        mtime early-exit. Generates only from COOKED upstream items,
        so a failed trace produces no bundle instead of a silently
        degraded one.
    make_index (Python Script after Wait for All)
        index.html over all built bundles (QA sheets linked).
"""

import argparse
import os
import sys

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

TOPNET_PATH = '/obj/penstroke_tops'
DEFAULT_ROOTS = [os.path.join(_REPO, 'output', 'handwriting')]
DEFAULT_BUNDLES = os.path.join(_REPO, 'output', 'hfont_dev', 'hfonts')
DEFAULT_TRACES = os.path.join(_REPO, 'output', 'handwriting')
DEFAULT_HIP = os.path.join(_REPO, 'output', 'hfont_dev',
                           'penstroke_tops.hip')
DEFAULT_SELECTIONS = os.path.join(_REPO, 'selections')
DEFAULT_COREL = os.path.join(_REPO, 'corel')

# Code templates. Tokens (__TOKEN__) are substituted with .replace()
# — NOT str.format — so braces and backslashes in the code stay inert.

SCAN_CODE = '''
import os

import hou
from penstroke.fontscan import scan

top = hou.node('__TOPNET__')
roots = [r.strip() for r in top.evalParm('roots').splitlines()
         if r.strip()]
records = scan(roots,
               name=top.evalParm('namefilter') or None,
               category=top.evalParm('category') or None,
               limit=top.evalParm('limit') or None)
charset = top.parm('charset').evalAsString()
traces_root = top.evalParm('tracesroot')
bundles_root = top.evalParm('bundlesroot')

for i, rec in enumerate(records):
    norm = rec['family'].lower().replace(' ', '')
    trace_dir = rec['trace_dir'] or os.path.join(traces_root, norm)
    store = rec['store'] or os.path.join(trace_dir, 'strokes.json')
    w = item_holder.addWorkItem(index=i)
    w.setStringAttrib('family', rec['family'])
    w.setStringAttrib('name_norm', norm)
    w.setStringAttrib('ttf', rec['ttf'])
    w.setStringAttrib('store', store)
    w.setStringAttrib('trace_dir', trace_dir)
    # metadata.json is trace_font's LAST write = completion marker.
    w.setStringAttrib('trace_done', os.path.join(trace_dir,
                                                 'metadata.json'))
    w.setStringAttrib('bundle',
                      os.path.join(bundles_root, norm + '.hfont'))
    w.setStringAttrib('charset', charset)
'''

TRACE_CODE = '''
import os
for upstream_item in upstream_items:
    w = item_holder.addWorkItem(parent=upstream_item)
    if not os.path.exists(upstream_item.stringAttribValue('trace_done')):
        w.setCommand('"__LAUNCHER__" "%s" "%s" %s "%s"' % (
            upstream_item.stringAttribValue('ttf'),
            upstream_item.stringAttribValue('trace_dir'),
            upstream_item.stringAttribValue('charset'),
            upstream_item.stringAttribValue('name_norm')))
'''

SYNC_CODE = '''
import hou
from penstroke.handshake import has_pending

top = hou.node('__TOPNET__')
inbox = top.evalParm('selectionsroot')
corel = top.evalParm('corelroot')
for upstream_item in upstream_items:
    w = item_holder.addWorkItem(parent=upstream_item)
    trace_dir = upstream_item.stringAttribValue('trace_dir')
    if has_pending(trace_dir, inbox=inbox or None, corel_dir=corel or None):
        cmd = '"__LAUNCHER__" sync-edits "%s"' % trace_dir
        if inbox:
            cmd += ' --inbox "%s"' % inbox
        if corel:
            cmd += ' --corel "%s"' % corel
        w.setCommand(cmd)
'''

BUILD_CODE = '''
import os

ttf = work_item.attribValue('ttf')
bundle = work_item.attribValue('bundle')
store = work_item.attribValue('store')
charset = work_item.attribValue('charset')

from penstroke import hfont
from penstroke.houdini import rep_outline, rep_strokes


def current(geo_path, src_path):
    return (os.path.exists(geo_path) and os.path.exists(src_path)
            and os.path.getmtime(geo_path) >= os.path.getmtime(src_path))


need_outline = True
need_strokes = os.path.exists(store)
if not need_strokes:
    print('WARNING: no stroke store for',
          work_item.attribValue('family'),
          '- trace failed or skipped; building outline-only bundle')
if os.path.exists(os.path.join(bundle, hfont.MANIFEST_NAME)):
    try:
        hf = hfont.HFont(bundle)
        if 'outline' in hf.manifest['reps'] and \\
                current(hf.rep_geo_path('outline'), ttf):
            need_outline = False
        if need_strokes and 'strokes' in hf.manifest['reps'] and \\
                current(hf.rep_geo_path('strokes'), store):
            need_strokes = False
    except Exception:
        pass

# Failures must not abort the batch: report loudly, leave the bundle
# absent/partial (visible in the index), let the other fonts cook.
try:
    if need_outline:
        rep_outline.build_outline_rep(ttf, bundle, charset=charset,
                                      verbose=False)
    if need_strokes:
        rep_strokes.build_strokes_rep(store, bundle, verbose=False)
    print('bundle ok:', bundle)
except Exception:
    import traceback
    print('BUNDLE FAILED:', work_item.attribValue('family'))
    traceback.print_exc()
'''

INDEX_CODE = '''
import glob
import html
import json
import os

import hou

bundles_root = hou.node('__TOPNET__').evalParm('bundlesroot')
rows = []
for man_path in sorted(glob.glob(os.path.join(bundles_root, '*.hfont',
                                              'manifest.json'))):
    bundle = os.path.dirname(man_path)
    rel = os.path.basename(bundle)
    with open(man_path, encoding='utf-8') as f:
        man = json.load(f)
    reps = ', '.join(sorted(man.get('reps', {})))
    qa = []
    for rep in ('strokes', 'outline'):
        png = os.path.join(bundle, 'qa', rep + '.png')
        if os.path.exists(png):
            qa.append('<a href="%s/qa/%s.png">%s</a>' % (rel, rep, rep))
    rows.append('<tr><td>%s</td><td>%s</td><td>%s</td></tr>'
                % (html.escape(man['family']), reps, ' '.join(qa)))
doc = ('<!DOCTYPE html><meta charset="utf-8"><title>hfonts</title>'
       '<style>body{font-family:system-ui;margin:40px}'
       'td{padding:4px 16px 4px 0}</style>'
       '<h1>hfonts (%d)</h1>' % len(rows)
       + '<table><tr><th>family</th><th>reps</th><th>QA</th></tr>'
       + ''.join(rows) + '</table>')
with open(os.path.join(bundles_root, 'index.html'), 'w',
          encoding='utf-8') as f:
    f.write(doc)
print('index:', os.path.join(bundles_root, 'index.html'))

# Also refresh the PREVIEWS index over the trace folders (separate
# file, batch_handwriting convention: links each font's interactive
# preview.html with the glyph-selection tool).
traces_root = hou.node('__TOPNET__').evalParm('tracesroot')
prows = []
for d in sorted(os.listdir(traces_root)):
    full = os.path.join(traces_root, d)
    if not os.path.isdir(full):
        continue
    if os.path.exists(os.path.join(full, 'preview.html')):
        prows.append('<li><a href="%s/preview.html">%s</a></li>'
                     % (html.escape(d), html.escape(d)))
    else:
        prows.append('<li>%s - no preview</li>' % html.escape(d))
pdoc = ('<!DOCTYPE html><meta charset="utf-8">'
        '<title>penstroke - previews</title>'
        '<style>body{font-family:system-ui;margin:40px}'
        'li{margin:3px 0}</style>'
        '<h1>Previews (%d)</h1><ul>' % len(prows)
        + ''.join(prows) + '</ul>')
with open(os.path.join(traces_root, 'index.html'), 'w',
          encoding='utf-8') as f:
    f.write(pdoc)
print('previews index:', os.path.join(traces_root, 'index.html'))
'''


def _topnet_parm_templates():
    import hou
    return [
        hou.StringParmTemplate(
            'roots', 'Font Roots (one per line)', 1,
            tags={'editor': '1', 'editorlines': '3'},
            help='Directories to scan: google/fonts checkouts, '
                 'penstroke trace outputs, or plain TTF folders.'),
        hou.StringParmTemplate(
            'tracesroot', 'Traces Root', 1,
            string_type=hou.stringParmType.FileReference,
            help='Where fresh traces land (existing trace dirs are '
                 'matched by family name).'),
        hou.StringParmTemplate(
            'bundlesroot', 'Hfonts Root', 1,
            string_type=hou.stringParmType.FileReference),
        hou.StringParmTemplate(
            'selectionsroot', 'Selections Inbox', 1,
            string_type=hou.stringParmType.FileReference,
            help='Global drop folder for preview.html selection files '
                 '(sel-<font>-<hash>.json); routed to each font via '
                 'the "font" field. Per-font <trace_dir>/selections/ '
                 'works too.'),
        hou.StringParmTemplate(
            'corelroot', 'Corel Exchange', 1,
            string_type=hou.stringParmType.FileReference,
            help='Global Corel exchange folder, both directions: '
                 'sync writes sel-<font>-<hash>.csv for the Corel '
                 'import macro; the export macro saves back onto the '
                 'same file (or any sel-<font>-* name) and the next '
                 'cook merges it.'),
        hou.StringParmTemplate(
            'namefilter', 'Family Name Regex', 1),
        hou.StringParmTemplate(
            'category', 'Google Fonts Category', 1,
            help='e.g. HANDWRITING. Empty = all. Only applies to '
                 'METADATA.pb records.'),
        hou.MenuParmTemplate('charset', 'Charset',
                             ('ascii', 'latin', 'all'),
                             default_value=1),
        hou.IntParmTemplate('limit', 'Limit (0 = all)', 1,
                            default_value=(0,), min=0, max=500),
    ]


def build_graph():
    import hou
    topnet = hou.node('/obj').createNode('topnet', 'penstroke_tops')
    assert topnet.path() == TOPNET_PATH, topnet.path()

    ptg = topnet.parmTemplateGroup()
    folder = hou.FolderParmTemplate('penstroke', 'Penstroke',
                                    _topnet_parm_templates())
    ptg.insertBefore(ptg.entries()[0], folder)
    topnet.setParmTemplateGroup(ptg)

    scan_node = topnet.createNode('pythonprocessor', 'font_scan')
    scan_node.parm('generate').set(
        SCAN_CODE.replace('__TOPNET__', TOPNET_PATH))

    launcher = os.path.join(_REPO, 'scripts',
                            'run_trace.cmd').replace(os.sep, '/')
    trace = topnet.createNode('pythonprocessor', 'trace_missing')
    trace.setInput(0, scan_node)
    trace.parm('generate').set(
        TRACE_CODE.replace('__LAUNCHER__', launcher))

    sync_launcher = os.path.join(_REPO, 'scripts',
                                 'run_penstroke.cmd').replace(os.sep, '/')
    sync = topnet.createNode('pythonprocessor', 'sync_edits')
    sync.setInput(0, trace)
    # Generate from cooked items only: a failed trace must not get a
    # sync command (its store/metadata may be half-written).
    sync.parm('pdg_workitemgeneration').set('0')
    sync.parm('generate').set(
        SYNC_CODE.replace('__LAUNCHER__', sync_launcher)
                 .replace('__TOPNET__', TOPNET_PATH))

    build = topnet.createNode('pythonscript', 'build_bundle')
    build.setInput(0, sync)
    build.parm('inprocess').set(True)
    build.parm('pdg_workitemgeneration').set('0')   # from cooked items
    build.parm('script').set(BUILD_CODE)

    wait = topnet.createNode('waitforall', 'wait_all')
    wait.setInput(0, build)

    index = topnet.createNode('pythonscript', 'make_index')
    index.setInput(0, wait)
    index.parm('inprocess').set(True)
    index.parm('script').set(
        INDEX_CODE.replace('__TOPNET__', TOPNET_PATH))

    index.setDisplayFlag(True)
    topnet.layoutChildren()
    return topnet, index


def main(argv=None):
    import hou
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--roots', nargs='+', default=DEFAULT_ROOTS)
    ap.add_argument('--bundles', default=DEFAULT_BUNDLES)
    ap.add_argument('--traces', default=DEFAULT_TRACES)
    ap.add_argument('--selections', default=DEFAULT_SELECTIONS,
                    help='Global selections inbox folder.')
    ap.add_argument('--corel', default=DEFAULT_COREL,
                    help='Global Corel exchange folder.')
    ap.add_argument('--name', default='')
    ap.add_argument('--category', default='')
    ap.add_argument('--charset', default='latin',
                    choices=['ascii', 'latin', 'all'])
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--hip', default=DEFAULT_HIP)
    ap.add_argument('--no-cook', action='store_true')
    args = ap.parse_args(argv)

    topnet, index = build_graph()
    topnet.parm('roots').set(
        '\n'.join(os.path.abspath(r) for r in args.roots))
    topnet.parm('tracesroot').set(os.path.abspath(args.traces))
    topnet.parm('bundlesroot').set(os.path.abspath(args.bundles))
    topnet.parm('selectionsroot').set(os.path.abspath(args.selections))
    os.makedirs(os.path.abspath(args.selections), exist_ok=True)
    topnet.parm('corelroot').set(os.path.abspath(args.corel))
    os.makedirs(os.path.abspath(args.corel), exist_ok=True)
    topnet.parm('namefilter').set(args.name)
    topnet.parm('category').set(args.category)
    topnet.parm('charset').set(args.charset)
    topnet.parm('limit').set(args.limit)
    os.makedirs(os.path.abspath(args.bundles), exist_ok=True)

    os.makedirs(os.path.dirname(os.path.abspath(args.hip)), exist_ok=True)
    hou.hipFile.save(args.hip)
    print(f'saved {args.hip}')

    if not args.no_cook:
        index.cookWorkItems(block=True)
        n = len([d for d in os.listdir(os.path.abspath(args.bundles))
                 if d.endswith('.hfont')])
        print(f'cooked: {n} bundles under {os.path.abspath(args.bundles)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
