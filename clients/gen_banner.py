#!/usr/bin/env python3
"""Generate Android TV banner PNG (320x180) from pytr_box.svg: icon left + PYTR text right on dark bg."""
import subprocess, sys, tempfile, os

def generate(svg_path, out_path, width=320, height=180):
    bg_color = "#181818"
    icon_size = int(height * 0.75)  # icon 75% of height
    icon_y = (height - icon_size) // 2
    icon_x = int(height * 0.15)  # left margin
    text_x = icon_x + icon_size + int(height * 0.15)
    text_y = height // 2

    # Read original SVG content to embed
    with open(svg_path) as f:
        svg_content = f.read()

    # Extract inner SVG content (everything between <svg ...> and </svg>)
    import re
    inner = re.search(r'<svg[^>]*>(.*)</svg>', svg_content, re.DOTALL)
    if not inner:
        raise ValueError("Cannot parse SVG")
    inner_svg = inner.group(1)

    # Get viewBox
    vb_match = re.search(r'viewBox="([^"]*)"', svg_content)
    vb = vb_match.group(1) if vb_match else "0 0 170 170"

    banner_svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" fill="{bg_color}" rx="12"/>
  <svg x="{icon_x}" y="{icon_y}" width="{icon_size}" height="{icon_size}" viewBox="{vb}">
    {inner_svg}
  </svg>
  <text x="{text_x}" y="{text_y}" fill="#ffffff" font-family="sans-serif" font-weight="700" font-size="52" dominant-baseline="central" letter-spacing="3">PYTR</text>
</svg>'''

    with tempfile.NamedTemporaryFile(suffix='.svg', mode='w', delete=False) as tmp:
        tmp.write(banner_svg)
        tmp_path = tmp.name

    try:
        subprocess.run(['rsvg-convert', '-w', str(width), '-h', str(height), tmp_path, '-o', out_path], check=True)
    finally:
        os.unlink(tmp_path)

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('svg', help='Source square SVG (pytr_box.svg)')
    p.add_argument('out', help='Output PNG path')
    p.add_argument('--width', type=int, default=320)
    p.add_argument('--height', type=int, default=180)
    a = p.parse_args()
    generate(a.svg, a.out, a.width, a.height)
