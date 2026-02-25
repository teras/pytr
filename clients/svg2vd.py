#!/usr/bin/env python3
"""Convert a clean SVG (paths + optional rect) to Android Vector Drawable XML."""
import re, sys, xml.etree.ElementTree as ET

def rect_to_path(attrs):
    w = float(attrs.get('width', 0))
    h = float(attrs.get('height', 0))
    rx = float(attrs.get('rx', 0))
    ry = float(attrs.get('ry', rx))
    if rx == 0 and ry == 0:
        return f"M0,0h{w}v{h}H0z"
    return (f"M{rx},0 L{w-rx},0 Q{w},0 {w},{ry} "
            f"L{w},{h-ry} Q{w},{h} {w-rx},{h} "
            f"L{rx},{h} Q0,{h} 0,{h-ry} "
            f"L0,{ry} Q0,0 {rx},0 Z")

def convert(svg_path, out_path, vp_width=None, vp_height=None, dp_width=None, dp_height=None):
    tree = ET.parse(svg_path)
    root = tree.getroot()
    ns = {'svg': 'http://www.w3.org/2000/svg'}

    vb = root.get('viewBox', '').split()
    vw = float(vb[2]) if len(vb) >= 4 else float(root.get('width', 100))
    vh = float(vb[3]) if len(vb) >= 4 else float(root.get('height', 100))
    vpw = vp_width or vw
    vph = vp_height or vh
    dpw = dp_width or vpw
    dph = dp_height or vph

    paths = []
    for el in root.iter():
        tag = el.tag.split('}')[-1]  # strip namespace
        if tag == 'rect':
            d = rect_to_path(el.attrib)
            fill = el.get('fill', '#000000')
            paths.append((fill, d))
        elif tag == 'path':
            d = el.get('d', '')
            fill = el.get('fill', '#000000')
            if d:
                paths.append((fill, d))

    # If viewport differs from viewBox, offset paths to center the content
    ox = (vpw - vw) / 2
    oy = (vph - vh) / 2
    use_group = ox != 0 or oy != 0

    lines = ['<?xml version="1.0" encoding="utf-8"?>',
             '<vector xmlns:android="http://schemas.android.com/apk/res/android"',
             f'    android:width="{dpw}dp"',
             f'    android:height="{dph}dp"',
             f'    android:viewportWidth="{vpw}"',
             f'    android:viewportHeight="{vph}">']

    if use_group:
        lines.append(f'    <group android:translateX="{ox}" android:translateY="{oy}">')

    indent = '        ' if use_group else '    '
    for fill, d in paths:
        lines.append(f'{indent}<path')
        lines.append(f'{indent}    android:fillColor="{fill}"')
        lines.append(f'{indent}    android:pathData="{d}" />')

    if use_group:
        lines.append('    </group>')
    lines.append('</vector>')

    with open(out_path, 'w') as f:
        f.write('\n'.join(lines) + '\n')

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('svg')
    p.add_argument('out')
    p.add_argument('--vp-width', type=float)
    p.add_argument('--vp-height', type=float)
    p.add_argument('--dp-width', type=float)
    p.add_argument('--dp-height', type=float)
    a = p.parse_args()
    convert(a.svg, a.out, a.vp_width, a.vp_height, a.dp_width, a.dp_height)
