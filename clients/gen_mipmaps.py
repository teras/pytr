#!/usr/bin/env python3
"""Generate Android adaptive launcher icon from face SVG (no background box).

Generates per-density:
  - ic_launcher_foreground.png  (108dp canvas, face in 72dp safe zone)
  - ic_launcher_background.png  (solid color)
  - ic_launcher.png             (fallback for pre-API-26, 80dp)
Plus:
  - mipmap-anydpi-v26/ic_launcher.xml  (adaptive icon XML)
"""
import subprocess, os, re, tempfile

DENSITIES = {
    'mipmap-mdpi':    (108, 80),     # (adaptive_canvas, fallback_size)
    'mipmap-hdpi':    (162, 120),
    'mipmap-xhdpi':   (216, 160),
    'mipmap-xxhdpi':  (324, 240),
    'mipmap-xxxhdpi': (432, 320),
}

BG_COLOR = '#929292'


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

    for folder, (canvas, fallback) in DENSITIES.items():
        out_dir = os.path.join(res_dir, folder)
        os.makedirs(out_dir, exist_ok=True)

        # Foreground: face in 60/108 of canvas (slightly smaller for breathing room in circle)
        content = int(canvas * 60 / 108)
        margin = (canvas - content) // 2
        fg_svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas}" height="{canvas}"'
                  f' viewBox="0 0 {canvas} {canvas}">'
                  f'<svg x="{margin}" y="{margin}" width="{content}" height="{content}" viewBox="{vb}">'
                  f'{inner}</svg></svg>')
        fg = os.path.join(out_dir, 'ic_launcher_foreground.png')
        _render(fg_svg, canvas, canvas, fg)

        # Background: solid color
        bg_svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas}" height="{canvas}"><rect width="{canvas}" height="{canvas}" fill="{BG_COLOR}"/></svg>'
        bg = os.path.join(out_dir, 'ic_launcher_background.png')
        _render(bg_svg, canvas, canvas, bg)

        # Fallback: face on colored bg, square (5% padding so face doesn't touch edges)
        pad = int(fallback * 0.05)
        inner_sz = fallback - 2 * pad
        fb_svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{fallback}" height="{fallback}"'
                  f' viewBox="0 0 {fallback} {fallback}">'
                  f'<rect width="{fallback}" height="{fallback}" fill="{BG_COLOR}" rx="{fallback // 6}"/>'
                  f'<svg x="{pad}" y="{pad}" width="{inner_sz}" height="{inner_sz}" viewBox="{vb}">'
                  f'{inner}</svg></svg>')
        fb = os.path.join(out_dir, 'ic_launcher.png')
        _render(fb_svg, fallback, fallback, fb)

        print(f'  {folder}: fg={canvas}x{canvas} fb={fallback}x{fallback}')

    # Adaptive icon XML
    v26 = os.path.join(res_dir, 'mipmap-anydpi-v26')
    os.makedirs(v26, exist_ok=True)
    with open(os.path.join(v26, 'ic_launcher.xml'), 'w') as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n'
                '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
                '    <background android:drawable="@mipmap/ic_launcher_background" />\n'
                '    <foreground android:drawable="@mipmap/ic_launcher_foreground" />\n'
                '</adaptive-icon>\n')
    print(f'  {v26}/ic_launcher.xml')


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('svg', help='Face SVG (without background box)')
    p.add_argument('res_dir', help='Android res/ directory')
    a = p.parse_args()
    generate(a.svg, a.res_dir)
