#!/bin/bash
# Deploy PYTR to WebOS TV with cache clear and app restart.
# Usage: ./deploy.sh [DEVICE_NAME]
#   DEVICE_NAME defaults to the first non-emulator TV device found.
set -e
cd "$(dirname "$0")"

DEVICE="${1:-}"
APP_ID="onl.ycode.pytr"

# ── Find device if not specified ────────────────────────────────────
if [ -z "$DEVICE" ]; then
    DEVICE=$(ares-setup-device -F 2>/dev/null \
        | python3 -c "
import sys, json
devs = json.load(sys.stdin)
for d in devs:
    if d['name'] != 'emulator':
        print(d['name']); break
" 2>/dev/null)
    if [ -z "$DEVICE" ]; then
        echo "Error: no TV device found. Register one with: ares-setup-device" >&2
        exit 1
    fi
fi
echo "==> Device: $DEVICE"

# ── Package ─────────────────────────────────────────────────────────
echo "==> Packaging app..."
OUT_DIR="$(mktemp -d)"
ares-package . -o "$OUT_DIR" --no-minify
IPK=$(ls "$OUT_DIR"/*.ipk 2>/dev/null | head -1)
if [ -z "$IPK" ]; then
    echo "Error: ares-package produced no .ipk" >&2
    rm -rf "$OUT_DIR"
    exit 1
fi
echo "    $IPK"

# ── Close running app (ignore errors if not running) ────────────────
echo "==> Closing app on TV..."
ares-launch -d "$DEVICE" --close "$APP_ID" 2>/dev/null || true

# ── Install ─────────────────────────────────────────────────────────
echo "==> Installing..."
ares-install "$IPK" -d "$DEVICE"
rm -rf "$OUT_DIR"

# ── Clear browser cache via luna-send over SSH ──────────────────────
echo "==> Clearing browser cache..."
# Get SSH connection details from ares config
DEVICE_INFO=$(ares-setup-device -F 2>/dev/null \
    | python3 -c "
import sys, json, os
devs = json.load(sys.stdin)
name = '$DEVICE'
for d in devs:
    if d['name'] == name:
        ip = d['deviceinfo']['ip']
        port = d['deviceinfo']['port']
        user = d['deviceinfo']['user']
        pk = d['details'].get('privatekey', '')
        passphrase = d['details'].get('passphrase', '')
        # Find the key file
        key_dir = os.path.expanduser('~/.ssh')
        key_path = os.path.join(key_dir, pk) if pk else ''
        print(f'{ip}|{port}|{user}|{key_path}|{passphrase}')
        break
")

IFS='|' read -r IP PORT USER KEYFILE PASSPHRASE <<< "$DEVICE_INFO"

if [ -n "$KEYFILE" ] && [ -f "$KEYFILE" ]; then
    # SSH into TV and clear webapp cache for our app
    ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -q \
        -i "$KEYFILE" -p "$PORT" "$USER@$IP" \
        "rm -rf /home/prisoner/apps/usr/palm/data/${APP_ID}/cache 2>/dev/null; \
         rm -rf /tmp/webappmanager2/webappcache/${APP_ID} 2>/dev/null; \
         echo 'Cache cleared'" 2>/dev/null || echo "    (cache clear via SSH skipped)"
else
    echo "    (no SSH key found, skipping cache clear)"
fi

# ── Launch ──────────────────────────────────────────────────────────
echo "==> Launching..."
ares-launch -d "$DEVICE" "$APP_ID"

echo "==> Done! App deployed and launched on $DEVICE"
