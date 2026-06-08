"""Process many Google handwriting fonts in one go.

Each font is downloaded from the Google Fonts GitHub repo (if not already
present) and traced into its own subdirectory under the chosen output root.

Usage:
    python scripts/batch_google_fonts.py output_root/

The font list is defined inline below. To add more, find the font's path
in https://github.com/google/fonts and add a (prefix, name, url) tuple.
"""

import os
import sys
import urllib.request
from pathlib import Path

# Add src to path so this script works without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from penstroke import trace_font


# Each entry: (folder_name, display_name, ttf_url)
FONTS = [
    ('caveat', 'Caveat',
     'https://github.com/google/fonts/raw/main/ofl/caveat/Caveat%5Bwght%5D.ttf'),
    ('indie_flower', 'Indie Flower',
     'https://github.com/google/fonts/raw/main/ofl/indieflower/IndieFlower-Regular.ttf'),
    ('kalam', 'Kalam',
     'https://github.com/google/fonts/raw/main/ofl/kalam/Kalam-Regular.ttf'),
    ('shadows_into_light', 'Shadows Into Light',
     'https://github.com/google/fonts/raw/main/ofl/shadowsintolight/ShadowsIntoLight.ttf'),
    ('patrick_hand', 'Patrick Hand',
     'https://github.com/google/fonts/raw/main/ofl/patrickhand/PatrickHand-Regular.ttf'),
    ('architects_daughter', 'Architects Daughter',
     'https://github.com/google/fonts/raw/main/ofl/architectsdaughter/ArchitectsDaughter-Regular.ttf'),
]


def download_font(url, out_path):
    """Download a font file if it doesn't already exist."""
    if os.path.exists(out_path):
        return
    print(f"Downloading {url}...")
    urllib.request.urlretrieve(url, out_path)


def main(output_root):
    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    # Cache downloaded TTFs in a sibling directory so we don't re-download
    cache_dir = output_root / '_ttf_cache'
    cache_dir.mkdir(exist_ok=True)

    for folder, display_name, url in FONTS:
        ttf_name = os.path.basename(url).replace('%5B', '[').replace('%5D', ']')
        ttf_path = cache_dir / ttf_name
        try:
            download_font(url, ttf_path)
        except Exception as e:
            print(f"Failed to download {display_name}: {e}")
            continue

        out_dir = output_root / folder
        try:
            trace_font(
                str(ttf_path),
                str(out_dir),
                font_name=display_name,
                license_id='OFL-1.1',
                verbose=True,
            )
        except Exception as e:
            print(f"Failed to process {display_name}: {e}")
            continue

    # Top-level index page
    write_index(output_root)


def write_index(output_root):
    """Write a top-level index.html linking to each font's outputs."""
    fonts_present = [f for f, _, _ in FONTS if (output_root / f).is_dir()]

    rows = []
    for folder in fonts_present:
        display = next(name for f, name, _ in FONTS if f == folder)
        rows.append(f'''
        <div class="card">
            <h3>{display}</h3>
            <p>
                <a href="{folder}/alphabet_animated.svg">alphabet</a> ·
                <a href="{folder}/word_demo.html">word demo</a> ·
                <a href="{folder}/report.md">report</a> ·
                <a href="{folder}/metadata.json">metadata</a>
            </p>
        </div>''')

    html = f'''<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>penstroke — font index</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 900px;
         margin: 40px auto; padding: 0 20px; }}
  .card {{ padding: 16px; margin: 12px 0; background: white;
           border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  .card h3 {{ margin: 0 0 6px; }}
  .card a {{ color: #2253c4; }}
</style>
</head>
<body>
<h1>penstroke — font output index</h1>
<p>{len(fonts_present)} fonts processed.</p>
{"".join(rows)}
</body>
</html>'''

    with open(output_root / 'index.html', 'w') as f:
        f.write(html)
    print(f"\nWrote index: {output_root / 'index.html'}")


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print("Usage: python scripts/batch_google_fonts.py output_root/")
        sys.exit(1)
    main(sys.argv[1])
