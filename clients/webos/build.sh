#!/bin/bash
set -e
cd "$(dirname "$0")"

SVG="../pytr_box.svg"

# Regenerate icons if SVG is newer (or icons don't exist)
if [ ! -f "icon.png" ] || [ "$SVG" -nt "icon.png" ]; then
    echo "Generating icons from SVG..."
    rsvg-convert -w 80 -h 80 "$SVG" -o icon.png
    rsvg-convert -w 130 -h 130 "$SVG" -o largeIcon.png
    echo "Icons updated"
fi

ares-package . -o .
echo "Built: pytr-tv.ipk"
