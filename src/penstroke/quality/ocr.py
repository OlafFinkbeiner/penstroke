"""OCR-based quality validation.

Renders the traced glyph back to a raster and runs OCR on it, comparing
against the original character. Catches semantic failures (the trace
"looks like" a different letter) that pixel-based coverage misses.

The metric is DIFFERENTIAL: it only carries signal about the trace when
the ORIGINAL font OCRs correctly — otherwise a failure says something
about the font (or about tesseract), not about the trace. Characters
outside tesseract's letters+digits whitelist (punctuation, accented
Latin-1) can never be recognized and are skipped entirely; scoring them
used to mark every punctuation glyph "significant issues" on machines
with tesseract installed and clean on machines without — a report that
was both wrong and non-reproducible.

`ocr_recognizable` returns (score, issue) with:
    1.0  traced glyph OCRs as the correct character (or a confusable)
    0.7  OCRs correct but wrong case (R→r)
    0.0  traced OCRs wrong while the original reads correctly — the
         only true-failure case, and the one worth reporting
    (None, None) when the metric is not applicable: OCR unavailable,
         char outside the whitelist, or the original font itself does
         not OCR as this char (no baseline, no signal).

Requires `pytesseract` and the `tesseract` binary on PATH.
"""

import string
import numpy as np
from PIL import Image, ImageDraw

from penstroke.core.smoothing import taper_profile


_OCR_AVAILABLE = None
_pytesseract = None


def ocr_available():
    """Is the OCR metric runnable (pytesseract + tesseract binary)?

    Recorded in metadata.json so two machines' reports are comparable —
    a font must not look cleaner just because tesseract was missing.
    """
    return _check_ocr()


def _check_ocr():
    """Lazily import pytesseract and cache result."""
    global _OCR_AVAILABLE, _pytesseract
    if _OCR_AVAILABLE is None:
        try:
            import pytesseract
            pytesseract.get_tesseract_version()
            _pytesseract = pytesseract
            _OCR_AVAILABLE = True
        except Exception:
            _OCR_AVAILABLE = False
    return _OCR_AVAILABLE


def _render_to_image(traced, mask_shape, size=200):
    """Render traced strokes as a black-on-white raster suitable for OCR."""
    H, W = mask_shape
    scale = size / max(H, W)
    new_w, new_h = int(W * scale), int(H * scale)
    img = Image.new('L', (new_w, new_h), 255)
    draw = ImageDraw.Draw(img)
    for xs, ys, widths in traced:
        n = len(xs)
        widths_t = widths * taper_profile(n)
        for x, y, w in zip(xs, ys, widths_t):
            r = max(0.5, w * scale / 2)
            cx, cy = x * scale, y * scale
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=0)
    pad = 20
    padded = Image.new('L', (new_w + 2 * pad, new_h + 2 * pad), 255)
    padded.paste(img, (pad, pad))
    return padded


def _ocr_char(img):
    """OCR a single-character image. Returns (predicted, confidence)."""
    # PSM 10 = single character mode. Whitelist letters+digits only
    # (punctuation in the whitelist confuses tesseract's arg parser).
    allowed = string.ascii_letters + string.digits
    config = f'--psm 10 -c tessedit_char_whitelist={allowed}'
    try:
        text = _pytesseract.image_to_string(img, config=config).strip()
        data = _pytesseract.image_to_data(
            img, config=config, output_type=_pytesseract.Output.DICT)
        confs = [int(c) for c in data['conf'] if int(c) > 0]
        return text, max(confs) if confs else 0
    except Exception:
        return '', 0


# Characters that are commonly confused by OCR even on perfect input.
# When the traced glyph OCRs as one of these confusables, don't penalize.
_CONFUSABLE = {
    'o': '0Oo', 'O': '0Oo', '0': '0Oo',
    'l': 'lI1|', 'I': 'lI1|', '1': 'lI1|',
    's': 'sS5', 'S': 'sS5', '5': 'sS5',
    'z': 'zZ2', 'Z': 'zZ2', '2': 'zZ2',  # z and 2 are genuinely similar in many handwriting fonts
    'g': 'gq9',
    'b': 'b6',
    'B': 'B8',
    # No 7 in here even though it looks like a Z — that's a real bug signal
}


def ocr_recognizable(char, mask, traced):
    """Score how well OCR recognizes the traced glyph as `char`.

    Differential metric — see the module docstring. Returns
    (score, issue_or_none), or (None, None) when not applicable
    (OCR unavailable, char not OCR-able, or no baseline: the original
    font itself doesn't read as `char`).
    """
    if not _check_ocr():
        return None, None
    # Outside tesseract's whitelist the "correct" answer is impossible
    # by construction — the metric carries no information.
    if not (char in string.ascii_letters or char in string.digits):
        return None, None
    if not traced:
        return 0.0, "no strokes to OCR"

    confusables = _CONFUSABLE.get(char, char)

    # Baseline first: if the ORIGINAL font doesn't OCR as this char,
    # a trace failure says nothing about the trace.
    orig_img = Image.fromarray((255 - mask * 255).astype(np.uint8))
    pad = 20
    padded_orig = Image.new('L', (orig_img.width + 2*pad, orig_img.height + 2*pad), 255)
    padded_orig.paste(orig_img, (pad, pad))
    orig_pred, _orig_conf = _ocr_char(padded_orig)
    orig_ok = (char in orig_pred
               or any(c in orig_pred for c in confusables)
               or char.lower() in orig_pred.lower())
    if not orig_ok:
        return None, None

    traced_img = _render_to_image(traced, mask.shape)
    pred, _conf = _ocr_char(traced_img)
    pred_clean = pred.strip()

    # Direct hit
    if char in pred_clean:
        return 1.0, None
    # Confusable hit (e.g., 'o' OCRs as '0')
    if any(c in pred_clean for c in confusables):
        return 1.0, None
    # Case-insensitive hit
    if char.lower() in pred_clean.lower():
        return 0.7, f"OCR returned {pred_clean!r} (case ambiguous)"
    return 0.0, f"OCR sees {pred_clean!r} instead of {char!r} (original reads correctly)"
