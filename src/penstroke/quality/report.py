"""Assemble a Markdown quality report from per-letter trace metrics.

For each letter we collect:
  - the metrics dict from `assess_letter`
  - the template used (rowmans/cursive/etc.)
  - the stroke count

The report groups letters into "clean", "minor issues", and "significant
issues" tiers based on the overall quality score (lowest metric per letter).
The intent is: a font's report.md should tell a human consumer at a glance
which letters they can confidently ship and which need a closer look.
"""

import json
from penstroke.quality.metrics import (
    has_strokes, coverage, stroke_count_matches_template,
)
from penstroke.quality.ocr import ocr_recognizable


def assess_letter(mask, traced, expected_stroke_count=None, char=None):
    """Run all metrics on one traced letter. Returns a dict of {metric_name:
    (score, issue_or_none)} plus an aggregated `overall_score`.

    If `char` is provided and OCR is available, the OCR-recognizability
    check runs too.
    """
    results = {
        'has_strokes': has_strokes(traced),
        'coverage': coverage(mask, traced),
        'stroke_count': stroke_count_matches_template(traced, expected_stroke_count),
    }
    if char is not None:
        ocr_result = ocr_recognizable(char, mask, traced)
        if ocr_result[0] is not None:
            results['ocr'] = ocr_result
    # Overall: lowest score wins (worst metric drags everything down)
    overall = min(s for s, _ in results.values())
    results['overall_score'] = (overall, None)
    return results


def build_report(font_name, per_letter_results):
    """Build a Markdown report from per-letter results.

    Args:
        font_name: human-readable font name.
        per_letter_results: dict mapping char to a dict with keys
            'metrics' (output of assess_letter), 'template_used',
            'stroke_count'.

    Returns:
        Markdown string.
    """
    # Tier letters by issues + score. The score alone doesn't always tell
    # the story (a letter can have an overall score of 0.85 just because of
    # numerical coverage measurement, with no actual visible problem). So
    # we trust the explicit `issue` strings when deciding tier.
    clean: list[str] = []
    minor: list[tuple[str, list[str]]] = []
    major: list[tuple[str, list[str]]] = []

    for ch, info in per_letter_results.items():
        metrics = info['metrics']
        overall, _ = metrics['overall_score']
        issues = [issue for _name, (_score, issue) in metrics.items()
                  if issue is not None]
        if not issues:
            clean.append(ch)
        elif overall >= 0.6:
            minor.append((ch, issues))
        else:
            major.append((ch, issues))

    # Template usage summary
    template_counts: dict[str, int] = {}
    for info in per_letter_results.values():
        t = info.get('template_used') or 'none'
        template_counts[t] = template_counts.get(t, 0) + 1

    lines = [
        f"# {font_name} — Trace Report",
        "",
        f"**Letters traced:** {len(per_letter_results)}",
        "",
        "**Templates used:** " + ", ".join(
            f"{name} ({n})" for name, n in sorted(template_counts.items(), key=lambda kv: -kv[1])
        ),
        "",
        "## Summary",
        "",
        f"- ✅ Clean: {len(clean)} letters",
        f"- ⚠️  Minor issues: {len(minor)} letters",
        f"- ❌ Significant issues: {len(major)} letters",
        "",
    ]

    if clean:
        lines.append("### Clean letters")
        lines.append("")
        lines.append(" ".join(clean))
        lines.append("")

    if minor:
        lines.append("### Letters with minor issues")
        lines.append("")
        for ch, issues in minor:
            lines.append(f"- **{ch}**: {'; '.join(issues)}")
        lines.append("")

    if major:
        lines.append("### Letters with significant issues")
        lines.append("")
        for ch, issues in major:
            lines.append(f"- **{ch}**: {'; '.join(issues)}")
        lines.append("")

    return "\n".join(lines)


def build_metadata_json(font_name, font_file, license_id, per_letter_results,
                       canvas_dims, baseline_y, upem,
                       filename_fn=None):
    """Build the machine-readable metadata.json for a font's output folder.

    Args:
        filename_fn: callable mapping a character to its SVG filename.
            If None, falls back to a simple a-z/cap_A-Z convention (which
            doesn't handle special chars — the caller should pass the
            pipeline's `safe_filename` instead).
    """
    if filename_fn is None:
        def filename_fn(c):
            return f'cap_{c}.svg' if c.isupper() else f'{c}.svg'

    letters = {}
    for ch, info in per_letter_results.items():
        metrics = info['metrics']
        overall_score, _ = metrics['overall_score']
        issues = [issue for _name, (_score, issue) in metrics.items()
                  if issue is not None]
        letters[ch] = {
            'file': f'glyphs/{filename_fn(ch)}',
            'template_used': info.get('template_used'),
            'stroke_count': info.get('stroke_count', 0),
            'advance_px': info.get('advance_px'),
            'quality_score': round(overall_score, 3),
            'issues': issues,
        }

    return json.dumps({
        'font_name': font_name,
        'font_file': font_file,
        'license': license_id,
        'units_per_em': upem,
        'canvas_dims': list(canvas_dims),
        'baseline_y': baseline_y,
        'letters': letters,
    }, indent=2)
