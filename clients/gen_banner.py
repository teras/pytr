#!/usr/bin/env python3
"""Generate Android TV banner: face icon left + PYTR text right.

Uses the face SVG (no background box). Generates per-density:
  - ic_banner.png  (combined: face + text on dark background)
No adaptive layers — single self-contained image.
"""
import subprocess, os, re, tempfile

DENSITIES = {
    'mipmap-mdpi':    (320, 180),
    'mipmap-hdpi':    (480, 270),
    'mipmap-xhdpi':   (640, 360),
    'mipmap-xxhdpi':  (960, 540),
    'mipmap-xxxhdpi': (1280, 720),
}

BG_COLOR = '#181818'


def _extract_svg(svg_path):
    with open(svg_path) as f:
        content = f.read()
    inner = re.search(r'<svg[^>]*>(.*)</svg>', content, re.DOTALL)
    vb = re.search(r'viewBox="([^"]*)"', content)
    return inner.group(1), vb.group(1) if vb else '0 0 160 160'


def _render(svg_str, w, h, out):
    with tempfile.NamedTemporaryFile(suffix='.svg', mode='w', delete=False) as f:
        f.write(svg_str); tmp = f.name
    try:
        subprocess.run(['rsvg-convert', '-w', str(w), '-h', str(h), tmp, '-o', out], check=True)
    finally:
        os.unlink(tmp)


def generate(svg_path, res_dir):
    inner, vb = _extract_svg(svg_path)

    for folder, (w, h) in DENSITIES.items():
        out_dir = os.path.join(res_dir, folder)
        os.makedirs(out_dir, exist_ok=True)

        icon_size = int(h * 0.7)
        icon_y = (h - icon_size) // 2
        icon_x = int(h * 0.15)
        text_x = icon_x + icon_size + int(h * 0.1)
        text_y = h // 2
        font_size = int(h * 0.28)

        # Combined banner: dark background + face + PYTR text
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
               f'<rect width="{w}" height="{h}" fill="{BG_COLOR}"/>'
               f'<svg x="{icon_x}" y="{icon_y}" width="{icon_size}" height="{icon_size}" viewBox="{vb}">'
               f'{inner}</svg>'
               f'<text x="{text_x}" y="{text_y}" fill="#ffffff" font-family="sans-serif"'
               f' font-weight="700" font-size="{font_size}" dominant-baseline="central"'
               f' letter-spacing="3">PYTR</text></svg>')
        out = os.path.join(out_dir, 'ic_banner.png')
        _render(svg, w, h, out)

        print(f'  {folder}: {w}x{h}')


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('svg', help='Face SVG (without background box)')
    p.add_argument('res_dir', help='Android res/ directory')
    a = p.parse_args()
    generate(a.svg, a.res_dir)
