#!/usr/bin/env python3
"""Generate WebOS TV icons from face SVG, transparent background, edge-to-edge."""
import subprocess, os, re, tempfile


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


def generate(svg_path, out_dir):
    inner, vb = _extract_svg(svg_path)

    for name, size in [('icon.png', 80), ('largeIcon.png', 130)]:
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}"'
               f' viewBox="{vb}">'
               f'{inner}</svg>')
        out = os.path.join(out_dir, name)
        _render(svg, size, size, out)
        print(f'  {name}: {size}x{size}')


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('svg', help='Face SVG (without background box)')
    p.add_argument('out_dir', help='Output directory')
    a = p.parse_args()
    generate(a.svg, a.out_dir)
