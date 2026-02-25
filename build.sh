#!/bin/bash
set -e
cd "$(dirname "$0")"

NO_DOCKER=false
for arg in "$@"; do
    case "$arg" in
        --no-docker) NO_DOCKER=true ;;
    esac
done

# ── Prerequisite check ──────────────────────────────────────────────
missing=()
command -v python3  >/dev/null || missing+=("python3")
command -v gradle   >/dev/null || missing+=("gradle")
command -v rsvg-convert >/dev/null || missing+=("rsvg-convert (librsvg2-bin)")
command -v ar       >/dev/null || missing+=("ar (binutils)")
command -v tar      >/dev/null || missing+=("tar")
if [ "$NO_DOCKER" = false ]; then
    command -v docker >/dev/null || missing+=("docker")
fi

if [ -z "${ANDROID_HOME:-}${ANDROID_SDK_ROOT:-}" ]; then
    missing+=("ANDROID_HOME or ANDROID_SDK_ROOT env var")
fi

if [ ${#missing[@]} -gt 0 ]; then
    echo "Missing prerequisites:"
    for m in "${missing[@]}"; do echo "  - $m"; done
    exit 1
fi

mkdir -p build

# ── Build Android APK ────────────────────────────────────────────────
build_apk() {
    echo "── Building Android APK ──"

    local svg="clients/pytr_box.svg"
    local res_dir="clients/android/app/src/main/res"

    # Generate vector drawable launcher icon (square) from SVG
    local launcher="$res_dir/drawable/ic_launcher.xml"
    if [ ! -f "$launcher" ] || [ "$svg" -nt "$launcher" ]; then
        echo "Generating launcher vector drawable from SVG..."
        python3 clients/svg2vd.py "$svg" "$launcher" --dp-width 48 --dp-height 48
    fi

    # Generate mipmap PNGs at all densities
    local mipmap_check="$res_dir/mipmap-xxxhdpi/ic_launcher.png"
    if [ ! -f "$mipmap_check" ] || [ "$svg" -nt "$mipmap_check" ]; then
        echo "Generating mipmap PNGs..."
        python3 clients/gen_mipmaps.py "$svg" "$res_dir"
    fi

    # Generate banner PNG (320x180, icon + PYTR text)
    local banner="$res_dir/drawable/ic_banner.png"
    if [ ! -f "$banner" ] || [ "$svg" -nt "$banner" ]; then
        echo "Generating TV banner PNG..."
        python3 clients/gen_banner.py "$svg" "$banner"
    fi

    # Ensure local.properties exists
    if [ ! -f "clients/android/local.properties" ]; then
        echo "Warning: clients/android/local.properties not found — release signing may fail"
    fi

    gradle -p clients/android assembleRelease
    cp clients/android/app/build/outputs/apk/release/app-release.apk build/pytr-tv.apk
    echo "Built: build/pytr-tv.apk"
}

# Check if APK needs rebuilding
apk_stale=false
if [ ! -f "build/pytr-tv.apk" ]; then
    apk_stale=true
else
    while IFS= read -r -d '' f; do
        if [ "$f" -nt "build/pytr-tv.apk" ]; then
            apk_stale=true
            break
        fi
    done < <(find clients/android/app/src/ -type f -print0)
fi

if [ "$apk_stale" = true ]; then
    build_apk
else
    echo "── Android APK is up to date ──"
fi

# ── Build WebOS IPK ──────────────────────────────────────────────────
build_ipk() {
    echo "── Building WebOS IPK ──"

    local webos_dir="clients/webos"
    local svg="clients/pytr_box.svg"
    local tmp_dir
    tmp_dir=$(mktemp -d)

    # Generate icons from SVG if needed
    if [ ! -f "$webos_dir/icon.png" ] || [ "$svg" -nt "$webos_dir/icon.png" ]; then
        echo "Generating WebOS icons from SVG..."
        rsvg-convert -w 80 -h 80 "$svg" -o "$webos_dir/icon.png"
        rsvg-convert -w 130 -h 130 "$svg" -o "$webos_dir/largeIcon.png"
    fi

    # Read app ID and version from appinfo.json
    local app_id app_version
    app_id=$(python3 -c "import json; print(json.load(open('$webos_dir/appinfo.json'))['id'])")
    app_version=$(python3 -c "import json; print(json.load(open('$webos_dir/appinfo.json'))['version'])")

    # Build data.tar.gz — app files under /usr/palm/applications/<id>/
    local data_root="$tmp_dir/data"
    local app_dest="$data_root/usr/palm/applications/$app_id"
    local svc_dest="$data_root/usr/palm/services/${app_id}.service"
    mkdir -p "$app_dest" "$svc_dest"

    # Copy app files (exclude build artifacts)
    for f in appinfo.json index.html icon.png largeIcon.png; do
        [ -f "$webos_dir/$f" ] && cp "$webos_dir/$f" "$app_dest/"
    done
    [ -d "$webos_dir/webOSTVjs" ] && cp -r "$webos_dir/webOSTVjs" "$app_dest/"

    # Copy service files
    if [ -d "$webos_dir/services" ]; then
        cp "$webos_dir/services/"* "$svc_dest/"
    fi

    tar czf "$tmp_dir/data.tar.gz" -C "$data_root" .

    # Build control.tar.gz
    local ctrl_dir="$tmp_dir/control"
    mkdir -p "$ctrl_dir"
    cat > "$ctrl_dir/control" << EOF
Package: $app_id
Version: $app_version
Section: misc
Priority: optional
Architecture: all
webOS-Package-Format-Version: 2
maintainer: PYTR
Description: PYTR TV Client
EOF
    tar czf "$tmp_dir/control.tar.gz" -C "$ctrl_dir" .

    # Build debian-binary
    echo "2.0" > "$tmp_dir/debian-binary"

    # Assemble IPK (ar archive)
    ar r "$tmp_dir/${app_id}_${app_version}_all.ipk" \
        "$tmp_dir/debian-binary" \
        "$tmp_dir/control.tar.gz" \
        "$tmp_dir/data.tar.gz" 2>/dev/null

    cp "$tmp_dir/${app_id}_${app_version}_all.ipk" build/pytr-tv.ipk
    rm -rf "$tmp_dir"
    echo "Built: build/pytr-tv.ipk"
}

# Check if IPK needs rebuilding
ipk_stale=false
if [ ! -f "build/pytr-tv.ipk" ]; then
    ipk_stale=true
else
    while IFS= read -r -d '' f; do
        if [ "$f" -nt "build/pytr-tv.ipk" ]; then
            ipk_stale=true
            break
        fi
    done < <(find clients/webos/ -type f ! -name '*.ipk' ! -name 'build.sh' -print0)
fi

if [ "$ipk_stale" = true ]; then
    build_ipk
else
    echo "── WebOS IPK is up to date ──"
fi

# ── Build Docker container ───────────────────────────────────────────
if [ "$NO_DOCKER" = false ]; then
    echo "── Building Docker container ──"
    docker compose build
    echo "Done!"
else
    echo "── Skipping Docker build (--no-docker) ──"
fi
