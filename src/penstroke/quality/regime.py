"""Classify a font as 'script' or 'print' so the trace pipeline can
apply regime-appropriate post-processing.

The distinction matters because the two regimes have different correctness
criteria. In a script / handwriting font (Caveat, brush, calligraphic),
the natural drawing motion is continuous; **retracing is legitimate**
because lifting the pen costs more than going back over a line. In a
print / typographic font (Garamond, sans, slab), every stroke is an
idealised discrete geometric element and overlap means a duplicate.

Two stages of templates/fixes.py are wrong by default for script fonts:

  - deduplicate_overlapping_strokes: would kill legitimate retraces.
  - split_topmost_interior_strokes: would break a flowing N-shape (a
    'b' that traces up the stem, around the bowl, back to baseline)
    into two pieces, when the right answer is one continuous motion.

Detection strategy: if a per-font spec.json is available, average the
canonical stroke_count across all letters. A flowing script averages
≤ 1.5 strokes per letter (many letters are one continuous motion); a
clean print averages ≥ 2.0 (most letters have discrete stems and
crossbars). When no spec is available, default to 'print' — that's
the safer default since the stages that get skipped under 'script'
are only there to clean up redundancy.
"""

import json
import os


SCRIPT_AVG_THRESHOLD = 1.7   # average ≤ 1.7 → script; > → print


def classify_from_spec(spec_path):
    """Return 'script' or 'print' (or None if no spec available)."""
    if not os.path.exists(spec_path):
        return None
    try:
        with open(spec_path, encoding='utf-8') as f:
            spec = json.load(f)
    except Exception:
        return None
    counts = []
    for entry in spec.values():
        if isinstance(entry, dict) and 'stroke_count' in entry:
            counts.append(int(entry['stroke_count']))
    if not counts:
        return None
    avg = sum(counts) / len(counts)
    return 'script' if avg <= SCRIPT_AVG_THRESHOLD else 'print'


def classify_for_output_dir(output_dir):
    """Convenience: classify by reading output_dir/spec.json if present."""
    return classify_from_spec(os.path.join(output_dir, 'spec.json'))
