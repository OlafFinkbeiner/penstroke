"""Font discovery tests — pure python, no Houdini required.

    python tests/test_fontscan.py
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from penstroke.fontscan import scan

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'caveat.ttf')


def _make_tree(tmp):
    """A family present BOTH as a Google checkout (with category) and a
    penstroke trace output (with a stroke store), under separate roots."""
    gf = os.path.join(tmp, 'gf', 'testfam')
    os.makedirs(gf)
    shutil.copy(FIXTURE, os.path.join(gf, 'Testfam-Regular.ttf'))
    with open(os.path.join(gf, 'METADATA.pb'), 'w', encoding='utf-8') as f:
        f.write('name: "Testfam"\ncategory: "HANDWRITING"\nlicense: "OFL"\n')
    with open(os.path.join(gf, 'OFL.txt'), 'w', encoding='utf-8') as f:
        f.write('license')

    tr = os.path.join(tmp, 'traces', 'testfam')
    os.makedirs(os.path.join(tr, 'source'))
    shutil.copy(FIXTURE, os.path.join(tr, 'source', 'Caveat.ttf'))
    with open(os.path.join(tr, 'metadata.json'), 'w', encoding='utf-8') as f:
        f.write('{}')                       # presence = complete trace
    with open(os.path.join(tr, 'strokes.json'), 'w', encoding='utf-8') as f:
        f.write('{"glyphs": {}}')
    return os.path.join(tmp, 'gf'), os.path.join(tmp, 'traces')


def test_merge_category_and_store_either_order():
    with tempfile.TemporaryDirectory() as tmp:
        gf_root, tr_root = _make_tree(tmp)
        for roots in ([gf_root, tr_root], [tr_root, gf_root]):
            recs = scan(roots)
            assert len(recs) == 1, (roots, len(recs))
            r = recs[0]
            assert r['category'] == 'HANDWRITING', (roots, r['category'])
            # trace store + dir survive the merge
            assert r['store'] and os.path.exists(r['store']), roots
            assert os.path.basename(r['trace_dir']) == 'testfam', roots
            # the source TTF that was actually traced is the one kept
            assert os.path.basename(r['ttf']) == 'Caveat.ttf', \
                (roots, r['ttf'])
    print('✓ merge: category + store survive regardless of walk order')


def test_category_filter_uses_merged_value():
    with tempfile.TemporaryDirectory() as tmp:
        gf_root, tr_root = _make_tree(tmp)
        # trace dir walked first, then enriched: the filter must still
        # match on the merged-in category.
        hits = scan([tr_root, gf_root], category='HANDWRITING')
        assert len(hits) == 1, hits
        # trace dir alone has no category, so the filter excludes it
        none = scan([tr_root], category='HANDWRITING')
        assert none == [], none
    print('✓ filter: category filter honors the merged category')


if __name__ == '__main__':
    test_merge_category_and_store_either_order()
    test_category_filter_uses_merged_value()
    print('\nAll fontscan tests passed.')
