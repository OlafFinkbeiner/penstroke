"""Compare an AI-derived per-letter spec against the actual trace output.

Reads `spec.json` (produced by the vision-model spec extractor) and
`metadata.json` (produced by the trace pipeline). For each letter:

  - Expected stroke count vs. traced stroke count.
  - Expected dot count vs. ... (TODO when the tracer exposes it).
  - Lists features the spec says must be covered, for human review.

The validation result is a markdown fragment that the report can append.
We deliberately do NOT do pixel-precise feature coverage yet — the spec
features are textual, not geometric. That arrives in a future pass when
the spec extractor also returns approximate feature locations.
"""

import json
import os


def load_spec(output_dir):
    path = os.path.join(output_dir, 'spec.json')
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def load_metadata(output_dir):
    with open(os.path.join(output_dir, 'metadata.json'), encoding='utf-8') as f:
        return json.load(f)


def build_validation_report(output_dir):
    """Return a markdown string comparing spec.json to the trace metadata.

    Returns an empty string if there's no spec to compare against.
    """
    spec = load_spec(output_dir)
    if spec is None:
        return ''
    metadata = load_metadata(output_dir)
    letters = metadata.get('letters', {})

    rows = []
    mismatches = []
    for ch, sp in spec.items():
        traced = letters.get(ch)
        if traced is None:
            rows.append((ch, sp['stroke_count'], None, sp['dot_count']))
            continue
        rows.append((ch, sp['stroke_count'], traced['stroke_count'], sp['dot_count']))
        if traced['stroke_count'] < sp['stroke_count']:
            mismatches.append((ch, sp, traced, 'under'))
        elif traced['stroke_count'] > sp['stroke_count']:
            mismatches.append((ch, sp, traced, 'over'))

    lines = ['## Spec validation', '',
             '_Comparison of AI vision-derived spec against the actual trace._', '']
    lines.append('| letter | spec strokes | traced | dots (spec) | match |')
    lines.append('|--------|-------------:|-------:|------------:|:-----:|')
    for ch, sp_n, tr_n, dots in rows:
        if tr_n is None:
            marker = '—'
        elif tr_n == sp_n:
            marker = '✓'
        elif tr_n < sp_n:
            marker = f'⬇ -{sp_n - tr_n}'
        else:
            marker = f'⬆ +{tr_n - sp_n}'
        tr_disp = str(tr_n) if tr_n is not None else '—'
        lines.append(f'| {ch} | {sp_n} | {tr_disp} | {dots} | {marker} |')
    lines.append('')

    if mismatches:
        lines.append('### Mismatched letters (worth eyeballing)')
        lines.append('')
        for ch, sp, tr, kind in mismatches:
            arrow = 'fewer strokes than expected' if kind == 'under' else 'more strokes than expected'
            lines.append(f'**{ch}** — spec {sp["stroke_count"]}, traced {tr["stroke_count"]} ({arrow})')
            feat = ', '.join(sp['features'])
            lines.append(f'  Spec features: {feat}')
            if sp.get('notes'):
                lines.append(f'  Notes: _{sp["notes"]}_')
            lines.append('')

    return '\n'.join(lines)
