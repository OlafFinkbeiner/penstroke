"""Pick the best Hershey template for each character + target skeleton.

The strategy is hybrid:
  1. A per-letter strategy map names which Hershey variants are *candidates*
     for that character. Most letters use only `rowmans` (the standard
     reference). Letters that handwriting fonts often draw differently
     (a, e, g, r) include `cursive` as an alternative.
  2. For each candidate, we score it against the target's skeleton topology
     using `score_template_match`. The lowest-scoring candidate wins.

If a target's skeleton has a closed loop (e.g., the bowl of a double-story
'a'), the closed-loop count in the score heavily favors templates with
matching closed loops. If the skeleton has no closed loops (single-story
'a'), the cursive template wins because rowmans's 2-stroke 'a' template has
a closed bowl, which doesn't match.
"""

from penstroke.templates.hershey import get_template
from penstroke.templates.scripts import GREEK_MAP
from penstroke.templates.topology import (
    template_topology, skeleton_topology, score_template_match,
)


# Letters where the printed (rowmans) and handwritten (cursive) shapes
# diverge. For these we evaluate both and let topology scoring decide.
# For everything else we use the rowmans-first default below.
LETTER_STRATEGY = {
    # Lowercase: handwriting fonts often use simpler single-stroke forms
    'a': ['cursive', 'rowmans'],   # single-story vs double-story
    'e': ['rowmans', 'cursive'],   # most handwriting still uses rowmans-like 'e'
    'g': ['rowmans', 'cursive'],   # open-tail vs single-stroke curl
    'f': ['rowmans'],
    't': ['rowmans'],
    's': ['rowmans'],
    'r': ['rowmans', 'cursive'],
    # z/Z: cursive's single-stroke z looks like a 2 or 7 to OCR. Force the
    # 3-stroke rowmans template (top horizontal, diagonal, bottom horizontal)
    # which matches how most handwriting fonts actually draw z too.
    'z': ['rowmans'],
    'Z': ['rowmans'],
    # A: cursive template can match the topology of fonts where the crossbar
    # connects to the legs at junctions (the skeleton has 2 endpoints, not 3),
    # but the resulting 1-stroke trace looks like a 4 or messy curl. Force
    # the 3-stroke rowmans template.
    'A': ['rowmans'],

    # Uppercase: force rowmans for multi-stroke letters. Handwriting fonts
    # often draw these as one continuous skeleton path (legs meet at the apex
    # of 'A', stem-bowl-leg of 'R' as one curve), which would otherwise score
    # cursive's 1-stroke templates very well. But the "right" visual stroke
    # decomposition for these letters is the block-letter form regardless.
    'A': ['rowmans'],
    'B': ['rowmans'],
    'E': ['rowmans'],
    'F': ['rowmans'],
    'H': ['rowmans'],
    'K': ['rowmans'],
    'M': ['rowmans'],
    'N': ['rowmans'],
    'R': ['rowmans'],
    'T': ['rowmans'],
    'V': ['rowmans'],
    'W': ['rowmans'],
    'X': ['rowmans'],
    'Y': ['rowmans'],
    'Z': ['rowmans'],
    # C, D, G, I, J, L, O, P, Q, S, U: rowmans is reasonable but cursive also
    # works (they're 1-2 strokes in both). Let topology score pick.
}

# Used when LETTER_STRATEGY has no entry for this character.
DEFAULT_ORDER = ['rowmans', 'cursive', 'scripts', 'futural']


def choose_template(char, skel):
    """Pick the best Hershey template for a character + target skeleton.

    Returns (template, font_name, score) where template is the
    (strokes, bbox) tuple from `get_template`. Returns (None, None, inf)
    if no candidate has a template for this character.

    Handles Greek letters by routing through Hershey's `greek` font (which
    stores Greek glyphs at ASCII slot positions — Alpha at 'A', etc.).
    Other non-Latin scripts (Hebrew, Arabic) have no Hershey template;
    they fall through to the geometric-decomposition fallback in core/strokes.py.
    """
    # Greek: route through Hershey 'greek' font using the ASCII slot mapping.
    if char in GREEK_MAP:
        greek_slot = GREEK_MAP[char]
        tmpl = get_template(greek_slot, 'greek')
        if tmpl is not None:
            return tmpl, 'greek', 0.0  # we trust the explicit Unicode mapping
        return None, None, float('inf')

    skel_topo = skeleton_topology(skel)
    candidates = LETTER_STRATEGY.get(char, DEFAULT_ORDER)

    best = None
    best_score = float('inf')
    best_font = None
    for font_name in candidates:
        tmpl = get_template(char, font_name)
        if tmpl is None:
            continue
        strokes, _bbox = tmpl
        tmpl_topo = template_topology(strokes)
        score = score_template_match(skel_topo, tmpl_topo)
        if score < best_score:
            best_score = score
            best = tmpl
            best_font = font_name
    return best, best_font, best_score
