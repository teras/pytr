# 🎬 PYTR - Private YouTube Relay

[![License](https://img.shields.io/github/license/teras/pytr)](LICENSE)
[![Docker Pulls](https://img.shields.io/docker/pulls/teras/pytr)](https://hub.docker.com/r/teras/pytr)
[![Docker Image Size](https://img.shields.io/docker/image-size/teras/pytr/latest)](https://hub.docker.com/r/teras/pytr)
[![Last Commit](https://img.shields.io/github/last-commit/teras/pytr)](https://github.com/teras/pytr)

A self-hosted web interface for searching and streaming YouTube videos.

## 🚀 Quick Start

```bash
mkdir pytr && cd pytr
curl -sL https://raw.githubusercontent.com/teras/pytr/main/docker-compose.prod.yml -o docker-compose.yml
docker compose up -d
```

Open http://localhost:8000 — the discovery service enables TV apps to auto-detect your server on the local network.

## ✨ Features

- 🔍 Search YouTube videos with infinite scroll
- 📺 Channel browsing and related videos
- 📡 Adaptive streaming (DASH up to 4K, HLS fallback for multi-audio)
- 💬 Subtitle support (manual and auto-captions)
- 🌐 Multi-audio language switching
- 👥 Netflix-style profiles with watch history, favorites, and per-profile preferences
- 🔒 Password-protected access with optional per-profile PIN lock
- 🖼️ Embeddable player for use with LibRedirect
- 🍪 YouTube cookie support for age-restricted content
- 📱 Mobile-friendly responsive interface
- 📺 TV apps for Android TV and LG webOS with auto-discovery

## 🔧 Building from Source

```bash
git clone https://github.com/teras/pytr.git
cd pytr
./build.sh   # builds TV apps + Docker image
docker compose up -d
```

On first launch, a setup wizard will guide you through creating an admin profile and setting the app password.

Data is persisted in `./data/` (SQLite database with profiles, history, favorites, and settings).

## 🔗 Browser Extension (Redirect YouTube → PYTR)

Use [LibRedirect](https://libredirect.github.io) to automatically redirect YouTube URLs and embeds to your PYTR instance.

### Firefox

Install from [Firefox Add-ons](https://addons.mozilla.org/firefox/addon/libredirect/).

### Chromium / Brave / Edge (Linux)

1. Download the `.crx` file from [LibRedirect releases](https://github.com/libredirect/browser_extension/releases)
2. Go to `chrome://extensions`, enable **Developer mode**
3. Refresh the page, then drag the `.crx` file into the extensions page

### Setup

1. Open LibRedirect settings → **YouTube**
2. Set frontend to **Invidious**
3. Under **Custom instances**, add your PYTR URL (e.g. `http://localhost:8000`)
4. Disable all default public instances
5. Enable **Embeds** to also replace YouTube players on third-party sites

## 📺 TV App

PYTR includes TV apps for **Android TV** and **LG webOS**. Open `http://<your-pytr-host>:8000/setup-tv` to install them directly to your TV.

Before installing, enable **Developer Mode** on your TV:
- **Android TV**: Settings → About → tap "Build number" 7 times, then enable USB debugging in Developer options
- **LG webOS**: Install the [Developer Mode app](https://webostv.developer.lge.com/develop/getting-started/developer-mode-app) from the LG Content Store

### Firewall

If you use a firewall, open these ports:

```bash
sudo firewall-cmd --add-port=8000/tcp   # Web UI
sudo firewall-cmd --add-port=5444/udp   # TV auto-discovery
```

## ⚙️ Configuration

### 🍪 YouTube Cookies

To access age-restricted content, extract YouTube cookies from your browser:

```bash
pip install yt-dlp   # if not already installed
python3 extract-cookies.py
```

The script supports Firefox, Chrome, Chromium, Brave, and Edge. It will ask you to select your browser, then extract only YouTube/Google cookies to `data/cookies.txt`.

After extracting, restart the container: `docker compose restart`

### 👤 Non-default UID/GID

The container runs as UID/GID `1000:1000` by default, which matches most Linux single-user setups. If your user has a different UID/GID, create a `.env` file:

```bash
UID=1001
GID=1001
```

### 🔀 Reverse Proxy

If running behind a reverse proxy (e.g. nginx), add `TRUSTED_PROXY=1` to your `.env` file so that brute-force protection sees real client IPs instead of the proxy's address.
