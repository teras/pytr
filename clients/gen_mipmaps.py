#!/usr/bin/env python3
"""Generate Android mipmap PNGs from square SVG at all density buckets."""
import subprocess, sys, os

# Android mipmap sizes for launcher icons (dp × density factor)
# Base: 48dp
DENSITIES = {
    'mipmap-mdpi':    48,   # 1x
    'mipmap-hdpi':    72,   # 1.5x
    'mipmap-xhdpi':   96,   # 2x
    'mipmap-xxhdpi':  144,  # 3x
    'mipmap-xxxhdpi': 192,  # 4x
}

def generate(svg_path, res_dir, name='ic_launcher'):
    for folder, size in DENSITIES.items():
        out_dir = os.path.join(res_dir, folder)
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, f'{name}.png')
        subprocess.run([
            'rsvg-convert', '-w', str(size), '-h', str(size),
            svg_path, '-o', out_path,
        ], check=True)
        print(f'  {out_path} ({size}x{size})')

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('svg', help='Source square SVG')
    p.add_argument('res_dir', help='Android res/ directory')
    p.add_argument('--name', default='ic_launcher', help='Output filename (without .png)')
    a = p.parse_args()
    generate(a.svg, a.res_dir, a.name)
