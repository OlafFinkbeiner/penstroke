"""Build the penstroke TOPs graph (run under hython).

    hython scripts/build_tops_graph.py [--roots DIR ...] [--limit N]
        [--name RE] [--category C] [--charset latin]
        [--bundles DIR] [--hip PATH] [--no-cook]

Graph (one work item per font):

    font_scan (Python Processor)
        penstroke.fontscan.scan() in-process; attributes per item:
        family, ttf, store, trace_dir, bundle, charset.
    trace_missing (Generic Generator)
        Out-of-process venv CLI `penstroke trace` per item. Expected
        output = the item's `store` attribute with Automatic cache
        mode, so fonts that already have a stroke store SKIP the
        trace — the PDG-native resumable batch.
    build_bundle (Python Script, in-process)
        Outline rep always; strokes rep when a store exists; cheap
        mtime early-exit when the bundle is already current.
    make_index (Python Script after Wait for All)
        index.html over all built bundles (QA sheets inline).

By default cooks the graph after building and saves the .hip so the
graph stays inspectable/re-cookable in the Houdini UI.
"""

import argparse
import os
import sys

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_SRC = os.path.join(_REPO, 'src')
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

VENV_PYTHON = os.path.join(_REPO, '.venv', 'Scripts', 'python.exe')
DEFAULT_ROOTS = [os.path.join(_REPO, 'output', 'handwriting')]
DEFAULT_BUNDLES = os.path.join(_REPO, 'output', 'hfont_dev', 'bundles')
DEFAULT_HIP = os.path.join(_REPO, 'output', 'hfont_dev',
                           'penstroke_tops.hip')

SCAN_CODE = '''
import json
import os
from penstroke.fontscan import scan

cfg = json.loads({cfg!r})
records = scan(cfg['roots'], name=cfg['name'], category=cfg['category'],
               limit=cfg['limit'])
for i, rec in enumerate(records):
    trace_dir = rec['trace_dir'] or os.path.join(
        cfg['traces_root'], rec['family'].lower().replace(' ', ''))
    store = rec['store'] or os.path.join(trace_dir, 'strokes.json')
    bundle = os.path.join(cfg['bundles_root'],
                          rec['family'].lower().replace(' ', '') + '.hfont')
    w = item_holder.addWorkItem(index=i)
    w.setStringAttrib('family', rec['family'])
    w.setStringAttrib('ttf', rec['ttf'])
    w.setStringAttrib('store', store)
    w.setStringAttrib('trace_dir', trace_dir)
    # metadata.json is trace_font's LAST write = the completion
    # marker (a partial strokes.json must not skip the trace).
    w.setStringAttrib('trace_done', os.path.join(trace_dir,
                                                 'metadata.json'))
    w.setStringAttrib('bundle', bundle)
    w.setStringAttrib('charset', cfg['charset'])
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

bundles_root = {bundles_root!r}
rows = []
for man_path in sorted(glob.glob(os.path.join(bundles_root, '*.hfont',
                                              'manifest.json'))):
    bundle = os.path.dirname(man_path)
    rel = os.path.basename(bundle)
    with open(man_path, encoding='utf-8') as f:
        man = json.load(f)
    reps = ', '.join(sorted(man.get('reps', {{}})))
    qa = []
    for rep in ('strokes', 'outline'):
        png = os.path.join(bundle, 'qa', rep + '.png')
        if os.path.exists(png):
            qa.append(f'<a href="{{rel}}/qa/{{rep}}.png">{{rep}}</a>')
    rows.append(f'<tr><td>{{html.escape(man["family"])}}</td>'
                f'<td>{{reps}}</td><td>{{" ".join(qa)}}</td></tr>')
doc = ('<!DOCTYPE html><meta charset="utf-8"><title>hfont bundles</title>'
       '<style>body{{font-family:system-ui;margin:40px}}'
       'td{{padding:4px 16px 4px 0}}</style>'
       f'<h1>hfont bundles ({{len(rows)}})</h1>'
       '<table><tr><th>family</th><th>reps</th><th>QA</th></tr>'
       + ''.join(rows) + '</table>')
with open(os.path.join(bundles_root, 'index.html'), 'w',
          encoding='utf-8') as f:
    f.write(doc)
print('index:', os.path.join(bundles_root, 'index.html'))
'''


def build_graph(cfg):
    import hou
    import json
    topnet = hou.node('/obj').createNode('topnet', 'penstroke_tops')

    scan_node = topnet.createNode('pythonprocessor', 'font_scan')
    scan_node.parm('generate').set(
        SCAN_CODE.format(cfg=json.dumps(cfg)))

    trace = topnet.createNode('genericgenerator', 'trace_missing')
    trace.setInput(0, scan_node)
    trace.parm('windowscommand').set(
        f'"{VENV_PYTHON}" -m penstroke trace "@ttf" "@trace_dir" '
        f'--charset @charset --quiet')
    trace.parm('expectedoutputsfrom').set('1')      # from attribute
    trace.parm('expectedoutputattr').set('trace_done')
    trace.parm('pdg_cachemode').set('0')            # automatic

    build = topnet.createNode('pythonscript', 'build_bundle')
    build.setInput(0, trace)
    build.parm('inprocess').set(True)
    build.parm('script').set(BUILD_CODE)

    wait = topnet.createNode('waitforall', 'wait_all')
    wait.setInput(0, build)

    index = topnet.createNode('pythonscript', 'make_index')
    index.setInput(0, wait)
    index.parm('inprocess').set(True)
    index.parm('script').set(
        INDEX_CODE.format(bundles_root=cfg['bundles_root']))

    index.setDisplayFlag(True)
    topnet.layoutChildren()
    return topnet, index


def main(argv=None):
    import hou
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--roots', nargs='+', default=DEFAULT_ROOTS)
    ap.add_argument('--bundles', default=DEFAULT_BUNDLES)
    ap.add_argument('--traces',
                    default=os.path.join(_REPO, 'output', 'handwriting'),
                    help='Where fresh traces land (trace_dir for fonts '
                         'scanned without one).')
    ap.add_argument('--name', default=None)
    ap.add_argument('--category', default=None)
    ap.add_argument('--charset', default='latin',
                    choices=['ascii', 'latin', 'all'])
    ap.add_argument('--limit', type=int, default=None)
    ap.add_argument('--hip', default=DEFAULT_HIP)
    ap.add_argument('--no-cook', action='store_true')
    args = ap.parse_args(argv)

    cfg = {
        'roots': [os.path.abspath(r) for r in args.roots],
        'bundles_root': os.path.abspath(args.bundles),
        'traces_root': os.path.abspath(args.traces),
        'name': args.name,
        'category': args.category,
        'charset': args.charset,
        'limit': args.limit,
    }
    os.makedirs(cfg['bundles_root'], exist_ok=True)
    topnet, index = build_graph(cfg)

    os.makedirs(os.path.dirname(os.path.abspath(args.hip)), exist_ok=True)
    hou.hipFile.save(args.hip)
    print(f'saved {args.hip}')

    if not args.no_cook:
        index.cookWorkItems(block=True)
        n = len([d for d in os.listdir(cfg['bundles_root'])
                 if d.endswith('.hfont')])
        print(f'cooked: {n} bundles under {cfg["bundles_root"]}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
