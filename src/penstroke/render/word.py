"""Compose individual letter SVGs into a typeset word as an HTML demo.

Each letter is placed at the cumulative sum of advance widths, with the
letter's own `pad` margin subtracted so the glyph itself lines up correctly.
Letters share a common baseline (built into the canvas), so descenders
hang below it and capitals ascend above naturally — no per-letter Y
adjustment needed.

The output is a standalone HTML page that embeds the SVGs inline, draws
a faint baseline rule, and includes a replay button that restarts the
SMIL animations on all the letters in unison.
"""

import os
import re


def make_word_demo_html(font_label, font_dir, word="hello world"):
    """Build an HTML page demonstrating word composition from glyph SVGs.

    Args:
        font_label: friendly name for the heading (e.g., "Caveat").
        font_dir: directory containing the per-letter SVG files
            (`a.svg`...`z.svg`, `cap_A.svg`...`cap_Z.svg`).
        word: the word to typeset.

    Returns:
        HTML document as a string, or None if no SVGs were found.
    """
    # Load each unique letter's SVG + metadata once
    glyph_data = {}
    for ch in set(word):
        if ch.isspace():
            continue
        fname = f'cap_{ch}.svg' if ch.isupper() else f'{ch}.svg'
        path = os.path.join(font_dir, fname)
        if not os.path.exists(path):
            continue
        with open(path, encoding='utf-8') as f:
            content = f.read()

        def attr(name, src=content):
            m = re.search(rf'data-{name}="([0-9.]+)"', src)
            return float(m.group(1)) if m else None

        glyph_data[ch] = {
            'svg': content,
            'baseline': attr('baseline'),
            'pad': attr('pad'),
            'advance': attr('advance'),
            'canvas_w': float(re.search(r'width="([0-9.]+)"', content).group(1)),
            'canvas_h': float(re.search(r'height="([0-9.]+)"', content).group(1)),
        }

    if not glyph_data:
        return None

    # All letters share canvas_h since they came from the same font run;
    # just take the first one's value as canonical.
    canvas_h = max(g['canvas_h'] for g in glyph_data.values())
    sample = next(iter(glyph_data.values()))

    # Lay out letters left to right, accumulating advance widths.
    # Each letter's SVG is positioned so its glyph appears at offset_x;
    # since each SVG has its own internal `pad` margin, we shift by -pad
    # so the glyph (not the pad) sits at offset_x.
    offset_x = 0.0
    placements = []  # (svg_content, css_left, char)
    for ch in word:
        if ch.isspace():
            offset_x += sample['advance'] * 0.5  # half-em word space
            continue
        g = glyph_data.get(ch)
        if not g:
            continue
        placements.append((g['svg'], offset_x - g['pad'], ch))
        offset_x += g['advance']

    total_width = offset_x + 80  # padding at the right edge
    baseline_y = sample['baseline']

    # Build HTML — single page with embedded SVGs and a baseline rule overlay
    letters_html = []
    for svg_content, css_left, ch in placements:
        # Escape the data-ch attribute value since it's user-data going into HTML
        ch_attr = (ch.replace('&', '&amp;').replace('"', '&quot;')
                     .replace('<', '&lt;').replace('>', '&gt;'))
        letters_html.append(
            f'  <div class="letter" style="left: {css_left:.1f}px;" '
            f'data-ch="{ch_attr}">{svg_content}</div>'
        )

    return f'''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>{font_label} — word composition demo</title>
<style>
  body {{
    font-family: -apple-system, system-ui, sans-serif;
    background: #fafafa;
    padding: 40px;
    color: #222;
  }}
  h1 {{ font-weight: 500; font-size: 22px; margin: 0 0 8px; }}
  p {{ color: #666; font-size: 14px; max-width: 700px; line-height: 1.5; }}
  .word-stage {{
    position: relative;
    width: {total_width}px;
    height: {canvas_h}px;
    background: white;
    border-radius: 12px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    margin: 30px 0;
    overflow: visible;
  }}
  .baseline-rule {{
    position: absolute;
    left: 20px;
    right: 20px;
    height: 1px;
    background: rgba(200, 100, 100, 0.4);
    top: {baseline_y}px;
    pointer-events: none;
  }}
  .letter {{ position: absolute; top: 0; }}
  button {{
    padding: 8px 18px; background: #222; color: white;
    border: none; border-radius: 6px; cursor: pointer; margin-right: 10px;
  }}
  button:hover {{ background: #444; }}
  .info {{ font-size: 12px; color: #888; margin-top: 8px; }}
</style>
</head>
<body>
<h1>{font_label} — word composition</h1>
<p>Each letter is loaded from its own SVG file, positioned using the
original font's advance width and baseline. The red line is the shared
baseline. Letters typeset correctly into words because they share a
common coordinate frame.</p>

<div>
  <button id="restart">↻ Restart animation</button>
  <span class="info">Word: <strong>{word}</strong></span>
</div>

<div class="word-stage" id="stage">
  <div class="baseline-rule"></div>
{chr(10).join(letters_html)}
</div>

<script>
  document.getElementById('restart').addEventListener('click', () => {{
    document.querySelectorAll('svg').forEach(svg => {{
      try {{ svg.setCurrentTime(0); }} catch(e) {{}}
    }});
  }});
</script>
</body>
</html>'''
