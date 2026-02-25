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

# Auto-detect Android SDK if not set
if [ -z "${ANDROID_HOME:-}${ANDROID_SDK_ROOT:-}" ]; then
    for candidate in "$HOME/Android/Sdk" "$HOME/Android" "$HOME/.android/sdk" \
                     "/opt/android-sdk" "/usr/lib/android-sdk" \
                     "$HOME/Library/Android/sdk"; do
        if [ -d "$candidate/platforms" ]; then
            export ANDROID_HOME="$candidate"
            echo "Auto-detected ANDROID_HOME=$candidate"
            break
        fi
    done
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

    local face_svg="web/static/pytr.svg"
    local res_dir="clients/android/app/src/main/res"

    # Generate adaptive launcher icon (foreground/background layers + fallback PNGs)
    local mipmap_check="$res_dir/mipmap-anydpi-v26/ic_launcher.xml"
    if [ ! -f "$mipmap_check" ] || [ "$face_svg" -nt "$mipmap_check" ]; then
        echo "Generating launcher icon (adaptive + mipmap PNGs)..."
        python3 clients/gen_mipmaps.py "$face_svg" "$res_dir"
    fi

    # Generate TV banner (single combined PNG per density)
    local banner_check="$res_dir/mipmap-xxxhdpi/ic_banner.png"
    if [ ! -f "$banner_check" ] || [ "$face_svg" -nt "$banner_check" ]; then
        echo "Generating TV banner (mipmap PNGs)..."
        python3 clients/gen_banner.py "$face_svg" "$res_dir"
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
    local svg="web/static/pytr.svg"

    # Generate icons from face SVG with background and padding
    if [ ! -f "$webos_dir/icon.png" ] || [ "$svg" -nt "$webos_dir/icon.png" ]; then
        echo "Generating WebOS icons from SVG..."
        python3 clients/gen_webos_icons.py "$svg" "$webos_dir"
    fi

    ares-package "$webos_dir" -o build/ --no-minify
    local app_id app_version
    app_id=$(python3 -c "import json; print(json.load(open('$webos_dir/appinfo.json'))['id'])")
    app_version=$(python3 -c "import json; print(json.load(open('$webos_dir/appinfo.json'))['version'])")
    mv "build/${app_id}_${app_version}_all.ipk" build/pytr-tv.ipk
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
