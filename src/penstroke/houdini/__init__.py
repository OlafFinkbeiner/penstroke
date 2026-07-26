"""Houdini-side builders and helpers (run under hython, need `hou`).

Everything here writes/reads hfont bundles exclusively through
penstroke.hfont so the format stays defined in one place.
"""

import re


def dirify_dialog_script(ds, dir_parm_names):
    """Turn the named parms from `type file` into `type directory`.

    HOM's StringParmTemplate(FileReference) always emits `type file`,
    which gives a FILE chooser — wrong for parms that point at a folder
    (an .hfont bundle, an output dir). There's no HOM enum for a
    directory parm, but the .ds type `directory` (a real folder chooser)
    round-trips through an HDA's DialogScript section. So HDA builders
    set their parm interface normally, then run the saved DialogScript
    section through this before the final save.

    `dir_parm_names` is the set of parm names to convert.
    """
    names = set(dir_parm_names)
    out, cur = [], None
    for line in ds.splitlines():
        m = re.match(r'\s*name\s+"([^"]+)"', line)
        if m:
            cur = m.group(1)
        if cur in names and re.match(r'\s*type\s+file\s*$', line):
            line = line.replace('file', 'directory')
        out.append(line)
    return '\n'.join(out)
