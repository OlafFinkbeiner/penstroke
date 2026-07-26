"""Atomic file-write helpers.

Files that accumulate state (strokes.json — hours of hand edits — and
hfont manifest.json) must never be truncated by a crash mid-write.
Write to a temp file in the target directory, then os.replace(), which
is atomic on both POSIX and Windows: readers see either the old or the
new content, never a partial file.

Stdlib-only so hython can import it.
"""

import json
import os
import tempfile


def write_json_atomic(path, data, indent=None, trailing_newline=False):
    """Serialize `data` as UTF-8 JSON to `path` atomically."""
    path = os.path.abspath(path)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path),
                               prefix=os.path.basename(path) + '.',
                               suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
            if trailing_newline:
                f.write('\n')
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
