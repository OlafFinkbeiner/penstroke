"""Unicode-to-Hershey character mapping for non-Latin scripts.

Hershey's `greek` font stores Greek letters at ASCII positions (e.g., the
glyph at 'A' is uppercase Alpha). This module provides the Unicode→ASCII
mapping so a target font's Greek letters can be looked up by their proper
Unicode codepoint.

There is no Hershey font for Hebrew, Arabic, Devanagari, etc. For those
scripts the pipeline falls back to geometric stroke decomposition (which
works decently for letters with simple topology) or skips the letter
entirely if it produces no useful trace.
"""

# Hershey greek font slot mapping. Verified by visually inspecting each
# slot in HersheyFonts.greek. Some Greek letters share Latin slot positions:
# Α=A, Β=B, etc. for the ones where the Greek letter looks like its Latin
# slot. The non-obvious mappings come from inspecting hershey_greek.png:
#   Χ at C, Δ at D, Φ at F, Γ at G, Θ at J or Q, Λ at L, Ξ at X,
#   Π at P, Σ at S, Υ at U, Ψ at Y, Ω at W, Ζ at Z.
_GREEK_UPPER = {
    'Α': 'A', 'Β': 'B', 'Χ': 'C', 'Δ': 'D', 'Ε': 'E', 'Φ': 'F',
    'Γ': 'G', 'Η': 'H', 'Ι': 'I', 'Θ': 'J', 'Κ': 'K', 'Λ': 'L',
    'Μ': 'M', 'Ν': 'N', 'Ο': 'O', 'Π': 'P', 'Ρ': 'R', 'Σ': 'S',
    'Τ': 'T', 'Υ': 'U', 'Ω': 'W', 'Ξ': 'X', 'Ψ': 'Y', 'Ζ': 'Z',
}

# Lowercase Greek → ASCII slot in Hershey greek
_GREEK_LOWER = {
    'α': 'a', 'β': 'b', 'χ': 'c', 'δ': 'd', 'ε': 'e', 'φ': 'f',
    'γ': 'g', 'η': 'h', 'ι': 'i', 'θ': 'j', 'κ': 'k', 'λ': 'l',
    'μ': 'm', 'ν': 'n', 'ο': 'o', 'π': 'p', 'ρ': 'r', 'σ': 's',
    'τ': 't', 'υ': 'u', 'ω': 'w', 'ξ': 'x', 'ψ': 'y', 'ζ': 'z',
}

GREEK_MAP = {**_GREEK_UPPER, **_GREEK_LOWER}


def hershey_lookup_char(unicode_char):
    """Convert a Unicode character to (hershey_font, ascii_slot) for lookup.

    Returns:
        (font_name, char_in_hershey) if a Hershey template exists for
        this character, or (None, None) if no template is available.
    """
    if unicode_char in GREEK_MAP:
        return 'greek', GREEK_MAP[unicode_char]
    # ASCII characters use rowmans/cursive as already handled by selection.py
    if 32 <= ord(unicode_char) < 128:
        return None, unicode_char  # let normal Latin pipeline handle it
    # Hebrew, Arabic, Devanagari, etc. — no Hershey template available
    return None, None


# Character set definitions for convenient batch processing
GREEK_LETTERS = (
    'ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ' +   # uppercase
    'αβγδεζηθικλμνξοπρστυφχψω' +     # lowercase
    'ς'                                # final sigma
)

# Hebrew alphabet (22 letters, plus final forms). No Hershey templates exist;
# these are listed so the pipeline can be called on Hebrew fonts at all.
HEBREW_LETTERS = 'אבגדהוזחטיכלמנסעפצקרשת' + 'ךםןףץ'  # base + final forms
