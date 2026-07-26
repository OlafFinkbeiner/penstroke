"""Install the penstroke Houdini package file.

    hython scripts/install_houdini_package.py [--prefs DIR]

Writes <houdini-prefs>/packages/penstroke.json, which makes every
Houdini/hython session on this machine penstroke-ready:

  - $PENSTROKE      -> this repo (resolved at install time)
  - PYTHONPATH      += $PENSTROKE/src   (replaces the old
                       `hython -m pip install --user -e .` step)
  - HOUDINI_PATH    += $PENSTROKE/houdini  (its otls/ folder is on the
                       default OTL scan path, so penstroke::text_layout
                       and penstroke::tops load automatically)

The prefs dir is taken from hou.homeHoudiniDirectory() when running
under hython, else discovered by globbing Documents/houdini* (newest
version wins). Re-running overwrites the package file (idempotent).
"""

import argparse
import glob
import json
import os
import sys

_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def find_prefs_dir():
    try:
        import hou
        return hou.homeHoudiniDirectory()
    except ImportError:
        pass
    candidates = []
    home = os.path.expanduser('~')
    for docs in (os.path.join(home, 'Documents'),
                 os.path.join(home, 'OneDrive', 'Documents')):
        candidates += glob.glob(os.path.join(docs, 'houdini*.*'))
    if not candidates:
        raise SystemExit('no Houdini prefs dir found - run under hython '
                         'or pass --prefs')
    return sorted(candidates)[-1]


def package_body(repo):
    return {
        'env': [
            {'PENSTROKE': repo.replace(os.sep, '/')},
            {'PYTHONPATH': '$PENSTROKE/src'},
        ],
        'path': '$PENSTROKE/houdini',
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--prefs', default=None,
                    help='Houdini prefs dir (default: auto-detect).')
    args = ap.parse_args(argv)

    prefs = args.prefs or find_prefs_dir()
    pkg_dir = os.path.join(prefs, 'packages')
    os.makedirs(pkg_dir, exist_ok=True)
    pkg_path = os.path.join(pkg_dir, 'penstroke.json')
    with open(pkg_path, 'w', encoding='utf-8') as f:
        json.dump(package_body(_REPO), f, indent=4)
        f.write('\n')
    print(f'installed {pkg_path}')
    print('  PENSTROKE  =', _REPO)
    print('  PYTHONPATH += $PENSTROKE/src')
    print('  HOUDINI_PATH += $PENSTROKE/houdini (otls auto-scanned)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
