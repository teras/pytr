#!/bin/bash
set -e
cd "$(dirname "$0")"

SVG="../pytr_box.svg"
BANNER="app/src/main/res/drawable/ic_banner.xml"

# Regenerate vector drawable if SVG is newer (or doesn't exist)
if [ ! -f "$BANNER" ] || [ "$SVG" -nt "$BANNER" ]; then
    echo "Generating vector drawable from SVG..."
    python ../svg2vd.py "$SVG" "$BANNER" --vp-width 320 --vp-height 180 --dp-width 320 --dp-height 180
    echo "Icon updated: $BANNER"
fi

gradle assembleRelease
cp app/build/outputs/apk/release/app-release.apk pytr-tv.apk
echo "Built: pytr-tv.apk"
